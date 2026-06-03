"""Gatheral raw-SVI smile FIT for the solved implied-volatility surface.

This module fits the **Gatheral raw SVI** parameterization of total implied
variance to the tidy IV surface produced by
:func:`~src.greeks_visualizer.solve_iv_surface` (columns ``expiry``/``T``/
``strike``/``iv``). For one expiry slice the raw SVI form is::

    w(k) = a + b * ( rho * (k - m) + sqrt((k - m)^2 + s^2) )

where ``w = sigma^2 * T`` is total variance and ``k = ln(K / F)`` is
log-moneyness (``F = S * exp((r - q) * T)`` is the forward). The five
parameters ``(a, b, rho, m, s)`` are calibrated per expiry with
:func:`scipy.optimize.least_squares` on total variance.

**Scope / honesty:** this is a smile *fit / interpolation* of the observed
solved-IV surface — it minimizes squared total-variance residuals per slice.
It is **not** guaranteed calendar- or butterfly-arbitrage-free: no
cross-slice or convexity constraints are enforced beyond the standard SVI
parameter bounds (``b >= 0``, ``|rho| < 1``, ``s > 0``). For an
arbitrage-constrained surface reach for a dedicated calibrator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import numpy as np
from scipy.optimize import least_squares

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd
    from numpy.typing import ArrayLike, NDArray


class SVIParams(NamedTuple):
    """The five raw-SVI parameters for one expiry slice.

    Attributes:
        a: vertical level of total variance.
        b: angle / wing slope (``>= 0``).
        rho: skew / rotation (``-1 < rho < 1``).
        m: horizontal shift of the smile minimum (in log-moneyness).
        sigma: smoothness / curvature of the minimum (``> 0``); named ``sigma``
            after Gatheral's notation (it is the SVI ``s`` parameter, NOT a
            Black-Scholes volatility).
    """

    a: float
    b: float
    rho: float
    m: float
    sigma: float


def svi_total_variance(k: ArrayLike, params: SVIParams | tuple[float, ...]) -> NDArray[np.float64]:
    """Raw-SVI total variance ``w(k)`` for log-moneyness ``k``.

    Args:
        k: log-moneyness ``ln(K / F)`` (scalar or array-like).
        params: an :class:`SVIParams` or a 5-tuple ``(a, b, rho, m, sigma)``.

    Returns:
        Total implied variance ``w = sigma_iv^2 * T`` as a float64 ndarray
        (0-d for scalar input). Always non-negative for valid parameters.
    """
    a, b, rho, m, s = params
    ka = np.asarray(k, dtype=np.float64)
    centered = ka - m
    return np.asarray(a + b * (rho * centered + np.sqrt(centered**2 + s**2)), dtype=np.float64)


def svi_implied_vol(
    k: ArrayLike, params: SVIParams | tuple[float, ...], T: float
) -> NDArray[np.float64]:
    """Implied volatility from a fitted SVI slice at log-moneyness ``k``.

    Converts the fitted total variance ``w(k)`` back to an annualized vol via
    ``sigma = sqrt(w / T)``. Negative total variance (only possible for
    pathological parameters) is clipped to zero before the square root.
    """
    if T <= 0:
        raise ValueError("T must be positive to convert total variance to IV")
    w = svi_total_variance(k, params)
    return np.asarray(np.sqrt(np.maximum(w, 0.0) / T), dtype=np.float64)


def _initial_guess(k: NDArray[np.float64], w: NDArray[np.float64]) -> list[float]:
    """A robust starting point for the least-squares calibration."""
    w_min = float(np.min(w))
    w_max = float(np.max(w))
    k_span = float(np.max(k) - np.min(k)) or 1.0
    a0 = max(w_min, 1e-6)
    b0 = max((w_max - w_min) / k_span, 1e-3)
    rho0 = -0.3
    m0 = float(k[np.argmin(w)])
    s0 = max(k_span / 4.0, 1e-2)
    return [a0, b0, rho0, m0, s0]


def fit_svi_slice(
    k: ArrayLike,
    total_variance: ArrayLike,
    initial: SVIParams | tuple[float, ...] | None = None,
    max_nfev: int = 2000,
) -> SVIParams:
    """Calibrate raw-SVI to one expiry slice of total variance.

    Fits ``(a, b, rho, m, sigma)`` by minimizing the squared residual between
    the SVI total variance and the observed ``total_variance`` at each
    log-moneyness ``k``, via :func:`scipy.optimize.least_squares` (bounded).

    Args:
        k: log-moneyness ``ln(K / F)`` for the slice (at least 5 points for a
            well-posed 5-parameter fit; fewer will still fit but is
            under-determined).
        total_variance: observed total variance ``w = iv^2 * T`` per ``k``.
        initial: optional starting parameters; a data-driven guess is used when
            omitted.
        max_nfev: max function evaluations for the solver.

    Returns:
        The fitted :class:`SVIParams`.

    Raises:
        ValueError: if fewer than 3 finite (k, w) points are supplied.
    """
    ka = np.asarray(k, dtype=np.float64)
    wa = np.asarray(total_variance, dtype=np.float64)
    finite = np.isfinite(ka) & np.isfinite(wa)
    ka, wa = ka[finite], wa[finite]
    if ka.size < 3:
        raise ValueError("need at least 3 finite (k, total_variance) points to fit SVI")

    x0 = list(initial) if initial is not None else _initial_guess(ka, wa)

    # Bounds: a free (>= ~0), b >= 0, -1 < rho < 1, m free, sigma > 0.
    w_max = float(np.max(wa))
    lower = [-w_max, 0.0, -0.999, -np.inf, 1e-6]
    upper = [np.inf, np.inf, 0.999, np.inf, np.inf]
    # Clamp the initial guess inside the bounds so least_squares accepts it.
    x0 = [min(max(v, lo), hi) for v, lo, hi in zip(x0, lower, upper, strict=True)]

    def residual(p: NDArray[np.float64]) -> NDArray[np.float64]:
        return svi_total_variance(ka, tuple(p)) - wa

    result = least_squares(residual, x0, bounds=(lower, upper), max_nfev=max_nfev, method="trf")
    return SVIParams(*(float(v) for v in result.x))


def fit_svi_surface(
    surface_df: pd.DataFrame,
    spot: float,
    r: float = 0.045,
    q: float = 0.0,
) -> dict[str, SVIParams]:
    """Fit a raw-SVI slice to each expiry in a solved IV surface.

    Consumes the tidy DataFrame produced by
    :func:`~src.greeks_visualizer.solve_iv_surface` verbatim (columns
    ``expiry``/``T``/``strike``/``iv``). For each expiry it computes the forward
    ``F = spot * exp((r - q) * T)``, the log-moneyness ``k = ln(strike / F)``,
    and the total variance ``w = iv^2 * T``, then fits one SVI slice.

    Args:
        surface_df: the solved-IV surface (``expiry``/``T``/``strike``/``iv``).
        spot: underlying spot price (to build the forward).
        r: risk-free rate; ``q`` continuous dividend yield.

    Returns:
        A dict mapping each expiry (``YYYY-MM-DD``) -> fitted :class:`SVIParams`.
        Expiries with fewer than 3 solvable IV points are skipped.
    """
    fits: dict[str, SVIParams] = {}
    for expiry in sorted(set(surface_df["expiry"])):
        sub = surface_df[surface_df["expiry"] == expiry]
        T = float(sub["T"].iloc[0])
        if T <= 0:
            continue
        forward = spot * np.exp((r - q) * T)
        strikes = sub["strike"].to_numpy(dtype=float)
        iv = sub["iv"].to_numpy(dtype=float)
        k = np.log(strikes / forward)
        w = iv**2 * T
        finite = np.isfinite(k) & np.isfinite(w)
        if int(np.count_nonzero(finite)) < 3:
            continue
        fits[expiry] = fit_svi_slice(k[finite], w[finite])
    return fits


def svi_smile(
    params: SVIParams | tuple[float, ...],
    T: float,
    strikes: ArrayLike,
    forward: float,
) -> NDArray[np.float64]:
    """Evaluate a fitted SVI slice as an IV smile over actual strikes.

    Convenience for plotting/interpolation: maps ``strikes`` to log-moneyness
    against ``forward`` and returns the fitted implied volatilities.
    """
    ks = np.asarray(strikes, dtype=np.float64)
    k = np.log(ks / forward)
    return svi_implied_vol(k, params, T)
