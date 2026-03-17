"""Paper-trading portfolio and fill simulator for engine signals."""

from __future__ import annotations

import math

from engine.types import EngineFeatureVector, EngineSignal, PaperPortfolioSnapshot, PaperTrade, PaperTraderConfig


class PaperTrader:
    """Simulate entries, exits, and PnL from engine signals."""

    def __init__(self, config: PaperTraderConfig | None = None) -> None:
        self.config = config or PaperTraderConfig()
        self.cash = self.config.initial_cash
        self.position_qty = 0.0
        self.avg_entry_price = 0.0
        self.gross_realized_pnl = 0.0
        self.realized_pnl = 0.0
        self.total_fees_paid = 0.0
        self.trade_count = 0

    def on_signal(
        self,
        features: EngineFeatureVector,
        signal: EngineSignal,
    ) -> tuple[list[PaperTrade], PaperPortfolioSnapshot]:
        """Process a signal, return any generated fills, and the updated portfolio snapshot."""
        trades: list[PaperTrade] = []

        if signal.action == "enter_long" and self.position_qty <= 0.0:
            if self.position_qty < 0.0:
                trades.extend(self._close_short(features, self.config.exit_order_type))
            trades.append(self._open_long(features, self.config.entry_order_type))
        elif self.config.allow_shorting and signal.action == "enter_short" and self.position_qty >= 0.0:
            if self.position_qty > 0.0:
                trades.extend(self._close_long(features, self.config.exit_order_type))
            trades.append(self._open_short(features, self.config.entry_order_type))
        elif signal.action == "exit_long" and self.position_qty > 0.0:
            trades.extend(self._close_long(features, self.config.exit_order_type))
        elif self.config.allow_shorting and signal.action == "exit_short" and self.position_qty < 0.0:
            trades.extend(self._close_short(features, self.config.exit_order_type))

        snapshot = self._portfolio_snapshot(features)
        return trades, snapshot

    def _fee_rate(self, order_type: str) -> float:
        if order_type == "limit":
            return self.config.maker_fee_rate
        return self.config.taker_fee_rate

    def _fill_price(self, side: str, order_type: str, features: EngineFeatureVector) -> float:
        if order_type == "limit":
            return features.mid_price
        return features.best_ask if side == "buy" else features.best_bid

    def _open_long(self, features: EngineFeatureVector, order_type: str) -> PaperTrade:
        qty = self.config.position_size
        price = self._fill_price("buy", order_type, features)
        fee = qty * price * self._fee_rate(order_type)
        self.cash -= (qty * price) + fee
        self.position_qty = qty
        self.avg_entry_price = price
        self.total_fees_paid += fee
        self.trade_count += 1
        return PaperTrade(
            ts=features.ts,
            side="buy",
            action="open_long",
            order_type=order_type,
            qty=qty,
            price=price,
            fee=fee,
            gross_realized_pnl=0.0,
            realized_pnl=0.0,
            position_after=self.position_qty,
            cash_after=self.cash,
        )

    def _open_short(self, features: EngineFeatureVector, order_type: str) -> PaperTrade:
        qty = self.config.position_size
        price = self._fill_price("sell", order_type, features)
        fee = qty * price * self._fee_rate(order_type)
        self.cash += (qty * price) - fee
        self.position_qty = -qty
        self.avg_entry_price = price
        self.total_fees_paid += fee
        self.trade_count += 1
        return PaperTrade(
            ts=features.ts,
            side="sell",
            action="open_short",
            order_type=order_type,
            qty=qty,
            price=price,
            fee=fee,
            gross_realized_pnl=0.0,
            realized_pnl=0.0,
            position_after=self.position_qty,
            cash_after=self.cash,
        )

    def _close_long(self, features: EngineFeatureVector, order_type: str) -> list[PaperTrade]:
        qty = abs(self.position_qty)
        if math.isclose(qty, 0.0):
            return []
        price = self._fill_price("sell", order_type, features)
        gross_pnl = (price - self.avg_entry_price) * qty
        fee = qty * price * self._fee_rate(order_type)
        realized_pnl = gross_pnl - fee
        self.cash += (qty * price) - fee
        self.gross_realized_pnl += gross_pnl
        self.realized_pnl += realized_pnl
        self.total_fees_paid += fee
        self.position_qty = 0.0
        self.avg_entry_price = 0.0
        self.trade_count += 1
        return [
            PaperTrade(
                ts=features.ts,
                side="sell",
                action="close_long",
                order_type=order_type,
                qty=qty,
                price=price,
                fee=fee,
                gross_realized_pnl=gross_pnl,
                realized_pnl=realized_pnl,
                position_after=self.position_qty,
                cash_after=self.cash,
            )
        ]

    def _close_short(self, features: EngineFeatureVector, order_type: str) -> list[PaperTrade]:
        qty = abs(self.position_qty)
        if math.isclose(qty, 0.0):
            return []
        price = self._fill_price("buy", order_type, features)
        gross_pnl = (self.avg_entry_price - price) * qty
        fee = qty * price * self._fee_rate(order_type)
        realized_pnl = gross_pnl - fee
        self.cash -= (qty * price) + fee
        self.gross_realized_pnl += gross_pnl
        self.realized_pnl += realized_pnl
        self.total_fees_paid += fee
        self.position_qty = 0.0
        self.avg_entry_price = 0.0
        self.trade_count += 1
        return [
            PaperTrade(
                ts=features.ts,
                side="buy",
                action="close_short",
                order_type=order_type,
                qty=qty,
                price=price,
                fee=fee,
                gross_realized_pnl=gross_pnl,
                realized_pnl=realized_pnl,
                position_after=self.position_qty,
                cash_after=self.cash,
            )
        ]

    def _portfolio_snapshot(self, features: EngineFeatureVector) -> PaperPortfolioSnapshot:
        mark_price = features.mid_price
        gross_unrealized_pnl = 0.0
        if self.position_qty > 0.0:
            gross_unrealized_pnl = (mark_price - self.avg_entry_price) * self.position_qty
        elif self.position_qty < 0.0:
            gross_unrealized_pnl = (self.avg_entry_price - mark_price) * abs(self.position_qty)

        unrealized_pnl = gross_unrealized_pnl
        gross_total_pnl = self.gross_realized_pnl + gross_unrealized_pnl
        total_equity = self.cash + (self.position_qty * mark_price)
        net_total_pnl = total_equity - self.config.initial_cash
        return PaperPortfolioSnapshot(
            ts=features.ts,
            cash=self.cash,
            position_qty=self.position_qty,
            avg_entry_price=self.avg_entry_price,
            gross_realized_pnl=self.gross_realized_pnl,
            realized_pnl=self.realized_pnl,
            gross_unrealized_pnl=gross_unrealized_pnl,
            unrealized_pnl=unrealized_pnl,
            gross_total_pnl=gross_total_pnl,
            net_total_pnl=net_total_pnl,
            total_equity=total_equity,
            total_fees_paid=self.total_fees_paid,
            trade_count=self.trade_count,
        )
