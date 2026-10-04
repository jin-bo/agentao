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
