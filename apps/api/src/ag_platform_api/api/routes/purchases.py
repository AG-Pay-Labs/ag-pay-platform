from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from ag_platform_api.api.dependencies import Broker, CurrentUser, DatabaseSession
from ag_platform_api.models import CartItem, Purchase, Subscription
from ag_platform_api.schemas import (
    PurchaseRead,
    SubscriptionRead,
    SubscriptionUpdate,
)
from ag_platform_api.services.serializers import purchase_read, subscription_read

router = APIRouter(tags=["purchases"])


def purchase_load_options():
    return (
        joinedload(Purchase.cart_item).joinedload(CartItem.credential),
        joinedload(Purchase.subscription),
    )


@router.get("/purchases", response_model=list[PurchaseRead])
async def list_purchases(
    user: CurrentUser,
    db: DatabaseSession,
) -> list[PurchaseRead]:
    purchases = (
        (
            await db.scalars(
                select(Purchase)
                .options(*purchase_load_options())
                .where(Purchase.owner_id == user.id)
                .order_by(Purchase.purchased_at.desc())
            )
        )
        .unique()
        .all()
    )
    return [purchase_read(purchase) for purchase in purchases]


@router.get("/purchases/{purchase_id}", response_model=PurchaseRead)
async def get_purchase(
    purchase_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
) -> PurchaseRead:
    purchase = await db.scalar(
        select(Purchase)
        .options(*purchase_load_options())
        .where(Purchase.id == purchase_id, Purchase.owner_id == user.id)
    )
    if purchase is None:
        raise HTTPException(status_code=404, detail="Purchase not found")
    return purchase_read(purchase)


@router.get("/subscriptions", response_model=list[SubscriptionRead])
async def list_subscriptions(
    user: CurrentUser,
    db: DatabaseSession,
) -> list[SubscriptionRead]:
    subscriptions = (
        (
            await db.scalars(
                select(Subscription)
                .options(joinedload(Subscription.purchase).joinedload(Purchase.cart_item))
                .where(Subscription.owner_id == user.id)
                .order_by(Subscription.created_at.desc())
            )
        )
        .unique()
        .all()
    )
    return [subscription_read(subscription) for subscription in subscriptions]


@router.patch("/subscriptions/{subscription_id}", response_model=SubscriptionRead)
async def update_subscription(
    subscription_id: UUID,
    payload: SubscriptionUpdate,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> SubscriptionRead:
    subscription = await db.scalar(
        select(Subscription)
        .options(selectinload(Subscription.purchase).selectinload(Purchase.cart_item))
        .where(Subscription.id == subscription_id, Subscription.owner_id == user.id)
        .with_for_update()
    )
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    subscription.status = payload.status
    subscription.next_billing_at = payload.next_billing_at
    await db.commit()
    await broker.publish(
        "subscription.updated",
        {"subscription_id": subscription.id, "status": subscription.status.value},
    )
    return subscription_read(subscription)
