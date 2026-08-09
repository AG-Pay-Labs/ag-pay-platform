from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ag_platform_api.api.dependencies import get_broker
from ag_platform_api.core.config import Settings, get_settings
from ag_platform_api.db.base import Base
from ag_platform_api.db.session import get_db
from ag_platform_api.main import app


class FakeRedis:
    async def ping(self) -> bool:
        return True


class FakeBroker:
    def __init__(self) -> None:
        self.redis = FakeRedis()
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.online_agents: dict[str, int] = {}

    async def publish(self, event_type: str, payload: dict[str, Any]) -> bool:
        self.events.append((event_type, payload))
        return True

    async def mark_agent_online(self, agent_id: str, ttl_seconds: int) -> bool:
        self.online_agents[agent_id] = ttl_seconds
        return True


@pytest.fixture
def broker() -> FakeBroker:
    return FakeBroker()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api-tests.sqlite3'}",
        redis_url="redis://unused:6379/15",
        jwt_secret="test-secret-that-is-longer-than-thirty-two-characters",
        access_token_expire_minutes=5,
        agent_token_expire_days=1,
        pairing_token_expire_minutes=5,
        agent_online_window_seconds=60,
    )


@pytest_asyncio.fixture
async def db_session_factory(
    settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(settings.database_url)

    @event.listens_for(engine.sync_engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection: Any, _: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    yield session_factory
    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    settings: Settings,
    broker: FakeBroker,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_broker] = lambda: broker

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
        yield test_client

    app.dependency_overrides.clear()
