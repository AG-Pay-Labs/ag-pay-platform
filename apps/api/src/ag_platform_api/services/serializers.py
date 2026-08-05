from datetime import UTC, datetime, timedelta

from ag_platform_api.core.config import Settings
from ag_platform_api.models import (
    Agent,
    AgentStatus,
    CartItem,
    Purchase,
    Subscription,
)
from ag_platform_api.schemas import (
    AgentRead,
    CartItemRead,
    PurchaseRead,
    SubscriptionRead,
)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


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


def cart_item_read(item: CartItem) -> CartItemRead:
    return CartItemRead(
        id=item.id,
        agent_id=item.agent_id,
        credential_id=item.credential_id,
        selected_payment_method_id=item.selected_payment_method_id,
        title=item.title,
        description=item.description,
        product_url=item.product_url,
        merchant=item.merchant,
        reason=item.reason,
        quantity=item.quantity,
        unit_price=item.unit_price,
        total_amount=item.unit_price * item.quantity,
        currency=item.currency,
        billing_period=item.billing_period,
        status=item.status,
        decision_note=item.decision_note,
        account_email=item.credential.email,
        login_url=item.credential.login_url,
        approved_at=item.approved_at,
        cancelled_at=item.cancelled_at,
        created_at=item.created_at,
    )


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
        amount=purchase.amount,
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
        amount=purchase.amount,
        currency=purchase.currency,
        provider_reference=purchase.provider_reference,
        receipt_url=purchase.receipt_url,
        account_email=cart.credential.email,
        purchased_at=purchase.purchased_at,
        subscription=(
            subscription_read(purchase.subscription, purchase) if purchase.subscription else None
        ),
    )
