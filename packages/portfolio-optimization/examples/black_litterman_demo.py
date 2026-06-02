"""Black-Litterman end-to-end demo: prior -> view -> posterior -> weights.

Black-Litterman is **not** a plain CLI ``--objective`` because it needs investor
*views* (a pick matrix ``P`` and target returns ``Q``), which don't fit the flat
argparse surface. It is exposed two other ways:

* the FastAPI demo's ``POST /optimize/black-litterman`` endpoint (accepts a
  ``views`` list), and
* the library API used below (``optimize_black_litterman`` / ``black_litterman_returns``).

This script runs fully offline (injects a synthetic returns matrix via the same
contract the FastAPI demo and the backtester use) and prints the three stages:

1. the market-implied **equilibrium prior** (no views),
2. a **bullish view** on one asset shifting its **posterior** expected return up,
3. the resulting **optimized weights**, showing the tilt toward the favored asset.

    python examples/black_litterman_demo.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio_optimization_engine.optimizer import PortfolioOptimizer

TICKERS = ["AAPL", "MSFT", "GOOG", "JPM"]


def make_returns(seed: int = 0, periods: int = 750) -> pd.DataFrame:
    """Synthetic but correlated daily returns so the demo is deterministic."""
    rng = np.random.default_rng(seed)
    mu = np.array([0.0007, 0.0006, 0.0005, 0.0004])
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
    return pd.DataFrame(rng.multivariate_normal(mu, cov, size=periods), columns=TICKERS)


def main() -> None:
    returns = make_returns()

    # Inject returns (offline contract) instead of fetch_data()/calculate_returns().
    opt = PortfolioOptimizer(TICKERS, "2021-01-01", "2024-01-01", risk_free_rate=0.02)
    opt.returns = returns
    opt.mean_returns = returns.mean() * 252
    opt.cov_matrix = returns.cov() * 252

    # Stage 1 -- equilibrium prior (no views): the posterior equals Pi.
    prior = opt.black_litterman_returns()
    print("Equilibrium prior (annualized excess returns):")
    for t, r in prior.items():
        print(f"  {t:5s}: {r:+.2%}")

    # Stage 2 -- a bullish ABSOLUTE view on AAPL: E[R_AAPL] = prior + 8%.
    #   P picks AAPL (row [1, 0, 0, 0]); Q is the view's target return.
    p = np.array([[1.0, 0.0, 0.0, 0.0]])
    q = np.array([prior["AAPL"] + 0.08])
    posterior = opt.black_litterman_returns(p, q)
    print("\nWith a bullish AAPL view (prior + 8%), posterior shifts up:")
    for t in TICKERS:
        arrow = "  <-- view" if t == "AAPL" else ""
        print(f"  {t:5s}: {prior[t]:+.2%}  ->  {posterior[t]:+.2%}{arrow}")

    # Stage 3 -- optimize on each.
    base = opt.optimize_black_litterman()
    tilted = opt.optimize_black_litterman(p, q)
    print("\nOptimized weights (equilibrium vs. bullish-AAPL posterior):")
    print(f"{'Ticker':<8}{'equilibrium':>14}{'with view':>14}")
    for t, wb, wt in zip(TICKERS, base.weights, tilted.weights, strict=False):
        print(f"{t:<8}{wb:>13.2%}{wt:>14.2%}")
    print(
        f"\nAAPL weight rose from {base.weights[0]:.2%} to {tilted.weights[0]:.2%} "
        "as the bullish view pulled its posterior return up."
    )


if __name__ == "__main__":
    main()
