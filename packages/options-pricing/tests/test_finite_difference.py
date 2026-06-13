"""Tests for the Crank-Nicolson finite-difference American/European pricer.

These validate: the European limit matches the closed-form Black-Scholes
tightly; the American price matches the CRR :class:`BinomialTree` benchmark;
an American call with ``q=0`` equals the European call (no early exercise);
an American put exceeds the European put (early-exercise premium); grid
refinement improves accuracy; and edge cases (``T->0``, deep ITM/OTM) behave.
"""

import numpy as np
import pytest

from src.binomial_tree import BinomialTree
from src.black_scholes import black_scholes_price
from src.black_scholes import delta as bs_delta
from src.black_scholes import gamma as bs_gamma
from src.finite_difference import FDResult, fd_price

S, K, T, r, sigma = 100.0, 105.0, 0.5, 0.05, 0.20


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_european_matches_black_scholes(option_type):
    bs = black_scholes_price(S, K, T, r, sigma, option_type)
    fd = fd_price(S, K, T, r, sigma, option_type, american=False, n_space=400, n_time=400)
    assert fd.price == pytest.approx(bs, abs=2e-3)


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_european_greeks_match_closed_form(option_type):
    fd = fd_price(S, K, T, r, sigma, option_type, american=False, n_space=400, n_time=400)
    assert fd.delta == pytest.approx(bs_delta(S, K, T, r, sigma, option_type), abs=2e-3)
    assert fd.gamma == pytest.approx(bs_gamma(S, K, T, r, sigma), abs=2e-3)


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_american_matches_binomial(option_type):
    bt = BinomialTree(S, K, T, r, sigma, N=2000, option_type=option_type, american=True).price()
    fd = fd_price(S, K, T, r, sigma, option_type, american=True, n_space=400, n_time=400)
    assert fd.price == pytest.approx(bt, abs=5e-3)


def test_american_call_no_dividend_equals_european():
    """With q=0 an American call is never exercised early -> equals European."""
    am = fd_price(S, K, T, r, sigma, "call", q=0.0, american=True, n_space=400, n_time=400)
    eu = fd_price(S, K, T, r, sigma, "call", q=0.0, american=False, n_space=400, n_time=400)
    assert am.price == pytest.approx(eu.price, abs=1e-6)


def test_american_put_exceeds_european_put():
    am = fd_price(S, K, T, r, sigma, "put", american=True, n_space=400, n_time=400)
    eu = fd_price(S, K, T, r, sigma, "put", american=False, n_space=400, n_time=400)
    assert am.price > eu.price


def test_american_call_with_dividend_exceeds_european():
    """A high dividend makes early exercise of the call valuable."""
    am = fd_price(100, 100, 1.0, 0.05, 0.3, "call", q=0.10, american=True, n_space=400, n_time=400)
    eu = fd_price(100, 100, 1.0, 0.05, 0.3, "call", q=0.10, american=False, n_space=400, n_time=400)
    assert am.price > eu.price


def test_convergence_improves_with_refinement():
    bs = black_scholes_price(S, K, T, r, sigma, "call")
    err_coarse = abs(
        fd_price(S, K, T, r, sigma, "call", american=False, n_space=50, n_time=50).price - bs
    )
    err_fine = abs(
        fd_price(S, K, T, r, sigma, "call", american=False, n_space=400, n_time=400).price - bs
    )
    assert err_fine < err_coarse


def test_returns_fdresult_namedtuple():
    fd = fd_price(S, K, T, r, sigma, "call")
    assert isinstance(fd, FDResult)
    assert fd.price > 0
    assert np.isfinite(fd.delta)
    assert np.isfinite(fd.gamma)


def test_expired_returns_intrinsic():
    assert fd_price(110, 100, 0.0, r, sigma, "call").price == pytest.approx(10.0)
    assert fd_price(90, 100, 0.0, r, sigma, "put").price == pytest.approx(10.0)
    assert fd_price(90, 100, 0.0, r, sigma, "call").price == pytest.approx(0.0)


def test_expired_delta_step():
    assert fd_price(110, 100, 0.0, r, sigma, "call").delta == 1.0
    assert fd_price(90, 100, 0.0, r, sigma, "call").delta == 0.0
    assert fd_price(100, 100, 0.0, r, sigma, "call").delta == 0.5
    assert fd_price(90, 100, 0.0, r, sigma, "put").delta == -1.0
    assert fd_price(100, 100, 0.0, r, sigma, "put").delta == -0.5


def test_zero_vol_discounted_forward():
    fd = fd_price(100, 90, T, r, 0.0, "call")
    fwd = 100 * np.exp(r * T)
    expected = (fwd - 90) * np.exp(-r * T)
    assert fd.price == pytest.approx(expected)
    assert fd.gamma == 0.0


def test_deep_itm_call_matches_black_scholes():
    bs = black_scholes_price(200, 100, T, r, sigma, "call")
    fd = fd_price(200, 100, T, r, sigma, "call", american=False, n_space=400, n_time=400)
    assert fd.price == pytest.approx(bs, abs=1e-2)


def test_deep_otm_put_near_zero():
    fd = fd_price(200, 100, T, r, sigma, "put", american=False, n_space=400, n_time=400)
    assert 0.0 <= fd.price < 1e-3


def test_invalid_option_type_raises():
    with pytest.raises(ValueError, match="option_type"):
        fd_price(S, K, T, r, sigma, "straddle")


def test_even_and_odd_grid_both_centered():
    """Both an even and odd n_space recover the price (center node forced)."""
    bs = black_scholes_price(S, K, T, r, sigma, "call")
    p_even = fd_price(S, K, T, r, sigma, "call", american=False, n_space=400, n_time=400).price
    p_odd = fd_price(S, K, T, r, sigma, "call", american=False, n_space=401, n_time=400).price
    assert p_even == pytest.approx(bs, abs=2e-3)
    assert p_odd == pytest.approx(bs, abs=2e-3)
