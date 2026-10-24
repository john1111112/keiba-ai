"""Immutable evidence for feature snapshots, not proof of upstream data availability."""
import datetime as dt
import hashlib
import json
from pathlib import Path


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot_features(source_csv, destination_csv, race_id, *, now=None):
    """Copy supplied features to a fresh snapshot; never overwrite evidence."""
    import pandas as pd
    src, dst = Path(source_csv), Path(destination_csv)
    if not src.is_file() or src.resolve() == dst.resolve():
        raise ValueError('distinct existing source and new destination required')
    if dst.exists() or Path(str(dst)+'.manifest.json').exists():
        raise FileExistsError('snapshot already exists')
    frame = pd.read_csv(src)
    if frame.empty or not {'race_id','horse_number'}.issubset(frame.columns):
        raise ValueError('race_id and horse_number required')
    if not (frame['race_id'].astype(str) == str(race_id)).all():
        raise ValueError('mixed race IDs')
    if frame['horse_number'].isna().any() or frame['horse_number'].duplicated().any():
        raise ValueError('duplicate/missing horse numbers')
    moment = now or dt.datetime.now(dt.timezone.utc)
    if moment.tzinfo is None:
        raise ValueError('timezone-aware timestamp required')
    dst.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive create prevents silently replacing previously captured data.
    with dst.open('xb') as f:
        f.write(src.read_bytes())
    manifest = {'schema_version':1,'race_id':str(race_id),'created_at':moment.astimezone(dt.timezone.utc).isoformat(),
                'sha256':sha256(dst),'horse_count':len(frame),
                'warning':'Snapshot timestamp does not certify source feature availability or absence of leakage.'}
    with Path(str(dst)+'.manifest.json').open('x',encoding='utf-8') as f:
        json.dump(manifest,f,ensure_ascii=False,indent=2)
    return manifest


def verify_snapshot(csv_path, race_id, prediction_at):
    from .paper_trading import utc_time
    p=Path(csv_path)
    meta=json.loads(Path(str(p)+'.manifest.json').read_text(encoding='utf-8'))
    if meta.get('schema_version') != 1 or meta.get('race_id') != str(race_id):
        raise ValueError('snapshot metadata mismatch')
    if meta.get('sha256') != sha256(p):
        raise ValueError('feature snapshot modified after capture')
    if utc_time(meta['created_at']) > utc_time(prediction_at):
        raise ValueError('snapshot created after prediction time')
    return meta
