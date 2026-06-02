"""Shared fixtures and fakes for the market-data-pipeline test suite.

Everything here is in-memory: no live WebSocket, Redis, or TimescaleDB is touched.
"""

from datetime import UTC, datetime

import pytest

from src.normalizer import Trade

# --- Sample data ----------------------------------------------------------


@pytest.fixture
def binance_trade_msg():
    """A well-formed Binance @trade payload."""
    return {
        "s": "BTCUSDT",
        "p": "67500.50",
        "q": "0.15",
        "m": False,
        "T": 1712400000000,
    }


def make_trade(symbol="btc", price=100.0, qty=1.0, side="buy", ts=None):
    """Build a Trade with a sensible default timestamp."""
    if ts is None:
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    return Trade(symbol=symbol, price=price, quantity=qty, side=side, timestamp=ts)


@pytest.fixture
def trade_factory():
    return make_trade


# --- Fake WebSocket -------------------------------------------------------


class FakeWebSocket:
    """Async-iterable stand-in for a websockets connection.

    Yields the supplied raw messages, then stops iteration (simulating the
    server closing cleanly). Records whether ``close`` was awaited.
    """

    def __init__(self, messages=None, raise_on_iter=None):
        self._messages = list(messages or [])
        self._raise_on_iter = raise_on_iter
        self.closed = False
        self.sent: list = []

    async def send(self, message):
        """Record an outbound message (e.g. a subscribe payload)."""
        self.sent.append(message)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for msg in self._messages:
            yield msg
        if self._raise_on_iter is not None:
            raise self._raise_on_iter

    async def close(self):
        self.closed = True

    # support `async with websockets.connect(...) as ws`
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False


@pytest.fixture
def fake_ws_factory():
    return FakeWebSocket


# --- Fake Redis -----------------------------------------------------------


class FakeRedis:
    """Minimal in-memory async fake of the redis.asyncio.Redis surface used.

    Implements only the commands RedisCache calls: ping, hset, hgetall,
    lpush, ltrim, lrange, publish, aclose.
    """

    def __init__(self):
        self.hashes: dict[str, dict] = {}
        self.lists: dict[str, list] = {}
        self.published: list[tuple[str, str]] = []
        self.closed = False
        self.pinged = False

    async def ping(self):
        self.pinged = True
        return True

    async def hset(self, key, mapping=None, **kwargs):
        self.hashes.setdefault(key, {})
        if mapping:
            self.hashes[key].update(mapping)
        self.hashes[key].update(kwargs)
        return 1

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def lpush(self, key, *values):
        lst = self.lists.setdefault(key, [])
        for v in values:
            lst.insert(0, v)
        return len(lst)

    async def ltrim(self, key, start, stop):
        lst = self.lists.get(key, [])
        # Redis ltrim keeps [start, stop] inclusive.
        self.lists[key] = lst[start : stop + 1]
        return True

    async def lrange(self, key, start, stop):
        lst = self.lists.get(key, [])
        if stop == -1:
            return lst[start:]
        return lst[start : stop + 1]

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 1

    async def aclose(self):
        self.closed = True


@pytest.fixture
def fake_redis():
    return FakeRedis()


# --- Fake asyncpg pool ----------------------------------------------------


class FakeConnection:
    def __init__(self, recorder):
        self._rec = recorder

    async def execute(self, query, *args):
        self._rec.executed.append((query, args))
        return "OK"

    async def executemany(self, query, args_iter):
        rows = list(args_iter)
        self._rec.executemany_calls.append((query, rows))
        return None

    async def fetch(self, query, *args):
        self._rec.fetched.append((query, args))
        return self._rec.fetch_result


class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    """In-memory fake of an asyncpg pool."""

    def __init__(self):
        self.executed: list = []
        self.executemany_calls: list = []
        self.fetched: list = []
        self.fetch_result: list = []
        self.closed = False

    def acquire(self):
        return _AcquireCtx(FakeConnection(self))

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_pool():
    return FakePool()
