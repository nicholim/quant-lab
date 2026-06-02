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
    symbols: list[str] = field(
        default_factory=lambda: os.getenv("SYMBOLS", "btcusdt,ethusdt").split(",")
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    batch_size: int = field(default_factory=lambda: int(os.getenv("BATCH_SIZE", "100")))
    flush_interval: float = field(
        default_factory=lambda: float(os.getenv("FLUSH_INTERVAL_SECONDS", "5"))
    )
