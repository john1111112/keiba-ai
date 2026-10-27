"""Ensure unverified KEIBA DB cannot feed model, alerts or cached UI."""
import contextlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
from src.data_trust_gate import (
    QUARANTINED_DB, UntrustedRaceDataError,
    inspect_source_readiness, require_verified_race_data,
)

class RealDataSafetyTests(unittest.TestCase):
    def test_committed_legacy_db_denied(self):
        report=inspect_source_readiness(QUARANTINED_DB)
        self.assertEqual("BLOCKED_UNVERIFIED_SOURCE",report["status"])
        self.assertTrue(report["quarantined_legacy_db"])
        self.assertFalse(report["verified_for_training"])
        for purpose in ("training","prediction","backtest","alert"):
            with self.assertRaisesRegex(UntrustedRaceDataError,"KEIBA_REAL_DATA_GATE_BLOCKED"):
                require_verified_race_data(QUARANTINED_DB,purpose)

    def test_missing_database_denied_without_creating_it(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/"no-real-data.db"
            self.assertFalse(path.exists())
            with self.assertRaises(UntrustedRaceDataError):
                require_verified_race_data(path,"training")
            self.assertFalse(path.exists())

    def test_forged_claim_of_source_does_not_unlock_predictions(self):
        with tempfile.TemporaryDirectory() as t:
            path=Path(t)/"looks_good.db"
            with sqlite3.connect(path) as c:
                c.execute("CREATE TABLE races (race_id TEXT PRIMARY KEY)")
                c.execute("CREATE TABLE race_results (race_id TEXT, horse_number INTEGER)")
                c.execute("CREATE TABLE race_source_evidence (source TEXT, signature TEXT)")
                c.execute("CREATE TABLE runner_source_evidence (source TEXT, signature TEXT)")
                c.execute("INSERT INTO races VALUES ('202501010101')")
                c.execute("INSERT INTO race_results VALUES ('202501010101',1)")
                c.execute("INSERT INTO race_source_evidence VALUES ('JRA-VAN','FAKE')")
                c.execute("INSERT INTO runner_source_evidence VALUES ('JRA-VAN','FAKE')")
            report=inspect_source_readiness(path)
            self.assertEqual(1,report["race_source_evidence_rows"])
            self.assertEqual(1,report["runner_source_evidence_rows"])
            self.assertFalse(report["verified_for_predictions"])
            with self.assertRaises(UntrustedRaceDataError):
                require_verified_race_data(path,"prediction")

    def test_cli_exits_two_and_prints_status(self):
        cmd=[sys.executable,"-m","src.data_trust_gate"]
        p=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True,timeout=30)
        self.assertEqual(2,p.returncode,p.stdout+p.stderr)
        self.assertIn("BLOCKED_UNVERIFIED_SOURCE",p.stdout)
        self.assertIn('"races": 3174',p.stdout)

    def test_all_user_facing_paths_are_gated(self):
        expected={
            "app.py":'st.stop()',
            "scripts/build_race_cache.py":'require_verified_race_data(db_path, purpose="training")',
            "scripts/auto_notify_s_rank.py":'require_verified_race_data(purpose="alert")',
            "scripts/run_phase3b_walkforward.py":'require_verified_race_data(purpose="backtest")',
            "scripts/run_phase3c_evaluation.py":'require_verified_race_data(purpose="backtest")',
        }
        for path,required in expected.items():
            s=(ROOT/path).read_text(encoding="utf-8")
            self.assertIn(required,s,path)

if __name__=="__main__":
    unittest.main()
