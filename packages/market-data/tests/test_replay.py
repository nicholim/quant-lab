"""Tests for Pipeline.replay() — streaming stored records back in time order.

replay only depends on the StorageBackend read API (query_trades / query_ohlcv),
so it is exercised against BOTH a real tmp_path DuckDB store and an in-memory
fake backend, proving it is backend-agnostic.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.config import Config
from src.duckdb_storage import DuckDBStorage
from src.pipeline import Pipeline

BASE = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _trade_dict(price, ts, symbol="btcusdt", qty=1.0, side="buy"):
    return {
        "timestamp": ts,
        "symbol": symbol,
        "price": price,
        "quantity": qty,
        "side": side,
        "exchange": "binance",
    }


def _bar_dict(ts, open_, high, low, close, symbol="btcusdt"):
    return {
        "timestamp": ts,
        "symbol": symbol,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
        "trade_count": 2,
        "interval": "1m",
    }


class FakeReadBackend:
    """In-memory StorageBackend exposing only the read API replay uses.

    query_trades returns most-recent-first (like both real backends) so we can
    prove replay re-sorts to oldest-first.
    """

    def __init__(self, trades=None, bars=None):
        self._trades = list(trades or [])
        self._bars = list(bars or [])

    async def connect(self): ...
    async def disconnect(self): ...
    async def init_schema(self): ...
    async def insert_trades(self, trades): ...
    async def insert_ohlcv(self, bar): ...

    async def query_trades(self, symbol, start, end, limit=10000):
        rows = [
            {
                "time": t["timestamp"],
                "symbol": t["symbol"],
                "price": t["price"],
                "quantity": t["quantity"],
                "side": t["side"],
                "exchange": t["exchange"],
            }
            for t in self._trades
            if t["symbol"] == symbol and start <= t["timestamp"] < end
        ]
        rows.sort(key=lambda r: r["time"], reverse=True)  # most-recent-first
        return rows[:limit]

    async def query_ohlcv(self, symbol, interval, start, end):
        rows = [
            {
                "time": b["timestamp"],
                "symbol": b["symbol"],
                "open": b["open"],
                "high": b["high"],
                "low": b["low"],
                "close": b["close"],
                "volume": b["volume"],
                "trade_count": b["trade_count"],
            }
            for b in self._bars
            if b["symbol"] == symbol and b["interval"] == interval and start <= b["timestamp"] < end
        ]
        rows.sort(key=lambda r: r["time"])  # oldest-first
        return rows


def _pipeline_with(storage):
    p = Pipeline(Config())
    p.storage = storage
    return p


async def _collect(agen):
    return [r async for r in agen]


@pytest.fixture
async def duckdb_store(tmp_path):
    store = DuckDBStorage(str(tmp_path / "replay.duckdb"))
    await store.connect()
    await store.init_schema()
    yield store
    await store.disconnect()


class TestReplayTradesFake:
    async def test_yields_oldest_first(self):
        # insert deliberately out of order
        trades = [
            _trade_dict(102, BASE + timedelta(seconds=2)),
            _trade_dict(100, BASE),
            _trade_dict(101, BASE + timedelta(seconds=1)),
        ]
        p = _pipeline_with(FakeReadBackend(trades=trades))
        out = await _collect(p.replay("btcusdt", BASE, BASE + timedelta(minutes=1)))
        assert [r["price"] for r in out] == [100, 101, 102]
        assert [r["time"] for r in out] == sorted(r["time"] for r in out)

    async def test_window_is_half_open(self):
        trades = [
            _trade_dict(100, BASE),
            _trade_dict(101, BASE + timedelta(seconds=30)),
            _trade_dict(999, BASE + timedelta(minutes=1)),  # == end, excluded
        ]
        p = _pipeline_with(FakeReadBackend(trades=trades))
        out = await _collect(p.replay("btcusdt", BASE, BASE + timedelta(minutes=1)))
        assert [r["price"] for r in out] == [100, 101]

    async def test_symbol_isolation(self):
        trades = [
            _trade_dict(100, BASE, symbol="btcusdt"),
            _trade_dict(50, BASE + timedelta(seconds=1), symbol="ethusdt"),
        ]
        p = _pipeline_with(FakeReadBackend(trades=trades))
        out = await _collect(p.replay("ethusdt", BASE, BASE + timedelta(minutes=1)))
        assert [r["price"] for r in out] == [50]

    async def test_empty_window_yields_nothing(self):
        p = _pipeline_with(FakeReadBackend(trades=[_trade_dict(100, BASE)]))
        later = BASE + timedelta(hours=1)
        out = await _collect(p.replay("btcusdt", later, later + timedelta(minutes=1)))
        assert out == []

    async def test_paging_across_multiple_pages(self):
        # 5 trades, page_size=2 -> 3 pages, must come out fully ordered once.
        trades = [_trade_dict(100 + i, BASE + timedelta(seconds=i)) for i in range(5)]
        p = _pipeline_with(FakeReadBackend(trades=trades))
        out = await _collect(p.replay("btcusdt", BASE, BASE + timedelta(minutes=1), page_size=2))
        assert [r["price"] for r in out] == [100, 101, 102, 103, 104]
        assert len(out) == 5

    async def test_duplicate_boundary_timestamp_deduped(self):
        # A tie at the page boundary: seconds [0, 0, 1, 2]. With page_size=2 the
        # backward pager reads {2,1}, narrows upper to just past ts=0, then reads
        # {0,0} again — both rows at ts=0 are captured AND the row re-read across
        # the boundary is de-duplicated (each distinct trade yielded once).
        trades = [
            _trade_dict(100, BASE, qty=1.0),
            _trade_dict(200, BASE, qty=2.0),  # second distinct trade at ts=0
            _trade_dict(101, BASE + timedelta(seconds=1)),
            _trade_dict(102, BASE + timedelta(seconds=2)),
        ]
        p = _pipeline_with(FakeReadBackend(trades=trades))
        out = await _collect(p.replay("btcusdt", BASE, BASE + timedelta(minutes=1), page_size=2))
        assert len(out) == 4
        assert sorted(r["price"] for r in out) == [100, 101, 102, 200]
        # oldest-first overall
        assert [r["time"] for r in out] == sorted(r["time"] for r in out)

    async def test_more_ties_than_page_size_documented_limit(self):
        # Honest documentation of the read-API limitation: query_trades has no
        # offset, so if MORE rows than page_size share one exact timestamp the
        # extra rows cannot be reached. page_size must exceed the worst-case
        # same-timestamp burst. Here page_size=2 with 3 rows at one ts.
        trades = [_trade_dict(100 + i, BASE, qty=1.0 + i) for i in range(3)]
        p = _pipeline_with(FakeReadBackend(trades=trades))
        out = await _collect(p.replay("btcusdt", BASE, BASE + timedelta(minutes=1), page_size=2))
        # Only page_size rows are reachable when all share one timestamp.
        assert len(out) == 2
        # A page_size that covers the burst gets them all.
        out_full = await _collect(
            p.replay("btcusdt", BASE, BASE + timedelta(minutes=1), page_size=10)
        )
        assert len(out_full) == 3

    async def test_bad_source_raises(self):
        p = _pipeline_with(FakeReadBackend())
        with pytest.raises(ValueError, match="Unknown replay source"):
            await _collect(p.replay("btcusdt", BASE, BASE + timedelta(minutes=1), source="nope"))


class TestReplayOhlcvFake:
    async def test_yields_bars_oldest_first(self):
        bars = [
            _bar_dict(BASE + timedelta(minutes=2), 102, 103, 101, 102),
            _bar_dict(BASE, 100, 101, 99, 100),
            _bar_dict(BASE + timedelta(minutes=1), 101, 102, 100, 101),
        ]
        p = _pipeline_with(FakeReadBackend(bars=bars))
        out = await _collect(
            p.replay("btcusdt", BASE, BASE + timedelta(minutes=10), source="ohlcv")
        )
        assert [r["open"] for r in out] == [100, 101, 102]


class TestReplayDuckDB:
    async def test_trades_round_trip_ordered(self, duckdb_store):
        trades = [
            _trade_dict(102, BASE + timedelta(seconds=2)),
            _trade_dict(100, BASE),
            _trade_dict(101, BASE + timedelta(seconds=1)),
        ]
        await duckdb_store.insert_trades(trades)
        p = _pipeline_with(duckdb_store)
        out = await _collect(p.replay("btcusdt", BASE, BASE + timedelta(minutes=1)))
        assert [r["price"] for r in out] == [100, 101, 102]
        assert all(r["symbol"] == "btcusdt" for r in out)

    async def test_trades_paged_round_trip(self, duckdb_store):
        trades = [_trade_dict(100 + i, BASE + timedelta(seconds=i)) for i in range(7)]
        await duckdb_store.insert_trades(trades)
        p = _pipeline_with(duckdb_store)
        out = await _collect(p.replay("btcusdt", BASE, BASE + timedelta(minutes=1), page_size=3))
        assert [r["price"] for r in out] == [100, 101, 102, 103, 104, 105, 106]

    async def test_ohlcv_round_trip_ordered(self, duckdb_store):
        await duckdb_store.insert_ohlcv(_bar_dict(BASE + timedelta(minutes=1), 101, 102, 100, 101))
        await duckdb_store.insert_ohlcv(_bar_dict(BASE, 100, 101, 99, 100))
        p = _pipeline_with(duckdb_store)
        out = await _collect(
            p.replay("btcusdt", BASE, BASE + timedelta(minutes=10), source="ohlcv")
        )
        assert [r["open"] for r in out] == [100, 101]
