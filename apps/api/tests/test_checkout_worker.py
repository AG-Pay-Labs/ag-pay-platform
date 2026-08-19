import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from ag_platform_api.core.config import LOCAL_DIRECT_CARD_PROVIDER
from ag_platform_api.models import CheckoutExecutionStatus
from ag_platform_api.services.checkout.cvc_broker import CvcUnavailableError
from ag_platform_api.services.checkout.direct_card import DirectCardPan
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.repository import (
    ClaimResult,
    TerminalNotification,
)
from ag_platform_api.services.checkout.stripe_link import (
    LinkSpendBinding,
    LinkSpendRequest,
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
        self.retryable_errors: list[bool] = []

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
        self.retryable_errors.append(error.retryable)
        if not self.submitted:
            if error.retryable:
                return None
            return TerminalNotification(
                self.context.execution_id,
                self.context.cart_item_id,
                CheckoutExecutionStatus.failed,
                error.code.value,
            )
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
        assert merchant_order_reference in {None, "cs_test_hosted123"}
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


class FakeHostedCards:
    def __init__(self, reference: str) -> None:
        self.reference = reference
        self.card_calls = 0

    async def retrieve_card(self, reference: str) -> IssuingCardSecret:
        self.card_calls += 1
        assert reference == self.reference
        number = {
            "pm_stripe_demo_success": "4242424242424242",
            "pm_stripe_demo_decline": "4000000000000002",
        }[reference]
        return IssuingCardSecret(number, "123", 12, 2034)


class FakeHostedBrowser(FakeBrowser):
    def __init__(
        self,
        repository: FakeRepository,
        *,
        fail_after_submit: bool = False,
        verified: bool = True,
    ) -> None:
        super().__init__(repository)
        self.fail_after_submit = fail_after_submit
        self.verified = verified

    async def run(self, context: CheckoutContext, **callbacks: object) -> BrowserCheckoutResult:
        self.runs += 1
        assert self.repository.locked
        assert context.checkout_url == "https://checkout.stripe.com/c/pay/cs_test_hosted123#fixture"
        assert "observe_outcome" not in callbacks
        await callbacks["on_session_started"]("session_hosted123")  # type: ignore[operator]
        await callbacks["prepare_submission"]()  # type: ignore[operator]
        secret = await callbacks["load_card"]()  # type: ignore[operator]
        assert "redacted" in repr(secret)
        await callbacks["mark_submitted"]("session_hosted123")  # type: ignore[operator]
        if self.fail_after_submit:
            raise CheckoutError(CheckoutErrorCode.payment_form_not_found)
        if not self.verified:
            return BrowserCheckoutResult(None, None, AuthorizationOutcome.unknown)
        return BrowserCheckoutResult(
            "cs_test_hosted123",
            "https://letyouragentspay.com/playground/success?session_id=cs_test_hosted123",
        )


def hosted_worker_context(reference: str) -> CheckoutContext:
    base = worker_context()
    hosted_adapter = replace(
        base.adapter,
        allowed_origins=("https://checkout.stripe.com", "https://letyouragentspay.com"),
        payment_origins=("https://checkout.stripe.com",),
        result_origins=("https://letyouragentspay.com",),
        checkout_mode="stripe_hosted_test",
    )
    return replace(
        base,
        adapter_key="stripe-hosted",
        adapter=hosted_adapter,
        checkout_url="https://checkout.stripe.com/c/pay/cs_test_hosted123#fixture",
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


async def test_link_unknown_outcome_is_never_recorded_as_success_and_preserves_ordering() -> None:
    context = replace(
        worker_context(),
        provider="stripe_link",
        provider_card_id="csmrpd_wallet123",
        billing_details={
            "type": "personal",
            "full_name": "Alex Example",
            "address": {
                "line1": "1 Test Street",
                "city": "Madrid",
                "region": "Madrid",
                "postal_code": "28001",
                "country": "ES",
            },
        },
    )
    events: list[str] = []

    class LinkRepository(FakeRepository):
        def __init__(self, checkout_context: CheckoutContext) -> None:
            super().__init__(checkout_context)
            self.provider_request_id: str | None = None
            self.terminal_status: CheckoutExecutionStatus | None = None
            self.terminal_error: CheckoutErrorCode | None = None

        async def record_provider_request(self, execution_id, request_id: str) -> str:
            assert execution_id == self.context.execution_id
            assert self.locked
            assert not self.submitted
            events.append("request_persisted")
            self.provider_request_id = request_id
            return request_id

        async def finish_terminal(
            self,
            execution_id,
            *,
            status: CheckoutExecutionStatus,
            error_code: CheckoutErrorCode,
            merchant_order_reference: str | None = None,
        ) -> TerminalNotification:
            assert execution_id == self.context.execution_id
            assert self.locked
            assert self.submitted
            assert merchant_order_reference == "order_link123"
            events.append("terminal")
            self.terminal_status = status
            self.terminal_error = error_code
            return TerminalNotification(
                self.context.execution_id,
                self.context.cart_item_id,
                status,
                error_code.value,
            )

        async def succeed(self, *args: object, **kwargs: object) -> TerminalNotification:
            raise AssertionError("an unknown Link outcome must never create a purchase")

    repository = LinkRepository(context)
    link_request = LinkSpendRequest(
        "lsrq_request123",
        "pending_approval",
        LinkSpendBinding(
            execution_id=context.execution_id,
            payment_method_id=context.provider_card_id,
            merchant_url=context.checkout_origin,
            amount_minor=context.amount_minor,
            currency=context.currency,
            merchant_name="merchant.example.test",
            context="A" * 100,
            item_name=context.approved_title,
            item_quantity=context.approved_quantity,
            unit_amount_minor=1250,
        ),
    )

    class FakeLink:
        async def ensure_spend_request(self, **arguments: object) -> LinkSpendRequest:
            assert arguments["existing_request_id"] is None
            events.append("request_created")
            return link_request

        async def wait_for_approval(self, **arguments: object) -> None:
            assert arguments["request"] == link_request
            assert link_request.request_id == repository.provider_request_id
            events.append("approval_waited")

        async def retrieve_card(self, **arguments: object) -> IssuingCardSecret:
            assert arguments["request"] == link_request
            assert link_request.request_id == repository.provider_request_id
            events.append("credential_retrieved")
            return IssuingCardSecret("4000009990001984", "123", 12, 2030)

        async def report_outcome(self, **arguments: object) -> None:
            assert arguments["outcome"] == "abandoned"
            assert arguments["tag"] == "timeout"
            events.append("outcome_reported")

    class LinkBrowser(FakeBrowser):
        async def run(
            self, checkout_context: CheckoutContext, **callbacks: object
        ) -> BrowserCheckoutResult:
            self.runs += 1
            assert checkout_context == context
            events.append("browser_started")
            await callbacks["on_session_started"]("session_link123")  # type: ignore[operator]
            await callbacks["prepare_submission"]()  # type: ignore[operator]
            await callbacks["load_card"]()  # type: ignore[operator]
            events.append("card_loaded")
            await callbacks["mark_submitted"]("session_link123")  # type: ignore[operator]
            events.append("submitted")
            return BrowserCheckoutResult(
                "order_link123",
                None,
                AuthorizationOutcome.unknown,
            )

    browser = LinkBrowser(repository)
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        link=FakeLink(),  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
    )

    assert await worker.process_once()

    assert events == [
        "request_created",
        "request_persisted",
        "approval_waited",
        "browser_started",
        "credential_retrieved",
        "card_loaded",
        "submitted",
        "outcome_reported",
        "terminal",
    ]
    assert repository.retry_errors == []
    assert repository.terminal_status == CheckoutExecutionStatus.outcome_unknown
    assert repository.terminal_error == CheckoutErrorCode.payment_outcome_unknown
    assert repository.provider_reference is None
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


async def test_hosted_worker_uses_fixed_url_and_keyless_test_card_fixture() -> None:
    context = hosted_worker_context("pm_stripe_demo_success")
    repository = HostedRepository(context)
    browser = FakeHostedBrowser(repository)
    cards = FakeHostedCards("pm_stripe_demo_success")
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        demo=None,
        demo_cards=cards,  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
    )

    assert await worker.process_once()

    assert browser.runs == 1
    assert cards.card_calls == 1
    assert repository.terminal_status == CheckoutExecutionStatus.succeeded
    assert repository.terminal_error is None
    assert repository.merchant_order_reference == "cs_test_hosted123"
    assert repository.retry_errors == []
    assert repository.provider_reference == "cs_test_hosted123"
    assert repository.receipt_url == (
        "https://letyouragentspay.com/playground/success?session_id=cs_test_hosted123"
    )
    assert broker.events[0][0] == "checkout.succeeded"


async def test_hosted_worker_never_accepts_an_unverified_browser_result() -> None:
    context = hosted_worker_context("pm_stripe_demo_success")
    repository = HostedRepository(context)
    browser = FakeHostedBrowser(repository, verified=False)
    cards = FakeHostedCards("pm_stripe_demo_success")
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        demo=None,
        demo_cards=cards,  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
    )

    assert await worker.process_once()

    assert repository.terminal_status == CheckoutExecutionStatus.outcome_unknown
    assert repository.terminal_error == CheckoutErrorCode.payment_outcome_unknown
    assert repository.retry_errors == []
    assert broker.events[0][0] == "checkout.outcome_unknown"


async def test_hosted_worker_marks_post_submit_browser_failure_unknown_without_retrying() -> None:
    context = hosted_worker_context("pm_stripe_demo_decline")
    repository = HostedRepository(context)
    browser = FakeHostedBrowser(repository, fail_after_submit=True)
    cards = FakeHostedCards("pm_stripe_demo_decline")
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        demo=None,
        demo_cards=cards,  # type: ignore[arg-type]
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
    )

    assert await worker.process_once()

    assert repository.submitted
    assert browser.runs == 1
    assert repository.retry_errors == []
    assert repository.terminal_status == CheckoutExecutionStatus.outcome_unknown
    assert repository.terminal_error == CheckoutErrorCode.payment_outcome_unknown
    assert broker.events[0][0] == "checkout.outcome_unknown"


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


def local_direct_context() -> CheckoutContext:
    context = worker_context()
    return replace(
        context,
        adapter=replace(
            context.adapter,
            payment_form_strategy="browserbase_ai",
            card_number_selector=None,
            cvc_selector=None,
            submit_selector=None,
            expiry_selector=None,
        ),
        provider=LOCAL_DIRECT_CARD_PROVIDER,
        provider_card_id="ldc_card123",
    )


class LocalDirectRepository(FakeRepository):
    def __init__(self, context: CheckoutContext) -> None:
        super().__init__(context)
        self.saved_form: dict[str, object] | None = None
        self.terminal_status: CheckoutExecutionStatus | None = None
        self.terminal_error: CheckoutErrorCode | None = None

    async def record_resolved_form_config(
        self,
        execution_id: object,
        config: dict[str, object],
    ) -> None:
        assert execution_id == self.context.execution_id
        assert self.locked
        assert not self.submitted
        self.saved_form = dict(config)

    async def succeed(
        self,
        execution_id: object,
        *,
        provider_reference: str,
        merchant_order_reference: str | None,
        receipt_url: str | None,
    ) -> TerminalNotification:
        assert execution_id == self.context.execution_id
        assert self.locked
        assert self.submitted
        assert provider_reference == merchant_order_reference == "order_local123"
        assert receipt_url == "https://merchant.example.test/receipt/local"
        self.provider_reference = provider_reference
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
        execution_id: object,
        *,
        status: CheckoutExecutionStatus,
        error_code: CheckoutErrorCode,
        merchant_order_reference: str | None = None,
    ) -> TerminalNotification:
        assert execution_id == self.context.execution_id
        assert self.locked
        assert self.submitted
        assert merchant_order_reference is None
        self.terminal_status = status
        self.terminal_error = error_code
        return TerminalNotification(
            self.context.execution_id,
            self.context.cart_item_id,
            status,
            error_code.value,
        )


class FakeDirectCards:
    def __init__(self, context: CheckoutContext) -> None:
        self.context = context
        self.calls = 0

    async def retrieve_pan(self, **arguments: object) -> DirectCardPan:
        self.calls += 1
        assert arguments == {
            "owner_id": self.context.owner_id,
            "payment_method_id": self.context.payment_method_id,
            "provider_card_id": self.context.provider_card_id,
            "expected": self.context.card_metadata,
        }
        return DirectCardPan("4242424242424242", 12, 2030)


class OneShotCvcStore:
    def __init__(self, context: CheckoutContext) -> None:
        self.context = context
        self.calls = 0
        self.consumed = False

    async def take(self, **arguments: object) -> str:
        self.calls += 1
        assert arguments == {
            "execution_id": self.context.execution_id,
            "owner_id": self.context.owner_id,
            "payment_method_id": self.context.payment_method_id,
        }
        if self.consumed:
            raise CvcUnavailableError()
        self.consumed = True
        return "987"


class LocalDirectBrowser(FakeBrowser):
    def __init__(
        self,
        repository: FakeRepository,
        *,
        fail_after_load_before_submit: bool = False,
        fail_after_submit: bool = False,
    ) -> None:
        super().__init__(repository)
        self.fail_after_load_before_submit = fail_after_load_before_submit
        self.fail_after_submit = fail_after_submit
        self.loaded_secrets: list[tuple[str, str]] = []

    async def run(
        self,
        context: CheckoutContext,
        **callbacks: object,
    ) -> BrowserCheckoutResult:
        self.runs += 1
        assert self.repository.locked
        await callbacks["on_session_started"]("session_local123")  # type: ignore[operator]
        await callbacks["on_form_mapped"](  # type: ignore[operator]
            {
                "card_number_selector": "#number",
                "cvc_selector": "#security-code",
                "submit_selector": "#pay",
            }
        )
        await callbacks["prepare_submission"]()  # type: ignore[operator]
        secret = await callbacks["load_card"]()  # type: ignore[operator]
        self.loaded_secrets.append((secret.number, secret.cvc))
        if self.fail_after_load_before_submit:
            raise CheckoutError(CheckoutErrorCode.form_analysis_failed, retryable=True)
        await callbacks["mark_submitted"]("session_local123")  # type: ignore[operator]
        if self.fail_after_submit:
            raise CheckoutError(CheckoutErrorCode.payment_form_not_found)
        return BrowserCheckoutResult(
            "order_local123",
            "https://merchant.example.test/receipt/local",
        )


async def test_local_direct_worker_consumes_cvc_once_and_records_success() -> None:
    context = local_direct_context()
    repository = LocalDirectRepository(context)
    browser = LocalDirectBrowser(repository)
    direct_cards = FakeDirectCards(context)
    cvcs = OneShotCvcStore(context)
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        direct_cards=direct_cards,  # type: ignore[arg-type]
        direct_card_cvcs=cvcs,
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
    )

    assert await worker.process_once()

    assert direct_cards.calls == 1
    assert cvcs.calls == 1
    assert cvcs.consumed
    assert browser.loaded_secrets == [("4242424242424242", "987")]
    assert repository.saved_form == {
        "card_number_selector": "#number",
        "cvc_selector": "#security-code",
        "submit_selector": "#pay",
    }
    assert repository.terminal_status == CheckoutExecutionStatus.succeeded
    assert repository.provider_reference == "order_local123"
    assert repository.retry_errors == []
    assert broker.events[0][0] == "checkout.succeeded"


async def test_local_direct_post_submit_browser_error_is_unknown_without_retry() -> None:
    context = local_direct_context()
    repository = LocalDirectRepository(context)
    browser = LocalDirectBrowser(repository, fail_after_submit=True)
    direct_cards = FakeDirectCards(context)
    cvcs = OneShotCvcStore(context)
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        direct_cards=direct_cards,  # type: ignore[arg-type]
        direct_card_cvcs=cvcs,
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
    )

    assert await worker.process_once()

    assert repository.submitted
    assert browser.runs == 1
    assert cvcs.calls == 1
    assert cvcs.consumed
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


async def test_local_direct_never_retries_after_one_shot_cvc_is_consumed() -> None:
    context = local_direct_context()
    repository = LocalDirectRepository(context)
    browser = LocalDirectBrowser(repository, fail_after_load_before_submit=True)
    direct_cards = FakeDirectCards(context)
    cvcs = OneShotCvcStore(context)
    broker = FakeBroker()
    worker = CheckoutWorker(
        repository=repository,  # type: ignore[arg-type]
        browser=browser,  # type: ignore[arg-type]
        issuing=None,
        direct_cards=direct_cards,  # type: ignore[arg-type]
        direct_card_cvcs=cvcs,
        broker=broker,
        lease_seconds=120,
        max_attempts=3,
        poll_seconds=0.01,
    )

    assert await worker.process_once()

    assert browser.runs == 1
    assert not repository.submitted
    assert direct_cards.calls == 1
    assert cvcs.calls == 1 and cvcs.consumed
    assert repository.retry_errors == [CheckoutErrorCode.card_security_code_unavailable]
    assert repository.retryable_errors == [False]
    assert broker.events == [
        (
            "checkout.failed",
            {
                "execution_id": context.execution_id,
                "cart_item_id": context.cart_item_id,
                "status": "failed",
                "error_code": "card_security_code_unavailable",
            },
        )
    ]
