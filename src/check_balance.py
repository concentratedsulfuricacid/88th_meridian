"""Read-only helper to inspect Roostoo balances for the selected bot mode."""

from __future__ import annotations

import argparse
import json

from .live_config import build_live_config
from .roostoo_client import RoostooClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print Roostoo balances for the configured bot mode.")
    parser.add_argument("--competition", action="store_true", help="Use competition credentials instead of default credentials.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = build_live_config(competition=args.competition)
    client = RoostooClient(config)
    if not client.is_configured():
        raise RuntimeError("Roostoo API credentials are not configured.")
    print(json.dumps({"mode": config.bot_mode, "balances": client.get_balances()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
