"""Configuration for live trading the validated submission strategy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LiveConfig:
    """Configuration for the live Roostoo execution bot."""

    roostoo_base_url: str = os.getenv("ROOSTOO_BASE_URL", "https://mock-api.roostoo.com").rstrip("/")
    roostoo_api_key: str = os.getenv("ROOSTOO_API_KEY", "").strip()
    roostoo_api_secret: str = os.getenv("ROOSTOO_API_SECRET", "").strip()
    roostoo_timeout_seconds: int = int(os.getenv("ROOSTOO_TIMEOUT_SECONDS", "30"))
    binance_base_url: str = os.getenv("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")
    polling_seconds: int = int(os.getenv("POLLING_SECONDS", "60"))
    state_path: Path = Path(os.getenv("SUBMISSION_STATE_PATH", "src/state/live_state.json"))
    live_trading: bool = _env_flag("LIVE_TRADING", default=False)
    bot_mode: str = "default"


def build_live_config(
    *,
    competition: bool = False,
    polling_seconds: int | None = None,
    state_path: Path | None = None,
    live_trading: bool | None = None,
) -> LiveConfig:
    """Build runtime config for default or competition credentials."""
    base_url = os.getenv("ROOSTOO_BASE_URL", "https://mock-api.roostoo.com").rstrip("/")
    timeout_seconds = int(os.getenv("ROOSTOO_TIMEOUT_SECONDS", "30"))
    binance_base = os.getenv("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")
    resolved_polling = polling_seconds if polling_seconds is not None else int(os.getenv("POLLING_SECONDS", "60"))
    resolved_live = live_trading if live_trading is not None else _env_flag("LIVE_TRADING", default=False)

    if competition:
        return LiveConfig(
            roostoo_base_url=os.getenv("ROOSTOO_COMPETITION_BASE_URL", base_url).rstrip("/"),
            roostoo_api_key=os.getenv("ROOSTOO_COMPETITION_API_KEY", "").strip(),
            roostoo_api_secret=os.getenv("ROOSTOO_COMPETITION_API_SECRET", "").strip(),
            roostoo_timeout_seconds=timeout_seconds,
            binance_base_url=binance_base,
            polling_seconds=resolved_polling,
            state_path=state_path or Path(os.getenv("SUBMISSION_COMPETITION_STATE_PATH", "src/state/competition_live_state.json")),
            live_trading=resolved_live,
            bot_mode="competition",
        )

    return LiveConfig(
        roostoo_base_url=base_url,
        roostoo_api_key=os.getenv("ROOSTOO_API_KEY", "").strip(),
        roostoo_api_secret=os.getenv("ROOSTOO_API_SECRET", "").strip(),
        roostoo_timeout_seconds=timeout_seconds,
        binance_base_url=binance_base,
        polling_seconds=resolved_polling,
        state_path=state_path or Path(os.getenv("SUBMISSION_STATE_PATH", "src/state/live_state.json")),
        live_trading=resolved_live,
        bot_mode="default",
    )


DEFAULT_ENDPOINTS = {
    "server_time": "/v3/serverTime",
    "exchange_info": "/v3/exchangeInfo",
    "ticker": "/v3/ticker",
    "balances": "/v3/balance",
    "pending_count": "/v3/pending_count",
    "query_order": "/v3/query_order",
    "place_order": "/v3/place_order",
    "cancel_order": "/v3/cancel_order",
}
