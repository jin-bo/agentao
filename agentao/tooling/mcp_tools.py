"""MCP tool discovery and registration.

Extracted from ``Agentao._init_mcp``. Behavior is identical:

1. Load file configs (``load_mcp_config``).
2. Overlay in-memory ACP overrides (``agent._extra_mcp_servers``).
3. Connect all servers, register each remote tool wrapped in ``McpTool``.
4. Return the ``McpClientManager`` so the caller can disconnect on close.

Errors are logged and downgraded to a no-op — one broken server must
not abort session construction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..mcp import McpClientManager, McpTool, load_mcp_config

if TYPE_CHECKING:
    from ..agent import Agentao


def init_mcp(agent: "Agentao") -> Optional[McpClientManager]:
    """Initialize MCP for ``agent`` and register every discovered tool.

    Config sources merged (later overrides earlier):

      1. ``~/.agentao/mcp.json``              (global, file)
      2. ``<cwd>/.agentao/mcp.json``          (project, file)
      3. ``agent._extra_mcp_servers``         (Issue 11: ACP session-scoped)

    Returns the manager on success, ``None`` when no servers are
    configured. All failures are logged via ``agent.llm.logger``.
    """
    try:
        configs = load_mcp_config(project_root=agent.working_directory)
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
    return manager
