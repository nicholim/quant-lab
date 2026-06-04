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
with agent->exchange latency. It also does NOT add any stochastic-intensity /
Avellaneda-Stoikov fill model — fills come only from the real C++ engine.

Run it::

    python python/abides_lite.py --steps 4000 --seed 42

Build the extension first so ``orderbook`` exists::

    cmake -S . -B build && cmake --build build   # from cpp/order-book/
"""

from __future__ import annotations

import argparse
import heapq
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
class KernelResult:
    """Everything observable after a run — all engine state read via the binding."""

    symbol: str
    arrivals: list[ArrivalRecord] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    spreads: list[float] = field(default_factory=list)
    bids: list[dict] = field(default_factory=list)
    asks: list[dict] = field(default_factory=list)
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
        for order in orders:
            # The order is DECIDED now but ARRIVES after the agent's latency,
            # so submission order != arrival order when latencies differ.
            arrive_at = self._now + agent.latency
            self._push(arrive_at, ARRIVAL, agent.id, (order, self._now))
        # Re-arm the agent's clock with a little jitter for desynchronization.
        jitter = int(self.rng.uniform(0.5, 1.5) * agent.wake_interval)
        self._push(self._now + jitter, WAKEUP, agent.id)

    def _handle_arrival(self, ev: _Event) -> None:
        payload = cast("tuple[dict, int]", ev.payload)
        order, decide_time = payload
        engine_order = self._to_order(order)
        oid = engine_order.id
        trades = self.book.add_order(engine_order)

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
                break
            self._now = ev.time
            if ev.kind == WAKEUP:
                self._handle_wakeup(ev)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="ABIDES-lite agent simulation")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--ref-price", type=float, default=150.0)
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (deterministic run)")
    parser.add_argument("--steps", type=int, default=4_000, help="max events to process")
    parser.add_argument("--noise", type=int, default=6, help="number of noise agents")
    parser.add_argument("--makers", type=int, default=2, help="number of market-maker agents")
    args = parser.parse_args()

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
