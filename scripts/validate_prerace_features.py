"""Fail-closed contract for externally supplied *pre-race* model features.

This does NOT certify a model or enable predictions. It validates a candidate
feature export without ever reading historical result rows, final odds, or
post-start data. Every feature must have a documented pre-race origin.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

# Versioned explicit allowlist: features whose definitions can be measured
# before the start. Historical aggregate values require independently proven
# training cutoffs; these fields are intentionally excluded here.
ALLOWED = frozenset({
    "bracket_number", "horse_number", "age", "weight_carry", "distance",
    "race_course", "surface", "condition", "sex",
})
CATEGORICAL = frozenset({"race_course", "surface", "condition", "sex"})
PROHIBITED = frozenset({
    "finish_order", "target", "odds", "popularity", "time_sec",
    "time_str", "last_3f", "margin_str", "corner_4_pos",
    "payout", "result", "final_odds", "win_probability",
})


def aware_time(value):
    if not isinstance(value, str):
        raise ValueError("Timestamp must be an ISO 8601 string")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def validate_feature_bundle(payload, *, as_of_time):
    """Return a reproducible audit, not a model-ready certification."""
    if not isinstance(payload, dict):
        raise ValueError("Feature bundle must be an object")
    if set(payload) != {"race_id", "race_start_time", "features", "source", "captured_at", "received_at"}:
        raise ValueError("Feature bundle must use the exact version-1 schema")
    race = payload["race_id"]
    if not isinstance(race, str) or not race.strip():
        raise ValueError("race_id is required")
    source = payload["source"]
    if not isinstance(source, str) or source.strip().lower() in {"", "unknown", "none"}:
        raise ValueError("Explicit feature source required")
    cutoff = aware_time(as_of_time)
    start = aware_time(payload["race_start_time"])
    captured = aware_time(payload["captured_at"])
    received = aware_time(payload["received_at"])
    if not captured <= received <= cutoff < start:
        raise ValueError("Features must be captured and received before as-of and race start")
    features = payload["features"]
    if not isinstance(features, list) or not features:
        raise ValueError("Nonempty list of horse features required")
    seen = set()
    for row in features:
        if not isinstance(row, dict):
            raise ValueError("Each horse feature must be an object")
        keys = set(row)
        if keys & PROHIBITED:
            raise ValueError("Post-race, odds, or prediction columns are prohibited")
        if keys != ALLOWED:
            raise ValueError("Only exact approved feature columns accepted")
        horse = row["horse_number"]
        bracket = row["bracket_number"]
        if type(horse) is not int or not 1 <= horse <= 99 or horse in seen:
            raise ValueError("Horse numbers must be unique integers 1..99")
        seen.add(horse)
        if type(bracket) is not int or not 1 <= bracket <= 8:
            raise ValueError("Invalid bracket")
        age = row["age"]
        if type(age) is not int or not 2 <= age <= 30:
            raise ValueError("Invalid age")
        for field, lower, upper in (("weight_carry", 20, 90), ("distance", 800, 5000)):
            val = row[field]
            if type(val) not in (int, float) or not math.isfinite(val) or not lower <= val <= upper:
                raise ValueError("Invalid " + field)
        for field in CATEGORICAL:
            val = row[field]
            if not isinstance(val, str) or not val.strip() or val.strip().lower() in {"unknown", "none", "nan"}:
                raise ValueError("Invalid " + field)
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "status": "CANDIDATE_FEATURES_VALIDATED",
        "race_id": race,
        "as_of_time": cutoff.isoformat(),
        "horse_count": len(features),
        "feature_version": "prerace_raw_v1",
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "model_inference_allowed": False,
        "asof_certified": False,
        "bet_status": "NO_BET",
        "reason": "Input structure checked; independent source provenance and model feature compatibility not certified",
    }


def main():
    parser = argparse.ArgumentParser(description="Audit external pre-race feature bundle (no inference)")
    parser.add_argument("bundle")
    parser.add_argument("--as-of-time", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    data = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    report = validate_feature_bundle(data, as_of_time=args.as_of_time)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
