"""Immutable, isolated paper-trading ledger. No live betting or production DB writes."""
import csv
import datetime as dt
import hashlib
import json
import math
import sqlite3
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS predictions (
 race_id TEXT NOT NULL, horse_number INTEGER NOT NULL,
 prediction_at TEXT NOT NULL, cutoff_at TEXT NOT NULL,
 win_probability REAL NOT NULL, odds REAL NOT NULL,
 odds_observed_at TEXT NOT NULL, odds_fetched_at TEXT NOT NULL,
 model_id TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(race_id,horse_number,prediction_at)
);
CREATE TABLE IF NOT EXISTS settlements (
 race_id TEXT NOT NULL, horse_number INTEGER NOT NULL, prediction_at TEXT NOT NULL,
 won INTEGER NOT NULL CHECK(won IN (0,1)), official_payout_yen INTEGER NOT NULL CHECK(official_payout_yen>=0),
 settled_at TEXT NOT NULL, PRIMARY KEY(race_id,horse_number,prediction_at),
 FOREIGN KEY(race_id,horse_number,prediction_at) REFERENCES predictions(race_id,horse_number,prediction_at)
);
CREATE TRIGGER IF NOT EXISTS predictions_immutable_update BEFORE UPDATE ON predictions BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER IF NOT EXISTS predictions_immutable_delete BEFORE DELETE ON predictions BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER IF NOT EXISTS settlements_immutable_update BEFORE UPDATE ON settlements BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER IF NOT EXISTS settlements_immutable_delete BEFORE DELETE ON settlements BEGIN SELECT RAISE(ABORT,'immutable'); END;
'''

def utc_time(value):
    if not isinstance(value, str):
        raise ValueError('ISO-8601 timezone-aware time required')
    x = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if x.tzinfo is None:
        raise ValueError('timezone offset required')
    return x.astimezone(dt.timezone.utc).isoformat()

def connect(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript(SCHEMA)
    return c

def capture(path, records):
    """Atomic capture of pre-race predictions. Does not infer prediction timestamps."""
    rows = []
    for r in records:
        pred, cutoff = utc_time(r['prediction_at']), utc_time(r['cutoff_at'])
        observed, fetched = utc_time(r['odds_observed_at']), utc_time(r['odds_fetched_at'])
        if not (observed <= fetched <= pred <= cutoff):
            raise ValueError('future/late odds or post-cutoff prediction')
        prob, odds = float(r['win_probability']), float(r['odds'])
        number = int(r['horse_number'])
        if not (math.isfinite(prob) and 0 <= prob <= 1 and math.isfinite(odds) and odds >= 1 and number > 0):
            raise ValueError('invalid probability, odds or horse number')
        if not all(str(r[k]).strip() for k in ('race_id','model_id','source')):
            raise ValueError('race/model/source required')
        rows.append((str(r['race_id']),number,pred,cutoff,prob,odds,observed,fetched,str(r['model_id']),str(r['source']),dt.datetime.now(dt.timezone.utc).isoformat()))
    if not rows:
        return 0
    with connect(path) as c:
        c.executemany('INSERT INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?)', rows)
    return len(rows)

def settle(path, records):
    """Settlement must use verified official yen-per-100-yen payouts, not displayed odds."""
    rows = []
    for r in records:
        won = int(r['won'])
        payout = int(r['official_payout_yen'])
        if won not in (0,1) or payout < 0 or (won == 0 and payout != 0) or (won == 1 and payout < 100):
            raise ValueError('inconsistent official payout')
        rows.append((str(r['race_id']),int(r['horse_number']),utc_time(r['prediction_at']),won,payout,utc_time(r['settled_at'])))
    with connect(path) as c:
        c.executemany('INSERT INTO settlements VALUES (?,?,?,?,?,?)', rows)
    return len(rows)

def report(path, stake=100):
    if stake <= 0 or not isinstance(stake,int):
        raise ValueError('stake must be positive integer yen')
    with connect(path) as c:
        total, settled, returns = c.execute('''SELECT COUNT(*), COUNT(s.won), COALESCE(SUM(s.official_payout_yen),0)
          FROM predictions p LEFT JOIN settlements s USING(race_id,horse_number,prediction_at)''').fetchone()
    invested = settled * stake
    paid = returns * stake / 100
    return {'predictions':total,'settled':settled,'unsettled':total-settled,'stake_yen':invested,
            'returns_yen':paid,'profit_yen':paid-invested,'roi':(paid/invested if invested else None)}

def read_csv(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))
