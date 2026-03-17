"""Rolling normalization and starter signal score helpers."""

from __future__ import annotations

import math
from collections.abc import Sequence


def rolling_zscore(values: Sequence[float]) -> float:
    """Compute the z-score of the most recent value against the provided window."""
    if not values:
        return 0.0
    latest = values[-1]
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance)
    if math.isclose(std, 0.0):
        return 0.0
    return (latest - mean) / std


def starter_signal_score(
    ofi_values: Sequence[float],
    weighted_imbalance: float,
    afi_values: Sequence[float],
    spread_values: Sequence[float],
    alpha: float = 1.0,
    beta: float = 1.0,
    gamma: float = 1.0,
    delta: float = 1.0,
) -> float:
    """Compute the starter score ``alpha*z(OFI) + beta*imbalance + gamma*z(AFI) - delta*z(spread)``."""
    return (
        (alpha * rolling_zscore(ofi_values))
        + (beta * weighted_imbalance)
        + (gamma * rolling_zscore(afi_values))
        - (delta * rolling_zscore(spread_values))
    )
