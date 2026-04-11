"""Tests for per-session working directory isolation (Issue 05).

Covers:

- ``Agentao.working_directory`` property: ``None`` → lazy ``Path.cwd()``;
  set → frozen resolved ``Path``
- Memory manager bound to session cwd (so two sessions don't share a
  SQLite database)
- ``_load_project_instructions`` reads AGENTAO.md from session cwd
- System prompt renders the session cwd
- ``PermissionEngine(project_root=...)`` isolation
- ``load_mcp_config(project_root=...)`` isolation
- File tools with per-session ``working_directory`` resolve relative
  paths against it
- Shell tool's ``working_directory="."`` default resolves against the
  tool's session cwd
- ACP ``session/new`` factory wires ``working_directory=cwd`` through to
  the constructed runtime
- Default Agentao (no ``working_directory``) still reflects process cwd
  (CLI compatibility)
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from agentao.acp import initialize as acp_initialize
from agentao.acp import session_new as acp_session_new
from agentao.acp.protocol import ACP_PROTOCOL_VERSION
from agentao.acp.server import AcpServer
from agentao.mcp.config import load_mcp_config
from agentao.permissions import PermissionEngine
from agentao.tools.file_ops import (
    EditTool,
    ReadFileTool,
    ReadFolderTool,
    WriteFileTool,
)
from agentao.tools.search import FindFilesTool, SearchTextTool
from agentao.tools.shell import ShellTool


# ---------------------------------------------------------------------------
# Agentao construction is expensive (LLMClient + memory manager + tool
# registry + MCP autoload). Gate it behind a fixture that sets a dummy key.
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_llm_env(monkeypatch):
    """Ensure LLMClient construction works without a real API key."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    return None


def _make_agent(working_directory=None, stub_llm_env=None):
    """Construct a real Agentao instance without invoking the API."""
    from agentao.agent import Agentao

    return Agentao(working_directory=working_directory)


# ---------------------------------------------------------------------------
# Tool base class — _resolve_path and _resolve_directory helpers
# ---------------------------------------------------------------------------

class _FakeTool(ReadFileTool):
    """Concrete Tool subclass used for path-resolution tests in isolation."""


def test_resolve_path_absolute_pass_through(tmp_path):
    tool = _FakeTool()
    tool.working_directory = tmp_path
    abs_path = tmp_path / "nested" / "file.txt"
    assert tool._resolve_path(str(abs_path)) == abs_path


def test_resolve_path_relative_uses_working_directory(tmp_path):
    tool = _FakeTool()
    tool.working_directory = tmp_path
    assert tool._resolve_path("foo.txt") == tmp_path / "foo.txt"
    assert tool._resolve_path("a/b/c.py") == tmp_path / "a" / "b" / "c.py"


def test_resolve_path_relative_without_wd_returns_relative_path():
    """No working_directory set → legacy behavior: return the relative path as-is."""
    tool = _FakeTool()
    assert tool.working_directory is None
    result = tool._resolve_path("foo.txt")
    assert not result.is_absolute()
    assert str(result) == "foo.txt"


def test_resolve_path_tilde_expansion(tmp_path, monkeypatch):
    tool = _FakeTool()
    tool.working_directory = tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))
    result = tool._resolve_path("~/foo.txt")
    assert result == tmp_path / "foo.txt"


def test_resolve_directory_always_resolves_to_absolute(tmp_path):
    tool = _FakeTool()
    tool.working_directory = tmp_path
    result = tool._resolve_directory(".")
    assert result.is_absolute()
    assert result == tmp_path.resolve()


# ---------------------------------------------------------------------------
# File tools resolve relative paths against session cwd
# ---------------------------------------------------------------------------

def test_read_file_tool_resolves_relative_path_to_session_cwd(tmp_path):
    (tmp_path / "hello.txt").write_text("session content\n", encoding="utf-8")
    tool = ReadFileTool()
    tool.working_directory = tmp_path

    output = tool.execute(file_path="hello.txt")

    assert "session content" in output


def test_write_file_tool_writes_to_session_cwd(tmp_path):
    tool = WriteFileTool()
    tool.working_directory = tmp_path

    result = tool.execute(file_path="new.txt", content="written\n")

    assert "Successfully" in result
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "written\n"
    # Must NOT have written to the process cwd.
    assert not (Path.cwd() / "new.txt").exists() or str(Path.cwd()) == str(tmp_path)


def test_edit_tool_resolves_relative_path(tmp_path):
    target = tmp_path / "foo.txt"
    target.write_text("hello world\n", encoding="utf-8")
    tool = EditTool()
    tool.working_directory = tmp_path

    result = tool.execute(file_path="foo.txt", old_text="hello", new_text="howdy")

    assert "Replaced" in result
    assert target.read_text(encoding="utf-8") == "howdy world\n"


def test_read_folder_tool_resolves_relative(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b.txt").write_text("x", encoding="utf-8")
    tool = ReadFolderTool()
    tool.working_directory = tmp_path

    output = tool.execute(directory_path=".")

    assert "[DIR]  a/" in output
    assert "[FILE] b.txt" in output


def test_find_files_tool_resolves_relative(tmp_path):
    (tmp_path / "x.py").write_text("", encoding="utf-8")
    (tmp_path / "y.py").write_text("", encoding="utf-8")
    (tmp_path / "z.txt").write_text("", encoding="utf-8")
    tool = FindFilesTool()
    tool.working_directory = tmp_path

    output = tool.execute(pattern="*.py", directory=".")

    assert "x.py" in output
    assert "y.py" in output
    assert "z.txt" not in output


def test_two_sessions_see_independent_files(tmp_path):
    """The core isolation guarantee: same relative path resolves to different files."""
    dir_a = tmp_path / "session_a"
    dir_b = tmp_path / "session_b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "shared.txt").write_text("I am A\n", encoding="utf-8")
    (dir_b / "shared.txt").write_text("I am B\n", encoding="utf-8")

    tool_a = ReadFileTool()
    tool_a.working_directory = dir_a
    tool_b = ReadFileTool()
    tool_b.working_directory = dir_b

    assert "I am A" in tool_a.execute(file_path="shared.txt")
    assert "I am B" in tool_b.execute(file_path="shared.txt")


# ---------------------------------------------------------------------------
# Shell tool
# ---------------------------------------------------------------------------

def test_shell_tool_default_wd_resolves_to_session_cwd(tmp_path):
    """``working_directory="."`` must land in the tool's bound cwd, not the process cwd."""
    tool = ShellTool()
    tool.working_directory = tmp_path

    # Echo pwd so we can assert what the subprocess saw.
    if sys.platform == "win32":
        pytest.skip("pwd is a POSIX concept")
    output = tool.execute(command="pwd", working_directory=".")

    assert str(tmp_path.resolve()) in output


# ---------------------------------------------------------------------------
# PermissionEngine scoping
# ---------------------------------------------------------------------------

def test_permission_engine_reads_project_root(tmp_path):
    (tmp_path / ".agentao").mkdir()
    rules = {"rules": [{"tool": "custom_tool_a", "action": "allow"}]}
    (tmp_path / ".agentao" / "permissions.json").write_text(json.dumps(rules), encoding="utf-8")

    engine = PermissionEngine(project_root=tmp_path)

    assert any(r.get("tool") == "custom_tool_a" for r in engine.rules)


def test_permission_engines_for_different_projects_are_independent(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    (dir_a / ".agentao").mkdir(parents=True)
    (dir_b / ".agentao").mkdir(parents=True)
    (dir_a / ".agentao" / "permissions.json").write_text(
        json.dumps({"rules": [{"tool": "only_in_a", "action": "allow"}]}), encoding="utf-8"
    )
    (dir_b / ".agentao" / "permissions.json").write_text(
        json.dumps({"rules": [{"tool": "only_in_b", "action": "allow"}]}), encoding="utf-8"
    )

    engine_a = PermissionEngine(project_root=dir_a)
    engine_b = PermissionEngine(project_root=dir_b)

    assert any(r.get("tool") == "only_in_a" for r in engine_a.rules)
    assert not any(r.get("tool") == "only_in_a" for r in engine_b.rules)
    assert any(r.get("tool") == "only_in_b" for r in engine_b.rules)


def test_permission_engine_default_still_uses_process_cwd(monkeypatch, tmp_path):
    """No ``project_root`` → legacy behavior: read from ``Path.cwd()/.agentao``."""
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "permissions.json").write_text(
        json.dumps({"rules": [{"tool": "legacy_tool", "action": "allow"}]}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    engine = PermissionEngine()

    assert any(r.get("tool") == "legacy_tool" for r in engine.rules)


# ---------------------------------------------------------------------------
# load_mcp_config scoping
# ---------------------------------------------------------------------------

def test_load_mcp_config_reads_project_root(tmp_path):
    (tmp_path / ".agentao").mkdir()
    cfg = {"mcpServers": {"local": {"command": "/bin/true", "args": []}}}
    (tmp_path / ".agentao" / "mcp.json").write_text(json.dumps(cfg), encoding="utf-8")

    loaded = load_mcp_config(project_root=tmp_path)

    assert "local" in loaded
    assert loaded["local"]["command"] == "/bin/true"


def test_load_mcp_config_independent_per_project(tmp_path):
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    (dir_a / ".agentao").mkdir(parents=True)
    (dir_b / ".agentao").mkdir(parents=True)
    (dir_a / ".agentao" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"server_a": {"command": "/a", "args": []}}}), encoding="utf-8"
    )
    (dir_b / ".agentao" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"server_b": {"command": "/b", "args": []}}}), encoding="utf-8"
    )

    cfg_a = load_mcp_config(project_root=dir_a)
    cfg_b = load_mcp_config(project_root=dir_b)

    assert "server_a" in cfg_a and "server_b" not in cfg_a
    assert "server_b" in cfg_b and "server_a" not in cfg_b


# ---------------------------------------------------------------------------
# Agentao.working_directory property
# ---------------------------------------------------------------------------

def test_agentao_working_directory_defaults_to_process_cwd(stub_llm_env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    agent = _make_agent()
    try:
        assert agent.working_directory == Path.cwd()
        assert agent.working_directory == tmp_path
    finally:
        agent.close()


def test_agentao_working_directory_is_frozen_when_explicit(stub_llm_env, tmp_path, monkeypatch):
    agent = _make_agent(working_directory=tmp_path)
    try:
        expected = tmp_path.resolve()
        assert agent.working_directory == expected
        # Changing the process cwd must NOT affect the frozen runtime cwd.
        other = tmp_path.parent
        monkeypatch.chdir(other)
        assert agent.working_directory == expected
    finally:
        agent.close()


def test_agentao_memory_manager_bound_to_session_cwd(stub_llm_env, tmp_path):
    agent = _make_agent(working_directory=tmp_path)
    try:
        # MemoryManager stores its project_root on init; just verify it's
        # rooted under the session cwd, not the process cwd.
        root = Path(agent._memory_manager._project_root)
        assert root == tmp_path.resolve() / ".agentao"
    finally:
        agent.close()


def test_agentao_loads_project_instructions_from_session_cwd(stub_llm_env, tmp_path):
    (tmp_path / "AGENTAO.md").write_text("## Session-specific instructions\nhello\n", encoding="utf-8")

    agent = _make_agent(working_directory=tmp_path)
    try:
        assert agent.project_instructions is not None
        assert "Session-specific instructions" in agent.project_instructions
    finally:
        agent.close()


def test_agentao_system_prompt_includes_session_cwd(stub_llm_env, tmp_path):
    agent = _make_agent(working_directory=tmp_path)
    try:
        prompt = agent._build_system_prompt()
        assert f"Current Working Directory: {tmp_path.resolve()}" in prompt
    finally:
        agent.close()


def test_agentao_registers_tools_with_session_cwd(stub_llm_env, tmp_path):
    """Every file/shell/search tool registered on the agent must see the session cwd."""
    agent = _make_agent(working_directory=tmp_path)
    try:
        expected = tmp_path.resolve()
        for name in (
            "read_file",
            "write_file",
            "replace",
            "list_directory",
            "glob",
            "search_file_content",
            "run_shell_command",
        ):
            tool = agent.tools.get(name)
            assert tool is not None, f"missing tool: {name}"
            assert tool.working_directory == expected, (
                f"{name} has wrong working_directory: {tool.working_directory} != {expected}"
            )
    finally:
        agent.close()


def test_agentao_default_mode_leaves_tools_unbound(stub_llm_env, monkeypatch, tmp_path):
    """CLI default: tools have ``working_directory = None`` so they keep
    resolving relative paths against the process cwd lazily."""
    monkeypatch.chdir(tmp_path)
    agent = _make_agent()
    try:
        tool = agent.tools.get("read_file")
        assert tool is not None
        assert tool.working_directory is None
    finally:
        agent.close()


# ---------------------------------------------------------------------------
# Multi-session isolation (the acceptance criterion for Issue 05)
# ---------------------------------------------------------------------------

def test_two_agentao_instances_do_not_share_memory_db(stub_llm_env, tmp_path):
    dir_a = tmp_path / "session_a"
    dir_b = tmp_path / "session_b"
    dir_a.mkdir()
    dir_b.mkdir()

    agent_a = _make_agent(working_directory=dir_a)
    agent_b = _make_agent(working_directory=dir_b)
    try:
        root_a = Path(agent_a._memory_manager._project_root)
        root_b = Path(agent_b._memory_manager._project_root)
        assert root_a != root_b
        assert root_a == dir_a.resolve() / ".agentao"
        assert root_b == dir_b.resolve() / ".agentao"
    finally:
        agent_a.close()
        agent_b.close()


def test_two_agentao_instances_report_different_cwd_in_system_prompt(stub_llm_env, tmp_path):
    dir_a = tmp_path / "session_a"
    dir_b = tmp_path / "session_b"
    dir_a.mkdir()
    dir_b.mkdir()

    agent_a = _make_agent(working_directory=dir_a)
    agent_b = _make_agent(working_directory=dir_b)
    try:
        prompt_a = agent_a._build_system_prompt()
        prompt_b = agent_b._build_system_prompt()
        assert str(dir_a.resolve()) in prompt_a
        assert str(dir_a.resolve()) not in prompt_b
        assert str(dir_b.resolve()) in prompt_b
        assert str(dir_b.resolve()) not in prompt_a
    finally:
        agent_a.close()
        agent_b.close()


def test_two_agentao_instances_have_isolated_file_tool_reads(stub_llm_env, tmp_path):
    dir_a = tmp_path / "session_a"
    dir_b = tmp_path / "session_b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / "note.txt").write_text("note-A\n", encoding="utf-8")
    (dir_b / "note.txt").write_text("note-B\n", encoding="utf-8")

    agent_a = _make_agent(working_directory=dir_a)
    agent_b = _make_agent(working_directory=dir_b)
    try:
        tool_a = agent_a.tools.get("read_file")
        tool_b = agent_b.tools.get("read_file")
        out_a = tool_a.execute(file_path="note.txt")
        out_b = tool_b.execute(file_path="note.txt")
        assert "note-A" in out_a
        assert "note-B" not in out_a
        assert "note-B" in out_b
        assert "note-A" not in out_b
    finally:
        agent_a.close()
        agent_b.close()


# ---------------------------------------------------------------------------
# ACP wiring — session/new factory passes working_directory through
# ---------------------------------------------------------------------------

def test_acp_session_new_factory_binds_working_directory(stub_llm_env, tmp_path):
    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    acp_initialize.handle_initialize(
        server,
        {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {},
        },
    )

    dir_a = tmp_path / "project_a"
    dir_b = tmp_path / "project_b"
    dir_a.mkdir()
    dir_b.mkdir()

    result_a = acp_session_new.handle_session_new(
        server, {"cwd": str(dir_a), "mcpServers": []}
    )
    result_b = acp_session_new.handle_session_new(
        server, {"cwd": str(dir_b), "mcpServers": []}
    )

    state_a = server.sessions.require(result_a["sessionId"])
    state_b = server.sessions.require(result_b["sessionId"])

    try:
        # The real Agentao runtime must see the session cwd, not process cwd.
        assert state_a.agent.working_directory == dir_a.resolve()
        assert state_b.agent.working_directory == dir_b.resolve()
        # And file tools on each agent must be bound accordingly.
        tool_a = state_a.agent.tools.get("read_file")
        tool_b = state_b.agent.tools.get("read_file")
        assert tool_a.working_directory == dir_a.resolve()
        assert tool_b.working_directory == dir_b.resolve()
    finally:
        server.sessions.close_all()


# ---------------------------------------------------------------------------
# LLM debug log path is anchored to the session working directory
#
# Regression for the ACP launch failure: when an external client (e.g. Zed)
# spawned the agentao subprocess with cwd="/" on macOS, ``LLMClient`` opened
# ``"agentao.log"`` as a relative path, Python resolved it against the
# subprocess cwd, and ``FileHandler`` raised
# ``OSError: [Errno 30] Read-only file system: '/agentao.log'`` from inside
# ``Agentao.__init__``, killing the ACP server before any session could
# start. The fix routes the agent's effective working directory into
# ``LLMClient`` so the log always lands in a writable, session-scoped path.
# ---------------------------------------------------------------------------

def test_agentao_log_lands_in_session_cwd_not_process_cwd(
    stub_llm_env, tmp_path, monkeypatch
):
    """ACP regression: log file follows ``working_directory``, not process cwd."""
    process_cwd = tmp_path / "process_cwd"
    session_cwd = tmp_path / "session_cwd"
    process_cwd.mkdir()
    session_cwd.mkdir()
    monkeypatch.chdir(process_cwd)

    agent = _make_agent(working_directory=session_cwd)
    try:
        assert (session_cwd / "agentao.log").exists(), (
            "log file must be created under the session working directory"
        )
        assert not (process_cwd / "agentao.log").exists(), (
            "log file must NOT be created under the process cwd"
        )
    finally:
        agent.close()


def test_agentao_default_log_uses_process_cwd_for_cli_compat(
    stub_llm_env, tmp_path, monkeypatch
):
    """CLI default (no working_directory): log still lands in the process cwd."""
    monkeypatch.chdir(tmp_path)

    agent = _make_agent()
    try:
        assert (tmp_path / "agentao.log").exists()
    finally:
        agent.close()


def test_llm_client_falls_back_when_primary_log_path_unwritable(
    tmp_path, monkeypatch
):
    """``LLMClient._build_file_handler`` must not raise on a read-only target.

    Simulates the ACP failure mode (``OSError`` from FileHandler) by pointing
    the primary log at a path whose parent is a *file*, which makes
    ``mkdir(parents=True, exist_ok=True)`` raise ``NotADirectoryError`` (an
    ``OSError`` subclass). The fallback should redirect to ``~/.agentao``,
    which we relocate to ``tmp_path`` via HOME so the test stays hermetic.
    """
    monkeypatch.setenv("HOME", str(tmp_path))

    blocker = tmp_path / "not-a-dir"
    blocker.write_text("regular file, not a directory", encoding="utf-8")
    primary = blocker / "agentao.log"  # mkdir on the parent will fail

    from agentao.llm.client import LLMClient

    handler = LLMClient._build_file_handler(str(primary))
    try:
        assert handler is not None, "fallback handler must be returned"
        fallback_path = Path(tmp_path) / ".agentao" / "agentao.log"
        assert Path(handler.baseFilename) == fallback_path.resolve()
        assert fallback_path.exists()
    finally:
        if handler is not None:
            handler.close()


def test_llm_client_anchors_relative_log_to_cwd_at_construction(
    tmp_path, monkeypatch
):
    """A relative ``log_file`` is anchored to ``Path.cwd()`` at __init__ time."""
    monkeypatch.chdir(tmp_path)

    from agentao.llm.client import LLMClient

    handler = LLMClient._build_file_handler("agentao.log")
    try:
        assert handler is not None
        assert Path(handler.baseFilename) == (tmp_path / "agentao.log").resolve()
    finally:
        if handler is not None:
            handler.close()


# ---------------------------------------------------------------------------
# Memory-store fault tolerance during Agentao() construction
#
# Regression for the ACP launch failure: when an external client spawned
# the agentao subprocess in a restricted environment, ``MemoryManager``
# raised ``sqlite3.OperationalError: unable to open database file`` on the
# user-scope DB (``~/.agentao/memory.db``). The manager's try/except only
# caught ``OSError``, so the exception escaped and killed ``Agentao()``
# before any session could be created — breaking plain CLI boot and every
# ACP ``session/new`` spawn.
# ---------------------------------------------------------------------------

def test_agentao_survives_user_memory_db_sqlite_error(
    stub_llm_env, tmp_path, monkeypatch
):
    """``Agentao()`` must boot when the user memory DB cannot be opened."""
    import sqlite3

    from agentao.memory import manager as mgr_mod

    # Point HOME at a writable tmp dir so the test is hermetic, but still
    # fault-inject a sqlite3.OperationalError on the user-scope path so the
    # manager hits the exact crash path reported by the user.
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    user_db = str(home / ".agentao" / "memory.db")

    real_init = mgr_mod.SQLiteMemoryStore.__init__

    def fake_init(self, db_path):
        if db_path == user_db:
            raise sqlite3.OperationalError("unable to open database file")
        real_init(self, db_path)

    monkeypatch.setattr(mgr_mod.SQLiteMemoryStore, "__init__", fake_init)

    from agentao.agent import Agentao

    # Construction must succeed; user store is disabled, project store is live.
    agent = Agentao(working_directory=tmp_path)
    try:
        assert agent._memory_manager.user_store is None
        assert agent._memory_manager.project_store is not None
    finally:
        agent.close()


def test_acp_session_new_survives_user_memory_db_sqlite_error(
    stub_llm_env, tmp_path, monkeypatch
):
    """End-to-end ACP ``session/new`` must not crash on a dead user memory DB."""
    import sqlite3

    from agentao.memory import manager as mgr_mod

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    user_db = str(home / ".agentao" / "memory.db")

    real_init = mgr_mod.SQLiteMemoryStore.__init__

    def fake_init(self, db_path):
        if db_path == user_db:
            raise sqlite3.OperationalError("unable to open database file")
        real_init(self, db_path)

    monkeypatch.setattr(mgr_mod.SQLiteMemoryStore, "__init__", fake_init)

    stdin = io.StringIO("")
    stdout = io.StringIO()
    server = AcpServer(stdin=stdin, stdout=stdout)
    acp_initialize.handle_initialize(
        server,
        {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "clientCapabilities": {},
        },
    )

    project = tmp_path / "project"
    project.mkdir()

    result = acp_session_new.handle_session_new(
        server, {"cwd": str(project), "mcpServers": []}
    )
    try:
        assert "sessionId" in result
        state = server.sessions.require(result["sessionId"])
        assert state.agent.working_directory == project.resolve()
        assert state.agent._memory_manager.user_store is None
    finally:
        server.sessions.close_all()
