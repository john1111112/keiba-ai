"""Phase 11: verify Phase 9 manifest hashes against Phase 10 archived bytes.

Local-file integrity only. No licensed provider connection or source authenticity.
"""
import argparse
import datetime as dt
import json
from pathlib import Path

from scripts.phase10_snapshot_attestation import verify_snapshot
from scripts.phase9_pre_race_gate import audit
from src.paper_trading import utc_time

def audit_receipts(predictions_csv, odds_csv, manifest_path,
                   feature_receipt_path, odds_receipt_path, *, now=None):
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    feature_receipt = json.loads(Path(feature_receipt_path).read_text(encoding='utf-8'))
    odds_receipt = json.loads(Path(odds_receipt_path).read_text(encoding='utf-8'))
    feature_check = verify_snapshot(feature_receipt_path)
    odds_check = verify_snapshot(odds_receipt_path)
    if feature_check['sha256'] != manifest.get('feature_sha256'):
        raise ValueError('feature manifest hash does not match archived bytes')
    if odds_check['sha256'] != manifest.get('odds_sha256'):
        raise ValueError('odds manifest hash does not match archived bytes')
    for label, receipt in [('feature', feature_receipt), ('odds', odds_receipt)]:
        if receipt.get('race_id') != manifest.get('race_id'):
            raise ValueError(label + ' receipt race mismatch')
        if utc_time(receipt['race_start_at']) != utc_time(manifest['race_start_at']):
            raise ValueError(label + ' receipt start mismatch')
        if utc_time(receipt['observed_at']) >= utc_time(manifest['race_start_at']):
            raise ValueError(label + ' receipt is post-start')
    if utc_time(feature_receipt['observed_at']) > utc_time(manifest['feature_cutoff_at']):
        raise ValueError('feature observed after declared feature cutoff')
    if utc_time(odds_receipt['observed_at']) > utc_time(manifest['prediction_at']):
        raise ValueError('odds observed after prediction')
    # Verify that the supplied odds CSV is exactly the archived payload.
    archived_odds = Path(odds_receipt_path).parent / odds_receipt['snapshot_file']
    if archived_odds.read_bytes() != Path(odds_csv).read_bytes():
        raise ValueError('odds CSV differs from archived snapshot')
    result = audit(predictions_csv, odds_csv, manifest_path, now=now)
    result['status'] = 'local-receipts-consistent'
    result['odds_sha256'] = odds_check['sha256']
    result['warning'] = ('Local integrity only; upstream origin, actual '
                         'publication times, and feature as-of correctness are unverified.')
    return result

def main(argv=None):
    p = argparse.ArgumentParser()
    for flag in ('predictions','odds','manifest','feature-receipt','odds-receipt'):
        p.add_argument('--'+flag, required=True)
    args = p.parse_args(argv)
    print(json.dumps(audit_receipts(args.predictions,args.odds,args.manifest,
        args.feature_receipt,args.odds_receipt),ensure_ascii=False,indent=2))

if __name__ == '__main__':
    main()
