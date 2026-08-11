import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from ag_platform_api.models import CheckoutExecutionStatus
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.repository import (
    ClaimResult,
    TerminalNotification,
)
from ag_platform_api.services.checkout.stripe_payments_demo import (
    StripeHostedSession,
    StripeHostedVerification,
)
from ag_platform_api.services.checkout.types import (
    AuthorizationOutcome,
    AuthorizationResult,
    BrowserCheckoutResult,
    CheckoutAdapter,
    CheckoutContext,
    ExpectedCardMetadata,
    IssuingCardSecret,
)
from ag_platform_api.services.checkout.worker import CheckoutWorker


def worker_context() -> CheckoutContext:
    owner_id = uuid4()
    return CheckoutContext(
        execution_id=uuid4(),
        cart_item_id=uuid4(),
        owner_id=owner_id,
        agent_id=uuid4(),
        payment_method_id=uuid4(),
        adapter_key="demo",
        adapter=CheckoutAdapter(
            allowed_origins=("https://merchant.example.test",),
            payment_origins=("https://payments.example.test",),
            product_title_selector="#product-title",
            quantity_selector="#quantity",
            total_selector="#total",
            card_number_selector="#number",
            expiry_selector="#expiry",
            cvc_selector="#cvc",
            submit_selector="#submit",
            success_selector="#success",
        ),
        checkout_url="https://merchant.example.test/checkout/one",
        checkout_origin="https://merchant.example.test",
        approved_title="Managed checkout",
        approved_quantity=2,
        amount=Decimal("25.00"),
        currency="EUR",
        provider="stripe_issuing",
        provider_card_id="ic_card123",
        card_metadata=ExpectedCardMetadata(owner_id, "4242", "visa", 12, 2030),
        billing_details={},
    )


class FakeRepository:
    def __init__(self, context: CheckoutContext) -> None:
        self.context = context
        self.claimed = False
        self.submitted = False
        self.locked = False
        self.provider_reference: str | None = None
        self.retry_errors: list[CheckoutErrorCode] = []

    @asynccontextmanager
    async def card_lock(self, card_id: str):
        assert card_id == self.context.provider_card_id
        assert not self.locked
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    async def claim_next(self, **_: object) -> ClaimResult | None:
        if self.claimed:
            return None
        self.claimed = True
        return ClaimResult(self.context.execution_id, self.context.cart_item_id)

    async def prepare(self, _: object) -> CheckoutContext:
        return self.context

    async def record_browser_session(self, _: object, __: str) -> None:
        return None

    async def mark_submitted(self, _: object, __: str) -> datetime:
        self.submitted = True
        return datetime.now(UTC)

    async def renew_lease(self, _: object, **__: object) -> bool:
        return True

    async def retry_or_fail(
        self,
        _: object,
        error: CheckoutError,
        **__: object,
    ) -> TerminalNotification | None:
        assert self.locked
        self.retry_errors.append(error.code)
        if not self.submitted:
            return None
        return TerminalNotification(
            self.context.execution_id,
            self.context.cart_item_id,
            CheckoutExecutionStatus.outcome_unknown,
            CheckoutErrorCode.payment_outcome_unknown.value,
        )

    async def succeed(
        self,
        _: object,
        *,
        provider_reference: str,
        merchant_order_reference: str | None,
        receipt_url: str | None,
    ) -> TerminalNotification:
        assert self.submitted
        assert self.locked
        assert merchant_order_reference is None
        assert receipt_url == "https://merchant.example.test/receipt/one"
        self.provider_reference = provider_reference
        return TerminalNotification(
            self.context.execution_id,
            self.context.cart_item_id,
            CheckoutExecutionStatus.succeeded,
            None,
            uuid4(),
        )

    async def finish_terminal(
        self,
        _: object,
        *,
        status: CheckoutExecutionStatus,
        error_code: CheckoutErrorCode,
        merchant_order_reference: str | None = None,
    ) -> TerminalNotification:
        assert self.submitted
        assert self.locked
        assert merchant_order_reference == "pi_declined123"
        return TerminalNotification(
            self.context.execution_id,
            self.context.cart_item_id,
            status,
            error_code.value,
        )


class HostedRepository(FakeRepository):
    def __init__(self, context: CheckoutContext) -> None:
        super().__init__(context)
        self.terminal_status: CheckoutExecutionStatus | None = None
        self.terminal_error: CheckoutErrorCode | None = None
        self.merchant_order_reference: str | None = None
        self.receipt_url: str | None = None

    async def succeed(
        self,
        _: object,
        *,
        provider_reference: str,
        merchant_order_reference: str | None,
        receipt_url: str | None,
    ) -> TerminalNotification:
        assert self.submitted
        assert self.locked
        assert merchant_order_reference == "cs_test_hosted123"
        self.provider_reference = provider_reference
        self.merchant_order_reference = merchant_order_reference
        self.receipt_url = receipt_url
        self.terminal_status = CheckoutExecutionStatus.succeeded
        return TerminalNotification(
            self.context.execution_id,
            self.context.cart_item_id,
            CheckoutExecutionStatus.succeeded,
            None,
            uuid4(),
        )

    async def finish_terminal(
        self,
        _: object,
        *,
        status: CheckoutExecutionStatus,
        error_code: CheckoutErrorCode,
        merchant_order_reference: str | None = None,
    ) -> TerminalNotification:
        assert self.submitted
        assert self.locked
        assert merchant_order_reference == "cs_test_hosted123"
        self.terminal_status = status
        self.terminal_error = error_code
        self.merchant_order_reference = merchant_order_reference
        return TerminalNotification(
            self.context.execution_id,
            self.context.cart_item_id,
            status,
            error_code.value,
        )


class FakeBrowser:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.runs = 0

    async def run(self, context: CheckoutContext, **callbacks: object) -> BrowserCheckoutResult:
        self.runs += 1
        assert self.repository.locked
        on_session_started = callbacks["on_session_started"]
        prepare_submission = callbacks["prepare_submission"]
        load_card = callbacks["load_card"]
        mark_submitted = callbacks["mark_submitted"]
        await on_session_started("session_12345")  # type: ignore[operator]
        await prepare_submission()  # type: ignore[operator]
        secret = await load_card()  # type: ignore[operator]
        assert "4242424242424242" not in repr(secret)
        await mark_submitted("session_12345")  # type: ignore[operator]
        return BrowserCheckoutResult(None, "https://merchant.example.test/receipt/one")


class FillFailingBrowser(FakeBrowser):
    async def run(self, context: CheckoutContext, **callbacks: object) -> BrowserCheckoutResult:
        self.runs += 1
        assert self.repository.locked
        await callbacks["on_session_started"]("session_12345")  # type: ignore[operator]
        await callbacks["prepare_submission"]()  # type: ignore[operator]
        await callbacks["load_card"]()  # type: ignore[operator]
        await callbacks["mark_submitted"]("session_12345")  # type: ignore[operator]
        raise CheckoutError(CheckoutErrorCode.payment_form_not_found)


class DemoDeclineBrowser(FakeBrowser):
    async def run(self, context: CheckoutContext, **callbacks: object) -> BrowserCheckoutResult:
        self.runs += 1
        assert f"agpay_execution_id={context.execution_id}" in context.checkout_url
        await callbacks["on_session_started"]("session_12345")  # type: ignore[operator]
        await callbacks["prepare_submission"]()  # type: ignore[operator]
        await callbacks["load_card"]()  # type: ignore[operator]
        await callbacks["mark_submitted"]("session_12345")  # type: ignore[operator]
        return BrowserCheckoutResult(
            "pi_declined123",
            None,
            AuthorizationOutcome.declined,
        )


class FakeDemo:
    async def retrieve_card(self, reference: str) -> IssuingCardSecret:
        assert reference == "pm_stripe_demo_decline"
        return IssuingCardSecret("4000000000000002", "123", 12, 2034)

    async def verify_payment_intent(self, **arguments: object) -> AuthorizationResult:
        assert arguments["payment_intent_id"] == "pi_declined123"
        return AuthorizationResult(AuthorizationOutcome.declined)


class FakeHostedDemo:
    def __init__(
        self,
        verification: StripeHostedVerification,
        reference: str,
        *,
        expiration_confirmed: bool = True,
    ) -> None:
        self.verification = verification
        self.reference = reference
        self.expiration_confirmed = expiration_confirmed
        self.create_calls = 0
        self.verify_calls = 0
        self.card_calls = 0
        self.expire_calls = 0
        self.execution_id: object | None = None

    async def create_checkout_session(self, **arguments: object) -> StripeHostedSession:
        self.create_calls += 1
        self.execution_id = arguments["execution_id"]
        assert arguments["title"] == "Managed checkout"
        assert arguments["quantity"] == 2
        assert arguments["unit_amount_minor"] == 1250
        assert arguments["currency"] == "EUR"
        assert arguments["collect_phone"] is True
        return StripeHostedSession(
            "cs_test_hosted123",
            "https://checkout.stripe.com/c/pay/cs_test_hosted123#fixture",
        )

    async def retrieve_card(self, reference: str) -> IssuingCardSecret:
        self.card_calls += 1
        assert reference == self.reference
        number = {
            "pm_stripe_demo_success": "4242424242424242",
            "pm_stripe_demo_decline": "4000000000000002",
        }[reference]
        return IssuingCardSecret(number, "123", 12, 2034)

    async def verify_checkout_session(self, **arguments: object) -> StripeHostedVerification:
        self.verify_calls += 1
        assert arguments["session_id"] == "cs_test_hosted123"
        assert arguments["amount_minor"] == 2500
        assert arguments["currency"] == "EUR"
        return self.verification

    async def expire_checkout_session(self, session_id: str, execution_id: object) -> bool:
        self.expire_calls += 1
        assert session_id == "cs_test_hosted123"
        assert execution_id == self.execution_id
        return self.expiration_confirmed


class FakeHostedBrowser(FakeBrowser):
    def __init__(self, repository: FakeRepository, *, fail_after_submit: bool = False) -> None:
        super().__init__(repository)
        self.fail_after_submit = fail_after_submit

    async def run(self, context: CheckoutContext, **callbacks: object) -> BrowserCheckoutResult:
        self.runs += 1
        assert self.repository.locked
        assert context.checkout_url == (
            "https://checkout.stripe.com/c/pay/cs_test_hosted123#fixture"
        )
        await callbacks["on_session_started"]("session_hosted123")  # type: ignore[operator]
        await callbacks["prepare_submission"]()  # type: ignore[operator]
        secret = await callbacks["load_card"]()  # type: ignore[operator]
        assert "redacted" in repr(secret)
        await callbacks["mark_submitted"]("session_hosted123")  # type: ignore[operator]
        if self.fail_after_submit:
            raise CheckoutError(CheckoutErrorCode.payment_form_not_found)
        outcome = await callbacks["observe_outcome"]()  # type: ignore[operator]
        return BrowserCheckoutResult(None, None, outcome)


def hosted_worker_context(reference: str) -> CheckoutContext:
    base = worker_context()
    hosted_adapter = replace(
        base.adapter,
        allowed_origins=("https://checkout.stripe.com", "https://example.com"),
        payment_origins=("https://checkout.stripe.com",),
        result_origins=("https://example.com",),
        checkout_mode="stripe_hosted_test",
    )
    return replace(
        base,
        adapter_key="stripe-hosted",
        adapter=hosted_adapter,
        checkout_url="https://checkout.stripe.com/",
        checkout_origin="https://checkout.stripe.com",
        provider="prototype-vault",
        provider_card_id=reference,
        billing_details={"phone": "+34910000000"},
    )


class FakeIssuing:
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.authorization_calls = 0
        self.snapshot_calls = 0

    async def snapshot_authorization_ids(self, **_: object) -> frozenset[str]:
        assert self.repository.locked
        self.snapshot_calls += 1
        return frozenset({"iauth_preexisting"})

    async def retrieve_card(self, _: str, __: ExpectedCardMetadata) -> IssuingCardSecret:
        assert self.repository.locked
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def find_authorization(self, **arguments: object) -> AuthorizationResult:
        assert self.repository.locked
        assert arguments["excluded_authorization_ids"] == frozenset({"iauth_preexisting"})
        self.authorization_calls += 1
        if self.authorization_calls == 1:
            return AuthorizationResult(AuthorizationOutcome.unknown)
        return AuthorizationResult(AuthorizationOutcome.approved, "iauth_exact123")


class FakeBroker:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    async def publish(self, event_type: str, payload: dict[str, object]) -> bool:
        self.events.append((event_type, payload))
        return True


class FlakyClaimRepository(FakeRepository):
    def __init__(self, context: CheckoutContext, stop: asyncio.Event) -> None:
        super().__init__(context)
        self.stop = stop
        self.claim_calls = 0

    async def claim_next(self, **_: object) -> ClaimResult | None:
        self.claim_calls += 1
        if self.claim_calls == 1:
            raise RuntimeError("database failure secret=do-not-log")
        self.stop.set()
        return None


async def test_worker_reconciles_delayed_authorization_without_resubmitting(
    caplog,
) -> None:
    context = worker_context()
    repository = FakeRepository(context)
    browser = FakeBrowser(repository)
    issuing = FakeIssuing(repository)
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=issuing,  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
        authorization_timeout_seconds=0.1,
        authorization_poll_seconds=0.001,
    )

    with caplog.at_level(logging.INFO):
        assert await worker.process_once()

    assert browser.runs == 1
    assert issuing.snapshot_calls == 1
    assert issuing.authorization_calls == 2
    assert repository.provider_reference == "iauth_exact123"
    assert broker.events[0][0] == "checkout.succeeded"
    rendered_logs = caplog.text
    assert "4242424242424242" not in rendered_logs
    assert "123" not in rendered_logs


async def test_worker_never_retries_failure_after_card_disclosure_boundary() -> None:
    context = worker_context()
    repository = FakeRepository(context)
    browser = FillFailingBrowser(repository)
    issuing = FakeIssuing(repository)
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=issuing,  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
        authorization_timeout_seconds=0.1,
        authorization_poll_seconds=0.001,
    )

    assert await worker.process_once()

    assert browser.runs == 1
    assert repository.submitted
    assert repository.retry_errors == [CheckoutErrorCode.payment_form_not_found]
    assert broker.events == [
        (
            "checkout.outcome_unknown",
            {
                "execution_id": context.execution_id,
                "cart_item_id": context.cart_item_id,
                "status": "outcome_unknown",
                "error_code": "payment_outcome_unknown",
            },
        )
    ]


async def test_demo_decline_is_verified_and_published_as_failed() -> None:
    context = replace(
        worker_context(),
        provider="prototype-vault",
        provider_card_id="pm_stripe_demo_decline",
        adapter_key="stripe-demo",
    )
    repository = FakeRepository(context)
    browser = DemoDeclineBrowser(repository)
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        demo=FakeDemo(),  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
    )

    assert await worker.process_once()

    assert browser.runs == 1
    assert broker.events == [
        (
            "checkout.failed",
            {
                "execution_id": context.execution_id,
                "cart_item_id": context.cart_item_id,
                "status": "failed",
                "error_code": "payment_declined",
            },
        )
    ]


@pytest.mark.parametrize(
    (
        "verification",
        "reference",
        "expected_status",
        "expected_error",
        "expected_event",
        "expected_expire_calls",
    ),
    [
        (
            StripeHostedVerification(
                AuthorizationOutcome.approved,
                provider_reference="pi_hosted123",
                receipt_url="https://pay.stripe.com/receipts/payment/hosted123",
            ),
            "pm_stripe_demo_success",
            CheckoutExecutionStatus.succeeded,
            None,
            "checkout.succeeded",
            0,
        ),
        (
            StripeHostedVerification(AuthorizationOutcome.declined),
            "pm_stripe_demo_decline",
            CheckoutExecutionStatus.failed,
            CheckoutErrorCode.payment_declined,
            "checkout.failed",
            1,
        ),
        (
            StripeHostedVerification(AuthorizationOutcome.unknown),
            "pm_stripe_demo_decline",
            CheckoutExecutionStatus.outcome_unknown,
            CheckoutErrorCode.payment_outcome_unknown,
            "checkout.outcome_unknown",
            0,
        ),
    ],
)
async def test_hosted_worker_persists_and_publishes_verified_terminal_outcome(
    verification: StripeHostedVerification,
    reference: str,
    expected_status: CheckoutExecutionStatus,
    expected_error: CheckoutErrorCode | None,
    expected_event: str,
    expected_expire_calls: int,
) -> None:
    context = hosted_worker_context(reference)
    repository = HostedRepository(context)
    browser = FakeHostedBrowser(repository)
    demo = FakeHostedDemo(verification, reference)
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        demo=demo,  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
        authorization_timeout_seconds=0,
        authorization_poll_seconds=0.001,
    )

    assert await worker.process_once()

    assert browser.runs == 1
    assert demo.create_calls == 1
    assert demo.card_calls == 1
    assert demo.verify_calls == 1
    assert demo.expire_calls == expected_expire_calls
    assert repository.terminal_status == expected_status
    assert repository.terminal_error == expected_error
    assert repository.merchant_order_reference == "cs_test_hosted123"
    assert repository.retry_errors == []
    if expected_status == CheckoutExecutionStatus.succeeded:
        assert repository.provider_reference == "pi_hosted123"
        assert repository.receipt_url == ("https://pay.stripe.com/receipts/payment/hosted123")
    assert len(broker.events) == 1
    assert broker.events[0][0] == expected_event
    assert broker.events[0][1]["status"] == expected_status.value
    assert broker.events[0][1].get("error_code") == (
        expected_error.value if expected_error is not None else None
    )


async def test_hosted_decline_is_unknown_when_session_expiration_is_not_confirmed() -> None:
    verification = StripeHostedVerification(AuthorizationOutcome.declined)
    context = hosted_worker_context("pm_stripe_demo_decline")
    repository = HostedRepository(context)
    browser = FakeHostedBrowser(repository)
    demo = FakeHostedDemo(
        verification,
        "pm_stripe_demo_decline",
        expiration_confirmed=False,
    )
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        demo=demo,  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
        authorization_timeout_seconds=0,
        authorization_poll_seconds=0.001,
    )

    assert await worker.process_once()

    assert demo.expire_calls == 1
    assert repository.terminal_status == CheckoutExecutionStatus.outcome_unknown
    assert repository.terminal_error == CheckoutErrorCode.payment_outcome_unknown
    assert broker.events == [
        (
            "checkout.outcome_unknown",
            {
                "execution_id": context.execution_id,
                "cart_item_id": context.cart_item_id,
                "status": "outcome_unknown",
                "error_code": "payment_outcome_unknown",
            },
        )
    ]


async def test_hosted_worker_reconciles_post_submit_browser_failure_without_retrying() -> None:
    verification = StripeHostedVerification(AuthorizationOutcome.unknown)
    context = hosted_worker_context("pm_stripe_demo_decline")
    repository = HostedRepository(context)
    browser = FakeHostedBrowser(repository, fail_after_submit=True)
    demo = FakeHostedDemo(verification, "pm_stripe_demo_decline")
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        demo=demo,  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
        authorization_timeout_seconds=0,
        authorization_poll_seconds=0.001,
    )

    assert await worker.process_once()

    assert repository.submitted
    assert browser.runs == 1
    assert demo.verify_calls == 1
    assert repository.retry_errors == []
    assert repository.terminal_status == CheckoutExecutionStatus.outcome_unknown
    assert repository.terminal_error == CheckoutErrorCode.payment_outcome_unknown
    assert broker.events == [
        (
            "checkout.outcome_unknown",
            {
                "execution_id": context.execution_id,
                "cart_item_id": context.cart_item_id,
                "status": "outcome_unknown",
                "error_code": "payment_outcome_unknown",
            },
        )
    ]


async def test_worker_loop_survives_transient_claim_failure_without_logging_error_data(
    caplog,
) -> None:
    stop = asyncio.Event()
    context = worker_context()
    repository = FlakyClaimRepository(context, stop)
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=FakeBrowser(repository),  # type: ignore[arg-type]
        issuing=FakeIssuing(repository),  # type: ignore[arg-type]
        broker=None,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.001,
    )

    with caplog.at_level(logging.ERROR):
        await worker.run_forever(stop)

    assert repository.claim_calls == 2
    assert "checkout worker iteration failed" in caplog.text
    assert "do-not-log" not in caplog.text


async def test_invalid_zero_decimal_amount_fails_before_browser_session_or_submission() -> None:
    context = replace(worker_context(), amount=Decimal("25.50"), currency="JPY")
    repository = FakeRepository(context)
    browser = FakeBrowser(repository)
    issuing = FakeIssuing(repository)
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=issuing,  # type: ignore[arg-type]
        broker=None,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
    )

    assert await worker.process_once()

    assert browser.runs == 0
    assert issuing.snapshot_calls == 0
    assert not repository.submitted
    assert repository.retry_errors == [CheckoutErrorCode.currency_precision_invalid]
