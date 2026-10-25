"""Phase 14: Probability Calibration, Leak-Free 3-Way Split, and Paper Decision Audit.

Implements strict chronological Train/Calibration/Test splitting, Platt sigmoid calibration,
multi-runner probability normalization, ECE computation, and reproducible paper trade auditing.

Zero cost, synthetic-only fallback, fail-closed ROI gating, and no automated wagering.
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

def _safe_log(p, eps=1e-15):
    p = max(eps, min(1.0 - eps, p))
    return math.log(p)

def compute_log_loss(y_true, y_prob):
    if not y_true or len(y_true) != len(y_prob):
        return None
    total = sum(y * _safe_log(p) + (1.0 - y) * _safe_log(1.0 - p) for y, p in zip(y_true, y_prob))
    return -total / len(y_true)

def compute_brier_score(y_true, y_prob):
    if not y_true or len(y_true) != len(y_prob):
        return None
    return sum((p - y) ** 2 for y, p in zip(y_true, y_prob)) / len(y_true)

def compute_ece(y_true, y_prob, n_bins=10):
    """Compute Expected Calibration Error (ECE) across probability bins."""
    if not y_true or len(y_true) != len(y_prob):
        return None
    bins = [[] for _ in range(n_bins)]
    for y, p in zip(y_true, y_prob):
        b_idx = min(n_bins - 1, int(p * n_bins))
        bins[b_idx].append((y, p))

    total_n = len(y_true)
    ece = 0.0
    for b in bins:
        if not b:
            continue
        bin_size = len(b)
        avg_acc = sum(y for y, _ in b) / bin_size
        avg_conf = sum(p for _, p in b) / bin_size
        ece += (bin_size / total_n) * abs(avg_acc - avg_conf)
    return ece

def purged_three_way_split(race_records, train_ratio=0.6, calib_ratio=0.2):
    """Chronologically split races into Train, Calibration, and Test partitions."""
    if not race_records:
        return [], [], []

    races = {}
    for row in race_records:
        r_id = row['race_id']
        r_time = row['race_start_at']
        if r_id not in races:
            races[r_id] = {'race_start_at': r_time, 'rows': []}
        races[r_id]['rows'].append(row)

    sorted_race_keys = sorted(races.keys(), key=lambda r: (races[r]['race_start_at'], r))
    n_races = len(sorted_race_keys)
    if n_races < 3:
        raise ValueError("At least 3 distinct races required for Train/Calibration/Test split")

    idx1 = max(1, int(n_races * train_ratio))
    idx2 = max(idx1 + 1, int(n_races * (train_ratio + calib_ratio)))
    idx2 = min(idx2, n_races - 1)

    train_keys = set(sorted_race_keys[:idx1])
    calib_keys = set(sorted_race_keys[idx1:idx2])
    test_keys = set(sorted_race_keys[idx2:])

    # Temporal non-overlap checks
    t_max = max(races[r]['race_start_at'] for r in train_keys)
    c_min = min(races[r]['race_start_at'] for r in calib_keys)
    c_max = max(races[r]['race_start_at'] for r in calib_keys)
    test_min = min(races[r]['race_start_at'] for r in test_keys)

    if t_max > c_min:
        raise ValueError(f"Temporal leak: Train max ({t_max}) > Calibration min ({c_min})")
    if c_max > test_min:
        raise ValueError(f"Temporal leak: Calibration max ({c_max}) > Test min ({test_min})")

    # Cardinality overlap check
    if train_keys.intersection(calib_keys) or calib_keys.intersection(test_keys) or train_keys.intersection(test_keys):
        raise ValueError("Race ID overlap detected across split partitions")

    train_rows = [row for r in sorted_race_keys[:idx1] for row in races[r]['rows']]
    calib_rows = [row for r in sorted_race_keys[idx1:idx2] for row in races[r]['rows']]
    test_rows = [row for r in sorted_race_keys[idx2:] for row in races[r]['rows']]
    return train_rows, calib_rows, test_rows

def audit_probability_integrity(rows):
    """Validate probability bounds, missing values, and per-race sum-to-one invariants."""
    races = {}
    for idx, r in enumerate(rows):
        p = r.get('prob_raw')
        if p is None or math.isnan(p) or math.isinf(p) or not (0.0 <= p <= 1.0):
            raise ValueError(f"Invalid probability value {p} at row {idx}")
        races.setdefault(r['race_id'], []).append(p)

    for r_id, probs in races.items():
        total = sum(probs)
        # Sum of probabilities per race should approximate 1.0 within reasonable tolerance
        if not (0.8 <= total <= 1.2):
            raise ValueError(f"Race {r_id} probability sum anomaly: sum={total:.4f}")
    return True

class PlattCalibrator:
    """Univariate logistic calibration (Platt Scaling) fitted via gradient descent."""
    def __init__(self, lr=0.05, iterations=300):
        self.a = 1.0
        self.b = 0.0
        self.lr = lr
        self.iterations = iterations

    def fit(self, y_true, y_prob):
        if not y_true or len(y_true) != len(y_prob):
            return self
        logits = [_safe_log(p / (1.0 - p)) if p < 1.0 else 10.0 for p in y_prob]
        for _ in range(self.iterations):
            grad_a = 0.0
            grad_b = 0.0
            for y, logit in zip(y_true, logits):
                # Sigmoid(a * logit + b)
                z = max(-15.0, min(15.0, self.a * logit + self.b))
                pred = 1.0 / (1.0 + math.exp(-z))
                err = pred - y
                grad_a += err * logit
                grad_b += err
            self.a -= self.lr * (grad_a / len(y_true))
            self.b -= self.lr * (grad_b / len(y_true))
        return self

    def predict(self, y_prob):
        res = []
        for p in y_prob:
            logit = _safe_log(p / (1.0 - p)) if p < 1.0 else 10.0
            z = max(-15.0, min(15.0, self.a * logit + self.b))
            res.append(1.0 / (1.0 + math.exp(-z)))
        return res

def calibrate_race_probabilities(test_records, calibrator):
    """Apply calibrator and normalize probabilities per race to sum strictly to 1.0."""
    races = {}
    for r in test_records:
        races.setdefault(r['race_id'], []).append(r)

    calibrated_records = []
    for r_id, rows in races.items():
        raw_probs = [r['prob_raw'] for r in rows]
        scaled = calibrator.predict(raw_probs)
        total = sum(scaled) or 1.0
        normalized = [s / total for s in scaled]

        for r, p_norm in zip(rows, normalized):
            new_r = dict(r)
            new_r['prob_calibrated'] = round(p_norm, 6)
            calibrated_records.append(new_r)
    return calibrated_records

def audit_paper_decisions(records, ev_threshold=1.10, prob_threshold=0.10):
    """Simulate and audit paper-only betting decisions with strict logging and ROI gating."""
    audit_logs = []
    total_bets = 0
    total_skips = 0

    for r in records:
        race_id = r['race_id']
        horse = r['horse_number']
        p_calib = r.get('prob_calibrated', r.get('prob_raw', 0.0))
        odds = r.get('odds_value')

        if odds is not None and odds > 0:
            ev = round(p_calib * odds, 4)
        else:
            ev = None

        decision = "SKIP"
        reason = "unfavorable"

        if odds is None:
            reason = "missing_odds"
        elif p_calib < prob_threshold:
            reason = f"probability_below_threshold ({p_calib:.3f} < {prob_threshold})"
        elif ev is not None and ev < ev_threshold:
            reason = f"expected_value_below_threshold ({ev:.3f} < {ev_threshold})"
        elif ev is not None and ev >= ev_threshold and p_calib >= prob_threshold:
            decision = "BET"
            reason = f"ev_threshold_met (EV={ev:.3f} >= {ev_threshold})"

        if decision == "BET":
            total_bets += 1
        else:
            total_skips += 1

        audit_logs.append({
            'race_id': race_id,
            'horse_number': horse,
            'prediction_at': r.get('prediction_at'),
            'feature_cutoff_at': r.get('cutoff_at'),
            'race_start_at': r.get('race_start_at'),
            'odds_value': odds,
            'odds_observed_at': r.get('odds_observed_at'),
            'model_id': r.get('model_id', 'win-model-v2'),
            'prob_calibrated': p_calib,
            'expected_value': ev,
            'decision': decision,
            'reason': reason
        })

    # Strict ROI gating
    has_unverified = any(r.get('is_synthetic', False) for r in records)
    has_missing_payout = any(r.get('verified_payout_yen') is None for r in records if r.get('win') == 1)

    if has_unverified or has_missing_payout:
        roi_report = {
            'roi_status': 'unevaluable_unverified_data',
            'roi': None,
            'reason': 'Real official payouts and authentic pre-race feeds are not established. Paper ROI cannot be claimed.'
        }
    else:
        # Evaluated when real
        stake = total_bets * 100
        returns = sum(r['verified_payout_yen'] for r, l in zip(records, audit_logs) if l['decision'] == 'BET' and r.get('win') == 1)
        roi_report = {
            'roi_status': 'evaluated',
            'roi': round(returns / stake, 4) if stake else 0.0,
            'total_bets': total_bets,
            'stake_yen': stake,
            'returns_yen': returns
        }

    return {
        'total_evaluated_runners': len(records),
        'total_bets': total_bets,
        'total_skips': total_skips,
        'roi_report': roi_report,
        'audit_logs': audit_logs
    }

def run_phase14_pipeline(seed=42, output_report=None, output_audit_log=None):
    """Full execution of Phase 14 calibration and audit pipeline with synthetic data."""
    from scripts.phase13_offline_evaluation import generate_synthetic_benchmark, audit_feature_leakage
    records = generate_synthetic_benchmark(num_races=30, horses_per_race=10, seed=seed)

    # Prepare raw probabilities and normalize per race to sum to 1.0
    races = {}
    for r in records:
        r['prob_raw'] = r.pop('prob_model')
        r['model_id'] = 'win-model-v2'
        races.setdefault(r['race_id'], []).append(r)

    for r_id, rows in races.items():
        s = sum(x['prob_raw'] for x in rows) or 1.0
        for x in rows:
            x['prob_raw'] = round(x['prob_raw'] / s, 6)

    audit_feature_leakage(records)
    audit_probability_integrity(records)

    train_rows, calib_rows, test_rows = purged_three_way_split(records, train_ratio=0.6, calib_ratio=0.2)

    # Fit calibrator on calibration partition
    calibrator = PlattCalibrator().fit(
        [r['win'] for r in calib_rows],
        [r['prob_raw'] for r in calib_rows]
    )

    # Apply calibration to test partition
    calib_test = calibrate_race_probabilities(test_rows, calibrator)

    # Metric evaluation
    y_true = [r['win'] for r in calib_test]
    p_raw = [r['prob_raw'] for r in calib_test]
    p_cal = [r['prob_calibrated'] for r in calib_test]

    raw_ll = compute_log_loss(y_true, p_raw)
    cal_ll = compute_log_loss(y_true, p_cal)
    raw_brier = compute_brier_score(y_true, p_raw)
    cal_brier = compute_brier_score(y_true, p_cal)
    raw_ece = compute_ece(y_true, p_raw)
    cal_ece = compute_ece(y_true, p_cal)

    # Paper decision audit
    audit_res = audit_paper_decisions(calib_test)

    report = {
        'pipeline': 'Phase 14 Probability Calibration & Paper Decision Audit',
        'metadata': {
            'seed': seed,
            'total_races': 30,
            'train_races': len(set(r['race_id'] for r in train_rows)),
            'calib_races': len(set(r['race_id'] for r in calib_rows)),
            'test_races': len(set(r['race_id'] for r in test_rows)),
            'executed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()
        },
        'calibration_metrics': {
            'log_loss': {'raw': round(raw_ll, 4), 'calibrated': round(cal_ll, 4)},
            'brier_score': {'raw': round(raw_brier, 4), 'calibrated': round(cal_brier, 4)},
            'expected_calibration_error': {'raw': round(raw_ece, 4), 'calibrated': round(cal_ece, 4)}
        },
        'paper_decision_audit': {
            'total_evaluated_runners': audit_res['total_evaluated_runners'],
            'total_bets': audit_res['total_bets'],
            'total_skips': audit_res['total_skips'],
            'roi_report': audit_res['roi_report']
        },
        'disclaimer': (
            'NOTICE: SYNTHETIC BENCHMARK EVALUATION ONLY. '
            'PROBABILITY METRICS AND PAPER DECISIONS DO NOT REPRESENT REAL-WORLD MARKET BETTING RESULTS.'
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
            for item in audit_res['audit_logs']:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')

    return report

def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 14 Calibration & Paper Audit")
    parser.add_argument('--seed', type=int, default=42, help="Random seed")
    parser.add_argument('--output-report', help="Output path for JSON report")
    parser.add_argument('--output-audit-log', help="Output path for JSONL audit log")
    args = parser.parse_args(argv)

    res = run_phase14_pipeline(seed=args.seed, output_report=args.output_report, output_audit_log=args.output_audit_log)
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
