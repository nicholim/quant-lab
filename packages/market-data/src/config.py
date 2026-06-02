import os
from dataclasses import dataclass, field

_dotenv_loaded = False


def _load_dotenv_once() -> None:
    """Load a local ``.env`` once so config/secrets can live in a file.

    Lets ``REDIS_URL`` / ``DATABASE_URL`` / ``EXCHANGE`` / ``STORAGE_BACKEND``
    (and any other config) be set via a ``.env`` in the working dir or a parent
    (mirrors the options-pricing ``_load_dotenv_once`` pattern). python-dotenv
    does NOT override variables already present in the environment, so an
    exported (real) value always wins over ``.env``. Idempotent — repeated calls
    are no-ops, so re-importing config never re-clobbers a per-test environment.
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


# Pick up a local .env at import time, before any Config field reads an env var.
_load_dotenv_once()


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
