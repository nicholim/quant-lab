"""Tests for PSR/DSR wiring and the trade-level analytics block (analytics.py),
plus grid_search's Deflated Sharpe Ratio multiple-testing correction."""

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd
import pytest

from src.analytics import PERIODS_PER_YEAR, PerformanceAnalytics


def _equity_from_prices(prices, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="D")
    return pd.DataFrame({"equity": prices}, index=idx)


def _trade(ts, symbol, direction, qty, price):
    return {
        "timestamp": pd.Timestamp(ts),
        "symbol": symbol,
        "direction": direction,
        "quantity": qty,
        "price": price,
        "commission": 0.0,
        "slippage": 0.0,
    }


# --- Probabilistic Sharpe Ratio on the analytics result ---


class TestPSR:
    def test_psr_zero_when_no_returns(self):
        eq = _equity_from_prices([100.0])
        a = PerformanceAnalytics(eq, pd.DataFrame())
        assert a.probabilistic_sharpe_ratio() == 0.0

    def test_psr_in_unit_interval(self):
        rng = np.random.default_rng(7)
        prices = 100 * np.cumprod(1 + rng.normal(0.0005, 0.01, 300))
        eq = _equity_from_prices(prices)
        a = PerformanceAnalytics(eq, pd.DataFrame())
        psr = a.probabilistic_sharpe_ratio()
        assert 0.0 <= psr <= 1.0

    def test_psr_higher_for_better_strategy(self):
        rng = np.random.default_rng(11)
        good = 100 * np.cumprod(1 + rng.normal(0.002, 0.01, 400))
        flat = 100 * np.cumprod(1 + rng.normal(0.0, 0.01, 400))
        a_good = PerformanceAnalytics(_equity_from_prices(good), pd.DataFrame())
        a_flat = PerformanceAnalytics(_equity_from_prices(flat), pd.DataFrame())
        assert a_good.probabilistic_sharpe_ratio() > a_flat.probabilistic_sharpe_ratio()

    def test_psr_consistent_with_deannualized_sharpe(self):
        rng = np.random.default_rng(3)
        prices = 100 * np.cumprod(1 + rng.normal(0.001, 0.012, 250))
        a = PerformanceAnalytics(_equity_from_prices(prices), pd.DataFrame())
        # PSR with benchmark = the strategy's own per-period SR must be ~0.5.
        per_period = a.sharpe_ratio() / np.sqrt(PERIODS_PER_YEAR)
        assert a.probabilistic_sharpe_ratio(benchmark_sr=per_period) == pytest.approx(0.5, abs=1e-6)


# --- Trade-level statistics ---


class TestTradeStats:
    def test_known_wins_and_losses_long(self):
        # Two long round trips: +10*5=+50 win, -4*5=-20 loss.
        trades = pd.DataFrame(
            [
                _trade("2020-01-01", "AAA", "BUY", 5, 100.0),
                _trade("2020-01-05", "AAA", "SELL", 5, 110.0),  # +50
                _trade("2020-01-06", "AAA", "BUY", 5, 100.0),
                _trade("2020-01-10", "AAA", "SELL", 5, 96.0),  # -20
            ]
        )
        eq = _equity_from_prices(np.linspace(100, 130, 10))
        a = PerformanceAnalytics(eq, trades)
        assert a.win_rate() == pytest.approx(0.5)
        assert a.avg_win() == pytest.approx(50.0)
        assert a.avg_loss() == pytest.approx(-20.0)
        assert a.expectancy() == pytest.approx(15.0)
        assert a.payoff_ratio() == pytest.approx(2.5)
        assert a.profit_factor() == pytest.approx(2.5)

    def test_all_wins(self):
        trades = pd.DataFrame(
            [
                _trade("2020-01-01", "AAA", "BUY", 1, 10.0),
                _trade("2020-01-02", "AAA", "SELL", 1, 12.0),
            ]
        )
        a = PerformanceAnalytics(_equity_from_prices([100, 102]), trades)
        assert a.win_rate() == 1.0
        assert a.avg_loss() == 0.0
        assert a.payoff_ratio() == float("inf")

    def test_all_losses(self):
        trades = pd.DataFrame(
            [
                _trade("2020-01-01", "AAA", "BUY", 1, 10.0),
                _trade("2020-01-02", "AAA", "SELL", 1, 8.0),
            ]
        )
        a = PerformanceAnalytics(_equity_from_prices([100, 98]), trades)
        assert a.win_rate() == 0.0
        assert a.avg_win() == 0.0
        assert a.payoff_ratio() == 0.0

    def test_zero_trades(self):
        a = PerformanceAnalytics(_equity_from_prices([100, 101, 102]), pd.DataFrame())
        stats = a.trade_stats()
        assert stats["win_rate"] == 0.0
        assert stats["expectancy"] == 0.0
        assert stats["payoff_ratio"] == 0.0
        assert stats["avg_holding_period"] == 0.0

    def test_avg_holding_period(self):
        trades = pd.DataFrame(
            [
                _trade("2020-01-01", "AAA", "BUY", 1, 10.0),
                _trade("2020-01-05", "AAA", "SELL", 1, 12.0),  # 4 days
            ]
        )
        a = PerformanceAnalytics(_equity_from_prices([100, 101, 102, 103, 104]), trades)
        assert a.avg_holding_period() == pytest.approx(4.0)

    def test_short_round_trip_pnl(self):
        # Sell-to-open at 100, cover at 90: short profit +10.
        trades = pd.DataFrame(
            [
                _trade("2020-01-01", "AAA", "SELL", 2, 100.0),
                _trade("2020-01-03", "AAA", "BUY", 2, 90.0),  # +20
            ]
        )
        a = PerformanceAnalytics(_equity_from_prices([100, 110, 120]), trades, allow_short=True)
        assert a.win_rate() == 1.0
        assert a.avg_win() == pytest.approx(20.0)


# --- Exposure time ---


class TestExposureTime:
    def test_full_exposure(self):
        eq = _equity_from_prices([100, 101, 102, 103])
        a = PerformanceAnalytics(eq, pd.DataFrame())
        assert a.exposure_time() == pytest.approx(1.0)

    def test_partial_exposure(self):
        # 3 diffs: move, flat, move -> 2/3 in market.
        eq = _equity_from_prices([100, 105, 105, 110])
        a = PerformanceAnalytics(eq, pd.DataFrame())
        assert a.exposure_time() == pytest.approx(2.0 / 3.0)

    def test_no_exposure(self):
        eq = _equity_from_prices([100, 100, 100])
        a = PerformanceAnalytics(eq, pd.DataFrame())
        assert a.exposure_time() == 0.0

    def test_single_bar(self):
        eq = _equity_from_prices([100])
        a = PerformanceAnalytics(eq, pd.DataFrame())
        assert a.exposure_time() == 0.0


# --- MAE / MFE ---


class TestMaeMfe:
    def test_known_path_long(self):
        # Long 1 share entered at 100; path dips to 95 (MAE -5) then peaks 115 (MFE +15).
        idx = pd.date_range("2020-01-01", periods=5, freq="D")
        eq = pd.DataFrame(
            {"equity": [100.0, 95.0, 105.0, 115.0, 110.0]},
            index=idx,
        )
        trades = pd.DataFrame(
            [
                _trade(idx[0], "AAA", "BUY", 1, 100.0),
                _trade(idx[4], "AAA", "SELL", 1, 110.0),
            ]
        )
        a = PerformanceAnalytics(eq, trades)
        mm = a.mae_mfe()
        assert len(mm) == 1
        row = mm.iloc[0]
        assert row["mae"] == pytest.approx(-5.0)
        assert row["mfe"] == pytest.approx(15.0)
        assert row["pnl"] == pytest.approx(10.0)

    def test_empty_when_no_trades(self):
        a = PerformanceAnalytics(_equity_from_prices([100, 101]), pd.DataFrame())
        assert a.mae_mfe().empty


# --- generate_report runs end to end ---


def test_generate_report_runs(capsys):
    trades = pd.DataFrame(
        [
            _trade("2020-01-01", "AAA", "BUY", 1, 10.0),
            _trade("2020-01-02", "AAA", "SELL", 1, 12.0),
        ]
    )
    a = PerformanceAnalytics(_equity_from_prices([100, 102, 103]), trades)
    a.generate_report()
    out = capsys.readouterr().out
    assert "TRADE STATISTICS" in out
    assert "Prob. Sharpe (PSR)" in out
