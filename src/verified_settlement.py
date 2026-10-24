"""Verified win-ticket settlement importer. No scraping or inferred results."""
import csv
import datetime as dt
import sqlite3
from pathlib import Path
from .paper_trading import utc_time, connect, settle

REQUIRED = {'race_id','horse_number','prediction_at','won','official_payout_yen','settled_at','source_url','verified_by'}

def load_verified_csv(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
            raise ValueError('missing required CSV columns: ' + ','.join(sorted(REQUIRED - set(reader.fieldnames or []))))
        return list(reader)

def validate_and_plan(ledger_path, csv_path):
    """Return validated settlement records, without changing ledger."""
    rows = load_verified_csv(csv_path)
    if not rows:
        raise ValueError('empty settlement file')
    planned, keys = [], set()
    with connect(ledger_path) as c:
        for index, r in enumerate(rows, start=2):
            try:
                if not r['source_url'].strip().startswith(('https://','http://')) or not r['verified_by'].strip():
                    raise ValueError('source_url and verified_by are required')
                race = r['race_id'].strip()
                number = int(r['horse_number'])
                pred_at = utc_time(r['prediction_at'])
                settled_at = utc_time(r['settled_at'])
                won = int(r['won'])
                payout = int(r['official_payout_yen'])
                if number <= 0 or won not in (0,1) or payout < 0 or (won == 0 and payout != 0) or (won == 1 and payout < 100):
                    raise ValueError('invalid outcome or payout')
                if dt.datetime.fromisoformat(settled_at) <= dt.datetime.fromisoformat(pred_at):
                    raise ValueError('settlement precedes prediction')
                key = (race,number,pred_at)
                if key in keys:
                    raise ValueError('duplicate settlement key in CSV')
                keys.add(key)
                row = c.execute('SELECT 1 FROM predictions WHERE race_id=? AND horse_number=? AND prediction_at=?',key).fetchone()
                if row is None:
                    raise ValueError('no matching prediction')
                if c.execute('SELECT 1 FROM settlements WHERE race_id=? AND horse_number=? AND prediction_at=?',key).fetchone():
                    raise ValueError('already settled')
                planned.append(dict(
                    race_id=race, horse_number=number, prediction_at=pred_at,
                    won=won, official_payout_yen=payout, settled_at=settled_at,
                    source_url=r['source_url'].strip(),
                    verified_by=r['verified_by'].strip(),
                    verification_status='verified'
                ))
            except (ValueError,TypeError,KeyError) as exc:
                raise ValueError(f'CSV line {index}: {exc}') from exc
    return planned

def import_verified_csv(ledger_path,csv_path,apply=False):
    """Validate whole batch first; apply only when explicitly requested."""
    planned = validate_and_plan(ledger_path,csv_path)
    if apply:
        settle(ledger_path,planned)  # one SQLite transaction; immutable once inserted
    return {'validated':len(planned),'applied':len(planned) if apply else 0,'mode':'apply' if apply else 'dry-run'}
