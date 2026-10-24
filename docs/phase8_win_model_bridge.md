# Phase 8: Win-model prediction export (draft integration)

Adds an explicit WIN-model adapter to Phase 7. It does NOT train the model,
fetch odds, or automatically build leakage-safe features. The feature file
must be a real pre-race snapshot with a documented provenance.

IMPORTANT: The historical data/features.parquet cache may include
post-race fields; NEVER feed it blindly into this workflow. Antigravity must
verify that every feature used by the WIN model was available as-of the
declared feature cutoff, that the entire card is present, and that the model
is an authenticated WIN model (not the 3-place model).

Example (replace placeholders with actual verified timestamps/files):
python scripts/phase8_win_model_bridge.py --features /path/to/verified_pre_race_features.csv --model models/keiba_lgbm_win_v2_28cols.joblib --output /path/to/predictions.csv --feature-cutoff-at ISO8601 --prediction-at ISO8601 --cutoff-at ISO8601 --source pre_race_verified

Then run the Phase 7 capture command with the exported predictions CSV and
independently verified pre-race odds CSV.

The model loader may attempt recovery from a backup if verification fails;
test only on an isolated working copy. Never modify production model/DB/cache.
Run full suites and E2E on the feature branch; do not merge or deploy.
