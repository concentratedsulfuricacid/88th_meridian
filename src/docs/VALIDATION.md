# Validation

## Walk-Forward Setup

The locked validation result for the submission uses:

- portfolio: `50%` `ETHUSDT` weekly-vol sleeve + `50%` `ADAUSDT` / `DOGEUSDT` lead-lag sleeve
- warmup history before each test window: `90` calendar days
- test window length: `7` days
- step size: `7` days
- independent out-of-sample windows: `47`

This produces a stitched weekly walk-forward evaluation rather than a single in-sample backtest.

## Main Result

- stitched out-of-sample return: `+43.71%`
- mean weekly return: `+0.81%`
- median weekly return: `+0.30%`
- positive weeks: `55.32%`
- weeks with at least one trade: `93.62%`
- mean trades per week: `4.06`
- best week: `+9.39%`
- worst week: `-4.73%`

These numbers are the locked headline metrics for the packaged submission evaluator.

## Benchmark Comparison

On the same out-of-sample span:

- `ETH` buy and hold: `+1.86%`
- equal-weight buy and hold `ETH + ADA + DOGE`: `-34.30%`

The submission outperformed both passive benchmarks on the same walk-forward sample.

## Integrity Notes

The validation write-up is based on the leak-clean version of the strategy:

- the lead-lag beta is causal and shifted by one bar
- entries are formed on completed bars and executed on the next bar
- the stronger in-sample touch-entry idea was rejected because it failed the weekly walk-forward test

## Competition Framing

The competition is scored over a short window. That is why the portfolio matters:

- the weekly-vol sleeve contributes stronger directional return when `ETH` is trending
- the lead-lag sleeve contributes much higher short-window trade presence

The combined portfolio was preferred because it preserved edge while remaining active in most evaluation windows.
