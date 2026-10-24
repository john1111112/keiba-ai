"""Fail-closed bridge: independently supplied pre-race features + as-of odds -> paper ledger.

No scraping, no feature reconstruction from post-race results, no live wagers.
"""
import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .odds_history import get_latest_odds_as_of
from .paper_trading import capture, utc_time


def prepare_predictions(features, race_id, prediction_at, cutoff_at, odds_db, model, model_id, source='recorded_pre_race', *, generated_at=None):
    """Return paper-trading records. Caller must prove features existed at prediction_at.

    generated_at is an externally supplied generation timestamp; never backdate.
    """
    if generated_at is None or utc_time(generated_at) > utc_time(prediction_at):
        raise ValueError('generation time required and must be <= prediction_at; no backdating')
    if utc_time(prediction_at) > utc_time(cutoff_at):
        raise ValueError('prediction after cutoff')
    if features.empty or 'horse_number' not in features:
        raise ValueError('pre-race features with horse_number required')
    if 'race_id' in features and not (features['race_id'].astype(str) == str(race_id)).all():
        raise ValueError('mixed race IDs')
    if features['horse_number'].isna().any() or features['horse_number'].duplicated().any():
        raise ValueError('duplicate/missing horse number')
    cols = list(model.feature_cols)
    if not cols or any(c not in features.columns for c in cols):
        raise ValueError('missing model features; zero-filling prohibited')
    X = features[cols].apply(pd.to_numeric, errors='coerce')
    if not np.isfinite(X.to_numpy(dtype=float)).all():
        raise ValueError('invalid model features')
    quotes = get_latest_odds_as_of(str(race_id), prediction_at, db_path=odds_db)
    if quotes.empty or quotes['horse_number'].duplicated().any():
        raise ValueError('missing or duplicate as-of odds')
    joined = features[['horse_number']].merge(quotes, on='horse_number', how='left', validate='one_to_one', sort=False)
    if len(joined) != len(features) or joined[['odds_value','observed_at','fetched_at']].isna().any().any():
        raise ValueError('incomplete pre-race odds coverage')
    probs = np.asarray(model.model.predict_proba(X)[:, 1], dtype=float)
    if len(probs) != len(features) or not np.isfinite(probs).all() or (probs < 0).any() or (probs > 1).any():
        raise ValueError('invalid model output')
    if probs.sum() <= 0:
        raise ValueError('zero total probability')
    probs = probs / probs.sum()  # clearly labelled heuristic, not calibrated probabilities
    records = []
    for i, q in joined.iterrows():
        if not (math.isfinite(float(q.odds_value)) and float(q.odds_value) >= 1):
            raise ValueError('invalid odds')
        records.append(dict(race_id=str(race_id),horse_number=int(q.horse_number),prediction_at=prediction_at,
            cutoff_at=cutoff_at,win_probability=float(probs[i]),odds=float(q.odds_value),
            odds_observed_at=q.observed_at,odds_fetched_at=q.fetched_at,
            model_id=model_id,source=source))
    return records


def capture_from_csv(feature_csv, odds_db, ledger_db, race_id, prediction_at, cutoff_at, generated_at, model_path):
    from .model import KeibaAIWinModel
    path = Path(model_path)
    model = KeibaAIWinModel(model_path=path)
    model.load()  # built-in SHA-256 verification before deserialization
    from .feature_provenance import verify_snapshot
    manifest = verify_snapshot(feature_csv, race_id, prediction_at)
    frame = pd.read_csv(feature_csv)
    if len(frame) != manifest['horse_count']:
        raise ValueError('snapshot horse count mismatch')
    if utc_time(generated_at) != utc_time(manifest['created_at']):
        raise ValueError('generated_at must match snapshot manifest')
    model_id = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()
    records = prepare_predictions(frame,race_id,prediction_at,cutoff_at,odds_db,model,model_id,generated_at=generated_at)
    return capture(ledger_db, records)
