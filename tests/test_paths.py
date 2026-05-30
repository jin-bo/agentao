"""Tests for agentao.paths — robust home-directory resolution.

``Path.home()`` raises ``RuntimeError`` when neither ``$HOME`` (nor
``USERPROFILE`` on Windows) nor the password database can yield a home
dir. ``user_home()`` must degrade to a *private, per-user* writable
fallback instead of letting that crash an import or a turn — and must
not trust an attacker-pre-created path under the shared temp dir.
"""

from __future__ import annotations

import getpass
import os
import stat
import tempfile
from pathlib import Path

import pytest

from agentao import paths
from agentao.paths import (
    USER_DIR_NAME,
    _fallback_user_id,
    _is_private_to_current_user,
    user_home,
    user_root,
)

_POSIX_ONLY = pytest.mark.skipif(
    not hasattr(os, "getuid"), reason="POSIX ownership semantics"
)


def _raise(*_a, **_k):
    raise RuntimeError("no home directory")


@pytest.fixture(autouse=True)
def _reset_fallback_cache(monkeypatch):
    # The no-home fallback is cached process-wide; start each test clean.
    monkeypatch.setattr(paths, "_FALLBACK_HOME", None)


class TestUserHome:
    def test_returns_path_home_when_available(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/wherever/me")))
        assert user_home() == Path("/wherever/me")

    def test_happy_path_does_no_filesystem_io(self, monkeypatch) -> None:
        # The normal (resolvable home) path must not touch the temp dir.
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/wherever/me")))
        monkeypatch.setattr(
            tempfile, "mkdtemp", lambda *a, **k: pytest.fail("should not mkdtemp")
        )
        assert user_home() == Path("/wherever/me")

    def test_fallback_creates_private_per_user_dir(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        result = user_home()
        expected = tmp_path / f"agentao-{getpass.getuser()}"
        assert result == expected
        assert result.is_dir()
        if hasattr(os, "getuid"):
            assert stat.S_IMODE(result.stat().st_mode) == 0o700

    def test_resolved_lazily_each_call(self, monkeypatch) -> None:
        # A later monkeypatch of Path.home must be observed (no caching).
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/a")))
        assert user_home() == Path("/a")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/b")))
        assert user_home() == Path("/b")

    @_POSIX_ONLY
    def test_rejects_unsafe_preexisting_dir(self, monkeypatch, tmp_path) -> None:
        # A world/group-accessible pre-existing path (attacker-plantable on a
        # shared host) must NOT be trusted — user_home falls back to a fresh
        # private mkdtemp instead.
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        candidate = tmp_path / f"agentao-{getpass.getuser()}"
        candidate.mkdir(mode=0o777)
        os.chmod(candidate, 0o777)  # defeat umask
        result = user_home()
        assert result != candidate
        assert result.is_dir()
        assert stat.S_IMODE(result.stat().st_mode) == 0o700
        # And it must be stable across calls (cached), so global-scope
        # helpers don't diverge onto different mkdtemp roots in one process.
        assert user_home() == result

    def test_fallback_is_process_stable(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        assert user_home() == user_home()


class TestFallbackUserId:
    def test_uses_getuser_normally(self, monkeypatch) -> None:
        monkeypatch.setattr(getpass, "getuser", lambda: "alice")
        assert _fallback_user_id() == "alice"

    @_POSIX_ONLY
    def test_uses_uid_when_getuser_fails(self, monkeypatch) -> None:
        monkeypatch.setattr(getpass, "getuser", _raise)
        monkeypatch.setattr(os, "getuid", lambda: 4242)
        assert _fallback_user_id() == "4242"

    def test_placeholder_when_no_getuid(self, monkeypatch) -> None:
        monkeypatch.setattr(getpass, "getuser", _raise)
        monkeypatch.delattr(os, "getuid", raising=False)
        assert _fallback_user_id() == "unknown"


@_POSIX_ONLY
class TestIsPrivateToCurrentUser:
    def test_private_dir(self, tmp_path) -> None:
        d = tmp_path / "priv"
        d.mkdir(mode=0o700)
        os.chmod(d, 0o700)
        assert _is_private_to_current_user(d) is True

    def test_group_or_other_access_rejected(self, tmp_path) -> None:
        d = tmp_path / "open"
        d.mkdir()
        os.chmod(d, 0o755)
        assert _is_private_to_current_user(d) is False

    def test_nonexistent_rejected(self, tmp_path) -> None:
        assert _is_private_to_current_user(tmp_path / "nope") is False

    def test_symlink_rejected(self, tmp_path) -> None:
        target = tmp_path / "real"
        target.mkdir(mode=0o700)
        link = tmp_path / "link"
        link.symlink_to(target)
        assert _is_private_to_current_user(link) is False

    def test_file_rejected(self, tmp_path) -> None:
        f = tmp_path / "file"
        f.write_text("x")
        os.chmod(f, 0o600)
        assert _is_private_to_current_user(f) is False


class TestUserRoot:
    def test_appends_user_dir_name(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/home/x")))
        assert user_root() == Path("/home/x") / USER_DIR_NAME

    def test_does_not_crash_when_home_unresolvable(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        # Must not raise; lands under the per-user temp fallback home.
        assert user_root() == tmp_path / f"agentao-{getpass.getuser()}" / USER_DIR_NAME

    def test_uses_user_home(self, monkeypatch) -> None:
        # user_root must route through user_home (not Path.home directly), so
        # the fallback applies to user_root too.
        monkeypatch.setattr(paths, "user_home", lambda: Path("/sentinel"))
        assert user_root() == Path("/sentinel") / USER_DIR_NAME
