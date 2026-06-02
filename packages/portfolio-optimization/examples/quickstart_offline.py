"""End-to-end optimizer workflow that runs fully offline (no network).

Instead of fetching prices from Yahoo Finance, this example injects a synthetic
daily-returns matrix using the same contract the FastAPI demo and the sibling
backtesting-framework use: set ``.returns`` / ``.mean_returns`` / ``.cov_matrix``,
then call any ``optimize_*`` method. Run it with:

    python examples/quickstart_offline.py

It exercises: all six objectives, the efficient-frontier cloud, the standalone
metrics module (which the backtester shares), and a Monte Carlo VaR/CVaR projection.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio_optimization_engine.analysis import compute_portfolio_returns
from portfolio_optimization_engine.metrics import compute_metrics
from portfolio_optimization_engine.monte_carlo import MonteCarloSimulator
from portfolio_optimization_engine.optimizer import PortfolioOptimizer

TICKERS = ["AAPL", "MSFT", "GOOG", "JPM"]


def make_returns(seed: int = 0, periods: int = 750) -> pd.DataFrame:
    """Synthetic but correlated daily returns so the example is deterministic."""
    rng = np.random.default_rng(seed)
    mu = np.array([0.0007, 0.0006, 0.0005, 0.0004])  # daily drifts
    vol = np.array([0.018, 0.016, 0.017, 0.013])
    corr = np.array(
        [
            [1.0, 0.6, 0.5, 0.3],
            [0.6, 1.0, 0.55, 0.35],
            [0.5, 0.55, 1.0, 0.3],
            [0.3, 0.35, 0.3, 1.0],
        ]
    )
    cov = np.outer(vol, vol) * corr
    draws = rng.multivariate_normal(mu, cov, size=periods)
    return pd.DataFrame(draws, columns=TICKERS)


def main() -> None:
    returns = make_returns()

    # Inject returns instead of fetch_data()/calculate_returns() (offline contract).
    opt = PortfolioOptimizer(TICKERS, "2021-01-01", "2024-01-01", risk_free_rate=0.02)
    opt.returns = returns
    opt.mean_returns = returns.mean() * 252
    opt.cov_matrix = returns.cov() * 252

    frontier = opt.efficient_frontier(num_portfolios=5000, random_state=42)
    print(f"Efficient-frontier cloud: {len(frontier)} random portfolios")
    print(f"  best random Sharpe = {frontier['sharpe'].max():.3f}\n")

    objectives = {
        "Max Sharpe": opt.optimize_sharpe(),
        "Min Volatility": opt.optimize_min_volatility(),
        "Risk Parity": opt.optimize_risk_parity(),
        "Max Sortino": opt.optimize_sortino(),
        "Min CVaR": opt.optimize_min_cvar(confidence=0.95),
        "Constrained (AAPL<=30%)": opt.optimize_sharpe(max_weights={"AAPL": 0.30}),
    }

    print(f"{'Objective':<26}{'Return':>9}{'Vol':>9}{'Sharpe':>9}")
    for name, res in objectives.items():
        print(
            f"{name:<26}{res.expected_return:>8.2%}{res.volatility:>9.2%}{res.sharpe_ratio:>9.2f}"
        )

    # Standalone metrics module (shared, test-enforced parity with the backtester).
    best = objectives["Max Sharpe"]
    daily = compute_portfolio_returns(opt.returns, best.weights)
    m = compute_metrics(daily, risk_free_rate=0.02)
    print(
        f"\nMax-Sharpe metrics: CAGR={m.cagr:.2%}  MaxDD={m.max_drawdown:.2%}"
        f"  Sortino={m.sortino_ratio:.2f}  Calmar={m.calmar_ratio:.2f}"
    )

    # Monte Carlo VaR/CVaR projection on the chosen portfolio.
    mc = MonteCarloSimulator(best.expected_return, best.volatility, initial_value=100_000)
    mc.simulate(num_simulations=10_000, num_days=252, random_state=42)
    print(
        f"1-Year VaR (95%):  ${mc.calculate_var(0.95):,.0f}\n"
        f"1-Year CVaR (95%): ${mc.calculate_cvar(0.95):,.0f}"
    )


if __name__ == "__main__":
    main()
