# 88th Meridian

A confirmed intraday breakout strategy built for the Roostoo trading competition.
Long-only, spot-only, single exchange.

---

## Strategy

88th Meridian enters when a 5-minute bar **closes** above the prior 24-hour rolling high,
with a regime gate that blocks entries in downtrends. Stop and target are anchored to the
rolling high at signal time — not the entry fill price — preserving a clean 3:1 R:R
regardless of entry slippage.

| Parameter | Value |
|-----------|-------|
| Lookback | 288 × 5m bars (24 hours) |
| Confirmation | Bar close ≥ rolling high |
| Regime gate | Close > 20-day EMA |
| Stop | −100 bps from rolling high |
| Target | +300 bps from rolling high |
| EOD exit | Midnight UTC |
| Fees | 10 bps round-trip (limit orders) |

**Symbols:** FLOKIUSDT · DOGEUSDT · AVAXUSDT · FETUSDT · VIRTUALUSDT

**Sizing:** Equal weight — 20% per symbol slot.

---

## Backtest Results

Tested on 14 months of Binance 5m data (Jan 2025 – Feb 2026), realistic bar-close entry model.

| Symbol | Return |
|--------|--------|
| FLOKIUSDT | +56.87% |
| DOGEUSDT | +33.54% |
| AVAXUSDT | +31.90% |
| FETUSDT | +22.87% |
| VIRTUALUSDT | +7.87% |
| **Equal-weight** | **+30.61%** |

Rolling 10-day window analysis (75 windows): mean +1.65%, median +0.77%, worst −3.01%, zero windows below −5%.

---

## Running the Bot

```bash
# Install dependencies
pip install -r src/requirements-live.txt

# Run bot (competition mode, live orders)
python3 -m src.live_bot --competition --live

# Run monitoring dashboard on port 8080
python3 -m src.dashboard --competition --port 8080

# Dry run (no orders placed)
python3 -m src.live_bot --competition --run-once
```

---

## Deployment (AWS EC2 + SSM)

Run as systemd services so the bot persists after the terminal session closes.
See [src/docs/LIVE.md](src/docs/LIVE.md) for full systemd setup and EC2 security group configuration.

---

## Docs

- [Strategy](src/docs/STRATEGY.md) — signal logic, symbol selection, what we rejected
- [Validation](src/docs/VALIDATION.md) — backtest results, rolling windows, integrity notes
- [Live Execution](src/docs/LIVE.md) — bot architecture, entry/exit flow, deployment

---

## Research

Backtesting code lives in [`codex_spot_lab/`](codex_spot_lab/):

```bash
# Reproduce the main backtest
python3 -m codex_spot_lab.breakout_backtest \
  --symbols FLOKIUSDT DOGEUSDT AVAXUSDT FETUSDT VIRTUALUSDT \
  --n 288 --stop-bps 100 --target-bps 300 --confirm --regime-ema 5760
```
