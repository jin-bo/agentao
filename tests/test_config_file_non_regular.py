"""A config path that is not a regular file is reported, never waited on.

``Path.read_text`` on a named pipe blocks until a writer appears, so a FIFO
at a config path hung startup. Readers that checked ``is_file()`` first did
not hang, but read a directory or a pipe as a missing file, so their own
failure policy never ran: ``permissions.json`` dropped its rules without a
word, and ``agentao doctor`` reported the file as absent.

Each reader here runs on a daemon thread with a deadline, so a regression
fails the test instead of hanging the suite.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import pytest

from agentao.config_file import NotARegularFileError, read_config_bytes, read_config_text

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")


def _within(seconds, fn, *args, **kwargs):
    """``fn(*args, **kwargs)`` on a daemon thread; fail if it blocks."""
    box: dict = {}

    def run():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # handed back to the test
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"{getattr(fn, '__name__', fn)} blocked on a FIFO"
    if "error" in box:
        raise box["error"]
    return box.get("value")


_FIFOS: list = []


def _fifo(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(path)
    _FIFOS.append(path)
    return path


@pytest.fixture(autouse=True)
def _release_blocked_readers():
    """Open each FIFO for writing, so a reader that did block gets EOF.

    Without it a regression leaves a thread parked in ``open`` for the rest
    of the session.
    """
    yield
    while _FIFOS:
        path = _FIFOS.pop()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:  # no reader waiting (ENXIO), or already gone
            continue
        os.close(fd)


# ---------------------------------------------------------------------------
# The helper
# ---------------------------------------------------------------------------


def test_missing_is_none(tmp_path):
    assert read_config_bytes(tmp_path / "nope.json") is None
    assert read_config_text(tmp_path / "nope" / "deeper.json") is None


def test_missing_under_a_file_is_none(tmp_path):
    # ``NotADirectoryError``: a parent component is a file.
    (tmp_path / "f").write_text("x", encoding="utf-8")
    assert read_config_text(tmp_path / "f" / "settings.json") is None


def test_regular_file_strips_a_bom(tmp_path):
    p = tmp_path / "c.json"
    p.write_bytes(b"\xef\xbb\xbf{}")
    assert read_config_text(p) == "{}"
    assert read_config_bytes(p) == b"\xef\xbb\xbf{}"


def test_bytes_come_back_exactly(tmp_path):
    # Without ``O_BINARY`` a Windows descriptor is text mode: CRLF reads as LF
    # and the read stops at ``0x1A``. Only the Windows CI job can catch that.
    raw = b'{"a": 1}\r\n\x1a{"b": 2}\r\n'
    p = tmp_path / "c.json"
    p.write_bytes(raw)
    assert read_config_bytes(p) == raw


def test_directory_is_not_a_regular_file(tmp_path):
    (tmp_path / "c.json").mkdir()
    # Windows refuses to open a directory with ``PermissionError``.
    with pytest.raises(OSError) as excinfo:
        read_config_text(tmp_path / "c.json")
    if os.name != "nt":
        assert isinstance(excinfo.value, NotARegularFileError)


@needs_fifo
def test_fifo_is_refused_without_blocking(tmp_path):
    p = _fifo(tmp_path / "c.json")
    with pytest.raises(NotARegularFileError):
        _within(5, read_config_text, p)


# ---------------------------------------------------------------------------
# Every reader: a FIFO is the reader's own "unreadable" outcome
# ---------------------------------------------------------------------------


@needs_fifo
def test_skills_config_load_warns(tmp_path, caplog):
    from agentao.skills.manager import SkillManager

    mgr = object.__new__(SkillManager)
    mgr._config_file = _fifo(tmp_path / "skills_config.json")
    mgr.disabled_skills = {"stale"}
    with caplog.at_level(logging.WARNING):
        _within(5, mgr._load_config)
    assert mgr.disabled_skills == set()
    assert "not a regular file" in caplog.text


@needs_fifo
def test_skills_config_write_refuses(tmp_path):
    from agentao.skills.manager import SkillManager, _SkillConfigWriteError

    mgr = object.__new__(SkillManager)
    mgr._config_file = _fifo(tmp_path / "skills_config.json")
    with pytest.raises(_SkillConfigWriteError, match="not a regular file"):
        _within(5, mgr._read_config_for_write)


@needs_fifo
def test_skill_registry_loads_empty_with_a_warning(tmp_path, caplog):
    from agentao.skills.registry import SkillRegistry

    path = _fifo(tmp_path / "skills_registry.json")
    with caplog.at_level(logging.WARNING):
        # The constructor loads, so it runs under the deadline too.
        reg = _within(5, SkillRegistry, path)
    assert reg.list_all() == []
    assert "not a regular file" in caplog.text


@needs_fifo
def test_plugins_config_reads_empty(tmp_path):
    from agentao.embedding.plugins.manager import PluginManager

    assert _within(5, PluginManager._read_config, _fifo(tmp_path / "plugins_config.json")) == {}


@needs_fifo
def test_plugin_mcp_servers_file_warns(tmp_path):
    from agentao.embedding.plugins.manager import _resolve_mcp_servers

    _fifo(tmp_path / "servers.json")
    warnings: list = []
    assert _within(5, _resolve_mcp_servers, tmp_path, "servers.json", warnings=warnings) == {}
    assert "not a regular file" in warnings[0].message


@needs_fifo
def test_plugin_dot_mcp_json_warns(tmp_path):
    from agentao.embedding.plugins.mcp import _read_mcp_json

    warnings: list = []
    assert _within(5, _read_mcp_json, "p", _fifo(tmp_path / ".mcp.json"), warnings) == {}
    assert "not a regular file" in warnings[0].message


@needs_fifo
def test_user_llm_config_raises(tmp_path):
    from agentao.embedding.llm_config import LLMConfigError, load_user_llm_config

    with pytest.raises(LLMConfigError, match="NotARegularFileError"):
        _within(5, load_user_llm_config, _fifo(tmp_path / "llm.json"))


@needs_fifo
def test_acp_json_raises(tmp_path):
    from agentao.acp_client.config import load_acp_client_config
    from agentao.acp_client.models import AcpConfigError

    _fifo(tmp_path / ".agentao" / "acp.json")
    with pytest.raises(AcpConfigError, match="not a regular file"):
        _within(5, load_acp_client_config, project_root=tmp_path)


@needs_fifo
def test_mcp_json_warns(tmp_path, caplog):
    from agentao.mcp.config import _load_json_file

    p = _fifo(tmp_path / "mcp.json")
    with caplog.at_level(logging.WARNING):
        assert _within(5, _load_json_file, p) == {}
    assert "not a regular file" in caplog.text


@needs_fifo
def test_permissions_json_fails_closed(tmp_path):
    from agentao.embedding.permission_loader import (
        PermissionConfigError,
        load_permission_rules,
    )

    user_root = tmp_path / "home" / ".agentao"
    _fifo(user_root / "permissions.json")
    with pytest.raises(PermissionConfigError, match="not a regular file"):
        _within(5, load_permission_rules, project_root=tmp_path, user_root=user_root)


@needs_fifo
def test_settings_json_warns(tmp_path, caplog):
    from agentao.embedding.factory import _load_settings

    _fifo(tmp_path / ".agentao" / "settings.json")
    with caplog.at_level(logging.WARNING):
        assert _within(5, _load_settings, tmp_path) == {}
    assert "not a regular file" in caplog.text


@pytest.mark.parametrize("kind", ["dir", pytest.param("fifo", marks=needs_fifo)])
def test_doctor_reports_unreadable_not_absent(tmp_path, kind):
    from agentao.cli.diagnostics.loaders import _load_json_object

    p = tmp_path / "settings.json"
    if kind == "dir":
        p.mkdir()
    else:
        _fifo(p)
    data, status, finding = _within(5, _load_json_object, p, area="config")
    assert (data, status) == (None, "unreadable")
    assert finding is not None and finding.level == "error"


def test_doctor_still_reports_a_missing_file_as_absent(tmp_path):
    from agentao.cli.diagnostics.loaders import _load_json_object

    assert _load_json_object(tmp_path / "settings.json", area="config") == (None, "absent", None)


@pytest.mark.parametrize("content", [None, b"[1]", b"{bad"], ids=["missing", "not-a-dict", "invalid-json"])
def test_plugin_mcp_servers_problem_is_a_warning_not_a_crash(tmp_path, content):
    # Each warning branch imported ``PluginWarning`` from a module that does
    # not exist, so the warning raised ``ModuleNotFoundError`` instead, and
    # ``load_plugins`` rejected the whole plugin (skills, hooks, commands).
    from agentao.embedding.plugins.manager import _resolve_mcp_servers

    if content is not None:
        (tmp_path / "servers.json").write_bytes(content)
    warnings: list = []
    assert _resolve_mcp_servers(tmp_path, "servers.json", warnings=warnings) == {}
    assert len(warnings) == 1 and "servers.json" in warnings[0].message
