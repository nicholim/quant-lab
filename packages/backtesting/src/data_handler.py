from abc import ABC, abstractmethod
from collections.abc import Iterator

import pandas as pd

from .datastore import DataStore
from .events import MarketEvent
from .market_data import download_ohlcv

_OHLCV_AGG = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample an OHLCV frame to a coarser timeframe (e.g. 'W', 'ME').

    Open=first, High=max, Low=min, Close=last, Volume=sum. Only columns present
    in ``df`` are aggregated.
    """
    agg = {k: v for k, v in _OHLCV_AGG.items() if k in df.columns}
    return df.resample(rule).agg(agg).dropna(how="all")


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


class YFinanceDataHandler(DataHandler):
    """Data handler that fetches historical data via DuckDB cache (falls back to yfinance)."""

    def __init__(
        self,
        tickers: list[str],
        start_date: str,
        end_date: str,
        store: DataStore | None = None,
        offline: bool = False,
    ):
        self.tickers = tickers
        self.start_date = start_date
        self.end_date = end_date
        self._store = store
        self._offline = offline
        self._data: dict[str, pd.DataFrame] = {}
        self._current_idx: int = 0
        self._total_bars: int = 0

    def fetch(self) -> None:
        """Load data from DuckDB cache, downloading on cache miss.

        With a ``store`` the DuckDB cache is the primary layer; without one the
        no-store path goes straight through the resilient network layer
        (:func:`src.market_data.download_ohlcv`, retries/backoff/offline)."""
        for ticker in self.tickers:
            if self._store:
                df = self._store.fetch_ohlcv(
                    ticker, self.start_date, self.end_date, offline=self._offline
                )
            else:
                df = download_ohlcv(ticker, self.start_date, self.end_date, offline=self._offline)
            self._data[ticker] = df
        self._total_bars = min(len(df) for df in self._data.values())

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
