# Validation

## Main walk-forward result

Validated package result for the current candidate:

- portfolio: `50%` `ETHUSDT` weekly-vol sleeve + `50%` `ADAUSDT` / `DOGEUSDT` lead-lag sleeve
- walk-forward windowing: `90` day warmup, `7` day test windows, `7` day step
- independent out-of-sample windows: `47`

Result:

- stitched out-of-sample return: `+43.71%`
- mean weekly return: `+0.81%`
- median weekly return: `+0.30%`
- positive weeks: `55.32%`
- weeks with at least one trade: `93.62%`
- mean trades per week: `4.06`
- worst week: `-4.73%`

## Benchmarks on the same OOS window

- `ETH` buy and hold: `+1.86%`
- equal-weight buy and hold `ETH + ADA + DOGE`: `-34.30%`

So the packaged strategy beat both passive comparisons on the same test span.

## Integrity notes

- the lead-lag beta is causal and shifted by one bar to avoid lookahead
- entries are generated from completed bars and executed on the next bar
- the stronger in-sample touch-entry variant was rejected because it failed the rolling OOS test

## Competition note

The competition window is `8` trading days. This package keeps the validated `7` day rolling OOS harness as the default because those are the numbers already locked in from the research process. If you want an `8` day proxy run, use:

```bash
.venv/bin/python -m src.strategy --test-days 8 --step-days 8
```
