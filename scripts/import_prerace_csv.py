"""Import a human-captured pre-race win-odds CSV with explicit provenance.

One race and one observation per file. No web access, no guessed timestamps.
CSV columns: horse_number,odds_value. Metadata must be supplied explicitly.
This is user-attested evidence, never a certified market feed.
"""
import argparse
import csv
import json
import tempfile
from pathlib import Path

from scripts.import_manual_odds import validate_snapshot, import_snapshot
from scripts.prerace_readiness import race_readiness
from scripts.render_prerace_dashboard import render_dashboard


def parse_csv_snapshot(csv_path, *, race_id, race_start_time, observed_at,
                       fetched_at, source):
    path = Path(csv_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or set(reader.fieldnames) != {"horse_number", "odds_value"}:
            raise ValueError("CSV must have exactly horse_number,odds_value headers")
        odds = []
        for record in reader:
            if None in record or any(v is None for v in record.values()):
                raise ValueError("Malformed CSV row")
            odds.append({"horse_number": record["horse_number"],
                         "odds_value": record["odds_value"]})
    snapshot = {
        "race_id": race_id, "race_start_time": race_start_time,
        "observed_at": observed_at, "fetched_at": fetched_at,
        "source": source, "odds": odds,
    }
    validate_snapshot(snapshot)
    return snapshot


def run_csv_pipeline(csv_path, db_path, output_html, *, race_id,
                     race_start_time, observed_at, fetched_at, source,
                     dry_run=False):
    snapshot = parse_csv_snapshot(
        csv_path, race_id=race_id, race_start_time=race_start_time,
        observed_at=observed_at, fetched_at=fetched_at, source=source)
    if dry_run:
        return {"status": "VALIDATED_ONLY", "odds_count": len(snapshot["odds"]),
                "bet_status": "NO_BET", "provenance": "MANUAL_USER_ATTESTED_NOT_CERTIFIED"}
    # Reuse the existing atomic/idempotent importer. The temporary file is
    # created outside the output directory and removed after ingestion.
    with tempfile.TemporaryDirectory() as directory:
        payload = Path(directory) / "snapshot.json"
        payload.write_text(json.dumps(snapshot), encoding="utf-8")
        imported = import_snapshot(payload, db_path)
    report = race_readiness(
        db_path, race_id, as_of_time=fetched_at, race_start_time=race_start_time)
    if report["status"] != "NO_BET":
        raise ValueError("Unexpected certified status")
    html = Path(output_html)
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text(render_dashboard(report), encoding="utf-8")
    return {"status": "DASHBOARD_CREATED", "import": imported,
            "html": str(html), "bet_status": "NO_BET", "readiness": report}


def main():
    parser = argparse.ArgumentParser(description="Import human-attested pre-race win odds CSV")
    parser.add_argument("csv_path")
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--race-start-time", required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--fetched-at", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--out", default="data/prerace_dashboard.html")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_csv_pipeline(
        args.csv_path, args.db, args.out, race_id=args.race_id,
        race_start_time=args.race_start_time, observed_at=args.observed_at,
        fetched_at=args.fetched_at, source=args.source, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
