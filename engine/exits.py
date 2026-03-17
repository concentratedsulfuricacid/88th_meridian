"""Pluggable exit policies for the trading engine."""

from __future__ import annotations

import math

from engine.types import EngineConfig, EngineFeatureVector


def deterioration_exit_long(
    features: EngineFeatureVector,
    config: EngineConfig,
    position_age_seconds: float | None,
    position_entry_price: float | None,
    profit_floor_wait_seconds: float | None,
) -> tuple[bool, str]:
    """Exit when directional support, quality, or timing deteriorates."""
    del position_age_seconds, position_entry_price, profit_floor_wait_seconds
    if features.ofi_zscore < config.exit_ofi_zscore_threshold:
        return True, "OFI z-score deteriorated"
    if features.weighted_top_n_imbalance < config.exit_imbalance_threshold:
        return True, "weighted imbalance lost bullish support"
    if not features.quality_pass:
        return True, "quality filters failed"
    if not features.retest_long and not features.pullback_long and features.price_response < 0.0:
        return True, "timing support failed and price response turned negative"
    return False, "long rationale remains intact"


def score_fade_exit_long(
    features: EngineFeatureVector,
    config: EngineConfig,
    position_age_seconds: float | None,
    position_entry_price: float | None,
    profit_floor_wait_seconds: float | None,
) -> tuple[bool, str]:
    """Exit when long confluence score fades below the configured exit threshold."""
    del position_age_seconds, position_entry_price, profit_floor_wait_seconds
    if features.long_score < config.min_exit_confluence_score:
        return True, "long confluence score faded below the exit threshold"
    return False, "long confluence score remains above the exit threshold"


def _modeled_exit_price(features: EngineFeatureVector, config: EngineConfig) -> float:
    if config.exit_fill_reference == "mid":
        return features.mid_price
    if config.exit_fill_reference == "ask":
        return features.best_ask
    return features.best_bid


def _profit_floor_price(entry_price: float, config: EngineConfig) -> float:
    break_even = entry_price * (1.0 + config.entry_fee_rate) / max(1.0 - config.exit_fee_rate, 1e-12)
    return break_even * (1.0 + (config.min_profit_buffer_bps / 10000.0))


def score_fade_with_profit_floor_exit_long(
    features: EngineFeatureVector,
    config: EngineConfig,
    position_age_seconds: float | None,
    position_entry_price: float | None,
    profit_floor_wait_seconds: float | None,
) -> tuple[bool, str]:
    """Exit on score fade only if the modeled exit price clears fees and profit buffer."""
    del position_age_seconds
    if position_entry_price is None or math.isclose(position_entry_price, 0.0):
        return False, "entry price unavailable for profit-floor exit"
    if features.long_score >= config.min_exit_confluence_score:
        return False, "long confluence score remains above the exit threshold"

    modeled_exit_price = _modeled_exit_price(features, config)
    minimum_exit_price = _profit_floor_price(position_entry_price, config)
    if modeled_exit_price >= minimum_exit_price:
        return True, (
            f"long score faded below exit threshold and modeled exit price {modeled_exit_price:.8f} "
            f"cleared profit floor {minimum_exit_price:.8f}"
        )
    if profit_floor_wait_seconds is not None and profit_floor_wait_seconds >= config.max_profit_floor_wait_seconds:
        return True, (
            f"profit floor wait timeout of {config.max_profit_floor_wait_seconds:.1f}s elapsed; "
            f"modeled exit price {modeled_exit_price:.8f} did not clear {minimum_exit_price:.8f}"
        )
    return False, (
        f"long score faded but modeled exit price {modeled_exit_price:.8f} "
        f"has not cleared profit floor {minimum_exit_price:.8f}"
    )


def hard_flat_exit_long(
    features: EngineFeatureVector,
    config: EngineConfig,
    position_age_seconds: float | None,
    position_entry_price: float | None,
    profit_floor_wait_seconds: float | None,
) -> tuple[bool, str]:
    """Never exit automatically; useful as a control policy."""
    del features, config, position_age_seconds, position_entry_price, profit_floor_wait_seconds
    return False, "hard-flat exit disabled"


def time_based_exit_long(
    features: EngineFeatureVector,
    config: EngineConfig,
    position_age_seconds: float | None,
    position_entry_price: float | None,
    profit_floor_wait_seconds: float | None,
) -> tuple[bool, str]:
    """Exit once the configured holding time has elapsed."""
    del features, position_entry_price, profit_floor_wait_seconds
    if position_age_seconds is None:
        return False, "entry timestamp unavailable for time-based exit"
    if position_age_seconds >= config.max_holding_seconds:
        return True, f"maximum holding time of {config.max_holding_seconds:.1f}s elapsed"
    return False, f"holding for {position_age_seconds:.3f}s of {config.max_holding_seconds:.1f}s max"


EXIT_POLICIES = {
    "deterioration": deterioration_exit_long,
    "score_fade": score_fade_exit_long,
    "score_fade_with_profit_floor": score_fade_with_profit_floor_exit_long,
    "hold_forever": hard_flat_exit_long,
    "time_based": time_based_exit_long,
}
