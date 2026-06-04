"""Pluggable per-exchange adapters.

The pipeline is exchange-agnostic: everything that varies between venues is
captured behind the :class:`ExchangeAdapter` protocol so the WebSocket client,
normalizer, and pipeline never hard-code a single exchange. Three things differ
per exchange and nothing else does:

1. **The WebSocket URL** for a given list of symbols. Some venues (Binance)
   embed the stream names in the URL path; others (Coinbase) connect to one
   fixed endpoint and select streams via a subscribe message.
2. **The subscribe payload** sent (as JSON) right after the socket opens.
   Venues that select streams via the URL return ``None`` (nothing to send).
3. **Parsing one raw message into the SAME normalized** :class:`~src.normalizer.Trade`
   the pipeline already consumes — identical fields (lowercased ``symbol``,
   float ``price``/``quantity``, ``"buy"``/``"sell"`` ``side``, UTC-aware
   ``timestamp``, ``exchange`` tag). Messages that are not trades (heartbeats,
   subscription acks) or are malformed parse to ``None`` and are dropped.

Adding a venue is therefore one small class implementing this protocol — no
changes to the client, normalizer schema, or storage.

L2 order-book DEPTH is an OPT-IN, separate capability captured by the
:class:`DepthAdapter` protocol (mirroring :class:`ExchangeAdapter` but emitting
:class:`~src.normalizer.BookUpdate` snapshots). It lives ALONGSIDE the trades
adapter so the trades path is unchanged; an exchange that does not support depth
simply has no depth adapter and the pipeline's depth feed stays off.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .normalizer import BookLevel, BookUpdate, Trade

logger = logging.getLogger(__name__)


@runtime_checkable
class ConnectionAdapter(Protocol):
    """The URL + subscribe surface the WebSocket client needs.

    Both :class:`ExchangeAdapter` (trades) and :class:`DepthAdapter` (L2 depth)
    satisfy this, since the client only connects + subscribes — message PARSING
    happens in the pipeline callback, not the client. Typing the client against
    this narrower protocol lets it drive either feed without conflating their
    parse methods (``normalize_trade`` vs ``normalize_depth``).
    """

    name: str

    def ws_url(self, symbols: list[str]) -> str: ...

    def subscribe_payload(self, symbols: list[str]) -> dict | None: ...


@runtime_checkable
class ExchangeAdapter(Protocol):
    """What varies per exchange: WS URL, subscribe payload, message parsing."""

    #: Stable lowercase identifier, also stamped onto each normalized Trade.
    name: str

    def ws_url(self, symbols: list[str]) -> str:
        """Return the full WebSocket URL to connect to for ``symbols``.

        Binance embeds the per-symbol stream names in the path; Coinbase
        returns its single fixed feed endpoint (streams are selected by the
        subscribe payload instead).
        """
        ...

    def subscribe_payload(self, symbols: list[str]) -> dict | None:
        """Return the JSON message to send after connecting, or ``None``.

        ``None`` means the venue selects streams via the URL and needs no
        post-connect subscribe message (Binance).
        """
        ...

    def normalize_trade(self, raw: dict | list) -> Trade | None:
        """Parse one raw WS message into a normalized :class:`Trade`.

        Most venues send JSON objects (``dict``); some (Kraken v1) send JSON
        arrays (``list``), so the raw message may be either. Returns ``None``
        for non-trade messages (heartbeats, subscription confirmations) and for
        malformed payloads (logged, not raised).
        """
        ...


@runtime_checkable
class DepthAdapter(Protocol):
    """Opt-in L2 order-book DEPTH capability, parallel to :class:`ExchangeAdapter`.

    An exchange that supports a depth feed provides a class implementing this
    protocol; the pipeline then runs a SECOND websocket connection for depth
    alongside the trades feed. Venues without depth support simply have no
    depth adapter and the depth feed stays off — the trades path is untouched
    either way. The three things that vary per venue are the same as for trades
    (URL, subscribe payload, message parsing), but parsing yields a normalized
    :class:`~src.normalizer.BookUpdate` snapshot instead of a :class:`Trade`.
    """

    #: Stable lowercase identifier, also stamped onto each BookUpdate.
    name: str

    def ws_url(self, symbols: list[str]) -> str:
        """Return the full depth WebSocket URL to connect to for ``symbols``."""
        ...

    def subscribe_payload(self, symbols: list[str]) -> dict | None:
        """Return the JSON depth-subscribe message to send, or ``None``."""
        ...

    def normalize_depth(self, raw: dict | list) -> BookUpdate | None:
        """Parse one raw WS message into a normalized :class:`BookUpdate`.

        Returns ``None`` for non-depth messages (acks, heartbeats) and for
        malformed payloads (logged, not raised).
        """
        ...


class BinanceAdapter:
    """Binance combined ``@trade`` stream (keyless public market data).

    URL form ``wss://stream.binance.com:9443/ws/<sym>@trade/<sym>@trade``; the
    streams are embedded in the path, so no subscribe message is sent. Each raw
    trade message looks like::

        {"e": "trade", "s": "BTCUSDT", "p": "67500.50", "q": "0.15",
         "m": false, "T": 1712400000000, ...}

    where ``m`` is "buyer is the market maker" — i.e. the aggressor is the
    seller — so ``m=true`` normalizes to ``side="sell"`` (matches the original
    pipeline behavior byte-for-byte).
    """

    name = "binance"

    def __init__(self, ws_base_url: str = "wss://stream.binance.com:9443/ws") -> None:
        self._ws_base_url = ws_base_url.rstrip("/")

    def ws_url(self, symbols: list[str]) -> str:
        streams = "/".join(f"{s}@trade" for s in symbols)
        return f"{self._ws_base_url}/{streams}"

    def subscribe_payload(self, symbols: list[str]) -> dict | None:
        # Streams are embedded in the URL path; nothing to send.
        return None

    def normalize_trade(self, raw: dict | list) -> Trade | None:
        # Only act on trade events; ignore any other event type if present.
        if not isinstance(raw, dict):
            return None
        if "e" in raw and raw["e"] != "trade":
            return None
        try:
            return Trade(
                symbol=raw["s"].lower(),
                price=float(raw["p"]),
                quantity=float(raw["q"]),
                side="sell" if raw.get("m", False) else "buy",
                timestamp=datetime.fromtimestamp(raw["T"] / 1000, tz=UTC),
                exchange=self.name,
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to normalize binance trade: {e}")
            return None


class CoinbaseAdapter:
    """Coinbase Exchange ``matches`` channel (keyless public market data).

    Connects to the single fixed feed ``wss://ws-feed.exchange.coinbase.com``
    and selects products via a subscribe message::

        {"type": "subscribe",
         "channels": [{"name": "matches", "product_ids": ["BTC-USD", ...]}]}

    A trade arrives as a ``match`` (or the initial snapshot ``last_match``)::

        {"type": "match", "trade_id": 10, "sequence": 50,
         "maker_order_id": "...", "taker_order_id": "...",
         "time": "2014-11-07T08:19:27.028459Z", "product_id": "BTC-USD",
         "size": "5.23512", "price": "400.23", "side": "sell"}

    Symbol mapping: the pipeline uses lowercase no-dash symbols (``btcusd``)
    while Coinbase products are dashed upper-case (``BTC-USD``). The adapter
    dashes the configured symbol for the subscribe and strips the dash +
    lowercases ``product_id`` on the way back, so a configured ``btcusd``
    round-trips to the same normalized symbol as the rest of the pipeline.

    Side convention: Coinbase's ``side`` is the **maker** order side, whereas
    the pipeline (following Binance) reports the **aggressor/taker** side. The
    taker is the opposite of the maker, so a Coinbase ``side="sell"`` (maker
    sold, taker bought) normalizes to ``side="buy"`` — keeping ``side``
    consistent across exchanges as "who crossed the spread".
    """

    name = "coinbase"

    #: Message types that carry a trade.
    _TRADE_TYPES = frozenset({"match", "last_match"})

    def __init__(self, ws_base_url: str = "wss://ws-feed.exchange.coinbase.com") -> None:
        self._ws_base_url = ws_base_url

    def ws_url(self, symbols: list[str]) -> str:
        # Single fixed endpoint; products are chosen via subscribe_payload.
        return self._ws_base_url

    def subscribe_payload(self, symbols: list[str]) -> dict | None:
        return {
            "type": "subscribe",
            "channels": [
                {"name": "matches", "product_ids": [self._to_product_id(s) for s in symbols]}
            ],
        }

    def normalize_trade(self, raw: dict | list) -> Trade | None:
        if not isinstance(raw, dict):
            return None
        if raw.get("type") not in self._TRADE_TYPES:
            return None
        try:
            maker_side = raw["side"]
            # Aggressor = opposite of the maker side.
            taker_side = "buy" if maker_side == "sell" else "sell"
            return Trade(
                symbol=self._from_product_id(raw["product_id"]),
                price=float(raw["price"]),
                quantity=float(raw["size"]),
                side=taker_side,
                timestamp=self._parse_time(raw["time"]),
                exchange=self.name,
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to normalize coinbase trade: {e}")
            return None

    @staticmethod
    def _to_product_id(symbol: str) -> str:
        """``btcusd`` / ``btc-usd`` -> ``BTC-USD`` (leave already-dashed as-is)."""
        s = symbol.strip()
        if "-" in s:
            return s.upper()
        # No dash: assume a 3-char quote currency (USD/EUR/GBP/...).
        s = s.upper()
        if len(s) > 3:
            return f"{s[:-3]}-{s[-3:]}"
        return s

    @staticmethod
    def _from_product_id(product_id: str) -> str:
        """``BTC-USD`` -> ``btcusd`` (matches the rest of the pipeline's symbols)."""
        return product_id.replace("-", "").lower()

    @staticmethod
    def _parse_time(value: str) -> datetime:
        """Parse Coinbase's ISO-8601 (``...Z``) timestamp to a UTC-aware datetime."""
        # Python's fromisoformat handles the trailing 'Z' from 3.11+.
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt


class KrakenAdapter:
    """Kraken WebSocket **v1** public ``trade`` feed (keyless public market data).

    Connects to the single fixed endpoint ``wss://ws.kraken.com`` and selects
    pairs via a subscribe message::

        {"event": "subscribe",
         "subscription": {"name": "trade"},
         "pair": ["XBT/USD", "ETH/USD"]}

    A trade update arrives as a JSON **array** (not an object), so the WebSocket
    client's ``json.loads`` yields a list and ``normalize_trade`` receives it as
    the ``raw`` argument. The shape is::

        [
          channelID,                       # int (deprecated)
          [                                # array of trades in this update
            ["price", "volume", "time", "side", "orderType", "misc"],
            ...
          ],
          "trade",                         # channel name
          "XBT/USD"                        # pair
        ]

    where each inner trade is ``[price, volume, time(sec.fraction), side,
    orderType, misc]`` and ``side`` is ``"b"`` (buy) / ``"s"`` (sell). Kraken
    documents ``side`` as the **taker/aggressor** side — i.e. who crossed the
    spread — which matches the pipeline's Binance convention directly (NO flip
    needed, unlike Coinbase's maker side).

    Symbol mapping: the pipeline uses lowercase no-dash symbols (``btcusd``)
    while Kraken pairs are slash-separated with ``XBT`` for bitcoin
    (``XBT/USD``). The adapter maps ``btc``/``xbt`` <-> ``XBT`` on the way out
    and back, so a configured ``btcusd`` round-trips to the same normalized
    symbol the rest of the pipeline uses.

    A Kraken trade *update* can batch several fills; ``normalize_trade`` returns
    the **first** fill (the pipeline's one-message -> one-Trade contract). This
    is an explicit, documented simplification: low-volume demo pairs almost
    always send single-element batches, and the OHLCV roll-up is unaffected for
    a single representative trade. (Batches could be expanded to multiple Trades
    only by widening the adapter contract to return a list, which would touch
    the normalizer/client; out of scope for this additive pass.)
    """

    name = "kraken"

    def __init__(self, ws_base_url: str = "wss://ws.kraken.com") -> None:
        self._ws_base_url = ws_base_url

    def ws_url(self, symbols: list[str]) -> str:
        # Single fixed endpoint; pairs are chosen via subscribe_payload.
        return self._ws_base_url

    def subscribe_payload(self, symbols: list[str]) -> dict | None:
        return {
            "event": "subscribe",
            "subscription": {"name": "trade"},
            "pair": [self._to_pair(s) for s in symbols],
        }

    def normalize_trade(self, raw: dict | list) -> Trade | None:
        # Kraken trade updates are JSON arrays, not objects. Status/event
        # messages (subscription acks, heartbeats, system status) ARE objects
        # ({"event": ...}) -> ignored. A trade frame is
        # [channelID, [[...trade...], ...], "trade", "PAIR"].
        if not isinstance(raw, list) or len(raw) < 4:
            return None
        _channel_id, trades, channel_name, pair = raw[0], raw[1], raw[2], raw[3]
        if channel_name != "trade":
            return None
        if not isinstance(trades, list) or not trades:
            return None
        try:
            price_s, volume_s, time_s, side_code = trades[0][:4]
            return Trade(
                symbol=self._from_pair(pair),
                price=float(price_s),
                quantity=float(volume_s),
                # Kraken 'side' is already the taker/aggressor side: b->buy, s->sell.
                side="buy" if side_code == "b" else "sell",
                timestamp=datetime.fromtimestamp(float(time_s), tz=UTC),
                exchange=self.name,
            )
        except (KeyError, ValueError, IndexError, TypeError) as e:
            logger.warning(f"Failed to normalize kraken trade: {e}")
            return None

    @staticmethod
    def _to_pair(symbol: str) -> str:
        """``btcusd`` / ``xbtusd`` -> ``XBT/USD`` (Kraken uses XBT for bitcoin)."""
        s = symbol.strip().lower()
        if "/" in s:
            base, _, quote = s.partition("/")
        elif len(s) > 3:
            base, quote = s[:-3], s[-3:]
        else:
            base, quote = s, ""
        if base == "btc":
            base = "xbt"
        pair = base.upper()
        return f"{pair}/{quote.upper()}" if quote else pair

    @staticmethod
    def _from_pair(pair: str) -> str:
        """``XBT/USD`` -> ``btcusd`` (matches the rest of the pipeline's symbols)."""
        base, _, quote = pair.partition("/")
        base = base.lower()
        if base == "xbt":
            base = "btc"
        return f"{base}{quote.lower()}"


class BitstampAdapter:
    """Bitstamp WebSocket ``live_trades_<pair>`` channel (keyless public feed).

    Connects to the single fixed endpoint ``wss://ws.bitstamp.net`` and selects
    one channel per pair via a subscribe message (one per symbol, but the
    pipeline sends a single payload; Bitstamp accepts subscribing to multiple
    channels by sending multiple frames — here we send the first and document
    that multi-symbol Bitstamp needs one subscribe per channel). The subscribe
    shape is::

        {"event": "bts:subscribe",
         "data": {"channel": "live_trades_btcusd"}}

    A trade arrives as::

        {"event": "trade",
         "channel": "live_trades_btcusd",
         "data": {"id": 123, "timestamp": "1505558814",
                  "amount": 0.01513062, "amount_str": "0.01513062",
                  "price": 212.8, "price_str": "212.8",
                  "type": 0, "microtimestamp": "1505558814000000",
                  "buy_order_id": ..., "sell_order_id": ...}}

    where ``type`` is the trade direction: ``0`` = buy, ``1`` = sell. Bitstamp
    documents ``type`` as the side of the order that was executed against the
    book, i.e. the **taker/aggressor** side, matching the pipeline's Binance
    convention (NO flip). ASSUMPTION (documented): we treat ``type 0`` -> ``buy``
    and ``type 1`` -> ``sell`` as the aggressor side; this is the widely-used
    interpretation of the Bitstamp ``type`` field.

    Symbol mapping: the channel suffix is the lowercase no-dash pair
    (``btcusd``), which is already the pipeline's symbol form, so the
    ``channel`` -> symbol round-trip is the identity (strip the
    ``live_trades_`` prefix).

    Timestamp: ``microtimestamp`` (microseconds since epoch, as a string) is
    preferred for precision; falls back to ``timestamp`` (seconds) if absent.
    """

    name = "bitstamp"

    def __init__(self, ws_base_url: str = "wss://ws.bitstamp.net") -> None:
        self._ws_base_url = ws_base_url

    def ws_url(self, symbols: list[str]) -> str:
        # Single fixed endpoint; channels are chosen via subscribe_payload.
        return self._ws_base_url

    def subscribe_payload(self, symbols: list[str]) -> dict | None:
        # Bitstamp subscribes one channel per frame. The client sends a single
        # payload, so subscribe to the first symbol's channel; document that
        # additional Bitstamp symbols need one subscribe frame each.
        first = symbols[0] if symbols else ""
        return {
            "event": "bts:subscribe",
            "data": {"channel": self._to_channel(first)},
        }

    def normalize_trade(self, raw: dict | list) -> Trade | None:
        # Only "trade" events carry a fill. Subscription acks
        # ("bts:subscription_succeeded"), heartbeats, and reconnect requests
        # ("bts:request_reconnect") are ignored.
        if not isinstance(raw, dict) or raw.get("event") != "trade":
            return None
        try:
            data = raw["data"]
            channel = raw.get("channel", "")
            micro = data.get("microtimestamp")
            if micro is not None:
                ts = datetime.fromtimestamp(int(micro) / 1_000_000, tz=UTC)
            else:
                ts = datetime.fromtimestamp(int(data["timestamp"]), tz=UTC)
            return Trade(
                symbol=self._from_channel(channel),
                price=float(data["price"]),
                quantity=float(data["amount"]),
                # data["type"]: 0 = buy, 1 = sell (taker/aggressor side).
                side="buy" if int(data["type"]) == 0 else "sell",
                timestamp=ts,
                exchange=self.name,
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to normalize bitstamp trade: {e}")
            return None

    @staticmethod
    def _to_channel(symbol: str) -> str:
        """``btcusd`` / ``btc-usd`` -> ``live_trades_btcusd``."""
        s = symbol.strip().lower().replace("-", "").replace("/", "")
        return f"live_trades_{s}"

    @staticmethod
    def _from_channel(channel: str) -> str:
        """``live_trades_btcusd`` -> ``btcusd`` (already the pipeline form)."""
        prefix = "live_trades_"
        return channel[len(prefix) :] if channel.startswith(prefix) else channel.lower()


class BinanceDepthAdapter:
    """Binance partial book depth stream ``<sym>@depth<N>@100ms`` (keyless public).

    Chosen as the reference depth feed because it is the cleanest to consume:
    Binance pushes a SELF-CONTAINED top-N snapshot every 100 ms, so there is no
    diff/sequence-number bookkeeping or REST snapshot bootstrap to manage (unlike
    Binance's incremental ``@depth`` diff stream or Coinbase ``level2``). Each
    message already carries the full top-N book, which maps directly onto a
    :class:`~src.normalizer.BookUpdate`.

    URL form (streams embedded in the path, like the trades adapter, so no
    subscribe message is sent)::

        wss://stream.binance.com:9443/ws/<sym>@depth20@100ms/<sym>@depth20@100ms

    Each raw partial-depth message looks like::

        {"lastUpdateId": 160,
         "bids": [["0.0024", "10"], ["0.0023", "5"], ...],   # [price, qty] strings
         "asks": [["0.0026", "100"], ["0.0027", "20"], ...]}

    Binance delivers ``bids`` highest-price-first and ``asks`` lowest-price-first
    (best-first on both sides), which is exactly the :class:`BookUpdate`
    ordering, so no re-sorting is needed. The partial-depth payload carries no
    symbol field, so the configured single symbol is stamped on the snapshot;
    for multi-symbol fan-out the combined-stream wrapper (``{"stream":
    "btcusdt@depth20@100ms", "data": {...}}``) supplies the symbol from the
    ``stream`` name. The partial-depth payload also carries no event time, so we
    stamp it with ``datetime.now(UTC)`` at parse time (documented assumption:
    the 100 ms cadence makes receive-time a faithful proxy for book time).
    """

    name = "binance"

    def __init__(
        self,
        ws_base_url: str = "wss://stream.binance.com:9443/ws",
        *,
        levels: int = 20,
    ) -> None:
        self._ws_base_url = ws_base_url.rstrip("/")
        self._levels = levels
        # Symbol to stamp on the single-symbol /ws payload (no symbol field).
        self._symbol_hint: str | None = None

    def ws_url(self, symbols: list[str]) -> str:
        # When more than one symbol is requested, use Binance's combined-stream
        # endpoint so each message is wrapped with its originating stream name
        # (the partial-depth payload itself has no symbol field). A single symbol
        # keeps the raw /ws/<stream> form for the leanest possible connection.
        streams = [f"{s}@depth{self._levels}@100ms" for s in symbols]
        if len(streams) > 1:
            base = self._ws_base_url.rsplit("/ws", 1)[0]
            return f"{base}/stream?streams={'/'.join(streams)}"
        return f"{self._ws_base_url}/{streams[0]}"

    def subscribe_payload(self, symbols: list[str]) -> dict | None:
        # Streams are embedded in the URL path; nothing to send.
        return None

    def normalize_depth(self, raw: dict | list) -> BookUpdate | None:
        if not isinstance(raw, dict):
            return None
        # Combined-stream wrapper: {"stream": "btcusdt@depth20@100ms", "data": {...}}.
        symbol: str | None = None
        if "stream" in raw and "data" in raw:
            symbol = str(raw["stream"]).split("@", 1)[0].lower()
            raw = raw["data"]
            if not isinstance(raw, dict):
                return None
        if "bids" not in raw or "asks" not in raw:
            return None
        try:
            bids = [BookLevel(price=float(p), quantity=float(q)) for p, q in raw["bids"]]
            asks = [BookLevel(price=float(p), quantity=float(q)) for p, q in raw["asks"]]
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to normalize binance depth: {e}")
            return None
        return BookUpdate(
            symbol=symbol or self._symbol_hint or "",
            bids=bids,
            asks=asks,
            # Partial-depth payloads carry no event time; receive-time is a
            # faithful proxy at the 100 ms push cadence.
            timestamp=datetime.now(UTC),
            exchange=self.name,
        )

    def with_symbol_hint(self, symbol: str) -> BinanceDepthAdapter:
        """Return self after recording the symbol to stamp on un-wrapped payloads.

        The single-symbol partial-depth payload has no symbol field, so the
        pipeline tells the adapter which symbol the connection is for. The
        multi-symbol combined-stream wrapper supplies the symbol itself and
        ignores this hint.
        """
        self._symbol_hint = symbol.lower()
        return self


_ADAPTERS: dict[str, type] = {
    "binance": BinanceAdapter,
    "coinbase": CoinbaseAdapter,
    "kraken": KrakenAdapter,
    "bitstamp": BitstampAdapter,
}

#: Exchanges that ship an opt-in L2 depth adapter. Others have no depth feed.
_DEPTH_ADAPTERS: dict[str, type] = {
    "binance": BinanceDepthAdapter,
}


def build_adapter(exchange: str) -> ExchangeAdapter:
    """Construct the :class:`ExchangeAdapter` named by ``exchange`` (config).

    Defaults are wired in :class:`~src.config.Config` (``EXCHANGE`` env var,
    default ``"binance"`` so existing deployments are unchanged).
    """
    key = exchange.lower()
    cls = _ADAPTERS.get(key)
    if cls is None:
        known = ", ".join(sorted(_ADAPTERS))
        raise ValueError(f"Unknown EXCHANGE {exchange!r}; expected one of: {known}.")
    return cls()


def supports_depth(exchange: str) -> bool:
    """Whether ``exchange`` ships an opt-in L2 depth adapter."""
    return exchange.lower() in _DEPTH_ADAPTERS


def build_depth_adapter(exchange: str) -> DepthAdapter:
    """Construct the :class:`DepthAdapter` for ``exchange`` (opt-in depth feed).

    Raises :class:`ValueError` if the exchange has no depth adapter (callers
    should gate on :func:`supports_depth` first). Adding depth for another venue
    is one class implementing :class:`DepthAdapter` plus an entry here.
    """
    key = exchange.lower()
    cls = _DEPTH_ADAPTERS.get(key)
    if cls is None:
        known = ", ".join(sorted(_DEPTH_ADAPTERS)) or "(none)"
        raise ValueError(f"Exchange {exchange!r} has no L2 depth adapter; depth-capable: {known}.")
    return cls()
