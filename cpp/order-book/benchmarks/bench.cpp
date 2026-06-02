// Native C++ micro-benchmark for the matching engine.
//
// Isolates the PURE matching path: it pre-generates a synthetic order flow in
// C++ (no Python, no pybind11 binding overhead), then times OrderBook::add_order
// in a tight loop with std::chrono::steady_clock. The flow mirrors the Python
// harness in benchmarks/bench.py (~80% LIMIT / 20% MARKET, both sides, prices
// around a slowly drifting mid, the same tick/quantity ladder) so the native
// numbers are directly comparable to the binding numbers it reports.
//
// Reports throughput (orders/sec) and per-order latency percentiles
// (p50/p90/p99/max) in microseconds and nanoseconds. Flow generation is
// EXCLUDED from the timed region.
//
// Build (Release/-O2) + run:
//   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build
//   ./build/order_book_bench                 # 500k orders/run, ~few sec
//   ./build/order_book_bench 2000000 5 7      # <orders> <repeat> <seed>
//
// This is a benchmark, not a test: it is NOT registered with ctest.

#include "order_book.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <random>
#include <string>
#include <vector>

namespace {

// Pre-build a deterministic synthetic order flow so timing excludes gen cost.
// Mirrors benchmarks/bench.py::generate_order_flow: mid starts at 150.0, drifts
// by gauss(0, 0.02) per order; ~80% LIMIT / 20% MARKET; both sides; quantity in
// {10,25,50,100,200}; LIMIT price offset -2..+2 ticks (0.01) from the mid so
// some orders cross immediately and some rest.
std::vector<Order> generate_order_flow(std::size_t n, unsigned int seed) {
    std::mt19937 rng(seed);
    std::normal_distribution<double> drift(0.0, 0.02);
    std::uniform_real_distribution<double> unit(0.0, 1.0);
    std::uniform_int_distribution<int> offset_ticks(-2, 2);
    const int qty_ladder[] = {10, 25, 50, 100, 200};
    std::uniform_int_distribution<int> qty_pick(0, 4);

    std::vector<Order> orders;
    orders.reserve(n);

    double mid = 150.0;
    const double tick = 0.01;
    for (std::size_t i = 0; i < n; ++i) {
        mid += drift(rng);
        Side side = unit(rng) < 0.5 ? Side::BUY : Side::SELL;
        int quantity = qty_ladder[qty_pick(rng)];

        OrderType type;
        double price;
        if (unit(rng) < 0.20) {
            type = OrderType::MARKET;
            price = 0.0;
        } else {
            type = OrderType::LIMIT;
            double raw = mid + offset_ticks(rng) * tick;
            // Round to the tick (2 dp) like the Python harness's round(_, 2).
            price = std::round(raw * 100.0) / 100.0;
        }

        Order order;
        order.id = static_cast<uint64_t>(i + 1);
        order.symbol = "AAPL";
        order.side = side;
        order.type = type;
        order.price = price;
        order.quantity = quantity;
        order.remaining_quantity = quantity;
        order.tif = TimeInForce::GTC;
        orders.push_back(std::move(order));
    }
    return orders;
}

// Nearest-rank percentile on an already-sorted vector (matches bench.py).
double percentile(const std::vector<long long>& sorted_vals, double pct) {
    if (sorted_vals.empty()) {
        return 0.0;
    }
    const auto n = static_cast<double>(sorted_vals.size());
    long long k = static_cast<long long>(std::llround(pct / 100.0 * (n - 1.0)));
    k = std::max<long long>(0, std::min<long long>(static_cast<long long>(sorted_vals.size()) - 1, k));
    return static_cast<double>(sorted_vals[static_cast<std::size_t>(k)]);
}

}  // namespace

int main(int argc, char** argv) {
    std::size_t n_orders = 500'000;  // default ~few seconds
    int repeat = 3;
    unsigned int seed = 7;

    if (argc > 1) {
        n_orders = static_cast<std::size_t>(std::strtoull(argv[1], nullptr, 10));
    }
    if (argc > 2) {
        repeat = std::atoi(argv[2]);
    }
    if (argc > 3) {
        seed = static_cast<unsigned int>(std::strtoul(argv[3], nullptr, 10));
    }
    if (n_orders == 0 || repeat < 1) {
        std::cerr << "usage: order_book_bench [orders] [repeat] [seed]\n";
        return 1;
    }

    std::cout << "Order-book matching-engine benchmark (NATIVE C++, no binding)\n";
    std::cout << "Workload: " << n_orders
              << " orders/run (~80% LIMIT / 20% MARKET, both sides), seed " << seed
              << "\n\n";

    // --- Generate flow ONCE, outside any timed region. ---
    std::vector<Order> orders = generate_order_flow(n_orders, seed);

    // --- Throughput: feed the pre-built flow through a fresh book per run. ---
    std::cout << "Throughput (" << repeat << " runs):\n";
    double best_ops = 0.0;
    std::size_t last_trades = 0;
    for (int r = 0; r < repeat; ++r) {
        OrderBook book("AAPL");
        std::size_t trade_count = 0;
        auto t0 = std::chrono::steady_clock::now();
        for (const Order& order : orders) {
            // Copy: add_order takes Order by value (it mutates remaining_quantity).
            auto trades = book.add_order(order);
            trade_count += trades.size();
        }
        auto t1 = std::chrono::steady_clock::now();
        double elapsed =
            std::chrono::duration_cast<std::chrono::duration<double>>(t1 - t0).count();
        double ops = static_cast<double>(n_orders) / elapsed;
        best_ops = std::max(best_ops, ops);
        last_trades = trade_count;
        std::cout << "  run " << (r + 1) << ": " << std::fixed << std::setprecision(3)
                  << elapsed << "s  ->  " << std::setprecision(0) << ops
                  << " orders/sec  (" << trade_count << " trades)\n";
    }
    std::cout << "  best: " << std::setprecision(0) << best_ops << " orders/sec\n\n";

    // --- Latency: time each add_order individually. ---
    OrderBook lat_book("AAPL");
    std::vector<long long> latencies_ns;
    latencies_ns.reserve(n_orders);
    for (const Order& order : orders) {
        auto t0 = std::chrono::steady_clock::now();
        auto trades = lat_book.add_order(order);
        auto t1 = std::chrono::steady_clock::now();
        // Touch the result so the call can't be optimized away.
        if (trades.size() == 0xFFFFFFFF) {
            std::cout << "";
        }
        latencies_ns.push_back(
            std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count());
    }
    std::sort(latencies_ns.begin(), latencies_ns.end());

    double p50 = percentile(latencies_ns, 50);
    double p90 = percentile(latencies_ns, 90);
    double p99 = percentile(latencies_ns, 99);
    double pmax = static_cast<double>(latencies_ns.back());
    double sum = 0.0;
    for (long long v : latencies_ns) {
        sum += static_cast<double>(v);
    }
    double mean = sum / static_cast<double>(latencies_ns.size());

    std::cout << "Per-order latency:\n";
    std::cout << "  " << std::left << std::setw(8) << "metric" << std::right
              << std::setw(12) << "ns" << std::setw(12) << "us" << "\n";
    std::cout << "  " << std::string(32, '-') << "\n";
    const std::pair<const char*, double> rows[] = {
        {"mean", mean}, {"p50", p50}, {"p90", p90}, {"p99", p99}, {"max", pmax}};
    for (const auto& [label, val] : rows) {
        std::cout << "  " << std::left << std::setw(8) << label << std::right
                  << std::fixed << std::setprecision(1) << std::setw(12) << val
                  << std::setprecision(3) << std::setw(12) << (val / 1000.0)
                  << "\n";
    }

    std::cout << "\nSummary: " << std::setprecision(0) << best_ops
              << " orders/sec | p50 " << std::setprecision(3) << (p50 / 1000.0)
              << "us / p90 " << (p90 / 1000.0) << "us / p99 " << (p99 / 1000.0)
              << "us | " << last_trades << " trades on " << n_orders << " orders\n";

    return 0;
}
