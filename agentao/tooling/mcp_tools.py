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

# ``McpClientManager`` / ``McpTool`` pull in the heavy ``mcp`` SDK
# (httpx + click + rich + pydantic_settings + starlette). They are
# resolved lazily at function-call time via ``_ensure_mcp_classes()``
# (writes into module globals so ``init_mcp`` can use the bare names),
# AND surfaced as module attributes via PEP 562 ``__getattr__`` so
# tests that ``patch("agentao.tooling.mcp_tools.McpClientManager")``
# still work without forcing an import-time load. ``load_mcp_config``
# is config-only and stays eager (no SDK dependency).
from ..mcp import load_mcp_config
from ..paths import user_root

if TYPE_CHECKING:
    from ..agent import Agentao
    from ..mcp import McpClientManager, McpTool  # noqa: F401


def _ensure_mcp_classes() -> None:
    """Bind ``McpClientManager`` / ``McpTool`` into this module's globals.

    First call loads the mcp SDK; subsequent calls are a dict lookup.
    Idempotent and safe under unittest patches — once a name is in
    ``globals()`` (either by us or by ``mock.patch``), we leave it.
    """
    g = globals()
    if "McpClientManager" not in g:
        from ..mcp import McpClientManager as _MCM

        g["McpClientManager"] = _MCM
    if "McpTool" not in g:
        from ..mcp import McpTool as _MT

        g["McpTool"] = _MT


def __getattr__(name: str):
    if name in ("McpClientManager", "McpTool"):
        _ensure_mcp_classes()
        return globals()[name]
    raise AttributeError(f"module 'agentao.tooling.mcp_tools' has no attribute {name!r}")


def init_mcp(agent: "Agentao") -> Optional["McpClientManager"]:
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

    _ensure_mcp_classes()

    manager = McpClientManager(configs)
    try:
        manager.connect_all()
    except Exception as e:
        agent.llm.logger.warning(f"MCP connection error: {e}")

    register_mcp_tools(agent, manager)
    return manager


def register_mcp_tools(agent: "Agentao", manager: "McpClientManager") -> None:
    """Wrap every tool exposed by ``manager`` and register it on ``agent``.

    Used by both ``init_mcp`` (for file/ACP-discovered managers) and
    ``Agentao.__init__`` (for managers the embedded host injects directly).
    The host owns connect/disconnect; this only handles tool wrapping so
    the model sees the same surface regardless of how the manager was
    created.
    """
    _ensure_mcp_classes()

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
