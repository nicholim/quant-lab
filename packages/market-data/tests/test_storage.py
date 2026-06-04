"""Tests for TimeSeriesStorage against an in-memory FakePool (no live DB)."""

from datetime import UTC, datetime

import pytest

from src.storage import SCHEMA_SQL, TimeSeriesStorage


@pytest.fixture
def storage(fake_pool):
    s = TimeSeriesStorage("postgresql://fake/db")
    s._pool = fake_pool  # inject fake pool, bypass create_pool
    return s


class TestConnectDisconnect:
    async def test_connect_creates_pool(self, monkeypatch, fake_pool):
        captured = {}

        async def create_pool(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return fake_pool

        monkeypatch.setattr("src.storage.asyncpg.create_pool", create_pool)
        s = TimeSeriesStorage("postgresql://localhost/marketdata")
        await s.connect()
        assert captured["url"] == "postgresql://localhost/marketdata"
        assert captured["kwargs"] == {"min_size": 2, "max_size": 10}
        assert s._pool is fake_pool

    async def test_disconnect_closes_pool(self, storage, fake_pool):
        await storage.disconnect()
        assert fake_pool.closed is True

    async def test_disconnect_without_pool_is_noop(self):
        s = TimeSeriesStorage("postgresql://fake/db")
        await s.disconnect()  # _pool is None


class TestSchema:
    async def test_init_schema_executes_ddl(self, storage, fake_pool):
        await storage.init_schema()
        assert len(fake_pool.executed) == 1
        query, _ = fake_pool.executed[0]
        assert query == SCHEMA_SQL
        assert "create_hypertable" in query


class TestInsertTrades:
    def _trade(self, **over):
        base = {
            "timestamp": datetime(2024, 1, 1, tzinfo=UTC),
            "symbol": "btcusdt",
            "price": 100.0,
            "quantity": 1.0,
            "side": "buy",
            "exchange": "binance",
        }
        base.update(over)
        return base

    async def test_empty_list_is_noop(self, storage, fake_pool):
        await storage.insert_trades([])
        assert fake_pool.executemany_calls == []

    async def test_batch_insert_maps_columns_in_order(self, storage, fake_pool):
        trades = [self._trade(price=1.0), self._trade(price=2.0)]
        await storage.insert_trades(trades)
        assert len(fake_pool.executemany_calls) == 1
        _, rows = fake_pool.executemany_calls[0]
        assert len(rows) == 2
        # tuple order must match (time, symbol, price, quantity, side, exchange)
        first = rows[0]
        assert first[0] == trades[0]["timestamp"]
        assert first[1] == "btcusdt"
        assert first[2] == 1.0
        assert first[4] == "buy"
        assert first[5] == "binance"


class TestInsertOHLCV:
    async def test_insert_bar_passes_all_columns(self, storage, fake_pool):
        bar = {
            "timestamp": datetime(2024, 1, 1, tzinfo=UTC),
            "symbol": "btcusdt",
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 10.0,
            "trade_count": 3,
            "interval": "1m",
        }
        await storage.insert_ohlcv(bar)
        query, args = fake_pool.executed[0]
        assert "INSERT INTO ohlcv" in query
        assert args[1] == "btcusdt"
        assert args[2] == 1.0  # open
        assert args[7] == 3  # trade_count
        assert args[8] == "1m"  # interval


class TestInsertBook:
    async def test_insert_book_serializes_levels_as_json(self, storage, fake_pool):
        book = {
            "timestamp": datetime(2024, 1, 1, tzinfo=UTC),
            "symbol": "btcusdt",
            "bids": [{"price": 100.0, "quantity": 1.5}],
            "asks": [{"price": 101.0, "quantity": 0.8}],
            "exchange": "binance",
        }
        await storage.insert_book(book)
        query, args = fake_pool.executed[0]
        assert "INSERT INTO book" in query
        assert args[1] == "btcusdt"
        # bids/asks are passed as JSON strings (JSONB column).
        import json

        assert json.loads(args[2]) == [{"price": 100.0, "quantity": 1.5}]
        assert json.loads(args[3]) == [{"price": 101.0, "quantity": 0.8}]
        assert args[4] == "binance"


class TestQueries:
    async def test_query_book_passes_bounds_and_decodes_json(self, storage, fake_pool):
        # asyncpg returns JSONB columns as str; query_book must decode them.
        fake_pool.fetch_result = [
            {"symbol": "btcusdt", "bids": '[{"price": 100.0, "quantity": 1.0}]', "asks": "[]"}
        ]
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 2, tzinfo=UTC)
        rows = await storage.query_book("btcusdt", start, end, limit=50)
        assert rows[0]["bids"] == [{"price": 100.0, "quantity": 1.0}]
        assert rows[0]["asks"] == []
        _, args = fake_pool.fetched[0]
        assert args == ("btcusdt", start, end, 50)

    async def test_query_trades_passes_bounds_and_limit(self, storage, fake_pool):
        fake_pool.fetch_result = [{"symbol": "btcusdt", "price": 100.0}]
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 2, tzinfo=UTC)
        rows = await storage.query_trades("btcusdt", start, end, limit=50)
        assert rows == [{"symbol": "btcusdt", "price": 100.0}]
        query, args = fake_pool.fetched[0]
        assert args == ("btcusdt", start, end, 50)

    async def test_query_ohlcv_passes_interval(self, storage, fake_pool):
        fake_pool.fetch_result = []
        start = datetime(2024, 1, 1, tzinfo=UTC)
        end = datetime(2024, 1, 2, tzinfo=UTC)
        rows = await storage.query_ohlcv("btcusdt", "1m", start, end)
        assert rows == []
        _, args = fake_pool.fetched[0]
        assert args == ("btcusdt", "1m", start, end)
