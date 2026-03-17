"""JSONL persistence helpers for quote capture and replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from test_strat.types import QuoteSample


def append_quote(path: Path, quote: QuoteSample) -> None:
    """Append one normalized quote sample to a JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(quote.to_record(), separators=(",", ":")) + "\n")


def iter_quotes(path: Path) -> Iterator[QuoteSample]:
    """Yield normalized quote samples from a JSONL capture."""
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            yield QuoteSample.from_record(json.loads(line))
