"""File-based storage backend backed by DuckDB.

This is a drop-in alternative to :class:`~src.storage.TimeSeriesStorage` that
needs no external database and no network — it writes the same normalized
``trades`` / ``ohlcv`` tables to a local DuckDB file (or an in-memory DB). This
decouples the demo from TimescaleDB so the pipeline is runnable on free/cloud
infra (e.g. Render, where the managed Postgres can't host the ``timescaledb``
extension) and locally with zero setup.

The schema mirrors the TimescaleDB layout exactly so reads round-trip with the
same column order and types:

* ``trades``: ``time, symbol, price, quantity, side, exchange``
* ``ohlcv``:  ``time, symbol, open, high, low, close, volume, trade_count, interval``

DuckDB's Python API is synchronous, so every DB call is run on a worker thread
via :func:`asyncio.to_thread`, keeping the same ``async`` surface the pipeline
expects (and not blocking the event loop). Optionally the on-disk tables can be
exported to Parquet via :meth:`export_parquet` for downstream research tooling.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import duckdb

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    time        TIMESTAMPTZ NOT NULL,
    symbol      VARCHAR     NOT NULL,
    price       DOUBLE      NOT NULL,
    quantity    DOUBLE      NOT NULL,
    side        VARCHAR     NOT NULL,
    exchange    VARCHAR     NOT NULL DEFAULT 'binance'
);

CREATE TABLE IF NOT EXISTS ohlcv (
    time        TIMESTAMPTZ NOT NULL,
    symbol      VARCHAR     NOT NULL,
    open        DOUBLE      NOT NULL,
    high        DOUBLE      NOT NULL,
    low         DOUBLE      NOT NULL,
    close       DOUBLE      NOT NULL,
    volume      DOUBLE      NOT NULL,
    trade_count INTEGER     NOT NULL DEFAULT 0,
    interval    VARCHAR     NOT NULL DEFAULT '1m'
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades (symbol, "time");
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time ON ohlcv (symbol, "time");
"""

_TRADE_COLUMNS = ("time", "symbol", "price", "quantity", "side", "exchange")
_OHLCV_COLUMNS = ("time", "symbol", "open", "high", "low", "close", "volume", "trade_count")


class DuckDBStorage:
    """Local DuckDB sink for trades and OHLCV bars (no external DB, no network).

    ``database_path`` is a filesystem path to a ``.duckdb`` file (created on
    connect). Pass ``":memory:"`` for an ephemeral in-process DB (handy for
    tests). The public method surface matches the ``StorageBackend`` protocol
    so the pipeline can use it interchangeably with ``TimeSeriesStorage``.
    """

    def __init__(self, database_path: str = "marketdata.duckdb") -> None:
        self.database_path = database_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    async def connect(self) -> None:
        if self.database_path not in (":memory:", "") and (
            parent := os.path.dirname(self.database_path)
        ):
            os.makedirs(parent, exist_ok=True)
        self._conn = await asyncio.to_thread(duckdb.connect, self.database_path)
        logger.info("DuckDB connected (%s)", self.database_path)

    async def disconnect(self) -> None:
        if self._conn is not None:
            conn = self._conn
            self._conn = None
            await asyncio.to_thread(conn.close)
            logger.info("DuckDB disconnected")

    async def init_schema(self) -> None:
        conn = self._require_conn()
        await asyncio.to_thread(conn.execute, SCHEMA_SQL)
        logger.info("DuckDB schema initialized")

    async def insert_trades(self, trades: list[dict]) -> None:
        if not trades:
            return
        conn = self._require_conn()
        rows = [
            (
                t["timestamp"],
                t["symbol"],
                t["price"],
                t["quantity"],
                t["side"],
                t["exchange"],
            )
            for t in trades
        ]
        await asyncio.to_thread(self._executemany_trades, conn, rows)
        logger.debug("Inserted %d trades into DuckDB", len(trades))

    async def insert_ohlcv(self, bar: dict) -> None:
        conn = self._require_conn()
        row = (
            bar["timestamp"],
            bar["symbol"],
            bar["open"],
            bar["high"],
            bar["low"],
            bar["close"],
            bar["volume"],
            bar["trade_count"],
            bar["interval"],
        )
        await asyncio.to_thread(self._execute_ohlcv, conn, row)

    async def query_trades(
        self, symbol: str, start: datetime, end: datetime, limit: int = 10000
    ) -> list[dict]:
        conn = self._require_conn()
        sql = """
            SELECT time, symbol, price, quantity, side, exchange
            FROM trades
            WHERE symbol = ? AND time >= ? AND time < ?
            ORDER BY time DESC
            LIMIT ?
        """
        records = await asyncio.to_thread(self._fetch, conn, sql, [symbol, start, end, limit])
        return [dict(zip(_TRADE_COLUMNS, r, strict=True)) for r in records]

    async def query_ohlcv(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict]:
        conn = self._require_conn()
        sql = """
            SELECT time, symbol, open, high, low, close, volume, trade_count
            FROM ohlcv
            WHERE symbol = ? AND interval = ? AND time >= ? AND time < ?
            ORDER BY time ASC
        """
        records = await asyncio.to_thread(self._fetch, conn, sql, [symbol, interval, start, end])
        return [dict(zip(_OHLCV_COLUMNS, r, strict=True)) for r in records]

    async def export_parquet(self, output_dir: str) -> dict[str, str]:
        """Export the trades and ohlcv tables to Parquet files in ``output_dir``.

        Returns a mapping of table name -> written file path. Useful for handing
        the captured data to research/backtesting tooling without a live DB.
        """
        conn = self._require_conn()
        os.makedirs(output_dir, exist_ok=True)
        paths = {
            "trades": os.path.join(output_dir, "trades.parquet"),
            "ohlcv": os.path.join(output_dir, "ohlcv.parquet"),
        }
        await asyncio.to_thread(self._export_parquet, conn, paths)
        logger.info("Exported DuckDB tables to Parquet in %s", output_dir)
        return paths

    # --- sync helpers (run on a worker thread) ----------------------------

    @staticmethod
    def _executemany_trades(conn: duckdb.DuckDBPyConnection, rows: list[tuple]) -> None:
        conn.executemany(
            "INSERT INTO trades (time, symbol, price, quantity, side, exchange) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )

    @staticmethod
    def _execute_ohlcv(conn: duckdb.DuckDBPyConnection, row: tuple) -> None:
        conn.execute(
            "INSERT INTO ohlcv "
            "(time, symbol, open, high, low, close, volume, trade_count, interval) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    @staticmethod
    def _fetch(conn: duckdb.DuckDBPyConnection, sql: str, params: list) -> list[tuple]:
        return conn.execute(sql, params).fetchall()

    @staticmethod
    def _export_parquet(conn: duckdb.DuckDBPyConnection, paths: dict[str, str]) -> None:
        for table, path in paths.items():
            conn.execute(f"COPY {table} TO '{path}' (FORMAT PARQUET)")

    def _require_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            raise RuntimeError("connect() must be called first")
        return self._conn
