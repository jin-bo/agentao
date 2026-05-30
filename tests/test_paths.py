"""Tests for agentao.paths — robust home-directory resolution.

``Path.home()`` raises ``RuntimeError`` when neither ``$HOME`` (nor
``USERPROFILE`` on Windows) nor the password database can yield a home
dir. ``user_home()`` must degrade to a writable fallback instead of
letting that crash an import or a turn.
"""

from __future__ import annotations

import getpass
import os
import tempfile
from pathlib import Path

import pytest

from agentao import paths
from agentao.paths import USER_DIR_NAME, user_home, user_root


def _raise(*_a, **_k):
    raise RuntimeError("no home directory")


class TestUserHome:
    def test_returns_path_home_when_available(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/wherever/me")))
        assert user_home() == Path("/wherever/me")

    def test_falls_back_to_per_user_tempdir_when_home_unresolvable(self, monkeypatch) -> None:
        # When Path.home() raises, the home env vars are necessarily unset,
        # so the fallback is a per-user subdirectory of the temp dir.
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setattr(getpass, "getuser", lambda: "alice")
        assert user_home() == Path(tempfile.gettempdir()) / "agentao-alice"

    def test_fallback_namespaced_per_user(self, monkeypatch) -> None:
        # Two different users must not collide on the same fallback home.
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setattr(getpass, "getuser", lambda: "alice")
        a = user_home()
        monkeypatch.setattr(getpass, "getuser", lambda: "bob")
        b = user_home()
        assert a != b

    def test_fallback_uses_uid_when_getuser_fails(self, monkeypatch) -> None:
        # In a fully stripped account getpass.getuser() raises too; fall
        # through to the numeric uid (stable + unique per POSIX user)
        # rather than a shared placeholder.
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setattr(getpass, "getuser", _raise)
        monkeypatch.setattr(os, "getuid", lambda: 4242, raising=False)
        assert user_home() == Path(tempfile.gettempdir()) / "agentao-4242"

    def test_fallback_placeholder_only_without_getuid(self, monkeypatch) -> None:
        # Windows has no os.getuid; the shared placeholder is the last resort.
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setattr(getpass, "getuser", _raise)
        monkeypatch.delattr(os, "getuid", raising=False)
        assert user_home() == Path(tempfile.gettempdir()) / "agentao-unknown"

    def test_resolved_lazily_each_call(self, monkeypatch) -> None:
        # A later monkeypatch of Path.home must be observed (no caching).
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/a")))
        assert user_home() == Path("/a")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/b")))
        assert user_home() == Path("/b")


class TestUserRoot:
    def test_appends_user_dir_name(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/home/x")))
        assert user_root() == Path("/home/x") / USER_DIR_NAME

    def test_does_not_crash_when_home_unresolvable(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setattr(getpass, "getuser", lambda: "alice")
        # Must not raise; lands under the per-user temp fallback home.
        assert user_root() == Path(tempfile.gettempdir()) / "agentao-alice" / USER_DIR_NAME

    def test_uses_user_home(self, monkeypatch) -> None:
        # user_root must route through user_home (not Path.home directly), so
        # the fallback applies to user_root too.
        monkeypatch.setattr(paths, "user_home", lambda: Path("/sentinel"))
        assert user_root() == Path("/sentinel") / USER_DIR_NAME
