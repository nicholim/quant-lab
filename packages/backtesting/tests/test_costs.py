"""Tests for the pluggable commission/slippage model library (src/costs.py)
and its integration into SimulatedExecution.

Key guarantee: SimulatedExecution with no injected models produces fills that
are BYTE-IDENTICAL to the prior float-only behavior; injected models change the
computed commission/slippage as expected.
"""

from datetime import datetime

from src.costs import (
    FixedBpsSlippage,
    FixedCommission,
    PercentCommission,
    PercentSlippage,
    PerShareCommission,
)
from src.events import Direction, OrderEvent, OrderType
from src.execution import SimulatedExecution


def _order(symbol="X", qty=100, direction=Direction.BUY):
    return OrderEvent(
        timestamp=datetime(2021, 1, 1),
        symbol=symbol,
        quantity=qty,
        order_type=OrderType.MARKET,
        direction=direction,
    )


# --- commission models ----------------------------------------------------


class TestCommissionModels:
    def test_percent_commission(self):
        m = PercentCommission(0.001)
        assert abs(m.commission(50.0, 100) - 50.0 * 100 * 0.001) < 1e-12

    def test_per_share_commission(self):
        m = PerShareCommission(per_share=0.005)
        assert abs(m.commission(50.0, 200) - 0.005 * 200) < 1e-12

    def test_per_share_minimum_floor(self):
        m = PerShareCommission(per_share=0.005, minimum=1.0)
        # 0.005 * 100 = 0.5 < 1.0 -> floored to the minimum
        assert m.commission(50.0, 100) == 1.0
        # 0.005 * 500 = 2.5 > 1.0 -> above the floor
        assert abs(m.commission(50.0, 500) - 2.5) < 1e-12

    def test_fixed_commission(self):
        m = FixedCommission(2.5)
        assert m.commission(50.0, 100) == 2.5
        assert m.commission(999.0, 1) == 2.5  # independent of price/qty


# --- slippage models ------------------------------------------------------


class TestSlippageModels:
    def test_percent_slippage_direction(self):
        m = PercentSlippage(0.01)
        assert abs(m.adjust(100.0, Direction.BUY) - 101.0) < 1e-12  # buy slips up
        assert abs(m.adjust(100.0, Direction.SELL) - 99.0) < 1e-12  # sell slips down

    def test_fixed_bps_slippage_direction(self):
        m = FixedBpsSlippage(50)  # 50 bps = 0.5%
        assert abs(m.adjust(100.0, Direction.BUY) - 100.5) < 1e-12
        assert abs(m.adjust(100.0, Direction.SELL) - 99.5) < 1e-12


# --- SimulatedExecution integration ---------------------------------------


class TestExecutionIntegration:
    def test_default_models_match_float_behavior(self):
        """No models injected -> PercentCommission/PercentSlippage from the floats."""
        ex = SimulatedExecution(commission_pct=0.001, slippage_pct=0.0005)
        assert isinstance(ex.commission_model, PercentCommission)
        assert isinstance(ex.slippage_model, PercentSlippage)
        assert ex.commission_model.pct == 0.001
        assert ex.slippage_model.pct == 0.0005

    def test_fill_byte_identical_to_legacy_formula(self):
        """Default fill reproduces the old inline formula exactly (regression)."""
        ex = SimulatedExecution(commission_pct=0.001, slippage_pct=0.0005)
        base_price = 100.0
        order = _order(qty=100, direction=Direction.BUY)
        fill_price = ex._apply_slippage(base_price, order.direction)
        fill = ex._fill(order, fill_price, base_price=base_price, timestamp=order.timestamp)

        # Legacy: slippage = price*(1+pct); commission = fill_price*qty*pct.
        legacy_fill_price = base_price * (1 + 0.0005)
        legacy_commission = legacy_fill_price * 100 * 0.001
        legacy_slip = abs(legacy_fill_price - base_price) * 100
        assert fill.price == legacy_fill_price
        assert fill.commission == legacy_commission
        assert fill.slippage == legacy_slip

    def test_injected_commission_model_changes_cost(self):
        ex = SimulatedExecution(commission_model=FixedCommission(3.0))
        order = _order(qty=100, direction=Direction.BUY)
        fill = ex._fill(order, 100.0, base_price=100.0, timestamp=order.timestamp)
        assert fill.commission == 3.0  # not the percentage default

    def test_injected_slippage_model_changes_price(self):
        ex = SimulatedExecution(slippage_model=FixedBpsSlippage(100))  # 1%
        assert abs(ex._apply_slippage(100.0, Direction.BUY) - 101.0) < 1e-12
        assert abs(ex._apply_slippage(100.0, Direction.SELL) - 99.0) < 1e-12

    def test_per_share_model_with_minimum_in_fill(self):
        ex = SimulatedExecution(commission_model=PerShareCommission(0.005, minimum=1.0))
        small = _order(qty=50)
        big = _order(qty=1000)
        small_fill = ex._fill(small, 100.0, base_price=100.0, timestamp=small.timestamp)
        big_fill = ex._fill(big, 100.0, base_price=100.0, timestamp=big.timestamp)
        assert small_fill.commission == 1.0  # 0.25 floored to 1.0
        assert big_fill.commission == 5.0  # 0.005 * 1000

    def test_injected_models_override_floats(self):
        """When a model is passed the corresponding float is ignored."""
        ex = SimulatedExecution(
            commission_pct=0.001,
            slippage_pct=0.0005,
            commission_model=FixedCommission(7.0),
            slippage_model=FixedBpsSlippage(10),
        )
        order = _order(qty=10)
        fill = ex._fill(order, 100.0, base_price=100.0, timestamp=order.timestamp)
        assert fill.commission == 7.0
        assert abs(ex._apply_slippage(100.0, Direction.BUY) - 100.1) < 1e-12
