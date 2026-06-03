"""Headless smoke tests for the Streamlit front-end (``streamlit_app.py``).

These exercise the importable data/optimizer helpers the UI depends on against
the bundled OFFLINE fixture (no network, ``Agg`` backend), then verify the app
itself imports and builds via Streamlit's ``AppTest`` harness and produces real
weights. The app file lives OUTSIDE ``[tool.coverage.run] source`` (scoped to
``portfolio_optimization_engine``), so it is AppTest-exercised without diluting
the package coverage gate -- mirroring how ``api/`` and ``main.py`` are handled.
"""

import os

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import streamlit_app as app  # noqa: E402
from portfolio_optimization_engine.analysis import _OBJECTIVE_METHODS  # noqa: E402

APP_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "streamlit_app.py")


# --- data-path helpers (offline) --------------------------------------------


def test_sample_prices_and_returns_offline():
    prices = app.sample_prices()
    assert prices.shape[1] >= 2 and prices.shape[0] > 2
    rets = app.returns_from_prices(prices)
    assert rets.shape[0] == prices.shape[0] - 1
    assert not rets.isna().any().any()


def test_objectives_mirror_run_analysis_registry():
    """The six zero-arg objectives use the exact methods run_analysis calls."""
    zero_arg = {o.method for o in app.OBJECTIVES.values() if o.target is None}
    assert zero_arg == set(_OBJECTIVE_METHODS.values())


def test_run_objective_produces_valid_weights():
    rets = app.returns_from_prices(app.sample_prices())
    opt = app.build_optimizer(rets, 0.02)
    for key, spec in app.OBJECTIVES.items():
        target = 0.20 if spec.target == "vol" else (0.10 if spec.target == "return" else None)
        res = app.run_objective(opt, key, target_value=target)
        w = np.asarray(res.weights)
        assert w.shape == (rets.shape[1],)
        assert np.isclose(w.sum(), 1.0, atol=1e-6)
        # Long-only objectives stay non-negative.
        assert (w >= -1e-6).all()


def test_parse_returns_csv_with_date_column():
    idx = pd.date_range("2023-01-01", periods=5).strftime("%Y-%m-%d")
    raw = pd.DataFrame(
        {"Date": idx, "A": np.linspace(0.01, 0.02, 5), "B": np.linspace(-0.01, 0.01, 5)}
    )
    parsed = app.parse_returns_csv(raw)
    assert list(parsed.columns) == ["A", "B"]
    assert parsed.shape == (5, 2)


def test_parse_returns_csv_rejects_too_few_columns():
    raw = pd.DataFrame({"A": [0.01, 0.02, 0.03]})
    try:
        app.parse_returns_csv(raw)
        raise AssertionError("expected ValueError for <2 asset columns")
    except ValueError:
        pass


def test_all_objectives_table_offline():
    rets = app.returns_from_prices(app.sample_prices())
    table = app.all_objectives_table(rets, 0.02)
    # All zero-arg objectives compared; target-based ones excluded.
    expected = sum(1 for o in app.OBJECTIVES.values() if o.target is None)
    assert table.shape[0] == expected
    assert {"Return", "Volatility", "Sharpe", "CVaR", "Max drawdown"} <= set(table.columns)


def test_figures_build():
    rets = app.returns_from_prices(app.sample_prices())
    opt = app.build_optimizer(rets, 0.02)
    res = opt.optimize_sharpe()
    tickers = list(rets.columns)
    assert app.weights_figure(tickers, res.weights) is not None
    solved = opt.solved_efficient_frontier(n_points=10)
    cloud = opt.efficient_frontier(num_portfolios=200, random_state=1)
    assert app.frontier_figure(solved, cloud, res, "Max Sharpe") is not None
    prior = opt.black_litterman_returns()
    p = np.zeros((1, len(tickers)))
    p[0, 0] = 1.0
    posterior = opt.black_litterman_returns(p, np.array([0.2]))
    assert app.bl_shift_figure(tickers, prior, posterior) is not None


# --- full app build via AppTest (offline) -----------------------------------


def test_app_builds_offline_and_produces_weights():
    """The app imports and runs to completion offline, rendering weights."""
    from streamlit.testing.v1 import AppTest

    os.environ["PORTFOLIO_OFFLINE"] = "1"
    try:
        at = AppTest.from_file(APP_PATH, default_timeout=60)
        at.run()
        assert not at.exception
        # The four result tabs are present.
        assert len(at.tabs) == 4
        # A metric for "Expected return" rendered -> the optimization ran.
        labels = [m.label for m in at.metric]
        assert "Expected return" in labels
    finally:
        os.environ.pop("PORTFOLIO_OFFLINE", None)
