"""Read-only helper to inspect Roostoo orders for the selected bot mode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .live_config import build_live_config
from .roostoo_client import RoostooClient


EXPORT_DIR = Path("order_records")


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
    order_matches = payload.get("OrderMatched")
    if isinstance(order_matches, list):
        payload["OrderMatched"] = list(reversed(order_matches))

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    export_name = f"{config.bot_mode}_orders"
    if args.pending_only:
        export_name += "_pending"
    if args.pair:
        export_name += f"_{client.normalize_pair(args.pair).replace('/', '_')}"
    export_path = EXPORT_DIR / f"{export_name}.json"

    result = {
        "mode": config.bot_mode,
        "saved_to": str(export_path),
        "orders": payload,
    }
    export_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
