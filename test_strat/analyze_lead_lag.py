#!/usr/bin/env python3
"""Offline lead/lag analysis for Roostoo versus Binance quote captures."""

from __future__ import annotations

import argparse
import bisect
import collections
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_strat.storage import iter_quotes
from test_strat.types import QuoteSample


@dataclass(frozen=True)
class HorizonStats:
    """Summary metrics for one prediction horizon."""

    horizon_ms: int
    sample_count: int
    correlation: float
    mean_edge: float
    mean_future_return: float
    mean_future_return_when_edge_pos: float
    mean_future_return_when_edge_neg: float
    hit_rate: float


def parse_args() -> argparse.Namespace:
    """Parse CLI options for the lead/lag analyzer."""
    parser = argparse.ArgumentParser(
        description="Analyze whether Roostoo quote deviations lead future Binance returns."
    )
    parser.add_argument("input", type=Path, help="JSONL capture from collect_lead_lag.py")
    parser.add_argument("--grid-ms", type=int, default=50, help="Sampling grid in milliseconds.")
    parser.add_argument(
        "--horizons-ms",
        default="50,100,250,500,1000",
        help="Comma-separated future-return horizons in milliseconds.",
    )
    parser.add_argument("--max-stale-ms", type=int, default=1000)
    parser.add_argument(
        "--time-basis",
        choices=("recv", "event", "hybrid"),
        default="hybrid",
        help="Use receive time, event time, or event-when-available for alignment.",
    )
    return parser.parse_args()


def sample_ts_ms(sample: QuoteSample, time_basis: str) -> int:
    """Choose the timestamp used for cross-venue alignment."""
    if time_basis == "recv":
        return sample.recv_ts_ms
    if time_basis == "event":
        return sample.event_ts_ms
    return sample.event_ts_ms if sample.event_ts_ms else sample.recv_ts_ms


def filter_and_sort(samples: list[QuoteSample], source: str, time_basis: str) -> list[QuoteSample]:
    """Keep one venue's usable quote samples in time order."""
    usable = [sample for sample in samples if sample.source == source and sample.bid > 0.0 and sample.ask > 0.0]
    return sorted(usable, key=lambda sample: sample_ts_ms(sample, time_basis))


def locf_at(samples: list[QuoteSample], sample_times: list[int], target_ts_ms: int, max_stale_ms: int) -> QuoteSample | None:
    """Return the last quote at or before the requested time if it is still fresh."""
    index = bisect.bisect_right(sample_times, target_ts_ms) - 1
    if index < 0:
        return None
    sample = samples[index]
    if target_ts_ms - sample_times[index] > max_stale_ms:
        return None
    return sample


def pearson(xs: list[float], ys: list[float]) -> float:
    """Compute Pearson correlation, returning 0 when variance vanishes."""
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    mean_x = mean(xs)
    mean_y = mean(ys)
    centered_x = [value - mean_x for value in xs]
    centered_y = [value - mean_y for value in ys]
    numerator = sum(x * y for x, y in zip(centered_x, centered_y, strict=True))
    denom_x = math.sqrt(sum(x * x for x in centered_x))
    denom_y = math.sqrt(sum(y * y for y in centered_y))
    if math.isclose(denom_x, 0.0) or math.isclose(denom_y, 0.0):
        return 0.0
    return numerator / (denom_x * denom_y)


def future_mid(sample_times: list[int], mids: list[float], target_ts_ms: int) -> float | None:
    """Return the first observed mid at or after the requested future time."""
    index = bisect.bisect_left(sample_times, target_ts_ms)
    if index >= len(mids):
        return None
    return mids[index]


def analyze_capture(
    samples: list[QuoteSample],
    grid_ms: int,
    horizons_ms: list[int],
    max_stale_ms: int,
    time_basis: str,
) -> list[HorizonStats]:
    """Compute lead/lag metrics across several future-return horizons."""
    roostoo = filter_and_sort(samples, "roostoo", time_basis)
    binance = filter_and_sort(samples, "binance", time_basis)
    if not roostoo or not binance:
        counts = collections.Counter(sample.source for sample in samples)
        raise ValueError(f"capture must contain both roostoo and binance quotes; found sources={dict(counts)}")

    roostoo_times = [sample_ts_ms(sample, time_basis) for sample in roostoo]
    binance_times = [sample_ts_ms(sample, time_basis) for sample in binance]
    binance_mids = [sample.mid for sample in binance]

    start_ts_ms = max(roostoo_times[0], binance_times[0])
    end_ts_ms = min(roostoo_times[-1], binance_times[-1])
    if end_ts_ms <= start_ts_ms:
        raise ValueError("no overlapping time window between Roostoo and Binance samples")

    aligned_edges: list[tuple[int, float, float]] = []
    current_ts_ms = start_ts_ms
    while current_ts_ms <= end_ts_ms:
        roostoo_quote = locf_at(roostoo, roostoo_times, current_ts_ms, max_stale_ms)
        binance_quote = locf_at(binance, binance_times, current_ts_ms, max_stale_ms)
        if roostoo_quote and binance_quote:
            aligned_edges.append((current_ts_ms, roostoo_quote.mid - binance_quote.mid, binance_quote.mid))
        current_ts_ms += grid_ms

    if not aligned_edges:
        raise ValueError("no aligned samples survived the stale-quote filter")

    results: list[HorizonStats] = []
    for horizon_ms in horizons_ms:
        edges: list[float] = []
        future_returns: list[float] = []

        for current_ts_ms, edge, current_binance_mid in aligned_edges:
            future_binance_mid = future_mid(binance_times, binance_mids, current_ts_ms + horizon_ms)
            if future_binance_mid is None:
                continue
            edges.append(edge)
            future_returns.append(future_binance_mid - current_binance_mid)

        if not edges:
            results.append(
                HorizonStats(
                    horizon_ms=horizon_ms,
                    sample_count=0,
                    correlation=0.0,
                    mean_edge=0.0,
                    mean_future_return=0.0,
                    mean_future_return_when_edge_pos=0.0,
                    mean_future_return_when_edge_neg=0.0,
                    hit_rate=0.0,
                )
            )
            continue

        positive_returns = [ret for edge, ret in zip(edges, future_returns, strict=True) if edge > 0.0]
        negative_returns = [ret for edge, ret in zip(edges, future_returns, strict=True) if edge < 0.0]
        hits = [
            1.0
            for edge, ret in zip(edges, future_returns, strict=True)
            if (edge > 0.0 and ret > 0.0) or (edge < 0.0 and ret < 0.0)
        ]

        results.append(
            HorizonStats(
                horizon_ms=horizon_ms,
                sample_count=len(edges),
                correlation=pearson(edges, future_returns),
                mean_edge=mean(edges),
                mean_future_return=mean(future_returns),
                mean_future_return_when_edge_pos=mean(positive_returns) if positive_returns else 0.0,
                mean_future_return_when_edge_neg=mean(negative_returns) if negative_returns else 0.0,
                hit_rate=sum(hits) / len(edges),
            )
        )

    return results


def main() -> int:
    """Run the analysis and print a compact tabular summary."""
    args = parse_args()
    horizons_ms = [int(part.strip()) for part in args.horizons_ms.split(",") if part.strip()]
    samples = list(iter_quotes(args.input))
    stats = analyze_capture(
        samples=samples,
        grid_ms=args.grid_ms,
        horizons_ms=horizons_ms,
        max_stale_ms=args.max_stale_ms,
        time_basis=args.time_basis,
    )

    print(f"Input: {args.input}")
    print(f"Grid: {args.grid_ms} ms  Max stale: {args.max_stale_ms} ms  Time basis: {args.time_basis}")
    print(
        "horizon_ms sample_count correlation mean_edge mean_future_return "
        "mean_return_edge_pos mean_return_edge_neg hit_rate"
    )
    for item in stats:
        print(
            f"{item.horizon_ms:>10} {item.sample_count:>12} "
            f"{item.correlation:>11.6f} {item.mean_edge:>9.4f} {item.mean_future_return:>18.4f} "
            f"{item.mean_future_return_when_edge_pos:>20.4f} {item.mean_future_return_when_edge_neg:>20.4f} "
            f"{item.hit_rate:>8.4f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
