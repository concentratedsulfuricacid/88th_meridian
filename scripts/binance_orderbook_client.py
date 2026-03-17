"""Shared Binance local order book client for terminal and dashboard tools."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import ParseResult, urlencode, urlparse, urlunparse
from urllib.request import urlopen

import websockets


DEFAULT_BASE_URL = "wss://stream.binance.com:9443/ws"
VALID_SPEEDS = ("1000ms", "100ms")
MAX_SNAPSHOT_LIMIT = 1000


def build_diff_stream_url(base_url: str, symbol: str, speed: str) -> str:
    """Build the websocket URL for Binance diff-depth updates."""
    normalized = symbol.lower()
    speed_suffix = "" if speed == "1000ms" else f"@{speed}"
    return f"{base_url.rstrip('/')}/{normalized}@depth{speed_suffix}"


def build_snapshot_url(base_url: str, symbol: str, limit: int) -> str:
    """Infer the REST depth snapshot endpoint from the websocket base URL."""
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if host.startswith("stream."):
        api_host = host.replace("stream.", "api.", 1)
    else:
        api_host = host

    rest_url = ParseResult(
        scheme="https",
        netloc=api_host,
        path="/api/v3/depth",
        params="",
        query=urlencode({"symbol": symbol.upper(), "limit": limit}),
        fragment="",
    )
    return urlunparse(rest_url)


def fetch_depth_snapshot(base_url: str, symbol: str, limit: int) -> dict[str, Any]:
    """Fetch the initial order book snapshot over REST."""
    snapshot_url = build_snapshot_url(base_url, symbol, limit)
    with urlopen(snapshot_url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def sort_levels(side: dict[str, str], reverse: bool, limit: int) -> list[list[str]]:
    """Return ordered price levels for one side of the book."""
    prices = sorted(side.keys(), key=float, reverse=reverse)
    return [[price, side[price]] for price in prices[:limit]]


def initialize_local_book(snapshot: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Create mutable bid and ask maps from a REST snapshot."""
    bids = {price: qty for price, qty in snapshot.get("bids", []) if qty != "0" and qty != "0.00000000"}
    asks = {price: qty for price, qty in snapshot.get("asks", []) if qty != "0" and qty != "0.00000000"}
    return bids, asks


def apply_absolute_updates(side: dict[str, str], updates: list[list[str]]) -> None:
    """Apply Binance absolute quantity updates to one side of the local book."""
    for price, qty in updates:
        if float(qty) == 0.0:
            side.pop(price, None)
        else:
            side[price] = qty


def materialize_book(
    symbol: str,
    snapshot_limit: int,
    last_update_id: int,
    bids: dict[str, str],
    asks: dict[str, str],
) -> dict[str, Any]:
    """Build the normalized local order book payload consumed by the UIs."""
    return {
        "symbol": symbol.upper(),
        "lastUpdateId": last_update_id,
        "bids": sort_levels(bids, reverse=True, limit=snapshot_limit),
        "asks": sort_levels(asks, reverse=False, limit=snapshot_limit),
    }


@dataclass
class StreamState:
    """Thread-safe container for the latest local order book and connection status."""

    symbol: str
    base_url: str
    levels: int
    speed: str
    latest_payload: dict[str, Any] | None = None
    latest_error: str | None = None
    connected: bool = False
    updated_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> dict[str, Any]:
        """Return a copy of the current stream state for UI consumers."""
        with self.lock:
            return {
                "symbol": self.symbol,
                "base_url": self.base_url,
                "levels": self.levels,
                "speed": self.speed,
                "latest_payload": self.latest_payload,
                "latest_error": self.latest_error,
                "connected": self.connected,
                "updated_at": self.updated_at,
            }


async def stream_local_orderbook(
    symbol: str,
    base_url: str,
    snapshot_limit: int,
    speed: str,
    on_message,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Maintain a local order book from a REST snapshot and websocket diff stream."""
    stop_event = stop_event or asyncio.Event()
    stream_url = build_diff_stream_url(base_url, symbol, speed)

    while not stop_event.is_set():
        receiver_task: asyncio.Task[Any] | None = None
        try:
            async with websockets.connect(stream_url, ping_interval=20, ping_timeout=20) as ws:
                queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

                async def receiver() -> None:
                    while not stop_event.is_set():
                        raw_message = await asyncio.wait_for(ws.recv(), timeout=30)
                        await queue.put(json.loads(raw_message))

                receiver_task = asyncio.create_task(receiver())
                snapshot = await asyncio.to_thread(fetch_depth_snapshot, base_url, symbol, snapshot_limit)
                snapshot_last_update = int(snapshot["lastUpdateId"])
                bids, asks = initialize_local_book(snapshot)

                buffered_events: list[dict[str, Any]] = []
                while True:
                    try:
                        buffered_events.append(queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                while not buffered_events and not stop_event.is_set():
                    buffered_events.append(await asyncio.wait_for(queue.get(), timeout=30))

                buffered_events = [event for event in buffered_events if int(event["u"]) > snapshot_last_update]
                sync_update_id = snapshot_last_update + 1
                start_index = next(
                    (
                        index
                        for index, event in enumerate(buffered_events)
                        if int(event["U"]) <= sync_update_id <= int(event["u"])
                    ),
                    None,
                )

                if start_index is None:
                    if receiver_task:
                        receiver_task.cancel()
                    continue

                current_update_id = snapshot_last_update
                synced_events = buffered_events[start_index:]

                for event in synced_events:
                    if current_update_id and int(event["U"]) != current_update_id + 1:
                        raise ValueError("Depth update gap detected during snapshot synchronization")
                    apply_absolute_updates(bids, event.get("b", []))
                    apply_absolute_updates(asks, event.get("a", []))
                    current_update_id = int(event["u"])
                    payload = materialize_book(symbol, snapshot_limit, current_update_id, bids, asks)
                    maybe_awaitable = on_message(payload)
                    if asyncio.iscoroutine(maybe_awaitable):
                        await maybe_awaitable

                while not stop_event.is_set():
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    if int(event["U"]) != current_update_id + 1:
                        raise ValueError("Depth update gap detected; resync required")
                    apply_absolute_updates(bids, event.get("b", []))
                    apply_absolute_updates(asks, event.get("a", []))
                    current_update_id = int(event["u"])
                    payload = materialize_book(symbol, snapshot_limit, current_update_id, bids, asks)
                    maybe_awaitable = on_message(payload)
                    if asyncio.iscoroutine(maybe_awaitable):
                        await maybe_awaitable
        except asyncio.TimeoutError:
            await asyncio.sleep(1)
        except websockets.ConnectionClosed:
            await asyncio.sleep(1)
        except OSError:
            await asyncio.sleep(3)
        finally:
            if receiver_task:
                receiver_task.cancel()


def start_background_stream(state: StreamState) -> threading.Thread:
    """Start the local order book stream in a daemon thread and update ``state`` in place."""

    def runner() -> None:
        async def on_message(payload: dict[str, Any]) -> None:
            with state.lock:
                state.latest_payload = payload
                state.latest_error = None
                state.connected = True
                state.updated_at = time.time()

        async def loop_forever() -> None:
            while True:
                try:
                    await stream_local_orderbook(
                        symbol=state.symbol,
                        base_url=state.base_url,
                        snapshot_limit=state.levels,
                        speed=state.speed,
                        on_message=on_message,
                    )
                except websockets.InvalidStatus as exc:
                    with state.lock:
                        state.connected = False
                        state.latest_error = (
                            f"Websocket handshake failed with HTTP {exc.response.status_code}. "
                            "Try a different Binance endpoint or disable VPN if your IP is restricted."
                        )
                    return
                except Exception as exc:
                    with state.lock:
                        state.connected = False
                        state.latest_error = f"{type(exc).__name__}: {exc}"
                    await asyncio.sleep(3)

        asyncio.run(loop_forever())

    thread = threading.Thread(target=runner, name="binance-orderbook-stream", daemon=True)
    thread.start()
    return thread
