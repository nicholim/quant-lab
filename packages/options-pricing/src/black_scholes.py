from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from scipy.stats import norm

if TYPE_CHECKING:  # pragma: no cover
    from numpy.typing import ArrayLike, NDArray


def _d1(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    return (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    return _d1(S, K, T, r, sigma, q) - sigma * np.sqrt(T)


def black_scholes_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    q: float = 0.0,
) -> float:
    """Black-Scholes price for a European option.

    Args:
        S: Current spot price
        K: Strike price
        T: Time to expiration (years)
        r: Risk-free interest rate (annualized)
        sigma: Volatility (annualized)
        option_type: 'call' or 'put'
        q: Continuous dividend yield (annualized, default 0)
    """
    # At expiration, return intrinsic value
    if T <= 0:
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    # Zero vol: deterministic forward price
    if sigma <= 0:
        forward = S * np.exp((r - q) * T)
        if option_type == "call":
            return max(forward - K, 0.0) * np.exp(-r * T)
        return max(K - forward, 0.0) * np.exp(-r * T)

    d1 = _d1(S, K, T, r, sigma, q)
    d2 = _d2(S, K, T, r, sigma, q)

    if option_type == "call":
        return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)


def delta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    q: float = 0.0,
) -> float:
    """Option delta — sensitivity of price to spot."""
    if T <= 0 or sigma <= 0:
        if option_type == "call":
            return 1.0 if S > K else (0.5 if S == K else 0.0)
        return -1.0 if S < K else (-0.5 if S == K else 0.0)
    d1 = _d1(S, K, T, r, sigma, q)
    if option_type == "call":
        return float(np.exp(-q * T) * norm.cdf(d1))
    else:
        return float(np.exp(-q * T) * (norm.cdf(d1) - 1))


def gamma(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Option gamma — rate of change of delta."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    return float(np.exp(-q * T) * norm.pdf(d1) / (S * sigma * np.sqrt(T)))


def theta(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    q: float = 0.0,
) -> float:
    """Option theta — time decay (per calendar day)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = _d2(S, K, T, r, sigma, q)
    common = -(S * np.exp(-q * T) * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))

    if option_type == "call":
        return float(
            (common + q * S * np.exp(-q * T) * norm.cdf(d1) - r * K * np.exp(-r * T) * norm.cdf(d2))
            / 365
        )
    else:
        return float(
            (
                common
                - q * S * np.exp(-q * T) * norm.cdf(-d1)
                + r * K * np.exp(-r * T) * norm.cdf(-d2)
            )
            / 365
        )


def vega(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Option vega — sensitivity to volatility (per 1% move)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    return float(S * np.exp(-q * T) * norm.pdf(d1) * np.sqrt(T) / 100)


def rho(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    q: float = 0.0,
) -> float:
    """Option rho — sensitivity to interest rate (per 1% move)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    d2 = _d2(S, K, T, r, sigma, q)
    if option_type == "call":
        return float(K * T * np.exp(-r * T) * norm.cdf(d2) / 100)
    else:
        return float(-K * T * np.exp(-r * T) * norm.cdf(-d2) / 100)


def vanna(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Option vanna — cross-sensitivity d(delta)/d(sigma) = d(vega)/d(spot).

    Closed-form second-order Greek; identical for calls and puts. Returned in
    raw units (per unit spot, per unit vol). Zero for an expired/zero-vol option.
    """
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = _d2(S, K, T, r, sigma, q)
    return float(-np.exp(-q * T) * norm.pdf(d1) * d2 / sigma)


def volga(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Option volga (a.k.a. vomma) — d(vega)/d(sigma), the convexity in vol.

    Closed-form second-order Greek; identical for calls and puts. Returned in
    raw units (per unit vol). Zero for an expired/zero-vol option.
    """
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = _d2(S, K, T, r, sigma, q)
    raw_vega = S * np.exp(-q * T) * norm.pdf(d1) * np.sqrt(T)
    return float(raw_vega * d1 * d2 / sigma)


def charm(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    q: float = 0.0,
) -> float:
    """Option charm — delta decay, the change in delta as calendar time passes.

    Closed-form second-order Greek (per year of calendar time), using the
    standard convention ``charm = -d(delta)/dT`` where ``T`` is time-to-expiry.
    Differs by option type through the dividend-carry term. Zero for an
    expired/zero-vol option. Equivalently it is ``d(theta)/d(spot)``.
    """
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = _d2(S, K, T, r, sigma, q)
    common = np.exp(-q * T) * norm.pdf(d1) * (2 * (r - q) * T - d2 * sigma * np.sqrt(T))
    common /= 2 * T * sigma * np.sqrt(T)
    if option_type == "call":
        return float(q * np.exp(-q * T) * norm.cdf(d1) - common)
    else:
        return float(-q * np.exp(-q * T) * norm.cdf(-d1) - common)


def black_76_price(
    F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
) -> float:
    """Black-76 price for a European option on a future/forward.

    Prices options whose underlying is a futures/forward price ``F`` (no spot
    carry): the forward is already the risk-neutral expected price, so the model
    simply discounts the Black-Scholes-style payoff at ``r``.

    Args:
        F: Current futures/forward price
        K: Strike price
        T: Time to expiration (years)
        r: Risk-free interest rate (annualized), used only for discounting
        sigma: Volatility (annualized)
        option_type: 'call' or 'put'
    """
    # At expiration, return discounted intrinsic value (disc=1 at T=0)
    if T <= 0:
        if option_type == "call":
            return max(F - K, 0.0)
        return max(K - F, 0.0)

    disc = np.exp(-r * T)

    # Zero vol: deterministic discounted intrinsic on the forward
    if sigma <= 0:
        if option_type == "call":
            return float(max(F - K, 0.0) * disc)
        return float(max(K - F, 0.0) * disc)

    d1 = (np.log(F / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    if option_type == "call":
        return float(disc * (F * norm.cdf(d1) - K * norm.cdf(d2)))
    else:
        return float(disc * (K * norm.cdf(-d2) - F * norm.cdf(-d1)))


def black_76_delta(
    F: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
) -> float:
    """Black-76 delta — sensitivity of the option price to the forward price."""
    if T <= 0 or sigma <= 0:
        if option_type == "call":
            return 1.0 if F > K else (0.5 if F == K else 0.0)
        return -1.0 if F < K else (-0.5 if F == K else 0.0)
    disc = np.exp(-r * T)
    d1 = (np.log(F / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
    if option_type == "call":
        return float(disc * norm.cdf(d1))
    else:
        return float(disc * (norm.cdf(d1) - 1))


def black_76_gamma(F: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-76 gamma — rate of change of delta w.r.t. the forward price."""
    if T <= 0 or sigma <= 0:
        return 0.0
    disc = np.exp(-r * T)
    d1 = (np.log(F / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
    return float(disc * norm.pdf(d1) / (F * sigma * np.sqrt(T)))


def black_76_vega(F: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-76 vega — sensitivity to volatility (per 1% move)."""
    if T <= 0 or sigma <= 0:
        return 0.0
    disc = np.exp(-r * T)
    d1 = (np.log(F / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
    return float(disc * F * norm.pdf(d1) * np.sqrt(T) / 100)


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "call",
    q: float = 0.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """Solve for implied volatility using Newton-Raphson method.

    Returns None if the solver fails to converge.
    """
    if T <= 0:
        return None

    # Validate market price against intrinsic bounds
    intrinsic = max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    if market_price < intrinsic - tol:
        return None  # Below intrinsic — no valid IV

    sigma = 0.3  # initial guess

    for _ in range(max_iter):
        price = black_scholes_price(S, K, T, r, sigma, option_type, q)
        diff = price - market_price

        if abs(diff) < tol:
            return sigma

        # dPrice/dSigma (raw vega, not scaled)
        d1 = _d1(S, K, T, r, sigma, q)
        dvega = S * np.exp(-q * T) * norm.pdf(d1) * np.sqrt(T)

        if dvega < 1e-12:
            break

        sigma -= diff / dvega
        sigma = max(sigma, 1e-6)

    return None  # Did not converge


# --- vectorized / batch API -------------------------------------------------
#
# These broadcast over numpy arrays (or array-likes such as pandas Series) so a
# whole option chain can be priced in one shot, with NO python loop per
# contract. The numerics are deliberately the SAME formulae as the scalar
# functions above, so a one-element vector call equals the scalar call to
# machine precision. Degenerate inputs (T <= 0 or sigma <= 0) are handled
# elementwise via np.where masks rather than early returns.


def _d1_vec(
    S: NDArray[np.float64],
    K: NDArray[np.float64],
    T: NDArray[np.float64],
    r: NDArray[np.float64],
    sigma: NDArray[np.float64],
    q: NDArray[np.float64],
) -> NDArray[np.float64]:
    # Guard against divide-by-zero in the degenerate columns; those entries are
    # overwritten by the caller's mask, so the bogus value here never escapes.
    sig_t = sigma * np.sqrt(T)
    safe = np.where(sig_t > 0, sig_t, 1.0)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma**2) * T) / safe
    return np.asarray(d1, dtype=np.float64)


def _broadcast(*arrays: ArrayLike) -> tuple[NDArray[np.float64], ...]:
    """Cast every arg to float64 and broadcast them to a common shape."""
    cast = [np.asarray(a, dtype=np.float64) for a in arrays]
    return tuple(np.broadcast_arrays(*cast))


def black_scholes_price_vec(
    S: ArrayLike,
    K: ArrayLike,
    T: ArrayLike,
    r: ArrayLike,
    sigma: ArrayLike,
    option_type: str = "call",
    q: ArrayLike = 0.0,
) -> NDArray[np.float64]:
    """Vectorized Black-Scholes price (broadcasts over all numeric args).

    Accepts numpy arrays / pandas Series / scalars for any argument and returns
    a float64 ndarray of the broadcast shape. Numerically identical to
    :func:`black_scholes_price` elementwise, including the ``T<=0`` (intrinsic)
    and ``sigma<=0`` (discounted forward intrinsic) degenerate cases.
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")
    Sa, Ka, Ta, ra, siga, qa = _broadcast(S, K, T, r, sigma, q)

    d1 = _d1_vec(Sa, Ka, Ta, ra, siga, qa)
    d2 = d1 - siga * np.sqrt(Ta)
    disc_r = np.exp(-ra * Ta)
    disc_q = np.exp(-qa * Ta)

    if option_type == "call":
        normal = Sa * disc_q * norm.cdf(d1) - Ka * disc_r * norm.cdf(d2)
        intrinsic = np.maximum(Sa - Ka, 0.0)
        forward = np.maximum(Sa * np.exp((ra - qa) * Ta) - Ka, 0.0) * disc_r
    else:
        normal = Ka * disc_r * norm.cdf(-d2) - Sa * disc_q * norm.cdf(-d1)
        intrinsic = np.maximum(Ka - Sa, 0.0)
        forward = np.maximum(Ka - Sa * np.exp((ra - qa) * Ta), 0.0) * disc_r

    # sigma<=0 -> deterministic discounted forward; T<=0 -> intrinsic (wins).
    out = np.where(siga > 0, normal, forward)
    out = np.where(Ta > 0, out, intrinsic)
    return np.asarray(out, dtype=np.float64)


def greeks_vec(
    S: ArrayLike,
    K: ArrayLike,
    T: ArrayLike,
    r: ArrayLike,
    sigma: ArrayLike,
    option_type: str = "call",
    q: ArrayLike = 0.0,
) -> dict[str, NDArray[np.float64]]:
    """Vectorized first-order Greeks for a whole chain.

    Returns a dict of float64 ndarrays (``delta``, ``gamma``, ``theta``,
    ``vega``, ``rho``), each broadcast to the common input shape and matching
    the corresponding scalar function elementwise. ``gamma``/``vega`` are
    option-type independent; ``delta``/``theta``/``rho`` honor ``option_type``.
    Degenerate (``T<=0`` or ``sigma<=0``) entries follow the scalar conventions:
    gamma/theta/vega/rho -> 0; delta -> the expiry step (±1/±0.5/0).
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")
    Sa, Ka, Ta, ra, siga, qa = _broadcast(S, K, T, r, sigma, q)
    live = (Ta > 0) & (siga > 0)

    d1 = _d1_vec(Sa, Ka, Ta, ra, siga, qa)
    d2 = d1 - siga * np.sqrt(Ta)
    pdf_d1 = norm.pdf(d1)
    disc_q = np.exp(-qa * Ta)
    disc_r = np.exp(-ra * Ta)
    sqrt_t = np.sqrt(Ta)
    # Degenerate (T<=0 / sigma<=0) columns divide by zero in the "live" formulae
    # below; those entries are masked out by `live`, so silence the warnings and
    # neutralize the denominators to avoid nan/inf leaking before the mask.
    safe_sqrt_t = np.where(sqrt_t > 0, sqrt_t, 1.0)
    safe_sig = np.where(siga > 0, siga, 1.0)

    # --- delta (has a non-zero degenerate value) ---
    if option_type == "call":
        delta_live = disc_q * norm.cdf(d1)
        # expiry/zero-vol step: +1 ITM, +0.5 ATM, 0 OTM
        delta_deg = np.where(Sa > Ka, 1.0, np.where(Sa == Ka, 0.5, 0.0))
    else:
        delta_live = disc_q * (norm.cdf(d1) - 1.0)
        delta_deg = np.where(Sa < Ka, -1.0, np.where(Sa == Ka, -0.5, 0.0))
    delta_arr = np.where(live, delta_live, delta_deg)

    # --- gamma / vega (type independent, 0 when degenerate) ---
    gamma_live = disc_q * pdf_d1 / (Sa * safe_sig * safe_sqrt_t)
    gamma_arr = np.where(live, gamma_live, 0.0)
    vega_live = Sa * disc_q * pdf_d1 * sqrt_t / 100.0
    vega_arr = np.where(live, vega_live, 0.0)

    # --- theta (per calendar day, /365) ---
    common = -(Sa * disc_q * pdf_d1 * siga) / (2 * safe_sqrt_t)
    if option_type == "call":
        theta_live = (
            common + qa * Sa * disc_q * norm.cdf(d1) - ra * Ka * disc_r * norm.cdf(d2)
        ) / 365.0
    else:
        theta_live = (
            common - qa * Sa * disc_q * norm.cdf(-d1) + ra * Ka * disc_r * norm.cdf(-d2)
        ) / 365.0
    theta_arr = np.where(live, theta_live, 0.0)

    # --- rho (per 1% move, /100) ---
    if option_type == "call":
        rho_live = Ka * Ta * disc_r * norm.cdf(d2) / 100.0
    else:
        rho_live = -Ka * Ta * disc_r * norm.cdf(-d2) / 100.0
    rho_arr = np.where(live, rho_live, 0.0)

    return {
        "delta": np.asarray(delta_arr, dtype=np.float64),
        "gamma": np.asarray(gamma_arr, dtype=np.float64),
        "theta": np.asarray(theta_arr, dtype=np.float64),
        "vega": np.asarray(vega_arr, dtype=np.float64),
        "rho": np.asarray(rho_arr, dtype=np.float64),
    }


def implied_volatility_vec(
    market_price: ArrayLike,
    S: ArrayLike,
    K: ArrayLike,
    T: ArrayLike,
    r: ArrayLike,
    option_type: str = "call",
    q: ArrayLike = 0.0,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> NDArray[np.float64]:
    """Vectorized implied-volatility solver (Newton over arrays).

    Solves OUR implied vol for every (price, strike, ...) entry at once with a
    single broadcasted Newton iteration, returning a float64 ndarray. Entries
    that cannot be solved -- ``T<=0``, a price below intrinsic, a collapsing
    vega, or non-convergence within ``max_iter`` -- are returned as ``nan``
    rather than raising, so one bad contract never sinks a whole chain.

    Consistent with the scalar :func:`implied_volatility`: a converged entry
    matches the scalar solver to within ``tol`` (same Newton step, same 0.3
    seed, same ``sigma`` floor), and the scalar's ``None`` maps to ``nan``.
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")
    price, Sa, Ka, Ta, ra, qa = _broadcast(market_price, S, K, T, r, q)

    if option_type == "call":
        intrinsic = np.maximum(Sa - Ka, 0.0)
    else:
        intrinsic = np.maximum(Ka - Sa, 0.0)

    # Entries we will never attempt: expired or price below intrinsic bound.
    invalid = (Ta <= 0) | (price < intrinsic - tol)

    sigma = np.full(Sa.shape, 0.3, dtype=np.float64)
    converged = np.zeros(Sa.shape, dtype=bool)
    # "broken" = vega collapsed before convergence (matches scalar's break).
    broken = np.zeros(Sa.shape, dtype=bool)

    for _ in range(max_iter):
        active = ~invalid & ~converged & ~broken
        if not active.any():
            break
        prices = black_scholes_price_vec(Sa, Ka, Ta, ra, sigma, option_type, qa)
        diff = prices - price

        newly_done = active & (np.abs(diff) < tol)
        converged |= newly_done
        active = active & ~newly_done
        if not active.any():
            break

        d1 = _d1_vec(Sa, Ka, Ta, ra, sigma, qa)
        dvega = Sa * np.exp(-qa * Ta) * norm.pdf(d1) * np.sqrt(Ta)
        broken |= active & (dvega < 1e-12)
        step_ok = active & (dvega >= 1e-12)

        # Only advance the entries with usable vega; freeze the rest.
        safe_vega = np.where(step_ok, dvega, 1.0)
        sigma = np.where(step_ok, sigma - diff / safe_vega, sigma)
        sigma = np.maximum(sigma, 1e-6)

    return np.where(converged, sigma, np.nan)
