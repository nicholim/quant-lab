"""Tests for the resilient market-data download layer.

All network access is mocked (monkeypatch ``yfinance.download``) — no live
network is ever hit. Covers: success, retry-then-success on transient errors,
exhausting retries, non-transient errors (no retry), empty-frame handling,
MultiIndex flattening, and the bundled offline fixture path (arg + env flag).
"""

import os
import sys
import types

import pandas as pd
import pytest

from src import market_data
from src.market_data import (
    MarketDataError,
    _is_transient,
    _load_sample_ohlcv,
    download_ohlcv,
)


def _fake_frame(multiindex: bool = False) -> pd.DataFrame:
    idx = pd.date_range("2021-06-01", periods=3, freq="B", name="Date")
    df = pd.DataFrame(
        {
            "Open": [1.0, 2.0, 3.0],
            "High": [1.5, 2.5, 3.5],
            "Low": [0.5, 1.5, 2.5],
            "Close": [1.2, 2.2, 3.2],
            "Volume": [100, 200, 300],
        },
        index=idx,
    )
    if multiindex:
        df.columns = pd.MultiIndex.from_product([df.columns, ["AAPL"]])
    return df


def _install_fake_yfinance(monkeypatch, download_fn):
    """Insert a fake ``yfinance`` module whose ``download`` is ``download_fn``."""
    fake = types.ModuleType("yfinance")
    fake.download = download_fn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yfinance", fake)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make backoff instant so retry tests do not actually wait."""
    monkeypatch.setattr(market_data.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _clean_offline_env(monkeypatch):
    monkeypatch.delenv("BACKTESTING_OFFLINE", raising=False)


# --- transient detection ---------------------------------------------------


@pytest.mark.parametrize(
    "msg",
    ["Connection timed out", "rate limit exceeded", "HTTP 429 Too Many Requests", "503 Service"],
)
def test_transient_markers(msg):
    assert _is_transient(Exception(msg)) is True


def test_non_transient_marker():
    assert _is_transient(ValueError("no such ticker ZZZZ")) is False


# --- success / flatten -----------------------------------------------------


def test_download_success(monkeypatch):
    _install_fake_yfinance(monkeypatch, lambda *a, **k: _fake_frame())
    df = download_ohlcv("AAPL", "2021-06-01", "2021-06-30")
    assert list(df.columns) == market_data.OHLCV_COLUMNS
    assert len(df) == 3


def test_download_flattens_multiindex(monkeypatch):
    _install_fake_yfinance(monkeypatch, lambda *a, **k: _fake_frame(multiindex=True))
    df = download_ohlcv("AAPL", "2021-06-01", "2021-06-30")
    assert not isinstance(df.columns, pd.MultiIndex)
    assert list(df.columns) == market_data.OHLCV_COLUMNS


# --- retry behaviour -------------------------------------------------------


def test_retry_then_success_on_transient(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset by peer")
        return _fake_frame()

    _install_fake_yfinance(monkeypatch, flaky)
    df = download_ohlcv("AAPL", "2021-06-01", "2021-06-30", max_attempts=3)
    assert calls["n"] == 3
    assert len(df) == 3


def test_transient_exhausts_attempts(monkeypatch):
    calls = {"n": 0}

    def always_fail(*a, **k):
        calls["n"] += 1
        raise TimeoutError("request timed out")

    _install_fake_yfinance(monkeypatch, always_fail)
    with pytest.raises(MarketDataError) as exc:
        download_ohlcv("AAPL", "2021-06-01", "2021-06-30", max_attempts=3)
    assert calls["n"] == 3
    assert "after 3 attempt" in str(exc.value)


def test_non_transient_does_not_retry(monkeypatch):
    calls = {"n": 0}

    def bad_symbol(*a, **k):
        calls["n"] += 1
        raise ValueError("delisted: no data for ZZZZ")

    _install_fake_yfinance(monkeypatch, bad_symbol)
    with pytest.raises(MarketDataError):
        download_ohlcv("ZZZZ", "2021-06-01", "2021-06-30", max_attempts=3)
    assert calls["n"] == 1  # gave up immediately


def test_empty_frame_retries_then_raises(monkeypatch):
    calls = {"n": 0}

    def empty(*a, **k):
        calls["n"] += 1
        return pd.DataFrame()

    _install_fake_yfinance(monkeypatch, empty)
    with pytest.raises(MarketDataError) as exc:
        download_ohlcv("AAPL", "2021-06-01", "2021-06-30", max_attempts=2)
    assert calls["n"] == 2
    assert "No data returned" in str(exc.value)


def test_empty_then_success(monkeypatch):
    calls = {"n": 0}

    def empty_then_ok(*a, **k):
        calls["n"] += 1
        return pd.DataFrame() if calls["n"] == 1 else _fake_frame()

    _install_fake_yfinance(monkeypatch, empty_then_ok)
    df = download_ohlcv("AAPL", "2021-06-01", "2021-06-30", max_attempts=2)
    assert len(df) == 3


# --- offline fixture -------------------------------------------------------


def test_offline_arg_serves_fixture(monkeypatch):
    # Any call to yfinance.download would blow up; offline must not reach it.
    def boom(*a, **k):
        raise AssertionError("network should not be hit in offline mode")

    _install_fake_yfinance(monkeypatch, boom)
    df = download_ohlcv("AAPL", "2021-06-01", "2021-12-31", offline=True)
    assert list(df.columns) == market_data.OHLCV_COLUMNS
    assert len(df) > 0
    assert df.index.name == "Date"


def test_offline_env_flag(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network should not be hit when BACKTESTING_OFFLINE=1")

    _install_fake_yfinance(monkeypatch, boom)
    monkeypatch.setenv("BACKTESTING_OFFLINE", "1")
    df = download_ohlcv("MSFT", "2021-06-01", "2021-12-31")
    assert len(df) > 0


def test_offline_fixture_date_slice():
    full = _load_sample_ohlcv(None, None)
    sliced = _load_sample_ohlcv("2021-07-01", "2021-09-01")
    assert len(sliced) < len(full)
    assert sliced.index.min() >= pd.Timestamp("2021-07-01")
    assert sliced.index.max() <= pd.Timestamp("2021-09-01")


def test_offline_fixture_empty_window_raises():
    with pytest.raises(MarketDataError):
        _load_sample_ohlcv("2099-01-01", "2099-02-01")


@pytest.mark.parametrize(
    "val,expected", [("0", False), ("false", False), ("1", True), ("yes", True)]
)
def test_offline_env_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("BACKTESTING_OFFLINE", val)
    assert market_data._offline_enabled(False) is expected


# --- integration through the call sites ------------------------------------


def test_datastore_offline_caches_fixture(tmp_path, monkeypatch):
    from src.datastore import DataStore

    def boom(*a, **k):
        raise AssertionError("offline DataStore must not hit the network")

    _install_fake_yfinance(monkeypatch, boom)
    db = str(tmp_path / "t.duckdb")
    store = DataStore(db)
    try:
        df = store.fetch_ohlcv("AAPL", "2021-06-01", "2021-12-31", offline=True)
        assert len(df) > 0
        # Second call should hit the DuckDB cache (still no network).
        assert store.has_cached_data("AAPL", "2021-06-01", "2021-12-31")
        cached = store.fetch_ohlcv("AAPL", "2021-06-01", "2021-12-31")
        assert list(cached.columns) == ["Open", "High", "Low", "Close", "Volume"]
    finally:
        store.close()


def test_data_handler_offline_no_store(monkeypatch):
    from src.data_handler import YFinanceDataHandler

    def boom(*a, **k):
        raise AssertionError("offline handler must not hit the network")

    _install_fake_yfinance(monkeypatch, boom)
    handler = YFinanceDataHandler(["AAPL"], "2021-06-01", "2021-12-31", offline=True)
    handler.fetch()
    bars = handler.get_latest_bars("AAPL", 5)
    assert isinstance(bars, pd.DataFrame)
    assert handler._total_bars > 0


def test_cleanup_offline_env():
    # Sanity: the autouse fixture removed any ambient flag.
    assert os.environ.get("BACKTESTING_OFFLINE") in (None, "")
