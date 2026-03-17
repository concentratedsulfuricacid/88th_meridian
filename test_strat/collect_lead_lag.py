#!/usr/bin/env python3
"""Capture synchronized Roostoo and Binance quotes for lead/lag analysis."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
import time
from contextlib import suppress
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.binance_orderbook_client import DEFAULT_BASE_URL, MAX_SNAPSHOT_LIMIT, VALID_SPEEDS
from test_strat.binance_feed import stream_binance_quotes
from test_strat.roostoo_client import RoostooClient
from test_strat.storage import append_quote
from test_strat.types import QuoteSample


def parse_args() -> argparse.Namespace:
    """Parse CLI options for the lead/lag collector."""
    parser = argparse.ArgumentParser(
        description="Collect Roostoo ticker and Binance order book quotes into one JSONL file."
    )
    parser.add_argument("--pair", default="BTC/USD", help="Roostoo trading pair, e.g. BTC/USD.")
    parser.add_argument(
        "--binance-symbol",
        help="Binance symbol to compare against, e.g. BTCUSDT. Defaults to a /USD -> USDT mapping.",
    )
    parser.add_argument("--roostoo-base-url", default="https://mock-api.roostoo.com")
    parser.add_argument("--binance-base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--levels", type=int, default=20)
    parser.add_argument("--speed", default="100ms", choices=VALID_SPEEDS)
    parser.add_argument("--roostoo-poll-ms", type=int, default=200)
    parser.add_argument("--duration-sec", type=float, help="Optional capture duration.")
    parser.add_argument(
        "--output",
        type=Path,
        help="JSONL output path. Defaults to data/lead_lag/<pair>-<timestamp>.jsonl",
    )
    return parser.parse_args()


def default_binance_symbol(pair: str) -> str:
    """Map a Roostoo pair like BTC/USD to a Binance symbol like BTCUSDT."""
    base, _, quote = pair.partition("/")
    if not base or not quote:
        raise ValueError(f"pair must look like BASE/QUOTE, got {pair!r}")
    quote_symbol = "USDT" if quote.upper() == "USD" else quote.upper()
    return f"{base.upper()}{quote_symbol}"


def default_output_path(pair: str) -> Path:
    """Build a timestamped default output path."""
    pair_slug = pair.lower().replace("/", "_")
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return Path("data") / "lead_lag" / f"{pair_slug}-{timestamp}.jsonl"


async def main() -> int:
    """Run the dual-feed collector until interrupted or the duration expires."""
    args = parse_args()
    if not 1 <= args.levels <= MAX_SNAPSHOT_LIMIT:
        raise ValueError(f"--levels must be between 1 and {MAX_SNAPSHOT_LIMIT}")

    pair = args.pair.upper()
    binance_symbol = args.binance_symbol.upper() if args.binance_symbol else default_binance_symbol(pair)
    output_path = args.output or default_output_path(pair)
    stop_event = asyncio.Event()
    write_lock = asyncio.Lock()
    counts: dict[str, int] = {}
    client = RoostooClient(base_url=args.roostoo_base_url)

    async def write_quote(quote: QuoteSample) -> None:
        async with write_lock:
            counts[quote.source] = counts.get(quote.source, 0) + 1
            await asyncio.to_thread(append_quote, output_path, quote)

    async def poll_roostoo() -> None:
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                quote = await asyncio.to_thread(client.fetch_quote, pair)
                await write_quote(quote)
            except Exception as exc:
                error_quote = QuoteSample(
                    source="roostoo_error",
                    pair=pair,
                    event_ts_ms=int(time.time_ns() // 1_000_000),
                    recv_ts_ms=int(time.time_ns() // 1_000_000),
                    bid=0.0,
                    ask=0.0,
                    mid=0.0,
                    spread=0.0,
                    meta={"error": f"{type(exc).__name__}: {exc}"},
                )
                await write_quote(error_quote)

            elapsed_ms = (time.monotonic() - started) * 1000.0
            sleep_ms = max(args.roostoo_poll_ms - elapsed_ms, 0.0)
            await asyncio.sleep(sleep_ms / 1000.0)

    async def on_binance_quote(quote: QuoteSample) -> None:
        await write_quote(quote)

    async def run_binance_stream() -> None:
        try:
            await stream_binance_quotes(
                pair=pair,
                symbol=binance_symbol,
                base_url=args.binance_base_url,
                snapshot_limit=args.levels,
                speed=args.speed,
                on_quote=on_binance_quote,
                stop_event=stop_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_quote = QuoteSample(
                source="binance_error",
                pair=pair,
                event_ts_ms=int(time.time_ns() // 1_000_000),
                recv_ts_ms=int(time.time_ns() // 1_000_000),
                bid=0.0,
                ask=0.0,
                mid=0.0,
                spread=0.0,
                meta={"error": f"{type(exc).__name__}: {exc}"},
            )
            await write_quote(error_quote)
            print(f"Binance stream failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            stop_event.set()

    tasks = [
        asyncio.create_task(
            run_binance_stream(),
            name="binance-stream",
        ),
        asyncio.create_task(poll_roostoo(), name="roostoo-poll"),
    ]

    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, request_stop)

    if args.duration_sec:
        async def stop_later() -> None:
            await asyncio.sleep(args.duration_sec)
            stop_event.set()

        tasks.append(asyncio.create_task(stop_later(), name="stop-later"))

    try:
        await stop_event.wait()
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        with suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)

    print(f"Wrote lead/lag capture to {output_path}")
    print(f"Quote counts: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
