"""Read-only authenticity inspection of the committed historical race database.

A nonzero count of suspicious placeholders does NOT prove a given individual row
is fabricated. Conversely, complete-looking rows do NOT prove true provenance.
Never use this report to claim a certified as-of pre-race backtest.
"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "keiba.db"
OUTPUT = ROOT / "data_authenticity_output" / "report.json"
assert DB_PATH.exists(), "Historical database unavailable"
db = sqlite3.connect("file:" + str(DB_PATH) + "?mode=ro", uri=True)
db.row_factory = sqlite3.Row

def scalar(statement):
    return db.execute(statement).fetchone()[0]

n = int(scalar("SELECT COUNT(*) FROM race_results"))
races = int(scalar("SELECT COUNT(*) FROM races"))
queries = {
    "rows_with_sire_kizuna": "SELECT COUNT(*) FROM race_results WHERE sire='キズナ'",
    "rows_with_unknown_sire": "SELECT COUNT(*) FROM race_results WHERE sire IS NULL OR sire='' OR sire='不明'",
    "rows_with_odds_15_exact": "SELECT COUNT(*) FROM race_results WHERE odds=15.0",
    "rows_with_odds_matching_popularity_times_2_8": """SELECT COUNT(*) FROM race_results
        WHERE odds IS NOT NULL AND popularity IS NOT NULL AND popularity > 0
        AND ABS(odds - ROUND(MAX(1.5,popularity*2.8),1)) < 0.00001""",
    "rows_with_default_trainer_id_01000": "SELECT COUNT(*) FROM race_results WHERE trainer_id='01000'",
    "rows_with_default_jockey_id_01100": "SELECT COUNT(*) FROM race_results WHERE jockey_id='01100'",
    "rows_with_rest_weeks_4": "SELECT COUNT(*) FROM race_results WHERE rest_weeks=4",
    "rows_with_horse_weight_478": "SELECT COUNT(*) FROM race_results WHERE horse_weight=478",
    "rows_with_missing_horse_id": "SELECT COUNT(*) FROM race_results WHERE horse_id IS NULL OR horse_id='' OR horse_id LIKE 'h_%'",
    "rows_with_missing_trainer_id": "SELECT COUNT(*) FROM race_results WHERE trainer_id IS NULL OR trainer_id=''",
    "rows_with_missing_jockey_id": "SELECT COUNT(*) FROM race_results WHERE jockey_id IS NULL OR jockey_id=''",
    "rows_with_unknown_running_style": "SELECT COUNT(*) FROM race_results WHERE running_style IS NULL OR running_style=''",
    "rows_with_missing_odds": "SELECT COUNT(*) FROM race_results WHERE odds IS NULL OR odds <= 0",
    "payout_rows_without_matching_race": """SELECT COUNT(*) FROM payouts p
        LEFT JOIN races r ON p.race_id=r.race_id WHERE r.race_id IS NULL""",
    "stored_race_rows_created_after_race_date": """SELECT COUNT(*) FROM races
        WHERE created_at IS NOT NULL AND DATE(created_at) > date"""
}
stats={key:int(scalar(query)) for key,query in queries.items()}
years=[dict(row) for row in db.execute("""SELECT SUBSTR(r.date,1,4) AS year,
 COUNT(DISTINCT r.race_id) AS races, COUNT(res.id) AS horses
 FROM races r LEFT JOIN race_results res ON r.race_id=res.race_id
 GROUP BY SUBSTR(r.date,1,4) ORDER BY year""")]
db.close()
report={
 "status":"HISTORICAL_DATA_PROVENANCE_NOT_VERIFIED",
 "race_count":races,
 "runner_rows":n,
 "suspicious_value_counts":stats,
 "coverage_by_year":years,
 "first_priority":"Replace or validate records sourced from heuristics before any claimed competitive predictive accuracy",
 "hard_limitations":[
  "The code historically assigned horse pedigree, running style and previous-race odds with deterministic placeholders.",
  "Counts alone cannot identify exact fabricated records or authenticate other records.",
  "Historical DB created_at is database insert time, not necessarily source observation before post-time.",
  "No as-of pre-race availability or market-odds time proof is present for historical rows.",
  "No new races, results, data sources or wagers were fetched; read-only inspection only."
 ],
 "safe_to_claim_model_performance":False,
 "safe_to_claim_profitable_betting":False
}
OUTPUT.parent.mkdir(exist_ok=True)
OUTPUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print("DATA_AUTHENTICITY_REPORT="+json.dumps(report,ensure_ascii=False))
