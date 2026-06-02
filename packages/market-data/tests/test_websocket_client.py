"""Edge-case tests for MarketDataClient.

The real ``websockets.connect`` is monkeypatched with the FakeWebSocket from
conftest, and ``asyncio.sleep`` is patched out so backoff delays don't slow
the suite or hit a real clock.
"""

import json
import time

import pytest
from websockets.exceptions import ConnectionClosed

from src.websocket_client import MarketDataClient


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Make backoff sleeps instantaneous and deterministic."""
    recorded = []

    async def fake_sleep(delay):
        recorded.append(delay)

    monkeypatch.setattr("src.websocket_client.asyncio.sleep", fake_sleep)
    return recorded


def _patch_connect(monkeypatch, ws_or_factory):
    """Patch websockets.connect to return the given async-context object(s).

    Accepts a single object or a callable producing one per call.
    """

    def connect(url, **kwargs):
        if callable(ws_or_factory):
            return ws_or_factory()
        return ws_or_factory

    monkeypatch.setattr("src.websocket_client.websockets.connect", connect)


class TestConsume:
    async def test_dispatches_valid_json_to_callbacks(self, fake_ws_factory):
        client = MarketDataClient("wss://fake", max_retries=1)
        received = []

        async def cb(d):
            received.append(d)

        client.on_message(cb)
        ws = fake_ws_factory(messages=[json.dumps({"a": 1}), json.dumps({"b": 2})])
        await client._consume(ws)
        assert received == [{"a": 1}, {"b": 2}]

    async def test_malformed_json_is_skipped(self, fake_ws_factory):
        client = MarketDataClient("wss://fake")
        received = []

        async def cb(d):
            received.append(d)

        client.on_message(cb)
        ws = fake_ws_factory(messages=["{not json", json.dumps({"ok": True}), "}}}"])
        await client._consume(ws)
        # only the one valid message survives
        assert received == [{"ok": True}]

    async def test_partial_truncated_message_skipped(self, fake_ws_factory):
        client = MarketDataClient("wss://fake")
        received = []

        async def cb(d):
            received.append(d)

        client.on_message(cb)
        ws = fake_ws_factory(messages=['{"s": "BTCUSDT", "p": "100"'])  # truncated
        await client._consume(ws)
        assert received == []

    async def test_callback_exception_does_not_kill_consumer(self, fake_ws_factory):
        client = MarketDataClient("wss://fake")
        good = []

        async def boom(d):
            raise RuntimeError("callback blew up")

        async def good_cb(d):
            good.append(d)

        client.on_message(boom)
        client.on_message(good_cb)
        ws = fake_ws_factory(messages=[json.dumps({"x": 1}), json.dumps({"x": 2})])
        # should not raise; the good callback still runs for every message
        await client._consume(ws)
        assert good == [{"x": 1}, {"x": 2}]


class TestReconnect:
    async def test_backoff_is_exponential(self, no_sleep):
        client = MarketDataClient("wss://fake", max_retries=10)
        await client._reconnect()  # retry 1 -> 2s
        await client._reconnect()  # retry 2 -> 4s
        await client._reconnect()  # retry 3 -> 8s
        assert no_sleep == [2, 4, 8]
        assert client._retry_count == 3

    async def test_backoff_capped_at_60(self, no_sleep):
        client = MarketDataClient("wss://fake", max_retries=100)
        client._retry_count = 9  # next is 10 -> 2**10 = 1024, capped to 60
        await client._reconnect()
        assert no_sleep[-1] == 60

    async def test_stable_connection_drop_resets_retry_budget(self, no_sleep):
        """A connection that stayed up past the stable threshold (a healthy but
        quiet stream) should get a fresh retry budget when it drops, so it is
        never starved out by max_retries."""
        client = MarketDataClient("wss://fake", max_retries=10)
        client._retry_count = 8
        # Connected well over _STABLE_CONNECTION_SECONDS ago, no message needed.
        await client._reconnect(connected_at=time.monotonic() - 120)
        # reset to 0, then incremented for this attempt
        assert client._retry_count == 1

    async def test_immediate_flap_does_not_reset_budget(self, no_sleep):
        """A connection that drops almost immediately must NOT reset the budget,
        so genuine flapping still trips max_retries (the bug ce5ac45 fixed)."""
        client = MarketDataClient("wss://fake", max_retries=10)
        client._retry_count = 8
        await client._reconnect(connected_at=time.monotonic())  # ~0s uptime
        assert client._retry_count == 9

    async def test_failed_connect_does_not_reset_budget(self, no_sleep):
        """If the socket never established (connected_at is None), keep climbing."""
        client = MarketDataClient("wss://fake", max_retries=10)
        client._retry_count = 8
        await client._reconnect(connected_at=None)
        assert client._retry_count == 9


class TestConnectLoop:
    async def test_connection_closed_triggers_reconnect_then_stops(
        self, monkeypatch, fake_ws_factory, no_sleep
    ):
        """A ConnectionClosed during consume should drive a reconnect; with
        max_retries reached the loop exits cleanly."""
        # Build a ws that raises ConnectionClosed mid-stream.
        cc = ConnectionClosed(None, None)

        def factory():
            return fake_ws_factory(messages=[], raise_on_iter=cc)

        _patch_connect(monkeypatch, factory)
        client = MarketDataClient("wss://fake", max_retries=2)
        await client.connect(["btcusdt"])
        # retry_count should have reached max_retries, ending the loop
        assert client._retry_count >= client.max_retries
        # backoff was invoked at least once
        assert len(no_sleep) >= 1

    async def test_generic_error_triggers_reconnect(self, monkeypatch, fake_ws_factory, no_sleep):
        def factory():
            return fake_ws_factory(messages=[], raise_on_iter=ValueError("boom"))

        _patch_connect(monkeypatch, factory)
        client = MarketDataClient("wss://fake", max_retries=2)
        await client.connect(["btcusdt"])
        assert client._retry_count >= client.max_retries

    async def test_clean_stream_resets_retry_count(self, monkeypatch, fake_ws_factory, no_sleep):
        """A clean consume (no exception) returns and the while loop re-enters;
        we stop it via _running so it doesn't spin forever."""
        client = MarketDataClient("wss://fake", max_retries=5)
        seen = []

        async def cb(d):
            seen.append(d)
            # after first message, signal shutdown so the outer loop exits
            client._running = False

        client.on_message(cb)

        def factory():
            return fake_ws_factory(messages=[json.dumps({"hello": "world"})])

        _patch_connect(monkeypatch, factory)
        await client.connect(["btcusdt"])
        assert seen == [{"hello": "world"}]
        # retry_count reset to 0 once the stream delivers a message (in _consume)
        assert client._retry_count == 0

    async def test_url_built_from_symbols(self, monkeypatch, fake_ws_factory):
        captured = {}

        def connect(url, **kwargs):
            captured["url"] = url
            client._running = False  # stop after first attempt
            return fake_ws_factory(messages=[])

        monkeypatch.setattr("src.websocket_client.websockets.connect", connect)
        client = MarketDataClient("wss://base", max_retries=3)
        await client.connect(["btcusdt", "ethusdt"])
        assert captured["url"] == "wss://base/btcusdt@trade/ethusdt@trade"


class TestDisconnect:
    async def test_disconnect_closes_socket(self, fake_ws_factory):
        client = MarketDataClient("wss://fake")
        ws = fake_ws_factory()
        client._ws = ws
        client._running = True
        await client.disconnect()
        assert client._running is False
        assert ws.closed is True

    async def test_disconnect_without_socket_is_safe(self):
        client = MarketDataClient("wss://fake")
        await client.disconnect()  # no _ws set
        assert client._running is False
