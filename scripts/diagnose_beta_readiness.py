"""Offline readiness audit for a licensed pre-race odds integration.

Never reads credentials, calls external services, or certifies a provider.
Produces actionable blockers for Windows beta setup.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.odds_provider import OddsProviderManager


def readiness_report(*, manifest="data/upcoming_races.json", odds_db="data/odds_history.db"):
    providers = OddsProviderManager().diagnose_all_providers()
    blockers = []
    if not Path(manifest).is_file():
        blockers.append("MISSING_UPCOMING_RACES")
    if providers["all_sources_disconnected"]:
        blockers.append("NO_VERIFIED_LIVE_ODDS_PROVIDER")
    # A local database is historical storage, never proof of a live provider.
    if not Path(odds_db).is_file():
        blockers.append("NO_LOCAL_ODDS_HISTORY")
    blockers.append("NO_CERTIFIED_PRODUCTION_MODEL")
    blockers.append("NO_TRUSTED_PROVENANCE_TO_ALERT_BRIDGE")
    return {
        "status": "BLOCKED" if blockers else "READY",
        "bet_status": "NO_BET",
        "notifications_enabled": False,
        "provider_status": providers["status"],
        "blockers": blockers,
        "next_actions": [
            "Obtain a licensed pre-race odds source and connection instructions.",
            "Implement and verify provider identity and published timestamps.",
            "Collect independent pre-race features and labels before the sealed 2026 holdout.",
            "Calibrate and validate model on chronological non-holdout data.",
            "Integrate provenance-verified predictions before enabling notifications."
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline pre-race integration readiness")
    parser.add_argument("--manifest", default="data/upcoming_races.json")
    parser.add_argument("--db", default="data/odds_history.db")
    args = parser.parse_args(argv)
    print(json.dumps(readiness_report(manifest=args.manifest, odds_db=args.db),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
