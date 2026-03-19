"""Minimal signed Roostoo REST client for the submission strategy."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlencode

import requests

from .live_config import DEFAULT_ENDPOINTS, LiveConfig


class RoostooClient:
    """Roostoo REST client using the documented signed-request format."""

    def __init__(self, config: LiveConfig) -> None:
        self.base_url = config.roostoo_base_url
        self.api_key = config.roostoo_api_key
        self.api_secret = config.roostoo_api_secret
        self.timeout_seconds = config.roostoo_timeout_seconds
        self.session = requests.Session()

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.api_secret)

    @staticmethod
    def _timestamp_ms() -> str:
        return str(int(time.time() * 1000))

    @staticmethod
    def normalize_pair(symbol: str) -> str:
        symbol = symbol.upper().strip()
        if "/" in symbol:
            return symbol
        if symbol.endswith("USDT"):
            return f"{symbol[:-4]}/USD"
        if symbol.endswith("USD"):
            return f"{symbol[:-3]}/USD"
        return symbol

    @staticmethod
    def _to_string_payload(payload: dict[str, Any]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, value in payload.items():
            if value is None:
                continue
            if isinstance(value, bool):
                out[key] = "TRUE" if value else "FALSE"
            else:
                out[key] = str(value)
        return out

    def _encode_params(self, params: dict[str, Any]) -> str:
        normalized = self._to_string_payload(params)
        ordered = dict(sorted(normalized.items(), key=lambda item: item[0]))
        return urlencode(ordered, safe="/")

    def _signature(self, encoded_params: str) -> str:
        return hmac.new(
            self.api_secret.encode("utf-8"),
            encoded_params.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self, *, signed: bool, signature: str | None = None, form_encoded: bool = False) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if form_encoded:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if signed:
            if signature is None:
                raise ValueError("signature required for signed request")
            headers["RST-API-KEY"] = self.api_key
            headers["MSG-SIGNATURE"] = signature
        return headers

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        if signed and not self.is_configured():
            raise RuntimeError("Roostoo credentials are not configured.")

        payload = dict(params or {})
        if signed and "timestamp" not in payload:
            payload["timestamp"] = self._timestamp_ms()

        encoded_payload = self._encode_params(payload) if payload else ""
        signature = self._signature(encoded_payload) if signed else None
        request_kwargs: dict[str, Any] = {
            "method": method.upper(),
            "url": f"{self.base_url}{endpoint}",
            "headers": self._headers(signed=signed, signature=signature, form_encoded=(method.upper() == "POST")),
            "timeout": self.timeout_seconds,
        }
        if method.upper() == "GET":
            request_kwargs["params"] = payload
        elif encoded_payload:
            request_kwargs["data"] = encoded_payload

        response = self.session.request(**request_kwargs)
        response.raise_for_status()
        if not response.content:
            return {}
        parsed = response.json()
        return parsed if isinstance(parsed, dict) else {"raw": parsed}

    def get_server_time(self) -> dict[str, Any]:
        return self._request("GET", DEFAULT_ENDPOINTS["server_time"])

    def get_exchange_info(self) -> dict[str, Any]:
        return self._request("GET", DEFAULT_ENDPOINTS["exchange_info"])

    def get_ticker(self, pair: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"timestamp": self._timestamp_ms()}
        if pair:
            params["pair"] = self.normalize_pair(pair)
        return self._request("GET", DEFAULT_ENDPOINTS["ticker"], params=params)

    def get_balances(self) -> dict[str, Any]:
        return self._request("GET", DEFAULT_ENDPOINTS["balances"], signed=True)

    def query_orders(self, *, pending_only: bool = True, pair: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"pending_only": pending_only}
        if pair:
            params["pair"] = self.normalize_pair(pair)
        return self._request("POST", DEFAULT_ENDPOINTS["query_order"], params=params, signed=True)

    def place_market_order(self, *, symbol: str, side: str, quantity: float) -> dict[str, Any]:
        payload = {
            "pair": self.normalize_pair(symbol),
            "side": side.upper(),
            "type": "MARKET",
            "quantity": quantity,
        }
        return self._request("POST", DEFAULT_ENDPOINTS["place_order"], params=payload, signed=True)

    def place_limit_order(self, *, symbol: str, side: str, quantity: float, price: float) -> dict[str, Any]:
        payload = {
            "pair": self.normalize_pair(symbol),
            "side": side.upper(),
            "type": "LIMIT",
            "quantity": quantity,
            "price": price,
        }
        return self._request("POST", DEFAULT_ENDPOINTS["place_order"], params=payload, signed=True)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self._request("POST", DEFAULT_ENDPOINTS["cancel_order"], params={"order_id": order_id}, signed=True)

    @staticmethod
    def wallet_from_balances(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
        wallet = payload.get("SpotWallet") or payload.get("Wallet") or {}
        if not isinstance(wallet, dict):
            return {}
        out: dict[str, dict[str, float]] = {}
        for asset, value in wallet.items():
            if not isinstance(value, dict):
                continue
            try:
                free = float(value.get("Free", 0.0))
                lock = float(value.get("Lock", 0.0))
            except (TypeError, ValueError):
                continue
            out[str(asset)] = {"free": free, "lock": lock}
        return out
