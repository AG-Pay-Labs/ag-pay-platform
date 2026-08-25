from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ag_platform_api.core.config import LOCAL_DIRECT_CARD_PROVIDER
from ag_platform_api.models import (
    PaymentMethod,
    PaymentMethodStatus,
    StoredCardCredential,
)
from ag_platform_api.schemas import normalize_card_number
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.types import ExpectedCardMetadata, is_card_expired


@dataclass(frozen=True, slots=True, repr=False)
class DirectCardPan:
    number: str
    expiry_month: int
    expiry_year: int

    def __repr__(self) -> str:
        return "DirectCardPan(<redacted>)"


def card_brand(number: str) -> str:
    """Return best-effort display metadata without rejecting an unfamiliar IIN."""
    if number.startswith("4"):
        return "visa"
    if number.startswith(("34", "37")):
        return "amex"
    first_two = int(number[:2])
    first_four = int(number[:4])
    if 51 <= first_two <= 55 or 2221 <= first_four <= 2720:
        return "mastercard"
    first_six = int(number[:6])
    if (
        number.startswith("6011")
        or number.startswith("65")
        or 644 <= int(number[:3]) <= 649
        or 622126 <= first_six <= 622925
    ):
        return "discover"
    if 3528 <= first_four <= 3589:
        return "jcb"
    if number.startswith("62"):
        return "unionpay"
    first_three = int(number[:3])
    if 300 <= first_three <= 305 or number.startswith(("36", "38", "39")):
        return "diners"
    return "unknown"


class DirectCardPanCipher:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(
        self,
        number: str,
        *,
        owner_id: UUID,
        payment_method_id: UUID,
        provider_card_id: str,
    ) -> str:
        normalized = normalize_card_number(number)
        plaintext = json.dumps(
            {
                "v": 1,
                "owner_id": str(owner_id),
                "payment_method_id": str(payment_method_id),
                "provider_card_id": provider_card_id,
                "pan": normalized,
            },
            separators=(",", ":"),
        )
        try:
            return self._fernet.encrypt(plaintext.encode()).decode()
        finally:
            del plaintext, normalized

    def decrypt(
        self,
        ciphertext: str,
        *,
        owner_id: UUID,
        payment_method_id: UUID,
        provider_card_id: str,
    ) -> str:
        try:
            payload = json.loads(self._fernet.decrypt(ciphertext.encode()))
            if (
                not isinstance(payload, dict)
                or set(payload) != {"v", "owner_id", "payment_method_id", "provider_card_id", "pan"}
                or payload["v"] != 1
                or payload["owner_id"] != str(owner_id)
                or payload["payment_method_id"] != str(payment_method_id)
                or payload["provider_card_id"] != provider_card_id
                or not isinstance(payload["pan"], str)
            ):
                raise ValueError
            number = normalize_card_number(payload["pan"])
            del payload
            return number
        except (InvalidToken, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
            raise CheckoutError(CheckoutErrorCode.card_unavailable) from None


class LocalDirectCardGateway:
    """Loads an owner-bound encrypted PAN immediately before deterministic filling."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        encryption_key: str,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = DirectCardPanCipher(encryption_key)

    async def retrieve_pan(
        self,
        *,
        owner_id: UUID,
        payment_method_id: UUID,
        provider_card_id: str,
        expected: ExpectedCardMetadata,
    ) -> DirectCardPan:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(PaymentMethod, StoredCardCredential)
                    .join(
                        StoredCardCredential,
                        StoredCardCredential.payment_method_id == PaymentMethod.id,
                    )
                    .where(
                        PaymentMethod.id == payment_method_id,
                        PaymentMethod.owner_id == owner_id,
                        PaymentMethod.status == PaymentMethodStatus.active,
                        PaymentMethod.provider == LOCAL_DIRECT_CARD_PROVIDER,
                        PaymentMethod.provider_payment_method_id == provider_card_id,
                        StoredCardCredential.owner_id == owner_id,
                    )
                )
            ).one_or_none()
        if row is None:
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        payment_method, credential = row
        if (
            expected.owner_id != owner_id
            or payment_method.card_last4 != expected.last4
            or payment_method.card_brand != expected.brand
            or payment_method.expiry_month != expected.expiry_month
            or payment_method.expiry_year != expected.expiry_year
        ):
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        if is_card_expired(payment_method.expiry_month, payment_method.expiry_year):
            raise CheckoutError(CheckoutErrorCode.payment_method_expired)
        number = self._cipher.decrypt(
            credential.encrypted_pan,
            owner_id=owner_id,
            payment_method_id=payment_method_id,
            provider_card_id=provider_card_id,
        )
        if number[-4:] != expected.last4 or card_brand(number) != expected.brand:
            raise CheckoutError(CheckoutErrorCode.card_unavailable)
        return DirectCardPan(
            number=number,
            expiry_month=expected.expiry_month,
            expiry_year=expected.expiry_year,
        )
