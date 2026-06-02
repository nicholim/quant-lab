"""Edge-case tests for TickNormalizer: parsing, timestamps, and OHLCV boundaries."""

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


class TestNormalizeParsing:
    def test_non_numeric_price_returns_none(self, normalizer):
        msg = {"s": "BTCUSDT", "p": "abc", "q": "1", "m": False, "T": 1712400000000}
        assert normalizer.normalize_trade(msg) is None

    def test_non_numeric_quantity_returns_none(self, normalizer):
        msg = {"s": "BTCUSDT", "p": "100", "q": "xx", "m": False, "T": 1712400000000}
        assert normalizer.normalize_trade(msg) is None

    def test_missing_timestamp_returns_none(self, normalizer):
        msg = {"s": "BTCUSDT", "p": "100", "q": "1", "m": False}
        assert normalizer.normalize_trade(msg) is None

    def test_symbol_is_lowercased(self, normalizer):
        msg = {"s": "ETHUSDT", "p": "1", "q": "1", "m": False, "T": 1712400000000}
        assert normalizer.normalize_trade(msg).symbol == "ethusdt"

    def test_missing_m_defaults_to_buy(self, normalizer):
        msg = {"s": "BTCUSDT", "p": "1", "q": "1", "T": 1712400000000}
        assert normalizer.normalize_trade(msg).side == "buy"

    def test_integer_price_field_accepted(self, normalizer):
        # Binance sends strings, but float() also accepts numerics
        msg = {"s": "BTCUSDT", "p": 100, "q": 1, "m": False, "T": 1712400000000}
        t = normalizer.normalize_trade(msg)
        assert t.price == 100.0
        assert t.quantity == 1.0


class TestTimestamp:
    def test_ms_epoch_converted_to_utc(self, normalizer):
        # 1712400000000 ms = 2024-04-06 10:40:00 UTC
        msg = {"s": "X", "p": "1", "q": "1", "m": False, "T": 1712400000000}
        t = normalizer.normalize_trade(msg)
        assert t.timestamp == datetime(2024, 4, 6, 10, 40, 0, tzinfo=UTC)
        assert t.timestamp.tzinfo == UTC

    def test_sub_second_precision_preserved(self, normalizer):
        msg = {"s": "X", "p": "1", "q": "1", "m": False, "T": 1712400000123}
        t = normalizer.normalize_trade(msg)
        assert t.timestamp.microsecond == 123000


class TestOHLCVBoundaries:
    def test_single_trade_no_bar(self, normalizer):
        assert normalizer.accumulate_trade(trade("btc", 100, 1, 10)) is None

    def test_two_trades_same_minute_no_bar(self, normalizer):
        assert normalizer.accumulate_trade(trade("btc", 100, 1, 10)) is None
        assert normalizer.accumulate_trade(trade("btc", 101, 1, 20)) is None

    def test_minute_rollover_emits_bar(self, normalizer):
        normalizer.accumulate_trade(trade("btc", 100, 0.1, 10))
        normalizer.accumulate_trade(trade("btc", 120, 0.2, 30))
        normalizer.accumulate_trade(trade("btc", 90, 0.3, 50))
        bar = normalizer.accumulate_trade(trade("btc", 110, 0.1, 5, minute=1))
        assert isinstance(bar, OHLCVBar)
        assert bar.open == 100
        assert bar.high == 120
        assert bar.low == 90
        assert bar.close == 90  # last trade of minute 0
        assert bar.volume == pytest.approx(0.6)
        assert bar.trade_count == 3
        assert bar.interval == "1m"

    def test_bar_timestamp_truncated_to_minute(self, normalizer):
        normalizer.accumulate_trade(trade("btc", 100, 1, 10))
        normalizer.accumulate_trade(trade("btc", 101, 1, 40))
        bar = normalizer.accumulate_trade(trade("btc", 102, 1, 5, minute=1))
        assert bar.timestamp == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        assert bar.timestamp.second == 0
        assert bar.timestamp.microsecond == 0

    def test_remaining_trade_carries_into_next_bucket(self, normalizer):
        # minute 0 has 2 trades, minute 1 trade triggers emit; that minute-1
        # trade must remain in the accumulator for the next bar.
        normalizer.accumulate_trade(trade("btc", 100, 1, 10))
        normalizer.accumulate_trade(trade("btc", 105, 1, 40))
        bar1 = normalizer.accumulate_trade(trade("btc", 200, 1, 5, minute=1))
        assert bar1.trade_count == 2
        # add another minute-1 trade, then roll to minute 2 -> bar of minute 1
        normalizer.accumulate_trade(trade("btc", 210, 1, 30, minute=1))
        bar2 = normalizer.accumulate_trade(trade("btc", 300, 1, 5, minute=2))
        assert bar2.open == 200  # first minute-1 trade carried over
        assert bar2.close == 210
        assert bar2.trade_count == 2

    def test_multi_symbol_isolation(self, normalizer):
        normalizer.accumulate_trade(trade("btc", 100, 1, 10))
        normalizer.accumulate_trade(trade("eth", 50, 1, 10))
        normalizer.accumulate_trade(trade("btc", 110, 1, 40))
        # rolling btc to next minute must only emit a btc bar
        bar = normalizer.accumulate_trade(trade("btc", 120, 1, 5, minute=1))
        assert bar.symbol == "btc"
        assert bar.open == 100
        # eth accumulator untouched (still 1 trade)
        assert len(normalizer._bar_accumulators["eth"]) == 1

    def test_out_of_order_trade_does_not_emit(self, normalizer):
        # a trade with an earlier minute than the bucket head should not
        # trigger an emit (current_minute not > first_minute).
        normalizer.accumulate_trade(trade("btc", 100, 1, 10, minute=5))
        result = normalizer.accumulate_trade(trade("btc", 99, 1, 10, minute=3))
        assert result is None
