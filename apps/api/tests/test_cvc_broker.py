import asyncio
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from ag_platform_api.services.checkout.cvc_broker import (
    CvcBrokerError,
    CvcUnavailableError,
    LocalDirectCardCvcBroker,
    LocalDirectCardCvcClient,
)

TOKEN = "test-broker-token-that-is-at-least-thirty-two-characters"


@pytest.fixture
def short_socket_path() -> Iterator[Path]:
    # Darwin's sockaddr_un path limit is shorter than pytest's nested tmp_path.
    with tempfile.TemporaryDirectory(prefix="agpay-cvc-", dir="/tmp") as directory:
        yield Path(directory) / "broker.sock"


async def test_cvc_broker_is_owner_bound_one_shot_and_private(short_socket_path: Path) -> None:
    socket_path = short_socket_path
    broker = LocalDirectCardCvcBroker(
        socket_path=socket_path,
        broker_token=TOKEN,
        ttl_seconds=60,
        timeout_seconds=1,
    )
    await broker.start()
    try:
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
        client = LocalDirectCardCvcClient(
            socket_path=socket_path,
            broker_token=TOKEN,
            timeout_seconds=1,
        )
        execution_id = uuid4()
        owner_id = uuid4()
        payment_method_id = uuid4()
        await client.put(
            execution_id=execution_id,
            owner_id=owner_id,
            payment_method_id=payment_method_id,
            cvc="123",
        )

        with pytest.raises(CvcUnavailableError):
            await broker.take(
                execution_id=execution_id,
                owner_id=uuid4(),
                payment_method_id=payment_method_id,
            )
        assert (
            await broker.take(
                execution_id=execution_id,
                owner_id=owner_id,
                payment_method_id=payment_method_id,
            )
            == "123"
        )
        with pytest.raises(CvcUnavailableError):
            await broker.take(
                execution_id=execution_id,
                owner_id=owner_id,
                payment_method_id=payment_method_id,
            )
        assert "123" not in repr(broker)
    finally:
        await broker.close()
    assert not socket_path.exists()


async def test_cvc_broker_receipt_discard_and_restart_clear_memory(
    short_socket_path: Path,
) -> None:
    socket_path = short_socket_path
    execution_id = uuid4()
    owner_id = uuid4()
    payment_method_id = uuid4()
    broker = LocalDirectCardCvcBroker(
        socket_path=socket_path,
        broker_token=TOKEN,
        ttl_seconds=60,
        timeout_seconds=1,
    )
    await broker.start()
    client = LocalDirectCardCvcClient(
        socket_path=socket_path,
        broker_token=TOKEN,
        timeout_seconds=1,
    )
    receipt = await client.put(
        execution_id=execution_id,
        owner_id=owner_id,
        payment_method_id=payment_method_id,
        cvc="9876",
    )
    await client.discard(execution_id=execution_id, receipt="wrong-receipt")
    assert (
        await broker.take(
            execution_id=execution_id,
            owner_id=owner_id,
            payment_method_id=payment_method_id,
        )
        == "9876"
    )
    receipt = await client.put(
        execution_id=execution_id,
        owner_id=owner_id,
        payment_method_id=payment_method_id,
        cvc="9876",
    )
    await client.discard(execution_id=execution_id, receipt=receipt)
    with pytest.raises(CvcUnavailableError):
        await broker.take(
            execution_id=execution_id,
            owner_id=owner_id,
            payment_method_id=payment_method_id,
        )
    await broker.close()

    replacement = LocalDirectCardCvcBroker(
        socket_path=socket_path,
        broker_token=TOKEN,
        ttl_seconds=60,
        timeout_seconds=1,
    )
    await replacement.start()
    try:
        with pytest.raises(CvcUnavailableError):
            await replacement.take(
                execution_id=execution_id,
                owner_id=owner_id,
                payment_method_id=payment_method_id,
            )
    finally:
        await replacement.close()


async def test_cvc_broker_expires_without_persistence(short_socket_path: Path) -> None:
    socket_path = short_socket_path
    broker = LocalDirectCardCvcBroker(
        socket_path=socket_path,
        broker_token=TOKEN,
        ttl_seconds=0.01,  # type: ignore[arg-type]
        timeout_seconds=1,
    )
    await broker.start()
    try:
        client = LocalDirectCardCvcClient(
            socket_path=socket_path,
            broker_token=TOKEN,
            timeout_seconds=1,
        )
        execution_id = uuid4()
        owner_id = uuid4()
        payment_method_id = uuid4()
        await client.put(
            execution_id=execution_id,
            owner_id=owner_id,
            payment_method_id=payment_method_id,
            cvc="123",
        )
        await asyncio.sleep(0.02)
        with pytest.raises(CvcUnavailableError):
            await broker.take(
                execution_id=execution_id,
                owner_id=owner_id,
                payment_method_id=payment_method_id,
            )
    finally:
        await broker.close()


async def test_cvc_broker_rejects_permissive_parent_directory(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    broker = LocalDirectCardCvcBroker(
        socket_path=parent / "broker.sock",
        broker_token=TOKEN,
        ttl_seconds=60,
        timeout_seconds=1,
    )

    with pytest.raises(CvcBrokerError, match="relay is unavailable"):
        await broker.start()


async def test_second_broker_cannot_unlink_live_socket_with_different_token(
    short_socket_path: Path,
) -> None:
    first = LocalDirectCardCvcBroker(
        socket_path=short_socket_path,
        broker_token=TOKEN,
        ttl_seconds=60,
        timeout_seconds=1,
    )
    second = LocalDirectCardCvcBroker(
        socket_path=short_socket_path,
        broker_token="different-token-that-is-also-at-least-thirty-two-characters",
        ttl_seconds=60,
        timeout_seconds=1,
    )
    await first.start()
    try:
        with pytest.raises(CvcBrokerError):
            await second.start()
        assert await asyncio.to_thread(short_socket_path.exists)
        client = LocalDirectCardCvcClient(
            socket_path=short_socket_path,
            broker_token=TOKEN,
            timeout_seconds=1,
        )
        execution_id, owner_id, payment_method_id = uuid4(), uuid4(), uuid4()
        await client.put(
            execution_id=execution_id,
            owner_id=owner_id,
            payment_method_id=payment_method_id,
            cvc="123",
        )
        assert (
            await first.take(
                execution_id=execution_id,
                owner_id=owner_id,
                payment_method_id=payment_method_id,
            )
            == "123"
        )
    finally:
        await first.close()
