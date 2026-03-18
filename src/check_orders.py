"""Read-only helper to inspect Roostoo orders for the selected bot mode."""

from __future__ import annotations

import argparse
import json

from .live_config import build_live_config
from .roostoo_client import RoostooClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print Roostoo orders for the configured bot mode.")
    parser.add_argument("--competition", action="store_true", help="Use competition credentials instead of default credentials.")
    parser.add_argument("--pending-only", action="store_true", help="Show only pending orders.")
    parser.add_argument("--pair", type=str, default=None, help="Optional symbol filter such as ETHUSDT or ADAUSDT.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = build_live_config(competition=args.competition)
    client = RoostooClient(config)
    if not client.is_configured():
        raise RuntimeError("Roostoo API credentials are not configured.")
    payload = client.query_orders(pending_only=args.pending_only, pair=args.pair)
    print(json.dumps({"mode": config.bot_mode, "orders": payload}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
