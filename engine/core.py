"""Feature generation and confluence-based state machine for paper trading."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from engine.adapters import snapshot_from_payload, trades_from_payloads
from engine.exits import EXIT_POLICIES
from engine.types import EngineConfig, EngineFeatureVector, EngineSignal
from indicator import (
    OrderBookSnapshot,
    TradePrint,
    breakout_retest_state,
    current_mid,
    depth_metrics,
    level_1_imbalance,
    microprice,
    mlofi_increment,
    price_response,
    pullback_stabilization_state,
    rolling_volatility,
    rolling_zscore,
    spread,
    top_n_imbalance,
    vwap_buy_to_mid,
    vwap_sell_to_mid,
    weighted_top_n_imbalance,
)


class FeatureBuilder:
    """Stateful feature builder for live order book updates."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.previous_snapshot: OrderBookSnapshot | None = None
        self.snapshot_history: deque[OrderBookSnapshot] = deque(maxlen=max(self.config.breakout_lookback + 5, self.config.volatility_window + 5))
        self.mid_history: deque[float] = deque(maxlen=max(self.config.breakout_lookback + 5, self.config.volatility_window + 5))
        self.ofi_history: deque[float] = deque(maxlen=self.config.normalization_window)
        self.volatility_history: deque[float] = deque(maxlen=self.config.normalization_window)

    def build_from_payload(
        self,
        payload: dict[str, object],
        trade_payloads: Sequence[dict[str, object]] | None = None,
        ts: float | None = None,
    ) -> EngineFeatureVector:
        """Build a feature vector from a raw order book payload."""
        snapshot = snapshot_from_payload(payload, ts=ts)
        _ = (
            trades_from_payloads(
                trade_payloads or [],
                buy_side_labels=self.config.buy_side_labels,
                sell_side_labels=self.config.sell_side_labels,
            )
            if trade_payloads is not None
            else []
        )
        return self.build(snapshot)

    def build(
        self,
        snapshot: OrderBookSnapshot,
        trades: Sequence[TradePrint] | None = None,
    ) -> EngineFeatureVector:
        """Build a feature vector from a normalized order book snapshot."""
        del trades
        best_bid = snapshot.bids[0].price
        best_ask = snapshot.asks[0].price
        current_mid_price = current_mid(snapshot)
        current_microprice = microprice(snapshot)
        current_spread = spread(snapshot)
        current_spread_bps = (current_spread / current_mid_price) * 10000.0 if current_mid_price else 0.0
        current_l1_imbalance = level_1_imbalance(snapshot)
        current_top_n_imbalance = top_n_imbalance(snapshot, self.config.imbalance_depth)
        current_weighted_imbalance = weighted_top_n_imbalance(snapshot, self.config.imbalance_depth)
        current_vwap_buy_to_mid = vwap_buy_to_mid(snapshot, self.config.vwap_depth)
        current_vwap_sell_to_mid = vwap_sell_to_mid(snapshot, self.config.vwap_depth)
        depth_bid_qty, depth_ask_qty, depth_total_qty, depth_total_notional = depth_metrics(
            snapshot, self.config.liquidity_depth
        )

        if self.previous_snapshot is None:
            current_ofi = 0.0
            current_mlofi = tuple(0.0 for _ in range(self.config.mlofi_levels))
        else:
            current_mlofi = tuple(mlofi_increment(self.previous_snapshot, snapshot, self.config.mlofi_levels))
            current_ofi = current_mlofi[0]

        self.snapshot_history.append(snapshot)
        self.mid_history.append(current_mid_price)
        self.ofi_history.append(current_ofi)

        volatility_window = list(self.mid_history)[-self.config.volatility_window :]
        current_volatility = rolling_volatility(volatility_window)
        self.volatility_history.append(current_volatility)

        current_ofi_zscore = rolling_zscore(list(self.ofi_history))
        current_volatility_zscore = rolling_zscore(list(self.volatility_history))
        current_price_response = price_response(list(self.mid_history), self.config.price_response_lookback)
        breakout_long, breakout_short, retest_long, retest_short = breakout_retest_state(
            list(self.mid_history),
            snapshot,
            self.config.breakout_lookback,
        )
        pullback_long, pullback_short = pullback_stabilization_state(
            list(self.snapshot_history),
            list(self.mid_history),
            self.config.pullback_lookback,
        )

        quality_pass = (
            current_spread_bps <= self.config.max_spread_bps
            and current_volatility_zscore <= self.config.max_volatility_zscore
            and depth_total_qty >= self.config.min_depth_total_qty
            and depth_total_notional >= self.config.min_depth_total_notional
        )
        long_score = 0
        long_score += 2 if current_ofi_zscore > self.config.ofi_zscore_threshold else 0
        long_score += 1 if current_weighted_imbalance > self.config.imbalance_threshold else 0
        long_score += 1 if current_spread_bps <= self.config.max_spread_bps else 0
        long_score += 1 if current_volatility_zscore <= self.config.max_volatility_zscore else 0
        long_score += 1 if depth_total_notional >= self.config.min_depth_total_notional else 0
        long_score += 1 if current_price_response > self.config.min_price_response else 0
        long_score += 1 if (breakout_long or retest_long) else 0
        long_score += 1 if pullback_long else 0

        short_score = 0
        short_score += 2 if current_ofi_zscore < -self.config.ofi_zscore_threshold else 0
        short_score += 1 if current_weighted_imbalance < -self.config.imbalance_threshold else 0
        short_score += 1 if current_spread_bps <= self.config.max_spread_bps else 0
        short_score += 1 if current_volatility_zscore <= self.config.max_volatility_zscore else 0
        short_score += 1 if depth_total_notional >= self.config.min_depth_total_notional else 0
        short_score += 1 if current_price_response < -self.config.min_price_response else 0
        short_score += 1 if (breakout_short or retest_short) else 0
        short_score += 1 if pullback_short else 0

        long_timing_ready = (breakout_long or retest_long or pullback_long) if self.config.require_timing_confirmation else True
        short_timing_ready = (breakout_short or retest_short or pullback_short) if self.config.require_timing_confirmation else True
        long_ready = quality_pass and long_timing_ready and long_score >= self.config.min_entry_confluence_score
        short_ready = quality_pass and short_timing_ready and short_score >= self.config.min_entry_confluence_score

        market_state = "neutral"
        if quality_pass and long_score > short_score and current_weighted_imbalance > 0.0:
            market_state = "bullish"
        elif quality_pass and short_score > long_score and current_weighted_imbalance < 0.0:
            market_state = "bearish"
        elif not quality_pass:
            market_state = "blocked"

        self.previous_snapshot = snapshot

        return EngineFeatureVector(
            ts=snapshot.ts,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=current_mid_price,
            microprice=current_microprice,
            spread=current_spread,
            spread_bps=current_spread_bps,
            l1_imbalance=current_l1_imbalance,
            top_n_imbalance=current_top_n_imbalance,
            weighted_top_n_imbalance=current_weighted_imbalance,
            ofi=current_ofi,
            ofi_zscore=current_ofi_zscore,
            mlofi=current_mlofi,
            volatility=current_volatility,
            volatility_zscore=current_volatility_zscore,
            depth_bid_qty=depth_bid_qty,
            depth_ask_qty=depth_ask_qty,
            depth_total_qty=depth_total_qty,
            depth_total_notional=depth_total_notional,
            price_response=current_price_response,
            breakout_long=breakout_long,
            breakout_short=breakout_short,
            retest_long=retest_long,
            retest_short=retest_short,
            pullback_long=pullback_long,
            pullback_short=pullback_short,
            vwap_buy_to_mid=current_vwap_buy_to_mid,
            vwap_sell_to_mid=current_vwap_sell_to_mid,
            long_score=long_score,
            short_score=short_score,
            quality_pass=quality_pass,
            long_ready=long_ready,
            short_ready=short_ready,
            market_state=market_state,
        )


class SimpleTradingEngine:
    """Confluence-based state machine using directional, quality, and timing indicators."""

    def __init__(self, config: EngineConfig | None = None) -> None:
        self.config = config or EngineConfig()
        self.feature_builder = FeatureBuilder(self.config)
        self.current_position = 0
        self.last_trade_ts: float | None = None
        self.position_entry_ts: float | None = None
        self.position_entry_price: float | None = None
        self.profit_floor_wait_start_ts: float | None = None
        if self.config.exit_policy not in EXIT_POLICIES:
            raise ValueError(f"Unknown exit policy: {self.config.exit_policy}")

    def on_snapshot(
        self,
        snapshot: OrderBookSnapshot,
        trades: Sequence[TradePrint] | None = None,
    ) -> tuple[EngineFeatureVector, EngineSignal]:
        """Update the engine with a new normalized snapshot and return features plus a decision."""
        features = self.feature_builder.build(snapshot, trades=trades)
        signal = self._decide(features)
        self._apply_signal_state(signal, features.ts, features)
        return features, signal

    def on_payload(
        self,
        payload: dict[str, object],
        trade_payloads: Sequence[dict[str, object]] | None = None,
        ts: float | None = None,
    ) -> tuple[EngineFeatureVector, EngineSignal]:
        """Update the engine from raw payloads and return features plus a decision."""
        features = self.feature_builder.build_from_payload(payload, trade_payloads=trade_payloads, ts=ts)
        signal = self._decide(features)
        self._apply_signal_state(signal, features.ts, features)
        return features, signal

    def _modeled_fill_price(self, features: EngineFeatureVector, reference: str) -> float:
        if reference == "mid":
            return features.mid_price
        if reference == "ask":
            return features.best_ask
        return features.best_bid

    def _apply_signal_state(self, signal: EngineSignal, ts: float | None, features: EngineFeatureVector) -> None:
        self.current_position = signal.target_position
        if signal.action == "enter_long":
            self.last_trade_ts = ts
            self.position_entry_ts = ts
            self.position_entry_price = self._modeled_fill_price(features, self.config.entry_fill_reference)
            self.profit_floor_wait_start_ts = None
        elif signal.action == "exit_long" and signal.target_position == 0:
            self.last_trade_ts = ts
            self.position_entry_ts = None
            self.position_entry_price = None
            self.profit_floor_wait_start_ts = None
        elif signal.action == "stay_flat" and signal.target_position == 0:
            self.position_entry_ts = None
            self.position_entry_price = None
            self.profit_floor_wait_start_ts = None

    def _decide(self, features: EngineFeatureVector) -> EngineSignal:
        if self.current_position == 0:
            cooldown_remaining = None
            if (
                self.last_trade_ts is not None
                and features.ts is not None
                and self.config.trade_cooldown_seconds > 0.0
            ):
                cooldown_remaining = self.config.trade_cooldown_seconds - (features.ts - self.last_trade_ts)
            if cooldown_remaining is not None and cooldown_remaining > 0.0:
                return EngineSignal(
                    ts=features.ts,
                    action="stay_flat",
                    target_position=0,
                    score=max(features.long_score, features.short_score),
                    reason=f"trade cooldown active: {cooldown_remaining:.3f}s remaining",
                    market_state=features.market_state,
                )
            if features.long_ready and features.long_score >= features.short_score:
                return EngineSignal(
                    ts=features.ts,
                    action="enter_long",
                    target_position=1,
                    score=features.long_score,
                    reason="long directional signals, quality filters, and timing confirmation aligned",
                    market_state=features.market_state,
                )
            if self.config.allow_shorting and features.short_ready and features.short_score > features.long_score:
                return EngineSignal(
                    ts=features.ts,
                    action="enter_short",
                    target_position=-1,
                    score=features.short_score,
                    reason="short directional signals, quality filters, and timing confirmation aligned",
                    market_state=features.market_state,
                )
            return EngineSignal(
                ts=features.ts,
                action="stay_flat",
                target_position=0,
                score=max(features.long_score, features.short_score),
                reason="confluence score or confirmation was insufficient",
                market_state=features.market_state,
            )

        if self.current_position > 0:
            position_age_seconds = None
            profit_floor_wait_seconds = None
            if self.position_entry_ts is not None and features.ts is not None:
                position_age_seconds = features.ts - self.position_entry_ts
            if self.config.exit_policy == "score_fade_with_profit_floor" and features.long_score < self.config.min_exit_confluence_score:
                if self.profit_floor_wait_start_ts is None:
                    self.profit_floor_wait_start_ts = features.ts
                if self.profit_floor_wait_start_ts is not None and features.ts is not None:
                    profit_floor_wait_seconds = features.ts - self.profit_floor_wait_start_ts
            else:
                self.profit_floor_wait_start_ts = None
            should_exit, exit_reason = EXIT_POLICIES[self.config.exit_policy](
                features,
                self.config,
                position_age_seconds,
                self.position_entry_price,
                profit_floor_wait_seconds,
            )
            if should_exit:
                return EngineSignal(
                    ts=features.ts,
                    action="exit_long",
                    target_position=0,
                    score=features.long_score,
                    reason=exit_reason,
                    market_state=features.market_state,
                )
            return EngineSignal(
                ts=features.ts,
                action="hold_long",
                target_position=1,
                score=features.long_score,
                reason=exit_reason,
                market_state=features.market_state,
            )

        if not self.config.allow_shorting:
            return EngineSignal(
                ts=features.ts,
                action="stay_flat",
                target_position=0,
                score=features.short_score,
                reason="shorting disabled in spot-safe mode",
                market_state=features.market_state,
            )

        short_reason_gone = (
            features.ofi_zscore > -self.config.exit_ofi_zscore_threshold
            or features.weighted_top_n_imbalance > -self.config.exit_imbalance_threshold
            or not features.quality_pass
            or (not features.retest_short and not features.pullback_short and features.price_response > 0.0)
        )
        if short_reason_gone:
            return EngineSignal(
                ts=features.ts,
                action="exit_short",
                target_position=0,
                score=features.short_score,
                reason="short trade rationale deteriorated",
                market_state=features.market_state,
            )
        return EngineSignal(
            ts=features.ts,
            action="hold_short",
            target_position=-1,
            score=features.short_score,
            reason="short rationale remains intact",
            market_state=features.market_state,
        )
