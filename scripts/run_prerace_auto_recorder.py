"""
発走前オッズ・AI予測 自動実行・再試行・二重記録防止 統合ランナー (Pre-Race Auto Recorder Runner)

【機能】
1. 実際の発走前オッズ取得元（JV-Link / 許諾フィード / リアルタイム）の疎通・認可状態の診断 (--diagnose-providers)
   未接続の場合は「未接続」と明示報告し、架空データで成功扱いしない。
2. 直近出走予定レースの自動スキャン & 30分前・10分前・5分前スナップショット自動実行 (--schedule-scan)
3. 同一 (race_id, timing_tag) に対する二重記録防止 (Idempotency Guard)
4. オッズ取得時の指数バックオフ付き自動再試行 (Retry Logic)
5. 既存DB (data/keiba.db) は一切変更せず、独立DB (data/odds_history.db) のみ更新
"""
from __future__ import annotations
import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

# プロジェクトルートを追加
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from src.prerace_recorder import (
    record_prerace_snapshot,
    query_snapshot_records,
    determine_applicable_timing,
    TARGET_TIMINGS,
    ODDS_HISTORY_DB_PATH
)
from src.odds_provider import OddsProviderManager
import pandas as pd


def diagnose_odds_sources() -> Dict[str, Any]:
    """オッズ取得元の接続診断を実行"""
    mgr = OddsProviderManager()
    return mgr.diagnose_all_providers()


def run_schedule_scan(
    features_path: Path,
    db_path: Path,
    as_of_time: Optional[str] = None,
    tolerance_minutes: int = 5,
    max_retries: int = 3,
    backoff_sec: float = 1.0,
    allow_duplicate: bool = False
) -> Dict[str, Any]:
    """
    出走予定レース（finish_order == 0）をスキャンし、発走30分前・10分前・5分前の
    該当レースに対して二重記録防止・再試行付きスナップショットを実行する。
    """
    if not features_path.exists():
        return {
            "status": "NO_FEATURES_FILE",
            "message": f"特徴量ファイルが見つかりません: {features_path}",
            "processed_races": 0
        }

    df_feat = pd.read_parquet(features_path)
    # 未確定・出走前レースを抽出
    upcoming = df_feat[df_feat["finish_order"].fillna(0) == 0].copy()
    if len(upcoming) == 0:
        return {
            "status": "NO_UPCOMING_RACES",
            "message": "現在、未確定（出走前）の対象レースはありません。",
            "processed_races": 0,
            "target_snapshots": []
        }

    now_dt = datetime.datetime.fromisoformat(as_of_time) if as_of_time else datetime.datetime.now()
    races_grouped = upcoming.groupby("race_id")
    results = []

    provider_mgr = OddsProviderManager()

    for race_id, group in races_grouped:
        first_row = group.iloc[0]
        date_val = str(first_row.get("date", ""))[:10]
        # レース発走時刻（dateカラムやカスタムカラムから推測、または標準設定）
        race_time_str = first_row.get("race_time") or first_row.get("start_time")
        if not race_time_str:
            # 時刻情報がない場合はデフォルト仮定（例: 15:40:00）または日付結合
            race_time_str = f"{date_val} 15:40:00"

        # 30分前・10分前・5分前それぞれのタイミングをチェック
        applicable_timing = determine_applicable_timing(
            race_time_str,
            as_of_time=now_dt.isoformat(),
            tolerance_minutes=tolerance_minutes
        )

        if applicable_timing:
            res = record_prerace_snapshot(
                race_id=str(race_id),
                scheduled_race_time=race_time_str,
                timing_tag=applicable_timing,
                features_df=group,
                executed_at=now_dt.isoformat(),
                db_path=db_path,
                allow_duplicate=allow_duplicate,
                provider_manager=provider_mgr,
                max_retries=max_retries,
                backoff_sec=backoff_sec
            )
            results.append(res)

    return {
        "status": "COMPLETED",
        "scanned_races_count": len(races_grouped),
        "executed_snapshots_count": len(results),
        "as_of_time": now_dt.isoformat(),
        "snapshots": results
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Race Odds & Prediction Auto Recorder Runner")
    parser.add_argument("--diagnose-providers", action="store_true",
                        help="Check connectivity and readiness of all odds sources")
    parser.add_argument("--schedule-scan", action="store_true",
                        help="Scan upcoming races and automatically record applicable pre-race snapshots")
    parser.add_argument("--race-id", help="Target race ID for single execution")
    parser.add_argument("--race-time", help="Scheduled race start time (ISO or YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--timing", choices=["pre_30m", "pre_10m", "pre_5m", "auto"], default="auto")
    parser.add_argument("--odds-json", help="Path to explicit odds snapshot JSON")
    parser.add_argument("--features", default=str(repo_root / "data" / "features.parquet"))
    parser.add_argument("--db", default=str(ODDS_HISTORY_DB_PATH))
    parser.add_argument("--as-of-time", help="Simulated current time for scan testing (ISO)")
    parser.add_argument("--max-retries", type=int, default=3, help="Max retry attempts for odds fetch")
    parser.add_argument("--backoff-sec", type=float, default=1.0, help="Initial backoff seconds")
    parser.add_argument("--allow-duplicate", action="store_true", help="Bypass idempotency duplicate check")
    parser.add_argument("--out", help="Path to write JSON report")

    args = parser.parse_args(argv)

    # 1. プロバイダー診断モード
    if args.diagnose_providers:
        report = diagnose_odds_sources()
        out_json = json.dumps(report, ensure_ascii=False, indent=2)
        print(out_json)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(out_json + "\n", encoding="utf-8")
        return 0

    # 2. スケジュール自動スキャンモード
    if args.schedule_scan:
        report = run_schedule_scan(
            features_path=Path(args.features),
            db_path=Path(args.db),
            as_of_time=args.as_of_time,
            max_retries=args.max_retries,
            backoff_sec=args.backoff_sec,
            allow_duplicate=args.allow_duplicate
        )
        out_json = json.dumps(report, ensure_ascii=False, indent=2)
        print(out_json)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(out_json + "\n", encoding="utf-8")
        return 0

    # 3. 単一レース記録モード
    if not args.race_id or not args.race_time:
        print("[ERROR] --diagnose-providers, --schedule-scan, または (--race-id かつ --race-time) を指定してください。", file=sys.stderr)
        return 1

    timing = args.timing
    if timing == "auto":
        timing = determine_applicable_timing(args.race_time, as_of_time=args.as_of_time)
        if not timing:
            print("[ERROR] 指定時刻から適切な発走前ウィンドウ(30m/10m/5m)を特定できませんでした。", file=sys.stderr)
            return 1

    odds_records = None
    if args.odds_json and Path(args.odds_json).exists():
        with open(args.odds_json, "r", encoding="utf-8") as f:
            data = json.load(f)
            odds_records = data.get("odds") if isinstance(data, dict) and "odds" in data else (data if isinstance(data, list) else None)

    features_df = None
    if args.features and Path(args.features).exists():
        try:
            full_df = pd.read_parquet(args.features)
            features_df = full_df[full_df["race_id"] == str(args.race_id)]
        except Exception:
            pass

    mgr = OddsProviderManager()
    res = record_prerace_snapshot(
        race_id=args.race_id,
        scheduled_race_time=args.race_time,
        timing_tag=timing,
        odds_records=odds_records,
        features_df=features_df,
        executed_at=args.as_of_time,
        db_path=Path(args.db),
        allow_duplicate=args.allow_duplicate,
        provider_manager=mgr,
        max_retries=args.max_retries,
        backoff_sec=args.backoff_sec
    )

    out_json = json.dumps(res, ensure_ascii=False, indent=2)
    print(out_json)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(out_json + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
