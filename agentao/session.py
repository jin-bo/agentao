"""Session persistence — save and restore conversation history."""

import json
import re
import uuid as _uuid_mod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SYSTEM_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

_SESSION_SUBDIR = ".agentao/sessions"
_MAX_SESSIONS = 10
_TITLE_MAX_CHARS = 60


def _session_dir() -> Path:
    return Path.cwd() / _SESSION_SUBDIR


def _derive_title(messages: List[Dict[str, Any]]) -> str:
    """Return a short title derived from the first user message."""
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                content = _SYSTEM_REMINDER_RE.sub("", content).strip()
                if content:
                    return content[:_TITLE_MAX_CHARS] + ("…" if len(content) > _TITLE_MAX_CHARS else "")
    return ""


def _find_created_at(session_dir: Path, session_id: str) -> Optional[str]:
    """Search existing session files for the earliest `created_at` matching this UUID."""
    for path in session_dir.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("session_id") == session_id:
                return data.get("created_at")
        except (IOError, json.JSONDecodeError):
            continue
    return None


def save_session(
    messages: List[Dict[str, Any]],
    model: str,
    active_skills: Optional[List[str]] = None,
    session_id: Optional[str] = None,
) -> Tuple[Path, str]:
    """Serialize conversation to disk and rotate old sessions.

    Returns:
        ``(path, session_id)`` — path of the saved file and the stable session UUID.
    """
    session_dir = _session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    sid = session_id or str(_uuid_mod.uuid4())

    # Preserve original created_at for continuations; default to now for new sessions.
    created_at = _find_created_at(session_dir, sid) or now.isoformat()
    updated_at = now.isoformat()

    timestamp = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond:06d}"
    session_file = session_dir / f"{timestamp}.json"

    data = {
        "session_id": sid,
        "title": _derive_title(messages),
        "created_at": created_at,
        "updated_at": updated_at,
        "model": model,
        "active_skills": active_skills or [],
        "messages": messages,
    }
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    _rotate_sessions(session_dir)
    return session_file, sid


def load_session(
    session_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], str, List[str]]:
    """Load a saved session.

    Args:
        session_id: UUID string (or prefix), timestamp prefix, or None for latest.

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
        # Try UUID field search first — handles full UUIDs, 8-char prefixes, and
        # any other prefix of the stored session_id regardless of hyphens.
        uuid_matches = []
        for path in sessions:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("session_id", "").startswith(session_id):
                    uuid_matches.append(path)
            except (IOError, json.JSONDecodeError):
                continue
        if uuid_matches:
            # Sort by filename (which encodes the timestamp) so the newest
            # checkpoint is always selected regardless of glob iteration order.
            session_file = sorted(uuid_matches)[-1]
        else:
            # Timestamp prefix fallback (backward compat for old sessions).
            ts_matches = [s for s in sessions if s.stem.startswith(session_id)]
            if not ts_matches:
                raise FileNotFoundError(f"Session '{session_id}' not found")
            session_file = ts_matches[-1]
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

            # Derive title: prefer stored field, fall back to first_user_msg.
            title = data.get("title") or (first_user_msg[:_TITLE_MAX_CHARS] if first_user_msg else "")

            result.append({
                "id": path.stem,
                "session_id": data.get("session_id"),
                "title": title,
                "timestamp": data.get("updated_at") or data.get("timestamp", path.stem),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
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
    """Delete a session by UUID or timestamp prefix.

    Returns:
        True if deleted, False if not found.
    """
    session_dir = _session_dir()
    if not session_dir.exists():
        return False

    # Try UUID field search first — handles full UUIDs, 8-char prefixes, and any
    # prefix of the stored session_id regardless of whether hyphens are present.
    # Delete ALL files sharing the same session_id (multiple checkpoints from resaves).
    uuid_deleted = 0
    for path in list(session_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("session_id", "").startswith(session_id):
                path.unlink()
                uuid_deleted += 1
        except (IOError, json.JSONDecodeError):
            continue
    if uuid_deleted:
        return True

    # Timestamp prefix fallback (backward compat for old sessions without session_id).
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
