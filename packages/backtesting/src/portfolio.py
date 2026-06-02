from datetime import datetime

import pandas as pd

from .data_handler import DataHandler
from .events import Direction, FillEvent, OrderEvent, OrderType, SignalEvent
from .sizing import FixedFractionalSizer, Sizer, TargetWeightSizer


class Portfolio:
    """Tracks positions, cash, equity, and generates orders from signals.

    Position sizing is delegated to a pluggable ``Sizer`` (default:
    FixedFractionalSizer). Signals carrying a ``target_weight`` are always sized
    by a TargetWeightSizer regardless of the configured sizer, so rebalancing
    strategies work with any sizing policy.
    """

    def __init__(
        self,
        initial_capital: float = 100_000,
        position_size_pct: float = 0.1,
        sizer: Sizer | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        max_leverage: float | None = None,
        margin_rate: float = 0.0,
    ):
        self.initial_capital = initial_capital
        self.position_size_pct = position_size_pct
        self.sizer = sizer or FixedFractionalSizer(position_size_pct)
        self._target_weight_sizer = TargetWeightSizer()
        # Protective-exit thresholds (fractions, e.g. 0.1 = 10%). None disables.
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.trailing_stop_pct = trailing_stop_pct
        # Leverage: max gross exposure as a multiple of equity (None = no limit,
        # preserving prior unconstrained behavior). margin_rate is the annual
        # interest charged on borrowed cash (negative cash balance).
        self.max_leverage = max_leverage
        self.margin_rate = margin_rate
        self.cash = initial_capital
        self.positions: dict[str, int] = {}  # symbol -> quantity
        self._last_prices: dict[str, float] = {}
        self._entry_price: dict[str, float] = {}  # weighted-average cost per symbol
        self._high_water: dict[str, float] = {}  # peak price since entry (for trailing)
        self.equity_curve: list[dict] = []
        self.trade_log: list[dict] = []

    @property
    def total_equity(self) -> float:
        return self.cash + sum(
            qty * self._last_prices.get(sym, 0) for sym, qty in self.positions.items()
        )

    @property
    def gross_exposure(self) -> float:
        """Total absolute market value of open positions."""
        return sum(abs(qty) * self._last_prices.get(sym, 0) for sym, qty in self.positions.items())

    @property
    def buying_power(self) -> float:
        """How much additional notional can be bought. Infinite if no leverage cap."""
        if self.max_leverage is None:
            return float("inf")
        return max(0.0, self.max_leverage * self.total_equity - self.gross_exposure)

    def update_market(self, data: DataHandler, timestamp: datetime) -> None:
        """Mark positions to market, accrue margin interest, and record equity."""
        self._last_prices = {}
        for symbol in list(self.positions.keys()) + list(data.tickers):
            price = data.get_current_price(symbol)
            if price > 0:
                self._last_prices[symbol] = price

        # Charge daily interest on any borrowed cash (negative balance).
        if self.margin_rate and self.cash < 0:
            self.cash -= (-self.cash) * self.margin_rate / 252

        self.equity_curve.append(
            {
                "timestamp": timestamp,
                "equity": self.total_equity,
                "cash": self.cash,
            }
        )

    def process_signal(self, signal: SignalEvent, data: DataHandler) -> OrderEvent | None:
        """Convert a signal into an order via the configured Sizer."""
        sizer = self._target_weight_sizer if signal.target_weight is not None else self.sizer
        return sizer.size(signal, self, data)

    def process_fill(self, fill: FillEvent) -> None:
        """Update positions and cash from a fill."""
        current = self.positions.get(fill.symbol, 0)
        cost = fill.price * fill.quantity + fill.commission

        if fill.direction == Direction.BUY:
            new_qty = current + fill.quantity
            self.positions[fill.symbol] = new_qty
            self.cash -= cost
            # Track weighted-average entry and reset the trailing high-water mark.
            if current <= 0:
                self._entry_price[fill.symbol] = fill.price
                self._high_water[fill.symbol] = fill.price
            else:
                prev_entry = self._entry_price.get(fill.symbol, fill.price)
                self._entry_price[fill.symbol] = (
                    prev_entry * current + fill.price * fill.quantity
                ) / new_qty
        else:
            new_qty = current - fill.quantity
            self.positions[fill.symbol] = new_qty
            self.cash += fill.price * fill.quantity - fill.commission
            if new_qty <= 0:  # position closed -> drop protective tracking
                self._entry_price.pop(fill.symbol, None)
                self._high_water.pop(fill.symbol, None)

        self.trade_log.append(
            {
                "timestamp": fill.timestamp,
                "symbol": fill.symbol,
                "direction": fill.direction.value,
                "quantity": fill.quantity,
                "price": fill.price,
                "commission": fill.commission,
                "slippage": fill.slippage,
            }
        )

    def check_exits(self, data: DataHandler, timestamp) -> list[OrderEvent]:
        """Return market SELL orders for long positions that hit a protective exit.

        Evaluated on the current bar's close (the resulting orders fill at the next
        bar's open, like all orders). Triggers, in priority order: stop-loss and
        take-profit relative to the weighted-average entry, and a trailing stop
        relative to the peak price since entry.
        """
        if not (self.stop_loss_pct or self.take_profit_pct or self.trailing_stop_pct):
            return []

        orders: list[OrderEvent] = []
        for symbol, qty in list(self.positions.items()):
            if qty <= 0:
                continue
            entry = self._entry_price.get(symbol)
            if entry is None:
                continue
            price = data.get_current_price(symbol)
            if price <= 0:
                continue

            high_water = max(self._high_water.get(symbol, entry), price)
            self._high_water[symbol] = high_water

            triggered = (
                (self.stop_loss_pct and price <= entry * (1 - self.stop_loss_pct))
                or (self.take_profit_pct and price >= entry * (1 + self.take_profit_pct))
                or (self.trailing_stop_pct and price <= high_water * (1 - self.trailing_stop_pct))
            )
            if triggered:
                orders.append(
                    OrderEvent(
                        timestamp=timestamp,
                        symbol=symbol,
                        quantity=qty,
                        order_type=OrderType.MARKET,
                        direction=Direction.SELL,
                    )
                )
        return orders

    def get_equity_df(self) -> pd.DataFrame:
        """Return equity curve as a DataFrame."""
        return pd.DataFrame(self.equity_curve).set_index("timestamp")

    def get_trade_df(self) -> pd.DataFrame:
        """Return trade log as a DataFrame."""
        return pd.DataFrame(self.trade_log)
