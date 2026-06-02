"""End-to-end: the simulator drives the REAL C++ engine via the binding.

These tests prove the Python simulation layer is wired onto the compiled
``orderbook`` matching engine — order flow is generated, submitted through
``OrderBook.add_order``, and the collected output (trades, spread, depth) is
genuine engine state. They also exercise the visualizer rendering that real
output headless (matplotlib Agg).
"""

import os
import sys

import matplotlib
import pytest

matplotlib.use("Agg")  # headless: before pyplot import anywhere
import matplotlib.pyplot as plt  # noqa: E402

pytest.importorskip(
    "orderbook",
    reason="compiled _orderbook extension not built — run cmake --build build first",
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))

import orderbook as ob  # noqa: E402
import simulator  # noqa: E402
from simulator import (  # noqa: E402
    EngineSimulator,
    MarketSimulator,
    SimulationResult,
    simulate,
)
from visualizer import OrderBookVisualizer  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


# --------------------------------------------------------------------------
# EngineSimulator drives the real C++ book
# --------------------------------------------------------------------------


class TestEngineSimulator:
    def test_run_produces_real_trades_and_coherent_book(self):
        result = simulate(800, seed=42)
        assert isinstance(result, SimulationResult)
        assert result.n_orders == 800
        # The flow crosses the book -> real fills from the C++ engine.
        assert result.n_trades > 0
        assert result.total_volume > 0
        # Core matching-engine invariant: best_bid <= best_ask when both exist.
        if result.best_bid is not None and result.best_ask is not None:
            assert result.best_bid <= result.best_ask

    def test_depth_is_sorted_and_sums_sane(self):
        result = simulate(800, seed=7)
        bid_prices = [b["price"] for b in result.bids]
        ask_prices = [a["price"] for a in result.asks]
        assert bid_prices == sorted(bid_prices, reverse=True)  # desc
        assert ask_prices == sorted(ask_prices)  # asc
        # Best bid/ask agree with the top of each depth ladder.
        if result.bids:
            assert result.best_bid == bid_prices[0]
        if result.asks:
            assert result.best_ask == ask_prices[0]
        # Every level carries positive quantity and >=1 order.
        for level in result.bids + result.asks:
            assert level["quantity"] > 0
            assert level["order_count"] >= 1
        # Spread series is non-negative (best_ask - best_bid each sampled step).
        assert all(s >= 0 for s in result.spreads)

    def test_uses_the_real_engine_not_a_python_book(self):
        # Hand-built flow with a known crossing -> assert against the C++ output.
        flow = [
            {"id": 1, "symbol": "AAPL", "side": "SELL", "type": "LIMIT", "price": 150.0, "qty": 0},
            {"id": 2, "symbol": "AAPL", "side": "BUY", "type": "LIMIT", "price": 150.0, "qty": 0},
        ]
        # Normalize to the expected key.
        flow[0]["quantity"] = 100
        flow[1]["quantity"] = 60
        result = EngineSimulator("AAPL").run(flow)
        assert result.n_trades == 1
        assert result.trades[0]["price"] == 150.0
        assert result.trades[0]["quantity"] == 60
        assert result.trades[0]["buyer_order_id"] == 2
        assert result.trades[0]["seller_order_id"] == 1
        # 40 of the seller remains resting on the ask.
        assert result.best_ask == 150.0
        assert result.asks[0]["quantity"] == 40
        assert result.best_bid is None

    def test_tif_orders_run_through_engine(self):
        # use_tif sprinkles IOC/FOK/POST_ONLY -> those engine paths execute.
        result = simulate(600, seed=11, use_tif=True)
        assert result.n_orders == 600
        if result.best_bid is not None and result.best_ask is not None:
            assert result.best_bid <= result.best_ask

    def test_order_dict_maps_all_tifs(self):
        sim = EngineSimulator("AAPL")
        for tif in ("GTC", "IOC", "FOK", "POST_ONLY"):
            o = sim._to_order(
                {"id": 1, "side": "BUY", "type": "LIMIT", "price": 1.0, "quantity": 5, "tif": tif}
            )
            assert isinstance(o, ob.Order)
            assert o.tif == getattr(ob.TimeInForce, tif)

    def test_empty_flow(self):
        result = EngineSimulator("AAPL").run([])
        assert result.n_orders == 0
        assert result.n_trades == 0
        assert result.best_bid is None and result.best_ask is None
        assert result.bids == [] and result.asks == []


# --------------------------------------------------------------------------
# MarketSimulator flow generator (descriptors only, no matching)
# --------------------------------------------------------------------------


class TestFlowGenerator:
    def test_market_orders_zero_price_limits_positive(self):
        import random

        random.seed(0)
        orders = MarketSimulator("X", 100.0, 0.5).generate_random_orders(300)
        for o in orders:
            if o["type"] == "MARKET":
                assert o["price"] == 0.0
            else:
                assert o["price"] > 0.0

    def test_reproducible_with_seed(self):
        import random

        random.seed(123)
        a = MarketSimulator("X", 100.0, 0.5).generate_random_orders(40)
        random.seed(123)
        b = MarketSimulator("X", 100.0, 0.5).generate_random_orders(40)
        assert a == b

    def test_use_tif_emits_non_gtc(self):
        import random

        random.seed(5)
        orders = MarketSimulator().generate_random_orders(500, use_tif=True)
        tifs = {o["tif"] for o in orders}
        assert tifs - {"GTC"}  # at least one IOC/FOK/POST_ONLY present
        # MARKET orders never carry a special TIF.
        for o in orders:
            if o["type"] == "MARKET":
                assert o["tif"] == "GTC"


# --------------------------------------------------------------------------
# Visualizer renders real engine output headless
# --------------------------------------------------------------------------


class TestVisualizeRealOutput:
    def test_plot_simulation_writes_all_charts(self, tmp_path):
        result = simulate(800, seed=42)
        OrderBookVisualizer.plot_simulation(result, out_dir=str(tmp_path))
        # Depth always renders; tape/spread render when there is data.
        assert (tmp_path / "depth.png").exists()
        if result.trades:
            assert (tmp_path / "trade_tape.png").exists()
        if result.spreads:
            assert (tmp_path / "spread.png").exists()

    def test_plot_simulation_show_path_no_outdir(self):
        # out_dir=None hits the plt.show() branch (no-op under Agg) without error.
        result = simulate(200, seed=1)
        OrderBookVisualizer.plot_simulation(result, out_dir=None)

    def test_plot_simulation_empty_book(self, tmp_path):
        result = EngineSimulator("AAPL").run([])
        OrderBookVisualizer.plot_simulation(result, out_dir=str(tmp_path))
        assert (tmp_path / "depth.png").exists()  # empty depth still renders
        assert not (tmp_path / "trade_tape.png").exists()
        assert not (tmp_path / "spread.png").exists()


# --------------------------------------------------------------------------
# CLI entry point drives the engine end-to-end
# --------------------------------------------------------------------------


class TestCli:
    def test_main_summary(self, monkeypatch, capsys):
        monkeypatch.setattr(
            sys, "argv", ["simulator.py", "--orders", "300", "--seed", "3", "--tif"]
        )
        simulator.main()
        out = capsys.readouterr().out
        assert "Simulated 300 orders through the C++ engine" in out
        assert "trades executed:" in out

    def test_main_with_plot(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr(
            sys,
            "argv",
            ["simulator.py", "--orders", "400", "--seed", "9", "--plot", str(tmp_path)],
        )
        simulator.main()
        out = capsys.readouterr().out
        assert "charts written to" in out
        assert (tmp_path / "depth.png").exists()
