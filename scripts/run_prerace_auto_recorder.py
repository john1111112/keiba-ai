"""
発走前オッズ・AI予測 自動実行・再試行・二重記録防止 統合ランナー (Pre-Race Auto Recorder Runner)

【機能】
1. 実際の発走前オッズ取得元（JV-Link / 許諾フィード / リアルタイムWeb）の接続診断 (--diagnose-providers)
   HTTP 200のみでの成功扱いを禁止。利用許諾・実オッズ・時刻情報が未確認なら未接続として報告。
2. 次回開催・直近出走予定レースの自動記録準備 & スナップショット自動実行 (--schedule-scan)
3. 同一 (race_id, timing_tag) に対する二重記録防止 (Idempotency Guard)
4. オッズ取得時の指数バックオフ付き自動再試行 (Retry Logic)
5. 実取得件数 (captured_count) と欠損件数 (missing_count) の厳密集計・レポート出力
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


def diagnose_odds_sources(race_id: Optional[str] = None) -> Dict[str, Any]:
    """オッズ取得元の接続診断を実行"""
    mgr = OddsProviderManager()
    return mgr.diagnose_all_providers(race_id=race_id)


def run_schedule_scan(
    features_path: Path,
    db_path: Path,
    as_of_time: Optional[str] = None,
    tolerance_minutes: int = 5,
    max_retries: int = 3,
    backoff_sec: float = 1.0,
    allow_duplicate: bool = False,
    all_timings: bool = False
) -> Dict[str, Any]:
    """
    明示的に race_status=upcoming と指定されたレースをスキャンし、発走30分前・10分前・5分前の
    該当レースに対して二重記録防止・再試行付きスナップショットを実行する。
    """
    features_path = Path(features_path)
    db_path = Path(db_path)
    if not features_path.exists():
        return {
            "status": "NO_FEATURES_FILE",
            "message": f"特徴量ファイルが見つかりません: {features_path}",
            "processed_races": 0,
            "captured_count": 0,
            "missing_count": 0
        }

    if features_path.suffix.lower() == ".json":
        # A race schedule is a separate, explicit input, never derived from results.
        manifest = json.loads(features_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, list) or not manifest:
            return {"status": "BLOCKED_EMPTY_MANIFEST", "processed_races": 0,
                    "captured_count": 0, "missing_count": 0, "snapshots": []}
        if any(not isinstance(entry, dict) for entry in manifest):
            raise ValueError("Every race manifest entry must be an object")
        df_feat = pd.DataFrame(manifest)
    else:
        return {"status": "BLOCKED_NOT_RACE_MANIFEST",
                "message": "Only explicit JSON upcoming-race manifests are accepted; historical feature tables are forbidden.",
                "processed_races": 0, "captured_count": 0,
                "missing_count": 0, "snapshots": []}
    # Race result fields cannot certify upcoming status; require a separate
    # explicit upcoming race manifest before scanning.
    if "race_status" not in df_feat.columns:
        return {
            "status": "BLOCKED_NO_UPCOMING_MANIFEST",
            "message": "race_status field missing; finish_order cannot prove a race is upcoming.",
            "processed_races": 0, "captured_count": 0, "missing_count": 0,
            "snapshots": []
        }
    upcoming = df_feat[df_feat["race_status"].astype(str).str.lower() == "upcoming"].copy()
    if len(upcoming) == 0:
        return {
            "status": "NO_UPCOMING_RACES",
            "message": "現在、未確定（出走前）の対象レースはありません。",
            "processed_races": 0,
            "captured_count": 0,
            "missing_count": 0,
            "snapshots": []
        }

    now_dt = datetime.datetime.fromisoformat(as_of_time) if as_of_time else datetime.datetime.now(datetime.timezone.utc)
    if now_dt.tzinfo is None or now_dt.utcoffset() is None:
        raise ValueError("--as-of-time must include an explicit timezone")
    if "race_id" not in upcoming.columns:
        raise ValueError("Upcoming manifest requires race_id")
    if upcoming["race_id"].isna().any() or (upcoming["race_id"].astype(str).str.strip() == "").any():
        raise ValueError("Upcoming manifest contains empty race_id")
    # One scheduled start per race; conflicting rows must not choose an
    # arbitrary first row and accidentally capture the wrong race.
    for race_id, group in upcoming.groupby("race_id"):
        starts = set()
        for _, row in group.iterrows():
            raw = row.get("race_time")
            if pd.isna(raw) or not raw:
                raw = row.get("start_time")
            if pd.notna(raw) and raw:
                starts.add(str(raw))
        if len(starts) > 1:
            raise ValueError(f"Conflicting start times for race {race_id}")
    races_grouped = upcoming.groupby("race_id")
    results = []

    provider_mgr = OddsProviderManager()

    for race_id, group in races_grouped:
        first_row = group.iloc[0]
        race_time_str = first_row.get("race_time")
        if pd.isna(race_time_str) or not race_time_str:
            race_time_str = first_row.get("start_time")
        if pd.isna(race_time_str) or not race_time_str:
            results.append({"race_id": str(race_id), "status": "SKIPPED_MISSING_RACE_TIME", "odds_status": "missing", "message": "Race start time is unavailable; never invent a start time."})
            continue

        try:
            parsed_start = datetime.datetime.fromisoformat(str(race_time_str).replace("Z", "+00:00"))
            if parsed_start.tzinfo is None or parsed_start.utcoffset() is None or parsed_start <= now_dt:
                raise ValueError("missing timezone or already started")
        except (ValueError, TypeError):
            results.append({"race_id": str(race_id), "status": "SKIPPED_INVALID_RACE_TIME", "odds_status": "missing", "message": "Invalid, timezone-naive, or elapsed race start time."})
            continue

        # all_timings 指定時は pre_30m, pre_10m, pre_5m すべてを順次検証
        timings_to_run = list(TARGET_TIMINGS) if all_timings else []
        if not all_timings:
            applicable = determine_applicable_timing(
                race_time_str,
                as_of_time=now_dt.isoformat(),
                tolerance_minutes=tolerance_minutes
            )
            if applicable:
                timings_to_run.append(applicable)

        for timing in timings_to_run:
            offset = {"pre_30m": 30, "pre_10m": 10, "pre_5m": 5}[timing]
            target = parsed_start - datetime.timedelta(minutes=offset)
            if now_dt > target + datetime.timedelta(minutes=tolerance_minutes):
                results.append({"race_id": str(race_id), "timing_tag": timing,
                                "status": "SKIPPED_STALE_TARGET", "odds_status": "missing",
                                "message": "Target snapshot window has expired; do not backfill a false T-minus record."})
                continue
            if now_dt < target:
                results.append({"race_id": str(race_id), "timing_tag": timing,
                                "status": "SKIPPED_FUTURE_TARGET", "odds_status": "missing",
                                "message": "Target snapshot time has not arrived."})
                continue
            res = record_prerace_snapshot(
                race_id=str(race_id),
                scheduled_race_time=race_time_str,
                timing_tag=timing,
                features_df=None,  # Feature provenance not certified; never infer from result tables.
                executed_at=now_dt.isoformat(),
                db_path=db_path,
                allow_duplicate=allow_duplicate,
                provider_manager=provider_mgr,
                max_retries=max_retries,
                backoff_sec=backoff_sec
            )
            results.append(res)

    # 実取得件数と欠損件数の厳密集計
    captured_count = sum(1 for r in results if r.get("odds_status") == "captured")
    missing_count = sum(1 for r in results if r.get("odds_status") in ("missing", "rejected_post_race", "error"))
    skipped_duplicate_count = sum(1 for r in results if r.get("is_duplicate_prevented") or r.get("status") == "SKIPPED_ALREADY_RECORDED")

    return {
        "status": "COMPLETED",
        "scanned_races_count": len(races_grouped),
        "total_targets_evaluated": len(results),
        "captured_count": captured_count,
        "missing_count": missing_count,
        "skipped_duplicate_count": skipped_duplicate_count,
        "as_of_time": now_dt.isoformat(),
        "provider_diagnostics": provider_mgr.diagnose_all_providers(),
        "snapshots": results
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-Race Odds & Prediction Auto Recorder Runner")
    parser.add_argument("--diagnose-providers", action="store_true",
                        help="Check connectivity and readiness of all odds sources")
    parser.add_argument("--schedule-scan", action="store_true",
                        help="Scan upcoming races and automatically record applicable pre-race snapshots")
    parser.add_argument("--all-timings", action="store_true",
                        help="In schedule scan mode, evaluate all 3 timings (pre_30m, pre_10m, pre_5m) for all upcoming races")
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
        report = diagnose_odds_sources(race_id=args.race_id)
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
            allow_duplicate=args.allow_duplicate,
            all_timings=args.all_timings
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
