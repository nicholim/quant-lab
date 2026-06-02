#pragma once

#include "order_book.h"
#include "trade.h"

#include <string>
#include <unordered_map>
#include <vector>

class MatchingEngine {
public:
    std::vector<Trade> submit_order(Order order);
    bool cancel_order(const std::string& symbol, uint64_t order_id);
    const OrderBook& get_order_book(const std::string& symbol);

    std::vector<std::string> get_symbols() const;

private:
    std::unordered_map<std::string, OrderBook> books_;

    OrderBook& get_or_create_book(const std::string& symbol);
};
