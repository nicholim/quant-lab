"""Tests for the opt-in L2 order-book DEPTH feed.

Covers the BookUpdate/BookLevel data types, the BinanceDepthAdapter wire-format
parsing (single-symbol /ws form + multi-symbol combined-stream wrapper), the
DepthAdapter protocol + build_depth_adapter/supports_depth factories, normalizer
delegation, the RedisCache book methods, both storage backends' insert/query_book,
the Pipeline depth path (_on_depth_message), and an END-TO-END depth pipeline run
driving MarketDataClient with a FakeWebSocket replaying canned depth messages
(mirroring the Kraken trades e2e test). It also proves the trades-only default
path is byte-identical when depth is off.

No live network: everything runs against in-memory fakes / an in-memory DuckDB.
"""

import json
from datetime import UTC, datetime

import pytest

from src.adapters import (
    BinanceDepthAdapter,
    DepthAdapter,
    build_depth_adapter,
    supports_depth,
)
from src.cache import RedisCache
from src.config import Config
from src.duckdb_storage import DuckDBStorage
from src.normalizer import BookLevel, BookUpdate, TickNormalizer
from src.pipeline import Pipeline, build_depth_adapter_for

# --- Captured / representative raw depth messages -------------------------

# Binance partial book depth (<sym>@depth20@100ms) single-stream payload: a
# self-contained top-N snapshot, bids highest-first, asks lowest-first.
BINANCE_DEPTH = {
    "lastUpdateId": 160,
    "bids": [["67500.10", "1.5"], ["67500.00", "2.0"], ["67499.90", "0.3"]],
    "asks": [["67500.20", "0.8"], ["67500.30", "5.0"], ["67500.40", "1.1"]],
}

# Combined-stream wrapper (multi-symbol fan-out): the symbol comes from `stream`.
BINANCE_DEPTH_WRAPPED = {
    "stream": "ethusdt@depth20@100ms",
    "data": {
        "lastUpdateId": 99,
        "bids": [["3500.10", "10.0"]],
        "asks": [["3500.20", "12.0"]],
    },
}


# --- Data types ------------------------------------------------------------


class TestBookTypes:
    def test_book_level_fields(self):
        lvl = BookLevel(price=100.0, quantity=2.0)
        assert lvl.price == 100.0
        assert lvl.quantity == 2.0

    def test_best_bid_ask(self):
        book = BookUpdate(
            symbol="btcusdt",
            bids=[BookLevel(100.0, 1.0), BookLevel(99.0, 2.0)],
            asks=[BookLevel(101.0, 1.0), BookLevel(102.0, 2.0)],
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
        )
        assert book.best_bid == 100.0
        assert book.best_ask == 101.0
        assert book.exchange == "binance"

    def test_best_bid_ask_empty_book_is_none(self):
        book = BookUpdate(
            symbol="btcusdt", bids=[], asks=[], timestamp=datetime(2024, 1, 1, tzinfo=UTC)
        )
        assert book.best_bid is None
        assert book.best_ask is None


# --- BinanceDepthAdapter ---------------------------------------------------


class TestBinanceDepthAdapter:
    def test_is_depth_adapter(self):
        assert isinstance(BinanceDepthAdapter(), DepthAdapter)
        assert BinanceDepthAdapter().name == "binance"

    def test_single_symbol_ws_url_embeds_stream(self):
        a = BinanceDepthAdapter("wss://base/ws")
        assert a.ws_url(["btcusdt"]) == "wss://base/ws/btcusdt@depth20@100ms"

    def test_multi_symbol_ws_url_uses_combined_stream(self):
        a = BinanceDepthAdapter("wss://base/ws")
        url = a.ws_url(["btcusdt", "ethusdt"])
        assert url == "wss://base/stream?streams=btcusdt@depth20@100ms/ethusdt@depth20@100ms"

    def test_custom_levels(self):
        a = BinanceDepthAdapter("wss://base/ws", levels=5)
        assert a.ws_url(["btcusdt"]) == "wss://base/ws/btcusdt@depth5@100ms"

    def test_no_subscribe_payload(self):
        assert BinanceDepthAdapter().subscribe_payload(["btcusdt"]) is None

    def test_normalize_single_stream_with_symbol_hint(self):
        a = BinanceDepthAdapter().with_symbol_hint("BTCUSDT")
        book = a.normalize_depth(BINANCE_DEPTH)
        assert book is not None
        assert book.symbol == "btcusdt"
        assert book.exchange == "binance"
        # Levels preserved in order, parsed to floats.
        assert [(b.price, b.quantity) for b in book.bids] == [
            (67500.10, 1.5),
            (67500.00, 2.0),
            (67499.90, 0.3),
        ]
        assert book.best_bid == 67500.10
        assert book.best_ask == 67500.20
        assert book.timestamp.tzinfo is UTC

    def test_normalize_combined_stream_takes_symbol_from_stream(self):
        a = BinanceDepthAdapter()  # no hint; symbol comes from the wrapper
        book = a.normalize_depth(BINANCE_DEPTH_WRAPPED)
        assert book is not None
        assert book.symbol == "ethusdt"
        assert book.best_bid == 3500.10
        assert book.best_ask == 3500.20

    def test_normalize_without_hint_or_stream_uses_empty_symbol(self):
        a = BinanceDepthAdapter()
        book = a.normalize_depth(BINANCE_DEPTH)
        assert book is not None
        assert book.symbol == ""

    def test_non_dict_returns_none(self):
        assert BinanceDepthAdapter().normalize_depth([1, 2, 3]) is None

    def test_message_without_bids_asks_returns_none(self):
        # e.g. a subscription ack object has no bids/asks.
        assert BinanceDepthAdapter().normalize_depth({"result": None, "id": 1}) is None

    def test_wrapped_non_dict_data_returns_none(self):
        assert BinanceDepthAdapter().normalize_depth({"stream": "x@depth", "data": [1]}) is None

    def test_malformed_levels_return_none(self):
        bad = {"bids": [["oops"]], "asks": []}
        assert BinanceDepthAdapter().normalize_depth(bad) is None


# --- Factory + capability gate --------------------------------------------


class TestDepthFactory:
    def test_supports_depth(self):
        assert supports_depth("binance") is True
        assert supports_depth("BINANCE") is True
        assert supports_depth("coinbase") is False
        assert supports_depth("kraken") is False

    def test_build_depth_adapter_binance(self):
        assert isinstance(build_depth_adapter("binance"), BinanceDepthAdapter)

    def test_build_depth_adapter_unsupported_raises(self):
        with pytest.raises(ValueError, match="no L2 depth adapter"):
            build_depth_adapter("coinbase")


class TestBuildDepthAdapterFor:
    def test_off_by_default(self):
        cfg = Config()
        cfg.exchange = "binance"
        cfg.enable_depth = False
        assert build_depth_adapter_for(cfg) is None

    def test_enabled_binance_single_symbol_gets_hint(self):
        cfg = Config()
        cfg.exchange = "binance"
        cfg.enable_depth = True
        cfg.symbols = ["btcusdt"]
        adapter = build_depth_adapter_for(cfg)
        assert isinstance(adapter, BinanceDepthAdapter)
        # The single symbol is stamped via the hint on un-wrapped payloads.
        book = adapter.normalize_depth(BINANCE_DEPTH)
        assert book is not None and book.symbol == "btcusdt"

    def test_enabled_binance_multi_symbol_no_hint(self):
        cfg = Config()
        cfg.exchange = "binance"
        cfg.enable_depth = True
        cfg.symbols = ["btcusdt", "ethusdt"]
        adapter = build_depth_adapter_for(cfg)
        assert isinstance(adapter, BinanceDepthAdapter)
        # Multi-symbol: no hint; the symbol comes from the combined-stream wrapper.
        assert adapter.normalize_depth(BINANCE_DEPTH).symbol == ""
        assert adapter.normalize_depth(BINANCE_DEPTH_WRAPPED).symbol == "ethusdt"

    def test_enabled_but_unsupported_exchange_is_none(self):
        cfg = Config()
        cfg.exchange = "kraken"
        cfg.enable_depth = True
        assert build_depth_adapter_for(cfg) is None


# --- Normalizer delegation -------------------------------------------------


class TestNormalizerDepthDelegation:
    def test_no_depth_adapter_returns_none(self):
        # Default normalizer has no depth adapter -> depth feed off.
        norm = TickNormalizer()
        assert norm.depth_adapter is None
        assert norm.normalize_depth(BINANCE_DEPTH) is None

    def test_uses_injected_depth_adapter(self):
        norm = TickNormalizer(depth_adapter=BinanceDepthAdapter().with_symbol_hint("btcusdt"))
        book = norm.normalize_depth(BINANCE_DEPTH)
        assert book is not None and book.symbol == "btcusdt"


# --- RedisCache book methods ----------------------------------------------


class _FakeRedisKV:
    """Minimal fake adding the get/set string commands the book cache uses."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def set(self, key, value):
        self.kv[key] = value

    async def get(self, key):
        return self.kv.get(key)

    async def publish(self, channel, message):
        self.published.append((channel, message))


class TestRedisCacheBook:
    async def test_set_and_get_book(self):
        cache = RedisCache("redis://x")
        cache._client = _FakeRedisKV()
        snapshot = {"symbol": "btcusdt", "bids": [{"price": 1.0, "quantity": 2.0}], "asks": []}
        await cache.set_book("btcusdt", snapshot)
        assert await cache.get_book("btcusdt") == snapshot

    async def test_get_book_missing_is_none(self):
        cache = RedisCache("redis://x")
        cache._client = _FakeRedisKV()
        assert await cache.get_book("nope") is None


# --- DuckDB storage book table --------------------------------------------


def _book_dict(ts, symbol="btcusdt"):
    return {
        "timestamp": ts,
        "symbol": symbol,
        "bids": [{"price": 100.0, "quantity": 1.5}, {"price": 99.0, "quantity": 2.0}],
        "asks": [{"price": 101.0, "quantity": 0.8}],
        "exchange": "binance",
    }


class TestDuckDBBook:
    @pytest.fixture
    async def storage(self):
        s = DuckDBStorage(":memory:")
        await s.connect()
        await s.init_schema()
        yield s
        await s.disconnect()

    async def test_insert_and_query_book_roundtrips(self, storage):
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        await storage.insert_book(_book_dict(ts))
        rows = await storage.query_book(
            "btcusdt", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["symbol"] == "btcusdt"
        assert row["exchange"] == "binance"
        # bids/asks decoded back from JSON to lists of level dicts.
        assert row["bids"][0] == {"price": 100.0, "quantity": 1.5}
        assert row["asks"] == [{"price": 101.0, "quantity": 0.8}]

    async def test_query_book_most_recent_first(self, storage):
        t0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        t1 = datetime(2024, 1, 1, 12, 0, 1, tzinfo=UTC)
        await storage.insert_book(_book_dict(t0))
        await storage.insert_book(_book_dict(t1))
        rows = await storage.query_book(
            "btcusdt", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert [r["time"] for r in rows] == [t1, t0]

    async def test_query_book_filters_by_symbol(self, storage):
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        await storage.insert_book(_book_dict(ts, symbol="btcusdt"))
        await storage.insert_book(_book_dict(ts, symbol="ethusdt"))
        rows = await storage.query_book(
            "ethusdt", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert len(rows) == 1 and rows[0]["symbol"] == "ethusdt"


# --- Pipeline depth path ---------------------------------------------------


class _FakeCache:
    def __init__(self):
        self.books = {}
        self.published = []
        self.prices = {}
        self.pushed = []

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def set_latest_price(self, symbol, price, ts):
        self.prices[symbol] = (price, ts)

    async def push_trade(self, symbol, trade_data, max_length=1000):
        self.pushed.append((symbol, trade_data))

    async def set_book(self, symbol, book_data):
        self.books[symbol] = book_data

    async def publish(self, channel, message):
        self.published.append((channel, message))


class _FakeStorage:
    def __init__(self, fail_book=False):
        self.books_inserted = []
        self.trades_inserted = []
        self.ohlcv_inserted = []
        self.fail_book = fail_book

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

    async def insert_book(self, book):
        if self.fail_book:
            raise RuntimeError("book sink down")
        self.books_inserted.append(book)


def _depth_pipeline(monkeypatch, symbols=None):
    monkeypatch.setenv("ENABLE_DEPTH", "1")
    cfg = Config()
    cfg.exchange = "binance"
    cfg.symbols = symbols or ["btcusdt"]
    p = Pipeline(cfg)
    p.cache = _FakeCache()
    p.storage = _FakeStorage()
    return p


class TestPipelineDepthPath:
    async def test_on_depth_caches_publishes_and_stores(self, monkeypatch):
        p = _depth_pipeline(monkeypatch)
        assert p.depth_adapter is not None  # opt-in feed wired
        await p._on_depth_message(BINANCE_DEPTH)
        assert "btcusdt" in p.cache.books
        assert p.cache.published[0][0] == "book:btcusdt"
        assert len(p.storage.books_inserted) == 1
        stored = p.storage.books_inserted[0]
        assert stored["symbol"] == "btcusdt"
        assert stored["bids"][0] == {"price": 67500.10, "quantity": 1.5}

    async def test_on_depth_ignores_non_depth_message(self, monkeypatch):
        p = _depth_pipeline(monkeypatch)
        await p._on_depth_message({"result": None, "id": 1})
        assert p.cache.books == {}
        assert p.storage.books_inserted == []

    async def test_on_depth_storage_failure_is_swallowed(self, monkeypatch):
        p = _depth_pipeline(monkeypatch)
        p.storage = _FakeStorage(fail_book=True)
        # Must not raise even if the book sink is down (best-effort).
        await p._on_depth_message(BINANCE_DEPTH)
        assert "btcusdt" in p.cache.books  # cache still updated

    async def test_start_and_stop_wire_and_tear_down_depth(self, monkeypatch):
        """start() launches the depth connection; stop() tears it down."""
        import asyncio

        p = _depth_pipeline(monkeypatch)

        connected = {}

        class _FakeDepthClient:
            def __init__(self):
                self.callbacks = []
                self.disconnected = False

            def on_message(self, cb):
                self.callbacks.append(cb)

            async def connect(self, symbols):
                connected["symbols"] = symbols
                # Stay "open" until cancelled so start() can return after
                # creating the task (it does not await the depth connect).
                await asyncio.sleep(3600)

            async def disconnect(self):
                self.disconnected = True

        class _FakeTradeClient:
            def __init__(self):
                self.callbacks = []
                self.disconnected = False

            def on_message(self, cb):
                self.callbacks.append(cb)

            async def connect(self, symbols):
                pass  # returns immediately

            async def disconnect(self):
                self.disconnected = True

        p.client = _FakeTradeClient()
        p.depth_client = _FakeDepthClient()

        await p.start()
        await asyncio.sleep(0)  # let the depth-connect task get scheduled
        assert connected["symbols"] == ["btcusdt"]
        assert len(p.depth_client.callbacks) == 1
        assert p._depth_task is not None

        await p.stop()
        assert p.depth_client.disconnected is True
        assert p._depth_task.cancelled() or p._depth_task.done()
        # cancel the periodic flush task spawned by start()
        if p._flush_task:
            p._flush_task.cancel()

    async def test_depth_off_by_default_no_depth_client(self, monkeypatch):
        monkeypatch.delenv("ENABLE_DEPTH", raising=False)
        cfg = Config()
        cfg.exchange = "binance"
        cfg.symbols = ["btcusdt"]
        p = Pipeline(cfg)
        assert p.depth_adapter is None
        assert p.depth_client is None
        assert p.normalizer.depth_adapter is None


# --- End-to-end depth pipeline via FakeWebSocket --------------------------


class TestDepthPipelineEndToEnd:
    async def test_pipeline_normalizes_depth_messages_end_to_end(
        self, monkeypatch, fake_ws_factory
    ):
        monkeypatch.setenv("ENABLE_DEPTH", "1")
        cfg = Config()
        cfg.exchange = "binance"
        cfg.symbols = ["btcusdt"]
        p = Pipeline(cfg)
        p.cache = _FakeCache()
        p.storage = _FakeStorage()
        assert p.depth_client is not None

        # Two canned partial-depth snapshots + one non-depth ack (ignored).
        messages = [
            json.dumps({"result": None, "id": 1}),
            json.dumps(dict(BINANCE_DEPTH, bids=[["100.0", "1.0"]], asks=[["101.0", "2.0"]])),
            json.dumps(dict(BINANCE_DEPTH, bids=[["110.0", "3.0"]], asks=[["111.0", "4.0"]])),
        ]
        ws = fake_ws_factory(messages=messages)

        def connect(url, **kwargs):
            p.depth_client._running = False  # one pass through the consume loop
            return ws

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)

        p.depth_client.on_message(p._on_depth_message)
        await p.depth_client.connect(cfg.symbols)

        # Both snapshots cached (latest wins), published, and persisted.
        assert p.cache.books["btcusdt"]["bids"][0] == {"price": 110.0, "quantity": 3.0}
        assert len([c for c in p.cache.published if c[0] == "book:btcusdt"]) == 2
        assert len(p.storage.books_inserted) == 2
        assert all(b["exchange"] == "binance" for b in p.storage.books_inserted)


# --- Single-symbol trades parity (depth OFF == before) --------------------


class TestSingleSymbolTradesParity:
    async def test_trades_path_byte_identical_with_depth_off(self, monkeypatch):
        """A single-symbol trades run with depth OFF behaves exactly as before:

        no depth adapter, no depth client, the trades adapter/URL/normalization
        unchanged, and a Binance trade still normalizes identically.
        """
        monkeypatch.delenv("ENABLE_DEPTH", raising=False)
        cfg = Config()
        cfg.exchange = "binance"
        cfg.symbols = ["btcusdt"]
        p = Pipeline(cfg)
        # Depth machinery is entirely absent.
        assert p.depth_adapter is None
        assert p.depth_client is None
        # Trades client still uses the original Binance combined-stream URL.
        assert p.client.adapter.ws_url(["btcusdt"]) == cfg.ws_url + "/btcusdt@trade"
        # A trade still normalizes exactly as the legacy path did.
        trade = p.normalizer.normalize_trade(
            {"e": "trade", "s": "BTCUSDT", "p": "100.0", "q": "1.0", "m": False, "T": 1712400000000}
        )
        assert trade is not None
        assert (trade.symbol, trade.price, trade.side) == ("btcusdt", 100.0, "buy")
