import re
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

CheckoutSelector = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]
CHECKOUT_ADAPTER_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
STRIPE_HOSTED_TEST_ADAPTER_KEY = "stripe-hosted"
STRIPE_HOSTED_TEST_BOOTSTRAP_URL = "https://checkout.stripe.com/"


def normalize_checkout_origin(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() != "https" or parsed.hostname is None:
        raise ValueError("Checkout origins must be absolute HTTPS origins")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Checkout origins cannot contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ValueError("Checkout origins cannot contain a path")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Checkout origin port is invalid") from exc
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}{f':{port}' if port and port != 443 else ''}"


class CheckoutAdapterSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_origins: list[str] = Field(min_length=1, max_length=100)
    payment_origins: list[str] = Field(min_length=1, max_length=100)
    resource_origins: list[str] = Field(default_factory=list, max_length=100)
    result_origins: list[str] = Field(default_factory=list, max_length=10)
    checkout_mode: Literal["direct", "stripe_hosted_test"] = "direct"
    product_title_selector: CheckoutSelector
    quantity_selector: CheckoutSelector
    total_selector: CheckoutSelector
    card_number_selector: CheckoutSelector
    expiry_selector: CheckoutSelector | None = None
    expiry_month_selector: CheckoutSelector | None = None
    expiry_year_selector: CheckoutSelector | None = None
    cvc_selector: CheckoutSelector
    submit_selector: CheckoutSelector
    success_selector: CheckoutSelector
    decline_selector: CheckoutSelector | None = None
    name_selector: CheckoutSelector | None = None
    billing_line1_selector: CheckoutSelector | None = None
    billing_line2_selector: CheckoutSelector | None = None
    billing_city_selector: CheckoutSelector | None = None
    billing_region_selector: CheckoutSelector | None = None
    billing_postal_code_selector: CheckoutSelector | None = None
    billing_country_selector: CheckoutSelector | None = None
    billing_email_selector: CheckoutSelector | None = None
    billing_phone_selector: CheckoutSelector | None = None
    action_required_selector: CheckoutSelector | None = None
    order_reference_selector: CheckoutSelector | None = None
    receipt_url_selector: CheckoutSelector | None = None

    @field_validator(
        "allowed_origins",
        "payment_origins",
        "resource_origins",
        "result_origins",
    )
    @classmethod
    def validate_origins(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_checkout_origin(value) for value in values))

    @model_validator(mode="after")
    def validate_selectors(self) -> "CheckoutAdapterSettings":
        combined_expiry = self.expiry_selector is not None
        split_expiry = (
            self.expiry_month_selector is not None or self.expiry_year_selector is not None
        )
        if combined_expiry == split_expiry:
            raise ValueError("Configure either expiry_selector or expiry month and year selectors")
        if split_expiry and (
            self.expiry_month_selector is None or self.expiry_year_selector is None
        ):
            raise ValueError("Both expiry_month_selector and expiry_year_selector are required")
        for field_name in type(self).model_fields:
            if not field_name.endswith("_selector"):
                continue
            value = getattr(self, field_name)
            if value is not None and any(ord(character) < 32 for character in value):
                raise ValueError(f"{field_name} cannot contain control characters")
        if not set(self.result_origins).issubset(self.allowed_origins):
            raise ValueError("result_origins must also be listed in allowed_origins")
        return self


def stripe_hosted_test_adapter() -> CheckoutAdapterSettings:
    """Pinned, development-only adapter for Stripe's live hosted test checkout."""
    return CheckoutAdapterSettings(
        checkout_mode="stripe_hosted_test",
        allowed_origins=["https://checkout.stripe.com", "https://example.com"],
        result_origins=["https://example.com"],
        payment_origins=[
            "https://checkout.stripe.com",
            "https://js.stripe.com",
            "https://b.stripecdn.com",
            "https://newassets.hcaptcha.com",
            "https://m.stripe.network",
        ],
        resource_origins=[
            "https://api.stripe.com",
            "https://checkout-cookies.stripe.com",
            "https://merchant-ui-api.stripe.com",
            "https://m.stripe.com",
            "https://r.stripe.com",
            "https://hcaptcha.com",
            "https://api.hcaptcha.com",
            "https://maps.googleapis.com",
            "https://applepay.cdn-apple.com",
            "https://smp-paymentservices.apple.com",
        ],
        product_title_selector='[data-testid="product-summary-name"]',
        quantity_selector='[data-testid="product-summary-description"]',
        total_selector='[data-testid="product-summary-total-amount"]',
        billing_email_selector='input[autocomplete="email"]',
        card_number_selector='input[autocomplete="cc-number"]',
        expiry_selector='input[autocomplete="cc-exp"]',
        cvc_selector='input[autocomplete="cc-csc"]',
        name_selector='input[autocomplete="cc-name"]',
        billing_phone_selector='input[autocomplete="tel"]',
        billing_country_selector='select[autocomplete="billing country"]',
        billing_line1_selector="#billingAddressLine1",
        billing_line2_selector="#billingAddressLine2",
        billing_city_selector="#billingLocality",
        billing_region_selector="#billingAdministrativeArea",
        billing_postal_code_selector="#billingPostalCode",
        submit_selector='[data-testid="hosted-payment-submit-button"]',
        # Hosted mode observes the provider API; this selector is never authoritative.
        success_selector="body",
    )


class CheckoutRuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://agpay:agpay_postgres_dev@localhost:5432/agpay"
    redis_url: str = "redis://:agpay_redis_dev@localhost:6379/0"
    environment: str = "development"
    checkout_enabled: bool = False
    checkout_demo_enabled: bool = False
    checkout_hosted_demo_enabled: bool = False
    checkout_demo_adapter_key: str = "stripe-demo"
    checkout_adapters: dict[str, CheckoutAdapterSettings] = Field(default_factory=dict)
    checkout_worker_poll_seconds: float = Field(default=1.0, gt=0)
    checkout_lease_seconds: int = Field(default=120, ge=30, le=3600)
    checkout_max_attempts: int = Field(default=3, ge=1, le=20)
    checkout_result_timeout_seconds: int = Field(default=60, ge=5, le=900)
    checkout_authorization_timeout_seconds: int = Field(default=30, ge=1, le=300)
    checkout_authorization_poll_seconds: float = Field(default=1.0, gt=0, le=30)
    checkout_demo_observation_seconds: float = Field(default=30.0, ge=0, le=30)

    @field_validator("checkout_adapters")
    @classmethod
    def validate_checkout_adapter_keys(
        cls, value: dict[str, CheckoutAdapterSettings]
    ) -> dict[str, CheckoutAdapterSettings]:
        for key in value:
            if CHECKOUT_ADAPTER_KEY_PATTERN.fullmatch(key) is None:
                raise ValueError(
                    "Checkout adapter keys must use lowercase letters, digits, "
                    "hyphens, or underscores"
                )
        return value

    @field_validator("checkout_demo_adapter_key")
    @classmethod
    def validate_demo_adapter_key(cls, value: str) -> str:
        if CHECKOUT_ADAPTER_KEY_PATTERN.fullmatch(value) is None:
            raise ValueError("CHECKOUT_DEMO_ADAPTER_KEY is invalid")
        return value

    @model_validator(mode="after")
    def install_hosted_demo_adapter(self) -> "CheckoutRuntimeSettings":
        if not self.checkout_hosted_demo_enabled:
            return self
        if self.environment.lower() not in {"development", "test"}:
            raise ValueError("CHECKOUT_HOSTED_DEMO_ENABLED is development/test-only")
        if not self.checkout_demo_enabled:
            raise ValueError("CHECKOUT_HOSTED_DEMO_ENABLED requires CHECKOUT_DEMO_ENABLED")
        built_in = stripe_hosted_test_adapter()
        configured = self.checkout_adapters.get(STRIPE_HOSTED_TEST_ADAPTER_KEY)
        if configured is not None and configured != built_in:
            raise ValueError("The built-in stripe-hosted adapter cannot be overridden")
        self.checkout_adapters = {
            **self.checkout_adapters,
            STRIPE_HOSTED_TEST_ADAPTER_KEY: built_in,
        }
        return self


class Settings(CheckoutRuntimeSettings):
    app_name: str = "AG Platform API"
    api_v1_prefix: str = "/api/v1"
    jwt_secret: str = "development-only-change-me-please-32-chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    agent_token_expire_days: int = 365
    pairing_token_expire_minutes: int = 15
    agent_online_window_seconds: int = 120
    credential_encryption_key: str | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment.lower() not in {"development", "test"}:
            if self.checkout_demo_enabled:
                raise ValueError("CHECKOUT_DEMO_ENABLED is development/test-only")
            if self.jwt_secret == "development-only-change-me-please-32-chars":
                raise ValueError("JWT_SECRET must be changed outside development")
            if self.credential_encryption_key is None:
                raise ValueError("CREDENTIAL_ENCRYPTION_KEY is required outside development")
        return self


class CheckoutWorkerSettings(CheckoutRuntimeSettings):
    """Worker-only provider credentials, never loaded into the FastAPI settings object."""

    browserbase_api_key: SecretStr | None = None
    browserbase_project_id: str | None = None
    browserbase_region: str = "eu-central-1"
    browserbase_api_url: str = "https://api.browserbase.com/v1"
    stripe_secret_key: SecretStr | None = None
    stripe_demo_secret_key: SecretStr | None = None
    stripe_api_url: str = "https://api.stripe.com"

    @field_validator("browserbase_api_url", "stripe_api_url")
    @classmethod
    def validate_provider_api_url(cls, value: str, info) -> str:
        parsed = urlsplit(value.strip())
        expected_host = (
            "api.browserbase.com" if info.field_name == "browserbase_api_url" else "api.stripe.com"
        )
        expected_path = "/v1" if info.field_name == "browserbase_api_url" else ""
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname != expected_host
            or parsed.port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != expected_path
        ):
            raise ValueError(f"{info.field_name} must use the pinned official HTTPS endpoint")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_demo_credentials(self) -> "CheckoutWorkerSettings":
        if self.checkout_demo_enabled:
            if self.environment.lower() not in {"development", "test"}:
                raise ValueError("CHECKOUT_DEMO_ENABLED is development/test-only")
            if self.stripe_demo_secret_key is None or not (
                self.stripe_demo_secret_key.get_secret_value().startswith("sk_test_")
            ):
                raise ValueError("STRIPE_DEMO_SECRET_KEY must be a Stripe test-mode secret key")
        return self


class DemoMerchantSettings(BaseSettings):
    """Credentials and fixed catalog data for the separate local demo merchant."""

    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    stripe_demo_secret_key: SecretStr
    stripe_demo_publishable_key: str
    stripe_api_url: str = "https://api.stripe.com"
    demo_product_title: str = Field(default="AG Pay Browserbase Demo", min_length=1, max_length=255)
    demo_amount_minor: int = Field(default=2500, gt=0)
    demo_currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def validate_test_mode(self) -> "DemoMerchantSettings":
        if not self.stripe_demo_secret_key.get_secret_value().startswith("sk_test_"):
            raise ValueError("The demo merchant requires a Stripe test-mode secret key")
        if not self.stripe_demo_publishable_key.startswith("pk_test_"):
            raise ValueError("The demo merchant requires a Stripe test-mode publishable key")
        if self.demo_currency not in {"EUR", "USD"}:
            raise ValueError("The demo merchant supports EUR or USD")
        parsed = urlsplit(self.stripe_api_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.stripe.com"
            or parsed.port not in {None, 443}
            or parsed.path.rstrip("/")
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError("STRIPE_API_URL must use the official Stripe HTTPS endpoint")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_runtime_settings() -> CheckoutRuntimeSettings:
    return CheckoutRuntimeSettings()


@lru_cache
def get_worker_settings() -> CheckoutWorkerSettings:
    return CheckoutWorkerSettings()
