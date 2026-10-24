"""Create a review template, or validate a filled review without modifying the DB."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from src.runner_status_audit import audit_rows
from src.runner_status_verification import export_review_template, reconcile_review


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, default=root/'data'/'keiba.db')
    parser.add_argument('--review', type=Path, help='Completed review CSV; omitted = create blank template')
    parser.add_argument('--out', type=Path, default=root/'phase4d_output')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    audit = audit_rows(args.db)
    if args.review is None:
        target = args.out/'review_template.csv'
        export_review_template(audit, target)
        print(f'Created {target} ({(audit.evidence_status == "UNRESOLVED_NO_TIME").sum()} unresolved rows)')
        return
    review = pd.read_csv(args.review, dtype={'race_id': str, 'horse_number': str}, keep_default_na=False)
    enriched, verified = reconcile_review(audit, review)
    verified.to_csv(args.out/'validated_evidence.csv', index=False, encoding='utf-8-sig')
    enriched.loc[enriched.evidence_status.eq('UNRESOLVED_NO_TIME')].to_csv(args.out/'unresolved_with_review.csv', index=False, encoding='utf-8-sig')
    print(f'Validated: {len(verified)}; unresolved without verified status: {(enriched.verified_start_status == "UNKNOWN").sum()} (includes other cohorts)')
    print('No training data or production DB was modified.')

if __name__ == '__main__':
    main()
