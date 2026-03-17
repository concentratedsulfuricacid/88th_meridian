"""Live integration helpers for feeding Binance local-book updates into the engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.core import SimpleTradingEngine
from engine.paper import PaperTrader
from engine.types import EngineFeatureVector, EngineSignal, PaperPortfolioSnapshot, PaperTrade


@dataclass(frozen=True)
class EngineUpdate:
    """One live engine update containing both features and the resulting signal."""

    features: EngineFeatureVector
    signal: EngineSignal
    payload: dict[str, Any]
    trades: tuple[PaperTrade, ...] = ()
    portfolio: PaperPortfolioSnapshot | None = None


class LiveEngineProcessor:
    """Adapter that turns raw local-book payloads into engine outputs."""

    def __init__(
        self,
        engine: SimpleTradingEngine | None = None,
        paper_trader: PaperTrader | None = None,
    ) -> None:
        self.engine = engine or SimpleTradingEngine()
        self.paper_trader = paper_trader

    def on_payload(
        self,
        payload: dict[str, Any],
        trade_payloads: list[dict[str, Any]] | None = None,
        ts: float | None = None,
    ) -> EngineUpdate:
        """Process one order book payload and return the derived engine update."""
        features, signal = self.engine.on_payload(payload, trade_payloads=trade_payloads, ts=ts)
        trades: tuple[PaperTrade, ...] = ()
        portfolio: PaperPortfolioSnapshot | None = None
        if self.paper_trader is not None:
            generated_trades, portfolio = self.paper_trader.on_signal(features, signal)
            trades = tuple(generated_trades)
        return EngineUpdate(features=features, signal=signal, payload=payload, trades=trades, portfolio=portfolio)
