"""Read-only audit. Usage: python -m scripts.audit_race_quality"""
from pathlib import Path
import sqlite3
import pandas as pd
from src.race_quality import assess_races

ROOT=Path(__file__).resolve().parents[1]
def run(db_path=ROOT/'data'/'keiba.db', output=ROOT/'reports'/'race_quality_review.csv'):
    uri=Path(db_path).resolve().as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as con:
        df=pd.read_sql_query('SELECT race_id, horse_number, finish_order, time_str FROM race_results',con)
    report=assess_races(df)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    report.to_csv(output,index=False,encoding='utf-8-sig')
    print(f'races={len(report)} candidates={report.candidate_for_review.sum()} verified=0 output={output}')
    return report
if __name__=='__main__':run()
