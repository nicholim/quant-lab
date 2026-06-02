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
        "max Sortino, min CVaR, Hierarchical Risk Parity, and Black-Litterman). "
        "Send daily returns; get optimal weights + metrics."
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
    "hrp": "optimize_hrp",
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


class BLView(BaseModel):
    """A single Black-Litterman view: ``sum_i assets[ticker_i] * E[R_i] = q``.

    ``assets`` maps tickers to their loading in the view's pick row (use ``1.0``
    for an absolute view on one asset, or e.g. ``{"AAPL": 1.0, "MSFT": -1.0}`` for
    a relative "AAPL will outperform MSFT" view). ``q`` is the annualized expected
    return of the view. ``confidence`` (optional, in ``(0, 1]``) scales the view's
    uncertainty: higher = more certain = the posterior tilts further toward ``q``.
    Omitting ``confidence`` uses the standard ``diag(tau P Sigma P^T)`` default.
    """

    assets: dict[str, float] = Field(
        ..., description="Ticker -> loading in this view's pick row (P matrix row)."
    )
    q: float = Field(..., description="Annualized expected return of the view (Q entry).")
    confidence: float | None = Field(
        None, gt=0.0, le=1.0, description="Optional confidence in (0, 1]; higher = more certain."
    )


class BlackLittermanRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, description="Asset tickers/labels (length n).")
    returns: list[list[float]] = Field(
        ..., description="Daily returns matrix shaped (T, n), columns ordered like `tickers`."
    )
    views: list[BLView] = Field(
        default_factory=list,
        description=(
            "Investor views. With NO views the optimization runs on the "
            "market-implied equilibrium prior (the documented default)."
        ),
    )
    market_weights: dict[str, float] | None = Field(
        None,
        description=(
            "Neutral market portfolio for the equilibrium prior "
            "(Pi = delta * Sigma @ w_mkt). Defaults to equal-weight."
        ),
    )
    tau: float = Field(0.05, gt=0.0, description="Weight on the prior covariance (tau * Sigma).")
    risk_aversion: float = Field(2.5, gt=0.0, description="Risk-aversion delta for the prior.")
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
                "views": [{"assets": {"AAPL": 1.0}, "q": 0.20, "confidence": 0.6}],
                "tau": 0.05,
                "risk_aversion": 2.5,
                "risk_free_rate": 0.02,
            }
        }
    }


class BlackLittermanResponse(OptimizeResponse):
    """Adds the posterior expected-returns vector that drove the allocation."""

    posterior_returns: dict[str, float]
    prior_returns: dict[str, float]


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by Render's health check."""
    return {"status": "ok"}


@app.get("/objectives")
def objectives() -> dict[str, list[str]]:
    """List the optimization objectives this demo exposes.

    The ``objectives`` list are the values accepted by ``POST /optimize``.
    Black-Litterman needs views (P/Q), which don't fit the flat ``/optimize``
    body, so it has its own typed endpoint at ``POST /optimize/black-litterman``
    (listed under ``other``).
    """
    return {"objectives": list(_OBJECTIVES), "other": ["black_litterman"]}


def _build_optimizer(
    tickers: list[str], returns: list[list[float]], risk_free_rate: float
) -> PortfolioOptimizer:
    """Validate the returns matrix and build an injected-returns optimizer.

    Mirrors the backtester's contract: set ``.returns`` / ``.mean_returns`` /
    ``.cov_matrix`` directly (no network) before calling any ``optimize_*``.
    """
    returns_arr = np.asarray(returns, dtype=float)
    if returns_arr.ndim != 2 or returns_arr.shape[1] != len(tickers):
        raise HTTPException(
            status_code=422,
            detail=(
                f"`returns` must be shaped (T, {len(tickers)}) to match "
                f"{len(tickers)} tickers; got {returns_arr.shape}."
            ),
        )
    if returns_arr.shape[0] < 2:
        raise HTTPException(status_code=422, detail="`returns` needs at least 2 periods.")

    returns_df = pd.DataFrame(returns_arr, columns=tickers)
    optimizer = PortfolioOptimizer(
        tickers, "1970-01-01", "1970-01-02", risk_free_rate=risk_free_rate
    )
    optimizer.returns = returns_df
    optimizer.mean_returns = returns_df.mean() * 252
    optimizer.cov_matrix = returns_df.cov() * 252
    return optimizer


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

    optimizer = _build_optimizer(req.tickers, req.returns, req.risk_free_rate)

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


def _build_views(
    views: list[BLView], tickers: list[str], cov, tau: float
) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
    """Translate the view payload into (P, Q, omega) for ``optimize_black_litterman``.

    Returns ``(None, None, None)`` when there are no views, so the optimization
    runs on the equilibrium prior. ``confidence`` (if given on every view) scales
    the default ``diag(tau P Sigma P^T)`` uncertainty by ``1/confidence`` -- a more
    confident view (closer to 1) yields tighter uncertainty and a stronger tilt.
    """
    if not views:
        return None, None, None
    idx = {t: i for i, t in enumerate(tickers)}
    n = len(tickers)
    p_rows: list[np.ndarray] = []
    q_vals: list[float] = []
    for view in views:
        row = np.zeros(n, dtype=float)
        for ticker, loading in view.assets.items():
            if ticker not in idx:
                raise HTTPException(
                    status_code=422,
                    detail=f"View references unknown ticker '{ticker}'. Known: {tickers}",
                )
            row[idx[ticker]] = float(loading)
        p_rows.append(row)
        q_vals.append(float(view.q))
    p = np.vstack(p_rows)
    q = np.asarray(q_vals, dtype=float)

    # If every view carries a confidence, build a diagonal Omega: start from the
    # standard prior-proportional variance and divide by confidence so a higher
    # confidence -> smaller variance -> stronger pull toward the view.
    if all(v.confidence is not None for v in views):
        sigma = np.asarray(getattr(cov, "values", cov), dtype=float)
        base = np.diag(p @ (tau * sigma) @ p.T).copy()
        base = np.where(base > 1e-12, base, 1e-12)
        conf = np.array([float(v.confidence) for v in views])  # type: ignore[arg-type]
        omega: np.ndarray | None = np.diag(base / conf)
    else:
        omega = None  # falls back to the library default inside black_litterman
    return p, q, omega


@app.post("/optimize/black-litterman", response_model=BlackLittermanResponse)
def optimize_bl(req: BlackLittermanRequest) -> BlackLittermanResponse:
    """Black-Litterman optimization from a daily-returns matrix + optional views.

    Builds the market-implied equilibrium prior (``Pi = delta * Sigma @ w_mkt``,
    equal-weight ``w_mkt`` by default), blends in any supplied views to form the
    posterior expected returns, then max-Sharpe optimizes on the posterior — all
    via the existing ``PortfolioOptimizer.optimize_black_litterman`` (no logic
    duplicated). With NO views the posterior equals the prior, so the allocation
    is the equilibrium-prior max-Sharpe portfolio.
    """
    optimizer = _build_optimizer(req.tickers, req.returns, req.risk_free_rate)

    if req.market_weights is not None:
        missing = set(req.market_weights) - set(req.tickers)
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"market_weights references unknown tickers: {sorted(missing)}",
            )
        w_mkt: np.ndarray | None = np.array(
            [float(req.market_weights.get(t, 0.0)) for t in req.tickers]
        )
    else:
        w_mkt = None

    p, q, omega = _build_views(req.views, req.tickers, optimizer.cov_matrix, req.tau)

    try:
        prior = optimizer.black_litterman_returns(
            w_mkt=w_mkt, tau=req.tau, risk_aversion=req.risk_aversion
        )
        posterior = optimizer.black_litterman_returns(
            p, q, w_mkt=w_mkt, omega=omega, tau=req.tau, risk_aversion=req.risk_aversion
        )
        result = optimizer.optimize_black_litterman(
            p, q, w_mkt=w_mkt, omega=omega, tau=req.tau, risk_aversion=req.risk_aversion
        )
    except (ValueError, np.linalg.LinAlgError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    weights = {t: float(w) for t, w in zip(req.tickers, result.weights, strict=False)}
    return BlackLittermanResponse(
        objective=result.objective or "black_litterman",
        tickers=req.tickers,
        weights=weights,
        expected_return=result.expected_return,
        volatility=result.volatility,
        sharpe_ratio=result.sharpe_ratio,
        sortino_ratio=result.sortino_ratio,
        cvar=result.cvar,
        posterior_returns={t: float(v) for t, v in posterior.items()},
        prior_returns={t: float(v) for t, v in prior.items()},
    )
