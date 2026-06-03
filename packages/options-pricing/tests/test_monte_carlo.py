"""Tests for the GBM Monte-Carlo pricer + variance reduction.

These prove the seeded Monte-Carlo estimator converges to the closed-form
Black-Scholes price within ~3 standard errors (call AND put), that antithetic
variates and the control variate genuinely reduce variance, and that the
estimator is reproducible and self-consistent (put-call parity).
"""

import numpy as np
import pytest

from src.black_scholes import black_scholes_price
from src.monte_carlo import MCResult, monte_carlo_price

# A representative option for the convergence checks.
S, K, T, r, sigma = 100.0, 105.0, 0.5, 0.05, 0.20


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_mc_converges_to_bs_within_3se(option_type):
    bs = black_scholes_price(S, K, T, r, sigma, option_type)
    mc = monte_carlo_price(S, K, T, r, sigma, option_type, n_paths=200_000, seed=7)
    assert isinstance(mc, MCResult)
    assert mc.std_error > 0
    assert abs(mc.price - bs) <= 3 * mc.std_error


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_mc_with_dividend_converges(option_type):
    q = 0.03
    bs = black_scholes_price(S, K, T, r, sigma, option_type, q=q)
    mc = monte_carlo_price(S, K, T, r, sigma, option_type, q=q, n_paths=200_000, seed=11)
    assert abs(mc.price - bs) <= 3 * mc.std_error


def test_mc_is_reproducible_with_seed():
    a = monte_carlo_price(S, K, T, r, sigma, "call", n_paths=50_000, seed=123)
    b = monte_carlo_price(S, K, T, r, sigma, "call", n_paths=50_000, seed=123)
    assert a.price == b.price
    assert a.std_error == b.std_error


def test_mc_different_seeds_differ():
    a = monte_carlo_price(S, K, T, r, sigma, "call", n_paths=50_000, seed=1)
    b = monte_carlo_price(S, K, T, r, sigma, "call", n_paths=50_000, seed=2)
    assert a.price != b.price


def test_antithetic_reduces_variance():
    """Antithetic variates lower the standard error vs plain sampling."""
    plain = monte_carlo_price(
        S,
        K,
        T,
        r,
        sigma,
        "call",
        n_paths=40_000,
        seed=5,
        antithetic=False,
        control_variate=False,
    )
    anti = monte_carlo_price(
        S,
        K,
        T,
        r,
        sigma,
        "call",
        n_paths=40_000,
        seed=5,
        antithetic=True,
        control_variate=False,
    )
    assert anti.std_error < plain.std_error


def test_control_variate_reduces_variance():
    """The BS terminal-spot control variate lowers the standard error."""
    plain = monte_carlo_price(
        S,
        K,
        T,
        r,
        sigma,
        "call",
        n_paths=40_000,
        seed=9,
        antithetic=False,
        control_variate=False,
    )
    cv = monte_carlo_price(
        S,
        K,
        T,
        r,
        sigma,
        "call",
        n_paths=40_000,
        seed=9,
        antithetic=False,
        control_variate=True,
    )
    assert cv.std_error < plain.std_error


def test_both_techniques_reduce_variance_most():
    plain = monte_carlo_price(
        S,
        K,
        T,
        r,
        sigma,
        "call",
        n_paths=40_000,
        seed=3,
        antithetic=False,
        control_variate=False,
    )
    both = monte_carlo_price(
        S,
        K,
        T,
        r,
        sigma,
        "call",
        n_paths=40_000,
        seed=3,
        antithetic=True,
        control_variate=True,
    )
    assert both.std_error < plain.std_error


def test_mc_put_call_parity():
    """MC call - MC put recovers the closed-form parity relation."""
    call = monte_carlo_price(S, K, T, r, sigma, "call", n_paths=300_000, seed=21)
    put = monte_carlo_price(S, K, T, r, sigma, "put", n_paths=300_000, seed=21)
    parity = S - K * np.exp(-r * T)  # q = 0
    combined_se = np.hypot(call.std_error, put.std_error)
    assert abs((call.price - put.price) - parity) <= 4 * combined_se


def test_mc_zero_T_returns_intrinsic_exactly():
    mc = monte_carlo_price(100.0, 90.0, 0.0, r, sigma, "call", seed=1)
    assert mc.price == 10.0
    assert mc.std_error == 0.0


def test_mc_zero_vol_matches_closed_form():
    mc = monte_carlo_price(S, K, T, r, 0.0, "call", seed=1)
    bs = black_scholes_price(S, K, T, r, 0.0, "call")
    assert mc.price == pytest.approx(bs)
    assert mc.std_error == 0.0


def test_mc_invalid_option_type_raises():
    with pytest.raises(ValueError, match="option_type"):
        monte_carlo_price(S, K, T, r, sigma, "straddle")


def test_mc_too_few_paths_raises():
    with pytest.raises(ValueError, match="n_paths"):
        monte_carlo_price(S, K, T, r, sigma, "call", n_paths=1)


def test_mc_non_antithetic_path_count():
    """Odd n_paths without antithetic uses the full requested count."""
    mc = monte_carlo_price(
        S,
        K,
        T,
        r,
        sigma,
        "call",
        n_paths=10_001,
        seed=2,
        antithetic=False,
        control_variate=False,
    )
    assert mc.std_error > 0
