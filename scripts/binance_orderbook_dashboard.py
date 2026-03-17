#!/usr/bin/env python3
"""Dash/Plotly web dashboard for live Binance order book monitoring."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from dash import Dash, Input, Output, dcc, html, dash_table
import plotly.graph_objects as go

from binance_orderbook_client import (
    DEFAULT_BASE_URL,
    MAX_SNAPSHOT_LIMIT,
    VALID_SPEEDS,
    StreamState,
    start_background_stream,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the web dashboard."""
    parser = argparse.ArgumentParser(
        description="Serve a local dashboard for live Binance order book data."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--levels", type=int, default=200)
    parser.add_argument("--speed", default="100ms", choices=VALID_SPEEDS)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--show-top", type=int, default=10)
    return parser.parse_args()


def fmt_number(value: float | None, decimals: int = 6) -> str:
    """Format numeric values for dashboard labels and tables."""
    if value is None:
        return "n/a"
    return f"{value:,.{decimals}f}".rstrip("0").rstrip(".")


def parse_rows(rows: list[list[str]], limit: int) -> list[dict[str, float]]:
    """Convert raw Binance book rows into typed values with cumulative totals."""
    parsed = []
    running_qty = 0.0
    running_notional = 0.0
    for price_str, qty_str in rows[:limit]:
        price = float(price_str)
        qty = float(qty_str)
        notional = price * qty
        running_qty += qty
        running_notional += notional
        parsed.append(
            {
                "price": price,
                "qty": qty,
                "notional": notional,
                "cum_qty": running_qty,
                "cum_notional": running_notional,
            }
        )
    return parsed


def build_depth_figure(asks: list[dict[str, float]], bids: list[dict[str, float]]) -> go.Figure:
    """Create the visible-depth bar chart."""
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            name="Bids",
            x=[row["price"] for row in bids],
            y=[row["qty"] for row in bids],
            marker_color="#1f9d55",
        )
    )
    figure.add_trace(
        go.Bar(
            name="Asks",
            x=[row["price"] for row in asks],
            y=[row["qty"] for row in asks],
            marker_color="#d64545",
        )
    )
    figure.update_layout(
        barmode="group",
        template="plotly_white",
        title="Visible Depth",
        xaxis_title="Price",
        yaxis_title="Quantity",
        margin=dict(l=30, r=20, t=50, b=40),
        legend=dict(orientation="h", y=1.05, x=0.0),
    )
    return figure


def build_cumulative_figure(asks: list[dict[str, float]], bids: list[dict[str, float]]) -> go.Figure:
    """Create the cumulative depth line chart."""
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            name="Bid Depth",
            x=[row["price"] for row in bids],
            y=[row["cum_qty"] for row in bids],
            mode="lines+markers",
            line=dict(color="#1f9d55", width=3),
        )
    )
    figure.add_trace(
        go.Scatter(
            name="Ask Depth",
            x=[row["price"] for row in asks],
            y=[row["cum_qty"] for row in asks],
            mode="lines+markers",
            line=dict(color="#d64545", width=3),
        )
    )
    figure.update_layout(
        template="plotly_white",
        title="Cumulative Depth",
        xaxis_title="Price",
        yaxis_title="Cumulative Quantity",
        margin=dict(l=30, r=20, t=50, b=40),
        legend=dict(orientation="h", y=1.05, x=0.0),
    )
    return figure


def build_table_rows(rows: list[dict[str, float]], side: str) -> list[dict[str, str]]:
    """Build DataTable-ready rows for one side of the book."""
    return [
        {
            "side": side,
            "price": fmt_number(row["price"], 8),
            "qty": fmt_number(row["qty"], 8),
            "notional": fmt_number(row["notional"], 2),
            "cum_qty": fmt_number(row["cum_qty"], 8),
        }
        for row in rows
    ]


def build_app(state: StreamState, show_top: int) -> Dash:
    """Construct the Dash app and its periodic refresh callback."""
    app = Dash(__name__)

    stat_box_style = {
        "padding": "12px 14px",
        "border": "1px solid #d0d7de",
        "borderRadius": "8px",
        "backgroundColor": "#ffffff",
        "minWidth": "180px",
    }

    app.layout = html.Div(
        style={
            "fontFamily": "Menlo, Monaco, monospace",
            "padding": "20px",
            "backgroundColor": "#f3f5f7",
            "minHeight": "100vh",
        },
        children=[
            html.H2("Binance Order Book Dashboard", style={"marginBottom": "4px"}),
            html.Div(
                id="subtitle",
                style={"color": "#57606a", "marginBottom": "16px"},
            ),
            html.Div(
                style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "16px"},
                children=[
                    html.Div([html.Div("Connection"), html.Strong(id="connection-stat")], style=stat_box_style),
                    html.Div([html.Div("Best Bid"), html.Strong(id="best-bid")], style=stat_box_style),
                    html.Div([html.Div("Best Ask"), html.Strong(id="best-ask")], style=stat_box_style),
                    html.Div([html.Div("Spread"), html.Strong(id="spread")], style=stat_box_style),
                    html.Div([html.Div("Mid"), html.Strong(id="mid")], style=stat_box_style),
                    html.Div([html.Div("Imbalance"), html.Strong(id="imbalance")], style=stat_box_style),
                ],
            ),
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "16px", "marginBottom": "16px"},
                children=[
                    dcc.Graph(id="depth-chart", config={"displayModeBar": False}),
                    dcc.Graph(id="cumulative-chart", config={"displayModeBar": False}),
                ],
            ),
            html.Div(
                style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "16px"},
                children=[
                    html.Div(
                        [
                            html.H4("Asks"),
                            dash_table.DataTable(
                                id="asks-table",
                                columns=[
                                    {"name": "Side", "id": "side"},
                                    {"name": "Price", "id": "price"},
                                    {"name": "Qty", "id": "qty"},
                                    {"name": "Notional", "id": "notional"},
                                    {"name": "Cum Qty", "id": "cum_qty"},
                                ],
                                style_table={"overflowX": "auto"},
                                style_cell={"textAlign": "right", "fontFamily": "Menlo, Monaco, monospace"},
                                style_header={"fontWeight": "bold"},
                                style_data_conditional=[
                                    {"if": {"filter_query": "{side} = ask"}, "color": "#b42318"},
                                ],
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.H4("Bids"),
                            dash_table.DataTable(
                                id="bids-table",
                                columns=[
                                    {"name": "Side", "id": "side"},
                                    {"name": "Price", "id": "price"},
                                    {"name": "Qty", "id": "qty"},
                                    {"name": "Notional", "id": "notional"},
                                    {"name": "Cum Qty", "id": "cum_qty"},
                                ],
                                style_table={"overflowX": "auto"},
                                style_cell={"textAlign": "right", "fontFamily": "Menlo, Monaco, monospace"},
                                style_header={"fontWeight": "bold"},
                                style_data_conditional=[
                                    {"if": {"filter_query": "{side} = bid"}, "color": "#067647"},
                                ],
                            ),
                        ]
                    ),
                ],
            ),
            dcc.Interval(id="refresh", interval=1000, n_intervals=0),
        ],
    )

    @app.callback(
        Output("subtitle", "children"),
        Output("connection-stat", "children"),
        Output("best-bid", "children"),
        Output("best-ask", "children"),
        Output("spread", "children"),
        Output("mid", "children"),
        Output("imbalance", "children"),
        Output("depth-chart", "figure"),
        Output("cumulative-chart", "figure"),
        Output("asks-table", "data"),
        Output("bids-table", "data"),
        Input("refresh", "n_intervals"),
    )
    def refresh_dashboard(_: int):
        snapshot = state.snapshot()
        payload = snapshot["latest_payload"]

        subtitle = (
            f"{snapshot['symbol'].upper()}  |  levels={snapshot['levels']}  |  speed={snapshot['speed']}  |  "
            f"endpoint={snapshot['base_url']}"
        )

        if not payload:
            connection_text = snapshot["latest_error"] or "connecting"
            empty_figure = go.Figure().update_layout(template="plotly_white", margin=dict(l=30, r=20, t=30, b=30))
            return (
                subtitle,
                connection_text,
                "n/a",
                "n/a",
                "n/a",
                "n/a",
                "n/a",
                empty_figure,
                empty_figure,
                [],
                [],
            )

        asks = parse_rows(payload.get("asks", []), show_top)
        bids = parse_rows(payload.get("bids", []), show_top)
        best_ask = asks[0]["price"] if asks else None
        best_bid = bids[0]["price"] if bids else None
        spread = (best_ask - best_bid) if best_ask is not None and best_bid is not None else None
        mid = ((best_ask + best_bid) / 2.0) if spread is not None else None
        ask_total = sum(row["qty"] for row in asks)
        bid_total = sum(row["qty"] for row in bids)
        imbalance = (bid_total - ask_total) / (bid_total + ask_total) if (bid_total + ask_total) else 0.0
        updated_at = snapshot["updated_at"]
        updated_label = (
            datetime.fromtimestamp(updated_at, tz=timezone.utc).isoformat(timespec="seconds")
            if updated_at
            else "n/a"
        )
        connection_text = f"{'connected' if snapshot['connected'] else 'disconnected'}  |  updated={updated_label}"

        return (
            subtitle,
            connection_text,
            fmt_number(best_bid, 8),
            fmt_number(best_ask, 8),
            fmt_number(spread, 8),
            fmt_number(mid, 8),
            f"{imbalance:.2%}",
            build_depth_figure(asks, bids),
            build_cumulative_figure(asks, bids),
            build_table_rows(asks, "ask"),
            build_table_rows(bids, "bid"),
        )

    return app


def main() -> int:
    """Run the local web dashboard server."""
    args = parse_args()
    args.levels = max(1, min(args.levels, MAX_SNAPSHOT_LIMIT))
    state = StreamState(
        symbol=args.symbol,
        base_url=args.base_url,
        levels=args.levels,
        speed=args.speed,
    )
    start_background_stream(state)
    app = build_app(state, args.show_top)
    app.run(host=args.host, port=args.port, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
