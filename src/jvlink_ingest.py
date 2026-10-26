"""Offline-testable JV-Link reader. Never silently synthesize races or odds.

Adapter implements SDK calls JVInit/JVOpen/JVRead/JVClose and stores raw bytes.
JV-Data decoding must use official record layouts, not guessed offsets.
"""
from __future__ import annotations
from datetime import datetime, timezone
import time
from src.jvlink_raw_store import store_records


class JVLinkError(RuntimeError):
    pass


def _code(result):
    if isinstance(result, tuple):
        return int(result[0])
    return int(result)


def capture(link, db_path, *, sid, data_spec, from_time, max_records=100000,
            buffer_size=110000, fetched_at=None, sleep=time.sleep,
            max_wait_seconds=60):
    if data_spec not in ("0B15", "0B30", "0B41"):
        raise ValueError("Unsupported data spec")
    if not isinstance(sid, str) or not sid or len(sid) > 32:
        raise ValueError("Invalid JV-Link software ID")
    if max_records < 1 or max_records > 1000000:
        raise ValueError("Invalid record limit")
    if buffer_size < 4096 or buffer_size > 1000000:
        raise ValueError("Invalid buffer size")
    if not isinstance(from_time, str) or len(from_time) != 12 or not from_time.isdigit():
        raise ValueError("from_time must be YYYYMMDDHHMM")
    if max_wait_seconds < 0 or max_wait_seconds > 600:
        raise ValueError("Invalid wait timeout")
    opened = False
    records = []
    try:
        init_result = _code(link.JVInit(sid))
        if init_result != 0:
            raise JVLinkError("JVInit failed: " + str(init_result))
        open_result = link.JVOpen(data_spec, from_time, 0)
        if isinstance(open_result, tuple):
            rc = int(open_result[0])
        else:
            rc = int(open_result)
        if rc < 0:
            raise JVLinkError("JVOpen failed: " + str(rc))
        opened = True
        waiting_since = None
        for _ in range(max_records):
            response = link.JVRead(buffer_size)
            if not isinstance(response, tuple) or len(response) < 2:
                raise JVLinkError("Unexpected JVRead result; SDK adapter needs adjustment")
            rc = int(response[0])
            if rc == 0:
                break
            if rc == -1:
                continue  # Skip file switch marker.
            if rc == -3:
                if waiting_since is None:
                    waiting_since = time.monotonic()
                if time.monotonic() - waiting_since >= max_wait_seconds:
                    raise JVLinkError("JVRead download wait timed out")
                sleep(0.5)
                continue
            waiting_since = None
            if rc < 0:
                raise JVLinkError("JVRead failed: " + str(rc))
            raw = response[1]
            if isinstance(raw, str):
                raw = raw.encode("cp932", errors="strict")
            if not isinstance(raw, bytes):
                raise JVLinkError("JVRead did not return bytes or CP932 text")
            if len(raw) < rc:
                raise JVLinkError("JVRead buffer shorter than declared length")
            records.append(raw[:rc])
        else:
            raise JVLinkError("JVRead record limit reached without EOF")
        return store_records(db_path, records, data_spec=data_spec,
                             fetched_at=fetched_at or datetime.now(timezone.utc).isoformat())
    finally:
        if opened:
            link.JVClose()
