import numpy as np

from src.black_scholes import (
    black_scholes_price,
    delta,
    gamma,
    implied_volatility,
    rho,
    theta,
    vega,
)

# --- Pricing ---


class TestBlackScholesPrice:
    """Core pricing correctness."""

    def test_put_call_parity(self):
        """C - P = S*e^(-qT) - K*e^(-rT)."""
        S, K, T, r, sigma, q = 100, 105, 0.5, 0.05, 0.25, 0.02
        c = black_scholes_price(S, K, T, r, sigma, "call", q)
        p = black_scholes_price(S, K, T, r, sigma, "put", q)
        expected = S * np.exp(-q * T) - K * np.exp(-r * T)
        assert abs((c - p) - expected) < 1e-10

    def test_put_call_parity_no_dividend(self):
        S, K, T, r, sigma = 100, 100, 1.0, 0.05, 0.2
        c = black_scholes_price(S, K, T, r, sigma, "call")
        p = black_scholes_price(S, K, T, r, sigma, "put")
        expected = S - K * np.exp(-r * T)
        assert abs((c - p) - expected) < 1e-10

    def test_call_price_positive(self):
        assert black_scholes_price(100, 100, 1, 0.05, 0.2, "call") > 0

    def test_put_price_positive(self):
        assert black_scholes_price(100, 100, 1, 0.05, 0.2, "put") > 0

    def test_deep_itm_call_approaches_intrinsic(self):
        price = black_scholes_price(200, 100, 0.01, 0.05, 0.2, "call")
        assert price > 99  # close to S - K = 100

    def test_deep_otm_call_approaches_zero(self):
        price = black_scholes_price(50, 100, 0.01, 0.05, 0.2, "call")
        assert price < 0.01

    def test_dividend_decreases_call(self):
        no_div = black_scholes_price(100, 100, 1, 0.05, 0.2, "call", q=0)
        with_div = black_scholes_price(100, 100, 1, 0.05, 0.2, "call", q=0.03)
        assert with_div < no_div

    def test_dividend_increases_put(self):
        no_div = black_scholes_price(100, 100, 1, 0.05, 0.2, "put", q=0)
        with_div = black_scholes_price(100, 100, 1, 0.05, 0.2, "put", q=0.03)
        assert with_div > no_div


# --- Edge Cases ---


class TestEdgeCases:
    """T=0, sigma=0, and boundary behavior."""

    def test_t_zero_itm_call(self):
        assert black_scholes_price(110, 100, 0, 0.05, 0.2, "call") == 10.0

    def test_t_zero_otm_call(self):
        assert black_scholes_price(90, 100, 0, 0.05, 0.2, "call") == 0.0

    def test_t_zero_itm_put(self):
        assert black_scholes_price(90, 100, 0, 0.05, 0.2, "put") == 10.0

    def test_t_zero_atm(self):
        assert black_scholes_price(100, 100, 0, 0.05, 0.2, "call") == 0.0

    def test_sigma_zero_itm_call(self):
        price = black_scholes_price(110, 100, 0.5, 0.05, 0, "call")
        assert price > 0

    def test_sigma_zero_otm_call(self):
        assert black_scholes_price(90, 100, 0.5, 0.05, 0, "call") == 0.0

    def test_t_zero_delta_itm_call(self):
        assert delta(110, 100, 0, 0.05, 0.2, "call") == 1.0

    def test_t_zero_delta_otm_call(self):
        assert delta(90, 100, 0, 0.05, 0.2, "call") == 0.0

    def test_t_zero_gamma(self):
        assert gamma(100, 100, 0, 0.05, 0.2) == 0.0

    def test_t_zero_vega(self):
        assert vega(100, 100, 0, 0.05, 0.2) == 0.0


# --- Greeks ---


class TestGreeks:
    """Greek values, bounds, and relationships."""

    def test_call_delta_bounds(self):
        d = delta(100, 100, 0.5, 0.05, 0.2, "call")
        assert 0 <= d <= 1

    def test_put_delta_bounds(self):
        d = delta(100, 100, 0.5, 0.05, 0.2, "put")
        assert -1 <= d <= 0

    def test_call_put_delta_relationship(self):
        """call_delta - put_delta = e^(-qT) for same params."""
        q = 0.02
        cd = delta(100, 100, 0.5, 0.05, 0.2, "call", q)
        pd = delta(100, 100, 0.5, 0.05, 0.2, "put", q)
        assert abs((cd - pd) - np.exp(-q * 0.5)) < 1e-6

    def test_gamma_positive(self):
        assert gamma(100, 100, 0.5, 0.05, 0.2) > 0

    def test_gamma_peaks_atm(self):
        g_atm = gamma(100, 100, 0.5, 0.05, 0.2)
        g_itm = gamma(120, 100, 0.5, 0.05, 0.2)
        g_otm = gamma(80, 100, 0.5, 0.05, 0.2)
        assert g_atm > g_itm
        assert g_atm > g_otm

    def test_theta_negative_for_call(self):
        t = theta(100, 100, 0.5, 0.05, 0.2, "call")
        assert t < 0  # time decay

    def test_vega_positive(self):
        assert vega(100, 100, 0.5, 0.05, 0.2) > 0

    def test_call_rho_positive(self):
        assert rho(100, 100, 0.5, 0.05, 0.2, "call") > 0

    def test_put_rho_negative(self):
        assert rho(100, 100, 0.5, 0.05, 0.2, "put") < 0

    def test_delta_numerical_check(self):
        """Delta ≈ (price(S+h) - price(S-h)) / 2h."""
        S, K, T, r, sigma = 100, 100, 0.5, 0.05, 0.2
        h = 0.01
        numerical = (
            black_scholes_price(S + h, K, T, r, sigma, "call")
            - black_scholes_price(S - h, K, T, r, sigma, "call")
        ) / (2 * h)
        analytical = delta(S, K, T, r, sigma, "call")
        assert abs(numerical - analytical) < 1e-4


# --- Binomial Tree ---


class TestBinomialTree:
    """CRR model correctness."""

    def test_european_converges_to_bs(self):
        from src.binomial_tree import BinomialTree

        bs = black_scholes_price(100, 100, 1, 0.05, 0.2, "call")
        bt = BinomialTree(100, 100, 1, 0.05, 0.2, N=500, option_type="call").price()
        assert abs(bs - bt) < 0.02

    def test_american_geq_european_put(self):
        from src.binomial_tree import BinomialTree

        eu = BinomialTree(
            100, 110, 0.5, 0.05, 0.3, N=300, option_type="put", american=False
        ).price()
        am = BinomialTree(100, 110, 0.5, 0.05, 0.3, N=300, option_type="put", american=True).price()
        assert am >= eu - 1e-10

    def test_american_call_no_early_exercise_no_dividend(self):
        """Without dividends, American call = European call."""
        from src.binomial_tree import BinomialTree

        eu = BinomialTree(100, 100, 1, 0.05, 0.2, N=200, option_type="call", american=False).price()
        am = BinomialTree(100, 100, 1, 0.05, 0.2, N=200, option_type="call", american=True).price()
        assert abs(eu - am) < 0.01


# --- Implied Volatility ---


class TestImpliedVolatility:
    """IV solver correctness and robustness."""

    def test_round_trip(self):
        original = 0.25
        price = black_scholes_price(100, 100, 0.5, 0.05, original, "call")
        recovered = implied_volatility(price, 100, 100, 0.5, 0.05, "call")
        assert recovered is not None
        assert abs(recovered - original) < 1e-4

    def test_round_trip_with_dividend(self):
        original = 0.30
        price = black_scholes_price(100, 105, 0.5, 0.05, original, "put", q=0.02)
        recovered = implied_volatility(price, 100, 105, 0.5, 0.05, "put", q=0.02)
        assert recovered is not None
        assert abs(recovered - original) < 1e-4

    def test_returns_none_expired(self):
        assert implied_volatility(5.0, 100, 100, 0, 0.05, "call") is None

    def test_returns_none_sub_intrinsic_put(self):
        assert implied_volatility(50, 100, 200, 0.5, 0.05, "put") is None

    def test_deep_otm_low_vol(self):
        iv = implied_volatility(0.01, 100, 150, 0.5, 0.05, "call")
        assert iv is None or iv < 0.5  # either converges to low vol or fails gracefully
