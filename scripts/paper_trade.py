"""Run: python scripts/paper_trade.py capture|settle|report [CSV] [--db PATH]"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.paper_trading import capture, settle, report, read_csv

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('command',choices=['capture','settle','report'])
    p.add_argument('csv',nargs='?')
    p.add_argument('--db',default=str(Path(__file__).resolve().parents[1]/'data'/'paper_trading.db'))
    args = p.parse_args()
    if args.command == 'report':
        print(json.dumps(report(args.db),ensure_ascii=False,indent=2))
    else:
        if not args.csv:
            p.error('CSV path required for capture/settle')
        print((capture if args.command == 'capture' else settle)(args.db,read_csv(args.csv)))
