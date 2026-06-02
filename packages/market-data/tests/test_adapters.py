"""Tests for the pluggable ExchangeAdapter protocol and its implementations.

No live network: each adapter is fed a captured/representative raw message dict
and asserted to normalize to the exact expected Trade, plus the subscribe
payload / URL construction for given symbols. The end-to-end Coinbase pipeline
test drives the WS client with a FakeWebSocket replaying canned messages.
"""

import json
from datetime import UTC, datetime

import pytest

from src.adapters import (
    BinanceAdapter,
    BitstampAdapter,
    CoinbaseAdapter,
    ExchangeAdapter,
    KrakenAdapter,
    build_adapter,
)
from src.config import Config
from src.normalizer import TickNormalizer, Trade
from src.pipeline import Pipeline, build_exchange_adapter
from src.websocket_client import MarketDataClient

# --- Captured / representative raw messages -------------------------------

BINANCE_TRADE = {
    "e": "trade",
    "s": "BTCUSDT",
    "p": "67500.50",
    "q": "0.15",
    "m": False,
    "T": 1712400000000,
}

# Coinbase Exchange "matches" channel message (from the documented schema).
COINBASE_MATCH = {
    "type": "match",
    "trade_id": 10,
    "sequence": 50,
    "maker_order_id": "ac928c66-ca53-498f-9c13-a110027a60e8",
    "taker_order_id": "132fb6ae-456b-4654-b4e0-d681ac05cea1",
    "time": "2014-11-07T08:19:27.028459Z",
    "product_id": "BTC-USD",
    "size": "5.23512",
    "price": "400.23",
    "side": "sell",
}


# Kraken WebSocket v1 trade update: [channelID, [[trade...], ...], "trade", "PAIR"].
# Each inner trade is [price, volume, time, side, orderType, misc]; side b/s is
# the taker/aggressor side (no flip needed). Times are seconds.fraction strings.
KRAKEN_TRADE = [
    0,
    [["5541.20000", "0.15850568", "1534614057.321597", "s", "l", ""]],
    "trade",
    "XBT/USD",
]

# Bitstamp live_trades channel message envelope; data.type 0=buy, 1=sell
# (taker/aggressor side). microtimestamp is microseconds-since-epoch (string).
BITSTAMP_TRADE = {
    "event": "trade",
    "channel": "live_trades_btcusd",
    "data": {
        "id": 123456789,
        "timestamp": "1505558814",
        "amount": 0.01513062,
        "amount_str": "0.01513062",
        "price": 212.8,
        "price_str": "212.8",
        "type": 0,
        "microtimestamp": "1505558814000000",
        "buy_order_id": 111,
        "sell_order_id": 222,
    },
}


# --- Protocol conformance --------------------------------------------------


class TestProtocolConformance:
    def test_all_adapters_are_exchange_adapters(self):
        assert isinstance(BinanceAdapter(), ExchangeAdapter)
        assert isinstance(CoinbaseAdapter(), ExchangeAdapter)
        assert isinstance(KrakenAdapter(), ExchangeAdapter)
        assert isinstance(BitstampAdapter(), ExchangeAdapter)

    def test_build_adapter_selects_by_name(self):
        assert isinstance(build_adapter("binance"), BinanceAdapter)
        assert isinstance(build_adapter("coinbase"), CoinbaseAdapter)
        assert isinstance(build_adapter("kraken"), KrakenAdapter)
        assert isinstance(build_adapter("bitstamp"), BitstampAdapter)

    def test_build_adapter_is_case_insensitive(self):
        assert isinstance(build_adapter("COINBASE"), CoinbaseAdapter)
        assert isinstance(build_adapter("Kraken"), KrakenAdapter)
        assert isinstance(build_adapter("BITSTAMP"), BitstampAdapter)

    def test_build_adapter_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown EXCHANGE 'bogus'"):
            build_adapter("bogus")


# --- Binance adapter -------------------------------------------------------


class TestBinanceAdapter:
    def test_normalizes_trade_exactly(self):
        adapter = BinanceAdapter()
        trade = adapter.normalize_trade(BINANCE_TRADE)
        assert trade == Trade(
            symbol="btcusdt",
            price=67500.50,
            quantity=0.15,
            side="buy",
            timestamp=datetime.fromtimestamp(1712400000000 / 1000, tz=UTC),
            exchange="binance",
        )

    def test_maker_flag_maps_to_sell(self):
        adapter = BinanceAdapter()
        msg = dict(BINANCE_TRADE, m=True)
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.side == "sell"

    def test_message_with_no_event_type_still_parses(self):
        # The original pipeline payloads (raw @trade stream) have no "e" key.
        adapter = BinanceAdapter()
        msg = {k: v for k, v in BINANCE_TRADE.items() if k != "e"}
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.symbol == "btcusdt"

    def test_non_trade_event_ignored(self):
        adapter = BinanceAdapter()
        assert adapter.normalize_trade({"e": "depthUpdate", "s": "BTCUSDT"}) is None

    def test_malformed_message_returns_none(self):
        adapter = BinanceAdapter()
        assert adapter.normalize_trade({"garbage": True}) is None
        assert adapter.normalize_trade({"s": "BTCUSDT", "p": "notafloat"}) is None

    def test_list_payload_ignored(self):
        # Binance never sends arrays; defensively ignore a non-dict payload.
        assert BinanceAdapter().normalize_trade([1, 2, 3]) is None

    def test_ws_url_embeds_streams(self):
        adapter = BinanceAdapter("wss://stream.binance.com:9443/ws")
        assert (
            adapter.ws_url(["btcusdt", "ethusdt"])
            == "wss://stream.binance.com:9443/ws/btcusdt@trade/ethusdt@trade"
        )

    def test_ws_url_strips_trailing_slash(self):
        adapter = BinanceAdapter("wss://base/")
        assert adapter.ws_url(["btcusdt"]) == "wss://base/btcusdt@trade"

    def test_no_subscribe_payload(self):
        # Streams are URL-embedded, so nothing is sent after connect.
        assert BinanceAdapter().subscribe_payload(["btcusdt"]) is None

    def test_name(self):
        assert BinanceAdapter().name == "binance"


# --- Coinbase adapter ------------------------------------------------------


class TestCoinbaseAdapter:
    def test_normalizes_match_exactly(self):
        adapter = CoinbaseAdapter()
        trade = adapter.normalize_trade(COINBASE_MATCH)
        assert trade == Trade(
            symbol="btcusd",
            price=400.23,
            quantity=5.23512,
            # Coinbase side is the MAKER side ("sell"); the aggressor/taker
            # bought, so the normalized (taker-perspective) side is "buy".
            side="buy",
            timestamp=datetime(2014, 11, 7, 8, 19, 27, 28459, tzinfo=UTC),
            exchange="coinbase",
        )

    def test_maker_buy_maps_to_taker_sell(self):
        adapter = CoinbaseAdapter()
        msg = dict(COINBASE_MATCH, side="buy")
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.side == "sell"

    def test_last_match_snapshot_is_a_trade(self):
        adapter = CoinbaseAdapter()
        msg = dict(COINBASE_MATCH, type="last_match")
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.symbol == "btcusd"

    def test_subscription_ack_ignored(self):
        adapter = CoinbaseAdapter()
        ack = {"type": "subscriptions", "channels": [{"name": "matches"}]}
        assert adapter.normalize_trade(ack) is None

    def test_heartbeat_ignored(self):
        adapter = CoinbaseAdapter()
        assert adapter.normalize_trade({"type": "heartbeat"}) is None

    def test_list_payload_ignored(self):
        # Coinbase never sends arrays; defensively ignore a non-dict payload.
        assert CoinbaseAdapter().normalize_trade([1, 2, 3]) is None

    def test_malformed_match_returns_none(self):
        adapter = CoinbaseAdapter()
        # Right type but missing required fields.
        assert adapter.normalize_trade({"type": "match", "side": "sell"}) is None
        # Right type but non-numeric price.
        bad = dict(COINBASE_MATCH, price="NaN-ish")
        assert adapter.normalize_trade(bad) is None

    def test_timestamp_without_zone_assumed_utc(self):
        adapter = CoinbaseAdapter()
        msg = dict(COINBASE_MATCH, time="2014-11-07T08:19:27.028459")
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.timestamp.tzinfo is not None
        assert trade.timestamp == datetime(2014, 11, 7, 8, 19, 27, 28459, tzinfo=UTC)

    def test_ws_url_is_fixed_feed(self):
        adapter = CoinbaseAdapter()
        assert adapter.ws_url(["btcusd", "ethusd"]) == "wss://ws-feed.exchange.coinbase.com"

    def test_subscribe_payload_maps_symbols_to_products(self):
        adapter = CoinbaseAdapter()
        payload = adapter.subscribe_payload(["btcusd", "ethusd"])
        assert payload == {
            "type": "subscribe",
            "channels": [{"name": "matches", "product_ids": ["BTC-USD", "ETH-USD"]}],
        }

    def test_subscribe_payload_accepts_already_dashed_symbols(self):
        adapter = CoinbaseAdapter()
        payload = adapter.subscribe_payload(["btc-usd", "ETH-EUR"])
        assert payload is not None
        assert payload["channels"][0]["product_ids"] == ["BTC-USD", "ETH-EUR"]

    def test_short_symbol_left_as_is(self):
        # A symbol too short to split into base/3-char quote is passed through.
        adapter = CoinbaseAdapter()
        payload = adapter.subscribe_payload(["btc"])
        assert payload is not None
        assert payload["channels"][0]["product_ids"] == ["BTC"]

    def test_product_id_round_trips_to_pipeline_symbol(self):
        adapter = CoinbaseAdapter()
        # configured btcusd -> BTC-USD on subscribe -> btcusd on parse
        product = adapter.subscribe_payload(["btcusd"])["channels"][0]["product_ids"][0]
        assert product == "BTC-USD"
        trade = adapter.normalize_trade(dict(COINBASE_MATCH, product_id=product))
        assert trade is not None
        assert trade.symbol == "btcusd"

    def test_name(self):
        assert CoinbaseAdapter().name == "coinbase"


# --- Kraken adapter --------------------------------------------------------


class TestKrakenAdapter:
    def test_normalizes_trade_exactly(self):
        adapter = KrakenAdapter()
        trade = adapter.normalize_trade(KRAKEN_TRADE)
        assert trade == Trade(
            symbol="btcusd",  # XBT/USD -> btcusd
            price=5541.20000,
            quantity=0.15850568,
            # Kraken side "s" is already the taker/aggressor side -> "sell".
            side="sell",
            timestamp=datetime.fromtimestamp(1534614057.321597, tz=UTC),
            exchange="kraken",
        )

    def test_buy_side_maps_to_buy(self):
        adapter = KrakenAdapter()
        msg = [0, [["100.0", "1.0", "1534614057.0", "b", "l", ""]], "trade", "XBT/USD"]
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.side == "buy"

    def test_first_trade_of_a_batch_is_returned(self):
        adapter = KrakenAdapter()
        msg = [
            0,
            [
                ["100.0", "1.0", "1534614057.0", "b", "l", ""],
                ["101.0", "2.0", "1534614058.0", "s", "m", ""],
            ],
            "trade",
            "XBT/USD",
        ]
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.price == 100.0
        assert trade.side == "buy"

    def test_event_object_ignored(self):
        # Subscription acks / system status are JSON objects, not arrays.
        adapter = KrakenAdapter()
        status = {"event": "subscriptionStatus", "status": "subscribed"}
        assert adapter.normalize_trade(status) is None
        assert adapter.normalize_trade({"event": "heartbeat"}) is None

    def test_non_trade_channel_ignored(self):
        adapter = KrakenAdapter()
        msg = [0, [["1", "1", "1.0", "b", "l", ""]], "ticker", "XBT/USD"]
        assert adapter.normalize_trade(msg) is None

    def test_empty_trade_list_returns_none(self):
        adapter = KrakenAdapter()
        assert adapter.normalize_trade([0, [], "trade", "XBT/USD"]) is None

    def test_short_array_returns_none(self):
        adapter = KrakenAdapter()
        assert adapter.normalize_trade([0, []]) is None

    def test_malformed_trade_returns_none(self):
        adapter = KrakenAdapter()
        bad = [0, [["notafloat", "1.0", "1.0", "b", "l", ""]], "trade", "XBT/USD"]
        assert adapter.normalize_trade(bad) is None

    def test_ws_url_is_fixed_feed(self):
        adapter = KrakenAdapter()
        assert adapter.ws_url(["btcusd"]) == "wss://ws.kraken.com"

    def test_subscribe_payload_maps_symbols_to_pairs(self):
        adapter = KrakenAdapter()
        payload = adapter.subscribe_payload(["btcusd", "ethusd"])
        assert payload == {
            "event": "subscribe",
            "subscription": {"name": "trade"},
            "pair": ["XBT/USD", "ETH/USD"],
        }

    def test_subscribe_accepts_already_slashed_and_xbt(self):
        adapter = KrakenAdapter()
        payload = adapter.subscribe_payload(["XBT/USD", "xbteur"])
        assert payload is not None
        assert payload["pair"] == ["XBT/USD", "XBT/EUR"]

    def test_subscribe_short_symbol_left_unquoted(self):
        # A symbol too short to split into base + 3-char quote maps to bare base.
        adapter = KrakenAdapter()
        payload = adapter.subscribe_payload(["btc"])
        assert payload is not None
        assert payload["pair"] == ["XBT"]

    def test_pair_round_trips_to_pipeline_symbol(self):
        adapter = KrakenAdapter()
        # configured btcusd -> XBT/USD on subscribe -> btcusd on parse
        pair = adapter.subscribe_payload(["btcusd"])["pair"][0]
        assert pair == "XBT/USD"
        msg = [0, [["1", "1", "1.0", "b", "l", ""]], "trade", pair]
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.symbol == "btcusd"

    def test_non_xbt_pair_round_trips_unchanged(self):
        # A non-bitcoin pair keeps its base (no XBT<->btc remap).
        adapter = KrakenAdapter()
        msg = [0, [["3000.0", "1.0", "1.0", "b", "l", ""]], "trade", "ETH/USD"]
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.symbol == "ethusd"

    def test_name(self):
        assert KrakenAdapter().name == "kraken"


# --- Bitstamp adapter ------------------------------------------------------


class TestBitstampAdapter:
    def test_normalizes_trade_exactly(self):
        adapter = BitstampAdapter()
        trade = adapter.normalize_trade(BITSTAMP_TRADE)
        assert trade == Trade(
            symbol="btcusd",
            price=212.8,
            quantity=0.01513062,
            # type 0 = buy (taker/aggressor side).
            side="buy",
            timestamp=datetime.fromtimestamp(1505558814000000 / 1_000_000, tz=UTC),
            exchange="bitstamp",
        )

    def test_type_one_maps_to_sell(self):
        adapter = BitstampAdapter()
        msg = {**BITSTAMP_TRADE, "data": {**BITSTAMP_TRADE["data"], "type": 1}}
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.side == "sell"

    def test_falls_back_to_timestamp_without_microtimestamp(self):
        adapter = BitstampAdapter()
        data = {k: v for k, v in BITSTAMP_TRADE["data"].items() if k != "microtimestamp"}
        msg = {**BITSTAMP_TRADE, "data": data}
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.timestamp == datetime.fromtimestamp(1505558814, tz=UTC)

    def test_subscription_ack_ignored(self):
        adapter = BitstampAdapter()
        ack = {
            "event": "bts:subscription_succeeded",
            "channel": "live_trades_btcusd",
            "data": {},
        }
        assert adapter.normalize_trade(ack) is None

    def test_reconnect_request_ignored(self):
        adapter = BitstampAdapter()
        assert adapter.normalize_trade({"event": "bts:request_reconnect", "data": {}}) is None

    def test_malformed_trade_returns_none(self):
        adapter = BitstampAdapter()
        # Right event but missing data fields.
        assert adapter.normalize_trade({"event": "trade", "data": {}}) is None
        # Non-numeric price.
        bad = {**BITSTAMP_TRADE, "data": {**BITSTAMP_TRADE["data"], "price": "NaN-ish"}}
        assert adapter.normalize_trade(bad) is None

    def test_ws_url_is_fixed_feed(self):
        adapter = BitstampAdapter()
        assert adapter.ws_url(["btcusd"]) == "wss://ws.bitstamp.net"

    def test_subscribe_payload_maps_first_symbol_to_channel(self):
        adapter = BitstampAdapter()
        payload = adapter.subscribe_payload(["btcusd"])
        assert payload == {
            "event": "bts:subscribe",
            "data": {"channel": "live_trades_btcusd"},
        }

    def test_subscribe_strips_dashes_and_slashes(self):
        adapter = BitstampAdapter()
        payload = adapter.subscribe_payload(["BTC-USD"])
        assert payload is not None
        assert payload["data"]["channel"] == "live_trades_btcusd"

    def test_channel_round_trips_to_pipeline_symbol(self):
        adapter = BitstampAdapter()
        channel = adapter.subscribe_payload(["btcusd"])["data"]["channel"]
        assert channel == "live_trades_btcusd"
        msg = {**BITSTAMP_TRADE, "channel": channel}
        trade = adapter.normalize_trade(msg)
        assert trade is not None
        assert trade.symbol == "btcusd"

    def test_name(self):
        assert BitstampAdapter().name == "bitstamp"


# --- Normalizer delegation -------------------------------------------------


class TestNormalizerDelegation:
    def test_default_normalizer_parses_binance(self):
        norm = TickNormalizer()
        trade = norm.normalize_trade(BINANCE_TRADE)
        assert trade is not None
        assert trade.exchange == "binance"

    def test_normalizer_uses_injected_adapter(self):
        norm = TickNormalizer(CoinbaseAdapter())
        trade = norm.normalize_trade(COINBASE_MATCH)
        assert trade is not None
        assert trade.exchange == "coinbase"
        assert trade.symbol == "btcusd"

    def test_ohlcv_rollup_is_adapter_agnostic(self):
        """The roll-up works on normalized Trades regardless of source venue."""
        norm = TickNormalizer(CoinbaseAdapter())
        base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        t1 = Trade("btcusd", 100.0, 1.0, "buy", base, "coinbase")
        t2 = Trade("btcusd", 110.0, 2.0, "sell", base.replace(second=30), "coinbase")
        t3 = Trade("btcusd", 120.0, 1.0, "buy", base.replace(minute=1), "coinbase")
        assert norm.accumulate_trade(t1) is None
        assert norm.accumulate_trade(t2) is None
        bar = norm.accumulate_trade(t3)
        assert bar is not None
        assert (bar.open, bar.high, bar.low, bar.close) == (100.0, 110.0, 100.0, 110.0)
        assert bar.volume == 3.0


# --- Config selection ------------------------------------------------------


class TestConfigSelection:
    def test_default_exchange_is_binance(self, monkeypatch):
        monkeypatch.delenv("EXCHANGE", raising=False)
        assert Config().exchange == "binance"

    def test_exchange_env_lowercased(self, monkeypatch):
        monkeypatch.setenv("EXCHANGE", "Coinbase")
        assert Config().exchange == "coinbase"

    def test_build_exchange_adapter_default_binance_uses_ws_url(self):
        cfg = Config()
        cfg.exchange = "binance"
        cfg.ws_url = "wss://custom-binance/ws"
        adapter = build_exchange_adapter(cfg)
        assert isinstance(adapter, BinanceAdapter)
        # honors WS_URL so the connection URL stays byte-identical
        assert adapter.ws_url(["btcusdt"]) == "wss://custom-binance/ws/btcusdt@trade"

    def test_build_exchange_adapter_coinbase(self):
        cfg = Config()
        cfg.exchange = "coinbase"
        assert isinstance(build_exchange_adapter(cfg), CoinbaseAdapter)

    def test_build_exchange_adapter_kraken(self):
        cfg = Config()
        cfg.exchange = "kraken"
        assert isinstance(build_exchange_adapter(cfg), KrakenAdapter)

    def test_build_exchange_adapter_bitstamp(self):
        cfg = Config()
        cfg.exchange = "bitstamp"
        assert isinstance(build_exchange_adapter(cfg), BitstampAdapter)

    def test_build_exchange_adapter_unknown_raises(self):
        cfg = Config()
        cfg.exchange = "bogus"
        with pytest.raises(ValueError, match="Unknown EXCHANGE"):
            build_exchange_adapter(cfg)

    def test_pipeline_wires_selected_adapter(self):
        cfg = Config()
        cfg.exchange = "coinbase"
        # Avoid touching real storage: coinbase still defaults storage to
        # timescale, but Pipeline.__init__ only constructs the backend object.
        p = Pipeline(cfg)
        assert isinstance(p.adapter, CoinbaseAdapter)
        assert p.normalizer.adapter is p.adapter
        assert p.client.adapter is p.adapter


# --- WebSocket client drives the adapter -----------------------------------


class TestClientDrivesAdapter:
    async def test_default_client_uses_binance_url(self, monkeypatch, fake_ws_factory):
        captured = {}

        def connect(url, **kwargs):
            captured["url"] = url
            client._running = False
            return fake_ws_factory(messages=[])

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)
        client = MarketDataClient("wss://base", max_retries=3)
        await client.connect(["btcusdt", "ethusdt"])
        assert captured["url"] == "wss://base/btcusdt@trade/ethusdt@trade"

    async def test_coinbase_client_uses_feed_url_and_sends_subscribe(
        self, monkeypatch, fake_ws_factory
    ):
        captured = {}
        ws = fake_ws_factory(messages=[])

        def connect(url, **kwargs):
            captured["url"] = url
            client._running = False
            return ws

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)
        client = MarketDataClient("wss://ignored", max_retries=3, adapter=CoinbaseAdapter())
        await client.connect(["btcusd"])
        assert captured["url"] == "wss://ws-feed.exchange.coinbase.com"
        # subscribe payload was sent as JSON after connect
        assert len(ws.sent) == 1
        sent = json.loads(ws.sent[0])
        assert sent["channels"][0]["product_ids"] == ["BTC-USD"]

    async def test_binance_client_sends_no_subscribe(self, monkeypatch, fake_ws_factory):
        ws = fake_ws_factory(messages=[])

        def connect(url, **kwargs):
            client._running = False
            return ws

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)
        client = MarketDataClient("wss://base", max_retries=3)
        await client.connect(["btcusdt"])
        assert ws.sent == []


# --- End-to-end Coinbase pipeline via FakeWebSocket ------------------------


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


class TestCoinbasePipelineEndToEnd:
    async def test_pipeline_normalizes_coinbase_messages_end_to_end(
        self, monkeypatch, fake_ws_factory
    ):
        cfg = Config()
        cfg.exchange = "coinbase"
        cfg.symbols = ["btcusd"]
        cfg.batch_size = 2
        p = Pipeline(cfg)
        p.cache = _FakeCache()
        p.storage = _FakeStorage()

        # Two canned Coinbase match messages + a subscription ack (ignored).
        messages = [
            json.dumps({"type": "subscriptions", "channels": []}),
            json.dumps(dict(COINBASE_MATCH, price="400.00", size="1.0", side="sell")),
            json.dumps(dict(COINBASE_MATCH, price="401.00", size="2.0", side="buy")),
        ]
        ws = fake_ws_factory(messages=messages)

        def connect(url, **kwargs):
            p.client._running = False  # one pass through the consume loop
            return ws

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)

        # Drive the WS client directly with the pipeline's _on_message callback
        # (no periodic-flush task, no real sockets) so the FakeWebSocket replays
        # canned messages through the full adapter -> normalizer -> cache/storage
        # path exactly as start() would, but without an infinite flush loop.
        p.client.on_message(p._on_message)
        await p.client.connect(cfg.symbols)

        # The two matches were cached, published, and batch-flushed (batch_size=2).
        assert p.cache.prices["btcusd"][0] == 401.00
        assert len(p.cache.pushed) == 2
        assert len(p.cache.published) == 2
        # batch_size=2 -> exactly one flush of 2 trades, sides taker-normalized
        assert len(p.storage.trades_inserted) == 1
        flushed = p.storage.trades_inserted[0]
        assert [t["side"] for t in flushed] == ["buy", "sell"]
        assert all(t["exchange"] == "coinbase" for t in flushed)
        # the subscribe payload was actually sent on the wire
        assert len(ws.sent) == 1


class TestKrakenPipelineEndToEnd:
    async def test_pipeline_normalizes_kraken_messages_end_to_end(
        self, monkeypatch, fake_ws_factory
    ):
        cfg = Config()
        cfg.exchange = "kraken"
        cfg.symbols = ["btcusd"]
        cfg.batch_size = 2
        p = Pipeline(cfg)
        p.cache = _FakeCache()
        p.storage = _FakeStorage()

        # A subscription-status object (ignored) + two canned Kraken trade arrays.
        messages = [
            json.dumps({"event": "subscriptionStatus", "status": "subscribed"}),
            json.dumps([0, [["400.00", "1.0", "1534614057.0", "b", "l", ""]], "trade", "XBT/USD"]),
            json.dumps([0, [["401.00", "2.0", "1534614058.0", "s", "m", ""]], "trade", "XBT/USD"]),
        ]
        ws = fake_ws_factory(messages=messages)

        def connect(url, **kwargs):
            p.client._running = False
            return ws

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)

        p.client.on_message(p._on_message)
        await p.client.connect(cfg.symbols)

        assert p.cache.prices["btcusd"][0] == 401.00
        assert len(p.cache.pushed) == 2
        assert len(p.cache.published) == 2
        assert len(p.storage.trades_inserted) == 1
        flushed = p.storage.trades_inserted[0]
        # Kraken side is already the aggressor side: b -> buy, s -> sell.
        assert [t["side"] for t in flushed] == ["buy", "sell"]
        assert all(t["exchange"] == "kraken" for t in flushed)
        # the subscribe payload was sent on the wire
        assert len(ws.sent) == 1
        sent = json.loads(ws.sent[0])
        assert sent["pair"] == ["XBT/USD"]
