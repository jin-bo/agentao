"""Shared filesystem path helpers.

Centralizes a handful of path literals that used to be hand-rolled at
multiple call sites. Importing this module is cheap (no side effects).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

USER_DIR_NAME = ".agentao"


def user_home() -> Path:
    """Return the user's home directory, robust to an unset ``$HOME``.

    ``Path.home()`` raises ``RuntimeError`` when it cannot resolve a home
    directory — ``$HOME`` (and on Windows ``USERPROFILE``) is unset *and*
    there is no password-database entry for the current uid. This happens
    in stripped service accounts, some container and CI sandboxes, and
    headless launches (e.g. an ACP client spawning us with a minimal
    environment). Rather than let that crash an import or a turn, fall
    back to the system temp directory so user-scope state still has a
    writable home to land in.

    Resolved lazily on each call so tests that monkeypatch ``Path.home``
    see the patched value.
    """
    try:
        return Path.home()
    except RuntimeError:
        # Last resort: a guaranteed-writable location. Prefer an explicit
        # env var if one is set but unparsed by Path.home() on this
        # platform, else the system temp dir.
        for var in ("HOME", "USERPROFILE"):
            value = os.environ.get(var)
            if value:
                return Path(value)
        return Path(tempfile.gettempdir())


def user_root() -> Path:
    """Return ``~/.agentao`` — the user-scope config / state directory.

    Resolved lazily on each call (via :func:`user_home`) so tests that
    monkeypatch ``Path.home`` see the patched value. Callers that need to
    capture a stable snapshot should bind the return value once and pass
    it explicitly.
    """
    return user_home() / USER_DIR_NAME
