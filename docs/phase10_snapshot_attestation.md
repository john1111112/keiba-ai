# Phase 10: Local snapshot receipt (draft)

Records raw local input bytes in an append-only file alongside an SHA-256
receipt and a UTC observation timestamp. Verify rehashes the archived bytes.

This does NOT acquire data automatically from JRA, establish source
authenticity, attest the original publication time, or prove that the
input file was not modified before observation. Use only with data obtained
through permitted means. It is not a substitute for an authenticated provider
API and does not yet feed Phase 9's manifest hashes automatically.

Example:
python scripts/phase10_snapshot_attestation.py capture --source-path /path/to/odds.csv --archive-dir /path/to/existing/isolated_archive --race-id 202610250101 --race-start-at 2026-10-25T15:00:00+09:00 --source-name licensed-local-import
python scripts/phase10_snapshot_attestation.py verify --receipt /path/to/receipt.json

Do not run against production DB or overwrite production data. Local receipts
are not tamper-proof against an attacker who can rewrite both payload and
receipt; trusted remote attestations are a future task.
