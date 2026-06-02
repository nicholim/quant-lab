"""Live market-data layer for pricing REAL options from FREE data.

Data-source split (intentional):
  * Option chains + expirations  -> yfinance (the only free full-chain source).
  * Underlying spot quote         -> Finnhub primary, yfinance fallback.

Finnhub's free tier serves real-time stock quotes and is reliable from cloud
IPs (e.g. Render); it does NOT serve option chains, so chains stay on yfinance.
yfinance egress can be rate-limited from cloud IPs, hence the bundled offline
fixture and the ``offline`` / ``OPTIONS_PRICING_OFFLINE`` escape hatch so the
demo never hard-crashes when the network or keys are unavailable.

The API key is read from the ``FINNHUB_API_KEY`` env var. It is NEVER hardcoded;
if unset, spot quotes silently fall back to yfinance. For local development the
key may instead be placed in a ``.env`` file (see ``.env.example``); it is loaded
once at import via python-dotenv. Real environment variables always take
precedence over ``.env``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from .black_scholes import black_scholes_price, implied_volatility

_logger = logging.getLogger(__name__)

# --- configuration ---------------------------------------------------------

#: Sensible default risk-free rate used by the pricing flow when none is given.
DEFAULT_RISK_FREE_RATE = 0.045

_FINNHUB_QUOTE_URL = "https://finnhub.io/api/v1/quote"
_HTTP_TIMEOUT = 5  # seconds

_SAMPLE_CHAIN_PATH = Path(__file__).parent / "data" / "sample_chain.csv"
#: Spot price baked into the offline sample fixture (AAPL-like).
SAMPLE_SPOT = 195.0
#: Days-to-expiry the sample fixture's mid prices were generated for. The
#: offline expiry is computed as (today + this many days) so time-to-expiry is
#: always ~45d/365 and our IV solver round-trips the fixture on any run date.
SAMPLE_EXPIRY_DAYS = 45


def sample_expiry(now: datetime | None = None) -> str:
    """Offline-fixture expiry (YYYY-MM-DD), ~45 calendar days from ``now``."""
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(days=SAMPLE_EXPIRY_DAYS)).strftime("%Y-%m-%d")


#: Normalized option-chain columns produced by :func:`get_option_chain`.
CHAIN_COLUMNS = [
    "strike",
    "bid",
    "ask",
    "mid",
    "last",
    "market_iv",
    "volume",
    "open_interest",
]

_NORMALIZED_DTYPES = {
    "strike": float,
    "bid": float,
    "ask": float,
    "mid": float,
    "last": float,
    "market_iv": float,
    "volume": float,
    "open_interest": float,
}

# Process-lifetime spot cache, keyed by symbol.
_spot_cache: dict[str, float] = {}

# Emit the "key set but rejected" warning at most once per process.
_finnhub_auth_warned = False

_dotenv_loaded = False


def _load_dotenv_once() -> None:
    """Load a local ``.env`` once so secrets can live in a file, not the shell.

    Lets ``FINNHUB_API_KEY`` (and any other config) be set via a ``.env`` in the
    working dir or a parent. python-dotenv does NOT override variables already
    present in the environment, so an exported value always wins. Idempotent.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    _dotenv_loaded = True
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is an optional convenience
        return
    load_dotenv(find_dotenv(usecwd=True))


# Pick up a local .env at import time (before any env var is read), so the
# per-test environment reset in the test suite is never re-clobbered mid-test.
_load_dotenv_once()


class MarketDataError(Exception):
    """Raised when live market data cannot be retrieved or parsed."""


# --- helpers ----------------------------------------------------------------


def _offline_enabled(offline: bool) -> bool:
    """True if the caller asked for offline, or the env flag is set."""
    if offline:
        return True
    return os.environ.get("OPTIONS_PRICING_OFFLINE", "") not in ("", "0", "false", "False")


def clear_cache() -> None:
    """Clear the in-memory spot cache (mainly for tests)."""
    global _finnhub_auth_warned
    _spot_cache.clear()
    _finnhub_auth_warned = False


# --- spot quotes ------------------------------------------------------------


def _warn_finnhub_auth_once(status_code: int) -> None:
    """Log (once per process) that a SET ``FINNHUB_API_KEY`` was rejected."""
    global _finnhub_auth_warned
    if _finnhub_auth_warned:
        return
    _finnhub_auth_warned = True
    _logger.warning(
        "FINNHUB_API_KEY is set but Finnhub rejected it (HTTP %d). Falling back "
        "to yfinance for spot quotes. Check the key value — it must be the raw "
        "token with no surrounding quotes and no 'FINNHUB_API_KEY=' prefix.",
        status_code,
    )


def _finnhub_spot(symbol: str) -> float | None:
    """Fetch spot from Finnhub if a key is configured, else None.

    Returns None (rather than raising) so the caller can fall back to yfinance.
    """
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return None
    try:
        resp = requests.get(
            _FINNHUB_QUOTE_URL,
            params={"symbol": symbol, "token": key},
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException:
        return None
    if resp.status_code in (401, 403):
        # The key is SET but Finnhub rejected it. Surface this once instead of
        # silently falling back to yfinance, which would mask a misconfigured
        # key (e.g. a stray 'FINNHUB_API_KEY=' prefix or quotes in the value).
        _warn_finnhub_auth_once(resp.status_code)
        return None
    if resp.status_code != 200:
        return None
    try:
        current = float(resp.json().get("c", 0.0))
    except (ValueError, TypeError):
        return None
    if current <= 0:
        return None
    return current


def _yfinance_spot(symbol: str) -> float:
    """Fetch spot from yfinance (fast_info, then a recent close)."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    try:
        price = float(ticker.fast_info["last_price"])
        if price > 0:
            return price
    except (KeyError, TypeError, ValueError, AttributeError):
        pass

    try:
        hist = ticker.history(period="5d")
    except Exception as exc:  # pragma: no cover - network-only failure mode
        raise MarketDataError(f"yfinance spot lookup failed for {symbol!r}: {exc}") from exc
    if hist is None or hist.empty or "Close" not in hist:
        raise MarketDataError(f"No spot price available for {symbol!r}")
    return float(hist["Close"].iloc[-1])


def get_spot(symbol: str, offline: bool = False) -> float:
    """Return the latest spot price for ``symbol``.

    Tries Finnhub first (if ``FINNHUB_API_KEY`` is set), then yfinance.
    Results are cached per-symbol for the life of the process.
    In offline mode returns the fixture's baked-in spot.
    """
    symbol = symbol.upper()
    if _offline_enabled(offline):
        return SAMPLE_SPOT
    if symbol in _spot_cache:
        return _spot_cache[symbol]

    price = _finnhub_spot(symbol)
    if price is None:
        price = _yfinance_spot(symbol)
    _spot_cache[symbol] = price
    return price


# --- expirations & chains ---------------------------------------------------


def list_expirations(symbol: str, offline: bool = False) -> list[str]:
    """Return available option expiration dates (``YYYY-MM-DD``) for ``symbol``."""
    if _offline_enabled(offline):
        return [sample_expiry()]
    import yfinance as yf

    try:
        expiries = list(yf.Ticker(symbol.upper()).options)
    except Exception as exc:
        raise MarketDataError(f"Could not list expirations for {symbol!r}: {exc}") from exc
    if not expiries:
        raise MarketDataError(f"No option expirations found for {symbol!r}")
    return expiries


def _normalize_chain(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw chain DataFrame to :data:`CHAIN_COLUMNS`.

    ``mid`` is ``(bid + ask) / 2`` when both are positive, else falls back to
    ``last``.
    """
    out = pd.DataFrame()
    out["strike"] = pd.to_numeric(df.get("strike"), errors="coerce")
    out["bid"] = pd.to_numeric(df.get("bid"), errors="coerce").fillna(0.0)
    out["ask"] = pd.to_numeric(df.get("ask"), errors="coerce").fillna(0.0)
    out["last"] = pd.to_numeric(df.get("last", df.get("lastPrice")), errors="coerce").fillna(0.0)
    out["market_iv"] = pd.to_numeric(
        df.get("market_iv", df.get("impliedVolatility")), errors="coerce"
    ).fillna(0.0)
    out["volume"] = pd.to_numeric(df.get("volume"), errors="coerce").fillna(0.0)
    out["open_interest"] = pd.to_numeric(
        df.get("open_interest", df.get("openInterest")), errors="coerce"
    ).fillna(0.0)

    both_pos = (out["bid"] > 0) & (out["ask"] > 0)
    out["mid"] = ((out["bid"] + out["ask"]) / 2).where(both_pos, out["last"])

    out = out[CHAIN_COLUMNS].astype(_NORMALIZED_DTYPES)
    out = out.dropna(subset=["strike"]).sort_values("strike").reset_index(drop=True)
    return out


def _load_sample_chain(option_type: str) -> pd.DataFrame:
    """Load the bundled offline sample chain for ``option_type``."""
    if not _SAMPLE_CHAIN_PATH.exists():  # pragma: no cover - fixture ships with pkg
        raise MarketDataError(f"Sample chain fixture missing at {_SAMPLE_CHAIN_PATH}")
    raw = pd.read_csv(_SAMPLE_CHAIN_PATH)
    raw = raw[raw["option_type"] == option_type]
    if raw.empty:
        raise MarketDataError(f"No sample {option_type} data in fixture")
    return _normalize_chain(raw)


def get_option_chain(
    symbol: str,
    expiry: str,
    option_type: str = "call",
    offline: bool = False,
) -> pd.DataFrame:
    """Return a normalized option chain for ``symbol`` / ``expiry``.

    Columns: :data:`CHAIN_COLUMNS`. Calls yfinance; in offline mode returns the
    bundled sample fixture instead of hitting the network.
    """
    if option_type not in ("call", "put"):
        raise MarketDataError(f"option_type must be 'call' or 'put', got {option_type!r}")

    if _offline_enabled(offline):
        return _load_sample_chain(option_type)

    import yfinance as yf

    try:
        chain = yf.Ticker(symbol.upper()).option_chain(expiry)
    except Exception as exc:
        raise MarketDataError(
            f"Could not fetch {option_type} chain for {symbol!r} @ {expiry}: {exc}"
        ) from exc
    raw = chain.calls if option_type == "call" else chain.puts
    if raw is None or len(raw) == 0:
        raise MarketDataError(f"Empty {option_type} chain for {symbol!r} @ {expiry}")
    return _normalize_chain(raw)


# --- pricing flow -----------------------------------------------------------


def _years_to_expiry(expiry: str, now: datetime | None = None) -> float:
    """Calendar days to ``expiry`` (YYYY-MM-DD) divided by 365."""
    now = now or datetime.now(timezone.utc)
    try:
        exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise MarketDataError(f"Bad expiry {expiry!r}; expected YYYY-MM-DD") from exc
    days = (exp - now).days
    return max(days, 0) / 365.0


def price_chain(
    symbol: str,
    expiry: str,
    option_type: str = "call",
    r: float = DEFAULT_RISK_FREE_RATE,
    q: float = 0.0,
    offline: bool = False,
) -> pd.DataFrame:
    """Price a live option chain with our own Black-Scholes model.

    For each contract this adds, alongside the normalized chain columns:
      * ``model_price`` -- our Black-Scholes price at ``market_iv`` (proxy vol),
      * ``our_iv``      -- implied vol we solve from the market ``mid``,
      * ``mispricing``  -- ``model_price - mid``.

    Spot comes from :func:`get_spot`; ``T`` from calendar days/365.
    yfinance's ``impliedVolatility`` is kept as ``market_iv`` for comparison but
    is never trusted for ``our_iv`` -- we always solve our own.
    """
    spot = get_spot(symbol, offline=offline)
    expiry = sample_expiry() if _offline_enabled(offline) else expiry
    T = _years_to_expiry(expiry)
    chain = get_option_chain(symbol, expiry, option_type, offline=offline)

    model_prices: list[float] = []
    our_ivs: list[float | None] = []
    for row in chain.itertuples(index=False):
        strike = float(row.strike)
        mid = float(row.mid)
        proxy_iv = float(row.market_iv) if row.market_iv > 0 else 0.2
        model_prices.append(black_scholes_price(spot, strike, T, r, proxy_iv, option_type, q))
        iv = implied_volatility(mid, spot, strike, T, r, option_type, q) if mid > 0 else None
        our_ivs.append(iv)

    out = chain.copy()
    out["model_price"] = model_prices
    out["our_iv"] = our_ivs
    out["mispricing"] = out["model_price"] - out["mid"]
    out.attrs["spot"] = spot
    out.attrs["expiry"] = expiry
    out.attrs["T"] = T
    out.attrs["symbol"] = symbol.upper()
    return out
