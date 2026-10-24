"""Phase 13: Zero-Cost Leak-Free Offline Model Evaluation Pipeline.

Performs purged time-series splitting, verifies feature time integrity,
computes calibration/log loss/Brier score, and enforces strict
eligibility rules for ROI/hit rate evaluation.

Never misrepresents synthetic performance as real production results.
"""
import argparse
import datetime as dt
import json
import math
import random
from pathlib import Path

def _safe_log(p, eps=1e-15):
    p = max(eps, min(1.0 - eps, p))
    return math.log(p)

def compute_log_loss(y_true, y_prob):
    if not y_true or len(y_true) != len(y_prob):
        return None
    total = 0.0
    for y, p in zip(y_true, y_prob):
        total += y * _safe_log(p) + (1.0 - y) * _safe_log(1.0 - p)
    return -total / len(y_true)

def compute_brier_score(y_true, y_prob):
    if not y_true or len(y_true) != len(y_prob):
        return None
    total = sum((p - y) ** 2 for y, p in zip(y_true, y_prob))
    return total / len(y_true)

def purged_time_series_split(race_records, train_ratio=0.8):
    """Split races chronologically without overlapping race_ids or future data."""
    if not race_records:
        return [], []

    # Group by race_id and determine race start timestamp
    races = {}
    for row in race_records:
        r_id = row['race_id']
        r_time = row['race_start_at']
        if r_id not in races:
            races[r_id] = {'race_start_at': r_time, 'rows': []}
        races[r_id]['rows'].append(row)

    # Sort races strictly by race_start_at, then race_id
    sorted_race_keys = sorted(races.keys(), key=lambda r: (races[r]['race_start_at'], r))
    total_races = len(sorted_race_keys)
    if total_races < 2:
        raise ValueError("At least 2 distinct races required for time-series split")

    split_idx = max(1, min(total_races - 1, int(total_races * train_ratio)))
    train_races = set(sorted_race_keys[:split_idx])
    test_races = set(sorted_race_keys[split_idx:])

    # Temporal integrity check: train max time must precede test min time
    train_max_time = max(races[r]['race_start_at'] for r in train_races)
    test_min_time = min(races[r]['race_start_at'] for r in test_races)
    if train_max_time > test_min_time:
        raise ValueError(
            f"Temporal leak detected: train_max_time ({train_max_time}) "
            f"exceeds test_min_time ({test_min_time})"
        )

    # Overlap check
    if train_races.intersection(test_races):
        raise ValueError("Race overlap detected across train and test partitions")

    train_rows = [row for r in sorted_race_keys[:split_idx] for row in races[r]['rows']]
    test_rows = [row for r in sorted_race_keys[split_idx:] for row in races[r]['rows']]
    return train_rows, test_rows

def audit_feature_leakage(rows):
    """Verify that feature observation timestamps strictly precede cutoff and race start."""
    for idx, r in enumerate(rows):
        start = r.get('race_start_at')
        feat_time = r.get('feature_observed_at')
        cutoff = r.get('cutoff_at')

        if not start or not feat_time:
            continue

        if feat_time >= start:
            raise ValueError(
                f"Row {idx} ({r.get('race_id')}, #{r.get('horse_number')}): "
                f"Feature observed at {feat_time} on or after race start {start}"
            )
        if cutoff and feat_time > cutoff:
            raise ValueError(
                f"Row {idx} ({r.get('race_id')}, #{r.get('horse_number')}): "
                f"Feature observed at {feat_time} after cutoff {cutoff}"
            )
    return True

def generate_synthetic_benchmark(num_races=20, horses_per_race=10, seed=42):
    """Generate controlled synthetic racing data for pipeline verification only."""
    rng = random.Random(seed)
    base_time = dt.datetime(2026, 1, 1, 10, 0, tzinfo=dt.timezone.utc)
    records = []

    for r_idx in range(num_races):
        race_id = f"2026{r_idx + 1:04d}0101"
        race_start = base_time + dt.timedelta(hours=r_idx * 2)
        cutoff = race_start - dt.timedelta(minutes=10)
        feat_obs = race_start - dt.timedelta(minutes=25)
        pred_time = race_start - dt.timedelta(minutes=15)
        odds_obs = race_start - dt.timedelta(minutes=12)

        # Assign latent strengths
        strengths = [rng.expovariate(1.0) for _ in range(horses_per_race)]
        total_strength = sum(strengths)
        true_probs = [s / total_strength for s in strengths]

        # Winner sampled from true_probs
        winner_idx = rng.choices(range(horses_per_race), weights=true_probs, k=1)[0]

        for h_idx in range(horses_per_race):
            horse_num = h_idx + 1
            is_win = 1 if h_idx == winner_idx else 0
            t_prob = true_probs[h_idx]

            # Model prediction with slight noise
            noise = rng.gauss(0, 0.05)
            pred_prob = max(0.01, min(0.99, t_prob + noise))

            # Baseline model: uniform naive prior
            baseline_prob = 1.0 / horses_per_race

            # Fair synthetic odds with margin
            synthetic_odds = round(max(1.1, min(99.9, (1.0 / t_prob) * 0.8)), 1)

            records.append({
                'race_id': race_id,
                'horse_number': horse_num,
                'race_start_at': race_start.isoformat(),
                'feature_observed_at': feat_obs.isoformat(),
                'cutoff_at': cutoff.isoformat(),
                'prediction_at': pred_time.isoformat(),
                'odds_observed_at': odds_obs.isoformat(),
                'odds_value': synthetic_odds,
                'win': is_win,
                'prob_model': pred_prob,
                'prob_baseline': baseline_prob,
                'is_synthetic': True,
                # Verified payout field is explicitly omitted in unverified synthetic data
                'verified_payout_yen': None
            })

    return records

def evaluate_offline_predictions(test_records):
    """Evaluate predictions, strictly enforcing missing payout/odds gating."""
    audit_feature_leakage(test_records)

    y_true = [r['win'] for r in test_records]
    p_model = [r['prob_model'] for r in test_records]
    p_base = [r['prob_baseline'] for r in test_records]

    # Probabilistic scoring
    log_loss_model = compute_log_loss(y_true, p_model)
    log_loss_base = compute_log_loss(y_true, p_base)
    brier_model = compute_brier_score(y_true, p_prob := p_model)
    brier_base = compute_brier_score(y_true, p_base)

    # Top-1 accuracy (per race)
    races = {}
    for r in test_records:
        races.setdefault(r['race_id'], []).append(r)

    model_hits = 0
    base_hits = 0
    total_races = len(races)

    for r_id, rows in races.items():
        top_model = max(rows, key=lambda x: x['prob_model'])
        top_base = max(rows, key=lambda x: x['prob_baseline'])
        if top_model['win'] == 1:
            model_hits += 1
        if top_base['win'] == 1:
            base_hits += 1

    hit_rate_model = model_hits / total_races if total_races else 0.0
    hit_rate_base = base_hits / total_races if total_races else 0.0

    # Strict ROI Gating: verify if genuine pre-race odds and verified settlements exist
    has_unverified_synthetic = any(r.get('is_synthetic', False) for r in test_records)
    has_missing_payouts = any(
        r.get('verified_payout_yen') is None
        for r in test_records if r.get('win') == 1
    )

    if has_unverified_synthetic or has_missing_payouts:
        roi_evaluation = {
            'roi_status': 'unevaluable_missing_verified_odds_and_payouts',
            'roi': None,
            'net_profit_yen': None,
            'total_stake_yen': None,
            'reason': (
                'Verified official payouts and authenticated upstream pre-race odds '
                'are not available. Synthetic or unverified data cannot produce valid ROI.'
            )
        }
    else:
        # Calculate ROI only when all data are authenticated
        stake = total_races * 100
        returns = sum(r['verified_payout_yen'] for r in test_records if r['win'] == 1)
        roi_evaluation = {
            'roi_status': 'evaluated',
            'roi': round(returns / stake, 4) if stake else 0.0,
            'net_profit_yen': returns - stake,
            'total_stake_yen': stake,
            'reason': 'Calculated on verified official settlement records.'
        }

    return {
        'total_test_rows': len(test_records),
        'total_test_races': total_races,
        'metrics': {
            'model': {
                'log_loss': round(log_loss_model, 4) if log_loss_model else None,
                'brier_score': round(brier_model, 4) if brier_model else None,
                'top1_hit_rate': round(hit_rate_model, 4)
            },
            'baseline': {
                'log_loss': round(log_loss_base, 4) if log_loss_base else None,
                'brier_score': round(brier_base, 4) if brier_base else None,
                'top1_hit_rate': round(hit_rate_base, 4)
            }
        },
        'roi_evaluation': roi_evaluation,
        'disclaimer': (
            'NOTICE: SYNTHETIC BENCHMARK EVALUATION ONLY. '
            'DOES NOT CONSTITUTE ACTUAL WAGERING PERFORMANCE OR OFFICIAL MARKET RETURNS.'
        )
    }

def run_evaluation_pipeline(data_source='synthetic', seed=42, train_ratio=0.8, output_path=None):
    """Run full zero-cost offline evaluation pipeline."""
    if data_source == 'synthetic':
        records = generate_synthetic_benchmark(num_races=20, horses_per_race=10, seed=seed)
    else:
        raise ValueError(f"Unsupported data_source: {data_source}. Real feeds are currently unverified.")

    train_rows, test_rows = purged_time_series_split(records, train_ratio=train_ratio)
    report = evaluate_offline_predictions(test_rows)
    report['pipeline_metadata'] = {
        'seed': seed,
        'train_ratio': train_ratio,
        'train_races': len(set(r['race_id'] for r in train_rows)),
        'test_races': len(set(r['race_id'] for r in test_rows)),
        'executed_at_utc': dt.datetime.now(dt.timezone.utc).isoformat()
    }

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    return report

def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 13 Zero-Cost Offline Evaluation")
    parser.add_argument('--synthetic', action='store_true', default=True, help="Use synthetic benchmark")
    parser.add_argument('--seed', type=int, default=42, help="Random seed")
    parser.add_argument('--train-ratio', type=float, default=0.8, help="Train split ratio")
    parser.add_argument('--output-report', help="Path to write JSON evaluation report")
    args = parser.parse_args(argv)

    res = run_evaluation_pipeline(
        data_source='synthetic',
        seed=args.seed,
        train_ratio=args.train_ratio,
        output_path=args.output_report
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
