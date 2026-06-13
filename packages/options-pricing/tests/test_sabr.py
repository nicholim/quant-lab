"""Tests for the Hagan (2002) lognormal-SABR smile and calibration.

These prove the Hagan implied-vol approximation evaluates correctly (incl. the
ATM ``z/x(z) -> 1`` limit and vectorization over strikes), that
:func:`fit_sabr_slice` recovers known ``(alpha, rho, nu)`` from a generated
smile (clean and noisy) with ``beta`` fixed, and that a fitted SABR vol prices
through Black-76.
"""

import numpy as np
import pytest

from src.black_scholes import black_76_price
from src.sabr import (
    SABRParams,
    fit_sabr_slice,
    sabr_implied_vol,
    sabr_smile,
)

# A known SABR slice (beta fixed at the desk convention 0.5).
TRUE = SABRParams(alpha=0.30, beta=0.5, rho=-0.30, nu=0.40)
F = 100.0
T = 0.5


def test_atm_limit_matches_formula():
    """At K == F the z/x(z) ratio is 1, leaving the ATM expansion."""
    iv = float(sabr_implied_vol(F, F, T, *TRUE))
    one_m_beta = 1.0 - TRUE.beta
    fbeta = F**one_m_beta
    atm = (TRUE.alpha / fbeta) * (
        1.0
        + (
            (one_m_beta**2 / 24.0) * TRUE.alpha**2 / F ** (2 * one_m_beta)
            + 0.25 * TRUE.rho * TRUE.beta * TRUE.nu * TRUE.alpha / fbeta
            + (2.0 - 3.0 * TRUE.rho**2) / 24.0 * TRUE.nu**2
        )
        * T
    )
    assert iv == pytest.approx(atm, rel=1e-12)


def test_atm_continuity_around_forward():
    """No 0/0 blow-up: vol just off ATM is close to the ATM value."""
    atm = float(sabr_implied_vol(F, F, T, *TRUE))
    near = sabr_implied_vol(F, [F - 1e-6, F + 1e-6], T, *TRUE)
    assert np.all(np.isfinite(near))
    assert np.allclose(near, atm, atol=1e-4)


def test_vectorized_over_strikes():
    strikes = np.linspace(70, 130, 13)
    ivs = sabr_implied_vol(F, strikes, T, *TRUE)
    assert ivs.shape == strikes.shape
    assert np.all(ivs > 0)
    # Scalar call equals the matching element of the vector call.
    one = sabr_implied_vol(F, float(strikes[3]), T, *TRUE)
    assert float(one) == pytest.approx(float(ivs[3]))


def test_smile_has_skew():
    """rho < 0 -> downward skew: low strikes carry higher vol than high strikes."""
    lo = float(sabr_implied_vol(F, 80.0, T, *TRUE))
    hi = float(sabr_implied_vol(F, 120.0, T, *TRUE))
    assert lo > hi


def test_sabr_smile_wrapper_matches_implied_vol():
    strikes = np.array([85.0, 100.0, 115.0])
    a = sabr_smile(TRUE, F, T, strikes)
    b = sabr_implied_vol(F, strikes, T, *TRUE)
    assert np.allclose(a, b)


def test_fit_recovers_known_params():
    """Calibrating to the exact generated smile recovers (alpha, rho, nu)."""
    strikes = np.linspace(80, 120, 15)
    ivs = sabr_smile(TRUE, F, T, strikes)
    fitted = fit_sabr_slice(strikes, ivs, F, T, beta=0.5)
    assert fitted.beta == 0.5
    assert fitted.alpha == pytest.approx(TRUE.alpha, abs=1e-3)
    assert fitted.rho == pytest.approx(TRUE.rho, abs=1e-3)
    assert fitted.nu == pytest.approx(TRUE.nu, abs=1e-3)


def test_fit_on_noisy_smile():
    rng = np.random.default_rng(0)
    strikes = np.linspace(80, 120, 21)
    ivs = sabr_smile(TRUE, F, T, strikes)
    noisy = ivs + rng.normal(0, 1e-3, size=ivs.shape)
    fitted = fit_sabr_slice(strikes, noisy, F, T, beta=0.5)
    refit = sabr_smile(fitted, F, T, strikes)
    rms = float(np.sqrt(np.mean((refit - ivs) ** 2)))
    assert rms < 5e-3


def test_fit_with_explicit_initial_guess():
    strikes = np.linspace(80, 120, 15)
    ivs = sabr_smile(TRUE, F, T, strikes)
    fitted = fit_sabr_slice(strikes, ivs, F, T, beta=0.5, initial=(0.25, -0.1, 0.3))
    assert fitted.alpha == pytest.approx(TRUE.alpha, abs=1e-3)


def test_fit_too_few_points_raises():
    with pytest.raises(ValueError, match="at least 3"):
        fit_sabr_slice([95.0, 105.0], [0.2, 0.21], F, T)


def test_fit_drops_non_finite_and_nonpositive():
    strikes = np.array([80.0, 90.0, -5.0, 100.0, np.nan, 110.0, 120.0])
    ivs = np.array([0.30, 0.27, 0.5, 0.25, 0.25, 0.26, 0.28])
    fitted = fit_sabr_slice(strikes, ivs, F, T, beta=0.5)
    assert isinstance(fitted, SABRParams)
    assert fitted.alpha > 0


def test_fit_respects_fixed_beta():
    strikes = np.linspace(80, 120, 15)
    ivs = sabr_smile(SABRParams(0.3, 1.0, -0.3, 0.4), F, T, strikes)
    fitted = fit_sabr_slice(strikes, ivs, F, T, beta=1.0)
    assert fitted.beta == 1.0


def test_rejects_nonpositive_forward():
    with pytest.raises(ValueError, match="F > 0"):
        sabr_implied_vol(0.0, 100.0, T, *TRUE)


def test_rejects_nonpositive_T():
    with pytest.raises(ValueError, match="T must be positive"):
        sabr_implied_vol(F, 100.0, 0.0, *TRUE)


def test_rejects_nonpositive_strike():
    with pytest.raises(ValueError, match="K > 0"):
        sabr_implied_vol(F, [100.0, -1.0], T, *TRUE)


def test_sabr_vol_prices_through_black76():
    """A SABR vol feeds Black-76 to a finite, sensible price (the demo path)."""
    K = 110.0
    iv = float(sabr_implied_vol(F, K, T, *TRUE))
    price = black_76_price(F, K, T, r=0.02, sigma=iv, option_type="call")
    assert price > 0
    # Higher SABR vol for an ITM strike must price above a flatter ATM vol.
    iv_atm = float(sabr_implied_vol(F, F, T, *TRUE))
    price_atm = black_76_price(F, K, T, r=0.02, sigma=iv_atm, option_type="call")
    assert price > price_atm
