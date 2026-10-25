"""Phase 17: Reproducible Paper-Only Evaluation Reports and Comparability Comparator.

Ensures cryptographic dataset fingerprinting, finite numerical guarantees,
rigorous comparability checks between experimental runs, and integrated
audit hash chain verification.
Zero cost, synthetic-only fallback, fail-closed ROI gating, and no automated wagering.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

# Ensure repository root is on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.phase16_paper_quality_gate import run_phase16_pipeline
from scripts.phase15_walkforward_paper_monitor import verify_audit_log_hash_chain

REPORT_SCHEMA_VERSION = "17.0.0"

def get_current_git_commit():
    """Retrieve current git commit SHA safely."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True
        )
        return res.stdout.strip()
    except Exception:
        return "unknown_or_uncommitted"

def compute_dataset_fingerprint(records):
    """Compute deterministic SHA-256 digest of input dataset records."""
    canonical = json.dumps(records, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()

def check_finite_metrics(metrics_dict):
    """Validate that all numerical values in metrics are finite numbers."""
    for key, val in metrics_dict.items():
        if isinstance(val, (int, float)):
            if math.isnan(val) or math.isinf(val):
                raise ValueError(f"Non-finite metric value detected for {key}: {val}")
        elif isinstance(val, dict):
            check_finite_metrics(val)
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, (int, float)) and (math.isnan(v) or math.isinf(v)):
                    raise ValueError(f"Non-finite list item detected in {key}: {v}")
    return True

def generate_reproducible_report(seed=42, n_races=40, n_folds=3, output_report=None, output_audit_log=None):
    """Generate a fully reproducible, tamper-evident paper-only evaluation report."""
    from scripts.phase13_offline_evaluation import generate_synthetic_benchmark

    raw_benchmark = generate_synthetic_benchmark(num_races=n_races, horses_per_race=10, seed=seed)
    dataset_sha256 = compute_dataset_fingerprint(raw_benchmark)

    p16_res = run_phase16_pipeline(
        seed=seed,
        n_races=n_races,
        n_folds=n_folds,
        output_report=None,
        output_audit_log=output_audit_log
    )

    agg = p16_res.get('walk_forward_metrics', {})
    robustness = p16_res.get('robustness_diagnostics', {})
    chain_verif = p16_res.get('hash_chain_verification', {})
    qgate = p16_res.get('quality_gate_audit', {})

    metrics_payload = {
        'aggregate_metrics': agg,
        'robustness_diagnostics': robustness
    }
    check_finite_metrics(metrics_payload)

    report = {
        'report_metadata': {
            'report_schema_version': REPORT_SCHEMA_VERSION,
            'pipeline_name': 'Phase 17 Reproducible Paper Evaluation',
            'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
            'git_commit_sha': get_current_git_commit(),
            'random_seed': seed,
            'dataset_fingerprint_sha256': dataset_sha256,
            'split_configuration': {
                'total_races': n_races,
                'n_folds': n_folds,
                'horses_per_race': 10
            },
            'model_id': 'win-model-v2'
        },
        'provenance_and_licensing': {
            'data_provenance': 'synthetic_benchmark',
            'license_status': 'verified_licensed',
            'upstream_authenticity_attestation': 'unauthenticated_local_only',
            'provenance_warning': (
                'Local SHA-256 hash chaining detects post-hoc log tampering only. '
                'It does NOT prove upstream external data authenticity.'
            )
        },
        'quality_gate_summary': qgate,
        'performance_metrics': metrics_payload,
        'audit_integrity': {
            'hash_chain_valid': chain_verif.get('valid', False),
            'hash_chain_head': chain_verif.get('head_hash'),
            'record_count': chain_verif.get('count', 0),
            'audit_warning': chain_verif.get('warning')
        },
        'roi_evaluation': {
            'roi_status': 'unevaluable_unverified_data',
            'roi': None,
            'reason': 'Real official payouts and authenticated upstream pre-race odds are not established.'
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

    return report

def compare_reproducible_reports(report_a, report_b):
    """Compare two evaluation reports, strictly verifying baseline parity before ranking."""
    meta_a = report_a.get('report_metadata', {})
    meta_b = report_b.get('report_metadata', {})

    discrepancies = []

    # 1. Dataset fingerprint matching
    fp_a = meta_a.get('dataset_fingerprint_sha256')
    fp_b = meta_b.get('dataset_fingerprint_sha256')
    if fp_a != fp_b:
        discrepancies.append(f"Dataset fingerprint mismatch: '{fp_a}' vs '{fp_b}'")

    # 2. Seed matching
    seed_a = meta_a.get('random_seed')
    seed_b = meta_b.get('random_seed')
    if seed_a != seed_b:
        discrepancies.append(f"Random seed mismatch: {seed_a} vs {seed_b}")

    # 3. Split config matching
    split_a = meta_a.get('split_configuration', {})
    split_b = meta_b.get('split_configuration', {})
    if split_a != split_b:
        discrepancies.append(f"Split configuration mismatch: {split_a} vs {split_b}")

    # 4. Schema version
    ver_a = meta_a.get('report_schema_version')
    ver_b = meta_b.get('report_schema_version')
    if ver_a != ver_b:
        discrepancies.append(f"Schema version mismatch: '{ver_a}' vs '{ver_b}'")

    if discrepancies:
        return {
            'comparable': False,
            'status': 'incomparable_experimental_controls',
            'discrepancies': discrepancies,
            'message': 'Reports cannot be compared reliably due to mismatched baseline controls or datasets.'
        }

    # If identical controls, compute delta
    met_a = report_a.get('performance_metrics', {}).get('aggregate_metrics', {})
    met_b = report_b.get('performance_metrics', {}).get('aggregate_metrics', {})

    delta = {}
    for metric_name in met_a.keys():
        val_a = met_a.get(metric_name)
        val_b = met_b.get(metric_name)
        if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
            delta[metric_name] = round(val_b - val_a, 4)

    return {
        'comparable': True,
        'status': 'comparison_evaluated',
        'model_a': meta_a.get('model_id'),
        'model_b': meta_b.get('model_id'),
        'metric_deltas_b_minus_a': delta,
        'independent_sample_note': (
            'Race-level clusters are preserved; runner-level observations '
            'within the same race must not be treated as independent samples.'
        )
    }

def verify_integrated_audit_log_file(audit_log_path):
    """Verify an on-disk JSONL audit log with complete hash chain checks."""
    p = Path(audit_log_path)
    if not p.is_file():
        raise FileNotFoundError(f"Audit log file not found: {audit_log_path}")

    records = []
    with p.open('r', encoding='utf-8') as f:
        for idx, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception as e:
                raise ValueError(f"Malformed JSON at line {idx}: {e}")

    try:
        return verify_audit_log_hash_chain(records)
    except ValueError as e:
        return {
            'status': 'tamper_detected',
            'valid': False,
            'count': len(records),
            'error': str(e),
            'tampered_step': str(e),
            'warning': 'Local SHA-256 hash chaining detects post-hoc log tampering only. It does NOT prove upstream external data authenticity.'
        }

def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 17 Reproducible Report & Comparator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen = subparsers.add_parser("generate")
    gen.add_argument("--seed", type=int, default=42, help="Random seed")
    gen.add_argument("--races", type=int, default=40, help="Number of benchmark races")
    gen.add_argument("--folds", type=int, default=3, help="Walk-forward folds")
    gen.add_argument("--output-report", help="Output JSON path")
    gen.add_argument("--output-audit-log", help="Output JSONL audit log path")

    comp = subparsers.add_parser("compare")
    comp.add_argument("--report-a", required=True, help="Path to first report JSON")
    comp.add_argument("--report-b", required=True, help="Path to second report JSON")

    ver = subparsers.add_parser("verify-log")
    ver.add_argument("--audit-log", required=True, help="Path to JSONL audit log")

    args = parser.parse_args(argv)

    if args.command == "generate":
        res = generate_reproducible_report(
            seed=args.seed,
            n_races=args.races,
            n_folds=args.folds,
            output_report=args.output_report,
            output_audit_log=args.output_audit_log
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.command == "compare":
        rep_a = json.loads(Path(args.report_a).read_text(encoding='utf-8'))
        rep_b = json.loads(Path(args.report_b).read_text(encoding='utf-8'))
        diff = compare_reproducible_reports(rep_a, rep_b)
        print(json.dumps(diff, ensure_ascii=False, indent=2))
    elif args.command == "verify-log":
        ver_res = verify_integrated_audit_log_file(args.audit_log)
        print(json.dumps(ver_res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
