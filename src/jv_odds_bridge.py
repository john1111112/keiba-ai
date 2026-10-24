"""JV-Linkの公式サンプル等から出力したCSVを安全に取り込む境界。
JV-Linkそのものの呼出し・JV-Dataのバイナリ解析は行わない。
"""
import argparse
import csv
import datetime as dt
import math
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')
FIELDS = ('race_id', 'horse_number', 'odds_value', 'observed_at', 'fetched_at', 'start_at', 'source')


def parse_time(s):
    value = dt.datetime.fromisoformat(s)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('時刻には +09:00 等のタイムゾーン指定が必要')
    return value.astimezone(dt.timezone.utc)


def validate(row, now=None, allow_sample=False):
    missing = [k for k in FIELDS if not str(row.get(k, '')).strip()]
    if missing:
        raise ValueError('必須項目なし: ' + ', '.join(missing))
    source = row['source'].strip()
    if source == 'sample' and not allow_sample:
        raise ValueError('sample データは明示的な --allow-sample が必要')
    if source not in ('jra_van', 'sample'):
        raise ValueError('未許可のsource: ' + source)
    race_id = row['race_id'].strip()
    if not race_id.isascii() or not race_id.replace('_', '').isalnum():
        raise ValueError('race_id の形式が不正')
    number = int(row['horse_number'])
    if not 1 <= number <= 28:
        raise ValueError('馬番が不正')
    odds = float(row['odds_value'])
    if not math.isfinite(odds) or odds < 1.0:
        raise ValueError('単勝オッズが不正')
    observed, fetched, start = (parse_time(row[k]) for k in ('observed_at', 'fetched_at', 'start_at'))
    now = now or dt.datetime.now(dt.timezone.utc)
    if observed > fetched or fetched > now or observed >= start or fetched >= start:
        raise ValueError('発表・取得・発走時刻の順序が不正')
    return (race_id, number, odds, observed.isoformat(), fetched.isoformat(), source)


def import_csv(csv_path, db_path, allow_sample=False, now=None):
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not set(FIELDS).issubset(reader.fieldnames):
            raise ValueError('CSV列が不足')
        rows = [validate(r, now=now, allow_sample=allow_sample) for r in reader]
    if not rows:
        raise ValueError('空CSVは取り込まない')
    if len(rows) != len(set(rows)):
        raise ValueError('CSV内に重複あり')
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute('''CREATE TABLE IF NOT EXISTS odds_timeline (
          id INTEGER PRIMARY KEY AUTOINCREMENT, race_id TEXT NOT NULL,
          horse_id TEXT, horse_number INTEGER NOT NULL, odds_type TEXT NOT NULL,
          odds_value REAL NOT NULL, popularity INTEGER, observed_at TEXT NOT NULL,
          fetched_at TEXT NOT NULL, source TEXT NOT NULL, data_status TEXT NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        inserted = 0
        for race, horse, odds, observed, fetched, source in rows:
            # DBの既存構造を維持し、同一レコードの再取り込みを回避。
            cur = con.execute('''INSERT INTO odds_timeline
              (race_id, horse_number, odds_type, odds_value, observed_at, fetched_at, source, data_status)
              SELECT ?, ?, 'win', ?, ?, ?, ?, ? WHERE NOT EXISTS (
                SELECT 1 FROM odds_timeline WHERE race_id=? AND horse_number=?
                AND odds_type='win' AND observed_at=? AND source=?)''',
              (race, horse, odds, observed, fetched, source,
               'sample_only' if source == 'sample' else 'pre_race_flash',
               race, horse, observed, source))
            inserted += cur.rowcount
        con.commit()
    finally:
        con.close()
    return inserted


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='発走前単勝オッズ CSV → 既存SQLite履歴DB')
    parser.add_argument('csv_path', type=Path)
    parser.add_argument('--db', type=Path, default=Path('data/odds_history.db'))
    parser.add_argument('--allow-sample', action='store_true')
    args = parser.parse_args()
    print('登録:', import_csv(args.csv_path, args.db, allow_sample=args.allow_sample), '件')
