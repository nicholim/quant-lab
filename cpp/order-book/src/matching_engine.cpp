#include "matching_engine.h"

std::vector<Trade> MatchingEngine::submit_order(Order order) {
    auto& book = get_or_create_book(order.symbol);
    return book.add_order(std::move(order));
}

bool MatchingEngine::cancel_order(const std::string& symbol, uint64_t order_id) {
    auto it = books_.find(symbol);
    if (it == books_.end()) return false;
    return it->second.cancel_order(order_id);
}

const OrderBook& MatchingEngine::get_order_book(const std::string& symbol) {
    return get_or_create_book(symbol);
}

std::vector<std::string> MatchingEngine::get_symbols() const {
    std::vector<std::string> symbols;
    symbols.reserve(books_.size());
    for (const auto& [sym, _] : books_) {
        symbols.push_back(sym);
    }
    return symbols;
}

OrderBook& MatchingEngine::get_or_create_book(const std::string& symbol) {
    auto it = books_.find(symbol);
    if (it == books_.end()) {
        auto [new_it, _] = books_.emplace(symbol, OrderBook(symbol));
        return new_it->second;
    }
    return it->second;
}
