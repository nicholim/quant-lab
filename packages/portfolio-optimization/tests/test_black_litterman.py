"""Black-Litterman expected-returns model tests.

These inject synthetic returns directly (no network), mirroring how the
backtesting-framework drives the optimizer: set ``.returns`` / ``.mean_returns``
/ ``.cov_matrix`` then call the zero-arg-capable ``optimize_black_litterman()``.
Black-Litterman is additive and API-safe -- the helper lives in its own module
(mirroring ``covariance.py``) and the optimizer entry point returns the same
``PortfolioResult`` as every other objective and restores ``mean_returns``.
"""

import numpy as np
import pandas as pd
import pytest

from portfolio_optimization_engine import metrics as M
from portfolio_optimization_engine.black_litterman import (
    black_litterman,
    market_implied_prior,
)
from portfolio_optimization_engine.optimizer import PortfolioOptimizer


def make_optimizer(tickers, returns_df, risk_free_rate=0.02):
    """Build an optimizer with injected returns (the backtester's pattern)."""
    o = PortfolioOptimizer(tickers, "2021-01-01", "2022-01-01", risk_free_rate=risk_free_rate)
    o.returns = returns_df
    o.mean_returns = returns_df.mean() * 252
    o.cov_matrix = returns_df.cov() * 252
    return o


def synthetic_returns(seed=7, n_obs=600, tickers=("A", "B", "C")):
    rng = np.random.default_rng(seed)
    mus = np.linspace(0.0004, 0.0009, len(tickers))
    sigmas = np.linspace(0.010, 0.020, len(tickers))
    data = rng.normal(mus, sigmas, size=(n_obs, len(tickers)))
    df = pd.DataFrame(data, columns=list(tickers), index=pd.date_range("2021-01-01", periods=n_obs))
    return list(tickers), df


@pytest.fixture
def opt3():
    tickers, df = synthetic_returns()
    return tickers, make_optimizer(tickers, df)


# --- The pure helper: prior + master formula ---


class TestHelper:
    def test_market_implied_prior_formula(self):
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        w = np.array([0.5, 0.5])
        delta = 2.5
        pi = market_implied_prior(sigma, w, delta)
        # Pi = delta * Sigma @ w  -- hand-computed
        expected = delta * (sigma @ w)
        assert np.allclose(pi, expected)
        assert np.allclose(pi, [0.0575, 0.12])

    def test_no_views_returns_prior(self):
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        w = np.array([0.5, 0.5])
        pi = market_implied_prior(sigma, w, 2.5)
        post = black_litterman(sigma, w_mkt=w, risk_aversion=2.5)
        assert np.allclose(post, pi)

    def test_default_w_mkt_is_equal_weight(self):
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        post_default = black_litterman(sigma, risk_aversion=2.5)
        post_eq = black_litterman(sigma, w_mkt=np.array([0.5, 0.5]), risk_aversion=2.5)
        assert np.allclose(post_default, post_eq)

    def test_zero_confidence_view_returns_prior(self):
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        w = np.array([0.5, 0.5])
        pi = market_implied_prior(sigma, w, 2.5)
        # bullish view but with enormous uncertainty -> ignored
        P = np.array([[1.0, 0.0]])
        Q = np.array([pi[0] + 0.05])
        post = black_litterman(
            sigma, w_mkt=w, P=P, Q=Q, omega=np.array([[1e12]]), risk_aversion=2.5
        )
        assert np.allclose(post, pi, atol=1e-6)

    def test_empty_views_returns_prior(self):
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        pi = black_litterman(sigma)
        post_empty_p = black_litterman(sigma, P=np.empty((0, 2)), Q=np.array([]))
        assert np.allclose(post_empty_p, pi)
        # P given but Q missing -> still prior
        assert np.allclose(black_litterman(sigma, P=np.array([[1.0, 0.0]])), pi)

    def test_bullish_absolute_view_shifts_posterior_up(self):
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        w = np.array([0.5, 0.5])
        pi = market_implied_prior(sigma, w, 2.5)
        P = np.array([[1.0, 0.0]])
        Q = np.array([pi[0] + 0.05])  # bullish on asset 0
        post = black_litterman(sigma, w_mkt=w, P=P, Q=Q, risk_aversion=2.5, tau=0.05)
        assert post[0] > pi[0]

    def test_bearish_absolute_view_shifts_posterior_down(self):
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        w = np.array([0.5, 0.5])
        pi = market_implied_prior(sigma, w, 2.5)
        P = np.array([[0.0, 1.0]])
        Q = np.array([pi[1] - 0.05])  # bearish on asset 1
        post = black_litterman(sigma, w_mkt=w, P=P, Q=Q, risk_aversion=2.5, tau=0.05)
        assert post[1] < pi[1]

    def test_default_omega_diag_tau_p_sigma_pt(self):
        # When omega is None it should equal diag(tau * P Sigma P^T); a custom
        # omega exactly equal to that default must give the identical posterior.
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        w = np.array([0.4, 0.6])
        P = np.array([[1.0, 0.0], [0.0, 1.0]])
        Q = np.array([0.10, 0.08])
        tau = 0.05
        default_omega = np.diag(np.diag(P @ (tau * sigma) @ P.T))
        a = black_litterman(sigma, w_mkt=w, P=P, Q=Q, tau=tau, risk_aversion=2.5)
        b = black_litterman(
            sigma, w_mkt=w, P=P, Q=Q, omega=default_omega, tau=tau, risk_aversion=2.5
        )
        assert np.allclose(a, b)

    def test_higher_confidence_pulls_posterior_closer_to_view(self):
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        w = np.array([0.5, 0.5])
        P = np.array([[1.0, 0.0]])
        Q = np.array([0.20])
        loose = black_litterman(
            sigma, w_mkt=w, P=P, Q=Q, omega=np.array([[0.5]]), risk_aversion=2.5
        )
        tight = black_litterman(
            sigma, w_mkt=w, P=P, Q=Q, omega=np.array([[1e-5]]), risk_aversion=2.5
        )
        # tighter omega -> posterior for asset 0 nearer the (high) view return
        assert abs(tight[0] - 0.20) < abs(loose[0] - 0.20)

    def test_pi_override_bypasses_reverse_optimization(self):
        sigma = np.array([[0.04, 0.006], [0.006, 0.09]])
        custom_pi = np.array([0.10, 0.03])
        post = black_litterman(sigma, pi=custom_pi)
        assert np.allclose(post, custom_pi)

    def test_finite_and_sane_three_assets(self):
        _, df = synthetic_returns()
        cov = df.cov() * 252
        post = black_litterman(
            cov,
            P=np.array([[1.0, 0.0, 0.0], [0.0, 1.0, -1.0]]),
            Q=np.array([0.12, 0.02]),
        )
        assert post.shape == (3,)
        assert np.all(np.isfinite(post))
        # plausible annualized magnitudes (not exploded by inversion)
        assert np.all(np.abs(post) < 5.0)

    def test_dataframe_cov_accepted(self):
        _, df = synthetic_returns()
        cov_df = df.cov() * 252
        post = black_litterman(cov_df)
        assert post.shape == (3,)
        assert np.all(np.isfinite(post))

    # --- validation ---

    def test_bad_cov_shape_raises(self):
        with pytest.raises(ValueError, match="square"):
            black_litterman(np.array([1.0, 2.0, 3.0]))

    def test_bad_w_mkt_length_raises(self):
        sigma = np.eye(2)
        with pytest.raises(ValueError, match="w_mkt"):
            black_litterman(sigma, w_mkt=np.array([0.3, 0.3, 0.4]))

    def test_bad_pi_length_raises(self):
        sigma = np.eye(2)
        with pytest.raises(ValueError, match="pi"):
            black_litterman(sigma, pi=np.array([0.1, 0.1, 0.1]))

    def test_bad_p_columns_raises(self):
        sigma = np.eye(2)
        with pytest.raises(ValueError, match="columns"):
            black_litterman(sigma, P=np.array([[1.0, 0.0, 0.0]]), Q=np.array([0.1]))

    def test_q_length_mismatch_raises(self):
        sigma = np.eye(2)
        with pytest.raises(ValueError, match="Q must have length"):
            black_litterman(sigma, P=np.array([[1.0, 0.0]]), Q=np.array([0.1, 0.2]))

    def test_bad_omega_shape_raises(self):
        sigma = np.eye(2)
        with pytest.raises(ValueError, match="omega"):
            black_litterman(sigma, P=np.array([[1.0, 0.0]]), Q=np.array([0.1]), omega=np.eye(2))


# --- The optimizer entry point ---


class TestOptimizerEntryPoint:
    def test_before_calculate_raises(self):
        o = PortfolioOptimizer(["A", "B"], "2021-01-01", "2022-01-01")
        with pytest.raises(ValueError, match="calculate_returns"):
            o.optimize_black_litterman()
        with pytest.raises(ValueError, match="calculate_returns"):
            o.black_litterman_returns()

    def test_returns_valid_long_only_weights(self, opt3):
        tickers, o = opt3
        res = o.optimize_black_litterman()
        assert res.weights.shape == (len(tickers),)
        assert np.isclose(res.weights.sum(), 1.0)
        assert np.all(res.weights >= -1e-9)
        assert res.objective == "black_litterman"

    def test_no_views_posterior_equals_prior_series(self, opt3):
        tickers, o = opt3
        cov = np.asarray(o.cov_matrix)
        prior = market_implied_prior(cov, np.full(len(tickers), 1.0 / len(tickers)), 2.5)
        post = o.black_litterman_returns()
        assert list(post.index) == tickers
        assert np.allclose(post.values, prior)

    def test_bullish_view_tilts_weight_toward_asset(self, opt3):
        tickers, o = opt3
        prior = o.black_litterman_returns()
        # strong bullish absolute view on the first asset
        P = np.zeros((1, len(tickers)))
        P[0, 0] = 1.0
        Q = np.array([prior.iloc[0] + 0.10])
        with_view = o.optimize_black_litterman(P, Q)
        no_view = o.optimize_black_litterman()
        assert with_view.weights[0] > no_view.weights[0]

    def test_bullish_view_raises_posterior_return(self, opt3):
        tickers, o = opt3
        prior = o.black_litterman_returns()
        P = np.zeros((1, len(tickers)))
        P[0, 0] = 1.0
        Q = np.array([prior.iloc[0] + 0.10])
        post = o.black_litterman_returns(P, Q)
        assert post.iloc[0] > prior.iloc[0]

    def test_mean_returns_restored_after_call(self, opt3):
        _, o = opt3
        saved = o.mean_returns.copy()
        o.optimize_black_litterman(np.array([[1.0, 0.0, 0.0]]), np.array([0.30]))
        assert np.allclose(o.mean_returns.values, saved.values)
        assert list(o.mean_returns.index) == list(saved.index)

    def test_metrics_parity_via_shared_path(self, opt3):
        _, o = opt3
        res = o.optimize_black_litterman()
        w = res.weights
        # The reported return is computed against the BL POSTERIOR (which is set
        # as mean_returns during the solve, then restored), so it equals the
        # posterior @ w -- NOT the historical-sample return.
        posterior = o.black_litterman_returns()
        assert np.isclose(res.expected_return, float(posterior.values @ w))
        # Volatility comes from the (unchanged) covariance via the same method
        # every other objective uses, so it matches portfolio_volatility exactly.
        assert np.isclose(res.volatility, o.portfolio_volatility(w))
        # Reported Sharpe is internally consistent with the reported return/vol.
        assert np.isclose(
            res.sharpe_ratio, (res.expected_return - o.risk_free_rate) / res.volatility
        )
        # Vol from the cov matrix matches the shared metrics module's realized
        # annualized stat (both are sample-std based) to tight tolerance.
        port_daily = o.returns.values @ w
        assert np.isclose(res.volatility, M.annualized_volatility(pd.Series(port_daily)), rtol=1e-6)

    def test_constraints_respected(self, opt3):
        tickers, o = opt3
        res = o.optimize_black_litterman(max_weights=0.5)
        assert np.all(res.weights <= 0.5 + 1e-6)
        assert np.isclose(res.weights.sum(), 1.0)

    def test_two_asset_hand_checkable(self):
        # Build a 2-asset optimizer with a known covariance and verify the prior
        # series matches the reverse-optimization formula.
        rng = np.random.default_rng(3)
        tickers = ["A", "B"]
        df = pd.DataFrame(
            rng.normal([0.0005, 0.0003], [0.012, 0.008], size=(400, 2)),
            columns=tickers,
            index=pd.date_range("2021-01-01", periods=400),
        )
        o = make_optimizer(tickers, df)
        cov = np.asarray(o.cov_matrix)
        w = np.array([0.5, 0.5])
        expected_prior = 2.5 * (cov @ w)
        prior = o.black_litterman_returns(w_mkt=w, risk_aversion=2.5)
        assert np.allclose(prior.values, expected_prior)
        # a bearish absolute view on B lowers its posterior and its weight
        P = np.array([[0.0, 1.0]])
        Q = np.array([prior.iloc[1] - 0.10])
        post = o.black_litterman_returns(P, Q, w_mkt=w, risk_aversion=2.5)
        assert post.iloc[1] < prior.iloc[1]
        bearish = o.optimize_black_litterman(P, Q, w_mkt=w, risk_aversion=2.5)
        neutral = o.optimize_black_litterman(w_mkt=w, risk_aversion=2.5)
        assert bearish.weights[1] <= neutral.weights[1] + 1e-6

    def test_single_asset_degenerate(self):
        tickers = ["A"]
        df = pd.DataFrame(
            np.random.default_rng(1).normal(0.0005, 0.01, size=(300, 1)),
            columns=tickers,
            index=pd.date_range("2021-01-01", periods=300),
        )
        o = make_optimizer(tickers, df)
        res = o.optimize_black_litterman()
        assert np.isclose(res.weights[0], 1.0)
