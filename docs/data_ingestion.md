The pipeline is simple: websocket ingest, shared state, then one or more viewers.

It starts in [scripts/binance_orderbook_client.py](/Users/damianeng/repos/trading_comeptition/scripts/binance_orderbook_client.py). `build_stream_url()` builds the Binance partial-depth websocket URL, `stream_orderbook()` connects and reads JSON messages in a loop, and `start_background_stream()` runs that loop in a daemon thread. Every incoming payload is written into `StreamState`, which is just the latest snapshot plus connection metadata protected by a lock.

From there, each frontend pulls from that shared state rather than owning the websocket itself. The terminal app in [scripts/binance_orderbook_stream.py](/Users/damianeng/repos/trading_comeptition/scripts/binance_orderbook_stream.py) is the direct path: it opens the websocket, parses each message, optionally persists raw JSONL, then renders bids/asks into a text view immediately. That path is basically `websocket -> JSON payload -> formatted rows -> terminal`.

The two dashboards use the shared client. In [scripts/binance_orderbook_dashboard.py](/Users/damianeng/repos/trading_comeptition/scripts/binance_orderbook_dashboard.py), the Dash callback polls `state.snapshot()` every second, converts raw `asks` and `bids` strings into floats with `parse_rows()`, computes derived metrics like spread, mid, cumulative depth, and imbalance, then feeds those into Plotly figures and tables. So that path is `websocket thread -> StreamState -> periodic callback -> parsed rows -> charts/tables`.

The native app in [scripts/binance_orderbook_pyqtgraph.py](/Users/damianeng/repos/trading_comeptition/scripts/binance_orderbook_pyqtgraph.py) adds one extra layer: history. `refresh_view()` reads the newest `StreamState`, converts the payload into a normalized snapshot via `build_snapshot()`, and appends it to a rolling `deque`. Then `_refresh_plots()` turns that history buffer into time-series plot data, and `_render_selected_snapshot()` drives the side tables and stats for either the latest or hovered snapshot. So that path is `websocket thread -> StreamState -> normalized snapshot history -> historical plots + synced detail panels`.

The raw Binance payload you’re consuming is partial depth data: top `5`, `10`, or `20` bids/asks depending on your `--levels` flag. Each message contains arrays like `bids: [[price, qty], ...]` and `asks: [[price, qty], ...]`. Everything downstream is derived from that:
- `best_bid` = first bid price
- `best_ask` = first ask price
- `spread` = `best_ask - best_bid`
- `mid` = `(best_ask + best_bid) / 2`
- `cum_qty` = running sum down each side
- `notional` = `price * qty`
- `imbalance` = `(bid_total - ask_total) / (bid_total + ask_total)`

So conceptually the whole system is:

```text
Binance websocket
-> raw JSON payload
-> parse numeric levels
-> derive book metrics
-> either:
   -> terminal render
   -> Dash charts/tables
   -> pyqtgraph history + hover inspection
```

If you want, I can also draw this as a small mermaid diagram and point out exactly where to insert trade data, persistence, or signal logic.