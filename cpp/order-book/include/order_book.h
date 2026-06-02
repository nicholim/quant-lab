#pragma once

#include "order.h"
#include "trade.h"

#include <cstdint>
#include <list>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

struct DepthLevel {
    double price;
    int total_quantity;
    int order_count;
};

class OrderBook {
public:
    explicit OrderBook(const std::string& symbol);

    std::vector<Trade> add_order(Order order);
    bool cancel_order(uint64_t order_id);
    bool modify_order(uint64_t order_id, int new_quantity, double new_price);

    std::optional<double> get_best_bid() const;
    std::optional<double> get_best_ask() const;
    double get_spread() const;

    std::vector<DepthLevel> get_bid_depth(int levels = 10) const;
    std::vector<DepthLevel> get_ask_depth(int levels = 10) const;
    int get_volume_at_price(double price) const;

    const std::string& symbol() const { return symbol_; }
    int bid_count() const;
    int ask_count() const;

private:
    std::vector<Trade> match_order(Order& order);
    void insert_order(const Order& order);
    void remove_order_from_book(uint64_t order_id);

    std::string symbol_;

    // Bids: descending price (highest first) -> FIFO queue
    std::map<double, std::list<Order>, std::greater<double>> bids_;
    // Asks: ascending price (lowest first) -> FIFO queue
    std::map<double, std::list<Order>> asks_;

    // Quick lookup: order_id -> (side, price)
    std::unordered_map<uint64_t, std::pair<Side, double>> order_index_;
};
