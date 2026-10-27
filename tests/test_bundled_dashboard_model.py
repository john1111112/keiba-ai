"""Regression: the shipped dashboard's legacy model MUST be trusted and loadable.

This is independent of the win-prediction model's different 28-feature checksum.
"""
import hashlib
import json
import unittest
from pathlib import Path

from src.model import (
    KeibaAIModel, KeibaAIWinModel, MODEL_PATH, MODEL_WIN_V2_28_PATH,
    TRUSTED_V1_MODEL_SHA256,
)


class BundledDashboardModelTests(unittest.TestCase):
    def test_legacy_dashboard_model_is_verified_before_deserialization(self):
        expected = "fc9757cb9220e5b629fb292f17a2bf1e7343458f0b575e49301f232e0ac473c4"
        self.assertTrue(MODEL_PATH.is_file())
        self.assertEqual(hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest(), expected)
        self.assertEqual(TRUSTED_V1_MODEL_SHA256, expected)
        sidecar = MODEL_PATH.with_suffix(MODEL_PATH.suffix + ".sha256")
        self.assertTrue(sidecar.is_file())
        self.assertEqual(json.loads(sidecar.read_text(encoding="utf-8"))["sha256"], expected)
        ok, calculated = KeibaAIModel._verify_file_sha256_before_load(MODEL_PATH)
        self.assertTrue(ok)
        self.assertEqual(calculated, expected)

    def test_dashboard_and_background_models_both_load_real_bundled_artifacts(self):
        for model_cls, path in ((KeibaAIModel, MODEL_PATH), (KeibaAIWinModel, MODEL_WIN_V2_28_PATH)):
            with self.subTest(model=model_cls.__name__):
                instance = model_cls()
                self.assertEqual(instance.model_path, path)
                instance.load()
                self.assertIsNotNone(instance.model)
                self.assertEqual(len(instance.feature_cols), len(instance.model.feature_name_))


if __name__ == "__main__":
    unittest.main()
