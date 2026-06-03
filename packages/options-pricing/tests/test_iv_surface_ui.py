"""Headless smoke tests for the Streamlit IV-surface data path.

The Streamlit UI itself is hard to unit-test, so these tests exercise the exact
underlying functions the "IV surface" tab calls -- assembling multi-expiry
chains from the OFFLINE fixture, solving OUR implied vol via the vectorized
solver, and rendering the real solved IV surface -- proving the data path the
tab depends on works end-to-end and renders without a display (``Agg`` backend,
no network). Mirrors the style of ``test_greeks_visualizer.py``.

It also verifies ``app.py`` parses and builds via the Streamlit ``AppTest``
harness with the offline flag set so it never touches the network.
"""

import os

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from src.greeks_visualizer import (  # noqa: E402
    plot_solved_iv_surface,
    solve_iv_surface,
)
from src.market_data import (  # noqa: E402
    _years_to_expiry,
    get_option_chain,
    get_spot,
)


def _offline_multi_expiry(option_type: str = "call"):
    """Build (chains_by_expiry, expiry_years, spot) from the offline fixture.

    Replicates exactly what the IV-surface tab does in offline mode: vary T over
    a spread of synthetic future dates while reusing the bundled sample chain
    (offline ``get_option_chain`` ignores the requested expiry).
    """
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc)
    expiries = [(base + timedelta(days=d)).strftime("%Y-%m-%d") for d in (20, 45, 90, 160)]
    spot = get_spot("AAPL", offline=True)
    chains = {e: get_option_chain("AAPL", e, option_type, offline=True) for e in expiries}
    years = {e: _years_to_expiry(e) for e in expiries}
    return chains, years, spot


def _close_all():
    plt.close("all")


def test_offline_multi_expiry_has_distinct_T():
    chains, years, spot = _offline_multi_expiry()
    assert len(chains) == 4
    assert spot > 0
    # Distinct, increasing time-to-expiry across the synthesized expiries.
    ts = [years[e] for e in sorted(years)]
    assert all(t > 0 for t in ts)
    assert len(set(ts)) == len(ts)


def test_solve_iv_surface_offline_path():
    chains, years, spot = _offline_multi_expiry("call")
    surface = solve_iv_surface(chains, spot, years, option_type="call")
    assert not surface.empty
    assert set(surface.columns) == {"expiry", "T", "strike", "iv"}
    # Multiple expiries solved; IVs are sane fractions.
    assert surface["expiry"].nunique() >= 2
    assert (surface["iv"] > 0).all()
    assert (surface["iv"] < 5).all()


def test_plot_solved_iv_surface_offline_saves(tmp_path):
    chains, years, spot = _offline_multi_expiry("call")
    out = tmp_path / "iv_surface.png"
    surface = plot_solved_iv_surface(chains, spot, years, option_type="call", save_path=str(out))
    assert out.exists() and out.stat().st_size > 0
    assert not surface.empty
    _close_all()


def test_plot_solved_iv_surface_put_renders_without_save():
    chains, years, spot = _offline_multi_expiry("put")
    surface = plot_solved_iv_surface(chains, spot, years, option_type="put")
    assert not surface.empty
    _close_all()


def _run_app_offline():
    """Build + run app.py headlessly with the offline flag set (no network)."""
    from streamlit.testing.v1 import AppTest

    os.environ["OPTIONS_PRICING_OFFLINE"] = "1"
    try:
        app_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
        at = AppTest.from_file(app_path, default_timeout=60)
        at.run()
        return at
    finally:
        os.environ.pop("OPTIONS_PRICING_OFFLINE", None)


def test_app_builds_offline():
    """The Streamlit app imports and runs without error (offline, no network)."""
    at = _run_app_offline()
    assert not at.exception
    # All three tabs are present (Calculator, Live market, IV surface).
    assert len(at.tabs) == 3


def test_app_has_global_sidebar_inputs():
    """The polished sidebar groups the core global inputs the calculator needs."""
    at = _run_app_offline()
    assert not at.exception
    # Number inputs S, K, T, r, sigma all live in the sidebar.
    labels = {ni.label for ni in at.sidebar.number_input}
    assert "Spot price (S)" in labels
    assert "Strike price (K)" in labels
    assert "Time to expiry (years)" in labels
    # The offline-sample toggle and an option-type selectbox exist.
    assert any("Offline" in t.label for t in at.sidebar.toggle)
    assert any(sb.label == "Option type" for sb in at.sidebar.selectbox)


def test_app_shows_offline_banner_and_metrics():
    """Offline mode surfaces the banner and renders price/Greek metric cards."""
    at = _run_app_offline()
    assert not at.exception
    # Offline banner is shown via st.info.
    assert any("Offline sample mode is ON" in str(el.value) for el in at.info)
    # Calculator renders metric cards (Black-Scholes + the five Greeks).
    metric_labels = {m.label for m in at.metric}
    assert "Black-Scholes" in metric_labels
    assert {"Delta", "Gamma", "Theta", "Vega", "Rho"} <= metric_labels


def test_app_no_raw_exceptions_in_widgets():
    """No element rendered a traceback/error state in the default offline build."""
    at = _run_app_offline()
    assert not at.exception
    assert len(at.error) == 0
