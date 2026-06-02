import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379"))
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "postgresql://user:password@localhost:5432/marketdata"
        )
    )
    ws_url: str = field(
        default_factory=lambda: os.getenv("WS_URL", "wss://stream.binance.com:9443/ws")
    )
    # Which exchange adapter to stream from. "binance" (default, unchanged
    # behavior) embeds streams in the WS_URL path; "coinbase" connects to the
    # Coinbase Exchange matches feed and subscribes by product. Both are keyless
    # public market data. See src/adapters.py.
    exchange: str = field(default_factory=lambda: os.getenv("EXCHANGE", "binance").lower())
    symbols: list[str] = field(
        default_factory=lambda: os.getenv("SYMBOLS", "btcusdt,ethusdt").split(",")
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    batch_size: int = field(default_factory=lambda: int(os.getenv("BATCH_SIZE", "100")))
    flush_interval: float = field(
        default_factory=lambda: float(os.getenv("FLUSH_INTERVAL_SECONDS", "5"))
    )
    # Backpressure cap on the in-memory trade buffer between the WS stream and
    # storage. If a slow/stalled sink lets the buffer reach this many trades,
    # the pipeline awaits an inline flush before accepting more (blocking the
    # consumer) so the buffer can never grow unboundedly and OOM the worker.
    # Must be >= batch_size to be meaningful; default is 10x batch_size.
    max_buffer_size: int = field(default_factory=lambda: int(os.getenv("MAX_BUFFER_SIZE", "1000")))
    # Which storage sink the pipeline persists to. "timescale" (default,
    # unchanged behavior) needs an external TimescaleDB; "duckdb" writes to a
    # local file with no external DB or network — runnable on free/cloud infra.
    storage_backend: str = field(
        default_factory=lambda: os.getenv("STORAGE_BACKEND", "timescale").lower()
    )
    # Path to the DuckDB file when storage_backend == "duckdb".
    duckdb_path: str = field(
        default_factory=lambda: os.getenv("DUCKDB_PATH", "data/marketdata.duckdb")
    )
