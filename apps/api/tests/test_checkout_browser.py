from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from ag_platform_api.services.checkout.browserbase import (
    BrowserbaseCheckout,
    _PlaywrightBrowser,
)
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
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
        name: str,
        events: list[str],
        *,
        text: str = "",
        value: str | None = None,
        visible: bool = True,
        href: str | None = None,
        clicked: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self.events = events
        self.text = text
        self.value = value
        self.visible = visible
        self.href = href
        self.clicked = clicked

    async def count(self) -> int:
        return 1

    def nth(self, index: int) -> "FakeElement":
        assert index == 0
        return self

    async def is_visible(self) -> bool:
        return self.visible

    async def inner_text(self) -> str:
        self.events.append(f"text:{self.name}")
        return self.text

    async def input_value(self) -> str:
        if self.value is None:
            raise RuntimeError("not a form control")
        return self.value

    async def fill(self, _: str) -> None:
        self.events.append(f"fill:{self.name}")

    async def click(self) -> None:
        self.events.append(f"click:{self.name}")
        if self.clicked:
            self.clicked()

    async def get_attribute(self, name: str) -> str | None:
        return self.href if name == "href" else None

    async def element_handle(self) -> "FakeElement":
        return self


class FakeCollection:
    def __init__(self, elements: list[FakeElement]) -> None:
        self.elements = elements

    async def count(self) -> int:
        return len(self.elements)

    def nth(self, index: int) -> FakeElement:
        return self.elements[index]


class ChangingTextElement(FakeElement):
    def __init__(self, name: str, events: list[str], texts: list[str]) -> None:
        super().__init__(name, events)
        self.texts = texts

    async def inner_text(self) -> str:
        self.events.append(f"text:{self.name}")
        return self.texts.pop(0)


class ChangingValueElement(FakeElement):
    def __init__(self, name: str, events: list[str], values: list[str]) -> None:
        super().__init__(name, events)
        self.values = values

    async def input_value(self) -> str:
        return self.values.pop(0)


class FakeFrame:
    def __init__(self, url: str, elements: dict[str, list[FakeElement]]) -> None:
        self.url = url
        self.elements = elements

    def locator(self, selector: str) -> FakeCollection:
        return FakeCollection(self.elements.get(selector, []))


class FakePage(FakeFrame):
    def __init__(self, url: str, frames: list[FakeFrame], elements: dict[str, list[FakeElement]]):
        super().__init__(url, elements)
        self.frames = frames
        self.route_handler: Callable[..., Any] | None = None

    async def goto(self, _: str, **__: Any) -> None:
        return None

    async def route(self, _: str, handler: Callable[..., Any]) -> None:
        self.route_handler = handler

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


class FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeRoute:
    def __init__(self) -> None:
        self.action: str | None = None

    async def abort(self) -> None:
        self.action = "abort"

    async def continue_(self) -> None:
        self.action = "continue"


class FakeWebSocketRoute:
    def __init__(self) -> None:
        self.close_code: int | None = None

    async def close(self, *, code: int) -> None:
        self.close_code = code


class FakeRawContext:
    def __init__(self, page: FakePage, events: list[str]) -> None:
        self.page = page
        self.events = events
        self.route_handler: Callable[..., Any] | None = None
        self.web_socket_handler: Callable[..., Any] | None = None

    async def route(self, _: str, handler: Callable[..., Any]) -> None:
        self.events.append("context-route")
        self.route_handler = handler

    async def route_web_socket(self, _: str, handler: Callable[..., Any]) -> None:
        self.events.append("websocket-route")
        self.web_socket_handler = handler

    async def new_page(self) -> FakePage:
        self.events.append("new-page")
        return self.page

    async def close(self) -> None:
        self.events.append("context-close")


class FakeRawBrowser:
    def __init__(self, context: FakeRawContext, events: list[str]) -> None:
        self.context = context
        self.events = events
        self.context_options: dict[str, object] = {}

    async def new_context(self, **kwargs: object) -> FakeRawContext:
        self.context_options = kwargs
        self.events.append("new-context")
        return self.context

    async def close(self) -> None:
        self.events.append("browser-close")


class FakePlaywrightManager:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def stop(self) -> None:
        self.events.append("manager-stop")


class FakeGateway:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.origins: tuple[str, ...] = ()
        self.released_session_id: str | None = None

    async def create_session(self, origins: tuple[str, ...]) -> BrowserbaseSession:
        self.origins = origins
        return BrowserbaseSession("session_12345", "wss://never-used.example.test")

    async def connect(self, _: BrowserbaseSession) -> FakeBrowser:
        return self.browser

    async def release_session(self, session_id: str) -> bool:
        self.released_session_id = session_id
        return True


def context(adapter: CheckoutAdapter) -> CheckoutContext:
    owner_id = uuid4()
    return CheckoutContext(
        execution_id=uuid4(),
        cart_item_id=uuid4(),
        owner_id=owner_id,
        agent_id=uuid4(),
        payment_method_id=uuid4(),
        adapter_key="demo",
        adapter=adapter,
        checkout_url="https://merchant.example.test/checkout/one",
        checkout_origin="https://merchant.example.test",
        approved_title="Managed checkout",
        approved_quantity=2,
        amount=Decimal("25.00"),
        currency="EUR",
        provider="stripe_issuing",
        provider_card_id="ic_card123",
        card_metadata=ExpectedCardMetadata(owner_id, "4242", "visa", 12, 2030),
        billing_details={"full_name": "Alex Example", "email": "alex@example.test"},
    )


def adapter(**overrides: Any) -> CheckoutAdapter:
    values: dict[str, Any] = {
        "allowed_origins": ("https://merchant.example.test",),
        "payment_origins": ("https://payments.example.test",),
        "product_title_selector": "#product-title",
        "quantity_selector": "#quantity",
        "total_selector": "#total",
        "name_selector": "#name",
        "billing_email_selector": "#email",
        "card_number_selector": "#number",
        "expiry_selector": "#expiry",
        "cvc_selector": "#cvc",
        "submit_selector": "#submit",
        "success_selector": "#success",
        "order_reference_selector": "#order",
        "receipt_url_selector": "#receipt",
    }
    values.update(overrides)
    return CheckoutAdapter(**values)


async def test_browser_checkout_loads_card_late_and_marks_submitted_before_click() -> None:
    events: list[str] = []
    success = FakeElement("success", events, visible=False)
    main_elements: dict[str, list[FakeElement]] = {
        "#product-title": [FakeElement("product-title", events, text="Managed checkout")],
        "#quantity": [FakeElement("quantity", events, value="2")],
        "#total": [FakeElement("total", events, text="Total EUR 25.00")],
        "#name": [FakeElement("name", events)],
        "#email": [FakeElement("email", events)],
        "#success": [success],
        "#order": [FakeElement("order", events, text="ORDER-123")],
        "#receipt": [
            FakeElement(
                "receipt",
                events,
                href="https://merchant.example.test/receipts/ORDER-123",
            )
        ],
    }
    main_elements["#submit"] = [
        FakeElement("submit", events, clicked=lambda: setattr(success, "visible", True))
    ]
    payment_elements = {
        "#number": [FakeElement("number", events)],
        "#expiry": [FakeElement("expiry", events)],
        "#cvc": [FakeElement("cvc", events)],
    }
    main = FakeFrame("https://merchant.example.test/checkout/one", main_elements)
    payment = FakeFrame("https://payments.example.test/elements/card", payment_elements)
    page = FakePage(main.url, [main, payment], {})
    browser = FakeBrowser(page)
    checkout = BrowserbaseCheckout(FakeGateway(browser), result_timeout_seconds=1)  # type: ignore[arg-type]

    async def load_card() -> IssuingCardSecret:
        events.append("load-card")
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def session_started(_: str) -> None:
        events.append("session-started")

    async def submitted(_: str) -> None:
        events.append("submitted")

    async def prepare_submission() -> None:
        events.append("prepare-submission")

    result = await checkout.run(
        context(adapter()),
        load_card=load_card,
        on_session_started=session_started,
        prepare_submission=prepare_submission,
        mark_submitted=submitted,
    )

    assert result.order_reference == "ORDER-123"
    assert result.receipt_url == "https://merchant.example.test/receipts/ORDER-123"
    assert events.index("text:total") < events.index("load-card")
    assert events.index("prepare-submission") < events.index("load-card")
    assert events.index("load-card") < events.index("submitted")
    assert events.index("submitted") < events.index("fill:number")
    assert events.index("submitted") < events.index("click:submit")
    assert browser.closed


async def test_browser_checkout_supports_split_expiry_fields() -> None:
    events: list[str] = []
    success = FakeElement("success", events, visible=True)
    main_elements = {
        "#product-title": [FakeElement("product-title", events, text="Managed checkout")],
        "#quantity": [FakeElement("quantity", events, value="2")],
        "#total": [FakeElement("total", events, text="EUR 25.00")],
        "#submit": [FakeElement("submit", events)],
        "#success": [success],
    }
    payment_elements = {
        "#number": [FakeElement("number", events)],
        "#month": [FakeElement("month", events)],
        "#year": [FakeElement("year", events)],
        "#cvc": [FakeElement("cvc", events)],
    }
    main = FakeFrame("https://merchant.example.test/checkout/one", main_elements)
    payment = FakeFrame("https://payments.example.test/card", payment_elements)
    browser = FakeBrowser(FakePage(main.url, [main, payment], {}))
    checkout = BrowserbaseCheckout(FakeGateway(browser), result_timeout_seconds=1)  # type: ignore[arg-type]
    split = adapter(
        expiry_selector=None,
        expiry_month_selector="#month",
        expiry_year_selector="#year",
        name_selector=None,
        billing_email_selector=None,
        order_reference_selector=None,
        receipt_url_selector=None,
    )

    async def load_card() -> IssuingCardSecret:
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def nothing(_: str) -> None:
        return None

    await checkout.run(
        context(split),
        load_card=load_card,
        on_session_started=nothing,
        prepare_submission=lambda: nothing(""),
        mark_submitted=nothing,
    )

    assert "fill:month" in events
    assert "fill:year" in events


async def test_browser_checkout_rejects_unapproved_frame_before_loading_card() -> None:
    events: list[str] = []
    main = FakeFrame(
        "https://merchant.example.test/checkout/one",
        {"#total": [FakeElement("total", events, text="EUR 25.00")]},
    )
    injected = FakeFrame("https://evil.example.test/collect", {})
    browser = FakeBrowser(FakePage(main.url, [main, injected], {}))
    checkout = BrowserbaseCheckout(FakeGateway(browser), result_timeout_seconds=1)  # type: ignore[arg-type]
    loaded = False

    async def load_card() -> IssuingCardSecret:
        nonlocal loaded
        loaded = True
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def nothing(_: str) -> None:
        return None

    with pytest.raises(CheckoutError) as caught:
        await checkout.run(
            context(adapter()),
            load_card=load_card,
            on_session_started=nothing,
            prepare_submission=lambda: nothing(""),
            mark_submitted=nothing,
        )

    assert caught.value.code == CheckoutErrorCode.origin_blocked
    assert not loaded
    assert browser.closed


async def test_browser_releases_created_session_when_session_persistence_fails() -> None:
    page = FakePage("about:blank", [], {})
    browser = FakeBrowser(page)
    gateway = FakeGateway(browser)
    checkout = BrowserbaseCheckout(gateway, result_timeout_seconds=1)  # type: ignore[arg-type]

    async def fail_session_persistence(_: str) -> None:
        raise RuntimeError("database unavailable")

    async def unused_card() -> IssuingCardSecret:
        raise AssertionError("card must not be loaded")

    async def unused_callback(_: str = "") -> None:
        raise AssertionError("callback must not be reached")

    with pytest.raises(RuntimeError):
        await checkout.run(
            context(adapter()),
            load_card=unused_card,
            on_session_started=fail_session_persistence,
            prepare_submission=unused_callback,
            mark_submitted=unused_callback,
        )

    assert gateway.released_session_id == "session_12345"
    assert not browser.closed


async def test_browser_checkout_rechecks_total_immediately_before_submission() -> None:
    events: list[str] = []
    main_elements = {
        "#product-title": [FakeElement("product-title", events, text="Managed checkout")],
        "#quantity": [FakeElement("quantity", events, value="2")],
        "#total": [
            ChangingTextElement(
                "total",
                events,
                ["EUR 25.00", "EUR 25.00", "EUR 30.00"],
            )
        ],
        "#submit": [FakeElement("submit", events)],
        "#success": [FakeElement("success", events, visible=True)],
    }
    payment_elements = {
        "#number": [FakeElement("number", events)],
        "#expiry": [FakeElement("expiry", events)],
        "#cvc": [FakeElement("cvc", events)],
    }
    main = FakeFrame("https://merchant.example.test/checkout/one", main_elements)
    payment = FakeFrame("https://payments.example.test/card", payment_elements)
    browser = FakeBrowser(FakePage(main.url, [main, payment], {}))
    checkout = BrowserbaseCheckout(FakeGateway(browser), result_timeout_seconds=1)  # type: ignore[arg-type]
    submitted = False
    loaded = False

    async def load_card() -> IssuingCardSecret:
        nonlocal loaded
        loaded = True
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def session_started(_: str) -> None:
        return None

    async def mark_submitted(_: str) -> None:
        nonlocal submitted
        submitted = True

    with pytest.raises(CheckoutError) as caught:
        await checkout.run(
            context(
                adapter(
                    name_selector=None,
                    billing_email_selector=None,
                    order_reference_selector=None,
                    receipt_url_selector=None,
                )
            ),
            load_card=load_card,
            on_session_started=session_started,
            prepare_submission=lambda: session_started(""),
            mark_submitted=mark_submitted,
        )

    assert caught.value.code == CheckoutErrorCode.total_mismatch
    assert loaded
    assert not submitted
    assert "click:submit" not in events


@pytest.mark.parametrize(
    ("display_total", "approved_currency"),
    [
        ("Total CAD CA$25.00", "USD"),
        ("Total CNY ¥25.00", "JPY"),
        ("Total USD -25.00", "USD"),
    ],
)
async def test_browser_rejects_ambiguous_or_signed_currency_total_before_card_load(
    display_total: str,
    approved_currency: str,
) -> None:
    events: list[str] = []
    main = FakeFrame(
        "https://merchant.example.test/checkout/one",
        {
            "#product-title": [FakeElement("product-title", events, text="Managed checkout")],
            "#quantity": [FakeElement("quantity", events, value="2")],
            "#total": [FakeElement("total", events, text=display_total)],
        },
    )
    gateway = FakeGateway(FakeBrowser(FakePage(main.url, [main], {})))
    checkout = BrowserbaseCheckout(gateway, result_timeout_seconds=1)  # type: ignore[arg-type]
    loaded = False

    async def load_card() -> IssuingCardSecret:
        nonlocal loaded
        loaded = True
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def nothing(_: str = "") -> None:
        return None

    checkout_context = replace(
        context(adapter()),
        currency=approved_currency,
        amount=Decimal("25.00"),
    )
    with pytest.raises(CheckoutError) as caught:
        await checkout.run(
            checkout_context,
            load_card=load_card,
            on_session_started=nothing,
            prepare_submission=nothing,
            mark_submitted=nothing,
        )

    assert caught.value.code == CheckoutErrorCode.total_mismatch
    assert not loaded


async def test_browser_rejects_redirect_to_another_configured_merchant_before_card_load() -> None:
    events: list[str] = []
    redirected = FakeFrame(
        "https://other-merchant.example.test/checkout/one",
        {
            "#product-title": [FakeElement("product-title", events, text="Managed checkout")],
            "#quantity": [FakeElement("quantity", events, value="2")],
            "#total": [FakeElement("total", events, text="EUR 25.00")],
        },
    )
    gateway = FakeGateway(FakeBrowser(FakePage(redirected.url, [redirected], {})))
    checkout = BrowserbaseCheckout(gateway, result_timeout_seconds=1)  # type: ignore[arg-type]
    loaded = False

    async def load_card() -> IssuingCardSecret:
        nonlocal loaded
        loaded = True
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def nothing(_: str = "") -> None:
        return None

    checkout_context = context(
        adapter(
            allowed_origins=(
                "https://merchant.example.test",
                "https://other-merchant.example.test",
            )
        )
    )
    with pytest.raises(CheckoutError) as caught:
        await checkout.run(
            checkout_context,
            load_card=load_card,
            on_session_started=nothing,
            prepare_submission=nothing,
            mark_submitted=nothing,
        )

    assert caught.value.code == CheckoutErrorCode.origin_blocked
    assert not loaded


async def test_browser_checkout_rejects_same_price_item_substitution_before_card_load() -> None:
    events: list[str] = []
    main_elements = {
        "#product-title": [FakeElement("product-title", events, text="Different product")],
        "#quantity": [FakeElement("quantity", events, value="2")],
        "#total": [FakeElement("total", events, text="EUR 25.00")],
    }
    main = FakeFrame("https://merchant.example.test/checkout/one", main_elements)
    browser = FakeBrowser(FakePage(main.url, [main], {}))
    checkout = BrowserbaseCheckout(FakeGateway(browser), result_timeout_seconds=1)  # type: ignore[arg-type]
    loaded = False

    async def load_card() -> IssuingCardSecret:
        nonlocal loaded
        loaded = True
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def nothing(_: str) -> None:
        return None

    with pytest.raises(CheckoutError) as caught:
        await checkout.run(
            context(adapter()),
            load_card=load_card,
            on_session_started=nothing,
            prepare_submission=lambda: nothing(""),
            mark_submitted=nothing,
        )

    assert caught.value.code == CheckoutErrorCode.item_mismatch
    assert not loaded


async def test_browser_checkout_rechecks_quantity_before_submission() -> None:
    events: list[str] = []
    main_elements = {
        "#product-title": [FakeElement("product-title", events, text="Managed checkout")],
        "#quantity": [ChangingValueElement("quantity", events, ["2", "3"])],
        "#total": [FakeElement("total", events, text="EUR 25.00")],
        "#submit": [FakeElement("submit", events)],
    }
    payment_elements = {
        "#number": [FakeElement("number", events)],
        "#expiry": [FakeElement("expiry", events)],
        "#cvc": [FakeElement("cvc", events)],
    }
    main = FakeFrame("https://merchant.example.test/checkout/one", main_elements)
    payment = FakeFrame("https://payments.example.test/card", payment_elements)
    browser = FakeBrowser(FakePage(main.url, [main, payment], {}))
    checkout = BrowserbaseCheckout(FakeGateway(browser), result_timeout_seconds=1)  # type: ignore[arg-type]
    submitted = False

    async def load_card() -> IssuingCardSecret:
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def session_started(_: str) -> None:
        return None

    async def mark_submitted(_: str) -> None:
        nonlocal submitted
        submitted = True

    with pytest.raises(CheckoutError) as caught:
        await checkout.run(
            context(
                adapter(
                    name_selector=None,
                    billing_email_selector=None,
                    order_reference_selector=None,
                    receipt_url_selector=None,
                )
            ),
            load_card=load_card,
            on_session_started=session_started,
            prepare_submission=lambda: session_started(""),
            mark_submitted=mark_submitted,
        )

    assert caught.value.code == CheckoutErrorCode.quantity_mismatch
    assert not submitted
    assert "click:submit" not in events


async def test_browser_request_guard_blocks_unapproved_resource_origins(caplog) -> None:
    page = FakePage("about:blank", [], {})
    await BrowserbaseCheckout._install_request_guard(
        page,
        (
            "https://merchant.example.test",
            "https://payments.example.test",
            "https://static.example.test",
        ),
    )
    assert page.route_handler is not None

    allowed_route = FakeRoute()
    blocked_route = FakeRoute()
    await page.route_handler(
        allowed_route,
        FakeRequest("https://static.example.test/assets/checkout.js?token=secret-value"),
    )
    await page.route_handler(
        blocked_route,
        FakeRequest("https://evil.example.test/collect?pan=4242424242424242"),
    )

    assert allowed_route.action == "continue"
    assert blocked_route.action == "abort"
    assert "secret-value" not in caplog.text
    assert "4242424242424242" not in caplog.text


async def test_browser_blocks_card_fill_if_bound_frame_navigates_after_submission() -> None:
    events: list[str] = []
    main_elements = {
        "#product-title": [FakeElement("product-title", events, text="Managed checkout")],
        "#quantity": [FakeElement("quantity", events, value="2")],
        "#total": [FakeElement("total", events, text="EUR 25.00")],
        "#submit": [FakeElement("submit", events)],
        "#success": [FakeElement("success", events, visible=True)],
    }
    payment_elements = {
        "#number": [FakeElement("number", events)],
        "#expiry": [FakeElement("expiry", events)],
        "#cvc": [FakeElement("cvc", events)],
    }
    main = FakeFrame("https://merchant.example.test/checkout/one", main_elements)
    payment = FakeFrame("https://payments.example.test/card", payment_elements)
    browser = FakeBrowser(FakePage(main.url, [main, payment], {}))
    checkout = BrowserbaseCheckout(FakeGateway(browser), result_timeout_seconds=1)  # type: ignore[arg-type]

    async def load_card() -> IssuingCardSecret:
        events.append("load-card")
        return IssuingCardSecret("4242424242424242", "123", 12, 2030)

    async def nothing(_: str = "") -> None:
        return None

    async def mark_submitted(_: str) -> None:
        events.append("submitted")
        payment.url = "https://evil.example.test/collect"

    with pytest.raises(CheckoutError) as caught:
        await checkout.run(
            context(
                adapter(
                    name_selector=None,
                    billing_email_selector=None,
                    order_reference_selector=None,
                    receipt_url_selector=None,
                )
            ),
            load_card=load_card,
            on_session_started=nothing,
            prepare_submission=nothing,
            mark_submitted=mark_submitted,
        )

    assert caught.value.code == CheckoutErrorCode.origin_blocked
    assert events.index("load-card") < events.index("submitted")
    assert "fill:number" not in events


async def test_playwright_context_blocks_service_workers_websockets_and_popup_egress() -> None:
    events: list[str] = []
    page = FakePage("about:blank", [], {})
    raw_context = FakeRawContext(page, events)
    raw_browser = FakeRawBrowser(raw_context, events)
    manager = FakePlaywrightManager(events)
    browser = _PlaywrightBrowser(raw_browser, manager)

    created_page = await browser.new_page(("https://merchant.example.test",))

    assert created_page is page
    assert raw_browser.context_options == {"service_workers": "block"}
    assert events[:4] == ["new-context", "context-route", "websocket-route", "new-page"]
    assert raw_context.route_handler is not None
    assert raw_context.web_socket_handler is not None

    popup_request = FakeRoute()
    await raw_context.route_handler(
        popup_request,
        FakeRequest("https://evil.example.test/popup?secret=do-not-send"),
    )
    web_socket = FakeWebSocketRoute()
    await raw_context.web_socket_handler(web_socket)

    assert popup_request.action == "abort"
    assert web_socket.close_code == 1008
    await browser.close()
    assert events[-3:] == ["context-close", "browser-close", "manager-stop"]
