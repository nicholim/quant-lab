#pragma once

#include <chrono>
#include <cstdint>
#include <string>

enum class Side { BUY, SELL };
enum class OrderType { MARKET, LIMIT };

// Time-in-force / execution semantics, orthogonal to OrderType (price type).
//
// IOC/FOK/POST_ONLY are pure match-loop variants — they reuse the existing
// MARKET/LIMIT price logic and add no new book data structures. Modeling them
// here (rather than as extra OrderType enum values) mirrors how real exchanges
// and FIX work: a TimeInForce/ExecInst flag layered on top of the price type.
//
// - GTC       (default): existing behavior — match, then rest any remainder
//                         (LIMIT only; MARKET never rests).
// - IOC       Immediate-Or-Cancel: match what it can now, cancel the rest
//                         (never rests).
// - FOK       Fill-Or-Kill: fill the ENTIRE quantity immediately or do nothing
//                         (no partial fills, book left untouched on kill).
// - POST_ONLY Maker-only: reject if it would cross/take liquidity; otherwise
//                         rest as a maker (LIMIT only).
enum class TimeInForce { GTC, IOC, FOK, POST_ONLY };

struct Order {
    uint64_t id;
    std::string symbol;
    Side side;
    OrderType type;
    double price;
    int quantity;
    int remaining_quantity;
    TimeInForce tif = TimeInForce::GTC;
    std::chrono::steady_clock::time_point timestamp =
        std::chrono::steady_clock::now();

    bool is_filled() const { return remaining_quantity <= 0; }
};
