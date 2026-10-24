import sys
import io
import unittest
import tempfile
import shutil
import sqlite3
import hashlib
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
    get_db_stats,
    get_races_metadata,
    load_all_race_data,
    preview_cleanup_targets,
    execute_confirmed_cleanup,
)
from src.features import (
    build_features,
    build_features_for_race_with_history,
    FEATURE_COLS,
)
from src.model import KeibaAIModel, MODEL_PATH
from scripts.build_race_cache import compute_walkforward_probabilities


class TestPhase2Fixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp(prefix="keiba_phase2_test_"))
        cls.copy_db = cls.tmpdir / "test_keiba.db"
        shutil.copy2(DB_PATH, cls.copy_db)
        cls.prod_db_hash_before = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
        cls.prod_model_hash_before = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        # Verify production DB and Model were never touched
        prod_db_hash_after = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
        prod_model_hash_after = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
        assert cls.prod_db_hash_before == prod_db_hash_after, "Production DB was modified!"
        assert cls.prod_model_hash_before == prod_model_hash_after, "Production Model was modified!"
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_1_database_integrity_and_no_side_effect_delete(self):
        """
        [テスト2] データベース整合性チェック ＆ get_db_stats() が DELETE を行わないことの検証
        """
        conn = sqlite3.connect(self.copy_db)
        cur = conn.cursor()
        # Inject a dummy synthetic row into COPY DB to ensure get_db_stats() does NOT delete it
        cur.execute(
            "INSERT INTO races (race_id, race_name, date, race_course, race_number, surface, distance, direction, weather, condition, grade) "
            "VALUES ('202799999999', 'テストレース', '2027-01-01', '東京', 1, '芝', 1600, '左', '晴', '良', '一般')"
        )
        conn.commit()
        conn.close()

        stats = get_db_stats(self.copy_db)
        self.assertEqual(stats["race_count"], 3175, "get_db_stats() must be SELECT-only and not delete rows")

        # Preview cleanup targets without deleting
        preview = preview_cleanup_targets(self.copy_db)
        self.assertEqual(preview["synthetic_races"], 1)
        self.assertEqual(preview["orphan_payouts"], 6512)

        # Dry-run (confirm=False) must not delete
        res_dry = execute_confirmed_cleanup(self.copy_db, delete_synthetic=True, confirm=False)
        self.assertFalse(res_dry["executed"])
        self.assertEqual(get_db_stats(self.copy_db)["race_count"], 3175)

        # Confirmed cleanup on copy DB removes the injected test row
        res_exec = execute_confirmed_cleanup(self.copy_db, delete_synthetic=True, confirm=True)
        self.assertTrue(res_exec["executed"])
        self.assertEqual(get_db_stats(self.copy_db)["race_count"], 3174)

    def test_2_single_race_history_lookup_vs_full_build(self):
        """
        [テスト3] 特徴量生成テスト（キャッシュ外レースの過去走参照バグ修正＆学習時との完全一致検証）
        """
        # Pick an excluded partial race (e.g., '202608040705' on 2026-10-18) and a complete race ('202608040911' on 2026-10-25)
        for test_rid in ["202608040705", "202606040907", "202608040911"]:
            single_feat, fcols = build_features_for_race_with_history(test_rid, db_path=self.copy_db)
            self.assertGreater(len(single_feat), 0, f"Failed to build features for {test_rid}")

        # Compare single-race feature generation against full-dataset build_features() on '202608040911' (菊花賞)
        df_all = load_all_race_data(self.copy_db)
        feat_full_all, _ = build_features(df_all[df_all["date"] <= "2026-10-25"])
        full_kikka = feat_full_all[feat_full_all["race_id"] == "202608040911"].sort_values("horse_number").reset_index(drop=True)
        single_kikka, _ = build_features_for_race_with_history("202608040911", db_path=self.copy_db)
        single_kikka = single_kikka.sort_values("horse_number").reset_index(drop=True)

        # Ensure experienced horses are NOT treated as debut horses (past_race_count > 0)
        self.assertGreater((single_kikka["past_race_count"] > 0).sum(), 0)

        # Ensure 100% exact match between full build and single-race history build across all 31 feature columns
        for col in FEATURE_COLS:
            if pd.api.types.is_numeric_dtype(full_kikka[col]):
                np.testing.assert_allclose(
                    full_kikka[col].to_numpy(),
                    single_kikka[col].to_numpy(),
                    rtol=1e-6,
                    err_msg=f"Feature mismatch in {col} between training build and single-race inference build",
                )
            else:
                self.assertEqual(
                    full_kikka[col].astype(str).tolist(),
                    single_kikka[col].astype(str).tolist(),
                    f"Categorical mismatch in {col}",
                )

    def test_3_unconfirmed_and_aborted_exclusion_in_training(self):
        """
        [テスト4] 未確定レース（finish_order==0）および取消・除外・競走中止（finish_order>=29, <0, NaN）混入防止テスト
        """
        df_sample = pd.DataFrame({
            "race_id": [f"r_{i//10}" for i in range(120)],
            "date": ["2026-10-01"] * 100 + ["2026-10-25"] * 20,
            "finish_order": ([1, 2, 3, 4, 5, 6, 7, 8, 99, -1] * 10) + ([0] * 20),
            "target": [1] * 120,  # Intentionally corrupted target on unconfirmed rows
        })
        for c in FEATURE_COLS:
            df_sample[c] = 1.0

        clean_df, v_stats = KeibaAIModel.validate_and_filter_training_data(df_sample)
        self.assertEqual(v_stats["input_rows"], 120)
        self.assertEqual(v_stats["excluded_unconfirmed_rows"], 20)
        self.assertEqual(v_stats["excluded_aborted_or_invalid_rows"], 20)  # ten '99' and ten '-1'
        self.assertEqual(v_stats["valid_finished_rows"], 80)
        self.assertTrue((clean_df["finish_order"].between(1, 28)).all())

        # Also test model backup restoration when model file is corrupted
        corrupt_model_path = self.tmpdir / "corrupt_keiba_lgbm.joblib"
        corrupt_model_path.write_bytes(b"corrupted binary content")
        m = KeibaAIModel(model_path=corrupt_model_path)
        m.load()  # Should automatically restore from backup without training on head(1000)
        self.assertIsNotNone(m.model)
        self.assertEqual(m.feature_cols, list(FEATURE_COLS))
        self.assertEqual(getattr(m, "restored_from_sha256", None), self.prod_model_hash_before)

        # Test that an invalid model dict (wrong feature specification) is rejected and safely stops if no backups exist
        import joblib
        bad_spec_path = self.tmpdir / "bad_spec.joblib"
        joblib.dump({"model": m.model, "feature_cols": ["wrong_col"]}, bad_spec_path)
        self.assertFalse(KeibaAIModel._validate_loaded_model_dict(joblib.load(bad_spec_path)))

        # Test safe stop (RuntimeError) when both primary model and backups are unavailable/corrupted
        m_fail = KeibaAIModel(model_path=bad_spec_path)
        m_fail._find_backup_model_paths = lambda: []
        with self.assertRaises(RuntimeError):
            m_fail.load()

    def test_4_no_same_day_or_future_leakage(self):
        """
        [テスト5] 時系列リーク防止テスト（同日他レース結果の不干渉 ＆ 未来レース結果の不干渉 ＆ ウォークフォワードBlock 0除外）
        """
        # Create synthetic two-day dataset where Jockey J1 rides in Race 1 (morning) and Race 2 (afternoon) on the SAME day
        rows = []
        # 5 prior days so J1 has >= 5 rides
        for d_idx in range(1, 6):
            rows.append({
                "race_id": f"2026010{d_idx}0101", "race_name": "過去R", "date": f"2026-01-0{d_idx}",
                "race_course": "東京", "race_number": 1, "surface": "芝", "distance": 1600,
                "direction": "左", "weather": "晴", "condition": "良", "grade": "一般",
                "finish_order": 1, "bracket_number": 1, "horse_number": 1,
                "horse_id": f"h_past_{d_idx}", "horse_name": f"馬{d_idx}", "sex": "牡", "age": 3,
                "weight_carry": 56.0, "jockey_id": "J1", "jockey_name": "騎手1",
                "last_3f": 34.0, "odds": 2.0, "popularity": 1, "horse_weight": 480.0,
                "horse_weight_diff": 0.0, "sire": "ロードカナロア", "sire_type": "ミスプロ系",
                "running_style": "逃げ"
            })
        # Target day 2026-01-10: Race 1 (1R) and Race 2 (11R) on the SAME day
        rows.append({
            "race_id": "202601100101", "race_name": "当日1R", "date": "2026-01-10",
            "race_course": "東京", "race_number": 1, "surface": "芝", "distance": 1600,
            "direction": "左", "weather": "晴", "condition": "良", "grade": "一般",
            "finish_order": 1, "bracket_number": 1, "horse_number": 1,
            "horse_id": "h_same_1", "horse_name": "当日1R馬", "sex": "牡", "age": 3,
            "weight_carry": 56.0, "jockey_id": "J1", "jockey_name": "騎手1",
            "last_3f": 34.0, "odds": 2.0, "popularity": 1, "horse_weight": 480.0,
            "horse_weight_diff": 0.0, "sire": "ロードカナロア", "sire_type": "ミスプロ系",
            "running_style": "逃げ"
        })
        rows.append({
            "race_id": "202601100111", "race_name": "当日11R", "date": "2026-01-10",
            "race_course": "東京", "race_number": 11, "surface": "芝", "distance": 1600,
            "direction": "左", "weather": "晴", "condition": "良", "grade": "一般",
            "finish_order": 5, "bracket_number": 1, "horse_number": 1,
            "horse_id": "h_same_11", "horse_name": "当日11R馬", "sex": "牡", "age": 3,
            "weight_carry": 56.0, "jockey_id": "J1", "jockey_name": "騎手1",
            "last_3f": 35.0, "odds": 5.0, "popularity": 3, "horse_weight": 480.0,
            "horse_weight_diff": 0.0, "sire": "ロードカナロア", "sire_type": "ミスプロ系",
            "running_style": "追込"
        })
        df_orig = pd.DataFrame(rows)
        feat_a, _ = build_features(df_orig)

        # Now mutate the result of 2026-01-10 1R (from 1st place to 15th place) and add a future race on 2026-01-15
        df_mut = df_orig.copy()
        df_mut.loc[df_mut["race_id"] == "202601100101", "finish_order"] = 15
        df_mut = pd.concat([df_mut, pd.DataFrame([{
            "race_id": "202601150101", "race_name": "未来1R", "date": "2026-01-15",
            "race_course": "東京", "race_number": 1, "surface": "芝", "distance": 1600,
            "direction": "左", "weather": "晴", "condition": "良", "grade": "一般",
            "finish_order": 1, "bracket_number": 1, "horse_number": 1,
            "horse_id": "h_same_11", "horse_name": "当日11R馬", "sex": "牡", "age": 3,
            "weight_carry": 56.0, "jockey_id": "J1", "jockey_name": "騎手1",
            "last_3f": 33.0, "odds": 1.5, "popularity": 1, "horse_weight": 480.0,
            "horse_weight_diff": 0.0, "sire": "ロードカナロア", "sire_type": "ミスプロ系",
            "running_style": "逃げ"
        }])], ignore_index=True)
        feat_b, _ = build_features(df_mut)

        r11_a = feat_a[feat_a["race_id"] == "202601100111"].iloc[0]
        r11_b = feat_b[feat_b["race_id"] == "202601100111"].iloc[0]

        # Same-day 1R result and future 2026-01-15 result must have ZERO effect on 2026-01-10 11R features
        for c in FEATURE_COLS:
            self.assertEqual(r11_a[c], r11_b[c], f"Leakage detected in {c}: {r11_a[c]} vs {r11_b[c]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
