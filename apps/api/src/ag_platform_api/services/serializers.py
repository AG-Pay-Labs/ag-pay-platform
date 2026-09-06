import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from ag_platform_api.core.config import Settings
from ag_platform_api.models import (
    Agent,
    AgentStatus,
    CartItem,
    CheckoutEvent,
    CheckoutExecution,
    CheckoutExecutionStatus,
    CheckoutStatusTransition,
    Purchase,
    Subscription,
    X402Payment,
)
from ag_platform_api.schemas import (
    AgentRead,
    CartItemRead,
    CheckoutEventRead,
    CheckoutExecutionSummary,
    CheckoutStatusTransitionRead,
    HumanCartItemRead,
    HumanCheckoutExecutionSummary,
    PurchaseRead,
    SubscriptionRead,
    X402MetadataRead,
)
from ag_platform_api.services.checkout.errors import (
    SAFE_ERROR_MESSAGES,
    CheckoutErrorCode,
)

X402_TRANSACTION_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _api_money(value: Decimal) -> Decimal:
    """Keep legacy cents while retaining meaningful x402 sub-cent precision."""
    normalized = value.normalize()
    exponent = min(normalized.as_tuple().exponent, -2)
    return value.quantize(Decimal(1).scaleb(exponent))


def agent_connection_state(agent: Agent, settings: Settings) -> str:
    if agent.status is AgentStatus.revoked:
        return "revoked"
    if agent.status is AgentStatus.pending or agent.last_seen_at is None:
        return "pending"
    online_after = datetime.now(UTC) - timedelta(seconds=settings.agent_online_window_seconds)
    return "online" if _utc(agent.last_seen_at) >= online_after else "offline"


def agent_read(agent: Agent, settings: Settings) -> AgentRead:
    return AgentRead(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        status=agent.status,
        connection_state=agent_connection_state(agent, settings),
        instance_id=agent.instance_id,
        software_version=agent.software_version,
        capabilities=agent.capabilities,
        connected_at=agent.connected_at,
        last_seen_at=agent.last_seen_at,
        created_at=agent.created_at,
    )


def x402_transaction_evidence(
    payment: X402Payment | None,
    execution: CheckoutExecution | None,
) -> str | None:
    if (
        payment is None
        or execution is None
        or execution.status
        not in {
            CheckoutExecutionStatus.succeeded,
            CheckoutExecutionStatus.failed,
            CheckoutExecutionStatus.outcome_unknown,
        }
        or payment.transaction is None
        or X402_TRANSACTION_HASH.fullmatch(payment.transaction) is None
    ):
        return None
    return payment.transaction.lower()


def cart_item_read(item: CartItem) -> CartItemRead:
    return CartItemRead(
        id=item.id,
        agent_id=item.agent_id,
        credential_id=item.credential_id,
        selected_payment_method_id=item.selected_payment_method_id,
        title=item.title,
        description=item.description,
        product_url=item.product_url,
        checkout_adapter=item.checkout_adapter,
        checkout_url=item.checkout_url,
        merchant=item.merchant,
        reason=item.reason,
        quantity=item.quantity,
        unit_price=_api_money(item.unit_price),
        total_amount=_api_money(item.unit_price * item.quantity),
        currency=item.currency,
        billing_period=item.billing_period,
        status=item.status,
        decision_note=item.decision_note,
        account_email=item.credential.email if item.credential is not None else None,
        login_url=item.credential.login_url if item.credential is not None else None,
        approved_at=item.approved_at,
        cancelled_at=item.cancelled_at,
        created_at=item.created_at,
        execution=(
            checkout_execution_summary(item.checkout_execution)
            if item.checkout_execution is not None
            else None
        ),
        x402=(
            X402MetadataRead(
                resource_url=item.x402_payment.resource_url,
                network=item.x402_payment.network,
                asset=item.x402_payment.asset,
                symbol=item.x402_payment.asset_symbol,
                decimals=item.x402_payment.asset_decimals,
                amount_atomic=item.x402_payment.amount_atomic,
                transfer_method=item.x402_payment.transfer_method,
                pay_to=item.x402_payment.pay_to,
                transaction=x402_transaction_evidence(item.x402_payment, item.checkout_execution),
            )
            if item.x402_payment is not None
            else None
        ),
    )


def checkout_execution_summary(execution: CheckoutExecution) -> CheckoutExecutionSummary:
    error_code, error_message = safe_checkout_error(execution.error_code)
    return CheckoutExecutionSummary(
        id=execution.id,
        status=execution.status,
        attempt_count=execution.attempt_count,
        approved_amount=_api_money(execution.approved_amount),
        currency=execution.currency,
        checkout_origin=execution.checkout_origin,
        submitted_at=execution.submitted_at,
        completed_at=execution.completed_at,
        error_code=error_code,
        error_message=error_message,
        created_at=execution.created_at,
        updated_at=execution.updated_at,
    )


def human_cart_item_read(item: CartItem) -> HumanCartItemRead:
    serialized = cart_item_read(item).model_dump()
    serialized["execution"] = (
        human_checkout_execution_summary(item.checkout_execution)
        if item.checkout_execution is not None
        else None
    )
    return HumanCartItemRead.model_validate(serialized)


def human_checkout_execution_summary(
    execution: CheckoutExecution,
) -> HumanCheckoutExecutionSummary:
    serialized = checkout_execution_summary(execution).model_dump()
    serialized["merchant_order_reference"] = execution.merchant_order_reference
    serialized["browserbase_session_id"] = execution.browserbase_session_id
    serialized["status_history"] = [
        checkout_status_transition_read(transition) for transition in execution.status_transitions
    ]
    return HumanCheckoutExecutionSummary.model_validate(serialized)


def checkout_status_transition_read(
    transition: CheckoutStatusTransition,
) -> CheckoutStatusTransitionRead:
    error_code, error_message = safe_checkout_error(transition.error_code)
    return CheckoutStatusTransitionRead(
        status=transition.status,
        attempt_count=transition.attempt_count,
        error_code=error_code,
        error_message=error_message,
        occurred_at=transition.occurred_at,
    )


def checkout_event_read(event: CheckoutEvent) -> CheckoutEventRead:
    error_code, _ = safe_checkout_error(event.error_code)
    return CheckoutEventRead(
        cursor=event.cursor,
        event_id=event.event_id,
        request_id=event.cart_item_id,
        status=event.status,
        purchase_id=event.purchase_id,
        amount=_api_money(event.amount),
        currency=event.currency,
        error_code=error_code,
        occurred_at=event.created_at,
    )


def safe_checkout_error(error_code: str | None) -> tuple[str | None, str | None]:
    if error_code is None:
        return None, None
    try:
        safe_code = CheckoutErrorCode(error_code)
    except ValueError:
        safe_code = CheckoutErrorCode.checkout_failed
    return safe_code.value, SAFE_ERROR_MESSAGES[safe_code]


def subscription_read(
    subscription: Subscription, purchase: Purchase | None = None
) -> SubscriptionRead:
    purchase = purchase or subscription.purchase
    return SubscriptionRead(
        id=subscription.id,
        purchase_id=subscription.purchase_id,
        agent_id=subscription.agent_id,
        title=purchase.cart_item.title,
        billing_period=subscription.billing_period,
        status=subscription.status,
        amount=_api_money(purchase.amount),
        currency=purchase.currency,
        next_billing_at=subscription.next_billing_at,
        created_at=subscription.created_at,
    )


def purchase_read(purchase: Purchase) -> PurchaseRead:
    cart = purchase.cart_item
    return PurchaseRead(
        id=purchase.id,
        cart_item_id=purchase.cart_item_id,
        agent_id=purchase.agent_id,
        payment_method_id=purchase.payment_method_id,
        title=cart.title,
        description=cart.description,
        product_url=cart.product_url,
        status=purchase.status,
        amount=_api_money(purchase.amount),
        currency=purchase.currency,
        provider_reference=purchase.provider_reference,
        merchant_order_reference=purchase.merchant_order_reference,
        receipt_url=purchase.receipt_url,
        account_email=cart.credential.email if cart.credential is not None else None,
        purchased_at=purchase.purchased_at,
        subscription=(
            subscription_read(purchase.subscription, purchase) if purchase.subscription else None
        ),
    )
