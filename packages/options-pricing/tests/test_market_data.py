"""Tests for the live market-data layer (mocked — no live network).

yfinance.Ticker and requests.get are monkeypatched throughout; we never touch
the real network. Covers: Finnhub-primary, Finnhub->yfinance fallback, no-key
fallback, chain normalization (mid logic), price_chain math, the offline
fixture path, and MarketDataError.
"""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from src import market_data as md  # noqa: E402
from src.greeks_visualizer import (  # noqa: E402
    plot_market_iv_smile,
    plot_market_iv_surface,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    md.clear_cache()
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("OPTIONS_PRICING_OFFLINE", raising=False)
    yield
    md.clear_cache()
    plt.close("all")


# --- fakes -----------------------------------------------------------------


class _FakeResp:
    def __init__(self, status_code=200, payload=None, raise_exc=None):
        self.status_code = status_code
        self._payload = payload or {}
        self._raise_exc = raise_exc

    def json(self):
        if self._raise_exc:
            raise self._raise_exc
        return self._payload


class _FakeChain:
    def __init__(self, calls, puts):
        self.calls = calls
        self.puts = puts


class _FakeTicker:
    def __init__(self, symbol, *, fast=None, hist=None, options=None, chain=None):
        self.symbol = symbol
        self._fast = fast
        self._hist = hist
        self.options = options or []
        self._chain = chain

    @property
    def fast_info(self):
        if self._fast is None:
            raise KeyError("last_price")
        return self._fast

    def history(self, period="5d"):
        if self._hist is None:
            return pd.DataFrame()
        return self._hist

    def option_chain(self, expiry):
        if self._chain is None:
            raise ValueError("no chain")
        return self._chain


def _yf_factory(monkeypatch, ticker):
    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", lambda sym: ticker)


# --- .env loading -----------------------------------------------------------


def test_load_dotenv_once_is_idempotent_and_loads(monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(md, "_dotenv_loaded", False)
    # Stub python-dotenv so the test never reads a real .env from disk.
    import dotenv

    monkeypatch.setattr(dotenv, "find_dotenv", lambda *a, **k: "/tmp/.env")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda path: calls.append(path) or True)

    md._load_dotenv_once()
    assert md._dotenv_loaded is True
    assert calls == ["/tmp/.env"]

    # Second call is a no-op (guarded) — load_dotenv must not run again.
    md._load_dotenv_once()
    assert calls == ["/tmp/.env"]


# --- spot: Finnhub primary --------------------------------------------------


def test_finnhub_spot_primary(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(md.requests, "get", lambda *a, **k: _FakeResp(200, {"c": 201.5}))
    assert md.get_spot("AAPL") == 201.5
    # cached on second call (would fail if it re-hit; force get to raise)
    monkeypatch.setattr(md.requests, "get", lambda *a, **k: 1 / 0)
    assert md.get_spot("AAPL") == 201.5


def test_finnhub_non200_falls_back_to_yfinance(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(md.requests, "get", lambda *a, **k: _FakeResp(429, {}))
    _yf_factory(monkeypatch, _FakeTicker("AAPL", fast={"last_price": 188.0}))
    assert md.get_spot("AAPL") == 188.0


def test_finnhub_401_warns_then_falls_back(monkeypatch, caplog):
    """A SET-but-rejected key logs an actionable warning, then falls back."""
    monkeypatch.setenv("FINNHUB_API_KEY", "bad-key")
    monkeypatch.setattr(md.requests, "get", lambda *a, **k: _FakeResp(401, {"error": "Invalid"}))
    _yf_factory(monkeypatch, _FakeTicker("AAPL", fast={"last_price": 188.0}))
    with caplog.at_level("WARNING", logger="src.market_data"):
        assert md.get_spot("AAPL") == 188.0
    assert any("Finnhub rejected it" in r.message for r in caplog.records)
    assert any("HTTP 401" in r.getMessage() for r in caplog.records)


def test_finnhub_auth_warning_emitted_once(monkeypatch, caplog):
    """The auth warning is throttled to once per process (until clear_cache)."""
    monkeypatch.setenv("FINNHUB_API_KEY", "bad-key")
    monkeypatch.setattr(md.requests, "get", lambda *a, **k: _FakeResp(403, {}))
    _yf_factory(monkeypatch, _FakeTicker("AAPL", fast={"last_price": 1.0}))
    with caplog.at_level("WARNING", logger="src.market_data"):
        md._finnhub_spot("AAPL")
        md._finnhub_spot("MSFT")
    assert sum("Finnhub rejected it" in r.message for r in caplog.records) == 1


def test_finnhub_zero_price_falls_back(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(md.requests, "get", lambda *a, **k: _FakeResp(200, {"c": 0.0}))
    _yf_factory(monkeypatch, _FakeTicker("X", fast={"last_price": 5.0}))
    assert md.get_spot("X") == 5.0


def test_finnhub_request_exception_falls_back(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")

    def _boom(*a, **k):
        raise md.requests.RequestException("network down")

    monkeypatch.setattr(md.requests, "get", _boom)
    _yf_factory(monkeypatch, _FakeTicker("X", fast={"last_price": 9.0}))
    assert md.get_spot("X") == 9.0


def test_finnhub_bad_json_falls_back(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setattr(
        md.requests, "get", lambda *a, **k: _FakeResp(200, raise_exc=ValueError("nope"))
    )
    _yf_factory(monkeypatch, _FakeTicker("X", fast={"last_price": 7.0}))
    assert md.get_spot("X") == 7.0


# --- spot: no key -> yfinance ----------------------------------------------


def test_no_key_uses_yfinance_fast_info(monkeypatch):
    _yf_factory(monkeypatch, _FakeTicker("MSFT", fast={"last_price": 410.0}))
    assert md.get_spot("MSFT") == 410.0


def test_yfinance_fast_info_missing_uses_history(monkeypatch):
    hist = pd.DataFrame({"Close": [100.0, 101.0, 102.5]})
    _yf_factory(monkeypatch, _FakeTicker("T", fast=None, hist=hist))
    assert md.get_spot("T") == 102.5


def test_yfinance_fast_info_zero_uses_history(monkeypatch):
    hist = pd.DataFrame({"Close": [50.0, 55.0]})
    _yf_factory(monkeypatch, _FakeTicker("T", fast={"last_price": 0.0}, hist=hist))
    assert md.get_spot("T") == 55.0


def test_yfinance_empty_history_raises(monkeypatch):
    _yf_factory(monkeypatch, _FakeTicker("T", fast=None, hist=pd.DataFrame()))
    with pytest.raises(md.MarketDataError):
        md.get_spot("T")


# --- expirations ------------------------------------------------------------


def test_list_expirations(monkeypatch):
    _yf_factory(monkeypatch, _FakeTicker("AAPL", options=["2026-07-17", "2026-08-21"]))
    assert md.list_expirations("AAPL") == ["2026-07-17", "2026-08-21"]


def test_list_expirations_empty_raises(monkeypatch):
    _yf_factory(monkeypatch, _FakeTicker("AAPL", options=[]))
    with pytest.raises(md.MarketDataError):
        md.list_expirations("AAPL")


def test_list_expirations_yf_error_raises(monkeypatch):
    class _Boom:
        @property
        def options(self):
            raise RuntimeError("rate limited")

    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", lambda sym: _Boom())
    with pytest.raises(md.MarketDataError):
        md.list_expirations("AAPL")


def test_list_expirations_offline():
    out = md.list_expirations("ANYTHING", offline=True)
    assert out == [md.sample_expiry()]


# --- chain normalization ----------------------------------------------------


def test_normalize_chain_mid_logic():
    raw = pd.DataFrame(
        {
            "strike": [100, 105, 110],
            "bid": [5.0, 0.0, 2.0],
            "ask": [5.4, 0.0, 2.4],
            "lastPrice": [5.2, 3.3, 2.1],
            "impliedVolatility": [0.2, 0.25, 0.3],
            "volume": [10, 20, 30],
            "openInterest": [100, 200, 300],
        }
    )
    out = md._normalize_chain(raw)
    assert list(out.columns) == md.CHAIN_COLUMNS
    # both bid/ask positive -> (bid+ask)/2
    assert out.loc[0, "mid"] == pytest.approx(5.2)
    # bid/ask zero -> falls back to last
    assert out.loc[1, "mid"] == pytest.approx(3.3)
    assert out.loc[2, "mid"] == pytest.approx(2.2)


def test_get_option_chain_via_yfinance(monkeypatch):
    calls = pd.DataFrame(
        {
            "strike": [100],
            "bid": [5.0],
            "ask": [5.2],
            "lastPrice": [5.1],
            "impliedVolatility": [0.2],
            "volume": [10],
            "openInterest": [50],
        }
    )
    puts = calls.copy()
    _yf_factory(monkeypatch, _FakeTicker("AAPL", chain=_FakeChain(calls, puts)))
    out = md.get_option_chain("AAPL", "2026-07-17", "call")
    assert out.loc[0, "mid"] == pytest.approx(5.1)


def test_get_option_chain_bad_type():
    with pytest.raises(md.MarketDataError):
        md.get_option_chain("AAPL", "2026-07-17", "straddle")


def test_get_option_chain_empty_raises(monkeypatch):
    empty = pd.DataFrame(
        columns=["strike", "bid", "ask", "lastPrice", "impliedVolatility", "volume", "openInterest"]
    )
    _yf_factory(monkeypatch, _FakeTicker("AAPL", chain=_FakeChain(empty, empty)))
    with pytest.raises(md.MarketDataError):
        md.get_option_chain("AAPL", "2026-07-17", "call")


def test_get_option_chain_yf_error_raises(monkeypatch):
    _yf_factory(monkeypatch, _FakeTicker("AAPL", chain=None))
    with pytest.raises(md.MarketDataError):
        md.get_option_chain("AAPL", "2026-07-17", "put")


# --- offline fixture --------------------------------------------------------


def test_offline_fixture_chain():
    out = md.get_option_chain("AAPL", "ignored", "call", offline=True)
    assert list(out.columns) == md.CHAIN_COLUMNS
    assert len(out) == 11
    assert (out["strike"].diff().dropna() > 0).all()  # sorted


def test_offline_via_env_flag(monkeypatch):
    monkeypatch.setenv("OPTIONS_PRICING_OFFLINE", "1")
    out = md.get_option_chain("AAPL", "x", "put")
    assert len(out) == 11
    assert md.get_spot("AAPL") == md.SAMPLE_SPOT


def test_offline_env_flag_zero_is_off(monkeypatch):
    monkeypatch.setenv("OPTIONS_PRICING_OFFLINE", "0")
    assert not md._offline_enabled(False)


def test_load_sample_chain_missing_type(monkeypatch):
    # Patch the loader's CSV read to return rows of only one type.
    orig = pd.read_csv

    def _fake(path):
        df = orig(path)
        return df[df["option_type"] == "call"]

    monkeypatch.setattr(md.pd, "read_csv", _fake)
    with pytest.raises(md.MarketDataError):
        md._load_sample_chain("put")


# --- price_chain math -------------------------------------------------------


def test_price_chain_offline_math():
    priced = md.price_chain("AAPL", "ignored", "call", offline=True)
    for col in ("model_price", "our_iv", "mispricing"):
        assert col in priced.columns
    assert priced.attrs["spot"] == md.SAMPLE_SPOT
    assert priced.attrs["T"] > 0
    # fixture mids were generated by BS at market_iv ~45d out; our_iv must solve
    assert priced["our_iv"].notna().all()
    # mispricing == model_price - mid by construction
    assert (priced["mispricing"] - (priced["model_price"] - priced["mid"])).abs().max() < 1e-9
    # a recovered IV near the fixture's market_iv (within spread tolerance)
    atm = priced.iloc[(priced["strike"] - md.SAMPLE_SPOT).abs().argmin()]
    assert abs(atm["our_iv"] - atm["market_iv"]) < 0.05


def test_price_chain_synthetic(monkeypatch):
    # tiny synthetic chain: spot=100, one ATM call, mid set to a known BS price.
    from src.black_scholes import black_scholes_price

    spot, K, sigma = 100.0, 100.0, 0.2
    monkeypatch.setattr(md, "get_spot", lambda sym, offline=False: spot)
    # pin T by faking the expiry to ~365 days out
    expiry = md.sample_expiry()
    T = md._years_to_expiry(expiry)
    mid = black_scholes_price(spot, K, T, 0.045, sigma, "call")
    chain = pd.DataFrame(
        {
            "strike": [K],
            "bid": [mid - 0.01],
            "ask": [mid + 0.01],
            "last": [mid],
            "market_iv": [sigma],
            "volume": [1],
            "open_interest": [1],
        }
    )
    monkeypatch.setattr(md, "get_option_chain", lambda *a, **k: md._normalize_chain(chain))
    priced = md.price_chain("X", expiry, "call", r=0.045)
    assert priced.loc[0, "our_iv"] == pytest.approx(sigma, abs=1e-3)
    assert priced.loc[0, "model_price"] == pytest.approx(mid, abs=1e-2)


def test_price_chain_zero_mid_gives_none_iv(monkeypatch):
    monkeypatch.setattr(md, "get_spot", lambda sym, offline=False: 100.0)
    chain = pd.DataFrame(
        {
            "strike": [100.0],
            "bid": [0.0],
            "ask": [0.0],
            "last": [0.0],
            "market_iv": [0.0],
            "volume": [0],
            "open_interest": [0],
        }
    )
    monkeypatch.setattr(md, "get_option_chain", lambda *a, **k: md._normalize_chain(chain))
    priced = md.price_chain("X", md.sample_expiry(), "call")
    assert priced.loc[0, "our_iv"] is None


def test_years_to_expiry_bad_format():
    with pytest.raises(md.MarketDataError):
        md._years_to_expiry("07/17/2026")


def test_years_to_expiry_past_is_zero():
    assert md._years_to_expiry("2000-01-01") == 0.0


# --- IV smile / surface plots (headless smoke) ------------------------------


def test_plot_market_iv_smile_saves(tmp_path):
    priced = md.price_chain("AAPL", "x", "call", offline=True)
    out = tmp_path / "smile.png"
    plot_market_iv_smile(priced, iv_column="our_iv", save_path=str(out))
    assert out.exists() and out.stat().st_size > 0


def test_plot_market_iv_smile_market_column(tmp_path):
    priced = md.price_chain("AAPL", "x", "put", offline=True)
    out = tmp_path / "smile_mkt.png"
    plot_market_iv_smile(priced, iv_column="market_iv", save_path=str(out))
    assert out.exists() and out.stat().st_size > 0


def test_plot_market_iv_smile_no_save():
    priced = md.price_chain("AAPL", "x", "call", offline=True)
    plot_market_iv_smile(priced)  # exercises plt.show no-save branch under Agg


def test_plot_market_iv_surface_saves(tmp_path):
    chains = {
        md.sample_expiry(): md.price_chain("AAPL", "x", "call", offline=True),
        "2099-12-31": md.price_chain("AAPL", "x", "put", offline=True),
    }
    out = tmp_path / "surface.png"
    plot_market_iv_surface(chains, iv_column="our_iv", save_path=str(out))
    assert out.exists() and out.stat().st_size > 0


def test_plot_market_iv_surface_no_save():
    chains = {md.sample_expiry(): md.price_chain("AAPL", "x", "call", offline=True)}
    plot_market_iv_surface(chains)
