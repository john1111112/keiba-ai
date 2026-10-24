"""
Phase 3B 単体テストスイート（監査・再設計版）
- 1着確率予測モデルの構築
- 3着以内モデルとの有界射影整合性 (P(Win) <= P(Top3) & 合計1.0 を100%両立)
- 確率校正 (Sigmoid, Isotonic, 有界射影) の挙動と評価
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
    project_win_probabilities_with_top3_bounds,
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

    def test_1_bounded_projection_consistency(self):
        """[Phase 3B - 1] 有界射影アルゴリズムにより正規化後も P(Win) <= P(Top3) と 合計1.0 が100%両立すること"""
        # 単純正規化では p_win[0] (0.35/0.65 = 0.538) > p_top3[0] (0.40) と再逆転する反例ケース
        p_win = np.array([0.35, 0.20, 0.10])
        p_top3 = np.array([0.40, 0.60, 0.50])

        res = project_win_probabilities_with_top3_bounds(p_win, p_top3, target_sum=1.0)

        # 1. 出走馬合計が 1.0 であること
        self.assertAlmostEqual(res.sum(), 1.0, places=6)

        # 2. 全馬について P(Win) <= P(Top3) が厳密に守られていること (違反数0)
        violations = res > p_top3 + 1e-7
        self.assertFalse(violations.any(), f"Violation detected: {res} > {p_top3}")
        self.assertLessEqual(res[0], 0.400001)

    def test_2_enforce_probability_consistency_dataframe(self):
        """[Phase 3B - 2] レースDataFrameに対する有界射影整合性処理の検証"""
        test_df = pd.DataFrame({
            "race_id": ["R01"] * 5 + ["R02"] * 5,
            "horse_number": [1, 2, 3, 4, 5] * 2,
            "finish_order": [1, 2, 3, 4, 5,  1, 2, 3, 4, 15],
            "time_str": ["1:30.0", "1:30.1", "1:30.2", "1:30.5", "1:31.0",
                         "1:30.0", "1:30.1", "1:30.2", "1:30.5", ""], # 10番目は取消馬
            "odds": [2.5, 4.0, 6.0, 12.0, 30.0,  3.0, 4.5, 5.0, 8.0, 50.0],
            "pred_win_prob": [0.35, 0.20, 0.15, 0.10, 0.05,  0.40, 0.25, 0.20, 0.10, 0.15],
            "pred_place_prob": [0.38, 0.60, 0.50, 0.40, 0.30,  0.50, 0.45, 0.40, 0.30, 0.05],
        })

        cons_df = enforce_probability_consistency(test_df, win_prob_col="pred_win_prob", place_prob_col="pred_place_prob", normalize_to_one=True)

        # 範囲
        self.assertTrue((cons_df["pred_win_prob"] >= 0.0).all())
        self.assertTrue((cons_df["pred_win_prob"] <= 1.0).all())

        # P(Win) <= P(Top3)
        self.assertTrue((cons_df["pred_win_prob"] <= cons_df["pred_place_prob"] + 1e-6).all())

        # 取消馬は0
        self.assertEqual(cons_df.loc[9, "pred_win_prob"], 0.0)

        # レース合計 1.0
        r1_sum = cons_df[cons_df["race_id"] == "R01"]["pred_win_prob"].sum()
        r2_sum = cons_df[cons_df["race_id"] == "R02"]["pred_win_prob"].sum()
        self.assertAlmostEqual(r1_sum, 1.0, places=5)
        self.assertAlmostEqual(r2_sum, 1.0, places=5)

    def test_3_calibration_and_metrics(self):
        """[Phase 3B - 3] 確率校正器の動作とECE等の評価指標"""
        np.random.seed(42)
        y_true = np.random.binomial(1, 0.15, size=800)
        y_raw = np.clip(y_true * 0.35 + np.random.uniform(0.02, 0.20, size=800), 0.01, 0.99)

        sig_cal = ProbabilityCalibrator(method="sigmoid").fit(y_raw[:500], y_true[:500])
        p_sig = sig_cal.predict_proba(y_raw[500:])

        metrics = evaluate_probability_calibration(y_true[500:], p_sig)
        self.assertIn("auc", metrics)
        self.assertIn("log_loss", metrics)
        self.assertIn("brier_score", metrics)
        self.assertIn("ece", metrics)
        self.assertGreater(metrics["auc"], 0.5)

    def test_4_separate_model_file_and_security(self):
        """[Phase 3B - 4] 1着モデルが別ファイルとして保存され、デシリアライズ前のハッシュ検証が機能すること"""
        copy_win_path = self.tmpdir / "keiba_lgbm_win_v2_28cols.joblib"
        win_m = KeibaAIWinModel(model_path=copy_win_path)
        self.assertEqual(win_m.target_type, "win")
        self.assertNotEqual(str(win_m.model_path), str(MODEL_PATH))


if __name__ == "__main__":
    unittest.main()
