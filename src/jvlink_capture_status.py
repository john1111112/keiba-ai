"""Persist truthful SDK capture state for unattended diagnostics.

A raw capture is not a validated schedule, runner list, or odds snapshot.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def build_status(report, *, checked_at=None):
    if not isinstance(report, dict) or not isinstance(report.get("reports"), list):
        raise ValueError("Invalid JV-Link capture report")
    stamp = checked_at or datetime.now(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Status timestamp must include timezone")
    reports = report["reports"]
    failed = [r["spec"] for r in reports if r.get("status") == "FAILED"]
    return {
        "checked_at": stamp,
        "capture_status": report.get("status", "UNKNOWN"),
        "raw_records_received": sum(int(r.get("received", 0)) for r in reports),
        "raw_records_inserted": sum(int(r.get("inserted", 0)) for r in reports),
        "failed_specs": failed,
        "schedule_verified": False,
        "runners_verified": False,
        "prerace_odds_verified": False,
        "ai_predictions_verified": False,
        "notification_ready": False,
        "bet_status": "NO_BET",
    }


def save_status(path, status):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination
