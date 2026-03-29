# 88th Meridian — Spread Capture Bot

## Overview

The spread capture bot posts a limit buy at the current best bid, waits for the order
to fill, then immediately posts a limit sell at the current best ask. Each completed
cycle nets the bid-ask spread minus the 10 bps round-trip maker fee.

Edge is mechanical: the bot earns money as long as the spread is wider than 10 bps.
It does not predict price direction.

---

## State Machine

The bot is a simple three-state machine, persisted to disk after every transition so
restarts are safe and resume from where they left off.

```
FLAT
  │ place limit BUY at bid
  ▼
BUYING
  │ buy fills (coin balance increases)
  ▼
IN_POSITION
  │ limit SELL fills (coin balance drops to ~0)
  ▼
FLAT

Timeout path (at any state after --fill-timeout seconds):
  → cancel open order → market SELL remaining coin → FLAT
```

---

## Cycle Flow

```
FLAT
  → read best bid and ask from Roostoo ticker
  → print spread in bps
  → compute qty = floor(size_usd / bid) rounded down to step size 10
  → place LIMIT BUY at bid
  → save state → transition to BUYING

BUYING  (poll every 0.5s)
  → check coin balance: if increased by ~qty → buy filled
      → read current best ask
      → place LIMIT SELL at ask (fallback: bid × 1.0029 if ask unavailable)
      → save state → transition to IN_POSITION
  → if elapsed > fill_timeout → cancel buy → transition to FLAT

IN_POSITION  (poll every 0.5s)
  → check total coin balance (free + locked):
      if < 1% of qty → sell filled → record trade → transition to FLAT
  → if elapsed > fill_timeout:
      → cancel sell order
      → place market SELL for remaining coin
      → record trade as "timeout" → transition to FLAT
```

---

## Edge Calculation

```
gross_bps = (sell_price / buy_price - 1) × 10,000
net_bps   = gross_bps - 10          # 10 bps round-trip maker fee
```

The bot is profitable on any cycle where `spread_bps > 10`. PEPE typically trades
at 15–40 bps spread on Roostoo, giving 5–30 bps net per cycle.

---

## State File

One state file per symbol and account, written to `src/state/`:

```
src/state/spread_state_PEPE_USD_COMPETITION.json
```

Schema:

```json
{
  "status": "FLAT",
  "cum_net_bps": 142.3,
  "trade_num": 47,
  "buy_order_id": null,
  "buy_price": null,
  "qty": null,
  "coin_before": null,
  "usd_before": null,
  "t_buy": null,
  "sell_order_id": null,
  "sell_price": null
}
```

`cum_net_bps` and `trade_num` accumulate across restarts. All other fields are
cleared to null when transitioning to FLAT.

---

## CSV Trade Log

Every completed cycle is appended to:

```
src/state/spread_logs/PEPE_USD_COMPETITION.csv
```

| Column | Description |
|---|---|
| `timestamp` | UTC time of exit |
| `pair` | e.g. `PEPE/USD` |
| `bid` | Buy price |
| `ask` | Sell price |
| `spread_bps` | `(ask/bid − 1) × 10,000` |
| `buy_price` | Actual buy fill price |
| `sell_price` | Actual sell price |
| `gross_bps` | `(sell/buy − 1) × 10,000` |
| `net_bps` | `gross_bps − 10` |
| `hold_s` | Seconds from buy fill to sell fill |
| `cum_net_bps` | Running cumulative net bps |
| `roi_pct` | `cum_net_bps / 10,000 × 100` |
| `trade_num` | Sequential trade counter |
| `exit_reason` | `fill` or `timeout` |

---

## Rate Limiting

`--max-tpm` (default 15) caps the number of trade cycles started per minute.
The bot tracks a rolling 60-second window of cycle start timestamps and sleeps
until capacity is available if the limit is reached.

---

## Fill Timeout

`--fill-timeout` (default 1800s = 30 minutes) applies to both states:

- **BUYING:** if the limit buy hasn't filled in `fill_timeout` seconds, the order
  is cancelled and the bot returns to FLAT without placing a sell.

- **IN_POSITION:** if the limit sell hasn't filled in `fill_timeout` seconds from
  the original buy time, the sell is cancelled and the bot places a market sell
  for all remaining coin, then returns to FLAT. The cycle is logged with
  `exit_reason = timeout`.

The timeout protects against getting stuck holding coin if price moves away from
the sell level. Use a shorter timeout (e.g. `--fill-timeout 300`) in fast-moving
markets and a longer one in slow markets.

---

## Sizing

**Fixed notional (`--size-usd N`):**
```
qty = floor(N / bid) rounded down to step size 10
```
If free USD balance is below `N`, the cycle is skipped.

**Full balance (no `--size-usd`):**
```
size_usd = free_usd × 0.90
qty = floor(size_usd / bid) rounded down to step size 10
```

In both cases, qty is floored to a multiple of 10 (PEPE lot size).

---

## Deployment (AWS EC2 + systemd)

**1. Clone and install**

```bash
git clone <repo> 88_street
cd 88_street
pip install -r src/requirements-live.txt
cp src/.env.example src/.env   # fill in competition credentials
```

**2. Create systemd service**

```ini
# /etc/systemd/system/88meridian-spread.service
[Unit]
Description=88th Meridian spread capture bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/88_street
ExecStart=/usr/bin/python3 -m src.spread_bot \
    --competition \
    --size-usd 300000 \
    --forever \
    --fill-timeout 1800
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**3. Enable and start**

```bash
sudo systemctl daemon-reload
sudo systemctl enable 88meridian-spread
sudo systemctl start  88meridian-spread
```

**Useful commands**

```bash
# Check status
sudo systemctl status 88meridian-spread

# Follow live output
sudo journalctl -fu 88meridian-spread

# Restart after code update
git pull && sudo systemctl restart 88meridian-spread
```

---

## Live vs Ideal Gaps

| Gap | Impact |
|---|---|
| Bid/ask from poll, not true TOB | Roostoo ticker `MaxBid`/`MinAsk` may lag by up to one poll cycle (0.5s) |
| Fill detection via balance poll | 0.5s polling delay between fill and sell placement |
| Sell price from ask at fill time, not order time | If ask moves between buy fill and sell placement, sell price may differ from the spread seen at order time |
| Market sell on timeout uses `current_bid` | If bid drops after timeout triggers, exit price may be worse than expected |
