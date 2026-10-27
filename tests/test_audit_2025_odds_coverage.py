import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.audit_2025_odds_coverage import audit


class TestAudit2025OddsCoverage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_file_not_found(self):
        non_existent = Path(self.temp_dir.name) / "missing.db"
        with self.assertRaises(FileNotFoundError):
            audit(str(non_existent))

    def test_no_odds_timeline_table(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE race_results (race_id TEXT, odds REAL)")
        conn.commit()
        conn.close()

        res = audit(str(self.db_path))
        self.assertEqual(res["status"], "BLOCKED_NO_ODDS_TIMELINE")
        self.assertFalse(res["asof_certified"])
        self.assertEqual(res["eligible_races"], 0)

    def test_missing_provenance_fields(self):
        conn = sqlite3.connect(self.db_path)
        # Missing observed_at, fetched_at, data_status
        conn.execute("CREATE TABLE odds_timeline (race_id TEXT, horse_number INT, odds_value REAL)")
        conn.commit()
        conn.close()

        res = audit(str(self.db_path))
        self.assertEqual(res["status"], "BLOCKED_MISSING_PROVENANCE_FIELDS")
        self.assertFalse(res["asof_certified"])
        self.assertIn("observed_at", res["missing_fields"])
        self.assertIn("fetched_at", res["missing_fields"])
        self.assertIn("data_status", res["missing_fields"])

    def test_no_valid_snapshots_filtered_out(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE odds_timeline (
                race_id TEXT,
                horse_number INT,
                odds_value REAL,
                observed_at TEXT,
                fetched_at TEXT,
                data_status TEXT
            )
        """)
        # 1. Row from 2026 (forbidden / outside range)
        conn.execute("""
            INSERT INTO odds_timeline VALUES ('202601050101', 1, 2.5, '2026-01-05 10:00:00', '2026-01-05 10:00:05', 'active')
        """)
        # 2. Row from 2024 (outside range)
        conn.execute("""
            INSERT INTO odds_timeline VALUES ('202412280101', 1, 2.5, '2024-12-28 10:00:00', '2024-12-28 10:00:05', 'active')
        """)
        # 3. 2025 row but data_status is 'final_settled'
        conn.execute("""
            INSERT INTO odds_timeline VALUES ('202505050101', 1, 3.0, '2025-05-05 10:00:00', '2025-05-05 10:00:05', 'final_settled')
        """)
        # 4. 2025 row but fetched_at < observed_at (causality violation)
        conn.execute("""
            INSERT INTO odds_timeline VALUES ('202505050102', 1, 3.0, '2025-05-05 10:00:10', '2025-05-05 10:00:05', 'active')
        """)
        conn.commit()
        conn.close()

        res = audit(str(self.db_path), start="2025-01-01", end="2025-12-31")
        self.assertEqual(res["status"], "BLOCKED_NO_VALID_SNAPSHOTS")
        self.assertFalse(res["asof_certified"])
        self.assertEqual(res["candidate_races"], 0)
        self.assertEqual(res["eligible_races"], 0)

    def test_timestamped_snapshots_found(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE odds_timeline (
                race_id TEXT,
                horse_number INT,
                odds_value REAL,
                observed_at TEXT,
                fetched_at TEXT,
                data_status TEXT
            )
        """)
        # Valid 2025 rows for race 1
        conn.execute("""
            INSERT INTO odds_timeline VALUES ('202506010101', 1, 2.5, '2025-06-01 10:00:00', '2025-06-01 10:00:05', 'snapshot')
        """)
        conn.execute("""
            INSERT INTO odds_timeline VALUES ('202506010101', 2, 5.0, '2025-06-01 10:00:00', '2025-06-01 10:00:05', 'snapshot')
        """)
        conn.commit()
        conn.close()

        res = audit(str(self.db_path), start="2025-01-01", end="2025-12-31")
        self.assertEqual(res["status"], "TIMESTAMPED_SNAPSHOTS_FOUND_NOT_ASOF_CERTIFIED")
        self.assertFalse(res["asof_certified"])
        self.assertEqual(res["candidate_races"], 1)
        self.assertEqual(res["candidate_runner_snapshots"], 2)
        self.assertEqual(res["eligible_races"], 0)

    def test_cli_execution_with_out(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE race_results (race_id TEXT)")
        conn.commit()
        conn.close()

        out_path = Path(self.temp_dir.name) / "out.json"
        cmd = [
            sys.executable,
            str(Path(__file__).resolve().parent.parent / "scripts" / "audit_2025_odds_coverage.py"),
            "--db", str(self.db_path),
            "--out", str(out_path)
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        self.assertTrue(out_path.is_file())
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["status"], "BLOCKED_NO_ODDS_TIMELINE")


if __name__ == "__main__":
    unittest.main()
