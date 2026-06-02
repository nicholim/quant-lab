import json
import logging
from datetime import datetime

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis caching layer for latest prices and recent trades."""

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        self._client = redis.from_url(self.redis_url, decode_responses=True)
        await self._client.ping()
        logger.info("Redis connected")

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            logger.info("Redis disconnected")

    async def set_latest_price(self, symbol: str, price: float, timestamp: datetime) -> None:
        """Cache the latest price for a symbol."""
        assert self._client is not None, "connect() must be called first"
        await self._client.hset(
            f"price:{symbol}",
            mapping={"price": str(price), "timestamp": timestamp.isoformat()},
        )

    async def get_latest_price(self, symbol: str) -> dict | None:
        """Retrieve the latest cached price."""
        assert self._client is not None, "connect() must be called first"
        data = await self._client.hgetall(f"price:{symbol}")
        if data:
            return {"price": float(data["price"]), "timestamp": data["timestamp"]}
        return None

    async def push_trade(self, symbol: str, trade_data: dict, max_length: int = 1000) -> None:
        """Append trade to a capped list."""
        assert self._client is not None, "connect() must be called first"
        key = f"trades:{symbol}"
        await self._client.lpush(key, json.dumps(trade_data, default=str))
        await self._client.ltrim(key, 0, max_length - 1)

    async def get_recent_trades(self, symbol: str, count: int = 100) -> list[dict]:
        """Retrieve recent trades from cache."""
        assert self._client is not None, "connect() must be called first"
        key = f"trades:{symbol}"
        raw = await self._client.lrange(key, 0, count - 1)
        return [json.loads(item) for item in raw]

    async def publish(self, channel: str, message: dict) -> None:
        """Publish a message to a Redis pub/sub channel."""
        assert self._client is not None, "connect() must be called first"
        await self._client.publish(channel, json.dumps(message, default=str))
