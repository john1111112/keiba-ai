"""Read-only, no-trial-release diagnostic for unattended KEIBA AI.

Checks local installation, worker COM registration, and recorded production
evidence. Never calls JVInit/JVRTOpen, sends Discord, or places bets.
Exit code 0 means all machine-checkable steps passed, not that phone push
or reboot persistence has been personally verified.
"""
from __future__ import annotations

import argparse
import hashlib
from contextlib import closing
import json
import os
import platform
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
import re


ROOT = Path(__file__).resolve().parents[1]
TASK = "KeibaAI_BackgroundMonitor"


def _read_json(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def _result(code: str, passed: bool, evidence: str, next_action: str) -> dict:
    return {
        "code": code, "status": "PASS" if passed else "PENDING",
        "evidence": evidence, "next_action": "" if passed else next_action,
    }


def _recent(ts: Any, *, max_seconds: int = 180) -> bool:
    try:
        at = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if at.tzinfo is None:
            return False
        age = (datetime.now(timezone.utc) - at).total_seconds()
        return -15 <= age <= max_seconds
    except (TypeError, ValueError, OverflowError):
        return False


def inspect_discord_webhook(value: Any) -> tuple[bool, str]:
    """Validate the actual saved URL without ever printing its secret token."""
    if not isinstance(value, str) or not value.strip():
        return False, "Discord webhook URL has not been saved"
    if value != value.strip():
        return False, "Discord webhook URL contains leading or trailing whitespace"
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https":
            return False, "Discord webhook must use HTTPS"
        if parsed.hostname == "discordapp.com":
            return False, "Old discordapp.com domain: replace with discord.com"
        if parsed.hostname != "discord.com" or parsed.username or parsed.password or parsed.port is not None:
            return False, "URL host is not discord.com; this may be a channel URL rather than a webhook"
        if not re.fullmatch(r"/api/webhooks/[0-9]+/[A-Za-z0-9_.-]{8,}/?", parsed.path):
            return False, "Discord URL lacks an /api/webhooks/<id>/<token> path"
        if parsed.fragment:
            return False, "Webhook URL contains an unexpected fragment"
        return True, "Discord webhook URL structure is valid (delivery not yet tested)"
    except (ValueError, TypeError):
        return False, "Discord webhook URL cannot be parsed"


def check(root: Path = ROOT, *, include_os_probe: bool = True) -> dict:
    root = Path(root)
    data = root / "data"
    checks = []
    windows = platform.system() == "Windows"

    model_path = root / "models" / "keiba_lgbm_win_v2_28cols.joblib"
    manifest = _read_json(Path(str(model_path) + ".sha256"))
    expected = manifest.get("sha256")
    actual = (
        hashlib.sha256(model_path.read_bytes()).hexdigest()
        if model_path.is_file() else None
    )
    model_ok = (
        isinstance(expected, str)
        and len(expected) == 64 and expected.lower() == actual
    )
    checks.append(_result(
        "MODEL_FILE", model_ok,
        "28-feature model hash matches manifest" if model_ok
        else "Model file or SHA-256 manifest missing/mismatched",
        "Install or restore the verified win-probability model files."
    ))

    worker_ok = False
    worker_details = "Not checked (non-Windows)"
    if include_os_probe and windows:
        try:
            # Read the exact runtime selector, then use the side-effect-free
            # COM object creation probe; DO NOT open a JRA-VAN session.
            sys.path.insert(0, str(root))
            from src.jvlink_client import find_jvlink_python
            interp = find_jvlink_python()
            if interp:
                proc = subprocess.run(
                    [interp, str(root / "scripts" / "probe_jvlink_com.py")],
                    capture_output=True, text=True, timeout=12, check=False
                )
                details = json.loads(proc.stdout)
                worker_ok = proc.returncode == 0 and details.get("com_creatable") is True
                worker_details = (
                    f"{details.get('python_bitness')}bit; "
                    f"COM {'available' if worker_ok else 'unavailable'}"
                )
            else:
                worker_details = "No compatible Python + JV-Link COM installation found"
        except (OSError, subprocess.SubprocessError, ValueError, ImportError) as exc:
            worker_details = f"Worker COM probe failed: {type(exc).__name__}"
    checks.append(_result(
        "SDK_AND_COM", worker_ok, worker_details,
        "Install licensed JRA-VAN JV-Link SDK matching a 32/64-bit Python with pywin32."
    ))

    task_ok = False
    task_text = "Windows scheduled task not checked"
    if include_os_probe and windows:
        try:
            proc = subprocess.run(
                ["schtasks.exe", "/Query", "/TN", TASK, "/FO", "LIST"],
                capture_output=True, text=True, timeout=8, check=False
            )
            task_ok = proc.returncode == 0
            task_text = "Registered" if task_ok else "Not registered"
        except (OSError, subprocess.SubprocessError):
            task_text = "Task Scheduler unavailable"
    checks.append(_result(
        "SCHEDULED_TASK", task_ok, task_text,
        "Run INSTALL_AUTO_MONITOR.bat once to register automatic one-minute monitoring."
    ))

    config = _read_json(data / "alert_config.json")
    discord = _read_json(data / "discord_config.json")
    webhook_ok, webhook_detail = inspect_discord_webhook(discord.get("webhook_url"))
    discord_enabled = config.get("discord_enabled") is True
    safety_lock_off = config.get("safety_lock_enabled") is False
    discord_ok = discord_enabled and safety_lock_off and webhook_ok
    discord_reason = "; ".join([
        f"discord_enabled={discord_enabled}",
        f"safety_lock_off={safety_lock_off}",
        webhook_detail,
    ])
    checks.append(_result(
        "DISCORD_CONFIG", discord_ok,
        discord_reason,
        "Fix the reported Discord URL format or configuration flag (never share the secret token)."
    ))

    heartbeat = _read_json(data / "monitor_daemon_status.json")
    scan = _read_json(data / "last_background_scan.json")
    beat_ok = (
        heartbeat.get("run_mode") == "WINDOWS_TASK_SCHEDULER"
        and heartbeat.get("daemon_status") == "RUNNING"
        and _recent(heartbeat.get("last_run_at"))
    )
    checks.append(_result(
        "RUNNING_WHILE_UI_CLOSED", beat_ok,
        f"Heartbeat: {heartbeat.get('last_run_at', 'none')}; "
        f"mode={heartbeat.get('run_mode', 'unknown')}",
        "Start the Windows scheduled task, close the dashboard, and verify a fresh heartbeat."
    ))

    acquisition_ok = (
        scan.get("status") in ("COMPLETED", "INFERENCE_PARTIAL")
        and scan.get("is_certified_source") is True
        and _recent(scan.get("checked_at"), max_seconds=900)
        and (data / "authorized_feed" / "latest_jvlink_feed.json").is_file()
    )
    checks.append(_result(
        "REAL_JRA_FEED", acquisition_ok,
        f"Recorded status={scan.get('status', 'none')}; "
        f"certified={scan.get('is_certified_source', False)}",
        "On a racing day, connect real JRA-VAN Data Lab access and verify a freshly acquired signed feed."
    ))

    ai_ok = acquisition_ok and int(scan.get("processed_races") or 0) > 0
    checks.append(_result(
        "REAL_AI_INFERENCE", ai_ok,
        f"Races predicted={int(scan.get('processed_races') or 0)}",
        "Confirm win-probability inference for at least one live pre-race race."
    ))

    # A Discord webhook HTTP 200/204 is a server-side receipt. A mobile push
    # must still be acknowledged by the owner on their own phone.
    discord_delivery_ok = False
    last_delivery = "No recorded Discord delivery"
    db = data / "alert_state.db"
    if db.is_file():
        try:
            with closing(sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True, timeout=3)) as conn:
                row = conn.execute(
                    "SELECT first_sent_at, race_id, horse_number "
                    "FROM alert_delivery WHERE discord_status='DELIVERED' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if row:
                last_delivery = f"Discord accepted {row[0]}: {row[1]} horse #{row[2]}"
                discord_delivery_ok = _recent(row[0], max_seconds=7 * 86400)
        except (sqlite3.Error, OSError):
            last_delivery = "Discord delivery database unreadable"
    checks.append(_result(
        "DISCORD_SERVER_ACCEPTANCE", discord_delivery_ok, last_delivery,
        "Complete a real eligible candidate Discord webhook POST and confirm HTTP 200/204."
    ))

    return {
        "product": "KEIBA AI unattended all-race monitor",
        "note": "No Data Lab trial/session started; no Discord message sent.",
        "checks": checks,
        "machine_checkable_passed": sum(c["status"] == "PASS" for c in checks),
        "machine_checkable_total": len(checks),
        "manual_acceptance": [
            "Confirm the matching buy notification actually appeared on the phone.",
            "Reboot Windows, leave Streamlit closed, and verify monitoring resumes.",
            "Confirm full race coverage on an actual JRA racing day.",
        ],
        "ready_for_human_acceptance": all(c["status"] == "PASS" for c in checks),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="KEIBA AI unattended release checks")
    parser.add_argument("--json", action="store_true", help="Print machine-readable results")
    args = parser.parse_args(argv)
    result = check()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("KEIBA AI -- production acceptance checklist (read-only)")
        for c in result["checks"]:
            print(f"[{c['status']}] {c['code']}: {c['evidence']}")
            if c["next_action"]:
                print(f"    NEXT: {c['next_action']}")
        print("Manual checks: phone push received; monitoring survives reboot; full racing day.")
        print(f"Machine-checkable: {result['machine_checkable_passed']}/{result['machine_checkable_total']}")
    return 0 if result["ready_for_human_acceptance"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
