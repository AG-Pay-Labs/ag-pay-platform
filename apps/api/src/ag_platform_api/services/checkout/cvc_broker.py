from __future__ import annotations

import asyncio
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

MAX_FRAME_BYTES = 4096


class CvcBrokerError(Exception):
    def __init__(self, message: str = "The local card security-code relay is unavailable") -> None:
        super().__init__(message)


class CvcUnavailableError(Exception):
    def __init__(self) -> None:
        super().__init__("The card security code is unavailable")


class DirectCardCvcStore(Protocol):
    async def take(
        self,
        *,
        execution_id: UUID,
        owner_id: UUID,
        payment_method_id: UUID,
    ) -> str: ...


@dataclass(frozen=True, slots=True, repr=False)
class _CvcEntry:
    owner_id: UUID
    payment_method_id: UUID
    cvc: str
    receipt: str
    expires_at: float

    def __repr__(self) -> str:
        return "_CvcEntry(<redacted>)"


class LocalDirectCardCvcBroker:
    """Worker-owned, one-shot CVC memory reached by the local API over a Unix socket."""

    def __init__(
        self,
        *,
        socket_path: Path,
        broker_token: str,
        ttl_seconds: int,
        timeout_seconds: float,
    ) -> None:
        self._socket_path = socket_path
        self._broker_token = broker_token
        self._ttl_seconds = ttl_seconds
        self._timeout_seconds = timeout_seconds
        self._entries: dict[UUID, _CvcEntry] = {}
        self._server: asyncio.AbstractServer | None = None
        self._socket_identity: tuple[int, int] | None = None

    async def start(self) -> None:
        if self._server is not None:
            return
        parent = self._socket_path.parent
        if parent.is_symlink():
            raise CvcBrokerError()
        if not parent.exists():
            parent.mkdir(parents=True, mode=0o700)
        parent_stat = parent.stat()
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.getuid()
            or parent_stat.st_mode & 0o077
        ):
            raise CvcBrokerError()
        if self._socket_path.exists() or self._socket_path.is_symlink():
            socket_stat = self._socket_path.lstat()
            if (
                not stat.S_ISSOCK(socket_stat.st_mode)
                or socket_stat.st_uid != os.getuid()
                or await self._existing_socket_is_live()
            ):
                raise CvcBrokerError()
            self._socket_path.unlink()
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_connection,
                path=str(self._socket_path),
                limit=MAX_FRAME_BYTES + 1,
            )
            self._socket_path.chmod(0o600)
            created = self._socket_path.stat()
            self._socket_identity = (created.st_dev, created.st_ino)
        except (OSError, ValueError):
            self._server = None
            raise CvcBrokerError() from None

    async def close(self) -> None:
        server, self._server = self._server, None
        self._entries.clear()
        if server is not None:
            server.close()
            await server.wait_closed()
        try:
            current = self._socket_path.lstat()
            identity = (current.st_dev, current.st_ino)
            if stat.S_ISSOCK(current.st_mode) and identity == self._socket_identity:
                self._socket_path.unlink()
        except FileNotFoundError:
            pass
        self._socket_identity = None

    async def take(
        self,
        *,
        execution_id: UUID,
        owner_id: UUID,
        payment_method_id: UUID,
    ) -> str:
        self._prune()
        entry = self._entries.get(execution_id)
        if (
            entry is None
            or entry.owner_id != owner_id
            or entry.payment_method_id != payment_method_id
        ):
            raise CvcUnavailableError()
        self._entries.pop(execution_id, None)
        return entry.cvc

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [key for key, entry in self._entries.items() if entry.expires_at <= now]
        for key in expired:
            self._entries.pop(key, None)

    async def _existing_socket_is_live(self) -> bool:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=self._timeout_seconds,
            )
        except (OSError, TimeoutError):
            return False
        try:
            # A completed connect is sufficient proof that a process owns this
            # socket. Never unlink it merely because that process has a different token.
            return True
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        response: dict[str, object] = {"v": 1, "ok": False, "error": "invalid_request"}
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=self._timeout_seconds)
            if not raw.endswith(b"\n") or len(raw) > MAX_FRAME_BYTES:
                raise ValueError
            request = json.loads(raw)
            if not isinstance(request, dict) or request.get("v") != 1:
                raise ValueError
            token = request.get("token")
            if not isinstance(token, str) or not secrets.compare_digest(token, self._broker_token):
                response = {"v": 1, "ok": False, "error": "unauthorized"}
            elif request.get("op") == "ping" and set(request) == {"v", "op", "token"}:
                response = {"v": 1, "ok": True}
            elif request.get("op") == "put":
                response = self._put(request)
            elif request.get("op") == "discard":
                response = self._discard(request)
        except (TimeoutError, ValueError, TypeError, json.JSONDecodeError):
            pass
        try:
            writer.write(_encode_frame(response))
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    def _put(self, request: dict[str, object]) -> dict[str, object]:
        if set(request) != {
            "v",
            "op",
            "token",
            "execution_id",
            "owner_id",
            "payment_method_id",
            "cvc",
        }:
            return {"v": 1, "ok": False, "error": "invalid_request"}
        try:
            execution_id = UUID(str(request["execution_id"]))
            owner_id = UUID(str(request["owner_id"]))
            payment_method_id = UUID(str(request["payment_method_id"]))
        except ValueError:
            return {"v": 1, "ok": False, "error": "invalid_request"}
        cvc = request.get("cvc")
        if not isinstance(cvc, str) or not cvc.isdigit() or len(cvc) not in {3, 4}:
            return {"v": 1, "ok": False, "error": "invalid_request"}
        self._prune()
        if execution_id in self._entries:
            return {"v": 1, "ok": False, "error": "already_staged"}
        receipt = secrets.token_urlsafe(24)
        self._entries[execution_id] = _CvcEntry(
            owner_id=owner_id,
            payment_method_id=payment_method_id,
            cvc=cvc,
            receipt=receipt,
            expires_at=time.monotonic() + self._ttl_seconds,
        )
        return {"v": 1, "ok": True, "receipt": receipt}

    def _discard(self, request: dict[str, object]) -> dict[str, object]:
        if set(request) != {"v", "op", "token", "execution_id", "receipt"}:
            return {"v": 1, "ok": False, "error": "invalid_request"}
        try:
            execution_id = UUID(str(request["execution_id"]))
        except ValueError:
            return {"v": 1, "ok": False, "error": "invalid_request"}
        receipt = request.get("receipt")
        entry = self._entries.get(execution_id)
        if (
            isinstance(receipt, str)
            and entry is not None
            and secrets.compare_digest(receipt, entry.receipt)
        ):
            self._entries.pop(execution_id, None)
        return {"v": 1, "ok": True}


class LocalDirectCardCvcClient:
    def __init__(
        self,
        *,
        socket_path: Path,
        broker_token: str,
        timeout_seconds: float,
    ) -> None:
        self._socket_path = socket_path
        self._broker_token = broker_token
        self._timeout_seconds = timeout_seconds

    async def put(
        self,
        *,
        execution_id: UUID,
        owner_id: UUID,
        payment_method_id: UUID,
        cvc: str,
    ) -> str:
        response = await self._request(
            {
                "v": 1,
                "op": "put",
                "token": self._broker_token,
                "execution_id": str(execution_id),
                "owner_id": str(owner_id),
                "payment_method_id": str(payment_method_id),
                "cvc": cvc,
            }
        )
        receipt = response.get("receipt")
        if not response.get("ok") or not isinstance(receipt, str):
            raise CvcBrokerError()
        return receipt

    async def discard(self, *, execution_id: UUID, receipt: str) -> None:
        try:
            await self._request(
                {
                    "v": 1,
                    "op": "discard",
                    "token": self._broker_token,
                    "execution_id": str(execution_id),
                    "receipt": receipt,
                }
            )
        except CvcBrokerError:
            pass

    async def _request(self, payload: dict[str, object]) -> dict[str, object]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=self._timeout_seconds,
            )
            try:
                writer.write(_encode_frame(payload))
                await asyncio.wait_for(writer.drain(), timeout=self._timeout_seconds)
                raw = await asyncio.wait_for(reader.readline(), timeout=self._timeout_seconds)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
            if not raw.endswith(b"\n") or len(raw) > MAX_FRAME_BYTES:
                raise ValueError
            response = json.loads(raw)
            if not isinstance(response, dict) or response.get("v") != 1:
                raise ValueError
            return response
        except (OSError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
            raise CvcBrokerError() from None


def _encode_frame(payload: dict[str, object]) -> bytes:
    encoded = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > MAX_FRAME_BYTES:
        raise CvcBrokerError()
    return encoded
