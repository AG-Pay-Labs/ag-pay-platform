import json
import logging
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class EventBroker:
    """Small Redis Streams publisher for domain events.

    Database writes remain authoritative in the prototype. Publishing is best
    effort until a transactional outbox is introduced.
    """

    def __init__(self, redis: Redis, stream: str = "agpay:domain-events") -> None:
        self.redis = redis
        self.stream = stream

    async def publish(self, event_type: str, payload: dict[str, Any]) -> bool:
        envelope = {
            "type": event_type,
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(payload, default=str, separators=(",", ":")),
        }
        try:
            await self.redis.xadd(self.stream, envelope, maxlen=10_000, approximate=True)
        except RedisError:
            logger.warning("Could not publish domain event %s", event_type, exc_info=True)
            return False
        return True

    async def mark_agent_online(self, agent_id: str, ttl_seconds: int) -> bool:
        try:
            await self.redis.set(f"agpay:agent-presence:{agent_id}", "online", ex=ttl_seconds)
        except RedisError:
            logger.warning("Could not update presence for agent %s", agent_id, exc_info=True)
            return False
        return True
