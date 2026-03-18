# Submission 50/50 Blend

This folder is the self-contained submission package for the validated competition candidate.

It contains two sleeves:

- `50%` `ETHUSDT` weekly-volatility pullback
- `50%` `ADAUSDT` / `DOGEUSDT` 5-minute lead-lag

The package now has both:

- an offline evaluator in `strategy.py`
- a live execution path in `live_bot.py` that uses Binance for market data and the Roostoo API for execution
- a local `src/` folder with the runtime helpers, so the submission folder can be deployed by itself

## Files

- `strategy.py`: exact validated backtest wrapper for the submission candidate
- `src/`: local signal math, CSV loaders, and walk-forward helpers
- `roostoo_client.py`: signed Roostoo REST client
- `live_bot.py`: polling live bot that maps the validated rules into Roostoo market orders
- `live_config.py`: environment-driven live config
- `STRATEGY.md`: exact rule sheet
- `VALIDATION.md`: out-of-sample and benchmark summary
- `LIVE.md`: Roostoo API integration notes

## Traded coins

- weekly-vol sleeve trades `ETHUSDT`
- lead-lag sleeve trades `ADAUSDT` and `DOGEUSDT`
- `BTCUSDT`, `ETHUSDT`, and `SOLUSDT` are also used as leaders for the lead-lag signal

## Run

Backtest wrapper:

```bash
.venv/bin/python -m src.strategy
```

Live dry run against the Roostoo API:

```bash
.venv/bin/python -m src.live_bot --run-once
```

Actual live trading:

```bash
.venv/bin/python -m src.live_bot --live
```

Use `.env` in this folder or shell env vars for `ROOSTOO_API_KEY` and `ROOSTOO_API_SECRET`. See `LIVE.md` for the exact variables.
