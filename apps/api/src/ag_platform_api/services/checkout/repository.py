from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ag_platform_api.models import (
    Agent,
    AgentPaymentMethod,
    AgentStatus,
    CartItem,
    CartItemStatus,
    CheckoutEvent,
    CheckoutExecution,
    CheckoutExecutionStatus,
    CheckoutStatusTransition,
    PaymentMethod,
    PaymentMethodStatus,
    Purchase,
    PurchaseStatus,
)
from ag_platform_api.services.checkout.errors import (
    SAFE_ERROR_MESSAGES,
    CheckoutError,
    CheckoutErrorCode,
)
from ag_platform_api.services.checkout.origins import normalize_origin, validate_checkout_url
from ag_platform_api.services.checkout.types import (
    CheckoutAdapter,
    CheckoutContext,
    ExpectedCardMetadata,
)

TERMINAL_STATUSES = frozenset(
    {
        CheckoutExecutionStatus.succeeded,
        CheckoutExecutionStatus.failed,
        CheckoutExecutionStatus.action_required,
        CheckoutExecutionStatus.outcome_unknown,
    }
)


@dataclass(frozen=True, slots=True)
class TerminalNotification:
    execution_id: UUID
    cart_item_id: UUID
    status: CheckoutExecutionStatus
    error_code: str | None
    purchase_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ClaimResult:
    execution_id: UUID | None
    cart_item_id: UUID
    notification: TerminalNotification | None = None


class SqlAlchemyCheckoutRepository:
    _local_card_locks: dict[str, asyncio.Lock] = {}

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        bind = session_factory.kw.get("bind")
        if not isinstance(bind, AsyncEngine):
            raise TypeError("Checkout repository requires an AsyncEngine-bound session factory")
        self._engine = bind

    @asynccontextmanager
    async def card_lock(self, card_id: str):
        """Serialize browser submission and issuer reconciliation for one card."""
        if self._engine.dialect.name != "postgresql":
            lock = self._local_card_locks.setdefault(card_id, asyncio.Lock())
            await lock.acquire()
            try:
                yield
            finally:
                lock.release()
            return

        lock_key = int.from_bytes(
            hashlib.sha256(card_id.encode("utf-8")).digest()[:8],
            byteorder="big",
            signed=True,
        )
        async with self._engine.connect() as connection:
            await connection.execute(select(func.pg_advisory_lock(lock_key)))
            await connection.commit()
            try:
                yield
            finally:
                try:
                    unlocked = await connection.scalar(select(func.pg_advisory_unlock(lock_key)))
                    await connection.commit()
                    if unlocked is not True:
                        await connection.invalidate()
                except Exception:
                    # Invalidate so a possibly locked physical connection never returns to pool.
                    await connection.invalidate()

    async def claim_next(self, *, lease_seconds: int, max_attempts: int) -> ClaimResult | None:
        now = datetime.now(UTC)
        stale_running = and_(
            CheckoutExecution.status == CheckoutExecutionStatus.running,
            or_(
                CheckoutExecution.lease_expires_at.is_(None),
                CheckoutExecution.lease_expires_at <= now,
            ),
        )
        async with self._session_factory() as session, session.begin():
            execution = await session.scalar(
                select(CheckoutExecution)
                .where(
                    or_(
                        CheckoutExecution.status == CheckoutExecutionStatus.queued,
                        stale_running,
                    )
                )
                .order_by(CheckoutExecution.created_at, CheckoutExecution.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if execution is None:
                return None
            if execution.submitted_at is not None:
                notification = self._set_terminal(
                    session,
                    execution,
                    status=CheckoutExecutionStatus.outcome_unknown,
                    error_code=CheckoutErrorCode.payment_outcome_unknown,
                    now=now,
                )
                return ClaimResult(None, execution.cart_item_id, notification)
            if execution.attempt_count >= max_attempts:
                notification = self._set_terminal(
                    session,
                    execution,
                    status=CheckoutExecutionStatus.failed,
                    error_code=CheckoutErrorCode.checkout_failed,
                    now=now,
                )
                return ClaimResult(None, execution.cart_item_id, notification)
            execution.status = CheckoutExecutionStatus.running
            execution.attempt_count += 1
            execution.lease_expires_at = now + timedelta(seconds=lease_seconds)
            execution.error_code = None
            execution.error_message = None
            session.add(
                self._transition(
                    execution,
                    status=CheckoutExecutionStatus.running,
                    occurred_at=now,
                )
            )
            return ClaimResult(execution.id, execution.cart_item_id)

    async def prepare(self, execution_id: UUID) -> CheckoutContext:
        async with self._session_factory() as session, session.begin():
            execution, cart, agent, payment_method = await self._load_and_validate_locked(
                session, execution_id
            )
            if not isinstance(execution.adapter_config, Mapping):
                raise CheckoutError(CheckoutErrorCode.adapter_invalid)
            adapter = CheckoutAdapter.from_snapshot(execution.adapter_config)
            if cart.checkout_url is None:
                raise CheckoutError(CheckoutErrorCode.execution_invalid)
            return CheckoutContext(
                execution_id=execution.id,
                cart_item_id=cart.id,
                owner_id=execution.owner_id,
                agent_id=agent.id,
                payment_method_id=payment_method.id,
                adapter_key=execution.adapter_key,
                adapter=adapter,
                checkout_url=cart.checkout_url,
                checkout_origin=execution.checkout_origin,
                approved_title=cart.title,
                approved_quantity=cart.quantity,
                amount=execution.approved_amount,
                currency=execution.currency,
                provider=payment_method.provider,
                provider_card_id=payment_method.provider_payment_method_id,
                card_metadata=ExpectedCardMetadata(
                    owner_id=execution.owner_id,
                    last4=payment_method.card_last4,
                    brand=payment_method.card_brand,
                    expiry_month=payment_method.expiry_month,
                    expiry_year=payment_method.expiry_year,
                ),
                billing_details=deepcopy(payment_method.billing_details),
            )

    async def renew_lease(self, execution_id: UUID, *, lease_seconds: int) -> bool:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(CheckoutExecution)
                .where(
                    CheckoutExecution.id == execution_id,
                    CheckoutExecution.status == CheckoutExecutionStatus.running,
                )
                .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
            )
            return bool(result.rowcount)

    async def record_browser_session(self, execution_id: UUID, session_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            execution = await self._running_execution_locked(session, execution_id)
            execution.browserbase_session_id = session_id

    async def mark_submitted(self, execution_id: UUID, session_id: str) -> datetime:
        async with self._session_factory() as session, session.begin():
            execution, _, _, _ = await self._load_and_validate_locked(session, execution_id)
            if execution.submitted_at is not None:
                raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)
            now = datetime.now(UTC)
            execution.browserbase_session_id = session_id
            execution.submitted_at = now
            return now

    async def retry_or_fail(
        self,
        execution_id: UUID,
        error: CheckoutError,
        *,
        max_attempts: int,
    ) -> TerminalNotification | None:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            execution = await self._execution_locked(session, execution_id)
            if execution.status in TERMINAL_STATUSES:
                return None
            if execution.submitted_at is not None:
                return self._set_terminal(
                    session,
                    execution,
                    status=CheckoutExecutionStatus.outcome_unknown,
                    error_code=CheckoutErrorCode.payment_outcome_unknown,
                    now=now,
                )
            if error.retryable and execution.attempt_count < max_attempts:
                execution.status = CheckoutExecutionStatus.queued
                execution.lease_expires_at = None
                execution.error_code = error.code.value
                execution.error_message = error.safe_message
                session.add(
                    self._transition(
                        execution,
                        status=CheckoutExecutionStatus.queued,
                        error_code=error.code,
                        occurred_at=now,
                    )
                )
                return None
            return self._set_terminal(
                session,
                execution,
                status=CheckoutExecutionStatus.failed,
                error_code=error.code,
                now=now,
            )

    async def finish_terminal(
        self,
        execution_id: UUID,
        *,
        status: CheckoutExecutionStatus,
        error_code: CheckoutErrorCode,
        merchant_order_reference: str | None = None,
    ) -> TerminalNotification | None:
        if status not in {
            CheckoutExecutionStatus.failed,
            CheckoutExecutionStatus.action_required,
            CheckoutExecutionStatus.outcome_unknown,
        }:
            raise ValueError("finish_terminal requires a non-success terminal status")
        async with self._session_factory() as session, session.begin():
            execution = await self._execution_locked(session, execution_id)
            if execution.status in TERMINAL_STATUSES:
                return None
            execution.merchant_order_reference = merchant_order_reference
            return self._set_terminal(
                session,
                execution,
                status=status,
                error_code=error_code,
                now=datetime.now(UTC),
            )

    async def succeed(
        self,
        execution_id: UUID,
        *,
        provider_reference: str,
        merchant_order_reference: str | None,
        receipt_url: str | None,
    ) -> TerminalNotification | None:
        now = datetime.now(UTC)
        async with self._session_factory() as session, session.begin():
            execution = await self._execution_locked(session, execution_id)
            if execution.status == CheckoutExecutionStatus.succeeded:
                return None
            if execution.status in TERMINAL_STATUSES:
                return None
            execution, cart, agent, payment_method = await self._load_and_validate_locked(
                session, execution_id, execution=execution
            )
            if execution.submitted_at is None:
                raise CheckoutError(CheckoutErrorCode.execution_invalid)

            purchase = Purchase(
                owner_id=execution.owner_id,
                agent_id=agent.id,
                payment_method_id=payment_method.id,
                cart_item_id=cart.id,
                status=PurchaseStatus.completed,
                amount=execution.approved_amount,
                currency=execution.currency,
                provider_reference=provider_reference,
                merchant_order_reference=merchant_order_reference,
                receipt_url=receipt_url,
                purchased_at=now,
            )
            session.add(purchase)
            cart.status = CartItemStatus.purchased
            execution.status = CheckoutExecutionStatus.succeeded
            execution.completed_at = now
            execution.lease_expires_at = None
            execution.error_code = None
            execution.error_message = None
            execution.merchant_order_reference = merchant_order_reference
            session.add(
                self._transition(
                    execution,
                    status=CheckoutExecutionStatus.succeeded,
                    occurred_at=now,
                )
            )
            try:
                await session.flush()
            except IntegrityError:
                raise CheckoutError(CheckoutErrorCode.execution_invalid) from None
            session.add(
                self._event(
                    execution,
                    status=CheckoutExecutionStatus.succeeded,
                    purchase_id=purchase.id,
                )
            )
            return TerminalNotification(
                execution_id=execution.id,
                cart_item_id=cart.id,
                status=CheckoutExecutionStatus.succeeded,
                error_code=None,
                purchase_id=purchase.id,
            )

    async def _load_and_validate_locked(
        self,
        session: AsyncSession,
        execution_id: UUID,
        *,
        execution: CheckoutExecution | None = None,
    ) -> tuple[CheckoutExecution, CartItem, Agent, PaymentMethod]:
        execution = execution or await self._running_execution_locked(session, execution_id)
        if execution.status != CheckoutExecutionStatus.running:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        cart = await session.scalar(
            select(CartItem).where(CartItem.id == execution.cart_item_id).with_for_update()
        )
        agent = await session.scalar(
            select(Agent).where(Agent.id == execution.agent_id).with_for_update()
        )
        payment_method = await session.scalar(
            select(PaymentMethod)
            .where(PaymentMethod.id == execution.payment_method_id)
            .with_for_update()
        )
        if cart is None or agent is None or payment_method is None:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        if (
            cart.owner_id != execution.owner_id
            or cart.agent_id != execution.agent_id
            or agent.owner_id != execution.owner_id
            or payment_method.owner_id != execution.owner_id
        ):
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        if cart.status != CartItemStatus.approved:
            raise CheckoutError(CheckoutErrorCode.cart_not_approved)
        if cart.billing_period is not None:
            raise CheckoutError(CheckoutErrorCode.recurring_unsupported)
        if agent.status != AgentStatus.active:
            raise CheckoutError(CheckoutErrorCode.agent_inactive)
        if (
            payment_method.status != PaymentMethodStatus.active
            or cart.selected_payment_method_id != payment_method.id
        ):
            raise CheckoutError(CheckoutErrorCode.payment_method_unavailable)
        unresolved_sibling = await session.scalar(
            select(CheckoutExecution.id)
            .where(
                CheckoutExecution.owner_id == execution.owner_id,
                CheckoutExecution.payment_method_id == payment_method.id,
                CheckoutExecution.id != execution.id,
                CheckoutExecution.status.in_(
                    (
                        CheckoutExecutionStatus.action_required,
                        CheckoutExecutionStatus.outcome_unknown,
                    )
                ),
            )
            .limit(1)
            .with_for_update()
        )
        if unresolved_sibling is not None:
            raise CheckoutError(CheckoutErrorCode.card_reconciliation_required)
        assignment = await session.scalar(
            select(AgentPaymentMethod)
            .where(
                AgentPaymentMethod.agent_id == agent.id,
                AgentPaymentMethod.payment_method_id == payment_method.id,
            )
            .with_for_update()
        )
        if assignment is None:
            raise CheckoutError(CheckoutErrorCode.payment_method_unassigned)
        expected_amount: Decimal = cart.unit_price * cart.quantity
        if expected_amount != execution.approved_amount:
            raise CheckoutError(CheckoutErrorCode.amount_mismatch)
        if cart.currency.upper() != execution.currency.upper():
            raise CheckoutError(CheckoutErrorCode.currency_mismatch)
        if cart.checkout_adapter != execution.adapter_key or cart.checkout_url is None:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        if not isinstance(execution.adapter_config, Mapping):
            raise CheckoutError(CheckoutErrorCode.adapter_invalid)
        adapter = CheckoutAdapter.from_snapshot(execution.adapter_config)
        validate_checkout_url(cart.checkout_url, adapter.allowed_origins)
        if normalize_origin(cart.checkout_url) != normalize_origin(execution.checkout_origin):
            raise CheckoutError(CheckoutErrorCode.origin_blocked)
        return execution, cart, agent, payment_method

    @staticmethod
    async def _execution_locked(session: AsyncSession, execution_id: UUID) -> CheckoutExecution:
        execution = await session.scalar(
            select(CheckoutExecution).where(CheckoutExecution.id == execution_id).with_for_update()
        )
        if execution is None:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        return execution

    async def _running_execution_locked(
        self, session: AsyncSession, execution_id: UUID
    ) -> CheckoutExecution:
        execution = await self._execution_locked(session, execution_id)
        if execution.status != CheckoutExecutionStatus.running:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        return execution

    def _set_terminal(
        self,
        session: AsyncSession,
        execution: CheckoutExecution,
        *,
        status: CheckoutExecutionStatus,
        error_code: CheckoutErrorCode,
        now: datetime,
    ) -> TerminalNotification:
        execution.status = status
        execution.completed_at = now
        execution.lease_expires_at = None
        execution.error_code = error_code.value
        execution.error_message = SAFE_ERROR_MESSAGES[error_code]
        session.add_all(
            (
                self._event(execution, status=status, error_code=error_code.value),
                self._transition(
                    execution,
                    status=status,
                    error_code=error_code,
                    occurred_at=now,
                ),
            )
        )
        return TerminalNotification(
            execution_id=execution.id,
            cart_item_id=execution.cart_item_id,
            status=status,
            error_code=error_code.value,
        )

    @staticmethod
    def _event(
        execution: CheckoutExecution,
        *,
        status: CheckoutExecutionStatus,
        error_code: str | None = None,
        purchase_id: UUID | None = None,
    ) -> CheckoutEvent:
        return CheckoutEvent(
            execution_id=execution.id,
            owner_id=execution.owner_id,
            agent_id=execution.agent_id,
            cart_item_id=execution.cart_item_id,
            purchase_id=purchase_id,
            status=status,
            amount=execution.approved_amount,
            currency=execution.currency,
            error_code=error_code,
        )

    @staticmethod
    def _transition(
        execution: CheckoutExecution,
        *,
        status: CheckoutExecutionStatus,
        error_code: CheckoutErrorCode | None = None,
        occurred_at: datetime,
    ) -> CheckoutStatusTransition:
        return CheckoutStatusTransition(
            execution_id=execution.id,
            status=status,
            attempt_count=execution.attempt_count,
            error_code=error_code.value if error_code is not None else None,
            occurred_at=occurred_at,
        )
