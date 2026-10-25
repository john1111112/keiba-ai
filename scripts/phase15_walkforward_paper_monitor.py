"""Phase 15: Walk-Forward Model Evaluation, Paper Decision Monitoring, and Hash Chain Audit.

Implements multi-fold rolling walk-forward splitting, per-fold Platt calibration,
cryptographic hash chaining for tamper-evident paper trade logs, and fail-closed
audit evaluation. Zero cost, synthetic benchmark only, no real bets.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import random
import sys

# Ensure repository root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.phase14_calibration_paper_audit import (
    PlattCalibrator,
    calibrate_race_probabilities,
    compute_log_loss,
    compute_brier_score,
    compute_ece,
    audit_probability_integrity,
    audit_paper_decisions
)
from scripts.phase13_offline_evaluation import generate_synthetic_benchmark, audit_feature_leakage

def generate_walk_forward_folds(race_records, n_folds=3, min_train_races=10, calib_races=4, test_races=4):
    """Generate chronological rolling walk-forward folds without data leakage."""
    if not race_records:
        return []

    races = {}
    for row in race_records:
        r_id = row['race_id']
        r_time = row['race_start_at']
        if r_id not in races:
            races[r_id] = {'race_start_at': r_time, 'rows': []}
        races[r_id]['rows'].append(row)

    sorted_race_keys = sorted(races.keys(), key=lambda r: (races[r]['race_start_at'], r))
    total_races = len(sorted_race_keys)
    min_required = min_train_races + calib_races + test_races

    if total_races < min_required:
        raise ValueError(
            f"Insufficient races for walk-forward: need at least {min_required}, got {total_races}"
        )

    folds = []
    # Expanding window walk-forward
    available_shifts = total_races - min_required
    step_size = max(1, available_shifts // (n_folds - 1)) if n_folds > 1 else 1

    for f_idx in range(n_folds):
        start_offset = f_idx * step_size
        train_end = min_train_races + start_offset
        calib_end = train_end + calib_races
        test_end = calib_end + test_races

        if test_end > total_races:
            break

        train_keys = set(sorted_race_keys[:train_end])
        calib_keys = set(sorted_race_keys[train_end:calib_end])
        test_keys = set(sorted_race_keys[calib_end:test_end])

        # Temporal integrity validation
        t_max = max(races[r]['race_start_at'] for r in train_keys)
        c_min = min(races[r]['race_start_at'] for r in calib_keys)
        c_max = max(races[r]['race_start_at'] for r in calib_keys)
        test_min = min(races[r]['race_start_at'] for r in test_keys)

        if t_max > c_min:
            raise ValueError(f"Fold {f_idx} temporal leak: Train max > Calibration min")
        if c_max > test_min:
            raise ValueError(f"Fold {f_idx} temporal leak: Calibration max > Test min")

        # Overlap validation
        if train_keys.intersection(calib_keys) or calib_keys.intersection(test_keys) or train_keys.intersection(test_keys):
            raise ValueError(f"Fold {f_idx} race ID overlap detected across partitions")

        train_rows = [row for r in sorted_race_keys[:train_end] for row in races[r]['rows']]
        calib_rows = [row for r in sorted_race_keys[train_end:calib_end] for row in races[r]['rows']]
        test_rows = [row for r in sorted_race_keys[calib_end:test_end] for row in races[r]['rows']]

        folds.append({
            'fold_index': f_idx + 1,
            'train_races': len(train_keys),
            'calib_races': len(calib_keys),
            'test_races': len(test_keys),
            'train_rows': train_rows,
            'calib_rows': calib_rows,
            'test_rows': test_rows
        })

    return folds

def create_hash_chained_audit_log(audit_records):
    """Build a tamper-evident SHA-256 hash chain for sequential paper decision records."""
    chained_records = []
    prev_hash = "0" * 64

    for seq, rec in enumerate(audit_records, start=1):
        rec_copy = dict(rec)
        rec_copy['sequence_id'] = seq
        rec_copy['previous_hash'] = prev_hash

        # Canonical deterministic JSON string for hashing
        payload_str = json.dumps(rec_copy, sort_keys=True, ensure_ascii=False)
        rec_hash = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()
        rec_copy['record_hash'] = rec_hash

        chained_records.append(rec_copy)
        prev_hash = rec_hash

    return chained_records

def verify_audit_log_hash_chain(chained_records):
    """Verify cryptographic integrity of a sequential hash-chained audit log."""
    if not chained_records:
        return {'status': 'empty_log', 'valid': True, 'count': 0}

    expected_prev = "0" * 64
    for idx, rec in enumerate(chained_records, start=1):
        rec_copy = dict(rec)
        stored_hash = rec_copy.pop('record_hash', None)
        stored_prev = rec_copy.get('previous_hash')
        stored_seq = rec_copy.get('sequence_id')

        if stored_seq != idx:
            raise ValueError(f"Sequence break at index {idx}: expected {idx}, got {stored_seq}")
        if stored_prev != expected_prev:
            raise ValueError(f"Hash chain break at sequence {idx}: previous_hash mismatch")

        # Recalculate hash
        payload_str = json.dumps(rec_copy, sort_keys=True, ensure_ascii=False)
        calc_hash = hashlib.sha256(payload_str.encode('utf-8')).hexdigest()

        if calc_hash != stored_hash:
            raise ValueError(f"Tamper detected at sequence {idx}: hash mismatch")

        expected_prev = calc_hash

    return {
        'status': 'verified_intact',
        'valid': True,
        'count': len(chained_records),
        'head_hash': expected_prev,
        'warning': 'Local SHA-256 hash chaining detects post-hoc log tampering only. It does NOT prove upstream external data authenticity.'
    }

def evaluate_walk_forward(race_records, n_folds=3):
    """Execute walk-forward cross validation and compile fold-level & aggregate diagnostics."""
    audit_feature_leakage(race_records)
    audit_probability_integrity(race_records)

    folds = generate_walk_forward_folds(race_records, n_folds=n_folds)
    if not folds:
        return {'status': 'unevaluable_insufficient_samples', 'folds': []}

    fold_reports = []
    all_audit_logs = []

    for f in folds:
        calibrator = PlattCalibrator().fit(
            [r['win'] for r in f['calib_rows']],
            [r['prob_raw'] for r in f['calib_rows']]
        )
        calibrated_test = calibrate_race_probabilities(f['test_rows'], calibrator)

        y_true = [r['win'] for r in calibrated_test]
        p_raw = [r['prob_raw'] for r in calibrated_test]
        p_cal = [r['prob_calibrated'] for r in calibrated_test]

        raw_ll = compute_log_loss(y_true, p_raw)
        cal_ll = compute_log_loss(y_true, p_cal)
        raw_brier = compute_brier_score(y_true, p_raw)
        cal_brier = compute_brier_score(y_true, p_cal)
        raw_ece = compute_ece(y_true, p_raw)
        cal_ece = compute_ece(y_true, p_cal)

        # Top-1 accuracy
        races = {}
        for r in calibrated_test:
            races.setdefault(r['race_id'], []).append(r)
        hits = sum(1 for rows in races.values() if max(rows, key=lambda x: x['prob_calibrated'])['win'] == 1)
        top1_acc = round(hits / len(races), 4) if races else 0.0

        # Paper decisions
        decisions = audit_paper_decisions(calibrated_test)
        all_audit_logs.extend(decisions['audit_logs'])

        fold_reports.append({
            'fold': f['fold_index'],
            'train_races': f['train_races'],
            'calib_races': f['calib_races'],
            'test_races': f['test_races'],
            'test_runners': len(calibrated_test),
            'metrics': {
                'log_loss': {'raw': round(raw_ll, 4), 'calibrated': round(cal_ll, 4)},
                'brier_score': {'raw': round(raw_brier, 4), 'calibrated': round(cal_brier, 4)},
                'ece': {'raw': round(raw_ece, 4), 'calibrated': round(cal_ece, 4)},
                'top1_hit_rate': top1_acc
            },
            'decisions': {
                'total_bets': decisions['total_bets'],
                'total_skips': decisions['total_skips']
            }
        })

    # Hash chain the paper decision audit logs
    chained_logs = create_hash_chained_audit_log(all_audit_logs)
    chain_verification = verify_audit_log_hash_chain(chained_logs)

    # Compute fold averages
    avg_cal_ll = round(sum(f['metrics']['log_loss']['calibrated'] for f in fold_reports) / len(fold_reports), 4)
    avg_cal_brier = round(sum(f['metrics']['brier_score']['calibrated'] for f in fold_reports) / len(fold_reports), 4)
    avg_cal_ece = round(sum(f['metrics']['ece']['calibrated'] for f in fold_reports) / len(fold_reports), 4)
    avg_top1 = round(sum(f['metrics']['top1_hit_rate'] for f in fold_reports) / len(fold_reports), 4)

    return {
        'status': 'completed',
        'total_folds': len(fold_reports),
        'aggregate_metrics': {
            'mean_log_loss_calibrated': avg_cal_ll,
            'mean_brier_calibrated': avg_cal_brier,
            'mean_ece_calibrated': avg_cal_ece,
            'mean_top1_hit_rate': avg_top1
        },
        'folds': fold_reports,
        'hash_chain_verification': chain_verification,
        'chained_audit_logs': chained_logs,
        'disclaimer': (
            'NOTICE: SYNTHETIC BENCHMARK WALK-FORWARD EVALUATION ONLY. '
            'METRICS DO NOT REPRESENT ACTUAL RACING BETTING PERFORMANCE.'
        )
    }

def run_phase15_pipeline(seed=42, n_races=40, n_folds=3, output_report=None, output_audit_log=None):
    """Full pipeline runner for Phase 15."""
    records = generate_synthetic_benchmark(num_races=n_races, horses_per_race=10, seed=seed)

    # Normalize per-race raw probabilities
    races = {}
    for r in records:
        r['prob_raw'] = r.pop('prob_model')
        r['model_id'] = 'win-model-v2'
        races.setdefault(r['race_id'], []).append(r)

    for rows in races.values():
        s = sum(x['prob_raw'] for x in rows) or 1.0
        for x in rows:
            x['prob_raw'] = round(x['prob_raw'] / s, 6)

    result = evaluate_walk_forward(records, n_folds=n_folds)
    result['pipeline_metadata'] = {
        'seed': seed,
        'total_benchmark_races': n_races,
        'n_folds': n_folds,
        'executed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()
    }

    if output_report:
        p = Path(output_report)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Exclude raw chained log list from summary report for readability
        summary = dict(result)
        summary.pop('chained_audit_logs', None)
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')

    if output_audit_log:
        p = Path(output_audit_log)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('w', encoding='utf-8') as f:
            for item in result.get('chained_audit_logs', []):
                f.write(json.dumps(item, ensure_ascii=False) + '\n')

    return result

def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 15 Walk-Forward & Paper Monitor")
    parser.add_argument('--seed', type=int, default=42, help="Random seed")
    parser.add_argument('--folds', type=int, default=3, help="Number of walk-forward folds")
    parser.add_argument('--races', type=int, default=40, help="Total synthetic races")
    parser.add_argument('--output-report', help="Output path for JSON report")
    parser.add_argument('--output-audit-log', help="Output path for JSONL hash-chained audit log")
    args = parser.parse_args(argv)

    res = run_phase15_pipeline(
        seed=args.seed,
        n_races=args.races,
        n_folds=args.folds,
        output_report=args.output_report,
        output_audit_log=args.output_audit_log
    )
    # Print summary without massive log dump
    summary = dict(res)
    summary.pop('chained_audit_logs', None)
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
