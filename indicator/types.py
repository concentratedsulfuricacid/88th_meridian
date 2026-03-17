"""Shared types for order book and trade indicator calculations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class BookLevel:
    """One price level on one side of the order book."""

    price: float
    qty: float


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Normalized order book snapshot used by indicator functions."""

    bids: Sequence[BookLevel]
    asks: Sequence[BookLevel]
    ts: float | None = None


@dataclass(frozen=True)
class TradePrint:
    """Trade print with aggressor side classification."""

    price: float
    qty: float
    side: str
    ts: float | None = None
