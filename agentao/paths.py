"""Shared filesystem path helpers.

Centralizes a handful of path literals that used to be hand-rolled at
multiple call sites. Importing this module is cheap (no side effects).
"""

from __future__ import annotations

import getpass
import os
import stat
import tempfile
from pathlib import Path

USER_DIR_NAME = ".agentao"

# Process-stable cache for the no-home fallback (see ``user_home``). Only
# the degraded fallback populates it; a resolvable home never touches it.
_FALLBACK_HOME: Path | None = None


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

    The fallback is a *private, per-user* subdirectory of the system temp
    dir. The temp root is world-writable and shared between users, so the
    directory is created ``0700`` and validated to be owned by the current
    user with no group/other access before it is trusted — otherwise a
    local attacker could pre-create the predictable path and have us load
    their ``plugins`` / ``skills`` / ``sandbox.json`` as user-scope state.
    A pre-existing path that fails that check is abandoned for a fresh
    private ``mkdtemp`` rather than trusted.

    The normal path (a resolvable home) does no filesystem I/O; only the
    degraded fallback creates/validates the directory.

    Resolved lazily on each call so tests that monkeypatch ``Path.home``
    see the patched value.
    """
    try:
        return Path.home()
    except RuntimeError:
        # When Path.home() raises, the home env vars are necessarily unset
        # (it consults exactly those before failing), so the only useful
        # last resort is the temp dir — namespaced per user and locked down.
        # Cache it for the process: the unsafe-candidate branch resolves to a
        # fresh ``mkdtemp`` each call, so without caching, two ``user_root()``
        # callers (e.g. global skill registry vs. install dir) could end up
        # on different roots within one run.
        global _FALLBACK_HOME
        if _FALLBACK_HOME is None:
            candidate = Path(tempfile.gettempdir()) / f"agentao-{_fallback_user_id()}"
            _FALLBACK_HOME = _private_dir_or_mkdtemp(candidate)
        return _FALLBACK_HOME


def _fallback_user_id() -> str:
    """A stable per-user discriminator for the no-home temp fallback.

    ``getpass.getuser()`` can fail in exactly the stripped environments
    that trigger the fallback (no ``LOGNAME``/``USER`` env vars and no
    password-database entry), so fall through to the numeric uid — which
    is always available on POSIX and unique per account — before a shared
    placeholder. (Windows has no ``getuid`` but effectively never reaches
    this path, since ``USERPROFILE`` is set there.)
    """
    try:
        return getpass.getuser()
    except Exception:
        getuid = getattr(os, "getuid", None)
        if getuid is not None:
            return str(getuid())
        return "unknown"


def _private_dir_or_mkdtemp(path: Path) -> Path:
    """Return ``path`` after ensuring it is a directory private to this user.

    Creates ``path`` with ``0700`` perms. If it already exists, it is
    trusted only when it is a real directory (not a symlink), owned by the
    current user, with no group/other access — the lockdown that keeps a
    co-tenant on a shared host from planting config we would load. If the
    path can't be created or fails that check, fall back to a freshly
    created private ``mkdtemp`` directory (guaranteed ``0700`` and unique).
    """
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if _is_private_to_current_user(path):
            return path
    except OSError:
        pass
    return Path(tempfile.mkdtemp(prefix="agentao-"))


def _is_private_to_current_user(path: Path) -> bool:
    """True if ``path`` is a non-symlink dir owned by us with no g/o access."""
    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False  # symlink or non-directory — do not trust
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return False  # group/other access — not private
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        return False  # owned by another user
    return True


def user_root() -> Path:
    """Return ``~/.agentao`` — the user-scope config / state directory.

    Resolved lazily on each call (via :func:`user_home`) so tests that
    monkeypatch ``Path.home`` see the patched value. Callers that need to
    capture a stable snapshot should bind the return value once and pass
    it explicitly.
    """
    return user_home() / USER_DIR_NAME
