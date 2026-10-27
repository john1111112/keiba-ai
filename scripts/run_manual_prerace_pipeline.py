"""One-command local manual odds -> audit -> HTML pipeline (no paid feed).

Input times are user-attested, not independently certified. Never places bets.
"""
import argparse
import json
from pathlib import Path

from scripts.import_manual_odds import import_snapshot, validate_snapshot
from scripts.prerace_readiness import race_readiness
from scripts.render_prerace_dashboard import render_dashboard


def run_manual_pipeline(snapshot_path, db_path, output_html, *, as_of_time=None, dry_run=False):
    snapshot = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    validate_snapshot(snapshot)
    # A later as-of is allowed for a read-only historical review, but it must
    # still precede the declared start. Import never certifies the source.
    from scripts.detect_odds_drops import parse_time
    as_of = as_of_time or snapshot["fetched_at"]
    cutoff = parse_time(as_of)
    start = parse_time(snapshot["race_start_time"])
    fetched = parse_time(snapshot["fetched_at"])
    if cutoff is None or start is None or fetched is None or not (fetched <= cutoff < start):
        raise ValueError("Require fetched_at <= as_of_time < race_start_time")
    if dry_run:
        result = import_snapshot(snapshot_path, db_path, dry_run=True)
        return {"status": "VALIDATED_ONLY", "import": result, "html": None,
                "bet_status": "NO_BET"}
    imported = import_snapshot(snapshot_path, db_path)
    report = race_readiness(db_path, str(snapshot["race_id"]),
                            as_of_time=as_of, race_start_time=snapshot["race_start_time"])
    if report["status"] != "NO_BET":
        raise ValueError("Unexpected readiness status; fail closed")
    output = Path(output_html)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_dashboard(report), encoding="utf-8")
    return {"status": "DASHBOARD_CREATED", "import": imported,
            "html": str(output), "bet_status": "NO_BET",
            "readiness": report}


def main():
    parser = argparse.ArgumentParser(description="Import user-attested odds and build a NO BET dashboard")
    parser.add_argument("snapshot_json")
    parser.add_argument("--db", default="data/odds_history.db")
    parser.add_argument("--out", default="data/prerace_dashboard.html")
    parser.add_argument("--as-of-time", help="Timezone-aware as-of time (default: snapshot fetched_at)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = run_manual_pipeline(args.snapshot_json, args.db, args.out,
                                 as_of_time=args.as_of_time, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
