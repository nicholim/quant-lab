"""Python interface to the C++ price-time-priority matching engine.

This is a thin re-export of the compiled pybind11 extension ``_orderbook``
(built by CMake into this directory via ``pybind11_add_module``). It lets you
drive the engine directly from Python::

    from orderbook import MatchingEngine, Order, Side, OrderType, TimeInForce

    engine = MatchingEngine()
    engine.submit_order(Order(1, "AAPL", Side.SELL, OrderType.LIMIT, 150.0, 100))
    trades = engine.submit_order(
        Order(2, "AAPL", Side.BUY, OrderType.LIMIT, 150.0, 60)
    )
    # trades[0].price == 150.0, trades[0].quantity == 60

If the import below fails, the extension hasn't been built yet. Build it with::

    cmake -S . -B build && cmake --build build

from the ``cpp/order-book`` directory; the module lands here automatically.
"""

from __future__ import annotations

try:
    from ._orderbook import (  # type: ignore[import-not-found]
        DepthLevel,
        MatchingEngine,
        Order,
        OrderBook,
        OrderType,
        Side,
        TimeInForce,
        Trade,
    )
except ImportError as exc:  # pragma: no cover - exercised only pre-build
    raise ImportError(
        "The compiled '_orderbook' extension is not available. Build it with "
        "`cmake -S . -B build && cmake --build build` from cpp/order-book/ "
        "(the module is emitted into python/orderbook/)."
    ) from exc

__all__ = [
    "DepthLevel",
    "MatchingEngine",
    "Order",
    "OrderBook",
    "OrderType",
    "Side",
    "TimeInForce",
    "Trade",
]
