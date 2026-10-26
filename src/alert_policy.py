"""Fail-closed alert eligibility policy for unattended pre-race monitoring.

Never send a betting recommendation without independently certified model
probabilities AND contemporaneous pre-race odds. This module has no network
or notification side effects.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


def _time(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a timezone-aware ISO string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Naive timestamps forbidden")
    return dt.astimezone(timezone.utc)


def evaluate_alert(candidate, *, now, min_ev=1.15, max_odds_age_seconds=120,
                   min_seconds_to_start=300, max_seconds_to_start=1800):
    """Return a deterministic NO_ALERT or eligible result; never send anything."""
    base = {"status": "NO_ALERT", "bet_status": "NO_BET",
            "notification_allowed": False}
    try:
        if not isinstance(candidate, dict):
            raise ValueError("Missing candidate")
        required = {"race_id", "horse_number", "race_start_time",
                    "odds_observed_at", "odds_fetched_at", "win_odds",
                    "win_probability", "odds_asof_certified",
                    "model_asof_certified", "model_calibrated",
                    "prediction_created_at", "source"}
        if not required.issubset(candidate):
            raise ValueError("Incomplete candidate")
        if not all(candidate[key] is True for key in (
                "odds_asof_certified", "model_asof_certified", "model_calibrated")):
            raise ValueError("Uncertified odds or model")
        if not isinstance(candidate["race_id"], str) or not candidate["race_id"].strip():
            raise ValueError("Invalid race")
        horse = candidate["horse_number"]
        if type(horse) is not int or not 1 <= horse <= 99:
            raise ValueError("Invalid horse")
        if not isinstance(candidate["source"], str) or candidate["source"] not in (
                "jvlink_verified", "licensed_verified"):
            raise ValueError("Unverified source")
        odds, probability = candidate["win_odds"], candidate["win_probability"]
        if (type(odds) not in (float, int) or type(probability) not in (float, int)
                or not math.isfinite(odds) or odds < 1.0
                or not math.isfinite(probability) or not 0 < probability <= 1):
            raise ValueError("Invalid odds or probability")
        current, start = _time(now), _time(candidate["race_start_time"])
        observed = _time(candidate["odds_observed_at"])
        fetched = _time(candidate["odds_fetched_at"])
        predicted = _time(candidate["prediction_created_at"])
        if not observed <= fetched <= current < start or not predicted <= current:
            raise ValueError("Future, stale, or post-race evidence")
        age = (current - fetched).total_seconds()
        if age > max_odds_age_seconds:
            raise ValueError("Stale odds")
        seconds = (start - current).total_seconds()
        if not min_seconds_to_start <= seconds <= max_seconds_to_start:
            raise ValueError("Outside pre-race alert window")
        ev = float(odds * probability)
        if ev < min_ev:
            raise ValueError("Below EV threshold")
        return {"status": "ALERT_ELIGIBLE", "bet_status": "REVIEW_ONLY",
                "notification_allowed": True,
                "race_id": candidate["race_id"],
                "horse_number": horse,
                "theoretical_ev": round(ev, 6),
                "odds_age_seconds": age,
                "seconds_to_start": seconds,
                "warning": "Candidate for manual review, not a guaranteed profitable bet"}
    except (ValueError, TypeError, OverflowError, KeyError) as exc:
        return {**base, "reason": str(exc)}
