import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

from ag_platform_api.models import (
    AgentStatus,
    BillingPeriod,
    BillingProfileType,
    CartItemStatus,
    CheckoutExecutionStatus,
    PaymentApprovalMode,
    PaymentMethodStatus,
    PurchaseStatus,
    SubscriptionStatus,
)
from ag_platform_api.services.checkout.errors import CheckoutError
from ag_platform_api.services.checkout.types import decimal_to_minor

Username = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True, min_length=3, max_length=64),
]
Password = Annotated[SecretStr, Field(min_length=10, max_length=256)]
OPAQUE_PROVIDER_REFERENCE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,254}$")
PROVIDER_REFERENCE_PATTERNS = {
    "stripe_issuing": re.compile(r"^ic_[A-Za-z0-9]+$"),
    "stripe_link": re.compile(r"^csmrpd_[A-Za-z0-9]+$"),
    "prototype-vault": re.compile(r"^pm_[A-Za-z0-9_-]+$"),
}


def _uppercase(value: str) -> str:
    return value.strip().upper()


def _luhn_valid(value: str) -> bool:
    checksum = 0
    parity = len(value) % 2
    for index, character in enumerate(value):
        digit = int(character)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def normalize_card_number(value: SecretStr | str) -> str:
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    normalized = raw.replace(" ", "").replace("-", "")
    if not normalized.isdigit() or not 12 <= len(normalized) <= 19:
        raise ValueError("Card number must contain 12 to 19 digits")
    if not _luhn_valid(normalized):
        raise ValueError("Card number checksum is invalid")
    return normalized


def _contains_pan_or_cvc_like_digits(value: str) -> bool:
    if re.search(r"(?:^|[_-])\d{3,4}$", value):
        return True
    digits = "".join(character for character in value if character.isdigit())
    return 12 <= len(digits) <= 19 and _luhn_valid(digits)


Currency = Annotated[
    str,
    BeforeValidator(_uppercase),
    StringConstraints(pattern=r"^[A-Z]{3}$"),
]
CountryCode = Annotated[
    str,
    BeforeValidator(_uppercase),
    StringConstraints(pattern=r"^[A-Z]{2}$"),
]


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class Message(APIModel):
    message: str


class UserRegister(APIModel):
    username: Username
    password: Password


class UserRead(APIModel):
    id: UUID
    username: str
    is_active: bool
    created_at: datetime


class TokenResponse(APIModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class LoginRequest(APIModel):
    username: Username
    password: SecretStr


class AgentCreate(APIModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Annotated[str | None, StringConstraints(max_length=2000)] = None


class AgentRead(APIModel):
    id: UUID
    name: str
    description: str | None
    status: AgentStatus
    connection_state: Literal["pending", "online", "offline", "revoked"]
    instance_id: str | None
    software_version: str | None
    capabilities: list[str]
    connected_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime


class AgentCreated(AgentRead):
    pairing_token: str
    pairing_expires_at: datetime


class PairingTokenResponse(APIModel):
    pairing_token: str
    pairing_expires_at: datetime


class AgentPaymentPolicyRead(APIModel):
    id: UUID
    agent_id: UUID
    mode: PaymentApprovalMode
    threshold_amount: Decimal | None
    threshold_currency: str | None
    created_at: datetime
    updated_at: datetime


class AgentPaymentPolicyUpdate(APIModel):
    mode: PaymentApprovalMode
    threshold_amount: Decimal | None = Field(
        default=None,
        ge=0,
        max_digits=18,
        decimal_places=2,
    )
    threshold_currency: Currency | None = None

    @model_validator(mode="after")
    def validate_threshold(self) -> "AgentPaymentPolicyUpdate":
        threshold_mode = self.mode in {
            PaymentApprovalMode.above_amount,
            PaymentApprovalMode.subscriptions_or_above_amount,
        }
        threshold_supplied = (
            self.threshold_amount is not None or self.threshold_currency is not None
        )
        threshold_complete = (
            self.threshold_amount is not None and self.threshold_currency is not None
        )
        if threshold_mode and not threshold_complete:
            raise ValueError("Threshold amount and currency are required for this mode")
        if not threshold_mode and threshold_supplied:
            raise ValueError("Threshold amount and currency are not allowed for this mode")
        return self


class AgentHandshake(APIModel):
    pairing_token: Annotated[str, StringConstraints(min_length=20, max_length=200)]
    instance_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    software_version: Annotated[str | None, StringConstraints(max_length=100)] = None
    capabilities: list[Annotated[str, StringConstraints(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=100
    )


class AgentTokenResponse(APIModel):
    agent_id: UUID
    agent_access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class AgentHeartbeatResponse(APIModel):
    agent_id: UUID
    connection_state: Literal["online"] = "online"
    server_time: datetime


class BillingAddress(APIModel):
    line1: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    line2: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=255)] = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    region: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=120)] = None
    postal_code: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
    ]
    country: CountryCode


class PersonalBillingDetails(APIModel):
    type: Literal[BillingProfileType.personal] = BillingProfileType.personal
    full_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    email: EmailStr
    phone: Annotated[str | None, StringConstraints(max_length=32)] = None
    address: BillingAddress


class BusinessBillingDetails(APIModel):
    type: Literal[BillingProfileType.business] = BillingProfileType.business
    legal_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    vat_number: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=2, max_length=64)
    ]
    registration_number: Annotated[str | None, StringConstraints(max_length=64)] = None
    contact_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    email: EmailStr
    phone: Annotated[str | None, StringConstraints(max_length=32)] = None
    address: BillingAddress


BillingDetails = Annotated[
    PersonalBillingDetails | BusinessBillingDetails, Field(discriminator="type")
]


class PaymentMethodCreate(APIModel):
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    provider: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    provider_payment_method_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=255)
    ]
    card_brand: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
    ]
    card_last4: Annotated[str, StringConstraints(pattern=r"^\d{4}$")]
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2020, le=2200)
    billing_details: BillingDetails

    @model_validator(mode="after")
    def validate_expiry(self) -> "PaymentMethodCreate":
        now = datetime.now(UTC)
        if (self.expiry_year, self.expiry_month) < (now.year, now.month):
            raise ValueError("Card expiry must not be in the past")
        reference_pattern = PROVIDER_REFERENCE_PATTERNS.get(self.provider)
        if (
            reference_pattern is None
            or OPAQUE_PROVIDER_REFERENCE.fullmatch(self.provider_payment_method_id) is None
            or reference_pattern.fullmatch(self.provider_payment_method_id) is None
            or _contains_pan_or_cvc_like_digits(self.provider_payment_method_id)
        ):
            raise ValueError(
                "Provider payment-method reference must be an opaque provider identifier"
            )
        return self


class DirectCardPaymentMethodCreate(APIModel):
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    card_number: SecretStr = Field(min_length=12, max_length=32)
    expiry_month: int = Field(ge=1, le=12)
    expiry_year: int = Field(ge=2020, le=2200)
    billing_details: BillingDetails

    @model_validator(mode="after")
    def validate_card(self) -> "DirectCardPaymentMethodCreate":
        normalize_card_number(self.card_number)
        now = datetime.now(UTC)
        if (self.expiry_year, self.expiry_month) < (now.year, now.month):
            raise ValueError("Card expiry must not be in the past")
        return self


class PaymentMethodRead(APIModel):
    id: UUID
    display_name: str
    status: PaymentMethodStatus
    provider: str
    card_brand: str
    card_last4: str
    expiry_month: int
    expiry_year: int
    billing_profile_type: BillingProfileType
    billing_details: dict
    created_at: datetime


class AccountCredentialInput(APIModel):
    email: EmailStr
    password: Password
    login_url: AnyHttpUrl | None = None


class CheckoutSpec(APIModel):
    adapter: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=64,
            pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$",
        ),
    ]
    checkout_url: AnyHttpUrl

    @model_validator(mode="after")
    def validate_checkout_url(self) -> "CheckoutSpec":
        try:
            parsed = urlsplit(str(self.checkout_url))
            _ = parsed.port
        except ValueError:
            raise ValueError(
                "Managed checkout requires an absolute HTTPS URL without embedded credentials"
            ) from None
        if (
            parsed.scheme.lower() != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError(
                "Managed checkout requires an absolute HTTPS URL without embedded credentials"
            )
        return self


class CartItemCreate(APIModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)
    ]
    product_url: AnyHttpUrl
    merchant: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=255)] = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]
    quantity: int = Field(default=1, ge=1, le=10000)
    unit_price: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Currency
    billing_period: BillingPeriod | None = None
    account: AccountCredentialInput
    checkout: CheckoutSpec | None = None

    @model_validator(mode="after")
    def reject_unverified_managed_recurrence(self) -> "CartItemCreate":
        if self.checkout is not None and self.billing_period is not None:
            raise ValueError("Managed checkout does not support recurring purchases")
        if self.checkout is not None:
            try:
                decimal_to_minor(self.unit_price * self.quantity, self.currency)
            except CheckoutError as error:
                raise ValueError(error.safe_message) from None
        return self


class CheckoutExecutionSummary(APIModel):
    id: UUID
    status: CheckoutExecutionStatus
    attempt_count: int
    approved_amount: Decimal
    currency: str
    checkout_origin: str
    submitted_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class CartItemRead(APIModel):
    id: UUID
    agent_id: UUID
    credential_id: UUID
    selected_payment_method_id: UUID | None
    title: str
    description: str
    product_url: str
    checkout_adapter: str | None
    checkout_url: str | None
    merchant: str | None
    reason: str
    quantity: int
    unit_price: Decimal
    total_amount: Decimal
    currency: str
    billing_period: BillingPeriod | None
    status: CartItemStatus
    decision_note: str | None
    account_email: EmailStr
    login_url: str | None
    approved_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    execution: CheckoutExecutionSummary | None = None


class CheckoutStatusTransitionRead(APIModel):
    status: CheckoutExecutionStatus
    attempt_count: int
    error_code: str | None
    error_message: str | None
    occurred_at: datetime


class HumanCheckoutExecutionSummary(CheckoutExecutionSummary):
    merchant_order_reference: str | None
    browserbase_session_id: str | None
    status_history: list[CheckoutStatusTransitionRead]


class HumanCartItemRead(CartItemRead):
    execution: HumanCheckoutExecutionSummary | None = None


class CartApproval(APIModel):
    payment_method_id: UUID
    note: Annotated[str | None, StringConstraints(max_length=2000)] = None
    cvc: SecretStr | None = Field(default=None, min_length=3, max_length=4)

    @field_validator("cvc")
    @classmethod
    def validate_cvc(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().isdigit():
            raise ValueError("CVC must contain three or four digits")
        return value


class CartCancellation(APIModel):
    note: Annotated[str | None, StringConstraints(max_length=2000)] = None


class CredentialReveal(APIModel):
    email: EmailStr
    password: str
    login_url: str | None


class CredentialRevealRequest(APIModel):
    current_password: SecretStr


class PurchaseComplete(APIModel):
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Currency
    provider_reference: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    receipt_url: AnyHttpUrl | None = None
    next_billing_at: datetime | None = None


class CheckoutEventRead(APIModel):
    cursor: int
    event_id: UUID
    request_id: UUID
    status: CheckoutExecutionStatus
    purchase_id: UUID | None
    amount: Decimal
    currency: str
    error_code: str | None
    occurred_at: datetime


class CheckoutEventPage(APIModel):
    events: list[CheckoutEventRead]
    next_cursor: int


class SubscriptionRead(APIModel):
    id: UUID
    purchase_id: UUID
    agent_id: UUID
    title: str
    billing_period: BillingPeriod
    status: SubscriptionStatus
    amount: Decimal
    currency: str
    next_billing_at: datetime | None
    created_at: datetime


class PurchaseRead(APIModel):
    id: UUID
    cart_item_id: UUID
    agent_id: UUID
    payment_method_id: UUID
    title: str
    description: str
    product_url: str
    status: PurchaseStatus
    amount: Decimal
    currency: str
    provider_reference: str
    merchant_order_reference: str | None
    receipt_url: str | None
    account_email: EmailStr
    purchased_at: datetime
    subscription: SubscriptionRead | None = None


class SubscriptionUpdate(APIModel):
    status: SubscriptionStatus
    next_billing_at: datetime | None = None
