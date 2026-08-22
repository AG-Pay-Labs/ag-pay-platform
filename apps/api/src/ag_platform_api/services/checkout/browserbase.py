import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

import httpx

from ag_platform_api.core.config import LOCAL_DIRECT_CARD_PROVIDER
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.form_mapping import (
    CheckoutFormMapper,
    PaymentFormSelectorMap,
)
from ag_platform_api.services.checkout.origins import (
    browserbase_allowed_domains,
    normalize_origin,
    validate_checkout_url,
    validate_receipt_url,
    validate_stripe_hosted_test_checkout_url,
)
from ag_platform_api.services.checkout.types import (
    CURRENCY_EXPONENTS,
    AuthorizationOutcome,
    BrowserbaseSession,
    BrowserCheckoutResult,
    CheckoutAdapter,
    CheckoutContext,
    IssuingCardSecret,
    normalize_item_text,
)

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,255}$")
ORDER_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/# -]{0,127}$")
STRIPE_TEST_SESSION_PATTERN = re.compile(r"^cs_test_[A-Za-z0-9]+$")
STRIPE_HOSTED_QUANTITY_LABEL_PATTERN = re.compile(r"(?:^|\b)(?:qty|quantity)\b")
STRIPE_HOSTED_QUANTITY_VALUE_PATTERN = re.compile(
    r"(?:^|\b)(?:qty|quantity)\s*[:x×]?\s*(\d+)(?=\b|,)"
)
NUMBER_PATTERN = re.compile(r"(?<!\d)(\d{1,3}(?:[., ]\d{3})*[.,]\d{1,3}|\d+[.,]\d{1,3}|\d+)(?!\d)")
DETERMINISTIC_INPUT_SCRIPT = """
(element, value) => {
  let nextValue = String(value);
  let descriptor;
  if (element instanceof HTMLInputElement) {
    descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
  } else if (element instanceof HTMLSelectElement) {
    const wanted = nextValue.trim().toLocaleLowerCase();
    const option = Array.from(element.options).find((candidate) =>
      candidate.value === nextValue ||
      candidate.label.trim().toLocaleLowerCase() === wanted ||
      candidate.text.trim().toLocaleLowerCase() === wanted
    );
    if (!option) {
      throw new Error("payment select option was not found");
    }
    nextValue = option.value;
    descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  } else {
    throw new Error("unsupported payment control");
  }
  if (!descriptor || !descriptor.set) {
    throw new Error("payment control has no native value setter");
  }
  element.focus();
  descriptor.set.call(element, nextValue);
  element.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
  element.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  element.blur();
}
""".strip()
CARD_FIELD_PREFLIGHT_SCRIPT = """
(element, options) => {
  const input = element instanceof HTMLInputElement;
  const select = element instanceof HTMLSelectElement;
  const button = element instanceof HTMLButtonElement;
  const submit = options.kind === "submit";
  const splitExpiry = options.kind === "expiry_month" || options.kind === "expiry_year";
  const inputType = input ? element.type.toLocaleLowerCase() : "";
  const role = (element.getAttribute("role") || "").toLocaleLowerCase();
  const ariaDisabled = (element.getAttribute("aria-disabled") || "").toLocaleLowerCase() === "true";
  const style = window.getComputedStyle(element);
  const bounds = element.getBoundingClientRect();
  const submitControl = button || (input && ["button", "image", "submit"].includes(inputType));
  if (
    !element.isConnected || element.disabled || ariaDisabled || bounds.width <= 0 ||
    bounds.height <= 0 || style.display === "none" || style.visibility === "hidden" ||
    (submit && !submitControl && role !== "button")
  ) {
    return false;
  }
  if (!submit) {
    const splitSelect = select && splitExpiry;
    const blocked = new Set([
      "hidden", "button", "submit", "reset", "file", "checkbox", "radio"
    ]);
    if ((!input && !splitSelect) || (input && (element.readOnly || blocked.has(inputType)))) {
      return false;
    }
  }
  if (Object.prototype.hasOwnProperty.call(element, options.marker)) {
    return false;
  }
  Object.defineProperty(element, options.marker, { value: true, configurable: true });
  return true;
}
""".strip()
CARD_FIELD_PREFLIGHT_CLEANUP_SCRIPT = """
(element, marker) => {
  try { delete element[marker]; } catch (_) {}
}
""".strip()


class LocatorLike(Protocol):
    async def count(self) -> int: ...

    def nth(self, index: int) -> "LocatorLike": ...

    async def is_visible(self) -> bool: ...

    async def inner_text(self) -> str: ...

    async def input_value(self) -> str: ...

    async def fill(self, value: str) -> None: ...

    async def select_option(
        self,
        value: str | None = None,
        *,
        label: str | None = None,
    ) -> object: ...

    async def click(self) -> None: ...

    async def get_attribute(self, name: str) -> str | None: ...

    async def element_handle(self) -> "ElementHandleLike | None": ...


class ElementHandleLike(Protocol):
    async def fill(self, value: str) -> None: ...

    async def click(self) -> None: ...

    async def evaluate(self, expression: str, arg: object | None = None) -> object: ...


class RequestLike(Protocol):
    @property
    def url(self) -> str: ...


class RouteLike(Protocol):
    async def abort(self) -> None: ...

    async def continue_(self) -> None: ...


class FrameLike(Protocol):
    @property
    def url(self) -> str: ...

    def locator(self, selector: str) -> LocatorLike: ...


class PageLike(FrameLike, Protocol):
    @property
    def frames(self) -> list[FrameLike]: ...

    async def goto(self, url: str, **kwargs: Any) -> object: ...

    async def route(
        self,
        pattern: str,
        handler: Callable[[RouteLike, RequestLike], Awaitable[None]],
    ) -> None: ...

    async def wait_for_timeout(self, milliseconds: float) -> None: ...


class ConnectedBrowser(Protocol):
    async def new_page(self, permitted_origins: tuple[str, ...]) -> PageLike: ...

    async def close(self) -> None: ...


BrowserConnector = Callable[[str], Awaitable[ConnectedBrowser]]
CardLoader = Callable[[], Awaitable[IssuingCardSecret]]
SessionStarted = Callable[[str], Awaitable[None]]
PrepareSubmission = Callable[[], Awaitable[None]]
MarkSubmitted = Callable[[str], Awaitable[None]]
ObserveOutcome = Callable[[], Awaitable[AuthorizationOutcome]]
FormMapped = Callable[[Mapping[str, object]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class LocatedElement:
    locator: LocatorLike
    frame: FrameLike


@dataclass(frozen=True, slots=True)
class ResolvedCardField:
    handle: ElementHandleLike
    frame: FrameLike
    kind: str


class BrowserbaseGateway:
    def __init__(
        self,
        *,
        api_key: str,
        project_id: str,
        api_url: str = "https://api.browserbase.com/v1",
        region: str | None = None,
        session_timeout_seconds: int = 120,
        client: httpx.AsyncClient | None = None,
        connector: BrowserConnector | None = None,
    ) -> None:
        self._api_key = api_key
        self._project_id = project_id
        self._api_url = api_url.rstrip("/")
        self._region = region
        self._session_timeout_seconds = min(21_600, max(60, session_timeout_seconds))
        self._client = client or httpx.AsyncClient(timeout=15.0)
        self._owns_client = client is None
        self._connector = connector or _connect_playwright

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_session(
        self,
        origins: tuple[str, ...],
        *,
        record_session: bool = False,
        log_session: bool = False,
    ) -> BrowserbaseSession:
        allowed_domains = browserbase_allowed_domains(origins)
        browser_settings: dict[str, object] = {
            "recordSession": record_session,
            "logSession": log_session,
            "solveCaptchas": False,
            "allowedDomains": allowed_domains,
        }
        body: dict[str, object] = {
            "projectId": self._project_id,
            "browserSettings": browser_settings,
            "keepAlive": False,
            "timeout": self._session_timeout_seconds,
        }
        if self._region:
            body["region"] = self._region
        try:
            response = await self._client.post(
                f"{self._api_url}/sessions",
                headers={"X-BB-API-Key": self._api_key, "Content-Type": "application/json"},
                json=body,
            )
        except httpx.HTTPError:
            raise CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True) from None
        if response.status_code < 200 or response.status_code >= 300:
            raise CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True)
        try:
            payload = response.json()
            session_id = str(payload["id"])
            connect_url = str(payload["connectUrl"])
        except (KeyError, TypeError, ValueError):
            raise CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True) from None
        if not SESSION_ID_PATTERN.fullmatch(session_id) or not connect_url.startswith(
            ("wss://", "https://")
        ):
            raise CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True)
        session = BrowserbaseSession(session_id=session_id, connect_url=connect_url)
        del payload, connect_url
        return session

    async def release_session(self, session_id: str) -> bool:
        if SESSION_ID_PATTERN.fullmatch(session_id) is None:
            return False
        try:
            response = await self._client.post(
                f"{self._api_url}/sessions/{session_id}",
                headers={"X-BB-API-Key": self._api_key, "Content-Type": "application/json"},
                json={"status": "REQUEST_RELEASE"},
            )
        except httpx.HTTPError:
            return False
        return 200 <= response.status_code < 300

    async def connect(self, session: BrowserbaseSession) -> ConnectedBrowser:
        try:
            return await self._connector(session.connect_url)
        except CheckoutError:
            raise
        except Exception:
            raise CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True) from None


class BrowserbaseCheckout:
    def __init__(
        self,
        gateway: BrowserbaseGateway,
        *,
        result_timeout_seconds: float = 60,
        form_mapper: CheckoutFormMapper | None = None,
    ) -> None:
        self._gateway = gateway
        self._result_timeout_ms = max(1, int(result_timeout_seconds * 1000))
        self._form_mapper = form_mapper

    async def run(
        self,
        context: CheckoutContext,
        *,
        load_card: CardLoader,
        on_session_started: SessionStarted,
        prepare_submission: PrepareSubmission,
        mark_submitted: MarkSubmitted,
        observe_outcome: ObserveOutcome | None = None,
        on_form_mapped: FormMapped | None = None,
    ) -> BrowserCheckoutResult:
        adapter = context.adapter
        allowed_origins = tuple(normalize_origin(value) for value in adapter.allowed_origins)
        payment_origins = tuple(normalize_origin(value) for value in adapter.payment_origins)
        resource_origins = tuple(normalize_origin(value) for value in adapter.resource_origins)
        result_origins = tuple(normalize_origin(value) for value in adapter.result_origins)
        merchant_origins = (normalize_origin(context.checkout_origin),)
        if merchant_origins[0] not in allowed_origins:
            raise CheckoutError(CheckoutErrorCode.origin_blocked)
        checkout_url = validate_checkout_url(context.checkout_url, merchant_origins)
        observe_test_session = adapter.checkout_mode == "stripe_hosted_test"
        record_public_test_session = (
            observe_test_session and context.provider != LOCAL_DIRECT_CARD_PROVIDER
        )
        if observe_test_session:
            checkout_url = validate_stripe_hosted_test_checkout_url(checkout_url)
        session = await self._gateway.create_session(
            allowed_origins + payment_origins + resource_origins,
            record_session=record_public_test_session,
            log_session=record_public_test_session,
        )
        browser: ConnectedBrowser | None = None
        mapped_form = False
        new_form_snapshot: dict[str, str | None] | None = None
        try:
            await on_session_started(session.session_id)
            browser = await self._gateway.connect(session)
            page = await browser.new_page(allowed_origins + payment_origins + resource_origins)
            await self._install_request_guard(
                page,
                allowed_origins + payment_origins + resource_origins,
            )
            try:
                await page.goto(checkout_url, wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                raise CheckoutError(
                    CheckoutErrorCode.browser_navigation_failed, retryable=True
                ) from None
            self._validate_page(page, merchant_origins, payment_origins)
            await self._verify_item(page, context, merchant_origins)
            await self._verify_total(page, context, merchant_origins)
            if adapter.payment_form_strategy == "browserbase_ai":
                if context.resolved_form_config is not None:
                    mapping = PaymentFormSelectorMap.from_snapshot(
                        dict(context.resolved_form_config)
                    )
                else:
                    if self._form_mapper is None:
                        raise CheckoutError(CheckoutErrorCode.form_analysis_failed, retryable=True)
                    mapping = await self._form_mapper.map_payment_form(
                        browserbase_session_id=session.session_id,
                        billing_fields=self._billing_fields_to_map(
                            adapter, context.billing_details
                        ),
                    )
                    new_form_snapshot = mapping.to_snapshot()
                adapter = mapping.apply(adapter)
                mapped_form = True
                await self._validate_discovered_form(
                    page,
                    adapter,
                    merchant_origins=merchant_origins,
                    payment_origins=payment_origins,
                )
            discovered_origins = tuple(dict.fromkeys(merchant_origins + payment_origins))
            form_control_origins = discovered_origins if mapped_form else merchant_origins
            await self._fill_billing(
                page,
                adapter,
                context.billing_details,
                form_control_origins,
                javascript=mapped_form,
            )
            if adapter.submit_selector is None:
                raise CheckoutError(CheckoutErrorCode.adapter_invalid)
            submit = await self._unique_visible(
                page,
                adapter.submit_selector,
                form_control_origins,
                CheckoutErrorCode.payment_form_not_found,
                timeout_ms=10_000,
            )
            submit_handle = await self._resolve_handle(
                submit,
                form_control_origins,
                CheckoutErrorCode.payment_form_not_found,
            )
            card_fields = await self._resolve_card_fields(page, adapter, payment_origins)
            await prepare_submission()
            self._validate_page(page, merchant_origins, payment_origins)
            await self._verify_item(page, context, merchant_origins)
            await self._verify_total(page, context, merchant_origins)
            await self._preflight_card_fields(
                card_fields,
                submit_handle=submit_handle,
                retryable=adapter.payment_form_strategy == "resolved",
            )
            if new_form_snapshot is not None and on_form_mapped is not None:
                await on_form_mapped(new_form_snapshot)
            card = await load_card()
            try:
                self._validate_page(page, merchant_origins, payment_origins)
                await self._verify_item(page, context, merchant_origins)
                await self._verify_total(page, context, merchant_origins)
                await self._preflight_card_fields(
                    card_fields,
                    submit_handle=submit_handle,
                    retryable=adapter.payment_form_strategy == "resolved",
                )
                await mark_submitted(session.session_id)
                await self._fill_resolved_card(card_fields, card, payment_origins)
                try:
                    await submit_handle.click()
                except Exception:
                    raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown) from None
            finally:
                del card
            if adapter.checkout_mode == "stripe_hosted_test":
                if observe_outcome is not None:
                    outcome = await observe_outcome()
                    return BrowserCheckoutResult(None, None, outcome)
                return await self._wait_for_hosted_test_result(
                    page,
                    context,
                    merchant_origins,
                    result_origins,
                    payment_origins,
                )
            return await self._wait_for_result(
                page,
                adapter,
                tuple(dict.fromkeys(merchant_origins + result_origins)),
                payment_origins,
            )
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            try:
                await self._gateway.release_session(session.session_id)
            except Exception:
                pass

    @staticmethod
    async def _install_request_guard(
        page: PageLike,
        permitted_origins: tuple[str, ...],
    ) -> None:
        permitted = set(permitted_origins)

        async def guard(route: RouteLike, request: RequestLike) -> None:
            try:
                permitted_request = normalize_origin(request.url) in permitted
            except CheckoutError:
                permitted_request = False
            if permitted_request:
                await route.continue_()
            else:
                await route.abort()

        try:
            await page.route("**/*", guard)
        except Exception:
            raise CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True) from None

    @staticmethod
    def _validate_page(
        page: PageLike,
        allowed_origins: tuple[str, ...],
        payment_origins: tuple[str, ...],
    ) -> None:
        if page.url and page.url != "about:blank":
            validate_checkout_url(page.url, allowed_origins)
        valid_frame_origins = set(allowed_origins) | set(payment_origins)
        for frame in page.frames:
            if not frame.url or frame.url == "about:blank":
                continue
            if normalize_origin(frame.url) not in valid_frame_origins:
                raise CheckoutError(CheckoutErrorCode.origin_blocked)

    async def _verify_total(
        self,
        page: PageLike,
        context: CheckoutContext,
        allowed_origins: tuple[str, ...],
    ) -> None:
        located = await self._unique_visible(
            page,
            context.adapter.total_selector,
            allowed_origins,
            CheckoutErrorCode.total_not_found,
            timeout_ms=10_000,
        )
        try:
            text = (await located.locator.inner_text())[:500]
        except Exception:
            raise CheckoutError(CheckoutErrorCode.total_not_found) from None
        matches = (
            amount_text_matches(text, context.amount, context.currency)
            if context.adapter.checkout_mode == "stripe_hosted_test"
            else money_text_matches(text, context.amount, context.currency)
        )
        if not matches:
            raise CheckoutError(CheckoutErrorCode.total_mismatch)

    async def _verify_item(
        self,
        page: PageLike,
        context: CheckoutContext,
        allowed_origins: tuple[str, ...],
    ) -> None:
        title = await self._unique_visible(
            page,
            context.adapter.product_title_selector,
            allowed_origins,
            CheckoutErrorCode.item_mismatch,
            timeout_ms=10_000,
        )
        hosted_test = context.adapter.checkout_mode == "stripe_hosted_test"
        if hosted_test and context.approved_quantity == 1:
            quantity = await self._optional_unique_visible(
                page,
                context.adapter.quantity_selector,
                allowed_origins,
                CheckoutErrorCode.quantity_mismatch,
                timeout_ms=1_000,
            )
        else:
            quantity = await self._unique_visible(
                page,
                context.adapter.quantity_selector,
                allowed_origins,
                CheckoutErrorCode.quantity_mismatch,
                timeout_ms=10_000,
            )
        try:
            merchant_title = normalize_item_text((await title.locator.inner_text())[:500])
        except Exception:
            raise CheckoutError(CheckoutErrorCode.item_mismatch) from None
        if merchant_title != normalize_item_text(context.approved_title):
            raise CheckoutError(CheckoutErrorCode.item_mismatch)
        if quantity is None:
            merchant_quantity = "1"
        else:
            try:
                merchant_quantity = await quantity.locator.input_value()
            except Exception:
                try:
                    merchant_quantity = (await quantity.locator.inner_text())[:100]
                except Exception:
                    raise CheckoutError(CheckoutErrorCode.quantity_mismatch) from None
        if quantity is None:
            return
        normalized_quantity = normalize_item_text(merchant_quantity)
        if hosted_test:
            labels = STRIPE_HOSTED_QUANTITY_LABEL_PATTERN.findall(normalized_quantity)
            matches = STRIPE_HOSTED_QUANTITY_VALUE_PATTERN.findall(normalized_quantity)
            if not labels and not matches and context.approved_quantity == 1:
                # Stripe omits an explicit Qty label for a single line item and
                # renders this element as the product description.
                return
            if (
                len(labels) != 1
                or len(matches) != 1
                or int(matches[0]) != context.approved_quantity
            ):
                raise CheckoutError(CheckoutErrorCode.quantity_mismatch)
            return
        match = re.fullmatch(
            r"(?:qty|quantity)?\s*[:x×]?\s*(\d+)",
            normalized_quantity,
        )
        if match is None or int(match.group(1)) != context.approved_quantity:
            raise CheckoutError(CheckoutErrorCode.quantity_mismatch)

    async def _fill_billing(
        self,
        page: PageLike,
        adapter: CheckoutAdapter,
        billing_details: Mapping[str, Any],
        allowed_origins: tuple[str, ...],
        *,
        javascript: bool = False,
    ) -> None:
        address = billing_details.get("address", {})
        if not isinstance(address, Mapping):
            address = {}
        name = billing_details.get("full_name") or billing_details.get("contact_name")
        values = (
            (adapter.name_selector, name),
            (adapter.billing_email_selector, billing_details.get("email")),
            (adapter.billing_phone_selector, billing_details.get("phone")),
            (adapter.billing_country_selector, address.get("country")),
            (adapter.billing_line1_selector, address.get("line1")),
            (adapter.billing_line2_selector, address.get("line2")),
            (adapter.billing_city_selector, address.get("city")),
            (adapter.billing_region_selector, address.get("region")),
            (adapter.billing_postal_code_selector, address.get("postal_code")),
        )
        for selector, value in values:
            if selector is None or value is None:
                continue
            try:
                located = await self._unique_visible(
                    page,
                    selector,
                    allowed_origins,
                    CheckoutErrorCode.payment_form_not_found,
                    timeout_ms=5_000,
                )
            except CheckoutError:
                if adapter.checkout_mode == "stripe_hosted_test":
                    continue
                raise
            try:
                self._validate_located_frame(located, allowed_origins)
                if javascript:
                    handle = await self._resolve_handle(
                        located,
                        allowed_origins,
                        CheckoutErrorCode.payment_form_not_found,
                    )
                    await handle.evaluate(DETERMINISTIC_INPUT_SCRIPT, str(value)[:320])
                elif selector in {
                    adapter.billing_country_selector,
                    adapter.billing_region_selector,
                }:
                    try:
                        await located.locator.select_option(str(value)[:320])
                    except Exception:
                        try:
                            await located.locator.select_option(label=str(value)[:320])
                        except Exception:
                            await located.locator.fill(str(value)[:320])
                    await page.wait_for_timeout(100)
                else:
                    await located.locator.fill(str(value)[:320])
            except Exception:
                raise CheckoutError(CheckoutErrorCode.payment_form_not_found) from None

    async def _resolve_card_fields(
        self,
        page: PageLike,
        adapter: CheckoutAdapter,
        payment_origins: tuple[str, ...],
    ) -> tuple[ResolvedCardField, ...]:
        if adapter.card_number_selector is None or adapter.cvc_selector is None:
            raise CheckoutError(CheckoutErrorCode.adapter_invalid)
        fields: list[tuple[str, str]] = [
            (adapter.card_number_selector, "number"),
            (adapter.cvc_selector, "cvc"),
        ]
        if adapter.expiry_selector:
            fields.append((adapter.expiry_selector, "expiry"))
        else:
            if adapter.expiry_month_selector is None or adapter.expiry_year_selector is None:
                raise CheckoutError(CheckoutErrorCode.adapter_invalid)
            fields.extend(
                (
                    (adapter.expiry_month_selector, "expiry_month"),
                    (adapter.expiry_year_selector, "expiry_year"),
                )
            )
        resolved: list[ResolvedCardField] = []
        for selector, kind in fields:
            located = await self._unique_visible(
                page,
                selector,
                payment_origins,
                CheckoutErrorCode.payment_form_not_found,
                timeout_ms=10_000,
            )
            handle = await self._resolve_handle(
                located,
                payment_origins,
                CheckoutErrorCode.payment_form_not_found,
            )
            resolved.append(ResolvedCardField(handle=handle, frame=located.frame, kind=kind))
        return tuple(resolved)

    @staticmethod
    async def _preflight_card_fields(
        fields: tuple[ResolvedCardField, ...],
        *,
        submit_handle: ElementHandleLike | None = None,
        retryable: bool,
    ) -> None:
        marker = f"__agpay_field_probe_{secrets.token_hex(16)}"
        validated: list[ElementHandleLike] = []
        controls = [(field.handle, field.kind) for field in fields]
        if submit_handle is not None:
            controls.append((submit_handle, "submit"))
        try:
            for handle, kind in controls:
                valid = await handle.evaluate(
                    CARD_FIELD_PREFLIGHT_SCRIPT,
                    {"marker": marker, "kind": kind},
                )
                if valid is not True:
                    raise ValueError
                validated.append(handle)
        except Exception:
            code = (
                CheckoutErrorCode.form_analysis_failed
                if retryable
                else CheckoutErrorCode.payment_form_not_found
            )
            raise CheckoutError(code, retryable=retryable) from None
        finally:
            for handle in validated:
                try:
                    await handle.evaluate(CARD_FIELD_PREFLIGHT_CLEANUP_SCRIPT, marker)
                except Exception:
                    pass

    async def _fill_resolved_card(
        self,
        fields: tuple[ResolvedCardField, ...],
        card: IssuingCardSecret,
        payment_origins: tuple[str, ...],
    ) -> None:
        values = {
            "number": card.number,
            "cvc": card.cvc,
            "expiry": f"{card.expiry_month:02d}/{card.expiry_year % 100:02d}",
            "expiry_month": f"{card.expiry_month:02d}",
            "expiry_year": str(card.expiry_year),
        }
        for field in fields:
            self._validate_frame_origin(field.frame, payment_origins)
            try:
                await field.handle.evaluate(DETERMINISTIC_INPUT_SCRIPT, values[field.kind])
            except Exception:
                raise CheckoutError(CheckoutErrorCode.payment_form_not_found) from None

    @staticmethod
    def _billing_fields_to_map(
        adapter: CheckoutAdapter,
        billing_details: Mapping[str, Any],
    ) -> tuple[str, ...]:
        address = billing_details.get("address")
        if not isinstance(address, Mapping):
            address = {}
        candidates = (
            (
                "name",
                adapter.name_selector,
                billing_details.get("full_name") or billing_details.get("contact_name"),
            ),
            ("billing_email", adapter.billing_email_selector, billing_details.get("email")),
            ("billing_phone", adapter.billing_phone_selector, billing_details.get("phone")),
            ("billing_country", adapter.billing_country_selector, address.get("country")),
            ("billing_line1", adapter.billing_line1_selector, address.get("line1")),
            ("billing_line2", adapter.billing_line2_selector, address.get("line2")),
            ("billing_city", adapter.billing_city_selector, address.get("city")),
            ("billing_region", adapter.billing_region_selector, address.get("region")),
            (
                "billing_postal_code",
                adapter.billing_postal_code_selector,
                address.get("postal_code"),
            ),
        )
        return tuple(name for name, selector, value in candidates if selector is None and value)

    async def _validate_discovered_form(
        self,
        page: PageLike,
        adapter: CheckoutAdapter,
        *,
        merchant_origins: tuple[str, ...],
        payment_origins: tuple[str, ...],
    ) -> None:
        required = (
            (adapter.card_number_selector, "card_number"),
            (adapter.cvc_selector, "cvc"),
        )
        if adapter.expiry_selector is not None:
            required += ((adapter.expiry_selector, "expiry"),)
        else:
            required += (
                (adapter.expiry_month_selector, "expiry_month"),
                (adapter.expiry_year_selector, "expiry_year"),
            )
        for selector, kind in required:
            if selector is None:
                raise CheckoutError(CheckoutErrorCode.form_analysis_failed, retryable=True)
            located = await self._unique_visible(
                page,
                selector,
                payment_origins,
                CheckoutErrorCode.form_analysis_failed,
            )
            await self._validate_payment_field_semantics(located, kind)
        if adapter.submit_selector is None:
            raise CheckoutError(CheckoutErrorCode.form_analysis_failed, retryable=True)
        submit = await self._unique_visible(
            page,
            adapter.submit_selector,
            tuple(dict.fromkeys(merchant_origins + payment_origins)),
            CheckoutErrorCode.form_analysis_failed,
        )
        await self._validate_submit_semantics(submit)

    @staticmethod
    async def _semantic_attributes(
        located: LocatedElement,
    ) -> tuple[str, str, tuple[str, ...]]:
        attributes: dict[str, str] = {}
        try:
            for name in ("autocomplete", "name", "id", "aria-label", "placeholder", "type", "role"):
                value = await located.locator.get_attribute(name)
                if value is not None:
                    attributes[name] = value[:160].casefold()
        except Exception:
            raise CheckoutError(CheckoutErrorCode.form_analysis_failed, retryable=True) from None
        semantic = " ".join(
            attributes.get(name, "")
            for name in ("autocomplete", "name", "id", "aria-label", "placeholder")
        )
        normalized = re.sub(r"[^a-z0-9]+", "", semantic)
        normalized_values = tuple(
            re.sub(r"[^a-z0-9]+", "", attributes.get(name, ""))
            for name in ("autocomplete", "name", "id", "aria-label", "placeholder")
        )
        return (
            normalized,
            attributes.get("type", "") + " " + attributes.get("role", ""),
            normalized_values,
        )

    async def _validate_payment_field_semantics(
        self,
        located: LocatedElement,
        kind: str,
    ) -> None:
        normalized, control_type, normalized_values = await self._semantic_attributes(located)
        expected = {
            "card_number": ("ccnumber", "cardnumber", "creditcardnumber"),
            "cvc": ("cccsc", "cvc", "cvv", "securitycode", "cardcode"),
            "expiry": ("ccexp", "expiry", "expiration", "cardexp"),
            "expiry_month": ("ccexpmonth", "expirymonth", "expmonth"),
            "expiry_year": ("ccexpyear", "expiryyear", "expyear"),
        }[kind]
        matched = any(token in normalized for token in expected)
        if kind == "card_number" and "pan" in normalized_values:
            matched = True
        if not matched:
            raise CheckoutError(CheckoutErrorCode.form_analysis_failed, retryable=True)
        if any(blocked in control_type.split() for blocked in ("hidden", "button", "submit")):
            raise CheckoutError(CheckoutErrorCode.form_analysis_failed, retryable=True)

    async def _validate_submit_semantics(self, located: LocatedElement) -> None:
        normalized, control_type, _ = await self._semantic_attributes(located)
        if not (
            "submit" in control_type.split()
            or "button" in control_type.split()
            or any(
                token in normalized
                for token in ("pay", "submit", "placeorder", "completepurchase", "checkout")
            )
        ):
            raise CheckoutError(CheckoutErrorCode.form_analysis_failed, retryable=True)

    async def _wait_for_result(
        self,
        page: PageLike,
        adapter: CheckoutAdapter,
        allowed_origins: tuple[str, ...],
        payment_origins: tuple[str, ...],
    ) -> BrowserCheckoutResult:
        elapsed = 0
        interval = 250
        while elapsed <= self._result_timeout_ms:
            self._validate_page(page, allowed_origins, payment_origins)
            if adapter.decline_selector and await self._has_visible(
                page,
                adapter.decline_selector,
                allowed_origins,
            ):
                order_reference = await self._extract_order_reference(
                    page, adapter, allowed_origins
                )
                return BrowserCheckoutResult(
                    order_reference,
                    None,
                    AuthorizationOutcome.declined,
                )
            if adapter.action_required_selector and await self._has_visible(
                page,
                adapter.action_required_selector,
                allowed_origins + payment_origins,
            ):
                order_reference = await self._extract_order_reference(
                    page, adapter, allowed_origins
                )
                return BrowserCheckoutResult(
                    order_reference,
                    None,
                    AuthorizationOutcome.action_required,
                )
            if await self._has_visible(page, adapter.success_selector, allowed_origins):
                order_reference = await self._extract_order_reference(
                    page, adapter, allowed_origins
                )
                receipt_url = await self._extract_receipt_url(page, adapter, allowed_origins)
                return BrowserCheckoutResult(order_reference, receipt_url)
            await page.wait_for_timeout(interval)
            elapsed += interval
        raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)

    async def _wait_for_hosted_test_result(
        self,
        page: PageLike,
        context: CheckoutContext,
        merchant_origins: tuple[str, ...],
        result_origins: tuple[str, ...],
        payment_origins: tuple[str, ...],
    ) -> BrowserCheckoutResult:
        """Accept only the landing server's verified Stripe-session marker."""
        if result_origins != ("https://letyouragentspay.com",):
            raise CheckoutError(CheckoutErrorCode.adapter_invalid)
        expected_session_id = urlsplit(context.checkout_url).path.rstrip("/").rsplit("/", 1)[-1]
        if STRIPE_TEST_SESSION_PATTERN.fullmatch(expected_session_id) is None:
            raise CheckoutError(CheckoutErrorCode.adapter_invalid)
        allowed_page_origins = tuple(dict.fromkeys(merchant_origins + result_origins))
        elapsed = 0
        interval = 250
        while elapsed <= self._result_timeout_ms:
            self._validate_page(page, allowed_page_origins, payment_origins)
            try:
                marker = await self._unique_visible(
                    page,
                    context.adapter.success_selector,
                    result_origins,
                    CheckoutErrorCode.payment_outcome_unknown,
                )
            except CheckoutError:
                marker = None
            if marker is not None:
                try:
                    session_id = await marker.locator.get_attribute("data-agpay-stripe-session-id")
                    order_reference = await marker.locator.get_attribute(
                        "data-agpay-order-reference"
                    )
                    offer = await marker.locator.get_attribute("data-agpay-offer")
                    amount_minor = await marker.locator.get_attribute("data-agpay-amount-minor")
                    currency = await marker.locator.get_attribute("data-agpay-currency")
                    receipt_url = validate_receipt_url(
                        page.url,
                        base_url=page.url,
                        allowed_origins=result_origins,
                    )
                    receipt = urlsplit(receipt_url)
                    receipt_session_ids = parse_qs(receipt.query).get("session_id", [])
                except Exception:
                    raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown) from None
                verified = (
                    session_id is not None
                    and STRIPE_TEST_SESSION_PATTERN.fullmatch(session_id) is not None
                    and session_id == expected_session_id
                    and order_reference == session_id
                    and offer is not None
                    and 0 < len(offer) <= 128
                    and amount_minor == str(context.amount_minor)
                    and currency is not None
                    and currency.upper() == context.currency.upper()
                    and receipt.path == "/playground/success"
                    and receipt_session_ids == [session_id]
                )
                if not verified:
                    raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)
                return BrowserCheckoutResult(session_id, receipt_url)
            await page.wait_for_timeout(interval)
            elapsed += interval
        raise CheckoutError(CheckoutErrorCode.payment_outcome_unknown)

    async def _extract_order_reference(
        self,
        page: PageLike,
        adapter: CheckoutAdapter,
        allowed_origins: tuple[str, ...],
    ) -> str | None:
        if adapter.order_reference_selector is None:
            return None
        try:
            located = await self._unique_visible(
                page,
                adapter.order_reference_selector,
                allowed_origins,
                CheckoutErrorCode.checkout_failed,
            )
            value = (await located.locator.inner_text()).strip()
        except Exception:
            return None
        return value if ORDER_REFERENCE_PATTERN.fullmatch(value) else None

    async def _extract_receipt_url(
        self,
        page: PageLike,
        adapter: CheckoutAdapter,
        allowed_origins: tuple[str, ...],
    ) -> str | None:
        if adapter.receipt_url_selector is None:
            return None
        try:
            located = await self._unique_visible(
                page,
                adapter.receipt_url_selector,
                allowed_origins,
                CheckoutErrorCode.checkout_failed,
            )
            href = await located.locator.get_attribute("href")
        except Exception:
            return None
        if not href:
            return None
        try:
            return validate_receipt_url(href, base_url=page.url, allowed_origins=allowed_origins)
        except CheckoutError:
            return None

    async def _unique_visible(
        self,
        page: PageLike,
        selector: str,
        allowed_origins: tuple[str, ...],
        missing_code: CheckoutErrorCode,
        *,
        timeout_ms: int = 0,
    ) -> LocatedElement:
        elapsed = 0
        interval = 100
        allowed = set(allowed_origins)
        while True:
            matches: list[LocatedElement] = []
            for frame in page.frames:
                if not frame.url or frame.url == "about:blank":
                    continue
                if normalize_origin(frame.url) not in allowed:
                    continue
                try:
                    collection = frame.locator(selector)
                    count = await collection.count()
                    for index in range(count):
                        candidate = collection.nth(index)
                        if await candidate.is_visible():
                            matches.append(LocatedElement(locator=candidate, frame=frame))
                except Exception:
                    raise CheckoutError(missing_code) from None
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1 or elapsed >= timeout_ms:
                raise CheckoutError(missing_code)
            await page.wait_for_timeout(interval)
            elapsed += interval

    async def _optional_unique_visible(
        self,
        page: PageLike,
        selector: str,
        allowed_origins: tuple[str, ...],
        invalid_code: CheckoutErrorCode,
        *,
        timeout_ms: int = 0,
    ) -> LocatedElement | None:
        """Allow zero matches, while rejecting selector errors and ambiguity."""
        elapsed = 0
        interval = 100
        allowed = set(allowed_origins)
        while True:
            matches: list[LocatedElement] = []
            for frame in page.frames:
                if not frame.url or frame.url == "about:blank":
                    continue
                if normalize_origin(frame.url) not in allowed:
                    continue
                try:
                    collection = frame.locator(selector)
                    count = await collection.count()
                    for index in range(count):
                        candidate = collection.nth(index)
                        if await candidate.is_visible():
                            matches.append(LocatedElement(locator=candidate, frame=frame))
                except Exception:
                    raise CheckoutError(invalid_code) from None
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise CheckoutError(invalid_code)
            if elapsed >= timeout_ms:
                return None
            await page.wait_for_timeout(interval)
            elapsed += interval

    async def _resolve_handle(
        self,
        located: LocatedElement,
        allowed_origins: tuple[str, ...],
        error_code: CheckoutErrorCode,
    ) -> ElementHandleLike:
        self._validate_located_frame(located, allowed_origins)
        try:
            handle = await located.locator.element_handle()
        except Exception:
            raise CheckoutError(error_code) from None
        self._validate_located_frame(located, allowed_origins)
        if handle is None:
            raise CheckoutError(error_code)
        return handle

    def _validate_located_frame(
        self,
        located: LocatedElement,
        allowed_origins: tuple[str, ...],
    ) -> None:
        self._validate_frame_origin(located.frame, allowed_origins)

    @staticmethod
    def _validate_frame_origin(
        frame: FrameLike,
        allowed_origins: tuple[str, ...],
    ) -> None:
        if not frame.url or frame.url == "about:blank":
            raise CheckoutError(CheckoutErrorCode.origin_blocked)
        if normalize_origin(frame.url) not in set(allowed_origins):
            raise CheckoutError(CheckoutErrorCode.origin_blocked)

    async def _has_visible(
        self,
        page: PageLike,
        selector: str,
        allowed_origins: tuple[str, ...],
    ) -> bool:
        try:
            await self._unique_visible(
                page,
                selector,
                allowed_origins,
                CheckoutErrorCode.checkout_failed,
            )
        except CheckoutError:
            return False
        return True


def money_text_matches(text: str, amount: Decimal, currency: str) -> bool:
    currency = currency.upper()
    uppercase_text = text.upper()
    supported_currency_tokens = {
        token
        for token in re.findall(r"(?<![A-Z0-9])([A-Z]{3})(?![A-Z0-9])", uppercase_text)
        if token in CURRENCY_EXPONENTS
    }
    if supported_currency_tokens != {currency}:
        return False
    if re.search(r"[+\-−]\s*\d", text):
        return False
    decimal_places = CURRENCY_EXPONENTS.get(currency)
    if decimal_places is None:
        return False
    parsed: set[Decimal] = set()
    for match in NUMBER_PATTERN.finditer(text):
        try:
            parsed.add(parse_display_amount(match.group(1), decimal_places=decimal_places))
        except InvalidOperation:
            continue
    return parsed == {amount}


def amount_text_matches(text: str, amount: Decimal, currency: str) -> bool:
    """Match a single localized amount when provider API already binds its currency."""
    if re.search(r"[+\-−]\s*\d", text):
        return False
    decimal_places = CURRENCY_EXPONENTS.get(currency.upper())
    if decimal_places is None:
        return False
    parsed: set[Decimal] = set()
    for match in NUMBER_PATTERN.finditer(text):
        try:
            parsed.add(parse_display_amount(match.group(1), decimal_places=decimal_places))
        except InvalidOperation:
            continue
    return parsed == {amount}


def parse_display_amount(value: str, *, decimal_places: int = 2) -> Decimal:
    compact = value.replace(" ", "")
    if "," in compact and "." in compact:
        decimal_separator = "," if compact.rfind(",") > compact.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        if len(compact.rsplit(decimal_separator, 1)[1]) > decimal_places:
            raise InvalidOperation
        compact = compact.replace(thousands_separator, "").replace(decimal_separator, ".")
    elif compact.count(",") == 1:
        head, tail = compact.split(",")
        compact = f"{head}.{tail}" if 0 < len(tail) <= decimal_places else head + tail
    elif compact.count(".") == 1:
        head, tail = compact.split(".")
        compact = f"{head}.{tail}" if 0 < len(tail) <= decimal_places else head + tail
    else:
        compact = compact.replace(",", "").replace(".", "")
    return Decimal(compact)


async def _connect_playwright(connect_url: str) -> ConnectedBrowser:
    from playwright.async_api import async_playwright

    manager = await async_playwright().start()
    try:
        browser = await manager.chromium.connect_over_cdp(connect_url)
    except Exception:
        await manager.stop()
        raise
    return _PlaywrightBrowser(browser, manager)


class _PlaywrightBrowser:
    def __init__(self, browser: Any, manager: Any) -> None:
        self._browser = browser
        self._manager = manager
        self._contexts: list[Any] = []

    async def new_page(self, permitted_origins: tuple[str, ...]) -> PageLike:
        permitted = set(permitted_origins)

        async def guard(route: RouteLike, request: RequestLike) -> None:
            try:
                permitted_request = normalize_origin(request.url) in permitted
            except CheckoutError:
                permitted_request = False
            if permitted_request:
                await route.continue_()
            else:
                await route.abort()

        async def block_web_socket(web_socket: Any) -> None:
            await web_socket.close(code=1008)

        context = await self._browser.new_context(service_workers="block")
        try:
            await context.route("**/*", guard)
            await context.route_web_socket("**/*", block_web_socket)
            page = await context.new_page()
        except Exception:
            try:
                await context.close()
            except Exception:
                pass
            raise CheckoutError(CheckoutErrorCode.browser_session_failed, retryable=True) from None
        self._contexts.append(context)
        return page

    async def close(self) -> None:
        for context in self._contexts:
            try:
                await context.close()
            except Exception:
                pass
        try:
            await self._browser.close()
        finally:
            await self._manager.stop()
