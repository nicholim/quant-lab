// pybind11 bindings for the C++ matching engine.
//
// Exposes the core types (Order / Trade / DepthLevel), the Side / OrderType /
// TimeInForce enums, and the OrderBook + MatchingEngine classes so the engine
// can be driven directly from Python — submit MARKET/LIMIT orders with any
// GTC/IOC/FOK/POST_ONLY time-in-force and read back fills + book state without
// parsing demo stdout.
//
// Built into an importable module named `_orderbook` (see CMakeLists.txt's
// pybind11_add_module target). The thin Python package `orderbook/` re-exports
// it with a friendlier surface.

#include "matching_engine.h"
#include "order.h"
#include "order_book.h"
#include "trade.h"

#include <pybind11/chrono.h>
#include <pybind11/operators.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <sstream>
#include <string>

namespace py = pybind11;

namespace {

std::string side_to_string(Side s) { return s == Side::BUY ? "BUY" : "SELL"; }

std::string order_type_to_string(OrderType t) {
    return t == OrderType::MARKET ? "MARKET" : "LIMIT";
}

std::string tif_to_string(TimeInForce t) {
    switch (t) {
        case TimeInForce::GTC:
            return "GTC";
        case TimeInForce::IOC:
            return "IOC";
        case TimeInForce::FOK:
            return "FOK";
        case TimeInForce::POST_ONLY:
            return "POST_ONLY";
    }
    return "GTC";
}

}  // namespace

PYBIND11_MODULE(_orderbook, m) {
    m.doc() =
        "Python bindings for the C++ price-time-priority matching engine. "
        "Submit orders and read fills + book state directly from Python.";

    py::enum_<Side>(m, "Side")
        .value("BUY", Side::BUY)
        .value("SELL", Side::SELL);

    py::enum_<OrderType>(m, "OrderType")
        .value("MARKET", OrderType::MARKET)
        .value("LIMIT", OrderType::LIMIT);

    py::enum_<TimeInForce>(m, "TimeInForce")
        .value("GTC", TimeInForce::GTC)
        .value("IOC", TimeInForce::IOC)
        .value("FOK", TimeInForce::FOK)
        .value("POST_ONLY", TimeInForce::POST_ONLY);

    py::class_<Order>(m, "Order")
        .def(py::init([](uint64_t id, const std::string& symbol, Side side, OrderType type,
                         double price, int quantity, TimeInForce tif) {
                 Order o;
                 o.id = id;
                 o.symbol = symbol;
                 o.side = side;
                 o.type = type;
                 o.price = price;
                 o.quantity = quantity;
                 o.remaining_quantity = quantity;
                 o.tif = tif;
                 return o;
             }),
             py::arg("id"), py::arg("symbol"), py::arg("side"), py::arg("type"),
             py::arg("price") = 0.0, py::arg("quantity") = 0,
             py::arg("tif") = TimeInForce::GTC,
             "Create an order. remaining_quantity is initialized to quantity.")
        .def_readwrite("id", &Order::id)
        .def_readwrite("symbol", &Order::symbol)
        .def_readwrite("side", &Order::side)
        .def_readwrite("type", &Order::type)
        .def_readwrite("price", &Order::price)
        .def_readwrite("quantity", &Order::quantity)
        .def_readwrite("remaining_quantity", &Order::remaining_quantity)
        .def_readwrite("tif", &Order::tif)
        .def("is_filled", &Order::is_filled)
        .def("__repr__", [](const Order& o) {
            std::ostringstream ss;
            ss << "Order(id=" << o.id << ", symbol='" << o.symbol << "', side="
               << side_to_string(o.side) << ", type=" << order_type_to_string(o.type)
               << ", price=" << o.price << ", quantity=" << o.quantity
               << ", remaining=" << o.remaining_quantity << ", tif=" << tif_to_string(o.tif)
               << ")";
            return ss.str();
        });

    py::class_<Trade>(m, "Trade")
        .def_readonly("buyer_order_id", &Trade::buyer_order_id)
        .def_readonly("seller_order_id", &Trade::seller_order_id)
        .def_readonly("symbol", &Trade::symbol)
        .def_readonly("price", &Trade::price)
        .def_readonly("quantity", &Trade::quantity)
        .def("__repr__", [](const Trade& t) {
            std::ostringstream ss;
            ss << "Trade(symbol='" << t.symbol << "', price=" << t.price
               << ", quantity=" << t.quantity << ", buyer=" << t.buyer_order_id
               << ", seller=" << t.seller_order_id << ")";
            return ss.str();
        });

    py::class_<DepthLevel>(m, "DepthLevel")
        .def_readonly("price", &DepthLevel::price)
        .def_readonly("total_quantity", &DepthLevel::total_quantity)
        .def_readonly("order_count", &DepthLevel::order_count)
        .def("__repr__", [](const DepthLevel& d) {
            std::ostringstream ss;
            ss << "DepthLevel(price=" << d.price << ", total_quantity=" << d.total_quantity
               << ", order_count=" << d.order_count << ")";
            return ss.str();
        });

    py::class_<OrderBook>(m, "OrderBook")
        .def(py::init<const std::string&>(), py::arg("symbol"))
        .def("add_order", &OrderBook::add_order, py::arg("order"),
             "Submit an order; returns the list of resulting trades (fills).")
        .def("cancel_order", &OrderBook::cancel_order, py::arg("order_id"),
             "Cancel a resting order by id. Returns True if found and removed.")
        .def("modify_order", &OrderBook::modify_order, py::arg("order_id"),
             py::arg("new_quantity"), py::arg("new_price"),
             "Modify a resting order (loses time priority). Returns True if found.")
        .def("get_best_bid", &OrderBook::get_best_bid,
             "Best bid price, or None if no bids.")
        .def("get_best_ask", &OrderBook::get_best_ask,
             "Best ask price, or None if no asks.")
        .def("get_spread", &OrderBook::get_spread)
        .def("get_bid_depth", &OrderBook::get_bid_depth, py::arg("levels") = 10)
        .def("get_ask_depth", &OrderBook::get_ask_depth, py::arg("levels") = 10)
        .def("get_volume_at_price", &OrderBook::get_volume_at_price, py::arg("price"))
        .def("bid_count", &OrderBook::bid_count)
        .def("ask_count", &OrderBook::ask_count)
        .def_property_readonly("symbol", &OrderBook::symbol);

    py::class_<MatchingEngine>(m, "MatchingEngine")
        .def(py::init<>())
        .def("submit_order", &MatchingEngine::submit_order, py::arg("order"),
             "Route an order to its symbol's book; returns resulting trades.")
        .def("cancel_order", &MatchingEngine::cancel_order, py::arg("symbol"),
             py::arg("order_id"))
        .def("get_order_book", &MatchingEngine::get_order_book, py::arg("symbol"),
             py::return_value_policy::reference_internal,
             "Get (creating if needed) the OrderBook for a symbol.")
        .def("get_symbols", &MatchingEngine::get_symbols);
}
