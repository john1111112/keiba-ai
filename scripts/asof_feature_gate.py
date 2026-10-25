"""Strict structural as-of checks for historical pre-race feature records.

A passing check establishes chronology, NOT that timestamps or sources are
authentic. Independently verified provider evidence remains mandatory.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math


PROHIBITED_FEATURES = frozenset({
    "finish_order", "win", "winner", "time_sec", "time_str",
    "margin_str", "last_3f", "corner_4_pos", "running_style",
    "final_odds", "payout", "popularity", "odds",
})


def parse_aware_timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Timestamp must be a nonempty ISO-8601 string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Naive timestamp is prohibited")
    return result.astimezone(timezone.utc)


def validate_asof_race(runners: list[dict], *, allow_same_time: bool = True) -> dict:
    """Reject missing provenance, time leakage, and post-race feature names.

    Each runner requires a prediction timestamp, authentic race start timestamp
    (source IDs and externally verifiable evidence required by caller), and
    per-feature observed timestamps. This function cannot certify source truth.
    """
    if not isinstance(runners, list) or len(runners) < 2:
        raise ValueError("At least two runners required")
    seen = set()
    race_ids = set()
    prediction_times = set()
    race_start_times = set()
    for row in runners:
        if not isinstance(row, dict):
            raise ValueError("Runner must be a dict")
        rid, hid = row.get("race_id"), row.get("horse_id")
        if not rid or not hid or (rid, hid) in seen:
            raise ValueError("Missing/duplicate runner identity")
        seen.add((rid, hid))
        race_ids.add(rid)
        prediction = parse_aware_timestamp(row.get("prediction_at"))
        start = parse_aware_timestamp(row.get("race_start_at"))
        if not prediction < start:
            raise ValueError("Prediction must precede start")
        prediction_times.add(prediction)
        race_start_times.add(start)
        features = row.get("features")
        timestamps = row.get("feature_observed_at")
        sources = row.get("feature_source_ids")
        if not isinstance(features, dict) or not features:
            raise ValueError("Features required")
        if not isinstance(timestamps, dict) or set(timestamps) != set(features):
            raise ValueError("Every feature needs a corresponding observation timestamp")
        if not isinstance(sources, dict) or set(sources) != set(features):
            raise ValueError("Every feature needs a source ID")
        if PROHIBITED_FEATURES.intersection(features):
            raise ValueError("Prohibited outcome/unsupported market feature")
        for name, value in features.items():
            if not isinstance(name, str) or not name:
                raise ValueError("Invalid feature name")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Features must be finite numeric values")
            if not isinstance(sources[name], str) or not sources[name].strip():
                raise ValueError("Missing source ID")
            observed = parse_aware_timestamp(timestamps[name])
            if observed > prediction or (not allow_same_time and observed == prediction):
                raise ValueError("Feature was observed after prediction")
    if len(race_ids) != 1 or len(prediction_times) != 1 or len(race_start_times) != 1:
        raise ValueError("Runners must share race ID, prediction time and start time")
    return {
        "status": "STRUCTURAL_ASOF_PASS_PROVENANCE_UNVERIFIED",
        "race_id": next(iter(race_ids)),
        "runner_count": len(runners),
        "independent_source_verified": False,
        "holdout_eligible": False,
        "roi": None,
    }
