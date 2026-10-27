"""Import externally obtained race schedules without scraping or reading results.

CSV is an input transport, not independent verification of race existence.
The output is a strict schedule-only manifest for the pre-race recorder.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ("race_id", "race_status", "race_time")
STATUSES = {"upcoming", "cancelled", "postponed"}


def import_schedule_csv(source, destination, *, now=None):
    source, destination = Path(source), Path(destination)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Current time must be timezone aware")
    if source.resolve() == destination.resolve():
        raise ValueError("Source and destination must differ")
    if not source.is_file():
        raise ValueError("Schedule CSV not found")
    records = []
    seen = set()
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames) != FIELDS:
            raise ValueError("Exact CSV headers required: race_id,race_status,race_time")
        for line, row in enumerate(reader, start=2):
            if set(row) != set(FIELDS) or any(v is None for v in row.values()):
                raise ValueError(f"Malformed schedule row {line}")
            race_id = row["race_id"].strip()
            status = row["race_status"].strip().lower()
            raw = row["race_time"].strip()
            if not race_id or len(race_id) > 128 or status not in STATUSES:
                raise ValueError(f"Invalid race identity or status at row {line}")
            try:
                start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"Invalid start time at row {line}") from exc
            if start.tzinfo is None or start.utcoffset() is None:
                raise ValueError(f"Timezone required at row {line}")
            if status == "upcoming" and start <= current:
                raise ValueError(f"Upcoming race already started at row {line}")
            if race_id in seen:
                raise ValueError(f"Duplicate race_id at row {line}")
            seen.add(race_id)
            records.append({"race_id": race_id, "race_status": status,
                            "race_time": start.isoformat()})
            if len(records) > 1000:
                raise ValueError("Schedule exceeds 1000 races")
    if not records:
        raise ValueError("Schedule CSV must contain races")
    records.sort(key=lambda row: (row["race_time"], row["race_id"]))
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".upcoming-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(records, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return {"status": "IMPORTED_UNVERIFIED_SCHEDULE",
            "race_count": len(records), "upcoming_count": sum(
                row["race_status"] == "upcoming" for row in records),
            "bet_status": "NO_BET", "destination": str(destination)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Strict race schedule CSV importer")
    parser.add_argument("csv")
    parser.add_argument("--out", default="data/upcoming_races.json")
    args = parser.parse_args(argv)
    print(json.dumps(import_schedule_csv(args.csv, args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
