from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Direction(Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


@dataclass
class Event:
    timestamp: datetime


@dataclass
class MarketEvent(Event):
    """New bar data is available."""

    symbol: str = ""


@dataclass
class SignalEvent(Event):
    """Strategy generates a trading signal.

    ``target_weight`` (when set) requests a specific portfolio weight for the
    symbol; the portfolio sizes the order to move toward it (used by rebalancing
    strategies). When ``target_weight`` is None, ``direction``/``strength`` drive
    fixed-fractional sizing.
    """

    symbol: str = ""
    direction: Direction = Direction.HOLD
    strength: float = 1.0
    target_weight: float | None = None
    limit_price: float | None = None  # request a LIMIT entry instead of MARKET


@dataclass
class OrderEvent(Event):
    """Order to be submitted for execution.

    ``limit_price`` is the trigger price for LIMIT and STOP orders. Orders sharing
    an ``oco_group`` are one-cancels-other: when one fills, the others are
    cancelled (used to build brackets, e.g. take-profit LIMIT + stop-loss STOP).
    """

    symbol: str = ""
    quantity: int = 0
    order_type: OrderType = OrderType.MARKET
    direction: Direction = Direction.BUY
    limit_price: float | None = None
    oco_group: str | None = None


@dataclass
class FillEvent(Event):
    """Executed trade confirmation."""

    symbol: str = ""
    quantity: int = 0
    price: float = 0.0
    commission: float = 0.0
    direction: Direction = Direction.BUY
    slippage: float = 0.0
