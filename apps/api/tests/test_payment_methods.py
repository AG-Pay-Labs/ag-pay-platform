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
