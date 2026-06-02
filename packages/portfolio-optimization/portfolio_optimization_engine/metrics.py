"""Performance metrics for return series.

Pure functions operating on a pandas Series (or 1-D ndarray) of *periodic*
(e.g. daily) returns. This module has no dependency on the optimizer and does no
I/O, plotting, or network access, so it can be imported standalone — including by
a separate backtest framework — via
``from portfolio_optimization_engine.metrics import compute_metrics``.

Two conventions are used deliberately and documented per function:

* Annualization: ``annualized_return`` is arithmetic (``mean * periods``) to match
  ``PortfolioOptimizer.mean_returns``; ``cagr`` is geometric. They differ on purpose.
* Risk-free rate: Sharpe/Sortino/Omega use a per-period geometric rate
  ``(1 + rf)**(1/P) - 1``; ``alpha`` works in annual space with the raw annual rate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PerformanceMetrics:
    """Container for a portfolio's risk/return statistics.

    ``max_drawdown`` is reported as a non-positive number (e.g. -0.32 for a 32%
    drawdown, 0.0 if the equity curve never falls). ``beta``/``alpha`` are ``None``
    when no benchmark is supplied.
    """

    cagr: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    omega_ratio: float
    beta: float | None = None
    alpha: float | None = None

    def as_dict(self) -> dict[str, float | None]:
        return asdict(self)


def _as_array(returns) -> np.ndarray:
    """Coerce to a 1-D float array and drop NaNs."""
    arr = np.asarray(returns, dtype=float).ravel()
    arr = arr[~np.isnan(arr)]
    return arr


def cumulative_returns(returns) -> np.ndarray:
    """Equity curve from periodic returns: ``cumprod(1 + r)``."""
    r = _as_array(returns)
    return np.cumprod(1.0 + r)


def cagr(returns, periods_per_year: int = 252) -> float:
    """Compound annual growth rate (geometric)."""
    r = _as_array(returns)
    n = r.size
    if n == 0:
        raise ValueError("returns is empty")
    wealth = float(np.prod(1.0 + r))
    if wealth <= 0:
        return -1.0
    years = n / periods_per_year
    return wealth ** (1.0 / years) - 1.0


def annualized_return(returns, periods_per_year: int = 252) -> float:
    """Arithmetic annualized return (``mean * periods``), matching the optimizer."""
    r = _as_array(returns)
    if r.size == 0:
        raise ValueError("returns is empty")
    return float(np.mean(r)) * periods_per_year


def annualized_volatility(returns, periods_per_year: int = 252) -> float:
    """Annualized standard deviation of periodic returns."""
    r = _as_array(returns)
    if r.size < 2:
        return 0.0
    return float(np.std(r, ddof=1)) * np.sqrt(periods_per_year)


def max_drawdown(returns) -> float:
    """Worst peak-to-trough decline on the equity curve. Non-positive (0.0 if none)."""
    equity = cumulative_returns(returns)
    if equity.size == 0:
        raise ValueError("returns is empty")
    running_max = np.maximum.accumulate(equity)
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def sharpe_ratio(returns, risk_free_rate: float = 0.02, periods_per_year: int = 252) -> float:
    """Annualized Sharpe ratio from per-period excess returns."""
    r = _as_array(returns)
    if r.size < 2:
        return 0.0
    rf_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = r - rf_period
    sd = np.std(excess, ddof=1)
    if sd < 1e-12:  # effectively zero volatility (guards against fp dust)
        return 0.0
    return float(np.mean(excess) / sd) * np.sqrt(periods_per_year)


def sortino_ratio(returns, risk_free_rate: float = 0.02, periods_per_year: int = 252) -> float:
    """Annualized Sortino ratio (downside deviation below the per-period rf)."""
    r = _as_array(returns)
    if r.size == 0:
        raise ValueError("returns is empty")
    rf_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = r - rf_period
    downside = np.minimum(excess, 0.0)
    downside_dev = np.sqrt(np.mean(downside**2))
    mean_excess = float(np.mean(excess))
    if downside_dev < 1e-12:
        return float("inf") if mean_excess > 0 else 0.0
    return mean_excess / downside_dev * np.sqrt(periods_per_year)


def calmar_ratio(returns, periods_per_year: int = 252) -> float:
    """CAGR divided by the magnitude of max drawdown."""
    mdd = max_drawdown(returns)
    growth = cagr(returns, periods_per_year)
    if mdd == 0:
        return float("inf") if growth > 0 else 0.0
    return growth / abs(mdd)


def omega_ratio(returns, threshold: float = 0.0) -> float:
    """Probability-weighted gains over losses relative to a per-period threshold."""
    r = _as_array(returns)
    if r.size == 0:
        raise ValueError("returns is empty")
    diff = r - threshold
    gains = diff[diff > 0].sum()
    losses = -diff[diff < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else float("nan")
    return float(gains / losses)


def beta(returns, benchmark) -> float:
    """Beta vs benchmark: ``cov(r, b) / var(b)``."""
    r = _as_array(returns)
    b = _as_array(benchmark)
    if r.size != b.size or r.size < 2:
        raise ValueError("returns and benchmark must be equal length (>= 2)")
    cov = np.cov(r, b, ddof=1)
    var_b = cov[1, 1]
    if var_b == 0:
        return float("nan")
    return float(cov[0, 1] / var_b)


def alpha(returns, benchmark, risk_free_rate: float = 0.02, periods_per_year: int = 252) -> float:
    """Annualized Jensen's alpha (CAPM) vs benchmark."""
    beta_val = beta(returns, benchmark)
    ann_r = annualized_return(returns, periods_per_year)
    ann_b = annualized_return(benchmark, periods_per_year)
    return (ann_r - risk_free_rate) - beta_val * (ann_b - risk_free_rate)


def compute_metrics(
    returns,
    benchmark=None,
    risk_free_rate: float = 0.02,
    periods_per_year: int = 252,
) -> PerformanceMetrics:
    """Compute the full metrics panel for a periodic return series.

    Args:
        returns: pandas Series or 1-D array of periodic returns.
        benchmark: optional benchmark return series for beta/alpha. If both this
            and ``returns`` are pandas Series they are inner-joined on index first.
        risk_free_rate: annual risk-free rate.
        periods_per_year: periods per year (252 for daily).

    Raises:
        ValueError: if ``returns`` is empty.
    """
    if isinstance(returns, pd.Series) and isinstance(benchmark, pd.Series):
        aligned = pd.concat([returns, benchmark], axis=1, join="inner").dropna()
        r = aligned.iloc[:, 0].to_numpy(dtype=float)
        b = aligned.iloc[:, 1].to_numpy(dtype=float)
    else:
        r = _as_array(returns)
        b = _as_array(benchmark) if benchmark is not None else None

    if r.size == 0:
        raise ValueError("returns is empty")

    rf_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0

    beta_val = alpha_val = None
    if b is not None and b.size == r.size and b.size >= 2:
        beta_val = beta(r, b)
        alpha_val = alpha(r, b, risk_free_rate, periods_per_year)

    return PerformanceMetrics(
        cagr=cagr(r, periods_per_year),
        annualized_return=annualized_return(r, periods_per_year),
        annualized_volatility=annualized_volatility(r, periods_per_year),
        max_drawdown=max_drawdown(r),
        sharpe_ratio=sharpe_ratio(r, risk_free_rate, periods_per_year),
        sortino_ratio=sortino_ratio(r, risk_free_rate, periods_per_year),
        calmar_ratio=calmar_ratio(r, periods_per_year),
        omega_ratio=omega_ratio(r, rf_period),
        beta=beta_val,
        alpha=alpha_val,
    )
