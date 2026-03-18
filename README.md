# Submission Repository

This repository is intentionally limited to the competition submission package.

The submission is a two-sleeve long-only crypto strategy designed for short evaluation windows while staying inside the spot-only, one-exchange constraint set.

The portfolio is split equally between:

- an `ETHUSDT` weekly-volatility pullback sleeve
- an `ADAUSDT` / `DOGEUSDT` 5-minute lead-lag sleeve

The weekly-vol sleeve provides slower directional exposure during bullish `ETH` regimes. The lead-lag sleeve provides higher trade frequency by reacting to short-term moves in a `BTC/ETH/SOL` leader basket when `ADA` or `DOGE` underreact.

The submission code lives in [src](/Users/damianeng/repos/88_street/src), with the main write-up in:

- [src/docs/README.md](/Users/damianeng/repos/88_street/src/docs/README.md)
- [src/docs/STRATEGY.md](/Users/damianeng/repos/88_street/src/docs/STRATEGY.md)
- [src/docs/VALIDATION.md](/Users/damianeng/repos/88_street/src/docs/VALIDATION.md)
- [src/docs/LIVE.md](/Users/damianeng/repos/88_street/src/docs/LIVE.md)

Main commands:

```bash
.venv/bin/python -m src.strategy
.venv/bin/python -m src.live_bot --run-once
.venv/bin/python -m src.live_bot --live
```

The latest validated offline result from the packaged evaluator is:

- stitched out-of-sample return: `+43.71%`
- mean weekly return: `+0.81%`
- positive weeks: `55.32%`
- trade weeks: `93.62%`
