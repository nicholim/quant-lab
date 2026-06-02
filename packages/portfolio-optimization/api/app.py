"""Thin FastAPI demo wrapper around the portfolio-optimization-engine public API.

This module is a *demo surface* for hosting on Render. It does **not** reimplement
any optimization logic: it constructs a ``PortfolioOptimizer`` and drives it through
the same injected-returns contract the backtesting-framework uses
(``OptimizationRebalanceStrategy``) — set ``.returns`` / ``.mean_returns`` /
``.cov_matrix`` and call an existing ``optimize_*`` method.

Run locally:
    uvicorn api.app:app --reload

The demo accepts a matrix of *daily* asset returns so it works fully offline (no
Yahoo Finance call). Annualization (x252) matches ``PortfolioOptimizer.calculate_returns``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from portfolio_optimization_engine.optimizer import PortfolioOptimizer

app = FastAPI(
    title="Portfolio Optimization Engine — Demo API",
    description=(
        "Thin demo wrapper over the PortfolioOptimizer public API "
        "(Modern Portfolio Theory: max Sharpe, min volatility, risk parity, "
        "max Sortino, min CVaR). Send daily returns; get optimal weights + metrics."
    ),
    version="0.1.0",
)

# Map a stable, documented objective name -> the existing optimizer method.
# Keys are part of the demo's surface; values are the untouched public API.
_OBJECTIVES = {
    "sharpe": "optimize_sharpe",
    "min_volatility": "optimize_min_volatility",
    "risk_parity": "optimize_risk_parity",
    "sortino": "optimize_sortino",
    "min_cvar": "optimize_min_cvar",
}


class OptimizeRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, description="Asset tickers/labels (length n).")
    returns: list[list[float]] = Field(
        ...,
        description=(
            "Daily returns matrix shaped (T, n): one row per period, one column "
            "per ticker (same order as `tickers`)."
        ),
    )
    objective: str = Field(
        "sharpe",
        description=f"One of: {', '.join(_OBJECTIVES)}.",
    )
    risk_free_rate: float = Field(0.02, description="Annual risk-free rate.")

    model_config = {
        "json_schema_extra": {
            "example": {
                "tickers": ["AAPL", "MSFT", "GOOG"],
                "returns": [
                    [0.001, -0.002, 0.0005],
                    [0.0003, 0.0011, -0.0007],
                    [-0.0008, 0.0004, 0.0012],
                ],
                "objective": "sharpe",
                "risk_free_rate": 0.02,
            }
        }
    }


class OptimizeResponse(BaseModel):
    objective: str
    tickers: list[str]
    weights: dict[str, float]
    expected_return: float
    volatility: float
    sharpe_ratio: float
    sortino_ratio: float | None = None
    cvar: float | None = None


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by Render's health check."""
    return {"status": "ok"}


@app.get("/objectives")
def objectives() -> dict[str, list[str]]:
    """List the optimization objectives this demo exposes."""
    return {"objectives": list(_OBJECTIVES)}


@app.post("/optimize", response_model=OptimizeResponse)
def optimize(req: OptimizeRequest) -> OptimizeResponse:
    """Optimize a portfolio from a daily-returns matrix.

    Reuses ``PortfolioOptimizer`` via the injected-returns contract; no
    optimization logic is duplicated here.
    """
    method_name = _OBJECTIVES.get(req.objective)
    if method_name is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown objective '{req.objective}'. Valid: {list(_OBJECTIVES)}",
        )

    returns_arr = np.asarray(req.returns, dtype=float)
    if returns_arr.ndim != 2 or returns_arr.shape[1] != len(req.tickers):
        raise HTTPException(
            status_code=422,
            detail=(
                f"`returns` must be shaped (T, {len(req.tickers)}) to match "
                f"{len(req.tickers)} tickers; got {returns_arr.shape}."
            ),
        )
    if returns_arr.shape[0] < 2:
        raise HTTPException(status_code=422, detail="`returns` needs at least 2 periods.")

    returns_df = pd.DataFrame(returns_arr, columns=req.tickers)

    # Same construction pattern as the backtester: inject returns, then optimize.
    optimizer = PortfolioOptimizer(
        req.tickers, "1970-01-01", "1970-01-02", risk_free_rate=req.risk_free_rate
    )
    optimizer.returns = returns_df
    optimizer.mean_returns = returns_df.mean() * 252
    optimizer.cov_matrix = returns_df.cov() * 252

    try:
        result = getattr(optimizer, method_name)()
    except ValueError as exc:
        # Solver/feasibility failures from the existing API -> client error.
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    weights = {t: float(w) for t, w in zip(req.tickers, result.weights, strict=False)}
    return OptimizeResponse(
        objective=result.objective or req.objective,
        tickers=req.tickers,
        weights=weights,
        expected_return=result.expected_return,
        volatility=result.volatility,
        sharpe_ratio=result.sharpe_ratio,
        sortino_ratio=result.sortino_ratio,
        cvar=result.cvar,
    )
