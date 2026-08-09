from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from ag_platform_api.core.config import (
    CheckoutRuntimeSettings,
    CheckoutWorkerSettings,
    Settings,
)
from ag_platform_api.db import session as database_session
from ag_platform_api.services.checkout.browserbase import (
    BrowserbaseGateway,
    money_text_matches,
)
from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode
from ag_platform_api.services.checkout.origins import normalize_origin, validate_checkout_url
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

    api_settings = Settings(_env_file=None)
    worker_settings = CheckoutWorkerSettings(_env_file=None)

    assert "browserbase_api_key" not in type(api_settings).model_fields
    assert "stripe_secret_key" not in type(api_settings).model_fields
    assert "stripe_demo_secret_key" not in type(api_settings).model_fields
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


async def test_browserbase_session_payload_disables_observation_and_limits_domains() -> None:
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
            ("https://merchant.example.test", "https://pay.example.test")
        )

    assert captured == {
        "projectId": "project-test",
        "region": "eu-central-1",
        "keepAlive": False,
        "timeout": 120,
        "browserSettings": {
            "recordSession": False,
            "logSession": False,
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
