"""Fail-closed provenance gate for all user-facing KEIBA AI predictions and training.

Old data/keiba.db is permanently quarantined. This module deliberately does NOT
"verify" an archive just because its IDs look like JRA, its timestamps look plausible,
or it carries a self-declared source tag. A separate JV-Link source-verifying importer
and independent snapshot reconciliation are REQUIRED before removing the block.
Never provide an environment-variable bypass.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
QUARANTINED_DB = ROOT / "data" / "keiba.db"

class UntrustedRaceDataError(RuntimeError):
    """Raised when requested operation may read fabricated / unproven racing data."""

def inspect_source_readiness(db_path: Optional[Path] = None) -> dict:
    """Read-only inventory. Structural plausibility NEVER means source authenticity."""
    path = Path(db_path or QUARANTINED_DB).resolve()
    result = {
        "status": "BLOCKED_UNVERIFIED_SOURCE",
        "db_path": str(path),
        "quarantined_legacy_db": path == QUARANTINED_DB.resolve(),
        "races": 0,
        "runner_rows": 0,
        "race_source_evidence_rows": 0,
        "runner_source_evidence_rows": 0,
        "verified_for_training": False,
        "verified_for_predictions": False,
        "reason": "No independently checked official pre-race source and outcome evidence",
    }
    if not path.exists():
        result["reason"] = "Database file not present; cannot predict using fabricated placeholders"
        return result
    try:
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as c:
            tables = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table,key in [
                ("races","races"),
                ("race_results","runner_rows"),
                ("race_source_evidence","race_source_evidence_rows"),
                ("runner_source_evidence","runner_source_evidence_rows"),
            ]:
                if table in tables:
                    result[key] = int(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if not {"races", "race_results"}.issubset(tables):
                result["reason"] = "Required race and runner tables missing"
            elif result["quarantined_legacy_db"]:
                result["reason"] = (
                    "Legacy database contains known fabricated/default attributes, "
                    "unlinked payouts, and lacks verified capture-time records"
                )
            elif "race_source_evidence" not in tables or "runner_source_evidence" not in tables:
                result["reason"] = (
                    "Per-race/per-runner evidence and source timestamps missing"
                )
            else:
                result["reason"] = (
                    "Evidence rows exist but independent source-to-record attestation "
                    "is not yet implemented; no self-certification allowed"
                )
    except sqlite3.DatabaseError:
        result["reason"] = "SQLite unreadable or corrupt"
    return result

def require_verified_race_data(
    db_path: Optional[Path] = None,
    purpose: str = "prediction",
) -> None:
    """Never approve legacy/fake data. Require future source-verifier implementation.

    Intended as a hard release gate, not a training-data-quality heuristic.
    """
    if purpose not in {"training", "prediction", "backtest", "alert"}:
        raise ValueError("Unrecognized sensitive race-data operation")
    report = inspect_source_readiness(db_path)
    if not report["verified_for_training"] or not report["verified_for_predictions"]:
        raise UntrustedRaceDataError(
            "KEIBA_REAL_DATA_GATE_BLOCKED "
            f"purpose={purpose}: {report['reason']}. "
            "No fake or unverified race data may be used to claim live accuracy."
        )

def main():
    report = inspect_source_readiness()
    print("KEIBA_SOURCE_GATE=" + json.dumps(report, ensure_ascii=False))
    if report["verified_for_training"] and report["verified_for_predictions"]:
        return 0
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
