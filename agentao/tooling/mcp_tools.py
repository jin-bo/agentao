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

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

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
    # The manager is not returned yet, so nothing else can release it: a
    # failed registration, or an interrupt (Ctrl-C) while servers are still
    # connecting, disconnects it here, then propagates as before.
    try:
        try:
            manager.connect_all()
        except Exception as e:
            agent.llm.logger.warning(f"MCP connection error: {e}")
        register_mcp_tools(agent, manager)
    except BaseException:
        try:
            manager.disconnect_all()
        except Exception as e:
            agent.llm.logger.warning(
                f"MCP disconnect after a failed tool registration: {e}"
            )
        raise
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
    from ..mcp.client import McpClientManager as _RealManager

    # The whole-result path (embedded blobs saved, resource-link read hints)
    # needs the real manager's ``call_tool_result``; a host's duck-typed
    # manager keeps the plain ``call_tool -> str`` path it was written for.
    # ``isinstance``, not ``hasattr``: a mock answers any attribute.
    real = isinstance(manager, _RealManager)

    # Two MCP tools can map to one name (``my-srv`` and ``my_srv``, ``a.b`` and
    # ``a-b``). The registry would let the second overwrite the first, and the
    # model would then call a different tool than the one it was described.
    # The first keeps the name; a later one is refused, by name, in the log.
    # Per pass, not against the registry: a re-init (a plugin adding servers)
    # registers the same tools again over a new manager and must replace them.
    claimed: Dict[str, Tuple[str, Any]] = {}
    # One listing for both: a second ``get_all_tools()`` could see a server
    # that connected or dropped in between, and record a list it never
    # registered.
    entries = list(manager.get_all_tools())
    _register_entries(agent, manager, entries, claimed, real=real)
    # What a refresh removes, and what it compares a server's new list with.
    agent._mcp_registered = claimed
    by_server: Dict[str, List[Any]] = {}
    for server_name, tool_def in entries:
        by_server.setdefault(server_name, []).append(tool_def)
    agent._mcp_listed = by_server

    # The tools registered, not the tools listed: a collision left some out.
    count = len(claimed)
    if count:
        agent.llm.logger.info(
            f"MCP: {count} tools from {len(manager.clients)} server(s)"
        )

    if real:
        register_mcp_resource_tools(agent, manager)
        register_mcp_skills(agent, manager)


def _register_entries(
    agent: "Agentao",
    manager: "McpClientManager",
    entries: List[Tuple[str, Any]],
    claimed: Dict[str, Tuple[str, Any]],
    *,
    real: bool,
    keep_foreign: bool = False,
) -> None:
    """Wrap and register each ``(server, tool definition)``; ``claimed`` records them.

    A name already in ``claimed`` is refused, so the caller decides what the
    new tools may collide with: nothing (a full pass) or the other servers'
    tools (a refresh of one server). With ``keep_foreign`` a name the
    registry holds with an origin other than ``mcp`` (a host tool that took
    it since) is refused too.
    """
    from .registry import _bind_and_register

    def read_hint(server_name: str) -> bool:
        # §6: hint only when the model could follow it right now.
        if "read_mcp_resource" not in agent.tools.tools:
            return False
        if not manager.resources_allowed(server_name):
            return False
        client = manager.get_client(server_name)
        return client is not None and client.supports_resources

    for server_name, mcp_tool_def in entries:
        client = manager.get_client(server_name)
        trusted = client.is_trusted if client else False
        tool = McpTool(
            server_name=server_name,
            mcp_tool=mcp_tool_def,
            call_fn=manager.call_tool,
            trusted=trusted,
            result_fn=manager.call_tool_result if real else None,
            read_hint_fn=read_hint if real else None,
        )
        first = claimed.get(tool.name)
        if first is not None:
            agent.llm.logger.error(
                "MCP: tool %r from server %r maps to %s, already taken by %r "
                "from server %r; not registered. Rename one of them.",
                mcp_tool_def.name, server_name, tool.name, first[1].name, first[0],
            )
            continue
        if (
            keep_foreign
            and tool.name in agent.tools.tools
            and agent.tools.origin(tool.name) != "mcp"
        ):
            agent.llm.logger.error(
                "MCP: tool %r from server %r maps to %s, already taken by a "
                "%s tool; not registered.",
                mcp_tool_def.name, server_name, tool.name,
                agent.tools.origin(tool.name),
            )
            continue
        claimed[tool.name] = (server_name, mcp_tool_def)
        # Bound like a built-in, so an embedded blob in its result is saved
        # under this session's working directory (docs/design/mcp-resources.md §6).
        _bind_and_register(agent, tool, origin="mcp")
        agent.llm.logger.info(f"Registered MCP tool: {tool.name}")


def _listed_by_server(agent: "Agentao") -> Dict[str, List[Any]]:
    listed = getattr(agent, "_mcp_listed", None)
    if not isinstance(listed, dict):
        listed = {}
        agent._mcp_listed = listed
    return listed


def refresh_mcp_tools(agent: "Agentao") -> bool:
    """Bring the registry up to date with servers whose tool list changed.

    For ``"refresh_tools": true`` servers only (the manager marks them; see
    docs/design/mcp-tool-list-changed.md). The chat loop calls this before
    each model request and rebuilds the tool list it sends when it returns
    ``True``. Each changed server's tools are replaced as a set: a removed
    tool is unregistered, an added one registered, against the same
    collision rule as the first pass. A server whose re-list failed keeps
    its tools, and the other servers' tools are never touched (a server
    that is reconnecting keeps the tools the model knows).
    """
    manager = getattr(agent, "mcp_manager", None)
    if manager is None:
        return False
    from ..mcp.client import McpClientManager as _RealManager

    # A host's duck-typed manager has no notion of a stale list.
    if not isinstance(manager, _RealManager):
        return False
    relisted = manager.refresh_stale_tools()
    if not relisted:
        return False
    _ensure_mcp_classes()  # ``_register_entries`` builds ``McpTool``s

    registered = getattr(agent, "_mcp_registered", None)
    if not isinstance(registered, dict):
        registered = {}
        agent._mcp_registered = registered
    listed = _listed_by_server(agent)
    changed = False
    for server, tools in relisted.items():
        # Against what the server listed last, not what got registered: a
        # tool refused for a name collision is in one and not the other, and
        # would make every refresh of an unchanged list look like a change.
        if listed.get(server) == tools:
            continue
        old = {
            name: entry for name, entry in registered.items() if entry[0] == server
        }
        # The list the re-list returned, never ``get_all_tools()`` or the
        # client's current list: both are empty for a server that dropped
        # since, which would unregister every tool it has.
        new = [(server, d) for d in tools]
        changed = True
        for name in old:
            del registered[name]
            # Only what this pass registered: a host tool that took the name
            # since stays.
            if name in agent.tools.tools and agent.tools.origin(name) == "mcp":
                agent.tools.unregister(name)
        try:
            _register_entries(
                agent, manager, new, registered, real=True, keep_foreign=True,
            )
        except Exception:
            # One server's bad list must not drop the other servers' updates
            # (their flags are already cleared). ``listed`` keeps the old
            # list, so the same list is not taken for "unchanged" next time.
            agent.llm.logger.warning(
                "MCP server %r: registering the refreshed tools failed",
                server, exc_info=True,
            )
            continue
        listed[server] = list(tools)
        now = {name for name, entry in registered.items() if entry[0] == server}
        agent.llm.logger.info(
            "MCP server %r: tool list refreshed, %d tools (+%d, -%d)",
            server, len(now), len(now - set(old)), len(set(old) - now),
        )
    if _add_late_resource_tools(agent, manager, relisted):
        changed = True
    return changed


def _add_late_resource_tools(
    agent: "Agentao", manager: "McpClientManager", relisted: Dict[str, List[Any]],
) -> bool:
    """Register the resource tools when a refreshed server is the first to offer them.

    The server that needed login at startup (D13): the first pass found no
    server declaring ``resources``, so the three tools were never registered.
    Only after a refresh, only for a re-listed server that declares them and
    is allowed them. The rules of the first pass hold: ``disable_tools`` and
    the ``enabled_tools`` allowlist (which pruned them at construction), and
    a name already registered — by the first pass or by a host tool — is
    never replaced. Skills are not loaded this way: a server's skills need a
    restart (mcp-skills.md D8).
    """
    from ..mcp.resource_tools import resource_tools
    from .registry import _bind_and_register

    offered = any(
        manager.resources_allowed(name)
        and (client := manager.get_client(name)) is not None
        and client.supports_resources
        for name in relisted
    )
    if not offered:
        return False
    allow = getattr(agent, "_enabled_tools", None)
    added = False
    for tool in resource_tools(manager):
        if tool.name in agent._disable_tools or tool.name in agent.tools.tools:
            continue
        if isinstance(allow, frozenset) and tool.name not in allow:
            continue
        _bind_and_register(agent, tool, origin="mcp")
        agent.llm.logger.info(f"Registered MCP resource tool: {tool.name}")
        added = True
    return added


def register_mcp_resource_tools(agent: "Agentao", manager: "McpClientManager") -> None:
    """Register the three resource tools when a connected server declares ``resources``.

    Fixed for the process, like MCP tools. ``disable_tools`` is honoured here
    by name, since these do not pass through ``register_builtin_tools``;
    ``enabled_tools`` prunes them in its final pass like any non-``mcp_*``
    tool. ``origin="mcp"``: a sub-agent gets the parent's instances, over the
    parent's connections. See docs/design/mcp-resources.md §5.2.
    """
    from ..mcp.resource_tools import resource_tools
    from .registry import _bind_and_register

    offered = any(
        manager.resources_allowed(name) and client.supports_resources
        for name, client in list(manager.clients.items())
    )
    if not offered:
        return
    for tool in resource_tools(manager):
        if tool.name in agent._disable_tools:
            continue
        _bind_and_register(agent, tool, origin="mcp")
        agent.llm.logger.info(f"Registered MCP resource tool: {tool.name}")


def register_mcp_skills(agent: "Agentao", manager: "McpClientManager") -> None:
    """Attach the session's MCP skills and register ``read_skill_file``.

    Only for servers with ``"skills": true`` whose connection passed the gate
    (docs/design/mcp-skills.md §5.2). One :class:`McpSkills` per session: the
    skill manager carries it, and its child views — every sub-agent's —
    share it, which is what lets the §6 gates and same-origin verification
    see every load in the session. ``read_skill_file`` is registered even
    when the servers list nothing: a skill can still be loaded by URI (D2).
    """
    servers = manager.skill_servers()
    if not servers:
        return
    # A host-injected skill manager written before MCP Skills has no
    # ``attach_mcp_skills``: Skills stay off for it, rather than the
    # missing method raising out of ``Agentao.__init__``.
    if not callable(getattr(agent.skill_manager, "attach_mcp_skills", None)):
        agent.llm.logger.warning(
            "MCP: skills from %s not offered: the skill manager in use has no "
            "attach_mcp_skills", ", ".join(servers),
        )
        return
    from ..mcp.skill_tools import ReadSkillFileTool
    from ..mcp.skills import McpSkills
    from .registry import _bind_and_register

    mcp_skills = McpSkills(manager, servers)
    agent.skill_manager.attach_mcp_skills(mcp_skills)
    # The generic resource read routes a read inside a loaded skill's
    # directory to the skill's verifier (§6.1, same origin).
    reader = agent.tools.tools.get("read_mcp_resource")
    if reader is not None and hasattr(reader, "skill_session"):
        reader.skill_session = mcp_skills
    tool = ReadSkillFileTool(mcp_skills)
    if tool.name not in agent._disable_tools:
        _bind_and_register(agent, tool, origin="mcp")
    listed = sum(1 for e in mcp_skills.catalogue() if not e.unavailable)
    agent.llm.logger.info(
        f"MCP: {listed} skill(s) from {len(servers)} Skills server(s)"
    )
