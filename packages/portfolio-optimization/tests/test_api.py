"""Tests for the thin FastAPI demo wrapper (api/app.py).

Uses FastAPI's TestClient (no live server, no network -- the demo optimizes a
POSTed returns matrix). Exercises the newly-wired HRP objective on ``/optimize``
and the dedicated Black-Litterman endpoint with/without views.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.app import app  # noqa: E402

client = TestClient(app)

TICKERS = ["AAPL", "MSFT", "GOOG", "JPM"]


def _returns_matrix(seed: int = 0, periods: int = 400) -> list[list[float]]:
    """Deterministic correlated daily-returns matrix shaped (T, 4)."""
    rng = np.random.default_rng(seed)
    mu = np.array([0.0007, 0.0006, 0.0005, 0.0004])
    vol = np.array([0.018, 0.016, 0.017, 0.013])
    corr = np.array(
        [
            [1.0, 0.6, 0.5, 0.3],
            [0.6, 1.0, 0.55, 0.35],
            [0.5, 0.55, 1.0, 0.3],
            [0.3, 0.35, 0.3, 1.0],
        ]
    )
    cov = np.outer(vol, vol) * corr
    return rng.multivariate_normal(mu, cov, size=periods).tolist()


# --- /objectives ---


def test_objectives_lists_hrp_and_bl():
    resp = client.get("/objectives")
    assert resp.status_code == 200
    body = resp.json()
    assert "hrp" in body["objectives"]
    assert "black_litterman" in body["other"]


# --- /optimize with hrp ---


def test_optimize_hrp_returns_valid_weights():
    resp = client.post(
        "/optimize",
        json={"tickers": TICKERS, "returns": _returns_matrix(), "objective": "hrp"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["objective"] == "hrp"
    weights = body["weights"]
    assert set(weights) == set(TICKERS)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert all(w >= -1e-9 for w in weights.values())  # long-only
    assert body["volatility"] > 0


def test_optimize_unknown_objective_422():
    resp = client.post(
        "/optimize",
        json={"tickers": TICKERS, "returns": _returns_matrix(), "objective": "nope"},
    )
    assert resp.status_code == 422


# --- /optimize/black-litterman ---


def test_bl_no_views_runs_equilibrium_prior():
    resp = client.post(
        "/optimize/black-litterman",
        json={"tickers": TICKERS, "returns": _returns_matrix(), "views": []},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["objective"] == "black_litterman"
    assert sum(body["weights"].values()) == pytest.approx(1.0, abs=1e-6)
    # With no views the posterior collapses to the prior.
    for t in TICKERS:
        assert body["posterior_returns"][t] == pytest.approx(body["prior_returns"][t], abs=1e-9)


def test_bl_bullish_view_tilts_weights_up():
    """A bullish absolute view on AAPL raises its posterior return and weight."""
    matrix = _returns_matrix()
    base = client.post(
        "/optimize/black-litterman",
        json={"tickers": TICKERS, "returns": matrix, "views": []},
    ).json()
    tilted = client.post(
        "/optimize/black-litterman",
        json={
            "tickers": TICKERS,
            "returns": matrix,
            "views": [{"assets": {"AAPL": 1.0}, "q": base["prior_returns"]["AAPL"] + 0.10}],
        },
    ).json()
    # Posterior return on AAPL moved up toward the view.
    assert tilted["posterior_returns"]["AAPL"] > base["posterior_returns"]["AAPL"]
    # And the allocation tilts toward AAPL.
    assert tilted["weights"]["AAPL"] > base["weights"]["AAPL"]
    assert sum(tilted["weights"].values()) == pytest.approx(1.0, abs=1e-6)


def test_bl_confidence_strengthens_tilt():
    """A higher-confidence view tilts the posterior further toward q."""
    matrix = _returns_matrix()
    base = client.post(
        "/optimize/black-litterman",
        json={"tickers": TICKERS, "returns": matrix, "views": []},
    ).json()
    q = base["prior_returns"]["AAPL"] + 0.10

    low = client.post(
        "/optimize/black-litterman",
        json={
            "tickers": TICKERS,
            "returns": matrix,
            "views": [{"assets": {"AAPL": 1.0}, "q": q, "confidence": 0.1}],
        },
    ).json()
    high = client.post(
        "/optimize/black-litterman",
        json={
            "tickers": TICKERS,
            "returns": matrix,
            "views": [{"assets": {"AAPL": 1.0}, "q": q, "confidence": 0.95}],
        },
    ).json()
    # More confidence -> posterior closer to the view target q.
    assert abs(high["posterior_returns"]["AAPL"] - q) < abs(low["posterior_returns"]["AAPL"] - q)


def test_bl_relative_view_supported():
    """A relative view (AAPL outperforms JPM) is accepted and shifts the spread."""
    matrix = _returns_matrix()
    resp = client.post(
        "/optimize/black-litterman",
        json={
            "tickers": TICKERS,
            "returns": matrix,
            "views": [{"assets": {"AAPL": 1.0, "JPM": -1.0}, "q": 0.10}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sum(body["weights"].values()) == pytest.approx(1.0, abs=1e-6)


def test_bl_market_weights_accepted():
    resp = client.post(
        "/optimize/black-litterman",
        json={
            "tickers": TICKERS,
            "returns": _returns_matrix(),
            "views": [],
            "market_weights": {"AAPL": 0.4, "MSFT": 0.3, "GOOG": 0.2, "JPM": 0.1},
        },
    )
    assert resp.status_code == 200


def test_bl_unknown_view_ticker_422():
    resp = client.post(
        "/optimize/black-litterman",
        json={
            "tickers": TICKERS,
            "returns": _returns_matrix(),
            "views": [{"assets": {"TSLA": 1.0}, "q": 0.1}],
        },
    )
    assert resp.status_code == 422


def test_bl_unknown_market_weight_ticker_422():
    resp = client.post(
        "/optimize/black-litterman",
        json={
            "tickers": TICKERS,
            "returns": _returns_matrix(),
            "views": [],
            "market_weights": {"TSLA": 1.0},
        },
    )
    assert resp.status_code == 422


def test_bl_bad_returns_shape_422():
    resp = client.post(
        "/optimize/black-litterman",
        json={"tickers": TICKERS, "returns": [[0.1, 0.2]], "views": []},
    )
    assert resp.status_code == 422
