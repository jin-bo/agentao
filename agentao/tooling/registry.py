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
    from ..tools.base import RegistrableTool

_logger = logging.getLogger(__name__)

# Every name a built-in tool *could* register under, independent of which
# optional deps (``[web]``) or opt-in subsystems (``bg_store``) are live.
# Used to validate ``disable_tools`` at construction so a typo
# (``{"web_serach"}``) fails loudly instead of silently no-op'ing. This is
# a flat constant by design — NOT a tool-metadata registry. The test
# ``test_builtin_tool_names_constant_in_sync`` pins it to the names
# actually produced by ``register_builtin_tools`` so it can't drift.
#
# Scope is *static registration eligibility*, not live availability:
# ``web_search`` is listed even when ``[web]`` is absent (disabling it is
# then a harmless no-op). Agent-path tools (codebase_investigator /
# cli_help) register elsewhere and are intentionally out of scope.
BUILTIN_TOOL_NAMES: frozenset = frozenset({
    "read_file",
    "write_file",
    "replace",
    "list_directory",
    "glob",
    "search_file_content",
    "run_shell_command",
    "web_fetch",
    "web_search",
    "save_memory",
    "activate_skill",
    "ask_user",
    "todo_write",
    "check_background_agent",
    "cancel_background_agent",
})


def _bind_and_register(
    agent: "Agentao", tool: "RegistrableTool", *, replace: bool = False
) -> None:
    """Bind session capabilities onto ``tool`` and register it.

    Shared by built-in and ``extra_tools`` registration so injected tools
    inherit the exact same working-directory / filesystem / shell binding
    as built-ins (ACP session cwd isolation, host FS/shell redirection) —
    they never become "bare" tools.
    """
    tool.working_directory = agent._working_directory
    tool.filesystem = agent.filesystem
    tool.shell = agent.shell
    agent.tools.register(tool, replace=replace)


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

    # ``disable_tools`` only skips built-in registration; it is not a global
    # denylist (``extra_tools`` and MCP are untouched) and not a security
    # boundary (that stays with the permission engine). Its value is a
    # smaller schema so the model doesn't attempt inapplicable built-ins.
    # ``extra_tools`` is NOT registered here — see ``register_extra_tools``,
    # which runs after MCP and agent tools so it can override them.
    disabled = agent._disable_tools
    tools_to_register = [t for t in tools_to_register if t.name not in disabled]

    for tool in tools_to_register:
        _bind_and_register(agent, tool)


def register_extra_tools(agent: "Agentao") -> None:
    """Register host-supplied ``extra_tools`` — the true final pass.

    Called from ``agent.py`` *after* built-in, MCP, and agent tools are all
    registered, so an entry whose name matches a built-in or agent tool
    overrides it (explicit replacement, silent). Extra-tool names are
    forbidden the ``mcp_`` prefix at construction (see
    ``Agentao._validate_tool_injection``), so they never collide with — and
    cannot override — MCP tools; MCP replacement goes through the existing
    ``mcp_manager=`` / ``extra_mcp_servers=`` injection points instead.
    """
    for tool in agent._extra_tools:
        # Decide override against the live registry, which at this point
        # holds built-in + MCP + agent tools. The ``mcp_`` prefix ban means
        # a collision can only be with a built-in / agent tool.
        replace = tool.name in agent.tools.tools
        if replace:
            # ``register(replace=True)`` is silent (intentional override), but
            # an accidental name clash would otherwise vanish without a trace.
            # Log at INFO so the override is auditable in agentao.log.
            _logger.info(
                "extra_tools: '%s' (%s) overrides an already-registered tool",
                tool.name,
                type(tool).__name__,
            )
        _bind_and_register(agent, tool, replace=replace)


def apply_enabled_tools(agent: "Agentao") -> None:
    """Prune the registry to the host's ``enabled_tools`` allowlist.

    A no-op when ``agent._enabled_tools is None`` (allowlist disabled). When
    set — including the empty set — removes every built-in / agent-path tool
    whose name is absent from the allowlist, leaving ``extra_tools``, MCP
    (``mcp_*``), and plan-only tools untouched. See
    ``docs/design/host-tool-allowlist.md``.

    Runs as the true final pass — after built-in, MCP, agent, and extra
    registration — so the unknown-name (typo) guard can validate against the
    live registry. Agent-path tool names aren't known at construction time
    (``_validate_tool_injection`` runs before ``AgentManager`` is built),
    which is why this check lives here rather than in that method.

    The mutual-exclusion and reserved-name checks already ran at construction
    (see :meth:`Agentao._validate_tool_injection`), so ``allow`` here can only
    hold non-reserved names.
    """
    allow = agent._enabled_tools
    if allow is None:
        return

    from ..tools.base import ToolRegistry

    # Typo guard: every allowlisted name must resolve to a registerable tool.
    # Union with BUILTIN_TOOL_NAMES so a legal-but-absent built-in (e.g.
    # ``web_search`` without the ``[web]`` extra) isn't flagged as a typo —
    # same "registration eligibility != live availability" rule as disable_tools.
    known = set(agent.tools.tools) | BUILTIN_TOOL_NAMES
    unknown = sorted(allow - known)
    if unknown:
        raise ValueError(
            f"Agentao(enabled_tools=): unknown tool name(s) {unknown}. "
            f"Names must be built-ins or registered agent / extra tools."
        )

    # ``extra_tools`` are always kept — the host injected those instances
    # explicitly, so naming them in the allowlist too would be redundant.
    extra_names = {tool.name for tool in agent._extra_tools}

    for name in list(agent.tools.tools):
        if name.startswith("mcp_"):                 # out of allowlist scope
            continue
        if name in ToolRegistry._PLAN_ONLY_TOOLS:   # plan-mode state machine
            continue
        if name in extra_names:                     # host-injected, always kept
            continue
        if name not in allow:
            agent.tools.unregister(name)
            _logger.info("enabled_tools: pruned '%s' (not in allowlist)", name)
