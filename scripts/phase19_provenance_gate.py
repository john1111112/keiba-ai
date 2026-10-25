"""Fail closed when Phase19's fabricated timestamps are mistaken for evidence.

This is an audit gate, not a substitute for independent timestamp provenance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


PLACEHOLDER_KEYS = ("race_start_at", "feature_observed_at", "cutoff_at", "prediction_at")


def assess_phase19_provenance(source: str, *, verified_external_manifest: dict | None = None) -> dict:
    signatures = (
        'f"{r_date}T10:00:00Z"',
        'f"{r_date}T09:00:00Z"',
        'f"{r_date}T09:55:00Z"',
        'f"{r_date}T09:50:00Z"',
    )
    found = [sig for sig in signatures if sig in source]
    if found:
        return {
            "status": "BLOCKED_UNVERIFIED_RACE_TIME",
            "final_holdout_eligible": False,
            "reason": "Phase19 contains generated race-time/cutoff placeholders; do not treat them as timestamp provenance.",
            "placeholder_signatures_found": len(found),
            "roi": None,
        }
    # Absence of hardcoded signatures is not proof of authentic timestamps.
    return {
        "status": "NOT_EVALUABLE_PROVENANCE_NOT_ATTESTED",
        "final_holdout_eligible": False,
        "reason": "No independently verified per-race source timestamps have been established.",
        "placeholder_signatures_found": 0,
        "roi": None,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--phase19", default="scripts/phase19_realdata_validation.py")
    p.add_argument("--output", default=None)
    args = p.parse_args()
    result = assess_phase19_provenance(Path(args.phase19).read_text(encoding="utf-8"))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    raise SystemExit(2 if not result["final_holdout_eligible"] else 0)


if __name__ == "__main__":
    main()
