"""Drive the REAL C++ matching engine from Python via the ``orderbook`` binding.

This module generates a synthetic order flow and feeds it through the compiled
C++ price-time-priority engine (the importable ``orderbook`` pybind11 module),
collecting the engine's *actual* output — executed trades, the evolving spread,
and the live book depth (best bid/ask + depth ladders read straight off the
engine). The result is genuine matching-engine state, NOT a re-implemented
Python toy book.

There are two pieces:

* ``MarketSimulator`` — a deterministic order-flow *generator*. It only produces
  order descriptors (a mix of LIMIT/MARKET, both sides, prices around a drifting
  mid, optionally with IOC/FOK/POST_ONLY time-in-force). It does NO matching.
* ``EngineSimulator`` — submits that flow through a real ``orderbook.OrderBook``
  and returns a :class:`SimulationResult` carrying the real trades, the spread
  series, and a depth snapshot. This is the single source of truth for matching.

Build the extension first so ``orderbook`` exists::

    cmake -S . -B build && cmake --build build   # from cpp/order-book/

Then::

    python python/simulator.py                   # run + summary
    python python/simulator.py --plot out/        # also render charts

The old pure-Python ``save_orders`` JSON dump remains for convenience, but the
matching itself is now done entirely by the C++ engine through the binding.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field

import orderbook as ob


@dataclass
class SimulationResult:
    """Real engine output collected while replaying a flow through the C++ book.

    Every field is read directly off the live ``OrderBook`` — no Python-side
    matching. ``trades``/``bids``/``asks`` are plain dicts so the visualizer (and
    JSON export) need no knowledge of the binding types.
    """

    symbol: str
    trades: list[dict] = field(default_factory=list)
    spreads: list[float] = field(default_factory=list)
    bids: list[dict] = field(default_factory=list)
    asks: list[dict] = field(default_factory=list)
    best_bid: float | None = None
    best_ask: float | None = None
    n_orders: int = 0

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def total_volume(self) -> int:
        return sum(t["quantity"] for t in self.trades)


_SIDE = {"BUY": ob.Side.BUY, "SELL": ob.Side.SELL}
_TYPE = {"LIMIT": ob.OrderType.LIMIT, "MARKET": ob.OrderType.MARKET}
_TIF = {
    "GTC": ob.TimeInForce.GTC,
    "IOC": ob.TimeInForce.IOC,
    "FOK": ob.TimeInForce.FOK,
    "POST_ONLY": ob.TimeInForce.POST_ONLY,
}


class MarketSimulator:
    """Generate a deterministic synthetic order flow (descriptors only).

    Produces a list of order dicts; it performs NO matching. Feed the result
    into :class:`EngineSimulator` to run it through the real C++ engine.
    """

    def __init__(self, symbol: str = "AAPL", price_center: float = 150.0, spread: float = 0.50):
        self.symbol = symbol
        self.price_center = price_center
        self.spread = spread

    def generate_random_orders(self, n: int, *, use_tif: bool = False) -> list[dict]:
        """Generate ``n`` random orders with a realistic price distribution.

        ~80% LIMIT / 20% MARKET, both sides, limit prices placed around a slowly
        drifting mid so some orders cross (exercise matching) and some rest
        (build depth). When ``use_tif`` is set, a minority of LIMIT orders carry
        an IOC/FOK/POST_ONLY time-in-force so those engine paths are exercised.
        """
        orders: list[dict] = []
        price = self.price_center

        for i in range(n):
            # Random walk for the mid so the book keeps moving.
            price += random.gauss(0.0, 0.10)

            side = random.choice(["BUY", "SELL"])
            order_type = random.choices(["LIMIT", "MARKET"], weights=[0.8, 0.2])[0]

            if order_type == "LIMIT":
                if side == "BUY":
                    order_price = round(price - abs(random.gauss(0.0, self.spread)), 2)
                else:
                    order_price = round(price + abs(random.gauss(0.0, self.spread)), 2)
            else:
                order_price = 0.0

            quantity = random.choice([10, 25, 50, 100, 200, 500])

            tif = "GTC"
            if use_tif and order_type == "LIMIT" and random.random() < 0.15:
                tif = random.choice(["IOC", "FOK", "POST_ONLY"])

            orders.append(
                {
                    "id": i + 1,
                    "symbol": self.symbol,
                    "side": side,
                    "type": order_type,
                    "price": order_price,
                    "quantity": quantity,
                    "tif": tif,
                }
            )

        return orders

    def save_orders(self, orders: list[dict], filepath: str = "orders.json") -> None:
        """Save a generated flow to JSON (the flow descriptors, not engine state)."""
        with open(filepath, "w") as f:
            json.dump(orders, f, indent=2)
        print(f"Saved {len(orders)} orders to {filepath}")


class EngineSimulator:
    """Replay an order flow through the REAL C++ ``OrderBook`` and collect state.

    Submits each order via the pybind11 binding (``OrderBook.add_order``),
    accumulating every fill the engine returns and sampling the live spread after
    each submission. The final depth ladders + best bid/ask are read straight off
    the engine, so the resulting :class:`SimulationResult` is the engine's actual
    state — never a Python-side approximation.
    """

    def __init__(self, symbol: str = "AAPL", depth_levels: int = 10):
        self.symbol = symbol
        self.depth_levels = depth_levels

    def _to_order(self, o: dict) -> ob.Order:
        return ob.Order(
            int(o["id"]),
            o.get("symbol", self.symbol),
            _SIDE[o["side"]],
            _TYPE[o["type"]],
            float(o["price"]),
            int(o["quantity"]),
            _TIF.get(o.get("tif", "GTC"), ob.TimeInForce.GTC),
        )

    def run(self, orders: list[dict]) -> SimulationResult:
        """Feed ``orders`` through a fresh C++ book; return the real engine output."""
        book = ob.OrderBook(self.symbol)
        result = SimulationResult(symbol=self.symbol, n_orders=len(orders))
        add_order = book.add_order

        for o in orders:
            trades = add_order(self._to_order(o))
            for t in trades:
                result.trades.append(
                    {
                        "price": t.price,
                        "quantity": t.quantity,
                        "buyer_order_id": t.buyer_order_id,
                        "seller_order_id": t.seller_order_id,
                        "symbol": t.symbol,
                    }
                )
            # Sample the live spread off the engine when both sides are present.
            bid = book.get_best_bid()
            ask = book.get_best_ask()
            if bid is not None and ask is not None:
                result.spreads.append(ask - bid)

        # Final book state, read directly from the engine.
        result.bids = [
            {"price": d.price, "quantity": d.total_quantity, "order_count": d.order_count}
            for d in book.get_bid_depth(self.depth_levels)
        ]
        result.asks = [
            {"price": d.price, "quantity": d.total_quantity, "order_count": d.order_count}
            for d in book.get_ask_depth(self.depth_levels)
        ]
        result.best_bid = book.get_best_bid()
        result.best_ask = book.get_best_ask()
        return result


def simulate(
    n: int = 500,
    *,
    symbol: str = "AAPL",
    price_center: float = 150.0,
    spread: float = 0.50,
    seed: int | None = None,
    use_tif: bool = False,
    depth_levels: int = 10,
) -> SimulationResult:
    """Generate a flow and drive it through the real C++ engine in one call."""
    if seed is not None:
        random.seed(seed)
    flow = MarketSimulator(symbol, price_center, spread).generate_random_orders(n, use_tif=use_tif)
    return EngineSimulator(symbol, depth_levels).run(flow)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orders", type=int, default=500, help="number of orders to simulate")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed for reproducibility")
    parser.add_argument("--tif", action="store_true", help="sprinkle IOC/FOK/POST_ONLY orders")
    parser.add_argument(
        "--plot",
        metavar="DIR",
        default=None,
        help="render depth/trade-tape/spread charts (headless) into DIR",
    )
    args = parser.parse_args()

    result = simulate(args.orders, symbol=args.symbol, seed=args.seed, use_tif=args.tif)

    print(f"Simulated {result.n_orders} orders through the C++ engine ({result.symbol}):")
    print(f"  trades executed: {result.n_trades}  (volume {result.total_volume})")
    print(f"  best bid: {result.best_bid}   best ask: {result.best_ask}")
    if result.best_bid is not None and result.best_ask is not None:
        print(f"  final spread: {result.best_ask - result.best_bid:.2f}")
    print(f"  resting bid levels: {len(result.bids)}   ask levels: {len(result.asks)}")

    if args.plot:
        import os

        from visualizer import OrderBookVisualizer

        os.makedirs(args.plot, exist_ok=True)
        OrderBookVisualizer.plot_simulation(result, out_dir=args.plot)
        print(f"  charts written to {args.plot}/")


if __name__ == "__main__":
    main()
