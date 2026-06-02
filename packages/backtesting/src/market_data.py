"""Resilient market-data download layer for the backtester.

This is the single network layer beneath the DuckDB on-disk cache. All three
yfinance call sites in the framework (``datastore.fetch_ohlcv``,
``YFinanceDataHandler.fetch`` no-store path, and the ``Backtest`` benchmark
download) route through :func:`download_ohlcv` so the retry/offline/timeout
logic lives in ONE place.

Posture (mirrors ``packages/options-pricing/src/market_data.py``):
  * retry with exponential backoff on transient network / rate-limit errors,
  * a clear typed :class:`MarketDataError` on final failure,
  * a graceful OFFLINE fallback (``offline=`` arg or ``BACKTESTING_OFFLINE`` env
    flag) that serves a small BUNDLED deterministic OHLCV fixture instead of
    hitting the network, so demos never hard-fail on cloud egress.

The DuckDB cache in ``datastore.py`` remains the primary cache; this module is
only the thing that fetches on a cache miss.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

# --- configuration ---------------------------------------------------------

#: yfinance download attempts before giving up (initial try + retries).
DEFAULT_MAX_ATTEMPTS = 3
#: Base seconds for exponential backoff (attempt n waits BASE * 2**(n-1)).
DEFAULT_BACKOFF_BASE = 0.5
#: Per-request timeout passed to ``yf.download`` (seconds).
DEFAULT_TIMEOUT = 10

#: OHLCV columns the rest of the framework expects (yfinance "Title" case).
OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

_SAMPLE_OHLCV_PATH = Path(__file__).parent / "data" / "sample_ohlcv.csv"

# Substrings that mark a yfinance/network error as transient and worth retrying.
_TRANSIENT_MARKERS = (
    "timed out",
    "timeout",
    "rate limit",
    "too many requests",
    "429",
    "connection",
    "temporarily",
    "try again",
    "503",
    "502",
    "500",
    "reset by peer",
)


class MarketDataError(Exception):
    """Raised when market data cannot be downloaded after retries."""


def _offline_enabled(offline: bool) -> bool:
    """True if the caller asked for offline, or ``BACKTESTING_OFFLINE`` is set."""
    if offline:
        return True
    return os.environ.get("BACKTESTING_OFFLINE", "") not in ("", "0", "false", "False")


def _is_transient(exc: Exception) -> bool:
    """Heuristic: should this error be retried? Network/rate-limit -> yes."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the symbol level yfinance adds for single-ticker MultiIndex frames."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.droplevel(1)
    return df


def _load_sample_ohlcv(start_date: str | None, end_date: str | None) -> pd.DataFrame:
    """Load the bundled deterministic OHLCV fixture, sliced to the date window.

    The fixture is symbol-agnostic so any requested ticker resolves to the same
    deterministic series — enough for demos / offline CI, never live trading.
    """
    if not _SAMPLE_OHLCV_PATH.exists():  # pragma: no cover - fixture ships with pkg
        raise MarketDataError(f"Sample OHLCV fixture missing at {_SAMPLE_OHLCV_PATH}")
    df = pd.read_csv(_SAMPLE_OHLCV_PATH, parse_dates=["date"]).set_index("date")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[OHLCV_COLUMNS]
    if start_date is not None:
        df = df[df.index >= pd.Timestamp(start_date)]
    if end_date is not None:
        df = df[df.index <= pd.Timestamp(end_date)]
    if df.empty:
        raise MarketDataError(f"Sample OHLCV fixture has no rows in [{start_date}, {end_date}]")
    df.index.name = "Date"
    return df.copy()


def download_ohlcv(
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    offline: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    timeout: int = DEFAULT_TIMEOUT,
) -> pd.DataFrame:
    """Download OHLCV for ``symbol`` with retries, or serve the offline fixture.

    Returns a DataFrame indexed by date with :data:`OHLCV_COLUMNS` (single-level
    columns; the yfinance symbol MultiIndex level is dropped).

    Retries transient network/rate-limit failures with exponential backoff up to
    ``max_attempts``. Raises :class:`MarketDataError` on final failure (or on an
    empty result, which usually means a bad symbol/date range). When offline
    (arg or ``BACKTESTING_OFFLINE``), returns the bundled fixture without any
    network access.
    """
    if _offline_enabled(offline):
        return _load_sample_ohlcv(start_date, end_date)

    import yfinance as yf

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            df = yf.download(
                symbol,
                start=start_date,
                end=end_date,
                auto_adjust=True,
                progress=False,
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 - yfinance raises a grab-bag
            last_exc = exc
            if attempt < max_attempts and _is_transient(exc):
                time.sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise MarketDataError(
                f"Failed to download {symbol!r} after {attempt} attempt(s): {exc}"
            ) from exc

        if df is None or df.empty:
            # An empty frame from a transient hiccup is worth one more try.
            if attempt < max_attempts:
                time.sleep(backoff_base * (2 ** (attempt - 1)))
                continue
            raise MarketDataError(f"No data returned for {symbol!r} in [{start_date}, {end_date}]")

        return _flatten_columns(df)

    # Defensive: the final loop iteration always returns or raises, so this is
    # unreachable in practice — kept as a guard for static analysers.
    raise MarketDataError(  # pragma: no cover
        f"No data returned for {symbol!r} after {max_attempts} attempt(s)"
        + (f": {last_exc}" if last_exc else "")
    )
