"""Phase 7: explicitly supplied predictions + verified pre-race odds -> isolated paper ledger.

No scraping, invented probabilities, real bets, or production DB writes.
Capture/settlement are dry-run unless --apply is supplied.
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.paper_trading import capture, utc_time
from src.pre_race_odds_service import validate_pre_race_record
from src.verified_settlement import import_verified_csv
from src.paper_dashboard import load_dashboard

PRED_COLUMNS = {'race_id','horse_number','prediction_at','cutoff_at',
                'win_probability','model_id','source'}
ODDS_COLUMNS = {'race_id','horse_number','odds_value','observed_at',
                'fetched_at','race_start_at','source'}

def read_csv(path, required):
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError('missing CSV columns: ' + ', '.join(sorted(missing)))
        rows = list(reader)
    if not rows:
        raise ValueError('empty CSV')
    return rows

def plan_capture(predictions_csv, odds_csv, *, now_utc=None):
    predictions = read_csv(predictions_csv, PRED_COLUMNS)
    odds_rows = read_csv(odds_csv, ODDS_COLUMNS)
    odds_by_key = {}
    for row in odds_rows:
        race, horse, odds, observed, fetched, source, status = validate_pre_race_record(
            row, now_utc=now_utc, require_start_time=True)
        if status not in ('pre_race_flash','pre_race') or observed is None:
            raise ValueError(f'unverified pre-race odds: {race}/{horse}')
        key = (race, horse)
        if key in odds_by_key:
            raise ValueError(f'duplicate odds key: {key}; supply one snapshot per horse')
        odds_by_key[key] = (odds, observed, fetched, utc_time(row['race_start_at']))
    planned, seen = [], set()
    for row in predictions:
        race = row['race_id'].strip()
        horse = int(row['horse_number'])
        key = (race, horse)
        if key in seen:
            raise ValueError(f'duplicate prediction key: {key}')
        seen.add(key)
        if key not in odds_by_key:
            raise ValueError(f'missing verified odds: {key}')
        odds, observed, fetched, start = odds_by_key[key]
        pred, cutoff = utc_time(row['prediction_at']), utc_time(row['cutoff_at'])
        if not (observed <= fetched <= pred <= cutoff < start):
            raise ValueError(f'timestamp violation: {key}')
        prob = float(row['win_probability'])
        if not math.isfinite(prob) or not 0 <= prob <= 1:
            raise ValueError(f'invalid probability: {key}')
        if not row['model_id'].strip() or not row['source'].strip():
            raise ValueError(f'missing model/source: {key}')
        planned.append(dict(race_id=race, horse_number=horse,
                            prediction_at=pred, cutoff_at=cutoff,
                            win_probability=prob, odds=odds,
                            odds_observed_at=observed, odds_fetched_at=fetched,
                            model_id=row['model_id'].strip(), source=row['source'].strip()))
    return planned

def main(argv=None):
    parser = argparse.ArgumentParser(description='Phase 7 safe paper workflow')
    sub = parser.add_subparsers(dest='command', required=True)
    cap = sub.add_parser('capture')
    cap.add_argument('--predictions', required=True)
    cap.add_argument('--odds', required=True)
    cap.add_argument('--ledger', required=True)
    cap.add_argument('--apply', action='store_true')
    settle = sub.add_parser('settle')
    settle.add_argument('--csv', required=True)
    settle.add_argument('--ledger', required=True)
    settle.add_argument('--apply', action='store_true')
    report = sub.add_parser('report')
    report.add_argument('--ledger', required=True)
    args = parser.parse_args(argv)
    if args.command == 'capture':
        planned = plan_capture(args.predictions, args.odds)
        if args.apply:
            capture(args.ledger, planned)
        result = {'mode': 'apply' if args.apply else 'dry-run',
                  'validated': len(planned), 'applied': len(planned) if args.apply else 0}
    elif args.command == 'settle':
        # The Phase 6 validator opens/migrates the ledger even in dry-run mode.
        # Therefore settlement dry-run is NOT guaranteed read-only on existing DBs.
        result = import_verified_csv(args.ledger, args.csv, apply=args.apply)
    else:
        result = load_dashboard(args.ledger)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result

if __name__ == '__main__':
    main()
