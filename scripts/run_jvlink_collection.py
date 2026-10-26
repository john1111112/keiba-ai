"""Batch JV-Link capture for all three data specs; explicit opt-in only."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.jvlink_raw_store import open_jvlink
from src.jvlink_ingest import capture

SPECS = ("0B15", "0B30", "0B41")


def collect(factory, db_path, *, sid, from_time, now=None):
    reports = []
    for spec in SPECS:
        try:
            result = capture(factory(), db_path, sid=sid, data_spec=spec,
                             from_time=from_time, fetched_at=now)
            reports.append({"spec": spec, **result})
        except Exception as exc:
            reports.append({"spec": spec, "status": "FAILED",
                            "error_type": type(exc).__name__, "bet_status": "NO_BET"})
    return {"status": "RAW_CAPTURE_COMPLETE" if all(
        row["status"] == "RAW_RECORDS_STORED_UNPARSED" for row in reports
    ) else "RAW_CAPTURE_PARTIAL_FAILURE",
        "checked_at": now or datetime.now(timezone.utc).isoformat(),
        "reports": reports, "bet_status": "NO_BET"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect raw JV-Link records")
    parser.add_argument("--sid", required=True)
    parser.add_argument("--from-time", required=True)
    parser.add_argument("--db", default="data/jvlink_raw.sqlite3")
    parser.add_argument("--confirm-trial-active", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_trial_active:
        parser.error("Will call JVInit; trial must already be deliberately active")
    print(json.dumps(collect(open_jvlink, args.db, sid=args.sid,
                             from_time=args.from_time), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
