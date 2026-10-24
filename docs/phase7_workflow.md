# Phase 7 paper workflow (initial integration)

This is a CSV bridge, not automatic odds collection or automatic model inference.
All predictions and probabilities must be supplied by the existing model pipeline.
No live betting. No main merge or deployment.

Run from repository root:
python -m unittest discover -s tests -p test_phase7_workflow.py -v
python scripts/phase7_workflow.py capture --predictions predictions.csv --odds odds.csv --ledger /path/to/paper_ledger.db
python scripts/phase7_workflow.py capture --predictions predictions.csv --odds odds.csv --ledger /path/to/paper_ledger.db --apply
python scripts/phase7_workflow.py report --ledger /path/to/paper_ledger.db
python scripts/phase7_workflow.py settle --csv verified_settlements.csv --ledger /path/to/paper_ledger.db --apply

Capture dry-run is read-only. NOTE: Phase 6 settlement validation calls connect(),
which may initialize/migrate the ledger even without --apply. Always use a copy
of a real ledger for settlement dry-runs. Use only test DBs for integration tests.

CSV headers:
predictions.csv: race_id,horse_number,prediction_at,cutoff_at,win_probability,model_id,source
odds.csv: race_id,horse_number,odds_value,observed_at,fetched_at,race_start_at,source

All timestamps require ISO 8601 timezone offsets. This bridge validates temporal
ordering and requires one odds snapshot per race/horse. It does not independently
authenticate the provenance of manually supplied CSVs.

Antigravity: run Phase 5/6/7 suites and E2E, inspect diff and CI, fix integration
issues on this feature branch only. Do not merge main or deploy.
