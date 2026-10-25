"""Conservative, paper-only candidate filtering for horse-race predictions.

This module never places bets. Unverified odds cannot produce recommendations.
No production database, model, or network access is required.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True)
class SelectionPolicy:
    min_win_probability: float = 0.75
    min_expected_return: float = 0.05
    min_calibration_samples: int = 100

    def __post_init__(self) -> None:
        if not 0 < self.min_win_probability <= 1:
            raise ValueError("min_win_probability must be in (0, 1]")
        if not math.isfinite(self.min_expected_return) or self.min_expected_return < 0:
            raise ValueError("min_expected_return must be finite and nonnegative")
        if self.min_calibration_samples < 1:
            raise ValueError("min_calibration_samples must be positive")


def _probability(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0 <= value <= 1 else None


def _verified_odds(odds: Any, prediction_time: str) -> Optional[float]:
    """Only accept timestamped, provenance-marked, pre-decision decimal odds."""
    if not isinstance(odds, Mapping) or odds.get("verified") is not True:
        return None
    value = odds.get("decimal_odds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value) or value <= 1:
        return None
    if not isinstance(odds.get("source"), str) or not odds["source"].strip():
        return None
    try:
        observed = datetime.fromisoformat(odds["observed_at"])
        decision = datetime.fromisoformat(prediction_time)
        if observed.tzinfo is None or decision.tzinfo is None:
            return None
        if observed.astimezone(timezone.utc) > decision.astimezone(timezone.utc):
            return None
    except (TypeError, ValueError, KeyError):
        return None
    return value


def select_candidates(
    predictions: Sequence[Mapping[str, Any]],
    policy: SelectionPolicy = SelectionPolicy(),
) -> dict[str, Any]:
    """Evaluate every input prediction; only independently validated rows qualify.

    Each prediction requires race_id, horse_id, predicted_win_probability,
    conservative_win_probability, calibration_sample_count, prediction_time,
    and odds_snapshot. Conservative probability must not exceed point estimate.

    This is an offline research selector, NOT a wagering recommendation.
    """
    all_predictions: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for item in predictions:
        race_id = item.get("race_id")
        horse_id = item.get("horse_id")
        p = _probability(item.get("predicted_win_probability"))
        lower = _probability(item.get("conservative_win_probability"))
        sample_count = item.get("calibration_sample_count")
        prediction_time = item.get("prediction_time")
        reasons: list[str] = []

        if not race_id or not horse_id or not isinstance(prediction_time, str):
            reasons.append("MISSING_ID_OR_PREDICTION_TIME")
        if p is None or lower is None or lower > p:
            reasons.append("INVALID_OR_UNCALIBRATED_PROBABILITY")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < policy.min_calibration_samples:
            reasons.append("INSUFFICIENT_CALIBRATION_SAMPLES")
        if lower is not None and lower < policy.min_win_probability:
            reasons.append("WIN_PROBABILITY_BELOW_THRESHOLD")

        odds = _verified_odds(item.get("odds_snapshot"), prediction_time) if isinstance(prediction_time, str) else None
        expected_return = None
        if odds is None:
            reasons.append("UNVERIFIED_OR_MISSING_DECISION_TIME_ODDS")
        elif lower is not None and p is not None and lower <= p:
            expected_return = lower * odds - 1.0
            if expected_return < policy.min_expected_return:
                reasons.append("EXPECTED_RETURN_BELOW_THRESHOLD")

        record = {
            "race_id": race_id,
            "horse_id": horse_id,
            "predicted_win_probability": p,
            "conservative_win_probability": lower,
            "expected_return_lower_bound": expected_return,
            "status": "RESEARCH_CANDIDATE" if not reasons else "SKIP",
            "reasons": reasons,
        }
        all_predictions.append(record)
        if not reasons:
            candidates.append(record)

    return {
        "all_predictions": all_predictions,
        "research_candidates": candidates,
        "prediction_count": len(all_predictions),
        "candidate_count": len(candidates),
        "roi": None,
        "real_money_betting_enabled": False,
        "notice": "Research only; not evidence of profit or a recommendation to wager.",
    }
