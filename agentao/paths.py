"""Shared filesystem path helpers.

Centralizes a handful of path literals that used to be hand-rolled at
multiple call sites. Importing this module is cheap (no side effects).
"""

from __future__ import annotations

from pathlib import Path

USER_DIR_NAME = ".agentao"


def user_root() -> Path:
    """Return ``~/.agentao`` — the user-scope config / state directory.

    Resolved lazily on each call so tests that monkeypatch ``Path.home``
    see the patched value. Callers that need to capture a stable
    snapshot should bind the return value once and pass it explicitly.
    """
    return Path.home() / USER_DIR_NAME
