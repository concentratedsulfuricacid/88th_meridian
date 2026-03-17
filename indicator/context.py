"""Context, quality, and timing indicators built from order book history."""

from __future__ import annotations

import math
from collections.abc import Sequence

from indicator.book import mid_price
from indicator.types import OrderBookSnapshot


def rolling_volatility(mid_prices: Sequence[float]) -> float:
    """Compute realized volatility from consecutive mid-price log returns."""
    if len(mid_prices) < 2:
        return 0.0
    returns = [
        math.log(current / previous)
        for previous, current in zip(mid_prices, mid_prices[1:], strict=False)
        if previous > 0.0 and current > 0.0
    ]
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    return math.sqrt(variance)


def depth_metrics(snapshot: OrderBookSnapshot, depth: int) -> tuple[float, float, float, float]:
    """Return bid qty, ask qty, total qty, and total notional across the top ``depth`` levels."""
    if depth <= 0:
        raise ValueError("depth must be positive")
    bids = snapshot.bids[:depth]
    asks = snapshot.asks[:depth]
    bid_qty = sum(level.qty for level in bids)
    ask_qty = sum(level.qty for level in asks)
    total_qty = bid_qty + ask_qty
    total_notional = sum(level.price * level.qty for level in bids) + sum(level.price * level.qty for level in asks)
    return bid_qty, ask_qty, total_qty, total_notional


def price_response(mid_prices: Sequence[float], lookback: int) -> float:
    """Measure directional response as the current mid minus the lagged mid."""
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if len(mid_prices) <= lookback:
        return 0.0
    return mid_prices[-1] - mid_prices[-1 - lookback]


def breakout_retest_state(
    mid_prices: Sequence[float],
    current_snapshot: OrderBookSnapshot,
    lookback: int,
) -> tuple[bool, bool, bool, bool]:
    """Detect breakout direction and whether the current top of book holds the breakout level."""
    if lookback <= 1 or len(mid_prices) <= lookback:
        return False, False, False, False

    prior_window = mid_prices[-lookback - 1 : -1]
    current_mid = mid_prices[-1]
    previous_high = max(prior_window)
    previous_low = min(prior_window)
    best_bid = current_snapshot.bids[0].price if current_snapshot.bids else 0.0
    best_ask = current_snapshot.asks[0].price if current_snapshot.asks else 0.0

    breakout_long = current_mid > previous_high
    breakout_short = current_mid < previous_low
    retest_long = breakout_long and best_bid >= previous_high
    retest_short = breakout_short and best_ask <= previous_low
    return breakout_long, breakout_short, retest_long, retest_short


def pullback_stabilization_state(
    snapshots: Sequence[OrderBookSnapshot],
    mid_prices: Sequence[float],
    lookback: int,
) -> tuple[bool, bool]:
    """Detect whether price has pulled back and stabilized with book support."""
    if lookback <= 1 or len(mid_prices) < lookback:
        return False, False

    recent_prices = list(mid_prices[-lookback:])
    current_snapshot = snapshots[-1]
    current_mid = recent_prices[-1]
    recent_high = max(recent_prices)
    recent_low = min(recent_prices)
    retrace = recent_high - recent_low
    if math.isclose(retrace, 0.0):
        return False, False

    best_bid = current_snapshot.bids[0].price if current_snapshot.bids else 0.0
    best_ask = current_snapshot.asks[0].price if current_snapshot.asks else 0.0
    long_stable = current_mid >= (recent_high - 0.4 * retrace) and best_bid >= (recent_high - 0.5 * retrace)
    short_stable = current_mid <= (recent_low + 0.4 * retrace) and best_ask <= (recent_low + 0.5 * retrace)
    return long_stable, short_stable


def current_mid(snapshot: OrderBookSnapshot) -> float:
    """Convenience alias that mirrors the engine's use of midpoint history."""
    return mid_price(snapshot)
