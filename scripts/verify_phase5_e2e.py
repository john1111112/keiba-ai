"""
Phase 5 本番公開前エンドツーエンド検証スクリプト
- 隔離された検証用DB（data/test_paper_e2e.db）を使用
- 本番DB（data/keiba.db）およびモデルは一切変更しない
- 合成データ（source='sample'）であることを明示し、実データと偽装しない
- 発走前オッズ取込 -> 模擬購入記録 -> 公式払戻照合 -> 回収率・損益・未精算件数集計
"""
import sys
import os
import sqlite3
import datetime
from pathlib import Path
import pandas as pd
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.odds_history import init_odds_history_db, record_odds_snapshot, get_latest_odds_as_of
from src.paper_trading import connect, capture, settle, report
from src.paper_dashboard import load_dashboard
from src.verified_settlement import validate_and_plan, import_verified_csv


def run_e2e_verification(tmp_dir: Path):
    print("=" * 60)
    print(">>> Phase 5 E2E 隔離検証開始 (合成データによる機能検証) <<<")
    print("=" * 60)

    odds_db = tmp_dir / "test_odds_history.db"
    paper_db = tmp_dir / "test_paper_trading.db"
    settlement_csv = tmp_dir / "verified_settlement.csv"

    # 1. 発走前オッズ履歴の保存と未来オッズ遮断の検証
    print("\n[Step 1] 発走前オッズ履歴の記録と時点遮断テスト")
    init_odds_history_db(odds_db)

    # 2026年10月25日 15:40発走 レース 202610250111
    # 30分前(15:10), 10分前(15:30), 5分前(15:35), 確定後(15:45)
    odds_stream = [
        {"race_id": "202610250111", "horse_number": 1, "odds_value": 3.0, "observed_at": "2026-10-25T06:10:00+00:00", "fetched_at": "2026-10-25T06:10:00+00:00", "source": "sample", "data_status": "pre_race"},
        {"race_id": "202610250111", "horse_number": 2, "odds_value": 5.0, "observed_at": "2026-10-25T06:10:00+00:00", "fetched_at": "2026-10-25T06:10:00+00:00", "source": "sample", "data_status": "pre_race"},
        {"race_id": "202610250111", "horse_number": 1, "odds_value": 2.5, "observed_at": "2026-10-25T06:30:00+00:00", "fetched_at": "2026-10-25T06:30:00+00:00", "source": "sample", "data_status": "pre_race"},
        {"race_id": "202610250111", "horse_number": 2, "odds_value": 5.5, "observed_at": "2026-10-25T06:30:00+00:00", "fetched_at": "2026-10-25T06:30:00+00:00", "source": "sample", "data_status": "pre_race"},
        # 確定オッズ (15:45)
        {"race_id": "202610250111", "horse_number": 1, "odds_value": 2.3, "observed_at": "2026-10-25T06:45:00+00:00", "fetched_at": "2026-10-25T06:45:00+00:00", "source": "sample", "data_status": "final_settled"},
        {"race_id": "202610250111", "horse_number": 2, "odds_value": 6.0, "observed_at": "2026-10-25T06:45:00+00:00", "fetched_at": "2026-10-25T06:45:00+00:00", "source": "sample", "data_status": "final_settled"},
    ]
    record_odds_snapshot(odds_stream, odds_db)

    # 10分前時点 (15:30) でオッズを取得
    as_of = "2026-10-25T06:30:00+00:00"
    latest_odds = get_latest_odds_as_of("202610250111", as_of, db_path=odds_db, include_sample=True)
    assert len(latest_odds) == 2, f"Expected 2 horses, got {len(latest_odds)}"
    h1_odds = latest_odds.loc[latest_odds["horse_number"] == 1, "odds_value"].iloc[0]
    h2_odds = latest_odds.loc[latest_odds["horse_number"] == 2, "odds_value"].iloc[0]
    assert h1_odds == 2.5, f"Expected 2.5 at 15:30, got {h1_odds} (未来確定オッズ 2.3 の混入なし)"
    assert h2_odds == 5.5, f"Expected 5.5 at 15:30, got {h2_odds} (未来確定オッズ 6.0 の混入なし)"
    print(f"  [OK] 10分前オッズを正確に再現: 馬1=2.5倍, 馬2=5.5倍 (確定オッズは完全遮断)")

    # 2. 模擬運用台帳への予測記録（模擬購入）
    print("\n[Step 2] 模擬購入記録の登録")
    connect(paper_db)  # スキーマ初期化
    pred_time_r1 = "2026-10-25T06:30:00+00:00"
    cutoff_time_r1 = "2026-10-25T06:40:00+00:00"
    pred_time_r2 = "2026-10-25T06:40:00+00:00"
    cutoff_time_r2 = "2026-10-25T06:50:00+00:00"

    # レース1 (202610250111): 馬1 と 馬2 の2点を模擬購入
    predictions = [
        {
            "race_id": "202610250111", "horse_number": 1, "odds": 2.5,
            "win_probability": 0.45, "model_id": "keiba_lgbm_v2", "prediction_at": pred_time_r1,
            "cutoff_at": cutoff_time_r1, "odds_observed_at": pred_time_r1, "odds_fetched_at": pred_time_r1, "source": "sample"
        },
        {
            "race_id": "202610250111", "horse_number": 2, "odds": 5.5,
            "win_probability": 0.20, "model_id": "keiba_lgbm_v2", "prediction_at": pred_time_r1,
            "cutoff_at": cutoff_time_r1, "odds_observed_at": pred_time_r1, "odds_fetched_at": pred_time_r1, "source": "sample"
        },
        # レース2 (202610250112): 馬3 の1点を模擬購入（これは未精算のまま残す）
        {
            "race_id": "202610250112", "horse_number": 3, "odds": 10.0,
            "win_probability": 0.15, "model_id": "keiba_lgbm_v2", "prediction_at": pred_time_r2,
            "cutoff_at": cutoff_time_r2, "odds_observed_at": pred_time_r2, "odds_fetched_at": pred_time_r2, "source": "sample"
        }
    ]
    captured = capture(paper_db, predictions)
    assert captured == 3

    rep_before = report(paper_db)
    assert rep_before["predictions"] == 3
    assert rep_before["settled"] == 0
    assert rep_before["unsettled"] == 3
    print("  [OK] 3件の模擬購入を記録完了（全件未精算）")

    # 3. 公式払戻CSVの検証と照合（精算処理）
    print("\n[Step 3] 公式払戻データの検証と精算適用")
    # レース1の確定結果: 馬1が1着（単勝払戻 250円）、馬2は敗退（払戻0円）
    settle_rows = [
        {
            "race_id": "202610250111",
            "horse_number": 1,
            "prediction_at": pred_time_r1,
            "won": 1,
            "official_payout_yen": 250,
            "settled_at": "2026-10-25T07:00:00+00:00",
            "source_url": "https://www.jra.go.jp/datafile/seiseki/report/sample",
            "verified_by": "auditor_e2e"
        },
        {
            "race_id": "202610250111",
            "horse_number": 2,
            "prediction_at": pred_time_r1,
            "won": 0,
            "official_payout_yen": 0,
            "settled_at": "2026-10-25T07:00:00+00:00",
            "source_url": "https://www.jra.go.jp/datafile/seiseki/report/sample",
            "verified_by": "auditor_e2e"
        }
    ]
    pd.DataFrame(settle_rows).to_csv(settlement_csv, index=False)

    # Dry-run 検証
    res_dry = import_verified_csv(paper_db, settlement_csv, apply=False)
    assert res_dry["validated"] == 2 and res_dry["applied"] == 0
    print("  [OK] 払戻CSVの事前検証に合格 (Dry-run: DB更新ゼロ)")

    # 適用 (--apply)
    res_apply = import_verified_csv(paper_db, settlement_csv, apply=True)
    assert res_apply["applied"] == 2
    print(f"  [OK] 2件の精算を適用完了")

    # 4. 回収率・損益・未精算件数の計算検証
    print("\n[Step 4] 回収率・損益・未精算件数の精密検証")
    dash = load_dashboard(paper_db)
    overall = dash["overall"]

    # 期待される数値:
    # 記録総数: 3件
    # 精算済み: 2件 (レース1の2頭)
    # 未精算: 1件 (レース2の1頭)
    # 仮想投資額: 200円 (精算済み2件 * 100円)
    # 仮想払戻額: 250円 (馬1の的中払戻 250円 + 馬2の0円)
    # 損益: +50円 (250円 - 200円)
    # 回収率: 125.0% (250 / 200 = 1.25)
    print("  ダッシュボード集計結果:")
    print(f"    記録件数: {overall['predictions']} 件 (期待値: 3)")
    print(f"    精算済み: {overall['settled']} 件 (期待値: 2)")
    print(f"    未精算:   {overall['unsettled']} 件 (期待値: 1)")
    print(f"    投資額:   {overall['stake_yen']} 円 (期待値: 200)")
    print(f"    払戻額:   {overall['returns_yen']} 円 (期待値: 250)")
    print(f"    損益:     {overall['profit_yen']:+} 円 (期待値: +50)")
    print(f"    回収率:   {overall['roi']*100:.1f} % (期待値: 125.0%)")

    assert overall["predictions"] == 3
    assert overall["settled"] == 2
    assert overall["unsettled"] == 1
    assert overall["stake_yen"] == 200
    assert overall["returns_yen"] == 250
    assert overall["profit_yen"] == 50
    assert np.isclose(overall["roi"], 1.25)
    print("  [OK] 全集計項目が数学的・仕様通りに 100% 正確に一致")

    # 5. 二重精算拒絶テスト（Idempotency / Immutability）
    print("\n[Step 5] 二重精算拒絶テスト")
    try:
        import_verified_csv(paper_db, settlement_csv, apply=True)
        assert False, "重複精算が例外で拒絶されるべきです"
    except ValueError as e:
        print(f"  [OK] 重複精算が正しく拒絶されました: {e}")

    # 6. Streamlit 画面表示テスト
    print("\n[Step 6] Streamlit 画面の読み込みテスト")
    os.environ["KEIBA_PAPER_DB"] = str(paper_db)
    dash_from_env = load_dashboard(os.environ["KEIBA_PAPER_DB"])
    assert dash_from_env["overall"]["roi"] == 1.25
    print("  [OK] Streamlit環境変数経由でのダッシュボード読込確認")

    print("\n========================================================")
    print(">>> Phase 5 E2E 検証 正常終了 (ALL PASSED) <<<")
    print("========================================================")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        run_e2e_verification(Path(td))
