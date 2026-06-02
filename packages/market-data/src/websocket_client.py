import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from .adapters import BinanceAdapter, ExchangeAdapter

logger = logging.getLogger(__name__)

# A connection that stays open at least this long has survived a full ping/pong
# keepalive cycle (ping_interval=20 + ping_timeout=10) and is treated as healthy
# even if it never delivered an application message — e.g. a quiet, low-volume
# stream. Dropping after this point should not count against the retry budget.
_STABLE_CONNECTION_SECONDS = 60


class MarketDataClient:
    """Async WebSocket client with auto-reconnect for market data streams."""

    def __init__(
        self,
        ws_url: str,
        max_retries: int = 10,
        adapter: ExchangeAdapter | None = None,
    ):
        self.ws_url = ws_url
        self.max_retries = max_retries
        # The adapter decides the per-exchange URL + subscribe payload. Default
        # to Binance built from ``ws_url`` so callers that don't pass one (and
        # the existing tests) behave exactly as before.
        self.adapter: ExchangeAdapter = adapter or BinanceAdapter(ws_url)
        self._ws: ClientConnection | None = None
        self._callbacks: list[Callable[[dict | list], Awaitable[None]]] = []
        self._running = False
        self._retry_count = 0

    def on_message(self, callback: Callable[[dict | list], Awaitable[None]]) -> None:
        """Register an async callback for incoming messages."""
        self._callbacks.append(callback)

    async def connect(self, symbols: list[str]) -> None:
        """Connect to WebSocket and start consuming messages."""
        self._running = True
        url = self.adapter.ws_url(symbols)
        subscribe = self.adapter.subscribe_payload(symbols)

        while self._running and self._retry_count < self.max_retries:
            connected_at: float | None = None
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    connected_at = time.monotonic()
                    logger.info(f"Connected to {url}")
                    # Venues that select streams via a subscribe message (e.g.
                    # Coinbase) need it sent right after the socket opens; URL-
                    # embedded-stream venues (Binance) return None and skip this.
                    if subscribe is not None:
                        await ws.send(json.dumps(subscribe))
                        logger.info(f"Sent subscribe for {symbols}")
                    # The retry budget is cleared two ways: inside _consume() the
                    # moment a message arrives, and in _reconnect() if this
                    # connection stayed up long enough to be deemed healthy (so a
                    # quiet-but-stable stream isn't eventually starved out). A
                    # connection that never establishes or drops immediately keeps
                    # incrementing the counter, so genuine flapping still trips
                    # max_retries.
                    await self._consume(ws)
            except ConnectionClosed as e:
                logger.warning(f"Connection closed: {e}. Reconnecting...")
                await self._reconnect(connected_at)
            except Exception as e:
                logger.error(f"WebSocket error: {e}. Reconnecting...")
                await self._reconnect(connected_at)

        if self._retry_count >= self.max_retries:
            logger.error("Max retries reached. Stopping client.")

    async def _consume(self, ws) -> None:
        """Process incoming messages and dispatch to callbacks."""
        async for raw in ws:
            # A delivered message proves the stream is healthy: clear the
            # reconnect backoff counter so future drops get a fresh budget.
            self._retry_count = 0
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON: {raw[:100]}")
                continue

            for callback in self._callbacks:
                try:
                    await callback(data)
                except Exception as e:
                    logger.error(f"Callback error: {e}", exc_info=True)

    async def _reconnect(self, connected_at: float | None = None) -> None:
        """Exponential backoff reconnect logic.

        If the just-dropped connection had stayed open at least
        ``_STABLE_CONNECTION_SECONDS`` (``connected_at`` set and old enough), the
        retry budget is reset first: a stable connection that delivered no
        messages (a quiet stream) was still healthy, so its drop should not be
        counted as a failed attempt. Connections that never established
        (``connected_at is None``) or dropped quickly keep climbing toward
        ``max_retries``.
        """
        if (
            connected_at is not None
            and time.monotonic() - connected_at >= _STABLE_CONNECTION_SECONDS
        ):
            self._retry_count = 0
        self._retry_count += 1
        delay = min(2**self._retry_count, 60)
        logger.info(f"Retry {self._retry_count}/{self.max_retries} in {delay}s")
        await asyncio.sleep(delay)

    async def disconnect(self) -> None:
        """Gracefully close the connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            logger.info("WebSocket disconnected")
