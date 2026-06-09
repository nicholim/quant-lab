"""Tests for the Dash dashboard's importable helpers (no server launched).

Dash callbacks are awkward to unit-test, so we cover the testable seams:

* The module imports and ``OBJECTIVE_TO_KEY`` includes ``hrp`` (and every value
  is a valid ``run_analysis`` objective, so the shared analysis path keeps
  resolving ``analysis["results"][key]``).
* ``backtest_optimized`` threads its ``allow_short`` flag into the
  ``Portfolio(allow_short=...)`` it builds — default off (long-only, unchanged),
  on when requested — and routes the chosen objective (incl. ``hrp``) into
  ``OptimizationRebalanceStrategy``.

The optimization/analysis path is exercised against the optimizer package's own
``_OBJECTIVE_METHODS`` so adding ``hrp`` to the dashboard map can't silently
drift from what ``run_analysis`` actually computes. No live network.
"""

import types

from portfolio_optimization_engine.analysis import _OBJECTIVE_METHODS

import dashboard
from src.portfolio import Portfolio
from src.strategy import OptimizationRebalanceStrategy


def _walk(component):
    """Yield every Dash component in the tree (depth-first)."""
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if not isinstance(children, list | tuple):
        children = [children]
    for child in children:
        if hasattr(child, "children") or hasattr(child, "_prop_names"):
            yield from _walk(child)


def _ids(app):
    return {getattr(c, "id", None) for c in _walk(app.layout)}


def _classnames(node):
    # Accept an app (has .layout) or a bare component (has .children).
    root = node.layout if hasattr(node, "layout") else node
    names: set[str] = set()
    for c in _walk(root):
        cls = getattr(c, "className", None)
        if isinstance(cls, str):
            names.update(cls.split())
    return names


def test_module_imports_and_app_builds():
    app = dashboard.build_app()
    assert app is not None


def test_shared_plotly_template_registered():
    import plotly.io as pio

    assert "quantlab" in pio.templates


def test_figures_use_shared_template():
    # Each figure builder applies the shared template for visual consistency.
    import pandas as pd

    eq = pd.DataFrame(
        {"equity": [100000.0, 101000.0, 99500.0, 103000.0]},
        index=pd.date_range("2020-01-01", periods=4, freq="D"),
    )
    analytics = types.SimpleNamespace(equity=eq)
    for fig in (dashboard.equity_figure(analytics), dashboard.drawdown_figure(analytics)):
        # template comes through as the registered object, not the string name
        assert fig.layout.template is not None
        assert fig.layout.xaxis.title.text  # axis labelled


def test_layout_has_header_and_control_groups():
    app = dashboard.build_app()
    classes = _classnames(app)
    assert "app-header" in classes  # title bar
    assert "sidebar" in classes  # control panel rail
    assert "control-group" in classes  # grouped inputs (universe/strategy/rebalance)
    assert "btn-run" in classes


def test_layout_has_alert_and_headline_regions():
    app = dashboard.build_app()
    ids = _ids(app)
    # New UX regions for error/empty states and the headline KPI cards.
    assert "alert" in ids
    assert "headline" in ids
    # Original wiring preserved.
    for required in ("tickers", "objective", "allow-short", "run", "frontier", "equity"):
        assert required in ids


def test_objective_options_cover_every_objective():
    options = dashboard.objective_options()
    values = {o["value"] for o in options}
    assert values == set(dashboard.OBJECTIVE_TO_KEY)
    # hrp is surfaced as an acronym, the rest title-cased.
    labels = {o["value"]: o["label"] for o in options}
    assert labels["hrp"] == "HRP"
    assert labels["sharpe"] == "Sharpe"


def test_headline_cards_render_four_kpis_with_sign_tone():
    analytics = types.SimpleNamespace(
        total_return=lambda: 0.2534,
        sharpe_ratio=lambda: 1.42,
        sortino_ratio=lambda: 1.9,
        max_drawdown=lambda: -0.1812,
    )
    cards = dashboard.headline_cards(analytics)
    leaves = [c for c in _walk(cards) if getattr(c, "className", "") == "metric-card"]
    assert len(leaves) == 4
    classes = _classnames(cards)
    # Positive total return is toned green, drawdown red — number formatting applied.
    assert "metric-card__value--pos" in classes
    assert "metric-card__value--neg" in classes


def test_objective_map_includes_hrp():
    assert dashboard.OBJECTIVE_TO_KEY["hrp"] == "hrp"


def test_objective_map_values_are_valid_analysis_objectives():
    # Every dashboard objective must be a key run_analysis computes, or the
    # shared analysis path's ``analysis["results"][key]`` lookup would KeyError.
    for key in dashboard.OBJECTIVE_TO_KEY.values():
        assert key in _OBJECTIVE_METHODS


def test_default_objectives_still_present():
    # The hrp addition is additive: prior objectives are untouched.
    for obj in ("sharpe", "min_vol", "risk_parity", "sortino", "min_cvar"):
        assert obj in dashboard.OBJECTIVE_TO_KEY


def test_marker_covers_every_objective():
    # frontier_figure falls back to a default symbol, but every objective key
    # should still have an explicit marker symbol so the legend is consistent.
    for key in dashboard.OBJECTIVE_TO_KEY.values():
        assert key in dashboard._MARKER_SYMBOL


class _CapturingBacktest:
    """Stand-in for ``Backtest`` that records its constructor args instead of
    running an event loop (no network, no data fetch)."""

    last = None

    def __init__(self, data, strategy, portfolio, execution, **kwargs):
        _CapturingBacktest.last = {
            "data": data,
            "strategy": strategy,
            "portfolio": portfolio,
            "execution": execution,
            "kwargs": kwargs,
        }

    def run(self):
        return "analytics-sentinel"


def _patch_backtest(monkeypatch):
    # Avoid any network: YFinanceDataHandler is only constructed, never fetched
    # (the capturing Backtest doesn't call .run()'s data fetch).
    monkeypatch.setattr(dashboard, "Backtest", _CapturingBacktest)


def test_backtest_optimized_default_long_only(monkeypatch):
    _patch_backtest(monkeypatch)
    result = dashboard.backtest_optimized(["AAPL", "MSFT"], "2020-01-01", "2024-01-01", "sharpe")
    assert result == "analytics-sentinel"
    portfolio = _CapturingBacktest.last["portfolio"]
    assert isinstance(portfolio, Portfolio)
    assert portfolio.allow_short is False


def test_backtest_optimized_allow_short_threads_through(monkeypatch):
    _patch_backtest(monkeypatch)
    dashboard.backtest_optimized(
        ["AAPL", "MSFT"], "2020-01-01", "2024-01-01", "sharpe", allow_short=True
    )
    portfolio = _CapturingBacktest.last["portfolio"]
    assert portfolio.allow_short is True


def test_backtest_optimized_hrp_routes_to_strategy(monkeypatch):
    _patch_backtest(monkeypatch)
    dashboard.backtest_optimized(["AAPL", "MSFT"], "2020-01-01", "2024-01-01", "hrp")
    strategy = _CapturingBacktest.last["strategy"]
    assert isinstance(strategy, OptimizationRebalanceStrategy)
    assert strategy.objective == "hrp"


def test_backtest_offline_threads_into_data_handler(monkeypatch):
    """BACKTESTING_OFFLINE=1 makes the backtest half read the bundled fixture."""
    _patch_backtest(monkeypatch)
    monkeypatch.delenv("BACKTESTING_OFFLINE", raising=False)
    dashboard.backtest_optimized(["AAPL", "MSFT"], "2020-01-01", "2024-01-01", "sharpe")
    assert _CapturingBacktest.last["data"]._offline is False  # default: live
    monkeypatch.setenv("BACKTESTING_OFFLINE", "1")
    dashboard.backtest_optimized(["AAPL", "MSFT"], "2020-01-01", "2024-01-01", "sharpe")
    assert _CapturingBacktest.last["data"]._offline is True  # single flag => offline


def test_optimize_threads_offline_into_engine_config(monkeypatch):
    """A SINGLE BACKTESTING_OFFLINE=1 also offlines the optimizer half (it uses the
    portfolio engine's own fetch), so the whole dashboard runs without network."""
    captured = {}

    def _capture(config):
        captured["offline"] = config.offline
        return {"results": {}}

    monkeypatch.setattr(dashboard, "run_analysis", _capture)
    monkeypatch.delenv("BACKTESTING_OFFLINE", raising=False)
    dashboard.optimize(["AAPL", "MSFT"], "2020-01-01", "2024-01-01", 0.02, "sharpe")
    assert captured["offline"] is False
    monkeypatch.setenv("BACKTESTING_OFFLINE", "1")
    dashboard.optimize(["AAPL", "MSFT"], "2020-01-01", "2024-01-01", 0.02, "sharpe")
    assert captured["offline"] is True
