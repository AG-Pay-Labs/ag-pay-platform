from datetime import UTC, datetime, timedelta
from uuid import UUID

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_keys.exceptions import BadSignature
from eth_utils import to_checksum_address
from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from ag_platform_api.api.dependencies import AppSettings, Broker, CurrentUser, DatabaseSession
from ag_platform_api.api.routes.agents import owned_agent
from ag_platform_api.core.config import LOCAL_DIRECT_CARD_PROVIDER
from ag_platform_api.core.security import new_opaque_token
from ag_platform_api.models import (
    AgentPaymentMethod,
    PaymentMethod,
    PaymentMethodKind,
    PaymentMethodStatus,
    StoredCardCredential,
    WalletConnectionChallenge,
)
from ag_platform_api.schemas import (
    DirectCardPaymentMethodCreate,
    PaymentMethodCreate,
    PaymentMethodRead,
    WalletChallengeCreate,
    WalletChallengeRead,
    WalletConfigRead,
    WalletPaymentMethodCreate,
    normalize_card_number,
)
from ag_platform_api.services.checkout.direct_card import DirectCardPanCipher, card_brand

router = APIRouter(tags=["payment methods"])

WALLET_NETWORKS = {
    "eip155:84532": {"chain_id": 84532, "name": "Base Sepolia", "is_testnet": True},
    "eip155:8453": {"chain_id": 8453, "name": "Base", "is_testnet": False},
}


async def owned_payment_method(
    db: DatabaseSession, owner_id: UUID, payment_method_id: UUID
) -> PaymentMethod:
    payment_method = await db.scalar(
        select(PaymentMethod).where(
            PaymentMethod.id == payment_method_id,
            PaymentMethod.owner_id == owner_id,
        )
    )
    if payment_method is None:
        raise HTTPException(status_code=404, detail="Payment method not found")
    return payment_method


@router.post(
    "/payment-methods",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_method(
    payload: PaymentMethodCreate,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> PaymentMethod:
    billing_details = payload.billing_details.model_dump(mode="json")
    payment_method = PaymentMethod(
        owner_id=user.id,
        kind=PaymentMethodKind.card,
        display_name=payload.display_name,
        provider=payload.provider,
        provider_payment_method_id=payload.provider_payment_method_id,
        card_brand=payload.card_brand.lower(),
        card_last4=payload.card_last4,
        expiry_month=payload.expiry_month,
        expiry_year=payload.expiry_year,
        billing_profile_type=payload.billing_details.type,
        billing_details=billing_details,
    )
    db.add(payment_method)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Payment method already exists") from exc
    await db.refresh(payment_method)
    await broker.publish(
        "payment_method.created", {"payment_method_id": payment_method.id, "owner_id": user.id}
    )
    return payment_method


@router.post(
    "/payment-methods/direct-card",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_direct_card_payment_method(
    payload: DirectCardPaymentMethodCreate,
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
    broker: Broker,
) -> PaymentMethod:
    if not settings.local_direct_card_enabled or settings.direct_card_encryption_key is None:
        raise HTTPException(status_code=404, detail="Local direct-card research is not enabled")
    number = normalize_card_number(payload.card_number)
    cipher = DirectCardPanCipher(settings.direct_card_encryption_key.get_secret_value())
    payment_method = PaymentMethod(
        owner_id=user.id,
        kind=PaymentMethodKind.card,
        display_name=payload.display_name,
        provider=LOCAL_DIRECT_CARD_PROVIDER,
        provider_payment_method_id=new_opaque_token("ldc"),
        card_brand=card_brand(number),
        card_last4=number[-4:],
        expiry_month=payload.expiry_month,
        expiry_year=payload.expiry_year,
        billing_profile_type=payload.billing_details.type,
        billing_details=payload.billing_details.model_dump(mode="json"),
    )
    db.add(payment_method)
    await db.flush()
    encrypted_pan = cipher.encrypt(
        number,
        owner_id=user.id,
        payment_method_id=payment_method.id,
        provider_card_id=payment_method.provider_payment_method_id,
    )
    db.add(
        StoredCardCredential(
            payment_method_id=payment_method.id,
            owner_id=user.id,
            encrypted_pan=encrypted_pan,
        )
    )
    del number, encrypted_pan
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Payment method already exists") from exc
    await db.refresh(payment_method)
    await broker.publish(
        "payment_method.created", {"payment_method_id": payment_method.id, "owner_id": user.id}
    )
    return payment_method


@router.get("/payment-methods/wallet-config", response_model=WalletConfigRead)
async def wallet_config(user: CurrentUser, settings: AppSettings) -> WalletConfigRead:
    del user
    configured_networks = {asset.network for asset in settings.x402_assets}
    return WalletConfigRead(
        providers=[{"id": "metamask", "display_name": "MetaMask"}],
        networks=[
            {
                "network": network,
                **details,
                "x402_enabled": settings.x402_enabled
                and network in configured_networks
                and (network != "eip155:8453" or settings.x402_mainnet_enabled),
            }
            for network, details in WALLET_NETWORKS.items()
        ],
    )


@router.post(
    "/payment-methods/wallet/challenge",
    response_model=WalletChallengeRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_wallet_challenge(
    payload: WalletChallengeCreate,
    user: CurrentUser,
    db: DatabaseSession,
    settings: AppSettings,
) -> WalletChallengeRead:
    network = WALLET_NETWORKS[payload.network]
    try:
        address = to_checksum_address(payload.address)
    except ValueError as exc:  # pragma: no cover - Pydantic rejects malformed lengths/hex first
        raise HTTPException(status_code=422, detail="Wallet address is invalid") from exc
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.wallet_challenge_ttl_seconds)
    nonce = new_opaque_token("wch")
    message = "\n".join(
        (
            "AG Pay wants you to connect your wallet:",
            address,
            "",
            "Signing this message proves wallet ownership. It does not initiate a transaction.",
            f"Provider: {payload.provider}",
            f"Network: {payload.network}",
            f"Chain ID: {network['chain_id']}",
            f"Account: {user.id}",
            f"Nonce: {nonce}",
            f"Issued At: {now.isoformat()}",
            f"Expiration Time: {expires_at.isoformat()}",
        )
    )
    challenge = WalletConnectionChallenge(
        owner_id=user.id,
        provider=payload.provider,
        address=address,
        address_normalized=address.lower(),
        network=payload.network,
        chain_id=int(network["chain_id"]),
        message=message,
        expires_at=expires_at,
    )
    db.add(challenge)
    await db.commit()
    return WalletChallengeRead(
        challenge_id=challenge.id,
        message=challenge.message,
        expires_at=challenge.expires_at,
    )


@router.post(
    "/payment-methods/wallet",
    response_model=PaymentMethodRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_wallet_payment_method(
    payload: WalletPaymentMethodCreate,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> PaymentMethod:
    challenge = await db.scalar(
        select(WalletConnectionChallenge)
        .where(
            WalletConnectionChallenge.id == payload.challenge_id,
            WalletConnectionChallenge.owner_id == user.id,
        )
        .with_for_update()
    )
    now = datetime.now(UTC)
    if challenge is None:
        raise HTTPException(status_code=404, detail="Wallet challenge not found")
    expires_at = (
        challenge.expires_at
        if challenge.expires_at.tzinfo
        else challenge.expires_at.replace(tzinfo=UTC)
    )
    if challenge.consumed_at is not None or expires_at <= now:
        raise HTTPException(status_code=409, detail="Wallet challenge is expired or already used")
    try:
        recovered = Account.recover_message(
            encode_defunct(text=challenge.message), signature=payload.signature
        )
    except (BadSignature, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Wallet signature is invalid") from exc
    if recovered.lower() != challenge.address_normalized:
        raise HTTPException(status_code=422, detail="Wallet signature does not match the address")

    challenge.consumed_at = now
    payment_method = PaymentMethod(
        owner_id=user.id,
        kind=PaymentMethodKind.wallet,
        display_name=payload.display_name,
        provider=challenge.provider,
        provider_payment_method_id=f"{challenge.network}:{challenge.address_normalized}",
        card_brand=None,
        card_last4=None,
        expiry_month=None,
        expiry_year=None,
        billing_profile_type=None,
        billing_details=None,
        wallet_address=challenge.address,
        wallet_address_normalized=challenge.address_normalized,
        wallet_network=challenge.network,
        wallet_chain_id=challenge.chain_id,
        wallet_verified_at=now,
    )
    db.add(payment_method)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Wallet is already connected") from exc
    await db.refresh(payment_method)
    await broker.publish(
        "payment_method.created",
        {
            "payment_method_id": payment_method.id,
            "owner_id": user.id,
            "kind": payment_method.kind.value,
        },
    )
    return payment_method


@router.get("/payment-methods", response_model=list[PaymentMethodRead])
async def list_payment_methods(
    user: CurrentUser,
    db: DatabaseSession,
) -> list[PaymentMethod]:
    return list(
        (
            await db.scalars(
                select(PaymentMethod)
                .where(PaymentMethod.owner_id == user.id)
                .order_by(PaymentMethod.created_at.desc())
            )
        ).all()
    )


@router.delete("/payment-methods/{payment_method_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_payment_method(
    payment_method_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> Response:
    payment_method = await owned_payment_method(db, user.id, payment_method_id)
    payment_method.status = PaymentMethodStatus.disabled
    await db.execute(
        delete(AgentPaymentMethod).where(AgentPaymentMethod.payment_method_id == payment_method.id)
    )
    if payment_method.provider == LOCAL_DIRECT_CARD_PROVIDER:
        await db.execute(
            delete(StoredCardCredential).where(
                StoredCardCredential.payment_method_id == payment_method.id,
                StoredCardCredential.owner_id == user.id,
            )
        )
    await db.commit()
    await broker.publish("payment_method.disabled", {"payment_method_id": payment_method.id})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/agents/{agent_id}/payment-methods/{payment_method_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def assign_payment_method(
    agent_id: UUID,
    payment_method_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> Response:
    await owned_agent(db, user.id, agent_id)
    payment_method = await owned_payment_method(db, user.id, payment_method_id)
    if payment_method.status is not PaymentMethodStatus.active:
        raise HTTPException(status_code=409, detail="Payment method is disabled")
    existing = await db.get(
        AgentPaymentMethod,
        {"agent_id": agent_id, "payment_method_id": payment_method_id},
    )
    if existing is None:
        db.add(AgentPaymentMethod(agent_id=agent_id, payment_method_id=payment_method_id))
        await db.commit()
        await broker.publish(
            "agent.payment_method_assigned",
            {"agent_id": agent_id, "payment_method_id": payment_method_id},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/agents/{agent_id}/payment-methods/{payment_method_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unassign_payment_method(
    agent_id: UUID,
    payment_method_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
    broker: Broker,
) -> Response:
    await owned_agent(db, user.id, agent_id)
    await owned_payment_method(db, user.id, payment_method_id)
    await db.execute(
        delete(AgentPaymentMethod).where(
            AgentPaymentMethod.agent_id == agent_id,
            AgentPaymentMethod.payment_method_id == payment_method_id,
        )
    )
    await db.commit()
    await broker.publish(
        "agent.payment_method_unassigned",
        {"agent_id": agent_id, "payment_method_id": payment_method_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/agents/{agent_id}/payment-methods", response_model=list[PaymentMethodRead])
async def list_agent_payment_methods(
    agent_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
) -> list[PaymentMethod]:
    await owned_agent(db, user.id, agent_id)
    payment_methods = (
        await db.scalars(
            select(PaymentMethod)
            .join(AgentPaymentMethod)
            .where(
                AgentPaymentMethod.agent_id == agent_id,
                PaymentMethod.owner_id == user.id,
            )
            .order_by(PaymentMethod.created_at.desc())
        )
    ).all()
    return list(payment_methods)
