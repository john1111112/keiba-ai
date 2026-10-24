"""Strict, auditable pre-race win-odds CSV import (no network scraping)."""
import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.odds_history import init_odds_history_db  # noqa: E402

REQUIRED = ('race_id', 'horse_number', 'odds_value', 'observed_at', 'fetched_at', 'race_start_at', 'source')


def timestamp(raw):
    if not raw or not isinstance(raw, str):
        raise ValueError('timestamp is required')
    value = dt.datetime.fromisoformat(raw.strip().replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('timezone offset required')
    return value.astimezone(dt.timezone.utc)


def prepare(csv_path, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    with open(csv_path, newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or any(k not in reader.fieldnames for k in REQUIRED):
            raise ValueError('missing required columns: ' + ', '.join(REQUIRED))
        rows = list(reader)
    if not rows:
        raise ValueError('empty CSV')
    output, seen = [], set()
    for line, r in enumerate(rows, 2):
        try:
            race = r['race_id'].strip()
            source = r['source'].strip()
            horse = int(r['horse_number'])
            odds = float(r['odds_value'])
            observed, fetched, start = (timestamp(r[k]) for k in ('observed_at', 'fetched_at', 'race_start_at'))
            if not race or not source or horse <= 0 or not math.isfinite(odds) or odds < 1:
                raise ValueError('invalid race, source, horse number or odds')
            if not observed <= fetched < start:
                raise ValueError('odds are not provably available before race start')
            if fetched > now:
                raise ValueError('fetched_at is in the future')
            key = (race, horse, observed.isoformat(), fetched.isoformat(), source)
            if key in seen:
                raise ValueError('duplicate CSV row')
            seen.add(key)
            output.append((race, '', horse, 'win', odds, None, observed.isoformat(), fetched.isoformat(), source, 'pre_race_flash'))
        except (ValueError, TypeError, OverflowError, KeyError) as exc:
            raise ValueError(f'CSV line {line}: {exc}') from exc
    return output


def import_csv(csv_path, db_path, apply=False, now=None):
    rows = prepare(csv_path, now=now)
    result = {'validated': len(rows), 'inserted': 0, 'already_present': 0, 'dry_run': not apply}
    if not apply:
        return result
    init_odds_history_db(Path(db_path))
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute('BEGIN IMMEDIATE')
        for row in rows:
            exists = conn.execute('''SELECT 1 FROM odds_timeline WHERE race_id=? AND horse_number=?
                AND odds_type=? AND odds_value=? AND observed_at=? AND fetched_at=? AND source=?
                AND data_status=? LIMIT 1''', (row[0], row[2], row[3], row[4], row[6], row[7], row[8], row[9])).fetchone()
            if exists:
                result['already_present'] += 1
                continue
            conn.execute('''INSERT INTO odds_timeline (race_id,horse_id,horse_number,odds_type,odds_value,
                popularity,observed_at,fetched_at,source,data_status) VALUES (?,?,?,?,?,?,?,?,?,?)''', row)
            result['inserted'] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return result


def main():
    parser = argparse.ArgumentParser(description='Validate/import timestamped pre-race win odds')
    parser.add_argument('--csv', required=True)
    parser.add_argument('--db', default='data/odds_history.db')
    parser.add_argument('--apply', action='store_true', help='Write to DB; default is dry-run')
    args = parser.parse_args()
    try:
        print(json.dumps(import_csv(args.csv, args.db, apply=args.apply), ensure_ascii=False))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
