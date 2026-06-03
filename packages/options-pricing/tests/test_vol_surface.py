"""Tests for the Gatheral raw-SVI smile fit.

These prove SVI recovers a known synthetic (arbitrage-free) total-variance
curve to low residual, that :func:`svi_total_variance` / :func:`svi_implied_vol`
evaluate correctly, and that :func:`fit_svi_surface` consumes the tidy
``solve_iv_surface`` output verbatim and fits each expiry.
"""

import numpy as np
import pandas as pd
import pytest

from src.greeks_visualizer import solve_iv_surface
from src.market_data import _years_to_expiry, get_option_chain, get_spot
from src.vol_surface import (
    SVIParams,
    fit_svi_slice,
    fit_svi_surface,
    svi_implied_vol,
    svi_smile,
    svi_total_variance,
)

# A known arbitrage-free raw-SVI slice (b >= 0, |rho| < 1, s > 0).
TRUE = SVIParams(a=0.04, b=0.20, rho=-0.30, m=0.0, sigma=0.15)


def test_svi_total_variance_scalar_and_array():
    # At k = m the sqrt term is exactly s, so w = a + b*s.
    w0 = svi_total_variance(TRUE.m, TRUE)
    assert float(w0) == pytest.approx(TRUE.a + TRUE.b * TRUE.sigma)
    ks = np.linspace(-0.5, 0.5, 11)
    w = svi_total_variance(ks, TRUE)
    assert w.shape == ks.shape
    assert np.all(w > 0)


def test_svi_total_variance_accepts_tuple():
    w = svi_total_variance(0.1, tuple(TRUE))
    assert np.isfinite(float(w))


def test_fit_recovers_known_smile():
    """Fitting the exact SVI curve recovers params to a tiny residual."""
    k = np.linspace(-0.4, 0.4, 21)
    w = svi_total_variance(k, TRUE)
    fitted = fit_svi_slice(k, w)
    w_fit = svi_total_variance(k, fitted)
    rms = float(np.sqrt(np.mean((w_fit - w) ** 2)))
    assert rms < 1e-4


def test_fit_recovers_known_smile_with_noise():
    rng = np.random.default_rng(0)
    k = np.linspace(-0.4, 0.4, 25)
    w = svi_total_variance(k, TRUE)
    noisy = w + rng.normal(0, 1e-4, size=w.shape)
    fitted = fit_svi_slice(k, noisy)
    w_fit = svi_total_variance(k, fitted)
    rms = float(np.sqrt(np.mean((w_fit - w) ** 2)))
    assert rms < 5e-3


def test_svi_implied_vol_round_trips():
    """sqrt(w / T) recovers the IV that generated the total variance."""
    T = 0.5
    iv_true = 0.25
    k = np.array([-0.2, 0.0, 0.2])
    # A flat SVI: b=0, a = iv^2 * T.
    flat = SVIParams(a=iv_true**2 * T, b=0.0, rho=0.0, m=0.0, sigma=0.1)
    iv = svi_implied_vol(k, flat, T)
    assert np.allclose(iv, iv_true)


def test_svi_implied_vol_rejects_nonpositive_T():
    with pytest.raises(ValueError, match="T must be positive"):
        svi_implied_vol(0.0, TRUE, 0.0)


def test_fit_slice_too_few_points_raises():
    with pytest.raises(ValueError, match="at least 3"):
        fit_svi_slice([0.0, 0.1], [0.04, 0.05])


def test_fit_slice_drops_non_finite_points():
    k = np.array([-0.2, -0.1, np.nan, 0.1, 0.2])
    w = np.array([0.06, 0.05, 0.05, 0.05, 0.06])
    fitted = fit_svi_slice(k, w)
    assert isinstance(fitted, SVIParams)
    assert np.isfinite(fitted.a)


def test_svi_smile_over_strikes():
    forward = 100.0
    T = 0.5
    strikes = np.array([80.0, 100.0, 120.0])
    iv = svi_smile(TRUE, T, strikes, forward)
    assert iv.shape == strikes.shape
    assert np.all(iv > 0)


def _solved_surface(option_type: str = "call"):
    """Build a real solved-IV surface from the offline fixture (multi-expiry)."""
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc)
    expiries = [(base + timedelta(days=d)).strftime("%Y-%m-%d") for d in (20, 60, 120)]
    spot = get_spot("AAPL", offline=True)
    chains = {e: get_option_chain("AAPL", e, option_type, offline=True) for e in expiries}
    years = {e: _years_to_expiry(e) for e in expiries}
    surface = solve_iv_surface(chains, spot, years, option_type=option_type)
    return surface, spot


def test_fit_svi_surface_multiple_expiries():
    surface, spot = _solved_surface("call")
    assert not surface.empty
    fits = fit_svi_surface(surface, spot)
    # At least 2 expiries fitted.
    assert len(fits) >= 2
    for params in fits.values():
        assert isinstance(params, SVIParams)
        assert params.b >= 0
        assert -1 < params.rho < 1
        assert params.sigma > 0


def test_fit_svi_surface_fits_observed_iv_closely():
    """Each fitted slice tracks the solved IV smile to a low total-variance RMS."""
    surface, spot = _solved_surface("call")
    fits = fit_svi_surface(surface, spot)
    assert fits
    for expiry, params in fits.items():
        sub = surface[surface["expiry"] == expiry]
        T = float(sub["T"].iloc[0])
        forward = spot * np.exp(0.045 * T)
        k = np.log(sub["strike"].to_numpy(dtype=float) / forward)
        w_obs = sub["iv"].to_numpy(dtype=float) ** 2 * T
        w_fit = svi_total_variance(k, params)
        rms = float(np.sqrt(np.mean((w_fit - w_obs) ** 2)))
        # Solved IV is reasonably smooth -> SVI should track it tightly.
        assert rms < 1e-2


def test_fit_svi_surface_skips_sparse_expiries():
    """An expiry with <3 solvable points is skipped, not raised on."""
    df = pd.DataFrame(
        {
            "expiry": ["2030-01-01", "2030-01-01", "2030-06-01", "2030-06-01", "2030-06-01"],
            "T": [3.0, 3.0, 3.5, 3.5, 3.5],
            "strike": [90.0, 110.0, 80.0, 100.0, 120.0],
            "iv": [0.25, 0.22, 0.30, 0.20, 0.28],
        }
    )
    fits = fit_svi_surface(df, spot=100.0)
    # The 2-point expiry is dropped; the 3-point one is kept.
    assert "2030-01-01" not in fits
    assert "2030-06-01" in fits


def test_fit_svi_surface_skips_expired_slice():
    """A T <= 0 (expired) slice is skipped rather than fit."""
    df = pd.DataFrame(
        {
            "expiry": ["2020-01-01", "2020-01-01", "2020-01-01"],
            "T": [0.0, 0.0, 0.0],
            "strike": [90.0, 100.0, 110.0],
            "iv": [0.25, 0.20, 0.24],
        }
    )
    fits = fit_svi_surface(df, spot=100.0)
    assert fits == {}
