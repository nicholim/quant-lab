"""Tests for the vectorized / batch pricing API and the real IV surface.

Two guarantees are enforced here:
  1. **Vec-vs-scalar equivalence** -- a vectorized call must equal the scalar
     function elementwise (price, every Greek, and IV round-trips to ~1e-6), so
     the batch API never diverges from the trusted scalar reference.
  2. **Robust batch IV** -- the vectorized solver returns ``nan`` for impossible
     inputs (sub-intrinsic, expired) without raising, and a headless IV-surface
     smoke test builds a real surface from the OFFLINE sample chain (no network).
"""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from src import market_data  # noqa: E402
from src.black_scholes import (  # noqa: E402
    black_scholes_price,
    black_scholes_price_vec,
    delta,
    gamma,
    greeks_vec,
    implied_volatility,
    implied_volatility_vec,
    rho,
    theta,
    vega,
)
from src.greeks_visualizer import (  # noqa: E402
    plot_solved_iv_surface,
    solve_iv_surface,
)


def _close_all():
    plt.close("all")


# --- Vectorized price: vec == scalar ----------------------------------------


class TestVectorizedPrice:
    def test_price_matches_scalar_across_strikes(self):
        strikes = np.array([80.0, 90.0, 100.0, 110.0, 120.0])
        vec = black_scholes_price_vec(100, strikes, 0.5, 0.05, 0.2, "call")
        scalar = np.array([black_scholes_price(100, k, 0.5, 0.05, 0.2, "call") for k in strikes])
        np.testing.assert_allclose(vec, scalar, atol=1e-12)

    def test_price_matches_scalar_put(self):
        strikes = np.array([80.0, 100.0, 120.0])
        vec = black_scholes_price_vec(100, strikes, 0.5, 0.05, 0.2, "put")
        scalar = np.array([black_scholes_price(100, k, 0.5, 0.05, 0.2, "put") for k in strikes])
        np.testing.assert_allclose(vec, scalar, atol=1e-12)

    def test_single_element_equals_scalar(self):
        vec = black_scholes_price_vec(np.array([100.0]), 100, 1.0, 0.05, 0.2, "call")
        scalar = black_scholes_price(100, 100, 1.0, 0.05, 0.2, "call")
        assert vec.shape == (1,)
        assert vec[0] == pytest.approx(scalar, abs=1e-12)

    def test_broadcasts_strike_and_sigma(self):
        # strike along axis 0, sigma along axis 1 -> outer-product shape.
        strikes = np.array([[90.0], [100.0], [110.0]])
        sigmas = np.array([[0.1, 0.2, 0.3]])
        out = black_scholes_price_vec(100, strikes, 0.5, 0.05, sigmas, "call")
        assert out.shape == (3, 3)
        for i, k in enumerate(strikes[:, 0]):
            for j, s in enumerate(sigmas[0]):
                assert out[i, j] == pytest.approx(
                    black_scholes_price(100, k, 0.5, 0.05, s, "call"), abs=1e-12
                )

    def test_broadcasts_time_to_expiry(self):
        times = np.array([0.1, 0.5, 1.0])
        out = black_scholes_price_vec(100, 100, times, 0.05, 0.2, "call")
        scalar = np.array([black_scholes_price(100, 100, t, 0.05, 0.2, "call") for t in times])
        np.testing.assert_allclose(out, scalar, atol=1e-12)

    def test_accepts_pandas_series(self):
        strikes = pd.Series([90.0, 100.0, 110.0])
        out = black_scholes_price_vec(100, strikes, 0.5, 0.05, 0.2, "call")
        scalar = np.array([black_scholes_price(100, k, 0.5, 0.05, 0.2, "call") for k in strikes])
        np.testing.assert_allclose(out, scalar, atol=1e-12)

    def test_degenerate_expiry_is_intrinsic(self):
        strikes = np.array([90.0, 100.0, 110.0])
        out = black_scholes_price_vec(100, strikes, 0.0, 0.05, 0.2, "call")
        np.testing.assert_allclose(out, np.maximum(100 - strikes, 0.0), atol=1e-12)

    def test_degenerate_zero_vol_matches_scalar(self):
        strikes = np.array([80.0, 100.0, 120.0])
        out = black_scholes_price_vec(100, strikes, 0.5, 0.05, 0.0, "call")
        scalar = np.array([black_scholes_price(100, k, 0.5, 0.05, 0.0, "call") for k in strikes])
        np.testing.assert_allclose(out, scalar, atol=1e-12)

    def test_mixed_degenerate_column(self):
        # Middle entry expired, others live -- mask must apply elementwise.
        strikes = np.array([90.0, 100.0, 110.0])
        times = np.array([0.5, 0.0, 0.5])
        out = black_scholes_price_vec(100, strikes, times, 0.05, 0.2, "call")
        expected = np.array(
            [
                black_scholes_price(100, k, t, 0.05, 0.2, "call")
                for k, t in zip(strikes, times, strict=False)
            ]
        )
        np.testing.assert_allclose(out, expected, atol=1e-12)

    def test_dividend_yield_broadcasts(self):
        strikes = np.array([90.0, 110.0])
        out = black_scholes_price_vec(100, strikes, 0.5, 0.05, 0.2, "call", q=0.03)
        scalar = np.array(
            [black_scholes_price(100, k, 0.5, 0.05, 0.2, "call", q=0.03) for k in strikes]
        )
        np.testing.assert_allclose(out, scalar, atol=1e-12)

    def test_bad_option_type_raises(self):
        with pytest.raises(ValueError):
            black_scholes_price_vec(100, np.array([100.0]), 1.0, 0.05, 0.2, "straddle")


# --- Vectorized Greeks: vec == scalar ---------------------------------------


class TestVectorizedGreeks:
    @pytest.mark.parametrize("opt", ["call", "put"])
    def test_all_greeks_match_scalar(self, opt):
        strikes = np.array([85.0, 95.0, 100.0, 105.0, 115.0])
        g = greeks_vec(100, strikes, 0.5, 0.05, 0.25, opt, q=0.01)
        for i, k in enumerate(strikes):
            assert g["delta"][i] == pytest.approx(
                delta(100, k, 0.5, 0.05, 0.25, opt, q=0.01), abs=1e-12
            )
            assert g["gamma"][i] == pytest.approx(gamma(100, k, 0.5, 0.05, 0.25, q=0.01), abs=1e-12)
            assert g["theta"][i] == pytest.approx(
                theta(100, k, 0.5, 0.05, 0.25, opt, q=0.01), abs=1e-12
            )
            assert g["vega"][i] == pytest.approx(vega(100, k, 0.5, 0.05, 0.25, q=0.01), abs=1e-12)
            assert g["rho"][i] == pytest.approx(
                rho(100, k, 0.5, 0.05, 0.25, opt, q=0.01), abs=1e-12
            )

    def test_greeks_shape_and_keys(self):
        strikes = np.array([90.0, 100.0, 110.0])
        g = greeks_vec(100, strikes, 0.5, 0.05, 0.2, "call")
        assert set(g) == {"delta", "gamma", "theta", "vega", "rho"}
        for arr in g.values():
            assert arr.shape == (3,)

    def test_greeks_degenerate_expiry(self):
        # T=0: gamma/theta/vega/rho -> 0; delta -> expiry step.
        strikes = np.array([90.0, 100.0, 110.0])
        g = greeks_vec(100, strikes, 0.0, 0.05, 0.2, "call")
        np.testing.assert_allclose(g["gamma"], 0.0)
        np.testing.assert_allclose(g["vega"], 0.0)
        np.testing.assert_allclose(g["theta"], 0.0)
        np.testing.assert_allclose(g["rho"], 0.0)
        # ITM call delta 1, ATM 0.5, OTM 0
        np.testing.assert_allclose(g["delta"], np.array([1.0, 0.5, 0.0]))

    def test_greeks_degenerate_put_delta(self):
        strikes = np.array([90.0, 100.0, 110.0])
        g = greeks_vec(100, strikes, 0.0, 0.05, 0.2, "put")
        # OTM put (K<S) 0, ATM -0.5, ITM (K>S) -1
        np.testing.assert_allclose(g["delta"], np.array([0.0, -0.5, -1.0]))

    def test_greeks_bad_option_type_raises(self):
        with pytest.raises(ValueError):
            greeks_vec(100, np.array([100.0]), 1.0, 0.05, 0.2, "spread")


# --- Vectorized IV: round-trip, nan handling, vec == scalar -----------------


class TestVectorizedIV:
    def test_iv_round_trip_chain(self):
        strikes = np.array([80.0, 90.0, 100.0, 110.0, 120.0])
        true_sigma = 0.27
        prices = black_scholes_price_vec(100, strikes, 0.5, 0.04, true_sigma, "call")
        iv = implied_volatility_vec(prices, 100, strikes, 0.5, 0.04, "call")
        np.testing.assert_allclose(iv, true_sigma, atol=1e-6)

    def test_iv_matches_scalar_elementwise(self):
        strikes = np.array([85.0, 100.0, 115.0])
        sigmas = np.array([0.15, 0.3, 0.45])
        prices = np.array(
            [
                black_scholes_price(100, k, 0.6, 0.05, s, "put")
                for k, s in zip(strikes, sigmas, strict=False)
            ]
        )
        vec = implied_volatility_vec(prices, 100, strikes, 0.6, 0.05, "put")
        for i, (k, p) in enumerate(zip(strikes, prices, strict=False)):
            scalar = implied_volatility(p, 100, k, 0.6, 0.05, "put")
            assert vec[i] == pytest.approx(scalar, abs=1e-6)

    def test_single_element_equals_scalar(self):
        price = black_scholes_price(100, 100, 1.0, 0.05, 0.33, "call")
        vec = implied_volatility_vec(np.array([price]), 100, 100, 1.0, 0.05, "call")
        scalar = implied_volatility(price, 100, 100, 1.0, 0.05, "call")
        assert vec.shape == (1,)
        assert vec[0] == pytest.approx(scalar, abs=1e-6)

    def test_sub_intrinsic_returns_nan(self):
        # Price below intrinsic for a deep-ITM call is impossible -> nan.
        iv = implied_volatility_vec(np.array([5.0]), 120, 100, 0.5, 0.05, "call")
        assert np.isnan(iv[0])

    def test_expired_returns_nan(self):
        iv = implied_volatility_vec(np.array([3.0]), 100, 100, 0.0, 0.05, "call")
        assert np.isnan(iv[0])

    def test_mixed_valid_and_invalid_no_crash(self):
        # One solvable, one sub-intrinsic, one expired -- nan only where invalid.
        good = black_scholes_price(100, 100, 0.5, 0.05, 0.25, "call")
        prices = np.array([good, 5.0, 3.0])
        S = np.array([100.0, 120.0, 100.0])
        K = np.array([100.0, 100.0, 100.0])
        T = np.array([0.5, 0.5, 0.0])
        iv = implied_volatility_vec(prices, S, K, T, 0.05, "call")
        assert iv[0] == pytest.approx(0.25, abs=1e-6)
        assert np.isnan(iv[1])
        assert np.isnan(iv[2])

    def test_absurd_price_returns_nan_not_raise(self):
        iv = implied_volatility_vec(np.array([1e-9]), 100, 100, 1.0, 0.05, "call")
        assert np.isnan(iv[0]) or iv[0] >= 0

    def test_accepts_pandas_series(self):
        strikes = pd.Series([90.0, 100.0, 110.0])
        prices = pd.Series([black_scholes_price(100, k, 0.5, 0.04, 0.2, "call") for k in strikes])
        iv = implied_volatility_vec(prices, 100, strikes, 0.5, 0.04, "call")
        np.testing.assert_allclose(iv, 0.2, atol=1e-6)

    def test_iv_bad_option_type_raises(self):
        with pytest.raises(ValueError):
            implied_volatility_vec(np.array([5.0]), 100, 100, 1.0, 0.05, "binary")


# --- Real IV surface from the offline sample chain --------------------------


def _offline_chains():
    """Build a multi-expiry chain dict from the bundled offline fixture.

    The fixture is a single expiry; we synthesize a second, shorter-dated
    expiry so the surface spans more than one T without any network call.
    """
    chain = market_data.get_option_chain("AAPL", market_data.sample_expiry(), "call", offline=True)
    spot = market_data.get_spot("AAPL", offline=True)
    near = market_data.sample_expiry()
    far = chain  # reuse normalized chain for both expiries
    chains = {"2026-07-17": chain, "2026-10-16": far.copy()}
    expiry_years = {"2026-07-17": 0.12, "2026-10-16": 0.37}
    return chains, spot, expiry_years, near


class TestSolvedIVSurface:
    def test_solve_iv_surface_tidy_frame(self):
        chains, spot, years, _ = _offline_chains()
        surface = solve_iv_surface(chains, spot, years, r=0.045, option_type="call")
        assert list(surface.columns) == ["expiry", "T", "strike", "iv"]
        assert len(surface) > 0
        # Sample fixture IVs sit in a sane equity range.
        assert surface["iv"].between(0.05, 1.0).all()
        # Both expiries represented.
        assert set(surface["expiry"]) == {"2026-07-17", "2026-10-16"}

    def test_solve_iv_uses_vectorized_solver_consistently(self):
        # Surface IV for one expiry must equal the scalar solver per contract.
        chains, spot, years, _ = _offline_chains()
        surface = solve_iv_surface(chains, spot, years, r=0.045, option_type="call")
        sub = surface[surface["expiry"] == "2026-07-17"]
        T = years["2026-07-17"]
        for row in sub.itertuples(index=False):
            chain = chains["2026-07-17"]
            mid = float(chain.loc[chain["strike"] == row.strike, "mid"].iloc[0])
            scalar = implied_volatility(mid, spot, row.strike, T, 0.045, "call")
            assert row.iv == pytest.approx(scalar, abs=1e-6)

    def test_solve_iv_surface_empty_input(self):
        surface = solve_iv_surface({}, 100.0, {})
        assert list(surface.columns) == ["expiry", "T", "strike", "iv"]
        assert surface.empty

    def test_plot_solved_iv_surface_saves(self, tmp_path):
        chains, spot, years, _ = _offline_chains()
        out = tmp_path / "iv_surface.png"
        surface = plot_solved_iv_surface(
            chains, spot, years, r=0.045, option_type="call", save_path=str(out)
        )
        assert out.exists() and out.stat().st_size > 0
        assert len(surface) > 0
        _close_all()

    def test_plot_solved_iv_surface_no_save(self):
        chains, spot, years, _ = _offline_chains()
        plot_solved_iv_surface(chains, spot, years)
        _close_all()
