# Lead/Lag Test Scaffold

This folder is for testing whether Roostoo quotes lead Binance quotes enough to matter.

## What it collects

`collect_lead_lag.py` writes one JSONL stream containing normalized quotes from:

- `roostoo`: `GET /v3/ticker`
- `binance`: top of book from the existing local-book stream

Each line includes:

- `source`
- `pair`
- `event_ts_ms`
- `recv_ts_ms`
- `bid`
- `ask`
- `mid`
- `spread`
- optional `last`, `quote_age_ms`, `sequence`, `meta`

## Collect data

```bash
python test_strat/collect_lead_lag.py \
  --pair BTC/USD \
  --binance-symbol BTCUSDT \
  --roostoo-poll-ms 200 \
  --speed 100ms \
  --levels 20 \
  --duration-sec 600
```

This writes to `data/lead_lag/` by default.

## Analyze data

```bash
python test_strat/analyze_lead_lag.py data/lead_lag/btc_usd-YYYYMMDD-HHMMSS.jsonl
```

Useful flags:

- `--grid-ms 50`
- `--horizons-ms 50,100,250,500,1000`
- `--max-stale-ms 1000`
- `--time-basis hybrid`

## How to interpret the output

You care about positive predictive structure, not just price differences.

- `correlation`: correlation between `roostoo_mid - binance_mid` and future Binance return
- `mean_return_edge_pos`: average future Binance move when Roostoo is above Binance
- `mean_return_edge_neg`: average future Binance move when Roostoo is below Binance
- `hit_rate`: fraction of samples where the sign of the edge matches the sign of the future Binance move

This is only the first pass. If you see signal, the next step is to model fees, slippage, stale quotes, and latency explicitly.
