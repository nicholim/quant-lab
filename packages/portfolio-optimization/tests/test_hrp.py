"""Hierarchical Risk Parity (HRP) tests.

These inject synthetic returns directly (no network), mirroring how the
backtesting-framework drives the optimizer: set ``.returns`` / ``.mean_returns``
/ ``.cov_matrix`` then call ``optimize_hrp()`` zero-arg. HRP is additive and
API-safe -- it returns the same ``PortfolioResult`` as every other objective.
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


def block_correlated_returns(seed=0, n_obs=1500):
    """Two clear blocks: A/B/C tightly correlated, D/E/F tightly correlated,
    near-zero correlation across the two blocks.

    The first block is built far lower-variance than the second so HRP, which
    allocates inversely to cluster variance, must put most weight on block 1.
    """
    rng = np.random.default_rng(seed)
    tickers = ["A", "B", "C", "D", "E", "F"]
    f1 = rng.normal(0.0, 0.004, n_obs)  # low-vol common factor for block 1
    f2 = rng.normal(0.0, 0.020, n_obs)  # high-vol common factor for block 2
    cols = {}
    for t in ("A", "B", "C"):
        cols[t] = f1 + rng.normal(0.0, 0.0008, n_obs)
    for t in ("D", "E", "F"):
        cols[t] = f2 + rng.normal(0.0, 0.0040, n_obs)
    df = pd.DataFrame(cols, columns=tickers, index=pd.date_range("2021-01-01", periods=n_obs))
    return tickers, df


@pytest.fixture
def block_opt():
    tickers, df = block_correlated_returns()
    return tickers, make_optimizer(tickers, df)


# --- Core contract: long-only, fully invested, valid PortfolioResult ---


class TestHRPContract:
    def test_weights_sum_to_one_and_nonnegative(self, block_opt):
        _, opt = block_opt
        r = opt.optimize_hrp()
        assert r.weights.shape == (6,)
        assert r.weights.sum() == pytest.approx(1.0, abs=1e-9)
        assert all(w >= -1e-12 for w in r.weights)

    def test_result_exposes_expected_fields(self, block_opt):
        _, opt = block_opt
        r = opt.optimize_hrp()
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
        assert r.objective == "hrp"
        assert isinstance(r.weights, np.ndarray)

    def test_reported_stats_consistent_with_weights(self, block_opt):
        """vol/return reported on the result match recomputing them from the
        weights via the SAME portfolio-stat path the other optimizers use."""
        _, opt = block_opt
        r = opt.optimize_hrp()
        assert r.expected_return == pytest.approx(opt.portfolio_return(r.weights), rel=1e-12)
        assert r.volatility == pytest.approx(opt.portfolio_volatility(r.weights), rel=1e-12)
        assert r.sharpe_ratio == pytest.approx(opt.portfolio_sharpe(r.weights), rel=1e-12)

    def test_return_matches_shared_metrics(self, block_opt):
        """Annualized return via optimizer == via the shared metrics module
        (the cross-package source-of-truth contract)."""
        _, opt = block_opt
        r = opt.optimize_hrp()
        port_daily = opt.returns.values @ r.weights
        met_ret = M.annualized_return(port_daily, periods_per_year=252)
        assert r.expected_return == pytest.approx(met_ret, rel=1e-9)


# --- Risk-splitting sanity on a clearly block-correlated world ---


class TestHRPRiskSplitting:
    def test_splits_risk_across_the_two_blocks(self, block_opt):
        """HRP allocates inversely to cluster variance: the low-vol block 1
        (A/B/C) should receive substantially more total weight than the high-vol
        block 2 (D/E/F). Sanity, not exact equality."""
        tickers, opt = block_opt
        r = opt.optimize_hrp()
        w = dict(zip(tickers, r.weights, strict=True))
        block1 = w["A"] + w["B"] + w["C"]
        block2 = w["D"] + w["E"] + w["F"]
        # both blocks get a non-zero share -- risk is split across both, not
        # dumped entirely on one. (Block 2 has ~5x the vol so HRP, allocating
        # inversely to cluster variance, gives it only a small slice -- a few %.)
        assert block1 > 0.0
        assert block2 > 0.0
        # the low-variance block carries the clear majority of the budget
        assert block1 > 0.5
        assert block1 > 2.0 * block2

    def test_within_block_weights_are_comparable(self, block_opt):
        """Inside a block the three near-identical assets should get similar
        weight (no single name dominates its block)."""
        tickers, opt = block_opt
        r = opt.optimize_hrp()
        w = dict(zip(tickers, r.weights, strict=True))
        for block in (("A", "B", "C"), ("D", "E", "F")):
            vals = np.array([w[t] for t in block])
            # max within-block weight is at most ~2x the min (loose sanity bound)
            assert vals.max() <= 2.0 * vals.min() + 1e-6

    def test_higher_variance_asset_gets_less_than_its_low_vol_peers(self):
        """Six independent assets with monotonically increasing variance: HRP
        should down-weight the highest-variance name relative to the lowest."""
        rng = np.random.default_rng(3)
        tickers = [f"S{i}" for i in range(6)]
        vols = np.linspace(0.005, 0.030, 6)
        data = rng.normal(0.0, vols, size=(1200, 6))
        df = pd.DataFrame(data, columns=tickers, index=pd.date_range("2021-01-01", periods=1200))
        opt = make_optimizer(tickers, df)
        r = opt.optimize_hrp()
        w = dict(zip(tickers, r.weights, strict=True))
        assert w["S0"] > w["S5"]


# --- Backtester zero-arg injected-returns path ---


class TestHRPBacktesterPath:
    def test_runs_on_injected_returns_like_the_others(self):
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

        result = opt.optimize_hrp()
        targets = {t: float(w) for t, w in zip(tickers, result.weights, strict=True)}
        assert set(targets) == set(tickers)
        assert sum(targets.values()) == pytest.approx(1.0, abs=1e-9)
        assert all(w >= -1e-12 for w in targets.values())

    def test_before_calculate_raises(self):
        o = PortfolioOptimizer(["A", "B"], "2021-01-01", "2022-01-01")
        with pytest.raises(ValueError, match="calculate_returns"):
            o.optimize_hrp()


# --- Degenerate inputs don't crash ---


class TestHRPDegenerate:
    def test_single_asset_weights_to_one(self):
        rng = np.random.default_rng(1)
        df = pd.DataFrame(
            {"ONLY": rng.normal(0.0005, 0.01, 300)}, index=pd.date_range("2021-01-01", periods=300)
        )
        o = make_optimizer(["ONLY"], df)
        r = o.optimize_hrp()
        assert r.weights.shape == (1,)
        assert r.weights[0] == pytest.approx(1.0, abs=1e-12)

    def test_two_assets(self):
        rng = np.random.default_rng(2)
        a = rng.normal(0.0006, 0.008, 400)
        b = rng.normal(0.0004, 0.016, 400)
        df = pd.DataFrame({"A": a, "B": b}, index=pd.date_range("2021-01-01", periods=400))
        o = make_optimizer(["A", "B"], df)
        r = o.optimize_hrp()
        assert r.weights.sum() == pytest.approx(1.0, abs=1e-9)
        assert all(w >= -1e-12 for w in r.weights)
        # lower-vol A should get more than higher-vol B
        assert r.weights[0] > r.weights[1]

    def test_perfectly_correlated_assets_do_not_crash(self):
        # B = 1.5*A -> correlation 1, distance 0; must not NaN/crash.
        rng = np.random.default_rng(4)
        a = rng.normal(0.0006, 0.01, 400)
        df = pd.DataFrame({"A": a, "B": 1.5 * a}, index=pd.date_range("2021-01-01", periods=400))
        o = make_optimizer(["A", "B"], df)
        r = o.optimize_hrp()
        assert r.weights.sum() == pytest.approx(1.0, abs=1e-9)
        assert np.isfinite(r.volatility)
        assert all(np.isfinite(w) for w in r.weights)

    def test_duplicate_columns_singular_cov(self):
        rng = np.random.default_rng(5)
        a = rng.normal(0.0006, 0.01, 400)
        df = pd.DataFrame({"A": a, "B": a.copy()}, index=pd.date_range("2021-01-01", periods=400))
        o = make_optimizer(["A", "B"], df)
        r = o.optimize_hrp()
        assert r.weights.sum() == pytest.approx(1.0, abs=1e-9)
        assert np.isfinite(r.volatility)


# --- Stage helpers (unit) ---


class TestHRPStages:
    def test_quasi_diag_is_a_permutation_of_assets(self, block_opt):
        tickers, opt = block_opt
        cov = np.asarray(opt.cov_matrix, dtype=float)
        std = np.sqrt(np.diag(cov))
        corr = cov / np.outer(std, std)
        np.fill_diagonal(corr, 1.0)
        dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, None))
        from scipy.cluster.hierarchy import linkage
        from scipy.spatial.distance import squareform

        link = linkage(squareform(dist, checks=False), method="single")
        order = opt._hrp_quasi_diag(link)
        assert sorted(order) == list(range(len(tickers)))
        # the two blocks should come out contiguous in the quasi-diagonal order
        named = [tickers[i] for i in order]
        block1_positions = [named.index(t) for t in ("A", "B", "C")]
        assert max(block1_positions) - min(block1_positions) == 2

    def test_cluster_var_matches_inverse_variance_portfolio(self, block_opt):
        _, opt = block_opt
        cov = np.asarray(opt.cov_matrix, dtype=float)
        items = [0, 1, 2]
        sub = cov[np.ix_(items, items)]
        ivp = 1.0 / np.diag(sub)
        ivp = ivp / ivp.sum()
        expected = float(ivp @ sub @ ivp)
        assert opt._hrp_cluster_var(cov, items) == pytest.approx(expected, rel=1e-12)
