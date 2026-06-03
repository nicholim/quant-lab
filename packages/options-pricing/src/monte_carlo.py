"""Monte-Carlo pricer for European options under geometric Brownian motion.

This is a Monte-Carlo pricer **under GBM** (the same risk-neutral dynamics as
Black-Scholes) with two variance-reduction techniques layered on top:

* **Antithetic variates** — each standard-normal draw ``Z`` is paired with
  ``-Z``, halving the effective number of independent draws while cancelling
  the linear part of the payoff's Monte-Carlo error.
* **Black-Scholes control variate** — the discounted terminal *spot* has a
  known analytic expectation (``S0`` under the risk-neutral measure), so the
  realized terminal spot is used as a control whose Monte-Carlo error is
  subtracted out with the variance-minimizing beta. The closed-form
  :func:`~src.black_scholes.black_scholes_price` is reused for self-validation,
  not reimplemented.

It is **not** a general stochastic-volatility / exotic-payoff engine: only
European calls and puts on a single GBM underlying are supported, and the
result is validated to converge to the closed-form Black-Scholes price. For
exotics / Heston / finite-difference PDE reach for QuantLib.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from .black_scholes import black_scholes_price


class MCResult(NamedTuple):
    """A Monte-Carlo price estimate and its standard error.

    Attributes:
        price: the discounted Monte-Carlo price estimate.
        std_error: the standard error of that estimate (so a 95% CI is roughly
            ``price ± 1.96 * std_error``). Convergence to the closed-form
            Black-Scholes price is expected within ~3 standard errors.
    """

    price: float
    std_error: float


def monte_carlo_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    q: float = 0.0,
    n_paths: int = 100_000,
    seed: int | None = None,
    antithetic: bool = True,
    control_variate: bool = True,
) -> MCResult:
    """Monte-Carlo price (under GBM) of a European call/put, with std error.

    Simulates the terminal price of a geometric Brownian motion under the
    risk-neutral measure, discounts the payoff, and returns the mean estimate
    together with its standard error. Antithetic variates and a Black-Scholes
    (terminal-spot) control variate reduce the variance of the estimate.

    Args:
        S: current spot price.
        K: strike price.
        T: time to expiration (years).
        r: risk-free interest rate (annualized).
        sigma: volatility (annualized).
        option_type: ``'call'`` or ``'put'``.
        q: continuous dividend yield (annualized, default 0).
        n_paths: number of simulated terminal prices (the antithetic mode draws
            ``n_paths // 2`` normals and reflects them, so the path count is
            rounded down to an even number).
        seed: seed for a NumPy ``Generator`` (``None`` = nondeterministic).
        antithetic: pair each ``Z`` with ``-Z`` for variance reduction.
        control_variate: subtract the discounted-terminal-spot control (whose
            risk-neutral mean is ``S``) with the variance-minimizing beta.

    Returns:
        An :class:`MCResult` ``(price, std_error)``.
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")
    if n_paths < 2:
        raise ValueError("n_paths must be at least 2")

    rng = np.random.default_rng(seed)

    # Degenerate expiry / zero-vol: the closed-form is exact, MC adds no value.
    if T <= 0 or sigma <= 0:
        price = black_scholes_price(S, K, T, r, sigma, option_type, q)
        return MCResult(price, 0.0)

    if antithetic:
        half = n_paths // 2
        z_half = rng.standard_normal(half)
        z = np.concatenate([z_half, -z_half])
    else:
        z = rng.standard_normal(n_paths)

    drift = (r - q - 0.5 * sigma**2) * T
    diffusion = sigma * np.sqrt(T)
    terminal = S * np.exp(drift + diffusion * z)

    if option_type == "call":
        payoff = np.maximum(terminal - K, 0.0)
    else:
        payoff = np.maximum(K - terminal, 0.0)

    disc = np.exp(-r * T)
    discounted_payoff = disc * payoff

    if control_variate:
        # Control = discounted terminal spot. Under risk-neutral GBM
        # E[S_T] = S * exp((r - q) * T), so E[disc * S_T] = S * exp(-q * T)
        # (== S when there is no dividend yield).
        control = disc * terminal
        control_mean = S * np.exp(-q * T)
        cov = np.cov(discounted_payoff, control, ddof=1)
        var_control = cov[1, 1]
        if var_control > 0:
            beta = cov[0, 1] / var_control
        else:  # pragma: no cover - degenerate, var_control > 0 under GBM
            beta = 0.0
        adjusted = discounted_payoff - beta * (control - control_mean)
    else:
        adjusted = discounted_payoff

    # Standard error of the mean. With antithetic variates the draw and its
    # reflection are NOT independent, so each antithetic PAIR is the true
    # sampling unit: average the pair, then take the SE over the pair-means.
    # This correctly captures the negative-correlation variance reduction
    # (averaging over independent samples otherwise understates the benefit).
    if antithetic:
        pair_means = 0.5 * (adjusted[:half] + adjusted[half:])
        price = float(np.mean(pair_means))
        m = pair_means.size
        std_error = float(np.std(pair_means, ddof=1) / np.sqrt(m)) if m > 1 else 0.0
    else:
        n = adjusted.size
        price = float(np.mean(adjusted))
        std_error = float(np.std(adjusted, ddof=1) / np.sqrt(n))
    return MCResult(price, std_error)
