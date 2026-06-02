#include "matching_engine.h"
#include <iomanip>
#include <iostream>

void print_trades(const std::vector<Trade>& trades) {
    for (const auto& t : trades) {
        std::cout << "  TRADE: " << t.symbol << " " << t.quantity << " @ $"
                  << std::fixed << std::setprecision(2) << t.price
                  << " (buyer=" << t.buyer_order_id
                  << ", seller=" << t.seller_order_id << ")\n";
    }
}

void print_book(const OrderBook& book) {
    std::cout << "\n--- " << book.symbol() << " Order Book ---\n";

    auto asks = book.get_ask_depth(5);
    // Print asks in reverse (highest first)
    for (auto it = asks.rbegin(); it != asks.rend(); ++it) {
        std::cout << "  ASK  $" << std::fixed << std::setprecision(2)
                  << it->price << "  qty=" << it->total_quantity
                  << "  (" << it->order_count << " orders)\n";
    }

    auto spread = book.get_spread();
    std::cout << "  ---- spread: $" << std::fixed << std::setprecision(2)
              << spread << " ----\n";

    auto bids = book.get_bid_depth(5);
    for (const auto& level : bids) {
        std::cout << "  BID  $" << std::fixed << std::setprecision(2)
                  << level.price << "  qty=" << level.total_quantity
                  << "  (" << level.order_count << " orders)\n";
    }

    std::cout << "  Bids: " << book.bid_count()
              << " orders, Asks: " << book.ask_count() << " orders\n";
}

int main() {
    std::cout << "=== Order Book Simulator & Matching Engine ===\n\n";

    MatchingEngine engine;
    uint64_t next_id = 1;

    // 1. Place limit buy orders
    std::cout << "[1] Placing limit BUY orders...\n";
    engine.submit_order({next_id++, "AAPL", Side::BUY, OrderType::LIMIT, 149.00, 100, 100});
    engine.submit_order({next_id++, "AAPL", Side::BUY, OrderType::LIMIT, 149.50, 200, 200});
    engine.submit_order({next_id++, "AAPL", Side::BUY, OrderType::LIMIT, 150.00, 150, 150});

    // 2. Place limit sell orders
    std::cout << "[2] Placing limit SELL orders...\n";
    engine.submit_order({next_id++, "AAPL", Side::SELL, OrderType::LIMIT, 150.50, 100, 100});
    engine.submit_order({next_id++, "AAPL", Side::SELL, OrderType::LIMIT, 151.00, 200, 200});
    engine.submit_order({next_id++, "AAPL", Side::SELL, OrderType::LIMIT, 151.50, 50, 50});

    print_book(engine.get_order_book("AAPL"));

    // 3. Market buy order — should match against best ask
    std::cout << "\n[3] Market BUY 80 shares...\n";
    auto trades = engine.submit_order(
        {next_id++, "AAPL", Side::BUY, OrderType::MARKET, 0, 80, 80});
    print_trades(trades);
    print_book(engine.get_order_book("AAPL"));

    // 4. Limit sell that crosses the spread — partial fill
    std::cout << "\n[4] Limit SELL 200 @ $149.50 (crosses spread)...\n";
    trades = engine.submit_order(
        {next_id++, "AAPL", Side::SELL, OrderType::LIMIT, 149.50, 200, 200});
    print_trades(trades);
    print_book(engine.get_order_book("AAPL"));

    // 5. Cancel an order
    std::cout << "\n[5] Cancelling order #5 (SELL 200 @ $151.00)...\n";
    bool cancelled = engine.cancel_order("AAPL", 5);
    std::cout << "  Cancelled: " << (cancelled ? "yes" : "no") << "\n";
    print_book(engine.get_order_book("AAPL"));

    // 6. Large market sell — sweeps multiple bid levels
    std::cout << "\n[6] Market SELL 300 shares (sweeps bids)...\n";
    trades = engine.submit_order(
        {next_id++, "AAPL", Side::SELL, OrderType::MARKET, 0, 300, 300});
    print_trades(trades);
    print_book(engine.get_order_book("AAPL"));

    std::cout << "\n=== Done ===\n";
    return 0;
}
