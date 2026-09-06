import asyncio
import base64
import json
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from helpers import (
    API,
    assign_payment_method,
    bearer,
    connect_agent,
    create_agent,
    register_user,
)
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ag_platform_api.api.dependencies import get_x402_http_client
from ag_platform_api.api.routes.x402 import _classify_paid_response
from ag_platform_api.core.config import Settings
from ag_platform_api.main import app
from ag_platform_api.models import (
    AgentPaymentMethod,
    CheckoutEvent,
    CheckoutExecution,
    CheckoutExecutionStatus,
    CheckoutStatusTransition,
    PaymentMethod,
    PaymentMethodStatus,
    X402Payment,
)
from ag_platform_api.services.x402 import (
    PERMIT2_ADDRESS,
    X402_EXACT_PERMIT2_PROXY_ADDRESS,
    HttpxX402Client,
    X402HttpResponse,
    X402ServiceError,
    discover_x402_payment,
    validate_payment_payload,
)

NETWORK = "eip155:84532"
ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
PAY_TO = "0x1111111111111111111111111111111111111111"
RESOURCE_URL = "https://paid.example.test/data?item=1"


class FakeX402Client:
    def __init__(self, responses: list[X402HttpResponse | X402ServiceError]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.on_paid: Any = None

    async def get(
        self, resource_url: str, *, headers: dict[str, str] | None = None
    ) -> X402HttpResponse:
        self.calls.append((resource_url, dict(headers or {})))
        response = self.responses.pop(0)
        if isinstance(response, X402ServiceError):
            raise response
        if headers and self.on_paid is not None:
            await self.on_paid()
        return response


class _PinnedPeerStream:
    def __init__(self, peer: str = "93.184.216.34") -> None:
        self.peer = peer

    def get_extra_info(self, key: str) -> tuple[str, int] | None:
        return (self.peer, 443) if key == "server_addr" else None


class _FakeHttpxResponse:
    def __init__(
        self,
        *,
        peer: str = "93.184.216.34",
        status_code: int = 402,
        is_redirect: bool = False,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.extensions = {"network_stream": _PinnedPeerStream(peer)}
        self.is_redirect = is_redirect
        self.chunks = chunks or [b"payment required"]

    async def aiter_bytes(self) -> Any:
        for chunk in self.chunks:
            yield chunk


class _FakeHttpxStreamContext:
    def __init__(self, response: _FakeHttpxResponse) -> None:
        self.response = response

    async def __aenter__(self) -> _FakeHttpxResponse:
        return self.response

    async def __aexit__(self, *_: Any) -> None:
        return None


class _CapturingHttpxClient:
    def __init__(self, response: _FakeHttpxResponse | None = None) -> None:
        self.call: tuple[tuple[Any, ...], dict[str, Any]] | None = None
        self.response = response or _FakeHttpxResponse()

    def stream(self, *args: Any, **kwargs: Any) -> _FakeHttpxStreamContext:
        self.call = (args, kwargs)
        return _FakeHttpxStreamContext(self.response)

    async def aclose(self) -> None:
        return None


def encoded_header(value: dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()


async def test_http_client_pins_validated_dns_before_sending_headers(monkeypatch: Any) -> None:
    transport = _CapturingHttpxClient()
    monkeypatch.setattr(
        "ag_platform_api.services.x402.httpx.AsyncClient",
        lambda **_: transport,
    )
    resolutions = 0

    def resolve(*_: Any, **__: Any) -> list[tuple[Any, ...]]:
        nonlocal resolutions
        resolutions += 1
        if resolutions > 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 443),
            )
        ]

    monkeypatch.setattr("ag_platform_api.services.x402.socket.getaddrinfo", resolve)
    client = HttpxX402Client(timeout_seconds=1, max_response_bytes=1024)
    response = await client.get(
        "https://paid.example.test/resource?id=1",
        headers={"PAYMENT-SIGNATURE": "proof"},
    )
    assert response.status_code == 402
    assert resolutions == 1
    assert transport.call is not None
    args, kwargs = transport.call
    assert args == ("GET", "https://93.184.216.34/resource?id=1")
    assert kwargs["headers"] == {
        "PAYMENT-SIGNATURE": "proof",
        "Host": "paid.example.test",
    }
    assert kwargs["extensions"] == {"sni_hostname": "paid.example.test"}


async def test_http_client_rejects_mixed_private_dns_before_opening_stream(
    monkeypatch: Any,
) -> None:
    transport = _CapturingHttpxClient()
    monkeypatch.setattr(
        "ag_platform_api.services.x402.httpx.AsyncClient",
        lambda **_: transport,
    )
    monkeypatch.setattr(
        "ag_platform_api.services.x402.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ],
    )
    client = HttpxX402Client(timeout_seconds=1, max_response_bytes=1024)
    with pytest.raises(X402ServiceError) as raised:
        await client.get("https://paid.example.test/resource")
    assert raised.value.code == "x402_url_private"
    assert transport.call is None


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (_FakeHttpxResponse(peer="1.1.1.1"), "x402_peer_mismatch"),
        (
            _FakeHttpxResponse(status_code=302, is_redirect=True),
            "x402_redirect_rejected",
        ),
        (
            _FakeHttpxResponse(chunks=[b"a" * 600, b"b" * 600]),
            "x402_response_too_large",
        ),
    ],
)
async def test_http_client_rejects_unsafe_peer_redirect_and_stream_size(
    monkeypatch: Any,
    response: _FakeHttpxResponse,
    expected_code: str,
) -> None:
    transport = _CapturingHttpxClient(response)
    monkeypatch.setattr(
        "ag_platform_api.services.x402.httpx.AsyncClient",
        lambda **_: transport,
    )
    monkeypatch.setattr(
        "ag_platform_api.services.x402.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ],
    )
    client = HttpxX402Client(timeout_seconds=1, max_response_bytes=1024)
    with pytest.raises(X402ServiceError) as raised:
        await client.get("https://paid.example.test/resource")
    assert raised.value.code == expected_code


async def test_http_client_timeout_covers_dns_resolution(monkeypatch: Any) -> None:
    transport = _CapturingHttpxClient()
    monkeypatch.setattr(
        "ag_platform_api.services.x402.httpx.AsyncClient",
        lambda **_: transport,
    )

    async def slow_resolution(*_args: Any, **_kwargs: Any) -> Any:
        await asyncio.sleep(1)

    monkeypatch.setattr(
        "ag_platform_api.services.x402.asyncio.to_thread",
        slow_resolution,
    )
    client = HttpxX402Client(timeout_seconds=0.001, max_response_bytes=1024)
    with pytest.raises(X402ServiceError) as raised:
        await client.get("https://paid.example.test/resource")
    assert raised.value.code == "x402_transport_error"
    assert transport.call is None


def payment_required(
    *,
    amount: str = "10000",
    network: str = NETWORK,
    asset: str = ASSET,
    name: str = "USDC",
    version: str = "2",
) -> dict[str, Any]:
    return {
        "x402Version": 2,
        "resource": {
            "url": RESOURCE_URL,
            "description": "A test-protected JSON resource",
            "mimeType": "application/json",
        },
        "accepts": [
            {
                "scheme": "exact",
                "network": network,
                "asset": asset,
                "amount": amount,
                "payTo": PAY_TO,
                "maxTimeoutSeconds": 300,
                "extra": {
                    "name": name,
                    "version": version,
                    "assetTransferMethod": "eip3009",
                },
            }
        ],
    }


def discovery_response(
    *,
    amount: str = "10000",
    network: str = NETWORK,
    asset: str = ASSET,
    name: str = "USDC",
    version: str = "2",
) -> X402HttpResponse:
    return X402HttpResponse(
        status_code=402,
        headers={
            "PAYMENT-REQUIRED": encoded_header(
                payment_required(
                    amount=amount,
                    network=network,
                    asset=asset,
                    name=name,
                    version=version,
                )
            )
        },
        content=b"payment required",
    )


def paid_response(
    *,
    payer: str,
    content: bytes = b'{"answer":42}',
    network: str = NETWORK,
    transaction: str = "0x" + "ab" * 32,
    include_content_type: bool = True,
    include_amount: bool = False,
) -> X402HttpResponse:
    settlement = {
        "success": True,
        "payer": payer,
        "transaction": transaction,
        "network": network,
    }
    if include_amount:
        settlement["amount"] = "10000"
    headers = {"PAYMENT-RESPONSE": encoded_header(settlement)}
    if include_content_type:
        headers["content-type"] = "application/json"
    return X402HttpResponse(
        status_code=200,
        headers=headers,
        content=content,
    )


def failed_payment_response(*, payer: str) -> X402HttpResponse:
    settlement = {
        "success": False,
        "errorReason": "insufficient_funds",
        "payer": payer,
        "transaction": "",
        "network": NETWORK,
        "amount": "10000",
    }
    return X402HttpResponse(
        status_code=402,
        headers={"PAYMENT-RESPONSE": encoded_header(settlement)},
        content=b"payment rejected",
    )


async def connect_metamask(
    client: AsyncClient,
    *,
    network: str = NETWORK,
) -> tuple[str, dict[str, Any], str, Any]:
    user_token = await register_user(client, "wallet-owner")
    agent = await create_agent(client, user_token)
    connected = await connect_agent(client, agent["pairing_token"])
    account = Account.create()
    challenge = await client.post(
        f"{API}/payment-methods/wallet/challenge",
        headers=bearer(user_token),
        json={"provider": "metamask", "address": account.address, "network": network},
    )
    assert challenge.status_code == 201, challenge.text
    challenge_body = challenge.json()
    signature = account.sign_message(encode_defunct(text=challenge_body["message"])).signature.hex()
    wallet_response = await client.post(
        f"{API}/payment-methods/wallet",
        headers=bearer(user_token),
        json={
            "challenge_id": challenge_body["challenge_id"],
            "signature": f"0x{signature}",
            "display_name": "MetaMask test wallet",
        },
    )
    assert wallet_response.status_code == 201, wallet_response.text
    wallet = wallet_response.json()
    await assign_payment_method(client, user_token, agent["id"], wallet["id"])
    return user_token, wallet, connected["agent_access_token"], account


def signed_payment_payload(payment_required_body: dict[str, Any], account: Any) -> dict[str, Any]:
    requirement = payment_required_body["accepts"][0]
    now = int(datetime.now(UTC).timestamp())
    authorization = {
        "from": account.address,
        "to": requirement["payTo"],
        "value": requirement["amount"],
        "validAfter": "0",
        "validBefore": str(now + 240),
        "nonce": "0x" + "12" * 32,
    }
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
            "name": requirement["extra"]["name"],
            "version": requirement["extra"]["version"],
            "chainId": int(requirement["network"].split(":", 1)[1]),
            "verifyingContract": requirement["asset"],
        },
        "message": authorization,
    }
    signature = account.sign_typed_data(full_message=full_message).signature.hex()
    return {
        "x402Version": 2,
        "resource": payment_required_body["resource"],
        "accepted": requirement,
        "payload": {"authorization": authorization, "signature": f"0x{signature}"},
    }


async def propose_and_approve(
    client: AsyncClient,
    *,
    gateway: FakeX402Client,
    network: str = NETWORK,
    asset: str = ASSET,
) -> tuple[str, dict[str, Any], str, Any, dict[str, Any]]:
    app.dependency_overrides[get_x402_http_client] = lambda: gateway
    user_token, wallet, agent_token, account = await connect_metamask(client, network=network)
    proposed = await client.post(
        f"{API}/agent/x402-payment-requests",
        headers=bearer(agent_token),
        json={
            "resource_url": RESOURCE_URL,
            "reason": "The agent needs the protected test data.",
            "merchant": "Paid API",
        },
    )
    assert proposed.status_code == 201, proposed.text
    item = proposed.json()
    assert item["status"] == "proposed"
    assert item["credential_id"] is None
    assert item["account_email"] is None
    assert item["checkout_adapter"] == "x402"
    assert item["currency"] == "USD"
    assert item["unit_price"] == "0.01"
    assert item["x402"] == {
        "resource_url": RESOURCE_URL,
        "network": network,
        "asset": asset,
        "symbol": "USDC",
        "decimals": 6,
        "amount_atomic": "10000",
        "transfer_method": "eip3009",
        "pay_to": "0x1111111111111111111111111111111111111111",
        "transaction": None,
    }
    unavailable = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert unavailable.status_code == 409

    approved = await client.post(
        f"{API}/cart-items/{item['id']}/approve",
        headers=bearer(user_token),
        json={"payment_method_id": wallet["id"]},
    )
    assert approved.status_code == 200, approved.text
    approved_item = approved.json()
    assert approved_item["execution"]["status"] == "awaiting_signature"
    return user_token, wallet, agent_token, account, approved_item


async def test_metamask_ownership_and_wallet_config(
    client: AsyncClient,
    settings: Settings,
) -> None:
    user_token = await register_user(client, "wallet-config")
    config = await client.get(f"{API}/payment-methods/wallet-config", headers=bearer(user_token))
    assert config.status_code == 200
    assert config.json() == {
        "providers": [{"id": "metamask", "display_name": "MetaMask"}],
        "networks": [
            {
                "network": "eip155:84532",
                "chain_id": 84532,
                "name": "Base Sepolia",
                "is_testnet": True,
                "x402_enabled": True,
            },
            {
                "network": "eip155:8453",
                "chain_id": 8453,
                "name": "Base",
                "is_testnet": False,
                "x402_enabled": False,
            },
        ],
    }
    settings.x402_assets = []
    no_assets = await client.get(f"{API}/payment-methods/wallet-config", headers=bearer(user_token))
    assert all(not network["x402_enabled"] for network in no_assets.json()["networks"])

    account = Account.create()
    challenge = await client.post(
        f"{API}/payment-methods/wallet/challenge",
        headers=bearer(user_token),
        json={"provider": "metamask", "address": account.address, "network": NETWORK},
    )
    message = challenge.json()["message"]
    wrong_signature = Account.create().sign_message(encode_defunct(text=message)).signature.hex()
    wrong = await client.post(
        f"{API}/payment-methods/wallet",
        headers=bearer(user_token),
        json={
            "challenge_id": challenge.json()["challenge_id"],
            "signature": f"0x{wrong_signature}",
            "display_name": "Wrong signer",
        },
    )
    assert wrong.status_code == 422
    signature = account.sign_message(encode_defunct(text=message)).signature.hex()
    connected = await client.post(
        f"{API}/payment-methods/wallet",
        headers=bearer(user_token),
        json={
            "challenge_id": challenge.json()["challenge_id"],
            "signature": f"0x{signature}",
            "display_name": "MetaMask",
        },
    )
    assert connected.status_code == 201, connected.text
    assert connected.json()["kind"] == "wallet"
    assert connected.json()["address"] == account.address
    replay = await client.post(
        f"{API}/payment-methods/wallet",
        headers=bearer(user_token),
        json={
            "challenge_id": challenge.json()["challenge_id"],
            "signature": f"0x{signature}",
            "display_name": "Replay",
        },
    )
    assert replay.status_code == 409


async def test_x402_success_is_one_shot_and_result_is_encrypted(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, wallet, agent_token, account, item = await propose_and_approve(
        client, gateway=gateway
    )
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    assert signing.status_code == 200, signing.text
    assert signing.json()["wallet"]["id"] == wallet["id"]
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    protected_body = b'{"answer":42}'
    gateway.responses.append(
        paid_response(
            payer=account.address,
            content=protected_body,
            transaction="0x" + "AB" * 32,
        )
    )

    authorized = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["status"] == "purchased"
    assert authorized.json()["execution"]["status"] == "succeeded"
    assert authorized.json()["x402"]["network"] == NETWORK
    assert authorized.json()["x402"]["transaction"] == "0x" + "ab" * 32
    assert len(gateway.calls) == 2
    assert gateway.calls[0] == (RESOURCE_URL, {})
    assert set(gateway.calls[1][1]) == {"PAYMENT-SIGNATURE"}

    result = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert result.status_code == 200, result.text
    assert result.json() == {
        "cart_item_id": item["id"],
        "status": "succeeded",
        "mime_type": "application/json",
        "body": base64.b64encode(protected_body).decode(),
        "body_encoding": "base64",
        "transaction": "0x" + "ab" * 32,
        "network": NETWORK,
        "asset": ASSET,
    }
    replay = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert replay.status_code == 409
    assert len(gateway.calls) == 2

    async with db_session_factory() as db:
        payment = await db.get(X402Payment, UUID(item["id"]))
        assert payment is not None
        assert protected_body.decode() not in (payment.encrypted_response_body or "")
        assert "signature" not in json.dumps(payment.payment_response)
        execution = await db.get(CheckoutExecution, payment.execution_id)
        assert execution is not None and execution.attempt_count == 1
        statuses = list(
            (
                await db.scalars(
                    select(CheckoutStatusTransition.status)
                    .where(CheckoutStatusTransition.execution_id == execution.id)
                    .order_by(CheckoutStatusTransition.sequence)
                )
            ).all()
        )
        assert statuses == [
            CheckoutExecutionStatus.awaiting_signature,
            CheckoutExecutionStatus.authorized,
            CheckoutExecutionStatus.submitted,
            CheckoutExecutionStatus.succeeded,
        ]


async def test_success_without_content_type_uses_binary_result_mime(
    client: AsyncClient,
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, _, agent_token, account, item = await propose_and_approve(client, gateway=gateway)
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    gateway.responses.append(
        paid_response(payer=account.address, include_content_type=False, content=b"\x00\x01")
    )
    authorized = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert authorized.status_code == 200, authorized.text
    result = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert result.status_code == 200
    assert result.json()["mime_type"] == "application/octet-stream"
    assert result.json()["body"] == "AAE="


async def test_post_submission_wallet_disable_does_not_discard_valid_result(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, wallet, agent_token, account, item = await propose_and_approve(
        client, gateway=gateway
    )
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)

    async def disable_after_submission() -> None:
        async with db_session_factory() as db:
            payment_method = await db.get(PaymentMethod, UUID(wallet["id"]))
            assert payment_method is not None
            payment_method.status = PaymentMethodStatus.disabled
            await db.execute(
                delete(AgentPaymentMethod).where(
                    AgentPaymentMethod.payment_method_id == payment_method.id
                )
            )
            await db.commit()

    gateway.on_paid = disable_after_submission
    gateway.responses.append(paid_response(payer=account.address, content=b"still valid"))
    authorized = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["execution"]["status"] == "succeeded"
    result = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert result.status_code == 200
    assert result.json()["body"] == base64.b64encode(b"still valid").decode()
    assert len(gateway.calls) == 2


async def test_unbounded_receipt_transaction_is_never_persisted_as_reference(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, _, agent_token, account, item = await propose_and_approve(client, gateway=gateway)
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    gateway.responses.append(paid_response(payer=account.address, transaction="not-a-hash" * 3_000))
    authorized = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["execution"]["status"] == "outcome_unknown"
    result = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert result.status_code == 200
    assert result.json()["transaction"] is None
    async with db_session_factory() as db:
        payment = await db.get(X402Payment, UUID(item["id"]))
        assert payment is not None
        assert payment.transaction is None


async def test_unbound_receipt_transaction_is_not_exposed_to_human_or_agent(
    client: AsyncClient,
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, _, agent_token, account, item = await propose_and_approve(client, gateway=gateway)
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    gateway.responses.append(
        paid_response(
            payer=account.address,
            network="eip155:1",
            transaction="0x" + "cd" * 32,
        )
    )
    authorized = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["execution"]["status"] == "outcome_unknown"
    assert authorized.json()["x402"]["network"] == NETWORK
    assert authorized.json()["x402"]["transaction"] is None
    result = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert result.status_code == 200
    assert result.json()["transaction"] is None


async def test_exactly_bound_transaction_is_exposed_for_manual_reconciliation(
    client: AsyncClient,
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, _, agent_token, account, item = await propose_and_approve(client, gateway=gateway)
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    paid = paid_response(payer=account.address, transaction="0x" + "EF" * 32)
    gateway.responses.append(
        X402HttpResponse(
            status_code=500,
            headers=paid.headers,
            content=b"ambiguous after a bound receipt",
        )
    )
    authorized = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert authorized.status_code == 200, authorized.text
    assert authorized.json()["execution"]["status"] == "outcome_unknown"
    assert authorized.json()["x402"]["network"] == NETWORK
    assert authorized.json()["x402"]["transaction"] == "0x" + "ef" * 32
    result = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert result.status_code == 200
    assert result.json()["transaction"] == "0x" + "ef" * 32


def test_failure_receipt_must_match_the_frozen_payment() -> None:
    account = Account.create()
    payment = X402Payment(network=NETWORK, amount_atomic="10000")
    wallet = PaymentMethod(wallet_address_normalized=account.address.lower())
    matched = {
        "success": False,
        "errorReason": "insufficient_funds",
        "payer": account.address,
        "transaction": "",
        "network": NETWORK,
        "amount": "10000",
    }
    outcome, _, transaction = _classify_paid_response(
        X402HttpResponse(
            status_code=402,
            headers={"PAYMENT-RESPONSE": encoded_header(matched)},
            content=b"unpaid",
        ),
        payment=payment,
        wallet=wallet,
    )
    assert outcome == "failed"
    assert transaction is None

    mismatched = {
        **matched,
        "network": "eip155:1",
        "transaction": "0x" + "cd" * 32,
    }
    outcome, _, mismatched_transaction = _classify_paid_response(
        X402HttpResponse(
            status_code=402,
            headers={"PAYMENT-RESPONSE": encoded_header(mismatched)},
            content=b"unverifiable",
        ),
        payment=payment,
        wallet=wallet,
    )
    assert outcome == "outcome_unknown"
    assert mismatched_transaction is None

    mismatched_amount = {
        **matched,
        "amount": "10001",
        "transaction": "0x" + "ef" * 32,
    }
    outcome, _, mismatched_transaction = _classify_paid_response(
        X402HttpResponse(
            status_code=402,
            headers={"PAYMENT-RESPONSE": encoded_header(mismatched_amount)},
            content=b"unverifiable",
        ),
        payment=payment,
        wallet=wallet,
    )
    assert outcome == "outcome_unknown"
    assert mismatched_transaction is None


async def test_recent_submitted_execution_remains_visible_and_is_never_retried(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, _, agent_token, account, item = await propose_and_approve(client, gateway=gateway)
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    async with db_session_factory() as db:
        payment = await db.get(X402Payment, UUID(item["id"]))
        assert payment is not None and payment.execution_id is not None
        execution = await db.get(CheckoutExecution, payment.execution_id)
        assert execution is not None
        execution.status = CheckoutExecutionStatus.submitted
        execution.attempt_count = 1
        execution.submitted_at = datetime.now(UTC)
        await db.commit()

    result = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert result.status_code == 200
    assert result.json()["status"] == "submitted"
    assert result.json()["body"] is None
    retry = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert retry.status_code == 409
    assert len(gateway.calls) == 1


async def test_checkout_event_poll_reconciles_stale_submission_once_without_resubmitting(
    client: AsyncClient,
    settings: Settings,
    broker: Any,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, _, agent_token, account, item = await propose_and_approve(client, gateway=gateway)
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    paid_request_started = asyncio.Event()
    release_paid_response = asyncio.Event()

    async def hold_paid_response() -> None:
        paid_request_started.set()
        await release_paid_response.wait()

    gateway.on_paid = hold_paid_response
    gateway.responses.append(paid_response(payer=account.address, content=b"late result"))
    authorization = asyncio.create_task(
        client.post(
            f"{API}/cart-items/{item['id']}/x402/authorize",
            headers=bearer(user_token),
            json={"payment_payload": payment_payload},
        )
    )
    await asyncio.wait_for(paid_request_started.wait(), timeout=1)
    frozen_timeout = settings.x402_http_timeout_seconds
    settings.x402_http_timeout_seconds = 60
    async with db_session_factory() as db:
        payment = await db.get(X402Payment, UUID(item["id"]))
        assert payment is not None and payment.execution_id is not None
        execution = await db.get(CheckoutExecution, payment.execution_id)
        assert execution is not None
        assert execution.adapter_config["httpTimeoutSeconds"] == frozen_timeout
        execution.submitted_at = datetime.now(UTC) - timedelta(
            seconds=(frozen_timeout + settings.x402_reconciliation_grace_seconds + 1)
        )
        await db.commit()

    polled = await client.get(
        f"{API}/agent/checkout-events",
        headers=bearer(agent_token),
    )
    assert polled.status_code == 200, polled.text
    matching_events = [
        event for event in polled.json()["events"] if event["request_id"] == item["id"]
    ]
    assert matching_events[-1]["status"] == "outcome_unknown"
    assert matching_events[-1]["error_code"] == "payment_outcome_unknown"

    reconciled = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["status"] == "outcome_unknown"
    assert reconciled.json()["transaction"] is None
    assert reconciled.json()["body"] is None
    assert len(gateway.calls) == 2

    release_paid_response.set()
    late_authorization = await authorization
    assert late_authorization.status_code == 409
    assert len(gateway.calls) == 2

    repeated = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "outcome_unknown"
    reconciliation_events = [
        event for event in broker.events if event[0] == "checkout.outcome_unknown"
    ]
    assert len(reconciliation_events) == 1
    assert reconciliation_events[0][1]["reconciliation_reason"] == ("response_deadline_exceeded")

    async with db_session_factory() as db:
        payment = await db.get(X402Payment, UUID(item["id"]))
        assert payment is not None and payment.execution_id is not None
        execution = await db.get(CheckoutExecution, payment.execution_id)
        assert execution is not None
        assert execution.status is CheckoutExecutionStatus.outcome_unknown
        assert execution.error_code == "payment_outcome_unknown"
        transitions = list(
            (
                await db.scalars(
                    select(CheckoutStatusTransition).where(
                        CheckoutStatusTransition.execution_id == execution.id,
                        CheckoutStatusTransition.status == CheckoutExecutionStatus.outcome_unknown,
                    )
                )
            ).all()
        )
        events = list(
            (
                await db.scalars(
                    select(CheckoutEvent).where(
                        CheckoutEvent.execution_id == execution.id,
                        CheckoutEvent.status == CheckoutExecutionStatus.outcome_unknown,
                    )
                )
            ).all()
        )
        assert len(transitions) == 1
    assert len(events) == 1


async def test_human_cart_poll_reconciles_stale_submission_once(
    client: AsyncClient,
    settings: Settings,
    broker: Any,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, _, _, _, item = await propose_and_approve(client, gateway=gateway)
    async with db_session_factory() as db:
        payment = await db.get(X402Payment, UUID(item["id"]))
        assert payment is not None and payment.execution_id is not None
        execution = await db.get(CheckoutExecution, payment.execution_id)
        assert execution is not None
        execution.status = CheckoutExecutionStatus.submitted
        execution.attempt_count = 1
        execution.submitted_at = datetime.now(UTC) - timedelta(
            seconds=(
                settings.x402_http_timeout_seconds + settings.x402_reconciliation_grace_seconds + 1
            )
        )
        await db.commit()

    listed = await client.get(f"{API}/cart-items", headers=bearer(user_token))
    assert listed.status_code == 200, listed.text
    reconciled = next(entry for entry in listed.json() if entry["id"] == item["id"])
    assert reconciled["execution"]["status"] == "outcome_unknown"
    assert reconciled["execution"]["error_code"] == "payment_outcome_unknown"
    assert reconciled["x402"]["transaction"] is None

    repeated = await client.get(f"{API}/cart-items/{item['id']}", headers=bearer(user_token))
    assert repeated.status_code == 200
    assert repeated.json()["execution"]["status"] == "outcome_unknown"
    reconciliation_events = [
        event for event in broker.events if event[0] == "checkout.outcome_unknown"
    ]
    assert len(reconciliation_events) == 1
    assert len(gateway.calls) == 1


async def test_invalid_signature_never_submits_and_transport_ambiguity_is_not_retried(
    client: AsyncClient,
    settings: Settings,
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, _, agent_token, account, item = await propose_and_approve(client, gateway=gateway)
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    payment_payload["payload"]["signature"] = "0x" + "00" * 65
    invalid = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert invalid.status_code == 422
    assert len(gateway.calls) == 1

    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    settings.x402_enabled = False
    signing_disabled = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    assert signing_disabled.status_code == 409
    disabled = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert disabled.status_code == 409
    assert len(gateway.calls) == 1
    settings.x402_enabled = True
    configured_assets = settings.x402_assets
    settings.x402_assets = []
    asset_disabled = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert asset_disabled.status_code == 409
    assert len(gateway.calls) == 1
    settings.x402_assets = configured_assets
    gateway.responses.append(
        X402ServiceError("x402_transport_error", "The remote response was ambiguous")
    )
    submitted = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["execution"]["status"] == "outcome_unknown"
    assert submitted.json()["execution"]["error_code"] == "payment_outcome_unknown"
    assert len(gateway.calls) == 2
    result = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert result.status_code == 200
    assert result.json()["status"] == "outcome_unknown"
    assert result.json()["body"] is None
    retry = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert retry.status_code == 409
    assert len(gateway.calls) == 2


async def test_bound_payment_rejection_has_safe_failed_error(
    client: AsyncClient,
) -> None:
    gateway = FakeX402Client([discovery_response()])
    user_token, _, agent_token, account, item = await propose_and_approve(client, gateway=gateway)
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    gateway.responses.append(failed_payment_response(payer=account.address))
    rejected = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["execution"]["status"] == "failed"
    assert rejected.json()["execution"]["error_code"] == "payment_declined"
    assert rejected.json()["execution"]["status_history"][-1]["error_code"] == ("payment_declined")
    result = await client.get(
        f"{API}/agent/cart-items/{item['id']}/x402/result",
        headers=bearer(agent_token),
    )
    assert result.status_code == 200
    assert result.json()["status"] == "failed"
    assert result.json()["body"] is None
    retry = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert retry.status_code == 409
    assert len(gateway.calls) == 2


async def test_kill_switch_blocks_approval_before_creating_an_execution(
    client: AsyncClient,
    settings: Settings,
) -> None:
    gateway = FakeX402Client([discovery_response()])
    app.dependency_overrides[get_x402_http_client] = lambda: gateway
    user_token, wallet, agent_token, _ = await connect_metamask(client)
    proposed = await client.post(
        f"{API}/agent/x402-payment-requests",
        headers=bearer(agent_token),
        json={"resource_url": RESOURCE_URL, "reason": "Need protected data."},
    )
    assert proposed.status_code == 201, proposed.text
    settings.x402_enabled = False
    blocked = await client.post(
        f"{API}/cart-items/{proposed.json()['id']}/approve",
        headers=bearer(user_token),
        json={"payment_method_id": wallet["id"]},
    )
    assert blocked.status_code == 409
    current = await client.get(
        f"{API}/cart-items/{proposed.json()['id']}",
        headers=bearer(user_token),
    )
    assert current.status_code == 200
    assert current.json()["status"] == "proposed"
    assert current.json()["execution"] is None


async def test_base_mainnet_kill_switch_blocks_an_existing_signing_request(
    client: AsyncClient,
    settings: Settings,
) -> None:
    mainnet_asset = next(
        asset
        for asset in settings.x402_assets
        if asset.network == "eip155:8453" and asset.transfer_method == "eip3009"
    )
    settings.x402_mainnet_enabled = True
    gateway = FakeX402Client(
        [
            discovery_response(
                network=mainnet_asset.network,
                asset=mainnet_asset.asset,
                name=mainnet_asset.name,
                version=mainnet_asset.version,
            )
        ]
    )
    user_token, _, _, account, item = await propose_and_approve(
        client,
        gateway=gateway,
        network=mainnet_asset.network,
        asset=mainnet_asset.asset,
    )
    signing = await client.get(
        f"{API}/cart-items/{item['id']}/x402/signing-request",
        headers=bearer(user_token),
    )
    payment_payload = signed_payment_payload(signing.json()["payment_required"], account)
    settings.x402_mainnet_enabled = False
    blocked = await client.post(
        f"{API}/cart-items/{item['id']}/x402/authorize",
        headers=bearer(user_token),
        json={"payment_payload": payment_payload},
    )
    assert blocked.status_code == 409
    assert "mainnet" in blocked.json()["detail"].lower()
    assert len(gateway.calls) == 1


async def test_discovery_enforces_configured_atomic_cap(client: AsyncClient) -> None:
    gateway = FakeX402Client([discovery_response(amount="100000001")])
    app.dependency_overrides[get_x402_http_client] = lambda: gateway
    user_token = await register_user(client, "amount-cap")
    agent = await create_agent(client, user_token)
    connected = await connect_agent(client, agent["pairing_token"])
    response = await client.post(
        f"{API}/agent/x402-payment-requests",
        headers=bearer(connected["agent_access_token"]),
        json={"resource_url": RESOURCE_URL, "reason": "Need the data."},
    )
    assert response.status_code == 409
    assert "enabled x402 asset" in response.json()["detail"]
    assert len(gateway.calls) == 1


async def test_discovery_rejects_non_ascii_atomic_amount(settings: Settings) -> None:
    gateway = FakeX402Client([discovery_response(amount="²")])
    with pytest.raises(X402ServiceError) as raised:
        await discover_x402_payment(
            gateway,
            resource_url=RESOURCE_URL,
            assets=settings.x402_assets,
            preferred_networks={NETWORK},
        )
    assert raised.value.code == "x402_asset_unsupported"


async def test_base_usdt_permit2_accepts_direct_approval_payload_without_domain_extra(
    settings: Settings,
) -> None:
    account = Account.create()
    resource_url = "https://paid.example.test/usdt"
    usdt = next(asset for asset in settings.x402_assets if asset.symbol == "USDT")
    requirement = {
        "scheme": "exact",
        "network": "eip155:8453",
        "asset": usdt.asset,
        "amount": "250000",
        "payTo": PAY_TO,
        "maxTimeoutSeconds": 300,
        "extra": {"assetTransferMethod": "permit2"},
    }
    required = {
        "x402Version": 2,
        "resource": {"url": resource_url, "description": "USDT resource"},
        "accepts": [requirement],
    }
    gateway = FakeX402Client(
        [
            X402HttpResponse(
                status_code=402,
                headers={"PAYMENT-REQUIRED": encoded_header(required)},
                content=b"payment required",
            )
        ]
    )
    discovered = await discover_x402_payment(
        gateway,
        resource_url=resource_url,
        assets=settings.x402_assets,
        preferred_networks={"eip155:8453"},
    )
    now = int(datetime.now(UTC).timestamp())
    authorization = {
        "from": account.address,
        "permitted": {"token": usdt.asset, "amount": "250000"},
        "spender": X402_EXACT_PERMIT2_PROXY_ADDRESS,
        "nonce": "123456789",
        "deadline": str(now + 240),
        "witness": {"to": PAY_TO, "validAfter": "0"},
    }
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
            "chainId": 8453,
            "verifyingContract": PERMIT2_ADDRESS,
        },
        "message": authorization,
    }
    signature = account.sign_typed_data(full_message=full_message).signature.hex()
    raw_payload = {
        "x402Version": 2,
        "resource": discovered.payment_required["resource"],
        "accepted": discovered.selected_requirements,
        "payload": {
            "permit2Authorization": authorization,
            "signature": f"0x{signature}",
        },
    }
    validated = validate_payment_payload(
        raw_payload,
        payment_required=discovered.payment_required,
        requirements=discovered.selected_requirements,
        wallet_address=account.address,
        transfer_method="permit2",
    )
    assert validated.payload["permit2Authorization"]["from"] == account.address
