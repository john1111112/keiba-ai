"""Issue Automation Safety Gate and Dispatcher.

Enforces an offline human-in-the-loop allowlist before automated issue execution.
Guarantees fail-closed behavior, concurrency locking, and status auditing.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess

REPO_NAME = "john1111112/keiba-ai"
DEFAULT_ALLOWLIST_PATH = Path("config/issue_dispatch_allowlist.json")
DEFAULT_STATE_PATH = Path("data/issue_dispatch_state.json")
DEFAULT_LOCK_PATH = Path("data/.dispatch.lock")

class FileLock:
    """Exclusive file lock for preventing concurrent or duplicate dispatches."""
    def __init__(self, lock_path=DEFAULT_LOCK_PATH):
        self.lock_path = Path(lock_path)
        self.fd = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Atomic creation on POSIX and Windows
            self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, str(os.getpid()).encode())
            return self
        except FileExistsError:
            raise RuntimeError(f"Concurrency conflict: lock file {self.lock_path} already exists")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            try:
                self.lock_path.unlink(missing_ok=True)
            except OSError:
                pass

def load_allowlist(allowlist_path=DEFAULT_ALLOWLIST_PATH):
    p = Path(allowlist_path)
    if not p.is_file():
        return {"repository": REPO_NAME, "allowed_issues": {}}
    return json.loads(p.read_text(encoding='utf-8'))

def load_state(state_path=DEFAULT_STATE_PATH):
    p = Path(state_path)
    if not p.is_file():
        return {"repository": REPO_NAME, "issues": {}}
    return json.loads(p.read_text(encoding='utf-8'))

def save_state(state_data, state_path=DEFAULT_STATE_PATH):
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state_data, ensure_ascii=False, indent=2), encoding='utf-8')

def get_issue_info(issue_number, repo=REPO_NAME):
    """Retrieve issue metadata via GitHub CLI safely without exposing tokens."""
    try:
        cmd = [
            "gh", "issue", "view", str(issue_number),
            "-R", repo,
            "--json", "number,title,state,comments,updatedAt,url"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
        return json.loads(res.stdout)
    except Exception as e:
        return {"error": str(e), "number": issue_number, "state": "unknown"}

def evaluate_issue_dispatch(issue_number, *, allowlist_path=DEFAULT_ALLOWLIST_PATH, issue_metadata=None):
    """Evaluate whether an issue is approved for execution or restricted to dry-run."""
    allowlist = load_allowlist(allowlist_path)
    issue_key = str(issue_number)
    issue_config = allowlist.get("allowed_issues", {}).get(issue_key)

    if not issue_config:
        return {
            "issue_number": issue_number,
            "approved": False,
            "action_mode": "rejected",
            "decision": "DISALLOWED",
            "reason": f"Issue #{issue_number} is not registered in the allowlist."
        }

    is_approved = bool(issue_config.get("approved_by_user", False))
    action_mode = issue_config.get("action_mode", "dry_run")

    if not is_approved or action_mode != "execute":
        reasons = issue_config.get("disallow_reasons", ["Explicit human approval not granted."])
        return {
            "issue_number": issue_number,
            "approved": False,
            "action_mode": action_mode,
            "decision": "DRY_RUN_ONLY",
            "reason": "; ".join(reasons)
        }

    return {
        "issue_number": issue_number,
        "approved": True,
        "action_mode": "execute",
        "decision": "APPROVED",
        "reason": "Explicitly approved by user in local allowlist."
    }

def run_dry_run_inspection(issue_number, *, allowlist_path=DEFAULT_ALLOWLIST_PATH, state_path=DEFAULT_STATE_PATH):
    """Inspect and dispatch an issue based on allowlist authorization."""
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    meta = get_issue_info(issue_number)
    eval_res = evaluate_issue_dispatch(issue_number, allowlist_path=allowlist_path, issue_metadata=meta)

    is_authorized = (eval_res["decision"] == "APPROVED")

    # State update
    state = load_state(state_path)
    issue_key = str(issue_number)
    state.setdefault("issues", {})
    state["issues"][issue_key] = {
        "title": meta.get("title", ""),
        "state": meta.get("state", "unknown"),
        "last_checked_at": now_iso,
        "last_decision": eval_res["decision"],
        "action_mode": eval_res["action_mode"],
        "approved_by_user": eval_res["approved"],
        "execution_authorized": is_authorized,
        "execution_skipped": not is_authorized,
        "reason": eval_res["reason"]
    }
    save_state(state, state_path)

    return {
        "status": "authorized_for_execution" if is_authorized else "dry_run_completed",
        "issue_number": issue_number,
        "issue_title": meta.get("title"),
        "issue_state": meta.get("state"),
        "evaluation": eval_res,
        "inspected_at_utc": now_iso,
        "execution_authorized": is_authorized,
        "execution_occurred": False,
        "code_modified": False
    }

def main(argv=None):
    parser = argparse.ArgumentParser(description="Issue Automation Safety Gate")
    parser.add_argument("--issue", type=int, default=11, help="Issue number to inspect")
    parser.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST_PATH), help="Path to allowlist JSON")
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH), help="Path to state JSON")
    args = parser.parse_args(argv)

    with FileLock():
        result = run_dry_run_inspection(
            args.issue,
            allowlist_path=Path(args.allowlist),
            state_path=Path(args.state)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
