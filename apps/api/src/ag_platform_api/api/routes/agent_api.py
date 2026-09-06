from datetime import UTC, datetime, timedelta
from typing import Annotated
from urllib.parse import urlsplit
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
    X402Client,
)
from ag_platform_api.api.routes.cart import load_cart_item
from ag_platform_api.core.config import LOCAL_DIRECT_CARD_PROVIDER
from ag_platform_api.core.security import (
    decrypt_secret,
    encrypt_secret,
    hash_opaque_token,
    new_opaque_token,
)
from ag_platform_api.models import (
    Agent,
    AgentPaymentMethod,
    AgentStatus,
    CartItem,
    CartItemStatus,
    CheckoutEvent,
    CheckoutExecutionStatus,
    PaymentMethod,
    PaymentMethodKind,
    PaymentMethodStatus,
    PaymentRuleSet,
    Purchase,
    PurchaseCredential,
    PurchaseStatus,
    Subscription,
    X402Payment,
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
    X402PaymentRequestCreate,
    X402ResultRead,
)
from ag_platform_api.services.checkout_queue import CheckoutQueueError, queue_checkout_execution
from ag_platform_api.services.payment_policies import requires_human_approval
from ag_platform_api.services.serializers import (
    cart_item_read,
    checkout_event_read,
    purchase_read,
    x402_transaction_evidence,
)
from ag_platform_api.services.x402 import (
    X402ReconciliationNotice,
    X402ServiceError,
    discover_x402_payment,
    queue_x402_execution,
    reconcile_stale_x402_submission,
    reconcile_stale_x402_submissions,
)

router = APIRouter(prefix="/agent", tags=["agent API"])


async def _publish_x402_reconciliations(
    broker: Broker,
    reconciliations: list[X402ReconciliationNotice],
) -> None:
    for reconciliation in reconciliations:
        await broker.publish(
            "checkout.outcome_unknown",
            {
                "execution_id": reconciliation.execution_id,
                "cart_item_id": reconciliation.cart_item_id,
                "agent_id": reconciliation.agent_id,
                "status": reconciliation.status.value,
                "payment_protocol": "x402",
                "reconciliation_reason": "response_deadline_exceeded",
            },
        )


async def _reconcile_agent_x402_reads(
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
    *,
    owner_id: UUID,
    agent_id: UUID,
    cart_item_id: UUID | None = None,
) -> None:
    if cart_item_id is None:
        reconciliations = await reconcile_stale_x402_submissions(
            db,
            owner_id=owner_id,
            agent_id=agent_id,
            settings=settings,
        )
    else:
        reconciliation = await reconcile_stale_x402_submission(
            db,
            owner_id=owner_id,
            agent_id=agent_id,
            cart_item_id=cart_item_id,
            settings=settings,
        )
        reconciliations = [reconciliation] if reconciliation is not None else []
    await _publish_x402_reconciliations(broker, reconciliations)


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
        select(PaymentRuleSet)
        .where(
            PaymentRuleSet.id == agent.payment_rule_set_id,
            PaymentRuleSet.owner_id == agent.owner_id,
        )
        .with_for_update()
    )
    total_amount = payload.unit_price * payload.quantity
    approval_required = requires_human_approval(
        policy,
        amount=total_amount,
        currency=payload.currency,
        recurring=payload.billing_period is not None,
    )
    candidate_payment_methods: list[PaymentMethod] = []
    if not approval_required:
        payment_method_query = (
            select(PaymentMethod)
            .join(AgentPaymentMethod)
            .where(
                AgentPaymentMethod.agent_id == agent.id,
                PaymentMethod.owner_id == agent.owner_id,
                PaymentMethod.status == PaymentMethodStatus.active,
                PaymentMethod.kind == PaymentMethodKind.card,
                PaymentMethod.provider != LOCAL_DIRECT_CARD_PROVIDER,
            )
            .order_by(AgentPaymentMethod.payment_method_id)
            .with_for_update()
        )
        candidate_payment_methods = list((await db.scalars(payment_method_query)).all())

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
        status=CartItemStatus.proposed,
        selected_payment_method_id=None,
        decision_note=None,
        approved_at=None,
    )
    db.add(item)
    await db.flush()

    selected_payment_method: PaymentMethod | None = None
    for candidate in candidate_payment_methods:
        item.status = CartItemStatus.approved
        item.selected_payment_method_id = candidate.id
        item.decision_note = "Automatically approved by the agent payment rule."
        item.approved_at = datetime.now(UTC)
        try:
            await queue_checkout_execution(
                db,
                item=item,
                payment_method=candidate,
                settings=settings,
            )
        except CheckoutQueueError:
            item.status = CartItemStatus.proposed
            item.selected_payment_method_id = None
            item.decision_note = None
            item.approved_at = None
            continue
        selected_payment_method = candidate
        break
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


@router.post(
    "/x402-payment-requests",
    response_model=CartItemRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_x402_payment_request(
    payload: X402PaymentRequestCreate,
    agent: CurrentAgent,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
    x402_client: X402Client,
) -> CartItemRead:
    if not settings.x402_enabled:
        raise HTTPException(status_code=404, detail="x402 payments are not enabled")
    preferred_networks = set(
        (
            await db.scalars(
                select(PaymentMethod.wallet_network)
                .join(AgentPaymentMethod)
                .where(
                    AgentPaymentMethod.agent_id == agent.id,
                    PaymentMethod.owner_id == agent.owner_id,
                    PaymentMethod.status == PaymentMethodStatus.active,
                    PaymentMethod.kind == PaymentMethodKind.wallet,
                )
            )
        ).all()
    )
    # End the authentication/read transaction before calling an untrusted remote resource.
    await db.commit()
    enabled_assets = [
        asset
        for asset in settings.x402_assets
        if asset.network != "eip155:8453" or settings.x402_mainnet_enabled
    ]
    try:
        discovered = await discover_x402_payment(
            x402_client,
            resource_url=str(payload.resource_url),
            assets=enabled_assets,
            preferred_networks={network for network in preferred_networks if network is not None},
        )
    except X402ServiceError as exc:
        detail = exc.safe_message
        if exc.code == "x402_asset_unsupported" and not settings.x402_mainnet_enabled:
            detail = (
                "The resource does not accept an enabled x402 asset; Base mainnet x402 is disabled"
            )
        status_code = 502 if exc.code in {"x402_transport_error", "x402_dns_failed"} else 409
        raise HTTPException(status_code=status_code, detail=detail) from exc

    policy = await db.scalar(
        select(PaymentRuleSet)
        .where(
            PaymentRuleSet.id == agent.payment_rule_set_id,
            PaymentRuleSet.owner_id == agent.owner_id,
        )
        .with_for_update()
    )
    approval_required = requires_human_approval(
        policy,
        amount=discovered.nominal_usd,
        currency="USD",
        recurring=False,
    )
    resource_url = str(payload.resource_url)
    host = urlsplit(resource_url).hostname or "x402 resource"
    item = CartItem(
        owner_id=agent.owner_id,
        agent_id=agent.id,
        credential_id=None,
        title=(discovered.description or f"x402 access at {host}")[:255],
        description=(discovered.description or "Access to an x402-protected resource.")[:10000],
        product_url=resource_url,
        checkout_adapter="x402",
        checkout_url=resource_url,
        merchant=payload.merchant or host,
        reason=payload.reason,
        quantity=1,
        unit_price=discovered.nominal_usd,
        currency="USD",
        billing_period=None,
        status=CartItemStatus.proposed,
        selected_payment_method_id=None,
        decision_note=None,
        approved_at=None,
    )
    db.add(item)
    await db.flush()
    x402_payment = X402Payment(
        cart_item_id=item.id,
        resource_url=resource_url,
        payment_required=discovered.payment_required,
        selected_requirements=discovered.selected_requirements,
        network=discovered.asset.network,
        asset=discovered.asset.asset,
        asset_symbol=discovered.asset.symbol,
        asset_decimals=discovered.asset.decimals,
        amount_atomic=discovered.amount_atomic,
        pay_to=discovered.pay_to,
        transfer_method=discovered.asset.transfer_method,
    )
    db.add(x402_payment)
    item.x402_payment = x402_payment

    selected_wallet: PaymentMethod | None = None
    if not approval_required:
        selected_wallet = await db.scalar(
            select(PaymentMethod)
            .join(AgentPaymentMethod)
            .where(
                AgentPaymentMethod.agent_id == agent.id,
                PaymentMethod.owner_id == agent.owner_id,
                PaymentMethod.status == PaymentMethodStatus.active,
                PaymentMethod.kind == PaymentMethodKind.wallet,
                PaymentMethod.wallet_network == discovered.asset.network,
            )
            .order_by(AgentPaymentMethod.payment_method_id)
            .limit(1)
            .with_for_update()
        )
        if selected_wallet is not None:
            item.status = CartItemStatus.approved
            item.selected_payment_method_id = selected_wallet.id
            item.decision_note = "Automatically approved by the agent payment rule."
            item.approved_at = datetime.now(UTC)
            try:
                await queue_x402_execution(db, item=item, payment_method=selected_wallet)
            except X402ServiceError:
                item.status = CartItemStatus.proposed
                item.selected_payment_method_id = None
                item.decision_note = None
                item.approved_at = None
                selected_wallet = None
    await db.commit()
    item = await load_cart_item(db, item.id)
    event_type = "cart_item.approved" if selected_wallet is not None else "cart_item.proposed"
    event_payload = {
        "cart_item_id": item.id,
        "agent_id": agent.id,
        "owner_id": agent.owner_id,
        "payment_protocol": "x402",
    }
    if selected_wallet is not None:
        event_payload["payment_method_id"] = selected_wallet.id
        event_payload["approval_source"] = "payment_policy"
    await broker.publish(event_type, event_payload)
    return cart_item_read(item)


@router.get("/cart-items", response_model=list[CartItemRead])
async def list_agent_cart_items(
    agent: CurrentAgent,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
    item_status: Annotated[CartItemStatus | None, Query(alias="status")] = None,
) -> list[CartItemRead]:
    await _reconcile_agent_x402_reads(
        db,
        settings,
        broker,
        owner_id=agent.owner_id,
        agent_id=agent.id,
    )
    query = (
        select(CartItem)
        .options(
            selectinload(CartItem.credential),
            selectinload(CartItem.x402_payment),
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
    settings: AppSettings,
    broker: Broker,
) -> CartItemRead:
    await _reconcile_agent_x402_reads(
        db,
        settings,
        broker,
        owner_id=agent.owner_id,
        agent_id=agent.id,
        cart_item_id=cart_item_id,
    )
    item = await db.scalar(
        select(CartItem)
        .options(
            selectinload(CartItem.credential),
            selectinload(CartItem.x402_payment),
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


@router.get(
    "/cart-items/{cart_item_id}/x402/result",
    response_model=X402ResultRead,
)
async def get_x402_result(
    cart_item_id: UUID,
    agent: CurrentAgent,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
) -> X402ResultRead:
    await _reconcile_agent_x402_reads(
        db,
        settings,
        broker,
        owner_id=agent.owner_id,
        agent_id=agent.id,
        cart_item_id=cart_item_id,
    )
    item = await db.scalar(
        select(CartItem)
        .options(
            selectinload(CartItem.x402_payment).selectinload(X402Payment.execution),
        )
        .where(
            CartItem.id == cart_item_id,
            CartItem.agent_id == agent.id,
            CartItem.owner_id == agent.owner_id,
            CartItem.checkout_adapter == "x402",
        )
    )
    if item is None or item.x402_payment is None:
        raise HTTPException(status_code=404, detail="x402 payment request not found")
    payment = item.x402_payment
    if payment.execution is None:
        raise HTTPException(
            status_code=409,
            detail="The x402 result is unavailable until the request is approved",
        )
    execution = payment.execution
    body: str | None = None
    body_encoding: str | None = None
    if (
        execution.status is CheckoutExecutionStatus.succeeded
        and payment.encrypted_response_body is not None
    ):
        body = decrypt_secret(payment.encrypted_response_body, settings)
        body_encoding = "base64"
    return X402ResultRead(
        cart_item_id=item.id,
        status=execution.status,
        mime_type=payment.response_mime_type if body is not None else None,
        body=body,
        body_encoding=body_encoding,
        transaction=x402_transaction_evidence(payment, execution),
        network=payment.network,
        asset=payment.asset,
    )


@router.get("/checkout-events", response_model=CheckoutEventPage)
async def list_checkout_events(
    agent: CurrentAgent,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
    after_cursor: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> CheckoutEventPage:
    await _reconcile_agent_x402_reads(
        db,
        settings,
        broker,
        owner_id=agent.owner_id,
        agent_id=agent.id,
    )
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
