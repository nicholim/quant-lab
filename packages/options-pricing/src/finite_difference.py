"""Crank-Nicolson finite-difference pricer for American (and European) options.

Prices a vanilla call/put by solving the Black-Scholes PDE on a **log-spot
grid** with the **Crank-Nicolson** scheme (theta = 0.5), with **Rannacher
startup** (a few fully-implicit steps first) to damp the oscillations CN
produces near the non-smooth payoff kink. The European step is a tridiagonal
solve via :func:`scipy.linalg.solve_banded`; **American** early exercise is
imposed with **Projected SOR (PSOR)** against the linear complementarity
problem, so the result matches the CRR :class:`~src.binomial_tree.BinomialTree`
benchmark within a grid-dependent tolerance.

Grid: ``x = ln(S)`` is centered on ``ln(S0)`` and spans ``+/- n_std`` standard
deviations of log-spot (``sigma * sqrt(T)``). Dirichlet boundaries use the
known asymptotic option values. Delta and gamma are read off the grid by
central differences at ``S0``.

**Scope / honesty:** single-factor Black-Scholes PDE only. **No** Heston /
two-factor PDE, **no** ADI, **no** non-uniform meshes, **no** barriers. For
those, reach for QuantLib.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from scipy.linalg import solve_banded


class FDResult(NamedTuple):
    """A finite-difference price and its grid-based first/second Greeks.

    Attributes:
        price: the PDE option price at ``S0``.
        delta: dPrice/dS at ``S0`` by central difference on the log-spot grid.
        gamma: d2Price/dS2 at ``S0`` by central difference on the log-spot grid.
    """

    price: float
    delta: float
    gamma: float


def _psor(
    lower: np.ndarray,
    diag: np.ndarray,
    upper: np.ndarray,
    rhs: np.ndarray,
    payoff: np.ndarray,
    omega: float,
    tol: float,
    max_iter: int,
) -> np.ndarray:
    """Projected SOR solve of the LCP for the American early-exercise step.

    Solves ``A v = rhs`` subject to ``v >= payoff`` (the obstacle), where ``A``
    is the tridiagonal CN operator with bands ``lower``/``diag``/``upper``. The
    over-relaxation factor ``omega`` (default 1.2 in the caller) accelerates
    convergence; iteration stops at ``tol`` (max correction) or ``max_iter``.
    """
    v = np.maximum(rhs / diag, payoff)
    n = v.size
    for _ in range(max_iter):
        err = 0.0
        for i in range(n):
            gs = rhs[i]
            if i > 0:
                gs -= lower[i] * v[i - 1]
            if i < n - 1:
                gs -= upper[i] * v[i + 1]
            gs /= diag[i]
            candidate = max(v[i] + omega * (gs - v[i]), payoff[i])
            err = max(err, abs(candidate - v[i]))
            v[i] = candidate
        if err < tol:
            break
    return v


def fd_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    q: float = 0.0,
    american: bool = True,
    n_space: int = 200,
    n_time: int = 200,
    n_std: float = 6.0,
    rannacher_steps: int = 2,
    omega: float = 1.2,
    psor_tol: float = 1e-8,
    psor_max_iter: int = 10_000,
) -> FDResult:
    """Crank-Nicolson FD price of a vanilla American/European call/put.

    Args:
        S: current spot price.
        K: strike price.
        T: time to expiration (years).
        r: risk-free rate (annualized).
        sigma: volatility (annualized).
        option_type: ``'call'`` or ``'put'``.
        q: continuous dividend yield (annualized, default 0).
        american: if True, impose early exercise via PSOR; else European.
        n_space: number of interior log-spot grid points.
        n_time: number of time steps.
        n_std: half-width of the log-spot grid in standard deviations.
        rannacher_steps: number of fully-implicit startup steps (CN otherwise).
        omega: PSOR over-relaxation factor (American only).
        psor_tol: PSOR convergence tolerance on the max correction.
        psor_max_iter: PSOR iteration cap.

    Returns:
        An :class:`FDResult` ``(price, delta, gamma)``.

    Raises:
        ValueError: if ``option_type`` is not 'call'/'put'.
    """
    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")

    # Degenerate expiry / zero vol: intrinsic / discounted forward; the grid
    # solve is undefined (no diffusion), so return the closed-form analogue.
    def _intrinsic(spot: float) -> float:
        return max(spot - K, 0.0) if option_type == "call" else max(K - spot, 0.0)

    if T <= 0 or sigma <= 0:
        if T <= 0:
            price = _intrinsic(S)
        else:  # sigma == 0: deterministic discounted forward intrinsic
            fwd = S * np.exp((r - q) * T)
            price = _intrinsic(fwd) * np.exp(-r * T)
        # Delta is the step function; gamma 0. Keep it simple and finite.
        if option_type == "call":
            d = 1.0 if S > K else (0.5 if S == K else 0.0)
        else:
            d = -1.0 if S < K else (-0.5 if S == K else 0.0)
        return FDResult(float(price), float(d), 0.0)

    # --- log-spot grid centered on ln(S) ---
    # Force an ODD number of total points so ln(S) lands exactly on the center
    # node (no interpolation bias when reading the price/Greeks back off).
    x0 = np.log(S)
    width = n_std * sigma * np.sqrt(T)
    total = n_space + 2
    if total % 2 == 0:
        total += 1
    x = np.linspace(x0 - width, x0 + width, total)  # includes 2 boundaries
    dx = x[1] - x[0]
    dt = T / n_time
    spots = np.exp(x)
    payoff = np.array([_intrinsic(s) for s in spots])

    # Interior PDE coefficients (constant on a uniform log grid).
    nu = r - q - 0.5 * sigma * sigma
    a = 0.5 * sigma * sigma / (dx * dx) - 0.5 * nu / dx  # coeff of V_{i-1}
    b = -sigma * sigma / (dx * dx) - r  # coeff of V_i
    c = 0.5 * sigma * sigma / (dx * dx) + 0.5 * nu / dx  # coeff of V_{i+1}

    v = payoff.copy()
    n_int = x.size - 2

    def _boundaries(tau: float) -> tuple[float, float]:
        """Dirichlet values at the low/high log-spot boundary, time-to-expiry tau."""
        s_lo, s_hi = spots[0], spots[-1]
        disc_r = np.exp(-r * tau)
        disc_q = np.exp(-q * tau)
        if option_type == "call":
            lo = 0.0
            hi = s_hi * disc_q - K * disc_r
        else:
            lo = K * disc_r - s_lo * disc_q
            hi = 0.0
        return float(max(lo, 0.0)), float(max(hi, 0.0))

    for step in range(n_time):
        tau_new = T - (step + 1) * dt  # time-to-expiry after this step
        theta = 1.0 if step < rannacher_steps else 0.5  # Rannacher startup

        lo_new, hi_new = _boundaries(tau_new)
        lo_old, hi_old = _boundaries(T - step * dt)

        # Implicit (LHS) tridiagonal operator: (I - theta*dt*L) v_new = rhs.
        diag = np.full(n_int, 1.0 - theta * dt * b)
        sub = np.full(n_int, -theta * dt * a)  # multiplies v_new[i-1]
        sup = np.full(n_int, -theta * dt * c)  # multiplies v_new[i+1]

        # Explicit (RHS): (I + (1-theta)*dt*L) v_old.
        v_int = v[1:-1]
        rhs = v_int.copy()
        rhs += (
            (1.0 - theta)
            * dt
            * (
                a * np.concatenate([[lo_old], v_int[:-1]])
                + b * v_int
                + c * np.concatenate([v_int[1:], [hi_old]])
            )
        )
        # Move known new-time boundary contributions to the RHS.
        rhs[0] += theta * dt * a * lo_new
        rhs[-1] += theta * dt * c * hi_new

        if american:
            v_new = _psor(
                lower=sub,
                diag=diag,
                upper=sup,
                rhs=rhs,
                payoff=payoff[1:-1],
                omega=omega,
                tol=psor_tol,
                max_iter=psor_max_iter,
            )
        else:
            ab = np.zeros((3, n_int))
            ab[0, 1:] = sup[:-1]  # super-diagonal
            ab[1, :] = diag
            ab[2, :-1] = sub[1:]  # sub-diagonal
            v_new = solve_banded((1, 1), ab, rhs)

        v = np.concatenate([[lo_new], v_new, [hi_new]])

    # Read price + Greeks off the grid at the node nearest ln(S) (== center).
    idx = int(np.argmin(np.abs(x - x0)))
    s_c = spots[idx]
    s_up, s_dn = spots[idx + 1], spots[idx - 1]
    price = float(v[idx])
    # Non-uniform-in-S central differences (uniform in x, not in S).
    delta = float((v[idx + 1] - v[idx - 1]) / (s_up - s_dn))
    gamma = float(
        ((v[idx + 1] - v[idx]) / (s_up - s_c) - (v[idx] - v[idx - 1]) / (s_c - s_dn))
        / (0.5 * (s_up - s_dn))
    )
    return FDResult(price, delta, gamma)
