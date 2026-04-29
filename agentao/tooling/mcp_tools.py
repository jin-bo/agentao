"""MCP tool discovery and registration.

Extracted from ``Agentao._init_mcp``. Behavior is identical:

1. Resolve server configs from ``agent._mcp_registry`` (the embedded-host
   injection point) or fall back to the file source via
   ``load_mcp_config`` for the bare-construction path.
2. Overlay in-memory ACP overrides (``agent._extra_mcp_servers``).
3. Connect all servers, register each remote tool wrapped in ``McpTool``.
4. Return the ``McpClientManager`` so the caller can disconnect on close.

Errors are logged and downgraded to a no-op — one broken server must
not abort session construction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..mcp import McpClientManager, McpTool, load_mcp_config
from ..paths import user_root

if TYPE_CHECKING:
    from ..agent import Agentao


def init_mcp(agent: "Agentao") -> Optional[McpClientManager]:
    """Initialize MCP for ``agent`` and register every discovered tool.

    Config sources merged (later overrides earlier):

      1. ``agent._mcp_registry.list_servers()`` if injected (Issue #17),
         else ``<wd>/.agentao/mcp.json`` + ``~/.agentao/mcp.json``
         via ``load_mcp_config`` for the bare-construction path.
      2. ``agent._extra_mcp_servers``         (Issue 11: ACP session-scoped)

    Returns the manager on success, ``None`` when no servers are
    configured. All failures are logged via ``agent.llm.logger``.
    """
    registry = getattr(agent, "_mcp_registry", None)
    try:
        if registry is not None:
            # Embedded host (or factory) provided an explicit registry.
            # The default ``FileBackedMCPRegistry`` reproduces the
            # pre-Protocol disk-read behavior; programmatic registries
            # skip the filesystem entirely.
            configs = registry.list_servers()
        else:
            # Bare-construction fallback: the legacy disk source. CLI
            # and ACP paths set ``_mcp_registry`` via the factory, so
            # this branch is only hit by ``Agentao(working_directory=...)``
            # without going through ``build_from_environment``.
            configs = load_mcp_config(
                project_root=agent.working_directory,
                user_root=user_root(),
            )
    except Exception as e:
        agent.llm.logger.warning(f"Failed to load MCP config: {e}")
        configs = {}

    # Overlay ACP-injected configs on top of the file-loaded set (Issue 11).
    # Same per-name semantics as ``load_mcp_config``'s global→project merge:
    # last writer wins, with a log line on collision for traceability.
    if agent._extra_mcp_servers:
        merged = dict(configs)
        for name, override in agent._extra_mcp_servers.items():
            if name in merged:
                agent.llm.logger.info(
                    "MCP: ACP session config overrides file-loaded server %r",
                    name,
                )
            merged[name] = override
        configs = merged

    if not configs:
        return None

    manager = McpClientManager(configs)
    try:
        manager.connect_all()
    except Exception as e:
        agent.llm.logger.warning(f"MCP connection error: {e}")

    register_mcp_tools(agent, manager)
    return manager


def register_mcp_tools(agent: "Agentao", manager: McpClientManager) -> None:
    """Wrap every tool exposed by ``manager`` and register it on ``agent``.

    Used by both ``init_mcp`` (for file/ACP-discovered managers) and
    ``Agentao.__init__`` (for managers the embedded host injects directly).
    The host owns connect/disconnect; this only handles tool wrapping so
    the model sees the same surface regardless of how the manager was
    created.
    """
    for server_name, mcp_tool_def in manager.get_all_tools():
        client = manager.get_client(server_name)
        trusted = client.is_trusted if client else False
        tool = McpTool(
            server_name=server_name,
            mcp_tool=mcp_tool_def,
            call_fn=manager.call_tool,
            trusted=trusted,
        )
        agent.tools.register(tool)
        agent.llm.logger.info(f"Registered MCP tool: {tool.name}")

    count = sum(1 for _ in manager.get_all_tools())
    if count:
        agent.llm.logger.info(
            f"MCP: {count} tools from {len(manager.clients)} server(s)"
        )
