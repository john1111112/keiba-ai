"""Read-only classification of race-result evidence; not an official starter-status feed.

No status (scratched/excluded/DNF) is inferred from missing time or finish_order=15.
External verified status is accepted only when explicitly supplied by an operator.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd

ALLOWED_VERIFIED = {'STARTED', 'SCRATCHED', 'EXCLUDED', 'NOT_STARTED'}


def audit_rows(db_path: str | Path) -> pd.DataFrame:
    # SQLite immutable URI ensures that auditing cannot mutate the production DB.
    uri = Path(db_path).resolve().as_uri() + '?mode=ro&immutable=1'
    with sqlite3.connect(uri, uri=True) as con:
        df = pd.read_sql_query('''SELECT r.race_id, r.date, rr.horse_number,
            rr.horse_id, rr.horse_name, rr.finish_order, rr.time_str,
            rr.odds, rr.popularity, rr.margin_str
            FROM race_results rr JOIN races r ON rr.race_id=r.race_id
            ORDER BY r.date,r.race_id,rr.horse_number''', con)
    time_present = df['time_str'].fillna('').astype(str).str.strip().ne('') & df['time_str'].fillna('').astype(str).str.strip().ne('-')
    finish = pd.to_numeric(df['finish_order'], errors='coerce')
    df['evidence_status'] = 'UNRESOLVED_NO_TIME'
    df.loc[finish.eq(0), 'evidence_status'] = 'NO_RESULT_RECORDED'
    df.loc[time_present & finish.ge(1) & finish.le(28), 'evidence_status'] = 'TIMED_FINISH'
    df['verified_start_status'] = 'UNKNOWN'
    df['training_eligible'] = False
    return df


def apply_verified_status(audit: pd.DataFrame, verified: pd.DataFrame) -> pd.DataFrame:
    """Join manually verified *pre-race* statuses; never guess unknown rows.

    verified columns: race_id, horse_number, start_status, verified_source,
    verified_at. Only STARTED with a known outcome can be labeled safely.
    """
    needed = {'race_id','horse_number','start_status','verified_source','verified_at'}
    if not needed.issubset(verified.columns):
        raise ValueError(f'missing verified columns: {sorted(needed-set(verified.columns))}')
    if verified.duplicated(['race_id','horse_number']).any():
        raise ValueError('duplicate verified horse key')
    if not verified['start_status'].isin(ALLOWED_VERIFIED).all():
        raise ValueError('unknown verified start status')
    if verified[['verified_source','verified_at']].isna().any().any() or (verified['verified_source'].astype(str).str.strip() == '').any():
        raise ValueError('verified source/time required')
    v = verified.rename(columns={'start_status':'verified_start_status'})
    out = audit.drop(columns=['verified_start_status','training_eligible']).merge(v, how='left', on=['race_id','horse_number'], validate='one_to_one')
    out['verified_start_status'] = out['verified_start_status'].fillna('UNKNOWN')
    # A recorded positive placing is an observed outcome; for DNF, a verified
    # STARTED alone is insufficient without an authoritative finish outcome.
    out['training_eligible'] = out['verified_start_status'].eq('STARTED') & out['evidence_status'].eq('TIMED_FINISH')
    return out
