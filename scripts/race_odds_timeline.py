"""Read-only pre-race win-odds timeline, without future or settled prices.

Each row is included only if both provider-observed and locally-fetched
timestamps precede the requested cutoff and the race start. Manual timestamps
are user-attested, not independently certified.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

from scripts.detect_odds_drops import parse_time

ALLOWED_STATUSES = {"manual_prerace", "pre_race_flash"}


def race_odds_timeline(db_path, race_id, *, as_of_time, race_start_time):
    cutoff, start = parse_time(as_of_time), parse_time(race_start_time)
    if cutoff is None or start is None or cutoff >= start:
        raise ValueError("Require timezone-aware as-of earlier than race start")
    result = {"race_id": str(race_id), "as_of_time": cutoff.isoformat(),
              "race_start_time": start.isoformat(), "status": "NO_ODDS_DB",
              "bet_status": "NO_BET", "asof_certified": False,
              "observations": [], "rejected_rows": 0}
    db = Path(db_path)
    if not db.is_file():
        return result
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as conn:
        try:
            rows = conn.execute("""
                SELECT horse_number, odds_value, observed_at, fetched_at,
                       source, data_status, id
                FROM odds_timeline
                WHERE race_id = ? AND odds_type = 'win'
                ORDER BY id
            """, (str(race_id),)).fetchall()
        except sqlite3.OperationalError:
            result["status"] = "NO_ODDS_TABLE"
            return result
    snapshots = defaultdict(dict)
    rejected = 0
    for horse, price, observed, fetched, source, status, row_id in rows:
        obs, got = parse_time(observed), parse_time(fetched)
        try:
            number = int(horse)
            value = float(price)
        except (ValueError, TypeError, OverflowError):
            rejected += 1
            continue
        if (number < 1 or number > 99 or not math.isfinite(value) or value < 1
                or obs is None or got is None or obs > got
                or obs > cutoff or got > cutoff or obs >= start or got >= start
                or str(status).strip().lower() not in ALLOWED_STATUSES
                or str(source).strip().lower() in {"", "unknown", "none"}):
            rejected += 1
            continue
        # Different local arrival times for the same provider observation do
        # not constitute two independent market publications. Prefer the
        # earliest recorded arrival, then the earliest row ID.
        key = (obs, number)
        old = snapshots[key].get("row")
        candidate = (got, row_id, value, str(source), str(status))
        if old is None or candidate[:2] < old[:2]:
            snapshots[key]["row"] = candidate
    by_time = defaultdict(list)
    for (observed, number), payload in snapshots.items():
        fetched, row_id, value, source, status = payload["row"]
        by_time[observed].append({
            "horse_number": number, "win_odds": value,
            "fetched_at": fetched.isoformat(),
            "source": source, "data_status": status,
        })
    result["observations"] = [
        {"observed_at": when.isoformat(),
         "horses": sorted(horses, key=lambda row: row["horse_number"])}
        for when, horses in sorted(by_time.items())
    ]
    result["rejected_rows"] = rejected
    result["status"] = "AUDITED"
    return result


def main():
    parser = argparse.ArgumentParser(description="Offline as-of pre-race odds timeline")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--as-of-time", required=True)
    parser.add_argument("--race-start-time", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    result = race_odds_timeline(args.db, args.race_id,
                                as_of_time=args.as_of_time,
                                race_start_time=args.race_start_time)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
