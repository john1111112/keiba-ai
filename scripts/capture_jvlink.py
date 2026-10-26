"""Explicit JV-Link capture command. This invokes SDK and may start trial usage."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from src.jvlink_ingest import capture
from src.jvlink_raw_store import open_jvlink


def main(argv=None):
    parser = argparse.ArgumentParser(description="Capture licensed JV-Link records")
    parser.add_argument("--sid", required=True)
    parser.add_argument("--spec", choices=["0B15", "0B30", "0B41"], required=True)
    parser.add_argument("--from-time", required=True, help="YYYYMMDDHHMM")
    parser.add_argument("--db", default="data/jvlink_raw.sqlite3")
    parser.add_argument("--confirm-trial-active", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_trial_active:
        parser.error("JVInit may activate SDK usage: pass --confirm-trial-active only after deliberately starting trial")
    result = capture(open_jvlink(), Path(args.db), sid=args.sid,
                     data_spec=args.spec, from_time=args.from_time)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
