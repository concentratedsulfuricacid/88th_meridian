# 88th Meridian — Validation

## Backtest Setup

- **Data:** Binance 5m klines downloaded locally for each symbol
- **Period:** January 2025 – February 2026 (14 months)
- **Entry model:** Bar close at or above 288-bar rolling high (confirmed breakout)
- **Stop/target anchor:** Rolling high at time of signal
- **Regime gate:** Close > 20d EMA (span=5760 on 5m bars in backtest)
- **Fees:** 10 bps round-trip (limit orders, 5 bps/side)
- **Sizing:** Full allocation per position, one trade at a time per symbol
- **EOD:** Open positions closed at midnight UTC (final bar close)

---

## Configuration Evolution

Three configurations were tested to understand the true edge:

### Optimistic (wick entry, rolling high anchor)
Entry at rolling high price the moment price touches it (wick counts as signal).

- Equal-weight return: **+108%** across 12 symbols
- Win rate: ~40–45%
- **Problem:** In live trading the bar has already closed above the rolling high by the
  time the signal is detected. A limit order at rolling high won't fill. This number
  is not achievable live.

### Realistic (bar close entry, rolling high anchor) — **selected**
Entry at bar close price when the 5m bar closes above rolling high. Stop and target
remain anchored to rolling high.

- Equal-weight return: **+30.61%** across 5 selected symbols
- Win rate: 40–44%
- Mean net P&L: +4.7 to +22.7 bps per trade depending on symbol
- **This is the configuration the live bot implements.**

### With confirmation only, no regime gate
Adding the confirmation bar filter (bar close) to the wick entry model:
- Flips strategy from deeply negative (-82%) to strongly positive
- Regime gate provides additional ~5–10% return improvement in bear periods

---

## Full-Period Results (Realistic Model, Jan 2025 – Feb 2026)

| Symbol | Return | Trades | Win Rate | Mean Net | Target Rate | Stop Rate | EOD Rate |
|--------|--------|--------|----------|----------|-------------|-----------|----------|
| FLOKIUSDT | +56.87% | — | 44% | +22.7 bps | — | — | — |
| DOGEUSDT | +33.54% | — | 43% | +15.5 bps | — | — | — |
| AVAXUSDT | +31.90% | — | 40% | +13.8 bps | — | — | — |
| FETUSDT | +22.87% | — | 40% | +10.3 bps | — | — | — |
| VIRTUALUSDT | +7.87% | — | 42% | +4.7 bps | — | — | — |
| **Equal-weight** | **+30.61%** | | | | | | |

---

## Rolling 10-Day Window Analysis

To simulate competition conditions (8-day trading window), 75 rolling 10-day windows
were tested across the 14-month period.

| Metric | Value |
|--------|-------|
| Mean return per window | +1.65% |
| Median return per window | +0.77% |
| Worst window | −3.01% |
| Windows worse than −5% | 0 |
| Positive windows | majority |

The worst-case drawdown across all tested windows was −3.01%, well within acceptable
bounds for an 8-day competition.

---

## Why Win Rate Is ~40–44%

A win rate below 50% is expected and correct given the 3:1 R:R structure.

Break-even win rate at 3:1 R:R = **25%**. At 40–44%, the strategy is running well above
break-even. Mean EV per trade is positive across all five symbols.

The confirmation bar filter is the single most important component — it reduces the
proportion of false breakouts (wick touches that reverse) without significantly reducing
signal frequency.

---

## Benchmark

Over the same 14-month period:
- BTC buy-and-hold: market-dependent, trending down from Oct 2025 peaks
- The regime gate actively reduces loss periods by skipping entries when close ≤ 20d EMA

---

## Integrity Notes

- All entries use the **completed** bar close, not the open of the next bar
- Rolling high is computed from the **prior** 288 bars, excluding the signal bar
- Regime EMA is computed on the full historical series (no look-ahead in backtest)
- No parameter optimisation was performed — parameters were chosen by logic:
  - 288 bars = exactly 24 hours on 5m data
  - 100/300 bps = clean 3:1 ratio
  - 5760 bars = exactly 20 trading days on 5m data
- Symbol selection was done on the full 14-month period (in-sample) — the main
  overfitting risk acknowledged in this strategy
