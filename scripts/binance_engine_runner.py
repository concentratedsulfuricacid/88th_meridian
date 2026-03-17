#!/usr/bin/env python3
"""Run the confluence trading engine and paper trader on the live Binance local order book feed."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import EXIT_POLICIES, EngineConfig, LiveEngineProcessor, PaperTrader, PaperTraderConfig, SimpleTradingEngine
from scripts.binance_orderbook_client import DEFAULT_BASE_URL, MAX_SNAPSHOT_LIMIT, VALID_SPEEDS, stream_local_orderbook


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the live engine runner."""
    parser = argparse.ArgumentParser(
        description="Feed the live Binance local order book into the confluence engine and paper trader."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--levels", type=int, default=300)
    parser.add_argument("--speed", default="100ms", choices=VALID_SPEEDS)
    parser.add_argument("--imbalance-depth", type=int, default=5)
    parser.add_argument("--liquidity-depth", type=int, default=10)
    parser.add_argument("--mlofi-levels", type=int, default=5)
    parser.add_argument("--vwap-depth", type=int, default=5)
    parser.add_argument("--normalization-window", type=int, default=100)
    parser.add_argument("--volatility-window", type=int, default=50)
    parser.add_argument("--price-response-lookback", type=int, default=8)
    parser.add_argument("--breakout-lookback", type=int, default=20)
    parser.add_argument("--pullback-lookback", type=int, default=8)
    parser.add_argument("--ofi-zscore-threshold", type=float, default=0.75)
    parser.add_argument("--imbalance-threshold", type=float, default=0.15)
    parser.add_argument("--max-spread-bps", type=float, default=2.5)
    parser.add_argument("--max-volatility-zscore", type=float, default=1.75)
    parser.add_argument("--min-depth-total-qty", type=float, default=1.0)
    parser.add_argument("--min-depth-total-notional", type=float, default=100000.0)
    parser.add_argument("--min-price-response", type=float, default=0.0)
    parser.add_argument("--min-entry-confluence-score", type=int, default=4)
    parser.add_argument("--min-exit-confluence-score", type=int, default=3)
    parser.add_argument("--disable-timing-confirmation", action="store_true")
    parser.add_argument("--exit-policy", choices=tuple(EXIT_POLICIES.keys()), default="score_fade_with_profit_floor")
    parser.add_argument("--max-holding-seconds", type=float, default=5.0)
    parser.add_argument("--min-profit-buffer-bps", type=float, default=0.0)
    parser.add_argument("--max-profit-floor-wait-seconds", type=float, default=5.0)
    parser.add_argument("--exit-ofi-zscore-threshold", type=float, default=-0.1)
    parser.add_argument("--exit-imbalance-threshold", type=float, default=0.0)
    parser.add_argument("--position-size", type=float, default=0.001)
    parser.add_argument("--entry-order-type", choices=("market", "limit"), default="market")
    parser.add_argument("--exit-order-type", choices=("market", "limit"), default="market")
    parser.add_argument("--maker-fee-rate", type=float, default=0.0005)
    parser.add_argument("--taker-fee-rate", type=float, default=0.001)
    parser.add_argument("--initial-cash", type=float, default=10000.0)
    parser.add_argument("--jsonl-output", type=Path, help="Optional path to persist feature, signal, and PnL updates.")
    parser.add_argument("--only-changes", action="store_true", help="Only print updates when the engine action changes.")
    return parser.parse_args()


def utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def serialize_update(update) -> dict[str, object]:
    """Convert an engine update into a JSON-serializable record."""
    portfolio = update.portfolio
    return {
        "ts": update.features.ts,
        "emitted_at": utc_now_iso(),
        "last_update_id": update.payload.get("lastUpdateId"),
        "market_state": update.features.market_state,
        "best_bid": update.features.best_bid,
        "best_ask": update.features.best_ask,
        "mid_price": update.features.mid_price,
        "microprice": update.features.microprice,
        "spread": update.features.spread,
        "spread_bps": update.features.spread_bps,
        "l1_imbalance": update.features.l1_imbalance,
        "top_n_imbalance": update.features.top_n_imbalance,
        "weighted_top_n_imbalance": update.features.weighted_top_n_imbalance,
        "ofi": update.features.ofi,
        "ofi_zscore": update.features.ofi_zscore,
        "mlofi": list(update.features.mlofi),
        "volatility": update.features.volatility,
        "volatility_zscore": update.features.volatility_zscore,
        "depth_total_qty": update.features.depth_total_qty,
        "depth_total_notional": update.features.depth_total_notional,
        "price_response": update.features.price_response,
        "breakout_long": update.features.breakout_long,
        "breakout_short": update.features.breakout_short,
        "retest_long": update.features.retest_long,
        "retest_short": update.features.retest_short,
        "pullback_long": update.features.pullback_long,
        "pullback_short": update.features.pullback_short,
        "vwap_buy_to_mid": update.features.vwap_buy_to_mid,
        "vwap_sell_to_mid": update.features.vwap_sell_to_mid,
        "long_score": update.features.long_score,
        "short_score": update.features.short_score,
        "quality_pass": update.features.quality_pass,
        "long_ready": update.features.long_ready,
        "short_ready": update.features.short_ready,
        "action": update.signal.action,
        "target_position": update.signal.target_position,
        "reason": update.signal.reason,
        "trades": [
            {
                "side": trade.side,
                "action": trade.action,
                "order_type": trade.order_type,
                "qty": trade.qty,
                "price": trade.price,
                "fee": trade.fee,
                "realized_pnl": trade.realized_pnl,
            }
            for trade in update.trades
        ],
        "portfolio": None
        if portfolio is None
        else {
            "cash": portfolio.cash,
            "position_qty": portfolio.position_qty,
            "avg_entry_price": portfolio.avg_entry_price,
            "realized_pnl": portfolio.realized_pnl,
            "unrealized_pnl": portfolio.unrealized_pnl,
            "total_equity": portfolio.total_equity,
            "trade_count": portfolio.trade_count,
        },
    }


async def persist_record(path: Path, record: dict[str, object]) -> None:
    """Append one engine update record to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


async def run_engine(args: argparse.Namespace) -> None:
    """Stream the local order book, generate signals, and paper trade them."""
    args.levels = max(1, min(args.levels, MAX_SNAPSHOT_LIMIT))
    entry_fill_reference = "mid" if args.entry_order_type == "limit" else "ask"
    exit_fill_reference = "mid" if args.exit_order_type == "limit" else "bid"
    entry_fee_rate = args.maker_fee_rate if args.entry_order_type == "limit" else args.taker_fee_rate
    exit_fee_rate = args.maker_fee_rate if args.exit_order_type == "limit" else args.taker_fee_rate
    engine_config = EngineConfig(
        imbalance_depth=args.imbalance_depth,
        liquidity_depth=args.liquidity_depth,
        mlofi_levels=args.mlofi_levels,
        vwap_depth=args.vwap_depth,
        normalization_window=args.normalization_window,
        volatility_window=args.volatility_window,
        price_response_lookback=args.price_response_lookback,
        breakout_lookback=args.breakout_lookback,
        pullback_lookback=args.pullback_lookback,
        ofi_zscore_threshold=args.ofi_zscore_threshold,
        imbalance_threshold=args.imbalance_threshold,
        max_spread_bps=args.max_spread_bps,
        max_volatility_zscore=args.max_volatility_zscore,
        min_depth_total_qty=args.min_depth_total_qty,
        min_depth_total_notional=args.min_depth_total_notional,
        min_price_response=args.min_price_response,
        min_entry_confluence_score=args.min_entry_confluence_score,
        min_exit_confluence_score=args.min_exit_confluence_score,
        require_timing_confirmation=not args.disable_timing_confirmation,
        exit_policy=args.exit_policy,
        max_holding_seconds=args.max_holding_seconds,
        exit_ofi_zscore_threshold=args.exit_ofi_zscore_threshold,
        exit_imbalance_threshold=args.exit_imbalance_threshold,
        min_profit_buffer_bps=args.min_profit_buffer_bps,
        max_profit_floor_wait_seconds=args.max_profit_floor_wait_seconds,
        entry_fee_rate=entry_fee_rate,
        exit_fee_rate=exit_fee_rate,
        entry_fill_reference=entry_fill_reference,
        exit_fill_reference=exit_fill_reference,
    )
    paper_config = PaperTraderConfig(
        position_size=args.position_size,
        entry_order_type=args.entry_order_type,
        exit_order_type=args.exit_order_type,
        maker_fee_rate=args.maker_fee_rate,
        taker_fee_rate=args.taker_fee_rate,
        initial_cash=args.initial_cash,
    )
    processor = LiveEngineProcessor(
        engine=SimpleTradingEngine(engine_config),
        paper_trader=PaperTrader(paper_config),
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_action: tuple[str, int] | None = None

    def request_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, request_stop)

    async def on_message(payload: dict[str, object]) -> None:
        nonlocal previous_action
        update = processor.on_payload(payload, ts=datetime.now(timezone.utc).timestamp())
        action_key = (update.signal.action, update.signal.target_position)
        if args.only_changes and action_key == previous_action:
            return
        previous_action = action_key

        portfolio = update.portfolio
        line = (
            f"{utc_now_iso()} symbol={args.symbol.upper()} update={payload.get('lastUpdateId')} "
            f"state={update.features.market_state} long_score={update.features.long_score} short_score={update.features.short_score} "
            f"spread_bps={update.features.spread_bps:.3f} ofi_z={update.features.ofi_zscore:.3f} "
            f"imb={update.features.weighted_top_n_imbalance:.3f} vol_z={update.features.volatility_zscore:.3f} "
            f"action={update.signal.action} target={update.signal.target_position}"
        )
        print(line)
        if portfolio is not None:
            print(
                f"portfolio cash={portfolio.cash:.2f} pos={portfolio.position_qty:.6f} avg={portfolio.avg_entry_price:.2f} "
                f"realized={portfolio.realized_pnl:.4f} unrealized={portfolio.unrealized_pnl:.4f} equity={portfolio.total_equity:.2f}"
            )
        print(f"reason={update.signal.reason}")
        for trade in update.trades:
            print(
                f"trade action={trade.action} side={trade.side} type={trade.order_type} qty={trade.qty:.6f} "
                f"price={trade.price:.2f} fee={trade.fee:.4f} realized={trade.realized_pnl:.4f}"
            )

        if args.jsonl_output:
            await persist_record(args.jsonl_output, serialize_update(update))

    print(
        (
            f"Starting live paper trader for {args.symbol.upper()} depth={args.levels} speed={args.speed} "
            f"entry_type={args.entry_order_type} exit_type={args.exit_order_type} "
            f"fees(limit={args.maker_fee_rate:.4%}, market={args.taker_fee_rate:.4%})"
        ),
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
    """Run the live paper-trading entrypoint."""
    args = parse_args()
    try:
        asyncio.run(run_engine(args))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
