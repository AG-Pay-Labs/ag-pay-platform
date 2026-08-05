from typing import Any

from httpx import AsyncClient

API = "/api/v1"
PASSWORD = "correct-horse-battery-staple"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register_user(
    client: AsyncClient,
    username: str,
    password: str = PASSWORD,
) -> str:
    response = await client.post(
        f"{API}/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()["access_token"]


async def create_agent(
    client: AsyncClient,
    user_token: str,
    *,
    name: str = "Shopping agent",
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/agents",
        headers=bearer(user_token),
        json={"name": name, "description": "An isolated test agent"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def connect_agent(
    client: AsyncClient,
    pairing_token: str,
    *,
    instance_id: str = "openclaw-test-instance",
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/agent/handshake",
        json={
            "pairing_token": pairing_token,
            "instance_id": instance_id,
            "software_version": "0.1.0-test",
            "capabilities": ["shopping", "receipts", "shopping"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def personal_payment_method(reference: str = "pm_personal") -> dict[str, Any]:
    return {
        "display_name": "Personal Visa",
        "provider": "prototype-vault",
        "provider_payment_method_id": reference,
        "card_brand": "Visa",
        "card_last4": "4242",
        "expiry_month": 12,
        "expiry_year": 2030,
        "billing_details": {
            "type": "personal",
            "full_name": "Alex Example",
            "email": "alex@example.com",
            "phone": "+34910000000",
            "address": {
                "line1": "1 Test Street",
                "city": "Madrid",
                "region": "Madrid",
                "postal_code": "28001",
                "country": "es",
            },
        },
    }


def business_payment_method(reference: str = "pm_business") -> dict[str, Any]:
    return {
        "display_name": "Company Mastercard",
        "provider": "prototype-vault",
        "provider_payment_method_id": reference,
        "card_brand": "Mastercard",
        "card_last4": "4444",
        "expiry_month": 10,
        "expiry_year": 2031,
        "billing_details": {
            "type": "business",
            "legal_name": "Example Robotics SL",
            "vat_number": "ESB12345678",
            "registration_number": "M-123456",
            "contact_name": "Sam Example",
            "email": "billing@example.com",
            "address": {
                "line1": "2 Business Avenue",
                "city": "Barcelona",
                "postal_code": "08001",
                "country": "ES",
            },
        },
    }


async def create_payment_method(
    client: AsyncClient,
    user_token: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/payment-methods",
        headers=bearer(user_token),
        json=payload or personal_payment_method(),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def assign_payment_method(
    client: AsyncClient,
    user_token: str,
    agent_id: str,
    payment_method_id: str,
) -> None:
    response = await client.put(
        f"{API}/agents/{agent_id}/payment-methods/{payment_method_id}",
        headers=bearer(user_token),
    )
    assert response.status_code == 204, response.text


def cart_item_payload(*, recurring: bool = False, suffix: str = "one") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": f"Agent purchase {suffix}",
        "description": "An item selected by the agent after comparing available options.",
        "product_url": f"https://merchant.example.test/products/{suffix}",
        "merchant": "Example Merchant",
        "reason": "It meets the owner's stated requirements and budget.",
        "quantity": 2,
        "unit_price": "12.50",
        "currency": "eur",
        "account": {
            "email": f"buyer-{suffix}@example.com",
            "password": f"purchase-password-{suffix}",
            "login_url": "https://merchant.example.test/login",
        },
    }
    if recurring:
        payload["billing_period"] = "monthly"
    return payload


async def propose_cart_item(
    client: AsyncClient,
    agent_token: str,
    *,
    recurring: bool = False,
    suffix: str = "one",
) -> dict[str, Any]:
    response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(agent_token),
        json=cart_item_payload(recurring=recurring, suffix=suffix),
    )
    assert response.status_code == 201, response.text
    return response.json()
