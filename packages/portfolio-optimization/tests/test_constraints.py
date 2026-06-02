"""Tests for new objectives and flexible constraints.

Uses a synthetic optimizer with injected returns (no network) so the math is
deterministic and the suite runs offline.
"""

import numpy as np
import pandas as pd
import pytest

from portfolio_optimization_engine.optimizer import PortfolioOptimizer


@pytest.fixture
def opt():
    """Optimizer with deterministic synthetic daily returns for 4 assets."""
    rng = np.random.default_rng(42)
    tickers = ["A", "B", "C", "D"]
    n_days = 750
    # distinct mean/vol per asset so objectives have non-degenerate solutions
    means = np.array([0.0008, 0.0005, 0.0011, 0.0003])
    vols = np.array([0.012, 0.008, 0.020, 0.006])
    data = rng.normal(means, vols, size=(n_days, 4))
    returns = pd.DataFrame(data, columns=tickers, index=pd.date_range("2021-01-01", periods=n_days))

    o = PortfolioOptimizer(tickers, "2021-01-01", "2023-12-31")
    o.returns = returns
    o.mean_returns = returns.mean() * 252
    o.cov_matrix = returns.cov() * 252
    return o


TOL = 1e-4


# --- Risk parity ---


class TestRiskParity:
    def test_weights_sum_to_one(self, opt):
        r = opt.optimize_risk_parity()
        assert abs(r.weights.sum() - 1.0) < 1e-6

    def test_equal_risk_contributions(self, opt):
        r = opt.optimize_risk_parity()
        rc = opt.portfolio_risk_contributions(r.weights)
        assert rc.max() - rc.min() < 1e-3

    def test_rejects_shorting(self, opt):
        with pytest.raises(ValueError):
            opt.optimize_risk_parity(allow_short=True)


# --- Sortino ---


class TestSortino:
    def test_weights_sum_to_one(self, opt):
        r = opt.optimize_sortino()
        assert abs(r.weights.sum() - 1.0) < 1e-6

    def test_beats_equal_weight(self, opt):
        r = opt.optimize_sortino()
        eq = np.array([0.25] * 4)
        assert r.sortino_ratio >= opt.portfolio_sortino(eq) - 1e-6

    def test_result_carries_sortino_and_objective(self, opt):
        r = opt.optimize_sortino()
        assert r.objective == "sortino"
        assert r.sortino_ratio is not None


# --- Min CVaR (LP) ---


class TestMinCVaR:
    def test_weights_sum_to_one(self, opt):
        r = opt.optimize_min_cvar(confidence=0.95)
        assert abs(r.weights.sum() - 1.0) < 1e-6

    def test_leq_equal_weight_cvar(self, opt):
        r = opt.optimize_min_cvar(confidence=0.95)
        eq = np.array([0.25] * 4)
        assert r.cvar <= opt.portfolio_cvar(eq, 0.95) + TOL

    def test_long_only_non_negative(self, opt):
        r = opt.optimize_min_cvar()
        assert all(w >= -1e-6 for w in r.weights)


# --- Target-based ---


class TestTargets:
    def test_max_return_respects_target_vol(self, opt):
        target = 0.18
        r = opt.optimize_max_return_target_vol(target_vol=target)
        assert r.volatility <= target + TOL

    def test_min_vol_meets_target_return(self, opt):
        target = 0.08
        r = opt.optimize_min_vol_target_return(target_return=target)
        assert r.expected_return >= target - TOL

    def test_infeasible_target_return_raises(self, opt):
        too_high = float(np.asarray(opt.mean_returns).max()) + 1.0
        with pytest.raises(ValueError):
            opt.optimize_min_vol_target_return(target_return=too_high)


# --- Flexible constraints ---


class TestConstraints:
    def test_max_weight_cap(self, opt):
        r = opt.optimize_sharpe(max_weights={"A": 0.3})
        a_idx = opt.tickers.index("A")
        assert r.weights[a_idx] <= 0.3 + TOL

    def test_min_weight_floor(self, opt):
        r = opt.optimize_sharpe(min_weights={"D": 0.2})
        d_idx = opt.tickers.index("D")
        assert r.weights[d_idx] >= 0.2 - TOL

    def test_group_cap(self, opt):
        r = opt.optimize_sharpe(groups={"g": (["A", "C"], 0.0, 0.5)})
        a, c = opt.tickers.index("A"), opt.tickers.index("C")
        assert r.weights[a] + r.weights[c] <= 0.5 + TOL

    def test_group_floor(self, opt):
        r = opt.optimize_min_volatility(groups={"g": (["A", "C"], 0.4, 1.0)})
        a, c = opt.tickers.index("A"), opt.tickers.index("C")
        assert r.weights[a] + r.weights[c] >= 0.4 - TOL

    def test_shorting_allows_negative_weight(self, opt):
        # force B to be shortable down to -0.5; weights still sum to 1
        r = opt.optimize_sharpe(min_weights={"B": -0.5}, allow_short=True)
        assert abs(r.weights.sum() - 1.0) < 1e-6
        assert r.weights.min() >= -0.5 - TOL

    def test_unknown_ticker_in_bound_raises(self, opt):
        with pytest.raises(ValueError):
            opt.optimize_sharpe(max_weights={"ZZZ": 0.3})

    def test_unknown_ticker_in_group_raises(self, opt):
        with pytest.raises(ValueError):
            opt.optimize_sharpe(groups={"g": (["ZZZ"], 0.0, 0.5)})

    def test_min_cvar_respects_group_cap(self, opt):
        r = opt.optimize_min_cvar(groups={"g": (["A", "C"], 0.0, 0.4)})
        a, c = opt.tickers.index("A"), opt.tickers.index("C")
        assert r.weights[a] + r.weights[c] <= 0.4 + TOL


# --- Backward-compatible PortfolioResult ---


class TestResultFields:
    def test_sharpe_result_populated(self, opt):
        r = opt.optimize_sharpe()
        assert r.objective == "sharpe"
        assert r.sortino_ratio is not None
        assert r.cvar is not None
