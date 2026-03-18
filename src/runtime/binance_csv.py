"""Local Binance CSV loaders used by the submission package."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


BINANCE_KLINE_COLUMNS = [
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
]


def _normalize_timestamp(values: pd.Series) -> pd.Series:
    sample = int(values.iloc[0])
    unit = "us" if sample >= 10_000_000_000_000 else "ms"
    return pd.to_datetime(values, unit=unit, utc=True)


def load_binance_klines(path: str | Path) -> pd.DataFrame:
    """Load Binance public-data klines with or without a header row."""
    data = pd.read_csv(path)
    if "open_time" not in data.columns:
        data = pd.read_csv(path, header=None, names=BINANCE_KLINE_COLUMNS)

    frame = data.copy()
    frame["open_time"] = _normalize_timestamp(frame["open_time"])
    frame["close_time"] = _normalize_timestamp(frame["close_time"])
    for column in (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
    ):
        frame[column] = frame[column].astype(float)
    frame["number_of_trades"] = frame["number_of_trades"].astype(int)
    return frame.sort_values("open_time").reset_index(drop=True)
