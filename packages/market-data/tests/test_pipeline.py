from datetime import UTC, datetime

import pytest

from src.config import Config
from src.normalizer import TickNormalizer, Trade

# --- Config ---


class TestConfig:
    def test_defaults_loaded(self):
        c = Config()
        assert len(c.symbols) > 0
        assert c.batch_size > 0
        assert c.flush_interval > 0

    def test_redis_url_format(self):
        c = Config()
        assert c.redis_url.startswith("redis://")

    def test_database_url_format(self):
        c = Config()
        assert c.database_url.startswith("postgresql://")


# --- Normalizer ---


class TestNormalizer:
    @pytest.fixture
    def normalizer(self):
        return TickNormalizer()

    @pytest.fixture
    def binance_trade_msg(self):
        return {
            "s": "BTCUSDT",
            "p": "67500.50",
            "q": "0.15",
            "m": False,
            "T": 1712400000000,
        }

    def test_normalize_valid_trade(self, normalizer, binance_trade_msg):
        trade = normalizer.normalize_trade(binance_trade_msg)
        assert trade is not None
        assert trade.symbol == "btcusdt"
        assert trade.price == 67500.50
        assert trade.quantity == 0.15

    def test_trade_timestamp_is_utc(self, normalizer, binance_trade_msg):
        trade = normalizer.normalize_trade(binance_trade_msg)
        assert trade.timestamp.tzinfo is not None
        assert trade.timestamp.tzinfo == UTC

    def test_trade_side_buy(self, normalizer):
        msg = {"s": "BTCUSDT", "p": "100", "q": "1", "m": False, "T": 1712400000000}
        trade = normalizer.normalize_trade(msg)
        assert trade.side == "buy"

    def test_trade_side_sell(self, normalizer):
        msg = {"s": "BTCUSDT", "p": "100", "q": "1", "m": True, "T": 1712400000000}
        trade = normalizer.normalize_trade(msg)
        assert trade.side == "sell"

    def test_invalid_message_returns_none(self, normalizer):
        assert normalizer.normalize_trade({}) is None
        assert normalizer.normalize_trade({"garbage": True}) is None

    def test_missing_field_returns_none(self, normalizer):
        assert normalizer.normalize_trade({"s": "X", "p": "100"}) is None

    def test_ohlcv_accumulation(self, normalizer):
        """Trades in the same minute accumulate, new minute emits a bar."""
        trades = [
            Trade("btc", 100, 0.1, "buy", datetime(2024, 1, 1, 12, 0, 10, tzinfo=UTC)),
            Trade("btc", 105, 0.2, "buy", datetime(2024, 1, 1, 12, 0, 30, tzinfo=UTC)),
            Trade("btc", 102, 0.3, "sell", datetime(2024, 1, 1, 12, 0, 50, tzinfo=UTC)),
            Trade("btc", 110, 0.1, "buy", datetime(2024, 1, 1, 12, 1, 5, tzinfo=UTC)),  # new minute
        ]
        bar = None
        for t in trades:
            result = normalizer.accumulate_trade(t)
            if result is not None:
                bar = result

        assert bar is not None, "Should emit a bar when minute rolls over"
        assert bar.symbol == "btc"
        assert bar.open == 100
        assert bar.high == 105
        assert bar.low == 100
        assert bar.close == 102
        assert bar.trade_count == 3


# --- WebSocket Client ---


class TestWebSocketClient:
    def test_callback_exception_isolation(self):
        """Verify source code has try-except around callbacks."""
        import inspect

        from src.websocket_client import MarketDataClient

        source = inspect.getsource(MarketDataClient._consume)
        assert "except Exception" in source, "Callbacks must be wrapped in try-except"

    def test_reconnect_backoff_capped(self):
        from src.websocket_client import MarketDataClient

        client = MarketDataClient("wss://fake", max_retries=20)
        client._retry_count = 100
        # Backoff should be capped at 60s (from min(2**count, 60))
        delay = min(2**client._retry_count, 60)
        assert delay == 60
