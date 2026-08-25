import json
from collections.abc import Mapping
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from ag_platform_api.services.checkout.browserbase import (
    CARD_FIELD_PREFLIGHT_CLEANUP_SCRIPT,
    CARD_FIELD_PREFLIGHT_SCRIPT,
    DETERMINISTIC_INPUT_SCRIPT,
    BrowserbaseCheckout,
    LocatedElement,
    ResolvedCardField,
)
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.form_mapping import (
    PaymentFormSelectorMap,
    StagehandCheckoutFormMapper,
)
from ag_platform_api.services.checkout.types import (
    BrowserbaseSession,
    CheckoutAdapter,
    CheckoutContext,
    ExpectedCardMetadata,
    IssuingCardSecret,
)


class FakeElement:
    def __init__(
        self,
        *,
        text: str = "",
        value: str = "",
        attributes: Mapping[str, str] | None = None,
    ) -> None:
        self.text = text
        self.value = value
        self.attributes = dict(attributes or {})
        self.evaluate_calls: list[tuple[str, object | None]] = []
        self.fill_calls: list[str] = []
        self.clicked = False
        self.probe_markers: set[str] = set()

    async def count(self) -> int:
        return 1

    def nth(self, _: int) -> "FakeElement":
        return self

    async def is_visible(self) -> bool:
        return True

    async def inner_text(self) -> str:
        return self.text

    async def input_value(self) -> str:
        return self.value

    async def fill(self, value: str) -> None:
        self.fill_calls.append(value)

    async def select_option(
        self,
        value: str | None = None,
        *,
        label: str | None = None,
    ) -> object:
        self.value = value or label or ""
        return None

    async def click(self) -> None:
        self.clicked = True

    async def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)

    async def element_handle(self) -> "FakeElement":
        return self

    async def evaluate(self, expression: str, arg: object | None = None) -> object:
        if expression == CARD_FIELD_PREFLIGHT_SCRIPT:
            assert isinstance(arg, dict) and isinstance(arg.get("marker"), str)
            marker = arg["marker"]
            if marker in self.probe_markers:
                return False
            self.probe_markers.add(marker)
            return True
        if expression == CARD_FIELD_PREFLIGHT_CLEANUP_SCRIPT:
            assert isinstance(arg, str)
            self.probe_markers.discard(arg)
            return None
        self.evaluate_calls.append((expression, arg))
        return None


class EmptyLocator:
    async def count(self) -> int:
        return 0

    def nth(self, _: int) -> "EmptyLocator":
        return self


class FakeFrame:
    def __init__(self, url: str, elements: Mapping[str, FakeElement]) -> None:
        self.url = url
        self.elements = dict(elements)

    def locator(self, selector: str) -> FakeElement | EmptyLocator:
        return self.elements.get(selector, EmptyLocator())


class FakePage(FakeFrame):
    def __init__(self, main: FakeFrame, frames: list[FakeFrame]) -> None:
        super().__init__(main.url, main.elements)
        self.frames = frames

    async def goto(self, url: str, **_: Any) -> object:
        self.url = url
        return None

    async def route(self, _: str, __: object) -> None:
        return None

    async def wait_for_timeout(self, _: float) -> None:
        return None


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.closed = False

    async def new_page(self, _: tuple[str, ...]) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeGateway:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.released: str | None = None

    async def create_session(self, _: tuple[str, ...], **options: object) -> BrowserbaseSession:
        assert options == {"record_session": False, "log_session": False}
        return BrowserbaseSession("session_12345", "wss://browser.example.test/session")

    async def connect(self, _: BrowserbaseSession) -> FakeBrowser:
        return self.browser

    async def release_session(self, session_id: str) -> bool:
        self.released = session_id
        return True


class ObservingMapper:
    def __init__(self, events: list[str], mapping: PaymentFormSelectorMap) -> None:
        self.events = events
        self.mapping = mapping

    async def map_payment_form(
        self,
        *,
        browserbase_session_id: str,
        billing_fields: tuple[str, ...],
    ) -> PaymentFormSelectorMap:
        assert browserbase_session_id == "session_12345"
        assert billing_fields == ()
        self.events.append("observe-form")
        return self.mapping


def mapping_context() -> CheckoutContext:
    owner_id = uuid4()
    return CheckoutContext(
        execution_id=uuid4(),
        cart_item_id=uuid4(),
        owner_id=owner_id,
        agent_id=uuid4(),
        payment_method_id=uuid4(),
        adapter_key="mapped-direct",
        adapter=CheckoutAdapter(
            allowed_origins=("https://merchant.example.test",),
            payment_origins=("https://payments.example.test",),
            product_title_selector="#product-title",
            quantity_selector="#quantity",
            total_selector="#total",
            success_selector="#success",
            payment_form_strategy="browserbase_ai",
        ),
        checkout_url="https://merchant.example.test/checkout/one",
        checkout_origin="https://merchant.example.test",
        approved_title="Mapped product",
        approved_quantity=1,
        amount=Decimal("25.00"),
        currency="EUR",
        provider="local_direct_card",
        provider_card_id="ldc_card123",
        card_metadata=ExpectedCardMetadata(owner_id, "4242", "visa", 12, 2030),
        billing_details={},
    )


async def test_observed_form_is_saved_before_secret_loading_without_secret_values() -> None:
    events: list[str] = []
    mapping = PaymentFormSelectorMap(
        card_number_selector="#mapped-number",
        cvc_selector="#mapped-cvc",
        expiry_selector="#mapped-expiry",
        submit_selector="#mapped-submit",
    )
    submit = FakeElement(attributes={"type": "submit", "aria-label": "Pay now"})
    main = FakeFrame(
        "https://merchant.example.test/checkout/one",
        {
            "#product-title": FakeElement(text="Mapped product"),
            "#quantity": FakeElement(value="1"),
            "#total": FakeElement(text="Total EUR 25.00"),
            "#mapped-submit": submit,
            "#success": FakeElement(),
        },
    )
    number = FakeElement(attributes={"autocomplete": "cc-number", "type": "text"})
    cvc = FakeElement(attributes={"autocomplete": "cc-csc", "type": "text"})
    expiry = FakeElement(attributes={"autocomplete": "cc-exp", "type": "text"})
    payment = FakeFrame(
        "https://payments.example.test/card",
        {
            "#mapped-number": number,
            "#mapped-cvc": cvc,
            "#mapped-expiry": expiry,
        },
    )
    page = FakePage(main, [main, payment])
    browser = FakeBrowser(page)
    gateway = FakeGateway(browser)
    saved: list[dict[str, object]] = []
    card_number = "4242424242424242"
    security_code = "987"

    async def save_form(snapshot: Mapping[str, object]) -> None:
        events.append("save-form")
        saved.append(dict(snapshot))

    async def load_card() -> IssuingCardSecret:
        events.append("load-secret")
        return IssuingCardSecret(card_number, security_code, 12, 2030)

    async def session_started(_: str) -> None:
        events.append("session-started")

    async def prepare_submission() -> None:
        events.append("prepare-submission")

    async def mark_submitted(_: str) -> None:
        events.append("submitted")

    checkout = BrowserbaseCheckout(
        gateway,  # type: ignore[arg-type]
        result_timeout_seconds=0.01,
        form_mapper=ObservingMapper(events, mapping),
    )
    await checkout.run(
        mapping_context(),
        load_card=load_card,
        on_session_started=session_started,
        prepare_submission=prepare_submission,
        mark_submitted=mark_submitted,
        on_form_mapped=save_form,
    )

    assert events.index("observe-form") < events.index("save-form")
    assert events.index("save-form") < events.index("load-secret")
    assert saved == [mapping.to_snapshot()]
    serialized = json.dumps(saved)
    assert card_number not in serialized
    assert security_code not in serialized
    assert number.evaluate_calls == [(DETERMINISTIC_INPUT_SCRIPT, card_number)]
    assert cvc.evaluate_calls == [(DETERMINISTIC_INPUT_SCRIPT, security_code)]
    assert expiry.evaluate_calls == [(DETERMINISTIC_INPUT_SCRIPT, "12/30")]
    assert not number.fill_calls and not cvc.fill_calls and not expiry.fill_calls
    assert submit.clicked
    assert browser.closed
    assert gateway.released == "session_12345"


async def test_invalid_dom_alias_is_not_saved_and_never_loads_credentials() -> None:
    events: list[str] = []
    mapping = PaymentFormSelectorMap(
        card_number_selector="#mapped-number",
        cvc_selector="#mapped-cvc",
        expiry_selector="#mapped-expiry",
        submit_selector="#mapped-submit",
    )
    main = FakeFrame(
        "https://merchant.example.test/checkout/one",
        {
            "#product-title": FakeElement(text="Mapped product"),
            "#quantity": FakeElement(value="1"),
            "#total": FakeElement(text="Total EUR 25.00"),
            "#mapped-submit": FakeElement(attributes={"type": "submit"}),
        },
    )
    aliased = FakeElement(attributes={"name": "card-number", "aria-label": "CVC", "type": "text"})
    payment = FakeFrame(
        "https://payments.example.test/card",
        {
            "#mapped-number": aliased,
            "#mapped-cvc": aliased,
            "#mapped-expiry": FakeElement(attributes={"autocomplete": "cc-exp", "type": "text"}),
        },
    )
    gateway = FakeGateway(FakeBrowser(FakePage(main, [main, payment])))

    async def save_form(_: Mapping[str, object]) -> None:
        events.append("save-form")

    async def load_card() -> IssuingCardSecret:
        events.append("load-secret")
        return IssuingCardSecret("4242424242424242", "987", 12, 2030)

    async def session_started(_: str) -> None:
        events.append("session-started")

    async def prepare_submission() -> None:
        events.append("prepare-submission")

    async def mark_submitted(_: str) -> None:
        events.append("submitted")

    checkout = BrowserbaseCheckout(
        gateway,  # type: ignore[arg-type]
        form_mapper=ObservingMapper(events, mapping),
    )
    with pytest.raises(CheckoutError) as caught:
        await checkout.run(
            mapping_context(),
            load_card=load_card,
            on_session_started=session_started,
            prepare_submission=prepare_submission,
            mark_submitted=mark_submitted,
            on_form_mapped=save_form,
        )

    assert caught.value.code == CheckoutErrorCode.form_analysis_failed
    assert "save-form" not in events
    assert "load-secret" not in events
    assert "submitted" not in events


async def test_resolved_payment_handles_use_deterministic_native_setter_evaluation() -> None:
    frame = FakeFrame("https://payments.example.test/card", {})
    fields = []
    handles: dict[str, FakeElement] = {}
    for kind in ("number", "cvc", "expiry_month", "expiry_year"):
        handle = FakeElement()
        handles[kind] = handle
        fields.append(ResolvedCardField(handle=handle, frame=frame, kind=kind))

    checkout = BrowserbaseCheckout(object())  # type: ignore[arg-type]
    await checkout._fill_resolved_card(
        tuple(fields),
        IssuingCardSecret("4242424242424242", "123", 12, 2030),
        ("https://payments.example.test",),
    )

    assert {kind: handle.evaluate_calls for kind, handle in handles.items()} == {
        "number": [(DETERMINISTIC_INPUT_SCRIPT, "4242424242424242")],
        "cvc": [(DETERMINISTIC_INPUT_SCRIPT, "123")],
        "expiry_month": [(DETERMINISTIC_INPUT_SCRIPT, "12")],
        "expiry_year": [(DETERMINISTIC_INPUT_SCRIPT, "2030")],
    }
    assert all(not handle.fill_calls for handle in handles.values())


async def test_ai_mapped_billing_controls_are_injected_with_javascript() -> None:
    name = FakeElement()
    country = FakeElement()
    frame = FakeFrame(
        "https://payments.example.test/card",
        {"#name": name, "#country": country},
    )
    page = FakePage(frame, [frame])
    adapter = CheckoutAdapter(
        allowed_origins=("https://merchant.example.test",),
        payment_origins=("https://payments.example.test",),
        product_title_selector="#title",
        quantity_selector="#quantity",
        total_selector="#total",
        success_selector="#success",
        payment_form_strategy="resolved",
        card_number_selector="#number",
        cvc_selector="#cvc",
        expiry_selector="#expiry",
        submit_selector="#submit",
        name_selector="#name",
        billing_country_selector="#country",
    )

    await BrowserbaseCheckout(object())._fill_billing(  # type: ignore[arg-type]
        page,
        adapter,
        {
            "full_name": "Alex Example",
            "address": {"country": "ES"},
        },
        ("https://payments.example.test",),
        javascript=True,
    )

    assert name.evaluate_calls == [(DETERMINISTIC_INPUT_SCRIPT, "Alex Example")]
    assert country.evaluate_calls == [(DETERMINISTIC_INPUT_SCRIPT, "ES")]
    assert not name.fill_calls and not country.fill_calls


def test_payment_form_mapping_rejects_duplicate_controls() -> None:
    mapping = PaymentFormSelectorMap(
        card_number_selector="#same-control",
        cvc_selector="#same-control",
        expiry_selector="#expiry",
        submit_selector="#submit",
    )

    with pytest.raises(CheckoutError) as caught:
        mapping.validate()

    assert caught.value.code == CheckoutErrorCode.form_analysis_failed


async def test_card_field_preflight_rejects_aliases_and_non_editable_nodes() -> None:
    frame = FakeFrame("https://payments.example.test/card", {})
    same = FakeElement()
    aliased = (
        ResolvedCardField(handle=same, frame=frame, kind="number"),
        ResolvedCardField(handle=same, frame=frame, kind="cvc"),
    )
    with pytest.raises(CheckoutError) as duplicate:
        await BrowserbaseCheckout._preflight_card_fields(aliased, retryable=True)
    assert duplicate.value.code == CheckoutErrorCode.form_analysis_failed
    assert duplicate.value.retryable

    distinct_cvc = FakeElement()
    with pytest.raises(CheckoutError) as submit_alias:
        await BrowserbaseCheckout._preflight_card_fields(
            (
                ResolvedCardField(handle=same, frame=frame, kind="number"),
                ResolvedCardField(handle=distinct_cvc, frame=frame, kind="cvc"),
            ),
            submit_handle=same,
            retryable=True,
        )
    assert submit_alias.value.code == CheckoutErrorCode.form_analysis_failed

    class NonEditableElement(FakeElement):
        async def evaluate(self, expression: str, arg: object | None = None) -> object:
            if expression == CARD_FIELD_PREFLIGHT_SCRIPT:
                return False
            return await super().evaluate(expression, arg)

    invalid = (
        ResolvedCardField(
            handle=NonEditableElement(),
            frame=frame,
            kind="number",
        ),
    )
    with pytest.raises(CheckoutError) as non_editable:
        await BrowserbaseCheckout._preflight_card_fields(invalid, retryable=True)
    assert non_editable.value.code == CheckoutErrorCode.form_analysis_failed


async def test_company_name_does_not_satisfy_pan_semantics() -> None:
    element = FakeElement(attributes={"name": "company-name", "type": "text"})
    located = LocatedElement(
        locator=element,
        frame=FakeFrame("https://payments.example.test/card", {}),
    )

    with pytest.raises(CheckoutError) as caught:
        await BrowserbaseCheckout(object())._validate_payment_field_semantics(  # type: ignore[arg-type]
            located,
            "card_number",
        )

    assert caught.value.code == CheckoutErrorCode.form_analysis_failed


async def test_stagehand_mapper_calls_observe_only_on_existing_session(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    selectors = iter(("#number", "#cvc", "#expiry", "#submit"))

    class FakeSessions:
        async def start(self, **kwargs: object) -> object:
            calls.append(("start", kwargs))
            return SimpleNamespace(id="session_existing")

        async def observe(self, **kwargs: object) -> object:
            calls.append(("observe", kwargs))
            return SimpleNamespace(
                data=SimpleNamespace(result=[SimpleNamespace(selector=next(selectors))])
            )

    class FakeAsyncStagehand:
        def __init__(self, **kwargs: object) -> None:
            calls.append(("client", kwargs))
            self.sessions = FakeSessions()

        async def __aenter__(self) -> "FakeAsyncStagehand":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("stagehand.AsyncStagehand", FakeAsyncStagehand)
    mapper = StagehandCheckoutFormMapper(
        browserbase_api_key="browserbase-key",
        model_name="google/gemini-2.5-flash",
        timeout_seconds=30,
    )

    mapping = await mapper.map_payment_form(
        browserbase_session_id="session_existing",
        billing_fields=(),
    )

    assert mapping == PaymentFormSelectorMap(
        card_number_selector="#number",
        cvc_selector="#cvc",
        expiry_selector="#expiry",
        submit_selector="#submit",
    )
    assert [name for name, _ in calls] == [
        "client",
        "start",
        "observe",
        "observe",
        "observe",
        "observe",
    ]
    start = calls[1][1]
    assert isinstance(start, dict)
    assert start["browserbase_session_id"] == "session_existing"
    assert start["self_heal"] is False
