"""
Streamlit 画面自動テスト (AppTest)
1. pages/01_模擬運用レポート.py:
   - Case A: DB未作成時の安全な案内表示 (例外なし)
   - Case B: 運用台帳が存在する場合の回収率・メトリクス表示
2. app.py:
   - メイン予測画面の正常起動・レンダリング検証
"""
import os
import sys
import tempfile
from pathlib import Path
import unittest
from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.paper_trading import connect, capture, settle
from src.paper_dashboard import load_dashboard


class TestStreamlitApp(unittest.TestCase):
    def test_paper_report_page_missing_db(self):
        """Case A: DB未作成時の安全な案内表示"""
        missing_db_path = REPO_ROOT / "data" / "non_existent_paper_trading.db"
        if missing_db_path.exists():
            missing_db_path.unlink()

        os.environ["KEIBA_PAPER_DB"] = str(missing_db_path)
        at = AppTest.from_file(str(REPO_ROOT / "pages" / "01_模擬運用レポート.py"))
        at.run(timeout=10)

        # 例外エラーが発生していないこと
        self.assertFalse(at.exception)
        # info メッセージが表示されていること
        info_texts = [i.value for i in at.info]
        self.assertTrue(any("模擬運用データはまだありません" in t for t in info_texts))
        # ファイルが勝手に作成されていないこと
        self.assertFalse(missing_db_path.exists())
        print("[OK] 模擬運用レポート (DB未作成): 正常に案内メッセージを表示し、エラーなし")

    def test_paper_report_page_with_data(self):
        """Case B: 運用台帳が存在する場合のメトリクス表示"""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "paper_test.db"
            connect(db_path)

            pred_at = "2026-10-25T06:30:00+00:00"
            cutoff_at = "2026-10-25T06:40:00+00:00"
            capture(db_path, [
                {"race_id": "R1", "horse_number": 1, "odds": 2.5, "win_probability": 0.45,
                 "model_id": "model_v2", "prediction_at": pred_at, "cutoff_at": cutoff_at,
                 "odds_observed_at": pred_at, "odds_fetched_at": pred_at, "source": "sample"},
                {"race_id": "R1", "horse_number": 2, "odds": 5.5, "win_probability": 0.20,
                 "model_id": "model_v2", "prediction_at": pred_at, "cutoff_at": cutoff_at,
                 "odds_observed_at": pred_at, "odds_fetched_at": pred_at, "source": "sample"}
            ])
            settle(db_path, [
                {"race_id": "R1", "horse_number": 1, "prediction_at": pred_at,
                 "won": 1, "official_payout_yen": 250, "settled_at": "2026-10-25T07:00:00+00:00"},
                {"race_id": "R1", "horse_number": 2, "prediction_at": pred_at,
                 "won": 0, "official_payout_yen": 0, "settled_at": "2026-10-25T07:00:00+00:00"}
            ])

            os.environ["KEIBA_PAPER_DB"] = str(db_path)
            at = AppTest.from_file(str(REPO_ROOT / "pages" / "01_模擬運用レポート.py"))
            at.run(timeout=10)

            self.assertFalse(at.exception)
            metric_vals = {m.label: m.value for m in at.metric}
            self.assertEqual(metric_vals.get("記録した予測"), "2件")
            self.assertEqual(metric_vals.get("精算済み"), "2件")
            self.assertEqual(metric_vals.get("未精算"), "0件")
            self.assertEqual(metric_vals.get("回収率"), "125.0%")
            print("[OK] 模擬運用レポート (データあり): メトリクス（回収率125.0%・精算2件）が正常表示")

    def test_main_app_startup(self):
        """メイン画面 app.py の正常起動確認"""
        at = AppTest.from_file(str(REPO_ROOT / "app.py"))
        at.run(timeout=60)
        self.assertFalse(at.exception)
        print("[OK] メイン画面 app.py: 例外なく正常に初期レンダリング完了")


if __name__ == "__main__":
    unittest.main()
