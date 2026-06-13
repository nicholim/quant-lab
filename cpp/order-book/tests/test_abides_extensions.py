"""Tests for the ABIDES-lite extensions: L3 event tape, cancel events with
latency, kernel fill-attribution, the Avellaneda-Stoikov maker, and the
default-demo byte-parity guard.

All matching is still done by the real C++ engine through the binding — these
tests only verify the Python scheduling/attribution layer and the A-S quote
math, never a re-implemented book.
"""

import os
import subprocess
import sys

import pytest

pytest.importorskip(
    "orderbook",
    reason="compiled _orderbook extension not built — run cmake --build build first",
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))

import abides_lite as al  # noqa: E402
from abides_lite import (  # noqa: E402
    TAPE_CANCEL,
    TAPE_ORDER,
    TAPE_TRADE,
    Agent,
    AvellanedaStoikovAgent,
    SimulationKernel,
    compare_market_makers,
    simulate_agents,
)


class _ScriptedAgent(Agent):
    """Emits a fixed list of actions on first wake, then nothing."""

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
# L3 event tape
# --------------------------------------------------------------------------


class TestEventTape:
    def test_order_and_trade_records_with_attribution(self):
        k = SimulationKernel("AAPL", seed=0)
        # Agent 0 rests a sell; agent 1 crosses it with a buy.
        k.schedule_arrival(
            10, 0, {"side": "SELL", "type": "LIMIT", "price": 100.0, "quantity": 10}, 0
        )
        k.schedule_arrival(
            20, 1, {"side": "BUY", "type": "LIMIT", "price": 100.0, "quantity": 10}, 10
        )
        result = k.run()

        kinds = [r.kind for r in result.tape]
        assert kinds.count(TAPE_ORDER) == 2
        assert kinds.count(TAPE_TRADE) == 1

        trade = next(r for r in result.tape if r.kind == TAPE_TRADE)
        # Taker is the aggressive buyer (agent 1); maker is the resting seller (0).
        assert trade.taker_id == 1
        assert trade.maker_id == 0
        assert trade.side == "BUY"
        assert trade.quantity == 10

    def test_inventory_attribution_on_fill(self):
        k = SimulationKernel("AAPL", seed=0)
        k.schedule_arrival(
            10, 0, {"side": "SELL", "type": "LIMIT", "price": 100.0, "quantity": 10}, 0
        )
        k.schedule_arrival(
            20, 1, {"side": "BUY", "type": "LIMIT", "price": 100.0, "quantity": 10}, 10
        )
        result = k.run()
        # Buyer (1) is long +10; seller (0) is short -10.
        assert result.agent_inventory[1] == 10
        assert result.agent_inventory[0] == -10

    def test_tape_records_cancel(self):
        k = SimulationKernel("AAPL", seed=0)
        k.schedule_arrival(
            10, 0, {"side": "BUY", "type": "LIMIT", "price": 100.0, "quantity": 10}, 0
        )
        k.run(until=15)  # let the order rest; order_id is 1
        k.schedule_cancel(20, 0, 1)
        result = k.run()
        cancels = [r for r in result.tape if r.kind == TAPE_CANCEL]
        assert len(cancels) == 1
        assert cancels[0].order_id == 1
        assert cancels[0].quantity == 1  # removed
        assert k.book.get_best_bid() is None  # the resting bid is gone


# --------------------------------------------------------------------------
# Cancel-with-latency ordering
# --------------------------------------------------------------------------


class TestCancelLatency:
    def test_cancel_applies_in_arrival_time_order(self):
        # An order rests at t=10; a cancel emitted earlier but with big latency
        # must still be processed AFTER an intervening fill that arrives sooner.
        k = SimulationKernel("AAPL", seed=0)
        # Resting sell from agent 0.
        k.schedule_arrival(
            10, 0, {"side": "SELL", "type": "LIMIT", "price": 100.0, "quantity": 10}, 0
        )
        # Cancel of order_id 1 scheduled to land at t=100.
        k.schedule_cancel(100, 0, 1)
        # A crossing buy lands at t=50 (before the cancel) and fills the sell.
        k.schedule_arrival(
            50, 1, {"side": "BUY", "type": "LIMIT", "price": 100.0, "quantity": 10}, 40
        )
        result = k.run()
        # The fill happened (buy crossed before cancel).
        assert result.n_trades == 1
        # The later cancel found nothing left to remove.
        cancel = next(r for r in result.tape if r.kind == TAPE_CANCEL)
        assert cancel.time == 100
        assert cancel.quantity == 0  # not found — already fully filled

    def test_agent_emitted_cancel_routes_with_latency(self):
        k = SimulationKernel("AAPL", seed=0)
        # First wake: rest a bid. Second wake: cancel it.
        rester = _ScriptedAgent(
            0,
            latency=5,
            orders=[{"side": "BUY", "type": "LIMIT", "price": 100.0, "quantity": 10}],
        )
        k.add_agent(rester, first_wake=0)
        k.run(until=50)  # order rests (id 1)
        canceller = _ScriptedAgent(0, latency=5, orders=[{"cancel": 1}])
        k.agents[0] = canceller  # replace so its wake emits a cancel
        k._push(60, al.WAKEUP, 0)
        result = k.run(until=70)
        cancels = [r for r in result.tape if r.kind == TAPE_CANCEL]
        assert len(cancels) == 1
        assert cancels[0].time == 65  # decide 60 + latency 5
        assert cancels[0].quantity == 1


# --------------------------------------------------------------------------
# Avellaneda-Stoikov quote math
# --------------------------------------------------------------------------


class TestAvellanedaStoikovMath:
    def _agent(self):
        return AvellanedaStoikovAgent(
            0,
            latency=0,
            wake_interval=1,
            symbol="AAPL",
            ref_price=100.0,
            gamma=0.1,
            k=1.5,
            horizon=1000,
        )

    def test_reservation_skews_below_mid_when_long(self):
        a = self._agent()
        a._mids = [100.0, 100.5, 99.5, 100.2]  # gives a positive sigma
        r_long, _, _, _ = a.compute_quotes(100.0, q=10, tau=1.0)
        r_flat, _, _, _ = a.compute_quotes(100.0, q=0, tau=1.0)
        r_short, _, _, _ = a.compute_quotes(100.0, q=-10, tau=1.0)
        assert r_long < r_flat < r_short  # long -> quote lower to sell down
        assert r_flat == pytest.approx(100.0)

    def test_half_spread_shrinks_as_tau_to_zero(self):
        a = self._agent()
        a._mids = [100.0, 101.0, 99.0, 100.5]
        _, hs_far, _, _ = a.compute_quotes(100.0, q=0, tau=1.0)
        _, hs_near, _, _ = a.compute_quotes(100.0, q=0, tau=0.01)
        assert hs_near < hs_far
        # As tau -> 0 the spread tends to the liquidity term ln(1+gamma/k)/gamma.
        import math

        floor = math.log1p(a.gamma / a.k) / a.gamma
        _, hs_zero, _, _ = a.compute_quotes(100.0, q=0, tau=0.0)
        assert hs_zero == pytest.approx(floor)

    def test_wake_cancels_stale_quotes_before_requoting(self):
        a = self._agent()
        # Pretend two quotes are resting.
        a._live_quotes = [7, 8]
        actions = a.wake(0, al.ob.OrderBook("AAPL"), al.random.Random(0))
        cancels = [x for x in actions if "cancel" in x]
        assert {c["cancel"] for c in cancels} == {7, 8}
        assert a._live_quotes == []  # cleared

    def test_inventory_updates_from_fills(self):
        a = self._agent()
        a.on_fill(1, "BUY", 100.0, 5, now=0)
        a.on_fill(2, "SELL", 100.0, 2, now=0)
        assert a.inventory == 3


# --------------------------------------------------------------------------
# Fill-attribution end-to-end: a resting MM quote gets hit
# --------------------------------------------------------------------------


class TestMakerFillAttribution:
    def test_resting_as_quote_hit_updates_inventory(self):
        k = SimulationKernel("AAPL", seed=0)
        mm = AvellanedaStoikovAgent(
            0,
            latency=0,
            wake_interval=10**9,
            symbol="AAPL",
            ref_price=100.0,
            gamma=0.1,
            k=1.5,
            horizon=10**9,
            quote_size=10,
        )
        k.add_agent(mm, first_wake=0)
        k.run(until=1)  # MM posts a bid and an ask
        bid = k.book.get_best_bid()
        assert bid is not None
        # An aggressor sells into the MM's resting bid.
        k.schedule_arrival(
            2, 99, {"side": "SELL", "type": "LIMIT", "price": bid, "quantity": 10}, 1
        )
        result = k.run(until=3)
        assert result.n_trades >= 1
        # The MM bought (its bid was hit) -> long inventory; tracked on the agent.
        assert mm.inventory == 10
        assert result.agent_inventory[0] == 10


# --------------------------------------------------------------------------
# Comparison + determinism + parity
# --------------------------------------------------------------------------


class TestComparisonAndParity:
    def test_compare_market_makers_deterministic(self):
        r1 = compare_market_makers(seed=42, steps=2000)
        r2 = compare_market_makers(seed=42, steps=2000)
        assert r1["avellaneda_stoikov"].trades == r2["avellaneda_stoikov"].trades
        assert r1["naive"].trades == r2["naive"].trades

    def test_as_controls_inventory_better_than_naive(self):
        res = compare_market_makers(seed=42, steps=4000)
        as_inv = abs(res["avellaneda_stoikov"].agent_inventory.get(6, 0))
        naive_inv = abs(res["naive"].agent_inventory.get(6, 0))
        # A-S skews quotes against inventory, so it holds far less than the naive
        # maker that has no inventory control.
        assert as_inv < naive_inv

    def test_extensions_do_not_break_default_run(self):
        # The tape/inventory are additive; the existing summary fields hold.
        result = simulate_agents(seed=42, steps=4000)
        assert result.n_orders == 2249
        assert result.n_trades == 998
        assert result.total_volume == 28225
        assert len(result.tape) > 0

    def test_default_demo_byte_parity(self):
        # The default main() path must stay byte-identical to the recorded
        # baseline (additive features consume zero RNG on the default path).
        out = subprocess.run(
            [
                sys.executable,
                os.path.join(PROJECT_ROOT, "python", "abides_lite.py"),
                "--steps",
                "4000",
                "--seed",
                "42",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        expected = (
            "ABIDES-lite: 6 noise + 2 maker agents on AAPL\n"
            "  events processed : 4000\n"
            "  orders arrived   : 2249\n"
            "  trades executed  : 998  (volume 28225)\n"
            "  best bid/ask     : 150.0 / 150.04\n"
            "  final spread     : 0.04\n"
            "  latency reorderings: 799 arrivals landed out of decision order "
            "(arrival order is driven by latency, not submission order)\n"
        )
        assert out == expected
