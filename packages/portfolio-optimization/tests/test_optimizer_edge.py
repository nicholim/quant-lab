"""Edge cases for PortfolioOptimizer + metrics parity + the public API contract.

These tests inject synthetic returns directly (no network), mirroring exactly how
the backtesting-framework's OptimizationRebalanceStrategy drives the optimizer:
set ``.returns`` / ``.mean_returns`` / ``.cov_matrix`` and call an ``optimize_*``
method. Regressions in that contract should fail here.
"""

import numpy as np
import pandas as pd
import pytest

from portfolio_optimization_engine import metrics as M
from portfolio_optimization_engine.optimizer import PortfolioOptimizer


def make_optimizer(tickers, returns_df, risk_free_rate=0.02):
    """Build an optimizer with injected returns (the backtester's pattern)."""
    o = PortfolioOptimizer(tickers, "2021-01-01", "2022-01-01", risk_free_rate=risk_free_rate)
    o.returns = returns_df
    o.mean_returns = returns_df.mean() * 252
    o.cov_matrix = returns_df.cov() * 252
    return o


@pytest.fixture
def two_asset():
    rng = np.random.default_rng(7)
    tickers = ["A", "B"]
    data = rng.normal([0.0007, 0.0003], [0.011, 0.007], size=(500, 2))
    df = pd.DataFrame(data, columns=tickers, index=pd.date_range("2021-01-01", periods=500))
    return make_optimizer(tickers, df)


# --- Guard rails: ordering of fetch/calculate ---


class TestGuards:
    def test_calculate_returns_before_fetch_raises(self):
        o = PortfolioOptimizer(["A", "B"], "2021-01-01", "2022-01-01")
        with pytest.raises(ValueError, match="fetch_data"):
            o.calculate_returns()

    def test_solve_before_calculate_raises(self):
        o = PortfolioOptimizer(["A", "B"], "2021-01-01", "2022-01-01")
        with pytest.raises(ValueError, match="calculate_returns"):
            o.optimize_sharpe()

    def test_efficient_frontier_before_calculate_raises(self):
        o = PortfolioOptimizer(["A", "B"], "2021-01-01", "2022-01-01")
        with pytest.raises(ValueError, match="calculate_returns"):
            o.efficient_frontier(10)

    def test_min_cvar_before_calculate_raises(self):
        o = PortfolioOptimizer(["A", "B"], "2021-01-01", "2022-01-01")
        with pytest.raises(ValueError, match="calculate_returns"):
            o.optimize_min_cvar()


# --- Degenerate inputs ---


class TestDegenerate:
    def test_single_asset_weights_to_one(self):
        rng = np.random.default_rng(1)
        df = pd.DataFrame(
            {"ONLY": rng.normal(0.0005, 0.01, 300)}, index=pd.date_range("2021-01-01", periods=300)
        )
        o = make_optimizer(["ONLY"], df)
        r = o.optimize_min_volatility()
        assert r.weights.shape == (1,)
        assert r.weights[0] == pytest.approx(1.0, abs=1e-6)

    def test_single_asset_sharpe(self):
        rng = np.random.default_rng(2)
        df = pd.DataFrame(
            {"ONLY": rng.normal(0.0008, 0.012, 300)}, index=pd.date_range("2021-01-01", periods=300)
        )
        o = make_optimizer(["ONLY"], df)
        r = o.optimize_sharpe()
        assert r.weights[0] == pytest.approx(1.0, abs=1e-6)

    def test_single_asset_min_cvar(self):
        rng = np.random.default_rng(3)
        df = pd.DataFrame(
            {"ONLY": rng.normal(0.0005, 0.01, 300)}, index=pd.date_range("2021-01-01", periods=300)
        )
        o = make_optimizer(["ONLY"], df)
        r = o.optimize_min_cvar()
        assert r.weights[0] == pytest.approx(1.0, abs=1e-6)

    def test_perfectly_correlated_assets_min_vol(self):
        # B is a deterministic linear function of A -> covariance is singular.
        rng = np.random.default_rng(4)
        a = rng.normal(0.0006, 0.01, 400)
        df = pd.DataFrame({"A": a, "B": 1.5 * a}, index=pd.date_range("2021-01-01", periods=400))
        o = make_optimizer(["A", "B"], df)
        r = o.optimize_min_volatility()
        # solver must still return a valid budget-respecting allocation
        assert abs(r.weights.sum() - 1.0) < 1e-5
        assert all(w >= -1e-6 for w in r.weights)
        assert np.isfinite(r.volatility)

    def test_duplicate_asset_singular_cov_does_not_crash(self):
        rng = np.random.default_rng(5)
        a = rng.normal(0.0006, 0.01, 400)
        # identical columns -> rank-deficient covariance
        df = pd.DataFrame({"A": a, "B": a.copy()}, index=pd.date_range("2021-01-01", periods=400))
        o = make_optimizer(["A", "B"], df)
        r = o.optimize_min_volatility()
        assert abs(r.weights.sum() - 1.0) < 1e-5
        assert np.isfinite(r.volatility)


# --- Objective economics on a clean 2-asset world ---


class TestObjectiveEconomics:
    def test_min_vol_no_greater_than_any_single_asset(self, two_asset):
        r = two_asset.optimize_min_volatility()
        vol_a = two_asset.portfolio_volatility(np.array([1.0, 0.0]))
        vol_b = two_asset.portfolio_volatility(np.array([0.0, 1.0]))
        assert r.volatility <= max(vol_a, vol_b) + 1e-6

    def test_sharpe_at_least_min_vol_sharpe(self, two_asset):
        s = two_asset.optimize_sharpe()
        mv = two_asset.optimize_min_volatility()
        assert s.sharpe_ratio >= mv.sharpe_ratio - 1e-4

    def test_max_return_target_vol_binds(self, two_asset):
        # choose a target strictly between the min-vol floor and the highest
        # single-asset vol so the cap is both feasible AND binding.
        vol_min = two_asset.optimize_min_volatility().volatility
        vol_a = two_asset.portfolio_volatility(np.array([1.0, 0.0]))
        vol_b = two_asset.portfolio_volatility(np.array([0.0, 1.0]))
        target = 0.5 * (vol_min + max(vol_a, vol_b))
        r = two_asset.optimize_max_return_target_vol(target_vol=target)
        assert r.volatility <= target + 1e-4

    def test_target_return_long_only_cap_enforced(self, two_asset):
        max_mu = float(np.asarray(two_asset.mean_returns).max())
        with pytest.raises(ValueError, match="exceeds"):
            two_asset.optimize_min_vol_target_return(target_return=max_mu + 0.5)

    def test_target_return_feasible_meets_target(self, two_asset):
        # a target below the long-only ceiling must be met without error
        max_mu = float(np.asarray(two_asset.mean_returns).max())
        min_mu = float(np.asarray(two_asset.mean_returns).min())
        target = 0.5 * (min_mu + max_mu)
        r = two_asset.optimize_min_vol_target_return(target_return=target)
        assert r.expected_return >= target - 1e-3


# --- Constraint normalization helpers ---


class TestBoundNormalization:
    def test_scalar_bound_broadcasts(self, two_asset):
        arr = two_asset._normalize_bound(0.1, default=0.0)
        assert arr.tolist() == [0.1, 0.1]

    def test_list_bound_used_verbatim(self, two_asset):
        arr = two_asset._normalize_bound([0.1, 0.4], default=0.0)
        assert arr.tolist() == [0.1, 0.4]

    def test_wrong_length_array_raises(self, two_asset):
        with pytest.raises(ValueError, match="length"):
            two_asset._normalize_bound([0.1, 0.2, 0.3], default=0.0)

    def test_none_uses_default(self, two_asset):
        arr = two_asset._normalize_bound(None, default=0.25)
        assert arr.tolist() == [0.25, 0.25]


# --- Metrics parity: optimizer stats vs the shared metrics module ---


class TestMetricsParity:
    """The optimizer and the standalone metrics module must agree on the same
    return series (the shared source-of-truth contract with the backtester)."""

    def test_annualized_return_matches(self, two_asset):
        w = np.array([0.6, 0.4])
        port_daily = two_asset.returns.values @ w
        opt_ret = two_asset.portfolio_return(w)
        met_ret = M.annualized_return(port_daily, periods_per_year=252)
        assert opt_ret == pytest.approx(met_ret, rel=1e-9)

    def test_downside_deviation_matches_sortino_denominator(self, two_asset):
        # optimizer.downside_deviation uses MAR = rf; metrics.sortino uses a
        # per-period geometric rf, so they differ by construction. Assert the
        # optimizer's own Sortino is internally consistent instead.
        w = np.array([0.5, 0.5])
        dd = two_asset.downside_deviation(w)
        excess = two_asset.portfolio_return(w) - two_asset.risk_free_rate
        assert two_asset.portfolio_sortino(w) == pytest.approx(excess / max(dd, 1e-12), rel=1e-9)

    def test_cvar_is_tail_average(self, two_asset):
        w = np.array([0.5, 0.5])
        port_daily = two_asset.returns.values @ w
        losses = -port_daily
        var = np.quantile(losses, 0.95)
        expected = losses[losses >= var].mean()
        assert two_asset.portfolio_cvar(w, 0.95) == pytest.approx(expected, rel=1e-9)


# --- Backtester public-API contract ---


class TestBacktesterContract:
    """Mirror OptimizationRebalanceStrategy._compute_targets so a public-API
    break (renamed method, changed attrs, result shape) is caught here."""

    @pytest.mark.parametrize("objective", ["sharpe", "min_vol", "min_cvar", "risk_parity"])
    def test_injected_returns_drive_each_objective(self, objective):
        rng = np.random.default_rng(11)
        tickers = ["A", "B", "C"]
        returns = pd.DataFrame(
            rng.normal([0.0006, 0.0004, 0.0007], [0.01, 0.008, 0.012], size=(260, 3)),
            columns=tickers,
            index=pd.date_range("2021-01-01", periods=260),
        )
        opt = PortfolioOptimizer(tickers, "1900-01-01", "1900-01-02")
        opt.returns = returns
        opt.mean_returns = returns.mean() * 252
        opt.cov_matrix = returns.cov() * 252

        method = {
            "sharpe": opt.optimize_sharpe,
            "min_vol": opt.optimize_min_volatility,
            "min_cvar": opt.optimize_min_cvar,
            "risk_parity": opt.optimize_risk_parity,
        }[objective]
        result = method()

        # the strategy maps result.weights -> {ticker: float}; emulate it
        targets = {t: float(w) for t, w in zip(tickers, result.weights, strict=True)}
        assert set(targets) == set(tickers)
        assert sum(targets.values()) == pytest.approx(1.0, abs=1e-5)
        assert all(w >= -1e-6 for w in targets.values())  # long-only

    def test_result_exposes_expected_fields(self):
        rng = np.random.default_rng(12)
        tickers = ["A", "B"]
        returns = pd.DataFrame(rng.normal(0.0005, 0.01, (200, 2)), columns=tickers)
        opt = PortfolioOptimizer(tickers, "1900-01-01", "1900-01-02")
        opt.returns = returns
        opt.mean_returns = returns.mean() * 252
        opt.cov_matrix = returns.cov() * 252
        r = opt.optimize_sharpe()
        # attributes the backtester / export rely on
        for attr in (
            "weights",
            "expected_return",
            "volatility",
            "sharpe_ratio",
            "sortino_ratio",
            "cvar",
            "objective",
        ):
            assert hasattr(r, attr)
        assert isinstance(r.weights, np.ndarray)
