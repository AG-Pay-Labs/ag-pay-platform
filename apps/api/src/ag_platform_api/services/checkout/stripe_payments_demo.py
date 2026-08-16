import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.types import (
    AuthorizationOutcome,
    AuthorizationResult,
    IssuingCardSecret,
)

DEMO_REFERENCES = frozenset(
    {"pm_stripe_demo_success", "pm_stripe_demo_decline", "pm_stripe_demo_3ds"}
)
PAYMENT_INTENT_PATTERN = re.compile(r"^pi_[A-Za-z0-9]+$")
CHECKOUT_SESSION_PATTERN = re.compile(r"^cs_test_[A-Za-z0-9]+$")


@dataclass(frozen=True, slots=True)
class StripeHostedSession:
    session_id: str
    checkout_url: str


@dataclass(frozen=True, slots=True)
class StripeHostedVerification:
    outcome: AuthorizationOutcome
    provider_reference: str | None = None
    receipt_url: str | None = None


class StripeTestCardFixtures:
    """Worker-only access to Stripe's public test-card fixtures."""

    async def retrieve_card(self, reference: str) -> IssuingCardSecret:
        # Official Stripe test fixtures. Do not log, persist, return, or expose these values.
        values = {
            "pm_stripe_demo_success": ("4242424242424242", "123"),
            "pm_stripe_demo_decline": ("4000000000000002", "123"),
            "pm_stripe_demo_3ds": ("4000000000003220", "123"),
        }
        try:
            number, cvc = values[reference]
        except KeyError:
            raise CheckoutError(CheckoutErrorCode.card_reference_invalid) from None
        return IssuingCardSecret(number, cvc, 12, 2034)


class StripePaymentsDemoGateway(StripeTestCardFixtures):
    """Development-only Stripe API fixture provider for legacy demo rails.

    The keyless fixed-URL hosted rail uses :class:`StripeTestCardFixtures`
    directly. This gateway remains credentialed because its legacy operations
    create and retrieve Stripe objects.
    """

    def __init__(
        self,
        *,
        secret_key: str,
        api_url: str = "https://api.stripe.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not secret_key.startswith("sk_test_"):
            raise ValueError("Stripe Payments demo requires a test-mode secret key")
        self._secret_key = secret_key
        self._api_url = api_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_checkout_session(
        self,
        *,
        execution_id: UUID,
        title: str,
        quantity: int,
        unit_amount_minor: int,
        currency: str,
        collect_phone: bool,
    ) -> StripeHostedSession:
        """Create an idempotent live-hosted Stripe test checkout for approved facts."""
        if quantity < 1 or unit_amount_minor < 1:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        safe_title = " ".join(title.split())
        if not safe_title or len(safe_title) > 127:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        data: dict[str, str] = {
            "mode": "payment",
            "payment_method_types[]": "card",
            "client_reference_id": str(execution_id),
            "metadata[agpay_execution_id]": str(execution_id),
            "payment_intent_data[metadata][agpay_execution_id]": str(execution_id),
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": str(unit_amount_minor),
            "line_items[0][price_data][product_data][name]": safe_title,
            "line_items[0][quantity]": str(quantity),
            "billing_address_collection": "required",
            "submit_type": "pay",
            "success_url": (
                "https://example.com/?agpay_checkout=complete&session_id={CHECKOUT_SESSION_ID}"
            ),
            "cancel_url": "https://example.com/?agpay_checkout=cancelled",
        }
        if collect_phone:
            data["phone_number_collection[enabled]"] = "true"
        try:
            response = await self._client.post(
                f"{self._api_url}/v1/checkout/sessions",
                headers={
                    "Authorization": f"Bearer {self._secret_key}",
                    "Idempotency-Key": f"agpay-hosted-{execution_id}",
                },
                data=data,
            )
            response.raise_for_status()
            payload = response.json()
            session_id = str(payload["id"])
            checkout_url = str(payload["url"])
            response_valid = (
                payload["livemode"] is False
                and payload["amount_total"] == unit_amount_minor * quantity
                and str(payload["currency"]).upper() == currency.upper()
                and payload["client_reference_id"] == str(execution_id)
                and isinstance(payload["metadata"], dict)
                and payload["metadata"].get("agpay_execution_id") == str(execution_id)
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise CheckoutError(
                CheckoutErrorCode.browser_navigation_failed,
                retryable=True,
            ) from None
        parsed = urlsplit(checkout_url)
        if (
            CHECKOUT_SESSION_PATTERN.fullmatch(session_id) is None
            or not response_valid
            or parsed.scheme != "https"
            or parsed.hostname != "checkout.stripe.com"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise CheckoutError(CheckoutErrorCode.browser_navigation_failed)
        del payload
        return StripeHostedSession(session_id=session_id, checkout_url=checkout_url)

    async def verify_checkout_session(
        self,
        *,
        session_id: str,
        execution_id: UUID,
        amount_minor: int,
        currency: str,
    ) -> StripeHostedVerification:
        if CHECKOUT_SESSION_PATTERN.fullmatch(session_id) is None:
            raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)
        try:
            response = await self._client.get(
                f"{self._api_url}/v1/checkout/sessions/{session_id}",
                headers={"Authorization": f"Bearer {self._secret_key}"},
                params=[
                    ("expand[]", "payment_intent"),
                    ("expand[]", "payment_intent.latest_charge"),
                ],
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, TypeError, ValueError):
            raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown) from None
        try:
            session_valid = (
                payload["id"] == session_id
                and payload["livemode"] is False
                and type(payload["amount_total"]) is int
                and payload["amount_total"] == amount_minor
                and str(payload["currency"]).upper() == currency.upper()
                and payload["client_reference_id"] == str(execution_id)
                and isinstance(payload["metadata"], dict)
                and payload["metadata"].get("agpay_execution_id") == str(execution_id)
            )
            intent = payload.get("payment_intent")
        except (KeyError, TypeError, ValueError):
            session_valid = False
            intent = None
        if not session_valid or not isinstance(intent, dict):
            return StripeHostedVerification(AuthorizationOutcome.unknown)
        try:
            intent_id = str(intent["id"])
            intent_valid = (
                PAYMENT_INTENT_PATTERN.fullmatch(intent_id) is not None
                and intent["livemode"] is False
                and type(intent["amount"]) is int
                and intent["amount"] == amount_minor
                and str(intent["currency"]).upper() == currency.upper()
                and isinstance(intent["metadata"], dict)
                and intent["metadata"].get("agpay_execution_id") == str(execution_id)
            )
            status = str(intent["status"])
            last_error = intent.get("last_payment_error")
            latest_charge = intent.get("latest_charge")
        except (KeyError, TypeError, ValueError):
            intent_valid = False
            intent_id = ""
            status = ""
            last_error = None
            latest_charge = None
        del payload, intent
        if not intent_valid:
            return StripeHostedVerification(AuthorizationOutcome.unknown)
        if status == "succeeded":
            return StripeHostedVerification(
                AuthorizationOutcome.approved,
                provider_reference=intent_id,
                receipt_url=self._safe_receipt_url(latest_charge),
            )
        if status == "requires_payment_method" and isinstance(last_error, dict):
            return StripeHostedVerification(AuthorizationOutcome.declined)
        if status == "requires_action":
            return StripeHostedVerification(AuthorizationOutcome.action_required)
        return StripeHostedVerification(AuthorizationOutcome.unknown)

    async def expire_checkout_session(self, session_id: str, execution_id: UUID) -> bool:
        if CHECKOUT_SESSION_PATTERN.fullmatch(session_id) is None:
            return False
        try:
            response = await self._client.post(
                f"{self._api_url}/v1/checkout/sessions/{session_id}/expire",
                headers={"Authorization": f"Bearer {self._secret_key}"},
            )
            response.raise_for_status()
            payload = response.json()
            expired = (
                payload["id"] == session_id
                and payload["livemode"] is False
                and payload["status"] == "expired"
                and payload["payment_status"] == "unpaid"
                and payload["client_reference_id"] == str(execution_id)
                and isinstance(payload["metadata"], dict)
                and payload["metadata"].get("agpay_execution_id") == str(execution_id)
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return False
        return expired

    @staticmethod
    def _safe_receipt_url(latest_charge: object) -> str | None:
        if not isinstance(latest_charge, dict):
            return None
        value = latest_charge.get("receipt_url")
        if not isinstance(value, str):
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "pay.stripe.com"
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        return value

    async def verify_payment_intent(
        self,
        *,
        payment_intent_id: str,
        execution_id: UUID,
        amount_minor: int,
        currency: str,
    ) -> AuthorizationResult:
        if PAYMENT_INTENT_PATTERN.fullmatch(payment_intent_id) is None:
            raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)
        try:
            response = await self._client.get(
                f"{self._api_url}/v1/payment_intents/{payment_intent_id}",
                headers={"Authorization": f"Bearer {self._secret_key}"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown) from None
        try:
            valid = (
                payload["id"] == payment_intent_id
                and payload["livemode"] is False
                and type(payload["amount"]) is int
                and payload["amount"] == amount_minor
                and str(payload["currency"]).upper() == currency.upper()
                and isinstance(payload["metadata"], dict)
                and payload["metadata"].get("agpay_execution_id") == str(execution_id)
            )
            status = str(payload["status"])
            last_error = payload.get("last_payment_error")
        except (KeyError, TypeError, ValueError):
            valid = False
            status = ""
            last_error = None
        del payload
        if not valid:
            return AuthorizationResult(AuthorizationOutcome.unknown)
        if status == "succeeded":
            return AuthorizationResult(AuthorizationOutcome.approved, payment_intent_id)
        if status == "requires_payment_method" and isinstance(last_error, dict):
            return AuthorizationResult(AuthorizationOutcome.declined)
        if status == "requires_action":
            return AuthorizationResult(AuthorizationOutcome.action_required)
        return AuthorizationResult(AuthorizationOutcome.unknown)
