import pytest
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ag_platform_api.models import PaymentMethod


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
