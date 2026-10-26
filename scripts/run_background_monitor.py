"""
常駐型バックグラウンド監視デーモン (Continuous Background Monitor Daemon)
- アプリ（Streamlit）画面を閉じていても、Windowsのバックグラウンドで定期実行を継続
- 指定秒数（デフォルト: 60秒）間隔でスキャンと推論、通知チェックを実行
- エラー発生時はログに記録し、プロセス停止せずに次回サイクルで自動復旧
- PIDファイルとステータスファイルを書き出し、外部（UI等）から稼働確認可能
"""
from __future__ import annotations
import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.run_background_scan import run_cycle

STATUS_PATH = REPO_ROOT / "data" / "monitor_daemon_status.json"
PID_PATH = REPO_ROOT / "data" / "monitor_daemon.pid"
LOCK_PATH = REPO_ROOT / "data" / "monitor_daemon.lock"

_keep_running = True
_lock_file_handle = None


def is_pid_alive(pid: int) -> bool:
    """プロセスIDが現在生存しているか確認（Windows/Unix対応）"""
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process != 0:
                kernel32.CloseHandle(process)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def acquire_process_lock() -> bool:
    """排他ロックを取得して多重起動を防止"""
    global _lock_file_handle
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 既存PIDの生存確認
        if PID_PATH.exists():
            try:
                existing_pid = int(PID_PATH.read_text(encoding="utf-8").strip())
                if existing_pid != os.getpid() and is_pid_alive(existing_pid):
                    print(f"[WARN] 既に別の監視デーモン (PID: {existing_pid}) が稼働中です。多重起動を防止し終了します。")
                    return False
            except Exception:
                pass

        _lock_file_handle = open(LOCK_PATH, "w", encoding="utf-8")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(_lock_file_handle.fileno(), msvcrt.LK_NBLCK, 1)
        _lock_file_handle.write(f"pid={os.getpid()}\n")
        _lock_file_handle.flush()
        return True
    except (IOError, OSError) as exc:
        print(f"[WARN] ロックファイルの取得に失敗しました (多重起動防止): {exc}")
        return False


def release_process_lock() -> None:
    """排他ロックを解放"""
    global _lock_file_handle
    if _lock_file_handle:
        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    msvcrt.locking(_lock_file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            _lock_file_handle.close()
        except Exception:
            pass
        _lock_file_handle = None
    if LOCK_PATH.exists():
        try:
            LOCK_PATH.unlink()
        except Exception:
            pass


def _signal_handler(signum, frame):
    global _keep_running
    _keep_running = False
    print("\n[INFO] シャットダウンシグナルを受信しました。終了処理を実行します...")


def run_daemon(
    manifest_path: str = "data/authorized_feed/latest.json",
    db_path: str = "data/alert_state.db",
    interval_seconds: int = 60,
    max_cycles: Optional[int] = None
) -> None:
    global _keep_running
    if not acquire_process_lock():
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")

    cycle_count = 0
    print(f"[INFO] KEIBA AI バックグラウンド監視デーモン起動 (PID: {os.getpid()})")
    print(f"[INFO] 監視間隔: {interval_seconds}秒, フィード: {manifest_path}")

    try:
        while _keep_running:
            cycle_count += 1
            now_iso = datetime.now(timezone.utc).isoformat()
            try:
                result = run_cycle(manifest_path, db_path)
                status_payload = {
                    "daemon_status": "RUNNING",
                    "pid": os.getpid(),
                    "cycle_count": cycle_count,
                    "last_run_at": now_iso,
                    "interval_seconds": interval_seconds,
                    "last_scan_result": result
                }
                STATUS_PATH.write_text(json.dumps(status_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[{now_iso}] サイクル {cycle_count} 完了: status={result.get('status')}, races={result.get('processed_races')}, notified={result.get('notifications_sent')}")
            except Exception as exc:
                err_payload = {
                    "daemon_status": "ERROR_RECOVERING",
                    "pid": os.getpid(),
                    "cycle_count": cycle_count,
                    "last_run_at": now_iso,
                    "last_error": str(exc)
                }
                STATUS_PATH.write_text(json.dumps(err_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"[{now_iso}] サイクル {cycle_count} エラー (自動復旧します): {exc}", file=sys.stderr)

            if max_cycles and cycle_count >= max_cycles:
                print(f"[INFO] 指定の最大サイクル数 ({max_cycles}) に達したため終了します。")
                break

            for _ in range(interval_seconds):
                if not _keep_running:
                    break
                time.sleep(1)
    finally:
        if PID_PATH.exists():
            PID_PATH.unlink()
        release_process_lock()
        stopped_payload = {
            "daemon_status": "STOPPED",
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "total_cycles": cycle_count
        }
        STATUS_PATH.write_text(json.dumps(stopped_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[INFO] デーモンを安全に停止しました。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Continuous background monitor daemon")
    parser.add_argument("--manifest", default="data/authorized_feed/latest.json")
    parser.add_argument("--db", default="data/alert_state.db")
    parser.add_argument("--interval", type=int, default=60, help="Scan interval in seconds (default: 60)")
    parser.add_argument("--max-cycles", type=int, default=None, help="Stop after N cycles (for testing)")
    args = parser.parse_args(argv)
    run_daemon(
        manifest_path=args.manifest,
        db_path=args.db,
        interval_seconds=args.interval,
        max_cycles=args.max_cycles
    )


if __name__ == "__main__":
    main()
