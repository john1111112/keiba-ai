"""Phase 4E: conservative, opt-in supervision guard.

A verification of STARTED does not prove a finish or loss. In particular a
missing time is never silently turned into a negative training label.
"""
from __future__ import annotations
import pandas as pd

KEY = ['race_id', 'horse_number']
STATUS = {'STARTED', 'SCRATCHED', 'EXCLUDED', 'NOT_STARTED'}


def filter_training_evidence(features: pd.DataFrame, verified: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Return safely labelable feature rows and an audit of exclusions.

    `verified` must be the validated_evidence.csv produced by Phase 4D.
    This is NOT a substitute for source verification: a CSV can be forged.
    """
    if not set(KEY + ['finish_order', 'time_str']).issubset(features.columns):
        raise ValueError('features missing race/horse key or finish/time evidence')
    f = features.copy()
    f['race_id'] = f['race_id'].astype(str)
    f['horse_number'] = pd.to_numeric(f['horse_number'], errors='raise')
    if f['horse_number'].isna().any() or (f['horse_number'] % 1 != 0).any():
        raise ValueError('invalid horse_number in features')
    f['horse_number'] = f['horse_number'].astype(int)
    if f.duplicated(KEY).any():
        raise ValueError('duplicate feature keys')
    status = pd.Series('UNKNOWN', index=f.index)
    if verified is not None and not verified.empty:
        required = set(KEY + ['start_status', 'verified_source', 'source_reference', 'verified_at', 'reviewer'])
        if not required.issubset(verified.columns):
            raise ValueError(f'verified evidence missing: {sorted(required - set(verified.columns))}')
        v = verified.copy()
        v['race_id'] = v['race_id'].astype(str)
        v['horse_number'] = pd.to_numeric(v['horse_number'], errors='raise')
        if v['horse_number'].isna().any() or (v['horse_number'] % 1 != 0).any():
            raise ValueError('invalid horse_number in evidence')
        v['horse_number'] = v['horse_number'].astype(int)
        if v.duplicated(KEY).any():
            raise ValueError('duplicate evidence keys')
        if not v['start_status'].isin(STATUS).all():
            raise ValueError('invalid start status')
        for col in ['verified_source', 'source_reference', 'verified_at', 'reviewer']:
            if v[col].isna().any() or v[col].astype(str).str.strip().eq('').any():
                raise ValueError(f'missing evidence provenance: {col}')
        if pd.to_datetime(v['verified_at'], utc=True, errors='coerce').isna().any():
            raise ValueError('invalid evidence timestamp')
        check = v[KEY + ['start_status']].merge(f[KEY], on=KEY, how='left', indicator=True, validate='one_to_one')
        if check['_merge'].ne('both').any():
            raise ValueError('verified horse not present in features')
        status = f[KEY].merge(v[KEY + ['start_status']], on=KEY, how='left', validate='one_to_one')['start_status'].fillna('UNKNOWN')
        status.index = f.index
    fo = pd.to_numeric(f['finish_order'], errors='coerce')
    timed = ~f['time_str'].fillna('').astype(str).str.strip().isin(['', '-', 'None', 'nan'])
    finished = fo.between(1, 28) & timed
    conflict = finished & status.isin(['SCRATCHED', 'EXCLUDED', 'NOT_STARTED'])
    if conflict.any():
        raise ValueError(f'evidence contradicts recorded finish for {int(conflict.sum())} horse(s)')
    allowed = finished & ~status.isin(['SCRATCHED', 'EXCLUDED', 'NOT_STARTED'])
    report = {
        'input_rows': int(len(f)),
        'labelable_finished_rows': int(allowed.sum()),
        'excluded_no_result_or_ambiguous': int((~finished).sum()),
        'verified_started_without_finish_excluded': int(((status == 'STARTED') & ~finished).sum()),
        'verified_nonstarters': int(status.isin(['SCRATCHED', 'EXCLUDED', 'NOT_STARTED']).sum()),
        'verified_rows': int(status.ne('UNKNOWN').sum()),
    }
    return f.loc[allowed].copy(), report
