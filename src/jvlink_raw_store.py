"""JV-Link raw record ingestion boundary.

JV-Data is fixed-width Shift-JIS; never guess byte offsets. This module
stores unmodified record bytes and receipt metadata for later decoding with
the official SDK structure definitions. It does not claim parsed odds.
"""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def store_records(db_path, records, *, data_spec, fetched_at=None):
    if data_spec not in {"0B15", "0B30", "0B41"}:
        raise ValueError("Unsupported JV-Data spec")
    received = fetched_at or datetime.now(timezone.utc).isoformat()
    stamp = datetime.fromisoformat(received.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Timezone-aware receipt required")
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS jv_raw_records (
            sha256 TEXT NOT NULL, data_spec TEXT NOT NULL,
            record_type TEXT NOT NULL, fetched_at TEXT NOT NULL,
            payload BLOB NOT NULL,
            PRIMARY KEY (sha256, data_spec)
        )""")
        for raw in records:
            if not isinstance(raw, bytes) or len(raw) < 2:
                raise ValueError("Expected raw JV-Data bytes")
            record_type = raw[:2].decode("ascii", errors="strict")
            if not record_type.isalnum():
                raise ValueError("Invalid record type")
            digest = hashlib.sha256(raw).hexdigest()
            conn.execute("""INSERT OR IGNORE INTO jv_raw_records
                (sha256, data_spec, record_type, fetched_at, payload)
                VALUES (?, ?, ?, ?, ?)""",
                (digest, data_spec, record_type, received, raw))
            count += 1
    return {"status": "RAW_RECORDS_STORED_UNPARSED", "received": count,
            "data_spec": data_spec, "bet_status": "NO_BET"}


def check_runtime():
    import platform
    if platform.system() != "Windows":
        return {"status": "WINDOWS_REQUIRED", "com_available": False}
    try:
        import win32com.client
        return {"status": "COM_LIBRARY_READY", "com_available": True,
                "jvlink_installed": "NOT_TESTED"}
    except ImportError:
        return {"status": "PYWIN32_REQUIRED", "com_available": False}


def open_jvlink(*, sid="KEIBA-AI"):
    """Instantiate COM only; do not call JVInit (may begin SDK usage)."""
    runtime = check_runtime()
    if not runtime["com_available"]:
        raise RuntimeError(runtime["status"])
    import win32com.client
    return win32com.client.Dispatch("JVDTLab.JVLink")
