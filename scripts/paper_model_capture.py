"""Capture model scores using supplied pre-race features and time-stamped odds."""
import argparse
import datetime as dt
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.paper_model_bridge import capture_from_csv
from src.feature_provenance import verify_snapshot

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for key in ('features','odds-db','ledger-db','race-id','cutoff','model'):
        p.add_argument('--'+key, required=True)
    a = p.parse_args()
    # Generate at the actual time of invocation; no arbitrary historical timestamp override.
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest = verify_snapshot(a.features, a.race_id, now)
    print(capture_from_csv(a.features,a.odds_db,a.ledger_db,a.race_id,now,a.cutoff,manifest['created_at'],a.model))
