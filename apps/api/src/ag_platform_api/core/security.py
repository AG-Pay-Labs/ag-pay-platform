import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet, InvalidToken
from pwdlib import PasswordHash

from ag_platform_api.core.config import Settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)


def create_access_token(user_id: str, settings: Settings) -> tuple[str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": user_id, "type": "user", "exp": expires_at, "iat": datetime.now(UTC)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_at


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def new_opaque_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_opaque_token(token: str, settings: Settings) -> str:
    value = f"{settings.jwt_secret}:{token}".encode()
    return hashlib.sha256(value).hexdigest()


def _fernet(settings: Settings) -> Fernet:
    if settings.credential_encryption_key:
        key = settings.credential_encryption_key.encode()
    else:
        digest = hashlib.sha256(settings.jwt_secret.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(secret: str, settings: Settings) -> str:
    return _fernet(settings).encrypt(secret.encode()).decode()


def decrypt_secret(ciphertext: str, settings: Settings) -> str:
    try:
        return _fernet(settings).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Stored secret cannot be decrypted with the configured key") from exc
