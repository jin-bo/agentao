"""Path-agnostic persistence for the background-agent task store.

Callers pass the file path explicitly; there is no module-level state.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

_STORE_VERSION = 1


def load_bg_task_store(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load persisted tasks from ``path``.

    Returns ``{}`` if the file is missing, has corrupt JSON, or carries a
    different version number.
    """
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


def save_bg_task_store(path: Path, snapshot: Dict[str, Dict[str, Any]]) -> None:
    """Atomically write a task snapshot to ``path``.

    Uses tempfile + ``os.replace()`` so a crash mid-write never leaves a
    corrupt file. The snapshot must already be a deep copy — this function
    does NOT acquire any lock. Silently no-ops on filesystem errors so
    persistence problems never crash the agent.
    """
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
        pass
