from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ag_platform_api.api.dependencies import Broker, DatabaseSession
from ag_platform_api.api.router import api_router
from ag_platform_api.core.config import get_settings
from ag_platform_api.schemas import Message
from ag_platform_api.services.broker import EventBroker

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.broker = EventBroker(redis)
    yield
    await redis.aclose()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Backend for connecting autonomous agents, assigning tokenized payment methods, "
        "approving purchase proposals, and tracking purchases and subscriptions."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/", response_model=Message, include_in_schema=False)
async def root() -> Message:
    return Message(message="AG Platform API")


@app.get("/health/live", response_model=Message, tags=["health"])
async def liveness() -> Message:
    return Message(message="ok")


@app.get("/health/ready", response_model=Message, tags=["health"])
async def readiness(db: DatabaseSession, broker: Broker) -> Message:
    try:
        await db.execute(text("SELECT 1"))
        await broker.redis.ping()
    except (SQLAlchemyError, RedisError) as exc:
        raise HTTPException(status_code=503, detail="A required dependency is unavailable") from exc
    return Message(message="ready")
