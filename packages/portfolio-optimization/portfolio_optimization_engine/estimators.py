"""Expected-return (mean) estimators.

The sample mean of historical returns is a famously noisy estimate of expected
return -- mean-variance optimizers amplify that noise into unstable, extreme
allocations. This module offers two more robust estimators that the optimizer
can opt into for the *mean* vector, mirroring how :mod:`covariance` offers
robust estimators for the second moment:

* :func:`ewma_mean` -- exponentially weights recent observations more heavily.
* :func:`james_stein_mean` -- shrinks the sample mean toward the grand mean with
  the positive-part Efron-Morris intensity.

This module is **opt-in**: nothing here runs unless a caller explicitly requests
it (see ``PortfolioOptimizer.calculate_returns(mean_estimator=...)``). The
default optimizer path uses the plain sample mean exactly as before, preserving
metrics parity with the backtester.

Implemented with numpy only -- no scikit-learn dependency.
"""

from __future__ import annotations

import numpy as np


def _as_returns_matrix(returns) -> np.ndarray:
    """Coerce a DataFrame/ndarray of returns to a (T, n) float matrix."""
    arr = np.asarray(getattr(returns, "values", returns), dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.ndim != 2:
        raise ValueError("returns must be a 2-D (T, n) array of asset returns")
    return arr


def ewma_mean(returns, lam: float = 0.94) -> np.ndarray:
    """Exponentially-weighted moving-average mean of per-period returns.

    Period ``t`` (counting from the most recent row backward) gets weight
    proportional to ``lam**k`` where ``k`` is its lag from the end of the sample,
    so the latest observations dominate.

    Parameters
    ----------
    returns:
        A ``(T, n)`` array (or DataFrame) of **per-period** asset returns.
    lam:
        Decay factor in ``(0, 1)``; closer to 1 = longer memory.

    Returns
    -------
    np.ndarray
        The length-``n`` EWMA mean on the same (per-period) scale as
        ``returns.mean(axis=0)``.
    """
    if not 0.0 < lam < 1.0:
        raise ValueError("lam must be in the open interval (0, 1)")
    X = _as_returns_matrix(returns)
    T = X.shape[0]
    if T < 1:
        raise ValueError("Need at least 1 observation for EWMA mean")
    lags = np.arange(T - 1, -1, -1, dtype=float)
    w = lam**lags
    w /= w.sum()
    return w @ X


def james_stein_mean(returns) -> np.ndarray:
    """Positive-part James-Stein (Efron-Morris) shrinkage of the sample mean.

    Shrinks each asset's sample mean toward the grand mean (the average across
    assets) by an intensity estimated from the data, reducing the estimation
    error of the joint mean vector. The intensity is clipped to ``[0, 1]`` (the
    "positive-part" variant), so the shrunk mean always lies between the sample
    mean and the grand mean.

    Parameters
    ----------
    returns:
        A ``(T, n)`` array (or DataFrame) of **per-period** asset returns.

    Returns
    -------
    np.ndarray
        The length-``n`` shrunk mean, on the same per-period scale as the input.
    """
    X = _as_returns_matrix(returns)
    T, n = X.shape
    if T < 2:
        raise ValueError("Need at least 2 observations for James-Stein shrinkage")

    sample_mean = X.mean(axis=0)
    grand_mean = float(sample_mean.mean())

    if n < 3:
        # James-Stein dominance needs n >= 3; below that, return the sample mean.
        return sample_mean

    # Per-asset variance of the sample mean (sigma_i^2 / T), use a pooled scalar
    # sigma^2 (average variance of the mean) for the standard EM intensity.
    var = X.var(axis=0, ddof=1)
    sigma2 = float(var.mean()) / T

    dispersion = float(np.sum((sample_mean - grand_mean) ** 2))
    if dispersion <= 0:
        return sample_mean

    # Efron-Morris shrinkage factor toward the grand mean.
    shrink = (n - 3) * sigma2 / dispersion
    shrink = max(0.0, min(1.0, shrink))

    return grand_mean + (1.0 - shrink) * (sample_mean - grand_mean)


# Estimator names accepted by ``estimate_mean``.
MeanEstimator = str  # "sample" | "ewma" | "james_stein"


def estimate_mean(returns, estimator: str = "sample", *, lam: float = 0.94) -> np.ndarray:
    """Dispatch to a per-period mean estimator by name.

    Parameters
    ----------
    returns:
        A ``(T, n)`` array (or DataFrame) of **per-period** asset returns.
    estimator:
        One of ``"sample"`` (plain ``mean(axis=0)``), ``"ewma"``, or
        ``"james_stein"``.
    lam:
        Decay factor forwarded to the EWMA estimator (ignored otherwise).

    Returns
    -------
    np.ndarray
        The length-``n`` per-period mean.
    """
    if estimator == "sample":
        return _as_returns_matrix(returns).mean(axis=0)
    if estimator == "ewma":
        return ewma_mean(returns, lam=lam)
    if estimator == "james_stein":
        return james_stein_mean(returns)
    raise ValueError(
        f"Unknown mean estimator {estimator!r}; use 'sample', 'ewma', or 'james_stein'"
    )
