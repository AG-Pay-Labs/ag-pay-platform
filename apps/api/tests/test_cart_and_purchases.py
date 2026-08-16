from helpers import (
    API,
    PASSWORD,
    assign_payment_method,
    bearer,
    connect_agent,
    create_agent,
    create_payment_method,
    propose_cart_item,
    register_user,
)
from httpx import AsyncClient


async def connected_wallet(client: AsyncClient, username: str) -> dict[str, str]:
    user_token = await register_user(client, username)
    created_agent = await create_agent(client, user_token)
    connected = await connect_agent(client, created_agent["pairing_token"])
    payment_method = await create_payment_method(
        client,
        user_token,
        payload=None,
    )
    await assign_payment_method(
        client,
        user_token,
        created_agent["id"],
        payment_method["id"],
    )
    return {
        "user_token": user_token,
        "agent_id": created_agent["id"],
        "agent_token": connected["agent_access_token"],
        "payment_method_id": payment_method["id"],
    }


async def test_agent_proposal_owner_approval_credential_reveal_and_purchase(
    client: AsyncClient,
) -> None:
    wallet = await connected_wallet(client, "purchase-owner")
    proposed = await propose_cart_item(client, wallet["agent_token"], suffix="approved")

    assert proposed["status"] == "proposed"
    assert proposed["agent_id"] == wallet["agent_id"]
    assert proposed["unit_price"] == "12.50"
    assert proposed["total_amount"] == "25.00"
    assert proposed["currency"] == "EUR"
    assert proposed["account_email"] == "buyer-approved@example.com"
    assert proposed["checkout_adapter"] is None
    assert proposed["checkout_url"] is None
    assert proposed["execution"] is None
    assert "password" not in proposed

    wrong_reveal = await client.post(
        f"{API}/cart-items/{proposed['id']}/credential/reveal",
        headers=bearer(wallet["user_token"]),
        json={"current_password": "incorrect-owner-password"},
    )
    assert wrong_reveal.status_code == 403

    reveal = await client.post(
        f"{API}/cart-items/{proposed['id']}/credential/reveal",
        headers=bearer(wallet["user_token"]),
        json={"current_password": PASSWORD},
    )
    assert reveal.status_code == 200
    assert reveal.json() == {
        "email": "buyer-approved@example.com",
        "password": "purchase-password-approved",
        "login_url": "https://merchant.example.test/login",
    }

    approved = await client.post(
        f"{API}/cart-items/{proposed['id']}/approve",
        headers=bearer(wallet["user_token"]),
        json={"payment_method_id": wallet["payment_method_id"], "note": "Within budget"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["selected_payment_method_id"] == wallet["payment_method_id"]
    # Legacy proposals intentionally record the human decision without queuing payment.
    assert approved.json()["checkout_adapter"] is None
    assert approved.json()["checkout_url"] is None
    assert approved.json()["execution"] is None

    wrong_total = await client.post(
        f"{API}/agent/cart-items/{proposed['id']}/purchase",
        headers=bearer(wallet["agent_token"]),
        json={
            "amount": "24.99",
            "currency": "EUR",
            "provider_reference": "charge-wrong-total",
        },
    )
    assert wrong_total.status_code == 409

    completed = await client.post(
        f"{API}/agent/cart-items/{proposed['id']}/purchase",
        headers=bearer(wallet["agent_token"]),
        json={
            "amount": "25.00",
            "currency": "eur",
            "provider_reference": "charge-approved",
            "receipt_url": "https://merchant.example.test/receipts/approved",
        },
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "completed"
    assert body["cart_item_id"] == proposed["id"]
    assert body["agent_id"] == wallet["agent_id"]
    assert body["payment_method_id"] == wallet["payment_method_id"]
    assert body["account_email"] == "buyer-approved@example.com"
    assert body["subscription"] is None

    purchases = await client.get(
        f"{API}/purchases",
        headers=bearer(wallet["user_token"]),
    )
    assert purchases.status_code == 200
    assert [purchase["id"] for purchase in purchases.json()] == [body["id"]]

    repeated = await client.post(
        f"{API}/agent/cart-items/{proposed['id']}/purchase",
        headers=bearer(wallet["agent_token"]),
        json={
            "amount": "25.00",
            "currency": "EUR",
            "provider_reference": "charge-approved-again",
        },
    )
    assert repeated.status_code == 409


async def test_owner_can_cancel_a_proposal_but_it_cannot_be_purchased(
    client: AsyncClient,
) -> None:
    wallet = await connected_wallet(client, "cancel-owner")
    proposed = await propose_cart_item(client, wallet["agent_token"], suffix="cancelled")

    cancelled = await client.post(
        f"{API}/cart-items/{proposed['id']}/cancel",
        headers=bearer(wallet["user_token"]),
        json={"note": "No longer needed"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["decision_note"] == "No longer needed"
    assert cancelled.json()["cancelled_at"] is not None

    purchase = await client.post(
        f"{API}/agent/cart-items/{proposed['id']}/purchase",
        headers=bearer(wallet["agent_token"]),
        json={
            "amount": "25.00",
            "currency": "EUR",
            "provider_reference": "charge-cancelled",
        },
    )
    assert purchase.status_code == 409


async def test_recurring_purchase_creates_and_updates_subscription(client: AsyncClient) -> None:
    wallet = await connected_wallet(client, "subscription-owner")
    proposed = await propose_cart_item(
        client,
        wallet["agent_token"],
        recurring=True,
        suffix="subscription",
    )

    approved = await client.post(
        f"{API}/cart-items/{proposed['id']}/approve",
        headers=bearer(wallet["user_token"]),
        json={"payment_method_id": wallet["payment_method_id"]},
    )
    assert approved.status_code == 200

    completed = await client.post(
        f"{API}/agent/cart-items/{proposed['id']}/purchase",
        headers=bearer(wallet["agent_token"]),
        json={
            "amount": "25.00",
            "currency": "EUR",
            "provider_reference": "charge-subscription",
            "next_billing_at": "2030-02-01T12:00:00Z",
        },
    )
    assert completed.status_code == 200, completed.text
    subscription = completed.json()["subscription"]
    assert subscription["billing_period"] == "monthly"
    assert subscription["status"] == "active"
    assert subscription["title"] == "Agent purchase subscription"

    listed = await client.get(
        f"{API}/subscriptions",
        headers=bearer(wallet["user_token"]),
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [subscription["id"]]

    paused = await client.patch(
        f"{API}/subscriptions/{subscription['id']}",
        headers=bearer(wallet["user_token"]),
        json={"status": "paused", "next_billing_at": "2030-03-01T12:00:00Z"},
    )
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["next_billing_at"].startswith("2030-03-01T12:00:00")
