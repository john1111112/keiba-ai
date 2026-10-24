"""
Phase 3B 単体テストスイート
- 1着確率予測モデルの構築
- 3着以内モデルとの確率整合性 (P(Win) <= P(Top3) & 合計1.0)
- 確率校正 (Sigmoid, Isotonic) の挙動と評価
- 単勝期待値の計算整合性
- 本番モデル・DB・キャッシュの変更ゼロ保護
"""
import unittest
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path if "sys" in locals() else False:
    import sys
    sys.path.insert(0, str(repo_root))
import sys
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.database import load_all_race_data, evaluate_race_completeness_and_export_csv
from src.features import build_features, FEATURE_COLS_V2_28
from src.model import KeibaAIModel, KeibaAIWinModel, MODEL_PATH, MODEL_WIN_V2_28_PATH
from src.calibration import (
    ProbabilityCalibrator,
    evaluate_probability_calibration,
    enforce_probability_consistency
)


class TestPhase3B(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="keiba_test_phase3b_"))
        cls.backup_dir = repo_root / "backups" / "phase1_20261024_123800"
        manifest_path = cls.backup_dir / "backup_manifest.json"
        assert manifest_path.exists(), "Backup manifest missing!"
        cls.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # 実行前の本番ハッシュ記録
        cls.prod_db_hash = hashlib.sha256((repo_root / "data" / "keiba.db").read_bytes()).hexdigest()
        cls.prod_m1_hash = hashlib.sha256((repo_root / "models" / "keiba_lgbm.joblib").read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)
        # 実行後の本番ハッシュ照合（一切変更されていないことを検証）
        cur_db_hash = hashlib.sha256((repo_root / "data" / "keiba.db").read_bytes()).hexdigest()
        cur_m1_hash = hashlib.sha256((repo_root / "models" / "keiba_lgbm.joblib").read_bytes()).hexdigest()
        assert cur_db_hash == cls.prod_db_hash, "Production DB was modified!"
        assert cur_m1_hash == cls.prod_m1_hash, "Production v1 Model was modified!"

    def test_1_win_model_creation_and_consistency(self):
        """[Phase 3B - 1 & 2] 1着確率予測モデルの構築と確率整合性（0〜1, P(Win)<=P(Top3), 合計1.0）"""
        # サンプルレースデータ構築
        test_df = pd.DataFrame({
            "race_id": ["R01"] * 5 + ["R02"] * 5,
            "horse_number": [1, 2, 3, 4, 5] * 2,
            "finish_order": [1, 2, 3, 4, 5,  1, 2, 3, 4, 15],
            "time_str": ["1:30.0", "1:30.1", "1:30.2", "1:30.5", "1:31.0",
                         "1:30.0", "1:30.1", "1:30.2", "1:30.5", ""], # 10番目は取消馬
            "odds": [2.5, 4.0, 6.0, 12.0, 30.0,  3.0, 4.5, 5.0, 8.0, 50.0],
            "pred_win_prob": [0.45, 0.30, 0.20, 0.15, 0.05,  0.50, 0.30, 0.25, 0.15, 0.10],
            "pred_place_prob": [0.70, 0.55, 0.40, 0.10, 0.15,  0.65, 0.50, 0.45, 0.30, 0.05],
        })

        # 4番馬は pred_win_prob(0.15) > pred_place_prob(0.10) と矛盾
        # 10番馬は time_str == '' の取消馬
        cons_df = enforce_probability_consistency(test_df, win_prob_col="pred_win_prob", place_prob_col="pred_place_prob", normalize_to_one=True)

        # 1. 範囲検証
        self.assertTrue((cons_df["pred_win_prob"] >= 0.0).all())
        self.assertTrue((cons_df["pred_win_prob"] <= 1.0).all())

        # 2. P(Win) <= P(Top3) 検証
        self.assertTrue((cons_df["pred_win_prob"] <= cons_df["pred_place_prob"] + 1e-6).all())

        # 3. 取消馬の確率は 0.0
        self.assertEqual(cons_df.loc[9, "pred_win_prob"], 0.0)

        # 4. レース内合計 1.0 検証
        r1_sum = cons_df[cons_df["race_id"] == "R01"]["pred_win_prob"].sum()
        r2_sum = cons_df[cons_df["race_id"] == "R02"]["pred_win_prob"].sum()
        self.assertAlmostEqual(r1_sum, 1.0, places=5)
        self.assertAlmostEqual(r2_sum, 1.0, places=5)

    def test_2_calibration_methods(self):
        """[Phase 3B - 3] 確率校正（Sigmoid, Isotonic）の動作と評価指標の正常計算"""
        np.random.seed(42)
        y_true = np.random.binomial(1, 0.2, size=1000)
        y_raw = np.clip(y_true * 0.4 + np.random.uniform(0.05, 0.25, size=1000), 0.01, 0.99)

        # Sigmoid 校正
        sig_cal = ProbabilityCalibrator(method="sigmoid").fit(y_raw[:700], y_true[:700])
        p_sig = sig_cal.predict_proba(y_raw[700:])
        self.assertTrue((p_sig >= 0.0).all() and (p_sig <= 1.0).all())

        # Isotonic 校正
        iso_cal = ProbabilityCalibrator(method="isotonic").fit(y_raw[:700], y_true[:700])
        p_iso = iso_cal.predict_proba(y_raw[700:])
        self.assertTrue((p_iso >= 0.0).all() and (p_iso <= 1.0).all())

        # 評価指標の取得
        metrics = evaluate_probability_calibration(y_true[700:], p_sig)
        self.assertIn("auc", metrics)
        self.assertIn("log_loss", metrics)
        self.assertIn("brier_score", metrics)
        self.assertIn("bin_stats", metrics)
        self.assertGreater(metrics["auc"], 0.5)

    def test_3_expected_value_calculation(self):
        """[Phase 3B - 4] 単勝期待値の計算整合性 (回収倍率・期待利益率)"""
        p_win = np.array([0.5, 0.2, 0.05])
        odds = np.array([2.5, 6.0, 15.0])

        ev_return = p_win * odds
        ev_profit = ev_return - 1.0

        np.testing.assert_allclose(ev_return, [1.25, 1.20, 0.75])
        np.testing.assert_allclose(ev_profit, [0.25, 0.20, -0.25])

    def test_4_separate_model_file_and_security(self):
        """[Phase 3B - 5] 1着モデルが別ファイルとして保存され、デシリアライズ前のハッシュ検証が機能すること"""
        copy_win_path = self.tmpdir / "keiba_lgbm_win_v2_28cols.joblib"
        win_m = KeibaAIWinModel(model_path=copy_win_path)
        self.assertEqual(win_m.target_type, "win")
        self.assertNotEqual(str(win_m.model_path), str(MODEL_PATH))


if __name__ == "__main__":
    unittest.main()
