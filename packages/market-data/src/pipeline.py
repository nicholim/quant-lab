import asyncio
import logging
from dataclasses import asdict

from .cache import RedisCache
from .config import Config
from .normalizer import TickNormalizer
from .storage import TimeSeriesStorage
from .websocket_client import MarketDataClient

logger = logging.getLogger(__name__)


class Pipeline:
    """Main pipeline orchestrating data flow from WebSocket to storage."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = MarketDataClient(config.ws_url)
        self.normalizer = TickNormalizer()
        self.cache = RedisCache(config.redis_url)
        self.storage = TimeSeriesStorage(config.database_url)

        self._trade_buffer: list[dict] = []
        self._flush_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Initialize connections and start the pipeline.

        Redis and TimescaleDB are hard dependencies for ingestion: without them
        there is nowhere to cache or persist trades. If either is unreachable we
        log a single actionable line (which env var to fix) and re-raise, so the
        worker exits non-zero with a clean message instead of a raw multi-frame
        connection traceback. On Render the worker then restarts and retries.
        """
        self._running = True
        logger.info(f"Starting pipeline for symbols: {self.config.symbols}")

        try:
            await self.cache.connect()
        except Exception as e:
            logger.error(
                "Could not connect to Redis at %s (%s: %s). "
                "Set REDIS_URL to a reachable instance and restart.",
                self.config.redis_url,
                type(e).__name__,
                e,
            )
            raise

        try:
            await self.storage.connect()
            await self.storage.init_schema()
        except Exception as e:
            logger.error(
                "Could not connect to/initialize TimescaleDB at %s (%s: %s). "
                "Set DATABASE_URL to a reachable TimescaleDB instance and restart.",
                self.config.database_url,
                type(e).__name__,
                e,
            )
            raise

        self.client.on_message(self._on_message)
        self._flush_task = asyncio.create_task(self._periodic_flush())

        await self.client.connect(self.config.symbols)

    async def stop(self) -> None:
        """Gracefully shut down all components."""
        self._running = False
        logger.info("Stopping pipeline...")

        await self.client.disconnect()

        # Flush remaining trades
        if self._trade_buffer:
            await self._flush_trades()

        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass

        await self.cache.disconnect()
        await self.storage.disconnect()
        logger.info("Pipeline stopped")

    async def _on_message(self, raw: dict) -> None:
        """Process incoming WebSocket message."""
        trade = self.normalizer.normalize_trade(raw)
        if trade is None:
            return

        trade_dict = asdict(trade)
        trade_dict["timestamp"] = trade.timestamp

        # Update cache
        await self.cache.set_latest_price(trade.symbol, trade.price, trade.timestamp)
        await self.cache.push_trade(trade.symbol, trade_dict)
        await self.cache.publish(f"trades:{trade.symbol}", trade_dict)

        # Buffer for batch DB insert
        self._trade_buffer.append(trade_dict)
        if len(self._trade_buffer) >= self.config.batch_size:
            await self._flush_trades()

        # Check for OHLCV bar completion
        bar = self.normalizer.accumulate_trade(trade)
        if bar:
            await self.storage.insert_ohlcv(asdict(bar))
            logger.debug(
                f"OHLCV bar: {bar.symbol} O={bar.open} H={bar.high} L={bar.low} C={bar.close}"
            )

    async def _flush_trades(self) -> None:
        """Flush buffered trades to storage."""
        if not self._trade_buffer:
            return
        batch = self._trade_buffer.copy()
        self._trade_buffer.clear()
        try:
            await self.storage.insert_trades(batch)
            logger.info(f"Flushed {len(batch)} trades to storage")
        except Exception as e:
            logger.error(f"Failed to flush trades: {e}")
            self._trade_buffer.extend(batch)  # re-add on failure

    async def _periodic_flush(self) -> None:
        """Periodically flush trade buffer to prevent stale data."""
        while self._running:
            await asyncio.sleep(self.config.flush_interval)
            await self._flush_trades()
