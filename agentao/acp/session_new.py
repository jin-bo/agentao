"""ACP ``session/new`` handler (Issue 04).

Creates a new ACP session: parses ``cwd`` and ``mcpServers`` from the
request, generates a ``sessionId``, builds an :class:`~agentao.agent.Agentao`
runtime bound to an :class:`~agentao.acp.transport.ACPTransport`, and
registers the whole thing with the server's :class:`AcpSessionManager`.

Scope policy vs. related issues:

- **Issue 05** will do the actual per-session cwd refactor. Here we merely
  parse, validate, and store the cwd on :class:`AcpSessionState`; the
  runtime still inherits the process-global ``Path.cwd()``. Once Issue 05
  lands, the factory can route cwd into the Agentao constructor.
- **Issue 11** will convert the parsed ``mcpServers`` entries into actual
  Agentao MCP client configs and connect them. Here we only validate the
  shape — an obvious-invalid config must fail the request loudly rather
  than deferring errors to Issue 11.
- **Issue 07** already replaced the :class:`ACPTransport` stub with the
  real ``session/update`` adapter. Constructing it here is still cheap
  and safe because Agentao's ``__init__`` stores the transport reference;
  the event callbacks are exercised later during ``chat()``.

Agent construction is injected via an ``agent_factory`` parameter so tests
can substitute a lightweight fake, and so Issue 05 can swap in a
cwd-aware factory without touching handler logic.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from .mcp_translate import translate_acp_mcp_servers
from .models import AcpSessionState
from .protocol import METHOD_SESSION_NEW, SERVER_NOT_INITIALIZED
from .server import JsonRpcHandlerError
from .session_manager import DuplicateSessionError
from .transport import ACPTransport

if TYPE_CHECKING:
    from agentao.agent import Agentao
    from .server import AcpServer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent factory (dependency-injected so tests can replace it)
# ---------------------------------------------------------------------------

AgentFactory = Callable[..., "Agentao"]
"""Signature: ``(cwd, client_capabilities, transport, permission_engine,
mcp_servers, model) -> Agentao``.

All parameters are keyword-only at call sites to reduce the risk that a
future signature change silently rebinds positional arguments. The
``mcp_servers`` argument was added by Issue 11 and ``model`` by the
``session/load`` model-restoration fix — factories that ignore extra
kwargs (the test ``FakeAgent`` factory pattern) keep working unchanged
because they accept ``**kwargs``.
"""


def default_agent_factory(
    *,
    cwd: Path,
    client_capabilities: Dict[str, Any],
    transport: ACPTransport,
    permission_engine: Any,
    mcp_servers: Optional[Dict[str, Dict[str, Any]]] = None,
    model: Optional[str] = None,
) -> "Agentao":
    """Default factory — constructs a real :class:`Agentao` runtime bound
    to the session's working directory (Issue 05) and any session-scoped
    MCP servers (Issue 11).

    Imports :class:`Agentao` lazily so merely importing
    ``agentao.acp.session_new`` does not pull the whole LLM stack. This
    matters because ACP-only consumers (e.g. the initialize smoke test)
    should not require ``OPENAI_API_KEY`` to be set.

    ``working_directory=cwd`` freezes the runtime to the session's cwd so
    the memory database, AGENTAO.md lookup, MCP config lookup, system
    prompt rendering, and file/shell/search tools all resolve against it.
    Two concurrent ACP sessions created from different directories
    therefore see independent state.

    ``client_capabilities`` is accepted so future factories can route e.g.
    ``fs.readTextFile: true`` to choose between local file tools and
    ACP-proxied file tools.

    ``mcp_servers`` (Issue 11) is the *already-translated* Agentao
    internal MCP config dict (see
    :func:`agentao.acp.mcp_translate.translate_acp_mcp_servers`). It
    overrides any file-loaded servers of the same name. ``None`` means
    "no extras" — the runtime falls back to file-only behavior.

    ``model`` (session/load model-restoration fix) lets the loader
    re-bind a session to the model it was originally saved under. ``None``
    or empty string means "use the runtime default" — that's the
    ``session/new`` path which has no persisted model to restore.
    """
    # Local import avoids pulling openai/tools/llm into the ACP package
    # at import time — handler modules stay lightweight for testing.
    from agentao.embedding import build_from_environment

    overrides: Dict[str, Any] = {
        "permission_engine": permission_engine,
        "transport": transport,
        "extra_mcp_servers": mcp_servers,
    }
    if model:  # empty string / None → use default discovered by factory
        overrides["model"] = model

    return build_from_environment(working_directory=cwd, **overrides)


# ---------------------------------------------------------------------------
# Parameter parsing
# ---------------------------------------------------------------------------

def _parse_cwd(raw: Any) -> Path:
    """Validate and normalize the ``cwd`` field from ``session/new`` params.

    ACP spec §session/new: ``cwd`` must be an absolute file system path.
    We additionally require that the path exists and is a directory, so
    clients get a clear error at session-creation time rather than at
    first tool execution.

    Raises :class:`TypeError` so the dispatcher maps to ``-32602``.
    """
    if not isinstance(raw, str):
        raise TypeError("session/new.cwd must be a string")
    if not raw:
        raise TypeError("session/new.cwd must not be empty")
    path = Path(raw)
    if not path.is_absolute():
        raise TypeError(f"session/new.cwd must be an absolute path, got {raw!r}")
    if not path.exists():
        raise TypeError(f"session/new.cwd does not exist: {raw!r}")
    if not path.is_dir():
        raise TypeError(f"session/new.cwd is not a directory: {raw!r}")
    return path


def _parse_mcp_servers(raw: Any) -> List[Dict[str, Any]]:
    """Validate the ``mcpServers`` array from ``session/new`` params.

    Returns the list verbatim after shape checks; the actual conversion to
    Agentao's internal MCP config format and the connection attempt both
    happen in Issue 11. That split keeps Issue 04's parsing logic thin and
    gives Issue 11 a clear single point to extend.

    Per ACP spec each entry is one of:

    - **Stdio (default)**: ``{name, command, args, env?}`` where ``env`` is
      a list of ``{name, value}`` dicts.
    - **SSE**: ``{type: "sse", name, url, headers}`` where ``headers`` is
      a list of ``{name, value}`` dicts.

    ``type: "http"`` is **not** accepted: the agent advertises
    ``mcpCapabilities.http: false`` in ``initialize`` because
    :class:`agentao.mcp.client.McpClient` only supports ``sse_client`` for
    URL-based transports. Accepting http here would silently dispatch
    through ``sse_client`` and fail to connect at session-prompt time;
    rejecting at parse time surfaces the misconfiguration immediately as
    ``INVALID_PARAMS``.

    Raises :class:`TypeError` for shape violations.
    """
    if not isinstance(raw, list):
        raise TypeError("session/new.mcpServers must be a JSON array")

    parsed: List[Dict[str, Any]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise TypeError(f"session/new.mcpServers[{i}] must be a JSON object")

        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise TypeError(f"session/new.mcpServers[{i}].name must be a non-empty string")

        transport_type = entry.get("type", "stdio")
        if transport_type not in ("stdio", "sse"):
            raise TypeError(
                f"session/new.mcpServers[{i}].type must be one of "
                f"'stdio', 'sse', got {transport_type!r} "
                f"(http is not supported — see mcpCapabilities.http=false)"
            )

        if transport_type == "stdio":
            command = entry.get("command")
            if not isinstance(command, str) or not command:
                raise TypeError(
                    f"session/new.mcpServers[{i}].command must be a non-empty string"
                )
            args = entry.get("args", [])
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise TypeError(
                    f"session/new.mcpServers[{i}].args must be an array of strings"
                )
            env = entry.get("env")
            if env is not None:
                _validate_name_value_list(env, f"session/new.mcpServers[{i}].env")
        else:  # http or sse
            url = entry.get("url")
            if not isinstance(url, str) or not url:
                raise TypeError(
                    f"session/new.mcpServers[{i}].url must be a non-empty string"
                )
            headers = entry.get("headers")
            if headers is not None:
                _validate_name_value_list(
                    headers, f"session/new.mcpServers[{i}].headers"
                )

        parsed.append(entry)
    return parsed


def _validate_name_value_list(raw: Any, field_name: str) -> None:
    """Validate an ACP ``[{name, value}, ...]`` list (env vars, HTTP headers).

    Both ``env`` and ``headers`` use the same shape in the ACP spec:
    a JSON array of objects, each with a ``name`` string and a ``value``
    string.
    """
    if not isinstance(raw, list):
        raise TypeError(f"{field_name} must be a JSON array")
    for j, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TypeError(f"{field_name}[{j}] must be a JSON object")
        if not isinstance(item.get("name"), str):
            raise TypeError(f"{field_name}[{j}].name must be a string")
        if not isinstance(item.get("value"), str):
            raise TypeError(f"{field_name}[{j}].value must be a string")


# ---------------------------------------------------------------------------
# Session ID generation
# ---------------------------------------------------------------------------

def _generate_session_id() -> str:
    """Produce an opaque session id.

    ACP does not mandate a format. ``sess_`` + 32-hex-char uuid4 gives us
    practical uniqueness (2^122 space) and a recognizable prefix that is
    convenient for humans reading logs.
    """
    return f"sess_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle_session_new(
    server: "AcpServer",
    params: Any,
    *,
    agent_factory: AgentFactory = default_agent_factory,
) -> Dict[str, Any]:
    """Handle ``session/new``.

    Flow:
      1. Guard: ``initialize`` must have been called first.
      2. Parse and validate ``cwd`` and ``mcpServers`` from params.
      3. Generate a unique session id.
      4. Build the :class:`ACPTransport`, :class:`PermissionEngine`, and
         :class:`Agentao` runtime via the injected factory.
      5. Assemble the :class:`AcpSessionState` and register it with the
         manager. On any failure after the runtime is built, call
         ``agent.close()`` so MCP subprocesses do not leak.
      6. Return ``{"sessionId": <id>}``.
    """
    if not server.state.initialized:
        raise JsonRpcHandlerError(
            code=SERVER_NOT_INITIALIZED,
            message="session/new called before initialize handshake",
        )

    if not isinstance(params, dict):
        raise TypeError("session/new params must be a JSON object")

    cwd = _parse_cwd(params.get("cwd"))
    mcp_servers = _parse_mcp_servers(params.get("mcpServers"))

    session_id = _generate_session_id()

    # Snapshot the connection-level client capabilities onto the session so
    # a later re-initialize cannot retroactively change semantics of an
    # already-running session. ``dict()`` shallow-copies is sufficient —
    # the values are JSON scalars or one-level-deep dicts.
    client_capabilities_snapshot = dict(server.state.client_capabilities)

    transport = ACPTransport(server=server, session_id=session_id)

    # PermissionEngine reads project-level rules from ``<cwd>/.agentao/
    # permissions.json``. Passing ``project_root=cwd`` (Issue 05) isolates
    # the engine from the process cwd so two sessions running in different
    # directories see independent rules.
    from agentao.paths import user_root
    from agentao.permissions import PermissionEngine
    permission_engine = PermissionEngine(
        project_root=cwd,
        user_root=user_root(),
    )

    # Translate ACP-shape MCP server entries to Agentao internal config
    # (Issue 11). The translator is total — it logs and drops malformed
    # entries rather than raising, so a single bad server can't crash
    # session/new.
    mcp_servers_internal = translate_acp_mcp_servers(mcp_servers)

    agent = None
    try:
        agent = agent_factory(
            cwd=cwd,
            client_capabilities=client_capabilities_snapshot,
            transport=transport,
            permission_engine=permission_engine,
            mcp_servers=mcp_servers_internal,
        )

        # Begin a replay instance for this ACP session when recording is
        # enabled in ``<cwd>/.agentao/settings.json``. Creating the
        # recorder at session birth (rather than first prompt) means the
        # on-disk file matches the logical session lifecycle.
        try:
            start_replay = getattr(agent, "start_replay", None)
            if callable(start_replay):
                start_replay(session_id)
        except Exception:
            logger.exception("acp: session/new replay start failed")

        state = AcpSessionState(
            session_id=session_id,
            agent=agent,
            cwd=cwd,
            client_capabilities=client_capabilities_snapshot,
            cancel_token=None,  # populated per-turn by Issue 06
        )

        try:
            server.sessions.create(state)
        except DuplicateSessionError:
            # Essentially impossible with 122-bit uuids, but if it ever
            # fires we must surface it as INTERNAL_ERROR and clean up.
            raise JsonRpcHandlerError(
                code=-32603,
                message=f"session id collision: {session_id}",
            )
    except Exception:
        # Clean up any partially-built runtime. ``agent.close()`` is
        # idempotent and wraps its own failures.
        if agent is not None:
            try:
                agent.close()
            except Exception:
                logger.exception("acp: error closing partially-built agent during session/new failure")
        raise

    if mcp_servers_internal:
        logger.info(
            "acp: session %s registered with %d ACP-provided MCP server(s)",
            session_id,
            len(mcp_servers_internal),
        )

    return {"sessionId": session_id}


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def register(
    server: "AcpServer",
    *,
    agent_factory: AgentFactory = default_agent_factory,
) -> None:
    """Register the ``session/new`` handler on an :class:`AcpServer`.

    ``agent_factory`` is exposed as a keyword argument so tests can inject
    a lightweight fake without monkey-patching module globals.
    """
    server.register(
        METHOD_SESSION_NEW,
        lambda params: handle_session_new(server, params, agent_factory=agent_factory),
    )
