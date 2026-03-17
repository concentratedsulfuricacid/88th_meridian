#!/usr/bin/env python3
"""Native pyqtgraph dashboard for exploring live Binance order book history."""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import EngineConfig, LiveEngineProcessor, PaperTrader, PaperTraderConfig, SimpleTradingEngine

from binance_orderbook_client import (
    DEFAULT_BASE_URL,
    MAX_SNAPSHOT_LIMIT,
    VALID_SPEEDS,
    StreamState,
    start_background_stream,
)


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the pyqtgraph desktop dashboard."""
    parser = argparse.ArgumentParser(
        description="Run a native pyqtgraph dashboard for live Binance order book data."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--levels", type=int, default=200)
    parser.add_argument("--speed", default="100ms", choices=VALID_SPEEDS)
    parser.add_argument("--show-top", type=int, default=10)
    parser.add_argument("--refresh-ms", type=int, default=250)
    parser.add_argument("--history-size", type=int, default=600)
    return parser.parse_args()


def fmt_number(value: float | None, decimals: int = 8) -> str:
    """Format floats for compact display in labels, plots, and tables."""
    if value is None:
        return "n/a"
    return f"{value:,.{decimals}f}".rstrip("0").rstrip(".")


def iso_utc(timestamp: float | None) -> str:
    """Convert a Unix timestamp to an ISO-8601 UTC string."""
    if timestamp is None:
        return "n/a"
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def parse_rows(rows: list[list[str]], limit: int) -> list[dict[str, float]]:
    """Convert raw Binance levels into numeric rows with cumulative depth."""
    parsed_rows: list[dict[str, float]] = []
    running_qty = 0.0

    for price_str, qty_str in rows[:limit]:
        price = float(price_str)
        qty = float(qty_str)
        running_qty += qty
        parsed_rows.append(
            {
                "price": price,
                "qty": qty,
                "notional": price * qty,
                "cum_qty": running_qty,
            }
        )

    return parsed_rows


def build_snapshot(payload: dict[str, Any], updated_at: float | None, show_top: int) -> dict[str, Any] | None:
    """Create a normalized snapshot structure for historical visualization."""
    asks = parse_rows(payload.get("asks", []), show_top)
    bids = parse_rows(payload.get("bids", []), show_top)
    if not asks or not bids:
        return None

    best_ask = asks[0]["price"]
    best_bid = bids[0]["price"]
    spread = best_ask - best_bid
    mid = (best_ask + best_bid) / 2.0
    ask_total = sum(row["qty"] for row in asks)
    bid_total = sum(row["qty"] for row in bids)
    imbalance = (bid_total - ask_total) / (bid_total + ask_total) if (bid_total + ask_total) else 0.0

    return {
        "ts": updated_at,
        "asks": asks,
        "bids": bids,
        "best_ask": best_ask,
        "best_bid": best_bid,
        "spread": spread,
        "mid": mid,
        "ask_total": ask_total,
        "bid_total": bid_total,
        "imbalance": imbalance,
        "last_update_id": payload.get("lastUpdateId"),
    }


def blend_color(hex_color: str, alpha: float) -> QtGui.QColor:
    """Create a QColor from a hex string with the requested transparency."""
    color = QtGui.QColor(hex_color)
    color.setAlphaF(max(0.0, min(alpha, 1.0)))
    return color


class RelativeTimeAxis(pg.AxisItem):
    """Axis that labels timestamps relative to the latest visible point."""

    def tickStrings(self, values, scale, spacing):  # type: ignore[override]
        """Render axis ticks as relative seconds from the newest timestamp."""
        if not values:
            return []
        end = values[-1]
        return [f"{value - end:.1f}s" for value in values]


class OrderBookDashboard(QtWidgets.QMainWindow):
    """Desktop dashboard that plots live Binance order book history and snapshots."""

    def __init__(
        self,
        state: StreamState,
        processor: LiveEngineProcessor,
        show_top: int,
        refresh_ms: int,
        history_size: int,
    ) -> None:
        """Initialize the main window and start periodic UI refreshes."""
        super().__init__()
        self.state = state
        self.processor = processor
        self.show_top = show_top
        self.last_seen_update: float | None = None
        self.history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self.selected_index: int | None = None
        self.latest_plot_points: list[dict[str, Any]] = []

        self.setWindowTitle(f"Binance Order Book - {state.symbol.upper()}")
        self.resize(1680, 980)
        self._build_ui()

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh_view)
        self.timer.start(refresh_ms)

    def _build_ui(self) -> None:
        """Construct the dashboard layout, plots, controls, and tables."""
        central = QtWidgets.QWidget()
        central.setStyleSheet("background: #0f172a; color: #e5e7eb;")
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.subtitle = QtWidgets.QLabel()
        self.subtitle.setStyleSheet("color: #94a3b8; font: 12px 'Menlo';")
        layout.addWidget(self.subtitle)

        self.hover_label = QtWidgets.QLabel("Hover the main plot to inspect a historical snapshot.")
        self.hover_label.setStyleSheet("color: #94a3b8; font: 12px 'Menlo';")
        layout.addWidget(self.hover_label)

        stats_layout = QtWidgets.QGridLayout()
        stats_layout.setHorizontalSpacing(10)
        stats_layout.setVerticalSpacing(10)
        self.stat_labels: dict[str, QtWidgets.QLabel] = {}
        stat_keys = [
            "connection",
            "view_mode",
            "best_bid",
            "best_ask",
            "spread",
            "mid",
            "imbalance",
            "levels",
            "position",
            "equity",
            "pre_fee_pnl",
            "fees_paid",
            "realized",
            "unrealized",
        ]
        titles = {
            "connection": "Connection",
            "view_mode": "Selection",
            "best_bid": "Best Bid",
            "best_ask": "Best Ask",
            "spread": "Spread",
            "mid": "Mid",
            "imbalance": "Imbalance",
            "levels": "Visible Levels",
            "position": "Position",
            "equity": "Equity",
            "pre_fee_pnl": "Pre-Fee PnL",
            "fees_paid": "Fees Paid",
            "realized": "Realized PnL",
            "unrealized": "Unrealized PnL",
        }
        for index, key in enumerate(stat_keys):
            frame = QtWidgets.QFrame()
            frame.setStyleSheet(
                "QFrame { background: #172554; border: 1px solid #334155; border-radius: 8px; }"
            )
            frame_layout = QtWidgets.QVBoxLayout(frame)
            frame_layout.setContentsMargins(10, 8, 10, 8)
            title = QtWidgets.QLabel(titles[key])
            title.setStyleSheet("color: #93c5fd; font: 11px 'Menlo';")
            value = QtWidgets.QLabel("n/a")
            value.setStyleSheet("color: #f8fafc; font: bold 15px 'Menlo';")
            frame_layout.addWidget(title)
            frame_layout.addWidget(value)
            self.stat_labels[key] = value
            stats_layout.addWidget(frame, index // 4, index % 4)
        layout.addLayout(stats_layout)

        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        layout.addWidget(main_splitter, stretch=1)

        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        main_splitter.addWidget(left_panel)

        plot_axis = {"bottom": RelativeTimeAxis(orientation="bottom")}
        self.main_plot = pg.PlotWidget(title="Order Book History", axisItems=plot_axis)
        self.main_plot.showGrid(x=True, y=True, alpha=0.2)
        self.main_plot.setLabel("left", "Price / Distance to Mid")
        self.main_plot.setLabel("bottom", "Time")
        self.main_plot.addLegend()
        left_layout.addWidget(self.main_plot, stretch=3)

        self.bid_scatter = pg.ScatterPlotItem(size=6, brush=pg.mkBrush("#067647"), pen=pg.mkPen("#067647"), name="Bids")
        self.ask_scatter = pg.ScatterPlotItem(size=6, brush=pg.mkBrush("#b42318"), pen=pg.mkPen("#b42318"), name="Asks")
        self.mid_curve = pg.PlotCurveItem(pen=pg.mkPen("#2563eb", width=2), name="Mid")
        self.entry_long_scatter = pg.ScatterPlotItem(size=14, symbol="t1", brush=pg.mkBrush("#22c55e"), pen=pg.mkPen("#22c55e", width=2), name="Long Entries")
        self.exit_long_scatter = pg.ScatterPlotItem(size=16, symbol="x", brush=pg.mkBrush("#f97316"), pen=pg.mkPen("#f97316", width=3), name="Long Exits / Sells")
        self.entry_short_scatter = pg.ScatterPlotItem(size=12, symbol="t", brush=pg.mkBrush("#ef4444"), pen=pg.mkPen("#ef4444"), name="Short Entries")
        self.exit_short_scatter = pg.ScatterPlotItem(size=16, symbol="x", brush=pg.mkBrush("#fca5a5"), pen=pg.mkPen("#fca5a5", width=3), name="Short Exits / Buys")
        self.main_plot.addItem(self.bid_scatter)
        self.main_plot.addItem(self.ask_scatter)
        self.main_plot.addItem(self.mid_curve)
        self.main_plot.addItem(self.entry_long_scatter)
        self.main_plot.addItem(self.exit_long_scatter)
        self.main_plot.addItem(self.entry_short_scatter)
        self.main_plot.addItem(self.exit_short_scatter)
        self.crosshair_v = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#6b7280", style=QtCore.Qt.PenStyle.DashLine))
        self.crosshair_h = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#6b7280", style=QtCore.Qt.PenStyle.DashLine))
        self.main_plot.addItem(self.crosshair_v, ignoreBounds=True)
        self.main_plot.addItem(self.crosshair_h, ignoreBounds=True)

        secondary_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        left_layout.addWidget(secondary_splitter, stretch=2)

        self.spread_plot = pg.PlotWidget(title="Spread History", axisItems={"bottom": RelativeTimeAxis(orientation="bottom")})
        self.spread_plot.showGrid(x=True, y=True, alpha=0.2)
        self.spread_plot.setLabel("left", "Spread")
        self.spread_curve = self.spread_plot.plot(pen=pg.mkPen("#f59e0b", width=2))
        self.spread_marker = pg.ScatterPlotItem(size=10, brush=pg.mkBrush("#f59e0b"))
        self.spread_plot.addItem(self.spread_marker)
        secondary_splitter.addWidget(self.spread_plot)

        self.imbalance_plot = pg.PlotWidget(title="Imbalance History", axisItems={"bottom": RelativeTimeAxis(orientation="bottom")})
        self.imbalance_plot.showGrid(x=True, y=True, alpha=0.2)
        self.imbalance_plot.setLabel("left", "Imbalance")
        self.imbalance_curve = self.imbalance_plot.plot(pen=pg.mkPen("#7c3aed", width=2))
        self.imbalance_marker = pg.ScatterPlotItem(size=10, brush=pg.mkBrush("#7c3aed"))
        self.imbalance_plot.addItem(self.imbalance_marker)
        secondary_splitter.addWidget(self.imbalance_plot)

        self.pnl_plot = pg.PlotWidget(title="Paper Trader Equity / PnL", axisItems={"bottom": RelativeTimeAxis(orientation="bottom")})
        self.pnl_plot.showGrid(x=True, y=True, alpha=0.2)
        self.pnl_plot.setLabel("left", "Value")
        self.equity_curve = self.pnl_plot.plot(pen=pg.mkPen("#22c55e", width=2), name="Equity")
        self.gross_total_curve = self.pnl_plot.plot(pen=pg.mkPen("#eab308", width=2), name="Pre-Fee PnL")
        self.realized_curve = self.pnl_plot.plot(pen=pg.mkPen("#38bdf8", width=2), name="Realized PnL")
        self.unrealized_curve = self.pnl_plot.plot(pen=pg.mkPen("#f59e0b", width=2), name="Unrealized PnL")
        self.pnl_marker = pg.ScatterPlotItem(size=10, brush=pg.mkBrush("#22c55e"))
        self.pnl_plot.addItem(self.pnl_marker)
        secondary_splitter.addWidget(self.pnl_plot)

        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        main_splitter.addWidget(right_panel)

        controls_box = QtWidgets.QGroupBox("Controls")
        controls_layout = QtWidgets.QFormLayout(controls_box)
        self.level_spin = QtWidgets.QSpinBox()
        self.level_spin.setRange(1, self.state.levels)
        self.level_spin.setValue(min(self.show_top, self.state.levels))
        self.normalize_combo = QtWidgets.QComboBox()
        self.normalize_combo.addItems(["Absolute Price", "Distance To Mid"])
        self.show_bids_check = QtWidgets.QCheckBox("Show bids")
        self.show_bids_check.setChecked(True)
        self.show_asks_check = QtWidgets.QCheckBox("Show asks")
        self.show_asks_check.setChecked(True)
        self.follow_latest_check = QtWidgets.QCheckBox("Follow latest snapshot")
        self.follow_latest_check.setChecked(True)
        controls_layout.addRow("Levels", self.level_spin)
        controls_layout.addRow("Normalize", self.normalize_combo)
        controls_layout.addRow("", self.show_bids_check)
        controls_layout.addRow("", self.show_asks_check)
        controls_layout.addRow("", self.follow_latest_check)
        right_layout.addWidget(controls_box)

        self.snapshot_box = QtWidgets.QPlainTextEdit()
        self.snapshot_box.setReadOnly(True)
        self.snapshot_box.setStyleSheet(
            "font: 12px 'Menlo'; background: #0b1220; color: #e5e7eb; border: 1px solid #334155;"
        )
        right_layout.addWidget(self.snapshot_box, stretch=1)

        self.trade_table = QtWidgets.QTableWidget()
        self.trade_table.setColumnCount(7)
        self.trade_table.setHorizontalHeaderLabels(["Time", "Action", "Side", "Qty", "Price", "Gross", "Net"])
        self.trade_table.verticalHeader().setVisible(False)
        self.trade_table.horizontalHeader().setStretchLastSection(True)
        self.trade_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.trade_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.trade_table.setAlternatingRowColors(True)
        self.trade_table.setStyleSheet(
            "QTableWidget { font: 12px 'Menlo'; background: #0b1220; alternate-background-color: #111827; "
            "gridline-color: #1f2937; color: #e5e7eb; border: 1px solid #334155; } "
            "QHeaderView::section { background: #1e293b; color: #94a3b8; border: none; padding: 6px; }"
        )
        right_layout.addWidget(self.trade_table, stretch=1)

        tables_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        layout.addWidget(tables_splitter, stretch=1)
        self.asks_table = self._build_table("Asks")
        self.bids_table = self._build_table("Bids")
        tables_splitter.addWidget(self.asks_table["container"])
        tables_splitter.addWidget(self.bids_table["container"])

        self.current_price_label = QtWidgets.QLabel("n/a")
        self.current_price_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.current_price_label.setStyleSheet("color: #34d399; font: bold 28px 'Menlo';")
        layout.addWidget(self.current_price_label)

        self.current_price_caption = QtWidgets.QLabel("Current Mid Price / Bot Context")
        self.current_price_caption.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.current_price_caption.setStyleSheet("color: #94a3b8; font: 12px 'Menlo';")
        layout.addWidget(self.current_price_caption)

        main_splitter.setSizes([1200, 420])
        tables_splitter.setSizes([840, 840])
        self.setCentralWidget(central)

        self.mouse_proxy = pg.SignalProxy(
            self.main_plot.scene().sigMouseMoved,
            rateLimit=60,
            slot=self._handle_mouse_move,
        )
        self.level_spin.valueChanged.connect(self._on_controls_changed)
        self.normalize_combo.currentIndexChanged.connect(self._on_controls_changed)
        self.show_bids_check.toggled.connect(self._on_controls_changed)
        self.show_asks_check.toggled.connect(self._on_controls_changed)
        self.follow_latest_check.toggled.connect(self._on_follow_latest_toggled)

    def _build_table(self, title: str) -> dict[str, QtWidgets.QWidget]:
        """Create one read-only order book table widget."""
        container = QtWidgets.QGroupBox(title)
        container.setStyleSheet(
            "QGroupBox { color: #e5e7eb; font: bold 13px 'Menlo'; border: 1px solid #334155; "
            "border-radius: 8px; margin-top: 10px; padding-top: 10px; background: #111827; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        layout = QtWidgets.QVBoxLayout(container)
        table = QtWidgets.QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Price", "Qty", "Notional", "Cum Qty"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        table.setAlternatingRowColors(True)
        table.setStyleSheet(
            "QTableWidget { font: 12px 'Menlo'; background: #0f172a; alternate-background-color: #111827; "
            "gridline-color: #1f2937; color: #e5e7eb; border: none; } "
            "QHeaderView::section { background: #1e293b; color: #94a3b8; border: none; padding: 6px; }"
        )
        layout.addWidget(table)
        return {"container": container, "table": table}

    def _set_trade_table_data(self, trades: list[dict[str, Any]]) -> None:
        """Populate the recent-trades table for quick inspection of fills."""
        self.trade_table.setRowCount(len(trades))
        for row_index, trade in enumerate(trades):
            values = [
                iso_utc(trade.get("ts")),
                str(trade.get("action", "")),
                str(trade.get("side", "")),
                fmt_number(trade.get("qty"), 6),
                fmt_number(trade.get("price"), 2),
                fmt_number(trade.get("gross_realized_pnl"), 4),
                fmt_number(trade.get("realized_pnl"), 4),
            ]
            action = str(trade.get("action", ""))
            is_sell = "sell" in str(trade.get("side", "")).lower() or action == "close_long"
            foreground = QtGui.QColor("#f97316") if is_sell else QtGui.QColor("#22c55e")
            background = QtGui.QColor("#3b1d12") if is_sell else QtGui.QColor("#0f2f1b")
            for column_index, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                item.setForeground(foreground)
                if is_sell:
                    item.setBackground(background)
                    item.setFont(QtGui.QFont("Menlo", 12, QtGui.QFont.Weight.Bold))
                self.trade_table.setItem(row_index, column_index, item)
        self.trade_table.resizeColumnsToContents()

    def _set_table_data(
        self,
        table: QtWidgets.QTableWidget,
        rows: list[dict[str, float]],
        color: str,
        side: str,
    ) -> None:
        """Populate a table with formatted order book rows."""
        table.setRowCount(len(rows))
        max_cum_qty = max((row["cum_qty"] for row in rows), default=0.0)
        for row_index, row in enumerate(rows):
            values = [
                fmt_number(row["price"]),
                fmt_number(row["qty"]),
                fmt_number(row["notional"], 2),
                fmt_number(row["cum_qty"]),
            ]
            intensity = (row["cum_qty"] / max_cum_qty) if max_cum_qty else 0.0
            row_background = blend_color(color, 0.12 + 0.28 * intensity)
            top_of_book_background = QtGui.QColor("#1f3a5f") if side == "bid" else QtGui.QColor("#4b2338")
            for column_index, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter)
                item.setForeground(pg.mkColor(color))
                if row_index == 0:
                    item.setBackground(top_of_book_background)
                    item.setForeground(QtGui.QColor("#f8fafc"))
                    item.setFont(QtGui.QFont("Menlo", 12, QtGui.QFont.Weight.Bold))
                else:
                    item.setBackground(row_background)
                table.setItem(row_index, column_index, item)
        table.resizeColumnsToContents()

    def _handle_mouse_move(self, event) -> None:
        """Update the selected historical snapshot from the mouse position."""
        if not self.history or not self.latest_plot_points:
            return

        pos = event[0]
        if not self.main_plot.sceneBoundingRect().contains(pos):
            return

        mouse_point = self.main_plot.plotItem.vb.mapSceneToView(pos)
        self.crosshair_v.setPos(mouse_point.x())
        self.crosshair_h.setPos(mouse_point.y())

        nearest_snapshot = min(
            self.latest_plot_points,
            key=lambda point: abs(point["x"] - mouse_point.x()) + abs(point["y"] - mouse_point.y()),
        )
        self.selected_index = nearest_snapshot["snapshot_index"]
        self.follow_latest_check.setChecked(False)
        self._render_selected_snapshot()

    def _append_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Append a new unique snapshot to the rolling history buffer."""
        if self.history and self.history[-1]["last_update_id"] == snapshot["last_update_id"]:
            return
        self.history.append(snapshot)
        if self.follow_latest_check.isChecked():
            self.selected_index = len(self.history) - 1

    def _on_controls_changed(self) -> None:
        """Redraw plots and tables immediately after a control change."""
        if not self.history:
            return
        self._refresh_plots()
        self._render_selected_snapshot()

    def _on_follow_latest_toggled(self, checked: bool) -> None:
        """Snap selection back to the newest snapshot when follow mode is enabled."""
        if checked and self.history:
            self.selected_index = len(self.history) - 1
            self._render_selected_snapshot()

    def refresh_view(self) -> None:
        """Pull the latest stream state, update history, and refresh the visible snapshot."""
        snapshot = self.state.snapshot()
        payload = snapshot["latest_payload"]
        self.subtitle.setText(
            f"{snapshot['symbol'].upper()} | levels={snapshot['levels']} | speed={snapshot['speed']} | endpoint={snapshot['base_url']}"
        )

        if snapshot["updated_at"] != self.last_seen_update and payload:
            engine_update = self.processor.on_payload(payload, ts=snapshot["updated_at"])
            parsed_snapshot = build_snapshot(payload, snapshot["updated_at"], self.show_top)
            self.last_seen_update = snapshot["updated_at"]
            if parsed_snapshot:
                parsed_snapshot["engine_update"] = engine_update
                self._append_snapshot(parsed_snapshot)
                self._refresh_plots()

        if not self.history:
            self.stat_labels["connection"].setText(snapshot["latest_error"] or "connecting")
            self.stat_labels["view_mode"].setText("waiting")
            return

        if self.follow_latest_check.isChecked():
            self.selected_index = len(self.history) - 1

        self._render_selected_snapshot()

    def _refresh_plots(self) -> None:
        """Rebuild the historical plot data from the current snapshot buffer."""
        visible_levels = self.level_spin.value()
        normalize_mode = self.normalize_combo.currentText()
        show_bids = self.show_bids_check.isChecked()
        show_asks = self.show_asks_check.isChecked()

        bid_points = []
        ask_points = []
        mid_x = []
        mid_y = []
        spreads_x = []
        spreads_y = []
        imbalance_x = []
        imbalance_y = []
        equity_x = []
        equity_y = []
        realized_y = []
        unrealized_y = []
        gross_total_y = []
        plot_points = []
        long_entries = []
        long_exits = []
        short_entries = []
        short_exits = []
        recent_trades: list[dict[str, Any]] = []

        for snapshot_index, snapshot in enumerate(self.history):
            x_value = snapshot["ts"] or float(snapshot_index)
            mid_value = snapshot["mid"]
            engine_update = snapshot.get("engine_update")
            mid_x.append(x_value)
            mid_y.append(0.0 if normalize_mode == "Distance To Mid" else mid_value)
            spreads_x.append(x_value)
            spreads_y.append(snapshot["spread"])
            imbalance_x.append(x_value)
            imbalance_y.append(snapshot["imbalance"])
            if engine_update and engine_update.portfolio is not None:
                equity_x.append(x_value)
                equity_y.append(engine_update.portfolio.total_equity)
                gross_total_y.append(engine_update.portfolio.gross_total_pnl)
                realized_y.append(engine_update.portfolio.realized_pnl)
                unrealized_y.append(engine_update.portfolio.unrealized_pnl)
            if engine_update:
                for trade in engine_update.trades:
                    marker_y = trade.price - mid_value if normalize_mode == "Distance To Mid" else trade.price
                    point = {"pos": (x_value, marker_y), "data": trade.action}
                    if trade.action == "open_long":
                        long_entries.append(point)
                    elif trade.action == "close_long":
                        long_exits.append(point)
                    elif trade.action == "open_short":
                        short_entries.append(point)
                    elif trade.action == "close_short":
                        short_exits.append(point)
                    recent_trades.append(
                        {
                            "ts": trade.ts,
                            "action": trade.action,
                            "side": trade.side,
                            "qty": trade.qty,
                            "price": trade.price,
                            "gross_realized_pnl": trade.gross_realized_pnl,
                            "realized_pnl": trade.realized_pnl,
                        }
                    )

            if show_bids:
                for level, row in enumerate(snapshot["bids"][:visible_levels]):
                    y_value = row["price"] - mid_value if normalize_mode == "Distance To Mid" else row["price"]
                    point = {"pos": (x_value, y_value), "data": (snapshot_index, "bid", level)}
                    bid_points.append(point)
                    plot_points.append({"x": x_value, "y": y_value, "snapshot_index": snapshot_index})

            if show_asks:
                for level, row in enumerate(snapshot["asks"][:visible_levels]):
                    y_value = row["price"] - mid_value if normalize_mode == "Distance To Mid" else row["price"]
                    point = {"pos": (x_value, y_value), "data": (snapshot_index, "ask", level)}
                    ask_points.append(point)
                    plot_points.append({"x": x_value, "y": y_value, "snapshot_index": snapshot_index})

        self.latest_plot_points = plot_points
        self.bid_scatter.setData(bid_points)
        self.ask_scatter.setData(ask_points)
        self.mid_curve.setData(mid_x, mid_y)
        self.spread_curve.setData(spreads_x, spreads_y)
        self.imbalance_curve.setData(imbalance_x, imbalance_y)
        self.equity_curve.setData(equity_x, equity_y)
        self.gross_total_curve.setData(equity_x, gross_total_y)
        self.realized_curve.setData(equity_x, realized_y)
        self.unrealized_curve.setData(equity_x, unrealized_y)
        self.entry_long_scatter.setData(long_entries)
        self.exit_long_scatter.setData(long_exits)
        self.entry_short_scatter.setData(short_entries)
        self.exit_short_scatter.setData(short_exits)
        self._set_trade_table_data(list(reversed(recent_trades[-12:])))

    def _render_selected_snapshot(self) -> None:
        """Render labels, tables, and detail text for the selected snapshot."""
        if not self.history:
            return

        index = self.selected_index if self.selected_index is not None else len(self.history) - 1
        index = max(0, min(index, len(self.history) - 1))
        snapshot = list(self.history)[index]
        engine_update = snapshot.get("engine_update")
        portfolio = engine_update.portfolio if engine_update else None

        latest_snapshot = self.history[-1]
        self.stat_labels["connection"].setText(f"{'connected' if self.state.snapshot()['connected'] else 'disconnected'} @ {iso_utc(latest_snapshot['ts'])}")
        self.stat_labels["view_mode"].setText("latest" if index == len(self.history) - 1 else f"hovered #{index + 1}")
        self.stat_labels["best_bid"].setText(fmt_number(snapshot["best_bid"]))
        self.stat_labels["best_ask"].setText(fmt_number(snapshot["best_ask"]))
        self.stat_labels["spread"].setText(fmt_number(snapshot["spread"]))
        self.stat_labels["mid"].setText(fmt_number(snapshot["mid"]))
        self.stat_labels["imbalance"].setText(f"{snapshot['imbalance']:.2%}")
        self.stat_labels["levels"].setText(str(self.level_spin.value()))
        self.stat_labels["position"].setText("n/a" if portfolio is None else fmt_number(portfolio.position_qty, 6))
        self.stat_labels["equity"].setText("n/a" if portfolio is None else fmt_number(portfolio.total_equity, 2))
        self.stat_labels["pre_fee_pnl"].setText("n/a" if portfolio is None else fmt_number(portfolio.gross_total_pnl, 4))
        self.stat_labels["fees_paid"].setText("n/a" if portfolio is None else fmt_number(portfolio.total_fees_paid, 4))
        self.stat_labels["realized"].setText("n/a" if portfolio is None else fmt_number(portfolio.realized_pnl, 4))
        self.stat_labels["unrealized"].setText("n/a" if portfolio is None else fmt_number(portfolio.unrealized_pnl, 4))

        self.hover_label.setText(
            f"selected_time={iso_utc(snapshot['ts'])} | last_update_id={snapshot['last_update_id']} | "
            f"bid_total={fmt_number(snapshot['bid_total'])} | ask_total={fmt_number(snapshot['ask_total'])}"
        )

        self.snapshot_box.setPlainText(
            "\n".join(
                [
                    f"timestamp:      {iso_utc(snapshot['ts'])}",
                    f"last_update_id: {snapshot['last_update_id']}",
                    f"best_bid:       {fmt_number(snapshot['best_bid'])}",
                    f"best_ask:       {fmt_number(snapshot['best_ask'])}",
                    f"spread:         {fmt_number(snapshot['spread'])}",
                    f"mid:            {fmt_number(snapshot['mid'])}",
                    f"imbalance:      {snapshot['imbalance']:.4%}",
                    f"bid_total:      {fmt_number(snapshot['bid_total'])}",
                    f"ask_total:      {fmt_number(snapshot['ask_total'])}",
                    f"position_qty:   {'n/a' if portfolio is None else fmt_number(portfolio.position_qty, 6)}",
                    f"equity:         {'n/a' if portfolio is None else fmt_number(portfolio.total_equity, 2)}",
                    f"pre_fee_pnl:    {'n/a' if portfolio is None else fmt_number(portfolio.gross_total_pnl, 4)}",
                    f"fees_paid:      {'n/a' if portfolio is None else fmt_number(portfolio.total_fees_paid, 4)}",
                    f"gross_realized: {'n/a' if portfolio is None else fmt_number(portfolio.gross_realized_pnl, 4)}",
                    f"realized_pnl:   {'n/a' if portfolio is None else fmt_number(portfolio.realized_pnl, 4)}",
                    f"gross_unreal:   {'n/a' if portfolio is None else fmt_number(portfolio.gross_unrealized_pnl, 4)}",
                    f"unrealized_pnl: {'n/a' if portfolio is None else fmt_number(portfolio.unrealized_pnl, 4)}",
                    f"bot_action:     {'n/a' if engine_update is None else engine_update.signal.action}",
                    f"bot_reason:     {'n/a' if engine_update is None else engine_update.signal.reason}",
                    f"fills:          {'none' if engine_update is None or not engine_update.trades else ', '.join(f'{trade.action}@{fmt_number(trade.price, 2)} x {fmt_number(trade.qty, 6)}' for trade in engine_update.trades)}",
                    "",
                    "controls:",
                    f"- normalize:    {self.normalize_combo.currentText()}",
                    f"- show bids:    {self.show_bids_check.isChecked()}",
                    f"- show asks:    {self.show_asks_check.isChecked()}",
                    f"- levels:       {self.level_spin.value()}",
                ]
            )
        )

        self._set_table_data(self.asks_table["table"], snapshot["asks"][: self.level_spin.value()], "#f87171", "ask")
        self._set_table_data(self.bids_table["table"], snapshot["bids"][: self.level_spin.value()], "#34d399", "bid")
        self.current_price_label.setText(fmt_number(snapshot["mid"]))
        if engine_update is not None:
            self.current_price_caption.setText(
                f"{engine_update.signal.action} | long/short {engine_update.features.long_score}/{engine_update.features.short_score} | {engine_update.features.market_state}"
            )
        else:
            self.current_price_caption.setText("Current Mid Price / Bot Context")
        self._update_plot_markers(index, snapshot)

    def _update_plot_markers(self, index: int, snapshot: dict[str, Any]) -> None:
        """Move plot markers and crosshairs to the selected snapshot."""
        x_value = snapshot["ts"] or float(index)
        engine_update = snapshot.get("engine_update")
        self.crosshair_v.setPos(x_value)
        y_value = 0.0 if self.normalize_combo.currentText() == "Distance To Mid" else snapshot["mid"]
        self.crosshair_h.setPos(y_value)
        self.spread_marker.setData([x_value], [snapshot["spread"]])
        self.imbalance_marker.setData([x_value], [snapshot["imbalance"]])
        if engine_update and engine_update.portfolio is not None:
            self.pnl_marker.setData([x_value], [engine_update.portfolio.total_equity])
        else:
            self.pnl_marker.setData([], [])


def main() -> int:
    """Run the native pyqtgraph order book dashboard."""
    args = parse_args()
    args.levels = max(1, min(args.levels, MAX_SNAPSHOT_LIMIT))
    state = StreamState(
        symbol=args.symbol,
        base_url=args.base_url,
        levels=args.levels,
        speed=args.speed,
    )
    processor = LiveEngineProcessor(
        engine=SimpleTradingEngine(EngineConfig()),
        paper_trader=PaperTrader(PaperTraderConfig()),
    )
    start_background_stream(state)

    pg.setConfigOptions(antialias=True, background="w", foreground="k")
    app = QtWidgets.QApplication([])
    window = OrderBookDashboard(
        state=state,
        processor=processor,
        show_top=args.show_top,
        refresh_ms=args.refresh_ms,
        history_size=args.history_size,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
