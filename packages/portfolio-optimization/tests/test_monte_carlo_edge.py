"""Edge cases + plot smoke for MonteCarloSimulator (complements test_optimizer.py)."""

import matplotlib

matplotlib.use("Agg")  # noqa: E402

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from portfolio_optimization_engine.monte_carlo import MonteCarloSimulator  # noqa: E402


@pytest.fixture(autouse=True)
def _headless(monkeypatch):
    monkeypatch.setattr(plt, "show", lambda *a, **k: None)
    yield
    plt.close("all")


def test_cvar_before_simulate_raises():
    mc = MonteCarloSimulator(0.10, 0.20, 100_000)
    with pytest.raises(ValueError, match="simulate"):
        mc.calculate_cvar(0.95)


def test_plot_before_simulate_raises():
    mc = MonteCarloSimulator(0.10, 0.20, 100_000)
    with pytest.raises(ValueError, match="simulate"):
        mc.plot_simulations()


def test_zero_volatility_paths_deterministic():
    # vol=0 collapses GBM to a pure drift; all paths identical
    mc = MonteCarloSimulator(0.10, 0.0, 100_000)
    paths = mc.simulate(50, 30, random_state=1)
    assert np.allclose(paths, paths[0])  # every path equals the first
    # with no dispersion the loss distribution is degenerate -> VaR ~ -drift gain
    assert np.isfinite(mc.calculate_var(0.95))


def test_plot_simulations_smoke(tmp_path):
    mc = MonteCarloSimulator(0.08, 0.18, 100_000)
    mc.simulate(300, 60, random_state=2)
    out = tmp_path / "mc.png"
    mc.plot_simulations(num_paths=50, save_path=str(out))
    assert out.exists()


def test_cvar_at_least_var_magnitude():
    mc = MonteCarloSimulator(0.05, 0.30, 100_000)
    mc.simulate(2000, 252, random_state=3)
    assert mc.calculate_cvar(0.99) >= mc.calculate_var(0.99)
