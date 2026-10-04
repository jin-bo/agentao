"""Tool descriptions state what the code does.

Each test ties a claim in a model-facing description to the code that makes
it true, so the two cannot drift apart again. Every case here was once wrong:
the description promised all lines, listed four of five statuses, called a
shell-capable agent read-only, or contradicted its own example.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args
from unittest.mock import MagicMock

import agentao
from agentao.agents.bg_store import BgTaskStatus
from agentao.agents.tools import CancelBackgroundAgentTool, CheckBackgroundAgentTool
from agentao.frontmatter import parse_frontmatter
from agentao.mcp.resource_tools import ListMcpResourcesTool, ListMcpResourceTemplatesTool
from agentao.tools.ask_user import AskUserTool
from agentao.tools.file_ops import MAX_LINE_LENGTH, MAX_LINES_DEFAULT, ReadFileTool
from agentao.tools.memory import SaveMemoryTool

_DEFINITIONS = Path(agentao.__file__).parent / "agents" / "definitions"


def test_read_file_states_the_default_line_cap():
    tool = ReadFileTool()
    text = tool.description + json.dumps(tool.parameters)
    assert str(MAX_LINES_DEFAULT) in text
    assert str(MAX_LINE_LENGTH) in text
    assert "all lines" not in text


def test_check_background_agent_names_every_status():
    description = CheckBackgroundAgentTool(MagicMock()).description
    for status in get_args(BgTaskStatus):
        assert f"'{status}'" in description, status


def test_cancel_background_agent_names_every_terminal_status():
    description = CancelBackgroundAgentTool(MagicMock()).description
    for status in ("completed", "failed", "cancelled"):
        assert status in description, status


def test_each_listing_tool_describes_its_own_server_parameter():
    resources = ListMcpResourcesTool(MagicMock()).parameters["properties"]["server"]
    templates = ListMcpResourceTemplatesTool(MagicMock()).parameters["properties"]["server"]
    assert "resources" in resources["description"]
    assert "templates" in templates["description"]
    assert "resources" not in templates["description"]


def test_save_memory_examples_obey_its_own_rule():
    # The description says not to save general project context.
    tool = SaveMemoryTool(MagicMock())
    assert "project context" in tool.description
    assert "project_context" not in json.dumps(tool.parameters)


def test_save_memory_does_not_send_a_yes_no_question_to_ask_user():
    # ask_user refuses yes/no confirmations, so save_memory must not suggest one.
    assert "yes/no" in AskUserTool().description
    assert "Should I remember" not in SaveMemoryTool(MagicMock()).description


def test_bundled_agent_descriptions_match_their_tools():
    for md in sorted(_DEFINITIONS.glob("*.md")):
        frontmatter, _ = parse_frontmatter(md.read_text(), source=str(md))
        description = frontmatter.get("description", "").lower()
        tools = frontmatter.get("tools")
        if tools is not None and "run_shell_command" in tools:
            assert "read-only" not in description, md.name
        if tools is None:
            # No ``tools:`` list still withholds agent and plan tools
            # (``_narrow_tools``), so "all tools" is never true.
            assert "all tools" not in description, md.name


def test_shell_background_steps_match_run_background(monkeypatch, tmp_path):
    # ``_run_background`` returns as soon as ``Popen`` does: nothing waits
    # for the command or reads its errors. POSIX reports a PGID, Windows a
    # PID with a tree kill.
    from agentao.tools.shell import ShellTool

    monkeypatch.setattr("agentao.tools.shell.IS_WINDOWS", False)
    posix = ShellTool().description
    assert "does not check the command for errors" in posix
    assert "briefly" not in posix
    assert "kill -- -PGID" in posix

    monkeypatch.setattr("agentao.tools.shell.IS_WINDOWS", True)
    windows = ShellTool().description
    assert "taskkill /F /T /PID <PID>" in windows
    assert "PGID" not in windows


def test_builtin_descriptions_use_no_semicolon(tmp_path):
    # ASD-STE100 Rule 8.1, which the system prompt follows since #402.
    import logging

    from agentao.agent import Agentao

    agent = Agentao(
        working_directory=tmp_path, logger=logging.getLogger("test"),
        api_key="x", base_url="http://127.0.0.1:1", model="m",
    )
    from agentao.agents.tools import AgentToolWrapper
    from agentao.agents.tools._complete import CompleteTaskTool
    from agentao.mcp.resource_tools import ReadMcpResourceTool
    from agentao.mcp.skill_tools import ReadSkillFileTool
    from agentao.tools.goal import UpdateGoalTool
    from agentao.tools.plan import PlanFinalizeTool, PlanSaveTool

    # Tools a bare agent does not register: background agents, MCP resources
    # and skills, goal, plan and sub-agent tools.
    tools = list(agent.tools.tools.values()) + [
        CheckBackgroundAgentTool(MagicMock()),
        CancelBackgroundAgentTool(MagicMock()),
        ListMcpResourcesTool(MagicMock()),
        ListMcpResourceTemplatesTool(MagicMock()),
        ReadMcpResourceTool(MagicMock()),
        ReadSkillFileTool(MagicMock()),
        UpdateGoalTool(MagicMock()),
        PlanSaveTool(MagicMock()),
        PlanFinalizeTool(MagicMock()),
        CompleteTaskTool(),
    ]
    for tool in tools:
        text = tool.description + json.dumps(tool.parameters)
        assert ";" not in text, tool.name

    # A sub-agent's own description is its definition's, so only the
    # schema is ours. Only ``parameters`` is read, and it needs the store.
    wrapper = AgentToolWrapper.__new__(AgentToolWrapper)
    wrapper._bg_store = MagicMock(max_concurrent=4)
    assert ";" not in json.dumps(wrapper.parameters)


def test_shell_posix_result_without_a_group_names_a_posix_stop(monkeypatch, tmp_path):
    # ``getpgid`` can lose the race with a command that already exited, and a
    # host executor may report no group. The result must still be POSIX.
    from agentao.capabilities.shell import BackgroundHandle
    from agentao.tools.shell import ShellTool

    class _NoGroup:
        def run_background(self, request):
            return BackgroundHandle(pid=99, pgid=None, command=request.command, cwd=tmp_path)

    monkeypatch.setattr("agentao.tools.shell.IS_WINDOWS", False)
    tool = ShellTool()
    tool.shell = _NoGroup()
    out = tool._run_background("sleep 1", tmp_path, None)
    assert "To stop: kill 99" in out
    assert "taskkill" not in out
