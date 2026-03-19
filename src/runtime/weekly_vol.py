"""Local weekly-vol sleeve helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .binance_csv import load_binance_klines


@dataclass(frozen=True)
class WeeklyVolConfig:
    """Configuration for the weekly-vol pullback sleeve."""

    symbol: str = "ETHUSDT"
    folder: Path = Path("data/binance_klines")
    bar_rule: str = "4h"
    regime_fast_ema: int = 20
    regime_slow_ema: int = 100
    pullback_lookback_bars: int = 5
    volatility_horizon: str = "weekly"
    entry_mode: str = "market"
    entry_sigma: float = 0.25
    stop_sigma: float = 1.0
    take_profit_sigma: float = 1.25
    max_hold_bars: int = 42
    touch_order_bars: int = 1
    fee_rate: float = 0.0005


def load_bars(folder: Path, symbol: str, bar_rule: str) -> pd.DataFrame:
    """Load hourly Binance data and resample to the requested bar rule."""
    files = sorted(folder.glob(f"{symbol}-1h-20*.csv"))
    if not files:
        raise FileNotFoundError(f"missing 1h files for {symbol} in {folder}")
    frame = (
        pd.concat([load_binance_klines(path)[["open_time", "open", "high", "low", "close"]] for path in files], ignore_index=True)
        .sort_values("open_time")
        .drop_duplicates("open_time")
        .set_index("open_time")
    )
    bars = pd.DataFrame(
        {
            "open": frame["open"].resample(bar_rule).first(),
            "high": frame["high"].resample(bar_rule).max(),
            "low": frame["low"].resample(bar_rule).min(),
            "close": frame["close"].resample(bar_rule).last(),
        }
    ).dropna()
    return bars.reset_index()


def prepare_bars(bars: pd.DataFrame, config: WeeklyVolConfig) -> pd.DataFrame:
    """Add the derived features required by the sleeve."""
    window = 6 if config.volatility_horizon == "daily" else 42
    close = bars["close"]
    returns = close.pct_change().fillna(0.0)
    frame = bars.copy()
    frame["ema_fast"] = close.ewm(span=config.regime_fast_ema, adjust=False).mean()
    frame["ema_slow"] = close.ewm(span=config.regime_slow_ema, adjust=False).mean()
    frame["recent_high"] = frame["high"].shift(1).rolling(config.pullback_lookback_bars).max()
    sigma_bar = returns.rolling(window).std()
    frame["vol_move"] = frame["close"] * sigma_bar * math.sqrt(window)
    return frame
