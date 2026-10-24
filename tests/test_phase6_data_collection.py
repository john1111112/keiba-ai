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
    get_verified_pre_race_odds,
)
from src.paper_trading import connect, capture, report, settle
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

    def test_6_require_start_time_for_prediction_inputs(self):
        """指示1: 発走時刻 (race_start_at) が不明なオッズは正式な予測入力から隔離・拒絶"""
        now = datetime.datetime.fromisoformat("2026-10-25T06:30:00+00:00")
        rec_no_start = {
            "race_id": "202610250101",
            "horse_number": 1,
            "odds_value": 3.0,
            "observed_at": "2026-10-25T06:20:00+00:00",
            "fetched_at": "2026-10-25T06:20:05+00:00",
            "source": "sample"
            # race_start_at なし
        }
        # 1. 予測入力バリデーション (require_start_time=True) では即時拒絶
        with self.assertRaises(ValueError):
            validate_pre_race_record(rec_no_start, now_utc=now, require_start_time=True)

        # 2. 一般取り込み時も 'isolated_missing_start_time' として隔離保存
        val = validate_pre_race_record(rec_no_start, now_utc=now, require_start_time=False)
        self.assertEqual(val[6], "isolated_missing_start_time")

    def test_7_unverified_observed_time_excluded_from_verified_odds(self):
        """指示3: 発表時刻が未確認のオッズは保存できても、検証済み発走前オッズとして扱わない"""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "odds_history.db"
            now = datetime.datetime.fromisoformat("2026-10-25T06:30:00+00:00")

            # 発表時刻未確認オッズ (observed_at=None)
            r_unverified = {
                "race_id": "202610250101", "horse_number": 1, "odds_value": 3.5,
                "observed_at": None, "fetched_at": "2026-10-25T06:20:00+00:00",
                "race_start_at": "2026-10-25T06:40:00+00:00", "source": "sample"
            }
            # 発表時刻確認済みオッズ (observed_atあり)
            r_verified = {
                "race_id": "202610250101", "horse_number": 2, "odds_value": 5.0,
                "observed_at": "2026-10-25T06:20:00+00:00", "fetched_at": "2026-10-25T06:20:00+00:00",
                "race_start_at": "2026-10-25T06:40:00+00:00", "source": "sample"
            }
            ingest_pre_race_odds([r_unverified, r_verified], db_path=db_path, now_utc=now)

            # 検証済みオッズの抽出
            verified_odds = get_verified_pre_race_odds(
                "202610250101", as_of_time="2026-10-25T06:25:00+00:00", db_path=db_path
            )
            h_nums = [r["horse_number"] for r in verified_odds]
            self.assertIn(2, h_nums, "Verified odds must include horse 2")
            self.assertNotIn(1, h_nums, "Unverified-time odds must be strictly excluded from verified odds")

    def test_8_enhanced_verified_payouts_provenance_check(self):
        """指示2: get_verified_payouts() は来歴・確認者・確認日時のない未確認データを除外"""
        with tempfile.TemporaryDirectory() as td:
            ledger_path = Path(td) / "paper_trading.db"
            connect(ledger_path)

            pred_at = "2026-10-25T06:20:00+00:00"
            cutoff_at = "2026-10-25T06:40:00+00:00"

            capture(ledger_path, [
                {"race_id": "202610250101", "horse_number": 1, "odds": 2.5, "win_probability": 0.40,
                 "model_id": "model_v2", "prediction_at": pred_at, "cutoff_at": cutoff_at,
                 "odds_observed_at": pred_at, "odds_fetched_at": pred_at, "source": "sample"}
            ])
            # 公式確認済み払戻を登録
            record_single_official_settlement(
                ledger_path=ledger_path, race_id="202610250101", horse_number=1,
                won=1, official_payout_yen=250, source_url="https://www.jra.go.jp/sample", verified_by="auditor_test"
            )

            # 1. 真正性が証明された台帳からの取得 -> 正常に取得できること
            v_payouts = get_verified_payouts("202610250101", ledger_path=ledger_path)
            self.assertEqual(len(v_payouts), 1)
            self.assertEqual(v_payouts[0]["payout"], 250)
            self.assertEqual(v_payouts[0]["status"], "verified")

            # 2. 来歴情報のない未確認データ (未精算のレース 202610250102) -> 空リスト返却
            unverified = get_verified_payouts("202610250102", ledger_path=ledger_path)
            self.assertEqual(unverified, [], "Unverified race without provenance must return empty list")

    def test_9_payouts_missing_columns_safe_handling(self):
        """指示5: payoutsテーブルにstatusやverified_at等のカラムが存在しない場合でもOperationalErrorにならず安全に除外"""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test_missing_cols.db"
            ledger_path = Path(td) / "empty_ledger.db"
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("""
            CREATE TABLE races (
                race_id TEXT PRIMARY KEY,
                race_name TEXT
            )
            """)
            cur.execute("""
            CREATE TABLE payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                bet_type TEXT NOT NULL,
                combination TEXT NOT NULL,
                payout INTEGER NOT NULL,
                popularity INTEGER
            )
            """)
            cur.execute("INSERT INTO races (race_id, race_name) VALUES ('202610250101', 'Test Race')")
            cur.execute("INSERT INTO payouts (race_id, bet_type, combination, payout, popularity) VALUES ('202610250101', '単勝', '1', 300, 1)")
            conn.commit()
            conn.close()

            # カラム不足のDBに対してget_verified_payoutsを実行 -> エラーにならず空リスト返却
            res = get_verified_payouts("202610250101", db_path=db_path, ledger_path=ledger_path)
            self.assertEqual(res, [], "payouts without status/source_url must safely return empty list without SQL errors")

    def test_10_settlements_unverified_and_invalid_status_excluded(self):
        """指示3, 4, 5: settlementsテーブルから取得時、不正な確認状態・未確認データ・情報不足データを厳格に除外"""
        with tempfile.TemporaryDirectory() as td:
            ledger_path = Path(td) / "paper_trading.db"
            connect(ledger_path)

            pred_at = "2026-10-25T06:20:00+00:00"
            cutoff_at = "2026-10-25T06:40:00+00:00"

            capture(ledger_path, [
                {"race_id": "202610250201", "horse_number": 1, "odds": 2.0, "win_probability": 0.5,
                 "model_id": "m1", "prediction_at": pred_at, "cutoff_at": cutoff_at,
                 "odds_observed_at": pred_at, "odds_fetched_at": pred_at, "source": "sample"},
                {"race_id": "202610250201", "horse_number": 2, "odds": 3.0, "win_probability": 0.3,
                 "model_id": "m1", "prediction_at": pred_at, "cutoff_at": cutoff_at,
                 "odds_observed_at": pred_at, "odds_fetched_at": pred_at, "source": "sample"},
                {"race_id": "202610250201", "horse_number": 3, "odds": 4.0, "win_probability": 0.2,
                 "model_id": "m1", "prediction_at": pred_at, "cutoff_at": cutoff_at,
                 "odds_observed_at": pred_at, "odds_fetched_at": pred_at, "source": "sample"},
                {"race_id": "202610250201", "horse_number": 4, "odds": 5.0, "win_probability": 0.1,
                 "model_id": "m1", "prediction_at": pred_at, "cutoff_at": cutoff_at,
                 "odds_observed_at": pred_at, "odds_fetched_at": pred_at, "source": "sample"},
            ])

            # 正常レコード (horse 1): verified
            record_single_official_settlement(
                ledger_path=ledger_path, race_id="202610250201", horse_number=1,
                won=1, official_payout_yen=200, source_url="https://www.jra.go.jp/official", verified_by="auditor_ok"
            )

            # 直接DBを操作して不正な状態をシミュレーション
            con = sqlite3.connect(ledger_path)
            try:
                # horse 2: status = 'pending' (未確認)
                con.execute("""
                INSERT INTO settlements (
                    race_id, horse_number, prediction_at, won, official_payout_yen, settled_at,
                    source_url, verified_by, verification_status
                ) VALUES (
                    '202610250201', 2, ?, 1, 300, '2026-10-25T07:00:00+00:00',
                    'https://www.jra.go.jp/official', 'auditor_ok', 'pending'
                )
                """, (pred_at,))
                # horse 3: source_url が不正（URL形式でない）
                con.execute("""
                INSERT INTO settlements (
                    race_id, horse_number, prediction_at, won, official_payout_yen, settled_at,
                    source_url, verified_by, verification_status
                ) VALUES (
                    '202610250201', 3, ?, 1, 400, '2026-10-25T07:00:00+00:00',
                    'not_a_valid_url', 'auditor_ok', 'verified'
                )
                """, (pred_at,))
                # horse 4: verified_by が欠損 (None/unknown)
                con.execute("""
                INSERT INTO settlements (
                    race_id, horse_number, prediction_at, won, official_payout_yen, settled_at,
                    source_url, verified_by, verification_status
                ) VALUES (
                    '202610250201', 4, ?, 1, 500, '2026-10-25T07:00:00+00:00',
                    'https://www.jra.go.jp/official', 'unknown', 'verified'
                )
                """, (pred_at,))
                con.commit()
            finally:
                con.close()

            # 検証済み払戻を取得
            v_list = get_verified_payouts("202610250201", ledger_path=ledger_path)

            # horse 1 のみ取得され、horse 2, 3, 4 は厳格に除外されること
            combinations = [v["combination"] for v in v_list]
            self.assertEqual(len(v_list), 1)
            self.assertEqual(combinations, [1])
            self.assertEqual(v_list[0]["payout"], 200)
            self.assertEqual(v_list[0]["status"], "verified")

    def test_5_production_db_immutability(self):
        """作業4: 本番データベースの不変検証 (CI上コミットハッシュ または ローカル実運用ハッシュ と一致)"""
        valid_hashes = {
            "f2655215907eaf152f8b6bd7d21a1b6bebf1ff680033a526e89c6bc83e612ea1",  # ローカル実運用バックアップハッシュ
            "a653bca16a9dcd7ed982e40f23310ce246069bf0759f98afe91ffb191d475104",  # リポジトリコミット追跡ハッシュ
        }
        actual_hash = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
        self.assertIn(actual_hash, valid_hashes, f"Production DB has unexpected hash: {actual_hash}")


if __name__ == "__main__":
    unittest.main()
