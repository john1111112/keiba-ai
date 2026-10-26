"""Offline unattended alert runner: read candidates, dedupe, optional Windows toast.

NO network calls or betting. The alert policy blocks uncertified candidates.
The process can be launched by Windows Task Scheduler even when the UI is closed.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.alert_policy import evaluate_alert


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS alert_delivery (
        race_id TEXT NOT NULL,
        horse_number INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        first_sent_at TEXT NOT NULL,
        PRIMARY KEY (race_id, horse_number, event_type)
    )""")


def _toast(title, message):
    if sys.platform != "win32":
        raise RuntimeError("Windows desktop notification requires Windows")
    # PowerShell is invoked with fixed code and environment variables only;
    # untrusted race text is never interpolated into executable code.
    code = r"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.Visible = $true
$icon.BalloonTipTitle = $env:KEIBA_ALERT_TITLE
$icon.BalloonTipText = $env:KEIBA_ALERT_MESSAGE
$icon.ShowBalloonTip(10000)
Start-Sleep -Seconds 12
$icon.Dispose()
"""
    env = os.environ.copy()
    env["KEIBA_ALERT_TITLE"] = title
    env["KEIBA_ALERT_MESSAGE"] = message
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", code],
                     env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def run_alert_cycle(candidates_path, state_db, *, now=None, deliver=False):
    current = now or datetime.now(timezone.utc).isoformat()
    if not Path(candidates_path).is_file():
        return {"status": "NO_CANDIDATE_DATA", "evaluated": 0,
                "eligible": 0, "delivered": 0, "blocked": 0,
                "bet_status": "NO_BET"}
    raw = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Candidates file must be a list")
    evaluated = [evaluate_alert(row, now=current) for row in raw]
    eligible = [row for row in evaluated if row["notification_allowed"]]
    if not deliver:
        return {"status": "DRY_RUN", "evaluated": len(evaluated),
                "eligible": len(eligible), "delivered": 0,
                "blocked": len(evaluated) - len(eligible),
                "bet_status": "NO_BET"}
    # JSON boolean claims and source strings are caller-controlled. They
    # cannot establish independent feed/model certification. Until a trusted
    # provider+model adapter supplies verified evidence, no live notifications.
    return {"status": "BLOCKED_UNVERIFIED_PROVENANCE",
            "evaluated": len(evaluated), "eligible_by_claim": len(eligible),
            "eligible": 0, "delivered": 0,
            "blocked": len(evaluated), "bet_status": "NO_BET",
            "reason": "Candidate JSON cannot certify odds or model provenance"}


def main():
    parser = argparse.ArgumentParser(description="Offline safe Windows notification runner")
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--state-db", default="data/alert_state.db")
    parser.add_argument("--now", help="Timezone-aware timestamp; defaults to actual UTC now")
    parser.add_argument("--deliver", action="store_true",
                        help="Actually show Windows notifications (default: dry-run)")
    args = parser.parse_args()
    report = run_alert_cycle(args.candidates, args.state_db, now=args.now,
                             deliver=args.deliver)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
