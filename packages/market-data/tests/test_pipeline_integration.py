"""Integration tests for Pipeline with all I/O components faked.

The Pipeline wires together the normalizer, cache, storage, and ws client.
We swap cache/storage/client for in-memory fakes and drive _on_message
directly to exercise buffering, batching boundaries, and OHLCV emission.
"""

import asyncio

import pytest

from src.config import Config
from src.pipeline import Pipeline


class FakeCache:
    def __init__(self):
        self.prices = {}
        self.pushed = []
        self.published = []
        self.connected = False
        self.disconnected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def set_latest_price(self, symbol, price, ts):
        self.prices[symbol] = (price, ts)

    async def push_trade(self, symbol, trade_data, max_length=1000):
        self.pushed.append((symbol, trade_data))

    async def publish(self, channel, message):
        self.published.append((channel, message))


class FakeStorage:
    def __init__(self, fail_inserts=False):
        self.trades_inserted = []
        self.ohlcv_inserted = []
        self.schema_inited = False
        self.connected = False
        self.disconnected = False
        self.fail_inserts = fail_inserts

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def init_schema(self):
        self.schema_inited = True

    async def insert_trades(self, batch):
        if self.fail_inserts:
            raise RuntimeError("db down")
        self.trades_inserted.append(list(batch))

    async def insert_ohlcv(self, bar):
        self.ohlcv_inserted.append(bar)


class FakeClient:
    def __init__(self):
        self.callbacks = []
        self.disconnected = False
        self.connected_symbols = None

    def on_message(self, cb):
        self.callbacks.append(cb)

    async def connect(self, symbols):
        self.connected_symbols = symbols

    async def disconnect(self):
        self.disconnected = True


@pytest.fixture
def pipeline(monkeypatch):
    cfg = Config()
    cfg.symbols = ["btcusdt"]
    cfg.batch_size = 3
    cfg.flush_interval = 0.01
    p = Pipeline(cfg)
    p.cache = FakeCache()
    p.storage = FakeStorage()
    p.client = FakeClient()
    return p


def binance_msg(price="100", qty="1", ts_ms=1712400000000, symbol="BTCUSDT", m=False):
    return {"s": symbol, "p": price, "q": qty, "m": m, "T": ts_ms}


class TestOnMessage:
    async def test_valid_trade_updates_cache_and_buffers(self, pipeline):
        await pipeline._on_message(binance_msg())
        assert "btcusdt" in pipeline.cache.prices
        assert len(pipeline.cache.pushed) == 1
        assert len(pipeline.cache.published) == 1
        assert len(pipeline._trade_buffer) == 1

    async def test_malformed_message_ignored(self, pipeline):
        await pipeline._on_message({"garbage": True})
        assert pipeline.cache.prices == {}
        assert pipeline._trade_buffer == []

    async def test_buffer_flushes_at_batch_size(self, pipeline):
        # batch_size = 3
        for i in range(3):
            await pipeline._on_message(binance_msg(ts_ms=1712400000000 + i))
        # buffer should have flushed exactly once and be empty
        assert pipeline._trade_buffer == []
        assert len(pipeline.storage.trades_inserted) == 1
        assert len(pipeline.storage.trades_inserted[0]) == 3

    async def test_buffer_below_batch_size_not_flushed(self, pipeline):
        await pipeline._on_message(binance_msg())
        await pipeline._on_message(binance_msg(ts_ms=1712400000001))
        assert len(pipeline._trade_buffer) == 2
        assert pipeline.storage.trades_inserted == []

    async def test_ohlcv_bar_persisted_on_rollover(self, pipeline):
        base = 1712400000000  # 2024-04-06 12:00:00.000
        # two trades in minute 0
        await pipeline._on_message(binance_msg(price="100", ts_ms=base))
        await pipeline._on_message(binance_msg(price="110", ts_ms=base + 30_000))
        # trade in minute 1 -> emits bar for minute 0
        await pipeline._on_message(binance_msg(price="120", ts_ms=base + 65_000))
        assert len(pipeline.storage.ohlcv_inserted) == 1
        bar = pipeline.storage.ohlcv_inserted[0]
        assert bar["open"] == 100.0
        assert bar["close"] == 110.0


class TestFlush:
    async def test_flush_empty_buffer_noop(self, pipeline):
        await pipeline._flush_trades()
        assert pipeline.storage.trades_inserted == []

    async def test_flush_clears_buffer(self, pipeline):
        await pipeline._on_message(binance_msg())
        await pipeline._flush_trades()
        assert pipeline._trade_buffer == []
        assert len(pipeline.storage.trades_inserted) == 1

    async def test_flush_failure_restores_buffer(self, pipeline):
        pipeline.storage.fail_inserts = True
        await pipeline._on_message(binance_msg())
        await pipeline._flush_trades()
        # the trade must be re-added so it isn't lost
        assert len(pipeline._trade_buffer) == 1


class TestLifecycle:
    async def test_start_wires_components(self, pipeline):
        pipeline._running = True

        async def fake_connect(symbols):
            pipeline.client.connected_symbols = symbols

        pipeline.client.connect = fake_connect
        await pipeline.start()
        assert pipeline.cache.connected
        assert pipeline.storage.connected
        assert pipeline.storage.schema_inited
        assert pipeline.client.connected_symbols == ["btcusdt"]
        assert len(pipeline.client.callbacks) == 1
        # cancel the periodic flush task spawned by start()
        if pipeline._flush_task:
            pipeline._flush_task.cancel()

    async def test_start_redis_unreachable_logs_actionable_and_reraises(self, pipeline, caplog):
        """If Redis is down, start() logs which env var to fix and re-raises."""

        async def boom_connect():
            raise ConnectionError("Error 61 connecting to redis")

        pipeline.cache.connect = boom_connect
        pipeline._running = True
        with caplog.at_level("ERROR"):
            with pytest.raises(ConnectionError):
                await pipeline.start()
        msg = caplog.text
        assert "Redis" in msg and "REDIS_URL" in msg
        # storage was never reached because we failed fast on the cache step
        assert pipeline.storage.connected is False

    async def test_start_timescale_unreachable_logs_actionable_and_reraises(self, pipeline, caplog):
        """If Timescale is down, start() logs DATABASE_URL guidance and re-raises."""

        async def boom_connect():
            raise OSError("Connect call failed timescale")

        pipeline.storage.connect = boom_connect
        pipeline._running = True
        with caplog.at_level("ERROR"):
            with pytest.raises(OSError):
                await pipeline.start()
        msg = caplog.text
        assert "TimescaleDB" in msg and "DATABASE_URL" in msg
        # cache connected first; the failure is isolated to the storage step
        assert pipeline.cache.connected is True

    async def test_stop_flushes_and_disconnects(self, pipeline):
        await pipeline._on_message(binance_msg())  # leave 1 trade buffered
        # give stop() a live periodic-flush task to cancel
        pipeline._running = True
        pipeline._flush_task = asyncio.create_task(pipeline._periodic_flush())
        await asyncio.sleep(0)  # let the task start
        await pipeline.stop()
        assert pipeline.client.disconnected
        assert pipeline.cache.disconnected
        assert pipeline.storage.disconnected
        # buffered trade flushed on stop
        assert len(pipeline.storage.trades_inserted) >= 1
        assert pipeline._flush_task.cancelled() or pipeline._flush_task.done()

    async def test_periodic_flush_runs_then_cancels(self, pipeline):
        await pipeline._on_message(binance_msg())
        pipeline._running = True
        task = asyncio.create_task(pipeline._periodic_flush())
        await asyncio.sleep(0.03)  # allow at least one flush cycle
        pipeline._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert len(pipeline.storage.trades_inserted) >= 1
