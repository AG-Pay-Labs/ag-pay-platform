from collections.abc import AsyncIterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ag_platform_api.db.base import Base
from ag_platform_api.models import (
    Agent,
    AgentPaymentMethod,
    AgentStatus,
    BillingPeriod,
    BillingProfileType,
    CartItem,
    CartItemStatus,
    CheckoutEvent,
    CheckoutExecution,
    CheckoutExecutionStatus,
    CheckoutStatusTransition,
    PaymentMethod,
    PaymentMethodStatus,
    Purchase,
    PurchaseCredential,
    User,
)
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.repository import SqlAlchemyCheckoutRepository

SessionMaker = async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def checkout_db(
    tmp_path: Path,
) -> AsyncIterator[tuple[SessionMaker, SqlAlchemyCheckoutRepository]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'checkout.sqlite3'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory, SqlAlchemyCheckoutRepository(session_factory)
    await engine.dispose()


async def seed_execution(
    session_factory: SessionMaker,
    *,
    recurring: bool = False,
) -> UUID:
    async with session_factory() as session, session.begin():
        user = User(username=f"owner-{id(session)}", password_hash="unused", is_active=True)
        session.add(user)
        await session.flush()
        agent = Agent(
            owner_id=user.id,
            name="Checkout agent",
            description=None,
            status=AgentStatus.active,
            capabilities=["shopping"],
        )
        payment = PaymentMethod(
            owner_id=user.id,
            display_name="Issuing virtual card",
            status=PaymentMethodStatus.active,
            provider="stripe_issuing",
            provider_payment_method_id="ic_card123",
            card_brand="Visa",
            card_last4="4242",
            expiry_month=12,
            expiry_year=2030,
            billing_profile_type=BillingProfileType.personal,
            billing_details={
                "type": "personal",
                "full_name": "Alex Example",
                "email": "alex@example.test",
                "address": {"line1": "1 Test Street", "country": "ES"},
            },
        )
        session.add_all([agent, payment])
        await session.flush()
        session.add(AgentPaymentMethod(agent_id=agent.id, payment_method_id=payment.id))
        credential = PurchaseCredential(
            owner_id=user.id,
            agent_id=agent.id,
            email="buyer@example.test",
            encrypted_password="encrypted-not-a-browser-card",
            login_url=None,
        )
        session.add(credential)
        await session.flush()
        cart = CartItem(
            owner_id=user.id,
            agent_id=agent.id,
            credential_id=credential.id,
            selected_payment_method_id=payment.id,
            title="Managed checkout",
            description="Repository state transition test",
            product_url="https://merchant.example.test/product/one",
            checkout_adapter="demo",
            checkout_url="https://merchant.example.test/checkout/one",
            merchant="Example Merchant",
            reason="Test checkout",
            quantity=2,
            unit_price=Decimal("12.50"),
            currency="EUR",
            billing_period=BillingPeriod.monthly if recurring else None,
            status=CartItemStatus.approved,
            approved_at=datetime.now(UTC),
        )
        session.add(cart)
        await session.flush()
        execution = CheckoutExecution(
            owner_id=user.id,
            agent_id=agent.id,
            payment_method_id=payment.id,
            cart_item_id=cart.id,
            adapter_key="demo",
            adapter_config={
                "allowed_origins": ["https://merchant.example.test"],
                "payment_origins": ["https://payments.example.test"],
                "product_title_selector": "#product-title",
                "quantity_selector": "#quantity",
                "total_selector": "#total",
                "card_number_selector": "#number",
                "expiry_selector": "#expiry",
                "expiry_month_selector": None,
                "expiry_year_selector": None,
                "cvc_selector": "#cvc",
                "submit_selector": "#submit",
                "success_selector": "#success",
                "name_selector": None,
                "billing_line1_selector": None,
                "billing_line2_selector": None,
                "billing_city_selector": None,
                "billing_region_selector": None,
                "billing_postal_code_selector": None,
                "billing_country_selector": None,
                "billing_email_selector": None,
                "billing_phone_selector": None,
                "action_required_selector": None,
                "order_reference_selector": None,
                "receipt_url_selector": None,
            },
            approved_amount=Decimal("25.00"),
            currency="EUR",
            checkout_origin="https://merchant.example.test",
        )
        session.add(execution)
        await session.flush()
        session.add(
            CheckoutStatusTransition(
                execution_id=execution.id,
                status=CheckoutExecutionStatus.queued,
                attempt_count=0,
            )
        )
        return execution.id


async def seed_sibling_execution(
    session_factory: SessionMaker,
    original_execution_id: UUID,
) -> UUID:
    async with session_factory() as session, session.begin():
        original = await session.get(CheckoutExecution, original_execution_id)
        assert original is not None
        credential = PurchaseCredential(
            owner_id=original.owner_id,
            agent_id=original.agent_id,
            email="sibling@example.test",
            encrypted_password="encrypted-not-a-browser-card",
            login_url=None,
        )
        session.add(credential)
        await session.flush()
        cart = CartItem(
            owner_id=original.owner_id,
            agent_id=original.agent_id,
            credential_id=credential.id,
            selected_payment_method_id=original.payment_method_id,
            title="Managed checkout sibling",
            description="Queued before the first execution becomes unresolved",
            product_url="https://merchant.example.test/product/two",
            checkout_adapter=original.adapter_key,
            checkout_url="https://merchant.example.test/checkout/two",
            merchant="Example Merchant",
            reason="Test card quarantine",
            quantity=1,
            unit_price=Decimal("25.00"),
            currency="EUR",
            status=CartItemStatus.approved,
            approved_at=datetime.now(UTC),
        )
        session.add(cart)
        await session.flush()
        execution = CheckoutExecution(
            owner_id=original.owner_id,
            agent_id=original.agent_id,
            payment_method_id=original.payment_method_id,
            cart_item_id=cart.id,
            adapter_key=original.adapter_key,
            adapter_config=deepcopy(original.adapter_config),
            approved_amount=Decimal("25.00"),
            currency="EUR",
            checkout_origin=original.checkout_origin,
        )
        session.add(execution)
        await session.flush()
        session.add(
            CheckoutStatusTransition(
                execution_id=execution.id,
                status=CheckoutExecutionStatus.queued,
                attempt_count=0,
            )
        )
        return execution.id


async def test_pre_submit_failure_retries_only_to_configured_max(
    checkout_db: tuple[SessionMaker, SqlAlchemyCheckoutRepository],
) -> None:
    session_factory, repository = checkout_db
    execution_id = await seed_execution(session_factory)
    first = await repository.claim_next(lease_seconds=120, max_attempts=2)
    assert first and first.execution_id == execution_id

    retry = await repository.retry_or_fail(
        execution_id,
        CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True),
        max_attempts=2,
    )
    assert retry is None
    second = await repository.claim_next(lease_seconds=120, max_attempts=2)
    assert second and second.execution_id == execution_id
    terminal = await repository.retry_or_fail(
        execution_id,
        CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True),
        max_attempts=2,
    )

    assert terminal and terminal.status == CheckoutExecutionStatus.failed
    async with session_factory() as session:
        execution = await session.get(CheckoutExecution, execution_id)
        event_count = await session.scalar(
            select(func.count())
            .select_from(CheckoutEvent)
            .where(CheckoutEvent.execution_id == execution_id)
        )
        transitions = list(
            await session.scalars(
                select(CheckoutStatusTransition)
                .where(CheckoutStatusTransition.execution_id == execution_id)
                .order_by(CheckoutStatusTransition.sequence)
            )
        )
    assert execution and execution.attempt_count == 2
    assert execution.status == CheckoutExecutionStatus.failed
    assert event_count == 1
    assert [transition.status for transition in transitions] == [
        CheckoutExecutionStatus.queued,
        CheckoutExecutionStatus.running,
        CheckoutExecutionStatus.queued,
        CheckoutExecutionStatus.running,
        CheckoutExecutionStatus.failed,
    ]
    assert [transition.attempt_count for transition in transitions] == [0, 1, 1, 2, 2]
    assert [transition.error_code for transition in transitions] == [
        None,
        None,
        CheckoutErrorCode.browser_session_failed.value,
        None,
        CheckoutErrorCode.browser_session_failed.value,
    ]


async def test_stale_post_submit_execution_becomes_unknown_and_is_never_retried(
    checkout_db: tuple[SessionMaker, SqlAlchemyCheckoutRepository],
) -> None:
    session_factory, repository = checkout_db
    execution_id = await seed_execution(session_factory)
    await repository.claim_next(lease_seconds=120, max_attempts=3)
    await repository.mark_submitted(execution_id, "session_12345")
    async with session_factory() as session, session.begin():
        execution = await session.get(CheckoutExecution, execution_id)
        assert execution
        execution.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    stale = await repository.claim_next(lease_seconds=120, max_attempts=3)
    assert stale and stale.execution_id is None
    assert stale.notification
    assert stale.notification.status == CheckoutExecutionStatus.outcome_unknown
    assert await repository.claim_next(lease_seconds=120, max_attempts=3) is None

    async with session_factory() as session:
        execution = await session.get(CheckoutExecution, execution_id)
        events = (
            await session.scalars(
                select(CheckoutEvent).where(CheckoutEvent.execution_id == execution_id)
            )
        ).all()
    assert execution and execution.attempt_count == 1
    assert execution.status == CheckoutExecutionStatus.outcome_unknown
    assert [event.error_code for event in events] == ["payment_outcome_unknown"]


async def test_prepare_rejects_removed_assignment(
    checkout_db: tuple[SessionMaker, SqlAlchemyCheckoutRepository],
) -> None:
    session_factory, repository = checkout_db
    execution_id = await seed_execution(session_factory)
    await repository.claim_next(lease_seconds=120, max_attempts=3)
    async with session_factory() as session, session.begin():
        await session.execute(delete(AgentPaymentMethod))

    with pytest.raises(CheckoutError) as caught:
        await repository.prepare(execution_id)
    assert caught.value.code == CheckoutErrorCode.payment_method_unassigned


async def test_success_is_atomic_and_idempotent_with_one_purchase_and_event(
    checkout_db: tuple[SessionMaker, SqlAlchemyCheckoutRepository],
) -> None:
    session_factory, repository = checkout_db
    execution_id = await seed_execution(session_factory)
    await repository.claim_next(lease_seconds=120, max_attempts=3)
    await repository.prepare(execution_id)
    await repository.record_browser_session(execution_id, "session_12345")
    await repository.mark_submitted(execution_id, "session_12345")

    completed = await repository.succeed(
        execution_id,
        provider_reference="iauth_exact123",
        merchant_order_reference="ORDER-123",
        receipt_url="https://merchant.example.test/receipts/one",
    )
    repeated = await repository.succeed(
        execution_id,
        provider_reference="iauth_exact123",
        merchant_order_reference="ORDER-123",
        receipt_url="https://merchant.example.test/receipts/one",
    )

    assert completed and completed.status == CheckoutExecutionStatus.succeeded
    assert repeated is None
    async with session_factory() as session:
        execution = await session.get(CheckoutExecution, execution_id)
        purchase_count = await session.scalar(select(func.count()).select_from(Purchase))
        event_count = await session.scalar(select(func.count()).select_from(CheckoutEvent))
        cart = await session.get(CartItem, completed.cart_item_id)
        purchase = await session.scalar(
            select(Purchase).where(Purchase.cart_item_id == completed.cart_item_id)
        )
        transitions = list(
            await session.scalars(
                select(CheckoutStatusTransition)
                .where(CheckoutStatusTransition.execution_id == execution_id)
                .order_by(CheckoutStatusTransition.sequence)
            )
        )
    assert execution and execution.status == CheckoutExecutionStatus.succeeded
    assert execution.merchant_order_reference == "ORDER-123"
    assert cart and cart.status == CartItemStatus.purchased
    assert purchase and purchase.merchant_order_reference == "ORDER-123"
    assert purchase_count == event_count == 1
    assert [transition.status for transition in transitions] == [
        CheckoutExecutionStatus.queued,
        CheckoutExecutionStatus.running,
        CheckoutExecutionStatus.succeeded,
    ]
    assert [transition.attempt_count for transition in transitions] == [0, 1, 1]


async def test_repository_rejects_legacy_queued_recurring_execution(
    checkout_db: tuple[SessionMaker, SqlAlchemyCheckoutRepository],
) -> None:
    session_factory, repository = checkout_db
    execution_id = await seed_execution(session_factory, recurring=True)
    await repository.claim_next(lease_seconds=120, max_attempts=3)

    with pytest.raises(CheckoutError) as caught:
        await repository.prepare(execution_id)

    assert caught.value.code == CheckoutErrorCode.recurring_unsupported


async def test_assignment_removed_after_fill_blocks_success_and_forces_unknown(
    checkout_db: tuple[SessionMaker, SqlAlchemyCheckoutRepository],
) -> None:
    session_factory, repository = checkout_db
    execution_id = await seed_execution(session_factory)
    await repository.claim_next(lease_seconds=120, max_attempts=3)
    await repository.prepare(execution_id)
    await repository.mark_submitted(execution_id, "session_12345")
    async with session_factory() as session, session.begin():
        await session.execute(delete(AgentPaymentMethod))

    with pytest.raises(CheckoutError) as caught:
        await repository.succeed(
            execution_id,
            provider_reference="iauth_exact123",
            merchant_order_reference=None,
            receipt_url=None,
        )
    terminal = await repository.retry_or_fail(
        execution_id,
        caught.value,
        max_attempts=3,
    )

    assert terminal and terminal.status == CheckoutExecutionStatus.outcome_unknown
    async with session_factory() as session:
        purchase_count = await session.scalar(select(func.count()).select_from(Purchase))
        event = await session.scalar(
            select(CheckoutEvent).where(CheckoutEvent.execution_id == execution_id)
        )
    assert purchase_count == 0
    assert event and event.error_code == "payment_outcome_unknown"


async def test_assignment_removed_after_prepare_blocks_submission_without_retrying_click(
    checkout_db: tuple[SessionMaker, SqlAlchemyCheckoutRepository],
) -> None:
    session_factory, repository = checkout_db
    execution_id = await seed_execution(session_factory)
    await repository.claim_next(lease_seconds=120, max_attempts=3)
    await repository.prepare(execution_id)
    async with session_factory() as session, session.begin():
        await session.execute(delete(AgentPaymentMethod))

    with pytest.raises(CheckoutError) as caught:
        await repository.mark_submitted(execution_id, "session_12345")

    assert caught.value.code == CheckoutErrorCode.payment_method_unassigned
    async with session_factory() as session:
        execution = await session.get(CheckoutExecution, execution_id)
    assert execution and execution.submitted_at is None


async def test_checkout_url_changed_after_prepare_blocks_submission(
    checkout_db: tuple[SessionMaker, SqlAlchemyCheckoutRepository],
) -> None:
    session_factory, repository = checkout_db
    execution_id = await seed_execution(session_factory)
    claim = await repository.claim_next(lease_seconds=120, max_attempts=3)
    assert claim
    await repository.prepare(execution_id)
    async with session_factory() as session, session.begin():
        cart = await session.get(CartItem, claim.cart_item_id)
        assert cart
        cart.checkout_url = "https://evil.example.test/checkout/changed"

    with pytest.raises(CheckoutError) as caught:
        await repository.mark_submitted(execution_id, "session_12345")

    assert caught.value.code == CheckoutErrorCode.origin_blocked
    async with session_factory() as session:
        execution = await session.get(CheckoutExecution, execution_id)
    assert execution and execution.submitted_at is None


async def test_queued_sibling_is_blocked_if_card_becomes_unresolved_before_disclosure(
    checkout_db: tuple[SessionMaker, SqlAlchemyCheckoutRepository],
) -> None:
    session_factory, repository = checkout_db
    first_execution_id = await seed_execution(session_factory)
    sibling_execution_id = await seed_sibling_execution(session_factory, first_execution_id)
    async with session_factory() as session, session.begin():
        first = await session.get(CheckoutExecution, first_execution_id)
        assert first is not None
        first.status = CheckoutExecutionStatus.outcome_unknown
        first.error_code = CheckoutErrorCode.payment_outcome_unknown.value
        first.error_message = CheckoutError(CheckoutErrorCode.payment_outcome_unknown).safe_message
        first.completed_at = datetime.now(UTC)

    claim = await repository.claim_next(lease_seconds=120, max_attempts=3)
    assert claim and claim.execution_id == sibling_execution_id
    with pytest.raises(CheckoutError) as caught:
        await repository.prepare(sibling_execution_id)

    assert caught.value.code == CheckoutErrorCode.card_reconciliation_required
    async with session_factory() as session:
        sibling = await session.get(CheckoutExecution, sibling_execution_id)
    assert sibling is not None
    assert sibling.submitted_at is None
