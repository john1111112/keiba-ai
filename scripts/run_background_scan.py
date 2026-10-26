"""Run one unattended pre-race scan without opening the UI.

No fabricated schedules, odds or model scores. Provider adapters fail closed.
Windows Task Scheduler may invoke this module periodically.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.run_prerace_auto_recorder import run_schedule_scan


def run_cycle(manifest, db, *, now=None):
    """Always return a safe status; malformed external manifests must not crash the task."""
    path = Path(manifest)
    if not path.is_file():
        return {"status": "NO_UPCOMING_MANIFEST", "processed_races": 0,
                "captured_count": 0, "bet_status": "NO_BET",
                "model_inference_allowed": False}
    try:
        result = run_schedule_scan(path, Path(db), as_of_time=now)
        if not isinstance(result, dict):
            raise ValueError("Invalid scanner response")
        result["bet_status"] = "NO_BET"
        result["model_inference_allowed"] = False
        return result
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"status": "SCAN_BLOCKED", "reason": type(exc).__name__,
                "processed_races": 0, "captured_count": 0,
                "bet_status": "NO_BET", "model_inference_allowed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unattended pre-race scan (no bets)")
    parser.add_argument("--manifest", default="data/upcoming_races.json")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--out", default="data/last_background_scan.json")
    args = parser.parse_args(argv)
    result = run_cycle(args.manifest, args.db)
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(payload)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(payload + "\n", encoding="utf-8")
    temp.replace(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
