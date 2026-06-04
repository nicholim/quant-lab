"""Tests for DuckDBStorage — round-trips against a real tmp_path DuckDB file.

DuckDB is embedded (no server, no network), so these use a real on-disk DB in a
pytest ``tmp_path`` rather than a fake. Timestamps are timezone-aware, so the
DB may return them in the local tz; equality is by instant, which is what we
assert.
"""

from datetime import UTC, datetime

import pytest

from src.duckdb_storage import _OHLCV_COLUMNS, _TRADE_COLUMNS, DuckDBStorage
from src.storage import TimeSeriesStorage
from src.storage_backend import StorageBackend


def _trade(**over):
    base = {
        "timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        "symbol": "btcusdt",
        "price": 100.0,
        "quantity": 1.5,
        "side": "buy",
        "exchange": "binance",
    }
    base.update(over)
    return base


def _bar(**over):
    base = {
        "timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        "symbol": "btcusdt",
        "open": 100.0,
        "high": 110.0,
        "low": 99.0,
        "close": 105.0,
        "volume": 3.0,
        "trade_count": 2,
        "interval": "1m",
    }
    base.update(over)
    return base


@pytest.fixture
async def storage(tmp_path):
    s = DuckDBStorage(str(tmp_path / "market.duckdb"))
    await s.connect()
    await s.init_schema()
    yield s
    await s.disconnect()


class TestProtocolConformance:
    def test_duckdb_is_a_storage_backend(self):
        assert isinstance(DuckDBStorage(":memory:"), StorageBackend)

    def test_timescale_is_a_storage_backend(self):
        # The existing Timescale storage conforms to the same protocol.
        assert isinstance(TimeSeriesStorage("postgresql://x/db"), StorageBackend)


class TestConnect:
    async def test_connect_creates_file_and_parent_dir(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "market.duckdb"
        s = DuckDBStorage(str(path))
        await s.connect()
        await s.init_schema()
        await s.disconnect()
        assert path.exists()

    async def test_disconnect_without_connect_is_noop(self):
        s = DuckDBStorage(":memory:")
        await s.disconnect()  # no connection yet

    async def test_methods_require_connect(self):
        s = DuckDBStorage(":memory:")
        with pytest.raises(RuntimeError, match="connect"):
            await s.init_schema()


class TestTradesRoundTrip:
    async def test_insert_then_query_round_trips(self, storage):
        trades = [_trade(price=100.0, quantity=1.0), _trade(price=110.0, quantity=2.0)]
        await storage.insert_trades(trades)
        rows = await storage.query_trades(
            "btcusdt",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )
        assert len(rows) == 2
        prices = sorted(r["price"] for r in rows)
        assert prices == [100.0, 110.0]
        # full column set + values preserved
        r = next(r for r in rows if r["price"] == 100.0)
        assert r["symbol"] == "btcusdt"
        assert r["quantity"] == 1.0
        assert r["side"] == "buy"
        assert r["exchange"] == "binance"
        # timestamp preserved as the same instant (tz may differ)
        assert r["time"] == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    async def test_empty_batch_is_noop(self, storage):
        await storage.insert_trades([])
        rows = await storage.query_trades(
            "btcusdt", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert rows == []

    async def test_query_returns_columns_matching_timescale_schema(self, storage):
        await storage.insert_trades([_trade()])
        rows = await storage.query_trades(
            "btcusdt", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert tuple(rows[0].keys()) == _TRADE_COLUMNS
        assert _TRADE_COLUMNS == ("time", "symbol", "price", "quantity", "side", "exchange")

    async def test_query_respects_time_bounds_and_symbol(self, storage):
        await storage.insert_trades(
            [
                _trade(symbol="btcusdt", timestamp=datetime(2024, 1, 1, 12, tzinfo=UTC)),
                _trade(symbol="btcusdt", timestamp=datetime(2024, 1, 3, 12, tzinfo=UTC)),
                _trade(symbol="ethusdt", timestamp=datetime(2024, 1, 1, 12, tzinfo=UTC)),
            ]
        )
        rows = await storage.query_trades(
            "btcusdt",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )
        assert len(rows) == 1
        assert rows[0]["time"] == datetime(2024, 1, 1, 12, tzinfo=UTC)

    async def test_query_limit(self, storage):
        await storage.insert_trades(
            [_trade(timestamp=datetime(2024, 1, 1, 12, 0, i, tzinfo=UTC)) for i in range(5)]
        )
        rows = await storage.query_trades(
            "btcusdt",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
            limit=3,
        )
        assert len(rows) == 3

    async def test_types_match_timescale(self, storage):
        await storage.insert_trades([_trade()])
        rows = await storage.query_trades(
            "btcusdt", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        r = rows[0]
        assert isinstance(r["price"], float)
        assert isinstance(r["quantity"], float)
        assert isinstance(r["symbol"], str)
        assert isinstance(r["time"], datetime)
        assert r["time"].tzinfo is not None


class TestOHLCVRoundTrip:
    async def test_insert_then_query_round_trips(self, storage):
        await storage.insert_ohlcv(_bar())
        bars = await storage.query_ohlcv(
            "btcusdt",
            "1m",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )
        assert len(bars) == 1
        b = bars[0]
        assert b["open"] == 100.0
        assert b["high"] == 110.0
        assert b["low"] == 99.0
        assert b["close"] == 105.0
        assert b["volume"] == 3.0
        assert b["trade_count"] == 2
        assert b["time"] == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    async def test_query_columns_match_timescale_schema(self, storage):
        await storage.insert_ohlcv(_bar())
        bars = await storage.query_ohlcv(
            "btcusdt", "1m", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert tuple(bars[0].keys()) == _OHLCV_COLUMNS

    async def test_interval_filter(self, storage):
        await storage.insert_ohlcv(_bar(interval="1m"))
        await storage.insert_ohlcv(_bar(interval="5m", open=200.0))
        bars = await storage.query_ohlcv(
            "btcusdt", "5m", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert len(bars) == 1
        assert bars[0]["open"] == 200.0

    async def test_ordered_ascending(self, storage):
        await storage.insert_ohlcv(_bar(timestamp=datetime(2024, 1, 1, 12, 2, tzinfo=UTC)))
        await storage.insert_ohlcv(_bar(timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=UTC)))
        await storage.insert_ohlcv(_bar(timestamp=datetime(2024, 1, 1, 12, 1, tzinfo=UTC)))
        bars = await storage.query_ohlcv(
            "btcusdt", "1m", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        times = [b["time"] for b in bars]
        assert times == sorted(times)


class TestPersistenceAndExport:
    async def test_data_persists_across_reconnect(self, tmp_path):
        path = str(tmp_path / "persist.duckdb")
        s1 = DuckDBStorage(path)
        await s1.connect()
        await s1.init_schema()
        await s1.insert_trades([_trade()])
        await s1.disconnect()

        s2 = DuckDBStorage(path)
        await s2.connect()
        rows = await s2.query_trades(
            "btcusdt", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        await s2.disconnect()
        assert len(rows) == 1

    async def test_export_parquet_writes_files(self, storage, tmp_path):
        await storage.insert_trades([_trade()])
        await storage.insert_ohlcv(_bar())
        out = tmp_path / "export"
        paths = await storage.export_parquet(str(out))
        assert (out / "trades.parquet").exists()
        assert (out / "ohlcv.parquet").exists()
        # The L2 depth `book` table is also exported (empty here, but written).
        assert (out / "book.parquet").exists()
        assert set(paths) == {"trades", "ohlcv", "book"}
