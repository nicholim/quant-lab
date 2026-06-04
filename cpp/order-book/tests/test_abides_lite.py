"""Tests for the ABIDES-lite discrete-event + agent layer.

These prove the three things that make this "ABIDES-lite" rather than a bare
matching engine:

1. the discrete-event kernel processes events in **simulated-time** order;
2. agent->exchange **latency** reorders arrivals (a slow agent that decides
   first can still hit the book *after* a fast agent that decides later);
3. the whole run is **deterministic** under a fixed seed.

All matching is done by the real C++ engine through the binding — these tests
never re-implement a book.
"""

import os
import sys

import pytest

pytest.importorskip(
    "orderbook",
    reason="compiled _orderbook extension not built — run cmake --build build first",
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))

import abides_lite as al  # noqa: E402
import orderbook as ob  # noqa: E402
from abides_lite import (  # noqa: E402
    ARRIVAL,
    WAKEUP,
    Agent,
    ArrivalRecord,
    KernelResult,
    MarketMakerAgent,
    NoiseAgent,
    SimulationKernel,
    build_default_kernel,
    count_latency_reorderings,
    simulate_agents,
)


class _ScriptedAgent(Agent):
    """An agent that emits a fixed list of orders on its first wake, then nothing.

    Used to drive the kernel with a known flow so latency reordering is provable
    rather than statistical.
    """

    def __init__(self, agent_id, latency, *, orders, wake_interval=10**9):
        super().__init__(agent_id, latency, wake_interval)
        self._orders = orders
        self._fired = False

    def wake(self, now, book, rng):
        if self._fired:
            return []
        self._fired = True
        return list(self._orders)


# --------------------------------------------------------------------------
# Kernel: time-ordered event processing
# --------------------------------------------------------------------------


class TestKernelOrdering:
    def test_events_processed_in_simulated_time_order(self):
        # Inject arrivals out of time order; the kernel must process by timestamp.
        k = SimulationKernel("AAPL", seed=0)

        # Schedule three arrivals at decreasing push order but mixed times.
        k.schedule_arrival(300, 0, {"side": "BUY", "type": "LIMIT", "price": 1.0, "quantity": 1}, 0)
        k.schedule_arrival(100, 0, {"side": "BUY", "type": "LIMIT", "price": 2.0, "quantity": 1}, 0)
        k.schedule_arrival(200, 0, {"side": "BUY", "type": "LIMIT", "price": 3.0, "quantity": 1}, 0)
        result = k.run()
        times = [a.arrive_time for a in result.arrivals]
        assert times == [100, 200, 300]  # strictly time-ordered, not push-ordered

    def test_fifo_within_same_timestamp(self):
        # Same timestamp -> insertion order (seq) decides; arrivals stay FIFO.
        k = SimulationKernel("AAPL", seed=0)
        for i in range(4):
            k.schedule_arrival(
                50, 0, {"side": "SELL", "type": "LIMIT", "price": 10.0 + i, "quantity": 1}, 0
            )
        result = k.run()
        # order_id is assigned at arrival time in processing order -> 1,2,3,4
        assert [a.order_id for a in result.arrivals] == [1, 2, 3, 4]

    def test_until_stops_the_clock(self):
        k = SimulationKernel("AAPL", seed=0)
        k.schedule_arrival(100, 0, {"side": "BUY", "type": "LIMIT", "price": 1.0, "quantity": 1}, 0)
        k.schedule_arrival(900, 0, {"side": "BUY", "type": "LIMIT", "price": 1.0, "quantity": 1}, 0)
        result = k.run(until=500)
        assert result.n_orders == 1  # the 900 event is beyond the horizon

    def test_until_preserves_out_of_window_event_for_resume(self):
        # The boundary event past `until` must NOT be consumed — a follow-up
        # run with a later horizon must still process it (resumable kernel).
        k = SimulationKernel("AAPL", seed=0)
        buy = {"side": "BUY", "type": "LIMIT", "price": 1.0, "quantity": 1}
        sell = {"side": "SELL", "type": "LIMIT", "price": 2.0, "quantity": 1}
        k.schedule_arrival(100, 0, buy, 0)
        k.schedule_arrival(900, 0, sell, 0)
        first = k.run(until=500)
        assert first.n_orders == 1
        # Resume: the 900 event was put back, so it is processed now.
        second = k.run(until=1000)
        assert second.n_orders == 2
        assert k.book.get_best_ask() == 2.0  # the resumed SELL reached the engine


# --------------------------------------------------------------------------
# Latency reordering — the headline capability
# --------------------------------------------------------------------------


class TestLatencyReordering:
    def test_slow_early_decider_arrives_after_fast_late_decider(self):
        # Agent S decides at t=0 but has 1000ns latency -> arrives at 1000.
        # Agent F decides at t=100 but has 10ns latency   -> arrives at 110.
        # Submission/decision order is S then F; ARRIVAL order must be F then S.
        k = SimulationKernel("AAPL", seed=0)

        slow = _ScriptedAgent(
            0,
            latency=1000,
            orders=[{"side": "BUY", "type": "LIMIT", "price": 100.0, "quantity": 10}],
        )
        fast = _ScriptedAgent(
            1,
            latency=10,
            orders=[{"side": "SELL", "type": "LIMIT", "price": 101.0, "quantity": 10}],
        )
        k.add_agent(slow, first_wake=0)
        k.add_agent(fast, first_wake=100)

        result = k.run(max_events=50)
        # Find the two scripted arrivals.
        arr = [a for a in result.arrivals if a.quantity == 10]
        assert len(arr) >= 2
        first, second = arr[0], arr[1]
        # The fast agent (decided LATER at 100) reached the book FIRST.
        assert first.agent_id == 1
        assert second.agent_id == 0
        assert first.decide_time > second.decide_time  # arrival order != decision order
        assert first.arrive_time < second.arrive_time
        # And the kernel recorded the inversion.
        assert count_latency_reorderings(result) >= 1

    def test_zero_latency_preserves_decision_order(self):
        # With equal (zero) latency the arrival order matches decision order.
        k = SimulationKernel("AAPL", seed=0)
        a0 = _ScriptedAgent(
            0, latency=0, orders=[{"side": "BUY", "type": "LIMIT", "price": 100.0, "quantity": 7}]
        )
        a1 = _ScriptedAgent(
            1, latency=0, orders=[{"side": "BUY", "type": "LIMIT", "price": 99.0, "quantity": 7}]
        )
        k.add_agent(a0, first_wake=0)
        k.add_agent(a1, first_wake=10)
        result = k.run(max_events=50)
        arr = [a for a in result.arrivals if a.quantity == 7]
        assert [a.agent_id for a in arr[:2]] == [0, 1]
        # Decisions in order -> no inversion among these two.
        assert arr[0].decide_time <= arr[1].decide_time

    def test_default_run_exhibits_reordering(self):
        result = simulate_agents(seed=42, steps=3000)
        assert count_latency_reorderings(result) > 0


# --------------------------------------------------------------------------
# Agents emit orders that flow through the REAL engine
# --------------------------------------------------------------------------


class TestAgentsHitRealEngine:
    def test_market_maker_quotes_both_sides_and_rests(self):
        k = SimulationKernel("AAPL", seed=1)
        mm = MarketMakerAgent(
            0, latency=0, wake_interval=10**9, symbol="AAPL", ref_price=150.0, half_spread=0.05
        )
        k.add_agent(mm, first_wake=0)
        result = k.run(max_events=10)
        # Both quotes rested (POST_ONLY, didn't cross an empty book).
        assert result.best_bid is not None and result.best_ask is not None
        assert result.best_bid < result.best_ask
        # Spread is ~2*half_spread.
        assert abs((result.best_ask - result.best_bid) - 0.10) < 1e-6

    def test_noise_agent_produces_one_order_per_wake(self):
        rng = al.random.Random(3)
        na = NoiseAgent(0, latency=5, wake_interval=100, symbol="AAPL", ref_price=150.0)
        book = ob.OrderBook("AAPL")
        orders = na.wake(0, book, rng)
        assert len(orders) == 1
        o = orders[0]
        assert o["side"] in ("BUY", "SELL")
        assert o["type"] in ("LIMIT", "MARKET")
        if o["type"] == "MARKET":
            assert o["price"] == 0.0
        else:
            assert o["price"] > 0.0

    def test_base_agent_wake_is_abstract(self):
        base = Agent(0, latency=0, wake_interval=1)
        with pytest.raises(NotImplementedError):
            base.wake(0, ob.OrderBook("AAPL"), al.random.Random(0))

    def test_noise_agent_handles_one_sided_book(self):
        rng = al.random.Random(0)
        na = NoiseAgent(
            0, latency=0, wake_interval=1, symbol="AAPL", ref_price=150.0, market_prob=0.0
        )
        # Bid-only book.
        bid_only = ob.OrderBook("AAPL")
        bid_only.add_order(ob.Order(1, "AAPL", ob.Side.BUY, ob.OrderType.LIMIT, 149.0, 10))
        assert na.wake(0, bid_only, rng)[0]["price"] > 0.0
        # Ask-only book.
        ask_only = ob.OrderBook("AAPL")
        ask_only.add_order(ob.Order(2, "AAPL", ob.Side.SELL, ob.OrderType.LIMIT, 151.0, 10))
        assert na.wake(0, ask_only, rng)[0]["price"] > 0.0

    def test_market_maker_handles_one_sided_book(self):
        rng = al.random.Random(0)
        mm = MarketMakerAgent(0, latency=0, wake_interval=1, symbol="AAPL", ref_price=150.0)
        bid_only = ob.OrderBook("AAPL")
        bid_only.add_order(ob.Order(1, "AAPL", ob.Side.BUY, ob.OrderType.LIMIT, 149.0, 10))
        q = mm.wake(0, bid_only, rng)
        assert q[0]["price"] < q[1]["price"]  # bid < ask
        ask_only = ob.OrderBook("AAPL")
        ask_only.add_order(ob.Order(2, "AAPL", ob.Side.SELL, ob.OrderType.LIMIT, 151.0, 10))
        q = mm.wake(0, ask_only, rng)
        assert q[0]["price"] < q[1]["price"]

    def test_full_run_yields_real_fills_and_coherent_book(self):
        result = simulate_agents(seed=7, steps=4000)
        assert isinstance(result, KernelResult)
        assert result.n_orders > 0
        assert result.n_trades > 0  # noise + makers cross -> real engine fills
        assert result.total_volume > 0
        if result.best_bid is not None and result.best_ask is not None:
            assert result.best_bid <= result.best_ask
        # Depth ladders are correctly sorted (read straight off the engine).
        bid_prices = [b["price"] for b in result.bids]
        ask_prices = [a["price"] for a in result.asks]
        assert bid_prices == sorted(bid_prices, reverse=True)
        assert ask_prices == sorted(ask_prices)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_identical_run(self):
        r1 = simulate_agents(seed=42, steps=3000)
        r2 = simulate_agents(seed=42, steps=3000)
        assert r1.trades == r2.trades
        assert [a.order_id for a in r1.arrivals] == [a.order_id for a in r2.arrivals]
        assert r1.best_bid == r2.best_bid
        assert r1.best_ask == r2.best_ask

    def test_different_seed_diverges(self):
        r1 = simulate_agents(seed=42, steps=3000)
        r2 = simulate_agents(seed=99, steps=3000)
        # Overwhelmingly likely to differ; assert on the trade tape.
        assert r1.trades != r2.trades


# --------------------------------------------------------------------------
# Builder + CLI
# --------------------------------------------------------------------------


class TestBuilderAndCli:
    def test_build_default_kernel_registers_all_agents(self):
        k = build_default_kernel(seed=5, n_noise=4, n_makers=3)
        assert len(k.agents) == 7
        assert sum(isinstance(a, NoiseAgent) for a in k.agents.values()) == 4
        assert sum(isinstance(a, MarketMakerAgent) for a in k.agents.values()) == 3

    def test_arrival_record_fields(self):
        r = simulate_agents(seed=1, steps=500)
        a = r.arrivals[0]
        assert isinstance(a, ArrivalRecord)
        assert a.arrive_time >= a.decide_time  # latency is non-negative
        assert a.order_id >= 1

    def test_event_kind_constants(self):
        assert WAKEUP != ARRIVAL

    def test_main_prints_summary(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["abides_lite.py", "--steps", "1500", "--seed", "3"])
        al.main()
        out = capsys.readouterr().out
        assert "ABIDES-lite:" in out
        assert "latency reorderings:" in out
        assert "trades executed" in out
