"""Structural fixtures only: synthetic values are never used for prediction."""
import hashlib
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from scripts.jra_v9_history_prerace_audit import audit_v9, V9AuditBlocked


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class V9ResearchAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / "jra_van_only"
        self.base.mkdir()
        self.snapdir = self.base / "prerace_evidence"
        self.snapdir.mkdir()
        self.history_raw = self.base / "jv_RACE_official_fixture.sqlite3"
        self.prerace_raw = self.snapdir / "current_week_RACE_official_fixture.sqlite3"
        self.research = self.base / "jra_research_official_fixture.sqlite3"
        self.snapshot = self.snapdir / "prerace_evidence_valid.sqlite3"
        self._raw(self.history_raw, "2026-10-27T11:00:00+00:00", "20251101000000")
        self._raw(self.prerace_raw, "2026-10-29T07:00:00+00:00", "00000000000000")
        self._research()
        self._card()

    def _raw(self, path, ts, from_time):
        with sqlite3.connect(path) as db:
            db.executescript(
                "CREATE TABLE acquisition("
                "data_spec TEXT,from_time TEXT,captured_at_utc TEXT,"
                "record_count INTEGER,sdk_result TEXT,origin TEXT);"
                "CREATE TABLE jv_record(id INTEGER PRIMARY KEY);"
            )
            db.execute("INSERT INTO acquisition VALUES (?,?,?,?,?,?)",
                       ("RACE", from_time, ts, 0, "JVOpen+JVGets_EOF_SUCCESS", "JRA-VAN/JV-Link COM"))

    def _research(self):
        with sqlite3.connect(self.research) as db:
            db.executescript(
                "CREATE TABLE source_receipts(source_file TEXT,sha256 TEXT,"
                "data_spec TEXT,captured_at_utc TEXT,record_count INTEGER);"
                "CREATE TABLE audit(key TEXT,value_json TEXT);"
                "CREATE TABLE race(race_id TEXT,race_date TEXT,runners_declared INTEGER,"
                "complete_postrace_result INTEGER);"
                "CREATE TABLE runner(race_id TEXT,horse_id TEXT,finish_post INTEGER,"
                "source_file TEXT,source_sha256 TEXT,data_kubun TEXT);"
            )
            db.execute("INSERT INTO source_receipts VALUES (?,?,?,?,?)",
                       (self.history_raw.name, sha(self.history_raw), "RACE",
                        "2026-10-27T11:00:00+00:00", 0))
            db.executemany("INSERT INTO audit VALUES (?,?)",
                           [("source_hashes_verified", "true"),
                            ("training_unlocked", "false"),
                            ("legacy_keiba_db_used", "false")])
            for horse, num_runs in (("horse1", 3), ("horse2", 2)):
                for d in range(num_runs):
                    rid = "2026-03-%02d" % (d+1) + "-" + horse
                    db.execute("INSERT INTO race VALUES (?,?,?,?)", (rid, "2026-03-%02d" % (d+1), 12, 1))
                    db.execute("INSERT INTO runner VALUES (?,?,?,?,?,?)",
                               (rid, horse, d+1, self.history_raw.name, "f"*64, "6"))

    def _card(self):
        with sqlite3.connect(self.snapshot) as db:
            db.executescript(
                "CREATE TABLE capture_evidence(source_archive_name TEXT,"
                "source_archive_sha256 TEXT,capture_finished_at_utc TEXT,"
                "source_type TEXT,clock_attestation TEXT,research_only INTEGER,"
                "training_unlocked INTEGER,alerts_sent INTEGER);"
                "CREATE TABLE race_prerace(race_id TEXT,post_time_utc TEXT,"
                "observed_at_utc TEXT,runners_declared INTEGER);"
                "CREATE TABLE runner_prerace(race_id TEXT,horse_id TEXT,"
                "horse_number INTEGER,se_raw_sha256 TEXT);"
            )
            db.execute("INSERT INTO capture_evidence VALUES (?,?,?,?,?,?,?,?)",
                       (self.prerace_raw.name,sha(self.prerace_raw),
                        "2026-10-29T07:10:00+00:00", "JVOpen RACE option=2",
                        "LOCAL_OS_CLOCK_AFTER_EOF_NOT_EXTERNAL_TIMESTAMP",1,0,0))
            db.execute("INSERT INTO race_prerace VALUES (?,?,?,?)",
                       ("2026103001010101", "2026-10-30T07:00:00+00:00",
                        "2026-10-29T07:10:00+00:00",5))
            db.executemany("INSERT INTO runner_prerace VALUES (?,?,?,?)",
                           [("2026103001010101", "horse"+str(i), i, "f"*64) for i in range(1,6)])

    def test_one_eligible_future_card_and_history(self):
        result = audit_v9(self.research, [self.snapshot])
        self.assertEqual(result["eligible_future_races"], 1)
        self.assertEqual(result["future_runner_rows"], 5)
        self.assertEqual(result["runners_with_3_prior_starts_observed_before_snapshot"], 1)
        self.assertEqual(result["runners_with_insufficient_prior_starts"], 4)
        self.assertEqual(result["runners_with_unknown_historical_identity"], 3)
        self.assertEqual(result["prior_runs_used"], 3)
        self.assertFalse(result["training_unlocked"])
        self.assertFalse(result["alerts_sent"])

    def test_no_future_card_is_not_a_model_approval(self):
        result = audit_v9(self.research, [])
        self.assertEqual(result["status"], "V9_NO_COMPLETE_FUTURE_CARD_YET")
        self.assertEqual(result["eligible_future_races"], 0)
        self.assertFalse(result["training_unlocked"])

    def test_old_source_received_later_is_never_prior(self):
        with sqlite3.connect(self.research) as db:
            db.execute("UPDATE source_receipts SET captured_at_utc=?",
                       ("2026-10-31T07:10:00+00:00",))
        with self.assertRaises(V9AuditBlocked):  # must match immutable raw acquisition
            audit_v9(self.research, [self.snapshot])

    def test_tampered_original_rejected(self):
        with sqlite3.connect(self.history_raw) as db:
            db.execute("CREATE TABLE attacker_modified_historical_source(x INTEGER)")
        with self.assertRaisesRegex(V9AuditBlocked, "HASH_MISMATCH"):
            audit_v9(self.research, [self.snapshot])

    def test_card_not_before_off_rejected(self):
        with sqlite3.connect(self.snapshot) as db:
            db.execute("UPDATE race_prerace SET post_time_utc=?",
                       ("2026-10-29T07:15:00+00:00",))
        with self.assertRaisesRegex(V9AuditBlocked, "correctly timed"):
            audit_v9(self.research, [self.snapshot])

    def test_runner_missing_rejected(self):
        with sqlite3.connect(self.snapshot) as db:
            db.execute("DELETE FROM runner_prerace WHERE horse_number=5")
        with self.assertRaisesRegex(V9AuditBlocked, "Incomplete"):
            audit_v9(self.research, [self.snapshot])

    def test_unverified_snapshot_source_flag_rejected(self):
        with sqlite3.connect(self.snapshot) as db:
            db.execute("UPDATE capture_evidence SET training_unlocked=1")
        with self.assertRaisesRegex(V9AuditBlocked, "safety flags"):
            audit_v9(self.research, [self.snapshot])

    def test_legacy_db_rejected(self):
        with self.assertRaises(V9AuditBlocked):
            audit_v9(self.base/"keiba.db", [self.snapshot])

    def test_malformed_clock_rejected(self):
        with sqlite3.connect(self.snapshot) as db:
            db.execute("UPDATE race_prerace SET observed_at_utc=?",
                       ("2026-10-29T07:10:00",))
        with self.assertRaisesRegex(V9AuditBlocked, "Naive"):
            audit_v9(self.research, [self.snapshot])

    def test_duplicate_snapshot_sources_rejected(self):
        with self.assertRaisesRegex(V9AuditBlocked, "Duplicate"):
            audit_v9(self.research, [self.snapshot,self.snapshot])

    def test_old_result_fields_never_output_as_current_race_inputs(self):
        report = audit_v9(self.research, [self.snapshot])
        self.assertNotIn("odds", str(report).lower())
        self.assertNotIn("finish_post", str(report).lower())
        self.assertEqual(report["betting_recommendations"], [])


if __name__ == "__main__":
    unittest.main()
