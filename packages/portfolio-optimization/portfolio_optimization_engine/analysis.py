"""Orchestration: turn an AnalysisConfig into optimization results + metrics.

``run_analysis`` is I/O-free (no printing, no plotting) so it is reusable by the
CLI, tests, and an external backtest framework. Console output and plotting live
in ``print_report`` / the CLI wrapper.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import AnalysisConfig
from .data_cache import fetch_close_prices
from .metrics import compute_metrics
from .monte_carlo import MonteCarloSimulator
from .optimizer import PortfolioOptimizer

# objective name -> method to call on the optimizer
_OBJECTIVE_METHODS = {
    "max_sharpe": "optimize_sharpe",
    "min_vol": "optimize_min_volatility",
    "risk_parity": "optimize_risk_parity",
    "sortino": "optimize_sortino",
    "min_cvar": "optimize_min_cvar",
}


def _selected_objectives(objective: str) -> list[str]:
    if objective == "both":
        return ["max_sharpe", "min_vol"]
    if objective == "all":
        return list(_OBJECTIVE_METHODS)
    return {
        "sharpe": ["max_sharpe"],
        "min_vol": ["min_vol"],
        "risk_parity": ["risk_parity"],
        "sortino": ["sortino"],
        "min_cvar": ["min_cvar"],
    }[objective]


def compute_portfolio_returns(returns: pd.DataFrame, weights: np.ndarray) -> pd.Series:
    """Daily realized return series of a fixed-weight portfolio."""
    return returns.dot(weights)


def _fetch_benchmark(config: AnalysisConfig, index: pd.Index) -> pd.Series | None:
    """Download benchmark daily returns aligned to the portfolio return index.

    Routes through the shared resilient data layer (retry/backoff/offline) rather
    than calling ``yf.download`` directly, so network logic lives in one place.
    """
    if not config.benchmark:
        return None
    prices = fetch_close_prices(
        config.benchmark,
        config.start_date,
        config.end_date,
        auto_adjust=True,
        offline=config.offline,
    )
    if isinstance(prices, pd.DataFrame):
        prices = prices.iloc[:, 0]
    bench = prices.pct_change().dropna()
    return bench.reindex(index).dropna()


def _run_monte_carlo(config: AnalysisConfig, primary) -> dict:
    mc = MonteCarloSimulator(
        expected_return=primary.expected_return,
        volatility=primary.volatility,
        initial_value=config.initial_value,
    )
    mc.simulate(
        num_simulations=config.monte_carlo_sims,
        num_days=config.monte_carlo_days,
        random_state=config.random_state,
    )
    return {
        "simulator": mc,
        "var_95": mc.calculate_var(0.95),
        "cvar_95": mc.calculate_cvar(0.95),
    }


def run_analysis(config: AnalysisConfig) -> dict:
    """Run the full optimization workflow. Returns a results dict; no I/O."""
    optimizer = PortfolioOptimizer(
        tickers=config.tickers,
        start_date=config.start_date,
        end_date=config.end_date,
        risk_free_rate=config.risk_free_rate,
        offline=config.offline,
    )
    optimizer.fetch_data()
    optimizer.calculate_returns()

    frontier = optimizer.efficient_frontier(config.num_portfolios, config.random_state)

    results = {}
    for name in _selected_objectives(config.objective):
        method = getattr(optimizer, _OBJECTIVE_METHODS[name])
        results[name] = method()

    benchmark_returns = _fetch_benchmark(config, optimizer.returns.index)  # type: ignore[union-attr]  # set by calculate_returns() above

    metrics = {
        name: compute_metrics(
            compute_portfolio_returns(optimizer.returns, res.weights),
            benchmark=benchmark_returns,
            risk_free_rate=config.risk_free_rate,
        )
        for name, res in results.items()
    }

    primary_name = "max_sharpe" if "max_sharpe" in results else next(iter(results))
    mc = _run_monte_carlo(config, results[primary_name])
    mc_summary = {
        "portfolio": primary_name,
        "var_95": mc["var_95"],
        "cvar_95": mc["cvar_95"],
    }

    return {
        "optimizer": optimizer,
        "frontier": frontier,
        "results": results,
        "metrics": metrics,
        "monte_carlo": mc,
        "mc_summary": mc_summary,
        "primary": primary_name,
    }


def print_report(analysis: dict, config: AnalysisConfig) -> None:
    """Print a human-readable console report from a run_analysis result."""
    results = analysis["results"]
    metrics = analysis["metrics"]
    tickers = config.tickers

    print("=" * 60)
    print("Portfolio Optimization Engine")
    print("=" * 60)

    for name, res in results.items():
        print("\n" + "-" * 60)
        print(name.replace("_", " ").upper())
        print("-" * 60)
        print(f"  Expected Return:  {res.expected_return:.2%}")
        print(f"  Volatility:       {res.volatility:.2%}")
        print(f"  Sharpe Ratio:     {res.sharpe_ratio:.2f}")
        if res.sortino_ratio is not None:
            print(f"  Sortino Ratio:    {res.sortino_ratio:.2f}")
        m = metrics.get(name)
        if m is not None:
            print(f"  CAGR:             {m.cagr:.2%}")
            print(f"  Max Drawdown:     {m.max_drawdown:.2%}")
            print(f"  Calmar Ratio:     {m.calmar_ratio:.2f}")
            print(f"  Omega Ratio:      {m.omega_ratio:.2f}")
            if m.beta is not None:
                print(f"  Beta:             {m.beta:.2f}")
                print(f"  Alpha:            {m.alpha:.2%}")
        print("  Weights:")
        for ticker, weight in zip(tickers, res.weights, strict=False):
            if abs(weight) > 0.01:
                print(f"    {ticker:6s}: {weight:.2%}")

    mc = analysis["mc_summary"]
    print("\n" + "-" * 60)
    print(f"MONTE CARLO (on {mc['portfolio']})")
    print("-" * 60)
    print(f"  1-Year VaR (95%):  ${mc['var_95']:,.0f}")
    print(f"  1-Year CVaR (95%): ${mc['cvar_95']:,.0f}")
