"""Built-in tool registration.

Extracted from ``Agentao._register_tools`` so the main agent module is
not the only place new tools get wired in. Behavior is unchanged — the
function registers the same tools in the same order and performs the
same per-tool working-directory binding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..agents.tools import CancelBackgroundAgentTool, CheckBackgroundAgentTool
from ..tools import (
    ActivateSkillTool,
    AskUserTool,
    EditTool,
    FindFilesTool,
    ReadFileTool,
    ReadFolderTool,
    SearchTextTool,
    ShellTool,
    WebFetchTool,
    WebSearchTool,
    WriteFileTool,
)

if TYPE_CHECKING:
    from ..agent import Agentao


def register_builtin_tools(agent: "Agentao") -> None:
    """Register all built-in tools on ``agent.tools``.

    The agent instance is expected to already have ``memory_tool``,
    ``todo_tool``, ``skill_manager``, ``transport``, and ``tools``
    initialized. Working directory is bound per-tool here so each tool's
    ``_resolve_path`` resolves against the session's root (Issue 05).
    """
    tools_to_register = [
        ReadFileTool(),
        WriteFileTool(),
        EditTool(),
        ReadFolderTool(),
        FindFilesTool(),
        SearchTextTool(),
        ShellTool(),
        WebFetchTool(),
        WebSearchTool(),
        agent.memory_tool,
        ActivateSkillTool(agent.skill_manager),
        AskUserTool(ask_user_callback=lambda *a, **kw: agent.transport.ask_user(*a, **kw)),
        agent.todo_tool,
        CheckBackgroundAgentTool(bg_store=agent.bg_store),
        CancelBackgroundAgentTool(bg_store=agent.bg_store),
    ]

    wd = agent._explicit_working_directory
    for tool in tools_to_register:
        tool.working_directory = wd
        agent.tools.register(tool)
