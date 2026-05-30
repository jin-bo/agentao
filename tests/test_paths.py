"""Tests for agentao.paths — robust home-directory resolution.

``Path.home()`` raises ``RuntimeError`` when neither ``$HOME`` (nor
``USERPROFILE`` on Windows) nor the password database can yield a home
dir. ``user_home()`` must degrade to a writable fallback instead of
letting that crash an import or a turn.
"""

from __future__ import annotations

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

    def test_falls_back_to_home_env_when_path_home_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.setenv("HOME", "/from/env")
        monkeypatch.delenv("USERPROFILE", raising=False)
        assert user_home() == Path("/from/env")

    def test_falls_back_to_userprofile_when_only_that_is_set(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.setenv("USERPROFILE", "/win/profile")
        assert user_home() == Path("/win/profile")

    def test_falls_back_to_tempdir_when_no_env(self, monkeypatch) -> None:
        monkeypatch.setattr(Path, "home", staticmethod(_raise))
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.delenv("USERPROFILE", raising=False)
        assert user_home() == Path(tempfile.gettempdir())

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
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.delenv("USERPROFILE", raising=False)
        # Must not raise; lands under the temp dir.
        assert user_root() == Path(tempfile.gettempdir()) / USER_DIR_NAME

    def test_uses_user_home(self, monkeypatch) -> None:
        # user_root must route through user_home (not Path.home directly), so
        # the fallback applies to user_root too.
        monkeypatch.setattr(paths, "user_home", lambda: Path("/sentinel"))
        assert user_root() == Path("/sentinel") / USER_DIR_NAME
