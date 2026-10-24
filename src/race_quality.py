"""Conservative race-level label quality checks (NOT proof of complete fields)."""
from __future__ import annotations
import pandas as pd


def assess_races(rows: pd.DataFrame) -> pd.DataFrame:
    required = {'race_id','horse_number','finish_order','time_str'}
    if not required.issubset(rows):
        raise ValueError(f'missing columns: {sorted(required-set(rows))}')
    f = rows.copy()
    f['race_id'] = f['race_id'].astype(str)
    fo = pd.to_numeric(f['finish_order'],errors='coerce')
    timed = ~f['time_str'].fillna('').astype(str).str.strip().isin(['','-','None','nan'])
    f['_winner'] = fo.eq(1) & timed
    f['_finished'] = fo.between(1,28) & timed
    f['_unresolved'] = ~f['_finished']
    records=[]
    for rid,g in f.groupby('race_id',sort=True):
        duplicates = g['horse_number'].isna().any() or g['horse_number'].duplicated().any()
        winner_count=int(g['_winner'].sum())
        records.append({'race_id':rid,'rows':len(g),'winner_count':winner_count,
          'finished_rows':int(g['_finished'].sum()),'unresolved_rows':int(g['_unresolved'].sum()),
          'duplicate_horse_number':bool(duplicates),
          'candidate_for_review':bool(winner_count==1 and not duplicates and len(g)>=2),
          'verified_complete':False})
    return pd.DataFrame.from_records(records)


def select_reviewed_races(features:pd.DataFrame, review:pd.DataFrame)->pd.DataFrame:
    """Require an EXTERNAL, explicit verified_complete=True flag; never infer it from results."""
    if 'race_id' not in features or not {'race_id','verified_complete'}.issubset(review):
        raise ValueError('race_id/verified_complete required')
    if review['race_id'].duplicated().any():
        raise ValueError('duplicate review race ids')
    if review['verified_complete'].isna().any() or not review['verified_complete'].isin([True,False]).all():
        raise ValueError('verified_complete must be explicit bool')
    ids=set(review.loc[review['verified_complete'].eq(True),'race_id'].astype(str))
    return features.loc[features['race_id'].astype(str).isin(ids)].copy()
