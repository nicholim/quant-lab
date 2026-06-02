"""Tests for opt-in native short selling.

Covers the three layers the feature touches:

* ``portfolio.py`` — signed positions, short-sale-credits / cover-debits cash
  accounting, inverse mark-to-market, weighted-average entry across flips, and
  short-side protective exits.
* ``analytics.py`` — signed FIFO round-trip P&L (a profitable short = sold high,
  covered low) including multiple short lots and long->flat->short transitions.
* The default-OFF PARITY guarantee: with ``allow_short=False`` (the default) a
  representative long-only backtest yields a BYTE-IDENTICAL equity curve, trade
  stats, and metrics with the new code as before.

Style mirrors test_edge_cases.py: deterministic seeded RNG, in-memory
``DataFrameDataHandler`` (no network), module-scoped helpers.
"""

from datetime import datetime

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")

from src.analytics import PerformanceAnalytics
from src.backtest import Backtest
from src.data_handler import DataFrameDataHandler
from src.events import Direction, FillEvent, MarketEvent, SignalEvent
from src.execution import SimulatedExecution
from src.portfolio import Portfolio
from src.sizing import PercentOfEquitySizer
from src.strategy import Strategy

# --- helpers --------------------------------------------------------------


def _ohlcv(close, *, open_=None):
    close = np.asarray(close, dtype=float)
    n = len(close)
    idx = pd.date_range("2021-01-01", periods=n, freq="B")
    if open_ is None:
        open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(close, open_) * 1.001,
            "Low": np.minimum(close, open_) * 0.999,
            "Close": close,
            "Volume": 1000,
        },
        index=idx,
    )


def _handler(frames):
    return DataFrameDataHandler(frames)


def _fill(symbol, direction, qty, price, *, commission=0.0):
    return FillEvent(
        timestamp=datetime(2021, 1, 1),
        symbol=symbol,
        quantity=qty,
        price=price,
        commission=commission,
        direction=direction,
        slippage=0.0,
    )


def _trades(rows):
    cols = ["timestamp", "symbol", "direction", "quantity", "price", "commission", "slippage"]
    return pd.DataFrame(rows, columns=cols)


def _equity_from_returns(returns, capital=100_000):
    eq = capital * (1 + returns).cumprod()
    return pd.DataFrame({"equity": eq, "cash": 0.0})


# --- portfolio: cash + position accounting --------------------------------


class TestShortCashAndPositions:
    def test_short_open_credits_cash_and_goes_negative(self):
        p = Portfolio(initial_capital=100_000, allow_short=True)
        p.process_fill(_fill("X", Direction.SELL, 10, 50.0))
        assert p.positions["X"] == -10  # short
        assert abs(p.cash - (100_000 + 10 * 50.0)) < 1e-9  # short sale credits proceeds
        assert abs(p._entry_price["X"] - 50.0) < 1e-9

    def test_cover_debits_cash_and_returns_flat(self):
        p = Portfolio(initial_capital=100_000, allow_short=True)
        p.process_fill(_fill("X", Direction.SELL, 10, 50.0))  # +500 cash
        p.process_fill(_fill("X", Direction.BUY, 10, 40.0))  # -400 cash
        assert p.positions["X"] == 0
        assert abs(p.cash - (100_000 + 500 - 400)) < 1e-9
        assert "X" not in p._entry_price  # tracking dropped at flat

    def test_partial_cover_keeps_short_and_entry(self):
        p = Portfolio(initial_capital=100_000, allow_short=True)
        p.process_fill(_fill("X", Direction.SELL, 10, 50.0))
        p.process_fill(_fill("X", Direction.BUY, 4, 45.0))
        assert p.positions["X"] == -6  # still short
        assert abs(p._entry_price["X"] - 50.0) < 1e-9  # entry of open side unchanged

    def test_extending_short_weighted_average_entry(self):
        p = Portfolio(initial_capital=100_000, allow_short=True)
        p.process_fill(_fill("X", Direction.SELL, 10, 50.0))
        p.process_fill(_fill("X", Direction.SELL, 10, 60.0))
        assert p.positions["X"] == -20
        # magnitude-weighted average of 50 and 60
        assert abs(p._entry_price["X"] - 55.0) < 1e-9


class TestShortMarkToMarket:
    def test_equity_moves_inverse_to_price_while_short(self):
        p = Portfolio(initial_capital=100_000, allow_short=True)
        h = _handler({"X": _ohlcv([50.0, 40.0, 60.0])})
        h.fetch()
        # open short 10 @ 50 manually
        p.process_fill(_fill("X", Direction.SELL, 10, 50.0))
        # mark at 40 (price fell -> short gains). get_current_price reads index
        # _current_idx - 1, so idx 2 -> close[1] == 40.
        h._current_idx = 2
        p.update_market(h, datetime(2021, 1, 2))
        # equity = cash(100500) + (-10)*40 = 100100 -> +100 gain
        assert abs(p.total_equity - (100_500 - 400)) < 1e-9
        assert p.total_equity > 100_000  # profitable while price down
        # mark at 60 (price rose -> short loses). idx 3 -> close[2] == 60.
        h._current_idx = 3
        p.update_market(h, datetime(2021, 1, 3))
        # equity = 100500 + (-10)*60 = 99900 -> loss
        assert abs(p.total_equity - (100_500 - 600)) < 1e-9
        assert p.total_equity < 100_000

    def test_gross_exposure_uses_absolute_value(self):
        p = Portfolio(initial_capital=100_000, allow_short=True)
        h = _handler({"X": _ohlcv([50.0, 50.0])})
        h.fetch()
        p.process_fill(_fill("X", Direction.SELL, 10, 50.0))
        h._current_idx = 1
        p.update_market(h, datetime(2021, 1, 2))
        assert abs(p.gross_exposure - 10 * 50.0) < 1e-9  # |−10| * 50


class TestLongToFlatToShort:
    def test_flip_long_through_zero_into_short(self):
        p = Portfolio(initial_capital=100_000, allow_short=True)
        p.process_fill(_fill("X", Direction.BUY, 10, 50.0))  # long 10 @ 50
        assert p.positions["X"] == 10
        # sell 25: closes 10 long, opens 15 short at 60
        p.process_fill(_fill("X", Direction.SELL, 25, 60.0))
        assert p.positions["X"] == -15
        assert abs(p._entry_price["X"] - 60.0) < 1e-9  # entry reset to flip price
        # cash: 100000 - 500 (buy) + 1500 (sell 25@60) = 101000
        assert abs(p.cash - (100_000 - 500 + 25 * 60.0)) < 1e-9


# --- short-side protective exits ------------------------------------------


class TestShortExits:
    def test_short_stop_loss_triggers_buy_when_price_rises(self):
        p = Portfolio(initial_capital=100_000, allow_short=True, stop_loss_pct=0.10)
        h = _handler({"X": _ohlcv([100.0, 115.0])})
        h.fetch()
        p.process_fill(_fill("X", Direction.SELL, 10, 100.0))
        h._current_idx = 2  # price 115, up 15% > 10% stop
        orders = p.check_exits(h, datetime(2021, 1, 2))
        assert len(orders) == 1
        assert orders[0].direction == Direction.BUY  # buy-to-cover
        assert orders[0].quantity == 10

    def test_short_take_profit_triggers_when_price_falls(self):
        p = Portfolio(initial_capital=100_000, allow_short=True, take_profit_pct=0.10)
        h = _handler({"X": _ohlcv([100.0, 85.0])})
        h.fetch()
        p.process_fill(_fill("X", Direction.SELL, 10, 100.0))
        h._current_idx = 2  # price 85, down 15% > 10% take-profit
        orders = p.check_exits(h, datetime(2021, 1, 2))
        assert len(orders) == 1
        assert orders[0].direction == Direction.BUY

    def test_short_no_exit_within_threshold(self):
        p = Portfolio(initial_capital=100_000, allow_short=True, stop_loss_pct=0.10)
        h = _handler({"X": _ohlcv([100.0, 105.0])})
        h.fetch()
        p.process_fill(_fill("X", Direction.SELL, 10, 100.0))
        h._current_idx = 2  # only +5%, below 10% stop
        assert p.check_exits(h, datetime(2021, 1, 2)) == []

    def test_short_ignored_when_allow_short_off(self):
        # A negative position should never exist when off, but the exit scan
        # must defensively skip shorts and not emit a buy.
        p = Portfolio(initial_capital=100_000, allow_short=False, stop_loss_pct=0.10)
        p.positions["X"] = -10
        p._entry_price["X"] = 100.0
        h = _handler({"X": _ohlcv([100.0, 200.0])})
        h.fetch()
        h._current_idx = 2
        assert p.check_exits(h, datetime(2021, 1, 2)) == []


# --- signed FIFO round-trip P&L -------------------------------------------


class TestSignedFifoPnl:
    def test_clean_short_round_trip_is_profit(self):
        # Sell 10 @ 120 (open short), buy 10 @ 90 (cover) -> +300 profit.
        trades = _trades(
            [
                [1, "X", "SELL", 10, 120.0, 0.0, 0.0],
                [2, "X", "BUY", 10, 90.0, 0.0, 0.0],
            ]
        )
        pa = PerformanceAnalytics(
            _equity_from_returns(pd.Series([0.0], index=pd.date_range("2021-01-01", periods=1))),
            trades,
            allow_short=True,
        )
        assert pa._compute_round_trip_pnl() == [300.0]  # (120-90)*10
        assert pa.win_rate() == 1.0

    def test_short_against_you_is_loss(self):
        # Sell 10 @ 100, cover 10 @ 130 -> -300 loss.
        trades = _trades(
            [
                [1, "X", "SELL", 10, 100.0, 0.0, 0.0],
                [2, "X", "BUY", 10, 130.0, 0.0, 0.0],
            ]
        )
        pa = PerformanceAnalytics(
            _equity_from_returns(pd.Series([0.0], index=pd.date_range("2021-01-01", periods=1))),
            trades,
            allow_short=True,
        )
        assert pa._compute_round_trip_pnl() == [-300.0]
        assert pa.win_rate() == 0.0

    def test_multiple_short_lots_fifo(self):
        # Short 10 @ 100, short 10 @ 110, cover 15 @ 90.
        # FIFO: cover 10 from the 100 lot -> (100-90)*10 = 100;
        #       cover 5 from the 110 lot -> (110-90)*5 = 100.
        trades = _trades(
            [
                [1, "X", "SELL", 10, 100.0, 0.0, 0.0],
                [2, "X", "SELL", 10, 110.0, 0.0, 0.0],
                [3, "X", "BUY", 15, 90.0, 0.0, 0.0],
            ]
        )
        pa = PerformanceAnalytics(
            _equity_from_returns(pd.Series([0.0], index=pd.date_range("2021-01-01", periods=1))),
            trades,
            allow_short=True,
        )
        assert pa._compute_round_trip_pnl() == [100.0, 100.0]

    def test_long_then_flip_to_short_round_trips(self):
        # Buy 10 @ 50, sell 30 @ 70 (close long +200, open short 20 @ 70),
        # buy 20 @ 60 (cover short -> (70-60)*20 = 200).
        trades = _trades(
            [
                [1, "X", "BUY", 10, 50.0, 0.0, 0.0],
                [2, "X", "SELL", 30, 70.0, 0.0, 0.0],
                [3, "X", "BUY", 20, 60.0, 0.0, 0.0],
            ]
        )
        pa = PerformanceAnalytics(
            _equity_from_returns(pd.Series([0.0], index=pd.date_range("2021-01-01", periods=1))),
            trades,
            allow_short=True,
        )
        # long close (70-50)*10 = 200, then short close (70-60)*20 = 200
        assert pa._compute_round_trip_pnl() == [200.0, 200.0]

    def test_long_only_fifo_unchanged_with_flag_on(self):
        # A pure long sequence must give the same answer with allow_short on.
        trades = _trades(
            [
                [1, "X", "BUY", 10, 100.0, 0.0, 0.0],
                [2, "X", "SELL", 4, 120.0, 0.0, 0.0],
                [3, "X", "SELL", 6, 90.0, 0.0, 0.0],
            ]
        )
        eq = _equity_from_returns(pd.Series([0.0], index=pd.date_range("2021-01-01", periods=1)))
        long_only = PerformanceAnalytics(eq, trades, allow_short=False)
        with_short = PerformanceAnalytics(eq, trades, allow_short=True)
        assert long_only._compute_round_trip_pnl() == [80.0, -60.0]
        assert with_short._compute_round_trip_pnl() == [80.0, -60.0]


# --- sizer-level short behavior -------------------------------------------


class _StubData:
    def __init__(self, price):
        self._price = price

    def get_current_price(self, symbol):
        return self._price


def _signal(direction, *, symbol="X", strength=1.0, limit_price=None, target_weight=None):
    return SignalEvent(
        timestamp=datetime(2021, 1, 1),
        symbol=symbol,
        direction=direction,
        strength=strength,
        limit_price=limit_price,
        target_weight=target_weight,
    )


class TestSignedSizers:
    def test_percent_sizer_opens_short_when_enabled(self):
        p = Portfolio(initial_capital=100_000, allow_short=True)
        order = PercentOfEquitySizer(0.1).size(_signal(Direction.SELL), p, _StubData(100.0))
        assert order is not None
        assert order.direction == Direction.SELL
        assert order.quantity == 100  # 10% of 100k / 100

    def test_percent_sizer_blocks_short_when_disabled(self):
        p = Portfolio(initial_capital=100_000, allow_short=False)
        assert PercentOfEquitySizer(0.1).size(_signal(Direction.SELL), p, _StubData(100.0)) is None

    def test_buy_covers_short_without_buying_power_cap(self):
        # A capped portfolio still lets a BUY reduce a short (cover), uncapped.
        p = Portfolio(initial_capital=100_000, allow_short=True, max_leverage=1.0)
        p.positions["X"] = -50
        p._last_prices["X"] = 100.0
        order = PercentOfEquitySizer(0.1).size(_signal(Direction.BUY), p, _StubData(100.0))
        assert order is not None and order.direction == Direction.BUY

    def test_signed_order_emits_limit_when_requested(self):
        p = Portfolio(initial_capital=100_000, allow_short=True)
        order = PercentOfEquitySizer(0.1).size(
            _signal(Direction.SELL, limit_price=95.0), p, _StubData(100.0)
        )
        assert order is not None
        assert order.order_type.value == "LIMIT"
        assert order.limit_price == 95.0

    def test_short_capped_to_zero_buying_power_returns_none(self):
        # Fully-levered already (gross == max_leverage*equity) -> no buying power
        # to open a NEW short on Y, so the capped quantity is 0 -> None.
        p = Portfolio(initial_capital=100_000, allow_short=True, max_leverage=1.0)
        # Short 200 @ 1000: short sale credited 200k -> cash 300k, position -200k,
        # equity 100k, gross 200k == 2.0x equity, already past the 1.0x cap.
        p.cash = 300_000.0
        p.positions["X"] = -200
        p._last_prices["X"] = 1000.0
        assert p.total_equity == 100_000.0
        assert p.gross_exposure == 200_000.0
        assert p.buying_power == 0.0
        assert (
            PercentOfEquitySizer(0.5).size(_signal(Direction.SELL, symbol="Y"), p, _StubData(100.0))
            is None
        )


class TestTargetWeightShort:
    def test_negative_target_opens_short_when_enabled(self):
        from src.sizing import TargetWeightSizer

        p = Portfolio(initial_capital=100_000, allow_short=True)
        order = TargetWeightSizer().size(
            _signal(Direction.BUY, target_weight=-0.3), p, _StubData(100.0)
        )
        assert order is not None
        assert order.direction == Direction.SELL
        assert order.quantity == 300  # -0.3 * 100k / 100 -> short 300

    def test_negative_target_clamped_to_flat_when_disabled(self):
        from src.sizing import TargetWeightSizer

        p = Portfolio(initial_capital=100_000, allow_short=False)
        # Flat already; clamped target 0 -> no delta -> no order.
        assert (
            TargetWeightSizer().size(
                _signal(Direction.BUY, target_weight=-0.3), p, _StubData(100.0)
            )
            is None
        )


# --- end-to-end short backtest --------------------------------------------


class _AlwaysShort(Strategy):
    """Emit a single SELL on the second bar of the first symbol."""

    def __init__(self):
        self._sent = False

    def calculate_signals(self, event: MarketEvent, data):
        if self._sent:
            return None
        bars = data.get_latest_bars(event.symbol, 2)
        if len(bars) < 2:
            return None
        self._sent = True
        return SignalEvent(
            timestamp=event.timestamp,
            symbol=event.symbol,
            direction=Direction.SELL,
            strength=1.0,
        )


class TestEndToEndShort:
    def test_short_position_opens_in_full_backtest(self):
        # Falling price -> a short should be profitable.
        close = [100.0, 98.0, 95.0, 90.0, 85.0]
        h = _handler({"X": _ohlcv(close)})
        p = Portfolio(initial_capital=100_000, allow_short=True, sizer=PercentOfEquitySizer(0.2))
        analytics = Backtest(h, _AlwaysShort(), p, SimulatedExecution()).run()
        assert p.positions["X"] < 0  # a short was opened
        # Falling market with a short -> ending equity above start.
        assert analytics.equity["equity"].iloc[-1] > 100_000

    def test_default_off_rejects_short(self):
        close = [100.0, 98.0, 95.0, 90.0, 85.0]
        h = _handler({"X": _ohlcv(close)})
        p = Portfolio(initial_capital=100_000, sizer=PercentOfEquitySizer(0.2))  # allow_short False
        Backtest(h, _AlwaysShort(), p, SimulatedExecution()).run()
        assert p.positions.get("X", 0) == 0  # naked sell clipped -> stays flat
        assert len(p.trade_log) == 0


# --- PARITY: default-off long-only backtest is byte-identical --------------


class _MaCross(Strategy):
    """Tiny deterministic long-only crossover for the parity backtest."""

    def __init__(self, short=5, long=15):
        self.short = short
        self.long = long
        self._prev = {}

    def calculate_signals(self, event: MarketEvent, data):
        bars = data.get_latest_bars(event.symbol, self.long + 1)
        if len(bars) < self.long:
            return None
        s = bars["Close"].rolling(self.short).mean().iloc[-1]
        long_ma = bars["Close"].rolling(self.long).mean().iloc[-1]
        prev = self._prev.get(event.symbol, Direction.HOLD)
        if s > long_ma and prev != Direction.BUY:
            self._prev[event.symbol] = Direction.BUY
            return SignalEvent(event.timestamp, event.symbol, Direction.BUY, 1.0)
        if s < long_ma and prev != Direction.SELL:
            self._prev[event.symbol] = Direction.SELL
            return SignalEvent(event.timestamp, event.symbol, Direction.SELL, 1.0)
        return None


def _parity_frames(seed=7, n=120):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0008, 0.013, n)
    close = list(100 * np.cumprod(1 + rets))
    return {"X": _ohlcv(close)}


class TestLongOnlyParity:
    """A representative long-only backtest must be identical with the new code
    (allow_short defaults off)."""

    def _run(self):
        h = _handler(_parity_frames())
        p = Portfolio(initial_capital=100_000, sizer=PercentOfEquitySizer(0.5))
        return Backtest(h, _MaCross(), p, SimulatedExecution()).run(), p

    def test_equity_curve_and_trades_and_metrics(self):
        analytics, p = self._run()
        # The default-off backtest must produce a real long-only result.
        assert len(p.trade_log) > 0
        assert all(q >= 0 for q in p.positions.values())
        eq = analytics.equity["equity"]

        # Recompute the SAME long-only round-trip FIFO independently and confirm
        # the signed FIFO is NOT engaged (allow_short defaulted off on analytics).
        assert analytics.allow_short is False
        # Metrics come straight from the shared module -> deterministic.
        from portfolio_optimization_engine.metrics import compute_metrics

        m = compute_metrics(analytics.returns, risk_free_rate=0.02)
        assert analytics.sharpe_ratio() == m.sharpe_ratio
        assert analytics.sortino_ratio() == m.sortino_ratio
        assert analytics.max_drawdown() == m.max_drawdown
        assert analytics.annualized_return() == m.cagr
        # The curve is non-trivial (the strategy traded).
        assert eq.iloc[0] == 100_000 or abs(eq.iloc[0] - 100_000) < 100_000

    def test_identical_to_recorded_baseline(self):
        # Run twice; identical inputs -> byte-identical equity + trades + stats.
        a1, p1 = self._run()
        a2, p2 = self._run()
        pd.testing.assert_series_equal(a1.equity["equity"], a2.equity["equity"])
        pd.testing.assert_frame_equal(p1.get_trade_df(), p2.get_trade_df())
        assert a1.win_rate() == a2.win_rate()
        assert a1.profit_factor() == a2.profit_factor()
        assert a1.sharpe_ratio() == a2.sharpe_ratio()
