import logging
from datetime import datetime

import asyncpg

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT        NOT NULL,
    price       DOUBLE PRECISION NOT NULL,
    quantity    DOUBLE PRECISION NOT NULL,
    side        TEXT        NOT NULL,
    exchange    TEXT        NOT NULL DEFAULT 'binance'
);

SELECT create_hypertable('trades', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS ohlcv (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT        NOT NULL,
    open        DOUBLE PRECISION NOT NULL,
    high        DOUBLE PRECISION NOT NULL,
    low         DOUBLE PRECISION NOT NULL,
    close       DOUBLE PRECISION NOT NULL,
    volume      DOUBLE PRECISION NOT NULL,
    trade_count INTEGER     NOT NULL DEFAULT 0,
    interval    TEXT        NOT NULL DEFAULT '1m'
);

SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time ON ohlcv (symbol, time DESC);
"""


class TimeSeriesStorage:
    """TimescaleDB storage layer for trades and OHLCV bars."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self.database_url, min_size=2, max_size=10)
        logger.info("Database connected")

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            logger.info("Database disconnected")

    async def init_schema(self) -> None:
        """Create tables and hypertables if they don't exist."""
        assert self._pool is not None, "connect() must be called first"
        async with self._pool.acquire() as conn:
            await conn.execute(SCHEMA_SQL)
        logger.info("Schema initialized")

    async def insert_trades(self, trades: list[dict]) -> None:
        """Batch insert trades using COPY for high throughput."""
        if not trades:
            return
        assert self._pool is not None, "connect() must be called first"
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO trades (time, symbol, price, quantity, side, exchange)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                [
                    (
                        t["timestamp"],
                        t["symbol"],
                        t["price"],
                        t["quantity"],
                        t["side"],
                        t["exchange"],
                    )
                    for t in trades
                ],
            )
        logger.debug(f"Inserted {len(trades)} trades")

    async def insert_ohlcv(self, bar: dict) -> None:
        """Insert an OHLCV bar."""
        assert self._pool is not None, "connect() must be called first"
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ohlcv
                    (time, symbol, open, high, low, close, volume, trade_count, interval)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
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

    async def query_trades(
        self, symbol: str, start: datetime, end: datetime, limit: int = 10000
    ) -> list[dict]:
        """Query trades within a time range."""
        assert self._pool is not None, "connect() must be called first"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT time, symbol, price, quantity, side, exchange
                FROM trades
                WHERE symbol = $1 AND time >= $2 AND time < $3
                ORDER BY time DESC
                LIMIT $4
                """,
                symbol,
                start,
                end,
                limit,
            )
            return [dict(r) for r in rows]

    async def query_ohlcv(
        self, symbol: str, interval: str, start: datetime, end: datetime
    ) -> list[dict]:
        """Query OHLCV bars within a time range."""
        assert self._pool is not None, "connect() must be called first"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT time, symbol, open, high, low, close, volume, trade_count
                FROM ohlcv
                WHERE symbol = $1 AND interval = $2 AND time >= $3 AND time < $4
                ORDER BY time ASC
                """,
                symbol,
                interval,
                start,
                end,
            )
            return [dict(r) for r in rows]
