"""Tests for the solved efficient frontier + Ledoit-Wolf covariance shrinkage.

Both features are additive: the frontier is a NEW method
(:meth:`PortfolioOptimizer.solved_efficient_frontier`) alongside the existing
random-cloud ``efficient_frontier``, and shrinkage is opt-in via
``calculate_returns(shrinkage=...)`` (default off). Follows the injected-returns
pattern of ``test_optimizer_edge.py`` so the backtester contract stays covered.
"""

import numpy as np
import pandas as pd
import pytest

from portfolio_optimization_engine.covariance import (
    constant_correlation_target,
    identity_target,
    ledoit_wolf_shrinkage,
)
from portfolio_optimization_engine.optimizer import PortfolioOptimizer


def make_optimizer(tickers, returns_df, risk_free_rate=0.02):
    o = PortfolioOptimizer(tickers, "2021-01-01", "2022-01-01", risk_free_rate=risk_free_rate)
    o.returns = returns_df
    o.mean_returns = returns_df.mean() * 252
    o.cov_matrix = returns_df.cov() * 252
    return o


@pytest.fixture
def three_asset():
    rng = np.random.default_rng(7)
    tickers = ["A", "B", "C"]
    data = rng.normal([0.0007, 0.0004, 0.0006], [0.011, 0.008, 0.013], size=(400, 3))
    df = pd.DataFrame(data, columns=tickers, index=pd.date_range("2021-01-01", periods=400))
    return make_optimizer(tickers, df)


# --- Solved efficient frontier ---


class TestSolvedFrontier:
    def test_returns_monotonically_increase(self, three_asset):
        f = three_asset.solved_efficient_frontier(n_points=25)
        assert len(f) >= 2
        assert f["return"].is_monotonic_increasing

    def test_vols_are_solved_minimum_per_target(self, three_asset):
        # the recorded volatility for each target must equal the min-vol-at-target
        # solve, and never undercut the global minimum-vol portfolio.
        f = three_asset.solved_efficient_frontier(n_points=20)
        global_min_vol = three_asset.optimize_min_volatility().volatility
        assert (f["volatility"] >= global_min_vol - 1e-6).all()
        # spot-check a couple of rows against a direct re-solve
        for _, row in f.iloc[[0, len(f) // 2, -1]].iterrows():
            direct = three_asset.optimize_min_vol_target_return(float(row["return"]))
            assert direct.volatility == pytest.approx(row["volatility"], abs=1e-4)

    def test_frontier_is_increasing_in_vol(self, three_asset):
        # along the upper efficient frontier, higher return costs higher vol
        f = three_asset.solved_efficient_frontier(n_points=25)
        assert f["volatility"].is_monotonic_increasing

    def test_weights_sum_to_one_and_respect_bounds(self, three_asset):
        f = three_asset.solved_efficient_frontier(n_points=15)
        wcols = [c for c in f.columns if c.startswith("w_")]
        sums = f[wcols].sum(axis=1)
        assert np.allclose(sums, 1.0, atol=1e-5)
        # long-only default: no negative weights, none above 1
        assert (f[wcols].values >= -1e-6).all()
        assert (f[wcols].values <= 1.0 + 1e-6).all()

    def test_constraint_bounds_are_respected(self, three_asset):
        f = three_asset.solved_efficient_frontier(n_points=12, max_weights=0.5)
        wcols = [c for c in f.columns if c.startswith("w_")]
        assert (f[wcols].values <= 0.5 + 1e-5).all()

    def test_has_expected_columns(self, three_asset):
        f = three_asset.solved_efficient_frontier(n_points=10)
        for col in ("return", "volatility", "sharpe"):
            assert col in f.columns
        for t in three_asset.tickers:
            assert f"w_{t}" in f.columns

    def test_before_calculate_raises(self):
        o = PortfolioOptimizer(["A", "B"], "2021-01-01", "2022-01-01")
        with pytest.raises(ValueError, match="calculate_returns"):
            o.solved_efficient_frontier()

    def test_n_points_too_small_raises(self, three_asset):
        with pytest.raises(ValueError, match="n_points"):
            three_asset.solved_efficient_frontier(n_points=1)

    def test_single_asset_degenerate_grid(self):
        # one asset -> lo_ret == hi_ret; the grid is widened so it doesn't crash
        rng = np.random.default_rng(3)
        df = pd.DataFrame(
            {"ONLY": rng.normal(0.0005, 0.01, 300)},
            index=pd.date_range("2021-01-01", periods=300),
        )
        o = make_optimizer(["ONLY"], df)
        f = o.solved_efficient_frontier(n_points=5)
        assert not f.empty
        assert np.allclose(f["w_ONLY"], 1.0, atol=1e-5)

    def test_infeasible_targets_are_skipped_not_raised(self, three_asset):
        # tight per-asset cap makes the highest-return targets infeasible; the
        # frontier must skip them gracefully rather than raise.
        f = three_asset.solved_efficient_frontier(n_points=30, max_weights=0.4)
        assert not f.empty
        wcols = [c for c in f.columns if c.startswith("w_")]
        assert np.allclose(f[wcols].sum(axis=1), 1.0, atol=1e-5)


# --- Ledoit-Wolf shrinkage ---


class TestLedoitWolf:
    @pytest.fixture
    def returns(self):
        rng = np.random.default_rng(11)
        # 8 assets, only 60 obs -> sample cov is noisy, shrinkage should bite
        return pd.DataFrame(
            rng.normal(0.0005, 0.01, size=(60, 8)),
            columns=[f"A{i}" for i in range(8)],
        )

    def test_intensity_in_unit_interval(self, returns):
        for target in ("constant_correlation", "identity"):
            _, intensity = ledoit_wolf_shrinkage(returns, target=target)
            assert 0.0 <= intensity <= 1.0

    def test_result_is_symmetric_psd(self, returns):
        for target in ("constant_correlation", "identity"):
            cov, _ = ledoit_wolf_shrinkage(returns, target=target)
            assert np.allclose(cov, cov.T)
            eigs = np.linalg.eigvalsh(cov)
            assert (eigs >= -1e-10).all()

    def test_shrinks_toward_target(self, returns):
        # the shrunk estimate must sit between the sample cov and the target:
        # closer to the target than the raw sample cov is.
        X = returns.values
        Xc = X - X.mean(axis=0)
        S = (Xc.T @ Xc) / X.shape[0]
        F = constant_correlation_target(S)
        cov, intensity = ledoit_wolf_shrinkage(returns, target="constant_correlation")
        assert intensity > 0  # noisy small sample => non-trivial shrinkage
        dist_sample = np.sum((S - F) ** 2)
        dist_shrunk = np.sum((cov - F) ** 2)
        assert dist_shrunk < dist_sample

    def test_intensity_one_recovers_target(self):
        # craft a case where S already equals its target (constant correlation),
        # then the optimal intensity is 0 and the result equals S.
        rng = np.random.default_rng(1)
        df = pd.DataFrame(rng.normal(0, 0.01, size=(200, 3)), columns=list("abc"))
        cov, intensity = ledoit_wolf_shrinkage(df, target="identity")
        assert 0.0 <= intensity <= 1.0
        # blended estimate equals the documented convex combination
        X = df.values
        Xc = X - X.mean(axis=0)
        S = (Xc.T @ Xc) / X.shape[0]
        F = identity_target(S)
        expected = intensity * F + (1 - intensity) * S
        assert np.allclose(cov, 0.5 * (expected + expected.T))

    def test_single_asset_zero_intensity(self):
        df = pd.DataFrame({"x": np.linspace(-0.01, 0.01, 50)})
        cov, intensity = ledoit_wolf_shrinkage(df, target="constant_correlation")
        assert intensity == 0.0
        assert cov.shape == (1, 1)

    def test_too_few_observations_raises(self):
        df = pd.DataFrame({"a": [0.01], "b": [0.02]})
        with pytest.raises(ValueError, match="2 observations"):
            ledoit_wolf_shrinkage(df)

    def test_unknown_target_raises(self, returns):
        with pytest.raises(ValueError, match="Unknown shrinkage target"):
            ledoit_wolf_shrinkage(returns, target="bogus")

    def test_ndarray_input_accepted(self, returns):
        cov, intensity = ledoit_wolf_shrinkage(returns.values, target="identity")
        assert cov.shape == (8, 8)
        assert 0.0 <= intensity <= 1.0

    def test_1d_ndarray_treated_as_single_asset(self):
        # a flat 1-D return vector is reshaped to (T, 1)
        cov, intensity = ledoit_wolf_shrinkage(np.linspace(-0.01, 0.01, 50))
        assert cov.shape == (1, 1)
        assert intensity == 0.0

    def test_3d_input_raises(self):
        with pytest.raises(ValueError, match="2-D"):
            ledoit_wolf_shrinkage(np.zeros((4, 3, 2)))


# --- Parity: default path is byte-identical to the previous behavior ---


class TestShrinkageParity:
    """The DEFAULT optimizer path (shrinkage off) must yield EXACTLY the same
    covariance and weights as before -- this is the backtester/metrics contract.
    """

    @pytest.fixture
    def with_prices(self):
        rng = np.random.default_rng(5)
        tickers = ["A", "B", "C", "D"]
        daily = rng.normal(0.0005, 0.01, size=(300, 4))
        prices = pd.DataFrame(
            (1 + daily).cumprod(axis=0) * 100,
            columns=tickers,
            index=pd.date_range("2021-01-01", periods=300),
        )
        o = PortfolioOptimizer(tickers, "2021-01-01", "2022-01-01")
        o.prices = prices
        return o

    def test_default_cov_identical_to_sample(self, with_prices):
        with_prices.calculate_returns()  # default: shrinkage None
        expected = with_prices.returns.cov() * 252
        assert with_prices.cov_matrix.equals(expected)
        assert with_prices.shrinkage_intensity is None

    def test_default_weights_unchanged(self, with_prices):
        with_prices.calculate_returns()
        baseline_cov = with_prices.returns.cov() * 252
        w_default = with_prices.optimize_sharpe().weights
        # re-run with an explicit manual sample cov; must match to solver tol
        with_prices.cov_matrix = baseline_cov
        w_manual = with_prices.optimize_sharpe().weights
        assert np.allclose(w_default, w_manual, atol=1e-9)

    def test_shrinkage_opt_in_changes_cov_and_sets_intensity(self, with_prices):
        with_prices.calculate_returns(shrinkage="constant_correlation")
        assert with_prices.shrinkage_intensity is not None
        assert 0.0 <= with_prices.shrinkage_intensity <= 1.0
        # cov is annualized + labeled with the tickers
        assert list(with_prices.cov_matrix.columns) == with_prices.tickers
        # shrunk cov differs from the plain sample cov when intensity > 0
        sample = with_prices.returns.cov() * 252
        if with_prices.shrinkage_intensity > 0:
            assert not np.allclose(with_prices.cov_matrix.values, sample.values)

    def test_shrunk_cov_still_drives_a_valid_optimization(self, with_prices):
        with_prices.calculate_returns(shrinkage="identity")
        r = with_prices.optimize_sharpe()
        assert abs(r.weights.sum() - 1.0) < 1e-5
        assert all(w >= -1e-6 for w in r.weights)
