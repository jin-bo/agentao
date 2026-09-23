"""Shared content-block / JSON-safety helpers for the ACP transport.

Used by the live event mapping (:mod:`agentao.acp.transport`), the history
replay path (:mod:`agentao.acp._transport_replay`), and the request/response
interactions (:mod:`agentao.acp._transport_interaction`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from .protocol import METHOD_SESSION_UPDATE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool name → ACP tool-call kind
# ---------------------------------------------------------------------------

#: Every name in :data:`agentao.tooling.registry.BUILTIN_TOOL_NAMES`, plus the
#: tools registered outside it (agent tools, plan tools, the CLI's injected
#: ``update_goal``). Exhaustive **by test**: ``tests/test_acp_tool_kind.py``
#: fails if a built-in has no entry here, because the previous version of this
#: table was written from a mental model of the tool set rather than from the
#: registry — it mapped ``edit_file``, ``edit``, ``read_folder``, ``find_files``
#: and ``search_text``, none of which agentao has ever registered, while the
#: real edit tool (``replace``) and the real search tool
#: (``search_file_content``) fell through to ``"other"``. Naming a kind
#: explicitly, even when the kind is ``"other"``, is what makes adding a tool a
#: decision instead of a default.
_TOOL_KIND_MAP: Dict[str, str] = {
    # reading files and directories
    "read_file": "read",
    "list_directory": "read",
    # modifying files
    "write_file": "edit",
    "replace": "edit",
    # finding things
    "glob": "search",
    "search_file_content": "search",
    "web_search": "search",
    # running commands
    "run_shell_command": "execute",
    # retrieving external data
    "web_fetch": "fetch",
    # reasoning and planning
    "plan_save": "think",
    "plan_finalize": "think",
    # session state, delegation and everything without a v1 kind that fits
    "activate_skill": "other",
    "save_memory": "other",
    "ask_user": "other",
    "todo_write": "other",
    "update_goal": "other",
    "check_background_agent": "other",
    "cancel_background_agent": "other",
    "codebase_investigator": "other",
    "cli_help": "other",
}

#: Conventional spellings a *host* may register its own tool under. Kept apart
#: from the agentao table above so the exhaustiveness test can hold that one to
#: the registry without this one polluting it. A host tool agentao has no entry
#: for still resolves to ``"other"``, which is what ACP v1 makes the default
#: (``ToolKind::Other`` carries ``#[serde(other)]``).
_HOST_TOOL_KIND_ALIASES: Dict[str, str] = {
    "bash": "execute",
    "shell": "execute",
    "edit": "edit",
    "edit_file": "edit",
    "grep": "search",
    "read_folder": "read",
}


def _tool_kind(tool_name: str) -> str:
    """Map an Agentao tool name to an ACP ``tool_call.kind`` enum value.

    Unknown tools (including all ``mcp_*`` tools) fall back to ``"other"``.
    """
    kind = _TOOL_KIND_MAP.get(tool_name)
    if kind is not None:
        return kind
    return _HOST_TOOL_KIND_ALIASES.get(tool_name, "other")


# ---------------------------------------------------------------------------
# todo_write → ACP ``plan`` update
# ---------------------------------------------------------------------------

_PLAN_ENTRY_STATUS = frozenset({"pending", "in_progress", "completed"})


def _todo_write_plan(raw_args: Any) -> Dict[str, Any] | None:
    """Map ``todo_write`` tool args to an ACP ``plan`` update, or ``None``.

    The ``todos`` list comes from the LLM and may be malformed, so it is
    validated rather than trusted: every entry must be a dict with a string
    ``content`` and a ``status`` in the ACP ``PlanEntryStatus`` set. agentao
    todos have no priority while ACP requires one, so every entry is emitted
    as ``"medium"``.

    Validation is **all-or-nothing**: an ACP ``plan`` replaces the *entire*
    checklist on each update, so silently dropping a malformed entry would
    make a real task vanish from the client's view. If the list is empty or
    *any* entry is malformed, return ``None`` so the caller falls back to the
    normal ``tool_call`` mapping (which carries the full raw args) instead of
    emitting a truncated or empty plan.
    """
    todos = raw_args.get("todos") if isinstance(raw_args, dict) else None
    if not isinstance(todos, list) or not todos:
        return None
    entries = []
    for t in todos:
        if not (
            isinstance(t, dict)
            and isinstance(t.get("content"), str)
            and t.get("status") in _PLAN_ENTRY_STATUS
        ):
            return None  # any malformed entry → fall back, never truncate
        entries.append(
            {"content": t["content"], "priority": "medium", "status": t["status"]}
        )
    return {"sessionUpdate": "plan", "entries": entries}


# ---------------------------------------------------------------------------
# JSON safety coercion
# ---------------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    """Recursively coerce a value into a JSON-serializable form.

    Handles the common offenders we expect to see in tool ``args``:
    :class:`pathlib.Path`, sets, tuples, and arbitrary objects. Anything
    already JSON-native passes through unchanged.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    return str(value)


# ---------------------------------------------------------------------------
# Content block helpers
# ---------------------------------------------------------------------------

def _text_block(text: str) -> Dict[str, Any]:
    """Build an ACP ``ContentBlock`` wrapping plain text."""
    return {"type": "text", "text": text}


#: Tool names :func:`_tool_call_diff` can answer for. Callers gate on this
#: before looking up the session cwd, so the common read/search/execute call
#: costs nothing extra.
DIFF_TOOLS = frozenset({"replace", "write_file"})


def _tool_call_diff(
    tool_name: str, args: Any, *, base: Path | None = None
) -> Dict[str, Any] | None:
    """Build an ACP ``diff`` content entry from a file-editing call's arguments.

    Returns ``None`` when the arguments do not determine a truthful hunk.

    ACP v1's ``Diff`` is ``{path, oldText?, newText}`` where ``oldText`` is
    "the original content (null for new files)"
    (``agentclientprotocol/agent-client-protocol@bf6d1ec``,
    ``agent-client-protocol-schema/src/v1/tool_call.rs:674``). It is a **hunk,
    not a whole file**: ACP's own reference adapter emits one entry per hunk of
    a structured patch (``agentclientprotocol/claude-agent-acp@d571358``,
    ``src/diff.ts``), so ``replace``'s ``old_text``/``new_text`` pair is exactly
    the shape the field wants.

    The entry is emitted at ``status: "pending"`` — with the ``tool_call`` that
    opens the call, and with the ``session/request_permission`` that asks the
    user to approve it, which is the moment the diff exists for. It describes
    what was **requested**; the terminal ``tool_call_update`` says whether it
    applied. (``replace`` matches whitespace- and typography-flexibly and
    ``replace_all`` can hit several sites, so the text that actually moved may
    differ from the text shown; ``rawInput`` carries the flag.)

    **A non-append ``write_file`` deliberately gets no diff.** Its arguments
    give ``newText`` but say nothing about whether the file already exists, and
    the transport cannot find out — it holds no filesystem, and a host may have
    injected one that does not answer to local paths. Emitting ``oldText: null``
    would render an overwrite as a creation, showing all-green at exactly the
    moment the user is being asked to approve destroying the previous contents.
    The reference adapter emits that optimistically and then *corrects* it from
    the tool's own structured patch; with nothing to correct it from, saying
    nothing is the fail-closed choice. Closing it means a tool-side pre-write
    snapshot — see ``docs/design/acp-server-conformance-review.md`` G2.
    """
    if not isinstance(args, dict):
        return None

    raw_path = args.get("file_path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = _absolute_path(raw_path, base)

    if tool_name == "replace":
        old_text = args.get("old_text")
        new_text = args.get("new_text")
        # ``EditTool`` refuses an empty ``old_text`` outright, so an empty one
        # here is not "create the file" — it is a call that will not run.
        if not isinstance(old_text, str) or not old_text:
            return None
        if not isinstance(new_text, str):
            return None
        return {
            "type": "diff",
            "path": path,
            "oldText": old_text,
            "newText": new_text,
        }

    if tool_name == "write_file" and args.get("append") is True:
        content = args.get("content")
        if not isinstance(content, str) or not content:
            return None
        # A pure addition: nothing is replaced, which is the same shape the
        # reference adapter gives a hunk with no old side.
        return {
            "type": "diff",
            "path": path,
            "oldText": None,
            "newText": content,
        }

    return None


def _absolute_path(raw: str, base: Path | None) -> str:
    """Resolve a tool argument's path for ACP's absolute-``path`` requirement.

    Relative paths are joined onto the session's ``cwd``. With no base — or
    with one that cannot be joined — the argument is passed through as the
    model wrote it, which is still more useful to a client than dropping the
    diff entirely.
    """
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            return str(candidate)
        if base is not None:
            return str(Path(base) / candidate)
    except (TypeError, ValueError):
        pass
    return raw


def proposed_tool_diff(
    server: Any, session_id: str, tool_name: str, args: Any
) -> Dict[str, Any] | None:
    """:func:`_tool_call_diff` for a live session, or ``None``.

    Shared by the two places a proposed edit is shown: the ``tool_call`` that
    opens the call (:meth:`ACPTransport._build_update`) and the
    ``session/request_permission`` that asks the user to approve it
    (:meth:`_InteractionMixin.confirm_tool`). The session lookup is gated on
    :data:`DIFF_TOOLS` so a read or a shell call does not pay for it.
    """
    if tool_name not in DIFF_TOOLS:
        return None
    return _tool_call_diff(tool_name, args, base=_session_cwd(server, session_id))


def _session_cwd(server: Any, session_id: str) -> Path | None:
    """The session's working directory, or ``None`` if it cannot be had.

    Every failure is a ``None``: a diff whose ``path`` is the argument as the
    model wrote it is worth more to a client than no diff at all, and this runs
    inside ``emit``, which must not raise.
    """
    if server is None:
        return None
    try:
        session = server.sessions.require(session_id)
    except Exception:
        return None
    cwd = getattr(session, "cwd", None)
    return cwd if isinstance(cwd, Path) else None


def _tool_content_text(text: str) -> Dict[str, Any]:
    """Build a ``ToolCallContent`` entry that wraps plain text.

    Per ACP spec, ``tool_call.content`` is an array of
    ``{type: "content", content: ContentBlock}`` entries (plus diff and
    terminal variants we do not use in v1).
    """
    return {"type": "content", "content": _text_block(text)}


# ---------------------------------------------------------------------------
# session/update envelope
# ---------------------------------------------------------------------------

def write_session_update(server: Any, session_id: str, update: Dict[str, Any]) -> None:
    """Write one ``session/update`` notification with the standard envelope.

    Centralizes the ``{"sessionId", "update"}`` envelope and the
    ``METHOD_SESSION_UPDATE`` method constant shared by the live event path
    (:meth:`ACPTransport.emit`), the history-replay path
    (:meth:`_ReplayMixin._emit_update`), and the set_mode handler
    (:func:`agentao.acp.session_set_mode._emit_current_mode_update`) — so a
    future envelope-shape change is a one-line edit instead of three.

    Deliberately does **not** catch exceptions: each caller owns its own
    error policy (the live and replay paths log-and-swallow with
    site-specific messages; set_mode wraps best-effort). A bare write that
    raises propagates to that caller's handler.
    """
    server.write_notification(
        METHOD_SESSION_UPDATE,
        {"sessionId": session_id, "update": update},
    )


def hook_notice_update(notices: Any) -> Dict[str, Any] | None:
    """The ``session/update`` that carries hook user-notices, or ``None``.

    One definition of the wire shape for both notice paths — the lifecycle
    dispatches, which hand their notices straight to
    :func:`write_user_notice`, and every other hook event, whose notices arrive
    on ``PLUGIN_HOOK_FIRED`` and are mapped in
    :meth:`AcpTransport._build_update`. A client has to be able to recognise a
    notice by its shape, so the two must not drift apart.
    """
    if isinstance(notices, str):
        notices = [notices]
    if not isinstance(notices, list):
        return None
    texts = [n for n in notices if isinstance(n, str) and n]
    if not texts:
        return None
    return {
        "sessionUpdate": "agent_message_chunk",
        "content": _text_block("\n".join(f"\u26a0 {t}" for t in texts)),
    }


def write_user_notice(server: Any, session_id: str, text: str) -> None:
    """Deliver one hook user-notice to the client. Best-effort.

    ``SessionStart`` / ``SessionEnd`` hooks reach the user through exit 2,
    which the CLI prints and ``agentao run`` folds into its warnings. ACP has
    no notice channel of its own, so the notice rides the one stream a client
    is guaranteed to render: an ``agent_message_chunk``, marked so it cannot be
    mistaken for model output.

    Swallows every failure, unlike :func:`write_session_update`. Its callers
    are a session being created and a connection being torn down, and neither
    may be failed by a diagnostic — on the close path the stream may already be
    gone, which is exactly the best-effort case.
    """
    update = hook_notice_update(text)
    if update is None:
        return
    try:
        write_session_update(server, session_id, update)
    except Exception:
        logger.debug(
            "acp: could not deliver hook notice for session %s", session_id,
            exc_info=True,
        )
