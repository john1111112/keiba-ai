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
from src.automated_monitor import run_monitor_scan, resolve_latest_authorized_feed


def run_cycle(manifest, db, *, now=None, dry_run=False, config_path=None, use_jvlink=False):
    """Run one cycle of unattended pre-race scan and notification."""
    path = Path(manifest)

    # JV-Link 取得オプションが有効な場合、最新フィードの自動取得・構築を試行
    if use_jvlink:
        try:
            from src.jvlink_client import JVLinkClient
            client = JVLinkClient()
            out_feed = client.fetch_and_build_live_feed(output_dir=path if path.is_dir() else path.parent)
            path = (path if path.is_dir() else path.parent) / "latest_jvlink_feed.json"
        except Exception as exc:
            return {
                "status": "JVLINK_FETCH_FAILED",
                "reason": str(exc),
                "processed_races": 0,
                "captured_count": 0,
                "bet_status": "NO_BET",
                "model_inference_allowed": False
            }

    # パスがディレクトリまたは未存在の場合、認可スナップショット解決を試行
    target_feed = path
    if path.is_dir() or not path.is_file():
        search_dir = path if path.is_dir() else path.parent
        resolved, reason = resolve_latest_authorized_feed(search_dir)
        if resolved and resolved.is_file():
            target_feed = resolved
        else:
            return {
                "status": "NO_UPCOMING_MANIFEST",
                "reason": reason,
                "processed_races": 0,
                "captured_count": 0,
                "bet_status": "NO_BET",
                "model_inference_allowed": False
            }

    if not target_feed.is_file():
        return {
            "status": "NO_UPCOMING_MANIFEST",
            "processed_races": 0,
            "captured_count": 0,
            "bet_status": "NO_BET",
            "model_inference_allowed": False
        }

    try:
        content = target_feed.read_text(encoding="utf-8")
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
            res = run_schedule_scan(target_feed, Path(db), as_of_time=now)
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
            feed_path=target_feed,
            state_db_path=Path(db),
            as_of_time=now,
            dry_run=dry_run,
            config_path=config_path
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
    parser.add_argument("--config", default="data/alert_config.json", help="Path to alert config JSON")
    parser.add_argument("--out", default="data/last_background_scan.json")
    parser.add_argument("--now", help="Timezone-aware timestamp (default: now)")
    parser.add_argument("--dry-run", action="store_true", help="Do not send actual notifications")
    parser.add_argument("--use-jvlink", action="store_true", help="Fetch live data directly via JV-Link worker before scan")
    args = parser.parse_args(argv)
    result = run_cycle(args.manifest, args.db, now=args.now, dry_run=args.dry_run, config_path=args.config, use_jvlink=args.use_jvlink)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(payload)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(payload + "\n", encoding="utf-8")
    temp.replace(target)
    if args.use_jvlink:
        # A scheduled one-shot scan also provides a heartbeat: Streamlit need
        # not be open, and the older daemon is not required.
        import os
        heartbeat = {
            "daemon_status": (
                "ERROR_RECOVERING" if result.get("status") in {
                    "JVLINK_FETCH_FAILED", "FEED_VALIDATION_REJECTED",
                    "SCAN_BLOCKED", "NO_UPCOMING_MANIFEST",
                    "MODEL_LOAD_ERROR", "MODEL_FILE_NOT_FOUND"
                } else "RUNNING"
            ),
            "run_mode": "WINDOWS_TASK_SCHEDULER",
            "pid": os.getpid(),
            "last_run_at": result["checked_at"],
            "last_scan_result": result
        }
        status = target.parent / "monitor_daemon_status.json"
        status_temp = status.with_suffix(status.suffix + ".tmp")
        status_temp.write_text(
            json.dumps(heartbeat, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8"
        )
        status_temp.replace(status)
    # Task Scheduler must not mark an acquisition/scan failure as success.
    # A legitimate NO_UPCOMING_RACES result remains a successful scan.
    failures = {
        "JVLINK_FETCH_FAILED", "NO_UPCOMING_MANIFEST",
        "SCAN_BLOCKED", "FEED_VALIDATION_REJECTED",
        "FEED_PARSE_ERROR", "MODEL_LOAD_ERROR",
        "MODEL_FILE_NOT_FOUND", "NO_FEED_FILE",
    }
    # Historical schedule-only CLI treats missing manifests as a no-op;
    # live scheduled tasks must still report missing/failed data as errors.
    if result.get("status") == "NO_UPCOMING_MANIFEST" and not args.use_jvlink:
        return 0
    return 1 if result.get("status") in failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
