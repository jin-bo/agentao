"""Shared filesystem path helpers.

Centralizes a handful of path literals that used to be hand-rolled at
multiple call sites. Importing this module is cheap (no side effects).
"""

from __future__ import annotations

import getpass
import tempfile
from pathlib import Path

USER_DIR_NAME = ".agentao"


def user_home() -> Path:
    """Return the user's home directory, robust to an unresolvable home.

    ``Path.home()`` (i.e. ``Path("~").expanduser()``) raises
    ``RuntimeError`` when it cannot resolve a home directory — ``$HOME``
    (or ``USERPROFILE`` on Windows) is unset *and* there is no
    password-database entry for the current user. This happens in
    stripped service accounts, some container and CI sandboxes, and
    headless launches (e.g. an ACP client spawning us with a minimal
    environment). Rather than let that crash an import or a turn, fall
    back to a writable location so user-scope state still has a home.

    The fallback is a *per-user* subdirectory of the system temp dir: the
    temp root itself is shared between users, so namespacing it by login
    name avoids one account's ``~/.agentao`` state (history, registries)
    colliding with another's on a multi-tenant host.

    Resolved lazily on each call so tests that monkeypatch ``Path.home``
    see the patched value.
    """
    try:
        return Path.home()
    except RuntimeError:
        # When Path.home() raises, the home env vars are necessarily unset
        # (it consults exactly those before failing), so the only useful
        # last resort is the temp dir — namespaced per user.
        try:
            who = getpass.getuser()
        except Exception:
            who = "unknown"
        return Path(tempfile.gettempdir()) / f"agentao-{who}"


def user_root() -> Path:
    """Return ``~/.agentao`` — the user-scope config / state directory.

    Resolved lazily on each call (via :func:`user_home`) so tests that
    monkeypatch ``Path.home`` see the patched value. Callers that need to
    capture a stable snapshot should bind the return value once and pass
    it explicitly.
    """
    return user_home() / USER_DIR_NAME
