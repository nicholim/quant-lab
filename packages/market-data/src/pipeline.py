import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime, timedelta

from .adapters import BinanceAdapter, ExchangeAdapter, build_adapter
from .cache import RedisCache
from .config import Config
from .normalizer import TickNormalizer
from .storage import TimeSeriesStorage
from .storage_backend import StorageBackend
from .websocket_client import MarketDataClient

logger = logging.getLogger(__name__)


def build_exchange_adapter(config: Config) -> ExchangeAdapter:
    """Select the exchange adapter from config.

    Defaults to Binance (``EXCHANGE`` unset / ``"binance"``) so existing
    deployments are unchanged. The Binance adapter is built from ``WS_URL`` so
    the connection URL stays byte-identical to the original pipeline; other
    venues (e.g. ``"coinbase"``) use their own documented default endpoint.
    """
    if config.exchange == "binance":
        return BinanceAdapter(config.ws_url)
    return build_adapter(config.exchange)


def build_storage(config: Config) -> StorageBackend:
    """Select the storage backend from config.

    Defaults to TimescaleDB (``STORAGE_BACKEND`` unset / ``"timescale"``) so
    existing deployments are unchanged. ``"duckdb"`` selects the local
    file-based sink, which needs no external DB or network.
    """
    backend = config.storage_backend
    if backend == "timescale":
        return TimeSeriesStorage(config.database_url)
    if backend == "duckdb":
        # Imported lazily so the duckdb dependency is only required when chosen.
        from .duckdb_storage import DuckDBStorage

        return DuckDBStorage(config.duckdb_path)
    raise ValueError(f"Unknown STORAGE_BACKEND {backend!r}; expected 'timescale' or 'duckdb'.")


class Pipeline:
    """Main pipeline orchestrating data flow from WebSocket to storage."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.adapter: ExchangeAdapter = build_exchange_adapter(config)
        self.client = MarketDataClient(config.ws_url, adapter=self.adapter)
        self.normalizer = TickNormalizer(self.adapter)
        self.cache = RedisCache(config.redis_url)
        self.storage: StorageBackend = build_storage(config)

        self._trade_buffer: list[dict] = []
        self._flush_task: asyncio.Task | None = None
        self._running = False
        # Backpressure accounting: how many trades we were forced to drop
        # because the sink stayed unreachable while the buffer was at its cap.
        self._dropped_trades = 0

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
            if self.config.storage_backend == "duckdb":
                logger.error(
                    "Could not open/initialize the DuckDB store at %s (%s: %s). "
                    "Set DUCKDB_PATH to a writable path and restart.",
                    self.config.duckdb_path,
                    type(e).__name__,
                    e,
                )
            else:
                logger.error(
                    "Could not connect to/initialize TimescaleDB at %s (%s: %s). "
                    "Set DATABASE_URL to a reachable TimescaleDB instance, or set "
                    "STORAGE_BACKEND=duckdb to use a local file store, and restart.",
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

        # Emit any final in-progress OHLCV bars before we tear down storage:
        # the last minute of each symbol is never closed by a later trade, so
        # without this flush that bar would be silently dropped.
        for bar in self.normalizer.flush_all():
            try:
                await self.storage.insert_ohlcv(asdict(bar))
            except Exception as e:  # noqa: BLE001 - best-effort on shutdown
                logger.error(f"Failed to persist final OHLCV bar for {bar.symbol}: {e}")

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

    async def _on_message(self, raw: dict | list) -> None:
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

        # Buffer for batch DB insert.
        self._trade_buffer.append(trade_dict)
        if len(self._trade_buffer) >= self.config.batch_size:
            await self._flush_trades()

        # Backpressure: if the sink is slow/stalled the buffer can keep growing
        # past batch_size (e.g. a failed flush re-adds its batch). Cap it so a
        # dead sink can't OOM the worker. Primary policy is to BLOCK — await an
        # inline flush, which back-pressures the WS consumer. Only if the sink
        # is still unreachable AFTER that flush (buffer still at the cap) do we
        # drop the oldest trades to enforce a hard memory bound, and we log a
        # running count so the loss is never silent.
        await self._apply_backpressure()

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

    async def _apply_backpressure(self) -> None:
        """Bound the in-memory trade buffer to ``config.max_buffer_size``.

        Policy (block first, drop only as a last resort, never silently):

        1. If the buffer has reached the cap, ``await`` an inline flush. This
           back-pressures the WS consumer (we don't accept more trades until
           the sink drains) and is lossless when the sink is healthy.
        2. If the buffer is STILL at/over the cap after that flush (the sink is
           unreachable and ``_flush_trades`` re-added its batch), drop the
           oldest trades down to the cap to guarantee a hard memory bound, and
           log a running dropped-count so the loss is visible, not silent.
        """
        cap = self.config.max_buffer_size
        if len(self._trade_buffer) < cap:
            return

        logger.warning(
            "Trade buffer reached backpressure cap (%d); awaiting an inline "
            "flush before accepting more trades.",
            cap,
        )
        await self._flush_trades()

        overflow = len(self._trade_buffer) - cap
        if overflow > 0:
            # Sink still down: enforce the hard bound by dropping oldest.
            del self._trade_buffer[:overflow]
            self._dropped_trades += overflow
            logger.warning(
                "Storage sink still unreachable with buffer at cap; dropped %d "
                "oldest trade(s) to stay within MAX_BUFFER_SIZE=%d "
                "(total dropped this run: %d).",
                overflow,
                cap,
                self._dropped_trades,
            )

    async def _periodic_flush(self) -> None:
        """Periodically flush trade buffer to prevent stale data."""
        while self._running:
            await asyncio.sleep(self.config.flush_interval)
            await self._flush_trades()

    async def replay(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        source: str = "trades",
        interval: str = "1m",
        page_size: int = 10000,
    ) -> AsyncIterator[dict]:
        """Stream stored records for ``symbol`` back out in timestamp order.

        Turns the ingest daemon into a research feeder: it replays what was
        persisted (by EITHER backend — it only uses the ``StorageBackend``
        read API) so downstream tooling can consume historical trades or bars
        as if they were live.

        ``source="trades"`` yields trade dicts (``time, symbol, price,
        quantity, side, exchange``) oldest-first; ``source="ohlcv"`` yields bar
        dicts (``time, symbol, open, high, low, close, volume, trade_count``)
        oldest-first for ``interval``. The ``[start, end)`` window is inclusive
        of ``start`` and exclusive of ``end`` (matching the backend read API).

        The Protocol's ``query_trades`` returns at most ``limit`` rows
        *most-recent-first* (it gives the newest slice of the window, not the
        oldest), so to stream an arbitrarily large window we page BACKWARD in
        ``page_size`` chunks — newest chunk first, narrowing ``end`` to the
        oldest timestamp seen — accumulate the chunks, then yield them sorted
        ascending so the consumer sees a clean oldest-first replay.

        Because the read API has no offset/tiebreak, ``page_size`` must exceed
        the worst-case number of trades sharing one exact timestamp; otherwise
        the surplus same-timestamp rows are unreachable (the default 10000 is
        well above any realistic single-millisecond burst).
        """
        if source == "ohlcv":
            for bar in await self.storage.query_ohlcv(symbol, interval, start, end):
                yield bar
            return
        if source != "trades":
            raise ValueError(f"Unknown replay source {source!r}; expected 'trades' or 'ohlcv'.")

        collected: list[dict] = []
        upper = end
        while upper > start:
            page = await self.storage.query_trades(symbol, start, upper, limit=page_size)
            if not page:
                break
            collected.extend(page)
            if len(page) < page_size:
                break
            # Backend gave the newest page in [start, upper); step the upper
            # bound down to (just past) the oldest timestamp we just read to
            # fetch the next older page. We keep the boundary timestamp itself
            # in range (``+1µs``) so rows sharing it that didn't fit in this
            # page are not skipped; the post-loop dedup removes the re-reads.
            oldest_ts = min(r["time"] for r in page)
            next_upper = oldest_ts + timedelta(microseconds=1)
            if next_upper >= upper:
                # No downward progress (the whole page is one timestamp); stop
                # to avoid an infinite loop. We have all rows at/above it.
                break
            upper = next_upper

        # Yield oldest-first, de-duplicated on the (time, price, quantity, side)
        # tuple in case a boundary timestamp was read in two adjacent pages.
        collected.sort(key=lambda r: r["time"])
        seen: set[tuple] = set()
        for record in collected:
            ident = (record["time"], record["price"], record["quantity"], record["side"])
            if ident in seen:
                continue
            seen.add(ident)
            yield record
