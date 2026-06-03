"""Headless smoke tests for the Streamlit live-monitor (``monitor.py``).

The monitor is a read-only view of the daemon's store. These tests prove its
data path and the Streamlit app build WITHOUT a display, WITHOUT network, and on
an EMPTY/tmp store -- i.e. the fresh-clone "sample data" path that always has to
render. They mirror the AppTest style of options-pricing's ``test_iv_surface_ui``.

``monitor.py`` lives at the package root (outside ``[tool.coverage.run]
source``), so this exercises it without diluting the ``src`` coverage gate.
"""

import os

import matplotlib

matplotlib.use("Agg")

import pandas as pd  # noqa: E402

import monitor  # noqa: E402

# --- Synthetic-sample data path -------------------------------------------


def test_synthesize_sample_is_deterministic():
    a_trades, a_bars = monitor.synthesize_sample("btcusdt")
    b_trades, b_bars = monitor.synthesize_sample("btcusdt")
    pd.testing.assert_frame_equal(a_trades, b_trades)
    pd.testing.assert_frame_equal(a_bars, b_bars)


def test_synthesize_sample_schema_and_rollup():
    trades, bars = monitor.synthesize_sample("ethusdt", n_minutes=30)
    assert list(trades.columns) == monitor._TRADE_COLUMNS
    assert list(bars.columns) == monitor._OHLCV_COLUMNS
    assert not trades.empty and not bars.empty
    # Bars are sane OHLC: low <= open/close <= high.
    assert (bars["low"] <= bars["open"]).all()
    assert (bars["low"] <= bars["close"]).all()
    assert (bars["high"] >= bars["open"]).all()
    assert (bars["high"] >= bars["close"]).all()
    assert (bars["trade_count"] > 0).all()
    # 30 minutes of trades -> ~30 one-minute bars.
    assert 20 <= len(bars) <= 31


def test_compute_metrics_empty_is_safe():
    m = monitor.compute_metrics(monitor._trades_frame([]))
    assert m == {"last_price": None, "spread_proxy": None, "trade_count": 0, "volume": None}


def test_compute_metrics_on_sample():
    trades, _ = monitor.synthesize_sample("btcusdt", n_minutes=10)
    m = monitor.compute_metrics(trades)
    assert m["trade_count"] == len(trades)
    assert m["last_price"] is not None and m["last_price"] > 0
    assert m["spread_proxy"] is not None and m["spread_proxy"] >= 0
    assert m["volume"] is not None and m["volume"] > 0


def test_build_price_chart_from_bars_and_tape():
    trades, bars = monitor.synthesize_sample("btcusdt", n_minutes=10)
    fig_bars = monitor.build_price_chart(bars, trades, "btcusdt")
    assert fig_bars.data  # has traces
    # Falls back to the raw tape when there are no bars yet.
    fig_tape = monitor.build_price_chart(monitor._ohlcv_frame([]), trades, "btcusdt")
    assert fig_tape.data


def test_fmt_helpers():
    assert monitor._fmt_price(None) == "--"
    assert monitor._fmt_price(1234.5) == "$1,234.50"
    assert monitor._fmt_qty(None) == "--"
    assert monitor._fmt_qty(0.12345) == "0.1235"


# --- Store read path on an empty DuckDB file ------------------------------


def test_load_store_data_empty_returns_empty(tmp_path):
    """An empty DuckDB store yields empty frames (the sample fallback then fires)."""
    from src.config import Config

    db = tmp_path / "empty.duckdb"
    config = Config(storage_backend="duckdb", duckdb_path=str(db))
    trades_df, ohlcv_df = monitor.load_store_data(config, "btcusdt", _start(), _end())
    assert trades_df.empty and ohlcv_df.empty


# --- Full AppTest build (offline / empty store -> sample path) ------------


def test_app_builds_on_empty_store(tmp_path):
    """The Streamlit app imports and runs without error on an empty store.

    Points the DuckDB backend at a fresh tmp path so the read path is exercised,
    finds nothing, and the deterministic sample renders -- no display, no network.
    """
    from streamlit.testing.v1 import AppTest

    db = tmp_path / "fresh.duckdb"
    prev = {
        "STORAGE_BACKEND": os.environ.get("STORAGE_BACKEND"),
        "DUCKDB_PATH": os.environ.get("DUCKDB_PATH"),
        "REDIS_URL": os.environ.get("REDIS_URL"),
    }
    os.environ["STORAGE_BACKEND"] = "duckdb"
    os.environ["DUCKDB_PATH"] = str(db)
    try:
        monitor_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "monitor.py")
        at = AppTest.from_file(monitor_path, default_timeout=60)
        at.run()
        assert not at.exception
        # Sample-data banner is shown (empty store) and the 4 metric cards render.
        assert at.info or at.warning
        assert len(at.metric) == 4
    finally:
        for key, value in prev.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# --- helpers --------------------------------------------------------------


def _start():
    from datetime import UTC, datetime, timedelta

    return datetime.now(UTC) - timedelta(days=1)


def _end():
    from datetime import UTC, datetime

    return datetime.now(UTC)
