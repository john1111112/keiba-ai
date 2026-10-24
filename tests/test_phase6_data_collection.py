"""
Phase 6: 実データ収集基盤・架空データ隔離・公式払戻照合 単体テストスイート
"""
import datetime
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

from src.database import get_payouts, get_verified_payouts, DB_PATH
from src.pre_race_odds_service import (
    validate_pre_race_record,
    ingest_pre_race_odds,
)
from src.paper_trading import connect, capture, report
from scripts.record_official_settlement import record_single_official_settlement


class TestPhase6DataCollection(unittest.TestCase):
    def test_1_orphan_payouts_isolation_guard(self):
        """作業1: 孤立架空払戻データが既定で除外・隔離されることの検証"""
        # 架空レースID (20210001000)
        p_isolated = get_payouts("20210001000")
        self.assertEqual(p_isolated, [], "Orphan synthetic payout must return empty list by default")

        p_verified = get_verified_payouts("20210001000")
        self.assertEqual(p_verified, [], "get_verified_payouts must exclude orphan payouts")

        # allow_orphan=True の場合のみ内部監査用に取得可能
        p_raw = get_payouts("20210001000", allow_orphan=True)
        self.assertGreater(len(p_raw), 0, "Raw query should retrieve internal audit data")

    def test_2_pre_race_odds_validation_and_timing(self):
        """作業2: 発走前オッズの発表時刻・取得時刻・発走後混入防止テスト"""
        now = datetime.datetime.fromisoformat("2026-10-25T06:30:00+00:00")

        # 正常な発走前オッズ (発表時刻あり)
        rec_valid = {
            "race_id": "202610250101",
            "horse_number": 3,
            "odds_value": 4.5,
            "observed_at": "2026-10-25T06:20:00+09:00",
            "fetched_at": "2026-10-25T06:20:05+09:00",
            "race_start_at": "2026-10-25T06:50:00+09:00",
            "source": "jra_official_sample"
        }
        val = validate_pre_race_record(rec_valid, now_utc=now)
        self.assertEqual(val[0], "202610250101")
        self.assertEqual(val[1], 3)
        self.assertEqual(val[2], 4.5)
        self.assertEqual(val[6], "pre_race_flash")

        # 発表時刻が確認できない場合 -> 推測補完せず None / unverified として記録
        rec_unverified_time = {
            "race_id": "202610250101",
            "horse_number": 3,
            "odds_value": 4.5,
            "observed_at": None,
            "fetched_at": "2026-10-25T06:20:05+09:00",
            "race_start_at": "2026-10-25T06:50:00+09:00",
            "source": "jra_official_sample"
        }
        val2 = validate_pre_race_record(rec_unverified_time, now_utc=now)
        self.assertIsNone(val2[3], "Observed time must remain None when unverified (no guessing)")
        self.assertEqual(val2[6], "pre_race_unverified_time")

        # 発走後データの拒絶 (fetched_at >= race_start_at)
        rec_post_start = dict(rec_valid, fetched_at="2026-10-25T06:55:00+09:00")
        with self.assertRaises(ValueError):
            validate_pre_race_record(rec_post_start, now_utc=now)

        # 未来データの拒絶 (fetched_at > now)
        rec_future = dict(rec_valid, fetched_at="2026-10-25T08:00:00+00:00")
        with self.assertRaises(ValueError):
            validate_pre_race_record(rec_future, now_utc=now)

    def test_3_pre_race_odds_append_only_ingest(self):
        """作業2: 追記型タイムライン保存の確認"""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "odds_history.db"

            now = datetime.datetime.fromisoformat("2026-10-25T06:30:00+00:00")
            r1 = {
                "race_id": "202610250101", "horse_number": 1, "odds_value": 2.5,
                "observed_at": "2026-10-25T06:10:00+00:00", "fetched_at": "2026-10-25T06:10:05+00:00",
                "race_start_at": "2026-10-25T06:40:00+00:00", "source": "sample"
            }
            r2 = {
                "race_id": "202610250101", "horse_number": 1, "odds_value": 2.3,
                "observed_at": "2026-10-25T06:20:00+00:00", "fetched_at": "2026-10-25T06:20:05+00:00",
                "race_start_at": "2026-10-25T06:40:00+00:00", "source": "sample"
            }

            c1 = ingest_pre_race_odds([r1], db_path=db_path, now_utc=now)
            c2 = ingest_pre_race_odds([r2], db_path=db_path, now_utc=now)
            self.assertEqual(c1, 1)
            self.assertEqual(c2, 1)

            # 同一レコード再登録はスキップ (idempotent)
            c3 = ingest_pre_race_odds([r2], db_path=db_path, now_utc=now)
            self.assertEqual(c3, 0)

            con = sqlite3.connect(db_path)
            total = con.execute("SELECT COUNT(*) FROM odds_timeline WHERE race_id='202610250101'").fetchone()[0]
            con.close()
            self.assertEqual(total, 2, "Both observations must be appended without overwriting")

    def test_4_official_settlement_reconciliation(self):
        """作業3: 公式払戻データの手動照合・登録の厳密性テスト"""
        with tempfile.TemporaryDirectory() as td:
            ledger_path = Path(td) / "paper_trading.db"
            connect(ledger_path)

            pred_at = "2026-10-25T06:20:00+00:00"
            cutoff_at = "2026-10-25T06:40:00+00:00"

            # 予測の記録
            capture(ledger_path, [
                {"race_id": "202610250101", "horse_number": 1, "odds": 2.5, "win_probability": 0.40,
                 "model_id": "model_v2", "prediction_at": pred_at, "cutoff_at": cutoff_at,
                 "odds_observed_at": pred_at, "odds_fetched_at": pred_at, "source": "sample"},
                {"race_id": "202610250101", "horse_number": 2, "odds": 5.0, "win_probability": 0.20,
                 "model_id": "model_v2", "prediction_at": pred_at, "cutoff_at": cutoff_at,
                 "odds_observed_at": pred_at, "odds_fetched_at": pred_at, "source": "sample"}
            ])

            # 1. 存在しない予測 (馬番99) の照合拒絶
            with self.assertRaises(ValueError):
                record_single_official_settlement(
                    ledger_path=ledger_path, race_id="202610250101", horse_number=99,
                    won=1, official_payout_yen=350, source_url="https://www.jra.go.jp/sample", verified_by="auditor"
                )

            # 2. 不正な払戻金額 (敗退なのに払戻金あり) の拒絶
            with self.assertRaises(ValueError):
                record_single_official_settlement(
                    ledger_path=ledger_path, race_id="202610250101", horse_number=2,
                    won=0, official_payout_yen=100, source_url="https://www.jra.go.jp/sample", verified_by="auditor"
                )

            # 3. 正常な精算の登録
            res1 = record_single_official_settlement(
                ledger_path=ledger_path, race_id="202610250101", horse_number=1,
                won=1, official_payout_yen=250, source_url="https://www.jra.go.jp/sample", verified_by="auditor"
            )
            self.assertEqual(res1["status"], "success")
            self.assertEqual(res1["payout"], 250)

            # 4. 二重精算の拒絶
            with self.assertRaises(ValueError):
                record_single_official_settlement(
                    ledger_path=ledger_path, race_id="202610250101", horse_number=1,
                    won=1, official_payout_yen=250, source_url="https://www.jra.go.jp/sample", verified_by="auditor"
                )

            # 5. 敗退馬の精算
            res2 = record_single_official_settlement(
                ledger_path=ledger_path, race_id="202610250101", horse_number=2,
                won=0, official_payout_yen=0, source_url="https://www.jra.go.jp/sample", verified_by="auditor"
            )
            self.assertEqual(res2["status"], "success")

            rep = report(ledger_path)
            self.assertEqual(rep["predictions"], 2)
            self.assertEqual(rep["settled"], 2)
            self.assertEqual(rep["unsettled"], 0)
            self.assertEqual(rep["stake_yen"], 200)
            self.assertEqual(rep["returns_yen"], 250)
            self.assertEqual(rep["profit_yen"], 50)
            self.assertAlmostEqual(rep["roi"], 1.25)

    def test_5_production_db_immutability(self):
        """作業4: 本番データベースのSHA-256不変検証"""
        expected_hash = "f2655215907eaf152f8b6bd7d21a1b6bebf1ff680033a526e89c6bc83e612ea1"
        actual_hash = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
        self.assertEqual(actual_hash, expected_hash, "Production DB must remain unchanged!")


if __name__ == "__main__":
    unittest.main()
