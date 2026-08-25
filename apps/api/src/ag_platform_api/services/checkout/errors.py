from dataclasses import dataclass
from enum import StrEnum


class CheckoutErrorCode(StrEnum):
    checkout_disabled = "checkout_disabled"
    execution_invalid = "execution_invalid"
    cart_not_approved = "cart_not_approved"
    agent_inactive = "agent_inactive"
    payment_method_unavailable = "payment_method_unavailable"
    payment_method_unassigned = "payment_method_unassigned"
    amount_mismatch = "amount_mismatch"
    currency_mismatch = "currency_mismatch"
    currency_unsupported = "currency_unsupported"
    currency_precision_invalid = "currency_precision_invalid"
    provider_unsupported = "provider_unsupported"
    recurring_unsupported = "recurring_unsupported"
    authorization_snapshot_failed = "authorization_snapshot_failed"
    card_reference_invalid = "card_reference_invalid"
    card_unavailable = "card_unavailable"
    payment_method_expired = "payment_method_expired"
    card_security_code_unavailable = "card_security_code_unavailable"
    card_reconciliation_required = "card_reconciliation_required"
    adapter_invalid = "adapter_invalid"
    form_analysis_failed = "form_analysis_failed"
    origin_blocked = "origin_blocked"
    item_mismatch = "item_mismatch"
    quantity_mismatch = "quantity_mismatch"
    total_not_found = "total_not_found"
    total_mismatch = "total_mismatch"
    payment_form_not_found = "payment_form_not_found"
    browser_session_failed = "browser_session_failed"
    browser_navigation_failed = "browser_navigation_failed"
    checkout_action_required = "checkout_action_required"
    payment_declined = "payment_declined"
    payment_outcome_unknown = "payment_outcome_unknown"
    checkout_failed = "checkout_failed"


SAFE_ERROR_MESSAGES: dict[CheckoutErrorCode, str] = {
    CheckoutErrorCode.checkout_disabled: "Checkout execution is disabled.",
    CheckoutErrorCode.execution_invalid: "The checkout execution is no longer valid.",
    CheckoutErrorCode.cart_not_approved: "The cart item is not approved for checkout.",
    CheckoutErrorCode.agent_inactive: "The purchasing agent is not active.",
    CheckoutErrorCode.payment_method_unavailable: "The approved payment method is unavailable.",
    CheckoutErrorCode.payment_method_unassigned: (
        "The approved payment method is no longer assigned to the agent."
    ),
    CheckoutErrorCode.amount_mismatch: "The checkout amount no longer matches the approval.",
    CheckoutErrorCode.currency_mismatch: "The checkout currency no longer matches the approval.",
    CheckoutErrorCode.currency_unsupported: "The managed checkout currency is not supported.",
    CheckoutErrorCode.currency_precision_invalid: (
        "The managed checkout amount is invalid for its currency."
    ),
    CheckoutErrorCode.provider_unsupported: "The payment provider is not supported for checkout.",
    CheckoutErrorCode.recurring_unsupported: (
        "Managed checkout does not support recurring purchases."
    ),
    CheckoutErrorCode.authorization_snapshot_failed: (
        "Existing card authorizations could not be verified before checkout."
    ),
    CheckoutErrorCode.card_reference_invalid: "The payment card reference is invalid.",
    CheckoutErrorCode.card_unavailable: "The approved virtual card is unavailable.",
    CheckoutErrorCode.payment_method_expired: "The approved payment method has expired.",
    CheckoutErrorCode.card_security_code_unavailable: (
        "The card security code is no longer available; approve a new checkout."
    ),
    CheckoutErrorCode.card_reconciliation_required: (
        "The virtual card is quarantined until an unresolved checkout is reconciled."
    ),
    CheckoutErrorCode.adapter_invalid: "The merchant checkout adapter is invalid.",
    CheckoutErrorCode.form_analysis_failed: "The payment form could not be mapped safely.",
    CheckoutErrorCode.origin_blocked: "Checkout attempted to leave an approved origin.",
    CheckoutErrorCode.item_mismatch: "The merchant item does not match the approved item.",
    CheckoutErrorCode.quantity_mismatch: "The merchant quantity does not match the approval.",
    CheckoutErrorCode.total_not_found: "The checkout total could not be located.",
    CheckoutErrorCode.total_mismatch: "The merchant total does not match the approved amount.",
    CheckoutErrorCode.payment_form_not_found: "The approved payment form could not be located.",
    CheckoutErrorCode.browser_session_failed: "The secure browser session could not be started.",
    CheckoutErrorCode.browser_navigation_failed: "The merchant checkout could not be loaded.",
    CheckoutErrorCode.checkout_action_required: "The merchant requires additional user action.",
    CheckoutErrorCode.payment_declined: "The approved payment method was declined.",
    CheckoutErrorCode.payment_outcome_unknown: "The merchant payment outcome could not be proven.",
    CheckoutErrorCode.checkout_failed: "The checkout could not be completed.",
}


@dataclass(frozen=True, slots=True)
class CheckoutError(Exception):
    """A safe worker error whose text is fixed and contains no provider data."""

    code: CheckoutErrorCode
    retryable: bool = False

    @property
    def safe_message(self) -> str:
        return SAFE_ERROR_MESSAGES[self.code]

    def __str__(self) -> str:
        return self.safe_message
