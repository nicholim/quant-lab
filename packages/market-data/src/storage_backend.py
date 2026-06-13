"""Pluggable storage-backend contract.

The pipeline persists normalized trades and OHLCV bars through a small async
surface. Historically that surface was hardwired to TimescaleDB
(:class:`~src.storage.TimeSeriesStorage`), which means a deploy needs an
external Timescale instance (Render's managed Postgres can't host the
``timescaledb`` extension). This module captures that exact surface as a
:class:`StorageBackend` protocol so alternative sinks — e.g. the file-based
:class:`~src.duckdb_storage.DuckDBStorage` — can be dropped in interchangeably
with zero external infra.

Both implementations expose the identical normalized schema:

* ``trades``: ``time, symbol, price, quantity, side, exchange``
* ``ohlcv``:  ``time, symbol, open, high, low, close, volume, trade_count, interval``
* ``book``:   ``time, symbol, bids, asks, exchange`` (L2 depth; bids/asks JSON)
* ``bar_features``: ``time, symbol, buy_volume, sell_volume, imbalance, vwap,
  interval`` (opt-in trade-flow enrichment; separate table so ``ohlcv`` rows
  stay byte-identical when the feature is off)
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """The async storage surface the pipeline depends on.

    Any object implementing these coroutines can be used as the pipeline's
    ``storage`` attribute. The write methods (``insert_trades`` / ``insert_ohlcv``)
    take the same plain ``dict`` payloads the normalizer produces via
    ``dataclasses.asdict`` plus a ``timestamp`` key, so the trade dict keys are
    ``timestamp, symbol, price, quantity, side, exchange`` and the bar dict keys
    are ``timestamp, symbol, open, high, low, close, volume, interval, trade_count``.
    """

    async def connect(self) -> None:
        """Open the backing connection / file handle."""

    async def disconnect(self) -> None:
        """Close the backing connection / file handle (no-op if never connected)."""

    async def init_schema(self) -> None:
        """Create the trades/ohlcv tables (idempotent)."""

    async def insert_trades(self, trades: list[dict]) -> None:
        """Persist a batch of normalized trade dicts (empty list is a no-op)."""

    async def insert_ohlcv(self, bar: dict) -> None:
        """Persist a single normalized OHLCV bar dict."""

    async def insert_book(self, book: dict) -> None:
        """Persist a single normalized L2 depth snapshot dict.

        The dict keys are ``timestamp, symbol, bids, asks, exchange`` where
        ``bids``/``asks`` are lists of ``{"price", "quantity"}`` dicts (as
        produced from a :class:`~src.normalizer.BookUpdate` via ``asdict``).
        OPT-IN: only exercised when a depth feed is enabled; backends store the
        levels as JSON so the schema stays a single wide row per snapshot.
        """

    async def insert_bar_features(self, features: dict) -> None:
        """Persist one bar-features row (opt-in trade-flow enrichment).

        The dict keys are ``timestamp, symbol, buy_volume, sell_volume,
        imbalance, vwap, interval`` (built by the pipeline from a
        :class:`~src.normalizer.BarFeatures` plus the matching bar's
        symbol/timestamp/interval). OPT-IN: only exercised when
        ``ENABLE_BAR_FEATURES`` is set.
        """

    async def query_bar_features(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict]:
        """Read bar-features rows for ``symbol``/``interval`` in ``[start, end)`` (oldest first)."""

    async def query_book(
        self, symbol: str, start: datetime, end: datetime, limit: int = 10000
    ) -> list[dict]:
        """Read L2 depth snapshots for ``symbol`` in ``[start, end)`` (most recent first)."""

    async def query_trades(
        self, symbol: str, start: datetime, end: datetime, limit: int = 10000
    ) -> list[dict]:
        """Read trades for ``symbol`` in ``[start, end)`` (most recent first)."""

    async def query_ohlcv(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict]:
        """Read OHLCV bars for ``symbol``/``interval`` in ``[start, end)`` (oldest first)."""
