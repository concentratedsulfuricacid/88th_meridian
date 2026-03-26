"""Spread capture bot — live on Roostoo competition account.

Strategy: limit BUY at bid, limit SELL at ask.
Nets spread_bps - 10bps maker fee per cycle.

Fill detection: polls balance change (more reliable than order status API).

Usage
-----
  python -m src.spread_bot --competition --size-usd 300000 --minutes 480
  python -m src.spread_bot --competition --size-usd 300000 --minutes 480 --fill-timeout 1800
"""
from __future__ import annotations

import argparse
import csv
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from src.live_config import build_live_config
from src.roostoo_client import RoostooClient

FEE_BPS = 10.0  # round-trip maker fee (0.05% each side)


def _client(competition: bool) -> RoostooClient:
    return RoostooClient(build_live_config(competition=competition))


def get_balance(client: RoostooClient, asset: str) -> float:
    try:
        return RoostooClient.wallet_from_balances(
            client.get_balances()
        ).get(asset, {}).get("free", 0.0)
    except Exception:
        return 0.0


def get_bid_ask(client: RoostooClient, pair: str) -> tuple[float, float] | tuple[None, None]:
    try:
        d = client.get_ticker()["Data"][pair]
        return float(d["MaxBid"]), float(d["MinAsk"])
    except Exception:
        return None, None


def wait_for_balance_change(
    client: RoostooClient,
    asset: str,
    before: float,
    direction: str,
    timeout_s: float,
) -> float | None:
    """Poll until asset free balance changes. Returns new balance or None on timeout."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        current = get_balance(client, asset)
        if direction == "up"   and current > before + 0.001:
            return current
        if direction == "down" and current < before - 0.001:
            return current
        time.sleep(0.25)
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Spread cycling bot.")
    ap.add_argument("--symbol",       default="PEPEUSDT")
    ap.add_argument("--size-usd",     type=float, default=300_000.0)
    ap.add_argument("--minutes",      type=float, default=480.0)
    ap.add_argument("--max-tpm",      type=float, default=15.0,
                    help="Max trades per minute (rate limit)")
    ap.add_argument("--fill-timeout", type=float, default=1800.0,
                    help="Seconds to wait for fill before time-stop (default 30min)")
    ap.add_argument("--log",          default=None)
    ap.add_argument("--competition",  action="store_true",
                    help="Use competition account credentials")
    args = ap.parse_args()

    sym  = args.symbol.upper()
    pair = (sym[:-4] + "/USD") if sym.endswith("USDT") else (sym if "/" in sym else sym + "/USD")
    coin = pair.split("/")[0]
    min_cycle_s = 60.0 / args.max_tpm

    acct     = "COMPETITION" if args.competition else "TEST"
    ts_str   = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir  = Path("src/state/spread_logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log) if args.log else log_dir / f"{pair.replace('/','_')}_{acct}_{ts_str}.csv"

    log_f  = open(log_path, "w", newline="", buffering=1)
    writer = csv.DictWriter(log_f, fieldnames=[
        "timestamp", "pair", "bid", "ask", "spread_bps",
        "buy_price", "sell_price", "gross_bps", "net_bps",
        "hold_s", "cum_net_bps", "roi_pct", "trade_num",
    ])
    writer.writeheader()

    client    = _client(args.competition)
    cum_net   = 0.0
    trade_num = 0
    recent_ts: deque = deque()
    deadline  = time.time() + args.minutes * 60

    print(f"[{acct}] Spread capture  {pair}  size=${args.size_usd:,.0f}", flush=True)
    print(f"fill_timeout={args.fill_timeout:.0f}s  max_tpm={args.max_tpm}  fee=10bps", flush=True)
    print(f"Log → {log_path}\n", flush=True)

    while time.time() < deadline:
        cycle_start = time.time()

        # Rate limit
        now_ts    = time.time()
        recent_ts = deque(t for t in recent_ts if now_ts - t < 60.0)
        if len(recent_ts) >= args.max_tpm:
            wait = 60.0 - (now_ts - recent_ts[0])
            if wait > 0:
                print(f"  [RATE] {len(recent_ts)}/{int(args.max_tpm)} tpm — waiting {wait:.1f}s", flush=True)
                time.sleep(wait)
                continue

        bid, ask = get_bid_ask(client, pair)
        if bid is None or bid <= 0:
            time.sleep(1)
            continue

        spread_bps = (ask / bid - 1.0) * 10_000
        now_str    = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(
            f"{now_str}  {pair}  bid={bid:.8f}  ask={ask:.8f}  "
            f"spread={spread_bps:.1f}bps  cum={cum_net:+.1f}bps",
            flush=True,
        )

        qty     = int(args.size_usd / bid)
        if qty <= 0:
            time.sleep(1)
            continue

        usd_before  = get_balance(client, "USD")
        coin_before = get_balance(client, coin)

        # ── BUY at bid ────────────────────────────────────────────────────
        resp = client.place_limit_order(symbol=pair, side="BUY", quantity=qty, price=bid)
        if not resp.get("Success"):
            print(f"  [BUY ] rejected: {resp.get('ErrMsg')}", flush=True)
            time.sleep(2)
            continue
        buy_id = resp.get("OrderDetail", {}).get("OrderID", "")
        print(f"  [BUY ] id={buy_id}  qty={qty:,}  px={bid:.8f} — waiting for fill...", flush=True)

        t_buy      = time.time()
        coin_after = wait_for_balance_change(client, coin, coin_before, "up", args.fill_timeout)
        if coin_after is None:
            print(f"  [BUY ] fill timeout — cancelling", flush=True)
            try:
                client.cancel_order(str(buy_id))
            except Exception:
                pass
            time.sleep(max(0, min_cycle_s - (time.time() - cycle_start)))
            continue

        filled_qty = int(coin_after - coin_before)
        print(f"  [BUY ] filled  qty={filled_qty:,}  est_px={bid:.8f}", flush=True)

        # ── SELL at ask ───────────────────────────────────────────────────
        _, ask_now = get_bid_ask(client, pair)
        sell_px    = ask_now if ask_now else ask

        resp2 = client.place_limit_order(symbol=pair, side="SELL", quantity=filled_qty, price=sell_px)
        if not resp2.get("Success"):
            print(f"  [SELL] rejected: {resp2.get('ErrMsg')}", flush=True)
            time.sleep(2)
            continue
        sell_id = resp2.get("OrderDetail", {}).get("OrderID", "")
        print(f"  [SELL] id={sell_id}  qty={filled_qty:,}  px={sell_px:.8f} — waiting for fill...", flush=True)

        usd_after_sell = wait_for_balance_change(client, "USD", usd_before, "up", args.fill_timeout)
        if usd_after_sell is None:
            print(f"  [SELL] fill timeout — cancelling and market selling", flush=True)
            try:
                client.cancel_order(str(sell_id))
            except Exception:
                pass
            coin_now = int(get_balance(client, coin))
            if coin_now > 0:
                try:
                    client.place_market_order(symbol=pair, side="SELL", quantity=coin_now)
                    print(f"  [EXIT] market sold {coin_now:,} {coin}", flush=True)
                except Exception as e:
                    print(f"  [EXIT] market sell error: {e}", flush=True)
            time.sleep(max(0, min_cycle_s - (time.time() - cycle_start)))
            continue

        # ── Log P&L ───────────────────────────────────────────────────────
        hold_s    = time.time() - t_buy
        gross_bps = (sell_px / bid - 1.0) * 10_000
        net_bps   = gross_bps - FEE_BPS
        cum_net  += net_bps
        roi_pct   = cum_net / 10_000 * 100
        trade_num += 1
        recent_ts.append(time.time())

        sign = "✓" if net_bps > 0 else "✗"
        print(
            f"  [{sign}] gross={gross_bps:+.1f}bps  net={net_bps:+.1f}bps  "
            f"hold={hold_s:.1f}s  cum={cum_net:+.1f}bps  ROI={roi_pct:+.4f}%  trades={trade_num}",
            flush=True,
        )

        writer.writerow({
            "timestamp":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pair":        pair,
            "bid":         round(bid, 8),
            "ask":         round(ask, 8),
            "spread_bps":  round(spread_bps, 2),
            "buy_price":   round(bid, 8),
            "sell_price":  round(sell_px, 8),
            "gross_bps":   round(gross_bps, 2),
            "net_bps":     round(net_bps, 2),
            "hold_s":      round(hold_s, 1),
            "cum_net_bps": round(cum_net, 2),
            "roi_pct":     round(roi_pct, 6),
            "trade_num":   trade_num,
        })

        elapsed = time.time() - cycle_start
        if elapsed < min_cycle_s:
            time.sleep(min_cycle_s - elapsed)

    log_f.close()
    print(f"\n{'='*60}", flush=True)
    final_roi = cum_net / 10_000 * 100
    print(f"DONE  trades={trade_num}  cum_net={cum_net:+.1f}bps  ROI={final_roi:+.4f}%", flush=True)
    print(f"Log → {log_path}", flush=True)


if __name__ == "__main__":
    main()
