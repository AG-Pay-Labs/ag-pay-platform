from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ag_platform_api.core.config import (
    STRIPE_HOSTED_TEST_ADAPTER_KEY,
    Settings,
    stripe_hosted_test_adapter,
)
from ag_platform_api.services.checkout.errors import CheckoutError
from ag_platform_api.services.checkout.repository import (
    HostedPaymentProof,
    HostedReconciliationCandidate,
    SqlAlchemyCheckoutRepository,
    TerminalNotification,
)

PAYMENT_VERIFICATION_ENDPOINT = "https://letyouragentspay.com/api/payment-playground/verification"
MAX_VERIFICATION_RESPONSE_BYTES = 32 * 1024
VERIFICATION_TIMEOUT = httpx.Timeout(8.0, connect=5.0)


class ReconciliationErrorCode(StrEnum):
    not_found = "not_found"
    unavailable = "unavailable"
    not_verified = "not_verified"
    invalid_state = "invalid_state"


SAFE_RECONCILIATION_MESSAGES: dict[ReconciliationErrorCode, str] = {
    ReconciliationErrorCode.not_found: "Cart item not found",
    ReconciliationErrorCode.unavailable: (
        "Trusted payment verification is temporarily unavailable."
    ),
    ReconciliationErrorCode.not_verified: ("Trusted payment proof does not verify this checkout."),
    ReconciliationErrorCode.invalid_state: (
        "This checkout is not eligible for payment reconciliation."
    ),
}


@dataclass
class ReconciliationError(Exception):
    code: ReconciliationErrorCode

    @property
    def safe_message(self) -> str:
        return SAFE_RECONCILIATION_MESSAGES[self.code]

    def __str__(self) -> str:
        return self.safe_message


SessionId = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^cs_test_[A-Za-z0-9]+$", max_length=255),
]
Currency = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[A-Za-z]{3}$", min_length=3, max_length=3),
]


class _VerifiedOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    slug: Annotated[
        str,
        StringConstraints(
            strict=True,
            min_length=1,
            max_length=128,
            pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        ),
    ]
    name: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]


class _VerifiedPaymentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verified: Literal[True]
    session_id: SessionId = Field(alias="sessionId")
    order_reference: SessionId = Field(alias="orderReference")
    offer: _VerifiedOffer
    amount_minor: int = Field(alias="amountMinor", strict=True, gt=0)
    currency: Currency


class TrustedPaymentVerifier(Protocol):
    async def verify(
        self,
        candidate: HostedReconciliationCandidate,
    ) -> HostedPaymentProof: ...


class LandingPaymentVerificationClient:
    """Read paid-session proof from one pinned, server-verified sandbox endpoint."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def verify(
        self,
        candidate: HostedReconciliationCandidate,
    ) -> HostedPaymentProof:
        try:
            async with self._client.stream(
                "POST",
                PAYMENT_VERIFICATION_ENDPOINT,
                json={"sessionId": candidate.stripe_session_id},
                headers={"Accept": "application/json"},
                follow_redirects=False,
                timeout=VERIFICATION_TIMEOUT,
            ) as response:
                body = await self._bounded_body(response)
                status_code = response.status_code
                response_url = response.url
                response_history = response.history
                content_type = response.headers.get("content-type", "")
                cache_control = response.headers.get("cache-control", "")
        except (httpx.HTTPError, ReconciliationError):
            raise ReconciliationError(ReconciliationErrorCode.unavailable) from None

        if status_code in {400, 404}:
            raise ReconciliationError(ReconciliationErrorCode.not_verified)
        if status_code != 200:
            code = (
                ReconciliationErrorCode.unavailable
                if status_code >= 500 or status_code == 429
                else ReconciliationErrorCode.not_verified
            )
            raise ReconciliationError(code)
        if (
            response_url != httpx.URL(PAYMENT_VERIFICATION_ENDPOINT)
            or response_history
            or content_type.split(";", 1)[0].strip().lower() != "application/json"
            or "no-store"
            not in {directive.strip().lower() for directive in cache_control.split(",")}
        ):
            raise ReconciliationError(ReconciliationErrorCode.unavailable)

        try:
            raw_payload = json.loads(body)
            verified = _VerifiedPaymentResponse.model_validate(raw_payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError):
            raise ReconciliationError(ReconciliationErrorCode.not_verified) from None
        finally:
            if "raw_payload" in locals():
                del raw_payload

        proof = HostedPaymentProof(
            session_id=verified.session_id,
            order_reference=verified.order_reference,
            offer_slug=verified.offer.slug,
            offer_name=verified.offer.name,
            amount_minor=verified.amount_minor,
            currency=verified.currency,
        )
        if not SqlAlchemyCheckoutRepository.proof_matches_candidate(proof, candidate):
            raise ReconciliationError(ReconciliationErrorCode.not_verified)
        return proof

    @staticmethod
    async def _bounded_body(response: httpx.Response) -> bytes:
        declared_length = response.headers.get("content-length")
        if declared_length is not None:
            try:
                parsed_length = int(declared_length)
                if parsed_length < 0 or parsed_length > MAX_VERIFICATION_RESPONSE_BYTES:
                    raise ReconciliationError(ReconciliationErrorCode.unavailable)
            except ValueError:
                raise ReconciliationError(ReconciliationErrorCode.unavailable) from None
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_VERIFICATION_RESPONSE_BYTES:
                raise ReconciliationError(ReconciliationErrorCode.unavailable)
            chunks.append(chunk)
        return b"".join(chunks)


class CheckoutReconciliationService:
    def __init__(
        self,
        *,
        repository: SqlAlchemyCheckoutRepository,
        verifier: TrustedPaymentVerifier,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._verifier = verifier
        self._settings = settings

    async def reconcile(
        self,
        *,
        owner_id: UUID,
        cart_item_id: UUID,
    ) -> TerminalNotification | None:
        if not self._feature_enabled():
            raise ReconciliationError(ReconciliationErrorCode.invalid_state)
        try:
            candidate = await self._repository.hosted_reconciliation_candidate(
                owner_id=owner_id,
                cart_item_id=cart_item_id,
            )
        except CheckoutError:
            raise ReconciliationError(ReconciliationErrorCode.invalid_state) from None
        if candidate is None:
            raise ReconciliationError(ReconciliationErrorCode.not_found)
        if candidate.already_succeeded:
            return None
        proof = await self._verifier.verify(candidate)
        try:
            return await self._repository.reconcile_hosted_succeeded(candidate, proof)
        except CheckoutError:
            raise ReconciliationError(ReconciliationErrorCode.invalid_state) from None

    def _feature_enabled(self) -> bool:
        return (
            self._settings.environment.lower() in {"development", "test"}
            and self._settings.checkout_enabled
            and self._settings.checkout_demo_enabled
            and self._settings.checkout_hosted_demo_enabled
            and self._settings.checkout_adapters.get(STRIPE_HOSTED_TEST_ADAPTER_KEY)
            == stripe_hosted_test_adapter()
        )
