"""Smoke tests for the plotting helpers.

These run headless on the Agg backend and assert only that each plot builds and
saves without raising (and patch ``plt.show`` so nothing blocks). They exercise
the real figure-construction code paths but do not assert on pixels.
"""

import matplotlib

matplotlib.use("Agg")  # noqa: E402 -- must precede pyplot import

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from portfolio_optimization_engine import visualization as viz  # noqa: E402
from portfolio_optimization_engine.optimizer import PortfolioResult  # noqa: E402


@pytest.fixture(autouse=True)
def _no_show_no_leak(monkeypatch):
    monkeypatch.setattr(plt, "show", lambda *a, **k: None)
    yield
    plt.close("all")


@pytest.fixture
def frontier_df():
    rng = np.random.default_rng(0)
    n = 200
    vol = rng.uniform(0.1, 0.4, n)
    ret = rng.uniform(0.02, 0.20, n)
    return pd.DataFrame(
        {
            "volatility": vol,
            "return": ret,
            "sharpe": (ret - 0.02) / vol,
        }
    )


@pytest.fixture
def returns_df():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2021-01-01", periods=250)
    return pd.DataFrame(rng.normal(0.0005, 0.01, (250, 3)), columns=["A", "B", "C"], index=idx)


def _result(weights):
    w = np.asarray(weights, dtype=float)
    return PortfolioResult(
        weights=w,
        expected_return=0.12,
        volatility=0.18,
        sharpe_ratio=0.55,
        sortino_ratio=0.8,
        cvar=0.03,
        objective="sharpe",
    )


def test_efficient_frontier_minimal(frontier_df, tmp_path):
    out = tmp_path / "ef.png"
    viz.plot_efficient_frontier(frontier_df, save_path=str(out))
    assert out.exists()


def test_efficient_frontier_with_markers(frontier_df):
    viz.plot_efficient_frontier(
        frontier_df,
        optimal_sharpe=_result([0.5, 0.3, 0.2]),
        optimal_min_vol=_result([0.2, 0.5, 0.3]),
        extra_portfolios={"risk_parity": _result([0.4, 0.4, 0.2])},
    )


def test_correlation_matrix(returns_df, tmp_path):
    out = tmp_path / "corr.png"
    viz.plot_correlation_matrix(returns_df, save_path=str(out))
    assert out.exists()


def test_portfolio_weights_pie(tmp_path):
    out = tmp_path / "pie.png"
    viz.plot_portfolio_weights(_result([0.5, 0.3, 0.2]), ["A", "B", "C"], save_path=str(out))
    assert out.exists()


def test_cumulative_returns(returns_df):
    viz.plot_cumulative_returns(returns_df)


def test_drawdown(returns_df):
    viz.plot_drawdown(returns_df["A"])
