"""88th Meridian — local monitoring dashboard.

Queries Roostoo directly for live balances, prices, and open orders.
Reads local trades.jsonl for trade history.

Usage:
  python -m src.dashboard [--port 8080] [--competition]

Then open http://localhost:8080 in your browser.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import requests
from flask import Flask, Response, jsonify

from .live_bot import BREAKOUT_SYMBOLS, N_BARS, BotState, load_state
from .live_config import build_live_config
from .roostoo_client import RoostooClient

app = Flask(__name__)
_live_config = None

LIMIT_FEE_RATE = 0.0005
ROUND_TRIP_FEE_BPS = LIMIT_FEE_RATE * 2.0 * 10_000.0
ORDER_RECORDS_DIR = Path("order_records")


def _client() -> RoostooClient:
    return RoostooClient(_live_config)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _safe_load_state() -> BotState:
    try:
        return load_state(_live_config.state_path)
    except Exception:
        return BotState()


def _estimate_live_pnl(entry_price: float, mark_price: float, qty: float) -> dict:
    gross_pnl_usd = (mark_price - entry_price) * qty
    net_pnl_usd = (mark_price * qty * (1.0 - LIMIT_FEE_RATE)) - (entry_price * qty * (1.0 + LIMIT_FEE_RATE))
    fee_drag_usd = gross_pnl_usd - net_pnl_usd
    notional = entry_price * qty
    gross_pnl_bps = (gross_pnl_usd / notional) * 10_000.0 if notional > 0.0 else None
    net_pnl_bps = (net_pnl_usd / notional) * 10_000.0 if notional > 0.0 else None
    return {
        "gross_pnl_usd": round(gross_pnl_usd, 2),
        "net_pnl_usd": round(net_pnl_usd, 2),
        "fee_drag_usd": round(fee_drag_usd, 2),
        "gross_pnl_bps": round(gross_pnl_bps, 1) if gross_pnl_bps is not None else None,
        "net_pnl_bps": round(net_pnl_bps, 1) if net_pnl_bps is not None else None,
    }


def _compute_live_pnl(
    wallet: dict[str, dict[str, float]],
    prices: dict[str, float],
    state: BotState,
) -> tuple[dict[str, dict], dict]:
    pnl_by_asset: dict[str, dict] = {}
    summary = {
        "open_positions": 0,
        "gross_pnl_usd": 0.0,
        "net_pnl_usd": 0.0,
        "fee_drag_usd": 0.0,
        "gross_pnl_bps": None,
        "net_pnl_bps": None,
        "tracked_notional_usd": 0.0,
        "fee_assumption_bps_round_trip": ROUND_TRIP_FEE_BPS,
    }

    for symbol, sym_state in state.symbols.items():
        if not sym_state.active or sym_state.qty <= 0.0 or sym_state.entry_price <= 0.0:
            continue
        asset = symbol.replace("USDT", "")
        held_qty = float(wallet.get(asset, {}).get("free", 0.0)) + float(wallet.get(asset, {}).get("lock", 0.0))
        live_qty = min(held_qty, sym_state.qty)
        mark_price = prices.get(f"{asset}/USD", 0.0)
        if live_qty <= 0.0 or mark_price <= 0.0:
            continue

        pnl = _estimate_live_pnl(sym_state.entry_price, mark_price, live_qty)
        pnl_by_asset[asset] = {
            "symbol": symbol,
            "entry_price": round(sym_state.entry_price, 8),
            "tracked_qty": round(live_qty, 6),
            **pnl,
        }
        summary["open_positions"] += 1
        summary["gross_pnl_usd"] += pnl["gross_pnl_usd"]
        summary["net_pnl_usd"] += pnl["net_pnl_usd"]
        summary["fee_drag_usd"] += pnl["fee_drag_usd"]
        summary["tracked_notional_usd"] += sym_state.entry_price * live_qty

    tracked_notional = summary["tracked_notional_usd"]
    if tracked_notional > 0.0:
        summary["gross_pnl_bps"] = round((summary["gross_pnl_usd"] / tracked_notional) * 10_000.0, 1)
        summary["net_pnl_bps"] = round((summary["net_pnl_usd"] / tracked_notional) * 10_000.0, 1)

    summary["gross_pnl_usd"] = round(summary["gross_pnl_usd"], 2)
    summary["net_pnl_usd"] = round(summary["net_pnl_usd"], 2)
    summary["fee_drag_usd"] = round(summary["fee_drag_usd"], 2)
    summary["tracked_notional_usd"] = round(summary["tracked_notional_usd"], 2)
    return pnl_by_asset, summary


def _get_portfolio() -> dict:
    client = _client()
    try:
        raw_balances = client.get_balances()
        wallet = client.wallet_from_balances(raw_balances)
    except Exception as e:
        return {"error": str(e)}

    try:
        ticker = client.get_ticker()
        prices_raw = ticker.get("Data", {}) or {}
        prices = {}
        for pair, detail in prices_raw.items():
            try:
                prices[pair] = float(detail.get("LastPrice", 0))
            except (TypeError, ValueError):
                pass
    except Exception:
        prices = {}

    state = _safe_load_state()
    pnl_by_asset, live_pnl = _compute_live_pnl(wallet, prices, state)

    # Compute equity
    equity = 0.0
    positions = []
    for asset, detail in wallet.items():
        free = float(detail.get("free", 0.0))
        lock = float(detail.get("lock", 0.0))
        total = free + lock
        if asset == "USD":
            equity += total
            continue
        pair = f"{asset}/USD"
        price = prices.get(pair, 0.0)
        value = total * price if price > 0 else 0.0
        equity += value
        if total > 0:
            position = {
                "asset": asset,
                "free": round(free, 6),
                "lock": round(lock, 6),
                "total": round(total, 6),
                "price": price,
                "value_usd": round(value, 2),
            }
            if asset in pnl_by_asset:
                position.update(pnl_by_asset[asset])
            positions.append(position)

    return {
        "equity_usd": round(equity, 2),
        "usd_free": round(float(wallet.get("USD", {}).get("free", 0.0)), 2),
        "positions": sorted(positions, key=lambda x: x["value_usd"], reverse=True),
        "live_pnl": live_pnl,
    }


def _get_open_orders() -> list[dict]:
    client = _client()
    try:
        resp = client.query_orders(pending_only=True)
        orders = resp.get("OrderList") or resp.get("Orders") or []
        if not isinstance(orders, list):
            return []
        out = []
        for o in orders:
            if not isinstance(o, dict):
                continue
            out.append({
                "order_id": o.get("OrderID", ""),
                "pair": o.get("Pair", ""),
                "side": o.get("Side", ""),
                "type": o.get("Type", ""),
                "quantity": o.get("Quantity", ""),
                "price": o.get("Price", ""),
                "status": o.get("Status", ""),
            })
        return out
    except Exception:
        return []


def _read_trades(limit: int = 200) -> list[dict]:
    order_records = _read_order_records(limit)
    if order_records:
        return order_records

    trades_path = Path(_live_config.state_path).parent / "trades.jsonl"
    if not trades_path.exists():
        return []
    lines = []
    try:
        with trades_path.open(encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def _read_order_records(limit: int = 200) -> list[dict]:
    export_path = ORDER_RECORDS_DIR / f"{_live_config.bot_mode}_orders.json"
    if not export_path.exists():
        return []

    try:
        payload = json.loads(export_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    raw_orders = payload.get("orders", {}).get("OrderMatched", [])
    if not isinstance(raw_orders, list):
        return []

    records = []
    for order in raw_orders[-limit:]:
        if not isinstance(order, dict):
            continue
        timestamp = order.get("FinishTimestamp") or order.get("CreateTimestamp")
        logged_at = ""
        try:
            if timestamp:
                logged_at = pd.to_datetime(int(timestamp), unit="ms", utc=True).isoformat()
        except Exception:
            logged_at = ""

        pair = str(order.get("Pair", ""))
        symbol = pair.replace("/USD", "USDT").replace("/", "")
        filled_price = order.get("FilledAverPrice")
        price = filled_price if filled_price not in (None, 0, 0.0, "0", "0.0", "") else order.get("Price")
        status = str(order.get("Status", ""))
        role = str(order.get("Role", ""))
        records.append({
            "logged_at": logged_at,
            "symbol": symbol,
            "side": order.get("Side", ""),
            "reason": status.lower(),
            "price": price,
            "status": status,
            "role": role,
            "order_id": order.get("OrderID", ""),
            "qty": order.get("FilledQuantity") or order.get("Quantity") or 0,
        })
    return records


def _pnl_summary(trades: list[dict]) -> dict:
    buys: dict[str, dict] = {}
    closed = []
    for record in trades:
        sym = record.get("symbol", "")
        side = record.get("side", "")
        reason = record.get("reason", "")
        status = str(record.get("status", "FILLED")).upper()
        if status not in ("", "FILLED"):
            continue
        if side == "BUY":
            buys[sym] = record
        elif side == "SELL" and reason != "target_resting":
            buy = buys.pop(sym, None)
            if buy:
                try:
                    bp = float(buy.get("price") or 0)
                    sp = float(record.get("price") or 0)
                    if bp > 0 and sp > 0:
                        pnl_bps = ((sp / bp) - 1.0) * 10_000.0
                        closed.append({
                            "symbol": sym,
                            "reason": reason,
                            "pnl_bps": round(pnl_bps, 1),
                            "buy_price": bp,
                            "sell_price": sp,
                            "logged_at": record.get("logged_at", ""),
                        })
                except (TypeError, ValueError):
                    pass
    wins = [t for t in closed if t["pnl_bps"] > 0]
    return {
        "closed_trades": len(closed),
        "wins": len(wins),
        "losses": len(closed) - len(wins),
        "win_rate": round(len(wins) / len(closed), 3) if closed else None,
        "mean_pnl_bps": round(sum(t["pnl_bps"] for t in closed) / len(closed), 1) if closed else None,
        "cum_pnl_bps": round(sum(t["pnl_bps"] for t in closed), 1),
        "recent_closed": closed[-10:],
    }


# ---------------------------------------------------------------------------
# Signal scanner
# ---------------------------------------------------------------------------

def _fetch_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    resp = requests.get(
        f"{_live_config.binance_base_url}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()
    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].astype(float)
    return df


def _get_signals() -> list[dict]:
    now = pd.Timestamp.utcnow()
    results = []
    for symbol in BREAKOUT_SYMBOLS:
        entry = {"symbol": symbol, "error": None}
        try:
            bars = _fetch_klines(symbol, "5m", N_BARS + 50)
            completed = bars.loc[bars["close_time"] < now].reset_index(drop=True)
            if len(completed) < N_BARS + 1:
                entry["error"] = "not enough bars"
                results.append(entry)
                continue

            last_bar = completed.iloc[-1]
            prior = completed.iloc[-(N_BARS + 1):-1]
            rolling_high = float(prior["high"].max())
            last_close = float(last_bar["close"])
            confirm = last_close >= rolling_high
            gap_bps = round((last_close / rolling_high - 1) * 10_000, 1)

            htf = _fetch_klines(symbol, "1h", 480)
            htf_done = htf.loc[htf["close_time"] < now]
            ema_20d = float(htf_done["close"].ewm(span=480, adjust=False).mean().iloc[-1])
            regime_ok = last_close > ema_20d

            entry.update({
                "last_close": round(last_close, 8),
                "rolling_high": round(rolling_high, 8),
                "gap_bps": gap_bps,
                "confirm": confirm,
                "ema_20d": round(ema_20d, 8),
                "regime_ok": regime_ok,
                "signal": confirm and regime_ok,
                "block_reason": (
                    None if (confirm and regime_ok)
                    else "below EMA" if (confirm and not regime_ok)
                    else "no breakout" if (not confirm and regime_ok)
                    else "no breakout + below EMA"
                ),
                "bar_time": last_bar["open_time"].isoformat(),
            })
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/portfolio")
def api_portfolio() -> Response:
    return jsonify(_get_portfolio())


@app.route("/api/orders")
def api_orders() -> Response:
    return jsonify(_get_open_orders())


@app.route("/api/trades")
def api_trades() -> Response:
    trades = _read_trades()
    return jsonify({"trades": trades[:50], "summary": _pnl_summary(trades)})


@app.route("/api/signals")
def api_signals() -> Response:
    return jsonify(_get_signals())


@app.route("/api/health")
def api_health() -> Response:
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>88th Meridian</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Courier New', monospace; background: #0d0d0d; color: #e0e0e0; padding: 24px; }
  h1 { color: #00ff88; font-size: 1.4rem; margin-bottom: 4px; }
  .subtitle { color: #555; font-size: 0.78rem; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: #161616; border: 1px solid #2a2a2a; border-radius: 8px; padding: 16px; }
  .card h2 { font-size: 0.7rem; color: #666; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
  .stat { font-size: 1.6rem; font-weight: bold; color: #fff; }
  .stat.green { color: #00ff88; }
  .stat.red { color: #ff4444; }
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }
  .section-title { font-size: 0.7rem; color: #666; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 10px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  th { text-align: left; color: #555; font-weight: normal; padding: 5px 8px; border-bottom: 1px solid #222; }
  td { padding: 5px 8px; border-bottom: 1px solid #1a1a1a; }
  .pos { color: #00ff88; }
  .neg { color: #ff4444; }
  .reason-stop { color: #ff4444; }
  .reason-target { color: #00ff88; }
  .reason-eod { color: #ffcc00; }
  .side-buy { color: #00ff88; }
  .side-sell { color: #ff4444; }
  #last-updated { font-size: 0.68rem; color: #333; margin-top: 20px; }
  .error { color: #ff4444; font-size: 0.8rem; padding: 8px; }
</style>
</head>
<body>
<h1>88th Meridian</h1>
<p class="subtitle">Refreshes every 30s &nbsp;·&nbsp; Roostoo live data</p>

<div class="grid">
  <div class="card">
    <h2>Total Equity</h2>
    <div class="stat" id="stat-equity">—</div>
  </div>
  <div class="card">
    <h2>Free USD</h2>
    <div class="stat" id="stat-usd">—</div>
  </div>
  <div class="card">
    <h2>Open Positions</h2>
    <div class="stat" id="stat-positions">—</div>
  </div>
  <div class="card">
    <h2>Open Orders</h2>
    <div class="stat" id="stat-orders">—</div>
  </div>
  <div class="card">
    <h2>Closed Trades</h2>
    <div class="stat" id="stat-trades">—</div>
  </div>
  <div class="card">
    <h2>Win Rate</h2>
    <div class="stat" id="stat-winrate">—</div>
  </div>
  <div class="card">
    <h2>Cum P&amp;L</h2>
    <div class="stat" id="stat-pnl">—</div>
  </div>
  <div class="card">
    <h2>Live Gross P&amp;L</h2>
    <div class="stat" id="stat-gross-pnl">—</div>
  </div>
  <div class="card">
    <h2>Live Net P&amp;L</h2>
    <div class="stat" id="stat-net-pnl">—</div>
  </div>
</div>

<div class="two-col">
  <div>
    <div class="section-title">Holdings</div>
    <div style="font-size:0.68rem;color:#444;margin-bottom:8px">Net P&amp;L assumes 10 bps round-trip fees on tracked strategy positions.</div>
    <table>
      <thead><tr><th>Asset</th><th>Total</th><th>Entry</th><th>Price</th><th>Value USD</th><th>Gross P&amp;L</th><th>Net P&amp;L</th></tr></thead>
      <tbody id="holdings-body"></tbody>
    </table>
  </div>
  <div>
    <div class="section-title">Open Orders</div>
    <table>
      <thead><tr><th>Pair</th><th>Side</th><th>Qty</th><th>Price</th></tr></thead>
      <tbody id="orders-body"></tbody>
    </table>
  </div>
</div>

<div>
  <div class="section-title">Recent Trades</div>
  <table>
    <thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Side</th><th>Reason / Status</th><th>Price</th></tr></thead>
    <tbody id="trades-body"></tbody>
  </table>
</div>

<div style="margin-top:24px">
  <div class="section-title">Signal Scanner — what the bot sees right now</div>
  <table>
    <thead>
      <tr>
        <th>Symbol</th>
        <th>Last Close</th>
        <th>Rolling High (24h)</th>
        <th>Gap</th>
        <th>Confirm</th>
        <th>20d EMA</th>
        <th>Regime</th>
        <th>Signal</th>
        <th>Block Reason</th>
      </tr>
    </thead>
    <tbody id="signals-body"></tbody>
  </table>
</div>

<div id="last-updated">Last updated: —</div>

<script>
function fmt(n, d=2) { return n != null ? Number(n).toFixed(d) : '—'; }
function fmtUSD(n) { return n != null ? '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2}) : '—'; }
function fmtSignedUSD(n) {
  if (n == null) return '—';
  const value = Number(n);
  const abs = Math.abs(value).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  return (value >= 0 ? '+$' : '-$') + abs;
}
function signedClass(n) { return n == null ? '' : (Number(n) >= 0 ? 'green' : 'red'); }
function signedCellClass(n) { return n == null ? '' : (Number(n) >= 0 ? 'pos' : 'neg'); }

async function refresh() {
  try {
    const [portRes, ordersRes, tradesRes, signalsRes] = await Promise.all([
      fetch('/api/portfolio'),
      fetch('/api/orders'),
      fetch('/api/trades'),
      fetch('/api/signals'),
    ]);
    const port = await portRes.json();
    const orders = await ordersRes.json();
    const tradesData = await tradesRes.json();
    const signals = await signalsRes.json();
    render(port, orders, tradesData, signals);
  } catch(e) {
    console.error(e);
  }
}

function render(port, orders, tradesData, signals) {
  const summary = tradesData.summary || {};
  const trades = tradesData.trades || [];
  const positions = port.positions || [];
  const livePnl = port.live_pnl || {};

  // Stats
  document.getElementById('stat-equity').textContent = fmtUSD(port.equity_usd);
  document.getElementById('stat-usd').textContent = fmtUSD(port.usd_free);
  document.getElementById('stat-positions').textContent = positions.length;
  document.getElementById('stat-orders').textContent = orders.length;
  document.getElementById('stat-trades').textContent = summary.closed_trades ?? '—';

  const wr = summary.win_rate;
  const wrEl = document.getElementById('stat-winrate');
  wrEl.textContent = wr != null ? (wr*100).toFixed(1)+'%' : '—';
  wrEl.className = 'stat ' + (wr != null ? (wr >= 0.5 ? 'green' : 'red') : '');

  const pnl = summary.cum_pnl_bps ?? 0;
  const pnlEl = document.getElementById('stat-pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmt(pnl, 1) + ' bps';
  pnlEl.className = 'stat ' + (pnl >= 0 ? 'green' : 'red');

  const grossPnl = livePnl.gross_pnl_usd;
  const grossPnlEl = document.getElementById('stat-gross-pnl');
  grossPnlEl.textContent = grossPnl != null ? fmtSignedUSD(grossPnl) : '—';
  grossPnlEl.className = 'stat ' + signedClass(grossPnl);

  const netPnl = livePnl.net_pnl_usd;
  const netPnlEl = document.getElementById('stat-net-pnl');
  netPnlEl.textContent = netPnl != null ? fmtSignedUSD(netPnl) : '—';
  netPnlEl.className = 'stat ' + signedClass(netPnl);

  // Holdings
  const hbody = document.getElementById('holdings-body');
  hbody.innerHTML = positions.length ? positions.map(p => `
    <tr>
      <td>${p.asset}</td>
      <td>${fmt(p.total, 4)}</td>
      <td>${p.entry_price != null ? '$'+fmt(p.entry_price, 6) : '—'}</td>
      <td>${p.price ? '$'+fmt(p.price, 6) : '—'}</td>
      <td class="pos">${fmtUSD(p.value_usd)}</td>
      <td class="${signedCellClass(p.gross_pnl_usd)}">${p.gross_pnl_usd != null ? fmtSignedUSD(p.gross_pnl_usd) : '—'}</td>
      <td class="${signedCellClass(p.net_pnl_usd)}">${p.net_pnl_usd != null ? fmtSignedUSD(p.net_pnl_usd) : '—'}</td>
    </tr>`).join('') : '<tr><td colspan="7" style="color:#444">No open positions</td></tr>';

  // Open orders
  const obody = document.getElementById('orders-body');
  obody.innerHTML = orders.length ? orders.map(o => `
    <tr>
      <td>${o.pair}</td>
      <td class="${o.side==='BUY'?'side-buy':'side-sell'}">${o.side}</td>
      <td>${o.quantity}</td>
      <td>${o.price}</td>
    </tr>`).join('') : '<tr><td colspan="4" style="color:#444">No open orders</td></tr>';

  // Recent trades
  const tbody = document.getElementById('trades-body');
  tbody.innerHTML = trades.slice(-30).map(t => {
    const time = t.logged_at ? t.logged_at.slice(0,19).replace('T',' ') : '—';
    const label = t.reason || t.status || '—';
    const rc = label==='stop' ? 'reason-stop' : label==='target_resting' || label==='filled' ? 'reason-target' : 'reason-eod';
    return `<tr>
      <td>${time}</td>
      <td>${t.symbol}</td>
      <td class="${t.side==='BUY'?'side-buy':'side-sell'}">${t.side}</td>
      <td class="${rc}">${label}</td>
      <td>${t.price != null ? fmt(t.price,6) : '—'}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" style="color:#444">No trades yet</td></tr>';

  // Signal scanner
  const sbody = document.getElementById('signals-body');
  sbody.innerHTML = (signals || []).map(s => {
    if (s.error) return `<tr><td>${s.symbol}</td><td colspan="8" style="color:#ff4444">${s.error}</td></tr>`;
    const signalCell = s.signal
      ? '<td style="color:#00ff88;font-weight:bold">FIRE</td>'
      : '<td style="color:#444">—</td>';
    const confirmCell = s.confirm
      ? '<td class="pos">YES</td>'
      : '<td class="neg">NO</td>';
    const regimeCell = s.regime_ok
      ? '<td class="pos">YES</td>'
      : '<td class="neg">NO</td>';
    const gap = s.gap_bps != null ? (s.gap_bps >= 0 ? '+' : '') + s.gap_bps + ' bps' : '—';
    const gapClass = s.gap_bps >= 0 ? 'pos' : 'neg';
    return `<tr>
      <td>${s.symbol}</td>
      <td>${s.last_close ?? '—'}</td>
      <td>${s.rolling_high ?? '—'}</td>
      <td class="${gapClass}">${gap}</td>
      ${confirmCell}
      <td>${s.ema_20d ?? '—'}</td>
      ${regimeCell}
      ${signalCell}
      <td style="color:#555">${s.block_reason ?? ''}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="9" style="color:#444">Loading...</td></tr>';

  document.getElementById('last-updated').textContent = 'Last updated: ' + new Date().toISOString().replace('T',' ').slice(0,19) + ' UTC';
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>
"""


@app.route("/")
def index() -> Response:
    return Response(_HTML, mimetype="text/html")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="88th Meridian local dashboard")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--competition", action="store_true")
    return p.parse_args()


def main() -> int:
    global _live_config
    args = parse_args()
    _live_config = build_live_config(competition=args.competition)

    print(f"Dashboard: http://localhost:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
