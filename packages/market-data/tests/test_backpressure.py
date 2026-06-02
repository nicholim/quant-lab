"""Tests for the bounded-buffer / backpressure cap on the trade buffer.

Policy under test:
- Primary: when the buffer hits ``max_buffer_size`` the pipeline awaits an
  inline flush (block / back-pressure the consumer) — lossless if the sink is
  healthy.
- Last resort: if the sink is STILL unreachable after that flush (the failed
  flush re-added its batch), drop the oldest trades down to the cap to enforce
  a hard memory bound, logging a running dropped count (never silent).
"""

import logging

from src.config import Config
from src.pipeline import Pipeline


class FakeCache:
    async def connect(self): ...
    async def disconnect(self): ...

    async def set_latest_price(self, symbol, price, ts): ...
    async def push_trade(self, symbol, trade_data, max_length=1000): ...
    async def publish(self, channel, message): ...


class FakeStorage:
    def __init__(self, fail_inserts=False):
        self.trades_inserted = []
        self.ohlcv_inserted = []
        self.fail_inserts = fail_inserts

    async def connect(self): ...
    async def disconnect(self): ...
    async def init_schema(self): ...

    async def insert_trades(self, batch):
        if self.fail_inserts:
            raise RuntimeError("db down")
        self.trades_inserted.append(list(batch))

    async def insert_ohlcv(self, bar):
        self.ohlcv_inserted.append(bar)


def _pipeline(batch_size, max_buffer_size, fail_inserts=False):
    cfg = Config()
    cfg.symbols = ["btcusdt"]
    cfg.batch_size = batch_size
    cfg.max_buffer_size = max_buffer_size
    p = Pipeline(cfg)
    p.cache = FakeCache()
    p.storage = FakeStorage(fail_inserts=fail_inserts)
    return p


def msg(price="100", ts_ms=1712400000000, m=False):
    return {"s": "BTCUSDT", "p": price, "q": "1", "m": m, "T": ts_ms}


class TestBackpressureHealthySink:
    async def test_buffer_never_exceeds_cap_when_sink_healthy(self):
        # batch_size huge so the normal batch flush never triggers; only the
        # backpressure cap (3) governs the buffer size.
        p = _pipeline(batch_size=10_000, max_buffer_size=3)
        for i in range(20):
            await p._on_message(msg(ts_ms=1712400000000 + i))
            assert len(p._trade_buffer) <= 3
        # everything flushed losslessly, nothing dropped
        assert p._dropped_trades == 0
        flushed = sum(len(b) for b in p.storage.trades_inserted)
        assert flushed + len(p._trade_buffer) == 20

    async def test_cap_triggers_inline_flush_warning(self, caplog):
        p = _pipeline(batch_size=10_000, max_buffer_size=2)
        with caplog.at_level(logging.WARNING):
            for i in range(2):
                await p._on_message(msg(ts_ms=1712400000000 + i))
        assert "backpressure cap" in caplog.text
        # the inline flush drained the buffer
        assert len(p._trade_buffer) == 0
        assert p._dropped_trades == 0


class TestBackpressureStalledSink:
    async def test_drops_oldest_and_logs_when_sink_unreachable(self, caplog):
        # sink always fails -> failed flush re-adds the batch, so the buffer
        # cannot drain; the cap must drop oldest to stay bounded.
        p = _pipeline(batch_size=10_000, max_buffer_size=3, fail_inserts=True)
        with caplog.at_level(logging.WARNING):
            for i in range(10):
                await p._on_message(msg(ts_ms=1712400000000 + i))
                # hard memory bound is enforced every step
                assert len(p._trade_buffer) <= 3
        assert p._dropped_trades > 0
        assert "dropped" in caplog.text.lower()
        assert "MAX_BUFFER_SIZE" in caplog.text

    async def test_dropped_count_accumulates(self):
        p = _pipeline(batch_size=10_000, max_buffer_size=2, fail_inserts=True)
        for i in range(6):
            await p._on_message(msg(ts_ms=1712400000000 + i))
        # 6 in, buffer bounded at 2 -> 4 dropped overall
        assert len(p._trade_buffer) == 2
        assert p._dropped_trades == 4

    async def test_below_cap_no_warning_no_drop(self, caplog):
        p = _pipeline(batch_size=10_000, max_buffer_size=5)
        with caplog.at_level(logging.WARNING):
            for i in range(4):
                await p._on_message(msg(ts_ms=1712400000000 + i))
        assert p._dropped_trades == 0
        assert "backpressure" not in caplog.text
        assert len(p._trade_buffer) == 4


class _FakeClient:
    def __init__(self):
        self.disconnected = False

    async def disconnect(self):
        self.disconnected = True


class TestFinalBarFlushedOnStop:
    async def test_stop_persists_final_ohlcv_bar(self):
        p = _pipeline(batch_size=10_000, max_buffer_size=10_000)
        p.client = _FakeClient()
        # two trades in the same minute -> a bar is buffered but never rolled
        base = 1712400000000  # 2024-04-06 12:00:00.000
        await p._on_message(msg(price="100", ts_ms=base))
        await p._on_message(msg(price="110", ts_ms=base + 30_000))
        # no rollover trade arrived, so nothing emitted during streaming
        assert p.storage.ohlcv_inserted == []
        await p.stop()
        # stop() flushed the final in-progress bar so it is not dropped
        assert len(p.storage.ohlcv_inserted) == 1
        bar = p.storage.ohlcv_inserted[0]
        assert bar["open"] == 100.0
        assert bar["close"] == 110.0
        assert bar["trade_count"] == 2

    async def test_stop_with_no_buffered_bars_emits_nothing(self):
        p = _pipeline(batch_size=10_000, max_buffer_size=10_000)
        p.client = _FakeClient()
        await p.stop()
        assert p.storage.ohlcv_inserted == []

    async def test_stop_final_bar_persist_failure_is_logged_not_raised(self, caplog):
        p = _pipeline(batch_size=10_000, max_buffer_size=10_000)
        p.client = _FakeClient()
        base = 1712400000000
        await p._on_message(msg(price="100", ts_ms=base))
        await p._on_message(msg(price="110", ts_ms=base + 30_000))

        async def boom(bar):
            raise RuntimeError("sink gone")

        p.storage.insert_ohlcv = boom
        with caplog.at_level(logging.ERROR):
            await p.stop()  # must not raise on a best-effort shutdown flush
        assert "final OHLCV bar" in caplog.text
