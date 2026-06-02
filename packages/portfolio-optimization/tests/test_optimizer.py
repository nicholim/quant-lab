import numpy as np
import pytest

from portfolio_optimization_engine.monte_carlo import MonteCarloSimulator
from portfolio_optimization_engine.optimizer import PortfolioOptimizer


@pytest.fixture(scope="module")
def optimizer():
    """Shared optimizer instance — downloads data once for all tests."""
    opt = PortfolioOptimizer(["AAPL", "MSFT", "JPM"], "2021-01-01", "2023-01-01")
    opt.fetch_data()
    opt.calculate_returns()
    return opt


# --- Optimization ---


class TestOptimization:
    def test_sharpe_weights_sum_to_one(self, optimizer):
        result = optimizer.optimize_sharpe()
        assert abs(result.weights.sum() - 1.0) < 1e-6

    def test_sharpe_weights_non_negative(self, optimizer):
        result = optimizer.optimize_sharpe()
        assert all(w >= -1e-6 for w in result.weights)

    def test_min_vol_weights_sum_to_one(self, optimizer):
        result = optimizer.optimize_min_volatility()
        assert abs(result.weights.sum() - 1.0) < 1e-6

    def test_min_vol_leq_equal_weight(self, optimizer):
        minvol = optimizer.optimize_min_volatility()
        eq_vol = optimizer.portfolio_volatility(np.array([1 / 3] * 3))
        assert minvol.volatility <= eq_vol + 1e-6

    def test_sharpe_geq_minvol_sharpe(self, optimizer):
        sharpe = optimizer.optimize_sharpe()
        minvol = optimizer.optimize_min_volatility()
        assert sharpe.sharpe_ratio >= minvol.sharpe_ratio - 0.01

    def test_portfolio_return_correct(self, optimizer):
        weights = np.array([1 / 3] * 3)
        ret = optimizer.portfolio_return(weights)
        expected = float(np.dot(weights, optimizer.mean_returns))
        assert abs(ret - expected) < 1e-10

    def test_portfolio_volatility_positive(self, optimizer):
        weights = np.array([1 / 3] * 3)
        assert optimizer.portfolio_volatility(weights) > 0


# --- Efficient Frontier ---


class TestEfficientFrontier:
    def test_reproducibility(self, optimizer):
        ef1 = optimizer.efficient_frontier(200, random_state=42)
        ef2 = optimizer.efficient_frontier(200, random_state=42)
        assert ef1.equals(ef2)

    def test_different_seeds_different_results(self, optimizer):
        ef1 = optimizer.efficient_frontier(100, random_state=1)
        ef2 = optimizer.efficient_frontier(100, random_state=2)
        assert not ef1.equals(ef2)

    def test_contains_required_columns(self, optimizer):
        ef = optimizer.efficient_frontier(100, random_state=1)
        assert "return" in ef.columns
        assert "volatility" in ef.columns
        assert "sharpe" in ef.columns

    def test_all_returns_finite(self, optimizer):
        ef = optimizer.efficient_frontier(100, random_state=1)
        assert ef["return"].notna().all()
        assert ef["volatility"].notna().all()


# --- Monte Carlo ---


class TestMonteCarlo:
    def test_reproducibility(self):
        mc = MonteCarloSimulator(0.10, 0.20, 100_000)
        mc.simulate(500, 252, random_state=42)
        v1 = mc.calculate_var(0.95)
        mc.simulate(500, 252, random_state=42)
        v2 = mc.calculate_var(0.95)
        assert v1 == v2

    def test_var_non_negative(self):
        mc = MonteCarloSimulator(0.10, 0.20, 100_000)
        mc.simulate(1000, 252, random_state=1)
        assert mc.calculate_var(0.95) >= 0

    def test_cvar_geq_var(self):
        mc = MonteCarloSimulator(0.10, 0.20, 100_000)
        mc.simulate(1000, 252, random_state=1)
        assert mc.calculate_cvar(0.95) >= mc.calculate_var(0.95)

    def test_higher_vol_higher_var(self):
        mc_low = MonteCarloSimulator(0.10, 0.10, 100_000)
        mc_low.simulate(5000, 252, random_state=1)
        mc_high = MonteCarloSimulator(0.10, 0.40, 100_000)
        mc_high.simulate(5000, 252, random_state=1)
        assert mc_high.calculate_var(0.95) > mc_low.calculate_var(0.95)

    def test_simulation_shape(self):
        mc = MonteCarloSimulator(0.10, 0.20, 100_000)
        paths = mc.simulate(100, 50, random_state=1)
        assert paths.shape == (100, 51)  # num_sims x (num_days + 1)

    def test_initial_value_preserved(self):
        mc = MonteCarloSimulator(0.10, 0.20, 50_000)
        paths = mc.simulate(100, 50, random_state=1)
        assert all(paths[:, 0] == 50_000)

    def test_simulate_before_var_raises(self):
        mc = MonteCarloSimulator(0.10, 0.20, 100_000)
        with pytest.raises(ValueError):
            mc.calculate_var(0.95)
