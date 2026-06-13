import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime, timedelta

from .adapters import (
    BinanceAdapter,
    BinanceDepthAdapter,
    DepthAdapter,
    ExchangeAdapter,
    build_adapter,
    build_depth_adapter,
    supports_depth,
)
from .cache import RedisCache
from .config import Config
from .features import compute_book_features
from .normalizer import OHLCVBar, TickNormalizer
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


def build_depth_adapter_for(config: Config) -> DepthAdapter | None:
    """Select the opt-in L2 depth adapter from config, or ``None``.

    Returns ``None`` (depth feed off) unless ``ENABLE_DEPTH`` is set AND the
    selected exchange ships a depth adapter. Binance is built from ``WS_URL`` so
    the depth stream uses the same host as the trades stream; a single-symbol
    Binance connection is given the symbol hint (its partial-depth payload has
    no symbol field).
    """
    if not config.enable_depth or not supports_depth(config.exchange):
        return None
    if config.exchange == "binance":
        adapter = BinanceDepthAdapter(config.ws_url)
        if len(config.symbols) == 1:
            adapter.with_symbol_hint(config.symbols[0])
        return adapter
    return build_depth_adapter(config.exchange)


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
        # OPT-IN L2 depth feed: a SECOND adapter + websocket connection, only
        # built when ENABLE_DEPTH is set and the exchange supports depth. When
        # off (the default) the depth attributes are None and nothing changes.
        self.depth_adapter: DepthAdapter | None = build_depth_adapter_for(config)
        self.normalizer = TickNormalizer(
            self.adapter,
            self.depth_adapter,
            enable_bar_features=config.enable_bar_features,
        )
        self.depth_client: MarketDataClient | None = None
        if self.depth_adapter is not None:
            self.depth_client = MarketDataClient(config.ws_url, adapter=self.depth_adapter)
        self.cache = RedisCache(config.redis_url)
        self.storage: StorageBackend = build_storage(config)

        self._trade_buffer: list[dict] = []
        self._flush_task: asyncio.Task | None = None
        self._depth_task: asyncio.Task | None = None
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

        # OPT-IN depth: run the second connection concurrently with trades.
        if self.depth_client is not None:
            logger.info("L2 depth feed enabled for symbols: %s", self.config.symbols)
            self.depth_client.on_message(self._on_depth_message)
            self._depth_task = asyncio.create_task(self.depth_client.connect(self.config.symbols))
            # Surface an unexpected depth-connection crash instead of letting it
            # die as a silent "Task exception was never retrieved" warning. The
            # trades feed keeps running regardless — depth is opt-in/best-effort.
            self._depth_task.add_done_callback(self._on_depth_task_done)

        await self.client.connect(self.config.symbols)

    def _on_depth_task_done(self, task: asyncio.Task) -> None:
        """Log if the opt-in depth connection crashed unexpectedly.

        Cancellation during :meth:`stop` is expected and ignored; any other
        exception is retrieved (so asyncio does not warn) and logged so an
        operator notices the depth feed died while trades kept flowing.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("L2 depth feed stopped unexpectedly: %s: %s", type(exc).__name__, exc)

    async def stop(self) -> None:
        """Gracefully shut down all components."""
        self._running = False
        logger.info("Stopping pipeline...")

        await self.client.disconnect()

        # Tear down the opt-in depth connection (if running) alongside trades.
        if self.depth_client is not None:
            await self.depth_client.disconnect()
        if self._depth_task is not None:
            self._depth_task.cancel()
            try:
                await self._depth_task
            except asyncio.CancelledError:
                pass

        # Emit any final in-progress OHLCV bars before we tear down storage:
        # the last minute of each symbol is never closed by a later trade, so
        # without this flush that bar would be silently dropped.
        for bar in self.normalizer.flush_all():
            try:
                await self.storage.insert_ohlcv(asdict(bar))
            except Exception as e:  # noqa: BLE001 - best-effort on shutdown
                logger.error(f"Failed to persist final OHLCV bar for {bar.symbol}: {e}")
            try:
                await self._emit_bar_features(bar)
            except Exception as e:  # noqa: BLE001 - best-effort on shutdown
                logger.error(f"Failed to persist final bar features for {bar.symbol}: {e}")

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
            # OPT-IN trade-flow enrichment: pop_bar_features returns None when
            # ENABLE_BAR_FEATURES is off, so the default path is byte-identical.
            await self._emit_bar_features(bar)

    async def _emit_bar_features(self, bar: OHLCVBar) -> None:
        """Publish + persist the trade-flow features for a just-emitted bar.

        No-op (a single dict pop returning ``None``) when bar features are
        disabled — the default trades-only path stays byte-identical.
        """
        features = self.normalizer.pop_bar_features(bar.symbol)
        if features is None:
            return
        feat_dict = asdict(features)
        feat_dict["symbol"] = bar.symbol
        feat_dict["timestamp"] = bar.timestamp
        feat_dict["interval"] = bar.interval
        await self.cache.publish(f"barfeat:{bar.symbol}", feat_dict)
        await self.storage.insert_bar_features(feat_dict)

    async def _on_depth_message(self, raw: dict | list) -> None:
        """Process an incoming L2 depth message (opt-in depth feed).

        Mirrors :meth:`_on_message` for the book: normalize -> cache the latest
        snapshot -> publish on ``book:<symbol>`` -> persist the snapshot. Depth
        snapshots are persisted one-at-a-time (each is a self-contained top-N
        book, low cardinality at the 100 ms cadence) rather than batch-buffered
        like trades, keeping this path independent of the trade buffer /
        backpressure machinery.
        """
        book = self.normalizer.normalize_depth(raw)
        if book is None:
            return

        book_dict = asdict(book)
        book_dict["timestamp"] = book.timestamp

        await self.cache.set_book(book.symbol, book_dict)
        await self.cache.publish(f"book:{book.symbol}", book_dict)
        try:
            await self.storage.insert_book(book_dict)
        except Exception as e:  # noqa: BLE001 - best-effort; don't kill the feed
            logger.error(f"Failed to persist depth snapshot for {book.symbol}: {e}")

        # Snapshot-level book features (mid/microprice, spread, depth
        # imbalance — NOT event-level OFI; see src/features.py). Computed for
        # every snapshot on the opt-in depth path: cache the latest under
        # bookfeat:<symbol> and publish for live consumers. Best-effort —
        # a cache hiccup must not kill the depth feed.
        feat_dict = asdict(compute_book_features(book))
        feat_dict["symbol"] = book.symbol
        feat_dict["timestamp"] = book.timestamp
        try:
            await self.cache.set_book_features(book.symbol, feat_dict)
            await self.cache.publish(f"bookfeat:{book.symbol}", feat_dict)
        except Exception as e:  # noqa: BLE001 - best-effort; don't kill the feed
            logger.error(f"Failed to cache book features for {book.symbol}: {e}")

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
