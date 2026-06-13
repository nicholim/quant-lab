"""Risk attribution (Euler decomposition) + cov-estimator wiring tests.

Uses injected-returns optimizers (no network) so the math is deterministic.
"""

import numpy as np
import pandas as pd
import pytest

from portfolio_optimization_engine.config import parse_args
from portfolio_optimization_engine.optimizer import PortfolioOptimizer


@pytest.fixture
def opt():
    rng = np.random.default_rng(42)
    tickers = ["A", "B", "C", "D"]
    n_days = 750
    means = np.array([0.0008, 0.0005, 0.0011, 0.0003])
    vols = np.array([0.012, 0.008, 0.020, 0.006])
    data = rng.normal(means, vols, size=(n_days, 4))
    returns = pd.DataFrame(data, columns=tickers, index=pd.date_range("2021-01-01", periods=n_days))
    o = PortfolioOptimizer(tickers, "2021-01-01", "2023-12-31")
    o.returns = returns
    o.mean_returns = returns.mean() * 252
    o.cov_matrix = returns.cov() * 252
    return o


class TestRiskAttribution:
    def test_euler_sum_ccr_equals_volatility(self, opt):
        """Component contributions sum to portfolio volatility (Euler's theorem)."""
        w = np.array([0.3, 0.2, 0.4, 0.1])
        attr = opt.risk_attribution(w)
        vol = opt.portfolio_volatility(w)
        assert attr["ccr"].sum() == pytest.approx(vol, abs=1e-12)
        assert attr["pct_risk"].sum() == pytest.approx(1.0, abs=1e-12)

    def test_ccr_matches_portfolio_risk_contributions(self, opt):
        """risk_attribution.ccr shares the math with portfolio_risk_contributions."""
        w = np.array([0.25, 0.25, 0.25, 0.25])
        attr = opt.risk_attribution(w)
        rc = opt.portfolio_risk_contributions(w)
        np.testing.assert_allclose(attr["ccr"].to_numpy(), rc, atol=1e-12)

    def test_pct_risk_is_ccr_over_vol(self, opt):
        w = np.array([0.4, 0.1, 0.3, 0.2])
        attr = opt.risk_attribution(w)
        vol = opt.portfolio_volatility(w)
        np.testing.assert_allclose(
            attr["pct_risk"].to_numpy(), attr["ccr"].to_numpy() / vol, atol=1e-12
        )

    def test_risk_parity_gives_equal_ccrs(self, opt):
        """A risk-parity solution should equalize the per-asset CCRs."""
        res = opt.optimize_risk_parity()
        attr = opt.risk_attribution(res.weights)
        ccr = attr["ccr"].to_numpy()
        assert ccr.std() / ccr.mean() < 0.05  # near-equal

    def test_columns_and_index(self, opt):
        w = np.array([0.3, 0.2, 0.4, 0.1])
        attr = opt.risk_attribution(w)
        assert list(attr.columns) == ["weight", "mcr", "ccr", "pct_risk"]
        assert list(attr.index) == opt.tickers

    def test_sector_rollup(self, opt):
        """Grouped attribution aggregates weight/ccr/pct_risk per group."""
        w = np.array([0.3, 0.2, 0.4, 0.1])
        groups = {"tech": (["A", "B"], 0.0, 1.0), "fin": (["C", "D"], 0.0, 1.0)}
        attr = opt.risk_attribution(w, groups=groups)
        assert set(attr.index) == {"tech", "fin"}
        # group weights sum to the member weights
        assert attr.loc["tech", "weight"] == pytest.approx(0.5)
        assert attr.loc["fin", "weight"] == pytest.approx(0.5)
        # grouped CCR still sums to portfolio vol
        assert attr["ccr"].sum() == pytest.approx(opt.portfolio_volatility(w), abs=1e-12)
        assert attr["pct_risk"].sum() == pytest.approx(1.0, abs=1e-12)
        # group mcr == group ccr / group weight
        assert attr.loc["tech", "mcr"] == pytest.approx(
            attr.loc["tech", "ccr"] / attr.loc["tech", "weight"], abs=1e-12
        )

    def test_rollup_unassigned_bucket(self, opt):
        w = np.array([0.25, 0.25, 0.25, 0.25])
        groups = {"tech": (["A", "B"], 0.0, 1.0)}
        attr = opt.risk_attribution(w, groups=groups)
        assert "unassigned" in attr.index
        assert attr.loc["unassigned", "weight"] == pytest.approx(0.5)


class TestCovEstimatorWiring:
    @pytest.fixture
    def with_prices(self):
        rng = np.random.default_rng(7)
        tickers = ["A", "B", "C", "D"]
        daily = rng.normal(0.0005, 0.01, size=(400, 4))
        prices = pd.DataFrame(
            (1 + daily).cumprod(axis=0) * 100,
            columns=tickers,
            index=pd.date_range("2021-01-01", periods=400),
        )
        o = PortfolioOptimizer(tickers, "2021-01-01", "2022-01-01")
        o.prices = prices
        return o

    def test_default_parity_byte_identical(self, with_prices):
        """Default calculate_returns == returns.cov()*252 and mean*252 (regression)."""
        with_prices.calculate_returns()
        r = with_prices.returns
        expected_cov = r.cov() * 252
        expected_mean = r.mean() * 252
        assert with_prices.cov_matrix.equals(expected_cov)
        np.testing.assert_allclose(
            with_prices.mean_returns.to_numpy(), expected_mean.to_numpy(), atol=1e-12
        )
        assert with_prices.shrinkage_intensity is None

    def test_sample_estimator_equals_default(self, with_prices):
        """Explicit 'sample' estimators reproduce the default path exactly."""
        with_prices.calculate_returns(cov_estimator="sample", mean_estimator="sample")
        r = with_prices.returns
        assert with_prices.cov_matrix.equals(r.cov() * 252)
        np.testing.assert_allclose(
            with_prices.mean_returns.to_numpy(), (r.mean() * 252).to_numpy(), atol=1e-12
        )

    def test_shrinkage_and_cov_estimator_conflict(self, with_prices):
        with pytest.raises(ValueError, match="at most one"):
            with_prices.calculate_returns(shrinkage="identity", cov_estimator="ewma")

    @pytest.mark.parametrize("est", ["ewma", "oas", "mp"])
    def test_named_cov_estimators_drive_valid_optimization(self, with_prices, est):
        with_prices.calculate_returns(cov_estimator=est)
        assert list(with_prices.cov_matrix.columns) == with_prices.tickers
        res = with_prices.optimize_sharpe()
        assert abs(res.weights.sum() - 1.0) < 1e-5

    def test_oas_sets_intensity(self, with_prices):
        with_prices.calculate_returns(cov_estimator="oas")
        assert with_prices.shrinkage_intensity is not None
        assert 0.0 <= with_prices.shrinkage_intensity <= 1.0

    @pytest.mark.parametrize("est", ["ewma", "james_stein"])
    def test_named_mean_estimators(self, with_prices, est):
        with_prices.calculate_returns(mean_estimator=est)
        assert list(with_prices.mean_returns.index) == with_prices.tickers


class TestConfigCLIWiring:
    def test_cli_estimator_flags(self):
        cfg = parse_args(["--cov-estimator", "ewma", "--mean-estimator", "james_stein"])
        assert cfg.cov_estimator == "ewma"
        assert cfg.mean_estimator == "james_stein"

    def test_cli_defaults(self):
        cfg = parse_args([])
        assert cfg.cov_estimator == "sample"
        assert cfg.mean_estimator == "sample"

    def test_bad_estimator_rejected_by_argparse(self):
        with pytest.raises(SystemExit):
            parse_args(["--cov-estimator", "bogus"])
