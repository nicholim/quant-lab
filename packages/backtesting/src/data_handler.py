from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

import pandas as pd

from .datastore import DataStore
from .events import MarketEvent
from .market_data import download_ohlcv

_OHLCV_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
_OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample an OHLCV frame to a coarser timeframe (e.g. 'W', 'ME').

    Open=first, High=max, Low=min, Close=last, Volume=sum. Only columns present
    in ``df`` are aggregated.
    """
    agg = {k: v for k, v in _OHLCV_AGG.items() if k in df.columns}
    return df.resample(rule).agg(agg).dropna(how="all")


def _normalize_ohlcv(df: pd.DataFrame, *, symbol: str) -> pd.DataFrame:
    """Coerce an arbitrary OHLCV frame to the engine's canonical shape.

    Accepts case-insensitive column names (``open``/``Open``/``OPEN``…), an
    optional ``Date``/``Datetime``/``Timestamp`` column to use as a
    DatetimeIndex, sorts by time, and validates the required OHLC columns are
    present. ``Volume`` is filled with 0 when missing so volume-agnostic
    sources (e.g. some FX/intraday feeds) still load. The returned frame has the
    exact columns/dtypes the rest of the engine (and ``YFinanceDataHandler``)
    produces, so bar/event semantics are identical across handlers.
    """
    out = df.copy()

    # Case-insensitive column resolution.
    lower = {str(c).strip().lower(): c for c in out.columns}

    # Promote a date-like column to the index if the index isn't already datetime.
    if not isinstance(out.index, pd.DatetimeIndex):
        date_col = next(
            (lower[k] for k in ("date", "datetime", "timestamp", "time") if k in lower), None
        )
        if date_col is not None:
            out = out.set_index(date_col)
            lower.pop(next(k for k, v in lower.items() if v == date_col))
        out.index = pd.to_datetime(out.index)

    rename: dict[str, str] = {}
    for canon in _OHLCV_COLS:
        key = canon.lower()
        if key in lower:
            rename[lower[key]] = canon
    out = out.rename(columns=rename)

    missing = [c for c in ("Open", "High", "Low", "Close") if c not in out.columns]
    if missing:
        raise ValueError(
            f"{symbol}: OHLCV data is missing required column(s) {missing}; "
            f"got columns {list(df.columns)}"
        )
    if "Volume" not in out.columns:
        out["Volume"] = 0.0

    out = out[_OHLCV_COLS].apply(pd.to_numeric, errors="coerce")
    out = out.sort_index()
    out.index.name = None
    return out


class DataHandler(ABC):
    """Abstract base class for data providers.

    The attribute annotations below declare the public interface that the rest
    of the framework (portfolio, sizing, execution, strategies) relies on. They
    are type declarations only; concrete subclasses set them in ``__init__``.
    """

    tickers: list[str]
    start_date: str
    end_date: str

    @abstractmethod
    def fetch(self) -> None: ...

    @abstractmethod
    def get_latest_bars(self, symbol: str, n: int = 1) -> pd.DataFrame: ...

    @abstractmethod
    def iter_bars(self) -> Iterator[MarketEvent]: ...

    @abstractmethod
    def get_current_price(self, symbol: str) -> float: ...

    @abstractmethod
    def get_resampled_bars(self, symbol: str, rule: str, n: int = 1) -> pd.DataFrame: ...

    @abstractmethod
    def get_current_bar(self, symbol: str) -> dict | None: ...

    @abstractmethod
    def get_next_open(self, symbol: str) -> float: ...


class _InMemoryDataHandler(DataHandler):
    """Shared base for handlers that serve pre-loaded ``{ticker: DataFrame}`` data.

    Implements every bar/event read method (``get_latest_bars``,
    ``iter_bars``, ``get_current_price``, ``get_resampled_bars``,
    ``get_current_bar``, ``get_next_open``) once, so all concrete handlers
    produce byte-identical bar windowing, no-look-ahead resampling and next-open
    fills. Subclasses only implement :meth:`fetch`, which must populate
    ``self._data`` (a ``{ticker: OHLCV DataFrame}`` mapping) and set
    ``self._total_bars`` — typically by calling :meth:`_finalize_data`.
    """

    def __init__(self, tickers: list[str], start_date: str, end_date: str):
        self.tickers = list(tickers)
        self.start_date = start_date
        self.end_date = end_date
        self._data: dict[str, pd.DataFrame] = {}
        self._current_idx: int = 0
        self._total_bars: int = 0

    def _finalize_data(self, frames: dict[str, pd.DataFrame]) -> None:
        """Store normalized frames and set the bar count to the shortest series."""
        self._data = frames
        self._total_bars = min(len(df) for df in frames.values()) if frames else 0

    def get_latest_bars(self, symbol: str, n: int = 1) -> pd.DataFrame:
        """Return the last N bars up to the current position."""
        if symbol not in self._data:
            raise ValueError(f"Unknown symbol: {symbol}")
        start = max(0, self._current_idx - n)
        return self._data[symbol].iloc[start : self._current_idx].copy()

    def get_current_price(self, symbol: str) -> float:
        """Return the current bar's close price (used for marking-to-market and sizing)."""
        if self._current_idx == 0:
            return 0.0
        return float(self._data[symbol].iloc[self._current_idx - 1]["Close"])

    def get_resampled_bars(self, symbol: str, rule: str, n: int = 1) -> pd.DataFrame:
        """Return the last ``n`` higher-timeframe bars (e.g. weekly 'W', monthly 'ME').

        Resamples only the bars up to and including the current bar, so there is
        no look-ahead. The most recent resampled bar may be a partial period
        (e.g. week-to-date), which is the information actually available now.
        """
        if symbol not in self._data:
            raise ValueError(f"Unknown symbol: {symbol}")
        history = self._data[symbol].iloc[: self._current_idx]
        if history.empty:
            return history
        return resample_ohlc(history, rule).iloc[-n:]

    def get_current_bar(self, symbol: str) -> dict | None:
        """Return the current bar's OHLC as a dict, or None before the first bar.

        Used to evaluate pending LIMIT/STOP orders against intrabar high/low.
        """
        if self._current_idx == 0:
            return None
        row = self._data[symbol].iloc[self._current_idx - 1]
        return {
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
        }

    def get_next_open(self, symbol: str) -> float:
        """Return the NEXT bar's open price — the realistic fill price.

        A signal is decided from the current bar's close; the order can only be
        executed at the next bar's open. Returns 0.0 when there is no next bar
        (last bar of the series), which signals "cannot fill".
        """
        if symbol not in self._data:
            raise ValueError(f"Unknown symbol: {symbol}")
        df = self._data[symbol]
        if self._current_idx >= len(df):
            return 0.0
        return float(df.iloc[self._current_idx]["Open"])

    def iter_bars(self) -> Iterator[MarketEvent]:
        """Yield MarketEvents one bar at a time, advancing the internal pointer."""
        for i in range(self._total_bars):
            self._current_idx = i + 1
            timestamp = self._data[self.tickers[0]].index[i]
            for symbol in self.tickers:
                yield MarketEvent(
                    timestamp=timestamp.to_pydatetime(),
                    symbol=symbol,
                )


class YFinanceDataHandler(_InMemoryDataHandler):
    """Data handler that fetches historical data via DuckDB cache (falls back to yfinance)."""

    def __init__(
        self,
        tickers: list[str],
        start_date: str,
        end_date: str,
        store: DataStore | None = None,
        offline: bool = False,
    ):
        super().__init__(tickers, start_date, end_date)
        self._store = store
        self._offline = offline

    def fetch(self) -> None:
        """Load data from DuckDB cache, downloading on cache miss.

        With a ``store`` the DuckDB cache is the primary layer; without one the
        no-store path goes straight through the resilient network layer
        (:func:`src.market_data.download_ohlcv`, retries/backoff/offline)."""
        frames: dict[str, pd.DataFrame] = {}
        for ticker in self.tickers:
            if self._store:
                df = self._store.fetch_ohlcv(
                    ticker, self.start_date, self.end_date, offline=self._offline
                )
            else:
                df = download_ohlcv(ticker, self.start_date, self.end_date, offline=self._offline)
            frames[ticker] = df
        self._finalize_data(frames)


class DataFrameDataHandler(_InMemoryDataHandler):
    """Data handler backed by pre-built in-memory ``{ticker: DataFrame}`` data.

    Kills the hard yfinance dependency for custom, synthetic, intraday or
    already-loaded data. Each frame must carry OHLC columns (case-insensitive;
    ``Volume`` optional, defaulted to 0) and either a DatetimeIndex or a
    date-like column (``Date``/``Datetime``/``Timestamp``/``Time``). Frames are
    normalized to the engine's canonical OHLCV shape on construction, so bar and
    event semantics are identical to :class:`YFinanceDataHandler`.

    ``start_date``/``end_date`` are optional; when omitted they are inferred
    from the data's overall index range. The frames are normalized eagerly in
    ``__init__`` and :meth:`fetch` is a no-op, so the handler is usable without
    any network access.
    """

    def __init__(
        self,
        data: dict[str, pd.DataFrame],
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        if not data:
            raise ValueError("DataFrameDataHandler requires at least one ticker frame")
        frames = {sym: _normalize_ohlcv(df, symbol=sym) for sym, df in data.items()}
        idx_min = min(df.index.min() for df in frames.values())
        idx_max = max(df.index.max() for df in frames.values())
        super().__init__(
            list(frames),
            start_date or str(idx_min.date()),
            end_date or str(idx_max.date()),
        )
        self._finalize_data(frames)

    def fetch(self) -> None:
        """No-op: frames were normalized and finalized in ``__init__``."""
        return None


class CSVDataHandler(_InMemoryDataHandler):
    """Data handler that loads OHLCV from CSV file(s) — no network, no yfinance.

    Two conventions are supported:

    * **One file per ticker** (``per_ticker=True``, the default): pass a
      directory and the handler reads ``<dir>/<TICKER>.csv`` for each ticker. A
      ``filename_template`` (default ``"{ticker}.csv"``) customizes the pattern.
    * **One combined file** (``per_ticker=False``): pass a single CSV path that
      contains a ``symbol`` (or ``ticker``) column distinguishing rows; the
      handler groups by it. Only the requested ``tickers`` are kept.

    Each CSV must have OHLC columns (case-insensitive; ``Volume`` optional) and
    a date column (``Date``/``Datetime``/``Timestamp``/``Time``) or a parseable
    first column. Rows are normalized to the engine's canonical OHLCV shape and
    optionally sliced to ``[start_date, end_date]``, so bar/event semantics
    match :class:`YFinanceDataHandler` exactly.
    """

    def __init__(
        self,
        tickers: list[str],
        path: str | Path,
        start_date: str | None = None,
        end_date: str | None = None,
        *,
        per_ticker: bool = True,
        filename_template: str = "{ticker}.csv",
        symbol_column: str = "symbol",
    ):
        super().__init__(tickers, start_date or "", end_date or "")
        self._path = Path(path)
        self._per_ticker = per_ticker
        self._filename_template = filename_template
        self._symbol_column = symbol_column

    def _slice_dates(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.start_date:
            df = df[df.index >= pd.to_datetime(self.start_date)]
        if self.end_date:
            df = df[df.index <= pd.to_datetime(self.end_date)]
        return df

    def _load_per_ticker(self) -> dict[str, pd.DataFrame]:
        if not self._path.is_dir():
            raise ValueError(
                f"per_ticker=True expects a directory; {self._path} is not a directory"
            )
        frames: dict[str, pd.DataFrame] = {}
        for ticker in self.tickers:
            fp = self._path / self._filename_template.format(ticker=ticker)
            if not fp.exists():
                raise FileNotFoundError(f"No CSV for ticker {ticker!r}: {fp}")
            frames[ticker] = self._slice_dates(_normalize_ohlcv(pd.read_csv(fp), symbol=ticker))
        return frames

    def _load_combined(self) -> dict[str, pd.DataFrame]:
        if not self._path.is_file():
            raise ValueError(f"per_ticker=False expects a file; {self._path} is not a file")
        raw = pd.read_csv(self._path)
        lower = {str(c).strip().lower(): c for c in raw.columns}
        sym_col = lower.get(self._symbol_column.lower()) or lower.get("ticker")
        if sym_col is None:
            raise ValueError(
                f"Combined CSV must have a {self._symbol_column!r} (or 'ticker') column; "
                f"got {list(raw.columns)}"
            )
        frames: dict[str, pd.DataFrame] = {}
        for ticker in self.tickers:
            sub = raw[raw[sym_col].astype(str) == str(ticker)].drop(columns=[sym_col])
            if sub.empty:
                raise ValueError(f"No rows for ticker {ticker!r} in {self._path}")
            frames[ticker] = self._slice_dates(_normalize_ohlcv(sub, symbol=ticker))
        return frames

    def fetch(self) -> None:
        """Read the CSV file(s), normalize, date-slice, and finalize."""
        frames = self._load_per_ticker() if self._per_ticker else self._load_combined()
        self._finalize_data(frames)
