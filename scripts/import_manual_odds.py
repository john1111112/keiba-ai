"""Import manually observed pre-race win odds, without inventing timestamps.

JSON format:
{
  "race_id": "202610250811", "race_start_time": "2026-10-25T15:40:00+09:00",
  "observed_at": "2026-10-25T15:35:00+09:00",
  "fetched_at": "2026-10-25T15:35:05+09:00",
  "source": "manual_verified_screen", "odds": [
    {"horse_number": 1, "odds_value": 4.5},
    {"horse_number": 2, "odds_value": 12.0}
  ]
}
Manual input is NOT independently certified. Save only user-attested times.
"""
import argparse
import json
import math
import sqlite3
from pathlib import Path

from scripts.detect_odds_drops import parse_time


def validate_snapshot(data):
    if not isinstance(data, dict):
        raise ValueError("Snapshot must be a JSON object")
    required = ("race_id", "race_start_time", "observed_at", "fetched_at", "source", "odds")
    if any(not data.get(key) for key in required):
        raise ValueError("Missing required snapshot metadata")
    start, observed, fetched = (parse_time(data[key]) for key in
                                ("race_start_time", "observed_at", "fetched_at"))
    if not all((start, observed, fetched)) or not (observed <= fetched < start):
        raise ValueError("Require timezone-aware observed <= fetched < race start")
    if str(data["source"]).strip().lower() in {"unknown", "final", "post_race"}:
        raise ValueError("Explicit non-final source is required")
    if not isinstance(data["odds"], list) or not data["odds"]:
        raise ValueError("Odds must be a nonempty list")
    seen = set()
    validated = []
    for item in data["odds"]:
        if not isinstance(item, dict):
            raise ValueError("Each odds entry must be an object")
        try:
            horse = int(item["horse_number"])
            price = float(item["odds_value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid horse number or odds") from exc
        if horse < 1 or horse in seen or not math.isfinite(price) or price < 1:
            raise ValueError("Duplicate horse or invalid odds")
        seen.add(horse)
        validated.append((horse, price))
    return validated


def import_snapshot(json_path, db_path, *, dry_run=False):
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    odds = validate_snapshot(data)
    result = {"status": "VALIDATED" if dry_run else "IMPORTED",
              "race_id": str(data["race_id"]), "odds_count": len(odds),
              "provenance": "MANUAL_USER_ATTESTED_NOT_CERTIFIED"}
    if dry_run:
        return result
    # Separate DB; never modify historical results database.
    from src.odds_history import init_odds_history_db
    init_odds_history_db(Path(db_path))
    with sqlite3.connect(db_path) as conn:
        for horse, price in odds:
            # Idempotent exact duplicate guard, without overwriting any records.
            exists = conn.execute("""
                SELECT 1 FROM odds_timeline WHERE race_id = ? AND horse_number = ?
                AND odds_type = 'win' AND observed_at = ? AND fetched_at = ?
                AND source = ? AND odds_value = ? LIMIT 1
            """, (str(data["race_id"]), horse, data["observed_at"],
                  data["fetched_at"], data["source"], price)).fetchone()
            if exists:
                continue
            conn.execute("""
                INSERT INTO odds_timeline
                (race_id, horse_number, odds_type, odds_value, observed_at,
                 fetched_at, source, data_status, timing_tag)
                VALUES (?, ?, 'win', ?, ?, ?, ?, 'manual_prerace', 'manual')
            """, (str(data["race_id"]), horse, price, data["observed_at"],
                  data["fetched_at"], data["source"]))
    return result


def main():
    parser = argparse.ArgumentParser(description="Import user-attested pre-race odds JSON")
    parser.add_argument("snapshot_json")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(import_snapshot(args.snapshot_json, args.db, dry_run=args.dry_run),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
