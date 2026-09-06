from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_keys.exceptions import BadSignature
from eth_utils import to_checksum_address
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from x402.http.utils import (
    decode_payment_required_header,
    decode_payment_response_header,
    encode_payment_signature_header,
)
from x402.schemas import PaymentPayload, PaymentRequired, SettleResponse

from ag_platform_api.core.config import Settings, X402AssetSettings
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
    X402Payment,
)

PAYMENT_REQUIRED_HEADER = "payment-required"
PAYMENT_SIGNATURE_HEADER = "PAYMENT-SIGNATURE"
PAYMENT_RESPONSE_HEADER = "payment-response"
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
X402_EXACT_PERMIT2_PROXY_ADDRESS = "0x402085c248EeA27D92E8b30b2C58ed07f9E20001"
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
EVM_SIGNATURE = re.compile(r"^0x[0-9a-fA-F]{130}$")
BYTES32 = re.compile(r"^0x[0-9a-fA-F]{64}$")
ASCII_ATOMIC_AMOUNT = re.compile(r"^[0-9]{1,78}$")


@dataclass(frozen=True, slots=True)
class X402ServiceError(Exception):
    code: str
    safe_message: str

    def __str__(self) -> str:
        return self.safe_message


@dataclass(frozen=True, slots=True)
class X402HttpResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes


class X402HttpClient(Protocol):
    async def get(
        self, resource_url: str, *, headers: Mapping[str, str] | None = None
    ) -> X402HttpResponse: ...


@dataclass(frozen=True, slots=True)
class PinnedX402Target:
    request_url: str
    host_header: str
    sni_hostname: str
    peer_ip: ipaddress.IPv4Address | ipaddress.IPv6Address


class HttpxX402Client:
    """One-attempt, redirect-free client for public HTTPS x402 resources."""

    def __init__(self, *, timeout_seconds: float, max_response_bytes: int) -> None:
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )
        self._max_response_bytes = max_response_bytes

    async def close(self) -> None:
        await self._client.aclose()

    async def get(
        self, resource_url: str, *, headers: Mapping[str, str] | None = None
    ) -> X402HttpResponse:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                target = await validate_public_https_url(resource_url)
                request_headers = {
                    key: value for key, value in (headers or {}).items() if key.lower() != "host"
                }
                request_headers["Host"] = target.host_header
                async with self._client.stream(
                    "GET",
                    target.request_url,
                    headers=request_headers,
                    extensions={"sni_hostname": target.sni_hostname},
                ) as response:
                    network_stream = response.extensions.get("network_stream")
                    peer = (
                        network_stream.get_extra_info("server_addr")
                        if network_stream is not None
                        else None
                    )
                    if (
                        not isinstance(peer, tuple)
                        or not peer
                        or ipaddress.ip_address(peer[0]) != target.peer_ip
                    ):
                        raise X402ServiceError(
                            "x402_peer_mismatch",
                            "The x402 resource connection did not use its validated public peer",
                        )
                    if response.is_redirect:
                        raise X402ServiceError(
                            "x402_redirect_rejected", "x402 resources cannot redirect"
                        )
                    length = response.headers.get("content-length")
                    if length is not None and int(length) > self._max_response_bytes:
                        raise X402ServiceError(
                            "x402_response_too_large", "The x402 resource response is too large"
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > self._max_response_bytes:
                            raise X402ServiceError(
                                "x402_response_too_large",
                                "The x402 resource response is too large",
                            )
                    return X402HttpResponse(
                        status_code=response.status_code,
                        headers={key.lower(): value for key, value in response.headers.items()},
                        content=bytes(content),
                    )
        except X402ServiceError:
            raise
        except (TimeoutError, httpx.HTTPError, ValueError) as exc:
            raise X402ServiceError(
                "x402_transport_error", "The x402 resource could not be reached safely"
            ) from exc


async def validate_public_https_url(resource_url: str) -> PinnedX402Target:
    parsed = urlsplit(resource_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise X402ServiceError("x402_url_invalid", "The x402 resource URL is invalid") from exc
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise X402ServiceError(
            "x402_url_invalid", "x402 resources must use a public HTTPS URL on port 443"
        )
    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        raise X402ServiceError("x402_url_private", "Private x402 resource hosts are not allowed")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise X402ServiceError("x402_url_invalid", "The x402 resource URL is invalid") from exc
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            ascii_host,
            port or 443,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise X402ServiceError(
            "x402_dns_failed", "The x402 resource host could not be resolved"
        ) from exc
    if not addresses:
        raise X402ServiceError("x402_dns_failed", "The x402 resource host could not be resolved")
    validated_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise X402ServiceError(
                "x402_url_private", "Private x402 resource hosts are not allowed"
            )
        if ip not in validated_addresses:
            validated_addresses.append(ip)
    selected_ip = validated_addresses[0]
    pinned_host = f"[{selected_ip}]" if selected_ip.version == 6 else str(selected_ip)
    pinned_url = parsed._replace(netloc=pinned_host).geturl()
    host_header = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    if port == 443:
        host_header = f"{host_header}:443"
    return PinnedX402Target(
        request_url=pinned_url,
        host_header=host_header,
        sni_hostname=ascii_host,
        peer_ip=selected_ip,
    )


@dataclass(frozen=True, slots=True)
class DiscoveredX402Payment:
    payment_required: dict[str, Any]
    selected_requirements: dict[str, Any]
    asset: X402AssetSettings
    amount_atomic: str
    nominal_usd: Decimal
    pay_to: str
    description: str | None
    mime_type: str | None


@dataclass(frozen=True, slots=True)
class X402ReconciliationNotice:
    execution_id: UUID
    cart_item_id: UUID
    agent_id: UUID
    owner_id: UUID
    status: CheckoutExecutionStatus


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    return next((value for key, value in headers.items() if key.lower() == target), None)


def _normalize_address(value: str, *, field: str) -> str:
    if EVM_ADDRESS.fullmatch(value) is None:
        raise X402ServiceError("x402_invalid_requirements", f"x402 {field} is invalid")
    try:
        return to_checksum_address(value)
    except ValueError as exc:
        raise X402ServiceError("x402_invalid_requirements", f"x402 {field} is invalid") from exc


def _asset_lookup(
    assets: list[X402AssetSettings], network: str, asset_address: str
) -> X402AssetSettings | None:
    return next(
        (
            asset
            for asset in assets
            if asset.network == network and asset.asset.lower() == asset_address.lower()
        ),
        None,
    )


def ensure_x402_submission_enabled(payment: X402Payment, settings: Settings) -> None:
    if not settings.x402_enabled:
        raise X402ServiceError("x402_disabled", "x402 payment submission is disabled")
    if payment.network == "eip155:8453" and not settings.x402_mainnet_enabled:
        raise X402ServiceError(
            "x402_mainnet_disabled", "Base mainnet x402 payment submission is disabled"
        )
    configured = _asset_lookup(settings.x402_assets, payment.network, payment.asset)
    requirements = payment.selected_requirements
    extra = requirements.get("extra") if isinstance(requirements, dict) else None
    try:
        amount = (
            int(payment.amount_atomic)
            if ASCII_ATOMIC_AMOUNT.fullmatch(payment.amount_atomic) is not None
            else 0
        )
    except (TypeError, ValueError):
        amount = 0
    persisted_requirement_matches = (
        isinstance(requirements, dict)
        and isinstance(extra, dict)
        and requirements.get("scheme") == "exact"
        and requirements.get("network") == payment.network
        and str(requirements.get("asset", "")).lower() == payment.asset.lower()
        and requirements.get("amount") == payment.amount_atomic
        and str(requirements.get("payTo", "")).lower() == payment.pay_to.lower()
    )
    configured_matches = (
        configured is not None
        and isinstance(extra, dict)
        and configured.symbol == payment.asset_symbol
        and configured.decimals == payment.asset_decimals
        and configured.transfer_method == payment.transfer_method
        and extra.get("assetTransferMethod", "eip3009") == configured.transfer_method
        and (extra.get("decimals") is None or extra.get("decimals") == configured.decimals)
        and (
            (
                configured.transfer_method == "eip3009"
                and extra.get("name") == configured.name
                and extra.get("version") == configured.version
            )
            or (
                configured.transfer_method == "permit2"
                and (extra.get("name") is None or extra.get("name") == configured.name)
                and (extra.get("version") is None or extra.get("version") == configured.version)
            )
        )
        and 0 < amount <= int(configured.max_amount_atomic)
    )
    if not persisted_requirement_matches or not configured_matches:
        raise X402ServiceError(
            "x402_asset_disabled",
            "This x402 asset is no longer enabled for payment submission",
        )


async def discover_x402_payment(
    client: X402HttpClient,
    *,
    resource_url: str,
    assets: list[X402AssetSettings],
    preferred_networks: set[str],
) -> DiscoveredX402Payment:
    response = await client.get(resource_url)
    if response.status_code != 402:
        raise X402ServiceError(
            "x402_payment_not_required", "The resource did not request an x402 payment"
        )
    header = _header(response.headers, PAYMENT_REQUIRED_HEADER)
    if header is None or len(header) > 65_536:
        raise X402ServiceError(
            "x402_payment_required_missing", "The resource returned no valid PAYMENT-REQUIRED"
        )
    try:
        raw_required = json.loads(base64.b64decode(header, validate=True))
        if (
            not isinstance(raw_required, dict)
            or raw_required.get("x402Version") != 2
            or not set(raw_required).issubset(
                {"x402Version", "error", "resource", "accepts", "extensions"}
            )
            or not isinstance(raw_required.get("accepts"), list)
            or not 1 <= len(raw_required["accepts"]) <= 50
        ):
            raise ValueError("invalid x402 v2 envelope")
        decoded = decode_payment_required_header(header)
    except (
        binascii.Error,
        json.JSONDecodeError,
        TypeError,
        UnicodeDecodeError,
        ValidationError,
        ValueError,
    ) as exc:
        raise X402ServiceError(
            "x402_payment_required_invalid", "The resource returned invalid x402 requirements"
        ) from exc
    if not isinstance(decoded, PaymentRequired) or decoded.x402_version != 2:
        raise X402ServiceError(
            "x402_version_unsupported", "Only x402 protocol version 2 is supported"
        )
    if decoded.extensions:
        raise X402ServiceError(
            "x402_extensions_unsupported",
            "x402 payment extensions are not supported in this release",
        )
    if decoded.resource is None or decoded.resource.url != resource_url:
        raise X402ServiceError(
            "x402_resource_mismatch", "The x402 requirements reference a different resource"
        )
    description = decoded.resource.description
    if description is not None and (
        len(description) > 10_000 or any(ord(character) < 32 for character in description)
    ):
        raise X402ServiceError("x402_resource_invalid", "The x402 resource description is invalid")

    candidates: list[tuple[int, int, Any, X402AssetSettings, str, Decimal, str]] = []
    for requirement in decoded.accepts:
        if requirement.scheme != "exact":
            continue
        asset = _asset_lookup(assets, str(requirement.network), requirement.asset)
        if asset is None:
            continue
        if not set(requirement.extra).issubset(
            {"name", "version", "decimals", "assetTransferMethod"}
        ):
            continue
        transfer_method = requirement.extra.get("assetTransferMethod", "eip3009")
        if transfer_method != asset.transfer_method:
            continue
        advertised_name = requirement.extra.get("name")
        advertised_version = requirement.extra.get("version")
        if asset.transfer_method == "eip3009":
            if advertised_name != asset.name or advertised_version != asset.version:
                continue
        elif (advertised_name is not None and advertised_name != asset.name) or (
            advertised_version is not None and advertised_version != asset.version
        ):
            continue
        advertised_decimals = requirement.extra.get("decimals")
        if advertised_decimals is not None and advertised_decimals != asset.decimals:
            continue
        if ASCII_ATOMIC_AMOUNT.fullmatch(requirement.amount) is None:
            continue
        amount = int(requirement.amount)
        if amount <= 0:
            continue
        if amount > int(asset.max_amount_atomic):
            continue
        nominal = Decimal(amount) / (Decimal(10) ** asset.decimals)
        if nominal <= 0 or nominal >= Decimal(10) ** 20:
            continue
        try:
            pay_to = _normalize_address(requirement.pay_to, field="recipient")
        except X402ServiceError:
            continue
        if not 1 <= requirement.max_timeout_seconds <= 3600:
            continue
        configured_order = assets.index(asset)
        preferred_order = 0 if requirement.network in preferred_networks else 1
        candidates.append(
            (preferred_order, configured_order, requirement, asset, str(amount), nominal, pay_to)
        )
    if not candidates:
        raise X402ServiceError(
            "x402_asset_unsupported",
            "The resource does not accept a configured Base x402 asset",
        )
    _, _, selected, asset, amount_atomic, nominal_usd, pay_to = min(
        candidates, key=lambda candidate: candidate[:2]
    )
    selected_dict = selected.model_dump(mode="json", by_alias=True, exclude_none=True)
    payment_required = decoded.model_copy(update={"accepts": [selected]}).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    return DiscoveredX402Payment(
        payment_required=payment_required,
        selected_requirements=selected_dict,
        asset=asset,
        amount_atomic=amount_atomic,
        nominal_usd=nominal_usd,
        pay_to=pay_to,
        description=decoded.resource.description if decoded.resource else None,
        mime_type=decoded.resource.mime_type if decoded.resource else None,
    )


async def queue_x402_execution(
    db: AsyncSession,
    *,
    item: CartItem,
    payment_method: PaymentMethod,
) -> CheckoutExecution:
    if item.checkout_adapter != "x402" or item.checkout_url is None:
        raise X402ServiceError("x402_request_invalid", "This cart item is not an x402 request")
    if (
        payment_method.kind is not PaymentMethodKind.wallet
        or payment_method.status is not PaymentMethodStatus.active
        or payment_method.owner_id != item.owner_id
        or payment_method.wallet_network is None
        or item.status is not CartItemStatus.approved
        or item.selected_payment_method_id != payment_method.id
    ):
        raise X402ServiceError(
            "x402_wallet_invalid", "An active connected wallet is required for x402"
        )
    x402_payment = await db.scalar(
        select(X402Payment).where(X402Payment.cart_item_id == item.id).with_for_update()
    )
    if x402_payment is None:
        raise X402ServiceError("x402_request_invalid", "The x402 payment request is incomplete")
    if payment_method.wallet_network != x402_payment.network:
        raise X402ServiceError(
            "x402_network_mismatch", "The selected wallet is connected to a different network"
        )
    assignment = await db.scalar(
        select(AgentPaymentMethod)
        .where(
            AgentPaymentMethod.agent_id == item.agent_id,
            AgentPaymentMethod.payment_method_id == payment_method.id,
        )
        .with_for_update()
    )
    if assignment is None:
        raise X402ServiceError(
            "x402_wallet_unassigned", "The selected wallet is not assigned to this agent"
        )
    existing = await db.scalar(
        select(CheckoutExecution).where(CheckoutExecution.cart_item_id == item.id).with_for_update()
    )
    if existing is not None:
        if existing.payment_method_id != payment_method.id or existing.adapter_key != "x402":
            raise X402ServiceError(
                "x402_execution_conflict", "This cart item already has another execution"
            )
        return existing
    origin = urlsplit(item.checkout_url)
    execution = CheckoutExecution(
        owner_id=item.owner_id,
        agent_id=item.agent_id,
        payment_method_id=payment_method.id,
        cart_item_id=item.id,
        adapter_key="x402",
        adapter_config={
            "x402Version": 2,
            "network": x402_payment.network,
            "asset": x402_payment.asset,
            "transferMethod": x402_payment.transfer_method,
        },
        approved_amount=item.unit_price * item.quantity,
        currency=item.currency,
        checkout_origin=f"https://{origin.netloc}",
        status=CheckoutExecutionStatus.awaiting_signature,
    )
    db.add(execution)
    await db.flush()
    x402_payment.execution_id = execution.id
    db.add(
        CheckoutStatusTransition(
            execution_id=execution.id,
            status=CheckoutExecutionStatus.awaiting_signature,
            attempt_count=0,
        )
    )
    item.checkout_execution = execution
    return execution


async def reconcile_stale_x402_submission(
    db: AsyncSession,
    *,
    owner_id: UUID,
    agent_id: UUID | None,
    cart_item_id: UUID,
    settings: Settings,
) -> X402ReconciliationNotice | None:
    """Conservatively close a timed-out submission without sending another request."""
    candidate_conditions = [
        CheckoutExecution.cart_item_id == cart_item_id,
        CheckoutExecution.owner_id == owner_id,
        CheckoutExecution.adapter_key == "x402",
        CheckoutExecution.status == CheckoutExecutionStatus.submitted,
        CheckoutExecution.submitted_at.is_not(None),
        CartItem.owner_id == owner_id,
        CartItem.checkout_adapter == "x402",
    ]
    if agent_id is not None:
        candidate_conditions.extend(
            (
                CheckoutExecution.agent_id == agent_id,
                CartItem.agent_id == agent_id,
            )
        )
    candidate = (
        await db.execute(
            select(CheckoutExecution.id, CheckoutExecution.agent_id)
            .join(CartItem, CartItem.id == CheckoutExecution.cart_item_id)
            .join(X402Payment, X402Payment.execution_id == CheckoutExecution.id)
            .where(*candidate_conditions)
        )
    ).one_or_none()
    if candidate is None:
        return None
    execution_id, resolved_agent_id = candidate
    locked_item_id = await db.scalar(
        select(CartItem.id)
        .where(
            CartItem.id == cart_item_id,
            CartItem.owner_id == owner_id,
            CartItem.agent_id == resolved_agent_id,
            CartItem.checkout_adapter == "x402",
        )
        .with_for_update()
    )
    if locked_item_id is None:  # pragma: no cover - protected by foreign keys
        return None
    execution = await db.scalar(
        select(CheckoutExecution)
        .where(
            CheckoutExecution.id == execution_id,
            CheckoutExecution.cart_item_id == locked_item_id,
            CheckoutExecution.owner_id == owner_id,
            CheckoutExecution.agent_id == resolved_agent_id,
            CheckoutExecution.adapter_key == "x402",
            CheckoutExecution.status == CheckoutExecutionStatus.submitted,
        )
        .with_for_update()
    )
    if execution is None or execution.submitted_at is None:
        return None
    submitted_at = (
        execution.submitted_at
        if execution.submitted_at.tzinfo is not None
        else execution.submitted_at.replace(tzinfo=UTC)
    )
    frozen_timeout = execution.adapter_config.get("httpTimeoutSeconds")
    if (
        isinstance(frozen_timeout, bool)
        or not isinstance(frozen_timeout, (int, float))
        or not 0 < frozen_timeout <= 60
    ):
        frozen_timeout = settings.x402_http_timeout_seconds
    now = datetime.now(UTC)
    deadline = submitted_at + timedelta(
        seconds=frozen_timeout + settings.x402_reconciliation_grace_seconds
    )
    if now < deadline:
        return None

    error_code = "payment_outcome_unknown"
    execution.status = CheckoutExecutionStatus.outcome_unknown
    execution.completed_at = now
    execution.error_code = error_code
    execution.error_message = (
        "The x402 request exceeded its response deadline; its payment outcome requires "
        "manual reconciliation and must not be retried."
    )
    db.add(
        CheckoutStatusTransition(
            execution_id=execution.id,
            status=CheckoutExecutionStatus.outcome_unknown,
            attempt_count=execution.attempt_count,
            error_code=error_code,
            occurred_at=now,
        )
    )
    db.add(
        CheckoutEvent(
            execution_id=execution.id,
            owner_id=execution.owner_id,
            agent_id=execution.agent_id,
            cart_item_id=execution.cart_item_id,
            purchase_id=None,
            status=CheckoutExecutionStatus.outcome_unknown,
            amount=execution.approved_amount,
            currency=execution.currency,
            error_code=error_code,
        )
    )
    await db.commit()
    return X402ReconciliationNotice(
        execution_id=execution.id,
        cart_item_id=execution.cart_item_id,
        agent_id=execution.agent_id,
        owner_id=execution.owner_id,
        status=CheckoutExecutionStatus.outcome_unknown,
    )


async def reconcile_stale_x402_submissions(
    db: AsyncSession,
    *,
    owner_id: UUID,
    agent_id: UUID | None,
    settings: Settings,
    limit: int = 100,
) -> list[X402ReconciliationNotice]:
    """Reconcile a bounded tenant-scoped batch from an authenticated read path."""
    candidate_conditions = [
        CheckoutExecution.owner_id == owner_id,
        CheckoutExecution.adapter_key == "x402",
        CheckoutExecution.status == CheckoutExecutionStatus.submitted,
        CheckoutExecution.submitted_at.is_not(None),
        CartItem.owner_id == owner_id,
        CartItem.checkout_adapter == "x402",
    ]
    if agent_id is not None:
        candidate_conditions.extend(
            (
                CheckoutExecution.agent_id == agent_id,
                CartItem.agent_id == agent_id,
            )
        )
    cart_item_ids = list(
        (
            await db.scalars(
                select(CheckoutExecution.cart_item_id)
                .join(CartItem, CartItem.id == CheckoutExecution.cart_item_id)
                .join(X402Payment, X402Payment.execution_id == CheckoutExecution.id)
                .where(*candidate_conditions)
                .order_by(CheckoutExecution.submitted_at, CheckoutExecution.id)
                .limit(limit)
            )
        ).all()
    )
    notices: list[X402ReconciliationNotice] = []
    for cart_item_id in cart_item_ids:
        notice = await reconcile_stale_x402_submission(
            db,
            owner_id=owner_id,
            agent_id=agent_id,
            cart_item_id=cart_item_id,
            settings=settings,
        )
        if notice is not None:
            notices.append(notice)
    return notices


def _int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise X402ServiceError("x402_payload_invalid", f"x402 {field} is invalid")
    try:
        parsed = int(value, 0) if isinstance(value, str) else int(value)
    except (BadSignature, TypeError, ValueError) as exc:
        raise X402ServiceError("x402_payload_invalid", f"x402 {field} is invalid") from exc
    if parsed < 0:
        raise X402ServiceError("x402_payload_invalid", f"x402 {field} is invalid")
    return parsed


def _recover_typed_signature(full_message: dict[str, Any], signature: Any) -> str:
    if not isinstance(signature, str) or EVM_SIGNATURE.fullmatch(signature) is None:
        raise X402ServiceError("x402_signature_invalid", "The x402 signature is invalid")
    try:
        signable = encode_typed_data(full_message=full_message)
        return Account.recover_message(signable, signature=signature)
    except (BadSignature, TypeError, ValueError) as exc:
        raise X402ServiceError("x402_signature_invalid", "The x402 signature is invalid") from exc


def _validate_eip3009(
    payload: dict[str, Any], requirements: dict[str, Any], wallet_address: str, now: int
) -> None:
    if set(payload) != {"authorization", "signature"}:
        raise X402ServiceError("x402_payload_invalid", "The x402 authorization is invalid")
    authorization = payload.get("authorization")
    signature = payload.get("signature")
    if not isinstance(authorization, dict):
        raise X402ServiceError("x402_payload_invalid", "The x402 authorization is invalid")
    if set(authorization) != {
        "from",
        "to",
        "value",
        "validAfter",
        "validBefore",
        "nonce",
    }:
        raise X402ServiceError("x402_payload_invalid", "The x402 authorization is invalid")
    source = _normalize_address(str(authorization.get("from", "")), field="payer")
    recipient = _normalize_address(str(authorization.get("to", "")), field="recipient")
    nonce = authorization.get("nonce")
    if not isinstance(nonce, str) or BYTES32.fullmatch(nonce) is None:
        raise X402ServiceError("x402_payload_invalid", "The x402 nonce is invalid")
    value = _int(authorization.get("value"), field="amount")
    valid_after = _int(authorization.get("validAfter"), field="validAfter")
    valid_before = _int(authorization.get("validBefore"), field="validBefore")
    if source.lower() != wallet_address.lower():
        raise X402ServiceError("x402_wallet_mismatch", "The signature is from another wallet")
    if recipient.lower() != str(requirements["payTo"]).lower():
        raise X402ServiceError("x402_payload_mismatch", "The x402 recipient does not match")
    if value != int(requirements["amount"]):
        raise X402ServiceError("x402_payload_mismatch", "The x402 amount does not match")
    if valid_after > now or valid_before <= now:
        raise X402ServiceError("x402_authorization_expired", "The x402 authorization is not valid")
    if valid_before > now + int(requirements["maxTimeoutSeconds"]):
        raise X402ServiceError(
            "x402_authorization_window", "The x402 authorization window is too long"
        )
    extra = requirements.get("extra") or {}
    full_message = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"},
                {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"},
                {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"},
                {"name": "nonce", "type": "bytes32"},
            ],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {
            "name": extra["name"],
            "version": extra["version"],
            "chainId": int(str(requirements["network"]).split(":", 1)[1]),
            "verifyingContract": requirements["asset"],
        },
        "message": {
            "from": source,
            "to": recipient,
            "value": value,
            "validAfter": valid_after,
            "validBefore": valid_before,
            "nonce": nonce,
        },
    }
    recovered = _recover_typed_signature(full_message, signature)
    if recovered.lower() != wallet_address.lower():
        raise X402ServiceError("x402_signature_invalid", "The x402 signature is invalid")


def _validate_permit2(
    payload: dict[str, Any], requirements: dict[str, Any], wallet_address: str, now: int
) -> None:
    if set(payload) != {"permit2Authorization", "signature"}:
        raise X402ServiceError("x402_payload_invalid", "The Permit2 authorization is invalid")
    authorization = payload.get("permit2Authorization")
    signature = payload.get("signature")
    if not isinstance(authorization, dict):
        raise X402ServiceError("x402_payload_invalid", "The Permit2 authorization is invalid")
    if set(authorization) != {"from", "permitted", "spender", "nonce", "deadline", "witness"}:
        raise X402ServiceError("x402_payload_invalid", "The Permit2 authorization is invalid")
    permitted = authorization.get("permitted")
    witness = authorization.get("witness")
    if not isinstance(permitted, dict) or not isinstance(witness, dict):
        raise X402ServiceError("x402_payload_invalid", "The Permit2 authorization is invalid")
    if set(permitted) != {"token", "amount"} or set(witness) != {"to", "validAfter"}:
        raise X402ServiceError("x402_payload_invalid", "The Permit2 authorization is invalid")
    source = _normalize_address(str(authorization.get("from", "")), field="payer")
    token = _normalize_address(str(permitted.get("token", "")), field="asset")
    spender = _normalize_address(str(authorization.get("spender", "")), field="spender")
    recipient = _normalize_address(str(witness.get("to", "")), field="recipient")
    amount = _int(permitted.get("amount"), field="amount")
    nonce = _int(authorization.get("nonce"), field="nonce")
    deadline = _int(authorization.get("deadline"), field="deadline")
    valid_after = _int(witness.get("validAfter"), field="validAfter")
    if source.lower() != wallet_address.lower():
        raise X402ServiceError("x402_wallet_mismatch", "The signature is from another wallet")
    if token.lower() != str(requirements["asset"]).lower():
        raise X402ServiceError("x402_payload_mismatch", "The Permit2 asset does not match")
    if spender.lower() != X402_EXACT_PERMIT2_PROXY_ADDRESS.lower():
        raise X402ServiceError("x402_payload_mismatch", "The Permit2 spender is not supported")
    if recipient.lower() != str(requirements["payTo"]).lower():
        raise X402ServiceError("x402_payload_mismatch", "The x402 recipient does not match")
    if amount != int(requirements["amount"]):
        raise X402ServiceError("x402_payload_mismatch", "The x402 amount does not match")
    if valid_after > now or deadline <= now:
        raise X402ServiceError("x402_authorization_expired", "The Permit2 authorization is invalid")
    if deadline > now + int(requirements["maxTimeoutSeconds"]):
        raise X402ServiceError(
            "x402_authorization_window", "The Permit2 authorization window is too long"
        )
    full_message = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "PermitWitnessTransferFrom": [
                {"name": "permitted", "type": "TokenPermissions"},
                {"name": "spender", "type": "address"},
                {"name": "nonce", "type": "uint256"},
                {"name": "deadline", "type": "uint256"},
                {"name": "witness", "type": "Witness"},
            ],
            "TokenPermissions": [
                {"name": "token", "type": "address"},
                {"name": "amount", "type": "uint256"},
            ],
            "Witness": [
                {"name": "to", "type": "address"},
                {"name": "validAfter", "type": "uint256"},
            ],
        },
        "primaryType": "PermitWitnessTransferFrom",
        "domain": {
            "name": "Permit2",
            "chainId": int(str(requirements["network"]).split(":", 1)[1]),
            "verifyingContract": PERMIT2_ADDRESS,
        },
        "message": {
            "permitted": {"token": token, "amount": amount},
            "spender": spender,
            "nonce": nonce,
            "deadline": deadline,
            "witness": {"to": recipient, "validAfter": valid_after},
        },
    }
    recovered = _recover_typed_signature(full_message, signature)
    if recovered.lower() != wallet_address.lower():
        raise X402ServiceError("x402_signature_invalid", "The x402 signature is invalid")


def validate_payment_payload(
    raw_payload: dict[str, Any],
    *,
    payment_required: dict[str, Any],
    requirements: dict[str, Any],
    wallet_address: str,
    transfer_method: str,
) -> PaymentPayload:
    if len(json.dumps(raw_payload, separators=(",", ":"))) > 65_536:
        raise X402ServiceError("x402_payload_too_large", "The x402 payment payload is too large")
    if not set(raw_payload).issubset(
        {"x402Version", "payload", "accepted", "resource", "extensions"}
    ):
        raise X402ServiceError("x402_payload_invalid", "The x402 payment payload is invalid")
    try:
        payload = PaymentPayload.model_validate(raw_payload)
    except ValidationError as exc:
        raise X402ServiceError(
            "x402_payload_invalid", "The x402 payment payload is invalid"
        ) from exc
    if payload.x402_version != 2:
        raise X402ServiceError("x402_version_unsupported", "Only x402 version 2 is supported")
    if payload.extensions:
        raise X402ServiceError(
            "x402_extensions_unsupported",
            "x402 payment extensions are not supported in this release",
        )
    accepted = payload.accepted.model_dump(mode="json", by_alias=True, exclude_none=True)
    if accepted != requirements:
        raise X402ServiceError(
            "x402_payload_mismatch", "The signed payment does not match the approved request"
        )
    expected_resource = payment_required.get("resource")
    actual_resource = (
        payload.resource.model_dump(mode="json", by_alias=True, exclude_none=True)
        if payload.resource is not None
        else None
    )
    if actual_resource != expected_resource:
        raise X402ServiceError(
            "x402_resource_mismatch", "The signed payment references a different resource"
        )
    now = int(datetime.now(UTC).timestamp())
    if transfer_method == "eip3009":
        _validate_eip3009(payload.payload, requirements, wallet_address, now)
    elif transfer_method == "permit2":
        _validate_permit2(payload.payload, requirements, wallet_address, now)
    else:  # pragma: no cover - database constraint and discovery selection prevent this
        raise X402ServiceError("x402_transfer_unsupported", "The x402 transfer is unsupported")
    return payload


def payment_signature_header(payload: PaymentPayload) -> str:
    return encode_payment_signature_header(payload)


def parse_settlement_response(response: X402HttpResponse) -> SettleResponse:
    header = _header(response.headers, PAYMENT_RESPONSE_HEADER)
    if header is None or len(header) > 65_536:
        raise X402ServiceError(
            "x402_payment_response_missing", "The paid resource returned no payment receipt"
        )
    try:
        return decode_payment_response_header(header)
    except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise X402ServiceError(
            "x402_payment_response_invalid", "The paid resource returned an invalid receipt"
        ) from exc


def new_http_client(settings: Settings) -> HttpxX402Client:
    return HttpxX402Client(
        timeout_seconds=settings.x402_http_timeout_seconds,
        max_response_bytes=settings.x402_max_response_bytes,
    )
