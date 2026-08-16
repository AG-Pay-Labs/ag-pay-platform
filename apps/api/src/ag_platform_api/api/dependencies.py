from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ag_platform_api.core.config import Settings, get_settings
from ag_platform_api.core.security import decode_access_token, hash_opaque_token
from ag_platform_api.db.session import SessionFactory, get_db
from ag_platform_api.models import Agent, AgentStatus, User
from ag_platform_api.services.broker import EventBroker
from ag_platform_api.services.checkout.reconciliation import (
    LandingPaymentVerificationClient,
    TrustedPaymentVerifier,
)

bearer_scheme = HTTPBearer(auto_error=False)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_database_session_factory() -> async_sessionmaker[AsyncSession]:
    return SessionFactory


DatabaseSessionFactory = Annotated[
    async_sessionmaker[AsyncSession], Depends(get_database_session_factory)
]


async def get_payment_verifier() -> AsyncIterator[TrustedPaymentVerifier]:
    verifier = LandingPaymentVerificationClient()
    try:
        yield verifier
    finally:
        await verifier.close()


PaymentVerifier = Annotated[TrustedPaymentVerifier, Depends(get_payment_verifier)]


def utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def get_current_user(
    credentials: BearerCredentials,
    db: DatabaseSession,
    settings: AppSettings,
) -> User:
    exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired user access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise exception
    try:
        payload = decode_access_token(credentials.credentials, settings)
        if payload.get("type") != "user":
            raise exception
        user_id = UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise exception from exc

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise exception
    return user


async def get_current_agent(
    credentials: BearerCredentials,
    db: DatabaseSession,
    settings: AppSettings,
) -> Agent:
    exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired agent access token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or not credentials.credentials.startswith("agt_"):
        raise exception

    token_hash = hash_opaque_token(credentials.credentials, settings)
    agent = await db.scalar(select(Agent).where(Agent.api_key_hash == token_hash))
    now = datetime.now(UTC)
    if (
        agent is None
        or agent.status is not AgentStatus.active
        or agent.api_key_expires_at is None
        or utc(agent.api_key_expires_at) <= now
    ):
        raise exception
    return agent


CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAgent = Annotated[Agent, Depends(get_current_agent)]


def get_broker(request: Request) -> EventBroker:
    return request.app.state.broker


Broker = Annotated[EventBroker, Depends(get_broker)]
