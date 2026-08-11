import json
from decimal import Decimal
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
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ag_platform_api.core.config import (
    STRIPE_HOSTED_TEST_ADAPTER_KEY,
    STRIPE_HOSTED_TEST_BOOTSTRAP_URL,
    CheckoutAdapterSettings,
    Settings,
    stripe_hosted_test_adapter,
)
from ag_platform_api.models import (
    CartItem,
    CheckoutEvent,
    CheckoutExecution,
    CheckoutExecutionStatus,
    CheckoutStatusTransition,
    PurchaseCredential,
    User,
)
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.repository import SqlAlchemyCheckoutRepository


def adapter_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "allowed_origins": ["https://merchant.example.test"],
        "payment_origins": ["https://payments.example.test"],
        "product_title_selector": "[data-checkout-product-title]",
        "quantity_selector": "[data-checkout-quantity]",
        "total_selector": "[data-checkout-total]",
        "card_number_selector": "#card-number",
        "expiry_selector": "#expiry",
        "cvc_selector": "#cvc",
        "submit_selector": "button[type='submit']",
        "success_selector": "[data-order-confirmed]",
        "name_selector": "#cardholder-name",
        "action_required_selector": "[data-action-required]",
        "order_reference_selector": "[data-order-reference]",
        "receipt_url_selector": "a[data-receipt]",
    }
    payload.update(overrides)
    return payload


def enable_checkout(settings: Settings, **adapter_overrides: Any) -> None:
    settings.checkout_enabled = True
    settings.checkout_adapters = {
        "demo": CheckoutAdapterSettings.model_validate(adapter_payload(**adapter_overrides))
    }


async def managed_wallet(client: AsyncClient, username: str) -> dict[str, str]:
    user_token = await register_user(client, username)
    created_agent = await create_agent(client, user_token)
    connected = await connect_agent(
        client,
        created_agent["pairing_token"],
        instance_id=f"{username}-instance",
    )
    payment_payload = personal_payment_method(
        "ic_" + "".join(character for character in username if character.isalnum())
    )
    payment_payload["provider"] = "stripe_issuing"
    payment_method = await create_payment_method(
        client,
        user_token,
        payload=payment_payload,
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


async def propose_managed(
    client: AsyncClient,
    wallet: dict[str, str],
    *,
    suffix: str,
    adapter: str = "demo",
    checkout_url: str | None = None,
) -> dict[str, Any]:
    payload = cart_item_payload(suffix=suffix)
    payload["checkout"] = {
        "adapter": adapter,
        "checkout_url": checkout_url
        or f"https://merchant.example.test/checkout/{suffix}?cart=managed",
    }
    response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def approve_managed(
    client: AsyncClient,
    wallet: dict[str, str],
    request_id: str,
) -> Any:
    return await client.post(
        f"{API}/cart-items/{request_id}/approve",
        headers=bearer(wallet["user_token"]),
        json={"payment_method_id": wallet["payment_method_id"]},
    )


async def set_auto_approval(client: AsyncClient, wallet: dict[str, str]) -> None:
    response = await client.patch(
        f"{API}/agents/{wallet['agent_id']}/payment-policy",
        headers=bearer(wallet["user_token"]),
        json={"mode": "never"},
    )
    assert response.status_code == 200, response.text


def test_checkout_adapter_configuration_is_strict() -> None:
    with pytest.raises(ValidationError):
        CheckoutAdapterSettings.model_validate(
            adapter_payload(allowed_origins=["http://merchant.example.test"])
        )
    with pytest.raises(ValidationError):
        CheckoutAdapterSettings.model_validate(
            adapter_payload(allowed_origins=["https://merchant.example.test/checkout"])
        )
    with pytest.raises(ValidationError):
        CheckoutAdapterSettings.model_validate(adapter_payload(total_selector="#total\nbody"))
    with pytest.raises(ValidationError):
        CheckoutAdapterSettings.model_validate(
            adapter_payload(expiry_selector=None, expiry_month_selector="#month")
        )
    with pytest.raises(ValidationError):
        CheckoutAdapterSettings.model_validate(adapter_payload(unexpected_secret="not-allowed"))
    with pytest.raises(ValidationError):
        CheckoutAdapterSettings.model_validate(
            adapter_payload(result_origins=["https://result.example.test"])
        )
    with pytest.raises(ValidationError):
        CheckoutAdapterSettings.model_validate(adapter_payload(checkout_mode="agentic"))

    split_expiry = CheckoutAdapterSettings.model_validate(
        adapter_payload(
            expiry_selector=None,
            expiry_month_selector="#expiry-month",
            expiry_year_selector="#expiry-year",
        )
    )
    assert split_expiry.expiry_selector is None


async def test_hosted_checkout_queues_one_pinned_frozen_adapter_snapshot(
    client: AsyncClient,
    settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings.checkout_enabled = True
    settings.checkout_demo_enabled = True
    settings.checkout_hosted_demo_enabled = True
    settings.checkout_adapters = {STRIPE_HOSTED_TEST_ADAPTER_KEY: stripe_hosted_test_adapter()}
    wallet = await managed_wallet(client, "managed-hosted-frozen")
    demo_method = await create_payment_method(
        client,
        wallet["user_token"],
        payload=personal_payment_method("pm_stripe_demo_decline"),
    )
    await assign_payment_method(
        client,
        wallet["user_token"],
        wallet["agent_id"],
        demo_method["id"],
    )
    proposed = await propose_managed(
        client,
        wallet,
        suffix="hosted-frozen",
        adapter=STRIPE_HOSTED_TEST_ADAPTER_KEY,
        checkout_url=STRIPE_HOSTED_TEST_BOOTSTRAP_URL,
    )
    approval = await client.post(
        f"{API}/cart-items/{proposed['id']}/approve",
        headers=bearer(wallet["user_token"]),
        json={"payment_method_id": demo_method["id"]},
    )

    assert approval.status_code == 200, approval.text
    execution_id = UUID(approval.json()["execution"]["id"])
    async with db_session_factory() as db:
        execution = await db.get(CheckoutExecution, execution_id)
    assert execution is not None
    assert execution.adapter_key == STRIPE_HOSTED_TEST_ADAPTER_KEY
    assert execution.checkout_origin == "https://checkout.stripe.com"
    assert execution.adapter_config == stripe_hosted_test_adapter().model_dump(mode="json")

    settings.checkout_adapters = {
        "different": CheckoutAdapterSettings.model_validate(adapter_payload())
    }
    repository = SqlAlchemyCheckoutRepository(db_session_factory)
    claim = await repository.claim_next(lease_seconds=120, max_attempts=1)
    assert claim and claim.execution_id == execution_id
    frozen = await repository.prepare(execution_id)

    assert frozen.adapter_key == STRIPE_HOSTED_TEST_ADAPTER_KEY
    assert frozen.adapter.checkout_mode == "stripe_hosted_test"
    assert frozen.adapter.result_origins == ("https://example.com",)
    assert frozen.adapter.submit_selector == '[data-testid="hosted-payment-submit-button"]'


async def test_human_approval_queues_one_frozen_safe_execution(
    client: AsyncClient,
    settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, "managed-human")
    proposed = await propose_managed(client, wallet, suffix="human")

    assert proposed["status"] == "proposed"
    assert proposed["checkout_adapter"] == "demo"
    assert proposed["checkout_url"].startswith("https://merchant.example.test/checkout/")
    assert proposed["execution"] is None

    approved = await approve_managed(client, wallet, proposed["id"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["execution"] == {
        "id": body["execution"]["id"],
        "status": "queued",
        "attempt_count": 0,
        "approved_amount": "25.00",
        "currency": "EUR",
        "checkout_origin": "https://merchant.example.test",
        "submitted_at": None,
        "completed_at": None,
        "error_code": None,
        "error_message": None,
        "merchant_order_reference": None,
        "browserbase_session_id": None,
        "status_history": [
            {
                "status": "queued",
                "attempt_count": 0,
                "error_code": None,
                "error_message": None,
                "occurred_at": body["execution"]["status_history"][0]["occurred_at"],
            }
        ],
        "created_at": body["execution"]["created_at"],
        "updated_at": body["execution"]["updated_at"],
    }
    serialized = json.dumps(body)
    assert "adapter_config" not in serialized
    assert "connect_url" not in serialized
    assert "provider_payment_method_id" not in serialized

    duplicate = await approve_managed(client, wallet, proposed["id"])
    assert duplicate.status_code == 409
    async with db_session_factory() as db:
        execution = await db.scalar(
            select(CheckoutExecution).where(CheckoutExecution.cart_item_id == UUID(proposed["id"]))
        )
        execution_count = await db.scalar(select(func.count()).select_from(CheckoutExecution))
    assert execution is not None
    assert execution.adapter_config["total_selector"] == "[data-checkout-total]"
    assert execution.adapter_config["product_title_selector"] == ("[data-checkout-product-title]")
    assert execution.adapter_config["quantity_selector"] == "[data-checkout-quantity]"
    assert execution.adapter_config["allowed_origins"] == ["https://merchant.example.test"]
    assert execution_count == 1

    async with db_session_factory() as db:
        execution = await db.scalar(
            select(CheckoutExecution).where(CheckoutExecution.cart_item_id == UUID(proposed["id"]))
        )
        assert execution is not None
        execution.status = CheckoutExecutionStatus.failed
        execution.error_code = "payment_declined"
        execution.error_message = "unsafe provider response containing payment data"
        await db.commit()
    safely_reloaded = await client.get(
        f"{API}/agent/cart-items/{proposed['id']}",
        headers=bearer(wallet["agent_token"]),
    )
    safe_summary = safely_reloaded.json()["execution"]
    assert safe_summary["error_message"] == "The approved payment method was declined."
    assert "unsafe provider" not in json.dumps(safe_summary)


async def test_human_checkout_history_is_ordered_tenant_scoped_and_sanitized(
    client: AsyncClient,
    settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, "managed-history-owner")
    other_wallet = await managed_wallet(client, "managed-history-other")
    proposed = await propose_managed(client, wallet, suffix="history")
    approved = await approve_managed(client, wallet, proposed["id"])
    assert approved.status_code == 200, approved.text

    execution_id = UUID(approved.json()["execution"]["id"])
    repository = SqlAlchemyCheckoutRepository(db_session_factory)
    first_claim = await repository.claim_next(lease_seconds=120, max_attempts=2)
    assert first_claim and first_claim.execution_id == execution_id
    assert (
        await repository.retry_or_fail(
            execution_id,
            CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True),
            max_attempts=2,
        )
        is None
    )
    second_claim = await repository.claim_next(lease_seconds=120, max_attempts=2)
    assert second_claim and second_claim.execution_id == execution_id
    terminal = await repository.retry_or_fail(
        execution_id,
        CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True),
        max_attempts=2,
    )
    assert terminal and terminal.status == CheckoutExecutionStatus.failed

    async with db_session_factory() as db:
        final_transition = await db.scalar(
            select(CheckoutStatusTransition)
            .where(
                CheckoutStatusTransition.execution_id == execution_id,
                CheckoutStatusTransition.status == CheckoutExecutionStatus.failed,
            )
            .order_by(CheckoutStatusTransition.sequence.desc())
        )
        assert final_transition is not None
        final_transition.error_code = "unsafe-provider-response-secret"
        await db.commit()

    human = await client.get(
        f"{API}/cart-items/{proposed['id']}", headers=bearer(wallet["user_token"])
    )
    assert human.status_code == 200, human.text
    history = human.json()["execution"]["status_history"]
    assert [transition["status"] for transition in history] == [
        "queued",
        "running",
        "queued",
        "running",
        "failed",
    ]
    assert [transition["attempt_count"] for transition in history] == [0, 1, 1, 2, 2]
    assert history[2]["error_code"] == "browser_session_failed"
    assert history[2]["error_message"] == "The secure browser session could not be started."
    assert history[-1]["error_code"] == "checkout_failed"
    assert history[-1]["error_message"] == "The checkout could not be completed."
    assert "secret" not in json.dumps(history)

    other_owner = await client.get(
        f"{API}/cart-items/{proposed['id']}",
        headers=bearer(other_wallet["user_token"]),
    )
    assert other_owner.status_code == 404

    agent = await client.get(
        f"{API}/agent/cart-items/{proposed['id']}",
        headers=bearer(wallet["agent_token"]),
    )
    assert agent.status_code == 200, agent.text
    assert "status_history" not in agent.json()["execution"]


async def test_human_checkout_history_exposes_queued_running_and_succeeded(
    client: AsyncClient,
    settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, "managed-history-success")
    proposed = await propose_managed(client, wallet, suffix="history-success")
    approved = await approve_managed(client, wallet, proposed["id"])
    assert approved.status_code == 200, approved.text

    execution_id = UUID(approved.json()["execution"]["id"])
    repository = SqlAlchemyCheckoutRepository(db_session_factory)
    claim = await repository.claim_next(lease_seconds=120, max_attempts=2)
    assert claim and claim.execution_id == execution_id
    await repository.mark_submitted(execution_id, "session_history_success")
    completed = await repository.succeed(
        execution_id,
        provider_reference="iauth_historysuccess",
        merchant_order_reference="ORDER-HISTORY-SUCCESS",
        receipt_url="https://merchant.example.test/receipts/history-success",
    )
    assert completed and completed.status == CheckoutExecutionStatus.succeeded

    human = await client.get(
        f"{API}/cart-items/{proposed['id']}", headers=bearer(wallet["user_token"])
    )
    assert human.status_code == 200, human.text
    history = human.json()["execution"]["status_history"]
    assert [transition["status"] for transition in history] == [
        "queued",
        "running",
        "succeeded",
    ]
    assert [transition["attempt_count"] for transition in history] == [0, 1, 1]
    assert all(transition["error_code"] is None for transition in history)


async def test_checkout_status_history_cascades_when_tenant_is_deleted(
    client: AsyncClient,
    settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, "managed-history-cascade")
    proposed = await propose_managed(client, wallet, suffix="history-cascade")
    approved = await approve_managed(client, wallet, proposed["id"])
    assert approved.status_code == 200, approved.text

    execution_id = UUID(approved.json()["execution"]["id"])
    async with db_session_factory() as db:
        execution = await db.get(CheckoutExecution, execution_id)
        assert execution is not None
        transition_count = await db.scalar(
            select(func.count())
            .select_from(CheckoutStatusTransition)
            .where(CheckoutStatusTransition.execution_id == execution_id)
        )
        assert transition_count == 1
        await db.execute(delete(User).where(User.id == execution.owner_id))
        await db.commit()
        remaining = await db.scalar(
            select(func.count())
            .select_from(CheckoutStatusTransition)
            .where(CheckoutStatusTransition.execution_id == execution_id)
        )
        assert remaining == 0


async def test_managed_checkout_always_waits_for_human_even_with_never_policy(
    client: AsyncClient,
    settings: Settings,
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, "managed-policy")
    await set_auto_approval(client, wallet)

    item = await propose_managed(client, wallet, suffix="policy")

    assert item["status"] == "proposed"
    assert item["selected_payment_method_id"] is None
    assert item["execution"] is None
    assert item["decision_note"] is None

    approved = await approve_managed(client, wallet, item["id"])

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["selected_payment_method_id"] == wallet["payment_method_id"]
    assert approved.json()["execution"]["status"] == "queued"


async def test_policy_does_not_autoapprove_managed_checkout_with_only_sandbox_method(
    client: AsyncClient,
    settings: Settings,
) -> None:
    enable_checkout(settings)
    user_token = await register_user(client, "managed-policy-sandbox-only")
    created_agent = await create_agent(client, user_token)
    connected = await connect_agent(client, created_agent["pairing_token"])
    payment_method = await create_payment_method(
        client,
        user_token,
        payload=personal_payment_method("pm_sandbox_only"),
    )
    await assign_payment_method(
        client,
        user_token,
        created_agent["id"],
        payment_method["id"],
    )
    wallet = {
        "user_token": user_token,
        "agent_id": created_agent["id"],
        "agent_token": connected["agent_access_token"],
        "payment_method_id": payment_method["id"],
    }
    await set_auto_approval(client, wallet)

    item = await propose_managed(client, wallet, suffix="sandbox-only")

    assert item["status"] == "proposed"
    assert item["selected_payment_method_id"] is None
    assert item["execution"] is None


async def test_human_managed_approval_rejects_non_issuing_method(
    client: AsyncClient,
    settings: Settings,
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, "managed-human-provider")
    sandbox = await create_payment_method(
        client,
        wallet["user_token"],
        payload=personal_payment_method("pm_managed_unsupported"),
    )
    await assign_payment_method(
        client,
        wallet["user_token"],
        wallet["agent_id"],
        sandbox["id"],
    )
    proposed = await propose_managed(client, wallet, suffix="human-provider")

    approval = await client.post(
        f"{API}/cart-items/{proposed['id']}/approve",
        headers=bearer(wallet["user_token"]),
        json={"payment_method_id": sandbox["id"]},
    )

    assert approval.status_code == 409
    assert (
        approval.json()["detail"]
        == "Managed checkout requires an assigned supported payment method"
    )


async def test_development_demo_decline_method_queues_only_for_demo_adapter(
    client: AsyncClient,
    settings: Settings,
) -> None:
    settings.checkout_enabled = True
    settings.checkout_demo_enabled = True
    settings.checkout_demo_adapter_key = "stripe-demo"
    settings.checkout_adapters = {
        "stripe-demo": CheckoutAdapterSettings.model_validate(adapter_payload())
    }
    wallet = await managed_wallet(client, "managed-demo-decline")
    demo_method = await create_payment_method(
        client,
        wallet["user_token"],
        payload=personal_payment_method("pm_stripe_demo_decline"),
    )
    await assign_payment_method(
        client,
        wallet["user_token"],
        wallet["agent_id"],
        demo_method["id"],
    )
    proposed = await propose_managed(
        client,
        wallet,
        suffix="demo-decline",
        adapter="stripe-demo",
    )

    approval = await client.post(
        f"{API}/cart-items/{proposed['id']}/approve",
        headers=bearer(wallet["user_token"]),
        json={"payment_method_id": demo_method["id"]},
    )

    assert approval.status_code == 200, approval.text
    assert approval.json()["execution"]["status"] == "queued"


async def test_human_approval_rejects_card_quarantined_by_unresolved_checkout(
    client: AsyncClient,
    settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, "managed-card-quarantine")
    first = await propose_managed(client, wallet, suffix="quarantine-first")
    second = await propose_managed(client, wallet, suffix="quarantine-second")
    first_approval = await approve_managed(client, wallet, first["id"])
    assert first_approval.status_code == 200
    async with db_session_factory() as db, db.begin():
        execution = await db.scalar(
            select(CheckoutExecution).where(CheckoutExecution.cart_item_id == UUID(first["id"]))
        )
        assert execution is not None
        execution.status = CheckoutExecutionStatus.outcome_unknown
        execution.error_code = "payment_outcome_unknown"
        execution.error_message = "The merchant payment outcome could not be proven."

    second_approval = await approve_managed(client, wallet, second["id"])

    assert second_approval.status_code == 409
    assert second_approval.json()["detail"] == (
        "The virtual card is quarantined until an unresolved checkout is reconciled."
    )


async def test_managed_recurring_proposal_is_rejected_until_interval_is_verified(
    client: AsyncClient,
    settings: Settings,
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, "managed-recurring")
    payload = cart_item_payload(suffix="managed-recurring", recurring=True)
    payload["checkout"] = {
        "adapter": "demo",
        "checkout_url": "https://merchant.example.test/checkout/managed-recurring",
    }
    proposed_response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
        json=payload,
    )
    assert proposed_response.status_code == 422
    serialized_errors = json.dumps(proposed_response.json())
    assert "Managed checkout does not support recurring purchases" in serialized_errors
    listed = await client.get(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
    )
    assert listed.status_code == 200
    assert listed.json() == []


@pytest.mark.parametrize(
    ("currency", "unit_price", "expected_message"),
    [
        ("JPY", "12.75", "amount is invalid for its currency"),
        ("ZZZ", "12.50", "currency is not supported"),
    ],
)
async def test_managed_checkout_rejects_unsupported_currency_precision_at_proposal(
    client: AsyncClient,
    settings: Settings,
    currency: str,
    unit_price: str,
    expected_message: str,
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, f"managed-currency-{currency.lower()}")
    payload = cart_item_payload(suffix=f"managed-currency-{currency.lower()}")
    payload["currency"] = currency
    payload["unit_price"] = unit_price
    payload["checkout"] = {
        "adapter": "demo",
        "checkout_url": f"https://merchant.example.test/checkout/currency-{currency.lower()}",
    }

    response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
        json=payload,
    )

    assert response.status_code == 422
    assert expected_message in json.dumps(response.json())


async def test_disabled_managed_checkout_stays_proposed_and_human_approval_fails_closed(
    client: AsyncClient,
    settings: Settings,
) -> None:
    settings.checkout_enabled = False
    settings.checkout_adapters = {"demo": CheckoutAdapterSettings.model_validate(adapter_payload())}
    wallet = await managed_wallet(client, "managed-policy-disabled")
    await set_auto_approval(client, wallet)
    payload = cart_item_payload(suffix="policy-disabled")
    payload["checkout"] = {
        "adapter": "demo",
        "checkout_url": "https://merchant.example.test/checkout/policy-disabled",
    }

    response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
        json=payload,
    )

    assert response.status_code == 201
    proposed = response.json()
    assert proposed["status"] == "proposed"
    assert proposed["execution"] is None

    approval = await approve_managed(client, wallet, proposed["id"])

    assert approval.status_code == 409
    assert approval.json()["detail"] == "Managed checkout is not enabled"


@pytest.mark.parametrize(
    ("checkout_enabled", "configured_adapter", "expected_detail"),
    [
        (False, "demo", "Managed checkout is not enabled"),
        (True, "other", "The requested checkout adapter is not configured"),
    ],
)
async def test_human_approval_fails_closed_without_enabled_configured_adapter(
    client: AsyncClient,
    settings: Settings,
    checkout_enabled: bool,
    configured_adapter: str,
    expected_detail: str,
) -> None:
    settings.checkout_enabled = checkout_enabled
    settings.checkout_adapters = {
        configured_adapter: CheckoutAdapterSettings.model_validate(adapter_payload())
    }
    wallet = await managed_wallet(client, f"managed-closed-{checkout_enabled}-{configured_adapter}")
    proposed = await propose_managed(client, wallet, suffix=f"closed-{configured_adapter}")

    approval = await approve_managed(client, wallet, proposed["id"])

    assert approval.status_code == 409
    assert approval.json()["detail"] == expected_detail
    unchanged = await client.get(
        f"{API}/cart-items/{proposed['id']}", headers=bearer(wallet["user_token"])
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["status"] == "proposed"
    assert unchanged.json()["execution"] is None


async def test_checkout_origin_is_validated_when_approval_would_queue(
    client: AsyncClient,
    settings: Settings,
) -> None:
    enable_checkout(settings)
    checkout_url = "https://evil.example.test/checkout/wrong-origin"
    wallet = await managed_wallet(client, "managed-url-wrong-origin")
    proposed = await propose_managed(
        client,
        wallet,
        suffix="url-wrong-origin",
        checkout_url=checkout_url,
    )

    approval = await approve_managed(client, wallet, proposed["id"])

    assert approval.status_code == 409
    assert approval.json()["detail"] == "Checkout URL origin is not allowed for this adapter"


@pytest.mark.parametrize(
    ("case_name", "checkout_url"),
    [
        ("http", "http://merchant.example.test/checkout/insecure"),
        (
            "credentialed",
            "https://buyer:proposal-url-secret@merchant.example.test/checkout/credentialed",
        ),
    ],
)
async def test_managed_checkout_rejects_unsafe_url_before_persisting_proposal(
    client: AsyncClient,
    settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
    case_name: str,
    checkout_url: str,
) -> None:
    enable_checkout(settings)
    suffix = f"unsafe-url-{case_name}"
    wallet = await managed_wallet(client, suffix)
    payload = cart_item_payload(suffix=suffix)
    payload["checkout"] = {"adapter": "demo", "checkout_url": checkout_url}

    response = await client.post(
        f"{API}/agent/cart-items",
        headers=bearer(wallet["agent_token"]),
        json=payload,
    )

    assert response.status_code == 422
    assert checkout_url not in response.text
    assert "proposal-url-secret" not in response.text
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])
    async with db_session_factory() as db:
        cart_count = await db.scalar(select(func.count()).select_from(CartItem))
        credential_count = await db.scalar(select(func.count()).select_from(PurchaseCredential))
    assert cart_count == 0
    assert credential_count == 0


async def test_agent_direct_lookup_is_scoped_to_authenticated_agent(
    client: AsyncClient,
    settings: Settings,
) -> None:
    enable_checkout(settings)
    wallet_a = await managed_wallet(client, "managed-direct-a")
    wallet_b = await managed_wallet(client, "managed-direct-b")
    item_a = await propose_managed(client, wallet_a, suffix="direct-a")

    own = await client.get(
        f"{API}/agent/cart-items/{item_a['id']}", headers=bearer(wallet_a["agent_token"])
    )
    other = await client.get(
        f"{API}/agent/cart-items/{item_a['id']}", headers=bearer(wallet_b["agent_token"])
    )

    assert own.status_code == 200
    assert own.json()["id"] == item_a["id"]
    assert own.json()["checkout_adapter"] == "demo"
    assert other.status_code == 404


async def test_merchant_order_reference_is_human_only_and_tenant_scoped(
    client: AsyncClient,
    settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enable_checkout(settings)
    wallet_a = await managed_wallet(client, "managed-reconciliation-a")
    wallet_b = await managed_wallet(client, "managed-reconciliation-b")
    proposed = await propose_managed(client, wallet_a, suffix="reconciliation")
    approved = await approve_managed(client, wallet_a, proposed["id"])
    assert approved.status_code == 200

    async with db_session_factory() as db, db.begin():
        execution = await db.scalar(
            select(CheckoutExecution).where(CheckoutExecution.cart_item_id == UUID(proposed["id"]))
        )
        assert execution is not None
        execution.merchant_order_reference = "ORDER-RECON-123"
        execution.browserbase_session_id = "session_human_only_123"
        execution.status = CheckoutExecutionStatus.outcome_unknown
        execution.error_code = "payment_outcome_unknown"

    human = await client.get(
        f"{API}/cart-items/{proposed['id']}",
        headers=bearer(wallet_a["user_token"]),
    )
    other_tenant = await client.get(
        f"{API}/cart-items/{proposed['id']}",
        headers=bearer(wallet_b["user_token"]),
    )
    agent = await client.get(
        f"{API}/agent/cart-items/{proposed['id']}",
        headers=bearer(wallet_a["agent_token"]),
    )

    assert human.status_code == 200
    assert human.json()["execution"]["merchant_order_reference"] == "ORDER-RECON-123"
    assert human.json()["execution"]["browserbase_session_id"] == "session_human_only_123"
    assert other_tenant.status_code == 404
    assert agent.status_code == 200
    assert "merchant_order_reference" not in json.dumps(agent.json())
    assert "browserbase_session_id" not in json.dumps(agent.json())


async def test_checkout_event_feed_is_monotonic_cursor_paginated_and_agent_scoped(
    client: AsyncClient,
    settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    enable_checkout(settings)
    wallet_a = await managed_wallet(client, "managed-events-a")
    wallet_b = await managed_wallet(client, "managed-events-b")
    proposed_a1 = await propose_managed(client, wallet_a, suffix="events-a1")
    proposed_b1 = await propose_managed(client, wallet_b, suffix="events-b1")
    proposed_a2 = await propose_managed(client, wallet_a, suffix="events-a2")
    approved_a1 = await approve_managed(client, wallet_a, proposed_a1["id"])
    approved_b1 = await approve_managed(client, wallet_b, proposed_b1["id"])
    approved_a2 = await approve_managed(client, wallet_a, proposed_a2["id"])
    assert approved_a1.status_code == approved_b1.status_code == approved_a2.status_code == 200
    item_a1 = approved_a1.json()
    item_b1 = approved_b1.json()
    item_a2 = approved_a2.json()

    async with db_session_factory() as db:
        for item, wallet, error_code in (
            (item_a1, wallet_a, "merchant_declined"),
            (item_b1, wallet_b, "action_required"),
            (item_a2, wallet_a, "confirmation_ambiguous"),
        ):
            db.add(
                CheckoutEvent(
                    execution_id=UUID(item["execution"]["id"]),
                    owner_id=(
                        await db.scalar(
                            select(CheckoutExecution.owner_id).where(
                                CheckoutExecution.id == UUID(item["execution"]["id"])
                            )
                        )
                    ),
                    agent_id=UUID(wallet["agent_id"]),
                    cart_item_id=UUID(item["id"]),
                    status=(
                        CheckoutExecutionStatus.action_required
                        if error_code == "action_required"
                        else CheckoutExecutionStatus.failed
                    ),
                    amount=Decimal("25.00"),
                    currency="EUR",
                    error_code=error_code,
                )
            )
            await db.flush()
        await db.commit()

    first = await client.get(
        f"{API}/agent/checkout-events?limit=1", headers=bearer(wallet_a["agent_token"])
    )
    assert first.status_code == 200, first.text
    first_page = first.json()
    assert len(first_page["events"]) == 1
    first_event = first_page["events"][0]
    assert set(first_event) == {
        "cursor",
        "event_id",
        "request_id",
        "status",
        "purchase_id",
        "amount",
        "currency",
        "error_code",
        "occurred_at",
    }
    assert first_event["request_id"] == item_a1["id"]
    assert first_event["purchase_id"] is None
    assert first_event["amount"] == "25.00"
    assert first_page["next_cursor"] == first_event["cursor"]

    second = await client.get(
        f"{API}/agent/checkout-events?after_cursor={first_page['next_cursor']}",
        headers=bearer(wallet_a["agent_token"]),
    )
    second_page = second.json()
    assert [event["request_id"] for event in second_page["events"]] == [item_a2["id"]]
    assert second_page["events"][0]["cursor"] > first_event["cursor"]

    other_agent = await client.get(
        f"{API}/agent/checkout-events", headers=bearer(wallet_b["agent_token"])
    )
    assert [event["request_id"] for event in other_agent.json()["events"]] == [item_b1["id"]]


async def test_agent_legacy_completion_is_blocked_for_managed_checkout(
    client: AsyncClient,
    settings: Settings,
) -> None:
    enable_checkout(settings)
    wallet = await managed_wallet(client, "managed-legacy-block")
    proposed = await propose_managed(client, wallet, suffix="legacy-block")
    approved = await approve_managed(client, wallet, proposed["id"])
    assert approved.status_code == 200

    completion = await client.post(
        f"{API}/agent/cart-items/{proposed['id']}/purchase",
        headers=bearer(wallet["agent_token"]),
        json={
            "amount": "25.00",
            "currency": "EUR",
            "provider_reference": "must-not-bypass-worker",
        },
    )

    assert completion.status_code == 409
    assert completion.json()["detail"] == (
        "Managed checkout completion is recorded by the checkout worker"
    )
