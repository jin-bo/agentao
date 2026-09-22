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
from agentao.permissions import PermissionEngine, PermissionMode
from agentao.skills import manager as skills_manager

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
    logger = logging.getLogger("test.read_only_mode_gate")
    agent = Agentao(
        api_key="k", base_url="https://test.local/v1", model="m",
        working_directory=tmp_path,
        logger=logger,
        permission_engine=PermissionEngine(project_root=tmp_path),
    )
    yield agent
    agent.close()


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
