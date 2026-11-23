"""Find the one saved session an id prefix names (``/export``, ``/sessions clone``).

``/sessions resume`` takes the first match. These commands refuse a prefix
that matches more than one session instead, and list the candidates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple


class NoUniqueSession(LookupError):
    """No saved session, or more than one, matches the prefix. The text is user-facing."""


def match_one_session(project_root: Path, prefix: str) -> Tuple[str, Dict[str, Any]]:
    """``(session_id, list_sessions entry)`` of the newest file of the one matching session."""
    from ...embedding.sessions import list_sessions

    matches: Dict[str, Dict[str, Any]] = {}
    for s in list_sessions(project_root=project_root):
        persisted = s.get("session_id")
        sid = persisted if isinstance(persisted, str) and persisted else s["id"]
        if sid.startswith(prefix) or s["id"].startswith(prefix):
            matches.setdefault(sid, s)  # newest first, so the newest file wins
    if not matches:
        raise NoUniqueSession(f"No saved session matches '{prefix}'. See /sessions list.")
    if len(matches) > 1:
        ids = ", ".join(sid[:8] for sid in matches)
        raise NoUniqueSession(
            f"'{prefix}' matches {len(matches)} sessions ({ids}). Use a longer prefix."
        )
    return next(iter(matches.items()))
