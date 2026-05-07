"""Deprecated re-export of session persistence helpers.

The implementation moved to :mod:`agentao.embedding.sessions` so that
session disk I/O lives at the embedded-host boundary instead of the
inference core. This module remains as a wrapper shim that:

* Preserves the legacy permissive signature
  (``project_root: Optional[Path] = None``) so all existing CLI / ACP /
  test imports keep working unchanged.
* Supplies the implicit ``Path.cwd()`` fallback locally before
  delegating to the new path; the new path keeps ``project_root``
  optional only for the duration of this migration window so this shim
  has something to delegate to. After 0.5.0 the new path will require
  ``project_root`` and the fallback will exist nowhere.
* Emits a single :class:`DeprecationWarning` on first import.

Scheduled for removal in 0.5.0 alongside the ``agentao.harness`` alias.
Migrate to::

    from agentao.embedding.sessions import save_session, load_session, ...

and pass ``project_root`` explicitly.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .embedding.sessions import format_session_time_local, strip_system_reminders
from .embedding.sessions import delete_all_sessions as _delete_all_sessions
from .embedding.sessions import delete_session as _delete_session
from .embedding.sessions import list_sessions as _list_sessions
from .embedding.sessions import load_session as _load_session
from .embedding.sessions import save_session as _save_session

warnings.warn(
    "agentao.session is deprecated; import from agentao.embedding.sessions "
    "instead and pass project_root explicitly. The agentao.session shim will "
    "be removed in 0.5.0.",
    DeprecationWarning,
    stacklevel=2,
)


def save_session(
    messages: List[Dict[str, Any]],
    model: str,
    active_skills: Optional[List[str]] = None,
    session_id: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> Tuple[Path, str]:
    """Legacy permissive wrapper — supplies ``Path.cwd()`` fallback locally."""
    return _save_session(
        messages=messages,
        model=model,
        active_skills=active_skills,
        session_id=session_id,
        project_root=project_root if project_root is not None else Path.cwd(),
    )


def load_session(
    session_id: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> Tuple[List[Dict[str, Any]], str, List[str]]:
    """Legacy permissive wrapper — supplies ``Path.cwd()`` fallback locally."""
    return _load_session(
        session_id=session_id,
        project_root=project_root if project_root is not None else Path.cwd(),
    )


def list_sessions(project_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Legacy permissive wrapper — supplies ``Path.cwd()`` fallback locally."""
    return _list_sessions(
        project_root=project_root if project_root is not None else Path.cwd(),
    )


def delete_session(session_id: str, project_root: Optional[Path] = None) -> bool:
    """Legacy permissive wrapper — supplies ``Path.cwd()`` fallback locally."""
    return _delete_session(
        session_id=session_id,
        project_root=project_root if project_root is not None else Path.cwd(),
    )


def delete_all_sessions(project_root: Optional[Path] = None) -> int:
    """Legacy permissive wrapper — supplies ``Path.cwd()`` fallback locally."""
    return _delete_all_sessions(
        project_root=project_root if project_root is not None else Path.cwd(),
    )


__all__ = [
    "save_session",
    "load_session",
    "list_sessions",
    "delete_session",
    "delete_all_sessions",
    "strip_system_reminders",
    "format_session_time_local",
]
