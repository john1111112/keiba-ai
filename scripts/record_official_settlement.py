"""
Phase 6: JRA公式確定払戻データ 手動登録・照合CLI
- JRA公式サイトの成績速報をもとに、人間が目視確認した単勝確定払戻を登録する
- 模擬運用台帳 (data/paper_trading.db) の予測レコードと race_id / horse_number で厳密に照合
- 架空データや不一致データは拒絶し登録しない
- 二重登録を防止 (すでに精算済みのレコードは拒絶)
"""
import argparse
import datetime
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.paper_trading import connect, settle, report, utc_time
from src.verified_settlement import REQUIRED


def record_single_official_settlement(
    ledger_path: Path,
    race_id: str,
    horse_number: int,
    won: int,
    official_payout_yen: int,
    source_url: str,
    verified_by: str,
    settled_at: Optional[str] = None
) -> dict:
    """単一の公式確定払戻を台帳に照合・登録する"""
    if won not in (0, 1):
        raise ValueError("won must be 1 (win) or 0 (lost)")
    if won == 0 and official_payout_yen != 0:
        raise ValueError("official_payout_yen must be 0 for lost bet")
    if won == 1 and official_payout_yen < 100:
        raise ValueError("official_payout_yen must be >= 100 for winning bet")
    if not source_url.strip().startswith(("https://", "http://")):
        raise ValueError("source_url must be a valid official URL (e.g. https://www.jra.go.jp/...)")
    if not verified_by.strip():
        raise ValueError("verified_by is required (person/auditor name)")

    now_utc = settled_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    clean_settled_at = utc_time(now_utc)

    with connect(ledger_path) as con:
        # 該当する予測が存在するか照合
        cur = con.execute("""
            SELECT prediction_at FROM predictions 
            WHERE race_id = ? AND horse_number = ?
            ORDER BY prediction_at DESC LIMIT 1
        """, (race_id, horse_number))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"No prediction found in ledger for race_id={race_id}, horse_number={horse_number}")

        prediction_at = row[0]

        # 既に精算済みか確認
        cur = con.execute("""
            SELECT 1 FROM settlements 
            WHERE race_id = ? AND horse_number = ? AND prediction_at = ?
        """, (race_id, horse_number, prediction_at))
        if cur.fetchone():
            raise ValueError(f"Race {race_id} Horse {horse_number} is already settled")

        # 精算レコードの作成・適用
        record = {
            "race_id": race_id,
            "horse_number": horse_number,
            "prediction_at": prediction_at,
            "won": won,
            "official_payout_yen": official_payout_yen,
            "settled_at": clean_settled_at
        }
        settle(ledger_path, [record])

    rep = report(ledger_path)
    return {
        "status": "success",
        "race_id": race_id,
        "horse_number": horse_number,
        "won": won,
        "payout": official_payout_yen,
        "current_roi": rep.get("roi"),
        "total_settled": rep.get("settled")
    }


def main():
    parser = argparse.ArgumentParser(description="JRA公式単勝確定払戻の手動登録・照合ツール")
    parser.add_argument("--ledger", type=Path, default=REPO_ROOT / "data" / "paper_trading.db")
    parser.add_argument("--race-id", type=str, required=True, help="12桁のJRA公式レースID")
    parser.add_argument("--horse-number", type=int, required=True, help="馬番 (1〜28)")
    parser.add_argument("--won", type=int, choices=[0, 1], required=True, help="1: 1着的中, 0: 敗退")
    parser.add_argument("--payout", type=int, default=0, help="100円当たりの公式確定払戻金額 (的中時のみ)")
    parser.add_argument("--source-url", type=str, required=True, help="公式確認元URL (例: https://www.jra.go.jp/...)")
    parser.add_argument("--verified-by", type=str, required=True, help="確認者氏名・ID")

    args = parser.parse_args()

    try:
        res = record_single_official_settlement(
            ledger_path=args.ledger,
            race_id=args.race_id,
            horse_number=args.horse_number,
            won=args.won,
            official_payout_yen=args.payout,
            source_url=args.source_url,
            verified_by=args.verified_by
        )
        print("【登録完了】", res)
    except Exception as e:
        print(f"【登録拒絶】エラー: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
