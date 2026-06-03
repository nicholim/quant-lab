"""Tests for the resilient data layer: retry/backoff, offline fallback, errors.

All network access is monkeypatched (no live yfinance). We patch
``data_cache.yf.download`` to simulate transient failures, success-after-retry,
non-transient failures, and empty results; and exercise the bundled offline
fixture path via both the ``offline=`` argument and ``PORTFOLIO_OFFLINE``.
"""

import pandas as pd
import pytest

from portfolio_optimization_engine import analysis, data_cache
from portfolio_optimization_engine.data_cache import (
    MarketDataError,
    fetch_close_prices,
)


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    """Make retries instant so the suite stays fast."""
    monkeypatch.setattr(data_cache.time, "sleep", lambda _s: None)


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("POE_CACHE_DIR", str(tmp_path))
    return tmp_path


def _close_frame():
    idx = pd.date_range("2023-01-02", periods=4, freq="B")
    return pd.DataFrame({"Close": [10.0, 10.5, 10.2, 10.8]}, index=idx)


# --- retry / backoff --------------------------------------------------------


def test_succeeds_after_transient_failures(monkeypatch):
    calls = {"n": 0}

    def flaky(tickers, start, end, auto_adjust=True):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("connection reset by peer")
        return _close_frame()

    monkeypatch.setattr(data_cache.yf, "download", flaky)
    out = fetch_close_prices("AAPL", "2023-01-01", "2023-02-01")
    assert calls["n"] == 3  # two failures then success
    assert list(out) == [10.0, 10.5, 10.2, 10.8]


def test_rate_limit_is_retried(monkeypatch):
    calls = {"n": 0}

    def flaky(tickers, start, end, auto_adjust=True):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("429 Too Many Requests - rate limit exceeded")
        return _close_frame()

    monkeypatch.setattr(data_cache.yf, "download", flaky)
    fetch_close_prices("AAPL", "2023-01-01", "2023-02-01")
    assert calls["n"] == 2


def test_transient_exhausts_retries_then_raises(monkeypatch):
    calls = {"n": 0}

    def always_timeout(tickers, start, end, auto_adjust=True):
        calls["n"] += 1
        raise TimeoutError("read operation timed out")

    monkeypatch.setattr(data_cache.yf, "download", always_timeout)
    with pytest.raises(MarketDataError, match="after 3 attempt"):
        fetch_close_prices("AAPL", "2023-01-01", "2023-02-01")
    assert calls["n"] == data_cache.MAX_RETRIES


def test_non_transient_fails_fast(monkeypatch):
    calls = {"n": 0}

    def boom(tickers, start, end, auto_adjust=True):
        calls["n"] += 1
        raise ValueError("malformed ticker symbol")

    monkeypatch.setattr(data_cache.yf, "download", boom)
    with pytest.raises(MarketDataError):
        fetch_close_prices("AAPL", "2023-01-01", "2023-02-01")
    assert calls["n"] == 1  # not retried


def test_empty_result_raises(monkeypatch):
    monkeypatch.setattr(data_cache.yf, "download", lambda *a, **k: pd.DataFrame({"Close": []}))
    with pytest.raises(MarketDataError):
        fetch_close_prices("AAPL", "2023-01-01", "2023-02-01")


# --- offline fallback -------------------------------------------------------


def test_offline_arg_serves_fixture_single_ticker():
    out = fetch_close_prices("AAPL", "2023-01-01", "2023-02-01", offline=True)
    assert isinstance(out, pd.Series)
    assert len(out) > 0


def test_offline_arg_serves_fixture_multi_ticker():
    out = fetch_close_prices(["AAPL", "MSFT"], "2023-01-01", "2023-02-01", offline=True)
    assert isinstance(out, pd.DataFrame)
    assert list(out.columns) == ["AAPL", "MSFT"]


def test_offline_env_flag(monkeypatch):
    monkeypatch.setenv("PORTFOLIO_OFFLINE", "1")
    # No yfinance patch: if this hit the network it would not be deterministic.
    out = fetch_close_prices(["AAPL", "SPY"], "2023-01-01", "2023-02-01")
    assert list(out.columns) == ["AAPL", "SPY"]


@pytest.mark.parametrize("val", ["", "0", "false", "False"])
def test_offline_env_flag_falsey_values_disable(monkeypatch, val):
    monkeypatch.setenv("PORTFOLIO_OFFLINE", val)
    monkeypatch.setattr(data_cache.yf, "download", lambda *a, **k: _close_frame())
    out = fetch_close_prices("AAPL", "2023-01-01", "2023-02-01")
    assert list(out) == [10.0, 10.5, 10.2, 10.8]


def test_offline_unknown_ticker_raises():
    with pytest.raises(MarketDataError, match="not in offline fixture"):
        fetch_close_prices("NOPE", "2023-01-01", "2023-02-01", offline=True)


def test_offline_fixture_covers_cli_default_universe():
    """The bundled fixture must cover the CLI's default tickers (incl. JPM/GS) so
    `python main.py --offline` works out of the box -- regression for the gap where
    JPM/GS were missing and the documented offline command crashed."""
    from portfolio_optimization_engine.config import AnalysisConfig

    defaults = AnalysisConfig().tickers
    sample = data_cache._load_sample_prices()
    missing = [t for t in defaults if t not in sample.columns]
    assert not missing, f"default tickers absent from offline fixture: {missing}"
    out = fetch_close_prices(["JPM", "GS"], "2023-01-01", "2023-02-01", offline=True)
    assert list(out.columns) == ["JPM", "GS"]
    assert (out > 0).all().all()


# --- download_close_prices interaction with offline + cache ----------------


def test_download_offline_does_not_write_cache(tmp_cache):
    out = data_cache.download_close_prices(
        ["AAPL", "MSFT"], "2023-01-01", "2023-02-01", offline=True
    )
    assert list(out.columns) == ["AAPL", "MSFT"]
    assert not list(tmp_cache.glob("*.pkl"))  # offline results are not cached


def test_download_online_writes_cache_then_serves_it(tmp_cache, monkeypatch):
    calls = {"n": 0}

    def dl(tickers, start, end, auto_adjust=True):
        calls["n"] += 1
        return _close_frame()

    monkeypatch.setattr(data_cache.yf, "download", dl)
    first = data_cache.download_close_prices(["AAPL"], "2023-01-01", "2023-02-01")
    second = data_cache.download_close_prices(["AAPL"], "2023-01-01", "2023-02-01")
    assert calls["n"] == 1  # second served from on-disk cache
    assert first.equals(second)
    assert list(tmp_cache.glob("*.pkl"))


# --- benchmark fetch routes through the shared layer ------------------------


def test_benchmark_uses_offline_fixture():
    from portfolio_optimization_engine.config import AnalysisConfig

    cfg = AnalysisConfig(benchmark="SPY", offline=True)
    idx = data_cache._load_sample_prices().index
    bench = analysis._fetch_benchmark(cfg, idx)
    assert bench is not None
    assert len(bench) > 0


def test_benchmark_none_when_unset():
    from portfolio_optimization_engine.config import AnalysisConfig

    cfg = AnalysisConfig(benchmark=None)
    assert analysis._fetch_benchmark(cfg, pd.Index([])) is None
