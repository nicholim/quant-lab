#include "order_book.h"
#include <algorithm>
#include <iostream>

OrderBook::OrderBook(const std::string& symbol) : symbol_(symbol) {}

std::vector<Trade> OrderBook::add_order(Order order) {
    order.timestamp = std::chrono::steady_clock::now();

    // --- Time-in-force pre-checks (no book mutation yet) ---

    // POST_ONLY: maker-only. If it would take liquidity, reject it outright
    // (no trades, nothing rests). Otherwise fall through and let it rest.
    if (order.tif == TimeInForce::POST_ONLY && would_cross(order)) {
        return {};
    }

    // FOK: all-or-nothing. Only proceed if the ENTIRE quantity can be filled
    // immediately; otherwise kill it (no trades, book untouched).
    if (order.tif == TimeInForce::FOK &&
        available_fill_quantity(order) < order.remaining_quantity) {
        return {};
    }

    auto trades = match_order(order);

    // Rest any remainder only for GTC limit orders. MARKET never rests; IOC and
    // FOK never rest (IOC cancels its remainder; FOK either filled fully above
    // or never matched). POST_ONLY that reaches here did not cross, so it rests.
    const bool rests =
        order.type == OrderType::LIMIT &&
        (order.tif == TimeInForce::GTC || order.tif == TimeInForce::POST_ONLY);
    if (!order.is_filled() && rests) {
        insert_order(order);
    }

    return trades;
}

bool OrderBook::cancel_order(uint64_t order_id) {
    auto it = order_index_.find(order_id);
    if (it == order_index_.end()) return false;

    remove_order_from_book(order_id);
    return true;
}

bool OrderBook::modify_order(uint64_t order_id, int new_quantity, double new_price) {
    auto it = order_index_.find(order_id);
    if (it == order_index_.end()) return false;

    auto [side, old_price] = it->second;

    // Remove old order
    remove_order_from_book(order_id);

    // Re-insert with new params (loses time priority)
    Order modified;
    modified.id = order_id;
    modified.symbol = symbol_;
    modified.side = side;
    modified.type = OrderType::LIMIT;
    modified.price = new_price;
    modified.quantity = new_quantity;
    modified.remaining_quantity = new_quantity;

    insert_order(modified);
    return true;
}

std::optional<double> OrderBook::get_best_bid() const {
    if (bids_.empty()) return std::nullopt;
    return bids_.begin()->first;
}

std::optional<double> OrderBook::get_best_ask() const {
    if (asks_.empty()) return std::nullopt;
    return asks_.begin()->first;
}

double OrderBook::get_spread() const {
    auto bid = get_best_bid();
    auto ask = get_best_ask();
    if (bid && ask) return *ask - *bid;
    return 0.0;
}

std::vector<DepthLevel> OrderBook::get_bid_depth(int levels) const {
    std::vector<DepthLevel> depth;
    int count = 0;
    for (const auto& [price, orders] : bids_) {
        if (count++ >= levels) break;
        int total_qty = 0;
        for (const auto& o : orders) total_qty += o.remaining_quantity;
        depth.push_back({price, total_qty, static_cast<int>(orders.size())});
    }
    return depth;
}

std::vector<DepthLevel> OrderBook::get_ask_depth(int levels) const {
    std::vector<DepthLevel> depth;
    int count = 0;
    for (const auto& [price, orders] : asks_) {
        if (count++ >= levels) break;
        int total_qty = 0;
        for (const auto& o : orders) total_qty += o.remaining_quantity;
        depth.push_back({price, total_qty, static_cast<int>(orders.size())});
    }
    return depth;
}

int OrderBook::get_volume_at_price(double price) const {
    int vol = 0;
    auto bit = bids_.find(price);
    if (bit != bids_.end()) {
        for (const auto& o : bit->second) vol += o.remaining_quantity;
    }
    auto ait = asks_.find(price);
    if (ait != asks_.end()) {
        for (const auto& o : ait->second) vol += o.remaining_quantity;
    }
    return vol;
}

int OrderBook::bid_count() const {
    int count = 0;
    for (const auto& [_, orders] : bids_) count += orders.size();
    return count;
}

int OrderBook::ask_count() const {
    int count = 0;
    for (const auto& [_, orders] : asks_) count += orders.size();
    return count;
}

// --- Private methods ---

std::vector<Trade> OrderBook::match_order(Order& order) {
    std::vector<Trade> trades;

    if (order.side == Side::BUY) {
        // Match against asks (lowest first)
        while (!order.is_filled() && !asks_.empty()) {
            auto best_ask_it = asks_.begin();
            double ask_price = best_ask_it->first;

            // For limit orders, stop if ask price > order price
            if (order.type == OrderType::LIMIT && ask_price > order.price) break;

            auto& queue = best_ask_it->second;
            while (!order.is_filled() && !queue.empty()) {
                auto& resting = queue.front();
                int fill_qty = std::min(order.remaining_quantity, resting.remaining_quantity);

                trades.push_back({
                    order.id,
                    resting.id,
                    symbol_,
                    ask_price,
                    fill_qty,
                    std::chrono::steady_clock::now(),
                });

                order.remaining_quantity -= fill_qty;
                resting.remaining_quantity -= fill_qty;

                if (resting.is_filled()) {
                    order_index_.erase(resting.id);
                    queue.pop_front();
                }
            }

            if (queue.empty()) {
                asks_.erase(best_ask_it);
            }
        }
    } else {
        // Match against bids (highest first)
        while (!order.is_filled() && !bids_.empty()) {
            auto best_bid_it = bids_.begin();
            double bid_price = best_bid_it->first;

            if (order.type == OrderType::LIMIT && bid_price < order.price) break;

            auto& queue = best_bid_it->second;
            while (!order.is_filled() && !queue.empty()) {
                auto& resting = queue.front();
                int fill_qty = std::min(order.remaining_quantity, resting.remaining_quantity);

                trades.push_back({
                    resting.id,
                    order.id,
                    symbol_,
                    bid_price,
                    fill_qty,
                    std::chrono::steady_clock::now(),
                });

                order.remaining_quantity -= fill_qty;
                resting.remaining_quantity -= fill_qty;

                if (resting.is_filled()) {
                    order_index_.erase(resting.id);
                    queue.pop_front();
                }
            }

            if (queue.empty()) {
                bids_.erase(best_bid_it);
            }
        }
    }

    return trades;
}

bool OrderBook::would_cross(const Order& order) const {
    if (order.side == Side::BUY) {
        if (asks_.empty()) return false;
        double best_ask = asks_.begin()->first;
        // A market buy always crosses (if there's anything to take); a limit
        // buy crosses only when it is willing to pay at least the best ask.
        if (order.type == OrderType::MARKET) return true;
        return order.price >= best_ask;
    } else {
        if (bids_.empty()) return false;
        double best_bid = bids_.begin()->first;
        if (order.type == OrderType::MARKET) return true;
        return order.price <= best_bid;
    }
}

int OrderBook::available_fill_quantity(const Order& order) const {
    int need = order.remaining_quantity;
    int available = 0;

    if (order.side == Side::BUY) {
        for (const auto& [ask_price, queue] : asks_) {
            if (order.type == OrderType::LIMIT && ask_price > order.price) break;
            for (const auto& resting : queue) {
                available += resting.remaining_quantity;
                if (available >= need) return need;
            }
        }
    } else {
        for (const auto& [bid_price, queue] : bids_) {
            if (order.type == OrderType::LIMIT && bid_price < order.price) break;
            for (const auto& resting : queue) {
                available += resting.remaining_quantity;
                if (available >= need) return need;
            }
        }
    }

    return available;
}

void OrderBook::insert_order(const Order& order) {
    if (order.side == Side::BUY) {
        bids_[order.price].push_back(order);
    } else {
        asks_[order.price].push_back(order);
    }
    order_index_[order.id] = {order.side, order.price};
}

void OrderBook::remove_order_from_book(uint64_t order_id) {
    auto it = order_index_.find(order_id);
    if (it == order_index_.end()) return;

    auto [side, price] = it->second;

    if (side == Side::BUY) {
        auto pit = bids_.find(price);
        if (pit != bids_.end()) {
            pit->second.remove_if([order_id](const Order& o) { return o.id == order_id; });
            if (pit->second.empty()) bids_.erase(pit);
        }
    } else {
        auto pit = asks_.find(price);
        if (pit != asks_.end()) {
            pit->second.remove_if([order_id](const Order& o) { return o.id == order_id; });
            if (pit->second.empty()) asks_.erase(pit);
        }
    }

    order_index_.erase(it);
}
