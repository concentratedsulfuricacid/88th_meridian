# 88th Meridian — Spread Capture Bot

A high-frequency spread capture strategy built for the Roostoo trading competition.
Posts a limit buy at the best bid, waits for fill, immediately posts a limit sell at
the best ask. Nets the bid-ask spread minus maker fees on every cycle.

---

## Strategy

| Parameter | Value |
|---|---|
| Entry | Limit BUY at current best bid |
| Exit | Limit SELL at current best ask (placed immediately after buy fills) |
| Gross edge per cycle | bid-ask spread in bps |
| Fee cost per cycle | 10 bps round-trip (5 bps/side, limit orders) |
| Net edge per cycle | spread bps − 10 bps |
| Time-stop | Cancel + market sell after `--fill-timeout` seconds (default 30 min) |
| Default symbol | PEPEUSDT |

**Example:** PEPE spread = 30 bps → gross 30 bps − 10 bps fees = **+20 bps net** per cycle.

---

## Quick Start

```bash
# Install dependencies
pip install -r src/requirements-live.txt

# Run on competition account, $300k per cycle, run forever
python3 -m src.spread_bot --competition --size-usd 300000 --forever

# Run with a 30-minute fill timeout
python3 -m src.spread_bot --competition --size-usd 300000 --forever --fill-timeout 1800

# Different symbol
python3 -m src.spread_bot --competition --symbol WIFUSDT --size-usd 100000 --forever

# Use full free balance each cycle (no --size-usd)
python3 -m src.spread_bot --competition --forever
```

---

## Configuration

Copy `src/.env.example` to `src/.env` and fill in credentials:

```
ROOSTOO_COMPETITION_BASE_URL=https://mock-api.roostoo.com
ROOSTOO_COMPETITION_API_KEY=your_key
ROOSTOO_COMPETITION_API_SECRET=your_secret
```

---

## CLI Reference

| Flag | Default | Description |
|---|---|---|
| `--symbol` | `PEPEUSDT` | Trading pair |
| `--size-usd` | full balance | USD notional per cycle. Omit to use 90% of free USD |
| `--forever` | off | Run indefinitely |
| `--minutes` | 480 | Runtime limit in minutes (ignored if `--forever`) |
| `--fill-timeout` | 1800 | Seconds to wait for a fill before cancelling and moving on |
| `--max-tpm` | 15 | Max trades per minute (rate-limit guard) |
| `--competition` | off | Use competition account credentials |
| `--log` | auto | Custom CSV log path |

---

## Deployment

```bash
# Persistent systemd service (AWS EC2)
sudo systemctl start 88meridian-spread

# Follow live output
sudo journalctl -fu 88meridian-spread
```

See [src/docs/SPREAD.md](src/docs/SPREAD.md) for full deployment guide, state schema,
and log format.

---

## Docs

| Doc | Contents |
|---|---|
| [src/docs/SPREAD.md](src/docs/SPREAD.md) | Bot architecture, state machine, deployment, CSV log format |
