"""Enable personal unattended notifications after explicit local installation.

This never registers a Windows service, makes wagers, or starts a subscription.
The provider provenance check and per-race odds freshness guard still apply.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def enable_monitor_config(config_path=None):
    path = Path(config_path or REPO_ROOT / "data" / "alert_config.json")
    config = {}
    if path.is_file():
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("Alert config must be a JSON object")
    # Only the explicit installer turns on background notifications.
    # Existing EV, probability, and deadline thresholds are retained.
    config.update({"discord_enabled": True, "safety_lock_enabled": False})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return config


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", help="Override the alert config location")
    args = parser.parse_args(argv)
    result = enable_monitor_config(args.config)
    print("Automatic Discord candidates enabled; provider and odds validation still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
