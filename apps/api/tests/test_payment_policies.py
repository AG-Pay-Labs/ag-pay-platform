from typing import Any
from uuid import UUID

import pytest
from helpers import (
    API,
    assign_payment_method,
    bearer,
    cart_item_payload,
    connect_agent,
    create_agent,
    create_payment_method,
    personal_payment_method,
    register_user,
)
from httpx import AsyncClient


async def update_policy(
    client: AsyncClient,
    user_token: str,
    agent_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = await client.patch(
        f"{API}/agents/{agent_id}/payment-policy",
        headers=bearer(user_token),
        json=payload,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def policy_wallet(
    client: AsyncClient,
    username: str,
    *,
    assign_card: bool = True,
) -> dict[str, str]:
    user_token = await register_user(client, username)
    created_agent = await create_agent(client, user_token)
    connected_agent = await connect_agent(client, created_agent["pairing_token"])
    result = {
        "user_token": user_token,
        "agent_id": created_agent["id"],
        "agent_token": connected_agent["agent_access_token"],
    }
    if assign_card:
        payment_method = await create_payment_method(
            client,
            user_token,
            payload=personal_payment_method(f"pm_{username}"),
        )
        await assign_payment_method(
            client,
            user_token,
            created_agent["id"],
            payment_method["id"],
        )
        result["payment_method_id"] = payment_method["id"]
    return result


async def test_payment_policy_list_includes_every_agent_with_persisted_defaults(
    client: AsyncClient,
) -> None:
    user_token = await register_user(client, "policy-list-owner")
    first_agent = await create_agent(client, user_token, name="First policy agent")
    second_agent = await create_agent(client, user_token, name="Second policy agent")

    first_response = await client.get(
        f"{API}/payment-policies",
        headers=bearer(user_token),
    )
    assert first_response.status_code == 200
    first_policies = first_response.json()
    assert {policy["agent_id"] for policy in first_policies} == {
        first_agent["id"],
        second_agent["id"],
    }
    assert {policy["mode"] for policy in first_policies} == {"always"}
    assert all(policy["threshold_amount"] is None for policy in first_policies)
    assert all(policy["threshold_currency"] is None for policy in first_policies)

    second_response = await client.get(
        f"{API}/payment-policies",
        headers=bearer(user_token),
    )
    assert second_response.status_code == 200
    assert {policy["agent_id"]: policy["id"] for policy in second_response.json()} == {
        policy["agent_id"]: policy["id"] for policy in first_policies
    }


@pytest.mark.parametrize(
    ("mode", "recurring", "amount", "threshold", "expected_status"),
    [
        ("always", False, "10.00", None, "proposed"),
        ("subscriptions_only", True, "10.00", None, "proposed"),
        ("subscriptions_only", False, "10.00", None, "approved"),
        ("never", True, "10.00", None, "approved"),
        ("above_amount", False, "10.00", "10.00", "approved"),
        ("above_amount", False, "10.00", "9.99", "proposed"),
        ("subscriptions_or_above_amount", True, "10.00", "999.00", "proposed"),
        ("subscriptions_or_above_amount", False, "10.00", "10.00", "approved"),
    ],
)
async def test_policy_modes_control_auto_approval(
    client: AsyncClient,
    broker: Any,
    mode: str,
    recurring: bool,
    amount: str,
    threshold: str | None,
    expected_status: str,
) -> None:
    case_name = f"{mode}-{str(recurring).lower()}-{amount.replace('.', '-')}"
    wallet = await policy_wallet(client, f"policy-{case_name}")
    policy_payload: dict[str, Any] = {"mode": mode}
    if threshold is not None:
        policy_payload.update(threshold_amount=threshold, threshold_currency="eur")
    await update_policy(
        client,
        wallet["user_token"],
        wallet["agent_id"],
        policy_payload,
    )

    payload = cart_item_payload(recurring=recurring, suffix=case_name)
    payload["quantity"] = 1
    payload["unit_price"] = amount
    response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
        json=payload,
    )

    assert response.status_code == 201, response.text
    item = response.json()
    assert item["status"] == expected_status
    if expected_status == "approved":
        assert item["selected_payment_method_id"] == wallet["payment_method_id"]
        assert item["approved_at"] is not None
        assert item["decision_note"] == "Automatically approved by the agent payment rule."
        assert broker.events[-1][0] == "cart_item.approved"
    else:
        assert item["selected_payment_method_id"] is None
        assert item["approved_at"] is None
        assert broker.events[-1][0] == "cart_item.proposed"


async def test_threshold_currency_mismatch_requires_approval(
    client: AsyncClient,
    broker: Any,
) -> None:
    wallet = await policy_wallet(client, "policy-currency-mismatch")
    await update_policy(
        client,
        wallet["user_token"],
        wallet["agent_id"],
        {
            "mode": "above_amount",
            "threshold_amount": "1000.00",
            "threshold_currency": "USD",
        },
    )

    response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
        json=cart_item_payload(suffix="currency-mismatch"),
    )

    assert response.status_code == 201
    assert response.json()["status"] == "proposed"
    assert response.json()["selected_payment_method_id"] is None
    assert broker.events[-1][0] == "cart_item.proposed"


async def test_policy_bypass_without_an_active_assigned_card_falls_back_to_proposed(
    client: AsyncClient,
    broker: Any,
) -> None:
    wallet = await policy_wallet(client, "policy-no-card", assign_card=False)
    await update_policy(
        client,
        wallet["user_token"],
        wallet["agent_id"],
        {"mode": "never"},
    )

    response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
        json=cart_item_payload(suffix="no-card"),
    )

    assert response.status_code == 201
    item = response.json()
    assert item["status"] == "proposed"
    assert item["selected_payment_method_id"] is None
    assert item["approved_at"] is None
    assert broker.events[-1][0] == "cart_item.proposed"


async def test_auto_approval_selects_an_active_card_deterministically(client: AsyncClient) -> None:
    wallet = await policy_wallet(client, "policy-multiple-cards")
    second_card = await create_payment_method(
        client,
        wallet["user_token"],
        payload=personal_payment_method("pm_policy_second_card"),
    )
    await assign_payment_method(
        client,
        wallet["user_token"],
        wallet["agent_id"],
        second_card["id"],
    )
    await update_policy(
        client,
        wallet["user_token"],
        wallet["agent_id"],
        {"mode": "never"},
    )

    response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
        json=cart_item_payload(suffix="multiple-cards"),
    )

    assert response.status_code == 201
    expected_id = min(
        UUID(wallet["payment_method_id"]),
        UUID(second_card["id"]),
    )
    assert response.json()["selected_payment_method_id"] == str(expected_id)


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "above_amount"},
        {"mode": "above_amount", "threshold_amount": "10.00"},
        {"mode": "above_amount", "threshold_currency": "EUR"},
        {"mode": "never", "threshold_amount": "10.00", "threshold_currency": "EUR"},
        {
            "mode": "above_amount",
            "threshold_amount": "-0.01",
            "threshold_currency": "EUR",
        },
        {
            "mode": "above_amount",
            "threshold_amount": "10.00",
            "threshold_currency": "EU",
        },
    ],
)
async def test_payment_policy_threshold_validation(
    client: AsyncClient,
    payload: dict[str, Any],
) -> None:
    user_token = await register_user(client, f"policy-validation-{abs(hash(str(payload)))}")
    agent = await create_agent(client, user_token)

    response = await client.patch(
        f"{API}/agents/{agent['id']}/payment-policy",
        headers=bearer(user_token),
        json=payload,
    )

    assert response.status_code == 422


async def test_payment_policy_routes_are_tenant_scoped(client: AsyncClient) -> None:
    owner_a = await register_user(client, "policy-tenant-a")
    agent_a = await create_agent(client, owner_a)
    owner_b = await register_user(client, "policy-tenant-b")
    agent_b = await create_agent(client, owner_b)

    cross_tenant_update = await client.patch(
        f"{API}/agents/{agent_a['id']}/payment-policy",
        headers=bearer(owner_b),
        json={"mode": "never"},
    )
    assert cross_tenant_update.status_code == 404

    policies_b = await client.get(
        f"{API}/payment-policies",
        headers=bearer(owner_b),
    )
    assert policies_b.status_code == 200
    assert [policy["agent_id"] for policy in policies_b.json()] == [agent_b["id"]]
    assert all(policy["agent_id"] != agent_a["id"] for policy in policies_b.json())
