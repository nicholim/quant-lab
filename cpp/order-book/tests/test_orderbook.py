"""
Order book tests that run via subprocess against the compiled C++ demo.
Also tests the Python visualization and simulator modules.
"""

import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")
DEMO_BIN = os.path.join(BUILD_DIR, "order_book_demo")


@pytest.fixture(scope="module", autouse=True)
def build_cpp():
    """Build C++ before running tests."""
    os.makedirs(BUILD_DIR, exist_ok=True)
    result = subprocess.run(["cmake", ".."], cwd=BUILD_DIR, capture_output=True, text=True)
    assert result.returncode == 0, f"CMake failed: {result.stderr}"
    result = subprocess.run(["make"], cwd=BUILD_DIR, capture_output=True, text=True)
    assert result.returncode == 0, f"Make failed: {result.stderr}"
    yield
    # Cleanup
    import shutil

    if os.path.exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)


class TestMatchingEngine:
    def _run_demo(self) -> str:
        result = subprocess.run([DEMO_BIN], capture_output=True, text=True)
        assert result.returncode == 0, f"Demo crashed: {result.stderr}"
        return result.stdout

    def test_demo_runs_without_crash(self):
        output = self._run_demo()
        assert "=== Done ===" in output

    def test_trades_generated(self):
        output = self._run_demo()
        trade_lines = [line for line in output.split("\n") if "TRADE:" in line]
        assert len(trade_lines) == 5  # 1 market buy + 2 limit sell cross + 2 market sell sweep

    def test_market_buy_matches_best_ask(self):
        output = self._run_demo()
        # Step 3: Market BUY 80 should match at $150.50 (best ask)
        assert "80 @ $150.50" in output

    def test_limit_sell_crosses_spread(self):
        output = self._run_demo()
        # Step 4: Limit SELL at $149.50 crosses $150.00 bid, then $149.50 bid
        assert "150 @ $150.00" in output
        assert "50 @ $149.50" in output

    def test_cancellation_works(self):
        output = self._run_demo()
        assert "Cancelled: yes" in output

    def test_sweep_clears_bids(self):
        output = self._run_demo()
        # After step 6: should show 0 bids
        assert "Bids: 0 orders" in output

    def test_partial_fill_shown(self):
        output = self._run_demo()
        # After market buy of 80, ask at $150.50 should have qty=20 remaining
        assert "qty=20" in output


class TestPythonModules:
    def test_visualizer_import(self):
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))
        from visualizer import OrderBookVisualizer

        viz = OrderBookVisualizer()
        assert callable(viz.plot_depth_chart)
        assert callable(viz.plot_trade_tape)
        assert callable(viz.plot_spread_over_time)

    def test_simulator_generates_orders(self):
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))
        from simulator import MarketSimulator

        sim = MarketSimulator("TEST", 100.0, 0.5)
        orders = sim.generate_random_orders(100)
        assert len(orders) == 100
        assert all("side" in o for o in orders)
        assert all("type" in o for o in orders)
        assert all(o["quantity"] > 0 for o in orders)

    def test_simulator_side_distribution(self):
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))
        from simulator import MarketSimulator

        sim = MarketSimulator("TEST", 100.0, 0.5)
        orders = sim.generate_random_orders(1000)
        buys = sum(1 for o in orders if o["side"] == "BUY")
        # Should be roughly 50/50
        assert 300 < buys < 700

    def test_no_stop_order_type(self):
        """Verify STOP was removed from order.h."""
        header_path = os.path.join(PROJECT_ROOT, "include", "order.h")
        with open(header_path) as f:
            content = f.read()
        assert "STOP" not in content
