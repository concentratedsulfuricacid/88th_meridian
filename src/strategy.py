"""Validated 50/50 submission strategy wrapper."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .runtime.lead_lag import LeadLagConfig, load_panel
from .runtime.walk_forward import RollingBlendResult, evaluate_walk_forward
from .runtime.weekly_vol import WeeklyVolConfig, load_bars


@dataclass(frozen=True)
class SubmissionConfig:
    """Configuration for the validated competition candidate."""

    weekly_vol_folder: Path = Path("data/binance_klines")
    lead_lag_folder: Path = Path("data/binance_5m")
    warmup_days: int = 90
    test_days: int = 7
    step_days: int = 7


def build_configs(config: SubmissionConfig) -> tuple[WeeklyVolConfig, LeadLagConfig]:
    """Build the exact validated sleeve configs."""
    weekly_vol = WeeklyVolConfig(
        symbol="ETHUSDT",
        folder=config.weekly_vol_folder,
        bar_rule="4h",
        regime_fast_ema=20,
        regime_slow_ema=100,
        pullback_lookback_bars=5,
        volatility_horizon="weekly",
        entry_mode="market",
        entry_sigma=0.25,
        stop_sigma=1.0,
        take_profit_sigma=1.25,
        max_hold_bars=42,
        fee_rate=0.0005,
    )
    lead_lag = LeadLagConfig(
        leaders=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
        laggers=("FETUSDT",),
        lookback_bars=3,
        hold_bars=12,
        leader_threshold=0.0045,
        gap_threshold=0.003,
        beta_min_periods=288,
        fee_rate=0.0005,
        max_positions=1,
    )
    return weekly_vol, lead_lag


def evaluate_submission(config: SubmissionConfig | None = None) -> RollingBlendResult:
    """Evaluate the validated submission strategy on the rolling OOS harness."""
    active = config or SubmissionConfig()
    weekly_vol, lead_lag = build_configs(active)
    weekly_vol_bars = load_bars(weekly_vol.folder, weekly_vol.symbol, weekly_vol.bar_rule)
    lead_lag_panel = load_panel(active.lead_lag_folder, (*lead_lag.leaders, *lead_lag.laggers))
    return evaluate_walk_forward(
        weekly_vol_bars,
        lead_lag_panel,
        weekly_vol,
        lead_lag,
        warmup_days=active.warmup_days,
        test_days=active.test_days,
        step_days=active.step_days,
    )


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Evaluate the validated 50/50 submission strategy.")
    parser.add_argument("--weekly-vol-folder", type=Path, default=Path("data/binance_klines"))
    parser.add_argument("--lead-lag-folder", type=Path, default=Path("data/binance_5m"))
    parser.add_argument("--warmup-days", type=int, default=90)
    parser.add_argument("--test-days", type=int, default=7)
    parser.add_argument("--step-days", type=int, default=7)
    return parser.parse_args()


def main() -> int:
    """Run the strategy wrapper CLI."""
    args = parse_args()
    result = evaluate_submission(
        SubmissionConfig(
            weekly_vol_folder=args.weekly_vol_folder,
            lead_lag_folder=args.lead_lag_folder,
            warmup_days=args.warmup_days,
            test_days=args.test_days,
            step_days=args.step_days,
        )
    )
    print(
        f"windows={result.windows} stitched_return_pct={result.stitched_return_pct:.2f} "
        f"mean_weekly_return_pct={result.mean_window_return_pct:.2f} "
        f"median_weekly_return_pct={result.median_window_return_pct:.2f} "
        f"positive_rate={result.positive_window_rate:.2%}"
    )
    print(
        f"pct_windows_with_trade={result.pct_windows_with_trade:.2%} "
        f"mean_trades_per_window={result.mean_trades_per_window:.2f} "
        f"best_week_pct={result.best_window_return_pct:.2f} "
        f"worst_week_pct={result.worst_window_return_pct:.2f}"
    )
    print(
        f"weekly_vol_mean_week_pct={result.weekly_vol_mean_window_return_pct:.2f} "
        f"lead_lag_mean_week_pct={result.lead_lag_mean_window_return_pct:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
