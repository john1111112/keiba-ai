"""Local diagnostics for captured JV-Link records; no inferred race or odds."""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path


def audit_raw(db_path):
    path = Path(db_path)
    if not path.is_file():
        return {"status": "NO_JV_RECORDS", "record_count": 0, "bet_status": "NO_BET"}
    with sqlite3.connect(path) as conn:
        try:
            rows = conn.execute("""SELECT data_spec, record_type, COUNT(*),
                MIN(fetched_at), MAX(fetched_at) FROM jv_raw_records
                GROUP BY data_spec, record_type ORDER BY data_spec, record_type""").fetchall()
        except sqlite3.OperationalError:
            return {"status": "INVALID_JV_RECORD_DB", "record_count": 0,
                    "bet_status": "NO_BET"}
    return {"status": "RAW_DATA_ONLY_UNPARSED", "record_count": sum(r[2] for r in rows),
            "groups": [{"data_spec": r[0], "record_type": r[1],
                        "count": r[2], "first_received_at": r[3],
                        "last_received_at": r[4]} for r in rows],
            "bet_status": "NO_BET"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit raw JV-Link captures")
    parser.add_argument("--db", default="data/jvlink_raw.sqlite3")
    args = parser.parse_args(argv)
    print(json.dumps(audit_raw(args.db), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
