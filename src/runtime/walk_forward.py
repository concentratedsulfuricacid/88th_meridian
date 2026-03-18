"""Local walk-forward harness for the submission package."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .lead_lag import LeadLagConfig, build_signals
from .weekly_vol import WeeklyVolConfig, prepare_bars


@dataclass(frozen=True)
class WindowResult:
    """One independent out-of-sample test window."""

    start: str
    end: str
    weekly_vol_return_pct: float
    weekly_vol_trades: int
    lead_lag_return_pct: float
    lead_lag_trades: int
    blend_return_pct: float
    blend_trades: int


@dataclass(frozen=True)
class RollingBlendResult:
    """Summary of the stitched walk-forward evaluation."""

    windows: int
    stitched_return_pct: float
    mean_window_return_pct: float
    median_window_return_pct: float
    positive_window_rate: float
    pct_windows_with_trade: float
    mean_trades_per_window: float
    best_window_return_pct: float
    worst_window_return_pct: float
    weekly_vol_mean_window_return_pct: float
    lead_lag_mean_window_return_pct: float
    window_log: tuple[WindowResult, ...]


def _backtest_weekly_vol_window(
    bars: pd.DataFrame,
    config: WeeklyVolConfig,
    eval_start: pd.Timestamp,
    eval_end: pd.Timestamp,
) -> tuple[float, int]:
    frame = prepare_bars(bars, config)
    window = 6 if config.volatility_horizon == "daily" else 42

    cash = 1.0
    qty = 0.0
    trades = 0
    position: dict[str, object] | None = None
    pending_entry: dict[str, object] | None = None
    start_index = max(config.regime_slow_ema, window, config.pullback_lookback_bars)

    for index in range(start_index, len(frame)):
        row = frame.iloc[index]
        current_ts = pd.Timestamp(row["open_time"])
        if current_ts >= eval_end:
            break

        if pending_entry is not None:
            expires_index = int(pending_entry["expires_index"])
            fill_price = float(pending_entry["entry_price"])
            if index > expires_index:
                pending_entry = None
            elif float(row["low"]) <= fill_price:
                qty = cash / (fill_price * (1.0 + config.fee_rate))
                cash -= qty * fill_price * (1.0 + config.fee_rate)
                position = {
                    "entry_index": index,
                    "stop_price": fill_price - (config.stop_sigma * float(pending_entry["volatility_move"])),
                    "target_price": fill_price + (config.take_profit_sigma * float(pending_entry["volatility_move"])),
                    "time_exit_index": min(index + config.max_hold_bars, len(frame) - 1),
                }
                pending_entry = None

        if position is not None:
            exit_price: float | None = None
            if index == int(position["entry_index"]):
                exit_price = None
            elif float(row["low"]) <= float(position["stop_price"]):
                exit_price = float(position["stop_price"])
            elif float(row["high"]) >= float(position["target_price"]):
                exit_price = float(position["target_price"])
            elif index >= int(position["time_exit_index"]):
                exit_price = float(row["open"])
            elif float(row["close"]) <= float(row["ema_slow"]):
                exit_price = float(row["open"])

            if exit_price is not None:
                cash = qty * exit_price * (1.0 - config.fee_rate)
                qty = 0.0
                position = None
                trades += 1

        if position is None and pending_entry is None and eval_start <= current_ts < eval_end and index < len(frame) - 1:
            regime = float(row["close"]) > float(row["ema_slow"]) and float(row["ema_fast"]) > float(row["ema_slow"])
            vol_move = row["vol_move"]
            recent_high = row["recent_high"]
            if regime and pd.notna(vol_move) and pd.notna(recent_high):
                threshold_price = float(recent_high) - (config.entry_sigma * float(vol_move))
                if config.entry_mode == "market":
                    next_ts = pd.Timestamp(frame.iloc[index + 1]["open_time"])
                    if next_ts >= eval_end:
                        continue
                    pullback = float(recent_high) - float(row["close"])
                    if pullback >= config.entry_sigma * float(vol_move):
                        entry_price = float(frame.iloc[index + 1]["open"])
                        qty = cash / (entry_price * (1.0 + config.fee_rate))
                        cash -= qty * entry_price * (1.0 + config.fee_rate)
                        position = {
                            "entry_index": index + 1,
                            "stop_price": entry_price - (config.stop_sigma * float(vol_move)),
                            "target_price": entry_price + (config.take_profit_sigma * float(vol_move)),
                            "time_exit_index": min(index + config.max_hold_bars, len(frame) - 1),
                        }
                elif config.entry_mode == "touch":
                    pending_entry = {
                        "entry_price": threshold_price,
                        "volatility_move": float(vol_move),
                        "expires_index": min(index + config.touch_order_bars, len(frame) - 1),
                    }
                else:
                    raise ValueError(f"unsupported entry_mode: {config.entry_mode}")

    if position is not None:
        last_close = float(frame.loc[frame["open_time"] < eval_end, "close"].iloc[-1])
        cash = qty * last_close * (1.0 - config.fee_rate)
        trades += 1

    return cash, trades


def _backtest_lead_lag_window(
    panel: pd.DataFrame,
    config: LeadLagConfig,
    eval_start: pd.Timestamp,
    eval_end: pd.Timestamp,
) -> tuple[float, int]:
    leader_return, gaps = build_signals(panel, config)

    cash = config.initial_cash
    slot_notional = config.initial_cash / config.max_positions
    open_positions: list[dict[str, object]] = []
    pending_entries: list[dict[str, object]] = []
    trades = 0

    for index in range(config.lookback_bars + 1, len(panel)):
        current_ts = pd.Timestamp(panel.iloc[index]["open_time"])
        if current_ts >= eval_end:
            break

        still_open: list[dict[str, object]] = []
        for position in open_positions:
            if int(position["exit_index"]) != index:
                still_open.append(position)
                continue
            target = str(position["target"])
            exit_price = float(panel.iloc[index][f"{target}_open"])
            cash += float(position["qty"]) * exit_price * (1.0 - config.fee_rate)
            trades += 1
        open_positions = still_open

        if pending_entries:
            remaining_pending: list[dict[str, object]] = []
            for entry in pending_entries:
                if int(entry["entry_index"]) != index:
                    remaining_pending.append(entry)
                    continue
                target = str(entry["target"])
                entry_price = float(panel.iloc[index][f"{target}_open"])
                deployable_cash = min(slot_notional, cash)
                if deployable_cash <= 0.0:
                    continue
                qty = deployable_cash / (entry_price * (1.0 + config.fee_rate))
                cost_basis = qty * entry_price * (1.0 + config.fee_rate)
                cash -= cost_basis
                open_positions.append(
                    {
                        "target": target,
                        "qty": qty,
                        "cost_basis": cost_basis,
                        "exit_index": int(entry["exit_index"]),
                    }
                )
            pending_entries = remaining_pending

        if eval_start <= current_ts < eval_end and index < len(panel) - config.hold_bars - 1:
            active_targets = {str(position["target"]) for position in open_positions}
            pending_targets = {str(entry["target"]) for entry in pending_entries}
            available_slots = config.max_positions - len(open_positions) - len(pending_entries)
            if available_slots > 0 and not pd.isna(leader_return.iloc[index]) and float(leader_return.iloc[index]) > config.leader_threshold:
                candidates: list[tuple[float, str]] = []
                for target in config.laggers:
                    if target in active_targets or target in pending_targets:
                        continue
                    gap_value = gaps[target].iloc[index]
                    if pd.isna(gap_value) or float(gap_value) <= config.gap_threshold:
                        continue
                    next_ts = pd.Timestamp(panel.iloc[index + 1]["open_time"])
                    if next_ts >= eval_end:
                        continue
                    candidates.append((float(gap_value), target))
                candidates.sort(reverse=True)
                for gap_score, target in candidates[:available_slots]:
                    pending_entries.append(
                        {
                            "target": target,
                            "entry_index": index + 1,
                            "exit_index": min(index + 1 + config.hold_bars, len(panel) - 1),
                        }
                    )

    last_row = panel.loc[panel["open_time"] < eval_end].iloc[-1]
    for position in open_positions:
        target = str(position["target"])
        exit_price = float(last_row[f"{target}_close"])
        cash += float(position["qty"]) * exit_price * (1.0 - config.fee_rate)
        trades += 1

    return cash, trades


def evaluate_walk_forward(
    weekly_vol_bars: pd.DataFrame,
    lead_lag_panel: pd.DataFrame,
    weekly_vol_config: WeeklyVolConfig,
    lead_lag_config: LeadLagConfig,
    *,
    warmup_days: int = 90,
    test_days: int = 7,
    step_days: int = 7,
) -> RollingBlendResult:
    """Evaluate the blend on independent rolling test windows."""
    overlap_start = max(pd.Timestamp(weekly_vol_bars["open_time"].min()), pd.Timestamp(lead_lag_panel["open_time"].min()))
    overlap_end = min(pd.Timestamp(weekly_vol_bars["open_time"].max()), pd.Timestamp(lead_lag_panel["open_time"].max()))
    first_test_start = overlap_start + pd.Timedelta(days=warmup_days)
    window_delta = pd.Timedelta(days=test_days)
    step_delta = pd.Timedelta(days=step_days)

    results: list[WindowResult] = []
    window_start = first_test_start.floor("D")
    while window_start + window_delta <= overlap_end:
        window_end = window_start + window_delta

        weekly_slice = weekly_vol_bars.loc[
            (weekly_vol_bars["open_time"] >= window_start - pd.Timedelta(days=warmup_days))
            & (weekly_vol_bars["open_time"] < window_end)
        ].reset_index(drop=True)
        lead_lag_slice = lead_lag_panel.loc[
            (lead_lag_panel["open_time"] >= window_start - pd.Timedelta(days=warmup_days))
            & (lead_lag_panel["open_time"] < window_end)
        ].reset_index(drop=True)

        if len(weekly_slice) == 0 or len(lead_lag_slice) == 0:
            window_start += step_delta
            continue

        weekly_equity, weekly_trades = _backtest_weekly_vol_window(weekly_slice, weekly_vol_config, window_start, window_end)
        lead_lag_equity, lead_lag_trades = _backtest_lead_lag_window(lead_lag_slice, lead_lag_config, window_start, window_end)
        blend_equity = (0.5 * weekly_equity) + (0.5 * lead_lag_equity)
        results.append(
            WindowResult(
                start=window_start.isoformat(),
                end=window_end.isoformat(),
                weekly_vol_return_pct=(weekly_equity - 1.0) * 100.0,
                weekly_vol_trades=weekly_trades,
                lead_lag_return_pct=(lead_lag_equity - 1.0) * 100.0,
                lead_lag_trades=lead_lag_trades,
                blend_return_pct=(blend_equity - 1.0) * 100.0,
                blend_trades=weekly_trades + lead_lag_trades,
            )
        )
        window_start += step_delta

    returns = pd.Series([window.blend_return_pct / 100.0 for window in results])
    trade_counts = pd.Series([window.blend_trades for window in results])
    weekly_returns = pd.Series([window.weekly_vol_return_pct for window in results])
    lead_lag_returns = pd.Series([window.lead_lag_return_pct for window in results])

    return RollingBlendResult(
        windows=len(results),
        stitched_return_pct=((returns.add(1.0).prod()) - 1.0) * 100.0 if len(returns) else 0.0,
        mean_window_return_pct=float(returns.mean() * 100.0) if len(returns) else 0.0,
        median_window_return_pct=float(returns.median() * 100.0) if len(returns) else 0.0,
        positive_window_rate=float((returns > 0.0).mean()) if len(returns) else 0.0,
        pct_windows_with_trade=float((trade_counts > 0).mean()) if len(trade_counts) else 0.0,
        mean_trades_per_window=float(trade_counts.mean()) if len(trade_counts) else 0.0,
        best_window_return_pct=float(returns.max() * 100.0) if len(returns) else 0.0,
        worst_window_return_pct=float(returns.min() * 100.0) if len(returns) else 0.0,
        weekly_vol_mean_window_return_pct=float(weekly_returns.mean()) if len(weekly_returns) else 0.0,
        lead_lag_mean_window_return_pct=float(lead_lag_returns.mean()) if len(lead_lag_returns) else 0.0,
        window_log=tuple(results),
    )
