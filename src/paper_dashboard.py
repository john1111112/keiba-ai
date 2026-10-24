"""Read-only paper trading metrics. Never creates or mutates the ledger."""
from pathlib import Path
import sqlite3


def load_dashboard(db_path):
    path = Path(db_path).resolve()
    if not path.is_file():
        return None
    uri = path.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        total, settled, payout_sum = conn.execute("""
            SELECT COUNT(*), COUNT(s.won), COALESCE(SUM(s.official_payout_yen), 0)
            FROM predictions p LEFT JOIN settlements s
              ON p.race_id=s.race_id AND p.horse_number=s.horse_number
              AND p.prediction_at=s.prediction_at
        """).fetchone()
        by_model = conn.execute("""
            SELECT p.model_id, COUNT(*), COUNT(s.won), COALESCE(SUM(s.official_payout_yen),0)
            FROM predictions p LEFT JOIN settlements s
              ON p.race_id=s.race_id AND p.horse_number=s.horse_number
              AND p.prediction_at=s.prediction_at
            GROUP BY p.model_id ORDER BY p.model_id
        """).fetchall()
    def metrics(n, k, paid):
        stake = k * 100
        return dict(predictions=n, settled=k, unsettled=n-k,
                    stake_yen=stake, returns_yen=paid,
                    profit_yen=paid-stake, roi=paid/stake if stake else None)
    return dict(overall=metrics(total,settled,payout_sum),
                models=[dict(model_id=model,**metrics(n,k,paid)) for model,n,k,paid in by_model])
