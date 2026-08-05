from helpers import (
    API,
    PASSWORD,
    assign_payment_method,
    bearer,
    connect_agent,
    create_agent,
    create_payment_method,
    personal_payment_method,
    propose_cart_item,
    register_user,
)
from httpx import AsyncClient


async def test_owner_and_agent_resources_are_tenant_isolated(client: AsyncClient) -> None:
    owner_a = await register_user(client, "tenant-a")
    agent_a = await create_agent(client, owner_a, name="Tenant A agent")
    connected_a = await connect_agent(client, agent_a["pairing_token"], instance_id="tenant-a")
    card_a = await create_payment_method(
        client,
        owner_a,
        payload=personal_payment_method("pm_tenant_a"),
    )
    await assign_payment_method(client, owner_a, agent_a["id"], card_a["id"])
    cart_a = await propose_cart_item(
        client,
        connected_a["agent_access_token"],
        recurring=True,
        suffix="tenant-a",
    )

    owner_b = await register_user(client, "tenant-b")
    agent_b = await create_agent(client, owner_b, name="Tenant B agent")
    connected_b = await connect_agent(client, agent_b["pairing_token"], instance_id="tenant-b")
    card_b = await create_payment_method(
        client,
        owner_b,
        payload=personal_payment_method("pm_tenant_b"),
    )

    other_agent = await client.get(f"{API}/agents/{agent_a['id']}", headers=bearer(owner_b))
    assert other_agent.status_code == 404
    assert (
        await client.get(f"{API}/cart-items/{cart_a['id']}", headers=bearer(owner_b))
    ).status_code == 404
    assert (
        await client.post(
            f"{API}/cart-items/{cart_a['id']}/credential/reveal",
            headers=bearer(owner_b),
            json={"current_password": PASSWORD},
        )
    ).status_code == 404

    cross_tenant_card = await client.put(
        f"{API}/agents/{agent_b['id']}/payment-methods/{card_a['id']}",
        headers=bearer(owner_b),
    )
    assert cross_tenant_card.status_code == 404
    cross_tenant_agent = await client.put(
        f"{API}/agents/{agent_a['id']}/payment-methods/{card_b['id']}",
        headers=bearer(owner_b),
    )
    assert cross_tenant_agent.status_code == 404

    agent_b_cart = await client.get(
        f"{API}/agent/cart-items",
        headers=bearer(connected_b["agent_access_token"]),
    )
    assert agent_b_cart.status_code == 200
    assert agent_b_cart.json() == []

    agent_b_purchase = await client.post(
        f"{API}/agent/cart-items/{cart_a['id']}/purchase",
        headers=bearer(connected_b["agent_access_token"]),
        json={
            "amount": "25.00",
            "currency": "EUR",
            "provider_reference": "cross-tenant-attempt",
        },
    )
    assert agent_b_purchase.status_code == 404

    approved_a = await client.post(
        f"{API}/cart-items/{cart_a['id']}/approve",
        headers=bearer(owner_a),
        json={"payment_method_id": card_a["id"]},
    )
    assert approved_a.status_code == 200
    completed_a = await client.post(
        f"{API}/agent/cart-items/{cart_a['id']}/purchase",
        headers=bearer(connected_a["agent_access_token"]),
        json={
            "amount": "25.00",
            "currency": "EUR",
            "provider_reference": "tenant-a-purchase",
            "next_billing_at": "2030-02-01T12:00:00Z",
        },
    )
    assert completed_a.status_code == 200
    purchase_a = completed_a.json()
    subscription_a = purchase_a["subscription"]

    other_purchase = await client.get(
        f"{API}/purchases/{purchase_a['id']}", headers=bearer(owner_b)
    )
    assert other_purchase.status_code == 404
    other_subscription = await client.patch(
        f"{API}/subscriptions/{subscription_a['id']}",
        headers=bearer(owner_b),
        json={"status": "cancelled"},
    )
    assert other_subscription.status_code == 404

    payment_methods_b = await client.get(f"{API}/payment-methods", headers=bearer(owner_b))
    assert payment_methods_b.status_code == 200
    assert [method["id"] for method in payment_methods_b.json()] == [card_b["id"]]

    for path in ("agents", "cart-items", "purchases", "subscriptions"):
        response = await client.get(f"{API}/{path}", headers=bearer(owner_b))
        assert response.status_code == 200
        if path == "agents":
            assert [item["id"] for item in response.json()] == [agent_b["id"]]
        else:
            assert response.json() == []
