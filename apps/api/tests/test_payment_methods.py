from datetime import UTC, datetime
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from helpers import (
    API,
    bearer,
    business_payment_method,
    create_agent,
    create_payment_method,
    personal_payment_method,
    register_user,
)
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ag_platform_api.models import PaymentMethod, StoredCardCredential
from ag_platform_api.services.checkout.direct_card import (
    DirectCardPanCipher,
    LocalDirectCardGateway,
    card_brand,
)
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.types import ExpectedCardMetadata, is_card_expired


def direct_card_payload(card_number: str = "4242424242424242") -> dict[str, object]:
    payload = personal_payment_method("pm_unused")
    return {
        "display_name": "Local research card",
        "card_number": card_number,
        "expiry_month": payload["expiry_month"],
        "expiry_year": payload["expiry_year"],
        "billing_details": payload["billing_details"],
    }


@pytest.mark.parametrize(
    ("card_number", "expected_brand"),
    [
        ("4242424242424242", "visa"),
        ("378282246310005", "amex"),
        ("5555555555554444", "mastercard"),
        ("6011111111111117", "discover"),
        ("9900000000000002", "unknown"),
    ],
)
def test_direct_card_brand_is_best_effort_and_unknown_iins_remain_supported(
    card_number: str,
    expected_brand: str,
) -> None:
    assert card_brand(card_number) == expected_brand


def test_card_expiry_uses_calendar_month_boundaries() -> None:
    boundary = datetime(2030, 1, 1, tzinfo=UTC)

    assert is_card_expired(12, 2029, at=boundary)
    assert not is_card_expired(1, 2030, at=boundary)
    assert not is_card_expired(2, 2030, at=boundary)


async def test_direct_card_enrollment_stores_only_encrypted_pan_and_safe_metadata(
    client: AsyncClient,
    settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = Fernet.generate_key().decode()
    settings.checkout_enabled = True
    settings.local_direct_card_enabled = True
    settings.direct_card_encryption_key = SecretStr(key)
    user_token = await register_user(client, "direct-card-owner")

    response = await client.post(
        f"{API}/payment-methods/direct-card",
        headers=bearer(user_token),
        json=direct_card_payload(),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider"] == "local_direct_card"
    assert body["card_last4"] == "4242"
    assert body["card_brand"] == "visa"
    assert "card_number" not in body
    assert "encrypted_pan" not in body
    assert "4242424242424242" not in response.text
    async with db_session_factory() as db:
        row = (
            await db.execute(
                select(PaymentMethod, StoredCardCredential)
                .join(StoredCardCredential)
                .where(PaymentMethod.id == UUID(body["id"]))
            )
        ).one_or_none()
    assert row is not None
    payment_method, credential = row
    assert credential is not None
    assert credential.encrypted_pan != "4242424242424242"
    assert "4242424242424242" not in credential.encrypted_pan
    assert (
        DirectCardPanCipher(key).decrypt(
            credential.encrypted_pan,
            owner_id=payment_method.owner_id,
            payment_method_id=payment_method.id,
            provider_card_id=payment_method.provider_payment_method_id,
        )
        == "4242424242424242"
    )
    with pytest.raises(CheckoutError):
        DirectCardPanCipher(key).decrypt(
            credential.encrypted_pan,
            owner_id=UUID(int=0),
            payment_method_id=payment_method.id,
            provider_card_id=payment_method.provider_payment_method_id,
        )

    listed = await client.get(f"{API}/payment-methods", headers=bearer(user_token))
    assert listed.status_code == 200
    assert "4242424242424242" not in listed.text
    assert "encrypted_pan" not in listed.text

    disabled = await client.delete(
        f"{API}/payment-methods/{body['id']}", headers=bearer(user_token)
    )
    assert disabled.status_code == 204
    async with db_session_factory() as db:
        assert (await db.get(StoredCardCredential, UUID(body["id"]))) is None


async def test_direct_card_gateway_rejects_expiry_before_decryption(
    client: AsyncClient,
    settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    key = Fernet.generate_key().decode()
    settings.checkout_enabled = True
    settings.local_direct_card_enabled = True
    settings.direct_card_encryption_key = SecretStr(key)
    user_token = await register_user(client, "direct-card-expired-gateway")
    response = await client.post(
        f"{API}/payment-methods/direct-card",
        headers=bearer(user_token),
        json=direct_card_payload(),
    )
    assert response.status_code == 201, response.text
    method_id = UUID(response.json()["id"])
    async with db_session_factory() as db, db.begin():
        payment_method = await db.get(PaymentMethod, method_id)
        assert payment_method is not None
        payment_method.expiry_month = 12
        payment_method.expiry_year = 2020
        owner_id = payment_method.owner_id
        provider_card_id = payment_method.provider_payment_method_id

    gateway = LocalDirectCardGateway(db_session_factory, encryption_key=key)
    with pytest.raises(CheckoutError) as caught:
        await gateway.retrieve_pan(
            owner_id=owner_id,
            payment_method_id=method_id,
            provider_card_id=provider_card_id,
            expected=ExpectedCardMetadata(
                owner_id=owner_id,
                last4="4242",
                brand="visa",
                expiry_month=12,
                expiry_year=2020,
            ),
        )

    assert caught.value.code == CheckoutErrorCode.payment_method_expired


@pytest.mark.parametrize("card_number", ["4242424242424241", "1234", "not-a-card"])
async def test_direct_card_enrollment_rejects_invalid_pan_without_persistence(
    client: AsyncClient,
    settings,
    db_session_factory: async_sessionmaker[AsyncSession],
    card_number: str,
) -> None:
    settings.checkout_enabled = True
    settings.local_direct_card_enabled = True
    settings.direct_card_encryption_key = SecretStr(Fernet.generate_key().decode())
    user_token = await register_user(client, f"invalid-direct-{abs(hash(card_number))}")

    response = await client.post(
        f"{API}/payment-methods/direct-card",
        headers=bearer(user_token),
        json=direct_card_payload(card_number),
    )

    assert response.status_code == 422
    assert card_number not in response.text
    async with db_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(StoredCardCredential)) == 0


async def test_direct_card_endpoint_never_accepts_cvc(
    client: AsyncClient,
    settings,
) -> None:
    settings.checkout_enabled = True
    settings.local_direct_card_enabled = True
    settings.direct_card_encryption_key = SecretStr(Fernet.generate_key().decode())
    user_token = await register_user(client, "direct-card-no-cvc")
    payload = direct_card_payload()
    payload["cvc"] = "123"

    response = await client.post(
        f"{API}/payment-methods/direct-card",
        headers=bearer(user_token),
        json=payload,
    )

    assert response.status_code == 422
    assert "123" not in response.text


@pytest.mark.parametrize(
    ("provider", "reference"),
    [
        ("prototype-vault", "123"),
        ("prototype-vault", "4242424242424242"),
        ("prototype-vault", "pm_card-4242-4242-4242-4242"),
        ("prototype-vault", "pm_card-123"),
        ("external-vault", "card_reference_safe_shape"),
        ("stripe_issuing", "pm_not_an_issuing_card"),
    ],
)
async def test_raw_or_provider_mismatched_references_are_rejected_without_persistence(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    provider: str,
    reference: str,
) -> None:
    user_token = await register_user(client, f"unsafe-reference-{abs(hash(reference))}")
    payload = personal_payment_method(reference)
    payload["provider"] = provider

    response = await client.post(
        f"{API}/payment-methods",
        headers=bearer(user_token),
        json=payload,
    )

    assert response.status_code == 422
    assert reference not in response.text
    async with db_session_factory() as db:
        count = await db.scalar(select(func.count()).select_from(PaymentMethod))
    assert count == 0


async def test_personal_and_business_cards_can_be_shared_across_owned_agents(
    client: AsyncClient,
) -> None:
    user_token = await register_user(client, "card-owner")
    first_agent = await create_agent(client, user_token, name="First agent")
    second_agent = await create_agent(client, user_token, name="Second agent")

    personal = await create_payment_method(
        client,
        user_token,
        payload=personal_payment_method("pm_personal_shared"),
    )
    assert personal["billing_profile_type"] == "personal"
    assert personal["billing_details"]["full_name"] == "Alex Example"
    assert personal["billing_details"]["address"]["country"] == "ES"
    assert personal["card_brand"] == "visa"
    assert "provider_payment_method_id" not in personal

    business = await create_payment_method(
        client,
        user_token,
        payload=business_payment_method("pm_business_shared"),
    )
    assert business["billing_profile_type"] == "business"
    assert business["billing_details"]["vat_number"] == "ESB12345678"

    for agent in (first_agent, second_agent):
        assignment = await client.put(
            f"{API}/agents/{agent['id']}/payment-methods/{personal['id']}",
            headers=bearer(user_token),
        )
        assert assignment.status_code == 204

    # Assignment is idempotent.
    duplicate_assignment = await client.put(
        f"{API}/agents/{first_agent['id']}/payment-methods/{personal['id']}",
        headers=bearer(user_token),
    )
    assert duplicate_assignment.status_code == 204

    for agent in (first_agent, second_agent):
        listed = await client.get(
            f"{API}/agents/{agent['id']}/payment-methods",
            headers=bearer(user_token),
        )
        assert listed.status_code == 200
        assert [method["id"] for method in listed.json()] == [personal["id"]]

    duplicate_card = await client.post(
        f"{API}/payment-methods",
        headers=bearer(user_token),
        json=personal_payment_method("pm_personal_shared"),
    )
    assert duplicate_card.status_code == 409

    unsafe_payload = personal_payment_method("pm_raw_card_rejected")
    unsafe_payload["card_number"] = "test-pan-must-not-be-accepted"
    unsafe_payload["cvc"] = "test-cvc-must-not-be-accepted"
    raw_card = await client.post(
        f"{API}/payment-methods",
        headers=bearer(user_token),
        json=unsafe_payload,
    )
    assert raw_card.status_code == 422
    assert {error["loc"][-1] for error in raw_card.json()["detail"]} == {
        "card_number",
        "cvc",
    }
    assert "test-pan-must-not-be-accepted" not in raw_card.text
    assert "test-cvc-must-not-be-accepted" not in raw_card.text
    assert all("input" not in error and "ctx" not in error for error in raw_card.json()["detail"])
