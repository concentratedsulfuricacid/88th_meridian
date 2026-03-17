"""Core types for trading engine feature, signal, and paper-trading state."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EngineFeatureVector:
    """One computed feature vector for a single order book update."""

    ts: float | None
    best_bid: float
    best_ask: float
    mid_price: float
    microprice: float
    spread: float
    spread_bps: float
    l1_imbalance: float
    top_n_imbalance: float
    weighted_top_n_imbalance: float
    ofi: float
    ofi_zscore: float
    mlofi: tuple[float, ...]
    volatility: float
    volatility_zscore: float
    depth_bid_qty: float
    depth_ask_qty: float
    depth_total_qty: float
    depth_total_notional: float
    price_response: float
    breakout_long: bool
    breakout_short: bool
    retest_long: bool
    retest_short: bool
    pullback_long: bool
    pullback_short: bool
    vwap_buy_to_mid: float
    vwap_sell_to_mid: float
    long_score: int
    short_score: int
    quality_pass: bool
    long_ready: bool
    short_ready: bool
    market_state: str


@dataclass(frozen=True)
class EngineSignal:
    """Output decision from the engine for the current feature vector."""

    ts: float | None
    action: str
    target_position: int
    score: int
    reason: str
    market_state: str


@dataclass
class EngineConfig:
    """Configuration for feature depths, thresholds, and signal behavior."""

    imbalance_depth: int = 5
    liquidity_depth: int = 10
    mlofi_levels: int = 5
    vwap_depth: int = 5
    normalization_window: int = 100
    volatility_window: int = 50
    price_response_lookback: int = 8
    breakout_lookback: int = 20
    pullback_lookback: int = 8
    ofi_zscore_threshold: float = 0.75
    imbalance_threshold: float = 0.15
    max_spread_bps: float = 2.5
    max_volatility_zscore: float = 1.75
    min_depth_total_qty: float = 1.0
    min_depth_total_notional: float = 100000.0
    min_price_response: float = 0.0
    min_entry_confluence_score: int = 5
    min_exit_confluence_score: int = 3
    trade_cooldown_seconds: float = 30.0
    require_timing_confirmation: bool = True
    exit_policy: str = "score_fade"
    max_holding_seconds: float = 2.5
    exit_ofi_zscore_threshold: float = -0.1
    exit_imbalance_threshold: float = 0.0
    min_profit_buffer_bps: float = 0.0
    max_profit_floor_wait_seconds: float = 5.0
    entry_fee_rate: float = 0.001
    exit_fee_rate: float = 0.001
    entry_fill_reference: str = "ask"
    exit_fill_reference: str = "bid"
    allow_shorting: bool = False
    buy_side_labels: tuple[str, ...] = field(default_factory=lambda: ("buy", "b"))
    sell_side_labels: tuple[str, ...] = field(default_factory=lambda: ("sell", "s"))


@dataclass
class PaperTraderConfig:
    """Configuration for paper-trading fills, fees, and position sizing."""

    position_size: float = 0.001
    entry_order_type: str = "market"
    exit_order_type: str = "market"
    maker_fee_rate: float = 0.0005
    taker_fee_rate: float = 0.001
    initial_cash: float = 10000.0
    allow_shorting: bool = False


@dataclass(frozen=True)
class PaperTrade:
    """One simulated trade fill in the paper trader."""

    ts: float | None
    side: str
    action: str
    order_type: str
    qty: float
    price: float
    fee: float
    gross_realized_pnl: float
    realized_pnl: float
    position_after: float
    cash_after: float


@dataclass(frozen=True)
class PaperPortfolioSnapshot:
    """Current paper portfolio state after processing one engine signal."""

    ts: float | None
    cash: float
    position_qty: float
    avg_entry_price: float
    gross_realized_pnl: float
    realized_pnl: float
    gross_unrealized_pnl: float
    unrealized_pnl: float
    gross_total_pnl: float
    net_total_pnl: float
    total_equity: float
    total_fees_paid: float
    trade_count: int
