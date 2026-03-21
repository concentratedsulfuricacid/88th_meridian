# 88th Meridian — Strategy

## Overview

Apex is a confirmed intraday breakout strategy executed across five liquid crypto assets
on the Roostoo spot competition account. It is long-only, spot-only, and targets a
single source of edge: price breaking above the prior 24-hour high with confirmation,
in a trending market regime.

The strategy was developed through iterative backtesting after rejecting an asymmetric
grid approach that was structurally broken due to an inverted risk-reward ratio.

---

## What We Rejected First

Before landing on Apex, we backtested an asymmetric intraday grid strategy:

- Daily anchor = UTC day open
- Three limit buy ladders below anchor at -0.5%, -1.0%, -1.5%
- Pre-paired TP sells at anchor, anchor-0.3%, anchor-0.5%
- Hard stop at -3.5% from anchor

**Result: -14.19% equal-weight across 12 symbols, 1/12 positive.**

The reason was structural: TP hits produced ~60 bps profit, but stop hits produced
~2,700 bps loss. With a 78% TP rate and 20% stop rate, EV per trade was -493 bps.
No filter or parameter change could fix an inverted R:R.

---

## The Apex Edge

The core observation: assets that break above their prior 24-hour high with a confirmed
bar close (not just a wick) tend to continue in that direction intraday.

Breakouts confirmed by a closing price above the rolling high have a materially lower
false breakout rate than wick-only entries. This single filter — requiring the bar
**close** to exceed the rolling high — flipped the strategy from deeply negative to
consistently profitable across the tested universe.

---

## Signal Logic

**Timeframe:** 5-minute bars from Binance REST klines.

**Rolling high:** Maximum high across the prior 288 completed bars (24 hours of 5m bars),
excluding the signal bar itself.

**Entry condition:** The last completed 5m bar closes at or above the rolling high.

**Regime gate:** Entry is skipped if the asset's close is at or below its 20-day EMA.
The EMA is computed from 480 × 1h bars (20 days) to ensure full convergence in a single
API call at runtime. This prevents entries in sustained downtrends.

---

## Trade Structure

All levels are anchored to `rolling_high`, not the entry fill price. This ensures the
live risk-reward matches what was backtested regardless of entry slippage.

| Level | Calculation | Value |
|-------|-------------|-------|
| Entry | Current market price (limit order at time of signal) | — |
| Stop | `rolling_high × (1 − 100 bps)` | −1% from rolling high |
| Target | `rolling_high × (1 + 300 bps)` | +3% from rolling high |
| EOD exit | Any open position at midnight UTC closes at current price | — |

**Risk-reward: 3:1** (target 300 bps, stop 100 bps).

---

## Symbol Universe

Five symbols were selected after testing the full 66-asset Roostoo competition universe
under realistic entry assumptions (bar close entry, rolling high anchored stop/target,
10 bps round-trip fees, regime gate active).

| Symbol | Backtest Return | Win Rate | Mean Net P&L |
|--------|----------------|----------|--------------|
| FLOKIUSDT | +56.87% | 44% | +22.7 bps/trade |
| DOGEUSDT | +33.54% | 43% | +15.5 bps/trade |
| AVAXUSDT | +31.90% | 40% | +13.8 bps/trade |
| FETUSDT | +22.87% | 40% | +10.3 bps/trade |
| VIRTUALUSDT | +7.87% | 42% | +4.7 bps/trade |

**Equal-weight portfolio: +30.61% over 14 months (Jan 2025 – Feb 2026), 5/5 positive.**

Selection criteria: positive return under the realistic entry model, sufficient trade
frequency, and confirmed edge with both the confirmation bar filter and regime gate active.

---

## Fee Model

All backtest and live numbers use limit orders only.

| Order type | Fee per side | Round-trip |
|------------|-------------|------------|
| Limit | 5 bps | 10 bps |

Market orders (20 bps round-trip) were explicitly rejected after verifying that the edge
does not survive the higher fee load.

---

## Position Sizing

Capital is split equally across all five symbols: 20% per slot. Allocation is computed
from total portfolio equity (cash + open positions marked to market) at the start of each
poll cycle.

One position per symbol at a time. A symbol cannot enter a new trade while it has an
active position.
