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


def strip_system_reminders(text: str) -> str:
    """Remove ``<system-reminder>…</system-reminder>`` blocks and trim whitespace."""
    return _SYSTEM_REMINDER_RE.sub("", text).strip()


def _session_dir(project_root: Optional[Path] = None) -> Path:
    """Return the ``.agentao/sessions`` directory for a project.

    When ``project_root`` is ``None`` (legacy CLI code path), falls back to
    the process cwd. ACP sessions pass the session's cwd so two sessions in
    different directories do not share persistence state (Issue 05).
    """
    root = project_root if project_root is not None else Path.cwd()
    return root / _SESSION_SUBDIR


def _derive_title(messages: List[Dict[str, Any]]) -> str:
    """Return a short title derived from the first user message."""
    for m in messages:
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                content = strip_system_reminders(content)
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


def _parse_session_datetime(value: Any) -> Optional[datetime]:
    """Parse a persisted session timestamp.

    New session files store ISO datetimes. Older files may only have the
    filename-style timestamp, so keep that as a compatibility fallback.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    iso_text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso_text)
    except ValueError:
        pass

    for fmt in ("%Y%m%d_%H%M%S_%f", "%Y%m%d_%H%M%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_session_time_local(value: Any) -> str:
    """Format a session timestamp in the machine's local terminal timezone."""
    dt = _parse_session_datetime(value)
    if dt is None:
        return str(value) if value is not None else ""

    # Naive timestamps came from older local datetime.now() saves, so treat
    # them as local wall time. Aware timestamps are converted to local time.
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M:%S %z")


def save_session(
    messages: List[Dict[str, Any]],
    model: str,
    active_skills: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> Tuple[Path, str]:
    """Serialize conversation to disk and rotate old sessions.

    Args:
        project_root: Optional project directory whose ``.agentao/sessions``
            subdirectory should hold the persisted session files. Defaults
            to the process cwd for CLI compatibility.

    Returns:
        ``(path, session_id)`` — path of the saved file and the stable session UUID.
    """
    session_dir = _session_dir(project_root)
    session_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()
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
    project_root: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], str, List[str]]:
    """Load a saved session.

    Args:
        session_id: UUID string (or prefix), timestamp prefix, or None for latest.
        project_root: Optional project directory containing the persisted
            ``.agentao/sessions`` subdir. Defaults to the process cwd.

    Returns:
        ``(messages, model, active_skills)``

    Raises:
        FileNotFoundError: If no sessions exist or the given ID is not found.
    """
    session_dir = _session_dir(project_root)
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


def list_sessions(project_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return metadata for all saved sessions, newest first.

    ``project_root`` defaults to the process cwd for CLI compatibility.
    """
    session_dir = _session_dir(project_root)
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
                first_user_msg = strip_system_reminders(first_user_msg)
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


def delete_session(session_id: str, project_root: Optional[Path] = None) -> bool:
    """Delete a session by UUID or timestamp prefix.

    ``project_root`` defaults to the process cwd for CLI compatibility.

    Returns:
        True if deleted, False if not found.
    """
    session_dir = _session_dir(project_root)
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


def delete_all_sessions(project_root: Optional[Path] = None) -> int:
    """Delete all saved sessions.

    ``project_root`` defaults to the process cwd for CLI compatibility.

    Returns:
        Number of sessions deleted.
    """
    session_dir = _session_dir(project_root)
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
