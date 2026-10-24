# Phase 9: pre-race gate (initial draft)

Read-only audit of Phase 8 predictions and Phase 6 verified-format odds
against a race manifest. Requires matching race identifiers, start time,
prediction timestamp, source, and card size, and validates timestamp ordering.

This is a metadata consistency gate ONLY. A self-declared timestamp, SHA,
or source string is not proof of actual collection time or JRA authenticity.
Do not claim verified live acquisition or real-world prediction readiness.

Manifest JSON fields: race_id, race_start_at, feature_cutoff_at,
prediction_at, source, feature_sha256, model_sha256.
Both hashes must be 64-character hexadecimal SHA-256 strings.
Hashes are currently declared metadata, not checked against source files.
Never use this gate as proof of source integrity until hash verification and
trusted acquisition attestations are implemented.

python scripts/phase9_pre_race_gate.py --predictions predictions.csv --odds odds.csv --manifest manifest.json

No ledger writes, real betting, scraping, or production deployment.
