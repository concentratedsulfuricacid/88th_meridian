# Strategy

## Portfolio Thesis

The submission combines two different sources of edge so that it can still act inside a short competition window:

- a slower directional sleeve that captures medium-horizon `ETH` trend continuation after volatility-scaled pullbacks
- a faster relative-value sleeve that captures short-term catch-up moves in liquid alts after the major leaders move first

Both sleeves are:

- long-only
- spot-only
- designed around a single-exchange execution model

The portfolio starts in `USDT` and splits capital equally across the two sleeves. If one sleeve is inactive, that capital remains in cash instead of being forced into another position.

## Sleeve 1: ETH Weekly-Volatility Pullback

This sleeve trades `ETHUSDT` on completed `4h` bars built from Binance `1h` spot data.

The core idea is that bullish `ETH` regimes tend to reward controlled pullback entries better than fixed-percentage dip buying. Instead of using a static distance, the strategy scales its pullback threshold, stop, and target using a rolling weekly volatility estimate. That makes the entry distance adaptive to quiet and volatile regimes.

The regime filter is:

- `close > EMA100`
- `EMA20 > EMA100`

Once the regime is bullish, the sleeve computes:

- weekly volatility from the last `42` completed `4h` returns
- a recent swing high from the last `5` completed bars

It then waits for price to retrace by at least `0.25 * weekly_vol_move` from that recent high. Entry is at the next `4h` bar open. The exit structure is volatility-scaled as well:

- stop loss at `1.0 * weekly_vol_move` below entry
- take profit at `1.25 * weekly_vol_move` above entry
- time stop after `42` bars
- regime exit if `close <= EMA100`

This sleeve is intentionally simple: trend filter, adaptive pullback threshold, adaptive exits.

## Sleeve 2: 5-Minute Lead-Lag

This sleeve uses a leader basket of:

- `BTCUSDT`
- `ETHUSDT`
- `SOLUSDT`

and trades whichever of these laggers is more attractive:

- `ADAUSDT`
- `DOGEUSDT`

The idea is that the leaders tend to incorporate new information first, while selected altcoins can lag briefly before catching up. The strategy does not buy alts just because they are up; it buys them when they are still behind their expected move relative to the leader basket.

Every `5` minutes, the sleeve computes:

- leader basket return over the last `3` bars, which is `15m`
- lagger return over the same `15m`
- a causal beta estimate between each lagger and the leader basket

The beta is estimated using expanding covariance and variance, then shifted by one bar so the signal uses only past information. The gap score is:

- `gap = leader_return - beta * lagger_return`

The sleeve only enters if:

- leader basket return is greater than `0.45%`
- lag gap is greater than `0.30%`

If both laggers qualify, it takes the one with the larger gap. Entry is at the next `5m` bar open. Exit is deliberately simple: hold for `12` bars, or `60` minutes, then exit at the next bar open.

## Why This Combination

The weekly-vol sleeve and the lead-lag sleeve solve different problems:

- the weekly-vol sleeve gives stronger average return per trade but lower event frequency
- the lead-lag sleeve gives much better short-window opportunity frequency

Together they create a portfolio that is more suitable for a fixed short competition window than either sleeve on its own.

## Fee Model

All validation numbers for the current locked submission use:

- `10 bps` round trip

## Live Execution Mapping

The live implementation keeps the same signal logic and maps it into Roostoo spot execution:

- weekly-vol sleeve trades `ETH/USD`
- lead-lag sleeve trades `ADA/USD` or `DOGE/USD`
- Binance market data builds the completed bars
- Roostoo provides balances, ticker snapshots, exchange metadata, and order execution
