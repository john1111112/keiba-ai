"""Phase 9: strict pre-race input gate; read-only and fail-closed.

This does not authenticate upstream provenance or fetch official odds.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path

from src.paper_trading import utc_time
from src.pre_race_odds_service import validate_pre_race_record

def _rows(path, columns):
    with open(path, newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        missing = set(columns) - set(reader.fieldnames or [])
        if missing:
            raise ValueError('missing columns: ' + ','.join(sorted(missing)))
        data = list(reader)
    if not data:
        raise ValueError('empty CSV')
    return data

def audit(predictions_csv, odds_csv, manifest_path, *, now=None):
    """Verify explicit pre-race provenance and exact one-to-one card coverage."""
    from scripts.phase7_workflow import plan_capture
    manifest_file = Path(manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    for field in ('race_start_at','feature_cutoff_at','prediction_at',
                  'source','feature_sha256','model_sha256','race_id'):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError('manifest field required: '+field)
    for field in ('feature_sha256','model_sha256'):
        digest = manifest[field].lower()
        if len(digest) != 64 or any(ch not in '0123456789abcdef' for ch in digest):
            raise ValueError('invalid sha256: '+field)
    start = utc_time(manifest['race_start_at'])
    feature_time = utc_time(manifest['feature_cutoff_at'])
    prediction_time = utc_time(manifest['prediction_at'])
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    current_iso = current.astimezone(dt.timezone.utc).isoformat()
    if not (feature_time <= prediction_time < start):
        raise ValueError('feature/prediction must precede race start')
    if prediction_time > current_iso:
        raise ValueError('prediction timestamp is in the future')
    predictions = _rows(predictions_csv, ('race_id','horse_number','prediction_at',
        'cutoff_at','win_probability','model_id','source'))
    odds = _rows(odds_csv, ('race_id','horse_number','odds_value','observed_at',
        'fetched_at','race_start_at','source'))
    if len(predictions) != len(odds):
        raise ValueError('prediction and odds card sizes differ')
    race = manifest['race_id']
    for row in predictions:
        if row['race_id'] != race or utc_time(row['prediction_at']) != prediction_time:
            raise ValueError('prediction race/timestamp differs from manifest')
        if row['source'] != manifest['source']:
            raise ValueError('prediction source differs from manifest')
        if utc_time(row['cutoff_at']) >= start:
            raise ValueError('cutoff not before race start')
    for row in odds:
        if row['race_id'] != race or utc_time(row['race_start_at']) != start:
            raise ValueError('odds race/start differs from manifest')
        validate_pre_race_record(row, now_utc=current, require_start_time=True)
    planned = plan_capture(predictions_csv, odds_csv, now_utc=current)
    if len(planned) != len(predictions):
        raise ValueError('card coverage mismatch')
    # This is an audit of declared metadata, not independent verification.
    return {'status':'metadata-consistent', 'race_id':race, 'horses':len(planned),
            'feature_sha256':manifest['feature_sha256'],
            'model_sha256':manifest['model_sha256'],
            'warning':'Source authenticity and feature as-of correctness are NOT independently proven.'}

def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--predictions', required=True)
    p.add_argument('--odds', required=True)
    p.add_argument('--manifest', required=True)
    args = p.parse_args(argv)
    print(json.dumps(audit(args.predictions,args.odds,args.manifest),
                     ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
