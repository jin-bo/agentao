"""Dotenv bootstrap + shared JSON-object loader for the diagnostics commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .models import FileStatus, Finding


def _load_dotenv(wd: Path) -> None:
    """Mirror ``build_from_environment``'s dotenv search order.

    Without this, ``_collect_provider`` reads ``os.getenv`` against a process
    env that has not seen the project's ``.env`` yet, and ``agentao doctor``
    falsely warns that the API key is missing right after the user ran
    ``agentao init`` (which writes the key to ``.env``).
    """
    from ..._env import safe_load_dotenv as _ld

    dotenv_path = wd / ".env"
    if dotenv_path.is_file():
        _ld(dotenv_path)
    else:
        _ld()


def _load_json_object(
    path: Path,
    *,
    area: str,
    label: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], FileStatus, Optional[Finding]]:
    """Read ``path`` as a JSON object with explicit absence/parse semantics.

    Returns ``(data, status, finding)``:

    - ``data`` is the parsed object, or ``None`` for any non-``"ok"`` status;
    - ``status`` distinguishes ``"absent"`` (no file) from ``"unreadable"``
      (filesystem error) and ``"malformed"`` (JSON or shape error);
    - ``finding`` is ``None`` when status is ``"absent"`` or ``"ok"``;
      otherwise it is an error-level Finding the caller can append.

    ``label`` is used in user-facing messages so files that exist in multiple
    scopes (``"user-scope mcp.json"``) read sensibly. Defaults to ``path.name``.
    """
    display = label or path.name
    if not path.is_file():
        return None, "absent", None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, "unreadable", Finding(
            level="error",
            area=area,
            message=f"Cannot read {display}: {type(exc).__name__}: {exc}",
            source=str(path),
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, "malformed", Finding(
            level="error",
            area=area,
            message=(
                f"Invalid JSON in {display}: "
                f"{exc.msg} (line {exc.lineno}, col {exc.colno})"
            ),
            source=str(path),
        )
    if not isinstance(data, dict):
        return None, "malformed", Finding(
            level="error",
            area=area,
            message=f"Top-level value in {display} is not an object",
            source=str(path),
        )
    return data, "ok", None
