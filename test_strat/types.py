"""Shared quote models for lead/lag data collection and replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class QuoteSample:
    """Normalized top-of-book quote snapshot from one venue."""

    source: str
    pair: str
    event_ts_ms: int
    recv_ts_ms: int
    bid: float
    ask: float
    mid: float
    spread: float
    last: float | None = None
    quote_age_ms: int | None = None
    sequence: int | None = None
    meta: dict[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        """Convert the sample to a JSON-serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "QuoteSample":
        """Rebuild a quote sample from persisted JSONL data."""
        return cls(
            source=str(record["source"]),
            pair=str(record["pair"]),
            event_ts_ms=int(record["event_ts_ms"]),
            recv_ts_ms=int(record["recv_ts_ms"]),
            bid=float(record["bid"]),
            ask=float(record["ask"]),
            mid=float(record["mid"]),
            spread=float(record["spread"]),
            last=float(record["last"]) if record.get("last") is not None else None,
            quote_age_ms=int(record["quote_age_ms"]) if record.get("quote_age_ms") is not None else None,
            sequence=int(record["sequence"]) if record.get("sequence") is not None else None,
            meta=dict(record["meta"]) if record.get("meta") else None,
        )
