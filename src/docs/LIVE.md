# 88th Meridian — Live Execution

## Architecture

The bot polls on a 60-second cycle. Each cycle:

1. Fetch Roostoo ticker prices and wallet balances
2. For each of the 5 symbols, check exit conditions first, then entry conditions
3. Optionally run a compliance trade if no order was logged today
4. Persist state to disk

Market data (Binance klines) is fetched per-symbol only when checking for an entry signal.
Exit checks use the Roostoo ticker price already in memory — no extra API calls.

---

## Market Data Sources

| Source | Used for |
|---|---|
| Binance `/api/v3/klines?interval=5m&limit=338` | Rolling high + confirmation bar |
| Binance `/api/v3/klines?interval=1h&limit=480` | 20-day EMA regime gate |
| Roostoo `/v3/ticker` | Current price for entry sizing and exit execution |
| Roostoo `/v3/balance` | Wallet free/locked balances |

The 5m fetch requests 338 bars and filters to completed bars (close_time < now).
Rolling high is the max of the prior 288, excluding the signal bar.

The 1h fetch requests 480 bars (20 days). EWM with `span=480` on hourly closes is
equivalent to the 20-day EMA from the backtest (`span=5760` on 5m data).
Using hourly bars ensures the EMA is fully warmed up in a single API call.

---

## Entry Flow

```
Poll fires
  → fetch Roostoo prices, wallet, compute total equity
  → for each symbol:
      if active position → check exits → skip entry check
      fetch 338 × 5m bars from Binance
      compute rolling_high = max(prior 288 bars' highs)
      if last bar close < rolling_high → skip
      if last bar volume < 1.5 × 20-bar avg volume → skip
      fetch 480 × 1h bars from Binance
      compute ema_20d = EWM(span=480) on 1h closes
      if last bar close <= ema_20d → skip
      ── signal confirmed ──
      allocation = total_equity / 5
      qty = floor(min(allocation, free_usd) / current_price)
      place LIMIT BUY at current_price
      if fill confirmed:
          place resting LIMIT SELL at target_price
          save state
```

**Why a limit order for the entry buy?**
The bar has already closed above rolling high, so current price is at or above rolling high.
A limit order at `current_price` fills immediately while paying 5 bps/side instead of
10 bps/side for a market order.

**`last_bar_time` deduplication**
The bot records the `open_time` of the last processed 5m bar. If consecutive polls
(60s apart) see the same bar as the last completed bar, the entry check is skipped.
This prevents double-entry within a single 5-minute bar window.

---

## Exit Flow

Three exit conditions are checked on each poll, in priority order:

### 1. Target hit (resting order, detected passively)

The resting LIMIT SELL placed at entry fires automatically when Roostoo price touches
`target_price`. The bot detects this by checking asset balance:

```python
target_already_filled = (
    target_order_id != ""
    and asset_balance < qty * 0.01
)
```

If detected, state is cleared. No sell order is placed.

### 2. Stop hit (polling-based)

```python
if current_price <= stop_price:
    cancel resting target order
    place LIMIT SELL at current_price
```

Stop is anchored to `rolling_high × (1 − 100 bps)`, not entry price.
The sell is placed at `current_price` (already at or below stop). Limit orders
fill on touch in Roostoo simulation.

### 3. EOD (midnight UTC)

```python
if today_utc != entry_day:
    cancel resting target order
    place LIMIT SELL at current_price
```

Triggered at the first poll after midnight UTC. Closes position at current market price.

### Target order recovery

If a position is active but `target_order_id` is empty (order failed to place at entry),
the bot re-places the resting target sell on the next poll:

```python
if target_order_id == "" and target_price > 0 and current_price < target_price:
    place LIMIT SELL at target_price
```

---

## State Schema

Persisted to `src/state/competition_live_state.json` after every cycle.

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

The file is overwritten atomically on each cycle. `last_bar_time` prevents re-processing
the same 5m bar on multiple polls within a single bar window.

---

## Trade Log

Every order submitted is appended to `src/state/trades.jsonl` (append-only):

```json
{
  "logged_at": "2026-03-22T14:35:01.123Z",
  "mode": "competition",
  "sleeve": "88th_meridian",
  "symbol": "FLOKIUSDT",
  "side": "BUY",
  "reason": "breakout",
  "requested_qty": 8100000.0,
  "filled_qty": 8100000.0,
  "order_id": "abc123",
  "status": "FILLED",
  "price": "0.00012345",
  "response": { ... }
}
```

**`reason` values:**

| Value | Meaning |
|---|---|
| `breakout` | Entry buy on confirmed breakout signal |
| `target_resting` | Resting limit sell placed at target after entry |
| `target_resting_recovery` | Target sell re-placed after missing at entry |
| `stop` | Exit sell triggered by stop breach |
| `eod` | Exit sell triggered by EOD (midnight UTC) |
| `compliance` | Daily compliance BTC buy+sell (see below) |

---

## Compliance Trade

If no order has been logged today by 20:00 UTC, the bot places a $5 limit buy + immediate
sell on BTCUSDT. This satisfies any daily activity requirements without affecting strategy
positions (BTC is not in the breakout symbol set).

---

## Credential Modes

| Flag | Env vars used | State file |
|---|---|---|
| (default) | `ROOSTOO_API_KEY`, `ROOSTOO_API_SECRET` | `src/state/live_state.json` |
| `--competition` | `ROOSTOO_COMPETITION_API_KEY`, `ROOSTOO_COMPETITION_API_SECRET` | `src/state/competition_live_state.json` |

Credentials are loaded from `src/.env` automatically. Format:

```
ROOSTOO_COMPETITION_BASE_URL=https://mock-api.roostoo.com
ROOSTOO_COMPETITION_API_KEY=your_key
ROOSTOO_COMPETITION_API_SECRET=your_secret
```

---

## Deployment (AWS EC2 + systemd)

**1. Clone and install**

```bash
git clone <repo> 88_street
cd 88_street
pip install -r src/requirements-live.txt
cp src/.env.example src/.env   # fill in credentials
```

**2. Create systemd service for the bot**

```ini
# /etc/systemd/system/88meridian-bot.service
[Unit]
Description=88th Meridian live bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/88_street
ExecStart=/usr/bin/python3 -m src.live_bot --competition --live
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**3. Create systemd service for the dashboard**

```ini
# /etc/systemd/system/88meridian-dashboard.service
[Unit]
Description=88th Meridian monitoring dashboard
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/88_street
ExecStart=/usr/bin/python3 -m src.dashboard --competition --port 8080
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**4. Enable and start**

```bash
sudo systemctl daemon-reload
sudo systemctl enable 88meridian-bot 88meridian-dashboard
sudo systemctl start  88meridian-bot 88meridian-dashboard
```

**5. EC2 security group**

Open inbound TCP port 8080 to your IP to access the dashboard at `http://<ec2-ip>:8080`.

**Useful commands**

```bash
# Check status
sudo systemctl status 88meridian-bot

# Follow live logs
sudo journalctl -fu 88meridian-bot

# Restart after code update
git pull && sudo systemctl restart 88meridian-bot 88meridian-dashboard
```

---

## Live vs Backtest Gaps

| Gap | Impact |
|---|---|
| Entry at `current_price`, not bar close | Slight positive slippage — enters above rolling high, consistent with realistic model |
| Stop detected at poll time, not bar-by-bar | Could miss a stop that touches and recovers within 60s |
| Stop exit uses stale `current_price` | If price moves further down between cancel and sell order, limit may rest briefly |
| EOD detected at first poll after midnight | Up to 60s of overnight exposure vs a clean midnight close |
| Equity computed once per cycle | Minor over-allocation if two symbols trigger on the same cycle; capped by free balance check |

None of these gaps materially affect expected performance given the 10 bps fee regime
and 3:1 R:R structure.
