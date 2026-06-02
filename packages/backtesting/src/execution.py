from abc import ABC, abstractmethod

from .data_handler import DataHandler
from .events import Direction, FillEvent, OrderEvent, OrderType


class ExecutionHandler(ABC):
    """Abstract base class for order execution."""

    @abstractmethod
    def execute_order(self, order: OrderEvent, data: DataHandler) -> FillEvent | None: ...

    def check_pending(self, data: DataHandler, timestamp) -> list[FillEvent]:
        """Fill any resting orders against the current bar. Default: none."""
        return []


class SimulatedExecution(ExecutionHandler):
    """Simulated execution with slippage, commission, and pending LIMIT/STOP orders.

    MARKET orders fill immediately at the next bar's open. LIMIT and STOP orders
    are queued and evaluated each bar via ``check_pending`` against the bar's
    intrabar high/low; orders sharing an ``oco_group`` are cancelled once a
    sibling fills (brackets / one-cancels-other).
    """

    def __init__(
        self,
        commission_pct: float = 0.001,
        slippage_pct: float = 0.0005,
    ):
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self._pending: list[OrderEvent] = []

    def execute_order(self, order: OrderEvent, data: DataHandler) -> FillEvent | None:
        """Fill a MARKET order at the next bar's open; queue LIMIT/STOP orders.

        Filling at the next open (not the signal bar's close) avoids the
        unrealistic same-bar fill: a signal computed from bar i's close cannot
        be executed at that same close. This matches backtesting.py / backtrader.
        """
        if order.order_type != OrderType.MARKET:
            self._pending.append(order)
            return None

        price = data.get_next_open(order.symbol)
        if price <= 0:
            return None
        fill_price = self._apply_slippage(price, order.direction)
        return self._fill(order, fill_price, base_price=price, timestamp=order.timestamp)

    def check_pending(self, data: DataHandler, timestamp) -> list[FillEvent]:
        """Evaluate queued LIMIT/STOP orders against the current bar; return fills.

        Called once per bar. Triggered orders fill; OCO siblings of a filled order
        are cancelled. Orders that don't trigger stay pending.
        """
        if not self._pending:
            return []

        fills: list[FillEvent] = []
        filled_groups: set[str] = set()
        still_pending: list[OrderEvent] = []

        for order in self._pending:
            if order.oco_group and order.oco_group in filled_groups:
                continue  # cancelled by a sibling that filled this bar
            bar = data.get_current_bar(order.symbol)
            if bar is None:
                still_pending.append(order)
                continue
            trigger = self._trigger_price(order, bar)
            if trigger is None:
                still_pending.append(order)
                continue
            # STOP becomes market-on-touch (slippage applies); LIMIT fills at its price.
            if order.order_type == OrderType.STOP:
                fill_price = self._apply_slippage(trigger, order.direction)
            else:
                fill_price = trigger
            fills.append(self._fill(order, fill_price, base_price=trigger, timestamp=timestamp))
            if order.oco_group:
                filled_groups.add(order.oco_group)

        self._pending = [
            o for o in still_pending if not (o.oco_group and o.oco_group in filled_groups)
        ]
        return fills

    # --- helpers ---

    def _apply_slippage(self, price: float, direction: Direction) -> float:
        if direction == Direction.BUY:
            return price * (1 + self.slippage_pct)
        return price * (1 - self.slippage_pct)

    def _trigger_price(self, order: OrderEvent, bar: dict) -> float | None:
        """Return the fill price if the order triggers on this bar, else None."""
        level = order.limit_price
        if level is None:
            return None
        o, high, low = bar["open"], bar["high"], bar["low"]

        if order.order_type == OrderType.LIMIT:
            if order.direction == Direction.BUY and low <= level:
                return min(o, level)  # gapped through -> fill at open (better)
            if order.direction == Direction.SELL and high >= level:
                return max(o, level)
        elif order.order_type == OrderType.STOP:
            if order.direction == Direction.BUY and high >= level:
                return max(o, level)
            if order.direction == Direction.SELL and low <= level:
                return min(o, level)
        return None

    def _fill(
        self, order: OrderEvent, fill_price: float, base_price: float, timestamp
    ) -> FillEvent:
        slippage_cost = abs(fill_price - base_price) * order.quantity
        commission = fill_price * order.quantity * self.commission_pct
        return FillEvent(
            timestamp=timestamp,
            symbol=order.symbol,
            quantity=order.quantity,
            price=fill_price,
            commission=commission,
            direction=order.direction,
            slippage=slippage_cost,
        )
