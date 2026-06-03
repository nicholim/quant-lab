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


# --- Min CDaR (LP) ---


def _equal_weight_cdar(opt, confidence=0.95):
    return opt.portfolio_cdar(np.array([0.25] * 4), confidence)


class TestMinCDaR:
    def test_weights_sum_to_one(self, opt):
        r = opt.optimize_min_cdar(confidence=0.95)
        assert abs(r.weights.sum() - 1.0) < 1e-6

    def test_long_only_non_negative(self, opt):
        r = opt.optimize_min_cdar()
        assert all(w >= -1e-6 for w in r.weights)

    def test_within_bounds(self, opt):
        r = opt.optimize_min_cdar(max_weights={"C": 0.2})
        c_idx = opt.tickers.index("C")
        assert r.weights[c_idx] <= 0.2 + TOL

    def test_respects_group_cap(self, opt):
        r = opt.optimize_min_cdar(groups={"g": (["A", "C"], 0.0, 0.4)})
        a, c = opt.tickers.index("A"), opt.tickers.index("C")
        assert r.weights[a] + r.weights[c] <= 0.4 + TOL

    def test_cdar_leq_equal_weight(self, opt):
        """The minimized CDaR is no worse than equal-weight's CDaR."""
        r = opt.optimize_min_cdar(confidence=0.95)
        opt_cdar = opt.portfolio_cdar(r.weights, 0.95)
        assert opt_cdar <= _equal_weight_cdar(opt) + TOL

    def test_cdar_value_sane_vs_max_drawdown(self, opt):
        """CDaR (mean of worst-tail drawdowns) >= 0 and <= equal-weight max DD."""
        r = opt.optimize_min_cdar(confidence=0.95)
        port = opt.returns.values @ r.weights
        cum = np.cumsum(port)
        max_dd = float((np.maximum.accumulate(cum) - cum).max())
        cdar = opt.portfolio_cdar(r.weights, 0.95)
        assert cdar >= -TOL
        # equal-weight arithmetic max drawdown bounds the optimized tail-mean DD
        eq = np.array([0.25] * 4)
        eq_cum = np.cumsum(opt.returns.values @ eq)
        eq_max_dd = float((np.maximum.accumulate(eq_cum) - eq_cum).max())
        assert cdar <= eq_max_dd + TOL
        assert cdar <= max_dd + TOL

    def test_result_objective_tag(self, opt):
        r = opt.optimize_min_cdar()
        assert r.objective == "min_cdar"


# --- Transaction-cost-aware rebalancing ---


class TestTransactionCost:
    def test_zero_cost_equals_baseline(self, opt):
        """current_weights with transaction_cost=0.0 == the cost-free optimum."""
        base = opt.optimize_sharpe()
        priced = opt.optimize_sharpe(current_weights=np.array([0.25] * 4), transaction_cost=0.0)
        np.testing.assert_allclose(priced.weights, base.weights, atol=1e-6)

    def test_default_path_unchanged(self, opt):
        """No txn kwargs => byte-identical to plain optimize_sharpe (regression)."""
        a = opt.optimize_sharpe()
        b = opt.optimize_sharpe()
        np.testing.assert_allclose(a.weights, b.weights, atol=1e-12)

    def test_nonzero_cost_reduces_turnover(self, opt):
        """A nonzero cost from a current_weights far from the optimum cuts turnover."""
        base = opt.optimize_sharpe()
        prior = np.array([0.25] * 4)  # equal-weight starting allocation
        base_turnover = float(np.abs(base.weights - prior).sum())
        priced = opt.optimize_sharpe(current_weights=prior, transaction_cost=5.0)
        priced_turnover = float(np.abs(priced.weights - prior).sum())
        assert priced_turnover < base_turnover - 1e-3
        assert abs(priced.weights.sum() - 1.0) < 1e-6

    def test_dict_current_weights_accepted(self, opt):
        prior = {"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25}
        r = opt.optimize_min_volatility(current_weights=prior, transaction_cost=1.0)
        assert abs(r.weights.sum() - 1.0) < 1e-6

    def test_per_asset_cost_array(self, opt):
        prior = np.array([0.25] * 4)
        r = opt.optimize_sortino(
            current_weights=prior, transaction_cost=np.array([2.0, 0.0, 2.0, 0.0])
        )
        assert abs(r.weights.sum() - 1.0) < 1e-6

    def test_unknown_ticker_in_current_weights_raises(self, opt):
        with pytest.raises(ValueError):
            opt.optimize_sharpe(current_weights={"ZZZ": 0.5}, transaction_cost=1.0)

    def test_wrong_length_current_weights_raises(self, opt):
        with pytest.raises(ValueError):
            opt.optimize_sharpe(current_weights=np.array([0.5, 0.5]), transaction_cost=1.0)


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
