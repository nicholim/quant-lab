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


def ewma_covariance(returns, lam: float = 0.94) -> np.ndarray:
    """Exponentially-weighted moving-average (RiskMetrics) covariance.

    Recent observations carry more weight: period ``t`` (counting from the most
    recent row backward) gets weight ``(1 - lam) * lam**k`` where ``k`` is its lag
    from the end of the sample. This tracks the latest volatility regime more
    responsively than the equally-weighted sample covariance.

    Parameters
    ----------
    returns:
        A ``(T, n)`` array (or DataFrame) of **per-period** asset returns.
    lam:
        Decay factor in ``(0, 1)``. The RiskMetrics default ``0.94`` corresponds
        to a daily horizon; closer to 1 = slower decay (longer memory).

    Returns
    -------
    np.ndarray
        The ``(n, n)`` EWMA covariance on the same (per-period) scale as
        ``np.cov(returns, rowvar=False)``. Symmetric by construction.
    """
    if not 0.0 < lam < 1.0:
        raise ValueError("lam must be in the open interval (0, 1)")
    X = _as_returns_matrix(returns)
    T, n = X.shape
    if T < 2:
        raise ValueError("Need at least 2 observations for EWMA covariance")

    # weights: most recent row (last) gets the largest weight.
    lags = np.arange(T - 1, -1, -1, dtype=float)
    w = (1.0 - lam) * lam**lags
    w /= w.sum()  # normalize so weights sum to 1

    mean = w @ X
    Xc = X - mean
    cov = (Xc * w[:, None]).T @ Xc
    return 0.5 * (cov + cov.T)


def oas_shrinkage(returns) -> tuple[np.ndarray, float]:
    """Oracle Approximating Shrinkage (Chen, Wiesel, Hero 2010), closed form.

    Shrinks the sample covariance toward the scaled identity ``mu * I`` (``mu``
    the mean variance) with the OAS intensity, which converges to the oracle
    (minimum-MSE) shrinkage faster than Ledoit-Wolf under Gaussian assumptions.

    Parameters
    ----------
    returns:
        A ``(T, n)`` array (or DataFrame) of **per-period** asset returns.

    Returns
    -------
    (shrunk_cov, intensity):
        ``shrunk_cov = (1 - rho) * S + rho * mu * I`` where ``S`` is the MLE
        sample covariance and ``rho`` is the OAS intensity clipped to ``[0, 1]``.
    """
    X = _as_returns_matrix(returns)
    T, n = X.shape
    if T < 2:
        raise ValueError("Need at least 2 observations for shrinkage")

    mean = X.mean(axis=0)
    Xc = X - mean
    S = (Xc.T @ Xc) / T  # MLE sample covariance
    mu = float(np.trace(S) / n)
    F = mu * np.eye(n)

    if n == 1:
        return S.copy(), 0.0

    tr_s2 = float(np.sum(S * S))  # trace(S @ S)
    tr_s_2 = float(np.trace(S)) ** 2  # (trace S)^2

    # Chen et al. (2010), eq. (23): closed-form OAS intensity.
    num = (1.0 - 2.0 / n) * tr_s2 + tr_s_2
    den = (T + 1.0 - 2.0 / n) * (tr_s2 - tr_s_2 / n)
    if den <= 0:
        rho = 1.0
    else:
        rho = num / den
    intensity = max(0.0, min(1.0, rho))

    shrunk = (1.0 - intensity) * S + intensity * F
    shrunk = 0.5 * (shrunk + shrunk.T)
    return shrunk, float(intensity)


def mp_denoise(returns) -> np.ndarray:
    """Marchenko-Pastur eigenvalue denoising of the covariance.

    Decomposes the *correlation* matrix, replaces the bulk eigenvalues that fall
    below the Marchenko-Pastur edge ``(1 + sqrt(n / T))**2`` (attributable to
    estimation noise) with their common mean, then rebuilds the correlation
    matrix with a unit diagonal (so its trace is preserved) and scales back to a
    covariance using the original standard deviations. This keeps the
    signal-carrying top eigenvalues while shrinking the noisy bulk, yielding a
    better-conditioned, still-PSD estimate.

    Parameters
    ----------
    returns:
        A ``(T, n)`` array (or DataFrame) of **per-period** asset returns with
        more periods than assets (``T / n > 1``).

    Returns
    -------
    np.ndarray
        The denoised ``(n, n)`` covariance on the same scale as
        ``np.cov(returns, rowvar=False)``.
    """
    X = _as_returns_matrix(returns)
    T, n = X.shape
    if T <= n:
        raise ValueError("mp_denoise requires more periods than assets (T / n > 1)")

    mean = X.mean(axis=0)
    Xc = X - mean
    S = (Xc.T @ Xc) / T  # MLE sample covariance
    std = np.sqrt(np.diag(S))
    denom = np.outer(std, std)
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.where(denom > 0, S / denom, 0.0)
    corr = 0.5 * (corr + corr.T)

    eigvals, eigvecs = np.linalg.eigh(corr)

    q = n / T
    edge = (1.0 + np.sqrt(q)) ** 2  # Marchenko-Pastur upper edge

    noise = eigvals < edge
    if noise.any():
        noise_mean = float(eigvals[noise].mean())
        eigvals = np.where(noise, noise_mean, eigvals)

    denoised_corr = (eigvecs * eigvals) @ eigvecs.T
    # rebuild with a unit diagonal so the correlation trace is preserved (== n)
    d = np.sqrt(np.diag(denoised_corr))
    with np.errstate(divide="ignore", invalid="ignore"):
        norm = np.where(np.outer(d, d) > 0, denoised_corr / np.outer(d, d), 0.0)
    np.fill_diagonal(norm, 1.0)

    cov = norm * denom
    return 0.5 * (cov + cov.T)


# Estimator names accepted by ``estimate_covariance``. The shrinkage targets
# ("constant_correlation", "identity") remain handled by ``ledoit_wolf_shrinkage``.
CovEstimator = str  # "sample" | "ewma" | "oas" | "mp"


def estimate_covariance(returns, estimator: str = "sample", *, lam: float = 0.94) -> np.ndarray:
    """Dispatch to a per-period covariance estimator by name.

    Parameters
    ----------
    returns:
        A ``(T, n)`` array (or DataFrame) of **per-period** asset returns.
    estimator:
        One of ``"sample"`` (plain MLE-free ``np.cov`` with ddof=1),
        ``"ewma"``, ``"oas"`` (OAS shrinkage), or ``"mp"`` (Marchenko-Pastur
        denoising). The shrinkage estimators that return an intensity remain
        available directly (``oas_shrinkage`` / ``ledoit_wolf_shrinkage``).
    lam:
        Decay factor forwarded to the EWMA estimator (ignored otherwise).

    Returns
    -------
    np.ndarray
        The ``(n, n)`` covariance on the same (per-period) scale as
        ``np.cov(returns, rowvar=False)``.
    """
    if estimator == "sample":
        X = _as_returns_matrix(returns)
        return np.cov(X, rowvar=False, ddof=1)
    if estimator == "ewma":
        return ewma_covariance(returns, lam=lam)
    if estimator == "oas":
        return oas_shrinkage(returns)[0]
    if estimator == "mp":
        return mp_denoise(returns)
    raise ValueError(
        f"Unknown covariance estimator {estimator!r}; use 'sample', 'ewma', 'oas', or 'mp'"
    )
