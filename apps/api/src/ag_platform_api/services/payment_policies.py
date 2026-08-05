from decimal import Decimal

from ag_platform_api.models import AgentPaymentPolicy, PaymentApprovalMode

THRESHOLD_MODES = {
    PaymentApprovalMode.above_amount,
    PaymentApprovalMode.subscriptions_or_above_amount,
}


def requires_human_approval(
    policy: AgentPaymentPolicy | None,
    *,
    amount: Decimal,
    currency: str,
    recurring: bool,
) -> bool:
    """Evaluate an agent policy, failing closed when threshold data is unusable."""
    if policy is None or policy.mode is PaymentApprovalMode.always:
        return True
    if policy.mode is PaymentApprovalMode.never:
        return False
    if policy.mode is PaymentApprovalMode.subscriptions_only:
        return recurring

    if policy.mode not in THRESHOLD_MODES:
        return True
    if policy.mode is PaymentApprovalMode.subscriptions_or_above_amount and recurring:
        return True
    if policy.threshold_amount is None or policy.threshold_currency is None:
        return True
    if policy.threshold_currency.upper() != currency.upper():
        return True
    return amount > policy.threshold_amount
