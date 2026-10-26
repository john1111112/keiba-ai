"""Read-only 2025 odds as-of coverage audit. No results, labels, ROI or holdout access."""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path

def audit(db_path: str, *, start: str = "2025-01-01", end: str = "2025-12-31") -> dict:
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "odds_timeline" not in tables:
            return {"status": "BLOCKED_NO_ODDS_TIMELINE", "asof_certified": False,
                    "eligible_races": 0, "note": "No timestamped odds snapshots; historical final odds cannot certify pre-race evaluation."}
        cols = {row[1] for row in con.execute("PRAGMA table_info(odds_timeline)")}
        required = {"race_id", "horse_number", "odds_value", "observed_at", "fetched_at", "data_status"}
        if not required.issubset(cols):
            return {"status": "BLOCKED_MISSING_PROVENANCE_FIELDS", "asof_certified": False,
                    "missing_fields": sorted(required - cols), "eligible_races": 0}
        # Date range comes from race ID prefix (YYYYMMDD), not from any outcome table.
        # This audit does not certify actual race start times or authenticity of source timestamps.
        rows = con.execute("""
            SELECT race_id, COUNT(DISTINCT horse_number), COUNT(*)
            FROM odds_timeline
            WHERE substr(race_id,1,8) BETWEEN ? AND ?
              AND observed_at IS NOT NULL AND observed_at != ''
              AND fetched_at IS NOT NULL AND fetched_at != ''
              AND fetched_at >= observed_at
              AND odds_value >= 1.0
              AND lower(data_status) NOT IN ('final_settled','final','post_race')
            GROUP BY race_id
        """, (start.replace("-",""), end.replace("-",""))).fetchall()
        return {"status": "TIMESTAMPED_SNAPSHOTS_FOUND_NOT_ASOF_CERTIFIED" if rows else "BLOCKED_NO_VALID_SNAPSHOTS",
                "asof_certified": False, "candidate_races": len(rows),
                "candidate_runner_snapshots": sum(r[2] for r in rows),
                "eligible_races": 0,
                "note": "Requires independently verified race start times, source provenance and full-runner snapshot coverage before scoring."}
    finally:
        con.close()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/odds_history.db")
    p.add_argument("--start", default="2025-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--out", default=None, help="Path to write JSON output")
    args = p.parse_args()
    res = audit(args.db, start=args.start, end=args.end)
    out_json = json.dumps(res, ensure_ascii=False, indent=2)
    print(out_json)
    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(out_json + "\n", encoding="utf-8")
