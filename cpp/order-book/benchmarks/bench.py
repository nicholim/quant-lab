"""Throughput + latency benchmark for the C++ matching engine (via the binding).

Drives the importable ``orderbook`` pybind11 module (the compiled C++
price-time-priority engine) with a large synthetic order flow and measures:

1. **Throughput** -- orders processed per second. A deterministic mix of
   LIMIT/MARKET orders on both sides, placed around a slowly drifting mid, is
   pre-generated, then fed through ``OrderBook.add_order`` in a tight timed loop.
   Reported as orders/sec.
2. **Latency** -- per-order processing latency. Each ``add_order`` call is timed
   individually with ``time.perf_counter_ns`` and the distribution is reported as
   p50 / p90 / p99 / max in microseconds.

The order flow deliberately crosses the book often (so the matching path, not
just resting inserts, is exercised) while still leaving resting liquidity, and
the mid drifts so the book does not collapse to a single price level.

Run:
    python benchmarks/bench.py
    python benchmarks/bench.py --orders 2000000 --repeat 5 --seed 7

Requires the compiled extension: build it first with
``cmake -S . -B build && cmake --build build`` from ``cpp/order-book/`` so the
``orderbook`` module exists, then run this script. The heavy workload only runs
under ``__main__`` so importing this file (e.g. pytest collection) is cheap.
"""

from __future__ import annotations

import argparse
import platform
import sys
import time
from pathlib import Path

# Allow `python benchmarks/bench.py` from cpp/order-book/: put python/ on the path.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python"))

import orderbook as ob  # noqa: E402


def generate_order_flow(n: int, seed: int) -> list[ob.Order]:
    """Pre-build a deterministic synthetic order flow (so timing excludes gen cost).

    ~80% LIMIT / 20% MARKET, both sides, prices placed around a slowly drifting
    mid. LIMIT orders are sprinkled across a few ticks each side of the mid so
    some cross immediately (exercise matching) and some rest (build depth).
    """
    import random as _random

    rng = _random.Random(seed)
    orders: list[ob.Order] = []
    mid = 150.0
    tick = 0.01
    for i in range(n):
        # Slow random-walk drift of the mid so the book keeps moving.
        mid += rng.gauss(0.0, 0.02)
        side = ob.Side.BUY if rng.random() < 0.5 else ob.Side.SELL
        quantity = rng.choice((10, 25, 50, 100, 200))
        if rng.random() < 0.20:
            order_type = ob.OrderType.MARKET
            price = 0.0
        else:
            order_type = ob.OrderType.LIMIT
            # Offset by -2..+2 ticks from mid; buys above / sells below mid cross.
            offset = rng.randint(-2, 2) * tick
            price = round(mid + offset, 2)
        orders.append(ob.Order(i + 1, "AAPL", side, order_type, price, quantity))
    return orders


def run_throughput(orders: list[ob.Order]) -> tuple[float, int, int]:
    """Feed the pre-built flow through a fresh book; return (elapsed_s, n, trades)."""
    book = ob.OrderBook("AAPL")
    add_order = book.add_order  # bind once, out of the hot loop
    trade_count = 0
    t0 = time.perf_counter()
    for order in orders:
        trades = add_order(order)
        trade_count += len(trades)
    elapsed = time.perf_counter() - t0
    return elapsed, len(orders), trade_count


def run_latency(orders: list[ob.Order]) -> list[int]:
    """Time each add_order individually; return per-order latencies in nanoseconds."""
    book = ob.OrderBook("AAPL")
    add_order = book.add_order
    perf_ns = time.perf_counter_ns
    latencies = [0] * len(orders)
    for i, order in enumerate(orders):
        t0 = perf_ns()
        add_order(order)
        latencies[i] = perf_ns() - t0
    return latencies


def percentile(sorted_vals: list[int], pct: float) -> float:
    """Nearest-rank percentile on an already-sorted list."""
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, int(round(pct / 100.0 * (len(sorted_vals) - 1)))))
    return float(sorted_vals[k])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--orders", type=int, default=500_000, help="orders per run (default 500k, ~few sec)"
    )
    parser.add_argument("--repeat", type=int, default=3, help="timed throughput repetitions")
    parser.add_argument("--seed", type=int, default=7, help="RNG seed for reproducibility")
    args = parser.parse_args()

    machine = f"{platform.machine()} / {platform.system()} {platform.release()}"
    print("Order-book matching-engine benchmark (pybind11 binding -> C++ engine)")
    print(f"Machine: {machine}, Python {platform.python_version()}")
    print(
        f"Workload: {args.orders:,} orders/run (~80% LIMIT / 20% MARKET, both sides), "
        f"seed {args.seed}\n"
    )

    orders = generate_order_flow(args.orders, args.seed)

    # --- Throughput ---
    print(f"Throughput ({args.repeat} runs):")
    best_ops = 0.0
    total_trades = 0
    for r in range(args.repeat):
        elapsed, n, trades = run_throughput(orders)
        ops = n / elapsed
        best_ops = max(best_ops, ops)
        total_trades = trades
        print(f"  run {r + 1}: {elapsed:.3f}s  ->  {ops:,.0f} orders/sec  ({trades:,} trades)")
    print(f"  best: {best_ops:,.0f} orders/sec\n")

    # --- Latency ---
    latencies = run_latency(orders)
    latencies.sort()
    p50 = percentile(latencies, 50) / 1000.0
    p90 = percentile(latencies, 90) / 1000.0
    p99 = percentile(latencies, 99) / 1000.0
    pmax = latencies[-1] / 1000.0
    mean = sum(latencies) / len(latencies) / 1000.0

    print("Per-order latency (microseconds):")
    print(f"  {'metric':<8}{'us':>10}")
    print(f"  {'-' * 18}")
    for label, val in (
        ("mean", mean),
        ("p50", p50),
        ("p90", p90),
        ("p99", p99),
        ("max", pmax),
    ):
        print(f"  {label:<8}{val:>10.3f}")

    print(
        f"\nSummary: {best_ops:,.0f} orders/sec | "
        f"p50 {p50:.3f}us / p90 {p90:.3f}us / p99 {p99:.3f}us | "
        f"{total_trades:,} trades on {args.orders:,} orders"
    )


if __name__ == "__main__":
    main()
