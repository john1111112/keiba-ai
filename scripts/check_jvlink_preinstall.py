"""JV-Link installation readiness, without starting a free trial or making COM calls."""
from __future__ import annotations
import argparse
import json
import os
import platform
import sys
from pathlib import Path


def diagnose(*, system=None, python_bits=None, sdk_dir=None):
    system = system or platform.system()
    python_bits = python_bits or (64 if sys.maxsize > 2**32 else 32)
    sdk = Path(sdk_dir) if sdk_dir else None
    checks = {
        "windows": system == "Windows",
        "python_architecture": python_bits,
        "sdk_directory_exists": bool(sdk and sdk.is_dir()),
        "sdk_directory": str(sdk) if sdk else None,
        "jvlink_installed": "NOT_CHECKED",
        "jvlink_authenticated": False,
        "live_race_data_verified": False,
    }
    return {
        "status": "PREINSTALL_READY" if checks["windows"] else "WINDOWS_REQUIRED",
        "checks": checks,
        "trial_started": False,
        "bet_status": "NO_BET",
        "next_step": "Implement and test JV-Link adapter using official SDK samples; install only after ready.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pretrial JV-Link environment audit")
    parser.add_argument("--sdk-dir", default=os.getenv("KEIBA_JVLINK_SDK_DIR"))
    args = parser.parse_args(argv)
    print(json.dumps(diagnose(sdk_dir=args.sdk_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
