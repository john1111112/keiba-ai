"""Read-only inventory of horse-racing DB columns and null coverage.

No table content or personal data is exported; only schema and aggregate counts.
Run: python scripts/market_gap_data_audit.py --db data/keiba.db --out data/reports/market_gap_audit.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

FEATURE_GROUPS = {
    "horse_identity": ("horse_id", "horse_name", "horse_number"),
    "jockey_trainer": ("jockey_id", "jockey", "trainer_id", "trainer"),
    "horse_body": ("horse_weight", "horse_weight_diff", "weight_carry"),
    "track_weather": ("race_course", "distance", "surface", "track_condition", "weather"),
    "past_performance": ("finish_order", "time_sec", "last_3f", "margin_str"),
    "market": ("odds", "popularity", "observed_at", "snapshot_at", "bet_type"),
    "payout": ("payout", "payout_amount"),
    "training_pedigree": ("training_time", "sire", "dam"),
}

# These are schema-level hints, not proof that a feature was available pre-race.
MODEL_USAGE_EVIDENCE = {
    "age": "phase19_realdata_validation.py:raw_score",
    "weight_carry": "phase19_realdata_validation.py:raw_score",
    "horse_number": "phase19_realdata_validation.py:raw_score",
}


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def audit(db_path: str) -> dict:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    # Immutable read-only connection: never modify the source database.
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [
            name for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        result = {}
        for table in tables:
            cols = [row[1] for row in connection.execute(f"PRAGMA table_info({_ident(table)})")]
            count = connection.execute(f"SELECT COUNT(*) FROM {_ident(table)}").fetchone()[0]
            coverage = {}
            for col in cols:
                nonnull = connection.execute(
                    f"SELECT COUNT(*) FROM {_ident(table)} WHERE {_ident(col)} IS NOT NULL"
                ).fetchone()[0]
                coverage[col] = {
                    "non_null": nonnull,
                    "null": count - nonnull,
                    "non_null_fraction": round(nonnull / count, 6) if count else None,
                }
            result[table] = {"rows": count, "columns": coverage}
        groups = {}
        for group, names in FEATURE_GROUPS.items():
            groups[group] = {
                name: [
                    {"table": table, **result[table]["columns"][name]}
                    for table in tables if name in result[table]["columns"]
                ]
                for name in names
            }
        return {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "database": path.name,
            "tables": result,
            "feature_groups": groups,
            "known_model_usage": MODEL_USAGE_EVIDENCE,
            "provenance_status": "NOT_VERIFIED",
            "pre_race_timestamp_status": "NOT_VERIFIED",
            "historical_odds_status": "NOT_VERIFIED",
            "roi": None,
            "notes": [
                "Column existence and non-null values do not establish accuracy, license, or pre-race availability.",
                "Historical race outcomes can only be used for predictions of subsequent races.",
                "Phase19 raw_score uses age, weight_carry and horse_number; other columns are not thereby model inputs.",
                "Do not treat post-race odds as decision-time odds.",
            ],
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/keiba.db")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    report = audit(args.db)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
