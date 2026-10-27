"""Convert a locally captured pre-race race card CSV to an auditable feature bundle.

This is a user-attested manual capture, not a live feed or certified data.
No network access, historical result queries, or betting actions.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path

from scripts.validate_prerace_features import ALLOWED, CATEGORICAL, validate_feature_bundle

CSV_COLUMNS = ("horse_number", "bracket_number", "age", "weight_carry",
               "distance", "race_course", "surface", "condition", "sex")
INTEGER = {"horse_number", "bracket_number", "age"}
FLOAT = {"weight_carry", "distance"}


def import_race_card_csv(csv_path, output_path, *, race_id, race_start_time,
                         captured_at, received_at, source, as_of_time, dry_run=False):
    csv_path, output_path = Path(csv_path), Path(output_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or len(reader.fieldnames) != len(CSV_COLUMNS) or set(reader.fieldnames) != set(CSV_COLUMNS):
            raise ValueError("CSV must contain exactly nine approved feature columns without duplicates")
        rows = []
        for raw in reader:
            if None in raw or any(value is None or not value.strip() for value in raw.values()):
                raise ValueError("Empty, malformed, or extra CSV field")
            converted = {}
            for field in CSV_COLUMNS:
                value = raw[field].strip()
                if field in INTEGER:
                    if not value.isascii() or not value.isdecimal():
                        raise ValueError("Invalid integer " + field)
                    converted[field] = int(value)
                elif field in FLOAT:
                    try:
                        converted[field] = float(value)
                    except ValueError as exc:
                        raise ValueError("Invalid numeric " + field) from exc
                elif field in CATEGORICAL:
                    converted[field] = value
                else:
                    raise ValueError("Unexpected field")
            rows.append(converted)
    bundle = {
        "race_id": race_id,
        "race_start_time": race_start_time,
        "captured_at": captured_at,
        "received_at": received_at,
        "source": source,
        "features": rows,
    }
    audit = validate_feature_bundle(bundle, as_of_time=as_of_time)
    if dry_run:
        return {**audit, "dry_run": True, "written": False}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if output_path.exists():
        # Never silently replace evidence or overwrite a changed snapshot.
        current = json.loads(output_path.read_text(encoding="utf-8"))
        if current != bundle:
            raise ValueError("Output already exists with different evidence")
        return {**audit, "dry_run": False, "written": False, "idempotent": True}
    fd, tmp_name = tempfile.mkstemp(prefix=".race_card_", suffix=".json", dir=output_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Exclusive create prevents replacing a file written concurrently.
        try:
            os.link(tmp_name, output_path)
        except FileExistsError as exc:
            raise ValueError("Output was created concurrently; no overwrite") from exc
    finally:
        Path(tmp_name).unlink(missing_ok=True)
    return {**audit, "dry_run": False, "written": True, "idempotent": False}


def main():
    parser = argparse.ArgumentParser(description="Offline pre-race race card CSV importer (NO BET)")
    parser.add_argument("csv")
    parser.add_argument("--out", required=True)
    parser.add_argument("--race-id", required=True)
    parser.add_argument("--race-start-time", required=True)
    parser.add_argument("--captured-at", required=True)
    parser.add_argument("--received-at", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--as-of-time", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = import_race_card_csv(args.csv, args.out, race_id=args.race_id,
                                  race_start_time=args.race_start_time,
                                  captured_at=args.captured_at, received_at=args.received_at,
                                  source=args.source, as_of_time=args.as_of_time,
                                  dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
