"""Strictly validate a licensed odds feed handoff without asserting certification.

The handoff is NOT wired to the trusted provider or alert pipeline.
Its purpose is to make a concrete, testable integration contract.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = {"race_id", "race_start_time", "source", "observed_at",
            "fetched_at", "odds", "license_reference"}


def validate_feed_handoff(payload, *, now=None):
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("Current time must have timezone")
    if not isinstance(payload, dict) or set(payload) != REQUIRED:
        raise ValueError("Exact licensed feed handoff fields required")
    if not isinstance(payload["race_id"], str) or not payload["race_id"].strip():
        raise ValueError("race_id is required")
    for field in ("source", "license_reference"):
        if not isinstance(payload[field], str) or not payload[field].strip():
            raise ValueError(f"{field} is required")
    parsed = {}
    for field in ("race_start_time", "observed_at", "fetched_at"):
        raw = payload[field]
        if not isinstance(raw, str):
            raise ValueError(f"{field} must be a timestamp")
        try:
            parsed[field] = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} invalid") from exc
        if parsed[field].tzinfo is None or parsed[field].utcoffset() is None:
            raise ValueError(f"{field} must include timezone")
    if not parsed["observed_at"] <= parsed["fetched_at"] <= current < parsed["race_start_time"]:
        raise ValueError("Odds must be observed and received before the race")
    if (current - parsed["fetched_at"]).total_seconds() > 600:
        raise ValueError("Odds receipt is stale")
    odds = payload["odds"]
    if not isinstance(odds, list) or not odds or len(odds) > 30:
        raise ValueError("Expected 1..30 win odds rows")
    seen = set()
    for row in odds:
        if not isinstance(row, dict) or set(row) != {"horse_number", "win_odds"}:
            raise ValueError("Exact win odds fields required")
        number, price = row["horse_number"], row["win_odds"]
        if type(number) is not int or not 1 <= number <= 30 or number in seen:
            raise ValueError("Invalid or duplicate horse_number")
        if type(price) not in (int, float) or not math.isfinite(price) or price < 1:
            raise ValueError("Invalid win odds")
        seen.add(number)
    return {"status": "SCHEMA_VALID_ONLY", "race_id": payload["race_id"],
            "horses": len(odds), "license_verified": False,
            "publication_time_verified": False, "provider_connected": False,
            "model_certified": False, "bet_status": "NO_BET"}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate licensed odds handoff schema")
    parser.add_argument("file")
    args = parser.parse_args(argv)
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    print(json.dumps(validate_feed_handoff(payload), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
