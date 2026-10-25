# Local validation: runbook and acceptance gates

This is a **development-only** validation procedure. It is not a sealed test, certified pre-race prediction, betting strategy, or verified ROI.

## Preconditions

- Checkout the stacked branch `feature/wire-validation-baseline` (or this branch).
- Have a local `data/keiba.db` with tables `races` and `race_results`. Do not upload this DB or 2026 outcome labels to GitHub.
- **Do not give Antigravity access to the separate 2026 holdout labels**, including through filesystem mounts, plugins, terminals, logs, CI artifacts or connected tools. If it has already seen them, designate a fresh future holdout.
- `races.date` must be ISO `YYYY-MM-DD`; finish_order must be interpretable as 1 for winners.
- Run in a Python environment that supports the project modules.

## Commands (from repository root)

```bash
python -m unittest discover -s tests -p 'test_validation_weight_tuning.py' -v
python -m unittest discover -s tests -p 'test_validation_baseline_comparison.py' -v
python -m unittest discover -s tests -p 'test_local_validation_tuning.py' -v
python -m scripts.local_validation_tuning --db data/keiba.db --train-start 2022-01-01 --train-end 2024-12-31 --validation-start 2025-01-01 --validation-end 2025-12-31 --out data/reports/validation_2025.json
```

Use Windows PowerShell or terminal. If the output file exists, choose a **new** name rather than overwriting a frozen report.

## Report interpretation

- `selection.weights`: chosen from validation labels, **not learned exclusively from training labels**.
- `validation_baseline_comparison.models.weighted` vs `uniform`: diagnostic on the **same validation data** that selected the weights. This is selection-biased, not an unbiased generalization estimate.
- `train_races` and `validation_races`: check sample size before drawing conclusions.
- `asof_status=NOT_CERTIFIED`: actual feature observation times have not been authenticated.
- `holdout_status=NOT_ACCESSED`: this runner does not query holdout; it does **not** establish global access isolation.
- `roi=null`: expected; never infer profitability without genuine pre-decision odds and verified payouts.

## Required evidence to share

Provide test pass/fail logs; program exit code; dataset schema summary; report JSON with sample counts, selected weights, both models' metrics, and any skipped/malformed races. Avoid including raw holdout labels.

## Stop conditions

If tests fail, missing schema fields, empty train/validation periods, inconsistent date formats, or unexplained runner exclusions: stop and repair before reporting performance. **Do not** backfill missing pre-race timestamps with fabricated values.

## Next engineering steps

1. Record coverage and exclusion reasons in the loader (current implementation silently excludes malformed races).
2. Establish independently auditable as-of provenance for each candidate feature.
3. Freeze model/policy before a genuinely isolated evaluation; use an independent grader.
4. Add an authentic market odds baseline only when historical decision-time snapshots are verified.
