"""Hagan (2002) lognormal-SABR smile and per-slice calibration.

This module implements the **Hagan et al. (2002) lognormal (Black) implied
volatility approximation** for the SABR stochastic-volatility model and a
per-expiry calibration that mirrors the SVI fit in :mod:`src.vol_surface`. The
SABR dynamics are::

    dF = alpha * F^beta dW1
    d(alpha) = nu * alpha dW2
    dW1 dW2 = rho dt

and Hagan's closed-form gives the Black-76 implied volatility ``sigma_B(K, F)``
as a function of the four parameters ``(alpha, beta, rho, nu)``. The smile is
typically fit with ``beta`` **fixed** by desk convention (0.5 here) and only
``(alpha, rho, nu)`` calibrated, because ``beta`` and ``rho`` are nearly
collinear in the fit.

**Scope / honesty:** this is the **Hagan asymptotic approximation**. It is
accurate for short-to-medium expiries and moderate strikes but degrades for
**very long maturities** and **extreme (deep ITM/OTM) strikes**, where the
expansion is known to break down and can even imply small arbitrage. It is
**NOT** an arbitrage-free SABR (no PDE / probability-density correction) and
there is **no negative-rate (shifted-SABR) variant** — ``F`` and ``K`` must be
positive. For an arbitrage-free or shifted SABR, reach for a dedicated library.

The output is a **Black-76** volatility, so a fitted SABR smile feeds straight
into :func:`src.black_scholes.black_76_price` to get an option price, e.g.::

    from src.black_scholes import black_76_price
    from src.sabr import SABRParams, sabr_implied_vol
    p = SABRParams(alpha=0.2, beta=0.5, rho=-0.3, nu=0.4)
    iv = float(sabr_implied_vol(F=100.0, K=110.0, T=0.5, *p))
    price = black_76_price(100.0, 110.0, 0.5, r=0.02, sigma=iv, option_type="call")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from scipy.optimize import least_squares

if TYPE_CHECKING:  # pragma: no cover
    from numpy.typing import ArrayLike, NDArray


class SABRParams(NamedTuple):
    """The four Hagan-SABR parameters for one expiry slice.

    Attributes:
        alpha: initial instantaneous volatility level (``> 0``). At-the-money
            Black vol is approximately ``alpha / F^(1-beta)``.
        beta: CEV elasticity (``0 <= beta <= 1``); fixed by desk convention in
            calibration (``beta = 1`` lognormal, ``beta = 0`` normal).
        rho: spot/vol correlation (``-1 < rho < 1``); controls smile skew.
        nu: volatility-of-volatility (``>= 0``); controls smile curvature.
    """

    alpha: float
    beta: float
    rho: float
    nu: float


def sabr_implied_vol(
    F: float,
    K: ArrayLike,
    T: float,
    alpha: float,
    beta: float,
    rho: float,
    nu: float,
) -> NDArray[np.float64]:
    """Hagan (2002) lognormal-SABR Black implied volatility.

    Vectorized over ``K`` (scalar or array-like); ``F``, ``T`` and the four
    parameters are scalars. Returns a float64 ndarray (0-d for scalar ``K``) of
    Black-76 implied volatilities.

    The ATM case ``K == F`` is handled explicitly: the ``z / x(z)`` ratio tends
    to ``1`` as ``F -> K``, so it is set to ``1`` there to avoid ``0/0``.

    Args:
        F: forward price (``> 0``).
        K: strike(s) (``> 0``).
        T: time to expiry in years (``> 0``).
        alpha: initial vol level (``> 0``).
        beta: CEV elasticity (``0 <= beta <= 1``).
        rho: spot/vol correlation (``-1 < rho < 1``).
        nu: vol-of-vol (``>= 0``).

    Returns:
        Black implied vol(s) as a float64 ndarray.

    Raises:
        ValueError: if ``F <= 0`` or ``T <= 0`` (no negative-rate shift here).
    """
    if F <= 0:
        raise ValueError("SABR (Hagan, unshifted) requires F > 0")
    if T <= 0:
        raise ValueError("T must be positive")
    Ka = np.asarray(K, dtype=np.float64)
    if np.any(Ka <= 0):
        raise ValueError("SABR (Hagan, unshifted) requires K > 0")

    one_m_beta = 1.0 - beta
    log_fk = np.log(F / Ka)
    fk_pow = (F * Ka) ** (one_m_beta / 2.0)

    # z and x(z); z/x(z) -> 1 as F -> K (the ATM limit).
    z = (nu / alpha) * fk_pow * log_fk
    # Numerically stable x(z) = ln[(sqrt(1 - 2 rho z + z^2) + z - rho)/(1 - rho)].
    sqrt_term = np.sqrt(1.0 - 2.0 * rho * z + z * z)
    x_of_z = np.log((sqrt_term + z - rho) / (1.0 - rho))
    # Where z ~ 0 (K ~ F) the ratio z/x(z) is 1; set it directly to avoid 0/0.
    atm = np.isclose(Ka, F) | np.isclose(z, 0.0)
    safe_x = np.where(atm, 1.0, x_of_z)
    safe_z = np.where(atm, 1.0, z)
    z_over_x = np.where(atm, 1.0, safe_z / safe_x)

    # Denominator expansion in log-moneyness.
    log_fk2 = log_fk * log_fk
    denom = fk_pow * (
        1.0 + (one_m_beta**2 / 24.0) * log_fk2 + (one_m_beta**4 / 1920.0) * log_fk2 * log_fk2
    )

    # Time-correction bracket (common to ATM and non-ATM forms).
    term1 = (one_m_beta**2 / 24.0) * (alpha * alpha) / (fk_pow * fk_pow)
    term2 = 0.25 * (rho * beta * nu * alpha) / fk_pow
    term3 = (2.0 - 3.0 * rho * rho) / 24.0 * (nu * nu)
    correction = 1.0 + (term1 + term2 + term3) * T

    vol = (alpha / denom) * z_over_x * correction
    return np.asarray(vol, dtype=np.float64)


def sabr_smile(
    params: SABRParams | tuple[float, ...],
    F: float,
    T: float,
    strikes: ArrayLike,
) -> NDArray[np.float64]:
    """Evaluate a SABR slice as a Black-implied-vol smile over strikes.

    Convenience wrapper for plotting/interpolation: unpacks ``params`` and calls
    :func:`sabr_implied_vol`.
    """
    alpha, beta, rho, nu = params
    return sabr_implied_vol(F, strikes, T, alpha, beta, rho, nu)


def _initial_guess(
    strikes: NDArray[np.float64], ivs: NDArray[np.float64], F: float, beta: float
) -> list[float]:
    """A data-driven starting point for the (alpha, rho, nu) calibration."""
    # ATM vol ~ alpha / F^(1-beta)  ->  alpha0 = atm_iv * F^(1-beta).
    atm_idx = int(np.argmin(np.abs(strikes - F)))
    atm_iv = float(ivs[atm_idx])
    alpha0 = max(atm_iv * F ** (1.0 - beta), 1e-4)
    rho0 = -0.2
    nu0 = 0.4
    return [alpha0, rho0, nu0]


def fit_sabr_slice(
    strikes: ArrayLike,
    ivs: ArrayLike,
    F: float,
    T: float,
    beta: float = 0.5,
    initial: tuple[float, float, float] | None = None,
    max_nfev: int = 2000,
) -> SABRParams:
    """Calibrate Hagan-SABR ``(alpha, rho, nu)`` to one expiry slice.

    Fits the three free parameters (``beta`` is **held fixed** at the desk
    convention) by minimizing the squared residual between the Hagan implied
    vols and the observed ``ivs`` at each strike, via
    :func:`scipy.optimize.least_squares` (bounded). Mirrors
    :func:`src.vol_surface.fit_svi_slice` in API/style so it fits the tidy
    slices produced by :func:`src.greeks_visualizer.solve_iv_surface`.

    Args:
        strikes: strikes for the slice (``> 0``).
        ivs: observed Black implied vols per strike.
        F: forward price for the slice (``> 0``).
        T: time to expiry (years, ``> 0``).
        beta: fixed CEV elasticity (default 0.5).
        initial: optional ``(alpha, rho, nu)`` starting point; a data-driven
            guess is used when omitted.
        max_nfev: max function evaluations for the solver.

    Returns:
        The fitted :class:`SABRParams` (with ``beta`` echoed back unchanged).

    Raises:
        ValueError: if fewer than 3 finite (strike, iv) points are supplied.
    """
    ka = np.asarray(strikes, dtype=np.float64)
    va = np.asarray(ivs, dtype=np.float64)
    finite = np.isfinite(ka) & np.isfinite(va) & (ka > 0) & (va > 0)
    ka, va = ka[finite], va[finite]
    if ka.size < 3:
        raise ValueError("need at least 3 finite (strike, iv) points to fit SABR")

    x0 = list(initial) if initial is not None else _initial_guess(ka, va, F, beta)

    # Bounds: alpha > 0, -1 < rho < 1, nu >= 0.
    lower = [1e-8, -0.999, 0.0]
    upper = [np.inf, 0.999, np.inf]
    x0 = [min(max(v, lo), hi) for v, lo, hi in zip(x0, lower, upper, strict=True)]

    def residual(p: NDArray[np.float64]) -> NDArray[np.float64]:
        alpha, rho, nu = float(p[0]), float(p[1]), float(p[2])
        model = sabr_implied_vol(F, ka, T, alpha, beta, rho, nu)
        return np.asarray(model - va, dtype=np.float64)

    result = least_squares(residual, x0, bounds=(lower, upper), max_nfev=max_nfev, method="trf")
    alpha, rho, nu = (float(v) for v in result.x)
    return SABRParams(alpha=alpha, beta=beta, rho=rho, nu=nu)
