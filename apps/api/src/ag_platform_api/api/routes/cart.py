from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ag_platform_api.api.dependencies import (
    AppSettings,
    Broker,
    CurrentUser,
    DatabaseSession,
)
from ag_platform_api.core.security import decrypt_secret, verify_password
from ag_platform_api.models import (
    AgentPaymentMethod,
    CartItem,
    CartItemStatus,
    CheckoutExecution,
    PaymentMethod,
    PaymentMethodStatus,
)
from ag_platform_api.schemas import (
    CartApproval,
    CartCancellation,
    CredentialReveal,
    CredentialRevealRequest,
    HumanCartItemRead,
)
from ag_platform_api.services.checkout_queue import CheckoutQueueError, queue_checkout_execution
from ag_platform_api.services.serializers import human_cart_item_read

router = APIRouter(prefix="/cart-items", tags=["cart"])


async def load_cart_item(db: DatabaseSession, cart_item_id: UUID) -> CartItem:
    item = await db.scalar(
        select(CartItem)
        .options(
            selectinload(CartItem.credential),
            selectinload(CartItem.checkout_execution).selectinload(
                CheckoutExecution.status_transitions
            ),
        )
        .where(CartItem.id == cart_item_id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Cart item not found")
    return item


async def owned_cart_item(
    db: DatabaseSession,
    owner_id: UUID,
    cart_item_id: UUID,
    *,
    for_update: bool = False,
) -> CartItem:
    query = (
        select(CartItem)
        .options(
            selectinload(CartItem.credential),
            selectinload(CartItem.checkout_execution).selectinload(
                CheckoutExecution.status_transitions
            ),
        )
        .where(CartItem.id == cart_item_id, CartItem.owner_id == owner_id)
    )
    if for_update:
        query = query.with_for_update()
    item = await db.scalar(query)
    if item is None:
        raise HTTPException(status_code=404, detail="Cart item not found")
    return item


@router.get("", response_model=list[HumanCartItemRead])
async def list_cart_items(
    user: CurrentUser,
    db: DatabaseSession,
    item_status: Annotated[CartItemStatus | None, Query(alias="status")] = None,
) -> list[HumanCartItemRead]:
    query = (
        select(CartItem)
        .options(
            selectinload(CartItem.credential),
            selectinload(CartItem.checkout_execution).selectinload(
                CheckoutExecution.status_transitions
            ),
        )
        .where(CartItem.owner_id == user.id)
        .order_by(CartItem.created_at.desc())
    )
    if item_status is not None:
        query = query.where(CartItem.status == item_status)
    items = (await db.scalars(query)).all()
    return [human_cart_item_read(item) for item in items]


@router.get("/{cart_item_id}", response_model=HumanCartItemRead)
async def get_cart_item(
    cart_item_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
) -> HumanCartItemRead:
    return human_cart_item_read(await owned_cart_item(db, user.id, cart_item_id))


@router.post("/{cart_item_id}/approve", response_model=HumanCartItemRead)
async def approve_cart_item(
    cart_item_id: UUID,
    payload: CartApproval,
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
) -> HumanCartItemRead:
    item = await owned_cart_item(db, user.id, cart_item_id, for_update=True)
    if item.status is not CartItemStatus.proposed:
        raise HTTPException(status_code=409, detail="Only proposed cart items can be approved")
    payment_method = await db.scalar(
        select(PaymentMethod)
        .where(
            PaymentMethod.id == payload.payment_method_id,
            PaymentMethod.owner_id == user.id,
            PaymentMethod.status == PaymentMethodStatus.active,
        )
        .with_for_update()
    )
    if payment_method is None:
        raise HTTPException(status_code=404, detail="Active payment method not found")
    assignment = await db.get(
        AgentPaymentMethod,
        {"agent_id": item.agent_id, "payment_method_id": payment_method.id},
    )
    if assignment is None:
        raise HTTPException(status_code=409, detail="Payment method is not assigned to this agent")

    item.status = CartItemStatus.approved
    item.selected_payment_method_id = payment_method.id
    item.decision_note = payload.note
    item.approved_at = datetime.now(UTC)
    try:
        await queue_checkout_execution(
            db,
            item=item,
            payment_method=payment_method,
            settings=settings,
        )
    except CheckoutQueueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=exc.message) from exc
    await db.commit()
    item = await load_cart_item(db, item.id)
    await broker.publish(
        "cart_item.approved",
        {
            "cart_item_id": item.id,
            "agent_id": item.agent_id,
            "payment_method_id": payment_method.id,
        },
    )
    return human_cart_item_read(item)


@router.post("/{cart_item_id}/cancel", response_model=HumanCartItemRead)
async def cancel_cart_item(
    cart_item_id: UUID,
    payload: CartCancellation,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> HumanCartItemRead:
    item = await owned_cart_item(db, user.id, cart_item_id, for_update=True)
    if item.status is not CartItemStatus.proposed:
        raise HTTPException(status_code=409, detail="Only proposed cart items can be cancelled")
    item.status = CartItemStatus.cancelled
    item.decision_note = payload.note
    item.cancelled_at = datetime.now(UTC)
    await db.commit()
    item = await load_cart_item(db, item.id)
    await broker.publish(
        "cart_item.cancelled", {"cart_item_id": item.id, "agent_id": item.agent_id}
    )
    return human_cart_item_read(item)


@router.post("/{cart_item_id}/credential/reveal", response_model=CredentialReveal)
async def reveal_cart_credential(
    cart_item_id: UUID,
    payload: CredentialRevealRequest,
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
) -> CredentialReveal:
    if not verify_password(payload.current_password.get_secret_value(), user.password_hash):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    item = await owned_cart_item(db, user.id, cart_item_id)
    password = decrypt_secret(item.credential.encrypted_password, settings)
    await broker.publish(
        "purchase_credential.revealed",
        {"cart_item_id": item.id, "owner_id": user.id},
    )
    return CredentialReveal(
        email=item.credential.email,
        password=password,
        login_url=item.credential.login_url,
    )
