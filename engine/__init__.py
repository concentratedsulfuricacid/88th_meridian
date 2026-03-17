"""Trading engine package for execution and strategy orchestration."""

from engine.adapters import snapshot_from_payload, trades_from_payloads
from engine.core import FeatureBuilder, SimpleTradingEngine
from engine.exits import EXIT_POLICIES
from engine.live import EngineUpdate, LiveEngineProcessor
from engine.paper import PaperTrader
from engine.types import (
    EngineConfig,
    EngineFeatureVector,
    EngineSignal,
    PaperPortfolioSnapshot,
    PaperTrade,
    PaperTraderConfig,
)

__all__ = [
    "EngineConfig",
    "EngineFeatureVector",
    "EngineSignal",
    "EngineUpdate",
    "EXIT_POLICIES",
    "FeatureBuilder",
    "LiveEngineProcessor",
    "PaperPortfolioSnapshot",
    "PaperTrade",
    "PaperTrader",
    "PaperTraderConfig",
    "SimpleTradingEngine",
    "snapshot_from_payload",
    "trades_from_payloads",
]
