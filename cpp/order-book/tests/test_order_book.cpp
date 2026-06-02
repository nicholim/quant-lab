// GoogleTest unit tests for the C++ matching engine core.
//
// Covers: price-time priority, partial fills, limit vs market orders,
// cancellation, modify, crossing the spread, multi-symbol routing, depth
// queries, and book invariants on the empty book.

#include "matching_engine.h"
#include "order_book.h"

#include <gtest/gtest.h>

#include <algorithm>

namespace {

// Helper to build an order with sane defaults so each test only sets the
// fields it cares about. remaining_quantity tracks quantity at submit time.
Order make_order(uint64_t id, const std::string& symbol, Side side, OrderType type,
                 double price, int quantity) {
    Order o;
    o.id = id;
    o.symbol = symbol;
    o.side = side;
    o.type = type;
    o.price = price;
    o.quantity = quantity;
    o.remaining_quantity = quantity;
    return o;
}

Order limit(uint64_t id, Side side, double price, int qty, const std::string& sym = "TEST") {
    return make_order(id, sym, side, OrderType::LIMIT, price, qty);
}

Order market(uint64_t id, Side side, int qty, const std::string& sym = "TEST") {
    return make_order(id, sym, side, OrderType::MARKET, 0.0, qty);
}

// ---------------------------------------------------------------------------
// Empty-book invariants
// ---------------------------------------------------------------------------

TEST(EmptyBook, NoBidNoAsk) {
    OrderBook book("TEST");
    EXPECT_FALSE(book.get_best_bid().has_value());
    EXPECT_FALSE(book.get_best_ask().has_value());
    EXPECT_EQ(book.bid_count(), 0);
    EXPECT_EQ(book.ask_count(), 0);
    EXPECT_TRUE(book.get_bid_depth().empty());
    EXPECT_TRUE(book.get_ask_depth().empty());
}

TEST(EmptyBook, SpreadIsZeroWhenIncomplete) {
    OrderBook book("TEST");
    // No bid, no ask.
    EXPECT_DOUBLE_EQ(book.get_spread(), 0.0);

    // Only a bid -> still 0 (no ask side to measure against).
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    EXPECT_DOUBLE_EQ(book.get_spread(), 0.0);
}

TEST(EmptyBook, CancelUnknownReturnsFalse) {
    OrderBook book("TEST");
    EXPECT_FALSE(book.cancel_order(999));
}

TEST(EmptyBook, ModifyUnknownReturnsFalse) {
    OrderBook book("TEST");
    EXPECT_FALSE(book.modify_order(999, 10, 100.0));
}

TEST(EmptyBook, MarketOrderOnEmptyBookProducesNoTrades) {
    OrderBook book("TEST");
    auto trades = book.add_order(market(1, Side::BUY, 100));
    EXPECT_TRUE(trades.empty());
    // Market orders never rest -> book stays empty.
    EXPECT_EQ(book.bid_count(), 0);
    EXPECT_EQ(book.ask_count(), 0);
}

// ---------------------------------------------------------------------------
// Resting limit orders & best-price / spread bookkeeping
// ---------------------------------------------------------------------------

TEST(RestingOrders, LimitOrdersRestAndSetBestPrices) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    book.add_order(limit(2, Side::SELL, 101.0, 10));

    ASSERT_TRUE(book.get_best_bid().has_value());
    ASSERT_TRUE(book.get_best_ask().has_value());
    EXPECT_DOUBLE_EQ(*book.get_best_bid(), 100.0);
    EXPECT_DOUBLE_EQ(*book.get_best_ask(), 101.0);
    EXPECT_DOUBLE_EQ(book.get_spread(), 1.0);
    EXPECT_EQ(book.bid_count(), 1);
    EXPECT_EQ(book.ask_count(), 1);
}

TEST(RestingOrders, BestBidIsHighestPrice) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 99.0, 10));
    book.add_order(limit(2, Side::BUY, 100.5, 10));
    book.add_order(limit(3, Side::BUY, 98.0, 10));
    EXPECT_DOUBLE_EQ(*book.get_best_bid(), 100.5);
}

TEST(RestingOrders, BestAskIsLowestPrice) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 101.0, 10));
    book.add_order(limit(2, Side::SELL, 100.5, 10));
    book.add_order(limit(3, Side::SELL, 102.0, 10));
    EXPECT_DOUBLE_EQ(*book.get_best_ask(), 100.5);
}

// ---------------------------------------------------------------------------
// Crossing the spread / basic matching
// ---------------------------------------------------------------------------

TEST(Matching, LimitBuyCrossesRestingAskFullFill) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 100.0, 10));   // resting ask
    auto trades = book.add_order(limit(2, Side::BUY, 100.0, 10));

    ASSERT_EQ(trades.size(), 1u);
    EXPECT_EQ(trades[0].buyer_order_id, 2u);
    EXPECT_EQ(trades[0].seller_order_id, 1u);
    EXPECT_DOUBLE_EQ(trades[0].price, 100.0);   // trades at resting price
    EXPECT_EQ(trades[0].quantity, 10);
    // Both fully filled -> empty book.
    EXPECT_EQ(book.bid_count(), 0);
    EXPECT_EQ(book.ask_count(), 0);
}

TEST(Matching, TradeExecutesAtRestingPriceNotAggressorPrice) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 100.0, 10));     // resting ask @100
    // Aggressive buy willing to pay 105 -> should still fill at 100.
    auto trades = book.add_order(limit(2, Side::BUY, 105.0, 10));
    ASSERT_EQ(trades.size(), 1u);
    EXPECT_DOUBLE_EQ(trades[0].price, 100.0);
}

TEST(Matching, NonCrossingLimitDoesNotMatch) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 101.0, 10));
    auto trades = book.add_order(limit(2, Side::BUY, 100.0, 10));   // below ask
    EXPECT_TRUE(trades.empty());
    EXPECT_EQ(book.bid_count(), 1);
    EXPECT_EQ(book.ask_count(), 1);
}

TEST(Matching, SellCrossesRestingBid) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    auto trades = book.add_order(limit(2, Side::SELL, 99.0, 10));   // crosses
    ASSERT_EQ(trades.size(), 1u);
    EXPECT_EQ(trades[0].buyer_order_id, 1u);
    EXPECT_EQ(trades[0].seller_order_id, 2u);
    EXPECT_DOUBLE_EQ(trades[0].price, 100.0);   // resting bid price
}

// ---------------------------------------------------------------------------
// Partial fills
// ---------------------------------------------------------------------------

TEST(PartialFill, AggressorPartiallyFillsThenRests) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 100.0, 30));      // small resting ask
    auto trades = book.add_order(limit(2, Side::BUY, 100.0, 50));  // wants 50

    ASSERT_EQ(trades.size(), 1u);
    EXPECT_EQ(trades[0].quantity, 30);
    // 20 remaining of the buy should rest as a new bid.
    EXPECT_EQ(book.ask_count(), 0);
    EXPECT_EQ(book.bid_count(), 1);
    ASSERT_TRUE(book.get_best_bid().has_value());
    EXPECT_DOUBLE_EQ(*book.get_best_bid(), 100.0);
    EXPECT_EQ(book.get_volume_at_price(100.0), 20);
}

TEST(PartialFill, RestingOrderPartiallyConsumed) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 100.0, 50));      // big resting ask
    auto trades = book.add_order(limit(2, Side::BUY, 100.0, 20));  // small buy

    ASSERT_EQ(trades.size(), 1u);
    EXPECT_EQ(trades[0].quantity, 20);
    // Resting ask should keep 30 remaining.
    EXPECT_EQ(book.bid_count(), 0);
    EXPECT_EQ(book.ask_count(), 1);
    EXPECT_EQ(book.get_volume_at_price(100.0), 30);
}

// ---------------------------------------------------------------------------
// Price-time priority
// ---------------------------------------------------------------------------

TEST(Priority, PriceTakesPriorityOverTime) {
    OrderBook book("TEST");
    // Worse-priced ask submitted first, better-priced ask second.
    book.add_order(limit(1, Side::SELL, 101.0, 10));
    book.add_order(limit(2, Side::SELL, 100.0, 10));
    // Buyer should hit the cheapest (id 2) first.
    auto trades = book.add_order(limit(3, Side::BUY, 101.0, 10));
    ASSERT_EQ(trades.size(), 1u);
    EXPECT_EQ(trades[0].seller_order_id, 2u);
    EXPECT_DOUBLE_EQ(trades[0].price, 100.0);
}

TEST(Priority, FIFOAtSamePriceLevel) {
    OrderBook book("TEST");
    // Two asks at the same price; order 1 arrives first.
    book.add_order(limit(1, Side::SELL, 100.0, 10));
    book.add_order(limit(2, Side::SELL, 100.0, 10));
    // A buy for 10 should match the earlier resting order (id 1) only.
    auto trades = book.add_order(limit(3, Side::BUY, 100.0, 10));
    ASSERT_EQ(trades.size(), 1u);
    EXPECT_EQ(trades[0].seller_order_id, 1u);
    // Order 2 still rests.
    EXPECT_EQ(book.ask_count(), 1);
    EXPECT_EQ(book.get_volume_at_price(100.0), 10);
}

TEST(Priority, SweepWalksLevelsInPriceOrder) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 100.0, 10));
    book.add_order(limit(2, Side::SELL, 101.0, 10));
    book.add_order(limit(3, Side::SELL, 102.0, 10));
    // Aggressive buy of 25 sweeps 100 (10) + 101 (10) + 102 (partial 5).
    auto trades = book.add_order(limit(4, Side::BUY, 105.0, 25));
    ASSERT_EQ(trades.size(), 3u);
    EXPECT_DOUBLE_EQ(trades[0].price, 100.0);
    EXPECT_DOUBLE_EQ(trades[1].price, 101.0);
    EXPECT_DOUBLE_EQ(trades[2].price, 102.0);
    EXPECT_EQ(trades[2].quantity, 5);
    // 5 remaining at the 102 level.
    EXPECT_EQ(book.get_volume_at_price(102.0), 5);
    EXPECT_EQ(book.ask_count(), 1);
}

// ---------------------------------------------------------------------------
// Market orders
// ---------------------------------------------------------------------------

TEST(MarketOrder, BuyMatchesBestAskAndNeverRests) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 100.0, 10));
    book.add_order(limit(2, Side::SELL, 101.0, 10));
    auto trades = book.add_order(market(3, Side::BUY, 10));
    ASSERT_EQ(trades.size(), 1u);
    EXPECT_DOUBLE_EQ(trades[0].price, 100.0);
    EXPECT_EQ(book.bid_count(), 0);   // market never rests
}

TEST(MarketOrder, UnfilledRemainderDoesNotRest) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 100.0, 10));   // only 10 available
    auto trades = book.add_order(market(2, Side::BUY, 50));  // wants 50
    ASSERT_EQ(trades.size(), 1u);
    EXPECT_EQ(trades[0].quantity, 10);
    // 40 unfilled simply discarded; nothing rests.
    EXPECT_EQ(book.bid_count(), 0);
    EXPECT_EQ(book.ask_count(), 0);
}

TEST(MarketOrder, SellSweepsMultipleBidLevels) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    book.add_order(limit(2, Side::BUY, 99.0, 10));
    auto trades = book.add_order(market(3, Side::SELL, 15));
    ASSERT_EQ(trades.size(), 2u);
    EXPECT_DOUBLE_EQ(trades[0].price, 100.0);   // best bid first
    EXPECT_DOUBLE_EQ(trades[1].price, 99.0);
    EXPECT_EQ(trades[1].quantity, 5);
    EXPECT_EQ(book.get_volume_at_price(99.0), 5);
}

// ---------------------------------------------------------------------------
// Cancellation
// ---------------------------------------------------------------------------

TEST(Cancel, RemovesRestingOrder) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    EXPECT_EQ(book.bid_count(), 1);
    EXPECT_TRUE(book.cancel_order(1));
    EXPECT_EQ(book.bid_count(), 0);
    EXPECT_FALSE(book.get_best_bid().has_value());
}

TEST(Cancel, OnlyCancelsTargetAtSharedPriceLevel) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    book.add_order(limit(2, Side::BUY, 100.0, 20));
    EXPECT_TRUE(book.cancel_order(1));
    EXPECT_EQ(book.bid_count(), 1);
    EXPECT_EQ(book.get_volume_at_price(100.0), 20);
}

TEST(Cancel, DoubleCancelReturnsFalse) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 100.0, 10));
    EXPECT_TRUE(book.cancel_order(1));
    EXPECT_FALSE(book.cancel_order(1));   // already gone
}

TEST(Cancel, FilledOrderCannotBeCancelled) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::SELL, 100.0, 10));
    book.add_order(limit(2, Side::BUY, 100.0, 10));   // fully fills id 1
    EXPECT_FALSE(book.cancel_order(1));   // no longer in the index
}

// ---------------------------------------------------------------------------
// Modify (re-prices, loses time priority)
// ---------------------------------------------------------------------------

TEST(Modify, ChangesPriceAndQuantity) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    EXPECT_TRUE(book.modify_order(1, 25, 99.0));
    EXPECT_EQ(book.get_volume_at_price(100.0), 0);
    EXPECT_EQ(book.get_volume_at_price(99.0), 25);
    ASSERT_TRUE(book.get_best_bid().has_value());
    EXPECT_DOUBLE_EQ(*book.get_best_bid(), 99.0);
}

TEST(Modify, MovesToBackOfNewPriceQueue) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    book.add_order(limit(2, Side::BUY, 99.0, 10));   // arrives at 99 first
    // Modify order 1 down to 99 -> it should sit behind order 2 in FIFO.
    EXPECT_TRUE(book.modify_order(1, 10, 99.0));
    auto trades = book.add_order(limit(3, Side::SELL, 99.0, 10));
    ASSERT_EQ(trades.size(), 1u);
    EXPECT_EQ(trades[0].buyer_order_id, 2u);   // earlier at this level wins
}

// ---------------------------------------------------------------------------
// Depth queries
// ---------------------------------------------------------------------------

TEST(Depth, AggregatesQuantityAndOrderCountPerLevel) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    book.add_order(limit(2, Side::BUY, 100.0, 15));   // same level
    book.add_order(limit(3, Side::BUY, 99.0, 20));

    auto depth = book.get_bid_depth();
    ASSERT_EQ(depth.size(), 2u);
    // Best (highest) bid first.
    EXPECT_DOUBLE_EQ(depth[0].price, 100.0);
    EXPECT_EQ(depth[0].total_quantity, 25);
    EXPECT_EQ(depth[0].order_count, 2);
    EXPECT_DOUBLE_EQ(depth[1].price, 99.0);
    EXPECT_EQ(depth[1].total_quantity, 20);
    EXPECT_EQ(depth[1].order_count, 1);
}

TEST(Depth, RespectsLevelLimit) {
    OrderBook book("TEST");
    for (int i = 0; i < 5; ++i) {
        book.add_order(limit(static_cast<uint64_t>(i + 1), Side::SELL, 100.0 + i, 10));
    }
    auto depth = book.get_ask_depth(3);
    ASSERT_EQ(depth.size(), 3u);
    EXPECT_DOUBLE_EQ(depth[0].price, 100.0);   // lowest ask first
    EXPECT_DOUBLE_EQ(depth[2].price, 102.0);
}

TEST(Depth, VolumeAtPriceSpansBothSides) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    book.add_order(limit(2, Side::SELL, 101.0, 30));
    EXPECT_EQ(book.get_volume_at_price(100.0), 10);
    EXPECT_EQ(book.get_volume_at_price(101.0), 30);
    EXPECT_EQ(book.get_volume_at_price(123.0), 0);   // empty level
}

// ---------------------------------------------------------------------------
// MatchingEngine multi-symbol routing
// ---------------------------------------------------------------------------

TEST(Engine, RoutesOrdersToCorrectSymbol) {
    MatchingEngine engine;
    engine.submit_order(limit(1, Side::BUY, 100.0, 10, "AAPL"));
    engine.submit_order(limit(2, Side::BUY, 50.0, 10, "MSFT"));

    EXPECT_DOUBLE_EQ(*engine.get_order_book("AAPL").get_best_bid(), 100.0);
    EXPECT_DOUBLE_EQ(*engine.get_order_book("MSFT").get_best_bid(), 50.0);
    // Books are independent: no cross-symbol best ask.
    EXPECT_FALSE(engine.get_order_book("AAPL").get_best_ask().has_value());
}

TEST(Engine, MatchingIsScopedPerSymbol) {
    MatchingEngine engine;
    engine.submit_order(limit(1, Side::SELL, 100.0, 10, "AAPL"));
    // A buy on MSFT must NOT match the AAPL ask.
    auto trades = engine.submit_order(limit(2, Side::BUY, 100.0, 10, "MSFT"));
    EXPECT_TRUE(trades.empty());
    EXPECT_EQ(engine.get_order_book("AAPL").ask_count(), 1);
    EXPECT_EQ(engine.get_order_book("MSFT").bid_count(), 1);
}

TEST(Engine, GetSymbolsListsCreatedBooks) {
    MatchingEngine engine;
    engine.submit_order(limit(1, Side::BUY, 100.0, 10, "AAPL"));
    engine.submit_order(limit(2, Side::BUY, 50.0, 10, "MSFT"));
    auto symbols = engine.get_symbols();
    ASSERT_EQ(symbols.size(), 2u);
    std::sort(symbols.begin(), symbols.end());
    EXPECT_EQ(symbols[0], "AAPL");
    EXPECT_EQ(symbols[1], "MSFT");
}

TEST(Engine, CancelOnUnknownSymbolReturnsFalse) {
    MatchingEngine engine;
    EXPECT_FALSE(engine.cancel_order("NOPE", 1));
}

TEST(Engine, CancelRoutesToSymbolBook) {
    MatchingEngine engine;
    engine.submit_order(limit(1, Side::BUY, 100.0, 10, "AAPL"));
    EXPECT_TRUE(engine.cancel_order("AAPL", 1));
    EXPECT_EQ(engine.get_order_book("AAPL").bid_count(), 0);
}

// ---------------------------------------------------------------------------
// Book invariant: spread stays non-negative after matching churn
// ---------------------------------------------------------------------------

TEST(Invariant, BestBidStrictlyBelowBestAskAfterChurn) {
    OrderBook book("TEST");
    book.add_order(limit(1, Side::BUY, 100.0, 10));
    book.add_order(limit(2, Side::SELL, 101.0, 10));
    book.add_order(limit(3, Side::BUY, 100.5, 5));    // tighten bid
    book.add_order(limit(4, Side::SELL, 100.5, 5));   // crosses the new bid

    if (book.get_best_bid() && book.get_best_ask()) {
        EXPECT_LT(*book.get_best_bid(), *book.get_best_ask());
        EXPECT_GE(book.get_spread(), 0.0);
    }
}

}  // namespace
