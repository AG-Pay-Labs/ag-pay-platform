from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import UUID

from ag_platform_api.services.checkout.errors import CheckoutError, CheckoutErrorCode

ZERO_DECIMAL_CURRENCIES = frozenset(
    {
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "JPY",
        "KMF",
        "KRW",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    }
)
THREE_DECIMAL_CURRENCIES = frozenset({"BHD", "JOD", "KWD", "OMR", "TND"})
STRIPE_PRESENTMENT_CURRENCIES = frozenset(
    """
    USD AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BIF BMD BND BOB BRL BSD
    BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CVE CZK DJF DKK DOP DZD EGP ETB EUR FJD
    FKP GBP GEL GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS INR ISK JMD JPY KES KGS
    KHR KMF KRW KYD KZT LAK LBP LKR LRD LSL MAD MDL MGA MKD MMK MNT MOP MUR MVR MWK
    MXN MYR MZN NAD NGN NIO NOK NPR NZD PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF
    SAR SBD SCR SEK SGD SHP SLE SOS SRD STD SZL THB TJS TOP TRY TTD TWD TZS UAH UGX UYU
    UZS VND VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW
    """.split()
)
CURRENCY_EXPONENTS = {
    **{currency: 2 for currency in STRIPE_PRESENTMENT_CURRENCIES},
    **{currency: 0 for currency in ZERO_DECIMAL_CURRENCIES},
    **{currency: 3 for currency in THREE_DECIMAL_CURRENCIES},
}


@dataclass(frozen=True, slots=True, repr=False)
class IssuingCardSecret:
    number: str
    cvc: str
    expiry_month: int
    expiry_year: int

    def __repr__(self) -> str:
        return "IssuingCardSecret(<redacted>)"


@dataclass(frozen=True, slots=True)
class ExpectedCardMetadata:
    owner_id: UUID
    last4: str
    brand: str
    expiry_month: int
    expiry_year: int


@dataclass(frozen=True, slots=True, repr=False)
class BrowserbaseSession:
    session_id: str
    connect_url: str

    def __repr__(self) -> str:
        return f"BrowserbaseSession(session_id={self.session_id!r}, connect_url=<redacted>)"


class AuthorizationOutcome(StrEnum):
    approved = "approved"
    declined = "declined"
    action_required = "action_required"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    outcome: AuthorizationOutcome
    provider_reference: str | None = None


@dataclass(frozen=True, slots=True)
class CheckoutAdapter:
    allowed_origins: tuple[str, ...]
    payment_origins: tuple[str, ...]
    product_title_selector: str
    quantity_selector: str
    total_selector: str
    card_number_selector: str
    cvc_selector: str
    submit_selector: str
    success_selector: str
    decline_selector: str | None = None
    resource_origins: tuple[str, ...] = ()
    expiry_selector: str | None = None
    expiry_month_selector: str | None = None
    expiry_year_selector: str | None = None
    name_selector: str | None = None
    billing_line1_selector: str | None = None
    billing_line2_selector: str | None = None
    billing_city_selector: str | None = None
    billing_region_selector: str | None = None
    billing_postal_code_selector: str | None = None
    billing_country_selector: str | None = None
    billing_email_selector: str | None = None
    billing_phone_selector: str | None = None
    action_required_selector: str | None = None
    order_reference_selector: str | None = None
    receipt_url_selector: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot: Mapping[str, Any]) -> "CheckoutAdapter":
        allowed_fields = set(cls.__dataclass_fields__)
        if not snapshot.keys() <= allowed_fields:
            raise CheckoutError(CheckoutErrorCode.adapter_invalid)
        try:
            adapter = cls(
                **{
                    **snapshot,
                    "allowed_origins": tuple(snapshot.get("allowed_origins", ())),
                    "payment_origins": tuple(snapshot.get("payment_origins", ())),
                    "resource_origins": tuple(snapshot.get("resource_origins", ())),
                }
            )
        except (TypeError, ValueError):
            raise CheckoutError(CheckoutErrorCode.adapter_invalid) from None
        adapter.validate()
        return adapter

    def validate(self) -> None:
        required = (
            self.allowed_origins,
            self.payment_origins,
            self.product_title_selector,
            self.quantity_selector,
            self.total_selector,
            self.card_number_selector,
            self.cvc_selector,
            self.submit_selector,
            self.success_selector,
        )
        if not all(required):
            raise CheckoutError(CheckoutErrorCode.adapter_invalid)
        combined = self.expiry_selector is not None
        split = self.expiry_month_selector is not None and self.expiry_year_selector is not None
        partial_split = (self.expiry_month_selector is None) != (self.expiry_year_selector is None)
        if combined == split or partial_split:
            raise CheckoutError(CheckoutErrorCode.adapter_invalid)
        selectors = (
            value
            for field_name, value in vars_for_slots(self).items()
            if field_name.endswith("_selector") and value is not None
        )
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 512
            for value in selectors
        ):
            raise CheckoutError(CheckoutErrorCode.adapter_invalid)


def vars_for_slots(instance: object) -> dict[str, Any]:
    return {
        field_name: getattr(instance, field_name)
        for field_name in instance.__class__.__dataclass_fields__
    }


@dataclass(frozen=True, slots=True)
class CheckoutContext:
    execution_id: UUID
    cart_item_id: UUID
    owner_id: UUID
    agent_id: UUID
    payment_method_id: UUID
    adapter_key: str
    adapter: CheckoutAdapter
    checkout_url: str
    checkout_origin: str
    approved_title: str
    approved_quantity: int
    amount: Decimal
    currency: str
    provider: str
    provider_card_id: str
    card_metadata: ExpectedCardMetadata
    billing_details: Mapping[str, Any]

    @property
    def amount_minor(self) -> int:
        return decimal_to_minor(self.amount, self.currency)


@dataclass(frozen=True, slots=True)
class BrowserCheckoutResult:
    order_reference: str | None
    receipt_url: str | None
    outcome: AuthorizationOutcome = AuthorizationOutcome.approved


def decimal_to_minor(amount: Decimal, currency: str) -> int:
    normalized_currency = currency.upper()
    try:
        exponent = CURRENCY_EXPONENTS[normalized_currency]
    except KeyError:
        raise CheckoutError(CheckoutErrorCode.currency_unsupported) from None
    scale = Decimal(10) ** exponent
    try:
        minor = amount * scale
        integral = minor.to_integral_exact()
    except (InvalidOperation, ValueError):
        raise CheckoutError(CheckoutErrorCode.currency_precision_invalid) from None
    if minor != integral or amount < 0:
        raise CheckoutError(CheckoutErrorCode.currency_precision_invalid)
    return int(integral)
