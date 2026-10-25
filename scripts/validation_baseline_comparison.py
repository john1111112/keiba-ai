"""Compare validation-selected weights to a uniform per-race baseline.

This evaluates the *same validation races used for weight selection*.
It is diagnostic only, NOT out-of-sample performance evidence.
"""
from __future__ import annotations

import math

from scripts.validation_weight_tuning import _probabilities


def compare_validation_baseline(races: list[list[dict]], weights: dict[str, float]) -> dict:
    if not races or not weights:
        raise ValueError("Races and selected weights are required")
    feature_names = tuple(weights)
    stats = {"weighted": {"log_loss_sum": 0.0, "brier_sum": 0.0, "top1_hits": 0},
             "uniform": {"log_loss_sum": 0.0, "brier_sum": 0.0, "top1_hits": 0}}
    total_runners = 0
    for race in races:
        if len(race) < 2 or sum(r.get("win") == 1 for r in race) != 1:
            raise ValueError("Each race must have exactly one winner and >=2 horses")
        if len({r.get("horse_id") for r in race}) != len(race):
            raise ValueError("Duplicate horse IDs")
        if any(set(feature_names) - set(r.get("features", {})) for r in race):
            raise ValueError("Missing feature")
        weighted = _probabilities(race, weights, feature_names)
        uniform = [1.0 / len(race)] * len(race)
        winner_index = next(i for i, r in enumerate(race) if r["win"] == 1)
        total_runners += len(race)
        for label, probs in (("weighted", weighted), ("uniform", uniform)):
            stats[label]["log_loss_sum"] -= math.log(max(probs[winner_index], 1e-15))
            stats[label]["brier_sum"] += sum(
                (p - int(i == winner_index)) ** 2 for i, p in enumerate(probs)
            )
            # Deterministic tie-breaking by input order; uniform top1 is arbitrary.
            stats[label]["top1_hits"] += int(probs.index(max(probs)) == winner_index)
    result = {}
    for label, row in stats.items():
        result[label] = {
            "race_log_loss": row["log_loss_sum"] / len(races),
            "runner_brier": row["brier_sum"] / total_runners,
            "top1_accuracy": row["top1_hits"] / len(races),
        }
    return {
        "status": "IN_SAMPLE_VALIDATION_DIAGNOSTIC_ONLY",
        "race_count": len(races),
        "runner_count": total_runners,
        "models": result,
        "market_odds_baseline": None,
        "holdout_evaluated": False,
        "roi": None,
        "warning": "Weights were selected on these same validation labels. Do not report as holdout or predictive edge.",
    }
