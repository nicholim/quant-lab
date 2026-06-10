import os
import shutil

import matplotlib
import pytest

matplotlib.use("Agg")

from datetime import datetime

import numpy as np
import pandas as pd

from src.analytics import PerformanceAnalytics
from src.backtest import Backtest
from src.data_handler import YFinanceDataHandler
from src.datastore import DataStore
from src.events import Direction, FillEvent, OrderEvent, OrderType, SignalEvent
from src.execution import SimulatedExecution
from src.portfolio import Portfolio
from src.strategy import MovingAverageCrossover

TEST_DB = "data/test_backtests.duckdb"


def ts():
    """Throwaway timestamp for constructing events in unit tests."""
    return datetime.now()


@pytest.fixture(scope="module")
def store():
    s = DataStore(TEST_DB)
    yield s
    s.close()
    if os.path.exists("data"):
        shutil.rmtree("data")


@pytest.fixture(scope="module")
def backtest_result(store):
    data = YFinanceDataHandler(["AAPL", "MSFT"], "2021-06-01", "2022-06-01", store=store)
    portfolio = Portfolio(initial_capital=100_000, position_size_pct=0.15)
    execution = SimulatedExecution(commission_pct=0.001, slippage_pct=0.0005)
    bt = Backtest(
        data,
        MovingAverageCrossover(20, 50),
        portfolio,
        execution,
        strategy_name="SMA Test",
        store=store,
    )
    analytics = bt.run()
    return analytics, portfolio


# --- Portfolio ---


class TestPortfolio:
    def test_long_only(self, backtest_result):
        _, portfolio = backtest_result
        for sym, qty in portfolio.positions.items():
            assert qty >= 0, f"Short position: {sym}={qty}"

    def test_initial_equity(self, backtest_result):
        analytics, _ = backtest_result
        first_eq = analytics.equity["equity"].iloc[0]
        assert abs(first_eq - 100_000) < 1000

    def test_equity_curve_not_empty(self, backtest_result):
        analytics, _ = backtest_result
        assert len(analytics.equity) > 100

    def test_equity_recorded_once_per_bar(self, backtest_result):
        """Equity is marked to market once per bar, not once per (bar, symbol)."""
        analytics, _ = backtest_result
        # two tickers in the fixture -> a per-symbol bug would duplicate rows
        assert analytics.equity.index.is_unique

    def test_sell_blocked_when_flat(self):
        p = Portfolio(initial_capital=10_000)
        p._last_prices = {"AAPL": 150.0}
        signal = SignalEvent(
            timestamp=datetime.now(), symbol="AAPL", direction=Direction.SELL, strength=1.0
        )

        class FakeData:
            tickers = ["AAPL"]

            def get_current_price(self, s):
                return 150.0

        order = p.process_signal(signal, FakeData())
        assert order is None, "Should not sell when position is 0"

    def test_target_weight_buy_from_flat(self):
        p = Portfolio(initial_capital=10_000)
        p._last_prices = {"AAPL": 100.0}
        sig = SignalEvent(timestamp=datetime.now(), symbol="AAPL", target_weight=0.5)

        class FakeData:
            tickers = ["AAPL"]

            def get_current_price(self, s):
                return 100.0

        order = p.process_signal(sig, FakeData())
        assert order.direction == Direction.BUY
        assert order.quantity == 50  # 10_000 * 0.5 / 100

    def test_target_weight_sell_to_reduce(self):
        p = Portfolio(initial_capital=10_000)
        p.positions = {"AAPL": 80}
        p._last_prices = {"AAPL": 100.0}  # equity = 10_000 + 80*100 = 18_000
        sig = SignalEvent(timestamp=datetime.now(), symbol="AAPL", target_weight=0.2)

        class FakeData:
            tickers = ["AAPL"]

            def get_current_price(self, s):
                return 100.0

        order = p.process_signal(sig, FakeData())
        assert order.direction == Direction.SELL
        assert order.quantity == 44  # desired int(18_000*0.2/100)=36, delta = 36-80

    def test_target_weight_no_order_when_on_target(self):
        p = Portfolio(initial_capital=10_000)
        p.positions = {"AAPL": 50}
        p._last_prices = {"AAPL": 100.0}  # equity = 15_000
        sig = SignalEvent(timestamp=datetime.now(), symbol="AAPL", target_weight=50 * 100 / 15_000)

        class FakeData:
            tickers = ["AAPL"]

            def get_current_price(self, s):
                return 100.0

        assert p.process_signal(sig, FakeData()) is None


# --- Analytics ---


class TestAnalytics:
    def test_win_rate_bounds(self, backtest_result):
        analytics, _ = backtest_result
        wr = analytics.win_rate()
        assert 0 <= wr <= 1

    def test_profit_factor_non_negative(self, backtest_result):
        analytics, _ = backtest_result
        pf = analytics.profit_factor()
        assert pf >= 0

    def test_max_drawdown_non_positive(self, backtest_result):
        analytics, _ = backtest_result
        assert analytics.max_drawdown() <= 0

    def test_drawdown_duration_non_negative(self, backtest_result):
        analytics, _ = backtest_result
        assert analytics.max_drawdown_duration() >= 0

    def test_sharpe_is_finite(self, backtest_result):
        analytics, _ = backtest_result
        assert np.isfinite(analytics.sharpe_ratio())

    def test_sortino_is_finite(self, backtest_result):
        analytics, _ = backtest_result
        assert np.isfinite(analytics.sortino_ratio())

    def test_total_return_consistent(self, backtest_result):
        analytics, _ = backtest_result
        eq = analytics.equity["equity"]
        expected = (eq.iloc[-1] / eq.iloc[0]) - 1
        assert abs(analytics.total_return() - expected) < 1e-10

    def test_round_trip_pnl_computed(self, backtest_result):
        analytics, _ = backtest_result
        pnl = analytics._compute_round_trip_pnl()
        assert isinstance(pnl, list)


# --- Execution ---


class TestExecution:
    def test_buy_slippage_increases_price(self):
        e = SimulatedExecution(commission_pct=0, slippage_pct=0.01)

        class FakeData:
            tickers = ["X"]

            def get_current_price(self, s):
                return 100.0

            def get_next_open(self, s):
                return 100.0

        order = OrderEvent(
            timestamp=datetime.now(),
            symbol="X",
            quantity=10,
            order_type=OrderType.MARKET,
            direction=Direction.BUY,
        )
        fill = e.execute_order(order, FakeData())
        assert fill.price > 100.0  # slippage makes buy worse

    def test_sell_slippage_decreases_price(self):
        e = SimulatedExecution(commission_pct=0, slippage_pct=0.01)

        class FakeData:
            tickers = ["X"]

            def get_current_price(self, s):
                return 100.0

            def get_next_open(self, s):
                return 100.0

        order = OrderEvent(
            timestamp=datetime.now(),
            symbol="X",
            quantity=10,
            order_type=OrderType.MARKET,
            direction=Direction.SELL,
        )
        fill = e.execute_order(order, FakeData())
        assert fill.price < 100.0

    def test_commission_applied(self):
        e = SimulatedExecution(commission_pct=0.01, slippage_pct=0)

        class FakeData:
            tickers = ["X"]

            def get_current_price(self, s):
                return 100.0

            def get_next_open(self, s):
                return 100.0

        order = OrderEvent(
            timestamp=datetime.now(),
            symbol="X",
            quantity=10,
            order_type=OrderType.MARKET,
            direction=Direction.BUY,
        )
        fill = e.execute_order(order, FakeData())
        assert fill.commission > 0

    def test_fills_at_next_open_not_signal_close(self):
        """A fill must use the NEXT bar's open, never the signal bar's close."""
        e = SimulatedExecution(commission_pct=0, slippage_pct=0)

        class FakeData:
            tickers = ["X"]

            def get_current_price(self, s):
                return 100.0  # signal-bar close

            def get_next_open(self, s):
                return 105.0  # next-bar open

        order = OrderEvent(
            timestamp=datetime.now(),
            symbol="X",
            quantity=10,
            order_type=OrderType.MARKET,
            direction=Direction.BUY,
        )
        fill = e.execute_order(order, FakeData())
        assert fill.price == 105.0
        assert fill.price != 100.0  # never the signal-bar close

    def test_no_fill_when_no_next_bar(self):
        e = SimulatedExecution()

        class FakeData:
            tickers = ["X"]

            def get_current_price(self, s):
                return 100.0

            def get_next_open(self, s):
                return 0.0  # last bar: no next open

        order = OrderEvent(
            timestamp=datetime.now(),
            symbol="X",
            quantity=10,
            order_type=OrderType.MARKET,
            direction=Direction.BUY,
        )
        assert e.execute_order(order, FakeData()) is None


# --- Margin / leverage ---


class TestLeverage:
    class _Data:
        tickers = ["X"]

        def get_current_price(self, s):
            return 100.0

    def test_buying_power_unbounded_by_default(self):
        p = Portfolio(initial_capital=10_000)
        p._last_prices = {"X": 100.0}
        assert p.buying_power == float("inf")

    def test_buying_power_with_leverage(self):
        p = Portfolio(initial_capital=10_000, max_leverage=2.0)
        p._last_prices = {"X": 100.0}
        assert p.buying_power == pytest.approx(20_000)  # 2x of 10k equity, no positions
        # simulate having spent 5k of cash on 50 shares: equity stays 10k
        p.positions["X"] = 50
        p.cash = 5_000
        assert p.gross_exposure == pytest.approx(5_000)
        assert p.total_equity == pytest.approx(10_000)
        assert p.buying_power == pytest.approx(2 * 10_000 - 5_000)  # 15k

    def test_fixed_fractional_capped_by_leverage(self):
        from src.sizing import PercentOfEquitySizer

        # want 150% of equity but capped at 1x leverage -> 100% of equity
        p = Portfolio(initial_capital=10_000, sizer=PercentOfEquitySizer(1.5), max_leverage=1.0)
        p._last_prices = {"X": 100.0}
        sig = SignalEvent(timestamp=ts(), symbol="X", direction=Direction.BUY)
        order = p.process_signal(sig, self._Data())
        assert order.quantity == 100  # 10_000 buying power / 100, not 150

    def test_leverage_allows_above_equity(self):
        from src.sizing import PercentOfEquitySizer

        p = Portfolio(initial_capital=10_000, sizer=PercentOfEquitySizer(1.5), max_leverage=2.0)
        p._last_prices = {"X": 100.0}
        sig = SignalEvent(timestamp=ts(), symbol="X", direction=Direction.BUY)
        order = p.process_signal(sig, self._Data())
        assert order.quantity == 150  # 150% of equity allowed under 2x leverage

    def test_margin_interest_charged_on_borrowed_cash(self):

        class FlatData:
            tickers = ["X"]

            def get_current_price(self, s):
                return 100.0

        p = Portfolio(initial_capital=10_000, margin_rate=0.252)  # 0.1%/day at 252d
        p.cash = -10_000  # borrowed
        p.update_market(FlatData(), datetime.now())
        assert p.cash == pytest.approx(-10_000 - 10_000 * 0.252 / 252)

    def test_no_interest_when_cash_positive(self):
        class FlatData:
            tickers = ["X"]

            def get_current_price(self, s):
                return 100.0

        p = Portfolio(initial_capital=10_000, margin_rate=0.10)
        p.update_market(FlatData(), datetime.now())
        assert p.cash == 10_000  # positive cash -> no interest


# --- Protective exits (stop-loss / take-profit / trailing) ---


class TestProtectiveExits:
    class _Data:
        tickers = ["X"]

        def __init__(self, price):
            self._price = price

        def get_current_price(self, s):
            return self._price

    def _long_portfolio(self, **kw):
        p = Portfolio(initial_capital=10_000, **kw)
        p.positions["X"] = 100
        p._entry_price["X"] = 100.0
        p._high_water["X"] = 100.0
        return p

    def test_no_exit_within_bounds(self):
        p = self._long_portfolio(stop_loss_pct=0.1, take_profit_pct=0.2)
        assert p.check_exits(self._Data(105.0), ts()) == []

    def test_stop_loss_triggers(self):
        p = self._long_portfolio(stop_loss_pct=0.1)
        orders = p.check_exits(self._Data(89.0), ts())  # -11% < -10%
        assert len(orders) == 1
        assert orders[0].direction == Direction.SELL
        assert orders[0].quantity == 100

    def test_take_profit_triggers(self):
        p = self._long_portfolio(take_profit_pct=0.2)
        orders = p.check_exits(self._Data(121.0), ts())  # +21% > +20%
        assert len(orders) == 1 and orders[0].direction == Direction.SELL

    def test_trailing_stop_triggers_after_peak(self):
        p = self._long_portfolio(trailing_stop_pct=0.1)
        p.check_exits(self._Data(150.0), ts())  # high-water rises to 150
        orders = p.check_exits(self._Data(134.0), ts())  # 134 < 150*0.9=135
        assert len(orders) == 1 and orders[0].direction == Direction.SELL

    def test_trailing_does_not_trigger_near_peak(self):
        p = self._long_portfolio(trailing_stop_pct=0.1)
        p.check_exits(self._Data(150.0), ts())
        assert p.check_exits(self._Data(140.0), ts()) == []  # 140 > 135

    def test_disabled_by_default(self):
        p = self._long_portfolio()  # no thresholds
        assert p.check_exits(self._Data(1.0), ts()) == []

    def test_entry_price_weighted_average(self):
        p = Portfolio(initial_capital=100_000)
        p.process_fill(
            FillEvent(timestamp=ts(), symbol="X", quantity=10, price=100.0, direction=Direction.BUY)
        )
        p.process_fill(
            FillEvent(timestamp=ts(), symbol="X", quantity=10, price=120.0, direction=Direction.BUY)
        )
        assert p._entry_price["X"] == pytest.approx(110.0)  # (100*10 + 120*10)/20

    def test_tracking_cleared_on_close(self):
        p = Portfolio(initial_capital=100_000)
        p.process_fill(
            FillEvent(timestamp=ts(), symbol="X", quantity=10, price=100.0, direction=Direction.BUY)
        )
        p.process_fill(
            FillEvent(
                timestamp=ts(), symbol="X", quantity=10, price=110.0, direction=Direction.SELL
            )
        )
        assert "X" not in p._entry_price and "X" not in p._high_water

    def test_stop_loss_exits_on_crash_end_to_end(self):
        from src.strategy import Strategy

        class BuyOnce(Strategy):
            """Buy once early, never sell — the only exit is the protective stop."""

            def __init__(self):
                self._done = False

            def calculate_signals(self, event, data):
                if not self._done and len(data.get_latest_bars(event.symbol, 5)) >= 2:
                    self._done = True
                    return SignalEvent(
                        timestamp=event.timestamp,
                        symbol=event.symbol,
                        direction=Direction.BUY,
                        strength=1.0,
                    )
                return None

        # Flat at 100, then crashes to 70 (a >10% stop must fire).
        n = 20
        close = np.array([100.0] * 10 + [70.0] * 10)
        idx = pd.date_range("2021-01-01", periods=n, freq="B")
        df = pd.DataFrame(
            {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1000}, index=idx
        )

        class MemoryHandler(YFinanceDataHandler):
            def fetch(self):
                pass

        h = MemoryHandler(["X"], "2021-01-01", "2021-01-28")
        h._data = {"X": df}
        h._total_bars = n

        portfolio = Portfolio(initial_capital=100_000, position_size_pct=0.5, stop_loss_pct=0.1)
        Backtest(
            h, BuyOnce(), portfolio, SimulatedExecution(commission_pct=0, slippage_pct=0)
        ).run()

        trades = portfolio.get_trade_df()
        assert (trades["direction"] == "BUY").any()
        assert (trades["direction"] == "SELL").any()  # stop-loss fired
        assert portfolio.positions.get("X", 0) == 0  # flat after the stop


# --- Pluggable sizers ---


class TestSizers:
    class _Data:
        tickers = ["X"]

        def __init__(self, price=100.0, bars=None):
            self._price = price
            self._bars = bars

        def get_current_price(self, s):
            return self._price

        def get_latest_bars(self, s, n=1):
            return self._bars

    def _portfolio(self, sizer):
        p = Portfolio(initial_capital=10_000, sizer=sizer)
        p._last_prices = {"X": 100.0}
        return p

    def test_default_is_fixed_fractional(self):
        from src.sizing import FixedFractionalSizer

        p = Portfolio(initial_capital=10_000, position_size_pct=0.2)
        assert isinstance(p.sizer, FixedFractionalSizer)
        assert p.sizer.position_size_pct == 0.2

    def test_fixed_fractional_quantity(self):
        from src.sizing import FixedFractionalSizer

        p = self._portfolio(FixedFractionalSizer(0.2))
        sig = SignalEvent(
            timestamp=datetime.now(), symbol="X", direction=Direction.BUY, strength=1.0
        )
        order = p.process_signal(sig, self._Data(price=100.0))
        assert order.quantity == 20  # 10_000 * 0.2 / 100

    def test_percent_of_equity_ignores_strength(self):
        from src.sizing import PercentOfEquitySizer

        p = self._portfolio(PercentOfEquitySizer(0.1))
        sig = SignalEvent(
            timestamp=datetime.now(), symbol="X", direction=Direction.BUY, strength=2.0
        )
        order = p.process_signal(sig, self._Data(price=100.0))
        assert order.quantity == 10  # 10_000 * 0.1 / 100, strength ignored

    def test_risk_based_scales_inverse_to_vol(self):
        from src.sizing import RiskBasedSizer

        # low-vol series -> larger position than high-vol series
        low = pd.DataFrame({"Close": 100 + np.linspace(0, 1, 30)})
        high = pd.DataFrame({"Close": 100 + np.cumsum(np.random.default_rng(0).normal(0, 3, 30))})
        sig = SignalEvent(timestamp=datetime.now(), symbol="X", direction=Direction.BUY)
        p = self._portfolio(RiskBasedSizer(risk_per_trade=0.02, lookback=20))
        q_low = p.process_signal(sig, self._Data(price=100.0, bars=low)).quantity
        p2 = self._portfolio(RiskBasedSizer(risk_per_trade=0.02, lookback=20))
        q_high = p2.process_signal(sig, self._Data(price=100.0, bars=high)).quantity
        assert q_low > q_high

    def test_target_weight_routes_regardless_of_sizer(self):
        from src.sizing import FixedFractionalSizer

        p = self._portfolio(FixedFractionalSizer(0.99))  # configured sizer
        sig = SignalEvent(timestamp=datetime.now(), symbol="X", target_weight=0.5)
        order = p.process_signal(sig, self._Data(price=100.0))
        # routed to TargetWeightSizer: 10_000 * 0.5 / 100 = 50, not the 0.99 fixed-frac
        assert order.quantity == 50


# --- Multi-timeframe resampling ---


class TestResampling:
    def _daily(self, n=120):
        idx = pd.date_range("2021-01-04", periods=n, freq="B")  # start on a Monday
        close = 100 + np.arange(n) * 0.5  # steady uptrend
        return pd.DataFrame(
            {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 10},
            index=idx,
        )

    def test_resample_ohlc_aggregation(self):
        from src.data_handler import resample_ohlc

        df = self._daily(10)
        weekly = resample_ohlc(df, "W")
        # first weekly bar's Open is the first daily Open; High is the week max
        first_week = df.iloc[:5]
        assert weekly["Open"].iloc[0] == first_week["Open"].iloc[0]
        assert weekly["High"].iloc[0] == first_week["High"].max()
        assert weekly["Close"].iloc[0] == first_week["Close"].iloc[-1]
        assert weekly["Volume"].iloc[0] == first_week["Volume"].sum()

    def test_get_resampled_bars_no_lookahead(self):
        df = self._daily(40)
        dh = YFinanceDataHandler(["X"], "2021-01-04", "2021-03-01")
        dh._data = {"X": df}
        dh._total_bars = len(df)
        dh._current_idx = 10  # only first 10 daily bars visible
        weekly = dh.get_resampled_bars("X", "W", n=10)
        # resampled only from visible history -> last weekly close <= 10th daily close
        assert weekly["Close"].iloc[-1] <= df["Close"].iloc[9]

    def test_trend_filtered_ma_runs(self):
        from src.strategy import TrendFilteredMA

        df = self._daily(120)

        class MemoryHandler(YFinanceDataHandler):
            def fetch(self):
                pass

        dh = MemoryHandler(["X"], "2021-01-04", "2021-06-30")
        dh._data = {"X": df}
        dh._total_bars = len(df)
        portfolio = Portfolio(initial_capital=100_000)
        Backtest(dh, TrendFilteredMA(10, 30, 4), portfolio, SimulatedExecution()).run()
        # steady uptrend with weekly filter -> ends long, no shorts
        assert len(portfolio.equity_curve) > 30
        assert all(q >= 0 for q in portfolio.positions.values())


# --- Limit / stop / OCO order execution ---


class TestOrderExecution:
    class _Bar:
        tickers = ["X"]

        def __init__(self, bar, next_open=100.0):
            self._bar = bar
            self._next_open = next_open

        def get_current_bar(self, s):
            return self._bar

        def get_next_open(self, s):
            return self._next_open

        def get_current_price(self, s):
            return self._bar["close"]

    @staticmethod
    def _bar(o, h, low, c):
        return {"open": o, "high": h, "low": low, "close": c}

    def test_market_fills_immediately(self):
        e = SimulatedExecution(commission_pct=0, slippage_pct=0)
        order = OrderEvent(ts(), "X", 10, OrderType.MARKET, Direction.BUY)
        fill = e.execute_order(order, self._Bar(self._bar(100, 101, 99, 100), next_open=100))
        assert fill is not None and fill.price == 100

    def test_limit_order_is_queued(self):
        e = SimulatedExecution()
        order = OrderEvent(ts(), "X", 10, OrderType.LIMIT, Direction.BUY, limit_price=95)
        assert e.execute_order(order, self._Bar(self._bar(100, 101, 99, 100))) is None
        assert len(e._pending) == 1

    def test_buy_limit_fills_when_price_dips(self):
        e = SimulatedExecution(commission_pct=0, slippage_pct=0)
        e.execute_order(
            OrderEvent(ts(), "X", 10, OrderType.LIMIT, Direction.BUY, limit_price=95),
            self._Bar(self._bar(100, 101, 99, 100)),
        )
        fills = e.check_pending(self._Bar(self._bar(96, 97, 94, 96)), ts())  # low 94 <= 95
        assert len(fills) == 1
        assert fills[0].price == 95  # open 96 > 95 -> fills at the limit
        assert e._pending == []

    def test_buy_limit_not_filled_above_limit(self):
        e = SimulatedExecution()
        e.execute_order(
            OrderEvent(ts(), "X", 10, OrderType.LIMIT, Direction.BUY, limit_price=95),
            self._Bar(self._bar(100, 101, 99, 100)),
        )
        fills = e.check_pending(self._Bar(self._bar(100, 102, 98, 101)), ts())  # low 98 > 95
        assert fills == [] and len(e._pending) == 1

    def test_sell_stop_triggers_on_low(self):
        e = SimulatedExecution(commission_pct=0, slippage_pct=0)
        e.execute_order(
            OrderEvent(ts(), "X", 10, OrderType.STOP, Direction.SELL, limit_price=90),
            self._Bar(self._bar(100, 101, 99, 100)),
        )
        fills = e.check_pending(self._Bar(self._bar(95, 96, 88, 89)), ts())  # low 88 <= 90
        assert len(fills) == 1 and fills[0].price == 90  # min(open 95, 90)

    def test_oco_cancels_sibling_on_fill(self):
        e = SimulatedExecution(commission_pct=0, slippage_pct=0)
        d = self._Bar(self._bar(100, 101, 99, 100))
        # bracket: take-profit SELL LIMIT @110 + stop SELL STOP @90, same OCO group
        e.execute_order(
            OrderEvent(
                ts(), "X", 10, OrderType.LIMIT, Direction.SELL, limit_price=110, oco_group="b1"
            ),
            d,
        )
        e.execute_order(
            OrderEvent(
                ts(), "X", 10, OrderType.STOP, Direction.SELL, limit_price=90, oco_group="b1"
            ),
            d,
        )
        assert len(e._pending) == 2
        fills = e.check_pending(self._Bar(self._bar(108, 112, 107, 111)), ts())  # high 112 >= 110
        assert len(fills) == 1  # take-profit filled
        assert e._pending == []  # stop sibling cancelled

    def test_limit_entry_end_to_end(self):
        from src.strategy import Strategy

        class BuyLimitOnce(Strategy):
            def __init__(self):
                self._done = False

            def calculate_signals(self, event, data):
                if not self._done and len(data.get_latest_bars(event.symbol, 5)) >= 2:
                    self._done = True
                    return SignalEvent(
                        timestamp=event.timestamp,
                        symbol=event.symbol,
                        direction=Direction.BUY,
                        strength=1.0,
                        limit_price=90.0,
                    )
                return None

        close = np.array([100, 100, 100, 85, 90, 95, 100, 100, 100, 100], dtype=float)
        idx = pd.date_range("2021-01-01", periods=len(close), freq="B")
        df = pd.DataFrame(
            {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1000},
            index=idx,
        )

        class MemoryHandler(YFinanceDataHandler):
            def fetch(self):
                pass

        h = MemoryHandler(["X"], "2021-01-01", "2021-01-14")
        h._data = {"X": df}
        h._total_bars = len(close)

        portfolio = Portfolio(initial_capital=100_000, position_size_pct=0.5)
        Backtest(
            h, BuyLimitOnce(), portfolio, SimulatedExecution(commission_pct=0, slippage_pct=0)
        ).run()

        trades = portfolio.get_trade_df()
        assert (trades["direction"] == "BUY").any()
        # limit filled around 90 (the dip), not the ~100 signal-bar price
        buy_price = trades[trades["direction"] == "BUY"]["price"].iloc[0]
        assert buy_price <= 90.0
        assert portfolio.positions.get("X", 0) > 0


# --- Data handler fill timing (no same-bar fill) ---


class TestFillTiming:
    def _handler(self):
        df = pd.DataFrame(
            {
                "Open": [10.0, 20.0, 30.0],
                "High": [11.0, 21.0, 31.0],
                "Low": [9.0, 19.0, 29.0],
                "Close": [11.0, 21.0, 31.0],
                "Volume": [100, 100, 100],
            },
            index=pd.date_range("2021-01-01", periods=3),
        )
        dh = YFinanceDataHandler(["X"], "2021-01-01", "2021-01-03")
        dh._data = {"X": df}
        dh._total_bars = 3
        return dh

    def test_current_price_is_signal_bar_close(self):
        dh = self._handler()
        dh._current_idx = 1  # processing bar 0
        assert dh.get_current_price("X") == 11.0  # bar 0 close

    def test_next_open_is_following_bar(self):
        dh = self._handler()
        dh._current_idx = 1  # processing bar 0
        assert dh.get_next_open("X") == 20.0  # bar 1 open
        # fill price differs from the signal-bar close -> no same-bar fill
        assert dh.get_next_open("X") != dh.get_current_price("X")

    def test_no_next_open_on_last_bar(self):
        dh = self._handler()
        dh._current_idx = 3  # past the last bar
        assert dh.get_next_open("X") == 0.0


# --- Shared metrics: backtester agrees with the engine ---


class TestSharedMetrics:
    def _analytics(self):
        rng = np.random.default_rng(7)
        rets = pd.Series(
            rng.normal(0.0005, 0.01, 300), index=pd.date_range("2021-01-01", periods=300)
        )
        eq = 100_000 * (1 + rets).cumprod()
        equity_df = pd.DataFrame({"equity": eq, "cash": 0.0})
        return PerformanceAnalytics(equity_df, pd.DataFrame(), risk_free_rate=0.02)

    def test_sharpe_matches_engine(self):
        from portfolio_optimization_engine.metrics import compute_metrics

        pa = self._analytics()
        m = compute_metrics(pa.returns, risk_free_rate=0.02)
        assert pa.sharpe_ratio() == m.sharpe_ratio

    def test_sortino_matches_engine(self):
        from portfolio_optimization_engine.metrics import compute_metrics

        pa = self._analytics()
        m = compute_metrics(pa.returns, risk_free_rate=0.02)
        assert pa.sortino_ratio() == m.sortino_ratio

    def test_drawdown_and_calmar_match_engine(self):
        from portfolio_optimization_engine.metrics import compute_metrics

        pa = self._analytics()
        m = compute_metrics(pa.returns, risk_free_rate=0.02)
        assert pa.max_drawdown() == m.max_drawdown
        assert pa.calmar_ratio() == m.calmar_ratio

    def test_beta_alpha_none_without_benchmark(self):
        pa = self._analytics()
        assert pa.beta() is None
        assert pa.alpha() is None

    def test_beta_alpha_with_benchmark(self):
        rng = np.random.default_rng(11)
        idx = pd.date_range("2021-01-01", periods=300)
        bench = pd.Series(rng.normal(0.0004, 0.011, 300), index=idx)
        port = 1.5 * bench + rng.normal(0, 0.001, 300)  # beta ~1.5 to benchmark
        eq = 100_000 * (1 + pd.Series(port, index=idx)).cumprod()
        pa = PerformanceAnalytics(
            pd.DataFrame({"equity": eq, "cash": 0.0}),
            pd.DataFrame(),
            benchmark_returns=bench,
        )
        assert pa.beta() is not None
        assert 1.3 < pa.beta() < 1.7  # recovers the injected beta
        assert pa.alpha() is not None


# --- Optimization rebalance strategy (engine integration, offline) ---


class TestOptimizationRebalance:
    def _handler(self):
        rng = np.random.default_rng(0)
        n = 150
        idx = pd.date_range("2021-01-01", periods=n, freq="B")
        data = {}
        for i, t in enumerate(["AAA", "BBB"]):
            rets = rng.normal(0.0005 + i * 0.0002, 0.012, n)
            close = 100 * np.cumprod(1 + rets)
            open_ = np.concatenate([[100.0], close[:-1]])  # open ~ prior close
            data[t] = pd.DataFrame(
                {
                    "Open": open_,
                    "High": close * 1.01,
                    "Low": close * 0.99,
                    "Close": close,
                    "Volume": 1000,
                },
                index=idx,
            )

        class MemoryHandler(YFinanceDataHandler):
            def fetch(self):  # data is pre-injected; no network
                pass

        h = MemoryHandler(["AAA", "BBB"], "2021-01-01", "2021-12-31")
        h._data = data
        h._total_bars = n
        return h

    def test_produces_equity_and_trades(self):
        from src.strategy import OptimizationRebalanceStrategy

        h = self._handler()
        portfolio = Portfolio(initial_capital=100_000)
        execution = SimulatedExecution()
        strat = OptimizationRebalanceStrategy(
            ["AAA", "BBB"], lookback=60, rebalance_freq=21, objective="sharpe"
        )
        analytics = Backtest(h, strat, portfolio, execution).run()
        assert len(analytics.equity) > 60
        assert len(portfolio.trade_log) > 0  # rebalancing executed trades

    def test_stays_long_only(self):
        from src.strategy import OptimizationRebalanceStrategy

        h = self._handler()
        portfolio = Portfolio(initial_capital=100_000)
        strat = OptimizationRebalanceStrategy(["AAA", "BBB"], lookback=60, rebalance_freq=21)
        Backtest(h, strat, portfolio, SimulatedExecution()).run()
        for sym, qty in portfolio.positions.items():
            assert qty >= 0, f"Short position: {sym}={qty}"

    def test_targets_sum_to_about_one(self):
        from src.strategy import OptimizationRebalanceStrategy

        h = self._handler()
        h.fetch()
        h._current_idx = 80  # enough history for lookback=60
        strat = OptimizationRebalanceStrategy(["AAA", "BBB"], lookback=60)
        targets = strat._compute_targets(h)
        assert targets is not None
        assert abs(sum(targets.values()) - 1.0) < 1e-6
        assert all(w >= -1e-9 for w in targets.values())  # long-only


# --- Cross-sectional ranking ---


class TestCrossSectionalMomentum:
    def _handler(self, drifts):
        n = 150
        idx = pd.date_range("2021-01-01", periods=n, freq="B")
        rng = np.random.default_rng(1)
        data = {}
        for t, drift in drifts.items():
            rets = rng.normal(drift, 0.01, n)
            close = 100 * np.cumprod(1 + rets)
            open_ = np.concatenate([[100.0], close[:-1]])
            data[t] = pd.DataFrame(
                {
                    "Open": open_,
                    "High": close * 1.01,
                    "Low": close * 0.99,
                    "Close": close,
                    "Volume": 1000,
                },
                index=idx,
            )

        class MemoryHandler(YFinanceDataHandler):
            def fetch(self):
                pass

        h = MemoryHandler(list(drifts), "2021-01-01", "2021-12-31")
        h._data = data
        h._total_bars = n
        return h

    def test_ranks_top_k_equal_weight(self):
        from src.strategy import CrossSectionalMomentum

        # WIN has the strongest drift -> should be the sole holding (top_k=1)
        h = self._handler({"WIN": 0.003, "MID": 0.0005, "LAG": -0.001})
        h.fetch()
        h._current_idx = 80
        strat = CrossSectionalMomentum(["WIN", "MID", "LAG"], lookback=60, top_k=1)
        targets = strat._rank(h)
        assert targets["WIN"] == 1.0
        assert targets["MID"] == 0.0 and targets["LAG"] == 0.0

    def test_top_k_two_splits_weight(self):
        from src.strategy import CrossSectionalMomentum

        h = self._handler({"A": 0.003, "B": 0.002, "C": -0.001})
        h.fetch()
        h._current_idx = 80
        strat = CrossSectionalMomentum(["A", "B", "C"], lookback=60, top_k=2)
        targets = strat._rank(h)
        assert targets["A"] == 0.5 and targets["B"] == 0.5
        assert targets["C"] == 0.0

    def test_runs_and_holds_winner(self):
        from src.strategy import CrossSectionalMomentum

        h = self._handler({"WIN": 0.003, "LAG": -0.001})
        portfolio = Portfolio(initial_capital=100_000)
        strat = CrossSectionalMomentum(["WIN", "LAG"], lookback=60, top_k=1, rebalance_freq=21)
        Backtest(h, strat, portfolio, SimulatedExecution()).run()
        # ends concentrated in the winner, no short positions
        assert portfolio.positions.get("WIN", 0) > 0
        assert all(q >= 0 for q in portfolio.positions.values())


# --- Parameter optimization ---


class TestParamSearch:
    def _data_factory(self):
        n = 200
        idx = pd.date_range("2021-01-01", periods=n, freq="B")
        rng = np.random.default_rng(3)
        close = 100 * np.cumprod(1 + rng.normal(0.0006, 0.012, n))
        open_ = np.concatenate([[100.0], close[:-1]])
        df = pd.DataFrame(
            {
                "Open": open_,
                "High": close * 1.01,
                "Low": close * 0.99,
                "Close": close,
                "Volume": 1000,
            },
            index=idx,
        )

        class MemoryHandler(YFinanceDataHandler):
            def fetch(self):
                pass

        def factory():
            h = MemoryHandler(["X"], "2021-01-01", "2021-12-31")
            h._data = {"X": df}
            h._total_bars = n
            return h

        return factory

    def test_grid_search_one_row_per_combo(self):
        from src.param_search import grid_search
        from src.strategy import MovingAverageCrossover

        grid = {"short_window": [5, 10], "long_window": [20, 40]}
        results = grid_search(MovingAverageCrossover, grid, self._data_factory(), store=None)
        assert len(results) == 4  # 2 x 2
        for col in ["short_window", "long_window", "sharpe", "max_drawdown", "total_trades"]:
            assert col in results.columns

    def test_heatmap_pivot_shape(self):
        import matplotlib

        matplotlib.use("Agg")
        from src.param_search import grid_search, heatmap
        from src.strategy import MovingAverageCrossover

        grid = {"short_window": [5, 10], "long_window": [20, 40]}
        results = grid_search(MovingAverageCrossover, grid, self._data_factory())
        pivot = heatmap(results, x_param="short_window", y_param="long_window", metric="sharpe")
        assert pivot.shape == (2, 2)

    def test_walk_forward_rolls_oos_windows(self):
        from src.param_search import walk_forward
        from src.strategy import MovingAverageCrossover

        # ~2 years of business days; a date-range-slicing in-memory factory
        idx = pd.date_range("2021-01-01", periods=520, freq="B")
        rng = np.random.default_rng(5)
        close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.012, len(idx)))
        full = pd.DataFrame(
            {
                "Open": close,
                "High": close * 1.01,
                "Low": close * 0.99,
                "Close": close,
                "Volume": 1000,
            },
            index=idx,
        )

        class MemoryHandler(YFinanceDataHandler):
            def fetch(self):
                pass

        def data_factory(start, end):
            sub = full.loc[start:end]
            h = MemoryHandler(["X"], start, end)
            h._data = {"X": sub}
            h._total_bars = len(sub)
            return h

        results = walk_forward(
            MovingAverageCrossover,
            {"short_window": [5, 10], "long_window": [20, 30]},
            data_factory=data_factory,
            start="2021-01-01",
            end="2022-12-31",
            is_months=6,
            oos_months=3,
            metric="sharpe",
        )
        # 6m IS + sliding 3m OOS over ~24 months -> several windows
        assert len(results) >= 3
        for col in ["oos_start", "oos_end", "short_window", "long_window", "oos_sharpe"]:
            assert col in results.columns
        # chosen params are always from the supplied grid
        assert set(results["short_window"].unique()).issubset({5, 10})
        # OOS windows advance monotonically
        starts = pd.to_datetime(results["oos_start"]).tolist()
        assert starts == sorted(starts)


# --- Interactive Bokeh plotting ---


class TestInteractive:
    def _analytics(self):
        rng = np.random.default_rng(9)
        idx = pd.date_range("2021-01-01", periods=200)
        eq = 100_000 * (1 + pd.Series(rng.normal(0.0005, 0.01, 200), index=idx)).cumprod()
        return PerformanceAnalytics(pd.DataFrame({"equity": eq, "cash": 0.0}), pd.DataFrame())

    def test_equity_html_written(self, tmp_path):
        from src.interactive import plot_equity_bokeh

        out = tmp_path / "equity.html"
        plot_equity_bokeh(self._analytics(), save_path=str(out))
        assert out.exists() and out.stat().st_size > 0
        assert "<html" in out.read_text().lower()

    def test_performance_html_written(self, tmp_path):
        from src.interactive import plot_performance_bokeh

        out = tmp_path / "perf.html"
        plot_performance_bokeh(self._analytics(), save_path=str(out))
        assert out.exists()
        text = out.read_text().lower()
        assert "<html" in text and "bokeh" in text  # self-contained Bokeh doc


# --- Dashboard (figure / metric builders, offline) ---


class TestDashboard:
    def _analytics(self):
        rng = np.random.default_rng(8)
        idx = pd.date_range("2021-01-01", periods=200)
        bench = pd.Series(rng.normal(0.0004, 0.01, 200), index=idx)
        eq = 100_000 * (1 + pd.Series(rng.normal(0.0005, 0.01, 200), index=idx)).cumprod()
        return PerformanceAnalytics(
            pd.DataFrame({"equity": eq, "cash": 0.0}), pd.DataFrame(), benchmark_returns=bench
        )

    def _opt_result(self):
        from portfolio_optimization_engine.optimizer import PortfolioResult

        return PortfolioResult(
            weights=np.array([0.5, 0.3, 0.2]),
            expected_return=0.15,
            volatility=0.20,
            sharpe_ratio=0.65,
            sortino_ratio=0.9,
            cvar=0.03,
            objective="sharpe",
        )

    def _frontier(self):
        rng = np.random.default_rng(2)
        return pd.DataFrame(
            {
                "return": rng.normal(0.12, 0.03, 300),
                "volatility": rng.normal(0.20, 0.04, 300),
                "sharpe": rng.normal(0.5, 0.2, 300),
            }
        )

    def test_frontier_figure(self):
        import dashboard

        fig = dashboard.frontier_figure(self._frontier(), {"max_sharpe": self._opt_result()})
        assert len(fig.data) >= 2  # scatter cloud + at least one marker

    def test_weights_figure(self):
        import dashboard

        fig = dashboard.weights_figure(self._opt_result(), ["A", "B", "C"])
        assert len(fig.data) == 1 and len(fig.data[0].x) == 3

    def test_equity_and_drawdown_figures(self):
        import dashboard

        a = self._analytics()
        assert len(dashboard.equity_figure(a).data) == 1
        assert len(dashboard.drawdown_figure(a).data) == 1

    def test_backtest_metric_rows_include_beta(self):
        import dashboard

        rows = dashboard.backtest_metric_rows(self._analytics())
        labels = [r[0] for r in rows]
        # Beta/Alpha appear when a benchmark is present...
        assert "Beta" in labels and "Alpha (ann.)" in labels
        # ...and the table carries the non-headline metrics only (Sharpe, Total
        # Return, Sortino, Max Drawdown are the KPI cards, not duplicated here).
        assert "CAGR" in labels and "Sharpe" not in labels

    def test_build_app_has_layout(self):
        import dashboard

        app = dashboard.build_app()
        assert app.layout is not None


# --- DuckDB ---


class TestDuckDB:
    def test_cache_hit(self, store):
        assert store.has_cached_data("AAPL", "2021-06-01", "2022-06-01")

    def test_backtest_saved(self, store):
        comp = store.compare_strategies()
        assert len(comp) >= 1

    def test_sql_query(self, store):
        result = store.query("SELECT COUNT(*) as n FROM trades")
        assert result["n"].iloc[0] > 0

    def test_trades_linked_to_run(self, store):
        result = store.query("""
            SELECT t.run_id, r.strategy_name
            FROM trades t JOIN backtest_runs r ON t.run_id = r.run_id
            LIMIT 1
        """)
        assert len(result) > 0
