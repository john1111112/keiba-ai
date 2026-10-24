"""Read-only operational status for paper-trading; no production writes."""
import argparse
import json
from pathlib import Path
from src.paper_dashboard import load_dashboard


def main():
    p = argparse.ArgumentParser(description='Show paper-trading status (read-only)')
    p.add_argument('--ledger', default='data/paper_trading.db')
    args = p.parse_args()
    ledger = Path(args.ledger)
    if not ledger.is_file():
        print(json.dumps({'status': 'not_started', 'ledger': str(ledger), 'message': 'No paper trading database yet.'}, ensure_ascii=False))
        return
    result = load_dashboard(ledger)
    print(json.dumps({'status': 'ready', 'ledger': str(ledger), **result}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
