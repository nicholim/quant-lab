"""Binding-driven tests: drive the C++ matching engine directly from Python.

These import the compiled pybind11 `_orderbook` extension (via the `orderbook`
re-export package) and submit orders / read book state in-process — replacing
the old subprocess-stdout-parsing approach against the `order_book_demo` binary.
Build the extension first: `cmake -S . -B build && cmake --build build`.

Exercises MARKET/LIMIT crossing + resting, multi-level sweeps, cancel/modify,
depth + best bid/ask queries, the MatchingEngine multi-symbol router, and the
IOC / FOK / POST_ONLY time-in-force paths through the binding.
"""

import pytest

pytest.importorskip(
    "orderbook",
    reason="compiled _orderbook extension not built — run cmake --build build first",
)

from orderbook import (  # noqa: E402
    MatchingEngine,
    Order,
    OrderBook,
    OrderType,
    Side,
    TimeInForce,
)


def limit(oid, side, price, qty, tif=TimeInForce.GTC):
    return Order(oid, "AAPL", side, OrderType.LIMIT, price, qty, tif)


def market(oid, side, qty, tif=TimeInForce.GTC):
    return Order(oid, "AAPL", side, OrderType.MARKET, 0.0, qty, tif)


@pytest.fixture
def book():
    return OrderBook("AAPL")


# --------------------------------------------------------------------------
# Basic matching: crossing, resting, partial fills
# --------------------------------------------------------------------------


class TestBasicMatching:
    def test_resting_limit_order_no_fill(self, book):
        trades = book.add_order(limit(1, Side.BUY, 149.0, 100))
        assert trades == []
        assert book.bid_count() == 1
        assert book.get_best_bid() == 149.0
        assert book.get_best_ask() is None
        assert book.get_volume_at_price(149.0) == 100

    def test_crossing_limit_full_fill(self, book):
        book.add_order(limit(1, Side.SELL, 150.0, 100))
        trades = book.add_order(limit(2, Side.BUY, 150.0, 100))
        assert len(trades) == 1
        assert trades[0].price == 150.0
        assert trades[0].quantity == 100
        assert trades[0].buyer_order_id == 2
        assert trades[0].seller_order_id == 1
        # Both fully consumed -> empty book.
        assert book.bid_count() == 0
        assert book.ask_count() == 0

    def test_partial_fill_leaves_remainder_resting(self, book):
        book.add_order(limit(1, Side.SELL, 150.0, 100))
        trades = book.add_order(limit(2, Side.BUY, 150.0, 60))
        assert len(trades) == 1
        assert trades[0].quantity == 60
        # 40 of the seller remains resting on the ask.
        assert book.ask_count() == 1
        assert book.get_volume_at_price(150.0) == 40
        assert book.bid_count() == 0

    def test_market_buy_matches_best_ask(self, book):
        book.add_order(limit(1, Side.SELL, 150.50, 100))
        book.add_order(limit(2, Side.SELL, 151.00, 100))
        trades = book.add_order(market(3, Side.BUY, 80))
        assert len(trades) == 1
        assert trades[0].price == 150.50
        assert trades[0].quantity == 80
        assert book.get_volume_at_price(150.50) == 20

    def test_market_sell_sweeps_multiple_bid_levels(self, book):
        book.add_order(limit(1, Side.BUY, 150.0, 150))
        book.add_order(limit(2, Side.BUY, 149.5, 200))
        trades = book.add_order(market(3, Side.SELL, 300))
        # 150 @ 150.0 then 150 @ 149.5
        assert len(trades) == 2
        assert trades[0].price == 150.0 and trades[0].quantity == 150
        assert trades[1].price == 149.5 and trades[1].quantity == 150
        # First level cleared, second has 50 left.
        assert book.get_volume_at_price(150.0) == 0
        assert book.get_volume_at_price(149.5) == 50

    def test_price_time_priority_fifo(self, book):
        # Two resting bids at the same price; earliest gets filled first.
        book.add_order(limit(1, Side.BUY, 150.0, 100))
        book.add_order(limit(2, Side.BUY, 150.0, 100))
        trades = book.add_order(limit(3, Side.SELL, 150.0, 100))
        assert len(trades) == 1
        assert trades[0].buyer_order_id == 1  # FIFO: order 1 first


# --------------------------------------------------------------------------
# Cancel / modify / depth queries
# --------------------------------------------------------------------------


class TestCancelModifyDepth:
    def test_cancel_removes_resting_order(self, book):
        book.add_order(limit(1, Side.BUY, 149.0, 100))
        assert book.cancel_order(1) is True
        assert book.bid_count() == 0
        assert book.cancel_order(999) is False  # unknown id

    def test_modify_changes_price_and_qty(self, book):
        book.add_order(limit(1, Side.BUY, 149.0, 100))
        assert book.modify_order(1, 50, 148.0) is True
        assert book.get_best_bid() == 148.0
        assert book.get_volume_at_price(148.0) == 50
        assert book.modify_order(999, 10, 1.0) is False

    def test_depth_levels_and_spread(self, book):
        book.add_order(limit(1, Side.BUY, 149.0, 100))
        book.add_order(limit(2, Side.BUY, 148.0, 200))
        book.add_order(limit(3, Side.SELL, 151.0, 50))
        bids = book.get_bid_depth(10)
        asks = book.get_ask_depth(10)
        assert [d.price for d in bids] == [149.0, 148.0]  # desc
        assert bids[0].total_quantity == 100
        assert bids[1].order_count == 1
        assert asks[0].price == 151.0
        assert book.get_spread() == pytest.approx(2.0)


# --------------------------------------------------------------------------
# MatchingEngine multi-symbol router
# --------------------------------------------------------------------------


class TestMatchingEngine:
    def test_routes_per_symbol(self):
        eng = MatchingEngine()
        eng.submit_order(Order(1, "AAPL", Side.SELL, OrderType.LIMIT, 150.0, 100))
        eng.submit_order(Order(2, "MSFT", Side.SELL, OrderType.LIMIT, 300.0, 50))
        assert sorted(eng.get_symbols()) == ["AAPL", "MSFT"]
        # An AAPL buy only touches the AAPL book.
        trades = eng.submit_order(Order(3, "AAPL", Side.BUY, OrderType.LIMIT, 150.0, 100))
        assert len(trades) == 1 and trades[0].symbol == "AAPL"
        assert eng.get_order_book("MSFT").ask_count() == 1

    def test_engine_cancel(self):
        eng = MatchingEngine()
        eng.submit_order(Order(1, "AAPL", Side.BUY, OrderType.LIMIT, 150.0, 100))
        assert eng.cancel_order("AAPL", 1) is True
        assert eng.cancel_order("NOPE", 1) is False
        assert eng.get_order_book("AAPL").bid_count() == 0


# --------------------------------------------------------------------------
# Time-in-force: IOC / FOK / POST_ONLY reachable through the binding
# --------------------------------------------------------------------------


class TestTimeInForce:
    def test_ioc_partial_fill_cancels_remainder(self, book):
        book.add_order(limit(1, Side.SELL, 150.0, 40))
        # IOC buy for 100: fills 40, cancels the other 60 (never rests).
        trades = book.add_order(limit(2, Side.BUY, 150.0, 100, TimeInForce.IOC))
        assert len(trades) == 1
        assert trades[0].quantity == 40
        assert book.bid_count() == 0  # remainder NOT resting
        assert book.ask_count() == 0

    def test_ioc_no_liquidity_fully_cancelled(self, book):
        trades = book.add_order(limit(1, Side.BUY, 150.0, 100, TimeInForce.IOC))
        assert trades == []
        assert book.bid_count() == 0  # nothing rests

    def test_fok_fills_completely_or_kills(self, book):
        book.add_order(limit(1, Side.SELL, 150.0, 60))
        # FOK buy for 100 but only 60 available -> kill, book untouched.
        trades = book.add_order(limit(2, Side.BUY, 150.0, 100, TimeInForce.FOK))
        assert trades == []
        assert book.ask_count() == 1
        assert book.get_volume_at_price(150.0) == 60
        # FOK buy for 60 -> fully fillable, executes.
        trades = book.add_order(limit(3, Side.BUY, 150.0, 60, TimeInForce.FOK))
        assert len(trades) == 1
        assert trades[0].quantity == 60
        assert book.ask_count() == 0

    def test_post_only_rejected_when_crossing(self, book):
        book.add_order(limit(1, Side.SELL, 150.0, 100))
        # POST_ONLY buy at the ask would take liquidity -> rejected (no trade,
        # nothing rests).
        trades = book.add_order(limit(2, Side.BUY, 150.0, 100, TimeInForce.POST_ONLY))
        assert trades == []
        assert book.bid_count() == 0
        assert book.ask_count() == 1  # resting sell untouched

    def test_post_only_rests_when_not_crossing(self, book):
        book.add_order(limit(1, Side.SELL, 150.0, 100))
        # POST_ONLY buy below the ask is a pure maker -> rests, no trade.
        trades = book.add_order(limit(2, Side.BUY, 149.0, 100, TimeInForce.POST_ONLY))
        assert trades == []
        assert book.bid_count() == 1
        assert book.get_best_bid() == 149.0
