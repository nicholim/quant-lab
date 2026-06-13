"""Snapshot-level L2 order-book features (opt-in, alongside the depth feed).

Pure, NaN-safe functions computing standard microstructure quantities from a
single normalized :class:`~src.normalizer.BookUpdate` snapshot:

* :func:`midprice` — ``(best_bid + best_ask) / 2``.
* :func:`microprice` — size-weighted mid,
  ``(bid_size * ask_price + ask_size * bid_price) / (bid_size + ask_size)``.
* :func:`quoted_spread` / :func:`quoted_spread_bps` — ``ask - bid`` absolute
  and in basis points of the mid.
* :func:`cumulative_depth` — summed size across the top-N levels of one side.
* :func:`depth_imbalance` — ``(B - A) / (B + A)`` over the top-N levels'
  cumulative sizes (``levels=1`` is the classic top-of-book imbalance).
* :func:`compute_book_features` — all of the above bundled into a
  :class:`BookFeatures` dataclass for caching/publishing.

Every function returns ``None`` (rather than raising) on an empty or one-sided
book, or when a denominator would be zero — feed hiccups must never kill the
pipeline.

SCOPE NOTE — snapshot-level only, NOT event-level OFI. These features are
computed from independent top-N snapshots. True order-flow imbalance in the
Cont–Kukanov–Stoikov sense requires event-level add/cancel/trade attribution
against an incrementally maintained book, which a 100 ms partial-depth
snapshot stream cannot provide. Do not present ``depth_imbalance`` as OFI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .normalizer import BookLevel, BookUpdate


@dataclass
class BookFeatures:
    """Bundle of snapshot-level book features (``None`` where undefined).

    ``bid_depth`` / ``ask_depth`` and the imbalances are computed over the top
    ``levels`` levels actually present (fewer if the snapshot is shallower).
    """

    midprice: float | None
    microprice: float | None
    quoted_spread: float | None
    quoted_spread_bps: float | None
    bid_depth: float | None
    ask_depth: float | None
    imbalance_l1: float | None
    imbalance: float | None
    levels: int


def _finite(value: float | None) -> float | None:
    """Coerce non-finite floats (NaN/inf from a corrupt feed) to ``None``."""
    if value is None or not math.isfinite(value):
        return None
    return value


def _top(book: BookUpdate) -> tuple[BookLevel, BookLevel] | None:
    """Best bid/ask levels, or ``None`` for an empty or one-sided book."""
    if not book.bids or not book.asks:
        return None
    bid, ask = book.bids[0], book.asks[0]
    if not (math.isfinite(bid.price) and math.isfinite(ask.price)):
        return None
    return bid, ask


def midprice(book: BookUpdate) -> float | None:
    """Mid price ``(best_bid + best_ask) / 2``; ``None`` if one-sided/empty."""
    top = _top(book)
    if top is None:
        return None
    bid, ask = top
    return _finite((bid.price + ask.price) / 2.0)


def microprice(book: BookUpdate) -> float | None:
    """Size-weighted mid at the top of book.

    ``(bid_size * ask_price + ask_size * bid_price) / (bid_size + ask_size)`` —
    the imbalance-weighted midprice (more bid size pulls the value toward the
    ask). This is the snapshot size-weighted mid, NOT Stoikov's (2018)
    martingale-adjusted micro-price, which adds a learned correction term.
    ``None`` for a one-sided/empty book or when both top sizes are zero.
    """
    top = _top(book)
    if top is None:
        return None
    bid, ask = top
    denom = bid.quantity + ask.quantity
    if not math.isfinite(denom) or denom <= 0:
        return None
    return _finite((bid.quantity * ask.price + ask.quantity * bid.price) / denom)


def quoted_spread(book: BookUpdate) -> float | None:
    """Quoted spread ``best_ask - best_bid``; ``None`` if one-sided/empty."""
    top = _top(book)
    if top is None:
        return None
    bid, ask = top
    return _finite(ask.price - bid.price)


def quoted_spread_bps(book: BookUpdate) -> float | None:
    """Quoted spread in basis points of the mid; ``None`` when mid is 0/undefined."""
    spread = quoted_spread(book)
    mid = midprice(book)
    if spread is None or mid is None or mid <= 0:
        return None
    return _finite(spread / mid * 10_000.0)


def cumulative_depth(levels: list[BookLevel], n: int | None = None) -> float | None:
    """Total resting size across the top ``n`` levels of one side.

    ``n=None`` sums every level present. Returns ``None`` for an empty side
    (so "no data" is distinguishable from a genuine zero-size level).
    """
    if not levels:
        return None
    selected = levels if n is None else levels[: max(n, 0)]
    if not selected:
        return None
    total = sum(lvl.quantity for lvl in selected)
    return _finite(total)


def depth_imbalance(book: BookUpdate, levels: int = 1) -> float | None:
    """Depth imbalance ``(B - A) / (B + A)`` over top-``levels`` cumulative sizes.

    ``levels=1`` is the classic top-of-book imbalance; larger ``levels`` use
    the cumulative size of the top-N levels per side. Returns a value in
    ``[-1, +1]``, or ``None`` for a one-sided/empty book or zero total depth.

    NOTE: this is a SNAPSHOT depth imbalance, not event-level order-flow
    imbalance (OFI) — see the module docstring.
    """
    if not book.bids or not book.asks:
        return None
    bid_depth = cumulative_depth(book.bids, levels)
    ask_depth = cumulative_depth(book.asks, levels)
    if bid_depth is None or ask_depth is None:
        return None
    total = bid_depth + ask_depth
    if not math.isfinite(total) or total <= 0:
        return None
    return _finite((bid_depth - ask_depth) / total)


def compute_book_features(book: BookUpdate, levels: int = 5) -> BookFeatures:
    """Compute the full :class:`BookFeatures` bundle for one snapshot.

    Never raises: every field degrades to ``None`` independently when the
    snapshot is empty, one-sided, or carries non-finite values. ``levels``
    bounds the depth/imbalance aggregation (clamped to what the snapshot has).
    """
    return BookFeatures(
        midprice=midprice(book),
        microprice=microprice(book),
        quoted_spread=quoted_spread(book),
        quoted_spread_bps=quoted_spread_bps(book),
        bid_depth=cumulative_depth(book.bids, levels),
        ask_depth=cumulative_depth(book.asks, levels),
        imbalance_l1=depth_imbalance(book, levels=1),
        imbalance=depth_imbalance(book, levels=levels),
        levels=levels,
    )
