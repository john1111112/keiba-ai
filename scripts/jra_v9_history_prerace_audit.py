"""V9: read-only audit of historical JRA-VAN results vs prospective RA/SE cards.

This module is intentionally NOT a training authorizer. In particular:
  * Historical race outcome and final odds are forbidden as CURRENT race inputs.
  * An older official RESULT may be a prior-run feature only when the original
    JV-Link archive was acquired BEFORE the new prospective card snapshot.
  * A current-week card is not proof that old backtests had pre-off snapshots.
  * A source receipt's self-description and the PC clock are not an independent
    attestation of odds, publication time, or provenance.
Never import legacy keiba.db or unlock notifications from this report.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

JST = timezone(timedelta(hours=9))
MIN_PRIOR_STARTS = 3


class V9AuditBlocked(RuntimeError):
    pass


def _utc(value):
    if not isinstance(value, str):
        raise V9AuditBlocked("Missing source observation timestamp")
    try:
        x = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V9AuditBlocked("Invalid source timestamp") from exc
    if x.tzinfo is None or x.utcoffset() is None:
        raise V9AuditBlocked("Naive timestamp cannot prove historical availability")
    return x.astimezone(timezone.utc)


def _digest(path):
    if not path.is_file():
        raise V9AuditBlocked("Original JRA-VAN raw archive missing")
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(2 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _connect(path):
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        return db
    except sqlite3.Error as exc:
        raise V9AuditBlocked("Source research DB not readable") from exc


def _require_tables(con, required):
    available = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = required - available
    if missing:
        raise V9AuditBlocked("Required research/evidence schema missing: " + ",".join(sorted(missing)))


def _original_archive(path, expected_sha, expected_spec, *, current_week=False):
    if not (isinstance(expected_sha, str) and len(expected_sha) == 64):
        raise V9AuditBlocked("Malformed source receipt hash")
    if _digest(path) != expected_sha:
        raise V9AuditBlocked("Original raw archive HASH_MISMATCH: " + path.name)
    with closing(_connect(path)) as con:
        _require_tables(con, {"acquisition", "jv_record"})
        items = con.execute(
            "SELECT data_spec,from_time,captured_at_utc,record_count,sdk_result,origin "
            "FROM acquisition").fetchall()
        if len(items) != 1:
            raise V9AuditBlocked("Raw capture must have exactly one acquisition receipt")
        r = items[0]
        if (r["data_spec"] != expected_spec or
            r["sdk_result"] != "JVOpen+JVGets_EOF_SUCCESS" or
            r["origin"] != "JRA-VAN/JV-Link COM"):
            raise V9AuditBlocked("Raw source provenance receipt not acceptable")
        if current_week and r["from_time"] != "00000000000000":
            raise V9AuditBlocked("Future-card source is not current-week RACE capture")
        if con.execute("SELECT COUNT(*) FROM jv_record").fetchone()[0] != r["record_count"]:
            raise V9AuditBlocked("Original record count does not match acquisition receipt")
        _utc(r["captured_at_utc"])
        return dict(r)


def audit_v9(research_db, snapshot_dbs):
    """Produce aggregate eligibility/missingness counters; NEVER emit predictions."""
    research_db = Path(research_db).resolve()
    snapshots = [Path(x).resolve() for x in snapshot_dbs]
    if not research_db.is_file() or research_db.name == "keiba.db":
        raise V9AuditBlocked("No verified research database or legacy DB forbidden")
    if len(snapshots) != len(set(snapshots)):
        raise V9AuditBlocked("Duplicate pre-off snapshot DB")
    if any(x == research_db or x.parent.parent != research_db.parent for x in snapshots):
        raise V9AuditBlocked("Prospective cards must reside in matching prerace_evidence folder")

    receipts = {}
    historical = defaultdict(list)
    with closing(_connect(research_db)) as db:
        _require_tables(db, {"source_receipts", "race", "runner", "audit"})
        evidence = dict(db.execute("SELECT key,value_json FROM audit").fetchall())
        try:
            if (json.loads(evidence["source_hashes_verified"]) is not True or
                json.loads(evidence["training_unlocked"]) is not False or
                json.loads(evidence["legacy_keiba_db_used"]) is not False):
                raise V9AuditBlocked("V7 research safety flags not valid")
        except (KeyError, ValueError, TypeError) as exc:
            raise V9AuditBlocked("Missing V7 research safety audit") from exc
        for item in db.execute("SELECT source_file,sha256,data_spec,captured_at_utc,record_count FROM source_receipts"):
            name = item["source_file"]
            if Path(name).name != name or not name.startswith(("jv_RACE_", "jv_RCVN_")):
                raise V9AuditBlocked("Unsafe source archive name")
            if item["data_spec"] not in ("RACE", "RCVN"):
                raise V9AuditBlocked("Unknown historical data type")
            archive = _original_archive(research_db.parent / name, item["sha256"], item["data_spec"])
            captured = _utc(item["captured_at_utc"])
            if captured != _utc(archive["captured_at_utc"]) or item["record_count"] != archive["record_count"]:
                raise V9AuditBlocked("Research receipt differs from official raw receipt")
            if name in receipts:
                raise V9AuditBlocked("Duplicated historical receipt")
            receipts[name] = captured
        if not receipts:
            raise V9AuditBlocked("No verified historical raw archives available")
        query = (
            "SELECT h.race_id,h.horse_id,h.finish_post,h.source_file,h.source_sha256,"
            "h.data_kubun,r.race_date,r.runners_declared,r.complete_postrace_result "
            "FROM runner h JOIN race r ON r.race_id=h.race_id"
        )
        seen = set()
        for row in db.execute(query):
            if not row["horse_id"] or not row["source_sha256"]:
                continue
            if row["source_file"] not in receipts:
                raise V9AuditBlocked("Runner has no verified historical source receipt")
            key = (row["race_id"], row["horse_id"])
            if key in seen:
                raise V9AuditBlocked("Duplicate horse/race identity in V7 research DB")
            seen.add(key)
            n, finish = row["runners_declared"], row["finish_post"]
            if (row["complete_postrace_result"] != 1
                or type(n) is not int or type(finish) is not int
                or not 5 <= n <= 28 or not 1 <= finish <= n
                or row["data_kubun"] not in ("6", "7")):
                continue
            # The archive may have been received after the race but only before
            # the PROSPECTIVE target event is it a valid prior observation.
            historical[row["horse_id"]].append({
                "race_id": row["race_id"],
                "date": row["race_date"],
                "source_received": receipts[row["source_file"]],
                "normalized_finish_rank": (n - finish) / (n - 1),
            })

    report = {
        "status": "V9_RESEARCH_AUDIT_ONLY_NO_TRAINING",
        "historical_verified_source_archives": len(receipts),
        "prospective_snapshots_checked": 0,
        "eligible_future_races": 0,
        "future_runner_rows": 0,
        "runners_with_3_prior_starts_observed_before_snapshot": 0,
        "runners_with_insufficient_prior_starts": 0,
        "runners_with_unknown_historical_identity": 0,
        "prior_runs_used": 0,
        "snapshot_source_archive_hash_verified": True,
        "research_source_archive_hash_verified": True,
        "independent_publication_time_attestation": False,
        "train_model": False,
        "training_unlocked": False,
        "betting_recommendations": [],
        "alerts_sent": False,
        "warning": "Valid prospective form evidence is NOT a historical as-of backtest, and does not approve training or betting.",
    }
    distinct = set()
    for snap in snapshots:
        with closing(_connect(snap)) as con:
            _require_tables(con, {"capture_evidence", "race_prerace", "runner_prerace"})
            meta = con.execute(
                "SELECT source_archive_name,source_archive_sha256,capture_finished_at_utc,"
                "source_type,clock_attestation,research_only,training_unlocked,alerts_sent "
                "FROM capture_evidence").fetchall()
            if len(meta) != 1:
                raise V9AuditBlocked("Prospective snapshot must have one receipt")
            receipt = meta[0]
            if (receipt["source_type"] != "JVOpen RACE option=2"
                or receipt["research_only"] != 1
                or receipt["training_unlocked"] != 0
                or receipt["alerts_sent"] != 0):
                raise V9AuditBlocked("Prospective evidence safety flags do not match v8.1")
            source_name = receipt["source_archive_name"]
            if (Path(source_name).name != source_name
                or not source_name.startswith("current_week_RACE_")):
                raise V9AuditBlocked("Invalid prospective source archive name")
            raw = _original_archive(snap.parent / source_name, receipt["source_archive_sha256"], "RACE", current_week=True)
            observed = _utc(receipt["capture_finished_at_utc"])
            if observed < _utc(raw["captured_at_utc"]):
                raise V9AuditBlocked("Snapshot completed before raw source started")
            report["prospective_snapshots_checked"] += 1
            for race in con.execute(
                "SELECT race_id,post_time_utc,observed_at_utc,runners_declared FROM race_prerace"):
                race_id = race["race_id"]
                race_key = (race_id, race["observed_at_utc"])
                if race_key in distinct:
                    # Duplicate samples must not inflate backtest sample counts.
                    continue
                distinct.add(race_key)
                post = _utc(race["post_time_utc"])
                obs = _utc(race["observed_at_utc"])
                if obs != observed or post <= obs + timedelta(minutes=15):
                    raise V9AuditBlocked("Card is not a correctly timed prospective snapshot")
                runners = con.execute(
                    "SELECT horse_id,horse_number,se_raw_sha256 FROM runner_prerace WHERE race_id=?",
                    (race_id,)).fetchall()
                n = race["runners_declared"]
                if (type(n) is not int or not 5 <= n <= 28
                    or len(runners) != n
                    or len({r["horse_id"] for r in runners}) != n
                    or len({r["horse_number"] for r in runners}) != n
                    or any(not r["se_raw_sha256"] for r in runners)):
                    raise V9AuditBlocked("Incomplete or duplicated prospective race field")
                report["eligible_future_races"] += 1
                for runner in runners:
                    report["future_runner_rows"] += 1
                    hid = runner["horse_id"]
                    if hid not in historical:
                        report["runners_with_unknown_historical_identity"] += 1
                    # Conservative day-level cutoff. The target day's old races
                    # are never treated as before today's off without precise time.
                    cutoff_date = obs.astimezone(JST).date().isoformat()
                    prior = [
                        h for h in historical.get(hid, [])
                        if h["date"] < cutoff_date and h["source_received"] <= obs
                    ]
                    prior.sort(key=lambda h: (h["date"], h["race_id"]), reverse=True)
                    if len(prior) < MIN_PRIOR_STARTS:
                        report["runners_with_insufficient_prior_starts"] += 1
                        continue
                    report["runners_with_3_prior_starts_observed_before_snapshot"] += 1
                    report["prior_runs_used"] += MIN_PRIOR_STARTS
    if report["eligible_future_races"] == 0:
        report["status"] = "V9_NO_COMPLETE_FUTURE_CARD_YET"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only cross-era JRA-VAN evidence audit")
    parser.add_argument("--research-db", required=True)
    parser.add_argument("--snapshots-dir", required=True)
    args = parser.parse_args(argv)
    snapshots = sorted(Path(args.snapshots_dir).glob("prerace_evidence*.sqlite3"))
    try:
        result = audit_v9(args.research_db, snapshots)
    except (V9AuditBlocked, OSError, sqlite3.DatabaseError) as exc:
        result = {
            "status": "V9_AUDIT_BLOCKED",
            "reason": str(exc)[:300],
            "training_unlocked": False, "alerts_sent": False,
            "betting_recommendations": [],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
