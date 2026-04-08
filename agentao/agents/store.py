"""Persistence layer for background agent task state."""

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_STORE_VERSION = 1
_recover_once_lock = threading.Lock()
_recovered_pid: Optional[int] = None


def _store_path() -> Path:
    return Path.cwd() / ".agentao" / "background_tasks.json"


def load_bg_task_store() -> Dict[str, Dict[str, Any]]:
    """Load persisted tasks from disk.

    Returns {} if the file is missing, corrupt JSON, or has a different version number.
    """
    path = _store_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != _STORE_VERSION:
            return {}
        tasks = data.get("tasks", {})
        return tasks if isinstance(tasks, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_bg_task_store(snapshot: Dict[str, Dict[str, Any]]) -> None:
    """Atomically write a task snapshot to disk.

    Uses tempfile + os.replace() so a crash mid-write never leaves a corrupt file.
    The snapshot must already be a deep copy — this function does NOT acquire any lock.
    """
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": _STORE_VERSION, "tasks": snapshot}
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
                f.write("\n")
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError:
        pass  # Silent fallback — never crash the agent because of persistence


def recover_bg_task_store(bg_tasks: dict, bg_lock: object) -> None:
    """Load persisted tasks and fix interrupted states in-place.

    Any task that was pending or running when the process died is re-classified as failed.
    Must be called before any background threads are started.

    Args:
        bg_tasks: The module-level _bg_tasks dict from tools.py
        bg_lock:  The module-level _bg_lock from tools.py
    """
    tasks = load_bg_task_store()
    if not tasks:
        return

    with bg_lock:
        for agent_id, rec in tasks.items():
            if rec.get("status") in ("pending", "running"):
                rec["status"] = "failed"
                rec["error"] = "process exited before task finished"
                if rec.get("finished_at") is None:
                    rec["finished_at"] = time.time()
            bg_tasks[agent_id] = rec

    # Write back the corrected state outside the lock
    with bg_lock:
        snapshot = {k: dict(v) for k, v in bg_tasks.items()}
    save_bg_task_store(snapshot)


def recover_bg_task_store_once(bg_tasks: dict, bg_lock: object) -> bool:
    """Recover persisted background tasks at most once per process."""
    global _recovered_pid

    pid = os.getpid()
    with _recover_once_lock:
        if _recovered_pid == pid:
            return False
        _recovered_pid = pid

    recover_bg_task_store(bg_tasks, bg_lock)
    return True


def _reset_bg_task_recovery_for_tests() -> None:
    """Test helper to clear per-process recovery state."""
    global _recovered_pid
    with _recover_once_lock:
        _recovered_pid = None
