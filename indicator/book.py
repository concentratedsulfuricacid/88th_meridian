"""Order book price and imbalance indicators."""

from __future__ import annotations

import math
from collections.abc import Sequence

from indicator.types import BookLevel, OrderBookSnapshot


def _require_top_of_book(snapshot: OrderBookSnapshot) -> tuple[BookLevel, BookLevel]:
    if not snapshot.bids or not snapshot.asks:
        raise ValueError("Order book snapshot requires at least one bid and one ask level")
    return snapshot.bids[0], snapshot.asks[0]


def _sum_qty(levels: Sequence[BookLevel]) -> float:
    return sum(level.qty for level in levels)


def _weighted_sum_qty(levels: Sequence[BookLevel], weights: Sequence[float]) -> float:
    return sum(weight * level.qty for weight, level in zip(weights, levels, strict=False))


def _vwap(levels: Sequence[BookLevel]) -> float:
    total_qty = _sum_qty(levels)
    if math.isclose(total_qty, 0.0):
        raise ValueError("VWAP requires positive total quantity")
    return sum(level.price * level.qty for level in levels) / total_qty


def _normalized_difference(bid_value: float, ask_value: float) -> float:
    total = bid_value + ask_value
    if math.isclose(total, 0.0):
        return 0.0
    return (bid_value - ask_value) / total


def top_n_levels(levels: Sequence[BookLevel], depth: int) -> Sequence[BookLevel]:
    """Return up to ``depth`` levels from the front of one side of the book."""
    if depth <= 0:
        raise ValueError("depth must be positive")
    return levels[:depth]


def mid_price(snapshot: OrderBookSnapshot) -> float:
    """Compute the standard midpoint from the best bid and best ask."""
    bid_1, ask_1 = _require_top_of_book(snapshot)
    return (bid_1.price + ask_1.price) / 2.0


def spread(snapshot: OrderBookSnapshot) -> float:
    """Compute the top-of-book spread."""
    bid_1, ask_1 = _require_top_of_book(snapshot)
    return ask_1.price - bid_1.price


def level_1_imbalance(snapshot: OrderBookSnapshot) -> float:
    """Compute top-of-book size imbalance."""
    bid_1, ask_1 = _require_top_of_book(snapshot)
    return _normalized_difference(bid_1.qty, ask_1.qty)


def top_n_imbalance(snapshot: OrderBookSnapshot, depth: int) -> float:
    """Compute the unweighted top-N book imbalance."""
    bids = top_n_levels(snapshot.bids, depth)
    asks = top_n_levels(snapshot.asks, depth)
    return _normalized_difference(_sum_qty(bids), _sum_qty(asks))


def inverse_level_weights(depth: int) -> list[float]:
    """Return weights ``1 / i`` for ``i = 1..depth``."""
    if depth <= 0:
        raise ValueError("depth must be positive")
    return [1.0 / i for i in range(1, depth + 1)]


def exponential_level_weights(depth: int, decay: float) -> list[float]:
    """Return weights ``exp(-decay * (i - 1))`` for ``i = 1..depth``."""
    if depth <= 0:
        raise ValueError("depth must be positive")
    return [math.exp(-decay * i) for i in range(depth)]


def weighted_top_n_imbalance(
    snapshot: OrderBookSnapshot,
    depth: int,
    weights: Sequence[float] | None = None,
) -> float:
    """Compute weighted top-N imbalance, defaulting to inverse-level weights."""
    bids = top_n_levels(snapshot.bids, depth)
    asks = top_n_levels(snapshot.asks, depth)
    effective_depth = min(len(bids), len(asks))
    if effective_depth == 0:
        raise ValueError("Order book snapshot requires at least one bid and one ask level")
    if weights is None:
        weights = inverse_level_weights(effective_depth)
    if len(weights) < effective_depth:
        raise ValueError("weights must cover at least the requested depth")
    bid_value = _weighted_sum_qty(bids[:effective_depth], weights[:effective_depth])
    ask_value = _weighted_sum_qty(asks[:effective_depth], weights[:effective_depth])
    return _normalized_difference(bid_value, ask_value)


def weighted_mid_price(snapshot: OrderBookSnapshot) -> float:
    """Compute the weighted mid-price using opposite-side top-of-book sizes."""
    bid_1, ask_1 = _require_top_of_book(snapshot)
    total_qty = bid_1.qty + ask_1.qty
    if math.isclose(total_qty, 0.0):
        return mid_price(snapshot)
    return ((bid_1.price * ask_1.qty) + (ask_1.price * bid_1.qty)) / total_qty


def microprice(snapshot: OrderBookSnapshot) -> float:
    """Compute the practical microprice proxy, equal to weighted mid-price here."""
    return weighted_mid_price(snapshot)


def vwap_buy_to_mid(snapshot: OrderBookSnapshot, depth: int) -> float:
    """Compute bid-side VWAP minus the current mid-price over the top ``depth`` bid levels."""
    bids = top_n_levels(snapshot.bids, depth)
    return _vwap(bids) - mid_price(snapshot)


def vwap_sell_to_mid(snapshot: OrderBookSnapshot, depth: int) -> float:
    """Compute ask-side VWAP minus the current mid-price over the top ``depth`` ask levels."""
    asks = top_n_levels(snapshot.asks, depth)
    return _vwap(asks) - mid_price(snapshot)
