# Strategy

This is the exact competition candidate currently packaged for submission.

## Portfolio

- start in `USDT`
- allocate `50%` of equity to the weekly-vol sleeve
- allocate `50%` of equity to the lead-lag sleeve
- sleeves trade independently
- inactive sleeve capital stays in cash

## Sleeve 1: ETH weekly-vol pullback

Market:

- `ETHUSDT`
- spot only
- long only

Data:

- official Binance `1h` spot klines
- resampled into completed `4h` bars

Regime filter:

- `close > EMA100`
- `EMA20 > EMA100`

Setup:

- compute rolling weekly volatility from the last `42` completed `4h` returns
- compute recent swing high from the last `5` completed bars
- wait for a pullback from that swing high of at least `0.25 * weekly_vol_move`

Entry:

- buy at the next `4h` bar open

Exit:

- stop loss at `1.0 * weekly_vol_move` below entry
- take profit at `1.25 * weekly_vol_move` above entry
- time stop after `42` bars
- regime exit if `close <= EMA100`

Fees used in validation:

- `10 bps` round trip

## Sleeve 2: 5m lead-lag

Leaders:

- `BTCUSDT`
- `ETHUSDT`
- `SOLUSDT`

Tradeable laggers:

- `ADAUSDT`
- `DOGEUSDT`

Data:

- official Binance `5m` spot klines

Signal:

- compute leader basket return over the last `3` bars, which is `15m`
- compute lagger return over the same `15m`
- estimate lagger beta to the leader basket using expanding covariance / variance
- shift beta by one bar so the estimate is causal
- define gap:
  - `gap = leader_return - beta * lagger_return`

Entry filter:

- leader basket return must be greater than `0.45%`
- lag gap must be greater than `0.30%`
- if both laggers qualify, take the one with the larger gap

Entry:

- buy at the next `5m` bar open
- one lagger position at a time

Exit:

- hold for `12` bars, which is `60m`
- exit at the next bar open

Fees used in validation:

- `10 bps` round trip

## Live translation

The live bot keeps the same portfolio structure:

- weekly-vol sleeve sends market orders in `ETH/USD`
- lead-lag sleeve sends market orders in `ADA/USD` or `DOGE/USD`
- Roostoo execution is used for orders and balances
- Binance market data is used to build the completed bars that drive the signals

The live implementation does not retune the strategy. It only maps the validated logic into API calls.
