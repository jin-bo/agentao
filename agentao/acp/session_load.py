"""ACP ``session/load`` handler (Issue 10).

Loads a previously persisted Agentao session by id, builds a fresh
:class:`~agentao.agent.Agentao` runtime bound to it, replays the
historical messages as ACP ``session/update`` notifications, and
registers the session so subsequent ``session/prompt`` calls continue
the conversation from where it left off.

Architectural choices
---------------------

- **Reuses :func:`agentao.embedding.sessions.load_session`**, which
  already handles UUID prefix lookup, timestamp prefix fallback,
  latest-wins ordering, and error reporting via
  :class:`FileNotFoundError`. This is the same code path the CLI
  ``/load`` command uses (the legacy ``agentao.session`` shim
  delegates here).

- **Replays through :meth:`ACPTransport.replay_history`** so the
  mapping from persisted OpenAI-format messages to ACP update events
  lives next to the live ``emit()`` mapping in
  :mod:`agentao.acp.transport`. Tests for the mapping are in
  ``tests/test_acp_transport.py`` and ``tests/test_acp_session_load.py``.

- **Returns success only after replay completes.** Since this handler
  runs synchronously inside the dispatcher worker, the ``return`` of
  ``handle_session_load`` is necessarily after every notification has
  been written through the shared write lock. ACP clients that wait
  for the response before sending the next ``session/prompt`` will
  therefore observe the full replayed history before any new turn.

- **Hard error for missing session.** ``FileNotFoundError`` from the
  load layer becomes :class:`JsonRpcHandlerError(INVALID_REQUEST)` so
  clients can distinguish "wrong id" from "I broke my server". The
  bare-string ``session/load`` not-found case (no sessions directory
  at all) gets the same treatment so the error surface is uniform.

- **Reuses Issue 04's ``agent_factory`` injection point.** Tests
  inject a lightweight ``FakeAgent`` to avoid pulling in the LLM
  stack, exactly the same pattern as ``session_new``.

Out of scope for v1
-------------------

- **Loading a session that already has the same id registered.** ACP
  spec says ``session/load`` is for sessions the client knows about
  but the server may have torn down (e.g. after a process restart).
  We treat a duplicate id as :class:`INTERNAL_ERROR` rather than
  silently replacing the running session — the client should issue
  ``session/cancel`` first if they want to overwrite a live session.

- **Restoring tool execution state, sub-agents, plan mode, active
  skills, or the model.** Only the message history carries over. The
  persisted model name is NOT re-bound (provider is never on disk, so
  re-binding the name onto the current provider can be inconsistent);
  the runtime keeps its process-default model. Skill activation and
  plan-mode flags are likewise reset because they depend on runtime
  SKILL.md / project state that may have changed since the session was
  persisted.

- **Streaming chunked replay.** Messages are emitted one notification
  per persisted entry; large historical messages are NOT split into
  multiple chunks. The mapping table in :meth:`replay_history` is
  documented as a 1:1 mapping for v1.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from agentao.embedding.sessions import load_session, load_session_record

from .mcp_translate import translate_acp_mcp_servers
from .models import AcpSessionState, ResumeDirective
from .protocol import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_SESSION_LOAD,
    SERVER_NOT_INITIALIZED,
)
from .server import JsonRpcHandlerError
from .session_manager import DuplicateSessionError
from .session_new import (
    AgentFactory,
    _parse_cwd,
    _parse_mcp_servers,
    default_agent_factory,
)
from .session_set_config_option import config_options_for_session
from .transport import ACPTransport

if TYPE_CHECKING:
    from .server import AcpServer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------

def _parse_session_id(raw: Any) -> str:
    """Validate the ``sessionId`` field for ``session/load``.

    Raises :class:`TypeError` so the dispatcher maps to ``-32602``.
    """
    if not isinstance(raw, str) or not raw:
        raise TypeError("session/load.sessionId must be a non-empty string")
    return raw


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle_session_load(
    server: "AcpServer",
    params: Any,
    *,
    agent_factory: AgentFactory = default_agent_factory,
) -> Dict[str, Any]:
    """Load a persisted Agentao session and replay its history.

    Flow:

      1. Guard: ``initialize`` must have been called first.
      2. Parse and validate ``sessionId``, ``cwd``, and ``mcpServers``.
      3. Refuse if the session id is already live in the registry —
         this is a protocol bug worth surfacing rather than racing.
      4. Look up the session on disk via :func:`load_session`. Missing
         id → ``INVALID_REQUEST``. The persisted ``model`` is loaded for
         metadata only — it is NOT re-bound onto the runtime (see
         :func:`_instantiate_loaded_session`); the loaded session keeps
         the process-default model.
      5. Build the :class:`ACPTransport` + :class:`Agentao` runtime via
         the injected factory (same path as ``session/new``).
      6. Hydrate the runtime's ``messages`` list from the loaded
         history *before* replay so a follow-up ``session/prompt``
         continues the same conversation.
      7. Replay the history through
         :meth:`ACPTransport.replay_history` so the client can
         reconstruct the conversation view.
      8. Register the session in the manager **after** replay
         finishes. Registering before replay would let a pipelined
         ``session/prompt`` start a live turn against this session
         while historical ``session/update`` notifications are still
         being emitted, interleaving replayed and live updates on the
         same ``sessionId``. Deferring registration keeps the session
         invisible to other handlers until the historical updates
         have all been written.
      9. Return ``{}`` (ACP ``session/load`` response is currently
         empty per spec; we use a literal empty dict so future fields
         can be added without changing the contract).
    """
    if not server.state.initialized:
        raise JsonRpcHandlerError(
            code=SERVER_NOT_INITIALIZED,
            message="session/load called before initialize handshake",
        )

    if not isinstance(params, dict):
        raise TypeError("session/load params must be a JSON object")

    session_id = _parse_session_id(params.get("sessionId"))
    cwd = _parse_cwd(params.get("cwd"))
    mcp_servers = _parse_mcp_servers(params.get("mcpServers"))

    # Refuse to overwrite a live session — that would race against
    # any in-flight prompt and silently swap out a runtime under it.
    if server.sessions.get(session_id) is not None:
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=(
                f"session/load: sessionId {session_id!r} is already active; "
                f"cancel and tear it down before reloading"
            ),
        )

    # 4) Pull the persisted history off disk.
    try:
        messages, _model, active_skills = load_session(
            session_id=session_id, project_root=cwd
        )
    except FileNotFoundError as e:
        raise JsonRpcHandlerError(
            code=INVALID_REQUEST,
            message=f"session/load: {e}",
        )
    logger.info(
        "acp: session/load found %d messages for %s in %s",
        len(messages),
        session_id,
        cwd,
    )

    # 5–8) Build runtime + transport, hydrate, replay, and register via the
    #      shared loader so ``session/load`` and startup-resume stay in
    #      lockstep. ``origin="session/load"`` only tunes log messages.
    state = _instantiate_loaded_session(
        server,
        session_id=session_id,
        cwd=cwd,
        mcp_servers=mcp_servers,
        messages=messages,
        agent_factory=agent_factory,
        origin="session/load",
    )

    # 9) ACP spec returns an (otherwise) empty result for session/load. We
    #    advertise the model config option here too — same as session/new —
    #    so a reloaded session exposes model/provider switching without a
    #    follow-up round trip.
    return {"configOptions": config_options_for_session(server, state)}


# ---------------------------------------------------------------------------
# Shared loader core (used by session/load and startup-resume)
# ---------------------------------------------------------------------------

def _instantiate_loaded_session(
    server: "AcpServer",
    *,
    session_id: str,
    cwd: Path,
    mcp_servers: List[Dict[str, Any]],
    messages: List[Dict[str, Any]],
    agent_factory: AgentFactory,
    origin: str,
) -> AcpSessionState:
    """Build, replay, and register a session from persisted history.

    Shared by :func:`handle_session_load` and :func:`resume_session_on_new`
    so both paths construct the runtime, hydrate ``agent.messages``, replay
    the conversation as ``session/update`` notifications, and register the
    session through one code path. Registration happens **after** replay so
    a pipelined ``session/prompt`` cannot interleave a live turn with the
    historical updates.

    The persisted ``model`` is intentionally **not** restored: a session
    stores only the model *name*, never its provider (api_key / base_url
    never touch disk). Re-binding the name onto whatever provider this
    process now uses can yield an inconsistent (provider, model) pair that
    only fails on the next LLM call. The runtime is built with the
    process-default model (``model=None``) so (provider, model) stays
    consistent; the saved name remains available in ``session/list`` for
    reference.

    ``origin`` is a label used only in log lines (e.g. ``"session/load"`` vs
    ``"resume"``). On any failure the partially-built runtime is closed so
    MCP subprocesses do not leak, then the error re-raises.
    """
    client_capabilities_snapshot = dict(server.state.client_capabilities)
    transport = ACPTransport(server=server, session_id=session_id)

    from agentao.embedding.permission_loader import load_permission_rules
    from agentao.paths import user_root
    from agentao.permissions import PermissionEngine
    ur = user_root()
    rules, loaded_sources = load_permission_rules(
        project_root=cwd, user_root=ur,
    )
    permission_engine = PermissionEngine(
        project_root=cwd,
        user_root=ur,
        rules=rules,
        loaded_sources=loaded_sources,
    )

    # Translate any ACP-provided MCP server entries (Issue 11). Same
    # path as session/new — translation is total and never raises.
    mcp_servers_internal = translate_acp_mcp_servers(mcp_servers)

    agent = None
    try:
        agent = agent_factory(
            cwd=cwd,
            client_capabilities=client_capabilities_snapshot,
            transport=transport,
            permission_engine=permission_engine,
            mcp_servers=mcp_servers_internal,
            model=None,  # use process-default model — persisted model not restored
        )

        # Bind the persisted ACP session id onto the agent so subsequent
        # harness lifecycle events carry the same id the host loaded
        # against. Done before history hydration so any event the
        # transport eventually re-emits is correlated correctly.
        try:
            agent._session_id = session_id
        except Exception:
            logger.exception(
                "acp: %s could not bind session id %s to agent",
                origin,
                session_id,
            )

        # Hydrate runtime BEFORE replay so subsequent prompts see the
        # full historical context. We assign through the public
        # ``messages`` attribute (set by ``Agentao.__init__``) — that
        # is the same field ``chat()`` reads from.
        try:
            agent.messages = list(messages)
            # History replaced wholesale; drop the Tier-1 token anchor so the
            # first post-load threshold check does not reuse a stale prefix
            # count from a prior conversation served by this instance.
            agent.context_manager.invalidate_token_anchor()
        except Exception:
            logger.exception(
                "acp: %s could not hydrate agent.messages for %s",
                origin,
                session_id,
            )
            # Continue — the client still gets the replay, and a new
            # prompt would just start a fresh conversation.

        # Replay history BEFORE registering the session so a pipelined
        # ``session/prompt`` cannot start a live turn that interleaves
        # with the historical update notifications. ``replay_history``
        # is best-effort and never raises, so a single corrupt message
        # can't destroy the load.
        try:
            emitted = transport.replay_history(messages)
        except Exception:
            # Defensive — replay_history should already trap everything.
            logger.exception(
                "acp: %s replay raised unexpectedly for %s", origin, session_id
            )
            emitted = 0
        logger.info(
            "acp: %s replayed %d update notification(s) for %s",
            origin,
            emitted,
            session_id,
        )

        # Begin a fresh replay instance. The spec requires a new instance
        # file rather than appending to the old one, even when the logical
        # ``session_id`` is reused.
        try:
            start_replay = getattr(agent, "start_replay", None)
            if callable(start_replay):
                start_replay(session_id)
        except Exception:
            logger.exception("acp: %s replay start failed", origin)

        # Now register: replay is done, no live turn can race against the
        # historical updates. Any race with a concurrent load/resume for
        # the same id is caught here.
        state = AcpSessionState(
            session_id=session_id,
            agent=agent,
            cwd=cwd,
            client_capabilities=client_capabilities_snapshot,
            cancel_token=None,
        )
        try:
            server.sessions.create(state)
        except DuplicateSessionError:
            raise JsonRpcHandlerError(
                code=INVALID_REQUEST,
                message=f"{origin}: sessionId {session_id!r} already active",
            )
    except Exception:
        # Single cleanup path for both JsonRpcHandlerError and unexpected
        # failures: close the partially-built runtime so MCP subprocesses
        # do not leak. ``agent.close()`` is idempotent and traps its own
        # errors, but we still wrap defensively.
        if agent is not None:
            try:
                agent.close()
            except Exception:
                logger.exception(
                    "acp: error closing agent during %s failure", origin
                )
        raise

    if mcp_servers_internal:
        logger.info(
            "acp: %s %s registered with %d ACP-provided MCP server(s)",
            origin,
            session_id,
            len(mcp_servers_internal),
        )

    return state


# ---------------------------------------------------------------------------
# Startup resume (consumed by the first session/new)
# ---------------------------------------------------------------------------

def resume_session_on_new(
    server: "AcpServer",
    *,
    cwd: Path,
    mcp_servers: List[Dict[str, Any]],
    directive: ResumeDirective,
    agent_factory: AgentFactory,
) -> Optional[Dict[str, Any]]:
    """Resume a persisted session in place of a fresh ``session/new``.

    Called by :func:`handle_session_new` when the server was launched with
    ``--resume`` and the directive has been atomically claimed. Resolves the
    directive's selector (``None`` → latest) against ``cwd``'s session store,
    loads the history, and reuses the shared loader so the reloaded session
    is indistinguishable from one created by ``session/load``.

    Returns ``{"sessionId", "configOptions", "modes"?}`` on success — the
    same shape ``session/new`` returns (``modes`` advertised when the session
    has a permission engine), so the client transparently continues the
    restored conversation. Returns ``None`` when there is nothing to resume,
    so the caller falls back to a normal fresh session rather than failing
    the request. ``None`` is returned for any recoverable problem: no
    sessions on disk, the requested id is missing, the persisted file is
    unreadable/corrupt, or the resolved session is already live in the
    registry (a prior ``session/load`` of the same id on this connection).
    """
    selector = directive.session_id
    try:
        session_id, messages, _model, _active_skills = load_session_record(
            session_id=selector, project_root=cwd
        )
    except (OSError, ValueError) as e:
        # FileNotFoundError (no store / unknown id) is an OSError; a corrupt
        # session file surfaces as json.JSONDecodeError ⊂ ValueError. Either
        # way, degrade to a fresh session instead of failing session/new.
        logger.warning(
            "acp: --resume requested (%s) but no session could be loaded "
            "from %s (%s); starting a fresh session instead",
            selector or "latest",
            cwd,
            e,
        )
        return None

    # If the persisted session is already registered (e.g. the client
    # explicitly session/load-ed it earlier on this connection), resuming it
    # would collide in the registry. Fall back to a fresh session rather than
    # raising INVALID_REQUEST out of the client's first session/new.
    if server.sessions.get(session_id) is not None:
        logger.warning(
            "acp: --resume target %s is already active; starting a fresh "
            "session instead",
            session_id,
        )
        return None

    logger.info(
        "acp: resuming session %s (%d messages) from %s on first session/new",
        session_id,
        len(messages),
        cwd,
    )

    state = _instantiate_loaded_session(
        server,
        session_id=session_id,
        cwd=cwd,
        mcp_servers=mcp_servers,
        messages=messages,
        agent_factory=agent_factory,
        origin="resume",
    )

    # Advertise the permission presets as ACP session modes, exactly as a
    # fresh session/new does — the resumed session owns an identical engine,
    # so a --resume client must get the same mode selector. Lazy import to
    # avoid a module-load cycle (session_new imports this module for resume).
    from .session_new import _session_modes

    response: Dict[str, Any] = {
        "sessionId": session_id,
        "configOptions": config_options_for_session(server, state),
    }
    modes = _session_modes(state)
    if modes is not None:
        response["modes"] = modes
    return response


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def register(
    server: "AcpServer",
    *,
    agent_factory: AgentFactory = default_agent_factory,
) -> None:
    """Register the ``session/load`` handler on an :class:`AcpServer`.

    ``agent_factory`` mirrors :func:`session_new.register` so tests can
    inject a lightweight fake without monkey-patching module globals.
    """
    server.register(
        METHOD_SESSION_LOAD,
        lambda params: handle_session_load(
            server, params, agent_factory=agent_factory
        ),
    )
