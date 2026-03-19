"""Live bot for the validated 50/50 submission strategy using Roostoo execution."""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .live_config import LiveConfig, build_live_config
from .roostoo_client import RoostooClient
from .runtime.lead_lag import build_signals
from .runtime.weekly_vol import prepare_bars
from .strategy import SubmissionConfig, build_configs


@dataclass
class WeeklyVolState:
    active: bool = False
    entry_price: float = 0.0
    vol_move: float = 0.0
    entry_bar_time: str = ""
    time_exit_bar_time: str = ""
    qty: float = 0.0


@dataclass
class LeadLagState:
    active: bool = False
    symbol: str = ""
    entry_bar_time: str = ""
    exit_bar_time: str = ""
    qty: float = 0.0


@dataclass
class BotState:
    last_weekly_bar_time: str = ""
    last_lead_lag_bar_time: str = ""
    weekly_vol: WeeklyVolState = field(default_factory=WeeklyVolState)
    lead_lag: LeadLagState = field(default_factory=LeadLagState)


def _state_to_dict(state: BotState) -> dict[str, Any]:
    return {
        "last_weekly_bar_time": state.last_weekly_bar_time,
        "last_lead_lag_bar_time": state.last_lead_lag_bar_time,
        "weekly_vol": asdict(state.weekly_vol),
        "lead_lag": asdict(state.lead_lag),
    }


def load_state(path: Path) -> BotState:
    if not path.exists():
        return BotState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BotState(
        last_weekly_bar_time=str(payload.get("last_weekly_bar_time", "")),
        last_lead_lag_bar_time=str(payload.get("last_lead_lag_bar_time", "")),
        weekly_vol=WeeklyVolState(**payload.get("weekly_vol", {})),
        lead_lag=LeadLagState(**payload.get("lead_lag", {})),
    )


def save_state(path: Path, state: BotState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_state_to_dict(state), indent=2), encoding="utf-8")


def append_trade_log(
    live: LiveConfig,
    *,
    sleeve: str,
    symbol: str,
    side: str,
    requested_qty: float,
    response: dict[str, Any],
) -> None:
    """Append one executed order record to the local trade log."""
    detail = response.get("OrderDetail", {}) if isinstance(response, dict) else {}
    trade_log_path = live.state_path.parent / "trades.jsonl"
    trade_log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "logged_at": pd.Timestamp.utcnow().isoformat(),
        "mode": live.bot_mode,
        "sleeve": sleeve,
        "symbol": symbol,
        "side": side,
        "requested_qty": requested_qty,
        "filled_qty": _filled_quantity(response),
        "state_path": str(live.state_path),
        "order_id": detail.get("OrderID") if isinstance(detail, dict) else None,
        "status": detail.get("Status") if isinstance(detail, dict) else None,
        "price": detail.get("FilledAverPrice") if isinstance(detail, dict) else None,
        "response": response,
    }
    with trade_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def fetch_binance_klines(base_url: str, symbol: str, interval: str, limit: int) -> pd.DataFrame:
    response = requests.get(
        f"{base_url}/api/v3/klines",
        params={"symbol": symbol, "interval": interval, "limit": limit},
        timeout=30,
    )
    response.raise_for_status()
    raw = response.json()
    if not isinstance(raw, list):
        raise RuntimeError(f"unexpected Binance response for {symbol} {interval}")
    frame = pd.DataFrame(
        raw,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_asset_volume",
            "number_of_trades",
            "taker_buy_base_asset_volume",
            "taker_buy_quote_asset_volume",
            "ignore",
        ],
    )
    frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = frame[column].astype(float)
    return frame


def load_completed_weekly_bars(base_url: str, symbol: str) -> pd.DataFrame:
    hourly = fetch_binance_klines(base_url, symbol, "1h", 1000)
    now = pd.Timestamp.utcnow()
    completed = hourly.loc[hourly["close_time"] < now].copy()
    completed["bucket"] = completed["open_time"].dt.floor("4h")
    counts = completed.groupby("bucket")["open_time"].count()
    complete_buckets = counts[counts == 4].index
    grouped = completed.loc[completed["bucket"].isin(complete_buckets)].groupby("bucket")
    bars = pd.DataFrame(
        {
            "open_time": grouped["open_time"].min(),
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
        }
    ).reset_index(drop=True)
    return bars


def load_completed_lead_lag_panel(base_url: str, symbols: tuple[str, ...]) -> pd.DataFrame:
    now = pd.Timestamp.utcnow()
    panel: pd.DataFrame | None = None
    for symbol in symbols:
        frame = fetch_binance_klines(base_url, symbol, "5m", 1000)
        completed = frame.loc[frame["close_time"] < now, ["open_time", "open", "close"]].copy()
        completed = completed.rename(columns={"open": f"{symbol}_open", "close": f"{symbol}_close"})
        panel = completed if panel is None else panel.merge(completed, on="open_time", how="inner")
    assert panel is not None
    return panel.sort_values("open_time").reset_index(drop=True)


def current_price_map(client: RoostooClient) -> dict[str, float]:
    payload = client.get_ticker()
    data = payload.get("Data", {}) if isinstance(payload, dict) else {}
    out: dict[str, float] = {}
    if not isinstance(data, dict):
        return out
    for pair, detail in data.items():
        if not isinstance(detail, dict):
            continue
        last = detail.get("LastPrice")
        try:
            out[str(pair)] = float(last)
        except (TypeError, ValueError):
            continue
    return out


def wallet_holdings(client: RoostooClient) -> dict[str, dict[str, float]]:
    return client.wallet_from_balances(client.get_balances())


def total_equity_usd(wallet: dict[str, dict[str, float]], prices: dict[str, float]) -> float:
    equity = 0.0
    for asset, detail in wallet.items():
        amount = float(detail.get("free", 0.0)) + float(detail.get("lock", 0.0))
        if asset == "USD":
            equity += amount
        elif amount > 0.0:
            equity += amount * prices.get(f"{asset}/USD", 0.0)
    return equity


def free_usd_balance(wallet: dict[str, dict[str, float]]) -> float:
    """Return free USD available for new buys."""
    return float(wallet.get("USD", {}).get("free", 0.0))


def exchange_rules(client: RoostooClient) -> dict[str, dict[str, Any]]:
    payload = client.get_exchange_info()
    pairs = payload.get("TradePairs", {}) if isinstance(payload, dict) else {}
    return pairs if isinstance(pairs, dict) else {}


def round_quantity(symbol: str, quantity: float, rules: dict[str, dict[str, Any]], prices: dict[str, float]) -> float:
    pair = RoostooClient.normalize_pair(symbol)
    detail = rules.get(pair, {})
    precision = int(detail.get("AmountPrecision", 6))
    min_order = float(detail.get("MiniOrder", 0.0))
    factor = 10 ** precision
    rounded = math.floor(quantity * factor) / factor
    if rounded <= 0.0:
        return 0.0
    price = prices.get(pair, 0.0)
    if price > 0.0 and rounded * price < min_order:
        return 0.0
    return rounded


def _filled_quantity(response: dict[str, Any]) -> float:
    detail = response.get("OrderDetail", {}) if isinstance(response, dict) else {}
    for key in ("FilledQuantity", "Quantity"):
        value = detail.get(key) if isinstance(detail, dict) else None
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _order_succeeded(response: dict[str, Any]) -> bool:
    return bool(isinstance(response, dict) and response.get("Success"))


def maybe_trade_weekly_vol(
    submission: SubmissionConfig,
    live: LiveConfig,
    client: RoostooClient,
    rules: dict[str, dict[str, Any]],
    prices: dict[str, float],
    wallet: dict[str, dict[str, float]],
    state: BotState,
) -> None:
    weekly_config, _ = build_configs(submission)
    bars = load_completed_weekly_bars(live.binance_base_url, weekly_config.symbol)
    if bars.empty:
        return
    prepared = prepare_bars(bars, weekly_config)
    latest = prepared.iloc[-1]
    latest_bar_time = pd.Timestamp(latest["open_time"]).isoformat()

    # Intrabar stop/target monitoring using live Roostoo prices.
    if state.weekly_vol.active:
        current_price = prices.get("ETH/USD", 0.0)
        exit_due = False
        exit_limit_price = current_price
        stop_price = state.weekly_vol.entry_price - weekly_config.stop_sigma * state.weekly_vol.vol_move
        tp_price = state.weekly_vol.entry_price + weekly_config.take_profit_sigma * state.weekly_vol.vol_move
        if current_price and current_price <= stop_price:
            exit_due = True
            exit_limit_price = stop_price
        elif current_price and current_price >= tp_price:
            exit_due = True
            exit_limit_price = tp_price
        elif state.weekly_vol.time_exit_bar_time and pd.Timestamp(latest["open_time"]) >= pd.Timestamp(state.weekly_vol.time_exit_bar_time):
            exit_due = True
        elif float(latest["close"]) <= float(latest["ema_slow"]):
            exit_due = True
        if exit_due:
            free_eth = wallet.get("ETH", {}).get("free", 0.0) + wallet.get("ETH", {}).get("lock", 0.0)
            qty = round_quantity("ETHUSDT", min(free_eth, state.weekly_vol.qty or free_eth), rules, prices)
            if qty > 0.0 and live.live_trading:
                response = client.place_limit_order(symbol="ETHUSDT", side="SELL", quantity=qty, price=exit_limit_price)
                append_trade_log(live, sleeve="weekly_vol", symbol="ETHUSDT", side="SELL", requested_qty=qty, response=response)
                if _order_succeeded(response):
                    state.weekly_vol = WeeklyVolState()
            else:
                state.weekly_vol = WeeklyVolState()

    if state.last_weekly_bar_time == latest_bar_time:
        return
    state.last_weekly_bar_time = latest_bar_time

    if state.weekly_vol.active:
        return

    regime = float(latest["close"]) > float(latest["ema_slow"]) and float(latest["ema_fast"]) > float(latest["ema_slow"])
    vol_move = latest["vol_move"]
    recent_high = latest["recent_high"]
    if not regime or pd.isna(vol_move) or pd.isna(recent_high):
        return
    pullback = float(recent_high) - float(latest["close"])
    if pullback < weekly_config.entry_sigma * float(vol_move):
        return

    equity = total_equity_usd(wallet, prices)
    sleeve_usd = min(equity * 0.5, free_usd_balance(wallet) / (1.0 + weekly_config.fee_rate))
    eth_price = prices.get("ETH/USD", 0.0)
    if sleeve_usd <= 0.0 or eth_price <= 0.0:
        return
    qty = round_quantity("ETHUSDT", sleeve_usd / eth_price, rules, prices)
    if qty <= 0.0:
        return

    filled_qty = qty
    if live.live_trading:
        response = client.place_limit_order(symbol="ETHUSDT", side="BUY", quantity=qty, price=eth_price)
        append_trade_log(live, sleeve="weekly_vol", symbol="ETHUSDT", side="BUY", requested_qty=qty, response=response)
        if not _order_succeeded(response):
            return
        filled_qty = _filled_quantity(response) or qty
    next_bar_time = pd.Timestamp(latest["open_time"]) + pd.Timedelta(hours=4)
    time_exit_bar_time = next_bar_time + pd.Timedelta(hours=4 * weekly_config.max_hold_bars)
    state.weekly_vol = WeeklyVolState(
        active=True,
        entry_price=eth_price,
        vol_move=float(vol_move),
        entry_bar_time=next_bar_time.isoformat(),
        time_exit_bar_time=time_exit_bar_time.isoformat(),
        qty=filled_qty,
    )


def maybe_trade_lead_lag(
    submission: SubmissionConfig,
    live: LiveConfig,
    client: RoostooClient,
    rules: dict[str, dict[str, Any]],
    prices: dict[str, float],
    wallet: dict[str, dict[str, float]],
    state: BotState,
) -> None:
    _, lead_config = build_configs(submission)
    panel = load_completed_lead_lag_panel(live.binance_base_url, (*lead_config.leaders, *lead_config.laggers))
    if panel.empty:
        return
    latest_bar_time = pd.Timestamp(panel.iloc[-1]["open_time"]).isoformat()
    leader_return, gaps = build_signals(panel, lead_config)
    latest_index = len(panel) - 1
    current_ts = pd.Timestamp(panel.iloc[latest_index]["open_time"])

    if state.lead_lag.active and state.lead_lag.exit_bar_time and current_ts >= pd.Timestamp(state.lead_lag.exit_bar_time):
        asset = state.lead_lag.symbol.replace("USDT", "")
        free_qty = wallet.get(asset, {}).get("free", 0.0) + wallet.get(asset, {}).get("lock", 0.0)
        qty = round_quantity(state.lead_lag.symbol, min(free_qty, state.lead_lag.qty or free_qty), rules, prices)
        if qty > 0.0 and live.live_trading:
            exit_pair = RoostooClient.normalize_pair(state.lead_lag.symbol)
            exit_price = prices.get(exit_pair, 0.0)
            response = client.place_limit_order(symbol=state.lead_lag.symbol, side="SELL", quantity=qty, price=exit_price)
            append_trade_log(
                live,
                sleeve="lead_lag",
                symbol=state.lead_lag.symbol,
                side="SELL",
                requested_qty=qty,
                response=response,
            )
            if _order_succeeded(response):
                state.lead_lag = LeadLagState()
        else:
            state.lead_lag = LeadLagState()

    if state.last_lead_lag_bar_time == latest_bar_time:
        return
    state.last_lead_lag_bar_time = latest_bar_time

    if state.lead_lag.active:
        return

    leader_value = leader_return.iloc[latest_index]
    if pd.isna(leader_value) or float(leader_value) <= lead_config.leader_threshold:
        return
    best_target: str | None = None
    best_gap = lead_config.gap_threshold
    for target in lead_config.laggers:
        gap_value = gaps[target].iloc[latest_index]
        if pd.isna(gap_value) or float(gap_value) <= best_gap:
            continue
        best_gap = float(gap_value)
        best_target = target
    if best_target is None:
        return

    equity = total_equity_usd(wallet, prices)
    sleeve_usd = min(equity * 0.5, free_usd_balance(wallet) / (1.0 + lead_config.fee_rate))
    pair = RoostooClient.normalize_pair(best_target)
    market_price = prices.get(pair, 0.0)
    if sleeve_usd <= 0.0 or market_price <= 0.0:
        return
    qty = round_quantity(best_target, sleeve_usd / market_price, rules, prices)
    if qty <= 0.0:
        return

    filled_qty = qty
    if live.live_trading:
        response = client.place_limit_order(symbol=best_target, side="BUY", quantity=qty, price=market_price)
        append_trade_log(live, sleeve="lead_lag", symbol=best_target, side="BUY", requested_qty=qty, response=response)
        if not _order_succeeded(response):
            return
        filled_qty = _filled_quantity(response) or qty
    exit_bar_time = current_ts + pd.Timedelta(minutes=5 * lead_config.hold_bars)
    state.lead_lag = LeadLagState(
        active=True,
        symbol=best_target,
        entry_bar_time=current_ts.isoformat(),
        exit_bar_time=exit_bar_time.isoformat(),
        qty=filled_qty,
    )


def run_once(submission: SubmissionConfig, live: LiveConfig) -> BotState:
    client = RoostooClient(live)
    if not client.is_configured():
        raise RuntimeError("Roostoo API credentials are not configured.")
    state = load_state(live.state_path)
    rules = exchange_rules(client)
    prices = current_price_map(client)
    wallet = wallet_holdings(client)
    maybe_trade_weekly_vol(submission, live, client, rules, prices, wallet, state)
    wallet = wallet_holdings(client)
    prices = current_price_map(client)
    maybe_trade_lead_lag(submission, live, client, rules, prices, wallet, state)
    save_state(live.state_path, state)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the live Roostoo execution bot for the validated 50/50 submission strategy.")
    parser.add_argument("--polling-seconds", type=int, default=60)
    parser.add_argument("--state-path", type=Path, default=None)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--competition", action="store_true", help="Use competition-specific Roostoo credentials and state path.")
    parser.add_argument("--reset-state", action="store_true", help="Delete the selected state file before starting.")
    parser.add_argument("--run-once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    submission = SubmissionConfig()
    live = build_live_config(
        competition=args.competition,
        polling_seconds=args.polling_seconds,
        state_path=args.state_path,
        live_trading=args.live,
    )
    if args.reset_state and live.state_path.exists():
        live.state_path.unlink()
    if args.run_once:
        state = run_once(submission, live)
        print(json.dumps({"mode": live.bot_mode, "state_path": str(live.state_path), **_state_to_dict(state)}, indent=2))
        return 0

    while True:
        state = run_once(submission, live)
        print(json.dumps({"mode": live.bot_mode, "state_path": str(live.state_path), **_state_to_dict(state)}, indent=2))
        time.sleep(live.polling_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
