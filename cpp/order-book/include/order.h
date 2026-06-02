#pragma once

#include <chrono>
#include <cstdint>
#include <string>

enum class Side { BUY, SELL };
enum class OrderType { MARKET, LIMIT };

struct Order {
    uint64_t id;
    std::string symbol;
    Side side;
    OrderType type;
    double price;
    int quantity;
    int remaining_quantity;
    std::chrono::steady_clock::time_point timestamp =
        std::chrono::steady_clock::now();

    bool is_filled() const { return remaining_quantity <= 0; }
};
