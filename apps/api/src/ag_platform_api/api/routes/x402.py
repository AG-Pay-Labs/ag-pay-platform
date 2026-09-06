from __future__ import annotations

import base64
import hashlib
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ag_platform_api.api.dependencies import (
    AppSettings,
    Broker,
    CurrentUser,
    DatabaseSession,
    X402Client,
)
from ag_platform_api.api.routes.cart import load_cart_item
from ag_platform_api.core.security import encrypt_secret
from ag_platform_api.models import (
    AgentPaymentMethod,
    CartItem,
    CartItemStatus,
    CheckoutEvent,
    CheckoutExecution,
    CheckoutExecutionStatus,
    CheckoutStatusTransition,
    PaymentMethod,
    PaymentMethodKind,
    PaymentMethodStatus,
    Purchase,
    PurchaseStatus,
    X402Payment,
)
from ag_platform_api.schemas import (
    HumanCartItemRead,
    X402AuthorizeCreate,
    X402SigningRequestRead,
)
from ag_platform_api.services.serializers import human_cart_item_read
from ag_platform_api.services.x402 import (
    PAYMENT_SIGNATURE_HEADER,
    X402HttpResponse,
    X402ServiceError,
    ensure_x402_submission_enabled,
    parse_settlement_response,
    payment_signature_header,
    validate_payment_payload,
)

router = APIRouter(prefix="/cart-items", tags=["x402"])
TRANSACTION_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")


async def _load_x402_item(
    db: DatabaseSession,
    *,
    owner_id: UUID,
    cart_item_id: UUID,
    for_update: bool,
) -> CartItem:
    query = (
        select(CartItem)
        .options(
            selectinload(CartItem.credential),
            selectinload(CartItem.x402_payment),
            selectinload(CartItem.checkout_execution).selectinload(
                CheckoutExecution.status_transitions
            ),
        )
        .where(
            CartItem.id == cart_item_id,
            CartItem.owner_id == owner_id,
            CartItem.checkout_adapter == "x402",
        )
    )
    if for_update:
        query = query.with_for_update()
    item = await db.scalar(query)
    if item is None or item.x402_payment is None or item.checkout_execution is None:
        raise HTTPException(status_code=404, detail="x402 payment execution not found")
    return item


async def _active_wallet(
    db: DatabaseSession,
    *,
    item: CartItem,
    for_update: bool,
) -> PaymentMethod:
    if item.selected_payment_method_id is None:
        raise HTTPException(status_code=409, detail="The x402 request has no selected wallet")
    query = select(PaymentMethod).where(
        PaymentMethod.id == item.selected_payment_method_id,
        PaymentMethod.owner_id == item.owner_id,
        PaymentMethod.status == PaymentMethodStatus.active,
        PaymentMethod.kind == PaymentMethodKind.wallet,
    )
    if for_update:
        query = query.with_for_update()
    wallet = await db.scalar(query)
    assignment_query = select(AgentPaymentMethod).where(
        AgentPaymentMethod.agent_id == item.agent_id,
        AgentPaymentMethod.payment_method_id == item.selected_payment_method_id,
    )
    if for_update:
        assignment_query = assignment_query.with_for_update()
    assignment = await db.scalar(assignment_query)
    if (
        wallet is None
        or assignment is None
        or wallet.wallet_address is None
        or wallet.wallet_address_normalized is None
        or wallet.wallet_network != item.x402_payment.network
        or item.checkout_execution.payment_method_id != wallet.id
    ):
        raise HTTPException(
            status_code=409,
            detail="The approved wallet is no longer active and assigned on this network",
        )
    return wallet


async def _submitted_wallet(
    db: DatabaseSession,
    *,
    item: CartItem,
    execution: CheckoutExecution,
) -> PaymentMethod | None:
    """Load the immutable signed payer after submission without reapplying eligibility."""
    return await db.scalar(
        select(PaymentMethod)
        .where(
            PaymentMethod.id == execution.payment_method_id,
            PaymentMethod.owner_id == item.owner_id,
            PaymentMethod.kind == PaymentMethodKind.wallet,
            PaymentMethod.wallet_network == item.x402_payment.network,
            PaymentMethod.wallet_address_normalized.is_not(None),
        )
        .with_for_update()
    )


@router.get("/{cart_item_id}/x402/signing-request", response_model=X402SigningRequestRead)
async def get_x402_signing_request(
    cart_item_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> X402SigningRequestRead:
    item = await _load_x402_item(db, owner_id=user.id, cart_item_id=cart_item_id, for_update=False)
    if (
        item.status is not CartItemStatus.approved
        or item.checkout_execution.status is not CheckoutExecutionStatus.awaiting_signature
    ):
        raise HTTPException(status_code=409, detail="This x402 payment is not awaiting a signature")
    try:
        ensure_x402_submission_enabled(item.x402_payment, settings)
    except X402ServiceError as exc:
        raise HTTPException(status_code=409, detail=exc.safe_message) from exc
    wallet = await _active_wallet(db, item=item, for_update=False)
    return X402SigningRequestRead(
        payment_required=item.x402_payment.payment_required,
        wallet=wallet,
    )


def _transition(
    execution: CheckoutExecution,
    status: CheckoutExecutionStatus,
    *,
    error_code: str | None = None,
    occurred_at: datetime | None = None,
) -> CheckoutStatusTransition:
    return CheckoutStatusTransition(
        execution_id=execution.id,
        status=status,
        attempt_count=execution.attempt_count,
        error_code=error_code,
        occurred_at=occurred_at or datetime.now(UTC),
    )


def _event(
    execution: CheckoutExecution,
    *,
    status: CheckoutExecutionStatus,
    purchase_id: UUID | None,
    error_code: str | None,
) -> CheckoutEvent:
    return CheckoutEvent(
        execution_id=execution.id,
        owner_id=execution.owner_id,
        agent_id=execution.agent_id,
        cart_item_id=execution.cart_item_id,
        purchase_id=purchase_id,
        status=status,
        amount=execution.approved_amount,
        currency=execution.currency,
        error_code=error_code,
    )


def _classify_paid_response(
    response: X402HttpResponse,
    *,
    payment: X402Payment,
    wallet: PaymentMethod,
) -> tuple[Literal["succeeded", "failed", "outcome_unknown"], dict | None, str | None]:
    try:
        settlement = parse_settlement_response(response)
    except X402ServiceError:
        return "outcome_unknown", None, None
    receipt = settlement.model_dump(mode="json", by_alias=True, exclude_none=True)
    transaction = (
        settlement.transaction.lower()
        if isinstance(settlement.transaction, str)
        and TRANSACTION_HASH.fullmatch(settlement.transaction) is not None
        else None
    )
    receipt_matches_payment = (
        settlement.network == payment.network
        and settlement.payer is not None
        and settlement.payer.lower() == wallet.wallet_address_normalized
        and (settlement.amount is None or settlement.amount == payment.amount_atomic)
    )
    if not receipt_matches_payment:
        return "outcome_unknown", receipt, None
    if not settlement.success:
        if settlement.error_reason == "settlement_pending":
            return "outcome_unknown", receipt, transaction
        return "failed", receipt, transaction
    valid_receipt = 200 <= response.status_code < 300 and transaction is not None
    if not valid_receipt:
        return "outcome_unknown", receipt, transaction
    return "succeeded", receipt, transaction


async def _finalize(
    db: DatabaseSession,
    *,
    user_id: UUID,
    cart_item_id: UUID,
    response: X402HttpResponse | None,
    transport_error: X402ServiceError | None,
    settings: AppSettings,
) -> tuple[CartItem, CheckoutExecutionStatus]:
    # The paid request runs outside the database transaction. Discard identity-map
    # state so a concurrent one-way deadline reconciliation cannot be overwritten.
    db.expire_all()
    item = await _load_x402_item(db, owner_id=user_id, cart_item_id=cart_item_id, for_update=True)
    execution = await db.scalar(
        select(CheckoutExecution)
        .where(
            CheckoutExecution.id == item.checkout_execution.id,
            CheckoutExecution.owner_id == user_id,
        )
        .with_for_update()
    )
    payment = await db.scalar(
        select(X402Payment).where(X402Payment.cart_item_id == item.id).with_for_update()
    )
    if (
        execution is None
        or payment is None
        or execution.status is not CheckoutExecutionStatus.submitted
    ):
        raise HTTPException(status_code=409, detail="The x402 payment is no longer submitted")

    # Eligibility was frozen before the signature and rechecked before durable
    # submission. Disabling or unassigning afterward must not erase a valid receipt.
    wallet = await _submitted_wallet(db, item=item, execution=execution)
    outcome: Literal["succeeded", "failed", "outcome_unknown"]
    receipt: dict | None = None
    transaction: str | None = None
    if transport_error is not None or response is None or wallet is None:
        outcome = "outcome_unknown"
    else:
        outcome, receipt, transaction = _classify_paid_response(
            response, payment=payment, wallet=wallet
        )

    now = datetime.now(UTC)
    payment.response_status_code = response.status_code if response is not None else None
    payment.payment_response = receipt
    payment.transaction = transaction
    purchase_id: UUID | None = None
    error_code: str | None = None
    if outcome == "succeeded" and response is not None and transaction is not None:
        mime_type = response.headers.get("content-type", "application/octet-stream")
        if any(ord(character) < 32 for character in mime_type) or len(mime_type) > 255:
            mime_type = "application/octet-stream"
        purchase = Purchase(
            owner_id=execution.owner_id,
            agent_id=execution.agent_id,
            payment_method_id=execution.payment_method_id,
            cart_item_id=item.id,
            status=PurchaseStatus.completed,
            amount=execution.approved_amount,
            currency=execution.currency,
            provider_reference=transaction,
            receipt_url=None,
            purchased_at=now,
        )
        db.add(purchase)
        await db.flush()
        purchase_id = purchase.id
        item.status = CartItemStatus.purchased
        payment.response_mime_type = mime_type
        payment.encrypted_response_body = encrypt_secret(
            base64.b64encode(response.content).decode("ascii"), settings
        )
    if outcome == "succeeded":
        execution.status = CheckoutExecutionStatus.succeeded
        execution.error_code = None
        execution.error_message = None
    elif outcome == "failed":
        execution.status = CheckoutExecutionStatus.failed
        error_code = "payment_declined"
        execution.error_code = error_code
        execution.error_message = "The x402 resource rejected the payment."
    else:
        execution.status = CheckoutExecutionStatus.outcome_unknown
        error_code = "payment_outcome_unknown"
        execution.error_code = error_code
        execution.error_message = (
            "The x402 payment outcome is unknown and must not be retried automatically."
        )
    execution.completed_at = now
    db.add(_transition(execution, execution.status, error_code=error_code, occurred_at=now))
    db.add(
        _event(
            execution,
            status=execution.status,
            purchase_id=purchase_id,
            error_code=error_code,
        )
    )
    item_id = item.id
    terminal_status = execution.status
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="The x402 payment was already recorded"
        ) from exc
    db.expire_all()
    return await load_cart_item(db, item_id), terminal_status


@router.post("/{cart_item_id}/x402/authorize", response_model=HumanCartItemRead)
async def authorize_x402_payment(
    cart_item_id: UUID,
    payload: X402AuthorizeCreate,
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
    x402_client: X402Client,
) -> HumanCartItemRead:
    if not settings.x402_enabled:
        raise HTTPException(status_code=409, detail="x402 payment submission is disabled")
    item = await _load_x402_item(db, owner_id=user.id, cart_item_id=cart_item_id, for_update=True)
    execution = await db.scalar(
        select(CheckoutExecution)
        .where(
            CheckoutExecution.id == item.checkout_execution.id,
            CheckoutExecution.owner_id == user.id,
        )
        .with_for_update()
    )
    payment = await db.scalar(
        select(X402Payment).where(X402Payment.cart_item_id == item.id).with_for_update()
    )
    if (
        execution is None
        or payment is None
        or item.status is not CartItemStatus.approved
        or execution.status is not CheckoutExecutionStatus.awaiting_signature
        or payment.execution_id != execution.id
        or payment.payment_signature_hash is not None
    ):
        raise HTTPException(status_code=409, detail="This x402 payment is not awaiting a signature")
    try:
        ensure_x402_submission_enabled(payment, settings)
    except X402ServiceError as exc:
        raise HTTPException(status_code=409, detail=exc.safe_message) from exc
    wallet = await _active_wallet(db, item=item, for_update=True)
    try:
        validated = validate_payment_payload(
            payload.payment_payload,
            payment_required=payment.payment_required,
            requirements=payment.selected_requirements,
            wallet_address=wallet.wallet_address or "",
            transfer_method=payment.transfer_method,
        )
        header = payment_signature_header(validated)
    except X402ServiceError as exc:
        raise HTTPException(status_code=422, detail=exc.safe_message) from exc

    signature_hash = hashlib.sha256(header.encode()).hexdigest()
    now = datetime.now(UTC)
    execution.adapter_config = {
        **execution.adapter_config,
        "httpTimeoutSeconds": settings.x402_http_timeout_seconds,
    }
    execution.status = CheckoutExecutionStatus.authorized
    db.add(_transition(execution, CheckoutExecutionStatus.authorized, occurred_at=now))
    execution.status = CheckoutExecutionStatus.submitted
    execution.attempt_count = 1
    execution.submitted_at = now
    payment.payment_signature_hash = signature_hash
    db.add(_transition(execution, CheckoutExecutionStatus.submitted, occurred_at=now))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="This x402 authorization was already used"
        ) from exc

    response: X402HttpResponse | None = None
    transport_error: X402ServiceError | None = None
    try:
        response = await x402_client.get(
            payment.resource_url,
            headers={PAYMENT_SIGNATURE_HEADER: header},
        )
    except X402ServiceError as exc:
        transport_error = exc

    finalized, terminal_status = await _finalize(
        db,
        user_id=user.id,
        cart_item_id=item.id,
        response=response,
        transport_error=transport_error,
        settings=settings,
    )
    await broker.publish(
        f"checkout.{terminal_status.value}",
        {
            "execution_id": finalized.checkout_execution.id,
            "cart_item_id": finalized.id,
            "agent_id": finalized.agent_id,
            "status": terminal_status.value,
            "payment_protocol": "x402",
        },
    )
    return human_cart_item_read(finalized)
