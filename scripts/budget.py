#!/usr/bin/env python
"""Live API budget from the cost ledger: spend today (America/New_York) and in total, against the caps.

    uv run python scripts/budget.py            # this machine's DATA_DIR
    fly ssh console -a punchwalk -C "/app/.venv/bin/python /app/scripts/budget.py"   # the deployed app
    ... --json                                  # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def budget() -> dict:
    from punchwalk.clients import cost
    from punchwalk.config import get_settings

    s = get_settings()
    rows = cost.read_ledger()
    total = round(sum(float(r.get("est_cost_usd") or 0.0) for r in rows if not r.get("cached")), 4)
    day = cost.today_eastern()
    today = round(cost.spend_on(day, rows), 4)
    return {
        "ledger": str(cost.ledger_path()),
        "day": str(day),
        "spend_today_usd": today,
        "daily_cap_usd": s.COST_DAILY_CAP_USD,
        "left_today_usd": round(max(0.0, s.COST_DAILY_CAP_USD - today), 4),
        "spend_total_usd": total,
        "total_cap_usd": s.COST_CAP_USD,
        "left_total_usd": round(max(0.0, s.COST_CAP_USD - total), 4),
        "live_calls": sum(1 for r in rows if not r.get("cached")),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    b = budget()
    if args.json:
        print(json.dumps(b, indent=1))
        return 0
    print(f"Ledger: {b['ledger']} ({b['live_calls']} live calls)")
    print(f"Today ({b['day']}, America/New_York): ${b['spend_today_usd']:.2f} of ${b['daily_cap_usd']:.2f}"
          f" (${b['left_today_usd']:.2f} left)")
    print(f"Total: ${b['spend_total_usd']:.2f} of ${b['total_cap_usd']:.2f} (${b['left_total_usd']:.2f} left)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
