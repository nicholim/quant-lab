"""Tests for the opt-in analytics features.

Feature 1 — trade-flow / VWAP bar enrichment (``ENABLE_BAR_FEATURES``):
exact buy/sell volume, flow imbalance, and VWAP accumulation in the
normalizer; the pipeline publish/persist path; both storage backends'
``bar_features`` table; the CLI flag; and byte-parity of the default path.

Feature 2 — snapshot-level L2 book features (rides the existing
``ENABLE_DEPTH`` gate): exact-value math for midprice/microprice/spread/
depth-imbalance on hand-built BookUpdates including empty and one-sided
books; the cache methods; and the pipeline depth path.

No live network: everything runs against in-memory fakes / an in-memory
DuckDB, mirroring the existing depth tests.
"""

import json
import math
from dataclasses import asdict
from datetime import UTC, datetime

import pytest

import main as cli_main
from src.cache import RedisCache
from src.config import Config
from src.duckdb_storage import DuckDBStorage
from src.features import (
    BookFeatures,
    compute_book_features,
    cumulative_depth,
    depth_imbalance,
    microprice,
    midprice,
    quoted_spread,
    quoted_spread_bps,
)
from src.normalizer import BarFeatures, BookLevel, BookUpdate, TickNormalizer, Trade
from src.pipeline import Pipeline
from src.storage import TimeSeriesStorage

TS = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def _book(bids, asks):
    return BookUpdate(
        symbol="btcusdt",
        bids=[BookLevel(p, q) for p, q in bids],
        asks=[BookLevel(p, q) for p, q in asks],
        timestamp=TS,
    )


def _trade(price, qty, side, ts=None, symbol="btcusdt"):
    return Trade(symbol=symbol, price=price, quantity=qty, side=side, timestamp=ts or TS)


# --- Feature 2: book-feature math -------------------------------------------


class TestBookFeatureMath:
    def test_midprice_exact(self):
        book = _book([(100.0, 1.0)], [(102.0, 3.0)])
        assert midprice(book) == 101.0

    def test_microprice_exact(self):
        # (bid_sz*ask_px + ask_sz*bid_px) / (bid_sz + ask_sz)
        # = (1*102 + 3*100) / 4 = 402/4 = 100.5 — pulled toward the big ask.
        book = _book([(100.0, 1.0)], [(102.0, 3.0)])
        assert microprice(book) == pytest.approx(100.5)

    def test_microprice_equal_sizes_is_midprice(self):
        book = _book([(100.0, 2.0)], [(102.0, 2.0)])
        assert microprice(book) == midprice(book) == 101.0

    def test_quoted_spread_exact(self):
        book = _book([(100.0, 1.0)], [(102.0, 3.0)])
        assert quoted_spread(book) == pytest.approx(2.0)

    def test_quoted_spread_bps_exact(self):
        # spread 2.0 on mid 101.0 -> 2/101 * 10000 bps.
        book = _book([(100.0, 1.0)], [(102.0, 3.0)])
        assert quoted_spread_bps(book) == pytest.approx(2.0 / 101.0 * 10_000.0)

    def test_cumulative_depth_top_n(self):
        bids = [BookLevel(100.0, 1.0), BookLevel(99.0, 2.0), BookLevel(98.0, 4.0)]
        assert cumulative_depth(bids, 1) == 1.0
        assert cumulative_depth(bids, 2) == 3.0
        assert cumulative_depth(bids, None) == 7.0
        assert cumulative_depth(bids, 10) == 7.0  # clamped to what exists

    def test_cumulative_depth_empty_side_is_none(self):
        assert cumulative_depth([], 1) is None
        assert cumulative_depth([], None) is None
        assert cumulative_depth([BookLevel(1.0, 1.0)], 0) is None

    def test_depth_imbalance_top_of_book_exact(self):
        # (B - A) / (B + A) = (3 - 1) / 4 = 0.5.
        book = _book([(100.0, 3.0)], [(101.0, 1.0)])
        assert depth_imbalance(book, levels=1) == pytest.approx(0.5)

    def test_depth_imbalance_top_n_exact(self):
        # B = 1+2 = 3, A = 2+4 = 6 -> (3-6)/9 = -1/3.
        book = _book([(100.0, 1.0), (99.0, 2.0)], [(101.0, 2.0), (102.0, 4.0)])
        assert depth_imbalance(book, levels=2) == pytest.approx(-1.0 / 3.0)

    def test_depth_imbalance_bounds(self):
        all_bid = _book([(100.0, 5.0)], [(101.0, 0.0)])
        all_ask = _book([(100.0, 0.0)], [(101.0, 5.0)])
        assert depth_imbalance(all_bid) == 1.0
        assert depth_imbalance(all_ask) == -1.0

    def test_empty_book_everything_none(self):
        book = _book([], [])
        assert midprice(book) is None
        assert microprice(book) is None
        assert quoted_spread(book) is None
        assert quoted_spread_bps(book) is None
        assert depth_imbalance(book) is None

    @pytest.mark.parametrize("bids,asks", [([(100.0, 1.0)], []), ([], [(101.0, 1.0)])])
    def test_one_sided_book_two_sided_metrics_none(self, bids, asks):
        book = _book(bids, asks)
        assert midprice(book) is None
        assert microprice(book) is None
        assert quoted_spread(book) is None
        assert quoted_spread_bps(book) is None
        assert depth_imbalance(book) is None

    def test_zero_top_sizes_microprice_and_imbalance_none(self):
        book = _book([(100.0, 0.0)], [(101.0, 0.0)])
        assert microprice(book) is None
        assert depth_imbalance(book) is None
        # Price-only metrics still defined.
        assert midprice(book) == 100.5

    def test_non_finite_prices_never_raise(self):
        book = _book([(math.nan, 1.0)], [(101.0, 1.0)])
        assert midprice(book) is None
        assert microprice(book) is None
        assert quoted_spread(book) is None
        assert depth_imbalance(book) is not None  # sizes are fine

    def test_non_finite_sizes_never_raise(self):
        book = _book([(100.0, math.inf)], [(101.0, 1.0)])
        assert microprice(book) is None
        assert depth_imbalance(book) is None
        assert cumulative_depth(book.bids, 1) is None

    def test_zero_mid_spread_bps_none(self):
        book = _book([(-1.0, 1.0)], [(1.0, 1.0)])  # mid == 0
        assert quoted_spread_bps(book) is None


class TestComputeBookFeatures:
    def test_full_bundle_exact_values(self):
        book = _book(
            [(100.0, 1.0), (99.0, 2.0), (98.0, 4.0)],
            [(101.0, 2.0), (102.0, 4.0)],
        )
        f = compute_book_features(book, levels=2)
        assert isinstance(f, BookFeatures)
        assert f.midprice == 100.5
        assert f.microprice == pytest.approx((1.0 * 101.0 + 2.0 * 100.0) / 3.0)
        assert f.quoted_spread == pytest.approx(1.0)
        assert f.quoted_spread_bps == pytest.approx(1.0 / 100.5 * 10_000.0)
        assert f.bid_depth == 3.0  # top-2 of bids
        assert f.ask_depth == 6.0
        assert f.imbalance_l1 == pytest.approx((1.0 - 2.0) / 3.0)
        assert f.imbalance == pytest.approx((3.0 - 6.0) / 9.0)
        assert f.levels == 2

    def test_empty_book_degrades_to_none_never_raises(self):
        f = compute_book_features(_book([], []))
        assert f.midprice is None
        assert f.microprice is None
        assert f.quoted_spread is None
        assert f.quoted_spread_bps is None
        assert f.bid_depth is None
        assert f.ask_depth is None
        assert f.imbalance_l1 is None
        assert f.imbalance is None

    def test_one_sided_book_keeps_own_side_depth(self):
        f = compute_book_features(_book([(100.0, 1.5), (99.0, 0.5)], []), levels=5)
        assert f.bid_depth == 2.0
        assert f.ask_depth is None
        assert f.midprice is None


# --- Feature 1: bar-features accumulation -----------------------------------


def _minute_trades():
    """Known buy/sell mix inside one minute + a roll-over trade in the next."""
    t0 = datetime(2024, 1, 1, 12, 0, 1, tzinfo=UTC)
    in_bar = [
        _trade(100.0, 2.0, "buy", ts=t0),
        _trade(101.0, 1.0, "sell", ts=t0.replace(second=10)),
        _trade(102.0, 3.0, "buy", ts=t0.replace(second=50)),
    ]
    rollover = _trade(105.0, 1.0, "sell", ts=datetime(2024, 1, 1, 12, 1, 0, tzinfo=UTC))
    return in_bar, rollover


class TestBarFeaturesAccumulation:
    def test_exact_vwap_and_imbalance_across_known_mix(self):
        norm = TickNormalizer(enable_bar_features=True)
        in_bar, rollover = _minute_trades()
        for t in in_bar:
            assert norm.accumulate_trade(t) is None
        bar = norm.accumulate_trade(rollover)
        assert bar is not None and bar.volume == 6.0

        f = norm.pop_bar_features("btcusdt")
        assert isinstance(f, BarFeatures)
        assert f.buy_volume == 5.0  # 2 + 3
        assert f.sell_volume == 1.0
        assert f.imbalance == pytest.approx((5.0 - 1.0) / 6.0)
        # VWAP = (100*2 + 101*1 + 102*3) / 6 = 607/6.
        assert f.vwap == pytest.approx(607.0 / 6.0)

    def test_pop_clears_the_stash(self):
        norm = TickNormalizer(enable_bar_features=True)
        in_bar, rollover = _minute_trades()
        for t in in_bar:
            norm.accumulate_trade(t)
        norm.accumulate_trade(rollover)
        assert norm.pop_bar_features("btcusdt") is not None
        assert norm.pop_bar_features("btcusdt") is None  # one-shot

    def test_flush_also_computes_features(self):
        norm = TickNormalizer(enable_bar_features=True)
        norm.accumulate_trade(_trade(100.0, 1.0, "sell"))
        bar = norm.flush("btcusdt")
        assert bar is not None
        f = norm.pop_bar_features("btcusdt")
        assert f == BarFeatures(buy_volume=0.0, sell_volume=1.0, imbalance=-1.0, vwap=100.0)

    def test_zero_volume_bar_falls_back_to_close_and_neutral_imbalance(self):
        norm = TickNormalizer(enable_bar_features=True)
        norm.accumulate_trade(_trade(100.0, 0.0, "buy"))
        norm.accumulate_trade(_trade(104.0, 0.0, "sell", ts=TS.replace(second=30)))
        bar = norm.flush("btcusdt")
        assert bar is not None and bar.volume == 0.0
        f = norm.pop_bar_features("btcusdt")
        assert f.imbalance == 0.0  # no division by zero
        assert f.vwap == bar.close == 104.0  # close fallback

    def test_disabled_by_default_pop_returns_none(self):
        norm = TickNormalizer()  # default: enrichment off
        in_bar, rollover = _minute_trades()
        for t in in_bar:
            norm.accumulate_trade(t)
        bar = norm.accumulate_trade(rollover)
        assert bar is not None  # the bar itself is unchanged
        assert norm.pop_bar_features("btcusdt") is None

    def test_per_symbol_stashes_are_independent(self):
        norm = TickNormalizer(enable_bar_features=True)
        norm.accumulate_trade(_trade(100.0, 1.0, "buy", symbol="btcusdt"))
        norm.accumulate_trade(_trade(10.0, 2.0, "sell", symbol="ethusdt"))
        norm.flush_all()
        assert norm.pop_bar_features("btcusdt").imbalance == 1.0
        assert norm.pop_bar_features("ethusdt").imbalance == -1.0


# --- Config / CLI wiring -----------------------------------------------------


class TestBarFeaturesFlag:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("ENABLE_BAR_FEATURES", raising=False)
        args = cli_main.build_parser().parse_args([])
        assert args.enable_bar_features is False
        assert cli_main.build_config(args).enable_bar_features is False

    def test_cli_flag_turns_it_on(self, monkeypatch):
        monkeypatch.delenv("ENABLE_BAR_FEATURES", raising=False)
        args = cli_main.build_parser().parse_args(["--enable-bar-features"])
        assert cli_main.build_config(args).enable_bar_features is True

    def test_env_var_parsing(self, monkeypatch):
        for truthy in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("ENABLE_BAR_FEATURES", truthy)
            assert Config().enable_bar_features is True
        for falsy in ("0", "false", "no", "", "off"):
            monkeypatch.setenv("ENABLE_BAR_FEATURES", falsy)
            assert Config().enable_bar_features is False

    def test_flag_reaches_the_normalizer(self, monkeypatch):
        monkeypatch.setenv("ENABLE_BAR_FEATURES", "1")
        monkeypatch.delenv("ENABLE_DEPTH", raising=False)
        p = Pipeline(Config())
        assert p.normalizer.enable_bar_features is True


# --- Cache: bookfeat methods --------------------------------------------------


class _FakeRedisKV:
    def __init__(self):
        self.kv: dict[str, str] = {}

    async def set(self, key, value):
        self.kv[key] = value

    async def get(self, key):
        return self.kv.get(key)


class TestRedisCacheBookFeatures:
    async def test_set_and_get_book_features(self):
        cache = RedisCache("redis://x")
        cache._client = _FakeRedisKV()
        features = {"symbol": "btcusdt", "midprice": 100.5, "imbalance": 0.25}
        await cache.set_book_features("btcusdt", features)
        assert await cache.get_book_features("btcusdt") == features
        assert "bookfeat:btcusdt" in cache._client.kv

    async def test_get_book_features_missing_is_none(self):
        cache = RedisCache("redis://x")
        cache._client = _FakeRedisKV()
        assert await cache.get_book_features("nope") is None


# --- Storage round-trips ------------------------------------------------------


def _features_dict(ts=None, symbol="btcusdt"):
    return {
        "timestamp": ts or TS,
        "symbol": symbol,
        "buy_volume": 5.0,
        "sell_volume": 1.0,
        "imbalance": 4.0 / 6.0,
        "vwap": 607.0 / 6.0,
        "interval": "1m",
    }


class TestDuckDBBarFeatures:
    @pytest.fixture
    async def storage(self):
        s = DuckDBStorage(":memory:")
        await s.connect()
        await s.init_schema()
        yield s
        await s.disconnect()

    async def test_insert_and_query_round_trips(self, storage):
        await storage.insert_bar_features(_features_dict())
        rows = await storage.query_bar_features(
            "btcusdt", "1m", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert len(rows) == 1
        r = rows[0]
        assert r["symbol"] == "btcusdt"
        assert r["buy_volume"] == 5.0
        assert r["sell_volume"] == 1.0
        assert r["imbalance"] == pytest.approx(4.0 / 6.0)
        assert r["vwap"] == pytest.approx(607.0 / 6.0)
        assert r["time"] == TS

    async def test_query_ordered_ascending_and_filters(self, storage):
        t0, t1 = TS, TS.replace(minute=1)
        await storage.insert_bar_features(_features_dict(ts=t1))
        await storage.insert_bar_features(_features_dict(ts=t0))
        await storage.insert_bar_features(_features_dict(ts=t0, symbol="ethusdt"))
        rows = await storage.query_bar_features(
            "btcusdt", "1m", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert [r["time"] for r in rows] == [t0, t1]
        assert all(r["symbol"] == "btcusdt" for r in rows)

    async def test_interval_filter(self, storage):
        await storage.insert_bar_features(_features_dict())
        rows = await storage.query_bar_features(
            "btcusdt", "5m", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert rows == []


class TestTimescaleBarFeatures:
    @pytest.fixture
    def storage(self, fake_pool):
        s = TimeSeriesStorage("postgresql://fake/db")
        s._pool = fake_pool
        return s

    async def test_schema_includes_bar_features_hypertable(self, storage, fake_pool):
        await storage.init_schema()
        ((query, _),) = fake_pool.executed
        assert "CREATE TABLE IF NOT EXISTS bar_features" in query
        assert "create_hypertable('bar_features'" in query
        assert "idx_bar_features_symbol_time" in query

    async def test_insert_passes_all_columns_in_order(self, storage, fake_pool):
        await storage.insert_bar_features(_features_dict())
        query, args = fake_pool.executed[0]
        assert "INSERT INTO bar_features" in query
        assert args == (TS, "btcusdt", 5.0, 1.0, 4.0 / 6.0, 607.0 / 6.0, "1m")

    async def test_query_passes_bounds(self, storage, fake_pool):
        fake_pool.fetch_result = [{"symbol": "btcusdt", "vwap": 101.0}]
        rows = await storage.query_bar_features(
            "btcusdt", "1m", datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, tzinfo=UTC)
        )
        assert rows == [{"symbol": "btcusdt", "vwap": 101.0}]
        query, args = fake_pool.fetched[0]
        assert "FROM bar_features" in query
        assert args[0] == "btcusdt" and args[1] == "1m"


# --- Pipeline wiring (fakes) ---------------------------------------------------


class _FakeCache:
    def __init__(self):
        self.books = {}
        self.book_features = {}
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

    async def set_book_features(self, symbol, features):
        self.book_features[symbol] = features

    async def publish(self, channel, message):
        self.published.append((channel, message))


class _FakeStorage:
    def __init__(self):
        self.trades_inserted = []
        self.ohlcv_inserted = []
        self.books_inserted = []
        self.bar_features_inserted = []

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
        self.books_inserted.append(book)

    async def insert_bar_features(self, features):
        self.bar_features_inserted.append(features)


def _pipeline(monkeypatch, *, bar_features=False, depth=False):
    if bar_features:
        monkeypatch.setenv("ENABLE_BAR_FEATURES", "1")
    else:
        monkeypatch.delenv("ENABLE_BAR_FEATURES", raising=False)
    if depth:
        monkeypatch.setenv("ENABLE_DEPTH", "1")
    else:
        monkeypatch.delenv("ENABLE_DEPTH", raising=False)
    cfg = Config()
    cfg.exchange = "binance"
    cfg.symbols = ["btcusdt"]
    p = Pipeline(cfg)
    p.cache = _FakeCache()
    p.storage = _FakeStorage()
    return p


def _binance_trade_msg(price, qty, is_buyer_maker, t_ms):
    return {
        "e": "trade",
        "s": "BTCUSDT",
        "p": str(price),
        "q": str(qty),
        "m": is_buyer_maker,
        "T": t_ms,
    }


# 2024-04-06T10:40:00Z, in ms.
_MINUTE0_MS = 1712400000000
_MINUTE1_MS = _MINUTE0_MS + 60_000


class TestPipelineBarFeatures:
    async def test_bar_completion_publishes_and_persists_features(self, monkeypatch):
        p = _pipeline(monkeypatch, bar_features=True)
        # m=False -> taker buy; m=True -> taker sell.
        await p._on_message(_binance_trade_msg(100.0, 2.0, False, _MINUTE0_MS))
        await p._on_message(_binance_trade_msg(102.0, 1.0, True, _MINUTE0_MS + 30_000))
        assert p.storage.bar_features_inserted == []  # bar not closed yet
        await p._on_message(_binance_trade_msg(105.0, 1.0, False, _MINUTE1_MS))

        assert len(p.storage.ohlcv_inserted) == 1
        assert len(p.storage.bar_features_inserted) == 1
        row = p.storage.bar_features_inserted[0]
        assert row["symbol"] == "btcusdt"
        assert row["interval"] == "1m"
        assert row["timestamp"] == p.storage.ohlcv_inserted[0]["timestamp"]
        assert row["buy_volume"] == 2.0
        assert row["sell_volume"] == 1.0
        assert row["imbalance"] == pytest.approx(1.0 / 3.0)
        assert row["vwap"] == pytest.approx((100.0 * 2.0 + 102.0 * 1.0) / 3.0)
        # Published on barfeat:<symbol> with the same payload.
        barfeat = [m for c, m in p.cache.published if c == "barfeat:btcusdt"]
        assert barfeat == [row]

    async def test_stop_flush_emits_final_bar_features(self, monkeypatch):
        p = _pipeline(monkeypatch, bar_features=True)
        await p._on_message(_binance_trade_msg(100.0, 4.0, True, _MINUTE0_MS))
        await p.stop()  # final in-progress bar flushed at shutdown
        assert len(p.storage.ohlcv_inserted) == 1
        assert len(p.storage.bar_features_inserted) == 1
        row = p.storage.bar_features_inserted[0]
        assert row["sell_volume"] == 4.0 and row["buy_volume"] == 0.0
        assert row["imbalance"] == -1.0
        assert [c for c, _ in p.cache.published if c == "barfeat:btcusdt"] == ["barfeat:btcusdt"]

    async def test_stop_flush_feature_sink_failure_is_swallowed(self, monkeypatch, caplog):
        import logging

        p = _pipeline(monkeypatch, bar_features=True)

        async def boom(features):
            raise RuntimeError("features sink down")

        p.storage.insert_bar_features = boom
        await p._on_message(_binance_trade_msg(100.0, 1.0, False, _MINUTE0_MS))
        with caplog.at_level(logging.ERROR):
            await p.stop()  # must not raise; best-effort on shutdown
        assert len(p.storage.ohlcv_inserted) == 1  # final bar still persisted
        assert "Failed to persist final bar features" in caplog.text

    async def test_flag_off_no_barfeat_anywhere(self, monkeypatch):
        p = _pipeline(monkeypatch, bar_features=False)
        await p._on_message(_binance_trade_msg(100.0, 2.0, False, _MINUTE0_MS))
        await p._on_message(_binance_trade_msg(105.0, 1.0, False, _MINUTE1_MS))
        await p.stop()
        assert p.storage.bar_features_inserted == []
        assert all(not c.startswith("barfeat:") for c, _ in p.cache.published)


class TestPipelineBookFeatures:
    BINANCE_DEPTH = {
        "lastUpdateId": 7,
        "bids": [["100.0", "3.0"], ["99.0", "1.0"]],
        "asks": [["101.0", "1.0"]],
    }

    async def test_depth_message_caches_and_publishes_book_features(self, monkeypatch):
        p = _pipeline(monkeypatch, depth=True)
        await p._on_depth_message(self.BINANCE_DEPTH)
        feat = p.cache.book_features["btcusdt"]
        assert feat["symbol"] == "btcusdt"
        assert feat["midprice"] == 100.5
        assert feat["quoted_spread"] == pytest.approx(1.0)
        assert feat["imbalance_l1"] == pytest.approx((3.0 - 1.0) / 4.0)
        # microprice = (3*101 + 1*100)/4 = 100.75
        assert feat["microprice"] == pytest.approx(100.75)
        published = [m for c, m in p.cache.published if c == "bookfeat:btcusdt"]
        assert published == [feat]

    async def test_one_sided_snapshot_degrades_without_killing_feed(self, monkeypatch):
        p = _pipeline(monkeypatch, depth=True)
        await p._on_depth_message({"lastUpdateId": 8, "bids": [["100.0", "1.0"]], "asks": []})
        feat = p.cache.book_features["btcusdt"]
        assert feat["midprice"] is None
        assert feat["bid_depth"] == 1.0
        assert feat["ask_depth"] is None

    async def test_book_features_cache_failure_is_swallowed(self, monkeypatch):
        p = _pipeline(monkeypatch, depth=True)

        async def boom(symbol, features):
            raise RuntimeError("cache down")

        p.cache.set_book_features = boom
        await p._on_depth_message(self.BINANCE_DEPTH)  # must not raise
        assert len(p.storage.books_inserted) == 1  # raw book still persisted


# --- End-to-end via FakeWebSocket (flags ON) ----------------------------------


class TestFeaturesEndToEnd:
    async def test_trades_stream_emits_bar_features_end_to_end(self, monkeypatch, fake_ws_factory):
        p = _pipeline(monkeypatch, bar_features=True)
        messages = [
            json.dumps(_binance_trade_msg(100.0, 2.0, False, _MINUTE0_MS)),
            json.dumps(_binance_trade_msg(102.0, 1.0, True, _MINUTE0_MS + 10_000)),
            json.dumps(_binance_trade_msg(105.0, 1.0, False, _MINUTE1_MS)),
        ]
        ws = fake_ws_factory(messages=messages)

        def connect(url, **kwargs):
            p.client._running = False  # one pass through the consume loop
            return ws

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)
        p.client.on_message(p._on_message)
        await p.client.connect(["btcusdt"])

        assert len(p.storage.ohlcv_inserted) == 1
        assert len(p.storage.bar_features_inserted) == 1
        row = p.storage.bar_features_inserted[0]
        assert row["buy_volume"] == 2.0 and row["sell_volume"] == 1.0
        assert row["vwap"] == pytest.approx(302.0 / 3.0)
        assert any(c == "barfeat:btcusdt" for c, _ in p.cache.published)

    async def test_depth_stream_emits_book_features_end_to_end(self, monkeypatch, fake_ws_factory):
        p = _pipeline(monkeypatch, depth=True)
        assert p.depth_client is not None
        messages = [
            json.dumps({"lastUpdateId": 1, "bids": [["100.0", "1.0"]], "asks": [["101.0", "3.0"]]}),
            json.dumps({"lastUpdateId": 2, "bids": [["110.0", "2.0"]], "asks": [["111.0", "2.0"]]}),
        ]
        ws = fake_ws_factory(messages=messages)

        def connect(url, **kwargs):
            p.depth_client._running = False
            return ws

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)
        p.depth_client.on_message(p._on_depth_message)
        await p.depth_client.connect(["btcusdt"])

        # Latest snapshot's features cached; one publish per snapshot.
        feat = p.cache.book_features["btcusdt"]
        assert feat["midprice"] == 110.5
        assert feat["imbalance_l1"] == 0.0
        assert len([c for c, _ in p.cache.published if c == "bookfeat:btcusdt"]) == 2


# --- Byte-parity of the default path (mirrors TestSingleSymbolTradesParity) ---


class TestDefaultPathParity:
    async def test_trades_path_byte_identical_with_all_features_off(self, monkeypatch):
        """With ENABLE_BAR_FEATURES and ENABLE_DEPTH both unset, the pipeline
        behaves exactly as before: no depth/feature machinery, identical bar
        dicts (no new keys), no barfeat/bookfeat traffic, no bar_features rows.
        """
        p = _pipeline(monkeypatch, bar_features=False, depth=False)
        # No depth machinery, no enrichment.
        assert p.depth_adapter is None
        assert p.depth_client is None
        assert p.normalizer.enable_bar_features is False
        # Trades client still uses the original Binance stream URL.
        assert p.client.adapter.ws_url(["btcusdt"]) == p.config.ws_url + "/btcusdt@trade"

        await p._on_message(_binance_trade_msg(100.0, 1.0, False, _MINUTE0_MS))
        await p._on_message(_binance_trade_msg(101.0, 2.0, True, _MINUTE1_MS))
        await p.stop()

        # The OHLCV rows carry exactly the pre-feature key set and values.
        assert len(p.storage.ohlcv_inserted) == 2
        first = p.storage.ohlcv_inserted[0]
        assert set(first.keys()) == {
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "timestamp",
            "interval",
            "trade_count",
        }
        assert (first["open"], first["close"], first["volume"]) == (100.0, 100.0, 1.0)
        # Zero feature traffic on cache or storage.
        assert p.storage.bar_features_inserted == []
        assert p.cache.book_features == {}
        channels = [c for c, _ in p.cache.published]
        assert all(c.startswith("trades:") for c in channels)
        assert p.normalizer.pop_bar_features("btcusdt") is None

    def test_bar_dataclass_unchanged_when_off(self):
        """asdict(bar) of a default normalizer's output is byte-identical to
        the legacy shape (BarFeatures lives in a separate dataclass/table)."""
        norm = TickNormalizer()
        norm.accumulate_trade(_trade(100.0, 1.0, "buy"))
        bar = norm.flush("btcusdt")
        assert asdict(bar) == {
            "symbol": "btcusdt",
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1.0,
            "timestamp": TS.replace(second=0),
            "interval": "1m",
            "trade_count": 1,
        }
