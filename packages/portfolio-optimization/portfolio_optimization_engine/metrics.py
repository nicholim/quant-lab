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


_EULER_MASCHERONI = 0.5772156649015329


def _normal_cdf(x: float) -> float:
    """Standard-normal CDF via the error function (no scipy dependency)."""
    from math import erf, sqrt

    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def probabilistic_sharpe_ratio(
    observed_sr: float,
    benchmark_sr: float,
    n: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probabilistic Sharpe Ratio (Bailey & Lopez de Prado, 2012).

    The probability that the true Sharpe ratio exceeds ``benchmark_sr`` given an
    ``observed_sr`` estimated from ``n`` returns. All Sharpe ratios must be in the
    SAME (typically non-annualized, per-period) frequency. The closed form uses
    the standard error of the SR estimator under non-normal returns::

        sigma_SR = sqrt((1 - skew*SR + (kurtosis - 1)/4 * SR^2) / (n - 1))
        PSR      = Phi((SR_obs - SR_bench) / sigma_SR)

    Args:
        observed_sr: estimated Sharpe ratio (same frequency as ``benchmark_sr``).
        benchmark_sr: Sharpe ratio to beat (often 0).
        n: number of return observations (must be >= 2).
        skew: sample skewness of the returns (0 for normal).
        kurtosis: sample kurtosis of the returns; NON-excess (3.0 for normal).

    Returns:
        Probability in [0, 1].

    Raises:
        ValueError: if ``n < 2``.
    """
    if n < 2:
        raise ValueError("n must be >= 2")
    var = 1.0 - skew * observed_sr + (kurtosis - 1.0) / 4.0 * observed_sr**2
    # Numerical guard: the variance term can go slightly negative for extreme
    # skew/kurtosis/SR combinations; clamp to a tiny positive value.
    var = max(var, 1e-12)
    sigma_sr = (var / (n - 1)) ** 0.5
    if sigma_sr < 1e-12:
        return 1.0 if observed_sr > benchmark_sr else 0.0
    return _normal_cdf((observed_sr - benchmark_sr) / sigma_sr)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum of ``n_trials`` independent Sharpe estimates.

    Gaussian extreme-value approximation (Bailey & Lopez de Prado, 2014) used to
    derive the deflation benchmark::

        E[max SR] = sqrt(var_SR) * ((1 - gamma) * Phi^-1(1 - 1/N)
                    + gamma * Phi^-1(1 - 1/(N*e)))

    where ``gamma`` is the Euler-Mascheroni constant and ``var_SR`` is the
    variance of the SR estimates ACROSS the ``n_trials`` (assumed mean-zero true
    SRs). For ``n_trials <= 1`` this is 0 (no multiple-testing inflation).

    Args:
        n_trials: number of independent strategy configurations tried.
        sr_variance: variance of the trial Sharpe ratios (same frequency as the
            observed SR fed to :func:`deflated_sharpe_ratio`).
    """
    if n_trials <= 1:
        return 0.0
    if sr_variance <= 0.0:
        return 0.0
    from math import e

    # Inverse normal CDF (probit) via the stdlib, scipy-free: statistics.NormalDist
    # provides an exact inv_cdf, so no rational approximation is needed.
    from statistics import NormalDist

    nd = NormalDist()
    q1 = nd.inv_cdf(1.0 - 1.0 / n_trials)
    q2 = nd.inv_cdf(1.0 - 1.0 / (n_trials * e))
    return (sr_variance**0.5) * ((1.0 - _EULER_MASCHERONI) * q1 + _EULER_MASCHERONI * q2)


def deflated_sharpe_ratio(
    observed_sr: float,
    n: int,
    n_trials: int,
    sr_variance: float,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

    A PSR whose benchmark is the EXPECTED MAXIMUM Sharpe ratio achievable by
    pure luck across ``n_trials`` independent backtests. Higher ``n_trials`` ->
    higher benchmark -> lower DSR, correcting for selection bias in a parameter
    sweep. With ``n_trials == 1`` this reduces to ``PSR(observed_sr, 0, ...)``.

    Args:
        observed_sr: Sharpe of the SELECTED (e.g. best) configuration, per-period.
        n: number of return observations behind ``observed_sr``.
        n_trials: number of configurations tried (e.g. grid size).
        sr_variance: variance of the trial Sharpe ratios. Estimate it as the
            sample variance of the per-period Sharpes of all tried configs; it is
            the honest input the deflation needs and is the caller's responsibility.
        skew: sample skewness of the selected config's returns.
        kurtosis: sample (non-excess) kurtosis of the selected config's returns.

    Returns:
        Probability in [0, 1]; always <= the corresponding PSR for ``n_trials > 1``.
    """
    benchmark_sr = expected_max_sharpe(n_trials, sr_variance)
    return probabilistic_sharpe_ratio(observed_sr, benchmark_sr, n, skew, kurtosis)


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
