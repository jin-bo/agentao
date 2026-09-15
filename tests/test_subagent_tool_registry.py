"""A sub-agent executes from the registry its model sees (#238), borrows the
parent's MCP tools (#239), and runs file and shell calls where the parent does.

A sub-agent used to be shown one registry and execute from another: the model
saw the definition's ``tools:`` list, while calls resolved against everything
construction had registered. So ``complete_task`` was never found, a tool
outside the list still ran, a sub-agent could spawn another, and a host tool
that replaced a built-in resolved to the built-in's default implementation.

Every call here goes through the sub-agent's real ``ToolRunner``. Only
``Agentao.chat`` is replaced, because a real ``chat()`` is a networked LLM turn;
the replacement runs the tool calls a model would have sent and records their
results, as the chat loop does.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import pytest
from openai.types.chat import ChatCompletionMessageToolCall

from agentao.agent import Agentao
from agentao.agents.bg_store import BackgroundTaskStore
from agentao.cancellation import CancellationToken
from agentao.capabilities import (
    BackgroundHandle,
    FileStat,
    LocalFileSystem,
    LocalShellExecutor,
    ShellResult,
)
from agentao.mcp import InMemoryMCPRegistry
from agentao.mcp.client import McpClientManager
from agentao.tools import ReadFileTool

from tests.support.stdio_mcp_server import started, stdio_server
from tests.support.tools import NamedTool


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows


def _parent(tmp_path, **kwargs):
    kwargs.setdefault("enable_builtin_agents", True)
    return Agentao(
        working_directory=tmp_path, api_key="k",
        base_url="https://test.local/v1", model="m", **kwargs,
    )


def _call(name, **arguments):
    return ChatCompletionMessageToolCall(
        id=f"call-{name}", type="function",
        function={"name": name, "arguments": json.dumps(arguments)},
    )


def _sub_agents_call(monkeypatch, *calls):
    """Make every sub-agent run ``calls`` through its own ``ToolRunner``.

    Returns ``(results, sub_agents)``: each result keyed by the name the call
    was made with, and the sub-agents that ran. Keyed by call, not by the name
    on the result message, which the planner's name repair can change."""
    results, sub_agents = {}, []

    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        sub_agents.append(self)
        if len(sub_agents) > 1:
            return ""  # a nested sub-agent, which must not exist; see its test
        _, messages = self.tool_runner.execute(list(calls))
        self.messages.extend(messages)
        by_id = {m["tool_call_id"]: m["content"] for m in messages}
        results.update({c.function.name: by_id[c.id] for c in calls})
        return ""

    monkeypatch.setattr(Agentao, "chat", chat)
    return results, sub_agents


def _run(parent, agent="agent_generalist"):
    return parent.tools.tools[agent]._run_sync("x")


def _not_found(text):
    return "not found" in text


def _eventually(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()


# ── one registry ────────────────────────────────────────────────────────────


def test_complete_task_hands_the_sub_agents_answer_back(tmp_path, monkeypatch):
    parent = _parent(tmp_path)
    results, _ = _sub_agents_call(monkeypatch, _call("complete_task", result="ANSWER"))
    try:
        result, stats = _run(parent)
    finally:
        parent.close()

    assert results["complete_task"] == "ANSWER"
    assert result == "ANSWER"
    assert stats["incomplete"] is None


def test_a_tool_outside_the_definitions_list_does_not_run(tmp_path, monkeypatch):
    """``codebase-investigator`` is read-only: ``write_file`` is not on its list."""
    target = tmp_path / "written.txt"
    parent = _parent(tmp_path)
    results, sub_agents = _sub_agents_call(
        monkeypatch, _call("write_file", file_path=str(target), content="x"),
    )
    try:
        _run(parent, "agent_codebase_investigator")
        (sub_agent,) = sub_agents
        advertised = {t["function"]["name"] for t in sub_agent.tools.to_openai_format()}
    finally:
        parent.close()

    assert _not_found(results["write_file"])
    assert not target.exists()
    assert "write_file" not in advertised
    assert "read_file" in advertised


def test_a_sub_agent_cannot_spawn_another(tmp_path, monkeypatch):
    """Through the parent's agent tools, or through its own: a project agent is
    registered into every runtime built in that project, the sub-agent too."""
    agents_dir = tmp_path / ".agentao" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "helper.md").write_text(
        "---\nname: helper\ndescription: project helper\nmax_turns: 5\n---\nHelp.\n"
    )
    parent = _parent(tmp_path)
    results, sub_agents = _sub_agents_call(
        monkeypatch, _call("agent_helper", task="y"), _call("agent_generalist", task="y"),
    )
    try:
        _run(parent, "agent_helper")
    finally:
        parent.close()

    assert _not_found(results["agent_helper"])
    assert _not_found(results["agent_generalist"])
    assert len(sub_agents) == 1


# ── the parent's restrictions ───────────────────────────────────────────────


def test_a_tool_the_parent_does_not_have_is_not_given_back(tmp_path, monkeypatch):
    """Disabled at construction, or removed later: the generalist has no
    ``tools:`` list, so only the parent's registry can keep these out."""
    target = tmp_path / "written.txt"
    target.write_text("old")
    parent = _parent(tmp_path, disable_tools={"write_file"})
    parent.remove_tool("replace")
    results, _ = _sub_agents_call(
        monkeypatch,
        _call("write_file", file_path=str(target), content="new"),
        _call("replace", file_path=str(target), old_text="old", new_text="new"),
        _call("list_directory", directory_path=str(tmp_path)),
    )
    try:
        _run(parent)
    finally:
        parent.close()

    assert _not_found(results["write_file"])
    assert _not_found(results["replace"])
    assert target.read_text() == "old"
    assert "written.txt" in results["list_directory"]


def test_the_parents_enabled_tools_bound_the_sub_agent(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("hello")
    parent = _parent(tmp_path, enabled_tools={"read_file", "agent_generalist"})
    results, _ = _sub_agents_call(
        monkeypatch,
        _call("read_file", file_path=str(tmp_path / "a.txt")),
        _call("list_directory", directory_path=str(tmp_path)),
    )
    try:
        _run(parent)
    finally:
        parent.close()

    assert "hello" in results["read_file"]
    assert _not_found(results["list_directory"])


class _Counting(NamedTool):
    def __init__(self, name):
        super().__init__(name)
        self.calls = 0

    def execute(self, **kwargs):
        self.calls += 1
        return f"host {self.name}"


def test_a_host_tool_is_left_out_even_where_it_replaced_a_built_in(tmp_path, monkeypatch):
    """Neither the host's implementation nor the built-in it replaced runs."""
    (tmp_path / "a.txt").write_text("from disk")
    host_read, host_only = _Counting("read_file"), _Counting("host_lookup")
    parent = _parent(tmp_path, extra_tools=[host_read, host_only])
    results, sub_agents = _sub_agents_call(
        monkeypatch,
        _call("read_file", file_path=str(tmp_path / "a.txt")),
        _call("host_lookup"),
    )
    try:
        _run(parent)
    finally:
        parent.close()
    (sub_agent,) = sub_agents

    assert _not_found(results["read_file"])
    assert _not_found(results["host_lookup"])
    assert host_read.calls == host_only.calls == 0
    assert {"read_file", "host_lookup"}.isdisjoint(sub_agent.tools.tools)


@pytest.mark.parametrize("entry", ["extra_tools", "add_tool", "register"])
def test_a_replacement_made_from_the_built_ins_own_class_is_left_out(
    tmp_path, monkeypatch, entry,
):
    """#256: the replacement is an instance of ``ReadFileTool`` itself. It used
    to be taken for the built-in, so the sub-agent read with its own default
    ``read_file`` instead of the host's."""
    (tmp_path / "a.txt").write_text("from disk")
    host_read = ReadFileTool()
    if entry == "extra_tools":
        parent = _parent(tmp_path, extra_tools=[host_read])
    else:
        parent = _parent(tmp_path)
        if entry == "add_tool":
            parent.add_tool(host_read, replace=True)
        else:
            parent.tools.register(host_read, replace=True)
    results, sub_agents = _sub_agents_call(
        monkeypatch, _call("read_file", file_path=str(tmp_path / "a.txt")),
    )
    try:
        assert parent.tools.tools["read_file"] is host_read
        _run(parent)
    finally:
        parent.close()
    (sub_agent,) = sub_agents

    assert _not_found(results["read_file"])
    assert "read_file" not in sub_agent.tools.tools


def test_a_configured_web_search_is_not_swapped_for_the_default(tmp_path, monkeypatch):
    """The example in ``docs/design/host-tool-injection.md``. The sub-agent's own
    ``WebSearchTool()`` has none of the host's configuration and, with no keys
    set, searches DuckDuckGo."""
    pytest.importorskip("bs4")
    from agentao.tools import WebSearchTool

    parent = _parent(tmp_path, extra_tools=[WebSearchTool(backend="bocha", api_key="host-key")])
    _, sub_agents = _sub_agents_call(monkeypatch)
    try:
        _run(parent)
    finally:
        parent.close()
    (sub_agent,) = sub_agents

    assert "web_search" not in sub_agent.tools.tools


def test_the_built_ins_a_sub_agent_gets_are_its_own_instances(tmp_path, monkeypatch):
    """Including ``check_background_agent``, which the agent-tool pass registers
    a second time on the parent."""
    store = BackgroundTaskStore(persistence_dir=None)
    parent = _parent(tmp_path, bg_store=store)
    _, sub_agents = _sub_agents_call(monkeypatch)
    try:
        _run(parent)
    finally:
        parent.close()
    (sub_agent,) = sub_agents

    for name in ("read_file", "check_background_agent", "cancel_background_agent"):
        assert name in sub_agent.tools.tools
        assert sub_agent.tools.tools[name] is not parent.tools.tools[name]
        assert sub_agent.tools.origin(name) == "builtin"
    assert sub_agent.tools.origin("complete_task") == "builtin"


def test_a_left_out_tool_is_not_found_rather_than_run_as_a_similar_one(tmp_path, monkeypatch):
    """The host replaced ``check_background_agent``, so the sub-agent does not
    get it. Its call used to be repaired to the nearest name it did get,
    ``cancel_background_agent``, which cancelled the task it meant to check."""
    store = BackgroundTaskStore(persistence_dir=None)
    store.register("task-1", "worker", "long job")
    store.mark_running("task-1")
    store.register_token("task-1", CancellationToken())
    parent = _parent(tmp_path, bg_store=store, extra_tools=[_Counting("check_background_agent")])
    results, _ = _sub_agents_call(monkeypatch, _call("check_background_agent", agent_id="task-1"))
    try:
        _run(parent)
    finally:
        parent.close()

    assert _not_found(results["check_background_agent"])
    assert store.get("task-1")["status"] == "running"


@pytest.mark.parametrize(
    "agent, warned",
    [("agent_codebase_investigator", True), ("agent_generalist", False)],
    ids=["listed", "not-listed"],
)
def test_a_left_out_tool_warns_only_when_the_definition_lists_it(
    tmp_path, monkeypatch, caplog, agent, warned,
):
    parent = _parent(tmp_path, extra_tools=[_Counting("read_file")])
    _sub_agents_call(monkeypatch)
    try:
        with caplog.at_level(logging.DEBUG, logger="agentao.agents.tools._wrapper"):
            _run(parent, agent)
    finally:
        parent.close()

    warnings = [
        r.getMessage() for r in caplog.records
        if r.levelno == logging.WARNING and "does not get" in r.getMessage()
    ]
    assert bool(warnings) is warned
    if warned:
        assert "read_file" in warnings[0]


# ── MCP: the parent's connection ────────────────────────────────────────────


def _from_project_mcp_json(tmp_path, config):
    (tmp_path / ".agentao").mkdir(exist_ok=True)
    (tmp_path / ".agentao" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"probe": config}})
    )
    return _parent(tmp_path), None


def _from_extra_mcp_servers(tmp_path, config):
    return _parent(tmp_path, extra_mcp_servers={"probe": config}), None


def _from_an_injected_manager(tmp_path, config):
    manager = McpClientManager({"probe": config})
    manager.connect_all()
    return _parent(tmp_path, mcp_manager=manager), manager


@pytest.mark.parametrize(
    "build",
    [_from_project_mcp_json, _from_extra_mcp_servers, _from_an_injected_manager],
    ids=["mcp.json", "extra_mcp_servers", "mcp_manager"],
)
def test_a_sub_agent_calls_mcp_over_the_parents_connection_alongside_it(
    tmp_path, monkeypatch, build,
):
    config, marks = stdio_server(tmp_path, delay=1.0)
    parent, manager = build(tmp_path, config)
    results, _ = _sub_agents_call(monkeypatch, _call("mcp_probe_slow_a"))
    child = threading.Thread(target=lambda: _run(parent), daemon=True)
    try:
        assert len(started(marks)) == 1
        child.start()
        assert _eventually(lambda: (marks / "called-slow_a").exists())
        _, messages = parent.tool_runner.execute([_call("mcp_probe_slow_b")])
        child.join(10)

        assert not child.is_alive()
        assert results["mcp_probe_slow_a"] == "slow_a"
        assert messages[0]["content"] == "slow_b"
        # Both calls were in flight together, over the one server the parent launched.
        assert (marks / "overlap").exists()
        assert len(started(marks)) == 1
        # And the sub-agent's close left that connection alone.
        assert not any(marks.glob("eof-*"))
    finally:
        parent.close()
        if manager is not None:
            manager.disconnect_all()


def test_a_sub_agent_launches_no_server_its_parent_did_not(tmp_path, monkeypatch):
    """A host gave the parent its MCP servers in code, and the project also has
    an ``mcp.json``. The sub-agent used to read that file and launch it."""
    config, marks = stdio_server(tmp_path)
    (tmp_path / ".agentao").mkdir()
    (tmp_path / ".agentao" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"probe": config}})
    )
    parent = _parent(tmp_path, mcp_registry=InMemoryMCPRegistry())
    results, _ = _sub_agents_call(monkeypatch, _call("mcp_probe_slow_a"))
    try:
        _run(parent)
    finally:
        parent.close()

    assert started(marks) == []
    assert _not_found(results["mcp_probe_slow_a"])


# ── where file and shell calls run ──────────────────────────────────────────


class _HostFileSystem:
    """An in-memory filesystem holding one file."""

    def __init__(self, path, text):
        self._path, self._data = path, text.encode()
        self.calls = []

    def _record(self, name, path):
        self.calls.append((name, path))

    def is_file(self, path):
        self._record("is_file", path)
        return path == self._path

    def read_partial(self, path, n):
        self._record("read_partial", path)
        return self._data[:n]

    def open_text(self, path):
        self._record("open_text", path)
        return iter(self._data.decode().splitlines(keepends=True))

    def stat(self, path):
        self._record("stat", path)
        return FileStat(size=len(self._data), mtime=0.0, is_dir=False, is_file=True)

    def __getattr__(self, name):
        raise AssertionError(f"unexpected filesystem call: {name}")


class _HostShell:
    def __init__(self):
        self.calls = []

    def run(self, request):
        self.calls.append(request.command)
        return ShellResult(returncode=0, stdout=b"from the host shell\n", stderr=b"")

    def run_background(self, request):
        self.calls.append(request.command)
        return BackgroundHandle(pid=1, pgid=1, command=request.command, cwd=request.cwd)


def test_sub_agent_file_and_shell_calls_run_on_the_parents_backends(tmp_path, monkeypatch):
    path = tmp_path / "only-in-the-host.txt"
    filesystem, shell = _HostFileSystem(path, "from the host filesystem"), _HostShell()
    parent = _parent(tmp_path, filesystem=filesystem, shell=shell)

    def local(self, *args, **kwargs):
        raise AssertionError(f"a sub-agent built a local {type(self).__name__}")

    monkeypatch.setattr(LocalFileSystem, "__init__", local)
    monkeypatch.setattr(LocalShellExecutor, "__init__", local)
    results, _ = _sub_agents_call(
        monkeypatch,
        _call("read_file", file_path=str(path)),
        _call("run_shell_command", command="echo hi"),
    )
    try:
        _run(parent)
    finally:
        parent.close()

    assert "from the host filesystem" in results["read_file"]
    assert "from the host shell" in results["run_shell_command"]
    assert ("open_text", path) in filesystem.calls
    assert shell.calls == ["echo hi"]
    assert not path.exists()
