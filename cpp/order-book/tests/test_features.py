"""Tests for the microstructure feature functions in ``orderbook.features``.

Feature math is verified on hand-built books (so the expected values are exact,
not statistical), including the empty and one-sided degenerate cases where every
function must return ``None``/``[]`` rather than raise.
"""

import pytest

pytest.importorskip(
    "orderbook",
    reason="compiled _orderbook extension not built — run cmake --build build first",
)

import orderbook as ob  # noqa: E402
from orderbook import features as feat  # noqa: E402


def _book_with(bids, asks, symbol="AAPL"):
    """Build a book from ``[(price, qty), ...]`` lists for each side."""
    book = ob.OrderBook(symbol)
    oid = 1
    for price, qty in bids:
        book.add_order(ob.Order(oid, symbol, ob.Side.BUY, ob.OrderType.LIMIT, price, qty))
        oid += 1
    for price, qty in asks:
        book.add_order(ob.Order(oid, symbol, ob.Side.SELL, ob.OrderType.LIMIT, price, qty))
        oid += 1
    return book


class TestScalars:
    def test_quoted_spread(self):
        book = _book_with([(100.0, 10)], [(101.0, 10)])
        assert feat.quoted_spread(book) == pytest.approx(1.0)

    def test_mid_price(self):
        book = _book_with([(100.0, 10)], [(102.0, 10)])
        assert feat.mid_price(book) == pytest.approx(101.0)

    def test_microprice_weights_by_opposite_size(self):
        # bid 100 size 30, ask 102 size 10.
        # micro = (bid_sz*ask_px + ask_sz*bid_px)/(bid_sz+ask_sz)
        #       = (30*102 + 10*100) / 40 = (3060 + 1000)/40 = 101.5
        book = _book_with([(100.0, 30)], [(102.0, 10)])
        assert feat.microprice(book) == pytest.approx(101.5)

    def test_microprice_equals_mid_when_balanced(self):
        book = _book_with([(100.0, 10)], [(102.0, 10)])
        assert feat.microprice(book) == pytest.approx(feat.mid_price(book))

    def test_volume_imbalance_top1(self):
        # B=30, A=10 -> (30-10)/(30+10) = 0.5
        book = _book_with([(100.0, 30)], [(102.0, 10)])
        assert feat.volume_imbalance(book, 1) == pytest.approx(0.5)

    def test_volume_imbalance_topk(self):
        # B = 30+20 = 50, A = 10+10 = 20 -> (50-20)/70
        book = _book_with([(100.0, 30), (99.0, 20)], [(102.0, 10), (103.0, 10)])
        assert feat.volume_imbalance(book, 2) == pytest.approx(30 / 70)

    def test_volume_imbalance_levels_below_one_clamps(self):
        book = _book_with([(100.0, 30)], [(102.0, 10)])
        assert feat.volume_imbalance(book, 0) == pytest.approx(0.5)


class TestDepthProfile:
    def test_cumulative_bid_profile(self):
        book = _book_with([(100.0, 30), (99.0, 20), (98.0, 10)], [])
        prof = feat.depth_profile(book, "BUY", 3)
        assert [p.price for p in prof] == [100.0, 99.0, 98.0]  # descending from touch
        assert [p.quantity for p in prof] == [30, 20, 10]
        assert [p.cumulative for p in prof] == [30, 50, 60]

    def test_cumulative_ask_profile(self):
        book = _book_with([], [(101.0, 5), (102.0, 15)])
        prof = feat.depth_profile(book, "ASK", 5)
        assert [p.price for p in prof] == [101.0, 102.0]  # ascending from touch
        assert [p.cumulative for p in prof] == [5, 20]

    def test_profile_respects_level_cap(self):
        book = _book_with([(100.0, 1), (99.0, 1), (98.0, 1)], [])
        assert len(feat.depth_profile(book, "BID", 2)) == 2

    def test_invalid_side_raises(self):
        book = _book_with([(100.0, 1)], [])
        with pytest.raises(ValueError):
            feat.depth_profile(book, "MIDDLE", 1)


class TestNoneSafety:
    def test_empty_book_scalars_are_none(self):
        book = ob.OrderBook("AAPL")
        assert feat.quoted_spread(book) is None
        assert feat.mid_price(book) is None
        assert feat.microprice(book) is None
        assert feat.volume_imbalance(book) is None
        assert feat.depth_profile(book, "BUY") == []
        assert feat.depth_profile(book, "SELL") == []

    def test_one_sided_book_scalars_are_none(self):
        bid_only = _book_with([(100.0, 10)], [])
        assert feat.quoted_spread(bid_only) is None
        assert feat.mid_price(bid_only) is None
        assert feat.microprice(bid_only) is None
        assert feat.volume_imbalance(bid_only) is None
        # The populated side still profiles.
        assert len(feat.depth_profile(bid_only, "BUY")) == 1
        assert feat.depth_profile(bid_only, "SELL") == []

        ask_only = _book_with([], [(101.0, 10)])
        assert feat.microprice(ask_only) is None
        assert feat.volume_imbalance(ask_only) is None


class TestSnapshot:
    def test_snapshot_bundles_all_features(self):
        book = _book_with([(100.0, 30)], [(102.0, 10)])
        snap = feat.snapshot(book, levels=5)
        assert snap["best_bid"] == 100.0
        assert snap["best_ask"] == 102.0
        assert snap["spread"] == pytest.approx(2.0)
        assert snap["mid"] == pytest.approx(101.0)
        assert snap["microprice"] == pytest.approx(101.5)
        assert snap["imbalance"] == pytest.approx(0.5)
        assert "imbalance_topk" in snap
        assert len(snap["bid_profile"]) == 1
        assert len(snap["ask_profile"]) == 1

    def test_snapshot_empty_book_is_none_safe(self):
        snap = feat.snapshot(ob.OrderBook("AAPL"))
        assert snap["mid"] is None
        assert snap["microprice"] is None
        assert snap["bid_profile"] == []
