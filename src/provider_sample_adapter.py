"""Read-only adapter for validating pre-race odds JSON from a contracted provider.

No network access, licensing assertion, DB writes or alert delivery.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.import_manual_odds import validate_snapshot


def inspect_provider_sample(sample_path):
    path = Path(sample_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Provider sample must be an object")
    # Reuse strict race, timestamps, source and per-horse odds validation.
    rows = validate_snapshot(payload)
    return {
        "status": "SAMPLE_SCHEMA_VALID",
        "race_id": payload["race_id"],
        "horse_count": len(rows),
        "independently_certified": False,
        "eligible_for_live_alerts": False,
        "message": "Schema validation is not independent proof of source, license or publication time.",
    }
