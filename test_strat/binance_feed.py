"""Adapters that normalize the existing Binance order book stream into quotes."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from scripts.binance_orderbook_client import stream_local_orderbook
from test_strat.types import QuoteSample


def now_ms() -> int:
    """Return the current Unix time in milliseconds."""
    return time.time_ns() // 1_000_000


def normalize_binance_quote(pair: str, payload: dict[str, Any], recv_ts_ms: int | None = None) -> QuoteSample:
    """Convert one Binance local-book payload into a normalized top-of-book quote."""
    recv_ts_ms = recv_ts_ms if recv_ts_ms is not None else now_ms()
    bids = payload.get("bids", [])
    asks = payload.get("asks", [])
    if not bids or not asks:
        raise ValueError("Binance payload requires at least one bid and one ask level")

    bid = float(bids[0][0])
    ask = float(asks[0][0])

    return QuoteSample(
        source="binance",
        pair=pair,
        event_ts_ms=recv_ts_ms,
        recv_ts_ms=recv_ts_ms,
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2.0,
        spread=ask - bid,
        sequence=int(payload["lastUpdateId"]) if payload.get("lastUpdateId") is not None else None,
        meta={"book_depth_levels": len(bids)},
    )


async def stream_binance_quotes(
    pair: str,
    symbol: str,
    base_url: str,
    snapshot_limit: int,
    speed: str,
    on_quote,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Run the existing Binance local-book stream and emit normalized quotes."""
    stop_event = stop_event or asyncio.Event()

    async def on_message(payload: dict[str, Any]) -> None:
        quote = normalize_binance_quote(pair=pair, payload=payload, recv_ts_ms=now_ms())
        maybe_awaitable = on_quote(quote)
        if asyncio.iscoroutine(maybe_awaitable):
            await maybe_awaitable

    await stream_local_orderbook(
        symbol=symbol,
        base_url=base_url,
        snapshot_limit=snapshot_limit,
        speed=speed,
        on_message=on_message,
        stop_event=stop_event,
    )
