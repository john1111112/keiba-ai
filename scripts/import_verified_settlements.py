"""Run from project root: python -m scripts.import_verified_settlements --help"""
import argparse
import json
from src.verified_settlement import import_verified_csv

def main():
    p=argparse.ArgumentParser(description='Validate verified official win payouts; dry-run by default')
    p.add_argument('--ledger',default='data/paper_trading.db')
    p.add_argument('--csv',required=True)
    p.add_argument('--apply',action='store_true',help='Commit settlements after validating all rows')
    args=p.parse_args()
    print(json.dumps(import_verified_csv(args.ledger,args.csv,apply=args.apply),ensure_ascii=False))
if __name__=='__main__': main()
