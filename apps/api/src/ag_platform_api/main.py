import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
SAFE_VALIDATION_MESSAGES = frozenset(
    {
        "Managed checkout does not support recurring purchases",
        "Managed checkout requires an absolute HTTPS URL without embedded credentials",
        "The managed checkout currency is not supported.",
        "The managed checkout amount is invalid for its currency.",
    }
)
SAFE_LOCATION_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
SAFE_ERROR_TYPE = re.compile(r"^[a-z0-9_.]{1,64}$")


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


def _sanitized_validation_error(error: dict[str, Any]) -> dict[str, object]:
    raw_location = error.get("loc")
    location: list[str | int] = []
    if isinstance(raw_location, (tuple, list)):
        for part in raw_location[:8]:
            if isinstance(part, int) and 0 <= part <= 1_000_000:
                location.append(part)
            elif isinstance(part, str) and SAFE_LOCATION_PART.fullmatch(part):
                location.append(part)
            else:
                location.append("field")
    if not location:
        location = ["body"]

    raw_type = error.get("type")
    error_type = (
        raw_type
        if isinstance(raw_type, str) and SAFE_ERROR_TYPE.fullmatch(raw_type)
        else "value_error"
    )
    raw_message = error.get("msg")
    message = "Invalid request value."
    if isinstance(raw_message, str):
        for safe_message in SAFE_VALIDATION_MESSAGES:
            if safe_message in raw_message:
                message = safe_message
                break
    if error_type == "missing":
        message = "Field required."
    elif error_type == "extra_forbidden":
        message = "Unexpected field."
    return {"type": error_type, "loc": location, "msg": message}


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    _: Request,
    error: RequestValidationError,
) -> JSONResponse:
    detail = [_sanitized_validation_error(item) for item in error.errors()[:50]]
    return JSONResponse(status_code=422, content={"detail": detail})


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
