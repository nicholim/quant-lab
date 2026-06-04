"""Tests for multi-symbol fan-out over ONE websocket connection.

The trades adapters already build multi-symbol URLs / subscribe payloads; these
tests prove that (a) a single configured symbol is byte-identical to the legacy
behavior, and (b) multiple symbols fan out over one connection and route to the
correct per-symbol cache keys / OHLCV accumulators. No live network: messages
are driven through the pipeline / a FakeWebSocket.
"""

import json

from src.adapters import BinanceAdapter, BinanceDepthAdapter, CoinbaseAdapter, KrakenAdapter
from src.config import Config
from src.pipeline import Pipeline

# --- Single-symbol parity (byte-identical to before) ----------------------


class TestSingleSymbolParity:
    def test_binance_trades_url_single_symbol_unchanged(self):
        a = BinanceAdapter("wss://stream.binance.com:9443/ws")
        assert a.ws_url(["btcusdt"]) == "wss://stream.binance.com:9443/ws/btcusdt@trade"

    def test_binance_depth_url_single_symbol_uses_plain_ws(self):
        # A single symbol stays on the lean /ws/<stream> form (no combined wrapper).
        a = BinanceDepthAdapter("wss://stream.binance.com:9443/ws")
        assert a.ws_url(["btcusdt"]) == "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"


# --- Multi-symbol fan-out URL / payload construction ----------------------


class TestFanOutWiring:
    def test_binance_trades_multi_symbol_url(self):
        a = BinanceAdapter("wss://base/ws")
        assert a.ws_url(["btcusdt", "ethusdt"]) == "wss://base/ws/btcusdt@trade/ethusdt@trade"

    def test_binance_depth_multi_symbol_combined_stream(self):
        a = BinanceDepthAdapter("wss://base/ws")
        url = a.ws_url(["btcusdt", "ethusdt", "solusdt"])
        assert url == (
            "wss://base/stream?streams="
            "btcusdt@depth20@100ms/ethusdt@depth20@100ms/solusdt@depth20@100ms"
        )

    def test_coinbase_multi_symbol_subscribe(self):
        payload = CoinbaseAdapter().subscribe_payload(["btcusd", "ethusd"])
        assert payload["channels"][0]["product_ids"] == ["BTC-USD", "ETH-USD"]

    def test_kraken_multi_symbol_subscribe(self):
        payload = KrakenAdapter().subscribe_payload(["btcusd", "ethusd"])
        assert payload["pair"] == ["XBT/USD", "ETH/USD"]


# --- Multi-symbol routing through the pipeline ----------------------------


class _FakeCache:
    def __init__(self):
        self.prices = {}
        self.pushed = []
        self.published = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def set_latest_price(self, symbol, price, ts):
        self.prices[symbol] = (price, ts)

    async def push_trade(self, symbol, trade_data, max_length=1000):
        self.pushed.append((symbol, trade_data))

    async def publish(self, channel, message):
        self.published.append((channel, message))


class _FakeStorage:
    def __init__(self):
        self.trades_inserted = []
        self.ohlcv_inserted = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def init_schema(self):
        pass

    async def insert_trades(self, batch):
        self.trades_inserted.append(list(batch))

    async def insert_ohlcv(self, bar):
        self.ohlcv_inserted.append(bar)


def _binance_trade(symbol, price, ts_ms):
    return {"e": "trade", "s": symbol, "p": price, "q": "1.0", "m": False, "T": ts_ms}


class TestMultiSymbolRouting:
    async def test_distinct_symbols_route_to_distinct_cache_keys(self, monkeypatch):
        cfg = Config()
        cfg.exchange = "binance"
        cfg.symbols = ["btcusdt", "ethusdt"]
        cfg.batch_size = 100  # don't flush mid-test
        p = Pipeline(cfg)
        p.cache = _FakeCache()
        p.storage = _FakeStorage()

        # Two symbols' trades arrive interleaved on ONE feed.
        await p._on_message(_binance_trade("BTCUSDT", "67000", 1712400000000))
        await p._on_message(_binance_trade("ETHUSDT", "3500", 1712400000000))

        assert p.cache.prices["btcusdt"][0] == 67000.0
        assert p.cache.prices["ethusdt"][0] == 3500.0
        # Each was published on its own per-symbol channel.
        channels = {c for c, _ in p.cache.published}
        assert channels == {"trades:btcusdt", "trades:ethusdt"}

    async def test_multi_symbol_ohlcv_accumulators_independent(self, monkeypatch):
        cfg = Config()
        cfg.exchange = "binance"
        cfg.symbols = ["btcusdt", "ethusdt"]
        cfg.batch_size = 100
        p = Pipeline(cfg)
        p.cache = _FakeCache()
        p.storage = _FakeStorage()

        base = 1712400000000
        # btc minute 0, eth minute 0, then btc minute 1 -> only btc's bar closes.
        await p._on_message(_binance_trade("BTCUSDT", "100", base))
        await p._on_message(_binance_trade("ETHUSDT", "200", base))
        await p._on_message(_binance_trade("BTCUSDT", "110", base + 65_000))

        assert len(p.storage.ohlcv_inserted) == 1
        assert p.storage.ohlcv_inserted[0]["symbol"] == "btcusdt"
        # eth's in-progress minute only closes on flush.
        bars = p.normalizer.flush_all()
        assert {b.symbol for b in bars} == {"ethusdt", "btcusdt"}

    async def test_multi_symbol_fanout_over_one_connection_end_to_end(
        self, monkeypatch, fake_ws_factory
    ):
        cfg = Config()
        cfg.exchange = "binance"
        cfg.symbols = ["btcusdt", "ethusdt"]
        cfg.batch_size = 100
        p = Pipeline(cfg)
        p.cache = _FakeCache()
        p.storage = _FakeStorage()

        messages = [
            json.dumps(_binance_trade("BTCUSDT", "67000", 1712400000000)),
            json.dumps(_binance_trade("ETHUSDT", "3500", 1712400000000)),
        ]
        ws = fake_ws_factory(messages=messages)
        captured = {}

        def connect(url, **kwargs):
            captured["url"] = url
            p.client._running = False
            return ws

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)
        p.client.on_message(p._on_message)
        await p.client.connect(cfg.symbols)

        # ONE connection, both symbols' streams embedded in the single URL.
        assert captured["url"].endswith("/btcusdt@trade/ethusdt@trade")
        assert p.cache.prices["btcusdt"][0] == 67000.0
        assert p.cache.prices["ethusdt"][0] == 3500.0
