"""Covariance estimators (sample + Ledoit-Wolf shrinkage).

The sample covariance of daily returns is noisy when the number of assets is
large relative to the sample length, which makes mean-variance optimization
unstable (it tilts heavily into the lowest-eigenvalue directions of the noisy
estimate). Ledoit-Wolf shrinkage pulls the sample covariance toward a structured
target with an analytically optimal intensity, trading a little bias for a large
reduction in estimation error.

This module is **opt-in**: nothing here runs unless a caller explicitly asks for
it (see ``PortfolioOptimizer.calculate_returns(shrinkage=...)``). The default
optimizer path computes the plain sample covariance exactly as before, preserving
metrics parity with the backtester.

Implemented with numpy only -- no scikit-learn dependency.
"""

from __future__ import annotations

import numpy as np

ShrinkageTarget = str  # "constant_correlation" | "identity"


def _as_returns_matrix(returns) -> np.ndarray:
    """Coerce a DataFrame/ndarray of returns to a (T, n) float matrix."""
    arr = np.asarray(getattr(returns, "values", returns), dtype=float)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.ndim != 2:
        raise ValueError("returns must be a 2-D (T, n) array of asset returns")
    return arr


def constant_correlation_target(sample_cov: np.ndarray) -> np.ndarray:
    """Constant-correlation target: keep variances, replace correlations with mean.

    The off-diagonals use the average sample correlation ``r_bar`` scaled by the
    geometric mean of the two assets' standard deviations.
    """
    var = np.diag(sample_cov)
    std = np.sqrt(var)
    denom = np.outer(std, std)
    # avoid division by zero for degenerate (zero-variance) assets
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.where(denom > 0, sample_cov / denom, 0.0)
    n = sample_cov.shape[0]
    if n > 1:
        off = corr[~np.eye(n, dtype=bool)]
        r_bar = float(off.mean()) if off.size else 0.0
    else:
        r_bar = 0.0
    target = r_bar * denom
    np.fill_diagonal(target, var)
    return target


def identity_target(sample_cov: np.ndarray) -> np.ndarray:
    """Scaled-identity target: ``mu * I`` where ``mu`` is the average variance."""
    n = sample_cov.shape[0]
    mu = float(np.trace(sample_cov) / n) if n else 0.0
    return mu * np.eye(n)


def _shrinkage_target(sample_cov: np.ndarray, target: ShrinkageTarget) -> np.ndarray:
    if target == "constant_correlation":
        return constant_correlation_target(sample_cov)
    if target == "identity":
        return identity_target(sample_cov)
    raise ValueError(
        f"Unknown shrinkage target {target!r}; use 'constant_correlation' or 'identity'"
    )


def ledoit_wolf_shrinkage(
    returns,
    target: ShrinkageTarget = "constant_correlation",
) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf shrinkage of the per-period sample covariance.

    Parameters
    ----------
    returns:
        A ``(T, n)`` array (or DataFrame) of **per-period** asset returns. The
        covariance is computed on this raw series; annualization (if any) is the
        caller's responsibility, so the returned matrix is on the same scale as
        ``np.cov(returns, rowvar=False)``.
    target:
        ``"constant_correlation"`` (default) or ``"identity"``.

    Returns
    -------
    (shrunk_cov, intensity):
        ``shrunk_cov = intensity * F + (1 - intensity) * S`` where ``S`` is the
        (biased, MLE) sample covariance and ``F`` is the structured target.
        ``intensity`` is clipped to ``[0, 1]``.

    Notes
    -----
    The optimal intensity follows Ledoit & Wolf (2003/2004): ``delta* =
    (pi - rho) / gamma / T`` where ``pi`` estimates the sum of asymptotic
    variances of the sample-covariance entries, ``rho`` the covariance between
    the entries and the target, and ``gamma`` the squared Frobenius distance
    between ``S`` and ``F``.
    """
    X = _as_returns_matrix(returns)
    T, n = X.shape
    if T < 2:
        raise ValueError("Need at least 2 observations for shrinkage")

    mean = X.mean(axis=0)
    Xc = X - mean
    # MLE sample covariance (divide by T, matching the Ledoit-Wolf derivation)
    S = (Xc.T @ Xc) / T
    F = _shrinkage_target(S, target)

    # gamma: squared Frobenius norm of (F - S)
    gamma = float(np.sum((F - S) ** 2))
    if gamma <= 0 or n == 1:
        # nothing to shrink toward (already equal to target, or single asset)
        return S.copy(), 0.0

    # pi: sum over (i, j) of asymptotic variance of sqrt(T) * S_ij
    Xc2 = Xc**2
    pi_mat = (Xc2.T @ Xc2) / T - S**2
    pi = float(np.sum(pi_mat))

    # rho: estimate of sum of asymptotic covariances of the target with S.
    # Diagonal terms contribute pi_ii; off-diagonal terms use the Ledoit-Wolf
    # constant-correlation adjustment. For the identity target the off-diagonal
    # target entries are constant, so their cross-asymptotic-cov term is 0.
    var = np.diag(S)
    std = np.sqrt(var)
    rho_diag = float(np.sum(np.diag(pi_mat)))

    if target == "constant_correlation":
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = np.where(np.outer(std, std) > 0, S / np.outer(std, std), 0.0)
        off = corr[~np.eye(n, dtype=bool)]
        r_bar = float(off.mean()) if off.size else 0.0

        # theta_ij = (1/T) sum_t [ (x_it^2 - S_ii) * (x_it x_jt - S_ij) ]
        term = np.zeros((n, n))
        for i in range(n):
            xi2 = Xc2[:, i]
            for j in range(n):
                if i == j:
                    continue
                term[i, j] = float(np.mean((xi2 - S[i, i]) * (Xc[:, i] * Xc[:, j] - S[i, j])))
        rho_off = 0.0
        for i in range(n):
            for j in range(n):
                if i == j:
                    continue
                sij = std[i] / std[j] if std[j] > 0 else 0.0
                sji = std[j] / std[i] if std[i] > 0 else 0.0
                rho_off += (r_bar / 2.0) * (sij * term[i, j] + sji * term[j, i])
        rho = rho_diag + rho_off
    else:  # identity target -> off-diagonal contribution is 0
        rho = rho_diag

    kappa = (pi - rho) / gamma
    intensity = max(0.0, min(1.0, kappa / T))

    shrunk = intensity * F + (1.0 - intensity) * S
    # enforce exact symmetry against floating-point drift
    shrunk = 0.5 * (shrunk + shrunk.T)
    return shrunk, float(intensity)
