import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

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
    """Normalize raw exchange messages into standardized data models."""

    def __init__(self) -> None:
        self._bar_accumulators: dict[str, list[Trade]] = defaultdict(list)

    def normalize_trade(self, raw: dict) -> Trade | None:
        """Normalize a Binance trade message into a Trade dataclass."""
        try:
            return Trade(
                symbol=raw["s"].lower(),
                price=float(raw["p"]),
                quantity=float(raw["q"]),
                side="sell" if raw.get("m", False) else "buy",
                timestamp=datetime.fromtimestamp(raw["T"] / 1000, tz=UTC),
                exchange="binance",
            )
        except (KeyError, ValueError) as e:
            logger.warning(f"Failed to normalize trade: {e}")
            return None

    def accumulate_trade(self, trade: Trade) -> OHLCVBar | None:
        """Accumulate trades and emit a 1-minute OHLCV bar when the minute rolls over."""
        key = trade.symbol
        bucket = self._bar_accumulators[key]
        bucket.append(trade)

        if len(bucket) < 2:
            return None

        first_minute = bucket[0].timestamp.replace(second=0, microsecond=0)
        current_minute = trade.timestamp.replace(second=0, microsecond=0)

        if current_minute > first_minute:
            # Emit completed bar from accumulated trades (excluding current)
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
