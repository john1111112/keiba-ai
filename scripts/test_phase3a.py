import sys
import io
import unittest
import tempfile
import shutil
import sqlite3
import hashlib
import json
from pathlib import Path
import pandas as pd
import numpy as np

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.database import (
    DB_PATH,
    INCOMPLETE_RACES_CSV_PATH,
    get_db_stats,
    get_races_metadata,
    load_all_race_data,
    evaluate_race_completeness_and_export_csv,
    preview_cleanup_targets,
    execute_confirmed_cleanup,
)
from src.features import (
    build_features,
    build_features_for_race_with_history,
    FEATURE_COLS_V1_31,
    FEATURE_COLS_V2_28,
    PEDIGREE_COLS,
)
from src.model import KeibaAIModel, MODEL_PATH, SecurityError
from src.betting_strategy import evaluate_race_value
from scripts.build_race_cache import compute_walkforward_probabilities, build_cache_pipeline


class TestPhase3A(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="keiba_phase3a_test_"))
        cls.copy_db = cls.tmpdir / "copy_keiba.db"
        shutil.copy2(DB_PATH, cls.copy_db)
        cls.prod_db_hash_before = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
        cls.prod_model_hash_before = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
        cls.prod_feat_hash_before = hashlib.sha256((REPO_ROOT / "data" / "features.parquet").read_bytes()).hexdigest()
        cls.prod_rank_hash_before = hashlib.sha256((REPO_ROOT / "data" / "race_rank_cache.parquet").read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        assert cls.prod_db_hash_before == hashlib.sha256(DB_PATH.read_bytes()).hexdigest(), "Production DB changed!"
        assert cls.prod_model_hash_before == hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest(), "Production Model changed!"
        assert cls.prod_feat_hash_before == hashlib.sha256((REPO_ROOT / "data" / "features.parquet").read_bytes()).hexdigest(), "Production features.parquet changed!"
        assert cls.prod_rank_hash_before == hashlib.sha256((REPO_ROOT / "data" / "race_rank_cache.parquet").read_bytes()).hexdigest(), "Production race_rank_cache.parquet changed!"
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_1_incomplete_race_detection_and_csv_export(self):
        """
        [Phase 3A - 1] 不完全レース判定ロジックとCSV出力の検証
        - 出走取消や除外による馬番欠番だけで不完全と断定しないこと
        - 1〜3着の存在、6頭以上、完走馬の着順連続性を確認し、CSVに出力すること
        """
        summary_df, incomplete_df = evaluate_race_completeness_and_export_csv(
            db_path=self.copy_db,
            csv_out_path=INCOMPLETE_RACES_CSV_PATH
        )
        self.assertTrue(INCOMPLETE_RACES_CSV_PATH.exists())
        self.assertEqual(len(summary_df), 3174)
        self.assertEqual(len(incomplete_df), 120)
        # Ensure the 2 obstacle races with scratched/aborted non-finishers ('202510010105', '202607030109') are NOT falsely marked incomplete
        comp_rids = set(summary_df.loc[summary_df["is_complete_for_eval"], "race_id"])
        self.assertIn("202510010105", comp_rids)
        self.assertIn("202607030109", comp_rids)

    def test_2_v1_31cols_and_v2_28cols_compatibility_and_walkforward(self):
        """
        [Phase 3A - 2 & 5] 28特徴量モデル（血統除外）と既存31特徴量モデルの互換性 ＆ 開催日単位境界ウォークフォワード検証
        """
        v2_model_path = self.tmpdir / "keiba_lgbm_v2_28cols.joblib"
        v2_feat_path = self.tmpdir / "features_v2_28.parquet"
        v2_rank_path = self.tmpdir / "rank_v2_28.parquet"
        v2_csv_path = self.tmpdir / "incomplete.csv"

        df_feat_v2, cache_df_v2, inc_df = build_cache_pipeline(
            db_path=self.copy_db,
            feat_out_path=v2_feat_path,
            rank_cache_out_path=v2_rank_path,
            incomplete_csv_out_path=v2_csv_path,
            feature_version="v2_28cols_no_pedigree",
            retrain_production_model=False,
            model_out_path=v2_model_path
        )
        # Verify v2 model saved with .sha256 sidecar and 28 features
        self.assertTrue(v2_model_path.exists())
        self.assertTrue(v2_model_path.with_suffix(".joblib.sha256").exists())

        m_v2 = KeibaAIModel(model_path=v2_model_path, feature_version="v2_28cols_no_pedigree")
        m_v2.load()
        self.assertEqual(len(m_v2.feature_cols), 28)
        self.assertEqual(m_v2.feature_version, "v2_28cols_no_pedigree")
        for pcol in PEDIGREE_COLS:
            self.assertNotIn(pcol, m_v2.feature_cols)

        # Verify existing 31-col production model still loads and predicts without error
        m_v1 = KeibaAIModel(model_path=MODEL_PATH, feature_version="v1_31cols")
        m_v1.load()
        self.assertEqual(len(m_v1.feature_cols), 31)
        sample_race = df_feat_v2[df_feat_v2["race_id"] == "202608040911"].copy()
        sample_race["pred_score"] = np.nan
        pred_v1 = m_v1.predict_race(sample_race)
        pred_v2 = m_v2.predict_race(sample_race)
        self.assertEqual(len(pred_v1), 18)
        self.assertEqual(len(pred_v2), 18)

    def test_3_expected_value_suspension_and_reference_rank(self):
        """
        [Phase 3A - 3] 期待値誤計算の廃止・算出不可設定・購入推奨と穴馬判定の一時停止検証
        """
        m_v1 = KeibaAIModel(model_path=MODEL_PATH)
        m_v1.load()
        r_feat, _ = build_features_for_race_with_history("202608040911", db_path=self.copy_db)
        pred = m_v1.predict_race(r_feat)
        self.assertTrue(pred["expected_value"].isna().all(), "expected_value must be NaN until 1st-place prob model exists")
        self.assertTrue(pred["win_expected_value"].isna().all())
        self.assertTrue(pred["place_expected_value"].isna().all())
        self.assertFalse(pred["is_ana"].any(), "EV-based ana horse flagging must be suspended")

        val = evaluate_race_value(pred)
        self.assertFalse(val["should_bet"], "EV-based betting recommendation must be suspended")
        self.assertTrue(val["is_reference_rank"])
        self.assertIsNone(val["top_ana"])
        self.assertEqual(len(val["recommendations"]), 0)
        self.assertIn("旧ルール参考評価", val["rank"])

    def test_4_security_pre_deserialization_hash_verification(self):
        """
        [Phase 3A - 6] デシリアライズ前のSHA-256ハッシュ検証 ＆ 未検証バックアップの拒否テスト
        """
        # 1. Untrusted / tampered joblib file without a valid .sha256 sidecar must be rejected BEFORE joblib.load
        tampered_path = self.tmpdir / "tampered.joblib"
        tampered_path.write_bytes(b"untrusted pickle payload")

        m_sec = KeibaAIModel(model_path=tampered_path)
        # Provide an unverified backup (no backup_manifest.json and no .sha256 sidecar) -> must also be rejected!
        unverified_bak_dir = self.tmpdir / "unverified_bk"
        unverified_bak_dir.mkdir(exist_ok=True)
        unverified_bak = unverified_bak_dir / "keiba_lgbm.joblib"
        # Copy real model but alter 1 byte or use a newly dumped model without manifest
        import joblib
        m_real = KeibaAIModel(MODEL_PATH)
        m_real.load()
        joblib.dump({"model": m_real.model, "feature_cols": m_real.feature_cols}, unverified_bak)

        m_sec._find_backup_model_paths = lambda: [unverified_bak]
        with self.assertRaises(RuntimeError):
            m_sec.load()

        # 2. Restoring from the verified Phase 1 backup (which has backup_manifest.json) must succeed
        m_restore = KeibaAIModel(model_path=tampered_path)
        m_restore.load()
        self.assertEqual(m_restore.restored_from_sha256, self.prod_model_hash_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
