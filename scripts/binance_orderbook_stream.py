#!/usr/bin/env python3
"""Terminal order book viewer for live Binance partial-depth websocket data."""

import argparse
import asyncio
import json
import signal
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from shutil import get_terminal_size
from typing import Any

from binance_orderbook_client import (
    DEFAULT_BASE_URL,
    MAX_SNAPSHOT_LIMIT,
    VALID_SPEEDS,
    stream_local_orderbook,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the terminal order book viewer."""
    parser = argparse.ArgumentParser(
        description="Stream live Binance order book data from the websocket API."
    )
    parser.add_argument(
        "--symbol",
        default="BTCUSDT",
        help="Trading pair symbol, e.g. BTCUSDT or ETHUSDT.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Websocket base URL, e.g. wss://stream.binance.com:9443/ws or wss://stream.binance.us:9443/ws.",
    )
    parser.add_argument(
        "--levels",
        type=int,
        default=200,
        help=f"Local order book depth to maintain from the REST snapshot, up to {MAX_SNAPSHOT_LIMIT}.",
    )
    parser.add_argument(
        "--speed",
        default="100ms",
        choices=VALID_SPEEDS,
        help="Update speed for the Binance partial depth stream.",
    )
    parser.add_argument(
        "--show-top",
        type=int,
        default=10,
        help="Number of bid and ask rows to print from each update.",
    )
    parser.add_argument(
        "--view",
        default="live",
        choices=("live", "log"),
        help="Render a live terminal book or print each update as a scrolling log.",
    )
    parser.add_argument(
        "--cumulative",
        action="store_true",
        help="Show cumulative quantity at each level.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSONL output path for raw websocket messages.",
    )
    return parser.parse_args()


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def to_float(value: str) -> float:
    """Convert a Binance numeric string field into a float."""
    return float(value)


def format_number(value: float) -> str:
    """Format a float for compact terminal display."""
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def screen_clear() -> str:
    """Return ANSI escape codes that clear the terminal and move the cursor home."""
    return "\033[2J\033[H"


def build_side_rows(rows: list[list[str]], limit: int, cumulative: bool) -> list[dict[str, float]]:
    """Transform raw bid or ask rows into display-oriented numeric records."""
    rendered_rows: list[dict[str, float]] = []
    running_qty = 0.0

    for price_str, qty_str in rows[:limit]:
        price = to_float(price_str)
        qty = to_float(qty_str)
        running_qty += qty
        rendered_rows.append(
            {
                "price": price,
                "qty": qty,
                "cum_qty": running_qty,
                "notional": price * qty,
                "display_qty": running_qty if cumulative else qty,
            }
        )

    return rendered_rows


def pad_rows(rows: list[dict[str, float]], target_len: int) -> list[dict[str, float]]:
    """Pad rows so both book sides render with aligned heights."""
    padded = rows[:]
    while len(padded) < target_len:
        padded.append(
            {"price": 0.0, "qty": 0.0, "cum_qty": 0.0, "notional": 0.0, "display_qty": 0.0}
        )
    return padded


def render_side_column(
    title: str,
    rows: list[dict[str, float]],
    qty_label: str,
    width: int,
) -> list[str]:
    """Render one side of the book as fixed-width text rows."""
    header = f"{title:<8} {'PRICE':>14} {'QTY' if qty_label == 'qty' else 'CUM_QTY':>14} {'NOTIONAL':>14}"
    lines = [header[:width], "-" * min(width, len(header))]

    for row in rows:
        if row["price"] == 0.0 and row["qty"] == 0.0:
            lines.append("")
            continue

        qty_value = row["display_qty"]
        line = (
            f"{title[:1]:<8} "
            f"{format_number(row['price']):>14} "
            f"{format_number(qty_value):>14} "
            f"{format_number(row['notional']):>14}"
        )
        lines.append(line[:width])

    return lines


def render_book(payload: dict[str, Any], symbol: str, show_top: int, cumulative: bool) -> str:
    """Render a full terminal snapshot for the current order book payload."""
    asks = build_side_rows(payload.get("asks", []), show_top, cumulative)
    bids = build_side_rows(payload.get("bids", []), show_top, cumulative)
    row_count = max(len(asks), len(bids), show_top)
    asks = pad_rows(asks, row_count)
    bids = pad_rows(bids, row_count)

    best_ask = asks[0]["price"] if asks and asks[0]["price"] else 0.0
    best_bid = bids[0]["price"] if bids and bids[0]["price"] else 0.0
    spread = best_ask - best_bid if best_ask and best_bid else 0.0
    mid = ((best_ask + best_bid) / 2.0) if best_ask and best_bid else 0.0
    ask_total = sum(row["qty"] for row in asks)
    bid_total = sum(row["qty"] for row in bids)

    terminal_width = max(get_terminal_size((120, 40)).columns, 80)
    column_width = max((terminal_width - 4) // 2, 40)
    qty_label = "cum_qty" if cumulative else "qty"
    ask_lines = render_side_column("ASKS", asks, qty_label, column_width)
    bid_lines = render_side_column("BIDS", bids, qty_label, column_width)

    header_lines = [
        screen_clear(),
        f"BINANCE ORDER BOOK  {symbol.upper()}",
        (
            f"time={utc_now_iso()}  last_update_id={payload.get('lastUpdateId', 'n/a')}  "
            f"best_bid={format_number(best_bid) if best_bid else 'n/a'}  "
            f"best_ask={format_number(best_ask) if best_ask else 'n/a'}"
        ),
        (
            f"spread={format_number(spread) if spread else 'n/a'}  "
            f"mid={format_number(mid) if mid else 'n/a'}  "
            f"bid_total={format_number(bid_total)}  ask_total={format_number(ask_total)}"
        ),
        "",
    ]

    body_lines = []
    for ask_line, bid_line in zip(ask_lines, bid_lines):
        body_lines.append(f"{ask_line:<{column_width}}    {bid_line:<{column_width}}".rstrip())

    footer = [
        "",
        "Ctrl+C to stop",
    ]

    return "\n".join(header_lines + body_lines + footer)


async def persist_message(path: Path, payload: dict[str, Any]) -> None:
    """Append a raw websocket payload to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")) + "\n")


async def stream_orderbook(args: argparse.Namespace) -> None:
    """Connect to Binance, stream updates, and render them to the terminal."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, request_stop)

    async def on_message(payload: dict[str, Any]) -> None:
        if args.output:
            await persist_message(args.output, payload)

        rendered = render_book(payload, args.symbol, args.show_top, args.cumulative)
        if args.view == "live":
            sys.stdout.write(rendered)
            sys.stdout.flush()
        else:
            print(rendered)

    print(
        f"Building local book for {args.symbol.upper()} from snapshot + diff stream (depth={args.levels}, speed={args.speed})",
        file=sys.stderr,
    )
    await stream_local_orderbook(
        symbol=args.symbol,
        base_url=args.base_url,
        snapshot_limit=args.levels,
        speed=args.speed,
        on_message=on_message,
        stop_event=stop_event,
    )


def main() -> int:
    """Run the terminal order book viewer."""
    args = parse_args()
    try:
        asyncio.run(stream_orderbook(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
