"""Tests for RedisCache against an in-memory FakeRedis (no live Redis)."""

import json
from datetime import UTC, datetime

import pytest

from src.cache import RedisCache


@pytest.fixture
def cache(fake_redis):
    c = RedisCache("redis://fake:6379")
    c._client = fake_redis  # inject the fake, bypassing connect()
    return c


class TestConnectDisconnect:
    async def test_connect_uses_from_url_and_pings(self, monkeypatch, fake_redis):
        captured = {}

        def from_url(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return fake_redis

        monkeypatch.setattr("src.cache.redis.from_url", from_url)
        c = RedisCache("redis://localhost:6379")
        await c.connect()
        assert captured["url"] == "redis://localhost:6379"
        assert captured["kwargs"]["decode_responses"] is True
        assert fake_redis.pinged is True

    async def test_disconnect_closes_client(self, cache, fake_redis):
        await cache.disconnect()
        assert fake_redis.closed is True

    async def test_disconnect_without_client_is_noop(self):
        c = RedisCache("redis://fake")
        await c.disconnect()  # _client is None; must not raise


class TestLatestPrice:
    async def test_set_and_get_round_trip(self, cache):
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        await cache.set_latest_price("btcusdt", 67500.5, ts)
        result = await cache.get_latest_price("btcusdt")
        assert result == {"price": 67500.5, "timestamp": ts.isoformat()}

    async def test_get_missing_symbol_returns_none(self, cache):
        assert await cache.get_latest_price("nope") is None

    async def test_price_stored_as_string_hash(self, cache, fake_redis):
        ts = datetime(2024, 1, 1, tzinfo=UTC)
        await cache.set_latest_price("eth", 3000.0, ts)
        stored = fake_redis.hashes["price:eth"]
        assert stored["price"] == "3000.0"  # stringified for redis hash
        assert stored["timestamp"] == ts.isoformat()


class TestTradeList:
    async def test_push_trade_prepends(self, cache, fake_redis):
        await cache.push_trade("btc", {"id": 1})
        await cache.push_trade("btc", {"id": 2})
        stored = fake_redis.lists["trades:btc"]
        # lpush puts newest first
        assert json.loads(stored[0]) == {"id": 2}
        assert json.loads(stored[1]) == {"id": 1}

    async def test_push_trade_respects_max_length(self, cache, fake_redis):
        for i in range(10):
            await cache.push_trade("btc", {"id": i}, max_length=3)
        stored = fake_redis.lists["trades:btc"]
        assert len(stored) == 3
        # the 3 newest survive (ids 9, 8, 7)
        ids = [json.loads(x)["id"] for x in stored]
        assert ids == [9, 8, 7]

    async def test_push_trade_serializes_datetime(self, cache, fake_redis):
        ts = datetime(2024, 1, 1, tzinfo=UTC)
        await cache.push_trade("btc", {"timestamp": ts})
        raw = fake_redis.lists["trades:btc"][0]
        # default=str makes datetime JSON-safe
        assert json.loads(raw)["timestamp"] == str(ts)

    async def test_get_recent_trades_count_limit(self, cache):
        for i in range(5):
            await cache.push_trade("btc", {"id": i})
        recent = await cache.get_recent_trades("btc", count=2)
        assert len(recent) == 2
        assert recent[0]["id"] == 4  # newest first

    async def test_get_recent_trades_empty(self, cache):
        assert await cache.get_recent_trades("btc") == []


class TestPublish:
    async def test_publish_serializes_message(self, cache, fake_redis):
        ts = datetime(2024, 1, 1, tzinfo=UTC)
        await cache.publish("trades:btc", {"price": 100, "ts": ts})
        channel, payload = fake_redis.published[0]
        assert channel == "trades:btc"
        decoded = json.loads(payload)
        assert decoded["price"] == 100
        assert decoded["ts"] == str(ts)
