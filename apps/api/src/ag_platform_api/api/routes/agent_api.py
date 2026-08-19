from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from ag_platform_api.api.dependencies import (
    AppSettings,
    Broker,
    CurrentAgent,
    DatabaseSession,
)
from ag_platform_api.api.routes.cart import load_cart_item
from ag_platform_api.core.config import LOCAL_DIRECT_CARD_PROVIDER
from ag_platform_api.core.security import encrypt_secret, hash_opaque_token, new_opaque_token
from ag_platform_api.models import (
    Agent,
    AgentPaymentMethod,
    AgentPaymentPolicy,
    AgentStatus,
    CartItem,
    CartItemStatus,
    CheckoutEvent,
    PaymentMethod,
    PaymentMethodStatus,
    Purchase,
    PurchaseCredential,
    PurchaseStatus,
    Subscription,
)
from ag_platform_api.schemas import (
    AgentHandshake,
    AgentHeartbeatResponse,
    AgentTokenResponse,
    CartItemCreate,
    CartItemRead,
    CheckoutEventPage,
    PurchaseComplete,
    PurchaseRead,
)
from ag_platform_api.services.checkout_queue import CheckoutQueueError, queue_checkout_execution
from ag_platform_api.services.payment_policies import requires_human_approval
from ag_platform_api.services.serializers import cart_item_read, checkout_event_read, purchase_read

router = APIRouter(prefix="/agent", tags=["agent API"])


@router.post("/handshake", response_model=AgentTokenResponse)
async def handshake(
    payload: AgentHandshake,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
) -> AgentTokenResponse:
    token_hash = hash_opaque_token(payload.pairing_token, settings)
    agent = await db.scalar(
        select(Agent).where(Agent.pairing_token_hash == token_hash).with_for_update()
    )
    now = datetime.now(UTC)
    if (
        agent is None
        or agent.status is AgentStatus.revoked
        or agent.pairing_expires_at is None
        or (
            agent.pairing_expires_at
            if agent.pairing_expires_at.tzinfo
            else agent.pairing_expires_at.replace(tzinfo=UTC)
        )
        <= now
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired pairing token")

    agent_token = new_opaque_token("agt")
    token_expires_at = now + timedelta(days=settings.agent_token_expire_days)
    agent.status = AgentStatus.active
    agent.instance_id = payload.instance_id
    agent.software_version = payload.software_version
    agent.capabilities = list(dict.fromkeys(payload.capabilities))
    agent.connected_at = now
    agent.last_seen_at = now
    agent.api_key_hash = hash_opaque_token(agent_token, settings)
    agent.api_key_expires_at = token_expires_at
    agent.pairing_token_hash = None
    agent.pairing_expires_at = None
    await db.commit()
    await broker.mark_agent_online(str(agent.id), settings.agent_online_window_seconds)
    await broker.publish("agent.connected", {"agent_id": agent.id, "owner_id": agent.owner_id})
    return AgentTokenResponse(
        agent_id=agent.id,
        agent_access_token=agent_token,
        expires_at=token_expires_at,
    )


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
async def heartbeat(
    agent: CurrentAgent,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
) -> AgentHeartbeatResponse:
    now = datetime.now(UTC)
    agent.last_seen_at = now
    await db.commit()
    await broker.mark_agent_online(str(agent.id), settings.agent_online_window_seconds)
    return AgentHeartbeatResponse(agent_id=agent.id, server_time=now)


@router.post("/cart-items", response_model=CartItemRead, status_code=status.HTTP_201_CREATED)
async def propose_cart_item(
    payload: CartItemCreate,
    agent: CurrentAgent,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
) -> CartItemRead:
    policy = await db.scalar(
        select(AgentPaymentPolicy)
        .where(
            AgentPaymentPolicy.agent_id == agent.id,
            AgentPaymentPolicy.owner_id == agent.owner_id,
        )
        .with_for_update()
    )
    total_amount = payload.unit_price * payload.quantity
    approval_required = payload.checkout is not None or requires_human_approval(
        policy,
        amount=total_amount,
        currency=payload.currency,
        recurring=payload.billing_period is not None,
    )
    selected_payment_method: PaymentMethod | None = None
    if not approval_required:
        payment_method_query = (
            select(PaymentMethod)
            .join(AgentPaymentMethod)
            .where(
                AgentPaymentMethod.agent_id == agent.id,
                PaymentMethod.owner_id == agent.owner_id,
                PaymentMethod.status == PaymentMethodStatus.active,
                PaymentMethod.provider != LOCAL_DIRECT_CARD_PROVIDER,
            )
            .order_by(AgentPaymentMethod.payment_method_id)
            .limit(1)
            .with_for_update()
        )
        selected_payment_method = await db.scalar(payment_method_query)

    credential = PurchaseCredential(
        owner_id=agent.owner_id,
        agent_id=agent.id,
        email=str(payload.account.email),
        encrypted_password=encrypt_secret(payload.account.password.get_secret_value(), settings),
        login_url=str(payload.account.login_url) if payload.account.login_url else None,
    )
    db.add(credential)
    await db.flush()
    item = CartItem(
        owner_id=agent.owner_id,
        agent_id=agent.id,
        credential_id=credential.id,
        title=payload.title,
        description=payload.description,
        product_url=str(payload.product_url),
        checkout_adapter=payload.checkout.adapter if payload.checkout else None,
        checkout_url=str(payload.checkout.checkout_url) if payload.checkout else None,
        merchant=payload.merchant,
        reason=payload.reason,
        quantity=payload.quantity,
        unit_price=payload.unit_price,
        currency=payload.currency,
        billing_period=payload.billing_period,
        status=(
            CartItemStatus.approved
            if selected_payment_method is not None
            else CartItemStatus.proposed
        ),
        selected_payment_method_id=(
            selected_payment_method.id if selected_payment_method is not None else None
        ),
        decision_note=(
            "Automatically approved by the agent payment rule."
            if selected_payment_method is not None
            else None
        ),
        approved_at=datetime.now(UTC) if selected_payment_method is not None else None,
    )
    db.add(item)
    await db.flush()
    if selected_payment_method is not None:
        try:
            await queue_checkout_execution(
                db,
                item=item,
                payment_method=selected_payment_method,
                settings=settings,
            )
        except CheckoutQueueError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail=exc.message) from exc
    await db.commit()
    item = await load_cart_item(db, item.id)
    if selected_payment_method is not None:
        await broker.publish(
            "cart_item.approved",
            {
                "cart_item_id": item.id,
                "agent_id": agent.id,
                "owner_id": agent.owner_id,
                "payment_method_id": selected_payment_method.id,
                "approval_source": "payment_policy",
            },
        )
    else:
        await broker.publish(
            "cart_item.proposed",
            {"cart_item_id": item.id, "agent_id": agent.id, "owner_id": agent.owner_id},
        )
    return cart_item_read(item)


@router.get("/cart-items", response_model=list[CartItemRead])
async def list_agent_cart_items(
    agent: CurrentAgent,
    db: DatabaseSession,
    item_status: Annotated[CartItemStatus | None, Query(alias="status")] = None,
) -> list[CartItemRead]:
    query = (
        select(CartItem)
        .options(
            selectinload(CartItem.credential),
            selectinload(CartItem.checkout_execution),
        )
        .where(CartItem.agent_id == agent.id, CartItem.owner_id == agent.owner_id)
        .order_by(CartItem.created_at.desc())
    )
    if item_status is not None:
        query = query.where(CartItem.status == item_status)
    items = (await db.scalars(query)).all()
    return [cart_item_read(item) for item in items]


@router.get("/cart-items/{cart_item_id}", response_model=CartItemRead)
async def get_agent_cart_item(
    cart_item_id: UUID,
    agent: CurrentAgent,
    db: DatabaseSession,
) -> CartItemRead:
    item = await db.scalar(
        select(CartItem)
        .options(
            selectinload(CartItem.credential),
            selectinload(CartItem.checkout_execution),
        )
        .where(
            CartItem.id == cart_item_id,
            CartItem.agent_id == agent.id,
            CartItem.owner_id == agent.owner_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Cart item not found")
    return cart_item_read(item)


@router.get("/checkout-events", response_model=CheckoutEventPage)
async def list_checkout_events(
    agent: CurrentAgent,
    db: DatabaseSession,
    after_cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> CheckoutEventPage:
    events = list(
        (
            await db.scalars(
                select(CheckoutEvent)
                .where(
                    CheckoutEvent.agent_id == agent.id,
                    CheckoutEvent.owner_id == agent.owner_id,
                    CheckoutEvent.cursor > after_cursor,
                )
                .order_by(CheckoutEvent.cursor)
                .limit(limit)
            )
        ).all()
    )
    return CheckoutEventPage(
        events=[checkout_event_read(event) for event in events],
        next_cursor=events[-1].cursor if events else after_cursor,
    )


@router.post("/cart-items/{cart_item_id}/purchase", response_model=PurchaseRead)
async def complete_purchase(
    cart_item_id: UUID,
    payload: PurchaseComplete,
    agent: CurrentAgent,
    db: DatabaseSession,
    broker: Broker,
) -> PurchaseRead:
    item = await db.scalar(
        select(CartItem)
        .options(
            selectinload(CartItem.credential),
            selectinload(CartItem.checkout_execution),
        )
        .where(
            CartItem.id == cart_item_id,
            CartItem.agent_id == agent.id,
            CartItem.owner_id == agent.owner_id,
        )
        .with_for_update()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Cart item not found")
    if item.checkout_execution is not None:
        raise HTTPException(
            status_code=409,
            detail="Managed checkout completion is recorded by the checkout worker",
        )
    if item.status is not CartItemStatus.approved or item.selected_payment_method_id is None:
        raise HTTPException(status_code=409, detail="Cart item is not approved for purchase")

    expected_amount = item.unit_price * item.quantity
    if payload.amount != expected_amount or payload.currency != item.currency:
        raise HTTPException(
            status_code=409,
            detail="Final amount and currency must match the approved cart item",
        )

    assignment = await db.get(
        AgentPaymentMethod,
        {
            "agent_id": agent.id,
            "payment_method_id": item.selected_payment_method_id,
        },
    )
    payment_method = await db.get(PaymentMethod, item.selected_payment_method_id)
    if (
        assignment is None
        or payment_method is None
        or payment_method.status is not PaymentMethodStatus.active
    ):
        raise HTTPException(status_code=409, detail="Approved payment method is no longer assigned")

    now = datetime.now(UTC)
    purchase = Purchase(
        owner_id=agent.owner_id,
        agent_id=agent.id,
        payment_method_id=item.selected_payment_method_id,
        cart_item_id=item.id,
        status=PurchaseStatus.completed,
        amount=payload.amount,
        currency=payload.currency,
        provider_reference=payload.provider_reference,
        receipt_url=str(payload.receipt_url) if payload.receipt_url else None,
        purchased_at=now,
    )
    db.add(purchase)
    item.status = CartItemStatus.purchased
    if item.billing_period is not None:
        await db.flush()
        db.add(
            Subscription(
                owner_id=agent.owner_id,
                agent_id=agent.id,
                purchase_id=purchase.id,
                billing_period=item.billing_period,
                next_billing_at=payload.next_billing_at,
            )
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Purchase was already recorded") from exc

    purchase = await db.scalar(
        select(Purchase)
        .options(
            joinedload(Purchase.cart_item).joinedload(CartItem.credential),
            joinedload(Purchase.subscription),
        )
        .where(Purchase.id == purchase.id)
    )
    if purchase is None:  # pragma: no cover - protects against external deletion
        raise HTTPException(status_code=500, detail="Purchase could not be reloaded")
    await broker.publish(
        "purchase.completed",
        {
            "purchase_id": purchase.id,
            "cart_item_id": item.id,
            "agent_id": agent.id,
            "payment_method_id": purchase.payment_method_id,
        },
    )
    return purchase_read(purchase)
