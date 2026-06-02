import numpy as np
from scipy.stats import norm


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
