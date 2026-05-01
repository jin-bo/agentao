"""Built-in tool registration.

Extracted from ``Agentao._register_tools`` so the main agent module is
not the only place new tools get wired in. Behavior is unchanged — the
function registers the same tools in the same order and performs the
same per-tool working-directory binding.
"""

from __future__ import annotations

import importlib.util
import logging
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

_logger = logging.getLogger(__name__)


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
    ]
    # Web tools depend on the `[web]` extra (beautifulsoup4). Skip
    # registration when bs4 is missing so the LLM-visible schema does
    # not advertise tools whose execute() would fail with a generic
    # ImportError. Mirrors the bg_store pattern below.
    if importlib.util.find_spec("bs4") is not None:
        tools_to_register.append(WebFetchTool())
        tools_to_register.append(WebSearchTool())
    else:
        _logger.info(
            "beautifulsoup4 not installed; web_fetch / web_search tools omitted. "
            "Run `pip install 'agentao[web]'` to enable."
        )
    tools_to_register.extend([
        agent.memory_tool,
        ActivateSkillTool(agent.skill_manager),
        AskUserTool(ask_user_callback=lambda *a, **kw: agent.transport.ask_user(*a, **kw)),
        agent.todo_tool,
    ])
    # When the background-agent store is disabled, omit the poll /
    # cancel tools so the LLM-visible schema doesn't advertise a
    # feature the runtime can't service.
    if agent.bg_store is not None:
        tools_to_register.append(CheckBackgroundAgentTool(bg_store=agent.bg_store))
        tools_to_register.append(CancelBackgroundAgentTool(bg_store=agent.bg_store))

    wd = agent._working_directory
    for tool in tools_to_register:
        tool.working_directory = wd
        tool.filesystem = agent.filesystem
        tool.shell = agent.shell
        agent.tools.register(tool)
