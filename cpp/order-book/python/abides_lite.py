"""ABIDES-lite: a discrete-event latency clock + agent-based participants layer.

This module adds an **agent-based, discrete-event simulation** on top of the
EXISTING C++ price-time-priority matching engine (the compiled ``orderbook``
pybind11 module). It is the project's answer to the headline ABIDES capability:
order *arrival* order at the book is governed by **simulated network latency**,
not by submission order.

Design (and why it lives in Python, not C++)
--------------------------------------------
The C++ ``OrderBook`` is the single source of truth for matching and stays
completely untouched — this layer never re-implements price-time priority. It
only *schedules when* agent-generated orders reach the book and reads book
state back through the existing binding. A Python layer is the most testable
place for the event loop + agent strategies (seedable ``random``, trivially
inspectable event queue), reuses the compiled engine verbatim, and mirrors the
existing ``simulator.py`` pattern that already drives the engine from Python.

The kernel
----------
``SimulationKernel`` holds a min-heap **event queue** keyed by simulated
timestamp (integer nanoseconds — no wall-clock). Events are processed strictly
in time order. Each agent has a configurable one-way **agent->exchange
latency**; when an agent decides to act at time ``t`` it emits an order that is
scheduled to *arrive* at the exchange at ``t + latency``. So if a slow agent
decides earlier than a fast agent, the fast agent's order can still hit the book
first. This latency-driven reordering is the core "vs ABIDES" feature.

Agents
------
``Agent`` is a small interface. Two concrete agents are provided:

* ``NoiseAgent`` — a zero-intelligence random trader (ABIDES' "ZI" archetype):
  wakes on a timer and submits random buys/sells (marketable or resting limits).
* ``MarketMakerAgent`` — quotes a symmetric bid/ask around the engine's current
  mid (or a reference price when the book is one-sided), refreshing its quotes
  each wake-up.
* ``AvellanedaStoikovAgent`` — a finite-horizon Avellaneda-Stoikov inventory-aware
  market maker. It uses the A-S closed form only to *place* quotes around an
  inventory-skewed reservation price; whether they fill is decided by the real
  matching engine (NOT a stochastic fill-intensity model).

Determinism
-----------
Given a fixed ``seed`` the entire run is reproducible: a single seeded ``random``
stream drives wake jitter and all agent decisions, and the event queue breaks
timestamp ties by a monotonic insertion sequence (FIFO within a timestamp), so
event processing order is fully determined.

What this is NOT (honesty vs ABIDES)
------------------------------------
This is a *lite* model. It does NOT implement ITCH/OUCH wire protocols, an
exchange-agent message bus, per-agent computation-time accounting, geography,
or thousands-of-agents scale. It models the one capability that distinguishes a
matching engine *simulator* from a bare matching engine: a discrete-event clock
with agent->exchange latency. The Avellaneda-Stoikov maker uses A-S to *quote*,
but it does NOT add any stochastic-intensity fill model — every fill (for every
agent) comes only from the real C++ matching engine.

Run it::

    python python/abides_lite.py --steps 4000 --seed 42

Build the extension first so ``orderbook`` exists::

    cmake -S . -B build && cmake --build build   # from cpp/order-book/
"""

from __future__ import annotations

import argparse
import heapq
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import cast

import orderbook as ob

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

# Kinds of events the kernel processes, in priority order WITHIN a timestamp is
# decided by the kernel's insertion sequence, not by kind.
WAKEUP = "WAKEUP"  # an agent gets a turn to think and (maybe) act
ARRIVAL = "ARRIVAL"  # an order an agent emitted reaches the exchange/book
CANCEL = "CANCEL"  # a cancel request an agent emitted reaches the book

# Unified L3 event-tape kinds (see TapeRecord). These describe what happened AT
# the book in simulated-time order, independent of the scheduling event kinds.
TAPE_ORDER = "ORDER"  # a (possibly partially filling) order reached the book
TAPE_TRADE = "TRADE"  # a fill printed (one per engine Trade)
TAPE_CANCEL = "CANCEL"  # a resting order was cancelled (or the attempt failed)


@dataclass(order=True)
class _Event:
    """A scheduled event. Ordered by (time, seq) so the heap is a stable queue.

    ``seq`` is a monotonically increasing insertion counter assigned by the
    kernel; it breaks timestamp ties deterministically (FIFO within a tick) so
    a fixed seed yields a fully reproducible processing order.
    """

    time: int
    seq: int
    kind: str = field(compare=False)
    agent_id: int = field(compare=False)
    payload: object = field(default=None, compare=False)


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


class Agent:
    """Base participant. Subclasses observe a read-only book view and emit orders.

    The kernel assigns ``id`` and ``latency`` (one-way agent->exchange delay in
    nanoseconds). ``wake(now, book, rng)`` is called when the agent's WAKEUP
    event fires; it returns a list of order dicts to submit (each scheduled to
    *arrive* at ``now + latency``) and may schedule its next wake-up by reading
    ``self.wake_interval``.
    """

    def __init__(self, agent_id: int, latency: int, wake_interval: int):
        self.id = agent_id
        self.latency = latency
        self.wake_interval = wake_interval

    def wake(self, now: int, book: ob.OrderBook, rng: random.Random) -> list[dict]:
        raise NotImplementedError

    # -- fill-attribution hooks (no-op defaults) --------------------------
    # The kernel maps each order_id back to the agent that emitted it, so it can
    # notify agents about the lifecycle of *their own* orders. Subclasses that
    # care about inventory (e.g. market makers) override these; the default
    # agents ignore them, so behaviour is unchanged unless opted in.

    def on_order_accepted(self, order_id: int, order: dict, now: int) -> None:
        """Called once when one of this agent's orders reaches the book."""

    def on_fill(self, order_id: int, side: str, price: float, quantity: int, now: int) -> None:
        """Called for each fill against one of this agent's orders.

        ``side`` is THIS agent's side of the trade (``"BUY"`` if the agent was
        the buyer, ``"SELL"`` if the seller). Override to track realized
        inventory / cash.
        """

    def on_cancel_result(self, order_id: int, removed: bool, now: int) -> None:
        """Called with the result of a cancel request this agent emitted."""


class NoiseAgent(Agent):
    """Zero-intelligence random trader (ABIDES' ZI archetype).

    On each wake-up it submits exactly one order: a random side, mostly resting
    limits placed near the current mid with a small chance of a marketable order
    that crosses. It has no view or inventory target — pure noise flow that
    builds depth and occasionally takes liquidity.
    """

    def __init__(
        self,
        agent_id: int,
        latency: int,
        wake_interval: int,
        *,
        symbol: str,
        ref_price: float,
        order_size: tuple[int, ...] = (10, 25, 50, 100),
        market_prob: float = 0.15,
    ):
        super().__init__(agent_id, latency, wake_interval)
        self.symbol = symbol
        self.ref_price = ref_price
        self.order_size = order_size
        self.market_prob = market_prob

    def wake(self, now: int, book: ob.OrderBook, rng: random.Random) -> list[dict]:
        side = rng.choice(["BUY", "SELL"])
        qty = rng.choice(self.order_size)

        # Anchor on the engine's live mid when available, else the reference.
        bid = book.get_best_bid()
        ask = book.get_best_ask()
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        elif bid is not None:
            mid = bid
        elif ask is not None:
            mid = ask
        else:
            mid = self.ref_price

        if rng.random() < self.market_prob:
            order_type = "MARKET"
            price = 0.0
        else:
            order_type = "LIMIT"
            # Place a few ticks off the mid; sign depends on side so it mostly
            # rests but sometimes crosses (the noise that exercises matching).
            offset = round(rng.gauss(0.0, 0.05), 2)
            price = round(mid - offset if side == "BUY" else mid + offset, 2)
            if price <= 0:
                price = round(mid, 2)

        return [
            {
                "symbol": self.symbol,
                "side": side,
                "type": order_type,
                "price": price,
                "quantity": qty,
            }
        ]


class MarketMakerAgent(Agent):
    """A simple symmetric market maker quoting around the engine's mid.

    Each wake-up it cancels nothing (its prior quotes simply rest/age) and posts
    a fresh bid and ask ``half_spread`` away from the current mid (or the
    reference price when the book is one-sided/empty). It is deliberately naive:
    no inventory skew, no Avellaneda-Stoikov reservation price — just a quoting
    participant whose orders flow through the REAL engine like any other.
    """

    def __init__(
        self,
        agent_id: int,
        latency: int,
        wake_interval: int,
        *,
        symbol: str,
        ref_price: float,
        half_spread: float = 0.05,
        quote_size: int = 100,
    ):
        super().__init__(agent_id, latency, wake_interval)
        self.symbol = symbol
        self.ref_price = ref_price
        self.half_spread = half_spread
        self.quote_size = quote_size

    def wake(self, now: int, book: ob.OrderBook, rng: random.Random) -> list[dict]:
        bid = book.get_best_bid()
        ask = book.get_best_ask()
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        elif bid is not None:
            mid = bid + self.half_spread
        elif ask is not None:
            mid = ask - self.half_spread
        else:
            mid = self.ref_price

        bid_px = round(mid - self.half_spread, 2)
        ask_px = round(mid + self.half_spread, 2)
        # POST_ONLY so the maker never accidentally takes liquidity (rejected if
        # it would cross) — exercises the engine's post-only path too.
        return [
            {
                "symbol": self.symbol,
                "side": "BUY",
                "type": "LIMIT",
                "price": bid_px,
                "quantity": self.quote_size,
                "tif": "POST_ONLY",
            },
            {
                "symbol": self.symbol,
                "side": "SELL",
                "type": "LIMIT",
                "price": ask_px,
                "quantity": self.quote_size,
                "tif": "POST_ONLY",
            },
        ]


class AvellanedaStoikovAgent(Agent):
    """A finite-horizon Avellaneda-Stoikov market maker on the REAL engine.

    Implements the closed-form A-S (2008) inventory-aware quoting around a
    reservation price, but — unlike model-based simulators (mbt-gym, the A-S
    paper itself) — its fills come from the **real C++ price-time-priority
    matching engine**, not a stochastic fill-intensity model. We use A-S only to
    *place* quotes; whether they fill is decided by actual incoming flow.

    Quote math (per wake-up, with inventory ``q`` and time-to-horizon ``tau``):

    * reservation price  ``r = mid - q * gamma * sigma**2 * tau``
    * half-spread        ``delta = gamma * sigma**2 * tau / 2 + ln(1 + gamma/k) / gamma``
    * bid = ``r - delta``, ask = ``r + delta``

    ``gamma`` (risk aversion) and ``k`` (order-book liquidity / fill-intensity
    decay) are **user-supplied** — we deliberately do NOT auto-fit ``k`` from the
    book, because a robust online estimate is out of scope and silently fitting
    it would misrepresent the model. ``sigma`` (volatility) IS estimated online
    from the standard deviation of observed mid-prices.

    Each wake-up the agent cancels its still-resting quotes (so it never stacks
    stale liquidity), then posts a fresh POST_ONLY bid/ask. Inventory ``q`` is
    tracked from real fills via :meth:`on_fill`.
    """

    def __init__(
        self,
        agent_id: int,
        latency: int,
        wake_interval: int,
        *,
        symbol: str,
        ref_price: float,
        gamma: float = 0.1,
        k: float = 1.5,
        horizon: int,
        quote_size: int = 100,
        sigma_floor: float = 0.01,
    ):
        super().__init__(agent_id, latency, wake_interval)
        self.symbol = symbol
        self.ref_price = ref_price
        self.gamma = gamma
        self.k = k
        self.horizon = horizon  # simulated-time T at which tau -> 0
        self.quote_size = quote_size
        self.sigma_floor = sigma_floor
        self.inventory = 0
        self._mids: list[float] = []
        self._live_quotes: list[int] = []  # resting order ids to cancel next wake

    # -- volatility estimate ---------------------------------------------

    def _sigma(self) -> float:
        """Online stdev of observed mids, floored so quotes never collapse."""
        n = len(self._mids)
        if n < 2:
            return self.sigma_floor
        mean = sum(self._mids) / n
        var = sum((m - mean) ** 2 for m in self._mids) / (n - 1)
        return max(math.sqrt(var), self.sigma_floor)

    def compute_quotes(self, mid: float, q: int, tau: float) -> tuple[float, float, float, float]:
        """Return ``(reservation, half_spread, bid, ask)`` for the A-S formulas.

        Pure function of ``mid``, inventory ``q`` and time-to-horizon ``tau``
        (using the agent's current ``gamma``/``k`` and online ``sigma``), so it
        is directly unit-testable for the reservation-skew and spread-vs-tau
        properties.
        """
        sigma = self._sigma()
        var = sigma * sigma
        reservation = mid - q * self.gamma * var * tau
        half_spread = self.gamma * var * tau / 2.0 + math.log1p(self.gamma / self.k) / self.gamma
        bid = reservation - half_spread
        ask = reservation + half_spread
        return reservation, half_spread, bid, ask

    def wake(self, now: int, book: ob.OrderBook, rng: random.Random) -> list[dict]:
        bid = book.get_best_bid()
        ask = book.get_best_ask()
        if bid is not None and ask is not None:
            mid = (bid + ask) / 2.0
        elif bid is not None:
            mid = bid
        elif ask is not None:
            mid = ask
        else:
            mid = self.ref_price
        self._mids.append(mid)

        # Time-to-horizon in [0, 1]; clamps to 0 once the horizon passes so the
        # inventory term and spread shrink to the minimum near the close.
        tau = max(0.0, (self.horizon - now) / self.horizon) if self.horizon > 0 else 0.0
        _, _, bid_px, ask_px = self.compute_quotes(mid, self.inventory, tau)
        bid_px = round(bid_px, 2)
        ask_px = round(ask_px, 2)

        actions: list[dict] = [{"cancel": oid} for oid in self._live_quotes]
        self._live_quotes = []
        if bid_px > 0:
            actions.append(
                {
                    "symbol": self.symbol,
                    "side": "BUY",
                    "type": "LIMIT",
                    "price": bid_px,
                    "quantity": self.quote_size,
                    "tif": "POST_ONLY",
                }
            )
        if ask_px > bid_px:
            actions.append(
                {
                    "symbol": self.symbol,
                    "side": "SELL",
                    "type": "LIMIT",
                    "price": ask_px,
                    "quantity": self.quote_size,
                    "tif": "POST_ONLY",
                }
            )
        return actions

    # -- fill attribution -------------------------------------------------

    def on_order_accepted(self, order_id: int, order: dict, now: int) -> None:
        # Track our resting quotes so we can cancel them on the next wake-up.
        if order.get("tif") == "POST_ONLY":
            self._live_quotes.append(order_id)

    def on_fill(self, order_id: int, side: str, price: float, quantity: int, now: int) -> None:
        self.inventory += quantity if side == "BUY" else -quantity
        # A filled quote is no longer resting; drop it from the cancel list.
        if order_id in self._live_quotes:
            self._live_quotes.remove(order_id)

    def on_cancel_result(self, order_id: int, removed: bool, now: int) -> None:
        if order_id in self._live_quotes:
            self._live_quotes.remove(order_id)


# ---------------------------------------------------------------------------
# Kernel
# ---------------------------------------------------------------------------

_SIDE = {"BUY": ob.Side.BUY, "SELL": ob.Side.SELL}
_TYPE = {"LIMIT": ob.OrderType.LIMIT, "MARKET": ob.OrderType.MARKET}
_TIF = {
    "GTC": ob.TimeInForce.GTC,
    "IOC": ob.TimeInForce.IOC,
    "FOK": ob.TimeInForce.FOK,
    "POST_ONLY": ob.TimeInForce.POST_ONLY,
}


@dataclass
class ArrivalRecord:
    """One order arrival at the book, in the order the engine actually saw it.

    ``decide_time`` is when the emitting agent decided; ``arrive_time`` is when
    it reached the book (= decide_time + the agent's latency). The gap between
    the two columns across records is what demonstrates latency reordering:
    arrivals are processed in ``arrive_time`` order even if ``decide_time``
    order differs.
    """

    arrive_time: int
    decide_time: int
    agent_id: int
    order_id: int
    side: str
    type: str
    price: float
    quantity: int
    n_fills: int


@dataclass
class TapeRecord:
    """One entry on the unified L3 event tape, in book-processing (sim-time) order.

    A single chronological tape that interleaves the three things that happen at
    the book, so downstream tooling can reconstruct the full event sequence with
    agent attribution:

    * ``kind == TAPE_ORDER``  — an order reached the book. ``agent_id`` is the
      submitter (the *taker* if it crossed). ``order_id`` is the engine id.
    * ``kind == TAPE_TRADE``  — a fill printed. ``taker_id`` is the aggressor's
      agent, ``maker_id`` the resting agent (either may be ``None`` if the order
      was injected directly rather than via an agent).
    * ``kind == TAPE_CANCEL`` — a cancel request was processed. ``order_id`` is
      the target; ``quantity`` is 1 if it was removed, 0 if not found.
    """

    time: int
    kind: str
    agent_id: int | None = None
    order_id: int | None = None
    side: str | None = None
    price: float | None = None
    quantity: int = 0
    taker_id: int | None = None
    maker_id: int | None = None


@dataclass
class KernelResult:
    """Everything observable after a run — all engine state read via the binding."""

    symbol: str
    arrivals: list[ArrivalRecord] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    tape: list[TapeRecord] = field(default_factory=list)
    spreads: list[float] = field(default_factory=list)
    bids: list[dict] = field(default_factory=list)
    asks: list[dict] = field(default_factory=list)
    agent_inventory: dict[int, int] = field(default_factory=dict)
    best_bid: float | None = None
    best_ask: float | None = None
    n_events: int = 0

    @property
    def n_orders(self) -> int:
        return len(self.arrivals)

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def total_volume(self) -> int:
        return sum(t["quantity"] for t in self.trades)


class SimulationKernel:
    """Discrete-event kernel driving agents into the REAL C++ matching engine.

    The kernel owns the simulated clock and the event queue. It never matches
    orders itself — every ARRIVAL is submitted to a real ``orderbook.OrderBook``
    and the engine's returned fills are recorded. Agent->exchange latency is
    applied between an agent's WAKEUP (decision) and the corresponding ARRIVAL,
    so the book sees orders in latency-adjusted order.
    """

    def __init__(self, symbol: str = "AAPL", seed: int | None = None, depth_levels: int = 10):
        self.symbol = symbol
        self.rng = random.Random(seed)
        self.depth_levels = depth_levels
        self.book = ob.OrderBook(symbol)
        self._queue: list[_Event] = []
        self._seq = 0
        self._now = 0
        self._next_order_id = 1
        self.agents: dict[int, Agent] = {}
        # order_id -> (agent_id, side) so fills/cancels can be attributed back to
        # the emitting agent. Direct schedule_arrival injections without an agent
        # in self.agents simply map to that (possibly unregistered) agent_id.
        self._order_owner: dict[int, tuple[int, str]] = {}
        self.result = KernelResult(symbol=symbol)

    # -- scheduling -------------------------------------------------------

    def _push(self, time: int, kind: str, agent_id: int, payload: object = None) -> None:
        heapq.heappush(self._queue, _Event(time, self._seq, kind, agent_id, payload))
        self._seq += 1

    def add_agent(self, agent: Agent, *, first_wake: int = 0) -> None:
        """Register an agent and schedule its first WAKEUP."""
        self.agents[agent.id] = agent
        self._push(first_wake, WAKEUP, agent.id)

    def schedule_arrival(
        self, arrive_time: int, agent_id: int, order: dict, decide_time: int
    ) -> None:
        """Public hook to inject an arrival at an exact time (used in tests)."""
        self._push(arrive_time, ARRIVAL, agent_id, (order, decide_time))

    def schedule_cancel(self, arrive_time: int, agent_id: int, order_id: int) -> None:
        """Public hook to schedule a cancel request to reach the book at a time."""
        self._push(arrive_time, CANCEL, agent_id, order_id)

    # -- order conversion -------------------------------------------------

    def _to_order(self, order: dict) -> ob.Order:
        oid = self._next_order_id
        self._next_order_id += 1
        return ob.Order(
            oid,
            order.get("symbol", self.symbol),
            _SIDE[order["side"]],
            _TYPE[order["type"]],
            float(order["price"]),
            int(order["quantity"]),
            _TIF.get(order.get("tif", "GTC"), ob.TimeInForce.GTC),
        )

    # -- event handlers ---------------------------------------------------

    def _handle_wakeup(self, ev: _Event) -> None:
        agent = self.agents[ev.agent_id]
        orders = agent.wake(self._now, self.book, self.rng)
        arrive_at = self._now + agent.latency
        for order in orders:
            # The order is DECIDED now but ARRIVES after the agent's latency,
            # so submission order != arrival order when latencies differ.
            # A dict carrying a "cancel" key is a cancel request, not an order;
            # the built-in agents never emit these, so the default path is
            # untouched (same RNG draws, same scheduling).
            if "cancel" in order:
                self._push(arrive_at, CANCEL, agent.id, int(order["cancel"]))
            else:
                self._push(arrive_at, ARRIVAL, agent.id, (order, self._now))
        # Re-arm the agent's clock with a little jitter for desynchronization.
        jitter = int(self.rng.uniform(0.5, 1.5) * agent.wake_interval)
        self._push(self._now + jitter, WAKEUP, agent.id)

    def _record_fills(self, order: dict, oid: int, taker_id: int | None, trades: list) -> None:
        """Append trades to the tape and notify both sides via on_fill hooks."""
        taker_side = order["side"]
        for t in trades:
            self.result.trades.append(
                {
                    "time": self._now,
                    "price": t.price,
                    "quantity": t.quantity,
                    "buyer_order_id": t.buyer_order_id,
                    "seller_order_id": t.seller_order_id,
                    "symbol": t.symbol,
                }
            )
            # The aggressor is the just-submitted order (oid); the resting order
            # is the other id. Look up the maker's owning agent for attribution.
            maker_oid = t.seller_order_id if taker_side == "BUY" else t.buyer_order_id
            maker_owner = self._order_owner.get(maker_oid)
            maker_id = maker_owner[0] if maker_owner else None
            self.result.tape.append(
                TapeRecord(
                    time=self._now,
                    kind=TAPE_TRADE,
                    order_id=oid,
                    side=taker_side,
                    price=t.price,
                    quantity=t.quantity,
                    taker_id=taker_id,
                    maker_id=maker_id,
                )
            )
            # Attribute the fill to inventory and notify both agents.
            self._apply_fill(taker_id, taker_side, t.price, t.quantity)
            maker_side = "SELL" if taker_side == "BUY" else "BUY"
            self._apply_fill(maker_id, maker_side, t.price, t.quantity, order_id=maker_oid)

    def _apply_fill(
        self,
        agent_id: int | None,
        side: str,
        price: float,
        quantity: int,
        order_id: int | None = None,
    ) -> None:
        if agent_id is None:
            return
        delta = quantity if side == "BUY" else -quantity
        self.result.agent_inventory[agent_id] = self.result.agent_inventory.get(agent_id, 0) + delta
        agent = self.agents.get(agent_id)
        if agent is not None:
            oid = order_id if order_id is not None else -1
            agent.on_fill(oid, side, price, quantity, self._now)

    def _handle_arrival(self, ev: _Event) -> None:
        payload = cast("tuple[dict, int]", ev.payload)
        order, decide_time = payload
        engine_order = self._to_order(order)
        oid = engine_order.id
        taker_id = ev.agent_id
        self._order_owner[oid] = (taker_id, order["side"])

        self.result.tape.append(
            TapeRecord(
                time=self._now,
                kind=TAPE_ORDER,
                agent_id=taker_id,
                order_id=oid,
                side=order["side"],
                price=float(order["price"]),
                quantity=int(order["quantity"]),
            )
        )
        agent = self.agents.get(taker_id)
        if agent is not None:
            agent.on_order_accepted(oid, order, self._now)

        trades = self.book.add_order(engine_order)
        self._record_fills(order, oid, taker_id, trades)

        self.result.arrivals.append(
            ArrivalRecord(
                arrive_time=self._now,
                decide_time=decide_time,
                agent_id=ev.agent_id,
                order_id=oid,
                side=order["side"],
                type=order["type"],
                price=float(order["price"]),
                quantity=int(order["quantity"]),
                n_fills=len(trades),
            )
        )

        bid = self.book.get_best_bid()
        ask = self.book.get_best_ask()
        if bid is not None and ask is not None:
            self.result.spreads.append(ask - bid)

    def _handle_cancel(self, ev: _Event) -> None:
        order_id = cast("int", ev.payload)
        removed = self.book.cancel_order(order_id)
        self.result.tape.append(
            TapeRecord(
                time=self._now,
                kind=TAPE_CANCEL,
                agent_id=ev.agent_id,
                order_id=order_id,
                quantity=1 if removed else 0,
            )
        )
        if removed:
            self._order_owner.pop(order_id, None)
        agent = self.agents.get(ev.agent_id)
        if agent is not None:
            agent.on_cancel_result(order_id, removed, self._now)

    # -- main loop --------------------------------------------------------

    def run(self, until: int | None = None, max_events: int | None = None) -> KernelResult:
        """Process events in simulated-time order until ``until`` / ``max_events``.

        Returns a :class:`KernelResult` whose every field is read off the live
        engine — never a Python-side approximation.
        """
        processed = 0
        while self._queue:
            if max_events is not None and processed >= max_events:
                break
            ev = heapq.heappop(self._queue)
            if until is not None and ev.time > until:
                # The event is past the window — put it back so the kernel can
                # be resumed with a later ``run(until=...)`` without losing it.
                heapq.heappush(self._queue, ev)
                break
            self._now = ev.time
            if ev.kind == WAKEUP:
                self._handle_wakeup(ev)
            elif ev.kind == CANCEL:
                self._handle_cancel(ev)
            else:
                self._handle_arrival(ev)
            processed += 1

        self.result.n_events = processed
        self.result.bids = [
            {"price": d.price, "quantity": d.total_quantity, "order_count": d.order_count}
            for d in self.book.get_bid_depth(self.depth_levels)
        ]
        self.result.asks = [
            {"price": d.price, "quantity": d.total_quantity, "order_count": d.order_count}
            for d in self.book.get_ask_depth(self.depth_levels)
        ]
        self.result.best_bid = self.book.get_best_bid()
        self.result.best_ask = self.book.get_best_ask()
        return self.result


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------


def build_default_kernel(
    *,
    symbol: str = "AAPL",
    ref_price: float = 150.0,
    seed: int | None = None,
    n_noise: int = 6,
    n_makers: int = 2,
    noise_latency: tuple[int, int] = (1_000, 50_000),
    maker_latency: int = 500,
) -> SimulationKernel:
    """Wire up a kernel with a mix of noise traders + market makers.

    Noise agents get a SPREAD of random latencies (some slow, some fast) so the
    discrete-event clock visibly reorders their arrivals; the market makers are
    given a small fixed latency (they sit close to the matching engine).
    """
    kernel = SimulationKernel(symbol=symbol, seed=seed)
    aid = 0
    for _ in range(n_noise):
        latency = kernel.rng.randint(*noise_latency)
        kernel.add_agent(
            NoiseAgent(
                aid,
                latency,
                wake_interval=10_000,
                symbol=symbol,
                ref_price=ref_price,
            ),
            first_wake=kernel.rng.randint(0, 5_000),
        )
        aid += 1
    for _ in range(n_makers):
        kernel.add_agent(
            MarketMakerAgent(
                aid,
                maker_latency,
                wake_interval=8_000,
                symbol=symbol,
                ref_price=ref_price,
            ),
            first_wake=0,
        )
        aid += 1
    return kernel


def simulate_agents(
    *,
    symbol: str = "AAPL",
    ref_price: float = 150.0,
    seed: int | None = 42,
    steps: int = 4_000,
    builder: Callable[..., SimulationKernel] | None = None,
) -> KernelResult:
    """One-call helper: build the default agent mix and run ``steps`` events."""
    builder = builder or build_default_kernel
    kernel = builder(symbol=symbol, ref_price=ref_price, seed=seed)
    return kernel.run(max_events=steps)


def count_latency_reorderings(result: KernelResult) -> int:
    """How many arrivals landed out of *decision* order (a latency-reorder proxy).

    Counts adjacent arrival pairs where the later-arriving order was actually
    decided earlier than its predecessor — i.e. arrival order != decision order,
    which can only happen because of differing agent->exchange latencies. A
    positive count is direct evidence the latency clock reordered the flow.
    """
    n = 0
    for prev, cur in zip(result.arrivals, result.arrivals[1:], strict=False):
        if cur.decide_time < prev.decide_time:
            n += 1
    return n


def _build_mm_comparison_kernel(
    *,
    symbol: str,
    ref_price: float,
    seed: int | None,
    steps: int,
    use_as: bool,
    n_noise: int = 6,
) -> SimulationKernel:
    """Build a noise-flow kernel with ONE market maker: A-S or naive.

    Identical noise flow either way (same seed, same agent ids/latencies), so
    the only difference between the two runs is the maker's quoting policy —
    making the inventory-behaviour comparison apples-to-apples and deterministic.
    """
    kernel = SimulationKernel(symbol=symbol, seed=seed)
    aid = 0
    for _ in range(n_noise):
        latency = kernel.rng.randint(1_000, 50_000)
        kernel.add_agent(
            NoiseAgent(aid, latency, wake_interval=10_000, symbol=symbol, ref_price=ref_price),
            first_wake=kernel.rng.randint(0, 5_000),
        )
        aid += 1
    maker: Agent
    if use_as:
        maker = AvellanedaStoikovAgent(
            aid,
            500,
            wake_interval=8_000,
            symbol=symbol,
            ref_price=ref_price,
            gamma=0.3,
            k=1.5,
            horizon=steps * 8_000,
        )
    else:
        maker = MarketMakerAgent(aid, 500, wake_interval=8_000, symbol=symbol, ref_price=ref_price)
    kernel.add_agent(maker, first_wake=0)
    return kernel


def compare_market_makers(
    *,
    symbol: str = "AAPL",
    ref_price: float = 150.0,
    seed: int | None = 42,
    steps: int = 4_000,
) -> dict[str, KernelResult]:
    """Run identical noise flow against the A-S maker vs the naive maker.

    Returns ``{"avellaneda_stoikov": result, "naive": result}``. Deterministic
    under a fixed seed. The A-S maker's ``agent_inventory`` should mean-revert
    toward zero (it skews quotes against inventory), whereas the naive maker has
    no inventory control.
    """
    as_kernel = _build_mm_comparison_kernel(
        symbol=symbol, ref_price=ref_price, seed=seed, steps=steps, use_as=True
    )
    naive_kernel = _build_mm_comparison_kernel(
        symbol=symbol, ref_price=ref_price, seed=seed, steps=steps, use_as=False
    )
    return {
        "avellaneda_stoikov": as_kernel.run(max_events=steps),
        "naive": naive_kernel.run(max_events=steps),
    }


def _print_mm_comparison(symbol: str, ref_price: float, seed: int, steps: int) -> None:
    results = compare_market_makers(symbol=symbol, ref_price=ref_price, seed=seed, steps=steps)
    print(f"Market-maker comparison on {symbol} (seed {seed}, {steps} events)")
    print("  identical noise flow; the only difference is the maker's quoting policy\n")
    for label in ("avellaneda_stoikov", "naive"):
        r = results[label]
        # The maker is the highest agent id (added last).
        maker_id = max(r.agent_inventory) if r.agent_inventory else None
        inv = r.agent_inventory.get(maker_id, 0) if maker_id is not None else 0
        print(f"  [{label}]")
        print(f"    trades executed : {r.n_trades}  (volume {r.total_volume})")
        print(f"    maker inventory : {inv}")
        print(f"    best bid/ask    : {r.best_bid} / {r.best_ask}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ABIDES-lite agent simulation")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--ref-price", type=float, default=150.0)
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (deterministic run)")
    parser.add_argument("--steps", type=int, default=4_000, help="max events to process")
    parser.add_argument("--noise", type=int, default=6, help="number of noise agents")
    parser.add_argument("--makers", type=int, default=2, help="number of market-maker agents")
    parser.add_argument(
        "--compare-mm",
        action="store_true",
        help="compare Avellaneda-Stoikov vs naive maker inventory on identical noise flow",
    )
    args = parser.parse_args()

    if args.compare_mm:
        _print_mm_comparison(args.symbol, args.ref_price, args.seed, args.steps)
        return

    kernel = build_default_kernel(
        symbol=args.symbol,
        ref_price=args.ref_price,
        seed=args.seed,
        n_noise=args.noise,
        n_makers=args.makers,
    )
    result = kernel.run(max_events=args.steps)

    print(f"ABIDES-lite: {args.noise} noise + {args.makers} maker agents on {result.symbol}")
    print(f"  events processed : {result.n_events}")
    print(f"  orders arrived   : {result.n_orders}")
    print(f"  trades executed  : {result.n_trades}  (volume {result.total_volume})")
    print(f"  best bid/ask     : {result.best_bid} / {result.best_ask}")
    if result.best_bid is not None and result.best_ask is not None:
        print(f"  final spread     : {result.best_ask - result.best_bid:.2f}")
    reorders = count_latency_reorderings(result)
    print(
        f"  latency reorderings: {reorders} arrivals landed out of decision order "
        "(arrival order is driven by latency, not submission order)"
    )


if __name__ == "__main__":
    main()
