"""Read-only production readiness gate for pre-race odds and unattended alerts.

Usage: python -m scripts.check_live_readiness
Exit 2 until provider, provenance, and model verification are complete.
This gate never claims that a provider's self-reported status is independent
proof of licensing, provenance, or production model calibration.
"""
from __future__ import annotations

import json
from src.odds_provider import OddsProviderManager


def main() -> int:
    diagnostics = OddsProviderManager().diagnose_all_providers()
    connected = [p for p in diagnostics.get("details", []) if p.get("connected") is True]
    report = {
        "ready_for_live_odds": False,
        "provider_reports_connected": bool(connected),
        "ready_for_betting_recommendations": False,
        "ready_for_unattended_delivery": False,
        "connected_provider_names": [p.get("provider") for p in connected],
        "blockers": (
            ["No provider reports a connection."]
            if not connected else []
        ) + [
            "Production model calibration and independent timestamp/license audit are not certified.",
            "Unattended delivery is fail-closed until independent provenance verification is implemented.",
        ],
        "policy": "NO BET / NO LIVE ALERTS until all independent checks pass",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
