"""Read-only mode: which tools it lets through, and every way it is entered.

The CLI and ``agentao run`` enter read-only mode by setting the runner's flag
*and* the engine's mode. ACP ``session/set_mode`` and an embedded host set only
the engine's mode, whose ``read-only`` preset is an empty rule list, so on those
paths writes and shell used to fall through to ASK (which the default transport
approves) and ``save_memory`` ran outright.

Tools whose only effect is session state (``activate_skill``, ``todo_write``)
are allowed in read-only mode; ``save_memory`` writes SQLite and is not.

Every test drives the real ``ToolRunner`` with the real built-in tools, and the
ACP case goes through the real ``session/set_mode`` handler, so the assertions
are on what reached the model and what reached the disk.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from agentao import Agentao
from agentao.acp import session_set_mode as acp_set_mode
from agentao.acp.models import AcpSessionState
from agentao.agents.tools import AgentToolWrapper
from agentao.permissions import PermissionEngine, PermissionMode
from agentao.skills import manager as skills_manager
from agentao.tools.base import Tool
from agentao.transport import SdkTransport

from .support.acp_server import make_initialized_server

_BLOCKED = "[Readonly mode]"


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(skills_manager, "_GLOBAL_SKILLS_DIR", tmp_path / "home" / "skills")
    monkeypatch.setattr(skills_manager, "_BUNDLED_SKILLS_DIR", tmp_path / "no-bundled-skills")
    skill = tmp_path / ".agentao" / "skills" / "demo-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: A demo skill\n---\n\n# demo-skill\n\nBODY\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.txt").write_text("original\n", encoding="utf-8")
    agent = _build(tmp_path)
    yield agent
    agent.close()


def _build(tmp_path, *, rules=None, **kwargs):
    return Agentao(
        api_key="k", base_url="https://test.local/v1", model="m",
        working_directory=tmp_path,
        logger=logging.getLogger("test.read_only_mode_gate"),
        permission_engine=PermissionEngine(project_root=tmp_path, rules=rules),
        **kwargs,
    )


def _enter_cli(agent):
    agent.permission_engine.set_mode(PermissionMode.READ_ONLY)
    agent.tool_runner.set_readonly_mode(True)


def _enter_engine_only(agent):
    # What the embedding guide tells a host to do.
    agent.permission_engine.set_mode(PermissionMode.READ_ONLY)


def _enter_acp(agent):
    server = make_initialized_server()
    state = AcpSessionState(session_id="s")
    state.agent = agent
    server.sessions.create(state)
    acp_set_mode.handle_session_set_mode(server, {"sessionId": "s", "modeId": "read-only"})


def _enter_subagent_snapshot(agent):
    # A sub-agent decides with a snapshot of its parent's engine.
    parent = PermissionEngine(project_root=agent.working_directory)
    parent.set_mode(PermissionMode.READ_ONLY)
    agent.tool_runner.set_permission_engine(
        parent.snapshot(project_root=agent.working_directory),
    )


_ENTRIES = {
    "cli": _enter_cli,
    "engine-only": _enter_engine_only,
    "acp-set-mode": _enter_acp,
    "subagent-snapshot": _enter_subagent_snapshot,
}


def _run(agent, calls):
    tool_calls = [
        SimpleNamespace(
            id=f"call_{i}", type="function",
            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        )
        for i, (name, args) in enumerate(calls)
    ]
    _, messages = agent.tool_runner.execute(tool_calls)
    by_id = {m["tool_call_id"]: m["content"] for m in messages}
    return {name: by_id[f"call_{i}"] for i, (name, _) in enumerate(calls)}


@pytest.mark.parametrize("enter", list(_ENTRIES.values()), ids=list(_ENTRIES))
def test_read_only_mode_holds_however_it_was_entered(agent, tmp_path, enter):
    enter(agent)
    notes = tmp_path / "notes.txt"
    results = _run(agent, [
        ("write_file", {"file_path": str(tmp_path / "new.txt"), "content": "x"}),
        ("replace", {"file_path": str(notes), "old_text": "original", "new_text": "changed"}),
        ("run_shell_command", {"command": "echo hi"}),
        ("save_memory", {"key": "k", "value": "v"}),
        ("read_file", {"file_path": str(notes)}),
        ("todo_write", {"todos": [{"content": "look around", "status": "pending"}]}),
        ("activate_skill", {"skill_name": "demo-skill", "task_description": "t"}),
    ])

    for name in ("write_file", "replace", "run_shell_command", "save_memory"):
        assert results[name].startswith(_BLOCKED), (name, results[name])
    assert not (tmp_path / "new.txt").exists()
    assert notes.read_text(encoding="utf-8") == "original\n"
    # The store itself, not only the message: before the fix this row landed.
    assert agent._memory_manager.get_all_entries() == []

    for name in ("read_file", "todo_write", "activate_skill"):
        assert _BLOCKED not in results[name], (name, results[name])
    assert "original" in results["read_file"]
    assert agent.tools.tools["todo_write"].todos == [
        {"content": "look around", "status": "pending"},
    ]
    assert "demo-skill" in agent.skill_manager.get_active_skills()


def test_leaving_read_only_through_the_engine_restores_writes(agent, tmp_path):
    _enter_engine_only(agent)
    agent.permission_engine.set_mode(PermissionMode.WORKSPACE_WRITE)
    results = _run(agent, [
        ("write_file", {"file_path": str(tmp_path / "new.txt"), "content": "x"}),
    ])
    assert _BLOCKED not in results["write_file"]
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "x"


def test_which_built_ins_read_only_mode_admits(agent):
    tools = agent.tools.tools
    assert tools["activate_skill"].is_read_only is True
    assert tools["todo_write"].is_read_only is True
    # A SQLite write outlives the session, so it stays out.
    assert tools["save_memory"].is_read_only is False


class _HostTool(Tool):
    """A host business tool, like ``examples/ticket-automation``'s ``draft_reply``."""

    def __init__(self, name, *, read_only=False, confirm=False):
        super().__init__()
        self._name, self._read_only, self._confirm = name, read_only, confirm
        self.calls = 0

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return "host tool"

    @property
    def parameters(self):
        return {"type": "object", "properties": {}}

    @property
    def is_read_only(self):
        return self._read_only

    @property
    def requires_confirmation(self):
        return self._confirm

    def execute(self, **kwargs):
        self.calls += 1
        return "ran"


def test_no_rule_can_allow_a_host_tool_that_is_not_read_only(tmp_path):
    # The behaviour change hosts on the engine-only path meet: the gate runs
    # before the engine, so an explicit ``allow`` no longer reaches the tool.
    agent = _build(tmp_path, rules=[{"tool": "draft_reply", "action": "allow"}])
    try:
        tool = _HostTool("draft_reply")
        agent.tools.register(tool)
        _enter_engine_only(agent)
        results = _run(agent, [("draft_reply", {})])
        assert results["draft_reply"].startswith(_BLOCKED)
        assert tool.calls == 0
    finally:
        agent.close()


def test_a_mode_switch_while_confirming_keeps_the_read_only_label(tmp_path):
    # Phase 2 can change the mode (the CLI's "allow all" answer does); a call
    # phase 1 denied for read-only must still say so in phase 3.
    def confirm(*_):
        agent.permission_engine.set_mode(PermissionMode.FULL_ACCESS)
        return True

    agent = _build(tmp_path, transport=SdkTransport(confirm_tool=confirm))
    try:
        asker = _HostTool("lookup", read_only=True, confirm=True)
        agent.tools.register(asker)
        _enter_engine_only(agent)
        results = _run(agent, [
            ("lookup", {}),
            ("write_file", {"file_path": str(tmp_path / "new.txt"), "content": "x"}),
        ])
        assert asker.calls == 1
        assert results["write_file"].startswith(_BLOCKED), results["write_file"]
        assert not (tmp_path / "new.txt").exists()
    finally:
        agent.close()


def test_a_sub_agent_spawned_from_engine_only_read_only_gets_the_flag(tmp_path):
    agent = _build(tmp_path, enable_builtin_agents=True)
    try:
        wrappers = [
            t for t in agent.tools.tools.values() if isinstance(t, AgentToolWrapper)
        ]
        assert wrappers, "expected at least one built-in agent tool"
        assert not any(w._readonly_mode_getter() for w in wrappers)
        _enter_engine_only(agent)
        assert all(w._readonly_mode_getter() is True for w in wrappers)
    finally:
        agent.close()
