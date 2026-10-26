"""Chronological research-only walk-forward evaluation, never sealed 2026.

Each fold retrains from scratch using races before its training cutoff,
evaluates only the subsequent non-overlapping interval, and reports metrics.
This tool never writes a model or makes bets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.train_prerace_research import train_research_model
from scripts.validate_prerace_features import aware_time


def walk_forward(bundles_dir, labels_path, *, boundaries, min_races=20):
    if not isinstance(boundaries, list) or len(boundaries) < 3:
        raise ValueError("At least three chronological boundaries required")
    parsed = [aware_time(value) for value in boundaries]
    if any(a >= b for a, b in zip(parsed, parsed[1:])):
        raise ValueError("Boundaries must be strictly increasing")
    if parsed[-1].year >= 2026:
        raise ValueError("Sealed 2026+ data is prohibited")
    folds = []
    # The first boundary is the earliest training end. For every subsequent
    # boundary, train on all earlier races and validate on the next interval.
    for i in range(len(boundaries) - 1):
        fold = train_research_model(
            bundles_dir, labels_path,
            train_before=boundaries[i],
            validate_before=boundaries[i + 1],
            min_races=min_races,
        )
        folds.append({
            "train_before": fold["train_before"],
            "validate_before": fold["validate_before"],
            "train_races": fold["train_races"],
            "validation_races": fold["validation_races"],
            "train_horses": fold["train_horses"],
            "validation_horses": fold["validation_horses"],
            "brier_score": fold["brier_score"],
            "race_normalized_brier_research_only": fold["race_normalized_brier_research_only"],
            "race_winner_nll_research_only": fold["race_winner_nll_research_only"],
            "uniform_race_winner_nll": fold["uniform_race_winner_nll"],
            "uniform_race_baseline_brier": fold["uniform_race_baseline_brier"],
            "uniform_race_baseline_log_loss": fold["uniform_race_baseline_log_loss"],
            "log_loss": fold["log_loss"],
            "roc_auc": fold["roc_auc"],
            "input_sha256": fold["input_sha256"],
        })
    total = sum(f["validation_horses"] for f in folds)
    return {
        "status": "RESEARCH_ONLY_UNCERTIFIED",
        "bet_status": "NO_BET",
        "production_inference_allowed": False,
        "asof_certified": False,
        "holdout_2026_accessed": False,
        "folds": folds,
        "total_validation_horses": total,
        "weighted_race_winner_nll_research_only": sum(f["race_winner_nll_research_only"] * f["validation_races"] for f in folds) / sum(f["validation_races"] for f in folds),
        "weighted_uniform_race_winner_nll": sum(f["uniform_race_winner_nll"] * f["validation_races"] for f in folds) / sum(f["validation_races"] for f in folds),
        "weighted_mean_brier": sum(f["brier_score"] * f["validation_horses"] for f in folds) / total,
        "weighted_mean_log_loss": sum(f["log_loss"] * f["validation_horses"] for f in folds) / total,
        "weighted_uniform_baseline_brier": sum(f["uniform_race_baseline_brier"] * f["validation_horses"] for f in folds) / total,
        "weighted_uniform_baseline_log_loss": sum(f["uniform_race_baseline_log_loss"] * f["validation_horses"] for f in folds) / total,
        "warning": "Overlapping expanding training windows; validation windows disjoint. Manual provenance not certified. No betting EV.",
    }


def main():
    parser = argparse.ArgumentParser(description="Offline chronological pre-race research folds")
    parser.add_argument("--bundles-dir", required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--boundaries", nargs="+", required=True,
                        help="ISO timezone-aware boundaries: train-end1 validate-end1 ...")
    parser.add_argument("--min-races", type=int, default=20)
    parser.add_argument("--out")
    args = parser.parse_args()
    result = walk_forward(args.bundles_dir, args.labels,
                          boundaries=args.boundaries, min_races=args.min_races)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
