#pragma once

#include <chrono>
#include <cstdint>
#include <string>

struct Trade {
    uint64_t buyer_order_id;
    uint64_t seller_order_id;
    std::string symbol;
    double price;
    int quantity;
    std::chrono::steady_clock::time_point timestamp;
};
