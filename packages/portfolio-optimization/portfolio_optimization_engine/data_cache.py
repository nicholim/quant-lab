"""Resilient, dependency-free data layer for downloaded price data.

This is the SINGLE shared place where the package touches the network. Both the
optimizer's price fetch (:func:`download_close_prices`) and the analysis layer's
benchmark fetch route through :func:`fetch_close_prices`, so all network logic
(retry, backoff, offline fallback, error wrapping) lives here.

Resilience posture (mirrors ``options-pricing``'s market-data layer):

* **On-disk cache** -- repeat optimizations over the same tickers/date range
  reuse a local pickle instead of re-downloading. The cache directory defaults
  to ``~/.cache/portfolio-optimization-engine`` and can be overridden with the
  ``POE_CACHE_DIR`` environment variable. This is the PRIMARY cache and its
  behavior is unchanged.
* **Retry + exponential backoff** -- transient network / rate-limit failures
  from ``yfinance`` are retried a few times before giving up.
* **Offline fallback** -- set ``PORTFOLIO_OFFLINE=1`` (or pass ``offline=True``)
  to serve a small bundled price fixture instead of hitting the network, so
  demos never hard-fail on restricted cloud egress (e.g. Render).
* **Typed error** -- a final failure raises :class:`MarketDataError` rather than
  leaking a raw ``yfinance``/network exception.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

# --- configuration ----------------------------------------------------------

#: Number of download attempts before giving up (1 try + retries).
MAX_RETRIES = 3
#: Base seconds for the exponential backoff between retries (2 ** attempt * base).
BACKOFF_BASE = 0.5

_SAMPLE_PRICES_PATH = Path(__file__).parent / "data" / "sample_prices.csv"


class MarketDataError(Exception):
    """Raised when price data cannot be retrieved or parsed."""


def cache_dir() -> Path:
    path = Path(
        os.environ.get("POE_CACHE_DIR") or Path.home() / ".cache" / "portfolio-optimization-engine"
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(tickers, start, end, auto_adjust) -> str:
    raw = "|".join(sorted(tickers)) + f"|{start}|{end}|{auto_adjust}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _offline_enabled(offline: bool) -> bool:
    """True if the caller asked for offline, or the env flag is set."""
    if offline:
        return True
    return os.environ.get("PORTFOLIO_OFFLINE", "") not in ("", "0", "false", "False")


# --- bundled offline fixture ------------------------------------------------


def _load_sample_prices() -> pd.DataFrame:
    """Load the bundled offline price fixture (Date-indexed, ticker columns)."""
    if not _SAMPLE_PRICES_PATH.exists():  # pragma: no cover - fixture ships with pkg
        raise MarketDataError(f"Sample price fixture missing at {_SAMPLE_PRICES_PATH}")
    return pd.read_csv(_SAMPLE_PRICES_PATH, index_col=0, parse_dates=True)


def _offline_close_prices(tickers) -> pd.DataFrame | pd.Series:
    """Serve close prices for ``tickers`` from the bundled fixture.

    Mirrors yfinance's shape: a Series for a single ticker, a DataFrame for many.
    Tickers absent from the fixture raise :class:`MarketDataError` so the offline
    path fails loudly on a typo rather than silently dropping columns.
    """
    sample = _load_sample_prices()
    wanted = [tickers] if isinstance(tickers, str) else list(tickers)
    missing = [t for t in wanted if t not in sample.columns]
    if missing:
        raise MarketDataError(
            f"Tickers {missing} not in offline fixture (have {list(sample.columns)})"
        )
    prices = sample[wanted].copy()
    if isinstance(tickers, str):
        return prices.iloc[:, 0]
    return prices


# --- resilient network fetch ------------------------------------------------


def _is_transient(exc: Exception) -> bool:
    """Heuristic: treat network / rate-limit / timeout errors as retryable."""
    text = f"{type(exc).__name__} {exc}".lower()
    needles = (
        "timeout",
        "timed out",
        "connection",
        "rate limit",
        "too many requests",
        "429",
        "temporarily",
        "unavailable",
        "503",
        "502",
        "reset",
    )
    return any(n in text for n in needles)


def fetch_close_prices(
    tickers,
    start,
    end,
    auto_adjust: bool = True,
    offline: bool = False,
) -> pd.DataFrame | pd.Series:
    """Resilient ``yf.download(...)["Close"]`` -- the SINGLE network entry point.

    Retries transient / rate-limit failures with exponential backoff, serves a
    bundled fixture when offline, and raises :class:`MarketDataError` on final
    failure instead of leaking a raw exception. Returns the ``Close`` frame/series
    exactly as the previous direct ``yf.download`` calls did, so callers are
    unchanged in shape.
    """
    if _offline_enabled(offline):
        return _offline_close_prices(tickers)

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            raw = yf.download(tickers, start=start, end=end, auto_adjust=auto_adjust)
            if raw is None or len(raw) == 0:
                raise MarketDataError(f"yfinance returned no data for {tickers!r}")
            return raw["Close"]
        except MarketDataError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize any download failure
            last_exc = exc
            if attempt < MAX_RETRIES - 1 and _is_transient(exc):
                time.sleep(BACKOFF_BASE * (2**attempt))
                continue
            break

    raise MarketDataError(
        f"Failed to download prices for {tickers!r} after {MAX_RETRIES} attempt(s): {last_exc}"
    ) from last_exc


def download_close_prices(
    tickers,
    start,
    end,
    auto_adjust: bool = True,
    use_cache: bool = True,
    offline: bool = False,
) -> pd.DataFrame:
    """Return adjusted close prices, served from the local cache when available.

    The on-disk pickle cache is the PRIMARY cache (behavior unchanged). Cache
    misses go through :func:`fetch_close_prices` (retry/backoff/offline). Offline
    results are not written to the on-disk cache so a real online run later still
    fetches live data under the same key.
    """
    path = cache_dir() / f"{_cache_key(tickers, start, end, auto_adjust)}.pkl"
    if use_cache and path.exists():
        return pd.read_pickle(path)

    offline_active = _offline_enabled(offline)
    prices = fetch_close_prices(tickers, start, end, auto_adjust=auto_adjust, offline=offline)
    if use_cache and not offline_active:
        prices.to_pickle(path)
    return prices
