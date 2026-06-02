import numpy as np
import pandas as pd
import pytest

from portfolio_optimization_engine.metrics import (
    PerformanceMetrics,
    alpha,
    annualized_return,
    annualized_volatility,
    beta,
    cagr,
    calmar_ratio,
    compute_metrics,
    max_drawdown,
    omega_ratio,
    sharpe_ratio,
    sortino_ratio,
)

# --- Basic shape / entry point ---


class TestComputeMetrics:
    def test_returns_dataclass(self):
        r = np.full(252, 0.001)
        m = compute_metrics(r)
        assert isinstance(m, PerformanceMetrics)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            compute_metrics(np.array([]))

    def test_no_benchmark_beta_alpha_none(self):
        m = compute_metrics(np.full(252, 0.001))
        assert m.beta is None
        assert m.alpha is None

    def test_as_dict_roundtrip(self):
        m = compute_metrics(np.full(252, 0.001))
        d = m.as_dict()
        assert set(d) == {
            "cagr",
            "annualized_return",
            "annualized_volatility",
            "max_drawdown",
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "omega_ratio",
            "beta",
            "alpha",
        }


# --- Known-answer cases ---


class TestKnownValues:
    def test_constant_positive_no_drawdown(self):
        r = np.full(252, 0.001)
        assert max_drawdown(r) == 0.0

    def test_constant_positive_calmar_inf(self):
        r = np.full(252, 0.001)
        assert calmar_ratio(r) == float("inf")

    def test_constant_positive_sortino_inf(self):
        # no period falls below the per-period rf=0 target
        r = np.full(252, 0.001)
        assert sortino_ratio(r, risk_free_rate=0.0) == float("inf")

    def test_zero_variance_sharpe_zero(self):
        r = np.full(252, 0.001)
        assert sharpe_ratio(r) == 0.0

    def test_cagr_simple(self):
        # +10% then -10% over 2 periods (P=2) => wealth 0.99, ~1 year
        r = np.array([0.10, -0.10])
        assert cagr(r, periods_per_year=2) == pytest.approx(0.99 - 1.0, abs=1e-12)

    def test_max_drawdown_known(self):
        # up 10%, down ~9.09% back to start, so peak at 1.1 -> trough 1.0
        r = np.array([0.10, -0.0909090909, 0.05])
        assert max_drawdown(r) == pytest.approx(-0.0909090909, abs=1e-6)

    def test_annualized_return_arithmetic(self):
        r = np.full(252, 0.001)
        assert annualized_return(r, 252) == pytest.approx(0.252)

    def test_annualized_vol_zero_for_constant(self):
        assert annualized_volatility(np.full(10, 0.01)) == pytest.approx(0.0, abs=1e-12)

    def test_total_wipeout_cagr(self):
        r = np.array([-1.0, 0.5])  # wealth hits 0
        assert cagr(r, periods_per_year=2) == -1.0

    def test_omega_inf_when_no_losses(self):
        assert omega_ratio(np.array([0.01, 0.02, 0.03])) == float("inf")


# --- Benchmark relationships ---


class TestBenchmark:
    def test_beta_one_when_identical(self):
        rng = np.random.default_rng(0)
        r = rng.normal(0.0005, 0.01, 500)
        assert beta(r, r) == pytest.approx(1.0, abs=1e-9)

    def test_alpha_zero_when_identical(self):
        rng = np.random.default_rng(1)
        r = rng.normal(0.0005, 0.01, 500)
        assert alpha(r, r, risk_free_rate=0.02) == pytest.approx(0.0, abs=1e-9)

    def test_beta_scales(self):
        rng = np.random.default_rng(2)
        b = rng.normal(0.0005, 0.01, 1000)
        r = 2.0 * b
        assert beta(r, b) == pytest.approx(2.0, abs=1e-9)

    def test_compute_metrics_with_series_alignment(self):
        idx = pd.date_range("2021-01-01", periods=300)
        r = pd.Series(np.random.default_rng(3).normal(0.0005, 0.01, 300), index=idx)
        b = pd.Series(np.random.default_rng(4).normal(0.0004, 0.012, 300), index=idx)
        m = compute_metrics(r, benchmark=b)
        assert m.beta is not None
        assert m.alpha is not None

    def test_higher_vol_higher_annualized_vol(self):
        rng = np.random.default_rng(5)
        low = rng.normal(0, 0.005, 1000)
        high = rng.normal(0, 0.02, 1000)
        assert annualized_volatility(high) > annualized_volatility(low)
