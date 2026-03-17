"""Order flow imbalance and aggressor flow indicators."""

from __future__ import annotations

import math
from collections.abc import Sequence

from indicator.types import BookLevel, OrderBookSnapshot, TradePrint


def _level_or_zero(levels: Sequence[BookLevel], level: int) -> BookLevel:
    if level < 1:
        raise ValueError("level must be >= 1")
    if level <= len(levels):
        return levels[level - 1]
    return BookLevel(price=0.0, qty=0.0)


def _bid_flow_increment(previous: BookLevel, current: BookLevel) -> float:
    if current.price > previous.price:
        return current.qty
    if math.isclose(current.price, previous.price):
        return current.qty - previous.qty
    return -previous.qty


def _ask_flow_increment(previous: BookLevel, current: BookLevel) -> float:
    if current.price > previous.price:
        return -previous.qty
    if math.isclose(current.price, previous.price):
        return current.qty - previous.qty
    return current.qty


def ofi_increment(previous: OrderBookSnapshot, current: OrderBookSnapshot) -> float:
    """Compute the event-level order flow imbalance increment at level 1."""
    previous_bid = _level_or_zero(previous.bids, 1)
    previous_ask = _level_or_zero(previous.asks, 1)
    current_bid = _level_or_zero(current.bids, 1)
    current_ask = _level_or_zero(current.asks, 1)
    delta_w = _bid_flow_increment(previous_bid, current_bid)
    delta_v = _ask_flow_increment(previous_ask, current_ask)
    return delta_w - delta_v


def ofi_window(snapshots: Sequence[OrderBookSnapshot]) -> float:
    """Aggregate level-1 OFI increments over a sequence of snapshots."""
    if len(snapshots) < 2:
        return 0.0
    return sum(ofi_increment(previous, current) for previous, current in zip(snapshots, snapshots[1:], strict=False))


def mlofi_increment(previous: OrderBookSnapshot, current: OrderBookSnapshot, levels: int) -> list[float]:
    """Compute event-level multi-level OFI increments up to ``levels``."""
    if levels <= 0:
        raise ValueError("levels must be positive")

    increments: list[float] = []
    for level in range(1, levels + 1):
        previous_bid = _level_or_zero(previous.bids, level)
        previous_ask = _level_or_zero(previous.asks, level)
        current_bid = _level_or_zero(current.bids, level)
        current_ask = _level_or_zero(current.asks, level)
        delta_w = _bid_flow_increment(previous_bid, current_bid)
        delta_v = _ask_flow_increment(previous_ask, current_ask)
        increments.append(delta_w - delta_v)
    return increments


def mlofi_window(snapshots: Sequence[OrderBookSnapshot], levels: int) -> list[float]:
    """Aggregate MLOFI increments over a sequence of snapshots."""
    if levels <= 0:
        raise ValueError("levels must be positive")
    totals = [0.0] * levels
    for previous, current in zip(snapshots, snapshots[1:], strict=False):
        increments = mlofi_increment(previous, current, levels)
        totals = [total + increment for total, increment in zip(totals, increments, strict=True)]
    return totals


def aggressive_buy_sell_flow(trades: Sequence[TradePrint]) -> tuple[float, float]:
    """Aggregate aggressive buy and sell volume from classified trade prints."""
    buy_volume = sum(trade.qty for trade in trades if trade.side.lower() == "buy")
    sell_volume = sum(trade.qty for trade in trades if trade.side.lower() == "sell")
    return buy_volume, sell_volume


def aggressive_flow_imbalance(trades: Sequence[TradePrint]) -> float:
    """Compute normalized aggressive-flow imbalance from trade prints."""
    buy_volume, sell_volume = aggressive_buy_sell_flow(trades)
    total_volume = buy_volume + sell_volume
    if math.isclose(total_volume, 0.0):
        return 0.0
    return (buy_volume - sell_volume) / total_volume
