"""Read-only, race-level audit of ALL stored race, runner, payout and provenance records.

This NEVER asserts a row is genuine from realistic-looking identifiers.
It neither deletes nor modifies the DB, model, or any wagering/notification state.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from datetime import datetime
import csv
import json
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data/keiba.db"
OUT = ROOT / "dataset_provenance_audit"
OUT.mkdir(exist_ok=True)

COURSE = {
    "01": "札幌", "02": "函館", "03": "福島", "04": "新潟", "05": "東京",
    "06": "中山", "07": "中京", "08": "京都", "09": "阪神", "10": "小倉",
}
DATABASE_URI = f"file:{DATABASE}?mode=ro"
conn = sqlite3.connect(DATABASE_URI, uri=True)
conn.row_factory = sqlite3.Row
tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
required = {"races", "race_results", "payouts"}
assert required.issubset(tables), f"Missing expected tables {required - tables}"
columns = {
    name: [r[1] for r in conn.execute(f"PRAGMA table_info({name})")]
    for name in sorted(required)
}

def sqlcount(query):
    return int(conn.execute(query).fetchone()[0])

race_rows = [dict(r) for r in conn.execute(
    "SELECT race_id, race_name, date, race_course, race_number, surface, distance,"
    "direction, weather, condition, grade, created_at FROM races ORDER BY date,race_id")]
stats = {
    "races": len(race_rows),
    "runner_records": sqlcount("SELECT COUNT(*) FROM race_results"),
    "payout_records": sqlcount("SELECT COUNT(*) FROM payouts"),
    "horse_names_matching_synthetic_generator_prefix": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE horse_name GLOB '競走馬_*'"),
    "horse_ids_matching_synthetic_generator_prefix": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE horse_id GLOB 'h_*'"),
    "jockey_ids_matching_synthetic_generator_prefix": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE jockey_id GLOB 'j_*'"),
    "trainer_ids_matching_synthetic_generator_prefix": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE trainer_id GLOB 't_*'"),
    "pedigree_exact_kizuna_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE sire='キズナ'"),
    "rest_weeks_exact_4_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE rest_weeks=4"),
    "horse_weight_exact_478_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE horse_weight=478"),
    "recorded_odds_15_exact_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE odds=15.0"),
    "odds_equals_old_popularity_formula_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE odds IS NOT NULL "
        "AND popularity IS NOT NULL AND popularity>0 "
        "AND ABS(odds - ROUND(MAX(1.5, popularity*2.8), 1)) < 0.00001"),
    "unlinked_payout_rows": sqlcount(
        "SELECT COUNT(*) FROM payouts p LEFT JOIN races r ON p.race_id=r.race_id WHERE r.race_id IS NULL"),
    "missing_horse_name_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE horse_name IS NULL OR TRIM(horse_name)=''"),
    "missing_horse_id_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE horse_id IS NULL OR TRIM(horse_id)=''"),
    "missing_jockey_id_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE jockey_id IS NULL OR TRIM(jockey_id)=''"),
    "missing_trainer_id_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE trainer_id IS NULL OR TRIM(trainer_id)=''"),
    "missing_or_zero_odds_rows": sqlcount(
        "SELECT COUNT(*) FROM race_results WHERE odds IS NULL OR odds<=0"),
    "duplicate_horse_same_date_race_pairs": sqlcount(
        "SELECT COUNT(*) FROM (SELECT r.date,res.horse_id "
        "FROM race_results res JOIN races r ON r.race_id=res.race_id "
        "WHERE res.horse_id IS NOT NULL "
        "GROUP BY r.date,res.horse_id HAVING COUNT(DISTINCT r.race_id)>1)"),
    "race_rows_inserted_after_race_date": sqlcount(
        "SELECT COUNT(*) FROM races WHERE created_at IS NOT NULL AND DATE(created_at)>date"),
}

r_summary = {}
for row in conn.execute("""
    SELECT race_id, COUNT(*) AS n,
           SUM(CASE WHEN finish_order=1 THEN 1 ELSE 0 END) AS n_winner,
           SUM(CASE WHEN finish_order>0 AND finish_order<=28 THEN 1 ELSE 0 END) AS n_finished,
           SUM(CASE WHEN finish_order=0 THEN 1 ELSE 0 END) AS n_pre
    FROM race_results GROUP BY race_id
"""):
    r_summary[str(row[0])] = dict(runner_count=int(row[1]),
        winner_count=int(row[2] or 0), finished_count=int(row[3] or 0),
        unconfirmed_count=int(row[4] or 0))

suspicious = defaultdict(int)
per_race=[]
for r in race_rows:
    rid = str(r["race_id"])
    row = dict(r_summary.get(rid) or {})
    issues = []
    if not (len(rid)==12 and rid.isdigit()):
        issues.append("NON_JRA_12_DIGIT_RACE_ID")
    else:
        expected = COURSE.get(rid[4:6])
        if not expected:
            issues.append("UNRECOGNIZED_COURSE_CODE")
        elif expected != str(r["race_course"]):
            issues.append("COURSE_CODE_MISMATCH")
        try:
            rn = int(rid[-2:])
            if rn!=int(r["race_number"] or -1):
                issues.append("RACE_NUMBER_MISMATCH")
            if not (1 <= rn <= 12):
                issues.append("RACE_NUMBER_OUT_OF_RANGE")
        except ValueError:
            issues.append("INVALID_RACE_NUMBER")
        if str(r["date"])[:4]!=rid[:4]:
            issues.append("DATE_YEAR_MISMATCH")
    if not row:
        issues.append("NO_RUNNERS")
    elif row["runner_count"]<5:
        issues.append("UNDER_5_HORSES_OR_PARTIAL_SCRAPE")
    if row and row["finished_count"] > 0 and row["winner_count"] != 1:
        issues.append("NOT_EXACTLY_ONE_WINNER")
    if row and row["finished_count"] > 0 and row["unconfirmed_count"] > 0:
        issues.append("MIXED_FINISHED_AND_UNCONFIRMED")
    try:
        if datetime.fromisoformat(str(r["date"])[:10]).weekday() not in (5,6,0):
            issues.append("NON_WEEKEND_OR_MONDAY_RACING_DATE_REVIEW")
    except ValueError:
        issues.append("INVALID_CALENDAR_DATE")
    # No per-record as-of source-time or source URL exists in the current tables.
    # Each race is unauthenticated regardless of how plausible its values look.
    for issue in issues:
        suspicious[issue]+=1
    per_race.append({
        "race_id": rid, "date":r["date"],"venue":r["race_course"],
        "race_name":r["race_name"],
        "runners": row.get("runner_count", 0),
        "winner_count":row.get("winner_count",0),
        "finished":row.get("finished_count",0),
        "issues": "|".join(issues),
        "asof_source_authenticated":False,
    })

conn.close()
provenance_fields={"source_url","source_id","source_provider","retrieved_at","observed_at",
    "as_of","asof_timestamp","published_at","source_sha256","is_authentic"}
usable_provenance_columns={
    name:sorted({c for c in cols if c.lower() in provenance_fields})
    for name,cols in columns.items()
}
has_source_timestamp=bool(
    set(usable_provenance_columns["races"]+usable_provenance_columns["race_results"])
    & {"retrieved_at","observed_at","as_of","asof_timestamp","published_at"})
has_source_identity=bool(
    set(usable_provenance_columns["races"]+usable_provenance_columns["race_results"])
    & {"source_url","source_id","source_provider"})
report={
    "status":"FAIL_CLOSED_NO_CERTIFIED_REAL_PRERACE_TRAINING_DATA",
    "database":"data/keiba.db (read only)",
    "tables":columns,
    "counts":stats,
    "flagged_races_by_reason":dict(suspicious),
    "source_record_columns":usable_provenance_columns,
    "source_identity_col_available":has_source_identity,
    "source_observed_at_col_available":has_source_timestamp,
    "asof_certified_races":0,
    "asof_certified_runner_rows":0,
    "certification_policy":"Zero certified means source proof is missing, NOT that every race is proven invented.",
    "known_static_source_failures":[
        "src/scraper.py formerly included a callable random horse/race/odds/payout generator",
        "scripts/fetch_bulk_real_jra.py formerly generated bloodlines from MD5 of horse IDs",
        "scripts/populate_full_weekend.py formerly derived past odds by popularity*2.8",
        "scripts/populate_full_weekend.py formerly derived running styles from MD5 horse IDs",
        "Importer hard-coded rest_weeks=4 and fallback pedigree/weather/going/weight",
        "Saved payout table contains rows which cannot be linked to current race records",
    ],
    "research_model_win_rate_validated":False,
    "payout_roi_validated":False,
    "requires_next":[
        "Read-only inventory of raw source evidence/records for every included race and runner",
        "Reacquire verified race-entry and past-performance rows from licensed/official source",
        "Store source, raw evidence hash and observation time on ingestion",
        "Keep quarantined legacy rows outside training/validation",
        "Freeze train/test race IDs and verify held-out winner picks on certified pre-race snapshots",
    ],
}
(OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
with (OUT/"race_flags.csv").open("w",encoding="utf-8-sig",newline="") as fh:
    writer=csv.DictWriter(fh,fieldnames=list(per_race[0]))
    writer.writeheader()
    writer.writerows(per_race)
print("FORENSIC_AUDIT="+json.dumps({
    "status":report["status"],"counts":stats,"flagged_races_by_reason":dict(suspicious),
    "asof_certified_races":0,
    "source_identity_col_available":has_source_identity,
    "source_observed_at_col_available":has_source_timestamp,
},ensure_ascii=False))
