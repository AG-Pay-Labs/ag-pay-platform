import re
from collections.abc import Mapping
from datetime import datetime

import httpx

from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.types import (
    AuthorizationOutcome,
    AuthorizationResult,
    ExpectedCardMetadata,
    IssuingCardSecret,
)

CARD_REFERENCE_PATTERN = re.compile(r"^ic_[A-Za-z0-9]+$")
AUTHORIZATION_REFERENCE_PATTERN = re.compile(r"^iauth_[A-Za-z0-9]+$")


class StripeIssuingGateway:
    def __init__(
        self,
        *,
        secret_key: str,
        api_url: str = "https://api.stripe.com",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._secret_key = secret_key
        self._api_url = api_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def retrieve_card(
        self,
        card_id: str,
        expected: ExpectedCardMetadata,
    ) -> IssuingCardSecret:
        self._validate_card_reference(card_id)
        payload = await self._get_json(
            f"/v1/issuing/cards/{card_id}",
            params=(("expand[]", "number"), ("expand[]", "cvc")),
            error_code=CheckoutErrorCode.card_unavailable,
        )
        try:
            card_is_safe = (
                payload["id"] == card_id
                and payload["status"] == "active"
                and payload["type"] == "virtual"
                and str(payload["last4"]) == expected.last4
                and str(payload["brand"]).casefold() == expected.brand.casefold()
                and int(payload["exp_month"]) == expected.expiry_month
                and int(payload["exp_year"]) == expected.expiry_year
                and isinstance(payload["metadata"], dict)
                and payload["metadata"].get("agpay_owner_id") == str(expected.owner_id)
            )
            number = str(payload["number"])
            cvc = str(payload["cvc"])
            expiry_month = int(payload["exp_month"])
            expiry_year = int(payload["exp_year"])
        except (KeyError, TypeError, ValueError):
            raise CheckoutError(CheckoutErrorCode.card_unavailable) from None
        if (
            not card_is_safe
            or not number.isascii()
            or not number.isdigit()
            or not 12 <= len(number) <= 19
            or not cvc.isascii()
            or not cvc.isdigit()
            or len(cvc) not in {3, 4}
        ):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        secret = IssuingCardSecret(number, cvc, expiry_month, expiry_year)
        del payload, number, cvc
        return secret

    async def find_authorization(
        self,
        *,
        card_id: str,
        created_at_or_after: datetime,
        amount_minor: int,
        currency: str,
        excluded_authorization_ids: frozenset[str] = frozenset(),
    ) -> AuthorizationResult:
        self._validate_card_reference(card_id)
        payload = await self._get_json(
            "/v1/issuing/authorizations",
            params={
                "card": card_id,
                "created[gte]": int(created_at_or_after.timestamp()),
                "limit": 100,
            },
            error_code=CheckoutErrorCode.payment_outcome_unknown,
        )
        try:
            records = payload["data"]
            if not isinstance(records, list):
                raise TypeError
        except (KeyError, TypeError):
            raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown) from None

        if payload.get("has_more") is True:
            return AuthorizationResult(AuthorizationOutcome.unknown)

        exact: list[tuple[bool, str]] = []
        expected_currency = currency.upper()
        for record in records:
            try:
                reference = str(record["id"])
                approved_value = record["approved"]
                amount_value = record["amount"]
                currency_value = record["currency"]
                record_card = record["card"]
                record_card_id = (
                    str(record_card["id"]) if isinstance(record_card, dict) else str(record_card)
                )
            except (KeyError, TypeError, ValueError):
                continue
            if (
                type(approved_value) is not bool
                or type(amount_value) is not int
                or not isinstance(currency_value, str)
            ):
                continue

            merchant_amount_present = "merchant_amount" in record
            merchant_currency_present = "merchant_currency" in record
            if merchant_amount_present != merchant_currency_present:
                continue
            if merchant_amount_present:
                merchant_amount_value = record["merchant_amount"]
                merchant_currency_value = record["merchant_currency"]
                if type(merchant_amount_value) is not int or not isinstance(
                    merchant_currency_value, str
                ):
                    continue
                amount_matches = (
                    merchant_amount_value == amount_minor
                    and merchant_currency_value.upper() == expected_currency
                )
            else:
                amount_matches = (
                    amount_value == amount_minor and currency_value.upper() == expected_currency
                )
            if (
                AUTHORIZATION_REFERENCE_PATTERN.fullmatch(reference)
                and reference not in excluded_authorization_ids
                and record_card_id == card_id
                and amount_matches
            ):
                exact.append((approved_value, reference))
        del payload, records

        if len(exact) != 1:
            return AuthorizationResult(AuthorizationOutcome.unknown)
        approved, reference = exact[0]
        if approved:
            return AuthorizationResult(AuthorizationOutcome.approved, reference)
        return AuthorizationResult(AuthorizationOutcome.declined)

    async def snapshot_authorization_ids(
        self,
        *,
        card_id: str,
        created_at_or_after: datetime,
    ) -> frozenset[str]:
        """Capture authorizations that predate this worker's irreversible submit."""
        self._validate_card_reference(card_id)
        payload = await self._get_json(
            "/v1/issuing/authorizations",
            params={
                "card": card_id,
                "created[gte]": int(created_at_or_after.timestamp()),
                "limit": 100,
            },
            error_code=CheckoutErrorCode.authorization_snapshot_failed,
        )
        try:
            records = payload["data"]
            if not isinstance(records, list) or payload.get("has_more") is True:
                raise TypeError
            references: set[str] = set()
            for record in records:
                reference = str(record["id"])
                record_card = record["card"]
                record_card_id = (
                    str(record_card["id"]) if isinstance(record_card, dict) else str(record_card)
                )
                if (
                    not AUTHORIZATION_REFERENCE_PATTERN.fullmatch(reference)
                    or record_card_id != card_id
                ):
                    raise ValueError
                references.add(reference)
        except (KeyError, TypeError, ValueError):
            raise CheckoutError(
                CheckoutErrorCode.authorization_snapshot_failed,
                retryable=True,
            ) from None
        del payload, records
        return frozenset(references)

    async def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str | int] | tuple[tuple[str, str], ...],
        error_code: CheckoutErrorCode,
    ) -> dict[str, object]:
        try:
            response = await self._client.get(
                f"{self._api_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._secret_key}"},
            )
        except httpx.HTTPError:
            raise CheckoutError(error_code, retryable=True) from None
        if response.status_code < 200 or response.status_code >= 300:
            raise CheckoutError(error_code, retryable=True)
        try:
            payload = response.json()
        except ValueError:
            raise CheckoutError(error_code, retryable=True) from None
        if not isinstance(payload, dict):
            raise CheckoutError(error_code, retryable=True)
        return payload

    @staticmethod
    def _validate_card_reference(card_id: str) -> None:
        if not CARD_REFERENCE_PATTERN.fullmatch(card_id):
            raise CheckoutError(CheckoutErrorCode.card_reference_invalid)
