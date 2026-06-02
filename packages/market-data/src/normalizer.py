from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters import ExchangeAdapter

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    symbol: str
    price: float
    quantity: float
    side: str  # "buy" or "sell"
    timestamp: datetime
    exchange: str = "binance"


@dataclass
class OHLCVBar:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime
    interval: str = "1m"
    trade_count: int = 0


class TickNormalizer:
    """Normalize raw exchange messages into standardized data models.

    Trade parsing is delegated to a pluggable :class:`~src.adapters.ExchangeAdapter`
    so the per-exchange message format lives in one place; the OHLCV roll-up
    below is exchange-agnostic and operates only on normalized :class:`Trade`s.
    Defaults to a :class:`~src.adapters.BinanceAdapter` so a no-arg
    ``TickNormalizer()`` parses Binance trades exactly as before.
    """

    def __init__(self, adapter: ExchangeAdapter | None = None) -> None:
        if adapter is None:
            # Imported here to avoid a module-level import cycle (adapters
            # imports Trade from this module).
            from .adapters import BinanceAdapter

            adapter = BinanceAdapter()
        self.adapter: ExchangeAdapter = adapter
        self._bar_accumulators: dict[str, list[Trade]] = defaultdict(list)

    def normalize_trade(self, raw: dict | list) -> Trade | None:
        """Normalize a raw exchange message into a Trade via the adapter."""
        return self.adapter.normalize_trade(raw)

    def accumulate_trade(self, trade: Trade) -> OHLCVBar | None:
        """Accumulate trades and emit a 1-minute OHLCV bar when the minute rolls over.

        A bar is emitted as soon as a trade for a LATER minute arrives, built from
        every accumulated trade in the (now-closed) earliest minute — even if that
        minute saw only a single trade. The trade that triggered the rollover is
        carried into the next bucket. The final in-progress minute is never closed
        by a later trade (there is none), so call :meth:`flush`/:meth:`flush_all`
        on shutdown to emit it.
        """
        key = trade.symbol
        bucket = self._bar_accumulators[key]
        bucket.append(trade)

        first_minute = bucket[0].timestamp.replace(second=0, microsecond=0)
        current_minute = trade.timestamp.replace(second=0, microsecond=0)

        if current_minute > first_minute:
            # Emit the completed earliest-minute bar (1+ trades), carry the rest.
            bar_trades = [
                t for t in bucket if t.timestamp.replace(second=0, microsecond=0) == first_minute
            ]
            remaining = [
                t for t in bucket if t.timestamp.replace(second=0, microsecond=0) != first_minute
            ]
            self._bar_accumulators[key] = remaining

            if bar_trades:
                return self._build_bar(trade.symbol, bar_trades, first_minute)

        return None

    def flush(self, symbol: str) -> OHLCVBar | None:
        """Emit the final in-progress bar for ``symbol`` and clear its accumulator.

        Used at shutdown / end-of-stream so the last minute's trades (which no
        later trade will ever close) are not silently dropped. Returns ``None``
        when nothing is buffered for the symbol.
        """
        bucket = self._bar_accumulators.get(symbol)
        if not bucket:
            return None
        first_minute = bucket[0].timestamp.replace(second=0, microsecond=0)
        self._bar_accumulators[symbol] = []
        return self._build_bar(symbol, bucket, first_minute)

    def flush_all(self) -> list[OHLCVBar]:
        """Emit the final in-progress bar for every symbol with buffered trades."""
        bars: list[OHLCVBar] = []
        for symbol in list(self._bar_accumulators):
            bar = self.flush(symbol)
            if bar is not None:
                bars.append(bar)
        return bars

    def _build_bar(self, symbol: str, trades: list[Trade], timestamp: datetime) -> OHLCVBar:
        prices = [t.price for t in trades]
        return OHLCVBar(
            symbol=symbol,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=sum(t.quantity for t in trades),
            timestamp=timestamp,
            trade_count=len(trades),
        )
