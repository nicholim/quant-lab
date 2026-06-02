"""Pluggable position sizing.

A Sizer turns a SignalEvent into an OrderEvent (or None). The Portfolio delegates
all sizing to a configured Sizer, so sizing policy is swappable without touching
the event loop. Long-only is preserved: BUY only opens/holds, SELL only reduces
an existing long position.
"""

from abc import ABC, abstractmethod

from .data_handler import DataHandler
from .events import Direction, OrderEvent, OrderType


class Sizer(ABC):
    """Abstract position sizer: signal -> order (or None)."""

    @abstractmethod
    def size(self, signal, portfolio, data: DataHandler) -> OrderEvent | None: ...

    @staticmethod
    def _cap_to_buying_power(portfolio, price: float, quantity: int) -> int:
        """Clamp a buy quantity to the portfolio's buying power (leverage limit)."""
        bp = getattr(portfolio, "buying_power", float("inf"))
        if bp == float("inf") or price <= 0:
            return quantity
        return max(0, min(quantity, int(bp / price)))

    @staticmethod
    def _long_only_order(signal, portfolio, quantity: int, price: float) -> OrderEvent | None:
        """BUY when flat, SELL only to reduce an existing long. None otherwise.

        Emits a LIMIT order when the signal carries a ``limit_price``, else MARKET.
        Buy quantities are capped to available buying power.
        """
        if quantity <= 0:
            return None
        order_type = OrderType.LIMIT if signal.limit_price is not None else OrderType.MARKET
        current_pos = portfolio.positions.get(signal.symbol, 0)
        if signal.direction == Direction.BUY and current_pos <= 0:
            quantity = Sizer._cap_to_buying_power(portfolio, price, quantity)
            if quantity <= 0:
                return None
            return OrderEvent(
                timestamp=signal.timestamp,
                symbol=signal.symbol,
                quantity=quantity,
                order_type=order_type,
                direction=Direction.BUY,
                limit_price=signal.limit_price,
            )
        if signal.direction == Direction.SELL and current_pos > 0:
            return OrderEvent(
                timestamp=signal.timestamp,
                symbol=signal.symbol,
                quantity=min(current_pos, quantity),
                order_type=order_type,
                direction=Direction.SELL,
                limit_price=signal.limit_price,
            )
        return None


class FixedFractionalSizer(Sizer):
    """Allocate ``position_size_pct`` of equity, scaled by signal strength."""

    def __init__(self, position_size_pct: float = 0.1):
        self.position_size_pct = position_size_pct

    def size(self, signal, portfolio, data: DataHandler) -> OrderEvent | None:
        price = data.get_current_price(signal.symbol)
        if price <= 0:
            return None
        value = portfolio.total_equity * self.position_size_pct * signal.strength
        return self._long_only_order(signal, portfolio, int(value / price), price)


class PercentOfEquitySizer(Sizer):
    """Allocate a fixed percent of equity, ignoring signal strength."""

    def __init__(self, pct: float = 0.1):
        self.pct = pct

    def size(self, signal, portfolio, data: DataHandler) -> OrderEvent | None:
        price = data.get_current_price(signal.symbol)
        if price <= 0:
            return None
        value = portfolio.total_equity * self.pct
        return self._long_only_order(signal, portfolio, int(value / price), price)


class RiskBasedSizer(Sizer):
    """Volatility-targeted sizing.

    Sizes the position so its expected daily P&L volatility is about
    ``risk_per_trade`` of equity, using the trailing realized volatility.
    """

    def __init__(self, risk_per_trade: float = 0.02, lookback: int = 20):
        self.risk_per_trade = risk_per_trade
        self.lookback = lookback

    def size(self, signal, portfolio, data: DataHandler) -> OrderEvent | None:
        price = data.get_current_price(signal.symbol)
        if price <= 0:
            return None
        bars = data.get_latest_bars(signal.symbol, self.lookback + 1)
        if len(bars) < self.lookback:
            return None
        vol = bars["Close"].pct_change().dropna().std()
        if not vol or vol <= 0:
            return None
        # notional * daily_vol == risk_per_trade * equity
        target_notional = (self.risk_per_trade * portfolio.total_equity) / vol
        return self._long_only_order(signal, portfolio, int(target_notional / price), price)


class TargetWeightSizer(Sizer):
    """Move the position toward ``signal.target_weight`` of equity (rebalancing).

    Used automatically for signals that carry a target_weight. Long-only as long
    as target_weight >= 0.
    """

    def size(self, signal, portfolio, data: DataHandler) -> OrderEvent | None:
        price = data.get_current_price(signal.symbol)
        if price <= 0:
            return None
        desired_qty = int((portfolio.total_equity * signal.target_weight) / price)
        current_pos = portfolio.positions.get(signal.symbol, 0)
        delta = desired_qty - current_pos
        if delta == 0:
            return None
        if delta > 0:  # buying more -> respect buying power
            delta = self._cap_to_buying_power(portfolio, price, delta)
            if delta <= 0:
                return None
        return OrderEvent(
            timestamp=signal.timestamp,
            symbol=signal.symbol,
            quantity=abs(delta),
            order_type=OrderType.MARKET,
            direction=Direction.BUY if delta > 0 else Direction.SELL,
        )
