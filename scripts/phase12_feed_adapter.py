"""Phase 12: Licensed Pre-Race Feed Adapter.

Reads authorized local feed fixtures, captures Phase 10 snapshots,
and exports normalized pre-race odds for Phase 9 & Phase 11 gates.

No network scraping, external HTTP calls, or real bets.
Fail-closed on unverified license or any data invalidity.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

from scripts.phase10_snapshot_attestation import capture_snapshot
from src.paper_trading import utc_time

ALLOWED_LICENSE_STATUS = 'verified_licensed'

def ingest_feed_fixture(
    fixture_path,
    *,
    race_id,
    race_start_at,
    provider_id,
    license_status,
    archive_dir=None,
    normalized_output_path=None,
    now_utc=None
):
    """Ingest an authorized local feed fixture and validate strict feed invariants."""
    # 1. License Verification (Fail-Closed)
    if license_status != ALLOWED_LICENSE_STATUS:
        raise ValueError(
            f"unverified or missing license: '{license_status}'. "
            f"Only '{ALLOWED_LICENSE_STATUS}' is accepted."
        )

    # 2. Basic Parameter Validation
    if not provider_id or not provider_id.strip():
        raise ValueError("provider_id required")
    if not race_id or not race_id.isalnum():
        raise ValueError("valid alphanumeric race_id required")

    # 3. Prevent Network / Remote Fetch
    fixture_str = str(fixture_path).lower()
    if fixture_str.startswith('http://') or fixture_str.startswith('https://') or fixture_str.startswith('ftp://'):
        raise ValueError("remote network fetch is prohibited in Phase 12 adapter")

    fixture = Path(fixture_path)
    if not fixture.is_file() or fixture.is_symlink():
        raise ValueError("regular source fixture file required")

    # 4. Measure Local Observation Time (Independent of CSV self-reported times)
    system_now = dt.datetime.now(dt.timezone.utc)
    observed = now_utc or system_now
    if observed.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    observed = observed.astimezone(dt.timezone.utc)

    # Future observation guard (against actual system clock)
    if observed > system_now + dt.timedelta(seconds=60):
        raise ValueError("future observation timestamp rejected")

    # Race start time verification
    start_iso = utc_time(race_start_at)
    start_dt = dt.datetime.fromisoformat(start_iso)
    if observed >= start_dt:
        raise ValueError("feed observation is post-start: refuse ingest")

    # 5. Read Raw Bytes & Cryptographic Checksum
    raw_bytes = fixture.read_bytes()
    if not raw_bytes:
        raise ValueError("empty fixture file rejected")
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # 6. Parse and Validate Feed CSV Records
    try:
        content = raw_bytes.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError("fixture must be utf-8 encoded") from exc

    reader = csv.DictReader(content.splitlines())
    required_cols = {'race_id', 'horse_number', 'odds_value'}
    if not required_cols.issubset(set(reader.fieldnames or [])):
        missing = sorted(required_cols - set(reader.fieldnames or []))
        raise ValueError(f"fixture missing required columns: {','.join(missing)}")

    records = []
    seen_horses = set()
    upstream_published_at = None

    for idx, row in enumerate(reader, start=1):
        # Race ID matching
        row_race = row.get('race_id', '').strip()
        if row_race != race_id:
            raise ValueError(f"row {idx}: race_id mismatch (expected '{race_id}', got '{row_race}')")

        # Horse number validation (1-18, no duplicates)
        try:
            horse_num = int(row['horse_number'])
            if not (1 <= horse_num <= 18):
                raise ValueError()
        except Exception:
            raise ValueError(f"row {idx}: invalid horse_number '{row.get('horse_number')}'")

        if horse_num in seen_horses:
            raise ValueError(f"row {idx}: duplicate horse_number {horse_num}")
        seen_horses.add(horse_num)

        # Odds value validation (>= 1.0, finite float)
        try:
            val = float(row['odds_value'])
            if math.isnan(val) or math.isinf(val) or val < 1.0:
                raise ValueError()
        except Exception:
            raise ValueError(f"row {idx}: invalid odds_value '{row.get('odds_value')}'")

        # Optional upstream publication timestamp extraction
        pub_raw = row.get('upstream_published_at') or row.get('published_at')
        if pub_raw and pub_raw.strip():
            try:
                pub_iso = utc_time(pub_raw.strip())
                if upstream_published_at is None:
                    upstream_published_at = pub_iso
                elif upstream_published_at != pub_iso:
                    # Inconsistent published timestamps within the same snapshot
                    raise ValueError(f"row {idx}: conflicting upstream_published_at")
            except Exception as e:
                raise ValueError(f"row {idx}: invalid upstream_published_at: {e}")

        records.append({
            'race_id': race_id,
            'horse_number': horse_num,
            'odds_value': f"{val:.1f}" if val.is_integer() else f"{val:.2f}".rstrip('0').rstrip('.'),
            'odds_float': val
        })

    if not records:
        raise ValueError("no odds records found in fixture")

    # Sort records deterministically by horse_number
    records.sort(key=lambda x: x['horse_number'])

    # 7. Export Normalized Pre-Race Odds CSV for Phase 9 & Phase 11 Gates
    normalized_path_str = None
    target_to_archive = fixture
    if normalized_output_path:
        out_path = Path(normalized_output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'race_id', 'horse_number', 'odds_value',
                'observed_at', 'fetched_at', 'race_start_at', 'source'
            ])
            obs_iso = observed.isoformat()
            for r in records:
                writer.writerow([
                    r['race_id'],
                    r['horse_number'],
                    f"{r['odds_float']:.1f}",
                    obs_iso,
                    obs_iso,
                    start_iso,
                    provider_id
                ])
        normalized_path_str = str(out_path)
        target_to_archive = out_path

    # 8. Phase 10 Snapshot Attestation (Optional Archive)
    snapshot_receipt = None
    snapshot_receipt_path = None
    if archive_dir:
        archive_path = Path(archive_dir)
        archive_path.mkdir(parents=True, exist_ok=True)
        snapshot_receipt = capture_snapshot(
            target_to_archive,
            archive_path,
            race_id=race_id,
            race_start_at=start_iso,
            source_name=provider_id,
            observed_at=observed
        )
        snapshot_receipt_path = str(
            archive_path / snapshot_receipt['snapshot_file'].replace('.snapshot', '.receipt.json')
        )

    # 9. Return Contract Ingest Result
    return {
        'status': 'accepted',
        'provider_id': provider_id,
        'source_identifier': fixture.name,
        'observed_at_utc': observed.isoformat(),
        'upstream_published_at_utc': upstream_published_at,
        'race_id': race_id,
        'race_start_at': start_iso,
        'horse_count': len(records),
        'license_status': license_status,
        'raw_sha256': raw_sha256,
        'snapshot_receipt': snapshot_receipt,
        'snapshot_receipt_path': snapshot_receipt_path,
        'normalized_odds_path': normalized_path_str,
        'warning': (
            'Read-only fixture ingested under declared license status. '
            'No automated network scraping or upstream JRA connection established.'
        )
    }

def main(argv=None):
    p = argparse.ArgumentParser(description="Phase 12 Licensed Feed Adapter")
    p.add_argument('--fixture', required=True, help="Path to feed fixture CSV")
    p.add_argument('--race-id', required=True, help="Target race ID")
    p.add_argument('--race-start-at', required=True, help="Race start ISO timestamp")
    p.add_argument('--provider-id', required=True, help="Provider identifier")
    p.add_argument('--license-status', required=True, help="Declared license status")
    p.add_argument('--archive-dir', help="Optional Phase 10 snapshot archive directory")
    p.add_argument('--normalized-output', help="Optional output path for normalized odds CSV")
    args = p.parse_args(argv)

    res = ingest_feed_fixture(
        args.fixture,
        race_id=args.race_id,
        race_start_at=args.race_start_at,
        provider_id=args.provider_id,
        license_status=args.license_status,
        archive_dir=args.archive_dir,
        normalized_output_path=args.normalized_output
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
