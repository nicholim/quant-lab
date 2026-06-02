"""Regression tests for the OHLCV roll-up final-bar / single-trade-bar bug.

Backlog item: the minute roll-up "drops the final bar (needs >=2 trades)".

Two facets:
1. The final in-progress minute is never closed by a later trade, so without an
   explicit flush() its bar is silently dropped (the main bug).
2. A minute that saw only a SINGLE trade must still emit a valid bar (both on
   rollover and on flush), not be skipped because the accumulator held < 2.
"""

from datetime import UTC, datetime

import pytest

from src.normalizer import OHLCVBar, TickNormalizer, Trade


@pytest.fixture
def normalizer():
    return TickNormalizer()


def trade(symbol, price, qty, second, side="buy", minute=0):
    return Trade(
        symbol=symbol,
        price=price,
        quantity=qty,
        side=side,
        timestamp=datetime(2024, 1, 1, 12, minute, second, tzinfo=UTC),
    )


class TestFinalBarFlush:
    def test_final_bar_dropped_without_flush(self, normalizer):
        # Two trades in minute 0, no later-minute trade ever arrives.
        assert normalizer.accumulate_trade(trade("btc", 100, 1, 10)) is None
        assert normalizer.accumulate_trade(trade("btc", 110, 1, 40)) is None
        # The bar for minute 0 has NOT been emitted yet (proves the old
        # behavior dropped it at end-of-stream with no flush path).
        assert normalizer._bar_accumulators["btc"]  # still buffered

    def test_flush_emits_final_bar(self, normalizer):
        normalizer.accumulate_trade(trade("btc", 100, 0.5, 10))
        normalizer.accumulate_trade(trade("btc", 120, 0.5, 40))
        bar = normalizer.flush("btc")
        assert isinstance(bar, OHLCVBar)
        assert bar.open == 100
        assert bar.high == 120
        assert bar.low == 100
        assert bar.close == 120
        assert bar.volume == pytest.approx(1.0)
        assert bar.trade_count == 2
        assert bar.timestamp == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        # accumulator cleared after flush
        assert normalizer._bar_accumulators["btc"] == []

    def test_flush_empty_symbol_returns_none(self, normalizer):
        assert normalizer.flush("btc") is None

    def test_flush_after_rollover_emits_the_carried_minute(self, normalizer):
        # minute 0 (2 trades) rolls over -> bar1; minute 1 trade carried;
        # flush must emit that carried minute-1 bar (otherwise dropped).
        normalizer.accumulate_trade(trade("btc", 100, 1, 10))
        normalizer.accumulate_trade(trade("btc", 105, 1, 40))
        bar1 = normalizer.accumulate_trade(trade("btc", 200, 1, 5, minute=1))
        assert bar1.trade_count == 2
        bar2 = normalizer.flush("btc")
        assert bar2 is not None
        assert bar2.open == 200
        assert bar2.close == 200
        assert bar2.trade_count == 1
        assert bar2.timestamp == datetime(2024, 1, 1, 12, 1, 0, tzinfo=UTC)


class TestSingleTradeBar:
    def test_single_trade_minute_emits_on_rollover(self, normalizer):
        # minute 0 sees exactly ONE trade, then a minute-1 trade arrives.
        # The old `len(bucket) < 2` reasoning must not cause the single-trade
        # minute-0 bar to be skipped.
        assert normalizer.accumulate_trade(trade("btc", 100, 2, 10)) is None
        bar = normalizer.accumulate_trade(trade("btc", 110, 1, 5, minute=1))
        assert isinstance(bar, OHLCVBar)
        assert bar.open == bar.high == bar.low == bar.close == 100
        assert bar.volume == pytest.approx(2.0)
        assert bar.trade_count == 1
        assert bar.timestamp == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)

    def test_single_trade_minute_emits_on_flush(self, normalizer):
        normalizer.accumulate_trade(trade("btc", 100, 3, 10))
        bar = normalizer.flush("btc")
        assert bar is not None
        assert bar.open == bar.high == bar.low == bar.close == 100
        assert bar.trade_count == 1
        assert bar.volume == pytest.approx(3.0)


class TestFlushAll:
    def test_flush_all_emits_every_buffered_symbol(self, normalizer):
        normalizer.accumulate_trade(trade("btc", 100, 1, 10))
        normalizer.accumulate_trade(trade("btc", 110, 1, 40))
        normalizer.accumulate_trade(trade("eth", 50, 1, 10))
        bars = normalizer.flush_all()
        symbols = {b.symbol for b in bars}
        assert symbols == {"btc", "eth"}
        # all accumulators drained
        assert all(not v for v in normalizer._bar_accumulators.values())

    def test_flush_all_empty_returns_empty_list(self, normalizer):
        assert normalizer.flush_all() == []
