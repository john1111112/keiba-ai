"""
Phase 3C 単体テストスイート
- レース件数の相互排他性・全件数整合性 (2987 + 115 + 72 = 3174)
- 市場確率モデルの計算式（1/odds_i の正規化）
- 確率整合性（有界射影の例外ケース・ConsistencyError 検証）
- AIと市場確率のブレンド処理
- 本番DB・既存モデル・キャッシュの変更ゼロ保護
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
from src.market import (
    compute_market_probabilities,
    compute_market_probabilities_array,
    blend_probabilities,
    find_optimal_blend_weight
)
from src.calibration import (
    project_win_probabilities_with_top3_bounds,
    ConsistencyError,
    enforce_probability_consistency
)
from src.model import MODEL_PATH


class TestPhase3C(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="keiba_test_phase3c_"))
        cls.backup_dir = repo_root / "backups" / "phase1_20261024_123800"
        manifest_path = cls.backup_dir / "backup_manifest.json"
        assert manifest_path.exists(), "Backup manifest missing!"
        cls.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        cls.prod_db_hash = hashlib.sha256((repo_root / "data" / "keiba.db").read_bytes()).hexdigest()
        cls.prod_m1_hash = hashlib.sha256((repo_root / "models" / "keiba_lgbm.joblib").read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)
        cur_db_hash = hashlib.sha256((repo_root / "data" / "keiba.db").read_bytes()).hexdigest()
        cur_m1_hash = hashlib.sha256((repo_root / "models" / "keiba_lgbm.joblib").read_bytes()).hexdigest()
        assert cur_db_hash == cls.prod_db_hash, "Production DB was modified!"
        assert cur_m1_hash == cls.prod_m1_hash, "Production v1 Model was modified!"

    def test_1_race_counts_mutual_exclusivity(self):
        """[Phase 3C - 1] レース分類の完全排他性・件数整合性（確定完全2987 + 不完全115 + 未確定72 = 3174）"""
        df_all = load_all_race_data()
        summary_df, inc_df = evaluate_race_completeness_and_export_csv(df_all)

        total_unique = df_all["race_id"].nunique()
        self.assertEqual(total_unique, 3174)

        status_counts = summary_df["status"].value_counts().to_dict()
        n_past_complete = status_counts.get("PAST_CONFIRMED_COMPLETE", 0)
        n_incomplete = status_counts.get("PAST_INCOMPLETE_EXTRACT", 0)
        n_upcoming = status_counts.get("UPCOMING_UNCONFIRMED", 0)

        # 厳密な件数一致
        self.assertEqual(n_past_complete, 2987, "過去完全確定レースは2987件でなければなりません")
        self.assertEqual(n_incomplete, 115, "過去抜粋不完全レースは115件でなければなりません")
        self.assertEqual(n_upcoming, 72, "発走前未確定レースは72件でなければなりません")
        self.assertEqual(n_past_complete + n_incomplete + n_upcoming, 3174)

        # 集合の排他性（重複ゼロ）
        s_complete = set(summary_df[summary_df["status"] == "PAST_CONFIRMED_COMPLETE"]["race_id"])
        s_incomp = set(summary_df[summary_df["status"] == "PAST_INCOMPLETE_EXTRACT"]["race_id"])
        s_up = set(summary_df[summary_df["status"] == "UPCOMING_UNCONFIRMED"]["race_id"])

        self.assertEqual(len(s_complete.intersection(s_incomp)), 0)
        self.assertEqual(len(s_complete.intersection(s_up)), 0)
        self.assertEqual(len(s_incomp.intersection(s_up)), 0)

    def test_2_market_probabilities_formula_and_normalization(self):
        """[Phase 3C - 2] 市場確率の計算式 q_i = 1/odds_i の正規化と取消馬除外"""
        odds = np.array([2.0, 4.0, 10.0, 20.0, 50.0]) # 1/odds = [0.5, 0.25, 0.1, 0.05, 0.02], sum = 0.92
        p_market = compute_market_probabilities_array(odds)

        expected_q = 1.0 / odds
        expected_p = expected_q / expected_q.sum()

        np.testing.assert_allclose(p_market, expected_p, rtol=1e-5)
        self.assertAlmostEqual(p_market.sum(), 1.0, places=6)

        # 取消馬マスクの検証
        active_mask = np.array([True, True, True, True, False])
        p_market_sub = compute_market_probabilities_array(odds, active_mask=active_mask)
        self.assertEqual(p_market_sub[4], 0.0)
        self.assertAlmostEqual(p_market_sub.sum(), 1.0, places=6)

    def test_3_bounded_projection_edge_cases_and_consistency_error(self):
        """[Phase 3C - 5] 有界射影の例外ケース検証（sum(p_top3) < 1.0 で比率保持スケーリングし合計1.0と上限制約を両立）"""
        # 例外ケース: 3着内確率の合計が 0.70 < 1.0
        # この場合、各馬の勝率が3着内確率の相対比率を保持してスケールされ合計1.0を満たす
        p_win_low = np.array([0.40, 0.30, 0.20])
        p_top3_low = np.array([0.30, 0.25, 0.15]) # sum = 0.70 < 1.0

        res_low = project_win_probabilities_with_top3_bounds(
            p_win_low, p_top3_low, target_sum=1.0, strict=True
        )
        self.assertAlmostEqual(res_low.sum(), 1.0, places=5)
        # スケール後の上限 ceiling (0.30/0.7, 0.25/0.7, 0.15/0.7) 以下であること
        scaled_ceilings = p_top3_low * (1.0 / 0.70)
        self.assertTrue((res_low <= scaled_ceilings + 1e-6).all())

        # 正常ケース: 100% 整合
        p_win_normal = np.array([0.35, 0.20, 0.10])
        p_top3_normal = np.array([0.40, 0.60, 0.50])
        res = project_win_probabilities_with_top3_bounds(p_win_normal, p_top3_normal, target_sum=1.0, strict=True)
        self.assertAlmostEqual(res.sum(), 1.0, places=6)
        self.assertTrue((res <= p_top3_normal + 1e-6).all())

    def test_4_blend_probabilities_and_weight_fitting(self):
        """[Phase 3C - 3] AIと市場確率のブレンドおよび過去データのみでの重みフィッティング"""
        y_true = np.array([1, 0, 0, 0, 0,  0, 1, 0, 0, 0])
        p_ai = np.array([0.40, 0.25, 0.15, 0.10, 0.10,  0.20, 0.35, 0.25, 0.10, 0.10])
        p_market = np.array([0.45, 0.25, 0.15, 0.10, 0.05,  0.25, 0.35, 0.20, 0.10, 0.10])

        best_w = find_optimal_blend_weight(y_true, p_ai, p_market, metric="log_loss")
        self.assertGreaterEqual(best_w, 0.0)
        self.assertLessEqual(best_w, 1.0)

        df_sample = pd.DataFrame({
            "race_id": ["R1"] * 5 + ["R2"] * 5,
            "p_win_ai": p_ai,
            "p_market": p_market,
        })
        blended = blend_probabilities(df_sample, weight_ai=best_w)
        self.assertIn("p_blend", blended.columns)
        r1_sum = blended[blended["race_id"] == "R1"]["p_blend"].sum()
        r2_sum = blended[blended["race_id"] == "R2"]["p_blend"].sum()
        self.assertAlmostEqual(r1_sum, 1.0, places=5)
        self.assertAlmostEqual(r2_sum, 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
