"""Tests for the covariance + mean estimators (covariance.py / estimators.py).

Numpy-only, deterministic, offline.
"""

import numpy as np
import pytest

from portfolio_optimization_engine.covariance import (
    estimate_covariance,
    ewma_covariance,
    mp_denoise,
    oas_shrinkage,
)
from portfolio_optimization_engine.estimators import (
    estimate_mean,
    ewma_mean,
    james_stein_mean,
)


@pytest.fixture
def returns():
    rng = np.random.default_rng(11)
    return rng.normal(0.0005, 0.01, size=(300, 5))


# --- EWMA covariance ---------------------------------------------------------


class TestEWMACovariance:
    def test_symmetric_and_shape(self, returns):
        cov = ewma_covariance(returns, lam=0.94)
        assert cov.shape == (5, 5)
        np.testing.assert_allclose(cov, cov.T, atol=1e-15)

    def test_recency_property(self):
        """A late variance spike raises the EWMA variance above the equal-weighted one."""
        rng = np.random.default_rng(3)
        calm = rng.normal(0.0, 0.005, size=(200, 1))
        wild = rng.normal(0.0, 0.05, size=(50, 1))  # recent high-vol regime
        series = np.vstack([calm, wild])
        ewma_var = ewma_covariance(series, lam=0.90)[0, 0]
        sample_var = np.cov(series, rowvar=False, ddof=1)
        assert ewma_var > float(sample_var)

    def test_invalid_lambda(self, returns):
        with pytest.raises(ValueError, match="lam"):
            ewma_covariance(returns, lam=1.0)

    def test_too_few_obs(self):
        with pytest.raises(ValueError, match="2 observations"):
            ewma_covariance(np.zeros((1, 3)))


# --- OAS shrinkage -----------------------------------------------------------


class TestOAS:
    def test_intensity_in_unit_interval(self, returns):
        cov, intensity = oas_shrinkage(returns)
        assert 0.0 <= intensity <= 1.0
        np.testing.assert_allclose(cov, cov.T, atol=1e-15)

    def test_hand_computed_two_asset_case(self):
        """Verify the closed form against a direct re-derivation on a tiny sample."""
        X = np.array(
            [
                [0.01, -0.02],
                [-0.005, 0.015],
                [0.02, 0.01],
                [-0.01, -0.005],
            ]
        )
        cov, intensity = oas_shrinkage(X)
        # Re-derive the OAS pieces independently.
        T, n = X.shape
        Xc = X - X.mean(axis=0)
        S = (Xc.T @ Xc) / T
        mu = np.trace(S) / n
        tr_s2 = np.sum(S * S)
        tr_s_2 = np.trace(S) ** 2
        num = (1 - 2 / n) * tr_s2 + tr_s_2
        den = (T + 1 - 2 / n) * (tr_s2 - tr_s_2 / n)
        rho = max(0.0, min(1.0, num / den))
        expected = (1 - rho) * S + rho * mu * np.eye(n)
        assert intensity == pytest.approx(rho)
        np.testing.assert_allclose(cov, expected, atol=1e-15)

    def test_single_asset_returns_sample(self):
        cov, intensity = oas_shrinkage(np.array([[0.01], [0.02], [-0.01]]))
        assert intensity == 0.0


# --- Marchenko-Pastur denoising ---------------------------------------------


class TestMPDenoise:
    def test_preserves_trace(self, returns):
        sample = np.cov(returns, rowvar=False, ddof=0)
        denoised = mp_denoise(returns)
        # variances (diagonal) preserved => trace preserved
        np.testing.assert_allclose(np.diag(denoised), np.diag(sample), atol=1e-12)
        assert np.trace(denoised) == pytest.approx(np.trace(sample), abs=1e-10)

    def test_psd(self, returns):
        denoised = mp_denoise(returns)
        eig = np.linalg.eigvalsh(denoised)
        assert eig.min() >= -1e-10

    def test_reduces_eigenvalue_dispersion(self):
        """Denoising clips the noisy bulk -> lower correlation-eigenvalue spread."""
        rng = np.random.default_rng(9)
        # nearly-noise returns where many small eigenvalues are pure noise
        X = rng.normal(0.0, 0.01, size=(120, 20))
        std = X.std(axis=0, ddof=0)
        corr = np.corrcoef(X, rowvar=False)
        sample_eig = np.linalg.eigvalsh(corr)
        denoised = mp_denoise(X)
        d_corr = denoised / np.outer(std, std)
        d_eig = np.linalg.eigvalsh(d_corr)
        assert d_eig.std() < sample_eig.std()

    def test_requires_more_periods_than_assets(self):
        with pytest.raises(ValueError, match="more periods"):
            mp_denoise(np.zeros((3, 5)))


# --- covariance dispatcher ---------------------------------------------------


class TestCovDispatcher:
    def test_sample_matches_numpy(self, returns):
        cov = estimate_covariance(returns, "sample")
        np.testing.assert_allclose(cov, np.cov(returns, rowvar=False, ddof=1), atol=1e-15)

    def test_dispatch_routes(self, returns):
        np.testing.assert_allclose(
            estimate_covariance(returns, "ewma", lam=0.9),
            ewma_covariance(returns, lam=0.9),
            atol=1e-15,
        )
        np.testing.assert_allclose(
            estimate_covariance(returns, "oas"), oas_shrinkage(returns)[0], atol=1e-15
        )
        np.testing.assert_allclose(
            estimate_covariance(returns, "mp"), mp_denoise(returns), atol=1e-15
        )

    def test_unknown(self, returns):
        with pytest.raises(ValueError, match="Unknown covariance estimator"):
            estimate_covariance(returns, "nope")


# --- mean estimators ---------------------------------------------------------


class TestMeanEstimators:
    def test_ewma_recency(self):
        """EWMA mean is pulled toward a recent positive drift more than the sample mean."""
        early = np.full((200, 1), -0.001)
        late = np.full((50, 1), 0.01)  # recent positive regime
        series = np.vstack([early, late])
        ewma = ewma_mean(series, lam=0.90)[0]
        sample = float(series.mean())
        assert ewma > sample

    def test_james_stein_shrinks_toward_grand_mean(self):
        rng = np.random.default_rng(21)
        X = rng.normal([0.002, -0.001, 0.003, 0.0005], 0.01, size=(250, 4))
        sample = X.mean(axis=0)
        grand = float(sample.mean())
        js = james_stein_mean(X)
        # each shrunk component lies between its sample mean and the grand mean
        for s, j in zip(sample, js, strict=True):
            lo, hi = sorted((s, grand))
            assert lo - 1e-12 <= j <= hi + 1e-12
        # and the dispersion around the grand mean shrinks
        assert np.sum((js - grand) ** 2) <= np.sum((sample - grand) ** 2) + 1e-12

    def test_james_stein_small_n_returns_sample(self):
        X = np.array([[0.01, -0.01], [0.02, 0.0], [-0.005, 0.015]])
        np.testing.assert_allclose(james_stein_mean(X), X.mean(axis=0), atol=1e-15)

    def test_mean_dispatcher(self):
        rng = np.random.default_rng(5)
        X = rng.normal(0.001, 0.01, size=(100, 4))
        np.testing.assert_allclose(estimate_mean(X, "sample"), X.mean(axis=0), atol=1e-15)
        np.testing.assert_allclose(
            estimate_mean(X, "ewma", lam=0.9), ewma_mean(X, lam=0.9), atol=1e-15
        )
        np.testing.assert_allclose(estimate_mean(X, "james_stein"), james_stein_mean(X), atol=1e-15)

    def test_unknown_mean_estimator(self):
        with pytest.raises(ValueError, match="Unknown mean estimator"):
            estimate_mean(np.zeros((10, 3)), "bogus")
