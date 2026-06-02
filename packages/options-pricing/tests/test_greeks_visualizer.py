"""Headless smoke tests for the matplotlib Greeks visualizer.

These verify the plotting helpers run end-to-end and write files without a
display, using the non-interactive ``Agg`` backend. They are correctness
guards against import/shape regressions, not pixel checks.
"""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from src.greeks_visualizer import (  # noqa: E402
    plot_greeks_vs_spot,
    plot_greeks_vs_time,
    plot_payoff_diagram,
    plot_volatility_surface,
)


def _close_all():
    plt.close("all")


def test_plot_greeks_vs_spot_saves(tmp_path):
    out = tmp_path / "greeks_spot.png"
    plot_greeks_vs_spot(K=100, T=0.25, r=0.05, sigma=0.2, save_path=str(out))
    assert out.exists() and out.stat().st_size > 0
    _close_all()


def test_plot_greeks_vs_time_saves(tmp_path):
    out = tmp_path / "greeks_time.png"
    plot_greeks_vs_time(S=100, K=100, r=0.05, sigma=0.2, save_path=str(out))
    assert out.exists() and out.stat().st_size > 0
    _close_all()


def test_plot_volatility_surface_saves(tmp_path):
    out = tmp_path / "surface.png"
    plot_volatility_surface(S=100, save_path=str(out))
    assert out.exists() and out.stat().st_size > 0
    _close_all()


def test_plot_payoff_call_saves(tmp_path):
    out = tmp_path / "payoff_call.png"
    plot_payoff_diagram(K=100, premium=5.0, option_type="call", save_path=str(out))
    assert out.exists() and out.stat().st_size > 0
    _close_all()


def test_plot_payoff_put_saves(tmp_path):
    out = tmp_path / "payoff_put.png"
    plot_payoff_diagram(K=100, premium=4.0, option_type="put", save_path=str(out))
    assert out.exists() and out.stat().st_size > 0
    _close_all()


def test_plots_run_without_save():
    # Exercise the no-save_path branch (plt.show is a no-op under Agg).
    plot_payoff_diagram(K=100, premium=5.0, option_type="call")
    _close_all()
