# 88th Meridian — Strategy

## Overview

88th Meridian is a confirmed intraday breakout strategy executed across five liquid crypto
assets on the Roostoo spot competition account. It is long-only, spot-only, and targets a
single source of edge: price breaking above the prior 24-hour high with confirmation and
genuine participation, in a trending market regime.

---

## Signal Logic

**Timeframe:** 5-minute bars fetched from Binance REST klines at runtime.

**Rolling high:** Maximum high across the prior 288 completed 5m bars (24 hours),
excluding the signal bar itself.

**Entry requires all three conditions to be true simultaneously:**

1. **Close confirmation** — the last completed 5m bar closes at or above the rolling high.
   A wick touch alone does not trigger. This is the most important filter: requiring the
   bar *close* to exceed the rolling high materially reduces false breakouts.

2. **Volume confirmation** — the breakout bar's volume must be at least 1.5× the 20-bar
   trailing average volume. Low-volume breakouts are disproportionately fakeouts in
   ranging, thin markets.

3. **Regime gate** — the asset's close must be above its 20-day EMA, computed from
   480 × 1h bars (EWM span=480). This skips entries during sustained downtrends.
   Using hourly bars ensures the EMA is fully warmed up in a single API call at runtime.

---

## Trade Structure

All levels are anchored to `rolling_high`, not the entry fill price. This means the
live risk-reward matches the backtest regardless of how far above rolling high price
is trading at the moment the signal is detected.

| Level | Calculation |
|---|---|
| Entry | Current Roostoo ticker price (limit order, fills immediately) |
| Stop | `rolling_high × (1 − 0.0100)` — 100 bps below rolling high |
| Target | `rolling_high × (1 + 0.0300)` — 300 bps above rolling high |
| EOD exit | Any open position at midnight UTC closes at current market price |

**Risk-reward: 3:1.** Break-even win rate at 3:1 is 25%. The strategy runs at 40–44%.

The target is placed as a resting limit sell order immediately after the entry fill.
Roostoo fills it automatically when price touches the target level — no additional bot
action is needed.

---

## Symbol Universe

Five symbols selected from the Roostoo competition universe after backtesting under
the realistic entry model (bar-close entry, rolling-high anchor, 10 bps round-trip fees,
regime gate active, 14 months of data):

| Symbol | Return | Win Rate | Mean Net |
|---|---|---|---|
| FLOKIUSDT | +56.87% | 44% | +22.7 bps/trade |
| DOGEUSDT | +33.54% | 43% | +15.5 bps/trade |
| AVAXUSDT | +31.90% | 40% | +13.8 bps/trade |
| FETUSDT | +22.87% | 40% | +10.3 bps/trade |
| VIRTUALUSDT | +7.87% | 42% | +4.7 bps/trade |

**Equal-weight: +30.61% over 14 months (Jan 2025 – Feb 2026), 5/5 positive.**

Selection criteria: positive return under the realistic entry model, sufficient trade
frequency (no symbols with fewer than ~20 trades in 14 months), and edge confirmed with
both the confirmation bar filter and regime gate active.

---

## Fee Model

All orders are placed as limit orders to pay the maker/taker rate, not the market order rate.

| Order type | Fee per side | Round-trip |
|---|---|---|
| Limit | 5 bps | 10 bps |

The strategy does not survive market order fees (20 bps round-trip). All backtesting
and live execution uses limit orders only.

---

## Position Sizing

Capital is split equally across all five symbols: 20% per slot.

```
allocation_usd = total_equity / 5
qty = floor(min(allocation_usd, free_usd_balance) / current_price)
```

One position per symbol at a time. A symbol cannot enter a new trade while it carries
an active position. Equity is computed from live Roostoo balances (cash + positions
marked to Roostoo ticker prices) at the start of each poll cycle.
