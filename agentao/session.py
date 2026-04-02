"""Session persistence — save and restore conversation history."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

_SESSION_SUBDIR = ".agentao/sessions"
_MAX_SESSIONS = 10


def _session_dir() -> Path:
    return Path.cwd() / _SESSION_SUBDIR


def save_session(
    messages: List[Dict[str, Any]],
    model: str,
    active_skills: Optional[List[str]] = None,
) -> Path:
    """Serialize conversation to disk and rotate old sessions.

    Returns:
        Path to the saved session file.
    """
    session_dir = _session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_file = session_dir / f"{timestamp}.json"

    data = {
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "active_skills": active_skills or [],
        "messages": messages,
    }
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    _rotate_sessions(session_dir)
    return session_file


def load_session(
    session_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str, List[str]]:
    """Load a saved session.

    Args:
        session_id: Timestamp prefix (e.g. ``"20241228_143022"``), or None for latest.

    Returns:
        ``(messages, model, active_skills)``

    Raises:
        FileNotFoundError: If no sessions exist or the given ID is not found.
    """
    session_dir = _session_dir()
    if not session_dir.exists():
        raise FileNotFoundError("No sessions directory found")

    sessions = sorted(session_dir.glob("*.json"))
    if not sessions:
        raise FileNotFoundError("No saved sessions found")

    if session_id:
        matches = [s for s in sessions if s.stem.startswith(session_id)]
        if not matches:
            raise FileNotFoundError(f"Session '{session_id}' not found")
        session_file = matches[-1]
    else:
        session_file = sessions[-1]

    with open(session_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    return (
        data.get("messages", []),
        data.get("model", ""),
        data.get("active_skills", []),
    )


def list_sessions() -> List[Dict[str, Any]]:
    """Return metadata for all saved sessions, newest first."""
    session_dir = _session_dir()
    if not session_dir.exists():
        return []

    result = []
    for path in sorted(session_dir.glob("*.json"), reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            messages = data.get("messages", [])
            first_user_msg = next(
                (m.get("content", "") for m in messages if m.get("role") == "user"),
                None,
            )
            if first_user_msg:
                first_user_msg = _SYSTEM_REMINDER_RE.sub("", first_user_msg).strip()
            if first_user_msg and len(first_user_msg) > 80:
                first_user_msg = first_user_msg[:77] + "..."
            result.append({
                "id": path.stem,
                "timestamp": data.get("timestamp", path.stem),
                "model": data.get("model", "unknown"),
                "message_count": len(messages),
                "active_skills": data.get("active_skills", []),
                "path": str(path),
                "first_user_msg": first_user_msg,
            })
        except (IOError, json.JSONDecodeError):
            continue
    return result


def delete_session(session_id: str) -> bool:
    """Delete a session by ID prefix.

    Returns:
        True if deleted, False if not found.
    """
    session_dir = _session_dir()
    matches = list(session_dir.glob(f"{session_id}*.json"))
    if not matches:
        return False
    matches[0].unlink()
    return True


def delete_all_sessions() -> int:
    """Delete all saved sessions.

    Returns:
        Number of sessions deleted.
    """
    session_dir = _session_dir()
    if not session_dir.exists():
        return 0
    count = 0
    for path in session_dir.glob("*.json"):
        path.unlink()
        count += 1
    return count


def _rotate_sessions(session_dir: Path):
    sessions = sorted(session_dir.glob("*.json"))
    while len(sessions) > _MAX_SESSIONS:
        sessions[0].unlink()
        sessions = sessions[1:]
