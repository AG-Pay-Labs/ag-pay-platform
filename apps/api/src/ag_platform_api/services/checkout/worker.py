import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from ag_platform_api.models import CheckoutExecutionStatus
from ag_platform_api.services.checkout.browserbase import BrowserbaseCheckout
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.repository import (
    ClaimResult,
    SqlAlchemyCheckoutRepository,
    TerminalNotification,
)
from ag_platform_api.services.checkout.stripe_issuing import StripeIssuingGateway
from ag_platform_api.services.checkout.stripe_payments_demo import (
    DEMO_REFERENCES,
    StripeHostedVerification,
    StripePaymentsDemoGateway,
)
from ag_platform_api.services.checkout.types import (
    AuthorizationOutcome,
    AuthorizationResult,
    BrowserCheckoutResult,
    CheckoutContext,
    decimal_to_minor,
)

logger = logging.getLogger(__name__)


class BrokerLike(Protocol):
    async def publish(self, event_type: str, payload: dict[str, object]) -> bool: ...


class CheckoutWorker:
    def __init__(
        self,
        *,
        repository: SqlAlchemyCheckoutRepository,
        browser: BrowserbaseCheckout,
        issuing: StripeIssuingGateway | None,
        demo: StripePaymentsDemoGateway | None = None,
        broker: BrokerLike | None,
        lease_seconds: int,
        max_attempts: int,
        poll_seconds: float,
        authorization_timeout_seconds: float = 30,
        authorization_poll_seconds: float = 1,
        demo_observation_seconds: float = 0,
    ) -> None:
        self._repository = repository
        self._browser = browser
        self._issuing = issuing
        self._demo = demo
        self._broker = broker
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._poll_seconds = poll_seconds
        self._authorization_timeout_seconds = authorization_timeout_seconds
        self._authorization_poll_seconds = authorization_poll_seconds
        self._demo_observation_seconds = demo_observation_seconds

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                worked = await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("checkout worker iteration failed")
                worked = False
            if worked:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                pass

    async def process_once(self) -> bool:
        claim = await self._repository.claim_next(
            lease_seconds=self._lease_seconds,
            max_attempts=self._max_attempts,
        )
        if claim is None:
            return False
        if claim.notification is not None:
            await self._after_commit(claim.notification)
            return True
        if claim.execution_id is None:  # pragma: no cover - ClaimResult invariant
            return True
        await self._process_claim(claim)
        return True

    async def _process_claim(self, claim: ClaimResult) -> None:
        execution_id = claim.execution_id
        if execution_id is None:  # pragma: no cover - guarded by process_once
            return
        heartbeat = asyncio.create_task(self._heartbeat(execution_id))
        notification: TerminalNotification | None = None
        try:
            initial_context = await self._repository.prepare(execution_id)
            if not self._provider_supported(initial_context):
                raise CheckoutError(CheckoutErrorCode.provider_unsupported)
            async with self._repository.card_lock(initial_context.provider_card_id):
                try:
                    context = await self._repository.prepare(execution_id)
                    if not self._provider_supported(context) or (
                        context.provider_card_id != initial_context.provider_card_id
                    ):
                        raise CheckoutError(CheckoutErrorCode.provider_unsupported)
                    notification = await self._execute_context(context)
                except CheckoutError as error:
                    notification = await self._handle_checkout_error(execution_id, error)
                except Exception:
                    notification = await self._repository.retry_or_fail(
                        execution_id,
                        CheckoutError(CheckoutErrorCode.checkout_failed, retryable=True),
                        max_attempts=self._max_attempts,
                    )
        except CheckoutError as error:
            notification = await self._handle_checkout_error(execution_id, error)
        except Exception:
            notification = await self._repository.retry_or_fail(
                execution_id,
                CheckoutError(CheckoutErrorCode.checkout_failed, retryable=True),
                max_attempts=self._max_attempts,
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        if notification is not None:
            await self._after_commit(notification)

    async def _execute_context(
        self,
        context: CheckoutContext,
    ) -> TerminalNotification | None:
        if context.provider == "prototype-vault":
            return await self._execute_demo_context(context)
        if self._issuing is None:
            raise CheckoutError(CheckoutErrorCode.provider_unsupported)
        return await self._execute_issuing_context(context)

    def _provider_supported(self, context: CheckoutContext) -> bool:
        if context.provider == "stripe_issuing":
            return self._issuing is not None
        return (
            context.provider == "prototype-vault"
            and self._demo is not None
            and context.provider_card_id in DEMO_REFERENCES
        )

    async def _execute_issuing_context(
        self,
        context: CheckoutContext,
    ) -> TerminalNotification | None:
        if self._issuing is None:  # pragma: no cover - guarded by routing
            raise CheckoutError(CheckoutErrorCode.provider_unsupported)
        execution_id = context.execution_id
        submitted_at: datetime | None = None
        existing_authorization_ids: frozenset[str] | None = None
        amount_minor = context.amount_minor

        async def load_card():
            return await self._issuing.retrieve_card(
                context.provider_card_id,
                context.card_metadata,
            )

        async def on_session_started(session_id: str) -> None:
            await self._repository.record_browser_session(execution_id, session_id)

        async def prepare_submission() -> None:
            nonlocal existing_authorization_ids
            existing_authorization_ids = await self._issuing.snapshot_authorization_ids(
                card_id=context.provider_card_id,
                created_at_or_after=datetime.now(UTC),
            )

        async def mark_submitted(session_id: str) -> None:
            nonlocal submitted_at
            submitted_at = await self._repository.mark_submitted(execution_id, session_id)

        result = await self._browser.run(
            context,
            load_card=load_card,
            on_session_started=on_session_started,
            prepare_submission=prepare_submission,
            mark_submitted=mark_submitted,
        )
        if submitted_at is None or existing_authorization_ids is None:
            raise CheckoutError(CheckoutErrorCode.execution_invalid)
        if result.outcome == AuthorizationOutcome.action_required:
            return await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.action_required,
                error_code=CheckoutErrorCode.checkout_action_required,
                merchant_order_reference=result.order_reference,
            )
        authorization = await self._reconcile_authorization(
            context,
            submitted_at,
            existing_authorization_ids,
            amount_minor,
        )
        if authorization.outcome == AuthorizationOutcome.declined:
            notification = await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.failed,
                error_code=CheckoutErrorCode.payment_declined,
                merchant_order_reference=result.order_reference,
            )
        elif authorization.outcome == AuthorizationOutcome.unknown:
            notification = await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.outcome_unknown,
                error_code=CheckoutErrorCode.payment_outcome_unknown,
                merchant_order_reference=result.order_reference,
            )
        elif authorization.provider_reference is None:
            notification = await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.outcome_unknown,
                error_code=CheckoutErrorCode.payment_outcome_unknown,
                merchant_order_reference=result.order_reference,
            )
        else:
            notification = await self._repository.succeed(
                execution_id,
                provider_reference=authorization.provider_reference,
                merchant_order_reference=result.order_reference,
                receipt_url=result.receipt_url,
            )
        return notification

    async def _execute_demo_context(
        self,
        context: CheckoutContext,
    ) -> TerminalNotification | None:
        if self._demo is None:  # pragma: no cover - guarded by routing
            raise CheckoutError(CheckoutErrorCode.provider_unsupported)
        if context.adapter.checkout_mode == "stripe_hosted_test":
            return await self._execute_hosted_demo_context(context)
        if context.adapter.checkout_mode != "direct":
            raise CheckoutError(CheckoutErrorCode.adapter_invalid)
        execution_id = context.execution_id
        parsed = urlsplit(context.checkout_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["agpay_execution_id"] = str(execution_id)
        demo_context = replace(
            context,
            checkout_url=urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
            ),
        )
        submitted_at: datetime | None = None

        async def load_card():
            return await self._demo.retrieve_card(context.provider_card_id)

        async def on_session_started(session_id: str) -> None:
            await self._repository.record_browser_session(execution_id, session_id)

        async def prepare_submission() -> None:
            if self._demo_observation_seconds:
                await asyncio.sleep(self._demo_observation_seconds)

        async def mark_submitted(session_id: str) -> None:
            nonlocal submitted_at
            submitted_at = await self._repository.mark_submitted(execution_id, session_id)

        result = await self._browser.run(
            demo_context,
            load_card=load_card,
            on_session_started=on_session_started,
            prepare_submission=prepare_submission,
            mark_submitted=mark_submitted,
        )
        if submitted_at is None or result.order_reference is None:
            raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)
        verified = await self._demo.verify_payment_intent(
            payment_intent_id=result.order_reference,
            execution_id=execution_id,
            amount_minor=context.amount_minor,
            currency=context.currency,
        )
        if verified.outcome == AuthorizationOutcome.declined:
            return await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.failed,
                error_code=CheckoutErrorCode.payment_declined,
                merchant_order_reference=result.order_reference,
            )
        if verified.outcome == AuthorizationOutcome.action_required:
            return await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.action_required,
                error_code=CheckoutErrorCode.checkout_action_required,
                merchant_order_reference=result.order_reference,
            )
        if verified.outcome != AuthorizationOutcome.approved or verified.provider_reference is None:
            return await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.outcome_unknown,
                error_code=CheckoutErrorCode.payment_outcome_unknown,
                merchant_order_reference=result.order_reference,
            )
        return await self._repository.succeed(
            execution_id,
            provider_reference=verified.provider_reference,
            merchant_order_reference=result.order_reference,
            receipt_url=result.receipt_url,
        )

    async def _execute_hosted_demo_context(
        self,
        context: CheckoutContext,
    ) -> TerminalNotification | None:
        if self._demo is None:  # pragma: no cover - guarded by routing
            raise CheckoutError(CheckoutErrorCode.provider_unsupported)
        execution_id = context.execution_id
        try:
            unit_amount_minor = decimal_to_minor(
                context.amount / context.approved_quantity,
                context.currency,
            )
        except (ArithmeticError, CheckoutError):
            raise CheckoutError(CheckoutErrorCode.currency_precision_invalid) from None
        hosted = await self._demo.create_checkout_session(
            execution_id=execution_id,
            title=context.approved_title,
            quantity=context.approved_quantity,
            unit_amount_minor=unit_amount_minor,
            currency=context.currency,
            collect_phone=bool(context.billing_details.get("phone")),
        )
        hosted_context = replace(context, checkout_url=hosted.checkout_url)
        submitted_at: datetime | None = None
        verified: StripeHostedVerification | None = None

        async def load_card():
            return await self._demo.retrieve_card(context.provider_card_id)

        async def on_session_started(session_id: str) -> None:
            await self._repository.record_browser_session(execution_id, session_id)

        async def prepare_submission() -> None:
            return None

        async def mark_submitted(session_id: str) -> None:
            nonlocal submitted_at
            submitted_at = await self._repository.mark_submitted(execution_id, session_id)

        async def observe_outcome() -> AuthorizationOutcome:
            nonlocal verified
            verified = await self._reconcile_hosted_checkout(
                session_id=hosted.session_id,
                execution_id=execution_id,
                amount_minor=context.amount_minor,
                currency=context.currency,
            )
            return verified.outcome

        browser_result = BrowserCheckoutResult(
            order_reference=None,
            receipt_url=None,
            outcome=AuthorizationOutcome.unknown,
        )
        try:
            browser_result = await self._browser.run(
                hosted_context,
                load_card=load_card,
                on_session_started=on_session_started,
                prepare_submission=prepare_submission,
                mark_submitted=mark_submitted,
                observe_outcome=observe_outcome,
            )
        except CheckoutError:
            if submitted_at is None:
                raise
        if submitted_at is None:
            raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)

        if verified is None:
            verified = await self._reconcile_hosted_checkout(
                session_id=hosted.session_id,
                execution_id=execution_id,
                amount_minor=context.amount_minor,
                currency=context.currency,
            )
        if verified.outcome == AuthorizationOutcome.declined:
            expired = await self._demo.expire_checkout_session(
                hosted.session_id,
                execution_id,
            )
            if not expired:
                return await self._repository.finish_terminal(
                    execution_id,
                    status=CheckoutExecutionStatus.outcome_unknown,
                    error_code=CheckoutErrorCode.payment_outcome_unknown,
                    merchant_order_reference=hosted.session_id,
                )
            return await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.failed,
                error_code=CheckoutErrorCode.payment_declined,
                merchant_order_reference=hosted.session_id,
            )
        if verified.outcome == AuthorizationOutcome.action_required:
            return await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.action_required,
                error_code=CheckoutErrorCode.checkout_action_required,
                merchant_order_reference=hosted.session_id,
            )
        if verified.outcome != AuthorizationOutcome.approved or verified.provider_reference is None:
            return await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.outcome_unknown,
                error_code=CheckoutErrorCode.payment_outcome_unknown,
                merchant_order_reference=hosted.session_id,
            )
        return await self._repository.succeed(
            execution_id,
            provider_reference=verified.provider_reference,
            merchant_order_reference=hosted.session_id,
            receipt_url=verified.receipt_url or browser_result.receipt_url,
        )

    async def _reconcile_hosted_checkout(
        self,
        *,
        session_id: str,
        execution_id: UUID,
        amount_minor: int,
        currency: str,
    ) -> StripeHostedVerification:
        if self._demo is None:  # pragma: no cover - guarded by routing
            raise CheckoutError(CheckoutErrorCode.provider_unsupported)
        deadline = time.monotonic() + self._authorization_timeout_seconds
        while True:
            try:
                verified = await self._demo.verify_checkout_session(
                    session_id=session_id,
                    execution_id=execution_id,
                    amount_minor=amount_minor,
                    currency=currency,
                )
            except CheckoutError as error:
                if error.code != CheckoutErrorCode.payment_outcome_unknown:
                    raise
                verified = StripeHostedVerification(AuthorizationOutcome.unknown)
            if verified.outcome != AuthorizationOutcome.unknown:
                return verified
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return verified
            await asyncio.sleep(min(self._authorization_poll_seconds, remaining))

    async def _reconcile_authorization(
        self,
        context: CheckoutContext,
        submitted_at: datetime,
        excluded_authorization_ids: frozenset[str],
        amount_minor: int,
    ) -> AuthorizationResult:
        deadline = time.monotonic() + self._authorization_timeout_seconds
        while True:
            try:
                authorization = await self._issuing.find_authorization(
                    card_id=context.provider_card_id,
                    created_at_or_after=submitted_at,
                    amount_minor=amount_minor,
                    currency=context.currency,
                    excluded_authorization_ids=excluded_authorization_ids,
                )
            except CheckoutError as error:
                if error.code != CheckoutErrorCode.payment_outcome_unknown:
                    raise
                authorization = AuthorizationResult(AuthorizationOutcome.unknown)
            if authorization.outcome != AuthorizationOutcome.unknown:
                return authorization
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return authorization
            await asyncio.sleep(min(self._authorization_poll_seconds, remaining))

    async def _handle_checkout_error(
        self,
        execution_id: UUID,
        error: CheckoutError,
    ) -> TerminalNotification | None:
        if error.code == CheckoutErrorCode.checkout_action_required:
            return await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.action_required,
                error_code=error.code,
            )
        if error.code == CheckoutErrorCode.payment_outcome_unknown:
            return await self._repository.finish_terminal(
                execution_id,
                status=CheckoutExecutionStatus.outcome_unknown,
                error_code=error.code,
            )
        return await self._repository.retry_or_fail(
            execution_id,
            error,
            max_attempts=self._max_attempts,
        )

    async def _heartbeat(self, execution_id: UUID) -> None:
        interval = max(5.0, self._lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                active = await self._repository.renew_lease(
                    execution_id, lease_seconds=self._lease_seconds
                )
            except Exception:
                active = False
            if not active:
                return

    async def _after_commit(self, notification: TerminalNotification) -> None:
        logger.info(
            "checkout execution_id=%s cart_item_id=%s status=%s error_code=%s",
            notification.execution_id,
            notification.cart_item_id,
            notification.status.value,
            notification.error_code,
        )
        if self._broker is None:
            return
        payload: dict[str, object] = {
            "execution_id": notification.execution_id,
            "cart_item_id": notification.cart_item_id,
            "status": notification.status.value,
        }
        if notification.error_code is not None:
            payload["error_code"] = notification.error_code
        if notification.purchase_id is not None:
            payload["purchase_id"] = notification.purchase_id
        try:
            await self._broker.publish(f"checkout.{notification.status.value}", payload)
        except Exception:
            # The committed CheckoutEvent is authoritative; broker publication is best effort.
            pass
