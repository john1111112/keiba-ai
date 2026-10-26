"""
発走前オッズおよびAI予測の実時刻付き保存基盤モジュール (Pre-Race As-Of Recorder)

【基本原則・制約】
1. 発走30分前(pre_30m)・10分前(pre_10m)・5分前(pre_5m)のタイムポイントを厳格管理
2. 取得できない場合や取得元未接続時は確実に「欠損扱い」(odds_status='missing')とし、架空データ・ダミー値は生成禁止
3. レース確定オッズ(final_settled)や発走時刻以降のオッズは完全遮断・代用禁止
4. 既存DB (data/keiba.db) は一切変更せず、独立DB (data/odds_history.db) のみへ追記保存
5. 提供元発表時刻(observed_at)、システム受信時刻(fetched_at)、AI予測実行時刻(predicted_at)の実時刻を厳密記録
6. 二重記録防止（Idempotency Guard）およびプロバイダー再試行（Retry Logic）を標準装備
"""
from __future__ import annotations
import datetime
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
import pandas as pd
import numpy as np

from .odds_history import (
    init_odds_history_db,
    record_odds_snapshot,
    get_latest_odds_as_of,
    ODDS_HISTORY_DB_PATH,
    PROHIBITED_STATUSES
)
from .market import compute_market_probabilities_array
from .features import CATEGORICAL_COLS
from .odds_provider import OddsProviderManager

TARGET_TIMINGS = ("pre_30m", "pre_10m", "pre_5m")
TIMING_OFFSETS_MINUTES = {
    "pre_30m": 30,
    "pre_10m": 10,
    "pre_5m": 5,
}


class PreRaceRecordingError(RuntimeError):
    """発走前オッズ・予測保存時の基本例外"""
    pass


class PostRaceOddsLeakageError(PreRaceRecordingError):
    """確定オッズまたは発走後オッズの混入検知例外"""
    pass


def parse_timestamp(ts: Union[str, datetime.datetime]) -> datetime.datetime:
    """タイムスタンプ文字列またはdatetimeをdatetimeオブジェクトに正規化"""
    if isinstance(ts, datetime.datetime):
        return ts
    s = str(ts).strip()
    if " " in s and "T" not in s:
        s = s.replace(" ", "T")
    return datetime.datetime.fromisoformat(s)


def format_iso(dt: datetime.datetime) -> str:
    """ISO 8601 文字列形式に変換"""
    return dt.isoformat()


def compute_target_snapshot_time(
    scheduled_race_time: Union[str, datetime.datetime],
    timing_tag: str
) -> datetime.datetime:
    """
    指定されたタイミング（pre_30m, pre_10m, pre_5m）の発走前目標時刻を計算する。
    """
    if timing_tag not in TIMING_OFFSETS_MINUTES:
        raise ValueError(f"無効なtiming_tagです: {timing_tag} (指定可能: {list(TIMING_OFFSETS_MINUTES.keys())})")
    race_dt = parse_timestamp(scheduled_race_time)
    offset_min = TIMING_OFFSETS_MINUTES[timing_tag]
    return race_dt - datetime.timedelta(minutes=offset_min)


def determine_applicable_timing(
    scheduled_race_time: Union[str, datetime.datetime],
    as_of_time: Optional[Union[str, datetime.datetime]] = None,
    tolerance_minutes: int = 5
) -> Optional[str]:
    """
    現在時刻または指定時刻が発走30分前・10分前・5分前のどのウィンドウに該当するかを判定する。
    差分が最小かつ許容誤差内の目標タイミングを選択する。
    """
    race_dt = parse_timestamp(scheduled_race_time)
    current_dt = parse_timestamp(as_of_time) if as_of_time is not None else datetime.datetime.now(race_dt.tzinfo)

    time_to_race = (race_dt - current_dt).total_seconds() / 60.0

    best_timing = None
    min_diff = float("inf")
    for tag, offset in TIMING_OFFSETS_MINUTES.items():
        diff = abs(time_to_race - offset)
        if diff <= tolerance_minutes and diff < min_diff:
            min_diff = diff
            best_timing = tag
    return best_timing


def validate_odds_records(
    odds_records: List[Dict[str, Any]],
    scheduled_race_time: Union[str, datetime.datetime],
    target_cutoff_time: datetime.datetime
) -> List[Dict[str, Any]]:
    """
    【厳密検証】発走前オッズレコードの安全性・時系列因果関係を検証する。
    - 確定オッズ(final_settled等)は即時拒絶
    - 発走時刻以降のオッズは完全拒絶（未来リーク防止）
    - 提供元観測時刻 <= システム取得時刻 の因果律検証
    - 目標スナップショット時刻以前の観測値であること
    """
    race_dt = parse_timestamp(scheduled_race_time)
    if race_dt.tzinfo is None or target_cutoff_time.tzinfo is None:
        raise ValueError("Race and cutoff timestamps must be timezone-aware")
    valid_list = []

    for idx, r in enumerate(odds_records):
        data_status = str(r.get("data_status", "")).lower()
        if data_status in PROHIBITED_STATUSES:
            raise PostRaceOddsLeakageError(
                f"レコード {idx}: 確定ステータス '{data_status}' のオッズ混入を検知・拒絶しました。"
            )

        # 観測時刻・取得時刻の抽出
        obs_raw = r.get("observed_at")
        fetch_raw = r.get("fetched_at")  # Missing acquisition time is never inferred
        if not obs_raw or not fetch_raw:
            continue

        try:
            obs_dt = parse_timestamp(obs_raw)
            fetch_dt = parse_timestamp(fetch_raw)
        except (ValueError, TypeError):
            continue

        # タイムゾーン整合性
        if (obs_dt.tzinfo is None or fetch_dt.tzinfo is None or
                obs_dt.utcoffset() is None or fetch_dt.utcoffset() is None or
                race_dt.tzinfo is None or target_cutoff_time.tzinfo is None):
            continue

        # 因果律: 観測時刻 <= 取得時刻
        if obs_dt > fetch_dt:
            continue

        # 発走時刻以降のオッズはリークとして完全拒絶
        if obs_dt >= race_dt or fetch_dt >= race_dt:
            raise PostRaceOddsLeakageError(
                f"レコード {idx}: 発走時刻 ({race_dt}) 以降のオッズ (obs={obs_dt}, fetch={fetch_dt}) を検知・拒絶しました。"
            )

        # スナップショット時刻より未来のデータは遮断
        if obs_dt > target_cutoff_time or fetch_dt > target_cutoff_time:
            continue

        # オッズ値バリデーション
        try:
            val = float(r.get("odds_value", 0.0))
            if val < 1.0 or not np.isfinite(val):
                continue
        except (ValueError, TypeError):
            continue

        if str(r.get("source", "unknown")).strip().lower() in {"", "unknown", "none"}:
            continue
        if str(r.get("odds_type", "win")) != "win":
            continue
        if not str(r.get("race_id", "")).strip():
            continue
        try:
            horse_value = float(r.get("horse_number", 0))
            if not np.isfinite(horse_value) or horse_value < 1 or not horse_value.is_integer():
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        valid_list.append(r)

    return valid_list


def record_prerace_snapshot(
    race_id: str,
    scheduled_race_time: Union[str, datetime.datetime],
    timing_tag: str,
    *,
    odds_records: Optional[List[Dict[str, Any]]] = None,
    features_df: Optional[pd.DataFrame] = None,
    win_model = None,
    place_model = None,
    blend_weight: float = 0.5,
    executed_at: Optional[Union[str, datetime.datetime]] = None,
    db_path: Optional[Path] = None,
    source_name: str = "realtime_logger",
    allow_duplicate: bool = False,
    provider_manager: Optional[OddsProviderManager] = None,
    max_retries: int = 3,
    backoff_sec: float = 1.0
) -> Dict[str, Any]:
    """
    【発走前スナップショット実時刻記録の統合関数】
    1. 二重記録防止（Idempotency Guard）: 同一 (race_id, timing_tag) が記録済みの場合は安全にスキップ
    2. オッズ提供が未指定の場合はプロバイダー経由で再試行取得。未接続なら「欠損扱い」として記録（架空データゼロ）
    3. オッズ提供がある場合は厳密検証の上、odds_timeline に追記
    4. 特徴量がある場合、AIモデル推論を実行し ai_predictions_timeline に実時刻(predicted_at)付きで保存
    5. スナップショット実行結果を asof_snapshot_runs に監査ログとして記録
    """
    if timing_tag not in TARGET_TIMINGS:
        raise ValueError(f"無効なtiming_tag: {timing_tag} (許可: {TARGET_TIMINGS})")

    target_db = Path(db_path or ODDS_HISTORY_DB_PATH)
    race_dt = parse_timestamp(scheduled_race_time)
    cutoff_dt = compute_target_snapshot_time(race_dt, timing_tag)
    exec_dt = parse_timestamp(executed_at) if executed_at is not None else datetime.datetime.now(race_dt.tzinfo)

    if race_dt.tzinfo is None or exec_dt.tzinfo is None or race_dt.utcoffset() is None or exec_dt.utcoffset() is None:
        raise ValueError("Race and execution timestamps require explicit timezone")
    if exec_dt >= race_dt:
        raise ValueError("Post-start execution cannot be recorded as a pre-race snapshot")
    if exec_dt < cutoff_dt:
        raise ValueError("Cannot record a future target snapshot before its scheduled cutoff")
    init_odds_history_db(target_db)
    exec_iso = format_iso(exec_dt)
    race_iso = format_iso(race_dt)
    cutoff_iso = format_iso(cutoff_dt)

    # 0. 二重記録防止チェック (Idempotency Guard)
    if not allow_duplicate:
        conn = sqlite3.connect(target_db)
        cur = conn.cursor()
        existing = cur.execute(
            "SELECT id, odds_status, pred_status, executed_at FROM asof_snapshot_runs WHERE race_id = ? AND timing_tag = ?",
            (str(race_id), str(timing_tag))
        ).fetchone()
        conn.close()

        if existing:
            return {
                "status": "SKIPPED_ALREADY_RECORDED",
                "race_id": str(race_id),
                "timing_tag": str(timing_tag),
                "scheduled_race_time": race_iso,
                "target_snapshot_time": cutoff_iso,
                "executed_at": exec_iso,
                "odds_status": existing[1],
                "pred_status": existing[2],
                "odds_count": 0,
                "pred_count": 0,
                "message": f"Snapshot for race {race_id} at {timing_tag} already recorded in asof_snapshot_runs (id={existing[0]}). Skipping duplicate insert.",
                "is_duplicate_prevented": True,
                "db_path": str(target_db)
            }

    # 1. オッズ処理（プロバイダー再試行・接続状態判定）
    odds_status = "missing"
    valid_odds: List[Dict[str, Any]] = []
    error_msg = None
    retry_audit: Optional[Dict[str, Any]] = None

    # 明示的なオッズ指定がない場合、プロバイダーマネージャーを介して再試行取得
    if odds_records is None:
        mgr = provider_manager or OddsProviderManager()
        fetched_odds, retry_audit = mgr.fetch_prerace_odds_with_retry(
            race_id=str(race_id),
            scheduled_race_time=race_iso,
            timing_tag=timing_tag,
            max_retries=max_retries,
            backoff_sec=backoff_sec
        )
        if fetched_odds:
            odds_records = fetched_odds
        else:
            if retry_audit.get("outcome") == "all_sources_disconnected":
                error_msg = "オッズ取得元がすべて未接続(DISCONNECTED)です。架空データ代用を禁止し欠損扱いとします。"
            elif retry_audit.get("outcome") == "retries_exhausted_missing":
                error_msg = f"オッズ取得試行が全{max_retries}回失敗しました（欠損扱い）。"

    if odds_records:
        try:
            valid_odds = validate_odds_records(odds_records, race_dt, cutoff_dt)
            # A single bad row or wrong race invalidates the entire snapshot.
            if len(valid_odds) != len(odds_records) or any(
                str(row.get("race_id")) != str(race_id) for row in valid_odds
            ) or len({int(row["horse_number"]) for row in valid_odds}) != len(valid_odds):
                raise PreRaceRecordingError("Incomplete, duplicate-horse, or mismatched-race odds snapshot")
            if valid_odds:
                odds_status = "captured"
                # odds_timeline への追記保存
                for r in valid_odds:
                    r["timing_tag"] = timing_tag
                inserted_count = record_odds_snapshot(valid_odds, target_db, timing_tag=timing_tag)
                if inserted_count != len(valid_odds):
                    odds_status = "error"
                    error_msg = "Some odds rows failed provenance validation; market data rejected"
                    valid_odds = []
            else:
                odds_status = "missing"
        except PostRaceOddsLeakageError as e:
            odds_status = "rejected_post_race"
            error_msg = str(e)
            valid_odds = []
        except Exception as e:
            odds_status = "error"
            error_msg = str(e)
            valid_odds = []
    else:
        odds_status = "missing"

    # 2. AI予測処理
    pred_status = "skipped"
    pred_count = 0
    predictions_to_insert = []

    model_ver = "win_v2_28cols"
    model_sha256 = None

    if features_df is not None and len(features_df) > 0:
        # Historical training tables can contain outcomes and post-race labels.
        # Until an as-of feature contract is independently certified, inference
        # must not run on such tables.
        forbidden_feature_columns = {
            "finish_order", "finish_position", "rank_result", "result",
            "target", "target_win", "target_place", "is_win", "is_place",
            "payout", "win_payout", "place_payout", "actual_finish",
            "race_result", "confirmed_odds", "final_odds",
        }
        present_forbidden = sorted(forbidden_feature_columns.intersection(
            str(col).lower() for col in features_df.columns
        ))
        if present_forbidden:
            pred_status = "blocked_uncertified_features"
            error_msg = (
                (error_msg + "; " if error_msg else "") +
                "Prediction blocked: result/settlement columns present: " +
                ", ".join(present_forbidden)
            )
            features_df = None
    if features_df is not None and len(features_df) > 0:
        race_feat = features_df[features_df["race_id"] == str(race_id)].copy() if "race_id" in features_df.columns else features_df.copy()
        if len(race_feat) > 0:
            try:
                # カテゴリカル変数の型キャスト（LightGBM整合性確保）
                for col in CATEGORICAL_COLS:
                    if col in race_feat.columns:
                        race_feat[col] = race_feat[col].astype("category")

                # モデル読み込み（未ロードの場合）
                if win_model is None:
                    from .model import KeibaAIWinModel
                    win_model = KeibaAIWinModel()
                    win_model.load()

                if hasattr(win_model, "model_path") and win_model.model_path.exists():
                    model_sha256 = hashlib.sha256(win_model.model_path.read_bytes()).hexdigest()

                # AI単独予測の算出
                pred_df = win_model.predict_win_race(race_feat, place_model=place_model)
                pred_df = pred_df.sort_values("horse_number").reset_index(drop=True)

                # オッズ辞書の作成（利用可能な場合）
                odds_map: Dict[int, float] = {}
                obs_map: Dict[int, str] = {}
                fetch_map: Dict[int, str] = {}
                for o in valid_odds:
                    try:
                        hn = int(o["horse_number"])
                        odds_map[hn] = float(o["odds_value"])
                        obs_map[hn] = str(o["observed_at"])
                        fetch_map[hn] = str(o["fetched_at"])
                    except Exception:
                        continue

                # 市場確率およびブレンド確率の計算
                market_probs = None
                if odds_status == "captured" and odds_map:
                    # 馬番順にオッズ配列を作成
                    horse_nums = pred_df["horse_number"].tolist()
                    odds_arr = np.array([odds_map.get(hn, 0.0) for hn in horse_nums])
                    active_mask = (odds_arr >= 1.0)
                    if active_mask.any():
                        market_probs = compute_market_probabilities_array(odds_arr, active_mask)

                # 予測レコードの構築
                for idx, row in pred_df.iterrows():
                    hn = int(row["horse_number"])
                    hname = str(row.get("horse_name", f"馬番{hn}"))
                    p_place = float(row.get("pred_place_prob", row.get("pred_score", 0.0)))
                    p_win = float(row.get("pred_win_prob", 0.0))

                    m_odds = odds_map.get(hn, None)
                    m_prob = float(market_probs[idx]) if (market_probs is not None and idx < len(market_probs) and market_probs[idx] > 0) else None

                    # ブレンド確率および期待値の算出（オッズがある場合のみ）
                    if m_odds is not None and m_prob is not None:
                        blended_p = float(blend_weight * p_win + (1.0 - blend_weight) * m_prob)
                        ev = float(blended_p * m_odds)
                        rec_status = "valid"
                    else:
                        blended_p = None
                        ev = None
                        rec_status = "odds_missing"

                    predictions_to_insert.append((
                        str(race_id),
                        int(hn),
                        hname,
                        p_place,
                        p_win,
                        m_odds,
                        m_prob,
                        blended_p,
                        ev,
                        int(idx + 1),  # rank
                        str(timing_tag),
                        exec_iso,  # predicted_at
                        obs_map.get(hn, None),
                        fetch_map.get(hn, None),
                        str(odds_status),
                        str(model_ver),
                        str(model_sha256 or ""),
                        rec_status
                    ))

                # ai_predictions_timeline への保存
                conn = sqlite3.connect(target_db)
                cur = conn.cursor()
                cur.executemany("""
                INSERT INTO ai_predictions_timeline (
                    race_id, horse_number, horse_name, pred_place_score, pred_win_prob,
                    market_odds, market_prob, blended_win_prob, expected_value,
                    rank, timing_tag, predicted_at, odds_observed_at, odds_fetched_at,
                    odds_status, model_version, model_sha256, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, predictions_to_insert)
                conn.commit()
                conn.close()

                pred_status = "recorded"
                pred_count = len(predictions_to_insert)

            except Exception as e:
                pred_status = "error"
                error_msg = f"{error_msg}; pred_error: {e}" if error_msg else f"pred_error: {e}"
        else:
            pred_status = "missing"
    else:
        if pred_status == "skipped":
            pred_status = "missing"

    # 3. 実行監査ログ (asof_snapshot_runs) の保存
    conn = sqlite3.connect(target_db)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO asof_snapshot_runs (
        race_id, timing_tag, scheduled_race_time, target_snapshot_time,
        executed_at, odds_status, odds_count, pred_status, pred_count, error_message
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(race_id),
        str(timing_tag),
        race_iso,
        cutoff_iso,
        exec_iso,
        odds_status,
        len(valid_odds),
        pred_status,
        pred_count,
        error_msg
    ))
    conn.commit()
    conn.close()

    return {
        "status": "RECORDED",
        "race_id": str(race_id),
        "timing_tag": timing_tag,
        "scheduled_race_time": race_iso,
        "target_snapshot_time": cutoff_iso,
        "executed_at": exec_iso,
        "odds_status": odds_status,
        "odds_count": len(valid_odds),
        "pred_status": pred_status,
        "pred_count": pred_count,
        "model_version": model_ver,
        "model_sha256": model_sha256,
        "error_message": error_msg,
        "retry_audit": retry_audit,
        "is_duplicate_prevented": False,
        "db_path": str(target_db)
    }


def query_snapshot_records(
    race_id: str,
    timing_tag: Optional[str] = None,
    db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """
    指定レース・タイミングのスナップショット履歴（監査ログ、オッズ、AI予測）を照会する。
    """
    target_db = Path(db_path or ODDS_HISTORY_DB_PATH)
    if not target_db.exists():
        return {"runs": [], "odds": [], "predictions": []}

    conn = sqlite3.connect(target_db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if timing_tag:
        runs = [dict(r) for r in cur.execute("SELECT * FROM asof_snapshot_runs WHERE race_id = ? AND timing_tag = ?", (race_id, timing_tag)).fetchall()]
        odds = [dict(r) for r in cur.execute("SELECT * FROM odds_timeline WHERE race_id = ? AND timing_tag = ?", (race_id, timing_tag)).fetchall()]
        preds = [dict(r) for r in cur.execute("SELECT * FROM ai_predictions_timeline WHERE race_id = ? AND timing_tag = ?", (race_id, timing_tag)).fetchall()]
    else:
        runs = [dict(r) for r in cur.execute("SELECT * FROM asof_snapshot_runs WHERE race_id = ?", (race_id,)).fetchall()]
        odds = [dict(r) for r in cur.execute("SELECT * FROM odds_timeline WHERE race_id = ?", (race_id,)).fetchall()]
        preds = [dict(r) for r in cur.execute("SELECT * FROM ai_predictions_timeline WHERE race_id = ?", (race_id,)).fetchall()]

    conn.close()
    return {"runs": runs, "odds": odds, "predictions": preds}
