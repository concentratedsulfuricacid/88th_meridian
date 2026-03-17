"""Minimal Roostoo market-data client for lead/lag studies."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from test_strat.types import QuoteSample


def now_ms() -> int:
    """Return the current Unix time in milliseconds."""
    return time.time_ns() // 1_000_000


@dataclass
class RoostooClient:
    """Blocking client for Roostoo public market-data endpoints."""

    base_url: str = "https://mock-api.roostoo.com"
    timeout: float = 5.0

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], int]:
        """Send a GET request and return parsed JSON with local receive time."""
        query = urlencode(params or {})
        url = f"{self.base_url.rstrip('/')}{path}"
        if query:
            url = f"{url}?{query}"
        with urlopen(url, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload, now_ms()

    def server_time(self) -> tuple[int, int]:
        """Return server time and local receive time in milliseconds."""
        payload, recv_ts_ms = self._request_json("/v3/serverTime")
        return int(payload["ServerTime"]), recv_ts_ms

    def fetch_quote(self, pair: str) -> QuoteSample:
        """Fetch the current Roostoo ticker for one pair as a normalized quote."""
        request_ts_ms = now_ms()
        payload, recv_ts_ms = self._request_json("/v3/ticker", {"pair": pair, "timestamp": request_ts_ms})

        if not payload.get("Success", False):
            raise ValueError(f"Roostoo ticker request failed: {payload.get('ErrMsg', 'unknown error')}")

        data = payload.get("Data", {})
        if pair not in data:
            raise ValueError(f"Roostoo ticker response did not include {pair}")

        ticker = data[pair]
        bid = float(ticker["MaxBid"])
        ask = float(ticker["MinAsk"])
        mid = (bid + ask) / 2.0
        event_ts_ms = int(payload.get("ServerTime", recv_ts_ms))

        return QuoteSample(
            source="roostoo",
            pair=pair,
            event_ts_ms=event_ts_ms,
            recv_ts_ms=recv_ts_ms,
            bid=bid,
            ask=ask,
            mid=mid,
            spread=ask - bid,
            last=float(ticker["LastPrice"]) if ticker.get("LastPrice") is not None else None,
            quote_age_ms=max(recv_ts_ms - event_ts_ms, 0),
            meta={
                "request_ts_ms": request_ts_ms,
                "change_24h": ticker.get("Change"),
                "coin_trade_value": ticker.get("CoinTradeValue"),
                "unit_trade_value": ticker.get("UnitTradeValue"),
            },
        )
