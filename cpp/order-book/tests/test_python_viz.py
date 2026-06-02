"""Edge-case coverage for the Python viz + simulator modules.

These are pure-Python tests (no C++ build needed). Matplotlib runs headless
via the Agg backend, under which the visualizer's plt.show() calls are no-ops;
we exercise the save_path branch into a tmp dir so plotting code paths are
covered deterministically.
"""

import json
import os
import sys

import matplotlib
import numpy as np
import pytest

matplotlib.use("Agg")  # headless: must be set before pyplot is imported
import matplotlib.pyplot as plt  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))

from simulator import MarketSimulator  # noqa: E402
from visualizer import OrderBookVisualizer  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figures():
    yield
    plt.close("all")


# --------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------


class TestSimulator:
    def test_reproducible_with_seed(self):
        import random

        random.seed(42)
        np.random.seed(42)
        a = MarketSimulator("X", 100.0, 0.5).generate_random_orders(50)

        random.seed(42)
        np.random.seed(42)
        b = MarketSimulator("X", 100.0, 0.5).generate_random_orders(50)
        assert a == b

    def test_market_orders_have_zero_price(self):
        np.random.seed(0)
        import random

        random.seed(0)
        orders = MarketSimulator("X", 100.0, 0.5).generate_random_orders(500)
        for o in orders:
            if o["type"] == "MARKET":
                assert o["price"] == 0.0
            else:
                assert o["price"] > 0.0

    def test_ids_are_unique_and_sequential(self):
        orders = MarketSimulator().generate_random_orders(20)
        ids = [o["id"] for o in orders]
        assert ids == list(range(1, 21))

    def test_zero_orders_returns_empty(self):
        assert MarketSimulator().generate_random_orders(0) == []

    def test_symbol_propagates(self):
        orders = MarketSimulator(symbol="MSFT").generate_random_orders(5)
        assert all(o["symbol"] == "MSFT" for o in orders)

    def test_save_orders_roundtrip(self, tmp_path, capsys):
        sim = MarketSimulator("X")
        orders = sim.generate_random_orders(10)
        path = tmp_path / "orders.json"
        sim.save_orders(orders, str(path))
        assert path.exists()
        with open(path) as f:
            loaded = json.load(f)
        assert loaded == orders
        assert "Saved 10 orders" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Visualizer
# --------------------------------------------------------------------------


class TestVisualizer:
    def _bids_asks(self):
        bids = [{"price": 100.0, "quantity": 150}, {"price": 99.5, "quantity": 200}]
        asks = [{"price": 100.5, "quantity": 100}, {"price": 101.0, "quantity": 50}]
        return bids, asks

    def test_depth_chart_saves_file(self, tmp_path):
        bids, asks = self._bids_asks()
        out = tmp_path / "depth.png"
        OrderBookVisualizer.plot_depth_chart(bids, asks, save_path=str(out))
        assert out.exists() and out.stat().st_size > 0

    def test_depth_chart_empty_book(self, tmp_path):
        # Both sides empty -> the `if bids`/`if asks` branches are skipped.
        out = tmp_path / "empty.png"
        OrderBookVisualizer.plot_depth_chart([], [], save_path=str(out))
        assert out.exists()

    def test_depth_chart_bids_only(self, tmp_path):
        bids, _ = self._bids_asks()
        out = tmp_path / "bids.png"
        OrderBookVisualizer.plot_depth_chart(bids, [], save_path=str(out))
        assert out.exists()

    def test_trade_tape_saves_file(self, tmp_path):
        trades = [{"price": 100.0 + i * 0.1, "quantity": 10 + i} for i in range(20)]
        out = tmp_path / "tape.png"
        OrderBookVisualizer.plot_trade_tape(trades, save_path=str(out))
        assert out.exists() and out.stat().st_size > 0

    def test_spread_over_time_saves_file(self, tmp_path):
        spreads = [0.5 + 0.01 * i for i in range(30)]
        out = tmp_path / "spread.png"
        OrderBookVisualizer.plot_spread_over_time(spreads, save_path=str(out))
        assert out.exists() and out.stat().st_size > 0
