"""Batch ingest manually captured odds snapshots from a local folder.

Explicit one-shot operation: no polling, network access, auto timestamps, or
betting. Files are processed in lexicographic order. Invalid files are
reported and left unchanged, so a user can repair and retry them.
"""
import argparse
import json
from pathlib import Path

from scripts.import_manual_odds import import_snapshot


def ingest_directory(folder, db_path, *, dry_run=False):
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError("Snapshot directory does not exist")
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".json")
    results = []
    for path in files:
        try:
            imported = import_snapshot(path, db_path, dry_run=dry_run)
            results.append({"file": path.name, "status": imported["status"],
                            "race_id": imported["race_id"],
                            "inserted_count": imported.get("inserted_count", 0),
                            "skipped_duplicate_count": imported.get("skipped_duplicate_count", 0),
                            "provenance": imported["provenance"]})
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            results.append({"file": path.name, "status": "REJECTED",
                            "error": str(exc)})
    return {
        "status": "PARTIAL_FAILURE" if any(r["status"] == "REJECTED" for r in results)
                  else "COMPLETE",
        "bet_status": "NO_BET",
        "asof_certified": False,
        "dry_run": bool(dry_run),
        "files_found": len(files),
        "imported_files": sum(r["status"] == "IMPORTED" for r in results),
        "rejected_files": sum(r["status"] == "REJECTED" for r in results),
        "inserted_rows": sum(r.get("inserted_count", 0) for r in results),
        "files": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Batch import manual pre-race odds JSON files")
    parser.add_argument("folder")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--out", help="Optional JSON report path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = ingest_directory(args.folder, args.db, dry_run=args.dry_run)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output + "\n", encoding="utf-8")
    return 1 if report["rejected_files"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
