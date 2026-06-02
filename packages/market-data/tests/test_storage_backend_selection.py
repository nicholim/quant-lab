"""Tests that config selects the storage backend and the pipeline drives it.

The pipeline is exercised end-to-end with the WS+Redis parts faked (in-memory),
but with a REAL DuckDBStorage backend against a tmp_path DB file — proving the
file sink works interchangeably with Timescale through the same surface and
that nothing requires a running Postgres/Timescale.
"""

from datetime import UTC, datetime

import pytest

from src.config import Config
from src.duckdb_storage import DuckDBStorage
from src.pipeline import Pipeline, build_storage
from src.storage import TimeSeriesStorage


class TestConfigSelection:
    def test_default_backend_is_timescale(self, monkeypatch):
        monkeypatch.delenv("STORAGE_BACKEND", raising=False)
        assert Config().storage_backend == "timescale"

    def test_env_selects_duckdb(self, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "duckdb")
        assert Config().storage_backend == "duckdb"

    def test_env_is_lowercased(self, monkeypatch):
        monkeypatch.setenv("STORAGE_BACKEND", "TimeScale")
        assert Config().storage_backend == "timescale"

    def test_default_duckdb_path(self, monkeypatch):
        monkeypatch.delenv("DUCKDB_PATH", raising=False)
        assert Config().duckdb_path == "data/marketdata.duckdb"

    def test_env_overrides_duckdb_path(self, monkeypatch):
        monkeypatch.setenv("DUCKDB_PATH", "/tmp/x.duckdb")
        assert Config().duckdb_path == "/tmp/x.duckdb"


class TestBuildStorage:
    def test_builds_timescale_by_default(self):
        cfg = Config()
        cfg.storage_backend = "timescale"
        assert isinstance(build_storage(cfg), TimeSeriesStorage)

    def test_builds_duckdb_when_selected(self):
        cfg = Config()
        cfg.storage_backend = "duckdb"
        cfg.duckdb_path = ":memory:"
        storage = build_storage(cfg)
        assert isinstance(storage, DuckDBStorage)
        assert storage.database_path == ":memory:"

    def test_unknown_backend_raises(self):
        cfg = Config()
        cfg.storage_backend = "cassandra"
        with pytest.raises(ValueError, match="Unknown STORAGE_BACKEND"):
            build_storage(cfg)

    def test_pipeline_uses_selected_backend(self):
        cfg = Config()
        cfg.storage_backend = "duckdb"
        cfg.duckdb_path = ":memory:"
        p = Pipeline(cfg)
        assert isinstance(p.storage, DuckDBStorage)


# --- Fakes for the WS + Redis parts (storage stays a REAL DuckDB) ----------


class FakeCache:
    def __init__(self):
        self.connected = False
        self.disconnected = False
        self.prices: dict = {}

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def set_latest_price(self, symbol, price, ts):
        self.prices[symbol] = (price, ts)

    async def push_trade(self, symbol, trade_data, max_length=1000):
        pass

    async def publish(self, channel, message):
        pass


class FakeClient:
    def __init__(self):
        self.callbacks: list = []
        self.connected_symbols = None
        self.disconnected = False

    def on_message(self, cb):
        self.callbacks.append(cb)

    async def connect(self, symbols):
        self.connected_symbols = symbols

    async def disconnect(self):
        self.disconnected = True


def binance_msg(price="100", qty="1", ts_ms=1712400000000, symbol="BTCUSDT", m=False):
    return {"s": symbol, "p": price, "q": qty, "m": m, "T": ts_ms}


@pytest.fixture
async def duckdb_pipeline(tmp_path):
    cfg = Config()
    cfg.symbols = ["btcusdt"]
    cfg.batch_size = 3
    cfg.flush_interval = 0.01
    cfg.storage_backend = "duckdb"
    cfg.duckdb_path = str(tmp_path / "pipe.duckdb")
    p = Pipeline(cfg)
    p.cache = FakeCache()
    p.client = FakeClient()
    # storage is a REAL DuckDBStorage selected by build_storage; init it
    await p.storage.connect()
    await p.storage.init_schema()
    yield p
    await p.storage.disconnect()


class TestPipelineDrivesDuckDB:
    async def test_batch_flush_persists_to_duckdb(self, duckdb_pipeline):
        p = duckdb_pipeline
        base = 1712400000000
        for i in range(3):  # batch_size == 3 -> one flush
            await p._on_message(binance_msg(price=str(100 + i), ts_ms=base + i))
        assert p._trade_buffer == []
        rows = await p.storage.query_trades(
            "btcusdt",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 5, 1, tzinfo=UTC),
        )
        assert len(rows) == 3
        assert sorted(r["price"] for r in rows) == [100.0, 101.0, 102.0]

    async def test_ohlcv_bar_persists_to_duckdb(self, duckdb_pipeline):
        p = duckdb_pipeline
        base = 1712400000000  # minute 0
        await p._on_message(binance_msg(price="100", ts_ms=base))
        await p._on_message(binance_msg(price="110", ts_ms=base + 30_000))
        await p._on_message(binance_msg(price="120", ts_ms=base + 65_000))  # rolls minute 0
        bars = await p.storage.query_ohlcv(
            "btcusdt",
            "1m",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 5, 1, tzinfo=UTC),
        )
        assert len(bars) == 1
        assert bars[0]["open"] == 100.0
        assert bars[0]["close"] == 110.0
        assert bars[0]["high"] == 110.0

    async def test_start_wires_duckdb_no_external_db(self, duckdb_pipeline):
        """start() connects Redis (fake) + the real DuckDB store; no Postgres."""
        p = duckdb_pipeline
        # fresh storage so start() drives connect/init itself
        await p.storage.disconnect()
        p.storage = build_storage(p.config)

        async def fake_connect(symbols):
            p.client.connected_symbols = symbols

        p.client.connect = fake_connect
        await p.start()
        assert p.cache.connected
        assert p.client.connected_symbols == ["btcusdt"]
        # the DuckDB store is live and usable after start()
        await p.storage.insert_trades(
            [
                {
                    "timestamp": datetime(2024, 1, 1, 12, tzinfo=UTC),
                    "symbol": "btcusdt",
                    "price": 1.0,
                    "quantity": 1.0,
                    "side": "buy",
                    "exchange": "binance",
                }
            ]
        )
        rows = await p.storage.query_trades(
            "btcusdt", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 5, 1, tzinfo=UTC)
        )
        assert len(rows) == 1
        if p._flush_task:
            p._flush_task.cancel()

    async def test_start_duckdb_unreadable_path_fails_actionable(self, tmp_path, caplog):
        """If the DuckDB path is unusable, start() logs DUCKDB_PATH guidance + re-raises."""
        cfg = Config()
        cfg.symbols = ["btcusdt"]
        cfg.storage_backend = "duckdb"
        # point at a path whose parent is a FILE -> makedirs/connect fails
        blocker = tmp_path / "afile"
        blocker.write_text("x")
        cfg.duckdb_path = str(blocker / "nested.duckdb")
        p = Pipeline(cfg)
        p.cache = FakeCache()
        p.client = FakeClient()
        with caplog.at_level("ERROR"):
            with pytest.raises(Exception):  # noqa: B017 - OSError/NotADirectoryError
                await p.start()
        assert "DuckDB" in caplog.text and "DUCKDB_PATH" in caplog.text
