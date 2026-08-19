from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ag_platform_api.core.config import LOCAL_DIRECT_CARD_PROVIDER
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
    StoredCardCredential,
)
from ag_platform_api.services.checkout.errors import (
    SAFE_ERROR_MESSAGES,
    CheckoutError,
    CheckoutErrorCode,
)
from ag_platform_api.services.checkout.origins import (
    normalize_origin,
    validate_checkout_url,
    validate_stripe_hosted_test_checkout_url,
)
from ag_platform_api.services.checkout.stripe_payments_demo import DEMO_REFERENCES
from ag_platform_api.services.checkout.types import (
    CheckoutAdapter,
    CheckoutContext,
    ExpectedCardMetadata,
    decimal_to_minor,
    is_card_expired,
    normalize_item_text,
)

TERMINAL_STATUSES = frozenset(
    {
        CheckoutExecutionStatus.succeeded,
        CheckoutExecutionStatus.failed,
        CheckoutExecutionStatus.action_required,
        CheckoutExecutionStatus.outcome_unknown,
    }
)
STRIPE_HOSTED_ADAPTER_KEY = "stripe-hosted"
STRIPE_CHECKOUT_ORIGIN = "https://checkout.stripe.com"
TRUSTED_RESULT_ORIGIN = "https://letyouragentspay.com"
TRUSTED_RECEIPT_PATH = "/playground/success"


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


@dataclass(frozen=True, slots=True, repr=False)
class HostedReconciliationCandidate:
    execution_id: UUID
    cart_item_id: UUID
    owner_id: UUID
    agent_id: UUID
    payment_method_id: UUID
    stripe_session_id: str
    approved_title: str
    amount: Decimal
    amount_minor: int
    currency: str
    receipt_url: str
    already_succeeded: bool

    def __repr__(self) -> str:
        return (
            "HostedReconciliationCandidate("
            f"execution_id={self.execution_id!r}, cart_item_id={self.cart_item_id!r}, "
            f"owner_id={self.owner_id!r}, stripe_session_id=<redacted>, "
            f"already_succeeded={self.already_succeeded!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class HostedPaymentProof:
    session_id: str
    order_reference: str
    offer_slug: str
    offer_name: str
    amount_minor: int
    currency: str

    def __repr__(self) -> str:
        return "HostedPaymentProof(<redacted>)"


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
                provider_request_id=execution.provider_request_id,
                card_metadata=ExpectedCardMetadata(
                    owner_id=execution.owner_id,
                    last4=payment_method.card_last4,
                    brand=payment_method.card_brand,
                    expiry_month=payment_method.expiry_month,
                    expiry_year=payment_method.expiry_year,
                ),
                billing_details=deepcopy(payment_method.billing_details),
                resolved_form_config=(
                    deepcopy(execution.resolved_form_config)
                    if isinstance(execution.resolved_form_config, Mapping)
                    else None
                ),
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

    async def record_resolved_form_config(
        self,
        execution_id: UUID,
        config: Mapping[str, object],
    ) -> None:
        snapshot = deepcopy(dict(config))
        async with self._session_factory() as session, session.begin():
            execution = await self._running_execution_locked(session, execution_id)
            if execution.submitted_at is not None:
                raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)
            if execution.resolved_form_config is not None:
                if execution.resolved_form_config != snapshot:
                    raise CheckoutError(CheckoutErrorCode.form_analysis_failed)
                return
            execution.resolved_form_config = snapshot

    async def record_provider_request(self, execution_id: UUID, request_id: str) -> str:
        """Persist the external request before any payment credential is retrieved."""
        async with self._session_factory() as session, session.begin():
            execution = await self._running_execution_locked(session, execution_id)
            if execution.provider_request_id is not None:
                if execution.provider_request_id != request_id:
                    raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)
                return execution.provider_request_id
            execution.provider_request_id = request_id
            return request_id

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

    async def hosted_reconciliation_candidate(
        self,
        *,
        owner_id: UUID,
        cart_item_id: UUID,
    ) -> HostedReconciliationCandidate | None:
        """Snapshot an owner-scoped unknown hosted checkout without locking it."""
        async with self._session_factory() as session:
            return await self._load_hosted_reconciliation_candidate(
                session,
                owner_id=owner_id,
                cart_item_id=cart_item_id,
                for_update=False,
            )

    async def reconcile_hosted_succeeded(
        self,
        candidate: HostedReconciliationCandidate,
        proof: HostedPaymentProof,
    ) -> TerminalNotification | None:
        """Record independently proven payment without submitting payment again."""
        async with self._session_factory() as session, session.begin():
            # Approval locks the selected method before checking unresolved
            # executions. Preserve that order so releasing quarantine cannot
            # deadlock a concurrent proposal approval for the same method.
            payment_method = await session.scalar(
                select(PaymentMethod)
                .where(
                    PaymentMethod.id == candidate.payment_method_id,
                    PaymentMethod.owner_id == candidate.owner_id,
                )
                .with_for_update()
            )
            if payment_method is None:
                raise CheckoutError(CheckoutErrorCode.execution_invalid)
            current = await self._load_hosted_reconciliation_candidate(
                session,
                owner_id=candidate.owner_id,
                cart_item_id=candidate.cart_item_id,
                for_update=True,
            )
            if current is None:
                raise CheckoutError(CheckoutErrorCode.execution_invalid)
            if current.already_succeeded:
                return None
            if current != candidate or not self.proof_matches_candidate(proof, current):
                raise CheckoutError(CheckoutErrorCode.execution_invalid)

            execution = await self._execution_locked(session, current.execution_id)
            cart = await session.scalar(
                select(CartItem).where(CartItem.id == current.cart_item_id).with_for_update()
            )
            if cart is None:
                raise CheckoutError(CheckoutErrorCode.execution_invalid)
            now = datetime.now(UTC)
            purchase = Purchase(
                owner_id=current.owner_id,
                agent_id=current.agent_id,
                payment_method_id=current.payment_method_id,
                cart_item_id=current.cart_item_id,
                status=PurchaseStatus.completed,
                amount=current.amount,
                currency=current.currency,
                provider_reference=proof.session_id,
                merchant_order_reference=proof.order_reference,
                receipt_url=current.receipt_url,
                purchased_at=now,
            )
            session.add(purchase)
            cart.status = CartItemStatus.purchased
            execution.status = CheckoutExecutionStatus.succeeded
            execution.completed_at = now
            execution.lease_expires_at = None
            execution.error_code = None
            execution.error_message = None
            execution.merchant_order_reference = proof.order_reference
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

    async def _load_hosted_reconciliation_candidate(
        self,
        session: AsyncSession,
        *,
        owner_id: UUID,
        cart_item_id: UUID,
        for_update: bool,
    ) -> HostedReconciliationCandidate | None:
        execution_query = select(CheckoutExecution).where(
            CheckoutExecution.owner_id == owner_id,
            CheckoutExecution.cart_item_id == cart_item_id,
        )
        if for_update:
            execution_query = execution_query.with_for_update()
        execution = await session.scalar(execution_query)
        if execution is None:
            return None

        cart_query = select(CartItem).where(
            CartItem.id == cart_item_id,
            CartItem.owner_id == owner_id,
        )
        agent_query = select(Agent).where(
            Agent.id == execution.agent_id,
            Agent.owner_id == owner_id,
        )
        payment_query = select(PaymentMethod).where(
            PaymentMethod.id == execution.payment_method_id,
            PaymentMethod.owner_id == owner_id,
        )
        if for_update:
            cart_query = cart_query.with_for_update()
            agent_query = agent_query.with_for_update()
            payment_query = payment_query.with_for_update()
        cart = await session.scalar(cart_query)
        agent = await session.scalar(agent_query)
        payment_method = await session.scalar(payment_query)
        if cart is None or agent is None or payment_method is None:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)

        if (
            cart.agent_id != execution.agent_id
            or cart.selected_payment_method_id != execution.payment_method_id
            or execution.adapter_key != STRIPE_HOSTED_ADAPTER_KEY
            or cart.checkout_adapter != STRIPE_HOSTED_ADAPTER_KEY
            or cart.checkout_url is None
            or cart.billing_period is not None
            or execution.submitted_at is None
            or execution.completed_at is None
            or execution.attempt_count < 1
            or execution.approved_amount != cart.unit_price * cart.quantity
            or execution.currency.upper() != cart.currency.upper()
            or execution.checkout_origin != STRIPE_CHECKOUT_ORIGIN
            or payment_method.provider != "prototype-vault"
            or payment_method.provider_payment_method_id not in DEMO_REFERENCES
            or execution.provider_request_id is not None
        ):
            raise CheckoutError(CheckoutErrorCode.execution_invalid)

        try:
            validate_stripe_hosted_test_checkout_url(cart.checkout_url)
            adapter = CheckoutAdapter.from_snapshot(execution.adapter_config)
            session_id = urlsplit(cart.checkout_url).path.rstrip("/").rsplit("/", 1)[-1]
            amount_minor = decimal_to_minor(execution.approved_amount, execution.currency)
        except CheckoutError:
            raise CheckoutError(CheckoutErrorCode.execution_invalid) from None
        if (
            adapter.checkout_mode != "stripe_hosted_test"
            or adapter.allowed_origins != (STRIPE_CHECKOUT_ORIGIN, TRUSTED_RESULT_ORIGIN)
            or adapter.result_origins != (TRUSTED_RESULT_ORIGIN,)
            or normalize_origin(cart.checkout_url) != execution.checkout_origin
        ):
            raise CheckoutError(CheckoutErrorCode.execution_invalid)

        purchase = await session.scalar(
            select(Purchase).where(Purchase.cart_item_id == cart.id).with_for_update()
            if for_update
            else select(Purchase).where(Purchase.cart_item_id == cart.id)
        )
        latest_transition = await session.scalar(
            select(CheckoutStatusTransition)
            .where(CheckoutStatusTransition.execution_id == execution.id)
            .order_by(CheckoutStatusTransition.sequence.desc())
            .limit(1)
        )
        events = list(
            await session.scalars(
                select(CheckoutEvent)
                .where(CheckoutEvent.execution_id == execution.id)
                .order_by(CheckoutEvent.cursor)
            )
        )
        receipt_url = f"{TRUSTED_RESULT_ORIGIN}{TRUSTED_RECEIPT_PATH}?session_id={session_id}"
        events_are_bound = all(
            event.owner_id == execution.owner_id
            and event.agent_id == execution.agent_id
            and event.cart_item_id == execution.cart_item_id
            and event.amount == execution.approved_amount
            and event.currency.upper() == execution.currency.upper()
            for event in events
        )

        if execution.status == CheckoutExecutionStatus.outcome_unknown:
            valid_unknown = (
                cart.status == CartItemStatus.approved
                and execution.error_code == CheckoutErrorCode.payment_outcome_unknown.value
                and purchase is None
                and latest_transition is not None
                and latest_transition.status == CheckoutExecutionStatus.outcome_unknown
                and latest_transition.attempt_count == execution.attempt_count
                and latest_transition.error_code == CheckoutErrorCode.payment_outcome_unknown.value
                and len(events) == 1
                and events_are_bound
                and events[0].status == CheckoutExecutionStatus.outcome_unknown
                and events[0].error_code == CheckoutErrorCode.payment_outcome_unknown.value
                and events[0].purchase_id is None
            )
            already_succeeded = False
        elif execution.status == CheckoutExecutionStatus.succeeded:
            valid_unknown = (
                cart.status == CartItemStatus.purchased
                and execution.error_code is None
                and execution.merchant_order_reference == session_id
                and purchase is not None
                and purchase.owner_id == owner_id
                and purchase.agent_id == execution.agent_id
                and purchase.payment_method_id == execution.payment_method_id
                and purchase.amount == execution.approved_amount
                and purchase.currency.upper() == execution.currency.upper()
                and purchase.provider_reference == session_id
                and purchase.merchant_order_reference == session_id
                and purchase.receipt_url == receipt_url
                and latest_transition is not None
                and latest_transition.status == CheckoutExecutionStatus.succeeded
                and latest_transition.attempt_count == execution.attempt_count
                and len(events) in {1, 2}
                and events_are_bound
                and events[-1].status == CheckoutExecutionStatus.succeeded
                and events[-1].error_code is None
                and events[-1].purchase_id == purchase.id
                and (
                    len(events) == 1
                    or (
                        events[0].status == CheckoutExecutionStatus.outcome_unknown
                        and events[0].error_code == CheckoutErrorCode.payment_outcome_unknown.value
                        and events[0].purchase_id is None
                    )
                )
            )
            already_succeeded = True
        else:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        if not valid_unknown:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)

        return HostedReconciliationCandidate(
            execution_id=execution.id,
            cart_item_id=cart.id,
            owner_id=owner_id,
            agent_id=execution.agent_id,
            payment_method_id=execution.payment_method_id,
            stripe_session_id=session_id,
            approved_title=cart.title,
            amount=execution.approved_amount,
            amount_minor=amount_minor,
            currency=execution.currency.upper(),
            receipt_url=receipt_url,
            already_succeeded=already_succeeded,
        )

    @staticmethod
    def proof_matches_candidate(
        proof: HostedPaymentProof,
        candidate: HostedReconciliationCandidate,
    ) -> bool:
        return (
            proof.session_id == candidate.stripe_session_id
            and proof.order_reference == candidate.stripe_session_id
            and normalize_item_text(proof.offer_name)
            == normalize_item_text(candidate.approved_title)
            and proof.amount_minor == candidate.amount_minor
            and proof.currency.upper() == candidate.currency
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
        if is_card_expired(payment_method.expiry_month, payment_method.expiry_year):
            raise CheckoutError(CheckoutErrorCode.payment_method_expired)
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
        if payment_method.provider == LOCAL_DIRECT_CARD_PROVIDER:
            if (
                adapter.checkout_mode != "direct"
                or adapter.payment_form_strategy != "browserbase_ai"
                or adapter.order_reference_selector is None
            ):
                raise CheckoutError(CheckoutErrorCode.adapter_invalid)
            credential = await session.scalar(
                select(StoredCardCredential.payment_method_id)
                .where(
                    StoredCardCredential.payment_method_id == payment_method.id,
                    StoredCardCredential.owner_id == execution.owner_id,
                )
                .with_for_update()
            )
            if credential is None:
                raise CheckoutError(CheckoutErrorCode.card_unavailable)
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
