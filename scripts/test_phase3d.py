"""
Phase 3D 検証テストスクリプト
1. オッズ履歴DBの追記型タイムライン保存（上書き防止）テスト
2. 予測時点再現テスト（発走30分前・10分前・5分前での未来オッズ遮断）
3. 市場確率とEVの数学的恒等性テスト
4. 本番DB・本番資産のSHA-256不変検証
"""
import sys
import os
import sqlite3
import datetime
import hashlib
import glob
from pathlib import Path
import pandas as pd
import numpy as np

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

from src.odds_history import init_odds_history_db, record_odds_snapshot, get_latest_odds_as_of
from src.market import compute_market_probabilities_array


def test_1_odds_history_timeline_append_only(test_db_path: Path):
    """テスト1: 追記型タイムライン保存の確認（上書きされず履歴が保持されるか）"""
    print("\n--- Test 1: オッズ履歴タイムライン追記型テスト ---")
    if test_db_path.exists():
        test_db_path.unlink()

    init_odds_history_db(test_db_path)

    # 15:10（30分前）のスナップショット
    snap_1510 = [
        {"race_id": "202605010101", "horse_number": 1, "odds_value": 3.5, "popularity": 1, "observed_at": "2026-05-01T15:10:00", "source": "JV-Link"},
        {"race_id": "202605010101", "horse_number": 2, "odds_value": 5.0, "popularity": 2, "observed_at": "2026-05-01T15:10:00", "source": "JV-Link"},
    ]
    # 15:30（10分前）のスナップショット
    snap_1530 = [
        {"race_id": "202605010101", "horse_number": 1, "odds_value": 3.2, "popularity": 1, "observed_at": "2026-05-01T15:30:00", "source": "JV-Link"},
        {"race_id": "202605010101", "horse_number": 2, "odds_value": 5.5, "popularity": 2, "observed_at": "2026-05-01T15:30:00", "source": "JV-Link"},
    ]

    c1 = record_odds_snapshot(snap_1510, test_db_path)
    c2 = record_odds_snapshot(snap_1530, test_db_path)

    assert c1 == 2 and c2 == 2, f"レコード数が不正: c1={c1}, c2={c2}"

    conn = sqlite3.connect(test_db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM odds_timeline WHERE race_id = '202605010101'")
    total_records = cur.fetchone()[0]
    conn.close()

    assert total_records == 4, f"上書きされずに合計4件保存されるべきですが {total_records} 件です"
    print(f"  [OK] 2回のスナップショット計4件が上書きされずタイムラインとして保存されました (Total: {total_records}件)")


def test_2_as_of_time_prediction_reproduction(test_db_path: Path):
    """テスト2: 予測時点再現テスト（30分前・10分前・5分前での未来オッズ遮断）"""
    print("\n--- Test 2: 予測時点再現＆未来オッズ遮断テスト ---")
    race_id = "202605010102"
    # 発走予定時刻: 15:40:00
    # 30分前: 15:10:00 (馬1: 4.0, 馬2: 6.0)
    # 10分前: 15:30:00 (馬1: 3.5, 馬2: 7.0)
    # 5分前:  15:35:00 (馬1: 3.0, 馬2: 8.0)
    # 確定後: 15:45:00 (馬1: 2.8, 馬2: 9.0)
    timeline_data = [
        {"race_id": race_id, "horse_number": 1, "odds_value": 4.0, "popularity": 1, "observed_at": "2026-05-01T15:10:00", "source": "JV-Link", "data_status": "pre_30m"},
        {"race_id": race_id, "horse_number": 2, "odds_value": 6.0, "popularity": 2, "observed_at": "2026-05-01T15:10:00", "source": "JV-Link", "data_status": "pre_30m"},
        {"race_id": race_id, "horse_number": 1, "odds_value": 3.5, "popularity": 1, "observed_at": "2026-05-01T15:30:00", "source": "JV-Link", "data_status": "pre_10m"},
        {"race_id": race_id, "horse_number": 2, "odds_value": 7.0, "popularity": 2, "observed_at": "2026-05-01T15:30:00", "source": "JV-Link", "data_status": "pre_10m"},
        {"race_id": race_id, "horse_number": 1, "odds_value": 3.0, "popularity": 1, "observed_at": "2026-05-01T15:35:00", "source": "JV-Link", "data_status": "pre_5m"},
        {"race_id": race_id, "horse_number": 2, "odds_value": 8.0, "popularity": 2, "observed_at": "2026-05-01T15:35:00", "source": "JV-Link", "data_status": "pre_5m"},
        {"race_id": race_id, "horse_number": 1, "odds_value": 2.8, "popularity": 1, "observed_at": "2026-05-01T15:45:00", "source": "JV-Link", "data_status": "final_settled"},
        {"race_id": race_id, "horse_number": 2, "odds_value": 9.0, "popularity": 2, "observed_at": "2026-05-01T15:45:00", "source": "JV-Link", "data_status": "final_settled"},
    ]
    record_odds_snapshot(timeline_data, test_db_path)

    # 1. 30分前 (15:10) 時点の取得
    df_30m = get_latest_odds_as_of(race_id, "2026-05-01T15:10:00", db_path=test_db_path)
    assert len(df_30m) == 2
    h1_30m = df_30m[df_30m["horse_number"] == 1]["odds_value"].iloc[0]
    h2_30m = df_30m[df_30m["horse_number"] == 2]["odds_value"].iloc[0]
    assert h1_30m == 4.0 and h2_30m == 6.0, f"30分前のオッズが不一致: {h1_30m}, {h2_30m}"
    print("  [OK] 30分前時点: 馬1=4.0, 馬2=6.0 を正確に取得（未来の15:30以降のオッズは遮断）")

    # 2. 10分前 (15:30) 時点の取得
    df_10m = get_latest_odds_as_of(race_id, "2026-05-01T15:30:00", db_path=test_db_path)
    assert len(df_10m) == 2
    h1_10m = df_10m[df_10m["horse_number"] == 1]["odds_value"].iloc[0]
    h2_10m = df_10m[df_10m["horse_number"] == 2]["odds_value"].iloc[0]
    assert h1_10m == 3.5 and h2_10m == 7.0, f"10分前のオッズが不一致: {h1_10m}, {h2_10m}"
    print("  [OK] 10分前時点: 馬1=3.5, 馬2=7.0 を正確に取得（未来の15:35以降のオッズは遮断）")

    # 3. 5分前 (15:35) 時点の取得
    df_5m = get_latest_odds_as_of(race_id, "2026-05-01T15:35:00", db_path=test_db_path)
    assert len(df_5m) == 2
    h1_5m = df_5m[df_5m["horse_number"] == 1]["odds_value"].iloc[0]
    h2_5m = df_5m[df_5m["horse_number"] == 2]["odds_value"].iloc[0]
    assert h1_5m == 3.0 and h2_5m == 8.0, f"5分前のオッズが不一致: {h1_5m}, {h2_5m}"
    print("  [OK] 5分前時点:  馬1=3.0, 馬2=8.0 を正確に取得（確定オッズ15:45は完全遮断）")


def test_3_market_ev_mathematical_identity():
    """テスト3: 市場確率とEVの数学的恒等性の検証"""
    print("\n--- Test 3: 市場確率とEVの数学的恒等性テスト ---")
    # 任意の実オッズ群
    odds_cases = [
        [2.5, 3.8, 5.2, 12.0, 35.0, 80.0],
        [1.5, 4.0, 10.0, 20.0, 50.0],
        [3.0, 3.0, 3.0, 4.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0]
    ]

    for idx, odds_list in enumerate(odds_cases):
        odds_arr = np.array(odds_list)
        probs = compute_market_probabilities_array(odds_arr)
        evs = probs * odds_arr

        # 1. 確率合計が厳密に1.0
        assert np.isclose(np.sum(probs), 1.0), f"確率合計が1.0ではありません: {np.sum(probs)}"

        # 2. EV_i = P_i * O_i = 1 / sum(1/odds)
        expected_ev = 1.0 / np.sum(1.0 / odds_arr)
        for i, (p, o, ev) in enumerate(zip(probs, odds_arr, evs)):
            assert np.isclose(ev, expected_ev), f"EVが全頭一致していません: ev={ev}, expected={expected_ev}"
            assert np.isclose(p * o, ev), f"P*OとEVが不一致: p*o={p*o}, ev={ev}"

        print(f"  [OK] Case {idx+1}: 出走{len(odds_list)}頭 | 控除率={(1-expected_ev)*100:.1f}% | 全頭同一EV={expected_ev:.4f} (恒等式 P_i * O_i == 1 / Σ(1/O) を確認)")


def test_4_production_integrity():
    """テスト4: 本番資産・DBの不変性検証（Phase 1 バックアップとのハッシュ完全照合）"""
    print("\n--- Test 4: 本番データベース・本番資産のSHA-256不変検証 ---")
    manifest_files = glob.glob(str(repo_root / "backups" / "*" / "keiba.db"))
    assert len(manifest_files) > 0, "Phase 1のバックアップが存在しません"
    backup_db = Path(manifest_files[0])
    prod_db = repo_root / "data" / "keiba.db"

    h_backup = hashlib.sha256(backup_db.read_bytes()).hexdigest()
    h_prod = hashlib.sha256(prod_db.read_bytes()).hexdigest()

    assert h_backup == h_prod, f"本番DBが改変されています!\nProd:   {h_prod}\nBackup: {h_backup}"
    print(f"  [OK] 本番DB (data/keiba.db) はPhase 1バックアップと100%一致しています")
    print(f"       SHA-256: {h_prod}")


if __name__ == "__main__":
    test_db = repo_root / "data" / "test_odds_history.db"
    try:
        test_1_odds_history_timeline_append_only(test_db)
        test_2_as_of_time_prediction_reproduction(test_db)
        test_3_market_ev_mathematical_identity()
        test_4_production_integrity()
        print("\n==========================================")
        print(">>> Phase 3D 全テスト正常合格 (ALL PASSED) <<<")
        print("==========================================")
    finally:
        if test_db.exists():
            test_db.unlink()
