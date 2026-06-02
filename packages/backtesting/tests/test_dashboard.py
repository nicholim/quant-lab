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

from portfolio_optimization_engine.analysis import _OBJECTIVE_METHODS

import dashboard
from src.portfolio import Portfolio
from src.strategy import OptimizationRebalanceStrategy


def test_module_imports_and_app_builds():
    app = dashboard.build_app()
    assert app is not None


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
    # frontier_figure falls back to a default marker, but every objective key
    # should still have an explicit style so the legend is consistent.
    for key in dashboard.OBJECTIVE_TO_KEY.values():
        assert key in dashboard._MARKER


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
