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
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .normalizer import Trade

logger = logging.getLogger(__name__)


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

    def normalize_trade(self, raw: dict) -> Trade | None:
        """Parse one raw WS message into a normalized :class:`Trade`.

        Returns ``None`` for non-trade messages (heartbeats, subscription
        confirmations) and for malformed payloads (logged, not raised).
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

    def normalize_trade(self, raw: dict) -> Trade | None:
        # Only act on trade events; ignore any other event type if present.
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

    def normalize_trade(self, raw: dict) -> Trade | None:
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


_ADAPTERS: dict[str, type] = {
    "binance": BinanceAdapter,
    "coinbase": CoinbaseAdapter,
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
