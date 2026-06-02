"""Black-Litterman expected-returns model.

The Black-Litterman (1992) model produces a posterior vector of expected excess
returns by *blending* two sources of information:

1. A **market-implied equilibrium** (the "prior"), obtained by *reverse*
   optimization. Given a neutral market portfolio ``w_mkt`` (market-cap or
   equal weights), the equilibrium excess returns that make ``w_mkt`` optimal
   under mean-variance utility with risk aversion ``delta`` are::

       Pi = delta * Sigma @ w_mkt

2. The investor's **views**, expressed as a linear system ``P @ E[R] = Q`` (with
   uncertainty ``Omega``): each row of the pick matrix ``P`` picks the assets a
   view is about, ``Q`` is the view's expected return, and ``Omega`` (diagonal,
   typically) captures how confident the investor is in each view.

The posterior expected return is the precision-weighted combination (the BL
"master formula")::

    E[R] = [ (tau*Sigma)^-1 + P^T Omega^-1 P ]^-1
           [ (tau*Sigma)^-1 Pi + P^T Omega^-1 Q ]

With **no views** (or zero-confidence views) the posterior collapses back to the
equilibrium prior ``Pi`` -- a useful sanity property, and the documented default
behavior of :func:`black_litterman` when ``P``/``Q`` are omitted.

This module is **opt-in** and additive: nothing here runs on the default
optimizer path, so metrics parity with the backtester is preserved. It mirrors
``covariance.py`` -- a small numpy-only helper plus a thin optimizer entry point
(:meth:`PortfolioOptimizer.optimize_black_litterman`).

Implemented with numpy only -- no scikit-learn / cvxpy / PyPortfolioOpt.
"""

from __future__ import annotations

import numpy as np


def _as_cov_matrix(cov) -> np.ndarray:
    """Coerce a DataFrame/ndarray covariance to a square (n, n) float matrix."""
    arr = np.asarray(getattr(cov, "values", cov), dtype=float)
    if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
        raise ValueError("cov must be a square (n, n) covariance matrix")
    return arr


def market_implied_prior(cov: np.ndarray, w_mkt: np.ndarray, risk_aversion: float) -> np.ndarray:
    """Equilibrium (prior) excess returns via reverse optimization.

    ``Pi = delta * Sigma @ w_mkt`` where ``delta`` is the risk-aversion
    coefficient and ``w_mkt`` the neutral market portfolio.
    """
    return float(risk_aversion) * (cov @ w_mkt)


def black_litterman(
    cov,
    w_mkt: np.ndarray | None = None,
    P: np.ndarray | None = None,
    Q: np.ndarray | None = None,
    *,
    omega: np.ndarray | str | None = None,
    tau: float = 0.05,
    risk_aversion: float = 2.5,
    pi: np.ndarray | None = None,
) -> np.ndarray:
    """Compute the Black-Litterman posterior expected (excess) returns.

    Parameters
    ----------
    cov:
        The ``(n, n)`` asset covariance matrix (``Sigma``). A DataFrame or
        ndarray; only its values are used.
    w_mkt:
        Neutral market portfolio weights, length ``n``. Used to build the
        equilibrium prior ``Pi = delta * Sigma @ w_mkt``. Defaults to
        equal-weight (``1/n`` each). Ignored if ``pi`` is supplied directly.
    P:
        View pick matrix, shape ``(k, n)`` -- one row per view. ``None`` (or an
        empty matrix) means *no views*, and the posterior equals the prior.
    Q:
        View expected returns, length ``k``. Required when ``P`` is given.
    omega:
        View uncertainty, shape ``(k, k)`` (typically diagonal). If ``None`` the
        standard default ``Omega = diag(tau * P Sigma P^T)`` is used -- i.e. each
        view's uncertainty is proportional to the prior variance of that view, so
        no extra confidence parameter is needed. Pass ``"idzorek"``-style custom
        matrices directly as an ndarray for full control.
    tau:
        Scalar weight on the prior covariance (``tau * Sigma`` is the uncertainty
        of the equilibrium estimate). Small (commonly ``0.01``-``0.05``).
    risk_aversion:
        Risk-aversion coefficient ``delta`` used to build the prior.
    pi:
        Optionally supply the prior excess returns directly (length ``n``),
        bypassing the reverse-optimization step (``w_mkt``/``risk_aversion``
        are then unused).

    Returns
    -------
    posterior_mean : ndarray, length ``n``
        The posterior expected (excess) returns. With no views this equals the
        equilibrium prior ``Pi``.
    """
    sigma = _as_cov_matrix(cov)
    n = sigma.shape[0]

    # --- Prior (equilibrium) returns ---
    if pi is not None:
        prior = np.asarray(pi, dtype=float).reshape(-1)
        if prior.shape != (n,):
            raise ValueError(f"pi must have length {n}, got {prior.shape}")
    else:
        if w_mkt is None:
            w = np.full(n, 1.0 / n, dtype=float)
        else:
            w = np.asarray(w_mkt, dtype=float).reshape(-1)
            if w.shape != (n,):
                raise ValueError(f"w_mkt must have length {n}, got {w.shape}")
        prior = market_implied_prior(sigma, w, risk_aversion)

    # --- No views: posterior == prior ---
    if P is None or Q is None:
        return prior
    P_arr = np.atleast_2d(np.asarray(P, dtype=float))
    Q_arr = np.asarray(Q, dtype=float).reshape(-1)
    if P_arr.size == 0 or Q_arr.size == 0:
        return prior
    if P_arr.shape[1] != n:
        raise ValueError(f"P must have {n} columns (one per asset), got {P_arr.shape}")
    k = P_arr.shape[0]
    if Q_arr.shape != (k,):
        raise ValueError(f"Q must have length {k} (one per view), got {Q_arr.shape}")

    tau_sigma = float(tau) * sigma

    # --- View uncertainty Omega ---
    if omega is None:
        # Standard default: diag(tau * P Sigma P^T). A near-zero diagonal entry
        # (a view on a zero-variance combination) would make Omega singular, so
        # floor it to a tiny positive number.
        diag = np.diag(P_arr @ tau_sigma @ P_arr.T).copy()
        diag = np.where(diag > 1e-12, diag, 1e-12)
        omega_mat = np.diag(diag)
    else:
        omega_mat = np.atleast_2d(np.asarray(omega, dtype=float))
        if omega_mat.shape != (k, k):
            raise ValueError(f"omega must be ({k}, {k}), got {omega_mat.shape}")

    # --- BL master formula ---
    # E[R] = (A)^-1 @ b  with
    #   A = (tau*Sigma)^-1 + P^T Omega^-1 P
    #   b = (tau*Sigma)^-1 @ Pi + P^T Omega^-1 @ Q
    tau_sigma_inv = np.linalg.inv(tau_sigma)
    omega_inv = np.linalg.inv(omega_mat)
    a = tau_sigma_inv + P_arr.T @ omega_inv @ P_arr
    b = tau_sigma_inv @ prior + P_arr.T @ omega_inv @ Q_arr
    posterior = np.linalg.solve(a, b)
    return np.asarray(posterior, dtype=float).reshape(-1)
