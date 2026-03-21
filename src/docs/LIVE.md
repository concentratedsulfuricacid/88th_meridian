# 88th Meridian — Live Execution

## Architecture

The live bot polls on a 60-second cycle. Each cycle:

1. Fetch Roostoo ticker prices and wallet balances
2. For each of the 5 symbols, check exit conditions first, then entry conditions
3. Persist state to disk after every cycle

Market data (Binance klines) is fetched per-symbol only when checking for an entry
signal. Exit checks use the Roostoo ticker price already in memory.

---

## Market Data

| Source | Used for |
|--------|----------|
| Binance REST `/api/v3/klines` | 5m bars for rolling high and confirmation bar |
| Binance REST `/api/v3/klines` | 1h bars for 20-day EMA regime gate |
| Roostoo REST `/v3/ticker` | Current price for entry sizing and exit execution |
| Roostoo REST `/v3/balance` | Wallet balances and free/locked qty |

The 5m fetch requests `N_BARS + 50 = 338` bars and filters to completed bars only
(close_time < now). Rolling high is computed from the last 288 of those bars,
excluding the signal bar.

The 1h fetch requests 480 bars (20 days). EWM with `span=480` on hourly closes is
equivalent to the 20-day EMA computed in the backtest on 5m data with `span=5760`.
Using hourly bars ensures the EMA is fully warmed up in a single API call.

---

## Entry Flow

```
Poll fires
  → fetch prices, wallet, equity
  → for each symbol:
      if active position → check exits → skip entry
      fetch 338 × 5m bars (Binance)
      compute rolling_high = max(prior 288 bars' highs)
      if last bar close < rolling_high → skip
      fetch 480 × 1h bars (Binance)
      compute ema_20d = EWM(span=480) on hourly closes
      if last bar close <= ema_20d → skip
      ── signal confirmed ──
      allocation = equity / 5
      qty = floor(min(allocation, free_usd) / current_price)
      place LIMIT BUY at current_price
      if fill confirmed:
        place resting LIMIT SELL at target (fires automatically on touch)
        save state
```

**Why limit for the entry buy?** The bar has already closed above rolling high, so
current price is above rolling high. A limit order at current_price fills immediately
(Roostoo guarantees fill on touch) while paying only 5 bps/side instead of 10 bps/side
for a market order.

---

## Exit Flow

Three exit conditions are checked on each poll, in this order:

### 1. Target hit (resting order)
The resting LIMIT SELL placed at entry is still live on Roostoo. When price touches
the target, Roostoo fills it automatically with no further bot action needed. The bot
detects this by checking if the asset balance has dropped to near zero:

```python
target_already_filled = (
    target_order_id != ""
    and asset_balance < qty * 0.01
)
```

If detected, state is cleared.

### 2. Stop hit (polling-based)
```python
if current_price <= stop_price:
    cancel resting target order
    place LIMIT SELL at current_price
```

Stop is anchored to `rolling_high × (1 − 100 bps)`, not entry price, matching the
backtest. The sell is placed at `current_price` (which is already at or below stop).
In Roostoo simulation, limit orders fill on touch so this is guaranteed to execute.

### 3. EOD (midnight UTC)
```python
if today_utc != entry_day:
    cancel resting target order
    place LIMIT SELL at current_price
```

Detected at the first poll after midnight UTC. Closes position at current market price.

---

## State Schema

State is persisted to `src/state/competition_live_state.json` after every cycle.

```json
{
  "symbols": {
    "FLOKIUSDT": {
      "active": true,
      "entry_price": 0.00012345,
      "stop_price": 0.00012220,
      "target_price": 0.00012716,
      "entry_day": "2026-03-22",
      "qty": 8100000.0,
      "last_bar_time": "2026-03-22T14:30:00+00:00",
      "target_order_id": "abc123"
    }
  }
}
```

`last_bar_time` prevents re-processing the same 5m bar on consecutive polls within
the same bar's window (polls every 60s, bars close every 300s).

---

## Trade Log

Every order submitted is appended to `src/state/trades.jsonl`:

```json
{"logged_at":"...","mode":"competition","sleeve":"breakout","symbol":"FLOKIUSDT","side":"BUY","reason":"breakout","requested_qty":8100000,"filled_qty":8100000,"order_id":"abc123","status":"FILLED","price":"0.00012345","response":{...}}
```

The log is append-only and survives bot restarts.

---

## Credential Modes

| Mode | Env vars | State file |
|------|----------|------------|
| Default | `ROOSTOO_API_KEY`, `ROOSTOO_API_SECRET` | `src/state/live_state.json` |
| Competition | `ROOSTOO_COMPETITION_API_KEY`, `ROOSTOO_COMPETITION_API_SECRET` | `src/state/competition_live_state.json` |

Run with `--competition` flag to use competition credentials.

---

## Deployment

The bot and dashboard are designed to run as persistent systemd services on an AWS EC2 instance.

```bash
# Bot
python3 -m src.live_bot --competition --live

# Dashboard (port 8080)
python3 -m src.dashboard --competition --port 8080
```

The dashboard reads the state file and trade log and serves a self-refreshing HTML page.
Open inbound TCP port 8080 in the EC2 security group to access it publicly.

---

## Known Live vs Backtest Gaps

| Gap | Impact |
|-----|--------|
| Entry at `current_price` not bar close | Slight positive slippage (enter above rolling high, matching realistic model) |
| Stop detected at poll time, not bar-by-bar | Could miss a stop that recovers within 60s |
| Stop exit limit at stale `current_price` | If price moves further during cancel+order sequence, limit may rest briefly |
| EOD detected at first poll after midnight | Up to 60s of overnight exposure vs clean midnight close |
| Equity computed once per cycle | Minor over-allocation if two symbols trigger on same cycle (capped by free balance) |

None of these gaps materially affect expected performance given the 10 bps fee regime
and 3:1 R:R structure.
