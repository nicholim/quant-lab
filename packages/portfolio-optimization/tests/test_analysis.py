"""Tests for the analysis orchestration layer (run_analysis / print_report).

All network access (price + benchmark downloads) is monkeypatched with
deterministic synthetic data, so the workflow runs offline and reproducibly.
``run_analysis`` is the I/O-free entry point exercised here end-to-end.
"""

import numpy as np
import pandas as pd
import pytest

from portfolio_optimization_engine import analysis
from portfolio_optimization_engine.analysis import (
    _selected_objectives,
    compute_portfolio_returns,
    print_report,
    run_analysis,
)
from portfolio_optimization_engine.config import AnalysisConfig


@pytest.fixture
def synthetic_prices():
    """Deterministic geometric-random-walk close prices for 4 tickers."""
    rng = np.random.default_rng(123)
    tickers = ["AAA", "BBB", "CCC", "DDD"]
    n_days = 400
    idx = pd.date_range("2021-01-01", periods=n_days, freq="B")
    rets = rng.normal(
        [0.0006, 0.0004, 0.0008, 0.0003], [0.01, 0.008, 0.015, 0.006], size=(n_days, 4)
    )
    prices = 100.0 * np.cumprod(1.0 + rets, axis=0)
    return pd.DataFrame(prices, columns=tickers, index=idx), tickers


@pytest.fixture
def patched_downloads(monkeypatch, synthetic_prices):
    """Patch both price + benchmark downloads to return synthetic data."""
    prices, tickers = synthetic_prices

    def fake_download_close_prices(tk, start, end, auto_adjust=True, use_cache=True, offline=False):
        return prices[list(tk)].copy()

    # _fetch_benchmark routes through data_cache.fetch_close_prices; return a
    # single-column frame (the shape yfinance's ["Close"] gives for one ticker).
    bench_idx = prices.index
    bench = pd.DataFrame(
        {
            "Close": 100.0
            * np.cumprod(1.0 + np.random.default_rng(7).normal(0.0005, 0.011, len(bench_idx)))
        },
        index=bench_idx,
    )

    def fake_fetch_close_prices(ticker, start, end, auto_adjust=True, offline=False):
        return bench.copy()

    # optimizer.fetch_data imports download_close_prices into its own module
    from portfolio_optimization_engine import optimizer as opt_mod

    monkeypatch.setattr(opt_mod, "download_close_prices", fake_download_close_prices)
    # analysis._fetch_benchmark imports fetch_close_prices into its own module
    monkeypatch.setattr(analysis, "fetch_close_prices", fake_fetch_close_prices)
    return tickers


# --- objective selection ---


class TestSelectedObjectives:
    def test_both(self):
        assert _selected_objectives("both") == ["max_sharpe", "min_vol"]

    def test_all_covers_every_method(self):
        sel = _selected_objectives("all")
        assert set(sel) == set(analysis._OBJECTIVE_METHODS)

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("sharpe", ["max_sharpe"]),
            ("min_vol", ["min_vol"]),
            ("risk_parity", ["risk_parity"]),
            ("sortino", ["sortino"]),
            ("min_cvar", ["min_cvar"]),
        ],
    )
    def test_single_objectives(self, name, expected):
        assert _selected_objectives(name) == expected


# --- compute_portfolio_returns ---


def test_compute_portfolio_returns_matches_dot():
    rng = np.random.default_rng(0)
    rets = pd.DataFrame(rng.normal(0, 0.01, (50, 3)), columns=["X", "Y", "Z"])
    w = np.array([0.2, 0.5, 0.3])
    series = compute_portfolio_returns(rets, w)
    assert np.allclose(series.values, rets.values @ w)


# --- run_analysis end-to-end ---


class TestRunAnalysis:
    def test_run_both_objectives(self, patched_downloads):
        cfg = AnalysisConfig(
            tickers=patched_downloads,
            start_date="2021-01-01",
            end_date="2022-06-01",
            objective="both",
            num_portfolios=50,
            monte_carlo_sims=200,
            monte_carlo_days=60,
            random_state=42,
        )
        out = run_analysis(cfg)
        assert set(out["results"]) == {"max_sharpe", "min_vol"}
        assert out["primary"] == "max_sharpe"
        # frontier shape
        assert len(out["frontier"]) == 50
        # metrics present for every result
        assert set(out["metrics"]) == set(out["results"])

    def test_reproducible_with_seed(self, patched_downloads):
        cfg = AnalysisConfig(
            tickers=patched_downloads,
            objective="sharpe",
            num_portfolios=30,
            monte_carlo_sims=100,
            monte_carlo_days=30,
            random_state=99,
        )
        a = run_analysis(cfg)
        b = run_analysis(cfg)
        assert a["frontier"].equals(b["frontier"])
        assert a["mc_summary"]["var_95"] == b["mc_summary"]["var_95"]
        np.testing.assert_allclose(
            a["results"]["max_sharpe"].weights, b["results"]["max_sharpe"].weights
        )

    def test_benchmark_populates_beta(self, patched_downloads):
        cfg = AnalysisConfig(
            tickers=patched_downloads,
            objective="sharpe",
            num_portfolios=20,
            monte_carlo_sims=50,
            monte_carlo_days=20,
            benchmark="SPY",
            random_state=1,
        )
        out = run_analysis(cfg)
        m = out["metrics"]["max_sharpe"]
        assert m.beta is not None
        assert m.alpha is not None

    def test_no_benchmark_leaves_beta_none(self, patched_downloads):
        cfg = AnalysisConfig(
            tickers=patched_downloads,
            objective="min_vol",
            num_portfolios=20,
            monte_carlo_sims=50,
            monte_carlo_days=20,
            random_state=1,
        )
        out = run_analysis(cfg)
        assert out["metrics"]["min_vol"].beta is None

    def test_primary_falls_back_when_no_sharpe(self, patched_downloads):
        cfg = AnalysisConfig(
            tickers=patched_downloads,
            objective="min_vol",
            num_portfolios=20,
            monte_carlo_sims=50,
            monte_carlo_days=20,
            random_state=1,
        )
        out = run_analysis(cfg)
        assert out["primary"] == "min_vol"


# --- offline end-to-end (bundled fixture, no monkeypatch) ---


def test_run_analysis_offline_uses_bundled_fixture():
    """offline=True drives the whole workflow off the bundled CSV, no network."""
    cfg = AnalysisConfig(
        tickers=["AAPL", "MSFT", "GOOGL"],
        objective="sharpe",
        num_portfolios=20,
        monte_carlo_sims=50,
        monte_carlo_days=20,
        benchmark="SPY",
        offline=True,
        random_state=5,
    )
    out = run_analysis(cfg)
    assert "max_sharpe" in out["results"]
    # benchmark also served offline -> beta/alpha populated
    assert out["metrics"]["max_sharpe"].beta is not None


# --- print_report (smoke: must not raise; output not asserted) ---


def test_print_report_smoke(patched_downloads, capsys):
    cfg = AnalysisConfig(
        tickers=patched_downloads,
        objective="sharpe",
        num_portfolios=20,
        monte_carlo_sims=50,
        monte_carlo_days=20,
        benchmark="SPY",
        random_state=3,
    )
    out = run_analysis(cfg)
    print_report(out, cfg)
    captured = capsys.readouterr().out
    assert "Portfolio Optimization Engine" in captured
    assert "MONTE CARLO" in captured
