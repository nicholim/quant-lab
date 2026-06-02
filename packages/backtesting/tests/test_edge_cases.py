"""Edge-case and gap-filling tests.

Complements ``test_backtester.py`` (the main suite) by covering:

* Empty / single-bar return series through PerformanceAnalytics.
* Trade-level analytics edge cases (no losses, no round trips, partial fills).
* Shared-metrics parity with the optimizer for hand-checked return series.
* Execution cost decomposition (commission vs. slippage) and STOP slippage.
* The MeanReversion / Momentum strategies (untested in the main suite).
* The OptimizationRebalanceStrategy across every objective + its failure path.

Style mirrors test_backtester.py: deterministic seeded RNG, in-memory
``YFinanceDataHandler`` subclasses (no network), and module-scoped helpers.
"""

from datetime import datetime

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")

from portfolio_optimization_engine.metrics import compute_metrics

from src.analytics import PerformanceAnalytics
from src.backtest import Backtest
from src.data_handler import YFinanceDataHandler
from src.events import Direction, MarketEvent, OrderEvent, OrderType
from src.execution import SimulatedExecution
from src.portfolio import Portfolio

# --- shared in-memory data helpers ---------------------------------------


def _ohlcv(close, *, open_=None):
    """Build an OHLCV frame from a close series (high/low straddle close)."""
    close = np.asarray(close, dtype=float)
    n = len(close)
    idx = pd.date_range("2021-01-01", periods=n, freq="B")
    if open_ is None:
        open_ = np.concatenate([[close[0]], close[:-1]])  # open ~ prior close
    return pd.DataFrame(
        {
            "Open": open_,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1000,
        },
        index=idx,
    )


def _handler(frames: dict[str, pd.DataFrame]) -> YFinanceDataHandler:
    """An offline YFinanceDataHandler pre-loaded with in-memory frames."""

    class MemoryHandler(YFinanceDataHandler):
        def fetch(self):  # data is pre-injected; no network
            pass

    tickers = list(frames)
    n = min(len(df) for df in frames.values())
    h = MemoryHandler(tickers, "2021-01-01", "2021-12-31")
    h._data = frames
    h._total_bars = n
    return h


def _equity_from_returns(returns: pd.Series, capital: float = 100_000) -> pd.DataFrame:
    eq = capital * (1 + returns).cumprod()
    return pd.DataFrame({"equity": eq, "cash": 0.0})


# --- empty / single-bar analytics ----------------------------------------


class TestDegenerateAnalytics:
    """A backtest that never trades or has <2 equity points must not crash."""

    def test_single_bar_equity_yields_zero_metrics(self):
        eq = pd.DataFrame(
            {"equity": [100_000.0], "cash": [0.0]},
            index=pd.date_range("2021-01-01", periods=1),
        )
        pa = PerformanceAnalytics(eq, pd.DataFrame())
        assert len(pa.returns) == 0
        # Every ratio short-circuits to a benign default (no division by zero).
        assert pa.sharpe_ratio() == 0.0
        assert pa.sortino_ratio() == 0.0
        assert pa.max_drawdown() == 0.0
        assert pa.annualized_return() == 0.0
        assert pa.annualized_volatility() == 0.0
        assert pa.calmar_ratio() == 0.0
        assert pa.total_return() == 0.0
        assert pa.max_drawdown_duration() == 0

    def test_empty_trades_have_zero_trade_stats(self):
        eq = _equity_from_returns(
            pd.Series([0.01, -0.005, 0.02], index=pd.date_range("2021-01-01", periods=3))
        )
        pa = PerformanceAnalytics(eq, pd.DataFrame())
        assert pa.win_rate() == 0.0
        assert pa.profit_factor() == 0.0
        assert pa._compute_round_trip_pnl() == []

    def test_no_benchmark_means_no_beta_alpha(self):
        eq = _equity_from_returns(
            pd.Series([0.01, -0.005], index=pd.date_range("2021-01-01", periods=2))
        )
        pa = PerformanceAnalytics(eq, pd.DataFrame())
        assert pa.beta() is None
        assert pa.alpha() is None

    def test_generate_report_runs_on_degenerate_input(self, capsys):
        eq = pd.DataFrame(
            {"equity": [100_000.0], "cash": [0.0]},
            index=pd.date_range("2021-01-01", periods=1),
        )
        PerformanceAnalytics(eq, pd.DataFrame()).generate_report()
        out = capsys.readouterr().out
        assert "PERFORMANCE REPORT" in out


# --- trade-level analytics edge cases -------------------------------------


def _trades(rows):
    cols = ["timestamp", "symbol", "direction", "quantity", "price", "commission", "slippage"]
    return pd.DataFrame(rows, columns=cols)


class TestTradeAnalytics:
    def test_profit_factor_infinite_with_no_losses(self):
        trades = _trades(
            [
                [1, "X", "BUY", 10, 100.0, 0.0, 0.0],
                [2, "X", "SELL", 10, 110.0, 0.0, 0.0],
            ]
        )
        eq = _equity_from_returns(pd.Series([0.01], index=pd.date_range("2021-01-01", periods=1)))
        pa = PerformanceAnalytics(eq, trades)
        assert pa.win_rate() == 1.0
        assert pa.profit_factor() == float("inf")
        assert pa._compute_round_trip_pnl() == [100.0]

    def test_partial_round_trips_match_fifo(self):
        # Buy 10 @ 100, then sell 4 @ 120 and 6 @ 90 -> two round trips, one each side.
        trades = _trades(
            [
                [1, "X", "BUY", 10, 100.0, 0.0, 0.0],
                [2, "X", "SELL", 4, 120.0, 0.0, 0.0],
                [3, "X", "SELL", 6, 90.0, 0.0, 0.0],
            ]
        )
        eq = _equity_from_returns(pd.Series([0.0], index=pd.date_range("2021-01-01", periods=1)))
        pa = PerformanceAnalytics(eq, trades)
        pnl = pa._compute_round_trip_pnl()
        assert pnl == [80.0, -60.0]  # (120-100)*4 and (90-100)*6
        assert pa.win_rate() == 0.5
        assert abs(pa.profit_factor() - (80.0 / 60.0)) < 1e-9

    def test_sell_without_open_long_is_ignored(self):
        trades = _trades([[1, "X", "SELL", 5, 100.0, 0.0, 0.0]])
        eq = _equity_from_returns(pd.Series([0.0], index=pd.date_range("2021-01-01", periods=1)))
        pa = PerformanceAnalytics(eq, trades)
        assert pa._compute_round_trip_pnl() == []


# --- shared-metrics parity (hand-checkable series) ------------------------


class TestMetricsParity:
    """Backtester analytics must echo the optimizer's metrics exactly."""

    def _series(self, seed=42, n=252):
        rng = np.random.default_rng(seed)
        return pd.Series(
            rng.normal(0.0006, 0.011, n),
            index=pd.date_range("2021-01-01", periods=n, freq="B"),
        )

    def test_all_ratios_match_engine(self):
        rets = self._series()
        pa = PerformanceAnalytics(_equity_from_returns(rets), pd.DataFrame(), risk_free_rate=0.03)
        m = compute_metrics(pa.returns, risk_free_rate=0.03)
        assert pa.sharpe_ratio() == m.sharpe_ratio
        assert pa.sortino_ratio() == m.sortino_ratio
        assert pa.max_drawdown() == m.max_drawdown
        assert pa.calmar_ratio() == m.calmar_ratio
        assert pa.annualized_return() == m.cagr
        assert pa.annualized_volatility() == m.annualized_volatility

    def test_max_drawdown_is_non_positive_and_known(self):
        # Equity rises to 100 then falls to 80 -> a clean 20% drawdown.
        eq = pd.DataFrame(
            {"equity": [100.0, 110.0, 100.0, 80.0, 90.0], "cash": 0.0},
            index=pd.date_range("2021-01-01", periods=5),
        )
        pa = PerformanceAnalytics(eq, pd.DataFrame())
        # peak 110 -> trough 80 == -27.27%
        assert abs(pa.max_drawdown() - (80.0 / 110.0 - 1.0)) < 1e-9
        assert pa.max_drawdown() <= 0.0

    def test_drawdown_duration_counts_underwater_bars(self):
        # Up, then three bars below the prior peak, then recover.
        eq = pd.DataFrame(
            {"equity": [100.0, 120.0, 110.0, 105.0, 108.0, 125.0], "cash": 0.0},
            index=pd.date_range("2021-01-01", periods=6),
        )
        pa = PerformanceAnalytics(eq, pd.DataFrame())
        assert pa.max_drawdown_duration() == 3


# --- execution cost decomposition -----------------------------------------


class TestExecutionCosts:
    def _two_bar_handler(self, open_next=100.0):
        df = pd.DataFrame(
            {
                "Open": [99.0, open_next],
                "High": [101.0, open_next * 1.05],
                "Low": [97.0, open_next * 0.95],
                "Close": [100.0, open_next],
                "Volume": [1000, 1000],
            },
            index=pd.date_range("2021-01-01", periods=2),
        )
        return _handler({"X": df})

    def test_commission_and_slippage_decomposed(self):
        data = self._two_bar_handler(open_next=100.0)
        data.fetch()
        data._current_idx = 1  # next open is bar index 1 == 100.0
        ex = SimulatedExecution(commission_pct=0.001, slippage_pct=0.01)
        order = OrderEvent(
            timestamp=datetime.now(),
            symbol="X",
            quantity=10,
            order_type=OrderType.MARKET,
            direction=Direction.BUY,
        )
        fill = ex.execute_order(order, data)
        # base price 100, buy slippage +1% -> 101 fill.
        assert abs(fill.price - 101.0) < 1e-9
        assert abs(fill.slippage - abs(101.0 - 100.0) * 10) < 1e-9  # 10
        assert abs(fill.commission - 101.0 * 10 * 0.001) < 1e-9  # 1.01

    def test_zero_costs_when_disabled(self):
        data = self._two_bar_handler()
        data.fetch()
        data._current_idx = 1
        ex = SimulatedExecution(commission_pct=0.0, slippage_pct=0.0)
        order = OrderEvent(
            timestamp=datetime.now(),
            symbol="X",
            quantity=5,
            order_type=OrderType.MARKET,
            direction=Direction.SELL,
        )
        fill = ex.execute_order(order, data)
        assert fill.commission == 0.0
        assert fill.slippage == 0.0
        assert fill.price == 100.0

    def test_market_order_unfilled_on_last_bar(self):
        data = self._two_bar_handler()
        data.fetch()
        data._current_idx = 2  # no next bar -> get_next_open returns 0.0
        ex = SimulatedExecution()
        order = OrderEvent(
            timestamp=datetime.now(),
            symbol="X",
            quantity=5,
            order_type=OrderType.MARKET,
            direction=Direction.BUY,
        )
        assert ex.execute_order(order, data) is None

    def test_stop_order_applies_slippage_on_trigger(self):
        # Bar low dips to 97; a sell-stop at 98 triggers, fills with slippage.
        data = self._two_bar_handler()
        data.fetch()
        data._current_idx = 1  # current bar is index 0 (low 97)
        ex = SimulatedExecution(slippage_pct=0.01, commission_pct=0.0)
        stop = OrderEvent(
            timestamp=datetime.now(),
            symbol="X",
            quantity=10,
            order_type=OrderType.STOP,
            direction=Direction.SELL,
            limit_price=98.0,
        )
        assert ex.execute_order(stop, data) is None  # queued
        fills = ex.check_pending(data, datetime.now())
        assert len(fills) == 1
        # trigger price min(open=99, level=98) = 98, sell slippage -1% -> 97.02
        assert abs(fills[0].price - 98.0 * 0.99) < 1e-9
        assert fills[0].slippage > 0.0


# --- MeanReversion / Momentum strategies ----------------------------------


class TestMeanReversion:
    def test_dip_triggers_buy(self):
        from src.strategy import MeanReversion

        close = [100.0] * 29 + [90.0]  # sharp dip -> z << -2
        h = _handler({"X": _ohlcv(close)})
        h._current_idx = len(close)
        sig = MeanReversion(lookback=20, entry_z=2.0).calculate_signals(
            MarketEvent(datetime.now(), "X"), h
        )
        assert sig is not None and sig.direction == Direction.BUY

    def test_flat_series_no_signal(self):
        from src.strategy import MeanReversion

        h = _handler({"X": _ohlcv([100.0] * 30)})
        h._current_idx = 30
        # std ~ 0 -> guarded, returns None
        assert (
            MeanReversion(lookback=20).calculate_signals(MarketEvent(datetime.now(), "X"), h)
            is None
        )

    def test_insufficient_history_no_signal(self):
        from src.strategy import MeanReversion

        h = _handler({"X": _ohlcv([100.0, 101.0, 99.0, 100.5, 100.0])})
        h._current_idx = 5
        assert (
            MeanReversion(lookback=20).calculate_signals(MarketEvent(datetime.now(), "X"), h)
            is None
        )


class TestMomentum:
    def test_strong_uptrend_buys(self):
        from src.strategy import MomentumStrategy

        close = list(100 * np.cumprod(1 + np.full(80, 0.01)))
        h = _handler({"X": _ohlcv(close)})
        h._current_idx = len(close)
        sig = MomentumStrategy(lookback=60).calculate_signals(MarketEvent(datetime.now(), "X"), h)
        assert sig is not None and sig.direction == Direction.BUY

    def test_strong_downtrend_sells(self):
        from src.strategy import MomentumStrategy

        close = list(100 * np.cumprod(1 + np.full(80, -0.01)))
        h = _handler({"X": _ohlcv(close)})
        h._current_idx = len(close)
        sig = MomentumStrategy(lookback=60).calculate_signals(MarketEvent(datetime.now(), "X"), h)
        assert sig is not None and sig.direction == Direction.SELL

    def test_flat_within_threshold_no_signal(self):
        from src.strategy import MomentumStrategy

        h = _handler({"X": _ohlcv([100.0] * 80)})
        h._current_idx = 80
        assert (
            MomentumStrategy(lookback=60).calculate_signals(MarketEvent(datetime.now(), "X"), h)
            is None
        )


# --- OptimizationRebalanceStrategy: every objective + failure path --------


class TestOptimizationObjectives:
    def _handler3(self, seed=0):
        n = 150
        rng = np.random.default_rng(seed)
        frames = {}
        for i, t in enumerate(["AAA", "BBB", "CCC"]):
            rets = rng.normal(0.0005 + i * 0.0002, 0.012, n)
            frames[t] = _ohlcv(list(100 * np.cumprod(1 + rets)))
        return _handler(frames)

    def test_each_objective_returns_long_only_simplex(self):
        from src.strategy import OptimizationRebalanceStrategy

        for objective in ("sharpe", "min_vol", "min_cvar", "risk_parity"):
            h = self._handler3()
            h.fetch()
            h._current_idx = 80
            strat = OptimizationRebalanceStrategy(
                ["AAA", "BBB", "CCC"], lookback=60, objective=objective
            )
            targets = strat._compute_targets(h)
            assert targets is not None, objective
            assert abs(sum(targets.values()) - 1.0) < 1e-6, objective
            assert all(w >= -1e-9 for w in targets.values()), objective

    def test_unknown_objective_falls_back_to_sharpe(self):
        from src.strategy import OptimizationRebalanceStrategy

        h = self._handler3()
        h.fetch()
        h._current_idx = 80
        bogus = OptimizationRebalanceStrategy(
            ["AAA", "BBB", "CCC"], lookback=60, objective="not_a_real_objective"
        )
        sharpe = OptimizationRebalanceStrategy(["AAA", "BBB", "CCC"], lookback=60)
        t_bogus = bogus._compute_targets(h)
        t_sharpe = sharpe._compute_targets(h)
        assert t_bogus is not None
        for k in t_sharpe:
            assert abs(t_bogus[k] - t_sharpe[k]) < 1e-9

    def test_insufficient_history_returns_none(self):
        from src.strategy import OptimizationRebalanceStrategy

        h = self._handler3()
        h.fetch()
        h._current_idx = 10  # < lookback
        strat = OptimizationRebalanceStrategy(["AAA", "BBB", "CCC"], lookback=60)
        assert strat._compute_targets(h) is None

    def test_min_cvar_full_backtest_is_long_only(self):
        from src.strategy import OptimizationRebalanceStrategy

        h = self._handler3()
        portfolio = Portfolio(initial_capital=100_000)
        strat = OptimizationRebalanceStrategy(
            ["AAA", "BBB", "CCC"], lookback=60, rebalance_freq=21, objective="min_cvar"
        )
        analytics = Backtest(h, strat, portfolio, SimulatedExecution()).run()
        assert len(analytics.equity) > 60
        assert len(portfolio.trade_log) > 0
        assert all(q >= 0 for q in portfolio.positions.values())
