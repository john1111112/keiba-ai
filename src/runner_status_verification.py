"""Phase 4D: review workflow. No status is inferred or fetched automatically."""
from __future__ import annotations
from pathlib import Path
import pandas as pd
from src.runner_status_audit import ALLOWED_VERIFIED, apply_verified_status

KEY = ['race_id', 'horse_number']
REVIEW_COLS = KEY + ['horse_name', 'date', 'start_status', 'verified_source', 'source_reference', 'verified_at', 'reviewer', 'review_note']


def review_template(audit: pd.DataFrame) -> pd.DataFrame:
    """Make a blank evidence sheet; never auto-populate a claimed official status."""
    rows = audit.loc[audit.evidence_status.eq('UNRESOLVED_NO_TIME'), ['race_id', 'horse_number', 'horse_name', 'date']].copy()
    for col in REVIEW_COLS[4:]:
        rows[col] = ''
    return rows[REVIEW_COLS].sort_values(KEY).reset_index(drop=True)


def validate_review(review: pd.DataFrame, audit: pd.DataFrame) -> pd.DataFrame:
    """Return fully supported verified rows. Reject conflicts and incomplete claims."""
    missing = set(REVIEW_COLS) - set(review.columns)
    if missing:
        raise ValueError(f'Missing review columns: {sorted(missing)}')
    data = review[REVIEW_COLS].copy()
    for col in ['race_id', 'horse_name', 'date', 'start_status', 'verified_source', 'source_reference', 'verified_at', 'reviewer', 'review_note']:
        data[col] = data[col].fillna('').astype(str).str.strip()
    data['horse_number'] = pd.to_numeric(data['horse_number'], errors='raise')
    if data['horse_number'].isna().any() or (data['horse_number'] % 1 != 0).any():
        raise ValueError('Invalid horse number')
    data['horse_number'] = data['horse_number'].astype(int)
    if data.duplicated(KEY).any():
        raise ValueError('Duplicate review keys')
    reference = audit[KEY + ['horse_name', 'date', 'evidence_status']].copy()
    reference['race_id'] = reference['race_id'].astype(str)
    reference['horse_number'] = pd.to_numeric(reference['horse_number']).astype(int)
    if reference.duplicated(KEY).any():
        raise ValueError('Duplicate audit keys')
    merged = data.merge(reference, on=KEY, how='left', validate='one_to_one', suffixes=('', '_db'), indicator=True)
    if (merged['_merge'] != 'both').any():
        raise ValueError('Review contains horses absent from the database')
    if (merged['evidence_status'] != 'UNRESOLVED_NO_TIME').any():
        raise ValueError('Review contains horses outside the unresolved cohort')
    if (merged['horse_name'] != merged['horse_name_db']).any() or (merged['date'] != merged['date_db'].astype(str)).any():
        raise ValueError('Review horse identity/date does not match database')
    # Blank rows are unreviewed, never interpreted as verified.
    filled = merged['start_status'].ne('')
    if (merged.loc[~filled, ['verified_source','source_reference','verified_at','reviewer']].ne('').any(axis=1)).any():
        raise ValueError('Partially completed review row')
    valid = merged.loc[filled].copy()
    if not valid['start_status'].isin(ALLOWED_VERIFIED).all():
        raise ValueError('Unrecognized start status')
    required = ['verified_source', 'source_reference', 'verified_at', 'reviewer']
    if valid[required].eq('').any().any():
        raise ValueError('Verified rows require source, source reference, time and reviewer')
    parsed = pd.to_datetime(valid['verified_at'], errors='coerce', utc=True)
    if parsed.isna().any():
        raise ValueError('Invalid verified_at timestamp')
    return valid[KEY + ['start_status','verified_source','source_reference','verified_at','reviewer','review_note']].reset_index(drop=True)


def reconcile_review(audit: pd.DataFrame, review: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    verified = validate_review(review, audit)
    enriched = apply_verified_status(audit, verified[KEY + ['start_status','verified_source','verified_at']]) if len(verified) else audit.copy()
    # Ambiguous outcomes stay out of training even after an official STARTED verification.
    if enriched.loc[enriched.evidence_status.eq('UNRESOLVED_NO_TIME'), 'training_eligible'].any():
        raise AssertionError('Unresolved result accidentally entered training set')
    return enriched, verified


def export_review_template(audit: pd.DataFrame, path: str | Path) -> None:
    review_template(audit).to_csv(path, index=False, encoding='utf-8-sig')
