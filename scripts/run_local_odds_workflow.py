"""One-shot local pre-race odds workflow: ingest -> audit -> race dashboards.

Only user-attested manual JSON is accepted. No automated feed, inference,
bets, certification, or changes to the historical results database.
"""
import argparse
import json
from pathlib import Path

from scripts.audit_prerace_history import audit_odds_history
from scripts.detect_odds_drops import parse_time
from scripts.import_odds_folder import ingest_directory
from scripts.import_manual_odds import validate_snapshot
from scripts.prerace_readiness import race_readiness
from scripts.render_prerace_dashboard import render_dashboard
from scripts.render_race_index import write_index
from scripts.race_odds_timeline import race_odds_timeline
from scripts.compare_prerace_odds import compare_odds


def run_local_workflow(folder, db_path, output_dir, *, dry_run=False):
    folder = Path(folder)
    output_dir = Path(output_dir)
    # Validate manifest times without trusting filenames or deriving them from
    # historical results. Conflicting race starts must not silently overwrite.
    starts = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            validate_snapshot(data)
        except (ValueError, OSError, TypeError):
            continue  # batch importer will report rejected files
        race = str(data["race_id"])
        current = parse_time(data["race_start_time"])
        if race in starts and starts[race] != current:
            raise ValueError("Conflicting race start times for race " + race)
        starts[race] = current
    batch = ingest_directory(folder, db_path, dry_run=dry_run)
    if dry_run:
        return {"status": "VALIDATED_ONLY" if not batch["rejected_files"] else "PARTIAL_FAILURE",
                "bet_status": "NO_BET", "batch": batch,
                "audit": None, "dashboards": []}
    audit = audit_odds_history(db_path, race_starts={
        race: start.isoformat() for race, start in starts.items()
    })
    dashboards = []
    # Use only successfully validated files for report time boundaries.
    # For each race use the latest fetched_at across imported/duplicate files.
    latest = {}
    for file in batch["files"]:
        if file["status"] != "IMPORTED":
            continue
        data = json.loads((folder / file["file"]).read_text(encoding="utf-8"))
        race = str(data["race_id"])
        fetched = parse_time(data["fetched_at"])
        if race not in latest or fetched > latest[race]:
            latest[race] = fetched
    for race, fetched in sorted(latest.items()):
        report = race_readiness(db_path, race,
                                as_of_time=fetched.isoformat(),
                                race_start_time=starts[race].isoformat())
        if report["status"] != "NO_BET":
            raise ValueError("Unexpected bet recommendation")
        # Race IDs are user supplied. Do not use them as filesystem paths.
        import hashlib
        safe_name = hashlib.sha256(race.encode("utf-8")).hexdigest()[:20]
        target = output_dir / ("race_" + safe_name + ".html")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_dashboard(report), encoding="utf-8")
        timeline = race_odds_timeline(db_path, race,
            as_of_time=fetched.isoformat(),
            race_start_time=starts[race].isoformat())
        timeline_target = output_dir / ("race_" + safe_name + "_timeline.json")
        timeline_target.write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        # Produce an odds movement report only when two distinct publication
        # times are available. Never synthesize a baseline from missing odds.
        movement_path = None
        observations = timeline.get("observations", [])
        if len(observations) >= 2:
            # First publication is not necessarily available at publication
            # time. Use the latest arrival within the first snapshot so the
            # baseline contains only data actually fetched by that cutoff.
            first = max(parse_time(horse["fetched_at"])
                        for horse in observations[0]["horses"]).isoformat()
            last = fetched.isoformat()
            if parse_time(first) >= parse_time(last):
                first = None
            if first is not None:
                movement = compare_odds(db_path, race, earlier=first, later=last,
                                        race_start_time=starts[race].isoformat())
                movement_target = output_dir / ("race_" + safe_name + "_movement.json")
                movement_target.write_text(
                    json.dumps(movement, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                movement_path = str(movement_target)
        dashboards.append({"race_id": race, "html": str(target),
                           "timeline": str(timeline_target),
                           "movement": movement_path, "bet_status": "NO_BET"})
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "quality_audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    result = {"status": "PARTIAL_FAILURE" if batch["rejected_files"] else "COMPLETE",
              "bet_status": "NO_BET", "batch": batch,
              "audit": str(audit_path), "dashboards": dashboards}
    result["index"] = write_index(result, output_dir)
    return result


def main():
    parser = argparse.ArgumentParser(description="Offline manual odds import, audit and HTML reports")
    parser.add_argument("folder")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--out-dir", default="data/prerace_reports")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_local_workflow(args.folder, args.db, args.out_dir,
                                dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["status"] == "PARTIAL_FAILURE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
