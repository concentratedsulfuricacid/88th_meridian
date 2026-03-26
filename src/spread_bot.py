"""Spread capture bot — live on Roostoo competition account.

Strategy: limit BUY at bid, limit SELL at ask.
Nets spread_bps - 10bps maker fee per cycle.

State machine (persisted to JSON so restarts are safe):
  FLAT → place BUY → BUYING → fill detected → place SELL →
  IN_POSITION → fill or time-stop → FLAT

Usage
-----
  python -m src.spread_bot --competition --size-usd 300000 --forever
  python -m src.spread_bot --competition --size-usd 300000 --forever --fill-timeout 1800
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from src.live_config import build_live_config
from src.roostoo_client import RoostooClient

FEE_BPS    = 10.0   # round-trip maker fee (0.05% each side)
POLL_S     = 0.5    # balance poll interval
STATE_DIR  = Path("src/state")


# ── helpers ───────────────────────────────────────────────────────────────────

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


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {"status": "FLAT", "cum_net_bps": 0.0, "trade_num": 0}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, indent=2))


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Spread cycling bot.")
    ap.add_argument("--symbol",       default="PEPEUSDT")
    ap.add_argument("--size-usd",     type=float, default=None,
                    help="USD per cycle. Omit or pass 'full' to use entire free balance.")
    ap.add_argument("--minutes",      type=float, default=480.0)
    ap.add_argument("--forever",      action="store_true",
                    help="Run indefinitely (ignores --minutes)")
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
    acct        = "COMPETITION" if args.competition else "TEST"
    min_cycle_s = 60.0 / args.max_tpm

    # state file — one per symbol+account so multiple bots don't collide
    state_path = STATE_DIR / f"spread_state_{pair.replace('/','_')}_{acct}.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # log file — single persistent file per symbol+account (append)
    log_dir  = Path("src/state/spread_logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path  = Path(args.log) if args.log else log_dir / f"{pair.replace('/','_')}_{acct}.csv"
    log_exists = log_path.exists()
    log_f  = open(log_path, "a", newline="", buffering=1)
    writer = csv.DictWriter(log_f, fieldnames=[
        "timestamp", "pair", "bid", "ask", "spread_bps",
        "buy_price", "sell_price", "gross_bps", "net_bps",
        "hold_s", "cum_net_bps", "roi_pct", "trade_num", "exit_reason",
    ])
    if not log_exists:
        writer.writeheader()

    client   = _client(args.competition)
    state    = load_state(state_path)
    deadline = float("inf") if args.forever else time.time() + args.minutes * 60
    recent_ts: deque = deque()

    print(f"[{acct}] Spread capture  {pair}  size=${args.size_usd:,.0f}", flush=True)
    print(f"fill_timeout={args.fill_timeout:.0f}s  size={'full' if args.size_usd is None else f'${args.size_usd:,.0f}'}  fee=10bps", flush=True)
    print(f"State → {state_path}", flush=True)
    print(f"Log   → {log_path}", flush=True)
    print(f"Resuming from state: {state['status']}\n", flush=True)

    while time.time() < deadline:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        status  = state["status"]

        # ── FLAT: place a new buy ──────────────────────────────────────────
        if status == "FLAT":
            now_ts    = time.time()
            recent_ts = deque(t for t in recent_ts if now_ts - t < 60.0)
            if len(recent_ts) >= args.max_tpm:
                wait = 60.0 - (now_ts - recent_ts[0])
                if wait > 0:
                    time.sleep(wait)
                    continue

            bid, ask = get_bid_ask(client, pair)
            if bid is None or bid <= 0:
                time.sleep(1)
                continue

            spread_bps = (ask / bid - 1.0) * 10_000
            print(
                f"{now_str}  {pair}  bid={bid:.8f}  ask={ask:.8f}  "
                f"spread={spread_bps:.1f}bps  cum={state['cum_net_bps']:+.1f}bps",
                flush=True,
            )

            coin_before = get_balance(client, coin)
            usd_before  = get_balance(client, "USD")
            if args.size_usd is not None:
                size_usd = args.size_usd
                if usd_before < size_usd:
                    print(f"  [SKIP] insufficient USD ({usd_before:.2f} < {size_usd:.2f})", flush=True)
                    time.sleep(5)
                    continue
            else:
                size_usd = usd_before * 0.90  # 90% of free balance
                if size_usd < 1.0:
                    print(f"  [SKIP] insufficient USD ({usd_before:.2f})", flush=True)
                    time.sleep(5)
                    continue

            qty = (int(size_usd / bid) // 10) * 10  # round down to step size 10
            if qty <= 0:
                time.sleep(1)
                continue

            resp = client.place_limit_order(symbol=pair, side="BUY", quantity=qty, price=bid)
            if not resp.get("Success"):
                print(f"  [BUY ] rejected: {resp.get('ErrMsg')}", flush=True)
                time.sleep(2)
                continue

            buy_id = resp.get("OrderDetail", {}).get("OrderID", "")
            print(f"  [BUY ] id={buy_id}  qty={qty:,}  px={bid:.8f} — waiting for fill...", flush=True)

            state.update({
                "status":        "BUYING",
                "buy_order_id":  str(buy_id),
                "buy_price":     bid,
                "qty":           qty,
                "coin_before":   coin_before,
                "usd_before":    usd_before,
                "t_buy":         time.time(),
                "sell_order_id": None,
                "sell_price":    None,
            })
            save_state(state_path, state)

        # ── BUYING: poll until buy fills ───────────────────────────────────
        elif status == "BUYING":
            coin_now    = get_balance(client, coin)
            coin_before = float(state.get("coin_before", 0))
            bid         = float(state["buy_price"])
            qty         = int(state["qty"])
            t_buy       = float(state["t_buy"])
            buy_id      = state["buy_order_id"]

            if coin_now >= coin_before + qty * 0.99:
                filled_qty = (int(coin_now - coin_before) // 10) * 10
                print(f"  [BUY ] filled  qty={filled_qty:,}  px={bid:.8f}", flush=True)

                _, ask_now = get_bid_ask(client, pair)
                sell_px    = ask_now if (ask_now and ask_now > bid) else bid * 1.0029

                resp2 = client.place_limit_order(symbol=pair, side="SELL", quantity=filled_qty, price=sell_px)
                if not resp2.get("Success"):
                    print(f"  [SELL] rejected: {resp2.get('ErrMsg')}", flush=True)
                    time.sleep(2)
                    continue

                sell_id = resp2.get("OrderDetail", {}).get("OrderID", "")
                print(f"  [SELL] id={sell_id}  qty={filled_qty:,}  px={sell_px:.8f} — waiting for fill...", flush=True)

                state.update({
                    "status":        "IN_POSITION",
                    "qty":           filled_qty,
                    "sell_order_id": str(sell_id),
                    "sell_price":    sell_px,
                    "t_buy":         t_buy,
                })
                save_state(state_path, state)

            elif time.time() - t_buy > args.fill_timeout:
                print(f"  [BUY ] fill timeout — cancelling", flush=True)
                try:
                    client.cancel_order(buy_id)
                except Exception:
                    pass
                state.update({"status": "FLAT", "buy_order_id": None})
                save_state(state_path, state)

            else:
                elapsed = time.time() - t_buy
                print(f"  [BUY ] waiting...  {elapsed:.0f}s / {args.fill_timeout:.0f}s", flush=True)
                time.sleep(POLL_S)

        # ── IN_POSITION: poll for sell fill, SL, or time-stop ─────────────
        elif status == "IN_POSITION":
            # Use total balance (free+lock) so locked sell orders don't falsely trigger fill detection
            _wallet    = RoostooClient.wallet_from_balances(client.get_balances())
            _v         = _wallet.get(coin, {})
            coin_now   = _v.get("free", 0.0) + _v.get("lock", 0.0)
            bid        = float(state["buy_price"])
            sell_px    = float(state["sell_price"])
            qty        = int(state["qty"])
            t_buy      = float(state["t_buy"])
            sell_id    = state["sell_order_id"]
            hold_s = time.time() - t_buy

            current_bid, _ = get_bid_ask(client, pair)

            if coin_now < qty * 0.01:  # coin balance dropped → sell filled
                # sell filled
                gross_bps = (sell_px / bid - 1.0) * 10_000
                net_bps   = gross_bps - FEE_BPS
                state["cum_net_bps"] += net_bps
                state["trade_num"]   += 1
                roi_pct = state["cum_net_bps"] / 10_000 * 100
                sign    = "✓" if net_bps > 0 else "✗"
                print(
                    f"  [{sign}] gross={gross_bps:+.1f}bps  net={net_bps:+.1f}bps  "
                    f"hold={hold_s:.1f}s  cum={state['cum_net_bps']:+.1f}bps  "
                    f"ROI={roi_pct:+.4f}%  trades={state['trade_num']}",
                    flush=True,
                )
                writer.writerow({
                    "timestamp":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "pair":        pair,
                    "bid":         round(bid, 8),
                    "ask":         round(sell_px, 8),
                    "spread_bps":  round((sell_px / bid - 1) * 10_000, 2),
                    "buy_price":   round(bid, 8),
                    "sell_price":  round(sell_px, 8),
                    "gross_bps":   round(gross_bps, 2),
                    "net_bps":     round(net_bps, 2),
                    "hold_s":      round(hold_s, 1),
                    "cum_net_bps": round(state["cum_net_bps"], 2),
                    "roi_pct":     round(roi_pct, 6),
                    "trade_num":   state["trade_num"],
                    "exit_reason": "fill",
                })
                recent_ts.append(time.time())
                time.sleep(1)  # wait for USD to be credited before next cycle
                state.update({"status": "FLAT", "sell_order_id": None, "sell_price": None})
                save_state(state_path, state)

            elif hold_s > args.fill_timeout:
                reason = "timeout"
                print(
                    f"  [EXIT] {reason}  hold={hold_s:.0f}s  current={current_bid:.8f}",
                    flush=True,
                )
                try:
                    client.cancel_order(sell_id)
                except Exception:
                    pass
                coin_now = int(coin_now)
                exit_px  = current_bid or bid
                if coin_now > 0:
                    try:
                        client.place_market_order(symbol=pair, side="SELL", quantity=coin_now)
                        print(f"  [EXIT] market sold {coin_now:,} {coin}", flush=True)
                    except Exception as e:
                        print(f"  [EXIT] market sell error: {e}", flush=True)

                gross_bps = (exit_px / bid - 1.0) * 10_000
                net_bps   = gross_bps - FEE_BPS
                state["cum_net_bps"] += net_bps
                state["trade_num"]   += 1
                roi_pct = state["cum_net_bps"] / 10_000 * 100
                print(
                    f"  [✗] gross={gross_bps:+.1f}bps  net={net_bps:+.1f}bps  "
                    f"cum={state['cum_net_bps']:+.1f}bps  trades={state['trade_num']}",
                    flush=True,
                )
                writer.writerow({
                    "timestamp":   datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "pair":        pair,
                    "bid":         round(bid, 8),
                    "ask":         round(sell_px, 8),
                    "spread_bps":  round((sell_px / bid - 1) * 10_000, 2),
                    "buy_price":   round(bid, 8),
                    "sell_price":  round(exit_px, 8),
                    "gross_bps":   round(gross_bps, 2),
                    "net_bps":     round(net_bps, 2),
                    "hold_s":      round(hold_s, 1),
                    "cum_net_bps": round(state["cum_net_bps"], 2),
                    "roi_pct":     round(roi_pct, 6),
                    "trade_num":   state["trade_num"],
                    "exit_reason": reason,
                })
                state.update({"status": "FLAT", "sell_order_id": None, "sell_price": None})
                save_state(state_path, state)

            else:
                print(
                    f"  [SELL] waiting...  hold={hold_s:.0f}s / {args.fill_timeout:.0f}s  "
                    f"current={current_bid:.8f}",
                    flush=True,
                )
                time.sleep(POLL_S)

    log_f.close()
    print(f"\n{'='*60}", flush=True)
    final_roi = state["cum_net_bps"] / 10_000 * 100
    print(
        f"DONE  trades={state['trade_num']}  "
        f"cum_net={state['cum_net_bps']:+.1f}bps  ROI={final_roi:+.4f}%",
        flush=True,
    )
    print(f"Log → {log_path}", flush=True)


if __name__ == "__main__":
    main()
