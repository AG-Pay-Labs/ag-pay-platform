import re
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


class StripePaymentsDemoGateway:
    """Development-only Stripe Payments fixture provider.

    Public Stripe test values are materialized only in the worker process. AG Pay stores
    and exposes the safe pm_stripe_demo_* reference, never the test card values.
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
