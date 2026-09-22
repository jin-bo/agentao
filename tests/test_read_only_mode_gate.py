"""Read-only mode: which tools it lets through.

Tools whose only effect is session state (``activate_skill``, ``todo_write``)
are allowed in read-only mode; ``save_memory`` writes SQLite and is not.

Every test drives the real ``ToolRunner`` with the real built-in tools, so the
assertions are on what reached the model and what reached the disk.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from agentao import Agentao
from agentao.permissions import PermissionEngine, PermissionMode
from agentao.skills import manager as skills_manager

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


_ENTRIES = {
    "cli": _enter_cli,
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


def test_which_built_ins_read_only_mode_admits(agent):
    tools = agent.tools.tools
    assert tools["activate_skill"].is_read_only is True
    assert tools["todo_write"].is_read_only is True
    # A SQLite write outlives the session, so it stays out.
    assert tools["save_memory"].is_read_only is False
