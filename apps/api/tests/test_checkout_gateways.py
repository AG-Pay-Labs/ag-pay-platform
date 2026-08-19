from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from ag_platform_api.checkout_worker import build_worker
from ag_platform_api.core.config import (
    STRIPE_HOSTED_TEST_ADAPTER_KEY,
    CheckoutAdapterSettings,
    CheckoutRuntimeSettings,
    CheckoutWorkerSettings,
    Settings,
    stripe_hosted_test_adapter,
)
from ag_platform_api.db import session as database_session
from ag_platform_api.services.checkout.browserbase import (
    BrowserbaseGateway,
    amount_text_matches,
    money_text_matches,
)
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.origins import (
    normalize_origin,
    validate_checkout_url,
    validate_stripe_hosted_test_checkout_url,
)
from ag_platform_api.services.checkout.stripe_issuing import StripeIssuingGateway
from ag_platform_api.services.checkout.stripe_payments_demo import StripePaymentsDemoGateway
from ag_platform_api.services.checkout.types import (
    AuthorizationOutcome,
    ExpectedCardMetadata,
    decimal_to_minor,
)


def test_api_settings_never_load_worker_provider_secrets(monkeypatch) -> None:
    monkeypatch.setenv("BROWSERBASE_API_KEY", "browserbase-worker-only")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "project-worker-only")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "stripe-worker-only")
    monkeypatch.setenv("STRIPE_DEMO_SECRET_KEY", "sk_test_demo-worker-only")
    monkeypatch.setenv("STRIPE_LINK_AUTH_DIRECTORY", "/worker-only/link-auth")

    api_settings = Settings(_env_file=None)
    worker_settings = CheckoutWorkerSettings(_env_file=None)

    assert "browserbase_api_key" not in type(api_settings).model_fields
    assert "stripe_secret_key" not in type(api_settings).model_fields
    assert "stripe_demo_secret_key" not in type(api_settings).model_fields
    assert "stripe_link_auth_directory" not in type(api_settings).model_fields
    assert "jwt_secret" not in type(worker_settings).model_fields
    assert "credential_encryption_key" not in type(worker_settings).model_fields
    assert isinstance(database_session.runtime_settings, CheckoutRuntimeSettings)
    assert "jwt_secret" not in type(database_session.runtime_settings).model_fields
    assert "browserbase_api_key" not in type(database_session.runtime_settings).model_fields
    assert "browserbase" not in repr(api_settings).lower()
    assert "stripe-worker-only" not in repr(worker_settings)
    assert "demo-worker-only" not in repr(worker_settings)
    assert worker_settings.browserbase_api_key is not None
    assert worker_settings.stripe_secret_key is not None


def test_local_direct_card_configuration_is_secret_and_development_only() -> None:
    encryption_key = Fernet.generate_key().decode()
    socket_path = Path("/tmp/agpay-config-test/cvc.sock")
    configured = CheckoutRuntimeSettings(
        _env_file=None,
        environment="test",
        checkout_enabled=True,
        local_direct_card_enabled=True,
        direct_card_encryption_key=encryption_key,
        local_direct_card_broker_token="b" * 32,
        local_direct_card_socket_path=socket_path,
    )
    assert configured.local_direct_card_enabled
    assert encryption_key not in repr(configured)

    with pytest.raises(ValidationError, match="development/test-only"):
        CheckoutRuntimeSettings(
            _env_file=None,
            environment="production",
            checkout_enabled=True,
            local_direct_card_enabled=True,
            direct_card_encryption_key=encryption_key,
            local_direct_card_broker_token="b" * 32,
            local_direct_card_socket_path=socket_path,
        )
    with pytest.raises(ValidationError, match="valid Fernet key"):
        CheckoutRuntimeSettings(
            _env_file=None,
            environment="test",
            checkout_enabled=True,
            local_direct_card_enabled=True,
            direct_card_encryption_key="not-a-key",
            local_direct_card_broker_token="b" * 32,
            local_direct_card_socket_path=socket_path,
        )


def test_ai_mapped_adapter_forbids_preconfigured_payment_controls() -> None:
    base = {
        "allowed_origins": ["https://merchant.example.test"],
        "payment_origins": ["https://payments.example.test"],
        "product_title_selector": "#title",
        "quantity_selector": "#quantity",
        "total_selector": "#total",
        "success_selector": "#success",
        "payment_form_strategy": "browserbase_ai",
        "order_reference_selector": "#order",
    }
    adapter = CheckoutAdapterSettings.model_validate(base)
    assert adapter.card_number_selector is None

    with pytest.raises(ValidationError, match="discovers payment and submit selectors"):
        CheckoutAdapterSettings.model_validate({**base, "card_number_selector": "#card-number"})


def test_stripe_link_configuration_is_pinned_and_development_test_only(tmp_path) -> None:
    auth_directory = tmp_path / "link-auth"
    auth_directory.mkdir(mode=0o700)
    configured = CheckoutWorkerSettings(
        _env_file=None,
        environment="test",
        stripe_link_enabled=True,
        stripe_link_test_mode=True,
        stripe_link_auth_directory=auth_directory,
    )
    assert configured.stripe_link_cli_version == "0.12.0"

    with pytest.raises(ValidationError, match="requires STRIPE_LINK_TEST_MODE"):
        CheckoutRuntimeSettings(
            _env_file=None,
            environment="test",
            stripe_link_enabled=True,
        )
    with pytest.raises(ValidationError, match="development/test-only"):
        CheckoutRuntimeSettings(
            _env_file=None,
            environment="production",
            stripe_link_enabled=True,
            stripe_link_test_mode=True,
        )
    with pytest.raises(ValidationError):
        CheckoutWorkerSettings(
            _env_file=None,
            environment="test",
            stripe_link_enabled=True,
            stripe_link_test_mode=True,
            stripe_link_auth_directory=auth_directory,
            stripe_link_cli_version="0.13.0",
        )


def test_stripe_hosted_adapter_is_pinned_and_development_test_only() -> None:
    runtime = CheckoutRuntimeSettings(
        _env_file=None,
        environment="test",
        checkout_demo_enabled=True,
        checkout_hosted_demo_enabled=True,
    )

    assert runtime.checkout_adapters == {
        STRIPE_HOSTED_TEST_ADAPTER_KEY: stripe_hosted_test_adapter()
    }
    assert runtime.checkout_adapters[STRIPE_HOSTED_TEST_ADAPTER_KEY].checkout_mode == (
        "stripe_hosted_test"
    )
    assert runtime.checkout_adapters[STRIPE_HOSTED_TEST_ADAPTER_KEY].result_origins == [
        "https://letyouragentspay.com"
    ]
    assert runtime.checkout_adapters[STRIPE_HOSTED_TEST_ADAPTER_KEY].success_selector == (
        '#agpay-payment-verification[data-agpay-payment-status="verified"]'
    )

    keyless_worker = CheckoutWorkerSettings(
        _env_file=None,
        environment="test",
        checkout_enabled=True,
        checkout_demo_enabled=True,
        checkout_hosted_demo_enabled=True,
        stripe_demo_secret_key="   ",
    )
    assert keyless_worker.stripe_demo_secret_key is None

    with pytest.raises(ValidationError, match="test-mode secret key"):
        CheckoutWorkerSettings(
            _env_file=None,
            environment="test",
            checkout_enabled=True,
            checkout_demo_enabled=True,
            checkout_hosted_demo_enabled=True,
            stripe_demo_secret_key="sk_live_not-allowed",
        )

    with pytest.raises(ValidationError, match="legacy direct demo rail"):
        CheckoutWorkerSettings(
            _env_file=None,
            environment="test",
            checkout_enabled=True,
            checkout_demo_enabled=True,
        )

    with pytest.raises(ValidationError, match="requires CHECKOUT_DEMO_ENABLED"):
        CheckoutRuntimeSettings(
            _env_file=None,
            environment="test",
            checkout_hosted_demo_enabled=True,
        )
    with pytest.raises(ValidationError, match="development/test-only"):
        CheckoutRuntimeSettings(
            _env_file=None,
            environment="production",
            checkout_demo_enabled=True,
            checkout_hosted_demo_enabled=True,
        )
    with pytest.raises(ValidationError, match="cannot be overridden"):
        CheckoutRuntimeSettings(
            _env_file=None,
            environment="test",
            checkout_demo_enabled=True,
            checkout_hosted_demo_enabled=True,
            checkout_adapters={
                STRIPE_HOSTED_TEST_ADAPTER_KEY: stripe_hosted_test_adapter().model_copy(
                    update={"submit_selector": "#untrusted-submit"}
                )
            },
        )


async def test_keyless_hosted_worker_builds_without_stripe_gateway() -> None:
    settings = CheckoutWorkerSettings(
        _env_file=None,
        environment="test",
        checkout_enabled=True,
        checkout_demo_enabled=True,
        checkout_hosted_demo_enabled=True,
        browserbase_api_key="browserbase-test-key",
        browserbase_project_id="browserbase-project",
        stripe_demo_secret_key="",
    )

    _, browserbase, issuing, demo = build_worker(settings, object())  # type: ignore[arg-type]
    try:
        assert issuing is None
        assert demo is None
    finally:
        await browserbase.close()


async def test_browserbase_session_payload_honors_observation_and_limits_domains() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.update(request.read() and __import__("json").loads(request.content))
        assert request.headers["X-BB-API-Key"] == "browserbase-test-key"
        return httpx.Response(
            201,
            json={
                "id": "session_12345",
                "connectUrl": "wss://connect.example.test/session?secret=do-not-render",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = BrowserbaseGateway(
            api_key="browserbase-test-key",
            project_id="project-test",
            region="eu-central-1",
            client=client,
        )
        session = await gateway.create_session(
            ("https://merchant.example.test", "https://pay.example.test"),
            record_session=True,
            log_session=True,
        )

    assert captured == {
        "projectId": "project-test",
        "region": "eu-central-1",
        "keepAlive": False,
        "timeout": 120,
        "browserSettings": {
            "recordSession": True,
            "logSession": True,
            "solveCaptchas": False,
            "allowedDomains": ["merchant.example.test", "pay.example.test"],
        },
    }
    assert session.session_id == "session_12345"
    assert "do-not-render" not in repr(session)


async def test_browserbase_session_release_uses_only_session_id_and_project() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"status": "COMPLETED"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = BrowserbaseGateway(
            api_key="browserbase-test-key",
            project_id="project-test",
            client=client,
        )
        released = await gateway.release_session("session_12345")

    assert released
    assert captured == {
        "url": "https://api.browserbase.com/v1/sessions/session_12345",
        "body": {"status": "REQUEST_RELEASE"},
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://merchant.example.test/checkout",
        "https://localhost/checkout",
        "https://127.0.0.1/checkout",
        "https://10.0.0.1/checkout",
        "https://[::1]/checkout",
        "https://user:password@merchant.example.test/checkout",
    ],
)
def test_checkout_origins_reject_non_https_private_and_credentialed_urls(url: str) -> None:
    with pytest.raises(CheckoutError) as caught:
        validate_checkout_url(url, ("https://merchant.example.test",))
    assert caught.value.code == CheckoutErrorCode.origin_blocked


def test_hosted_test_checkout_url_requires_and_preserves_concrete_test_session() -> None:
    fixed_url = "https://checkout.stripe.com/c/pay/cs_test_fixed123#preserved-fragment"
    assert validate_stripe_hosted_test_checkout_url(fixed_url) == fixed_url

    for invalid_url in (
        "https://checkout.stripe.com/",
        "https://checkout.stripe.com/c/pay/cs_live_fixed123#fragment",
        "https://checkout.stripe.com/c/pay/cs_test_fixed123?prefilled_email=not-approved",
    ):
        with pytest.raises(CheckoutError) as caught:
            validate_stripe_hosted_test_checkout_url(invalid_url)
        assert caught.value.code == CheckoutErrorCode.origin_blocked


def test_origin_normalization_and_total_matching_are_exact() -> None:
    assert normalize_origin("https://Merchant.Example.Test:443/path") == (
        "https://merchant.example.test"
    )
    assert money_text_matches("Total: EUR 1.234,56", Decimal("1234.56"), "EUR")
    assert money_text_matches("Total: BHD 25.120", Decimal("25.12"), "BHD")
    assert not money_text_matches("Subtotal EUR 24.99", Decimal("25.00"), "EUR")
    assert not money_text_matches("Total USD 25.00", Decimal("25.00"), "EUR")
    assert not money_text_matches("Total CAD CA$25.00", Decimal("25.00"), "USD")
    assert not money_text_matches("Total CNY ¥25", Decimal("25"), "JPY")
    assert not money_text_matches("Total USD -25.00", Decimal("25.00"), "USD")
    assert not money_text_matches("Total CAD / USD 25.00", Decimal("25.00"), "USD")


@pytest.mark.parametrize(
    ("text", "amount", "currency", "expected"),
    [
        ("25,00 €", Decimal("25.00"), "EUR", True),
        ("1.234,56 €", Decimal("1234.56"), "EUR", True),
        ("¥2,500", Decimal("2500"), "JPY", True),
        ("-25,00 €", Decimal("25.00"), "EUR", False),
        ("Subtotal 20,00 € Total 25,00 €", Decimal("25.00"), "EUR", False),
        ("25,00 €", Decimal("25.00"), "ZZZ", False),
    ],
)
def test_hosted_amount_matching_accepts_one_localized_provider_bound_total(
    text: str,
    amount: Decimal,
    currency: str,
    expected: bool,
) -> None:
    assert amount_text_matches(text, amount, currency) is expected


def test_minor_amount_conversion_supports_zero_two_and_three_decimal_currencies() -> None:
    assert decimal_to_minor(Decimal("25"), "JPY") == 25
    assert decimal_to_minor(Decimal("25.12"), "EUR") == 2512
    assert decimal_to_minor(Decimal("25.123"), "BHD") == 25123
    with pytest.raises(CheckoutError) as unsupported:
        decimal_to_minor(Decimal("25.00"), "ZZZ")
    assert unsupported.value.code == CheckoutErrorCode.currency_unsupported
    with pytest.raises(CheckoutError) as fractional_zero_decimal:
        decimal_to_minor(Decimal("25.50"), "JPY")
    assert fractional_zero_decimal.value.code == CheckoutErrorCode.currency_precision_invalid


async def test_stripe_card_secret_is_validated_and_repr_hidden() -> None:
    owner_id = uuid4()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get_list("expand[]") == ["number", "cvc"]
        assert request.headers["Authorization"] == "Bearer stripe-test-secret"
        return httpx.Response(
            200,
            json={
                "id": "ic_card123",
                "status": "active",
                "type": "virtual",
                "brand": "Visa",
                "last4": "4242",
                "exp_month": 12,
                "exp_year": 2030,
                "number": "4242424242424242",
                "cvc": "123",
                "metadata": {"agpay_owner_id": str(owner_id)},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripeIssuingGateway(secret_key="stripe-test-secret", client=client)
        secret = await gateway.retrieve_card(
            "ic_card123",
            ExpectedCardMetadata(owner_id, "4242", "visa", 12, 2030),
        )

    rendered = repr(secret)
    assert "4242424242424242" not in rendered
    assert "123" not in rendered
    assert "redacted" in rendered


async def test_stripe_card_requires_trusted_issuer_side_owner_binding() -> None:
    owner_id = uuid4()

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "ic_card123",
                "status": "active",
                "type": "virtual",
                "brand": "Visa",
                "last4": "4242",
                "exp_month": 12,
                "exp_year": 2030,
                "number": "4242424242424242",
                "cvc": "123",
                "metadata": {"agpay_owner_id": str(uuid4())},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripeIssuingGateway(secret_key="stripe-test-secret", client=client)
        with pytest.raises(CheckoutError) as caught:
            await gateway.retrieve_card(
                "ic_card123",
                ExpectedCardMetadata(owner_id, "4242", "visa", 12, 2030),
            )

    assert caught.value.code == CheckoutErrorCode.card_unavailable


async def test_stripe_authorization_requires_one_exact_card_amount_and_currency() -> None:
    responses = [
        {
            "data": [
                {
                    "id": "iauth_wrong_amount",
                    "approved": True,
                    "amount": 2499,
                    "currency": "eur",
                    "card": "ic_card123",
                },
                {
                    "id": "iauth_exact",
                    "approved": True,
                    "amount": 2750,
                    "currency": "usd",
                    "merchant_amount": 2500,
                    "merchant_currency": "eur",
                    "card": {"id": "ic_card123"},
                },
            ]
        },
        {
            "data": [
                {
                    "id": "iauth_exact_a",
                    "approved": True,
                    "amount": 2500,
                    "currency": "eur",
                    "card": "ic_card123",
                },
                {
                    "id": "iauth_exact_b",
                    "approved": True,
                    "amount": 2500,
                    "currency": "eur",
                    "card": "ic_card123",
                },
            ]
        },
        {
            "data": [
                {
                    "id": "iauth_declined",
                    "approved": False,
                    "amount": 2500,
                    "currency": "eur",
                    "card": "ic_card123",
                }
            ]
        },
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["card"] == "ic_card123"
        assert request.url.params["created[gte]"] == "1893456000"
        return httpx.Response(200, json=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripeIssuingGateway(secret_key="stripe-test-secret", client=client)
        arguments = {
            "card_id": "ic_card123",
            "created_at_or_after": datetime(2030, 1, 1, tzinfo=UTC),
            "amount_minor": 2500,
            "currency": "EUR",
        }
        approved = await gateway.find_authorization(**arguments)
        ambiguous = await gateway.find_authorization(**arguments)
        declined = await gateway.find_authorization(**arguments)

    assert approved.outcome == AuthorizationOutcome.approved
    assert approved.provider_reference == "iauth_exact"
    assert ambiguous.outcome == AuthorizationOutcome.unknown
    assert declined.outcome == AuthorizationOutcome.declined


@pytest.mark.parametrize(
    "merchant_fields",
    [
        {"merchant_amount": 3000, "merchant_currency": "usd"},
        {"merchant_amount": 2500},
        {"merchant_currency": "eur"},
        {"merchant_amount": "2500", "merchant_currency": "eur"},
        {"merchant_amount": 2500, "merchant_currency": None},
    ],
)
async def test_stripe_authorization_prioritizes_complete_merchant_presentment(
    merchant_fields: dict[str, object],
) -> None:
    record: dict[str, object] = {
        "id": "iauth_cardholder_exact",
        "approved": True,
        "amount": 2500,
        "currency": "eur",
        "card": "ic_card123",
    }
    record.update(merchant_fields)

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [record]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripeIssuingGateway(secret_key="stripe-test-secret", client=client)
        result = await gateway.find_authorization(
            card_id="ic_card123",
            created_at_or_after=datetime(2030, 1, 1, tzinfo=UTC),
            amount_minor=2500,
            currency="EUR",
        )

    assert result.outcome == AuthorizationOutcome.unknown


async def test_provider_error_does_not_expose_raw_response() -> None:
    owner_id = uuid4()

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            text="provider failure card=4242424242424242 cvc=123",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripeIssuingGateway(secret_key="stripe-test-secret", client=client)
        with pytest.raises(CheckoutError) as caught:
            await gateway.retrieve_card(
                "ic_card123",
                ExpectedCardMetadata(owner_id, "4242", "visa", 12, 2030),
            )

    assert caught.value.code == CheckoutErrorCode.card_unavailable
    assert "4242424242424242" not in str(caught.value)
    assert "123" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "last_error", "expected"),
    [
        ("succeeded", None, AuthorizationOutcome.approved),
        ("requires_payment_method", {"code": "card_declined"}, AuthorizationOutcome.declined),
        ("requires_action", None, AuthorizationOutcome.action_required),
        ("processing", None, AuthorizationOutcome.unknown),
    ],
)
async def test_stripe_payments_demo_verifies_exact_intent(
    status: str,
    last_error: object,
    expected: AuthorizationOutcome,
) -> None:
    execution_id = uuid4()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer sk_test_demo"
        return httpx.Response(
            200,
            json={
                "id": "pi_demo123",
                "livemode": False,
                "amount": 2500,
                "currency": "eur",
                "metadata": {"agpay_execution_id": str(execution_id)},
                "status": status,
                "last_payment_error": last_error,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        result = await gateway.verify_payment_intent(
            payment_intent_id="pi_demo123",
            execution_id=execution_id,
            amount_minor=2500,
            currency="EUR",
        )

    assert result.outcome == expected
    assert result.provider_reference == (
        "pi_demo123" if expected == AuthorizationOutcome.approved else None
    )


async def test_stripe_payments_demo_rejects_wrong_execution_binding() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "pi_demo123",
                "livemode": False,
                "amount": 2500,
                "currency": "eur",
                "metadata": {"agpay_execution_id": str(uuid4())},
                "status": "requires_payment_method",
                "last_payment_error": {"code": "card_declined"},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        result = await gateway.verify_payment_intent(
            payment_intent_id="pi_demo123",
            execution_id=uuid4(),
            amount_minor=2500,
            currency="EUR",
        )

    assert result.outcome == AuthorizationOutcome.unknown


async def test_stripe_hosted_session_creation_is_exact_idempotent_and_bound() -> None:
    execution_id = uuid4()
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["authorization"] = request.headers["Authorization"]
        captured["idempotency_key"] = request.headers["Idempotency-Key"]
        captured["content_type"] = request.headers["Content-Type"]
        captured["form"] = parse_qsl(request.content.decode(), keep_blank_values=True)
        return httpx.Response(
            200,
            json={
                "id": "cs_test_session123",
                "url": "https://checkout.stripe.com/c/pay/cs_test_session123#fixture",
                "livemode": False,
                "amount_total": 5000,
                "currency": "eur",
                "client_reference_id": str(execution_id),
                "metadata": {"agpay_execution_id": str(execution_id)},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        hosted = await gateway.create_checkout_session(
            execution_id=execution_id,
            title="  AG   Pay\nConcept  ",
            quantity=2,
            unit_amount_minor=2500,
            currency="EUR",
            collect_phone=True,
        )

    assert hosted.session_id == "cs_test_session123"
    assert hosted.checkout_url == ("https://checkout.stripe.com/c/pay/cs_test_session123#fixture")
    assert captured == {
        "method": "POST",
        "path": "/v1/checkout/sessions",
        "authorization": "Bearer sk_test_demo",
        "idempotency_key": f"agpay-hosted-{execution_id}",
        "content_type": "application/x-www-form-urlencoded",
        "form": [
            ("mode", "payment"),
            ("payment_method_types[]", "card"),
            ("client_reference_id", str(execution_id)),
            ("metadata[agpay_execution_id]", str(execution_id)),
            ("payment_intent_data[metadata][agpay_execution_id]", str(execution_id)),
            ("line_items[0][price_data][currency]", "eur"),
            ("line_items[0][price_data][unit_amount]", "2500"),
            ("line_items[0][price_data][product_data][name]", "AG Pay Concept"),
            ("line_items[0][quantity]", "2"),
            ("billing_address_collection", "required"),
            ("submit_type", "pay"),
            (
                "success_url",
                "https://example.com/?agpay_checkout=complete&session_id={CHECKOUT_SESSION_ID}",
            ),
            ("cancel_url", "https://example.com/?agpay_checkout=cancelled"),
            ("phone_number_collection[enabled]", "true"),
        ],
    }


async def test_stripe_hosted_session_rejects_a_title_it_cannot_show_exactly() -> None:
    called = False

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        with pytest.raises(CheckoutError) as caught:
            await gateway.create_checkout_session(
                execution_id=uuid4(),
                title="x" * 128,
                quantity=1,
                unit_amount_minor=2500,
                currency="EUR",
                collect_phone=False,
            )

    assert caught.value.code == CheckoutErrorCode.execution_invalid
    assert called is False


@pytest.mark.parametrize(
    "response_override",
    [
        {"id": "cs_live_not_allowed"},
        {"livemode": True},
        {"amount_total": 2499},
        {"currency": "usd"},
        {"client_reference_id": str(uuid4())},
        {"metadata": {"agpay_execution_id": str(uuid4())}},
    ],
)
async def test_stripe_hosted_session_creation_rejects_wrong_response_binding(
    response_override: dict[str, object],
) -> None:
    execution_id = uuid4()
    payload: dict[str, object] = {
        "id": "cs_test_session123",
        "url": "https://checkout.stripe.com/c/pay/cs_test_session123#fixture",
        "livemode": False,
        "amount_total": 2500,
        "currency": "eur",
        "client_reference_id": str(execution_id),
        "metadata": {"agpay_execution_id": str(execution_id)},
    }
    payload.update(response_override)

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        with pytest.raises(CheckoutError) as caught:
            await gateway.create_checkout_session(
                execution_id=execution_id,
                title="AG Pay Concept",
                quantity=1,
                unit_amount_minor=2500,
                currency="EUR",
                collect_phone=False,
            )

    assert caught.value.code == CheckoutErrorCode.browser_navigation_failed
    assert not caught.value.retryable


@pytest.mark.parametrize(
    "checkout_url",
    [
        "http://checkout.stripe.com/c/pay/cs_test_session123",
        "https://checkout.stripe.com.evil.example/c/pay/cs_test_session123",
        "https://buyer:secret@checkout.stripe.com/c/pay/cs_test_session123",
        "https://checkout.stripe.com:444/c/pay/cs_test_session123",
    ],
)
async def test_stripe_hosted_session_creation_rejects_untrusted_checkout_url(
    checkout_url: str,
) -> None:
    execution_id = uuid4()

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "cs_test_session123",
                "url": checkout_url,
                "livemode": False,
                "amount_total": 2500,
                "currency": "eur",
                "client_reference_id": str(execution_id),
                "metadata": {"agpay_execution_id": str(execution_id)},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        with pytest.raises(CheckoutError) as caught:
            await gateway.create_checkout_session(
                execution_id=execution_id,
                title="AG Pay Concept",
                quantity=1,
                unit_amount_minor=2500,
                currency="EUR",
                collect_phone=False,
            )

    assert caught.value.code == CheckoutErrorCode.browser_navigation_failed
    assert not caught.value.retryable
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "last_error", "expected"),
    [
        ("succeeded", None, AuthorizationOutcome.approved),
        ("requires_payment_method", {"code": "card_declined"}, AuthorizationOutcome.declined),
        ("requires_action", None, AuthorizationOutcome.action_required),
        ("processing", None, AuthorizationOutcome.unknown),
    ],
)
async def test_stripe_hosted_verification_requires_exact_session_and_intent_binding(
    status: str,
    last_error: object,
    expected: AuthorizationOutcome,
) -> None:
    execution_id = uuid4()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/checkout/sessions/cs_test_session123"
        assert request.headers["Authorization"] == "Bearer sk_test_demo"
        assert request.url.params.get_list("expand[]") == [
            "payment_intent",
            "payment_intent.latest_charge",
        ]
        return httpx.Response(
            200,
            json={
                "id": "cs_test_session123",
                "livemode": False,
                "amount_total": 5000,
                "currency": "eur",
                "client_reference_id": str(execution_id),
                "metadata": {"agpay_execution_id": str(execution_id)},
                "payment_intent": {
                    "id": "pi_hosted123",
                    "livemode": False,
                    "amount": 5000,
                    "currency": "eur",
                    "metadata": {"agpay_execution_id": str(execution_id)},
                    "status": status,
                    "last_payment_error": last_error,
                    "latest_charge": {
                        "receipt_url": "https://pay.stripe.com/receipts/payment/hosted123"
                    },
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        verified = await gateway.verify_checkout_session(
            session_id="cs_test_session123",
            execution_id=execution_id,
            amount_minor=5000,
            currency="EUR",
        )

    assert verified.outcome == expected
    assert verified.provider_reference == (
        "pi_hosted123" if expected == AuthorizationOutcome.approved else None
    )
    assert verified.receipt_url == (
        "https://pay.stripe.com/receipts/payment/hosted123"
        if expected == AuthorizationOutcome.approved
        else None
    )


async def test_stripe_hosted_verification_returns_unknown_for_wrong_session_binding() -> None:
    execution_id = uuid4()

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "cs_test_session123",
                "livemode": False,
                "amount_total": 5000,
                "currency": "eur",
                "client_reference_id": str(execution_id),
                "metadata": {"agpay_execution_id": str(uuid4())},
                "payment_intent": {
                    "id": "pi_hosted123",
                    "livemode": False,
                    "amount": 5000,
                    "currency": "eur",
                    "metadata": {"agpay_execution_id": str(execution_id)},
                    "status": "succeeded",
                    "latest_charge": {
                        "receipt_url": "https://pay.stripe.com/receipts/payment/hosted123"
                    },
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        verified = await gateway.verify_checkout_session(
            session_id="cs_test_session123",
            execution_id=execution_id,
            amount_minor=5000,
            currency="EUR",
        )

    assert verified.outcome == AuthorizationOutcome.unknown
    assert verified.provider_reference is None
    assert verified.receipt_url is None


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({}, True),
        ({"id": "cs_test_other"}, False),
        ({"livemode": True}, False),
        ({"status": "open"}, False),
        ({"payment_status": "paid"}, False),
        ({"client_reference_id": str(uuid4())}, False),
        ({"metadata": {"agpay_execution_id": str(uuid4())}}, False),
    ],
)
async def test_stripe_hosted_expiration_requires_exact_unpaid_binding(
    override: dict[str, object],
    expected: bool,
) -> None:
    execution_id = uuid4()
    payload: dict[str, object] = {
        "id": "cs_test_session123",
        "livemode": False,
        "status": "expired",
        "payment_status": "unpaid",
        "client_reference_id": str(execution_id),
        "metadata": {"agpay_execution_id": str(execution_id)},
    }
    payload.update(override)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == ("/v1/checkout/sessions/cs_test_session123/expire")
        assert request.headers["Authorization"] == "Bearer sk_test_demo"
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        expired = await gateway.expire_checkout_session(
            "cs_test_session123",
            execution_id,
        )

    assert expired is expected


@pytest.mark.parametrize(
    ("receipt_url", "expected"),
    [
        (
            "https://pay.stripe.com/receipts/payment/hosted123?source=test",
            "https://pay.stripe.com/receipts/payment/hosted123?source=test",
        ),
        ("http://pay.stripe.com/receipts/payment/hosted123", None),
        ("https://pay.stripe.com.evil.example/receipts/payment/hosted123", None),
        ("https://buyer:secret@pay.stripe.com/receipts/payment/hosted123", None),
        ("https://pay.stripe.com:444/receipts/payment/hosted123", None),
        (None, None),
    ],
)
async def test_stripe_hosted_receipt_url_is_sanitized(
    receipt_url: object,
    expected: str | None,
) -> None:
    execution_id = uuid4()

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "cs_test_session123",
                "livemode": False,
                "amount_total": 2500,
                "currency": "eur",
                "client_reference_id": str(execution_id),
                "metadata": {"agpay_execution_id": str(execution_id)},
                "payment_intent": {
                    "id": "pi_hosted123",
                    "livemode": False,
                    "amount": 2500,
                    "currency": "eur",
                    "metadata": {"agpay_execution_id": str(execution_id)},
                    "status": "succeeded",
                    "latest_charge": {"receipt_url": receipt_url},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = StripePaymentsDemoGateway(secret_key="sk_test_demo", client=client)
        verified = await gateway.verify_checkout_session(
            session_id="cs_test_session123",
            execution_id=execution_id,
            amount_minor=2500,
            currency="EUR",
        )

    assert verified.outcome == AuthorizationOutcome.approved
    assert verified.receipt_url == expected
