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


def validate_capture_args(*, sid, data_spec, from_time, max_records=100000,
                          buffer_size=110000, max_wait_seconds=60,
                          max_total_bytes=256 * 1024 * 1024):
    if data_spec not in ("0B15", "0B30", "0B41"):
        raise ValueError("Unsupported data spec")
    if not isinstance(sid, str) or not sid or len(sid) > 32:
        raise ValueError("Invalid JV-Link software ID")
    if not isinstance(max_records, int) or max_records < 1 or max_records > 1000000:
        raise ValueError("Invalid record limit")
    if not isinstance(buffer_size, int) or buffer_size < 4096 or buffer_size > 1000000:
        raise ValueError("Invalid buffer size")
    if not isinstance(from_time, str) or len(from_time) != 12 or not from_time.isdigit():
        raise ValueError("from_time must be YYYYMMDDHHMM")
    try:
        datetime.strptime(from_time, "%Y%m%d%H%M")
    except ValueError as exc:
        raise ValueError("Invalid JV-Link calendar timestamp") from exc
    if not isinstance(max_wait_seconds, (int, float)) or max_wait_seconds < 0 or max_wait_seconds > 600:
        raise ValueError("Invalid wait timeout")
    if not isinstance(max_total_bytes, int) or not 4096 <= max_total_bytes <= 1024 * 1024 * 1024:
        raise ValueError("Invalid capture byte budget")


def capture(link, db_path, *, sid, data_spec, from_time, max_records=100000,
            buffer_size=110000, fetched_at=None, sleep=time.sleep,
            max_wait_seconds=60, max_total_bytes=256 * 1024 * 1024):
    validate_capture_args(sid=sid, data_spec=data_spec, from_time=from_time,
                          max_records=max_records, buffer_size=buffer_size,
                          max_wait_seconds=max_wait_seconds, max_total_bytes=max_total_bytes)
    opened = False
    records = []
    wait_count = 0
    eof = False
    total_bytes = 0
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
                eof = True
                break
            if rc == -1:
                continue  # Skip file switch marker.
            if rc == -3:
                wait_count += 1
                if wait_count > 1200:
                    raise JVLinkError("JVRead exceeded maximum wait responses")
                if waiting_since is None:
                    waiting_since = time.monotonic()
                if time.monotonic() - waiting_since >= max_wait_seconds:
                    raise JVLinkError("JVRead download wait timed out")
                sleep(0.5)
                continue
            waiting_since = None
            wait_count = 0
            if rc < 0:
                raise JVLinkError("JVRead failed: " + str(rc))
            raw = response[1]
            if isinstance(raw, str):
                raise JVLinkError("JVRead returned decoded text; raw bytes are required to preserve official fixed-width offsets")
            if not isinstance(raw, bytes):
                raise JVLinkError("JVRead did not return raw bytes")
            if len(raw) < rc:
                raise JVLinkError("JVRead buffer shorter than declared length")
            total_bytes += rc
            if total_bytes > max_total_bytes:
                raise JVLinkError("JVRead capture exceeded byte budget; no partial capture committed")
            records.append(raw[:rc])
        else:
            raise JVLinkError("JVRead record limit reached without EOF")
        if not eof:
            raise JVLinkError("JVRead did not reach EOF")
        return store_records(db_path, records, data_spec=data_spec,
                             fetched_at=fetched_at or datetime.now(timezone.utc).isoformat())
    finally:
        if opened:
            link.JVClose()
