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
    if sys.platform != "win32":
        raise RuntimeError("Desktop delivery only supported on Windows")
    db_path = Path(state_db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    delivered, duplicate, failures = 0, 0, 0
    with sqlite3.connect(db_path, timeout=30) as conn:
        _init(conn)
        for row in eligible:
            key = (row["race_id"], row["horse_number"], "ev_candidate")
            # Reserve once; even concurrent scheduler invocations cannot spam.
            inserted = conn.execute(
                "INSERT OR IGNORE INTO alert_delivery VALUES (?,?,?,?)",
                (*key, current)).rowcount
            conn.commit()
            if not inserted:
                duplicate += 1
                continue
            title = "競馬AI｜要確認の候補"
            message = (f"{row['race_id']} 馬番{row['horse_number']} "
                       f"理論期待値 {row['theoretical_ev'] * 100:.1f}% "
                       "（購入推奨ではありません）")
            try:
                _toast(title, message)
                delivered += 1
            except Exception:
                # Delivery failure is reported. Reservation prevents retry spam;
                # manual reset is required to retry a failed delivery.
                failures += 1
    return {"status": "DELIVERY_ATTEMPTED", "evaluated": len(evaluated),
            "eligible": len(eligible), "delivered": delivered,
            "duplicate_suppressed": duplicate, "delivery_failures": failures,
            "blocked": len(evaluated) - len(eligible), "bet_status": "NO_BET"}


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
