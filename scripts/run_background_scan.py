"""Run unattended pre-race scan with LightGBM inference, EV filtering, and notification.

No fabricated schedules, odds or model scores. Provider adapters fail closed.
Windows Task Scheduler may invoke this module periodically.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_prerace_auto_recorder import run_schedule_scan
from src.automated_monitor import run_monitor_scan


def run_cycle(manifest, db, *, now=None, dry_run=False):
    """Run one cycle of unattended pre-race scan and notification."""
    path = Path(manifest)
    if not path.is_file():
        return {
            "status": "NO_UPCOMING_MANIFEST",
            "processed_races": 0,
            "captured_count": 0,
            "bet_status": "NO_BET",
            "model_inference_allowed": False
        }
    try:
        content = path.read_text(encoding="utf-8")
        payload = json.loads(content)
    except Exception as exc:
        return {
            "status": "SCAN_BLOCKED",
            "reason": type(exc).__name__,
            "processed_races": 0,
            "captured_count": 0,
            "bet_status": "NO_BET",
            "model_inference_allowed": False
        }

    # If payload is a schedule-only list (e.g. upcoming races schedule manifest),
    # invoke run_schedule_scan for backward-compatibility while keeping no-bet safety
    if isinstance(payload, list):
        try:
            res = run_schedule_scan(path, Path(db), as_of_time=now)
            if not isinstance(res, dict):
                raise ValueError("Invalid scanner response")
            res["bet_status"] = "NO_BET"
            res["model_inference_allowed"] = False
            return res
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return {
                "status": "SCAN_BLOCKED",
                "reason": type(exc).__name__,
                "processed_races": 0,
                "captured_count": 0,
                "bet_status": "NO_BET",
                "model_inference_allowed": False
            }

    # For authorized feed envelopes with races & horses, run automated monitor scan with model inference
    try:
        res = run_monitor_scan(
            feed_path=path,
            state_db_path=Path(db),
            as_of_time=now,
            dry_run=dry_run
        )
        return {
            "status": res["status"],
            "processed_races": res["processed_races"],
            "captured_count": res.get("eligible_candidates", 0),
            "bet_status": "NO_BET",
            "model_inference_allowed": res["model_inference_allowed"],
            "candidates_found": res.get("candidates_found", 0),
            "eligible_candidates": res.get("eligible_candidates", 0),
            "notifications_sent": res.get("notifications_sent", 0),
            "errors": res.get("errors", [])
        }
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {
            "status": "SCAN_BLOCKED",
            "reason": type(exc).__name__,
            "processed_races": 0,
            "captured_count": 0,
            "bet_status": "NO_BET",
            "model_inference_allowed": False
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Unattended pre-race scan and notification")
    parser.add_argument("--manifest", default="data/authorized_feed/latest.json")
    parser.add_argument("--db", default="data/alert_state.db")
    parser.add_argument("--out", default="data/last_background_scan.json")
    parser.add_argument("--now", help="Timezone-aware timestamp (default: now)")
    parser.add_argument("--dry-run", action="store_true", help="Do not send actual notifications")
    args = parser.parse_args(argv)
    result = run_cycle(args.manifest, args.db, now=args.now, dry_run=args.dry_run)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
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
