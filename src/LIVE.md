# Live Execution

This package now includes a live execution path against the Roostoo API documents:

- docs repo: `https://github.com/roostoo/Roostoo-API-Documents`
- default base URL used here: `https://mock-api.roostoo.com`

## What the live bot does

- uses Binance REST klines to build completed `4h` and `5m` bars
- uses Roostoo REST for balances, ticker snapshots, exchange metadata, and order placement
- preserves the validated strategy logic instead of inventing new live-only rules

## Endpoints used

- `GET /v3/serverTime`
- `GET /v3/exchangeInfo`
- `GET /v3/ticker`
- `GET /v3/balance`
- `POST /v3/place_order`
- `POST /v3/query_order`
- `POST /v3/cancel_order`

Signed requests use:

- `RST-API-KEY`
- `MSG-SIGNATURE`
- `timestamp`

The client signs the sorted form-encoded payload with `HMAC-SHA256`, matching the published Roostoo request format.

## Environment

You can place these in `src/.env` or export them in the shell:

```dotenv
ROOSTOO_BASE_URL=https://mock-api.roostoo.com
ROOSTOO_API_KEY=replace-me
ROOSTOO_API_SECRET=replace-me
ROOSTOO_TIMEOUT_SECONDS=30
BINANCE_BASE_URL=https://api.binance.com
POLLING_SECONDS=60
SUBMISSION_STATE_PATH=src/state/live_state.json
LIVE_TRADING=false
```

## Commands

Dry run one cycle:

```bash
.venv/bin/python -m src.live_bot --run-once
```

Continuous dry run:

```bash
.venv/bin/python -m src.live_bot
```

Actual live trading:

```bash
.venv/bin/python -m src.live_bot --live
```

## Important caveats

- the request-signing code is tested locally, but the bot has not been verified against your actual competition credentials from this workspace
- market data comes from Binance and execution comes from Roostoo, so live fills can differ from the backtest path
- the bot intentionally trades only the validated symbols: `ETH`, `ADA`, and `DOGE`
