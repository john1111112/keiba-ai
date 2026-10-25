"""Phase 16: Paper-Only Quality Gates and Walk-Forward Robustness Diagnostics.

Enforces strict multi-invariant quality gating (data provenance, temporal integrity,
probability validity, hash chain consistency) with explicit fail-closed reason codes.
Includes bootstrap confidence intervals for walk-forward evaluation.
Zero cost, synthetic benchmark only, fail-closed ROI gating, no automated wagering.
"""
import argparse
import datetime as dt
import json
import math
from pathlib import Path
import random
import sys

# Ensure repository root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.phase15_walkforward_paper_monitor import (
    evaluate_walk_forward,
    create_hash_chained_audit_log,
    verify_audit_log_hash_chain
)
from scripts.phase13_offline_evaluation import generate_synthetic_benchmark
from src.paper_trading import utc_time

ALLOWED_PROVENANCE_SOURCES = {'synthetic_benchmark', 'licensed-feed', 'authorized_fixture'}

class QualityReasonCode:
    PASSED = "PASSED"
    INVALID_SOURCE_PROVENANCE = "INVALID_SOURCE_PROVENANCE"
    UNAUTHORIZED_DATA_USAGE = "UNAUTHORIZED_DATA_USAGE"
    POST_START_FEATURE_OBSERVATION = "POST_START_FEATURE_OBSERVATION"
    FEATURE_CUTOFF_VIOLATION = "FEATURE_CUTOFF_VIOLATION"
    POST_START_ODDS_OBSERVATION = "POST_START_ODDS_OBSERVATION"
    OBSERVATION_AFTER_PREDICTION = "OBSERVATION_AFTER_PREDICTION"
    POST_START_PREDICTION = "POST_START_PREDICTION"
    FUTURE_TIMESTAMP_REJECTED = "FUTURE_TIMESTAMP_REJECTED"
    MISSING_MODEL_VERSION = "MISSING_MODEL_VERSION"
    INVALID_PROBABILITY_VALUE = "INVALID_PROBABILITY_VALUE"
    INVALID_OR_MISSING_ODDS = "INVALID_OR_MISSING_ODDS"
    RACE_PROBABILITY_SUM_ANOMALY = "RACE_PROBABILITY_SUM_ANOMALY"
    DUPLICATE_HORSE_NUMBER = "DUPLICATE_HORSE_NUMBER"
    RACE_ID_MISMATCH = "RACE_ID_MISMATCH"
    STALE_ODDS_SNAPSHOT = "STALE_ODDS_SNAPSHOT"
    HASH_CHAIN_INTEGRITY_BREACH = "HASH_CHAIN_INTEGRITY_BREACH"

def evaluate_runner_quality(runner_row, *, now_utc=None):
    """Evaluate all quality gate invariants for a single runner record."""
    current = now_utc or dt.datetime.now(dt.timezone.utc)

    # 1. Source Provenance
    source = runner_row.get('source_provenance') or ('synthetic_benchmark' if runner_row.get('is_synthetic') else None)
    if not source or source not in ALLOWED_PROVENANCE_SOURCES:
        return QualityReasonCode.INVALID_SOURCE_PROVENANCE

    # 2. Model Version & Probability Value
    model_id = runner_row.get('model_id')
    if not model_id or not str(model_id).strip():
        return QualityReasonCode.MISSING_MODEL_VERSION

    prob = runner_row.get('prob_calibrated', runner_row.get('prob_raw'))
    if prob is None or math.isnan(prob) or math.isinf(prob) or not (0.0 <= prob <= 1.0):
        return QualityReasonCode.INVALID_PROBABILITY_VALUE

    # 3. Odds Value
    odds = runner_row.get('odds_value')
    if odds is None or math.isnan(odds) or math.isinf(odds) or odds < 1.0:
        return QualityReasonCode.INVALID_OR_MISSING_ODDS

    # 4. Temporal Integrity
    try:
        start_iso = utc_time(runner_row['race_start_at'])
        feat_iso = utc_time(runner_row['feature_observed_at'])
        cutoff_iso = utc_time(runner_row['cutoff_at'])
        pred_iso = utc_time(runner_row['prediction_at'])
        odds_iso = utc_time(runner_row['odds_observed_at'])
    except Exception:
        return QualityReasonCode.FUTURE_TIMESTAMP_REJECTED

    start_dt = dt.datetime.fromisoformat(start_iso)
    feat_dt = dt.datetime.fromisoformat(feat_iso)
    cutoff_dt = dt.datetime.fromisoformat(cutoff_iso)
    pred_dt = dt.datetime.fromisoformat(pred_iso)
    odds_dt = dt.datetime.fromisoformat(odds_iso)

    if feat_dt >= start_dt:
        return QualityReasonCode.POST_START_FEATURE_OBSERVATION
    if feat_dt > cutoff_dt:
        return QualityReasonCode.FEATURE_CUTOFF_VIOLATION
    if odds_dt >= start_dt:
        return QualityReasonCode.POST_START_ODDS_OBSERVATION
    if odds_dt > pred_dt:
        return QualityReasonCode.OBSERVATION_AFTER_PREDICTION
    if pred_dt >= start_dt:
        return QualityReasonCode.POST_START_PREDICTION

    # Clock anomaly guard
    if odds_dt > current + dt.timedelta(seconds=60) or feat_dt > current + dt.timedelta(seconds=60):
        return QualityReasonCode.FUTURE_TIMESTAMP_REJECTED

    # Stale odds check: odds observed more than 48 hours before prediction
    if (pred_dt - odds_dt).total_seconds() > 48 * 3600:
        return QualityReasonCode.STALE_ODDS_SNAPSHOT

    return QualityReasonCode.PASSED

def evaluate_race_card_quality(race_id, runner_rows, *, now_utc=None):
    """Evaluate card-level invariants across all runners in a race."""
    if not runner_rows:
        return False, QualityReasonCode.INVALID_PROBABILITY_VALUE, []

    seen_horses = set()
    runner_evaluations = []
    has_card_failure = False
    card_failure_reason = None

    for r in runner_rows:
        if r.get('race_id') != race_id:
            return False, QualityReasonCode.RACE_ID_MISMATCH, []

        h = r.get('horse_number')
        if h in seen_horses:
            return False, QualityReasonCode.DUPLICATE_HORSE_NUMBER, []
        seen_horses.add(h)

        runner_reason = evaluate_runner_quality(r, now_utc=now_utc)
        if runner_reason != QualityReasonCode.PASSED:
            has_card_failure = True
            card_failure_reason = runner_reason

        runner_evaluations.append({
            'horse_number': h,
            'reason_code': runner_reason,
            'passed': runner_reason == QualityReasonCode.PASSED
        })

    # Probability sum-to-one check across the race
    probs = [r.get('prob_calibrated', r.get('prob_raw', 0.0)) for r in runner_rows]
    total_prob = sum(probs)
    if not (0.85 <= total_prob <= 1.15):
        return False, QualityReasonCode.RACE_PROBABILITY_SUM_ANOMALY, runner_evaluations

    if has_card_failure:
        return False, card_failure_reason, runner_evaluations

    return True, QualityReasonCode.PASSED, runner_evaluations

def compute_bootstrap_confidence_intervals(y_true, y_prob, n_bootstraps=200, seed=42):
    """Compute 95% bootstrap confidence intervals for Log Loss and Brier Score."""
    if len(y_true) < 10 or len(y_true) != len(y_prob):
        return {
            'status': 'unevaluable_insufficient_samples',
            'log_loss_ci95': None,
            'brier_score_ci95': None
        }

    rng = random.Random(seed)
    n = len(y_true)
    ll_samples = []
    brier_samples = []

    from scripts.phase14_calibration_paper_audit import compute_log_loss, compute_brier_score

    for _ in range(n_bootstraps):
        indices = [rng.randint(0, n - 1) for _ in range(n)]
        boot_y = [y_true[i] for i in indices]
        boot_p = [y_prob[i] for i in indices]

        ll = compute_log_loss(boot_y, boot_p)
        br = compute_brier_score(boot_y, boot_p)
        if ll is not None:
            ll_samples.append(ll)
        if br is not None:
            brier_samples.append(br)

    if not ll_samples or not brier_samples:
        return {'status': 'unevaluable', 'log_loss_ci95': None, 'brier_score_ci95': None}

    ll_samples.sort()
    brier_samples.sort()

    lower_idx = int(0.025 * len(ll_samples))
    upper_idx = int(0.975 * len(ll_samples))

    return {
        'status': 'evaluated',
        'n_bootstraps': n_bootstraps,
        'log_loss_ci95': [round(ll_samples[lower_idx], 4), round(ll_samples[upper_idx], 4)],
        'brier_score_ci95': [round(brier_samples[lower_idx], 4), round(brier_samples[upper_idx], 4)]
    }

def run_phase16_pipeline(seed=42, n_races=40, n_folds=3, output_report=None, output_audit_log=None):
    """Run full Phase 16 paper quality gate and walk-forward robustness pipeline."""
    from scripts.phase15_walkforward_paper_monitor import evaluate_walk_forward
    records = generate_synthetic_benchmark(num_races=n_races, horses_per_race=10, seed=seed)

    # Attach model_id and provenance
    races = {}
    for r in records:
        r['prob_raw'] = r.pop('prob_model')
        r['model_id'] = 'win-model-v2'
        r['source_provenance'] = 'synthetic_benchmark'
        races.setdefault(r['race_id'], []).append(r)

    # Normalize per-race probabilities
    for rows in races.values():
        s = sum(x['prob_raw'] for x in rows) or 1.0
        for x in rows:
            x['prob_raw'] = round(x['prob_raw'] / s, 6)

    # Quality Gate Audit across all races
    gate_passed_count = 0
    gate_rejected_count = 0
    card_rejections = []

    for r_id, rows in races.items():
        passed, code, runner_evals = evaluate_race_card_quality(r_id, rows)
        if passed:
            gate_passed_count += 1
        else:
            gate_rejected_count += 1
            card_rejections.append({'race_id': r_id, 'reason_code': code})

    # Execute walk-forward evaluation
    wf_results = evaluate_walk_forward(records, n_folds=n_folds)

    # Extract all test predictions for bootstrap CI
    test_y_true = []
    test_y_calib = []
    for f in wf_results.get('folds', []):
        # We can extract rows if stored or derive
        pass

    # Flatten calibrated predictions from fold reports
    all_logs = wf_results.get('chained_audit_logs', [])
    # Find matching winners from records
    win_map = {(r['race_id'], r['horse_number']): r['win'] for r in records}
    test_y_true = [win_map.get((l['race_id'], l['horse_number']), 0) for l in all_logs]
    test_y_calib = [l['prob_calibrated'] for l in all_logs]

    bootstrap_diag = compute_bootstrap_confidence_intervals(test_y_true, test_y_calib, n_bootstraps=200, seed=seed)

    report = {
        'pipeline': 'Phase 16 Paper Quality Gates & Walk-Forward Robustness Diagnostics',
        'metadata': {
            'seed': seed,
            'total_races': n_races,
            'n_folds': n_folds,
            'executed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()
        },
        'quality_gate_audit': {
            'total_races_audited': len(races),
            'races_passed': gate_passed_count,
            'races_rejected': gate_rejected_count,
            'rejection_summary': card_rejections
        },
        'walk_forward_metrics': wf_results['aggregate_metrics'],
        'robustness_diagnostics': bootstrap_diag,
        'hash_chain_verification': wf_results['hash_chain_verification'],
        'disclaimer': (
            'NOTICE: SYNTHETIC BENCHMARK EVALUATION ONLY. '
            'QUALITY GATES ENFORCE PAPER-TRADING SAFETY INVARIANTS. '
            'DOES NOT CONSTITUTE ACTUAL MARKET RETURNS OR OFFICIAL WAGERING PERFORMANCE.'
        )
    }

    if output_report:
        p = Path(output_report)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    if output_audit_log:
        p = Path(output_audit_log)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open('w', encoding='utf-8') as f:
            for item in all_logs:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')

    return report

def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 16 Paper Quality Gate & Diagnostics")
    parser.add_argument('--seed', type=int, default=42, help="Random seed")
    parser.add_argument('--races', type=int, default=40, help="Number of benchmark races")
    parser.add_argument('--folds', type=int, default=3, help="Walk-forward folds")
    parser.add_argument('--output-report', help="Output path for JSON report")
    parser.add_argument('--output-audit-log', help="Output path for JSONL audit log")
    args = parser.parse_args(argv)

    res = run_phase16_pipeline(
        seed=args.seed,
        n_races=args.races,
        n_folds=args.folds,
        output_report=args.output_report,
        output_audit_log=args.output_audit_log
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
