"""Validate a provider handoff manifest without accepting it as proof of licensing.

The handoff contains no secrets. This tool does not make network calls.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

REQUIRED = ("provider_name", "contact", "license_reference", "data_format",
            "timestamp_field", "timezone", "race_id_field", "horse_id_field",
            "odds_field", "sample_path")
FORMATS = {"json", "csv"}


def audit_handoff(data, *, root=None):
    issues = []
    if not isinstance(data, dict):
        return {"status": "BLOCKED", "issues": ["HANDOFF_NOT_OBJECT"],
                "verified": False}
    for field in REQUIRED:
        if not isinstance(data.get(field), str) or not data[field].strip():
            issues.append("MISSING_" + field.upper())
    if data.get("data_format") not in FORMATS:
        issues.append("UNSUPPORTED_FORMAT")
    sample = data.get("sample_path")
    if isinstance(sample, str) and sample.strip():
        path = Path(sample)
        if path.is_absolute() or ".." in path.parts:
            issues.append("UNSAFE_SAMPLE_PATH")
        elif root is not None and not (Path(root) / path).is_file():
            issues.append("SAMPLE_NOT_FOUND")
    endpoint = data.get("endpoint")
    if endpoint is not None:
        parsed = urlparse(endpoint) if isinstance(endpoint, str) else None
        if parsed is None or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            issues.append("INVALID_ENDPOINT")
    return {
        "status": "BLOCKED" if issues else "HANDOFF_FORMAT_COMPLETE",
        "issues": sorted(set(issues)),
        "verified": False,
        "live_odds_enabled": False,
        "notes": "Format checks only. Contract, source authenticity and published timestamps require independent review."
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit licensed odds feed handoff (offline)")
    parser.add_argument("handoff_json")
    args = parser.parse_args(argv)
    path = Path(args.handoff_json)
    result = audit_handoff(json.loads(path.read_text(encoding="utf-8")), root=path.parent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "HANDOFF_FORMAT_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
