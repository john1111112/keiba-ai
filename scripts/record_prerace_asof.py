"""
発走前オッズおよびAI予測の実時刻記録 CLI ツール (Pre-Race As-Of Recorder CLI)

【コマンド実行例】
1. 30分前スナップショット記録（オッズファイルあり）:
   python scripts/record_prerace_asof.py --race-id 202605010101 --race-time "2026-05-01 15:40:00" --timing pre_30m --odds-json data/snapshots/odds_30m.json --out data/reports/snapshot_receipt.json

2. オッズ取得不可（欠損扱いとして記録）:
   python scripts/record_prerace_asof.py --race-id 202605010101 --race-time "2026-05-01 15:40:00" --timing pre_10m

3. スナップショット履歴の照会:
   python scripts/record_prerace_asof.py --race-id 202605010101 --audit-query
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

# Add project root to path
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from src.prerace_recorder import (
    record_prerace_snapshot,
    query_snapshot_records,
    determine_applicable_timing,
    TARGET_TIMINGS,
    ODDS_HISTORY_DB_PATH
)
import pandas as pd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Race As-Of Odds & Prediction Recorder")
    parser.add_argument("--race-id", required=True, help="Target race ID (e.g. 202605010101)")
    parser.add_argument("--race-time", help="Scheduled race start time (ISO or YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--timing", choices=["pre_30m", "pre_10m", "pre_5m", "auto"], default="auto",
                        help="Snapshot timing target (pre_30m, pre_10m, pre_5m, or auto based on current time)")
    parser.add_argument("--odds-json", help="Path to JSON file containing odds snapshot records")
    parser.add_argument("--features", default=str(repo_root / "data" / "features.parquet"),
                        help="Path to features parquet file")
    parser.add_argument("--db", default=str(ODDS_HISTORY_DB_PATH),
                        help="Target SQLite database path (default: data/odds_history.db)")
    parser.add_argument("--out", help="Path to write execution receipt JSON")
    parser.add_argument("--audit-query", action="store_true",
                        help="Query and print existing snapshot audit records for --race-id")

    args = parser.parse_args(argv)

    # 履歴照会モード
    if args.audit_query:
        timing_filter = args.timing if args.timing in TARGET_TIMINGS else None
        res = query_snapshot_records(args.race_id, timing_tag=timing_filter, db_path=Path(args.db))
        out_json = json.dumps(res, ensure_ascii=False, indent=2)
        print(out_json)
        if args.out:
            out_p = Path(args.out)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(out_json + "\n", encoding="utf-8")
        return 0

    if not args.race_time:
        print("[ERROR] --race-time は必須です（照会モード以外）。", file=sys.stderr)
        return 1

    timing = args.timing
    if timing == "auto":
        determined = determine_applicable_timing(args.race_time)
        if not determined:
            print(f"[ERROR] 現在時刻から適用可能な発走前ウィンドウ（30分前・10分前・5分前）を特定できませんでした。", file=sys.stderr)
            return 1
        timing = determined

    # オッズデータの読み込み（指定がある場合）
    odds_records = None
    if args.odds_json:
        odds_p = Path(args.odds_json)
        if not odds_p.exists():
            print(f"[ERROR] オッズファイルが見つかりません: {args.odds_json}", file=sys.stderr)
            return 1
        with open(odds_p, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            if isinstance(raw_data, list):
                odds_records = raw_data
            elif isinstance(raw_data, dict) and "odds" in raw_data:
                odds_records = raw_data["odds"]
            else:
                odds_records = [raw_data]

    # 特徴量の読み込み
    features_df = None
    if args.features and Path(args.features).exists():
        try:
            full_df = pd.read_parquet(args.features)
            features_df = full_df[full_df["race_id"] == str(args.race_id)]
        except Exception as e:
            print(f"[WARN] 特徴量読み込み失敗 ({e})。特徴量なしで実行します。", file=sys.stderr)

    # スナップショット記録実行
    receipt = record_prerace_snapshot(
        race_id=args.race_id,
        scheduled_race_time=args.race_time,
        timing_tag=timing,
        odds_records=odds_records,
        features_df=features_df,
        db_path=Path(args.db)
    )

    out_json = json.dumps(receipt, ensure_ascii=False, indent=2)
    print(out_json)

    if args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(out_json + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
