"""Local lead-lag sleeve helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .binance_csv import load_binance_klines


@dataclass(frozen=True)
class LeadLagConfig:
    """Configuration for the 5-minute lead-lag sleeve."""

    leaders: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    laggers: tuple[str, ...] = ("FETUSDT",)
    lookback_bars: int = 3
    hold_bars: int = 12
    leader_threshold: float = 0.0045
    gap_threshold: float = 0.003
    beta_min_periods: int = 288
    fee_rate: float = 0.0005
    max_positions: int = 1
    initial_cash: float = 1.0


def _load_symbol(symbol: str, folder: Path) -> pd.DataFrame:
    files = sorted(folder.glob(f"{symbol}-5m-20*.csv"))
    if not files:
        raise FileNotFoundError(f"missing 5m data for {symbol} in {folder}")
    frame = (
        pd.concat([load_binance_klines(path)[["open_time", "open", "close"]] for path in files], ignore_index=True)
        .sort_values("open_time")
        .drop_duplicates("open_time")
    )
    return frame.rename(columns={"open": f"{symbol}_open", "close": f"{symbol}_close"})


def load_panel(folder: Path, symbols: tuple[str, ...]) -> pd.DataFrame:
    """Load and align a common 5-minute panel."""
    panel: pd.DataFrame | None = None
    for symbol in symbols:
        frame = _load_symbol(symbol, folder)
        panel = frame if panel is None else panel.merge(frame, on="open_time", how="inner")
    assert panel is not None
    return panel.sort_values("open_time").reset_index(drop=True)


def _causal_beta(target_return: pd.Series, leader_return: pd.Series, min_periods: int) -> pd.Series:
    covariance = target_return.expanding(min_periods=min_periods).cov(leader_return)
    variance = leader_return.expanding(min_periods=min_periods).var()
    return (covariance / variance).shift(1).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)


def build_signals(panel: pd.DataFrame, config: LeadLagConfig) -> tuple[pd.Series, dict[str, pd.Series]]:
    """Compute the leader return series and lagger gap series."""
    leader_return = sum(panel[f"{symbol}_close"].pct_change(config.lookback_bars) for symbol in config.leaders) / len(config.leaders)
    gaps: dict[str, pd.Series] = {}
    for target in config.laggers:
        target_return = panel[f"{target}_close"].pct_change(config.lookback_bars)
        beta = _causal_beta(target_return, leader_return, config.beta_min_periods)
        gaps[target] = leader_return - (beta * target_return)
    return leader_return, gaps
