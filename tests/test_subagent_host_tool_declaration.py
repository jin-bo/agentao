"""A host tool reaches a sub-agent only by declaring it, and then as a copy.

SUB-03 / PR-b of ``docs/design/subagent-runtime-safety-plan.md``. #255 left
every host tool out of every sub-agent, which is safe and too blunt: a host
that injected a deploy tool had no way to let a sub-agent use it. The opt-in is
``copies_to_subagents`` on the tool object, because a host registers through
three entries (``extra_tools=``, ``add_tool``, a bare ``tools.register``) and
only one of them could carry a per-tool argument.

What is asserted here is the *shape* of the opt-in, not just its happy path:
an undeclared tool stays absent and takes its name with it, the declaration
does not outrank the definition's ``tools:`` list, a copy that fails falls back
to nothing at all, and — the reason a copy exists — the parent and a sub-agent
streaming from the same declared tool at the same time do not cross.

Every call goes through the sub-agent's real ``ToolRunner``; only
``Agentao.chat`` is replaced, as in ``test_subagent_tool_registry.py``.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, List

import pytest
from openai.types.chat import ChatCompletionMessageToolCall

from agentao.agent import Agentao
from agentao.host import Tool
from agentao.tools import ReadFileTool
from agentao.transport import EventType

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


def _sub_agents_call(monkeypatch, *calls, on_sub_agent=None):
    """Make every sub-agent run ``calls`` through its own ``ToolRunner``.

    ``on_sub_agent`` runs before the calls, with the sub-agent as its
    argument — the only place a test can reach a sub-agent's transport, since
    the sub-agent is built inside the wrapper.
    """
    results: Dict[str, str] = {}
    sub_agents: List[Agentao] = []

    def chat(self, user_message, max_iterations=100, cancellation_token=None, images=None):
        sub_agents.append(self)
        if on_sub_agent is not None:
            on_sub_agent(self)
        _, messages = self.tool_runner.execute(list(calls))
        self.messages.extend(messages)
        by_id = {m["tool_call_id"]: m["content"] for m in messages}
        results.update({c.function.name: by_id[c.id] for c in calls})
        return ""

    monkeypatch.setattr(Agentao, "chat", chat)
    return results, sub_agents


def _run(parent, agent="agent_generalist"):
    return parent.tools.tools[agent]._run_sync("x")


def _advertised(sub_agent):
    return {t["function"]["name"] for t in sub_agent.tools.to_openai_format()}


# ── the tools a host would register ─────────────────────────────────────────


class DeclaredTool(Tool):
    """A host tool that opts in. Counts its own calls, per instance."""

    def __init__(self, name: str = "deploy", result: str = "declared") -> None:
        super().__init__()
        self._name = name
        self._result = result
        self.calls = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "a declared host tool"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def copies_to_subagents(self) -> bool:
        return True

    def execute(self, **kwargs: Any) -> str:
        self.calls += 1
        return f"{self._result}:{self.calls}"


class UncopyableTool(DeclaredTool):
    """Declares the opt-in and then cannot honour it."""

    def __copy__(self):
        raise RuntimeError("holds a socket")


class UndeclarableTool(DeclaredTool):
    """Its declaration raises rather than answering."""

    @property
    def copies_to_subagents(self) -> bool:
        raise ValueError("config not loaded")


class StreamingTool(Tool):
    """Streams two chunks, with both agents inside ``execute`` in between.

    The second chunk deliberately re-reads ``self.output_callback`` *after* the
    barrier. On one shared instance the later bind has overwritten the earlier
    one by then, so both agents' second chunks land on whichever transport
    bound last — which is the crossing the copy exists to prevent.
    """

    def __init__(self, barrier: threading.Barrier) -> None:
        super().__init__()
        self._barrier = barrier

    @property
    def name(self) -> str:
        return "stream_marker"

    @property
    def description(self) -> str:
        return "streams"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {"type": "object", "properties": {"who": {"type": "string"}}}

    @property
    def copies_to_subagents(self) -> bool:
        return True

    def execute(self, who: str = "?", **kwargs: Any) -> str:
        self.output_callback(f"{who}-1")
        self._barrier.wait(timeout=20)
        self.output_callback(f"{who}-2")
        return "ok"


# ── absent unless declared ──────────────────────────────────────────────────


def test_an_undeclared_host_tool_is_absent(tmp_path, monkeypatch):
    parent = _parent(tmp_path, extra_tools=[NamedTool("deploy")])
    results, sub_agents = _sub_agents_call(monkeypatch, _call("deploy"))
    try:
        _run(parent)
        (sub_agent,) = sub_agents
        advertised = _advertised(sub_agent)
    finally:
        parent.close()

    assert "deploy" not in advertised
    assert "not found" in results["deploy"]
    assert "deploy" in parent.tools.tools  # the parent still has it


def test_an_undeclared_replacement_takes_the_builtins_name_with_it(tmp_path, monkeypatch):
    """The built-in must not reappear under a name the host took over."""
    target = tmp_path / "secret.txt"
    target.write_text("BUILTIN-READ")
    parent = _parent(tmp_path)
    parent.add_tool(NamedTool("read_file"), replace=True)
    results, sub_agents = _sub_agents_call(
        monkeypatch, _call("read_file", file_path=str(target)),
    )
    try:
        _run(parent)
        (sub_agent,) = sub_agents
        advertised = _advertised(sub_agent)
    finally:
        parent.close()

    assert "read_file" not in advertised
    assert "BUILTIN-READ" not in results["read_file"]
    assert "not found" in results["read_file"]


def test_update_goal_stays_absent(tmp_path, monkeypatch):
    """The CLI's own injected tool holds the *parent's* goal, and declares nothing."""
    from agentao.tools.goal import UpdateGoalTool

    # The goal object is the parent session's; that is the whole hazard.
    goal_tool = UpdateGoalTool(goal=object())
    parent = _parent(tmp_path)
    parent.add_tool(goal_tool)
    _, sub_agents = _sub_agents_call(monkeypatch, _call("complete_task", result="x"))
    try:
        _run(parent)
        (sub_agent,) = sub_agents
        advertised = _advertised(sub_agent)
    finally:
        parent.close()

    assert goal_tool.copies_to_subagents is False
    assert "update_goal" in parent.tools.tools
    assert "update_goal" not in advertised


# ── present when declared, as a copy ────────────────────────────────────────


@pytest.mark.parametrize("entry", ["extra_tools", "add_tool", "register"])
def test_a_declared_host_tool_reaches_a_sub_agent_through_every_entry(
    tmp_path, monkeypatch, entry,
):
    """All three registration entries record origin ``host``, so all three opt in."""
    host_tool = DeclaredTool()
    if entry == "extra_tools":
        parent = _parent(tmp_path, extra_tools=[host_tool])
    else:
        parent = _parent(tmp_path)
        if entry == "add_tool":
            parent.add_tool(host_tool)
        else:
            parent.tools.register(host_tool)

    results, sub_agents = _sub_agents_call(monkeypatch, _call("deploy"))
    try:
        _run(parent)
        (sub_agent,) = sub_agents
        advertised = _advertised(sub_agent)
        childs = sub_agent.tools.tools["deploy"]
    finally:
        parent.close()

    assert "deploy" in advertised
    assert results["deploy"] == "declared:1"
    # A copy, registered as a host tool: not the parent's instance, and the
    # parent's own call count is untouched by the sub-agent's call.
    assert childs is not host_tool
    assert type(childs) is DeclaredTool
    assert host_tool.calls == 0
    assert sub_agent.tools.origin("deploy") == "host"


def test_a_declared_replacement_keeps_the_hosts_implementation(tmp_path, monkeypatch):
    """A declared host tool named ``read_file`` must not be the built-in."""
    target = tmp_path / "secret.txt"
    target.write_text("BUILTIN-READ")
    parent = _parent(tmp_path, extra_tools=[DeclaredTool("read_file", "HOST-READ")])
    results, sub_agents = _sub_agents_call(
        monkeypatch, _call("read_file", file_path=str(target)),
    )
    try:
        _run(parent)
        (sub_agent,) = sub_agents
        childs = sub_agent.tools.tools["read_file"]
    finally:
        parent.close()

    assert results["read_file"] == "HOST-READ:1"
    assert "BUILTIN-READ" not in results["read_file"]
    assert not isinstance(childs, ReadFileTool)


def test_the_declaration_does_not_bypass_the_definitions_allowlist(tmp_path, monkeypatch):
    """``codebase-investigator`` lists five tools, and ``deploy`` is not one."""
    parent = _parent(tmp_path, extra_tools=[DeclaredTool()])
    results, sub_agents = _sub_agents_call(monkeypatch, _call("deploy"))
    try:
        _run(parent, "agent_codebase_investigator")
        (sub_agent,) = sub_agents
        advertised = _advertised(sub_agent)
    finally:
        parent.close()

    assert "deploy" not in advertised
    assert "not found" in results["deploy"]
    assert "read_file" in advertised  # the list itself still works


def test_the_copy_is_made_once_per_spawn_so_state_survives_the_sub_task(
    tmp_path, monkeypatch,
):
    parent = _parent(tmp_path, extra_tools=[DeclaredTool()])
    results, sub_agents = _sub_agents_call(monkeypatch, _call("deploy"))

    def chat_twice(self, user_message, max_iterations=100, cancellation_token=None,
                   images=None):
        sub_agents.append(self)
        seen = []
        for i in (1, 2):
            _, messages = self.tool_runner.execute([
                ChatCompletionMessageToolCall(
                    id=f"c{i}", type="function",
                    function={"name": "deploy", "arguments": "{}"},
                ),
            ])
            seen.append(messages[0]["content"])
        results["both"] = "|".join(seen)
        return ""

    monkeypatch.setattr(Agentao, "chat", chat_twice)
    try:
        _run(parent)
    finally:
        parent.close()

    # One copy for the whole sub-task, not one per call: the count advances.
    assert results["both"] == "declared:1|declared:2"


# ── a copy that cannot be made ─────────────────────────────────────────────


def test_a_copy_that_raises_leaves_the_tool_absent(tmp_path, monkeypatch, caplog):
    """Never shared, never replaced by the built-in, and the reason is logged."""
    target = tmp_path / "secret.txt"
    target.write_text("BUILTIN-READ")
    host_tool = UncopyableTool("read_file")
    parent = _parent(tmp_path, extra_tools=[host_tool])
    results, sub_agents = _sub_agents_call(
        monkeypatch, _call("read_file", file_path=str(target)),
    )
    with caplog.at_level(logging.WARNING, logger="agentao.agents.tools._wrapper"):
        try:
            _run(parent)
            (sub_agent,) = sub_agents
            advertised = _advertised(sub_agent)
        finally:
            parent.close()

    assert "read_file" not in advertised
    assert "BUILTIN-READ" not in results["read_file"]  # not the built-in either
    assert host_tool.calls == 0                        # and not shared
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "read_file" in warning
    assert "RuntimeError" in warning and "holds a socket" in warning


def test_a_declaration_that_raises_fails_closed(tmp_path, monkeypatch, caplog):
    parent = _parent(tmp_path, extra_tools=[UndeclarableTool()])
    results, sub_agents = _sub_agents_call(monkeypatch, _call("deploy"))
    with caplog.at_level(logging.WARNING, logger="agentao.agents.tools._wrapper"):
        try:
            _run(parent)
            (sub_agent,) = sub_agents
            advertised = _advertised(sub_agent)
        finally:
            parent.close()

    assert "deploy" not in advertised
    assert "not found" in results["deploy"]
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "copies_to_subagents" in warning
    assert "ValueError" in warning and "config not loaded" in warning


# ── why the copy exists ────────────────────────────────────────────────────


def test_streaming_from_one_declared_tool_stays_on_each_agents_transport(
    tmp_path, monkeypatch,
):
    """Parent and sub-agent inside ``execute`` at the same time, no crossing."""
    barrier = threading.Barrier(2)
    parent = _parent(tmp_path, extra_tools=[StreamingTool(barrier)])

    parent_chunks: List[str] = []
    sub_chunks: List[str] = []

    def collect(sink):
        def listener(event):
            if event.type is EventType.TOOL_OUTPUT and event.data["tool"] == "stream_marker":
                sink.append(event.data["chunk"])
        return listener

    parent.transport.subscribe(collect(parent_chunks))
    _, sub_agents = _sub_agents_call(
        monkeypatch, _call("stream_marker", who="sub"),
        on_sub_agent=lambda sa: sa.transport.subscribe(collect(sub_chunks)),
    )

    error: List[BaseException] = []

    def drive_parent():
        try:
            parent.tool_runner.execute([_call("stream_marker", who="parent")])
        except BaseException as exc:  # surfaced below, never swallowed
            error.append(exc)
            barrier.abort()

    thread = threading.Thread(target=drive_parent, daemon=True)
    thread.start()
    try:
        _run(parent)
        thread.join(timeout=30)
    finally:
        parent.close()

    assert not error, error
    assert not thread.is_alive()
    # The sub-agent's transport saw exactly its own two chunks. This is the
    # assertion that fails on a shared instance: after the barrier both agents
    # re-read one ``output_callback``, so whichever bound last collects both
    # post-barrier chunks and ``parent-2`` shows up here.
    assert sub_chunks == ["sub-1", "sub-2"]
    # The parent's transport also sees the sub-agent's chunks, by design — the
    # wrapper bridges a sub-agent's output up so the CLI can show it — so only
    # the parent's own are asserted on this side.
    assert [c for c in parent_chunks if c.startswith("parent")] == [
        "parent-1", "parent-2",
    ]


def test_the_default_is_no(tmp_path):
    """The declaration is opt-in: every built-in answers False."""
    parent = _parent(tmp_path)
    try:
        declaring = sorted(
            name for name, tool in parent.tools.tools.items()
            if getattr(tool, "copies_to_subagents", False)
        )
    finally:
        parent.close()

    assert declaring == []
    assert NamedTool("x").copies_to_subagents is False
