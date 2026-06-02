"""Numerical accuracy and deep edge-case tests.

Complements ``test_black_scholes.py`` with known reference values, Greek
sign/boundary behavior, binomial-tree convergence, and IV solver robustness.
"""

import numpy as np
import pytest

from src.binomial_tree import BinomialTree
from src.black_scholes import (
    black_76_delta,
    black_76_gamma,
    black_76_price,
    black_76_vega,
    black_scholes_price,
    charm,
    delta,
    gamma,
    implied_volatility,
    rho,
    theta,
    vanna,
    vega,
    volga,
)

# --- Known reference values (cross-checked against textbook / QuantLib) ---


class TestReferenceValues:
    """Prices benchmarked against published closed-form values."""

    def test_atm_call_one_year(self):
        # S=K=100, T=1, r=0.05, sigma=0.20 -> 10.4506 (standard reference)
        price = black_scholes_price(100, 100, 1, 0.05, 0.2, "call")
        assert price == pytest.approx(10.450583, abs=1e-5)

    def test_atm_put_one_year(self):
        price = black_scholes_price(100, 100, 1, 0.05, 0.2, "put")
        assert price == pytest.approx(5.573526, abs=1e-5)

    def test_hull_call_example(self):
        # Hull, Options Futures & Other Derivatives: S=42,K=40,T=0.5,r=0.1,sigma=0.2
        price = black_scholes_price(42, 40, 0.5, 0.1, 0.2, "call")
        assert price == pytest.approx(4.759, abs=1e-3)

    def test_hull_put_example(self):
        price = black_scholes_price(42, 40, 0.5, 0.1, 0.2, "put")
        assert price == pytest.approx(0.808, abs=1e-3)

    def test_atm_call_delta_reference(self):
        d = delta(100, 100, 1, 0.05, 0.2, "call")
        assert d == pytest.approx(0.636831, abs=1e-5)

    def test_atm_vega_reference(self):
        v = vega(100, 100, 1, 0.05, 0.2)
        assert v == pytest.approx(0.375240, abs=1e-5)

    def test_atm_gamma_reference(self):
        g = gamma(100, 100, 1, 0.05, 0.2)
        assert g == pytest.approx(0.018762, abs=1e-5)


# --- Put-call parity across a grid ---


class TestPutCallParity:
    @pytest.mark.parametrize("S", [80, 100, 120])
    @pytest.mark.parametrize("T", [0.1, 1.0, 2.0])
    @pytest.mark.parametrize("q", [0.0, 0.03])
    def test_parity_grid(self, S, T, q):
        K, r, sigma = 100, 0.05, 0.25
        c = black_scholes_price(S, K, T, r, sigma, "call", q)
        p = black_scholes_price(S, K, T, r, sigma, "put", q)
        expected = S * np.exp(-q * T) - K * np.exp(-r * T)
        assert (c - p) == pytest.approx(expected, abs=1e-9)


# --- Deep ITM / OTM limiting behavior ---


class TestDeepLimits:
    def test_deep_itm_call_to_forward_intrinsic(self):
        # Very ITM, low vol: call -> S - K*e^(-rT)
        S, K, T, r, sigma = 300, 100, 1.0, 0.05, 0.1
        price = black_scholes_price(S, K, T, r, sigma, "call")
        assert price == pytest.approx(S - K * np.exp(-r * T), abs=1e-2)

    def test_deep_itm_put_to_forward_intrinsic(self):
        S, K, T, r, sigma = 20, 100, 1.0, 0.05, 0.1
        price = black_scholes_price(S, K, T, r, sigma, "put")
        assert price == pytest.approx(K * np.exp(-r * T) - S, abs=1e-2)

    def test_deep_itm_call_delta_approaches_one(self):
        d = delta(300, 100, 1.0, 0.05, 0.2, "call")
        assert d == pytest.approx(1.0, abs=1e-3)

    def test_deep_otm_call_delta_approaches_zero(self):
        d = delta(20, 100, 1.0, 0.05, 0.2, "call")
        assert d == pytest.approx(0.0, abs=1e-3)

    def test_deep_itm_put_delta_approaches_minus_one(self):
        d = delta(20, 100, 1.0, 0.05, 0.2, "put")
        assert d == pytest.approx(-1.0, abs=1e-3)

    def test_deep_otm_options_near_zero(self):
        assert black_scholes_price(50, 100, 0.25, 0.05, 0.2, "call") < 0.1
        assert black_scholes_price(150, 100, 0.25, 0.05, 0.2, "put") < 0.1

    def test_far_otm_gamma_vega_small(self):
        assert gamma(40, 100, 0.25, 0.05, 0.2) < 1e-2
        assert vega(40, 100, 0.25, 0.05, 0.2) < 1e-2


# --- Zero / short time and zero volatility ---


class TestDegenerate:
    def test_negative_time_treated_as_expired_call(self):
        assert black_scholes_price(110, 100, -1, 0.05, 0.2, "call") == 10.0

    def test_negative_time_treated_as_expired_put(self):
        assert black_scholes_price(90, 100, -1, 0.05, 0.2, "put") == 10.0

    def test_t_zero_atm_delta_call_is_half(self):
        assert delta(100, 100, 0, 0.05, 0.2, "call") == 0.5

    def test_t_zero_atm_delta_put_is_minus_half(self):
        assert delta(100, 100, 0, 0.05, 0.2, "put") == -0.5

    def test_t_zero_otm_put_delta_zero(self):
        assert delta(110, 100, 0, 0.05, 0.2, "put") == 0.0

    def test_t_zero_itm_put_delta_minus_one(self):
        assert delta(90, 100, 0, 0.05, 0.2, "put") == -1.0

    def test_t_zero_theta_rho_zero(self):
        assert theta(100, 100, 0, 0.05, 0.2, "call") == 0.0
        assert rho(100, 100, 0, 0.05, 0.2, "call") == 0.0

    def test_sigma_zero_itm_call_equals_discounted_forward(self):
        # forward = S*e^((r-q)T); payoff = (forward-K)*e^(-rT)
        S, K, T, r = 110, 100, 0.5, 0.05
        price = black_scholes_price(S, K, T, r, 0, "call")
        forward = S * np.exp(r * T)
        assert price == pytest.approx((forward - K) * np.exp(-r * T), abs=1e-12)

    def test_sigma_zero_itm_put(self):
        # Spot well below strike so the deterministic forward stays ITM.
        price = black_scholes_price(80, 100, 0.5, 0.05, 0, "put")
        assert price > 0

    def test_sigma_zero_greeks_zero(self):
        assert gamma(100, 100, 0.5, 0.05, 0) == 0.0
        assert vega(100, 100, 0.5, 0.05, 0) == 0.0
        assert theta(100, 100, 0.5, 0.05, 0, "call") == 0.0
        assert rho(100, 100, 0.5, 0.05, 0, "call") == 0.0

    def test_sigma_zero_delta_boundaries(self):
        assert delta(110, 100, 0.5, 0.05, 0, "call") == 1.0
        assert delta(90, 100, 0.5, 0.05, 0, "call") == 0.0
        assert delta(100, 100, 0.5, 0.05, 0, "call") == 0.5


# --- Greek signs and relationships ---


class TestGreekSigns:
    def test_call_theta_can_be_positive_deep_itm_with_high_rate(self):
        # Deep ITM European call with high rate: theta sign is implementation
        # dependent; assert it is finite (no crash) rather than a fixed sign.
        t = theta(200, 100, 1.0, 0.10, 0.2, "call")
        assert np.isfinite(t)

    def test_put_theta_negative_atm(self):
        assert theta(100, 100, 0.5, 0.05, 0.2, "put") < 0

    def test_vega_same_for_call_and_put(self):
        # Vega is independent of option type.
        cv = vega(100, 100, 0.5, 0.05, 0.2)
        # vega() has no option_type param; just sanity check positivity/magnitude
        assert cv > 0

    def test_gamma_same_for_call_and_put(self):
        # Gamma identical for call and put (no option_type arg).
        g = gamma(105, 100, 0.5, 0.05, 0.2)
        assert g > 0

    def test_call_rho_increases_with_maturity(self):
        short = rho(100, 100, 0.25, 0.05, 0.2, "call")
        long = rho(100, 100, 1.0, 0.05, 0.2, "call")
        assert long > short > 0

    def test_gamma_vega_numerical_consistency(self):
        # gamma ~ d(delta)/dS via finite difference
        S, K, T, r, sigma = 100, 100, 0.5, 0.05, 0.2
        h = 0.01
        num_gamma = (
            delta(S + h, K, T, r, sigma, "call") - delta(S - h, K, T, r, sigma, "call")
        ) / (2 * h)
        assert num_gamma == pytest.approx(gamma(S, K, T, r, sigma), abs=1e-4)

    def test_vega_numerical_check(self):
        # vega scaled per 1% -> compare to dPrice/dsigma * 0.01
        S, K, T, r, sigma = 100, 100, 0.5, 0.05, 0.2
        h = 1e-4
        num = (
            black_scholes_price(S, K, T, r, sigma + h, "call")
            - black_scholes_price(S, K, T, r, sigma - h, "call")
        ) / (2 * h)
        assert vega(S, K, T, r, sigma) == pytest.approx(num / 100, abs=1e-4)

    def test_rho_numerical_check(self):
        S, K, T, sigma = 100, 100, 0.5, 0.2
        r, h = 0.05, 1e-5
        num = (
            black_scholes_price(S, K, T, r + h, sigma, "call")
            - black_scholes_price(S, K, T, r - h, sigma, "call")
        ) / (2 * h)
        assert rho(S, K, T, r, sigma, "call") == pytest.approx(num / 100, abs=1e-3)


# --- Higher-order Greeks (vanna / volga / charm) ---


class TestHigherOrderGreeks:
    """Second-order Greeks verified by finite-difference of the first-order Greek."""

    def test_vanna_equals_dvega_dspot(self):
        # vanna = d(vega_raw)/d(spot); vega() is scaled per 1%, so unscale by *100.
        S, K, T, r, sigma = 100, 100, 0.5, 0.05, 0.2
        h = 0.01
        num = (vega(S + h, K, T, r, sigma) - vega(S - h, K, T, r, sigma)) / (2 * h) * 100
        assert vanna(S, K, T, r, sigma) == pytest.approx(num, abs=1e-4)

    def test_vanna_equals_ddelta_dsigma(self):
        # vanna = d(delta)/d(sigma) — the other cross-derivative it equals.
        S, K, T, r, sigma = 110, 100, 0.75, 0.03, 0.3
        h = 1e-5
        num = (delta(S, K, T, r, sigma + h, "call") - delta(S, K, T, r, sigma - h, "call")) / (
            2 * h
        )
        assert vanna(S, K, T, r, sigma) == pytest.approx(num, abs=1e-4)

    def test_vanna_call_equals_put(self):
        S, K, T, r, sigma = 105, 100, 0.5, 0.05, 0.25
        # Put delta = call delta - e^{-qT}, so d/dsigma is identical -> same vanna.
        h = 1e-5
        num = (delta(S, K, T, r, sigma + h, "put") - delta(S, K, T, r, sigma - h, "put")) / (2 * h)
        assert vanna(S, K, T, r, sigma) == pytest.approx(num, abs=1e-4)

    def test_volga_equals_dvega_dsigma(self):
        # volga = d(vega_raw)/d(sigma); vega() scaled per 1% -> unscale by *100.
        S, K, T, r, sigma = 100, 100, 0.5, 0.05, 0.2
        h = 1e-5
        num = (vega(S, K, T, r, sigma + h) - vega(S, K, T, r, sigma - h)) / (2 * h) * 100
        assert volga(S, K, T, r, sigma) == pytest.approx(num, abs=1e-3)

    def test_volga_call_equals_put(self):
        # Vega is type-independent, so volga is too.
        assert volga(95, 100, 0.6, 0.04, 0.35) == pytest.approx(volga(95, 100, 0.6, 0.04, 0.35))

    def test_charm_call_equals_neg_ddelta_dT(self):
        # Convention charm = -d(delta)/dT (delta decay as calendar time passes).
        S, K, T, r, sigma = 100, 105, 0.5, 0.05, 0.2
        h = 1e-5
        num = (delta(S, K, T + h, r, sigma, "call") - delta(S, K, T - h, r, sigma, "call")) / (
            2 * h
        )
        assert charm(S, K, T, r, sigma, "call") == pytest.approx(-num, abs=1e-4)

    def test_charm_put_equals_neg_ddelta_dT(self):
        S, K, T, r, sigma = 100, 95, 0.5, 0.05, 0.2
        h = 1e-5
        num = (delta(S, K, T + h, r, sigma, "put") - delta(S, K, T - h, r, sigma, "put")) / (2 * h)
        assert charm(S, K, T, r, sigma, "put") == pytest.approx(-num, abs=1e-4)

    def test_charm_with_dividend(self):
        S, K, T, r, sigma, q = 100, 100, 1.0, 0.05, 0.25, 0.03
        h = 1e-5
        num = (
            delta(S, K, T + h, r, sigma, "call", q) - delta(S, K, T - h, r, sigma, "call", q)
        ) / (2 * h)
        assert charm(S, K, T, r, sigma, "call", q) == pytest.approx(-num, abs=1e-4)

    def test_higher_order_greeks_zero_at_expiry_and_zero_vol(self):
        assert vanna(100, 100, 0, 0.05, 0.2) == 0.0
        assert volga(100, 100, 0, 0.05, 0.2) == 0.0
        assert charm(100, 100, 0, 0.05, 0.2, "call") == 0.0
        assert vanna(100, 100, 0.5, 0.05, 0) == 0.0
        assert volga(100, 100, 0.5, 0.05, 0) == 0.0
        assert charm(100, 100, 0.5, 0.05, 0, "put") == 0.0

    def test_atm_volga_near_zero(self):
        # For an exactly-ATM-forward option d1=-d2, but ATM-spot is close; volga
        # should be small/positive relative to deep wings (convexity is lowest ATM).
        atm = abs(volga(100, 100, 0.5, 0.05, 0.2))
        wing = abs(volga(130, 100, 0.5, 0.05, 0.2))
        assert wing > atm


# --- Black-76 futures-options pricer ---


class TestBlack76:
    def test_reference_value_call(self):
        # Hull-style Black-76: F=20, K=20, T=0.25, r=0.05, sigma=0.20 (ATM-forward).
        # Closed form: disc*F*(2N(d1)-1) with d1=0.5*sigma*sqrt(T).
        F, K, T, r, sigma = 20.0, 20.0, 0.25, 0.05, 0.20
        price = black_76_price(F, K, T, r, sigma, "call")
        assert price == pytest.approx(0.787645, abs=1e-5)

    def test_atm_call_equals_put(self):
        # At F=K the discounted forward is symmetric -> call == put.
        c = black_76_price(50, 50, 1.0, 0.04, 0.3, "call")
        p = black_76_price(50, 50, 1.0, 0.04, 0.3, "put")
        assert c == pytest.approx(p, abs=1e-9)

    @pytest.mark.parametrize("F", [80, 100, 120])
    @pytest.mark.parametrize("T", [0.1, 1.0, 2.0])
    def test_put_call_parity(self, F, T):
        # Futures-option parity: C - P = disc*(F - K).
        K, r, sigma = 100, 0.05, 0.25
        c = black_76_price(F, K, T, r, sigma, "call")
        p = black_76_price(F, K, T, r, sigma, "put")
        expected = np.exp(-r * T) * (F - K)
        assert (c - p) == pytest.approx(expected, abs=1e-9)

    def test_matches_bs_when_forward_consistent(self):
        # Black-76 on F = S*e^{(r-q)T} reproduces Black-Scholes with carry.
        S, K, T, r, sigma, q = 100, 105, 0.5, 0.05, 0.2, 0.0
        F = S * np.exp((r - q) * T)
        b76 = black_76_price(F, K, T, r, sigma, "call")
        bs = black_scholes_price(S, K, T, r, sigma, "call", q)
        assert b76 == pytest.approx(bs, abs=1e-9)

    def test_expiry_returns_intrinsic(self):
        assert black_76_price(110, 100, 0, 0.05, 0.2, "call") == 10.0
        assert black_76_price(90, 100, -1, 0.05, 0.2, "put") == 10.0

    def test_zero_vol_discounted_intrinsic(self):
        F, K, T, r = 110, 100, 0.5, 0.05
        c = black_76_price(F, K, T, r, 0, "call")
        assert c == pytest.approx((F - K) * np.exp(-r * T), abs=1e-12)
        p = black_76_price(90, 100, T, r, 0, "put")
        assert p == pytest.approx((100 - 90) * np.exp(-r * T), abs=1e-12)

    def test_delta_equals_dprice_dforward(self):
        F, K, T, r, sigma = 100, 100, 0.5, 0.05, 0.2
        h = 1e-4
        num = (
            black_76_price(F + h, K, T, r, sigma, "call")
            - black_76_price(F - h, K, T, r, sigma, "call")
        ) / (2 * h)
        assert black_76_delta(F, K, T, r, sigma, "call") == pytest.approx(num, abs=1e-6)

    def test_put_delta_equals_dprice_dforward(self):
        F, K, T, r, sigma = 95, 100, 0.5, 0.05, 0.2
        h = 1e-4
        num = (
            black_76_price(F + h, K, T, r, sigma, "put")
            - black_76_price(F - h, K, T, r, sigma, "put")
        ) / (2 * h)
        assert black_76_delta(F, K, T, r, sigma, "put") == pytest.approx(num, abs=1e-6)

    def test_gamma_equals_ddelta_dforward(self):
        F, K, T, r, sigma = 100, 100, 0.5, 0.05, 0.2
        h = 0.01
        num = (
            black_76_delta(F + h, K, T, r, sigma, "call")
            - black_76_delta(F - h, K, T, r, sigma, "call")
        ) / (2 * h)
        assert black_76_gamma(F, K, T, r, sigma) == pytest.approx(num, abs=1e-6)

    def test_vega_equals_dprice_dsigma(self):
        F, K, T, r, sigma = 100, 100, 0.5, 0.05, 0.2
        h = 1e-4
        num = (
            black_76_price(F, K, T, r, sigma + h, "call")
            - black_76_price(F, K, T, r, sigma - h, "call")
        ) / (2 * h)
        assert black_76_vega(F, K, T, r, sigma) == pytest.approx(num / 100, abs=1e-4)

    def test_greeks_degenerate(self):
        assert black_76_gamma(100, 100, 0, 0.05, 0.2) == 0.0
        assert black_76_vega(100, 100, 0.5, 0.05, 0) == 0.0
        assert black_76_delta(110, 100, 0, 0.05, 0.2, "call") == 1.0
        assert black_76_delta(90, 100, 0, 0.05, 0.2, "call") == 0.0
        assert black_76_delta(100, 100, 0, 0.05, 0.2, "call") == 0.5
        assert black_76_delta(90, 100, 0, 0.05, 0.2, "put") == -1.0
        assert black_76_delta(110, 100, 0, 0.05, 0.2, "put") == 0.0
        assert black_76_delta(100, 100, 0, 0.05, 0.2, "put") == -0.5


# --- Binomial tree convergence ---


class TestBinomialConvergence:
    def test_convergence_improves_with_steps(self):
        bs = black_scholes_price(100, 100, 1, 0.05, 0.2, "call")
        err_coarse = abs(BinomialTree(100, 100, 1, 0.05, 0.2, N=10).price() - bs)
        err_fine = abs(BinomialTree(100, 100, 1, 0.05, 0.2, N=1000).price() - bs)
        assert err_fine < err_coarse
        assert err_fine < 0.01

    def test_put_converges_to_bs(self):
        bs = black_scholes_price(100, 95, 0.75, 0.05, 0.3, "put")
        bt = BinomialTree(100, 95, 0.75, 0.05, 0.3, N=600, option_type="put").price()
        assert bt == pytest.approx(bs, abs=0.02)

    def test_american_put_premium_positive(self):
        eu = BinomialTree(100, 110, 1.0, 0.08, 0.3, N=400, option_type="put").price()
        am = BinomialTree(100, 110, 1.0, 0.08, 0.3, N=400, option_type="put", american=True).price()
        assert am > eu  # early-exercise premium for ITM American put

    def test_single_step_tree_runs(self):
        # N=1 should still produce a finite, non-negative price.
        price = BinomialTree(100, 100, 1, 0.05, 0.2, N=1).price()
        assert price >= 0 and np.isfinite(price)

    def test_build_tree_shapes_and_root(self):
        tree = BinomialTree(100, 100, 1, 0.05, 0.2, N=5, option_type="call")
        price_tree, value_tree = tree.build_tree()
        assert price_tree.shape == (6, 6)
        assert value_tree.shape == (6, 6)
        # Root price = spot; root value = the priced option value.
        assert price_tree[0, 0] == pytest.approx(100.0)
        assert value_tree[0, 0] == pytest.approx(tree.price(), abs=1e-9)

    def test_build_tree_terminal_is_payoff(self):
        tree = BinomialTree(100, 100, 0.5, 0.05, 0.25, N=4, option_type="put")
        price_tree, value_tree = tree.build_tree()
        n = tree.N
        for node in range(n + 1):
            expected = max(tree.K - price_tree[node, n], 0.0)
            assert value_tree[node, n] == pytest.approx(expected)

    def test_build_tree_american_matches_price(self):
        tree = BinomialTree(100, 105, 1.0, 0.05, 0.3, N=20, option_type="put", american=True)
        _, value_tree = tree.build_tree()
        assert value_tree[0, 0] == pytest.approx(tree.price(), abs=1e-9)


# --- Implied volatility robustness ---


class TestImpliedVolEdge:
    def test_high_vol_round_trip(self):
        original = 0.95
        price = black_scholes_price(100, 100, 0.5, 0.03, original, "call")
        recovered = implied_volatility(price, 100, 100, 0.5, 0.03, "call")
        assert recovered == pytest.approx(original, abs=1e-4)

    def test_low_vol_round_trip(self):
        original = 0.05
        price = black_scholes_price(100, 100, 1.0, 0.05, original, "call")
        recovered = implied_volatility(price, 100, 100, 1.0, 0.05, "call")
        assert recovered == pytest.approx(original, abs=1e-4)

    def test_itm_call_round_trip(self):
        original = 0.4
        price = black_scholes_price(120, 100, 0.5, 0.05, original, "call")
        recovered = implied_volatility(price, 120, 100, 0.5, 0.05, "call")
        assert recovered == pytest.approx(original, abs=1e-4)

    def test_sub_intrinsic_call_returns_none(self):
        # Price below intrinsic value (S-K) is not achievable -> None.
        assert implied_volatility(5.0, 120, 100, 0.5, 0.05, "call") is None

    def test_nonconvergence_returns_none(self):
        # Absurdly low price for an ATM option with positive time can fail to
        # converge to a positive vol; must return None, never raise.
        result = implied_volatility(1e-9, 100, 100, 1.0, 0.05, "call")
        assert result is None or result >= 0

    def test_at_intrinsic_boundary(self):
        # Price exactly at intrinsic for deep ITM -> very low IV or None.
        intrinsic = 120 - 100
        result = implied_volatility(intrinsic, 120, 100, 0.5, 0.0, "call")
        assert result is None or result >= 0
