# Execution Assumptions

The live implementation preserves the validated strategy logic and maps it into the Roostoo API environment.

## Market Data and Execution Split

The submission uses:

- Binance REST klines for completed `4h` and `5m` bars
- Roostoo REST for balances, ticker snapshots, exchange metadata, and order execution

That means the signal engine and the execution venue are linked but not identical. The design assumes that Binance bar structure is a reasonable signal source while Roostoo is the execution surface required by the competition.

## API Surface

The Roostoo integration uses the documented endpoints:

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

The client signs the sorted form-encoded payload with `HMAC-SHA256`.

## Credential Modes

The bot supports two operating modes:

- default mode for practice or staging credentials
- competition mode for the competition account credentials

The separation is deliberate. It allows the exact same bot logic to be used against two different Roostoo key sets without editing the strategy code.

## State Separation

State is separated by mode by default:

- default state: `src/state/live_state.json`
- competition state: `src/state/competition_live_state.json`

This matters because the bot maintains sleeve state such as open positions, last processed bar timestamps, and expected hold windows. Competition mode should not inherit practice-state assumptions accidentally.

## Execution Behavior

The live bot intentionally keeps the execution model simple:

- weekly-vol sleeve trades `ETH/USD`
- lead-lag sleeve trades `ADA/USD` or `DOGE/USD`
- orders are market orders
- sizing is sleeve-based rather than volatility-budgeted at runtime

This matches the backtested portfolio design more closely than introducing live-only discretionary overrides.

## Practical Caveats

- request-signing is tested locally
- the live path has been authenticated against the Roostoo mock environment
- the strategy still depends on the assumption that Binance-derived signals remain useful when executed through Roostoo
- the bot intentionally limits itself to the validated symbols `ETH`, `ADA`, and `DOGE`
