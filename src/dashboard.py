"""Simple monitoring dashboard for the live breakout bot.

Reads the state file and trades.jsonl written by live_bot.py and serves
a self-refreshing HTML page on port 8080.

Usage:
  python -m src.dashboard [--port 8080] [--competition]

Then open  http://<EC2_PUBLIC_IP>:8080  in any browser.
EC2: ensure port 8080 is open in the instance's security group (inbound TCP).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from flask import Flask, Response, jsonify

from .live_config import build_live_config

app = Flask(__name__)
_state_path: Path = Path("src/state/competition_live_state.json")
_trades_path: Path = Path("src/state/trades.jsonl")

# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def _read_state() -> dict:
    if not _state_path.exists():
        return {}
    try:
        return json.loads(_state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _read_trades(limit: int = 200) -> list[dict]:
    if not _trades_path.exists():
        return []
    lines = []
    try:
        with _trades_path.open(encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:
        return []
    records = []
    for line in lines[-limit:]:
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(records))  # most recent first


def _pnl_summary(trades: list[dict]) -> dict:
    """Compute simple P&L stats from trade log."""
    buys: dict[str, dict] = {}   # symbol -> last buy record
    closed: list[dict] = []

    # Walk chronologically (trades list is reversed, so reverse back)
    for record in reversed(trades):
        sym = record.get("symbol", "")
        side = record.get("side", "")
        reason = record.get("reason", "")

        if side == "BUY":
            buys[sym] = record
        elif side == "SELL" and reason != "target_resting":
            # Actual exit (stop, eod, or target auto-filled)
            buy = buys.pop(sym, None)
            if buy:
                try:
                    buy_price = float(buy.get("price") or 0)
                    sell_price = float(record.get("price") or 0)
                    if buy_price > 0 and sell_price > 0:
                        pnl_bps = ((sell_price / buy_price) - 1.0) * 10_000.0
                        closed.append({
                            "symbol": sym,
                            "reason": reason,
                            "pnl_bps": round(pnl_bps, 1),
                            "buy_price": buy_price,
                            "sell_price": sell_price,
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
        "recent_closed": closed[:10],
    }


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/state")
def api_state() -> Response:
    return jsonify(_read_state())


@app.route("/api/trades")
def api_trades() -> Response:
    trades = _read_trades()
    return jsonify({
        "trades": trades[:50],
        "summary": _pnl_summary(trades),
    })


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
<title>88 Street — Bot Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Courier New', monospace; background: #0d0d0d; color: #e0e0e0; padding: 24px; }
  h1 { color: #00ff88; font-size: 1.4rem; margin-bottom: 4px; }
  .subtitle { color: #666; font-size: 0.8rem; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card { background: #161616; border: 1px solid #2a2a2a; border-radius: 8px; padding: 16px; }
  .card h2 { font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; }
  .stat { font-size: 1.8rem; font-weight: bold; color: #fff; }
  .stat.green { color: #00ff88; }
  .stat.red { color: #ff4444; }
  .stat.yellow { color: #ffcc00; }
  .symbol-card { background: #161616; border: 1px solid #2a2a2a; border-radius: 8px; padding: 14px; margin-bottom: 10px; }
  .symbol-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .symbol-name { font-size: 1rem; font-weight: bold; color: #00ff88; }
  .badge { font-size: 0.7rem; padding: 2px 8px; border-radius: 12px; font-weight: bold; }
  .badge.active { background: #003322; color: #00ff88; border: 1px solid #00ff88; }
  .badge.idle { background: #1a1a1a; color: #555; border: 1px solid #333; }
  .row { display: flex; justify-content: space-between; font-size: 0.78rem; color: #aaa; margin-top: 4px; }
  .row span:last-child { color: #e0e0e0; }
  table { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
  th { text-align: left; color: #888; font-weight: normal; padding: 6px 8px; border-bottom: 1px solid #2a2a2a; }
  td { padding: 6px 8px; border-bottom: 1px solid #1a1a1a; }
  .pos { color: #00ff88; }
  .neg { color: #ff4444; }
  .reason-stop { color: #ff4444; }
  .reason-target { color: #00ff88; }
  .reason-eod { color: #ffcc00; }
  #last-updated { font-size: 0.7rem; color: #444; margin-top: 16px; }
</style>
</head>
<body>
<h1>88 Street — Breakout Bot</h1>
<p class="subtitle">Auto-refreshes every 30s &nbsp;·&nbsp; <span id="mode"></span></p>

<div class="grid">
  <div class="card">
    <h2>Active Positions</h2>
    <div class="stat" id="stat-active">—</div>
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
    <h2>Cumulative P&amp;L</h2>
    <div class="stat" id="stat-pnl">—</div>
  </div>
</div>

<div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px;">
  <div>
    <h2 style="font-size:0.75rem;color:#888;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px">Positions</h2>
    <div id="positions"></div>
  </div>
  <div>
    <h2 style="font-size:0.75rem;color:#888;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px">Recent Trades</h2>
    <table>
      <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Reason</th><th>Price</th></tr></thead>
      <tbody id="trades-body"></tbody>
    </table>
  </div>
</div>

<div id="last-updated">Last updated: —</div>

<script>
async function refresh() {
  try {
    const [stateRes, tradesRes] = await Promise.all([
      fetch('/api/state'),
      fetch('/api/trades'),
    ]);
    const state = await stateRes.json();
    const tradesData = await tradesRes.json();
    render(state, tradesData);
  } catch(e) {
    console.error(e);
  }
}

function fmt(n, decimals=2) {
  if (n == null) return '—';
  return Number(n).toFixed(decimals);
}

function render(state, tradesData) {
  const symbols = state.symbols || {};
  const summary = tradesData.summary || {};
  const trades = tradesData.trades || [];

  const active = Object.values(symbols).filter(s => s.active).length;
  document.getElementById('stat-active').textContent = active + ' / ' + Object.keys(symbols).length;

  const closed = summary.closed_trades ?? 0;
  document.getElementById('stat-trades').textContent = closed;

  const wr = summary.win_rate;
  const wrEl = document.getElementById('stat-winrate');
  wrEl.textContent = wr != null ? (wr * 100).toFixed(1) + '%' : '—';
  wrEl.className = 'stat ' + (wr != null ? (wr >= 0.5 ? 'green' : 'red') : '');

  const pnl = summary.cum_pnl_bps ?? 0;
  const pnlEl = document.getElementById('stat-pnl');
  pnlEl.textContent = (pnl >= 0 ? '+' : '') + fmt(pnl, 1) + ' bps';
  pnlEl.className = 'stat ' + (pnl >= 0 ? 'green' : 'red');

  // Positions
  const posDiv = document.getElementById('positions');
  posDiv.innerHTML = '';
  for (const [sym, s] of Object.entries(symbols)) {
    const card = document.createElement('div');
    card.className = 'symbol-card';
    const badge = s.active ? '<span class="badge active">ACTIVE</span>' : '<span class="badge idle">IDLE</span>';
    let inner = `<div class="symbol-header"><span class="symbol-name">${sym}</span>${badge}</div>`;
    if (s.active) {
      inner += `<div class="row"><span>Entry</span><span>${fmt(s.entry_price, 6)}</span></div>`;
      inner += `<div class="row"><span>Stop</span><span class="neg">${fmt(s.stop_price, 6)}</span></div>`;
      inner += `<div class="row"><span>Target</span><span class="pos">${fmt(s.target_price, 6)}</span></div>`;
      inner += `<div class="row"><span>Qty</span><span>${fmt(s.qty, 4)}</span></div>`;
      inner += `<div class="row"><span>Entry day</span><span>${s.entry_day || '—'}</span></div>`;
    }
    card.innerHTML = inner;
    posDiv.appendChild(card);
  }

  // Recent trades
  const tbody = document.getElementById('trades-body');
  tbody.innerHTML = '';
  for (const t of trades.slice(0, 30)) {
    const time = t.logged_at ? t.logged_at.slice(0, 19).replace('T', ' ') : '—';
    const reasonClass = t.reason === 'stop' ? 'reason-stop' : t.reason === 'target_resting' ? 'reason-target' : 'reason-eod';
    const sideClass = t.side === 'BUY' ? 'pos' : 'neg';
    const price = t.price != null ? fmt(t.price, 6) : '—';
    tbody.innerHTML += `<tr>
      <td>${time}</td>
      <td>${t.symbol}</td>
      <td class="${sideClass}">${t.side}</td>
      <td class="${reasonClass}">${t.reason}</td>
      <td>${price}</td>
    </tr>`;
  }

  document.getElementById('last-updated').textContent = 'Last updated: ' + new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
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
    p = argparse.ArgumentParser(description="Bot monitoring dashboard")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--competition", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    live = build_live_config(competition=args.competition)

    global _state_path, _trades_path
    _state_path = live.state_path
    _trades_path = live.state_path.parent / "trades.jsonl"

    print(f"Dashboard running on http://{args.host}:{args.port}")
    print(f"State file:  {_state_path}")
    print(f"Trades file: {_trades_path}")
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
