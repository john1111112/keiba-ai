# Phase 11: Local receipt-backed audit (draft)

This adds a read-only integrity check joining Phase 10 receipt hashes to
Phase 9's manifest and odds CSV. The odds file must match its archived
snapshot byte-for-byte. Feature receipt hash must match the manifest,
but the feature CSV itself is not an input to this audit.

Required extra manifest field: odds_sha256 (64 hex chars).

python -m scripts.phase11_receipt_gate --predictions predictions.csv --odds odds.csv --manifest manifest.json --feature-receipt feature.receipt.json --odds-receipt odds.receipt.json

**Not an automated JRA feed.** The data supplier, rights, timestamp authority,
and as-of feature availability remain unverified. An operator can forge
local receipts; this is not cryptographic remote attestation. Do not use
this as evidence of an independently verified pre-race data source.
