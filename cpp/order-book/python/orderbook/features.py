"""Microstructure feature functions computed from the live C++ order book.

These are thin, pure read helpers over the existing pybind11 binding
(``get_best_bid`` / ``get_best_ask`` / ``get_bid_depth`` / ``get_ask_depth``).
They never mutate the book and never re-implement matching — they only derive
the standard limit-order-book microstructure signals that quant LOB tooling
(e.g. ABIDES feature extractors, mbt-gym observation spaces) exposes:

* :func:`quoted_spread`   — best ask minus best bid.
* :func:`mid_price`       — arithmetic mid of the top of book.
* :func:`microprice`      — size-weighted (imbalance-weighted) top-of-book mid.
* :func:`volume_imbalance`— ``(B - A) / (B + A)`` over the top-k depth.
* :func:`depth_profile`   — cumulative quantity per side, top N levels.
* :func:`snapshot`        — a single dict bundling all of the above.

**None-safety contract.** Every function is total: on an empty or one-sided
book the relevant scalar returns ``None`` (and ``depth_profile`` returns an
empty list for the missing side). Nothing here ever raises on a degenerate
book — callers can safely fold these over a streaming simulation.
"""

from __future__ import annotations

from dataclasses import dataclass

import orderbook as ob


def quoted_spread(book: ob.OrderBook) -> float | None:
    """Best ask minus best bid, or ``None`` if either side is empty."""
    bid = book.get_best_bid()
    ask = book.get_best_ask()
    if bid is None or ask is None:
        return None
    return ask - bid


def mid_price(book: ob.OrderBook) -> float | None:
    """Arithmetic mid ``(bid + ask) / 2``, or ``None`` if one-sided/empty."""
    bid = book.get_best_bid()
    ask = book.get_best_ask()
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def _top_sizes(book: ob.OrderBook) -> tuple[float | None, int, float | None, int]:
    """Return ``(bid_px, bid_sz, ask_px, ask_sz)`` for the top level each side."""
    bids = book.get_bid_depth(1)
    asks = book.get_ask_depth(1)
    bid_px = bids[0].price if bids else None
    bid_sz = bids[0].total_quantity if bids else 0
    ask_px = asks[0].price if asks else None
    ask_sz = asks[0].total_quantity if asks else 0
    return bid_px, bid_sz, ask_px, ask_sz


def microprice(book: ob.OrderBook) -> float | None:
    """Size-weighted mid ``(bid_sz*ask_px + ask_sz*bid_px)/(bid_sz+ask_sz)``.

    Weights each side's price by the *opposite* side's resting size, so the
    value leans toward the price the book is more likely to trade at. This is
    the imbalance-weighted top-of-book mid, NOT Stoikov's (2018)
    martingale-adjusted micro-price (which adds a learned correction term).
    Returns ``None`` when either side is empty (no top-of-book on one side) or
    when the combined top-of-book size is zero.
    """
    bid_px, bid_sz, ask_px, ask_sz = _top_sizes(book)
    if bid_px is None or ask_px is None:
        return None
    denom = bid_sz + ask_sz
    if denom <= 0:
        return None
    return (bid_sz * ask_px + ask_sz * bid_px) / denom


def volume_imbalance(book: ob.OrderBook, levels: int = 1) -> float | None:
    """Order-flow imbalance ``(B - A) / (B + A)`` over the top ``levels`` depth.

    ``B`` and ``A`` are the cumulative resting bid/ask quantities over the top
    ``levels`` price levels (``levels=1`` is the classic top-of-book imbalance).
    The result is in ``[-1, 1]``: ``+1`` is all bid, ``-1`` is all ask, ``0`` is
    balanced. Returns ``None`` if either side is empty or both are zero.
    """
    if levels < 1:
        levels = 1
    bids = book.get_bid_depth(levels)
    asks = book.get_ask_depth(levels)
    if not bids or not asks:
        return None
    b = sum(d.total_quantity for d in bids)
    a = sum(d.total_quantity for d in asks)
    denom = b + a
    if denom <= 0:
        return None
    return (b - a) / denom


@dataclass
class ProfileLevel:
    """One level of a cumulative depth profile (price + running cumulative qty)."""

    price: float
    quantity: int
    cumulative: int


def depth_profile(book: ob.OrderBook, side: str, levels: int = 10) -> list[ProfileLevel]:
    """Cumulative depth profile for ``side`` (``"BUY"``/``"BID"`` or ``"SELL"``/``"ASK"``).

    Returns up to ``levels`` :class:`ProfileLevel` entries ordered away from the
    touch (bids descending, asks ascending — the engine's native ordering), each
    carrying the level quantity and the running cumulative quantity from the top
    of book. Returns an empty list when the requested side is empty.
    """
    key = side.upper()
    if key in ("BUY", "BID", "B"):
        depth = book.get_bid_depth(levels)
    elif key in ("SELL", "ASK", "A"):
        depth = book.get_ask_depth(levels)
    else:
        raise ValueError(f"side must be BUY/BID or SELL/ASK, got {side!r}")
    out: list[ProfileLevel] = []
    running = 0
    for d in depth:
        running += d.total_quantity
        out.append(ProfileLevel(price=d.price, quantity=d.total_quantity, cumulative=running))
    return out


def snapshot(book: ob.OrderBook, levels: int = 5) -> dict:
    """Bundle all microstructure features into one None-safe dict.

    Convenient for logging a feature row per simulated step. ``imbalance_topk``
    uses the supplied ``levels``; ``imbalance`` is the top-of-book (level-1)
    value. Every scalar follows the same None-safety contract as the individual
    functions.
    """
    return {
        "best_bid": book.get_best_bid(),
        "best_ask": book.get_best_ask(),
        "spread": quoted_spread(book),
        "mid": mid_price(book),
        "microprice": microprice(book),
        "imbalance": volume_imbalance(book, 1),
        "imbalance_topk": volume_imbalance(book, levels),
        "bid_profile": depth_profile(book, "BUY", levels),
        "ask_profile": depth_profile(book, "SELL", levels),
    }


__all__ = [
    "ProfileLevel",
    "depth_profile",
    "microprice",
    "mid_price",
    "quoted_spread",
    "snapshot",
    "volume_imbalance",
]
