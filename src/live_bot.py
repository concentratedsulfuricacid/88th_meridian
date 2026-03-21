"""Live bot for the 24h confirmed breakout strategy (Roostoo execution).

Strategy:
  - 5 symbols in parallel, each with independent state.
  - Entry: last completed 5m bar closes >= 288-bar rolling high (24h lookback).
  - Stop/Target: anchored to rolling_high (not entry price).
  - Stop:   -100 bps below rolling_high.
  - Target: +300 bps above rolling_high.
  - Regime gate: skip entry if 5m close <= 20d EMA (computed on 1h bars, span=480).
  - EOD:    any open position at midnight UTC closes via limit at current price.
  - Sizing: equal allocation — total equity / number of symbols (20% each).

Usage:
  python -m src.live_bot [--live] [--competition] [--run-once]
"""

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


# ---------------------------------------------------------------------------
# Strategy constants
# ---------------------------------------------------------------------------

BREAKOUT_SYMBOLS: tuple[str, ...] = (
    "FLOKIUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "FETUSDT",
    "VIRTUALUSDT",
)

N_BARS: int = 288          # 24h of 5m bars for rolling high lookback
STOP_BPS: float = 100.0    # stop distance below entry in bps
TARGET_BPS: float = 300.0  # target distance above entry in bps
REGIME_EMA_BARS: int = 5760  # 20d EMA on 5m bars — skip entry if close below


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class SymbolState:
    active: bool = False
    entry_price: float = 0.0
    stop_price: float = 0.0
    target_price: float = 0.0
    entry_day: str = ""          # UTC date of entry, e.g. "2026-03-22"
    qty: float = 0.0
    last_bar_time: str = ""      # open_time of last processed 5m bar (ISO)
    target_order_id: str = ""    # resting limit sell at target (fires on touch)


@dataclass
class BotState:
    symbols: dict[str, SymbolState] = field(default_factory=dict)

    def get(self, symbol: str) -> SymbolState:
        if symbol not in self.symbols:
            self.symbols[symbol] = SymbolState()
        return self.symbols[symbol]


def _state_to_dict(state: BotState) -> dict[str, Any]:
    return {"symbols": {sym: asdict(s) for sym, s in state.symbols.items()}}


def load_state(path: Path) -> BotState:
    if not path.exists():
        return BotState()
    payload = json.loads(path.read_text(encoding="utf-8"))
    state = BotState()
    for sym, data in payload.get("symbols", {}).items():
        state.symbols[sym] = SymbolState(**data)
    return state


def save_state(path: Path, state: BotState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_state_to_dict(state), indent=2), encoding="utf-8")


def append_trade_log(
    live: LiveConfig,
    *,
    symbol: str,
    side: str,
    reason: str,
    requested_qty: float,
    response: dict[str, Any],
) -> None:
    detail = response.get("OrderDetail", {}) if isinstance(response, dict) else {}
    trade_log_path = live.state_path.parent / "trades.jsonl"
    trade_log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "logged_at": pd.Timestamp.utcnow().isoformat(),
        "mode": live.bot_mode,
        "sleeve": "breakout",
        "symbol": symbol,
        "side": side,
        "reason": reason,
        "requested_qty": requested_qty,
        "filled_qty": _filled_quantity(response),
        "order_id": detail.get("OrderID") if isinstance(detail, dict) else None,
        "status": detail.get("Status") if isinstance(detail, dict) else None,
        "price": detail.get("FilledAverPrice") if isinstance(detail, dict) else None,
        "response": response,
    }
    with trade_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

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
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_asset_volume", "number_of_trades",
            "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
        ],
    )
    frame["open_time"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    frame["close_time"] = pd.to_datetime(frame["close_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = frame[col].astype(float)
    return frame


def load_completed_5m_bars(base_url: str, symbol: str, limit: int = 400) -> pd.DataFrame:
    """Return completed (closed) 5m bars, most recent last."""
    bars = fetch_binance_klines(base_url, symbol, "5m", limit)
    now = pd.Timestamp.utcnow()
    return bars.loc[bars["close_time"] < now].copy().reset_index(drop=True)


# ---------------------------------------------------------------------------
# Roostoo helpers
# ---------------------------------------------------------------------------

def current_price_map(client: RoostooClient) -> dict[str, float]:
    payload = client.get_ticker()
    data = payload.get("Data", {}) if isinstance(payload, dict) else {}
    out: dict[str, float] = {}
    if not isinstance(data, dict):
        return out
    for pair, detail in data.items():
        if not isinstance(detail, dict):
            continue
        try:
            out[str(pair)] = float(detail.get("LastPrice"))
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
    return float(wallet.get("USD", {}).get("free", 0.0))


def exchange_rules(client: RoostooClient) -> dict[str, dict[str, Any]]:
    payload = client.get_exchange_info()
    pairs = payload.get("TradePairs", {}) if isinstance(payload, dict) else {}
    return pairs if isinstance(pairs, dict) else {}


def round_quantity(
    symbol: str,
    quantity: float,
    rules: dict[str, dict[str, Any]],
    prices: dict[str, float],
) -> float:
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


# ---------------------------------------------------------------------------
# Strategy — per symbol
# ---------------------------------------------------------------------------

def _process_symbol(
    symbol: str,
    live: LiveConfig,
    client: RoostooClient,
    rules: dict[str, dict[str, Any]],
    prices: dict[str, float],
    wallet: dict[str, dict[str, float]],
    state: BotState,
    equity: float,
) -> None:
    sym_state = state.get(symbol)
    pair = RoostooClient.normalize_pair(symbol)
    current_price = prices.get(pair, 0.0)
    today_utc = pd.Timestamp.utcnow().strftime("%Y-%m-%d")

    # -----------------------------------------------------------------------
    # Exit logic: check open position before looking at new signals
    # -----------------------------------------------------------------------
    if sym_state.active:
        asset = symbol.replace("USDT", "")
        asset_balance = float(wallet.get(asset, {}).get("free", 0.0)) + float(wallet.get(asset, {}).get("lock", 0.0))
        target_already_filled = (
            sym_state.target_order_id != ""
            and asset_balance < sym_state.qty * 0.01
        )

        if target_already_filled:
            # Resting target limit filled automatically — just clear state
            sym_state.active = False
            sym_state.qty = 0.0
            sym_state.target_order_id = ""
            return

        exit_price: float | None = None
        reason = ""

        if current_price > 0.0 and current_price <= sym_state.stop_price:
            exit_price = current_price   # price already at/below stop — sell at current market
            reason = "stop"
        elif sym_state.entry_day and today_utc != sym_state.entry_day:
            exit_price = current_price if current_price > 0.0 else sym_state.entry_price
            reason = "eod"

        if exit_price is not None and exit_price > 0.0:
            # Cancel resting target order first so locked qty is released
            if sym_state.target_order_id and live.live_trading:
                client.cancel_order(sym_state.target_order_id)
                sym_state.target_order_id = ""
            # Use free + lock in case cancel hasn't settled yet
            asset_detail = wallet.get(asset, {})
            total_held = float(asset_detail.get("free", 0.0)) + float(asset_detail.get("lock", 0.0))
            qty = round_quantity(symbol, min(total_held, sym_state.qty or total_held), rules, prices)
            if qty > 0.0:
                if live.live_trading:
                    # Limit at exit_price: guaranteed to fill on touch (stop price already
                    # touched since current_price <= stop_price; EOD uses current price)
                    response = client.place_limit_order(
                        symbol=symbol, side="SELL", quantity=qty, price=exit_price
                    )
                    append_trade_log(
                        live, symbol=symbol, side="SELL", reason=reason,
                        requested_qty=qty, response=response,
                    )
                    if _order_succeeded(response):
                        sym_state.active = False
                        sym_state.qty = 0.0
                else:
                    sym_state.active = False
                    sym_state.qty = 0.0
            else:
                sym_state.active = False
                sym_state.qty = 0.0
        return  # don't look for new entry while in (or just exited) position

    # -----------------------------------------------------------------------
    # Entry logic: 24h confirmed breakout
    # -----------------------------------------------------------------------
    try:
        bars = load_completed_5m_bars(live.binance_base_url, symbol, limit=N_BARS + 50)
    except Exception:
        return

    if len(bars) < N_BARS + 1:
        return

    last_bar = bars.iloc[-1]
    last_bar_time = pd.Timestamp(last_bar["open_time"]).isoformat()

    # Skip if we already processed this bar
    if last_bar_time == sym_state.last_bar_time:
        return
    sym_state.last_bar_time = last_bar_time

    # Rolling high over prior N_BARS bars (exclude the signal bar itself)
    prior_bars = bars.iloc[-(N_BARS + 1):-1]
    rolling_high = float(prior_bars["high"].max())

    # Confirmation: bar close must be >= rolling high (not just a wick)
    if float(last_bar["close"]) < rolling_high:
        return

    # Regime gate: skip if close is below 20d EMA.
    # Use 1h bars (480 bars = 20 days) so the EWM is fully warmed up in one API call.
    try:
        htf = fetch_binance_klines(live.binance_base_url, symbol, "1h", 480)
        htf_completed = htf.loc[htf["close_time"] < pd.Timestamp.utcnow()]
        ema_20d = float(htf_completed["close"].ewm(span=480, adjust=False).mean().iloc[-1])
    except Exception:
        return
    if float(last_bar["close"]) <= ema_20d:
        return

    # Signal confirmed — place buy at current market price.
    # Stop and target are anchored to rolling_high (the backtest entry reference),
    # not to current_price, so live R:R matches what was backtested.
    if current_price <= 0.0:
        return

    allocation_usd = equity / len(BREAKOUT_SYMBOLS)
    affordable = min(allocation_usd, free_usd_balance(wallet))
    if affordable <= 0.0:
        return

    qty = round_quantity(symbol, affordable / current_price, rules, prices)
    if qty <= 0.0:
        return

    filled_qty = qty
    target_order_id = ""
    # Anchor stop/target to rolling_high, matching the backtest entry reference.
    stop = rolling_high * (1.0 - STOP_BPS / 10_000.0)
    target = rolling_high * (1.0 + TARGET_BPS / 10_000.0)

    if live.live_trading:
        # Entry: limit at current_price — fills immediately (price is above rolling_high).
        # Stop and target are anchored to rolling_high to match backtest levels.
        entry_response = client.place_limit_order(
            symbol=symbol, side="BUY", quantity=qty, price=current_price
        )
        append_trade_log(
            live, symbol=symbol, side="BUY", reason="breakout",
            requested_qty=qty, response=entry_response,
        )
        if not _order_succeeded(entry_response):
            return
        filled_qty = _filled_quantity(entry_response) or qty

        # Immediately place resting limit sell at target — fires automatically on touch
        target_qty = round_quantity(symbol, filled_qty, rules, prices)
        if target_qty > 0.0:
            target_response = client.place_limit_order(
                symbol=symbol, side="SELL", quantity=target_qty, price=target
            )
            append_trade_log(
                live, symbol=symbol, side="SELL", reason="target_resting",
                requested_qty=target_qty, response=target_response,
            )
            if _order_succeeded(target_response):
                detail = target_response.get("OrderDetail", {})
                target_order_id = str(detail.get("OrderID", "")) if isinstance(detail, dict) else ""

    sym_state.active = True
    sym_state.entry_price = current_price
    sym_state.stop_price = stop
    sym_state.target_price = target
    sym_state.entry_day = today_utc
    sym_state.qty = filled_qty
    sym_state.target_order_id = target_order_id


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_once(live: LiveConfig) -> BotState:
    client = RoostooClient(live)
    if not client.is_configured():
        raise RuntimeError("Roostoo API credentials are not configured.")

    state = load_state(live.state_path)
    rules = exchange_rules(client)
    prices = current_price_map(client)
    wallet = wallet_holdings(client)
    equity = total_equity_usd(wallet, prices)

    for symbol in BREAKOUT_SYMBOLS:
        # Refresh wallet between symbols so free balance stays accurate
        wallet = wallet_holdings(client)
        _process_symbol(symbol, live, client, rules, prices, wallet, state, equity)

    save_state(live.state_path, state)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="24h confirmed breakout strategy — Roostoo live execution."
    )
    parser.add_argument("--polling-seconds", type=int, default=60)
    parser.add_argument("--state-path", type=Path, default=None)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--competition", action="store_true",
                        help="Use competition credentials and state path.")
    parser.add_argument("--reset-state", action="store_true",
                        help="Delete the selected state file before starting.")
    parser.add_argument("--run-once", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    live = build_live_config(
        competition=args.competition,
        polling_seconds=args.polling_seconds,
        state_path=args.state_path,
        live_trading=args.live,
    )
    if args.reset_state and live.state_path.exists():
        live.state_path.unlink()

    if args.run_once:
        state = run_once(live)
        print(json.dumps({"mode": live.bot_mode, "state_path": str(live.state_path),
                          **_state_to_dict(state)}, indent=2))
        return 0

    while True:
        state = run_once(live)
        print(json.dumps({"mode": live.bot_mode, "state_path": str(live.state_path),
                          **_state_to_dict(state)}, indent=2))
        time.sleep(live.polling_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
