"""Adapters from stream payloads into normalized engine inputs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from indicator.types import BookLevel, OrderBookSnapshot, TradePrint


def snapshot_from_payload(payload: dict[str, Any], ts: float | None = None) -> OrderBookSnapshot:
    """Convert a Binance-style order book payload into an ``OrderBookSnapshot``."""
    return OrderBookSnapshot(
        bids=[BookLevel(price=float(price), qty=float(qty)) for price, qty in payload.get("bids", [])],
        asks=[BookLevel(price=float(price), qty=float(qty)) for price, qty in payload.get("asks", [])],
        ts=ts,
    )


def trades_from_payloads(
    payloads: Sequence[dict[str, Any]],
    buy_side_labels: Sequence[str] = ("buy", "b"),
    sell_side_labels: Sequence[str] = ("sell", "s"),
) -> list[TradePrint]:
    """Convert classified trade payloads into normalized ``TradePrint`` objects."""
    buy_labels = {label.lower() for label in buy_side_labels}
    sell_labels = {label.lower() for label in sell_side_labels}

    trades: list[TradePrint] = []
    for payload in payloads:
        side = str(payload.get("side", "")).lower()
        if side not in buy_labels and side not in sell_labels:
            raise ValueError(f"Unknown trade side: {payload.get('side')!r}")
        normalized_side = "buy" if side in buy_labels else "sell"
        trades.append(
            TradePrint(
                price=float(payload["price"]),
                qty=float(payload["qty"]),
                side=normalized_side,
                ts=payload.get("ts"),
            )
        )
    return trades
