"""Phase 10: local, append-only pre-race snapshot receipt.

Captures raw bytes and hashes with a local UTC clock. No scraping or bets.
A receipt proves local observation only, not authenticity of the upstream source.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

def _utc(value):
    if value.tzinfo is None:
        raise ValueError('timezone-aware datetime required')
    return value.astimezone(dt.timezone.utc)

def capture_snapshot(source_path, archive_dir, *, race_id, race_start_at,
                     source_name, observed_at=None):
    from src.paper_trading import utc_time
    start = dt.datetime.fromisoformat(utc_time(race_start_at))
    observed = _utc(observed_at or dt.datetime.now(dt.timezone.utc))
    if observed >= start:
        raise ValueError('race has started: refuse snapshot')
    if observed > dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60):
        raise ValueError('future observation rejected')
    if not race_id or not race_id.isalnum() or not source_name.strip():
        raise ValueError('race_id and source_name required')
    source = Path(source_path)
    if not source.is_file() or source.is_symlink():
        raise ValueError('regular source file required')
    archive = Path(archive_dir)
    if not archive.is_dir() or archive.is_symlink():
        raise ValueError('existing non-symlink archive directory required')
    data = source.read_bytes()
    if not data:
        raise ValueError('empty snapshot rejected')
    digest = hashlib.sha256(data).hexdigest()
    timestamp = observed.strftime('%Y%m%dT%H%M%S%fZ')
    stem = race_id + '_' + timestamp + '_' + digest[:12]
    payload_path = archive / (stem + '.snapshot')
    receipt_path = archive / (stem + '.receipt.json')
    if payload_path.exists() or receipt_path.exists():
        raise FileExistsError('snapshot receipt already exists')
    receipt = dict(race_id=race_id, source_name=source_name,
        race_start_at=start.isoformat(), observed_at=observed.isoformat(),
        sha256=digest, bytes=len(data), snapshot_file=payload_path.name,
        provenance_level='local_file_observation_only')
    # Exclusive creation; never overwrite a previous snapshot.
    fd = os.open(payload_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with receipt_path.open('x', encoding='utf-8') as f:
            json.dump(receipt, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        payload_path.unlink(missing_ok=True)
        raise
    return receipt

def verify_snapshot(receipt_path):
    path = Path(receipt_path)
    receipt = json.loads(path.read_text(encoding='utf-8'))
    name = receipt.get('snapshot_file', '')
    if not name or Path(name).name != name:
        raise ValueError('unsafe snapshot filename')
    payload = path.parent / name
    if not payload.is_file() or payload.is_symlink():
        raise ValueError('snapshot missing or not regular file')
    raw = payload.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != receipt.get('sha256') or len(raw) != receipt.get('bytes'):
        raise ValueError('snapshot hash or size mismatch')
    if utc_time_local(receipt['observed_at']) >= utc_time_local(receipt['race_start_at']):
        raise ValueError('post-start snapshot')
    return {'status':'hash-matched','sha256':digest,'bytes':len(raw),
            'provenance_level':'local_file_observation_only'}

def utc_time_local(value):
    from src.paper_trading import utc_time
    return dt.datetime.fromisoformat(utc_time(value))

def main(argv=None):
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest='command',required=True)
    cap=sub.add_parser('capture')
    for flag in ('source-path','archive-dir','race-id','race-start-at','source-name'):
        cap.add_argument('--'+flag,required=True)
    ver=sub.add_parser('verify')
    ver.add_argument('--receipt',required=True)
    a=p.parse_args(argv)
    if a.command=='capture':
        result=capture_snapshot(a.source_path,a.archive_dir,race_id=a.race_id,
            race_start_at=a.race_start_at,source_name=a.source_name)
    else:
        result=verify_snapshot(a.receipt)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
