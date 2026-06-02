import pandas as pd
import pytest

from portfolio_optimization_engine import data_cache


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("POE_CACHE_DIR", str(tmp_path))
    return tmp_path


def _fake_download(calls):
    def _dl(tickers, start, end, auto_adjust=True):
        calls.append((tuple(tickers), start, end))
        idx = pd.date_range("2021-01-01", periods=3)
        return pd.DataFrame({"Close": [10.0, 11.0, 12.0]}, index=idx)

    return _dl


def test_key_is_order_independent():
    k1 = data_cache._cache_key(["AAPL", "MSFT"], "2020-01-01", "2021-01-01", True)
    k2 = data_cache._cache_key(["MSFT", "AAPL"], "2020-01-01", "2021-01-01", True)
    assert k1 == k2


def test_key_changes_with_dates():
    k1 = data_cache._cache_key(["AAPL"], "2020-01-01", "2021-01-01", True)
    k2 = data_cache._cache_key(["AAPL"], "2020-01-01", "2022-01-01", True)
    assert k1 != k2


def test_second_call_served_from_cache(tmp_cache, monkeypatch):
    calls = []
    monkeypatch.setattr(data_cache.yf, "download", _fake_download(calls))

    first = data_cache.download_close_prices(["AAPL"], "2020-01-01", "2021-01-01")
    second = data_cache.download_close_prices(["AAPL"], "2020-01-01", "2021-01-01")

    assert len(calls) == 1  # only the first call hit "yfinance"
    assert first.equals(second)


def test_use_cache_false_always_downloads(tmp_cache, monkeypatch):
    calls = []
    monkeypatch.setattr(data_cache.yf, "download", _fake_download(calls))

    data_cache.download_close_prices(["AAPL"], "2020-01-01", "2021-01-01", use_cache=False)
    data_cache.download_close_prices(["AAPL"], "2020-01-01", "2021-01-01", use_cache=False)

    assert len(calls) == 2  # cache bypassed both times


def test_cache_file_written(tmp_cache, monkeypatch):
    monkeypatch.setattr(data_cache.yf, "download", _fake_download([]))
    data_cache.download_close_prices(["AAPL"], "2020-01-01", "2021-01-01")
    assert list(tmp_cache.glob("*.pkl"))  # a cache file exists
