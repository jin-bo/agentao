"""ACP client manager — registry of per-server process handles and clients.

:class:`ACPManager` is the single entry point for the CLI and agent layers to
start, stop, query, and communicate with project-local ACP servers.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Set, Tuple

from .client import (
    ACPClient,
    AcpClientError,
    AcpErrorCode,
    AcpInteractionRequiredError,
    AcpRpcError,
    AcpServerNotFound,
    _fingerprint_mcp_servers,
)
from .config import load_acp_client_config
from .inbox import Inbox, InboxMessage, MessageKind
from .interaction import InteractionKind, InteractionRegistry, PendingInteraction
from .models import (
    AcpClientConfig,
    AcpServerConfig,
    INTERACTION_POLICY_MODES,
    InteractionPolicy,
    PromptResult,
    ServerState,
    ServerStatus,
    classify_process_death,
)
from .process import ACPProcessHandle

logger = logging.getLogger("agentao.acp_client")


@dataclass
class _TurnContext:
    """Per-turn state tracked by :class:`ACPManager` for non-interactive calls.

    A single slot exists per named server (``ACPManager._active_turns``).
    Created when ``send_prompt`` starts a turn, removed when the turn
    completes. Non-interactive turns consult this context to auto-reject
    inbound server-initiated requests without exposing ``WAITING_FOR_USER``.

    ``effective_policy`` carries the resolved :class:`InteractionPolicy`
    for this turn (per-call override, else server default). The
    router reads it rather than the per-server config so per-call
    overrides land correctly on the running turn.
    """

    server: str
    interactive: bool
    interaction_error: Optional[AcpInteractionRequiredError] = None
    auto_replied_request_ids: Set[Any] = field(default_factory=set)
    cancelled: bool = False
    effective_policy: Optional[InteractionPolicy] = None


def _extract_display_text(method: str, params: Any) -> str:
    """Build a human-friendly display string from ACP notification/request params.

    Extracts a concise, user-readable summary from structured ACP payloads.
    The full raw params are logged at debug level for diagnostics.
    """
    if not isinstance(params, dict):
        return str(params) if params is not None else ""

    # Log full payload for debugging — never show raw dicts to the user.
    logger.debug("acp: %s params: %s", method, params)

    # -- session/request_permission ----------------------------------------
    if method == "session/request_permission":
        return _format_permission_text(params)

    # -- _agentao.cn/ask_user ----------------------------------------------
    if method == "_agentao.cn/ask_user":
        return params.get("question") or params.get("message") or "(input requested)"

    # -- session/update (most common) --------------------------------------
    if method == "session/update":
        return _format_session_update(params)

    # -- Generic fallback --------------------------------------------------
    for key in ("message", "text", "description", "question"):
        val = params.get(key)
        if val:
            return _truncate(str(val), 120)
    return "(notification)"


def _format_permission_text(params: dict) -> str:
    """Format a ``session/request_permission`` payload."""
    tool_call = params.get("toolCall")
    if not isinstance(tool_call, dict):
        return params.get("message") or "(permission requested)"
    title = tool_call.get("title") or "unknown tool"
    kind = tool_call.get("kind", "")
    raw_input = tool_call.get("rawInput")
    parts = [f"Allow {title}"]
    if kind:
        parts[0] += f" ({kind})"
    if isinstance(raw_input, dict):
        arg_items = list(raw_input.items())[:3]
        arg_str = ", ".join(f"{k}={_truncate(str(v), 50)}" for k, v in arg_items)
        if len(raw_input) > 3:
            arg_str += ", ..."
        if arg_str:
            parts.append(arg_str)
    return "?\n  ".join(parts) if len(parts) > 1 else parts[0] + "?"


def _format_session_update(params: dict) -> str:
    """Format a ``session/update`` notification into a concise line."""
    update = params.get("update")
    if not isinstance(update, dict):
        return "(update)"
    kind = update.get("sessionUpdate", "")

    # tool_call: show tool name + args summary
    if kind == "tool_call":
        title = update.get("title", "?")
        tool_kind = update.get("kind", "")
        status = update.get("status", "")
        suffix = f" ({tool_kind})" if tool_kind else ""
        raw = update.get("rawInput")
        if isinstance(raw, dict) and raw:
            args = ", ".join(
                f"{k}={_truncate(str(v), 40)}" for k, v in list(raw.items())[:3]
            )
            return f"{title}{suffix} [{status}]\n  {args}"
        return f"{title}{suffix} [{status}]"

    # tool_call_update: show status
    if kind == "tool_call_update":
        status = update.get("status", "?")
        call_id = update.get("toolCallId", "")
        short_id = call_id[:8] if call_id else ""
        return f"tool {short_id} — {status}"

    # agent_message_chunk: show full text (this is the LLM reply)
    if kind == "agent_message_chunk":
        content = update.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
            return text if text else ""
        return ""

    # agent_thought_chunk: show reasoning (dimmed in render)
    if kind == "agent_thought_chunk":
        content = update.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
            return text if text else ""
        return ""

    # user_message_chunk
    if kind == "user_message_chunk":
        content = update.get("content")
        if isinstance(content, dict):
            text = content.get("text", "")
            return _truncate(text, 80) if text else "(user message)"
        return "(user message)"

    return f"({kind})" if kind else "(update)"


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def _select_reject_option(options: List[Dict[str, Any]]) -> Optional[str]:
    """Pick the best option id to reject a ``session/request_permission`` request.

    ACP servers may expose a non-standard set of options: the canonical
    ``reject_once`` / ``reject_always`` ids are not guaranteed. Preference
    order:

    1. ``kind`` matches ``reject_once`` (exact match wins; first occurrence).
    2. ``kind`` matches any ``reject_*`` variant.
    3. ``optionId`` / ``id`` / ``name`` contains ``reject``, ``deny``, or
       ``cancel`` (case-insensitive).

    Returns ``None`` when no option looks reject-flavored; callers should
    fall back to an explicit ``cancelled`` outcome so the server does not
    hang waiting for a valid selection.
    """
    if not options:
        return None

    def _opt_id(opt: Dict[str, Any]) -> Optional[str]:
        for key in ("optionId", "id"):
            val = opt.get(key)
            if isinstance(val, str) and val:
                return val
        return None

    # Pass 1: kind == "reject_once" (canonical).
    for opt in options:
        if opt.get("kind") == "reject_once":
            oid = _opt_id(opt)
            if oid:
                return oid
    # Pass 2: any reject_* kind.
    for opt in options:
        kind = opt.get("kind")
        if isinstance(kind, str) and kind.startswith("reject"):
            oid = _opt_id(opt)
            if oid:
                return oid
    # Pass 3: reject/deny/cancel hint in id or name.
    hints = ("reject", "deny", "cancel")
    for opt in options:
        haystack = " ".join(
            str(opt.get(k, "")) for k in ("optionId", "id", "name", "label")
        ).lower()
        if any(h in haystack for h in hints):
            oid = _opt_id(opt)
            if oid:
                return oid
    return None


def _extract_options(interaction: "PendingInteraction") -> List[Dict[str, Any]]:
    """Return the ``options`` list from the original server request params.

    Servers can ship non-canonical option IDs (e.g. ``go_ahead`` /
    ``decline_now``) so the interactive approve / reject paths must
    echo the id the server actually sent rather than assuming the
    ACP-spec canonical ``allow_once`` / ``reject_once``.
    """
    details = interaction.details
    if not isinstance(details, dict):
        return []
    raw = details.get("options")
    if not isinstance(raw, list):
        return []
    return [o for o in raw if isinstance(o, dict)]


def _select_option_by_kind(
    options: List[Dict[str, Any]], preferred_kind: str,
) -> Optional[str]:
    """Return the ``optionId`` for the first option whose ``kind`` matches.

    Used to prefer ``allow_always`` over ``allow_once`` (and similarly
    for reject) without duplicating the broader fallback logic in
    :func:`_select_approve_option` / :func:`_select_reject_option`.
    """
    for opt in options:
        if opt.get("kind") == preferred_kind:
            for key in ("optionId", "id"):
                val = opt.get(key)
                if isinstance(val, str) and val:
                    return val
    return None


def _select_approve_option(options: List[Dict[str, Any]]) -> Optional[str]:
    """Pick the best option id to approve a ``session/request_permission`` request.

    Mirrors :func:`_select_reject_option` but looks for allow/accept/approve
    flavored entries. Returns ``None`` when no such option exists; callers
    should fall back to the reject path rather than send an invalid id.
    """
    if not options:
        return None

    def _opt_id(opt: Dict[str, Any]) -> Optional[str]:
        for key in ("optionId", "id"):
            val = opt.get(key)
            if isinstance(val, str) and val:
                return val
        return None

    # Pass 1: kind == "allow_once" (canonical).
    for opt in options:
        if opt.get("kind") == "allow_once":
            oid = _opt_id(opt)
            if oid:
                return oid
    # Pass 2: any allow_* kind.
    for opt in options:
        kind = opt.get("kind")
        if isinstance(kind, str) and kind.startswith("allow"):
            oid = _opt_id(opt)
            if oid:
                return oid
    # Pass 3: allow/accept/approve hint in id or name.
    hints = ("allow", "accept", "approve")
    for opt in options:
        haystack = " ".join(
            str(opt.get(k, "")) for k in ("optionId", "id", "name", "label")
        ).lower()
        if any(h in haystack for h in hints):
            oid = _opt_id(opt)
            if oid:
                return oid
    return None


class ACPManager:
    """Registry that owns one :class:`ACPProcessHandle` per configured server.

    Typical lifecycle::

        mgr = ACPManager.from_project()   # load config, create handles
        mgr.start_all()                   # launch subprocesses
        ...                               # CLI / agent work
        mgr.stop_all()                    # clean up on exit
    """

    def __init__(
        self,
        config: AcpClientConfig,
        *,
        notification_callback: Optional[Callable[[str, str, Any], None]] = None,
    ) -> None:
        self._config = config
        self._handles: Dict[str, ACPProcessHandle] = {}
        self._clients: Dict[str, ACPClient] = {}
        self._notification_callback = notification_callback
        self.inbox = Inbox()
        self.interactions = InteractionRegistry()

        # Per-server turn-bearing serialization. Acquired around the
        # synchronous send_prompt / prompt_once / cancel_turn entrypoints;
        # never held across async MCP loop internals (lock is a plain
        # threading.Lock, not an asyncio.Lock). Fail-fast contract:
        # callers use ``acquire(blocking=False)`` and surface
        # ``SERVER_BUSY`` instead of queueing.
        self._server_locks: Dict[str, threading.Lock] = {}
        self._server_locks_meta = threading.Lock()

        # Per-server handshake-bearing serialization. Distinct from the
        # turn lock so a direct ``connect_server`` / ``ensure_connected``
        # call cannot make a concurrent ``send_prompt`` / ``prompt_once``
        # spuriously raise ``SERVER_BUSY``: the turn lock is fail-fast,
        # but handshake setup is not turn activity. Re-entrant so
        # ``ensure_connected`` → ``connect_server`` on the same thread
        # doesn't self-deadlock.
        self._handshake_locks: Dict[str, "threading.RLock"] = {}
        self._handshake_locks_meta = threading.Lock()

        # Single active turn slot per named server.
        self._active_turns: Dict[str, _TurnContext] = {}
        self._active_turns_lock = threading.Lock()

        # Ephemeral clients created by ``prompt_once``. They do NOT appear
        # in ``self._clients`` or ``get_status()``; the separate map only
        # exists so callback routing (notifications, server requests) can
        # still find the active client for a given server name.
        self._ephemeral_clients: Dict[str, ACPClient] = {}
        self._ephemeral_lock = threading.Lock()

        # Week 2 status-snapshot diagnostics. ``_last_errors`` carries the
        # most recent human-readable error + its *store-time* timestamp,
        # set inside ``_record_last_error`` (not at raise time).
        # ``_config_warnings`` is a per-server deprecation surface that
        # Week 3 legacy-config handling will populate; today it is
        # plumbed through to ``ServerStatus`` as an empty list so
        # embedders can start depending on the field shape.
        self._last_errors: Dict[str, Tuple[str, datetime]] = {}
        self._last_errors_lock = threading.Lock()
        self._config_warnings: Dict[str, List[str]] = {
            name: [] for name in config.servers
        }

        # Week 4 Issue 16 — recovery state. ``_fatal_servers`` holds
        # servers whose last classified death was terminal; entries are
        # cleared only by an explicit ``restart_server(name)`` or
        # ``start_server(name)``. ``_restart_counts`` tracks the number
        # of consecutive auto-recoveries since the last successful turn
        # and bounds recovery via ``max_recoverable_restarts`` on the
        # server config. ``_handshake_fail_streak`` is bumped on each
        # handshake failure seen inside ``connect_server`` and reset on
        # success; two in a row flips the classification to fatal.
        self._fatal_servers: Set[str] = set()
        self._restart_counts: Dict[str, int] = {
            name: 0 for name in config.servers
        }
        self._handshake_fail_streak: Dict[str, int] = {
            name: 0 for name in config.servers
        }
        self._recovery_lock = threading.Lock()

        for name, server_cfg in config.servers.items():
            self._handles[name] = ACPProcessHandle(name, server_cfg)

    # ------------------------------------------------------------------
    # Per-server lock / turn slot helpers
    # ------------------------------------------------------------------

    def _get_server_lock(self, name: str) -> threading.Lock:
        with self._server_locks_meta:
            lock = self._server_locks.get(name)
            if lock is None:
                lock = threading.Lock()
                self._server_locks[name] = lock
            return lock

    def _get_handshake_lock(self, name: str) -> "threading.RLock":
        with self._handshake_locks_meta:
            lock = self._handshake_locks.get(name)
            if lock is None:
                lock = threading.RLock()
                self._handshake_locks[name] = lock
            return lock

    def _install_turn(self, name: str, ctx: _TurnContext) -> None:
        with self._active_turns_lock:
            self._active_turns[name] = ctx

    def _clear_turn(self, name: str) -> None:
        with self._active_turns_lock:
            self._active_turns.pop(name, None)

    def _get_active_turn(self, name: str) -> Optional[_TurnContext]:
        with self._active_turns_lock:
            return self._active_turns.get(name)

    def _client_for(self, name: str) -> Optional[ACPClient]:
        """Return the active client for a server (long-lived or ephemeral)."""
        client = self._clients.get(name)
        if client is not None:
            return client
        with self._ephemeral_lock:
            return self._ephemeral_clients.get(name)

    # ------------------------------------------------------------------
    # Week 2: last-error store (assignment-time timestamp)
    # ------------------------------------------------------------------

    # Error codes that are about caller-side concurrency / misuse rather
    # than server health. Recording them would overwrite a real failure
    # with noise every time the caller retries, so they are skipped.
    _NON_RECORDABLE_ERROR_CODES: frozenset = frozenset({
        AcpErrorCode.SERVER_BUSY,
        AcpErrorCode.SERVER_NOT_FOUND,
    })

    def _record_last_error(self, name: str, exc: BaseException) -> None:
        """Store the most recent embedder-facing error for *name*.

        Timestamp is taken **inside this method** using
        ``datetime.now(timezone.utc)`` — i.e., at store-time, not at
        the time the error was raised. Consumers read it via
        :meth:`get_status` to judge staleness. See the state-vs-error
        contract in ``docs/features/headless-runtime.md``.
        """
        if name not in self._handles:
            return
        if isinstance(exc, AcpClientError) and exc.code in self._NON_RECORDABLE_ERROR_CODES:
            return
        message = str(exc) or type(exc).__name__
        now = datetime.now(timezone.utc)
        with self._last_errors_lock:
            self._last_errors[name] = (message, now)

    # ------------------------------------------------------------------
    # Week 4: client / process death classification + recovery
    # ------------------------------------------------------------------

    def is_fatal(self, name: str) -> bool:
        """Return ``True`` if *name* is in the terminal fatal state.

        A server lands in the fatal state when the Week 4 classifier
        (``classify_process_death``) returns ``"fatal"`` — for example
        after OOM / SIGKILL, after consecutive handshake failures, or
        after exceeding ``max_recoverable_restarts``. The mark is
        sticky and **only** cleared by an explicit
        :meth:`restart_server` / :meth:`start_server` call.
        """
        if name not in self._handles:
            raise AcpServerNotFound(name)
        with self._recovery_lock:
            return name in self._fatal_servers

    def restart_count(self, name: str) -> int:
        """Return the current auto-recovery counter for *name*.

        Reset on the first successful turn after a recovery; bumped on
        every classified-recoverable rebuild. Used by embedders to
        detect flapping servers.
        """
        if name not in self._handles:
            raise AcpServerNotFound(name)
        with self._recovery_lock:
            return self._restart_counts.get(name, 0)

    def _classify_handle_death(
        self, name: str, *, during_active_turn: bool,
    ) -> str:
        """Run :func:`classify_process_death` against the handle's state.

        Pulls the live exit code / signal info off the handle and the
        running counters off the manager. Returns the string literal
        ``"recoverable"`` or ``"fatal"``.
        """
        handle = self._handles[name]
        proc = handle._proc
        exit_code: Optional[int] = None
        signaled = False
        if proc is not None:
            exit_code = proc.poll()
            if exit_code is not None and exit_code < 0:
                # Popen.poll on POSIX returns negative signal numbers
                # (e.g. -9 for SIGKILL); exit code 137 (128 + 9) on
                # some shells maps the same way.
                signaled = True
            elif exit_code in (137, 139, 143):
                signaled = True
        server_cfg = self._config.servers.get(name)
        cap = (
            server_cfg.max_recoverable_restarts
            if server_cfg is not None else 3
        )
        with self._recovery_lock:
            count = self._restart_counts.get(name, 0)
            streak = self._handshake_fail_streak.get(name, 0)
        return classify_process_death(
            exit_code=exit_code,
            signaled=signaled,
            during_active_turn=during_active_turn,
            restart_count=count,
            max_recoverable_restarts=cap,
            handshake_fail_streak=streak,
        )

    def _note_recovery_attempt(self, name: str) -> None:
        """Record one recoverable-death rebuild. Called inside the lock."""
        with self._recovery_lock:
            self._restart_counts[name] = self._restart_counts.get(name, 0) + 1

    def _note_successful_turn(self, name: str) -> None:
        """Reset recovery counters after a turn succeeds end-to-end."""
        with self._recovery_lock:
            self._restart_counts[name] = 0
            self._handshake_fail_streak[name] = 0

    def _note_handshake_failure(self, name: str) -> None:
        with self._recovery_lock:
            self._handshake_fail_streak[name] = (
                self._handshake_fail_streak.get(name, 0) + 1
            )

    def _note_handshake_success(self, name: str) -> None:
        """Clear the handshake-fail streak after a successful handshake.

        Called on both greenfield success (``connect_server``) and
        cached-client re-session success (``ensure_connected`` /
        ``prompt_once``). Without the cached-path call, a prior
        single failed ``session/new`` would leave the streak at 1
        indefinitely — the next unrelated handshake failure would
        then trip sticky-fatal despite not being a *consecutive*
        pair of failures.
        """
        with self._recovery_lock:
            self._handshake_fail_streak[name] = 0

    def _note_handshake_failure_and_maybe_fatal(self, name: str) -> None:
        """Single source of truth for the "2 consecutive handshakes ⇒ fatal" rule.

        ``connect_server``, the cached-client re-session branch in
        ``ensure_connected``, and ``prompt_once`` all reclassify
        handshake/session-setup exceptions as ``HANDSHAKE_FAIL``. Each
        of those entry points must also flip sticky-fatal on the second
        failure, otherwise a host choosing one API (e.g. the public
        ``connect_server``) silently opts out of the documented
        recovery contract. Increments the streak and fatal-marks in a
        single critical section so a racing clear cannot desync them.
        """
        with self._recovery_lock:
            streak = self._handshake_fail_streak.get(name, 0) + 1
            self._handshake_fail_streak[name] = streak
        if streak > 1:
            self._mark_fatal(name)

    def _reclassify_as_handshake_fail(
        self, exc: BaseException, name: str,
    ) -> bool:
        """Tag a handshake/session-setup failure.

        Mirrors the classification the three entry points used to
        duplicate inline. Returns ``True`` when *exc* belongs to the
        handshake-failure streak — callers pair a ``True`` return with
        :meth:`_note_handshake_failure_and_maybe_fatal`.

        ``AcpInteractionRequiredError`` is intentionally excluded: a
        permission cancel during setup is a user decision, not a
        handshake regression, and must not count toward the
        sticky-fatal streak.

        Mutation policy (delivers Appendix D §D.7 in full):

        * **Non-RPC** :class:`AcpClientError` with a setup-eligible
          ``code`` (``PROTOCOL_ERROR`` / ``TRANSPORT_DISCONNECT`` /
          ``REQUEST_TIMEOUT``): the original code is stashed in
          ``details["underlying_code"]`` **before** we reclassify
          ``exc.code`` to :attr:`AcpErrorCode.HANDSHAKE_FAIL`. That
          preserves the documented pattern embedders branch on
          (``case AcpErrorCode.HANDSHAKE_FAIL`` — see
          ``developer-guide/en/part-3/4-reverse-acp-call.md``) *and*
          lets §D.7's finer-classification example actually fire,
          because the underlying timeout/disconnect is still
          available on the exception.
        * :class:`AcpRpcError`: ``code`` (JSON-RPC int) and
          ``error_code`` (``PROTOCOL_ERROR``) are rigid public
          contract — never mutated. Only ``details["phase"]`` +
          ``details["server"]`` are stamped; embedders detect RPC
          handshake failures via ``isinstance(exc, AcpRpcError)`` +
          ``details["phase"] == "handshake"``.
        * An ``AcpClientError`` already tagged ``HANDSHAKE_FAIL``
          (e.g. a nested reclassification, or a direct raise from
          the client layer) still counts toward the streak and gets
          its ``details["phase"]`` / ``["server"]`` stamped.

        In every branch ``details["phase"] = "handshake"`` is the
        canonical cross-subclass signal.
        """
        if isinstance(exc, AcpInteractionRequiredError):
            return False
        if isinstance(exc, AcpRpcError):
            # RPC contract: leave ``code`` (int) and ``error_code``
            # (PROTOCOL_ERROR) alone. Phase signal lives in details.
            exc.details.setdefault("server", name)
            exc.details["phase"] = "handshake"
            return True
        if isinstance(exc, AcpClientError) and exc.code in (
            AcpErrorCode.PROTOCOL_ERROR,
            AcpErrorCode.TRANSPORT_DISCONNECT,
            AcpErrorCode.REQUEST_TIMEOUT,
        ):
            # Preserve the original classification so §D.7 readers
            # can still distinguish handshake-timeout from
            # handshake-disconnect, then flip the headline code to
            # the documented handshake bucket.
            exc.details.setdefault("underlying_code", exc.code)
            exc.code = AcpErrorCode.HANDSHAKE_FAIL
            exc.details.setdefault("server", name)
            exc.details["phase"] = "handshake"
            return True
        # Already an AcpClientError tagged HANDSHAKE_FAIL (e.g. a nested
        # reclassification) — still counts toward the streak.
        if (
            isinstance(exc, AcpClientError)
            and exc.code is AcpErrorCode.HANDSHAKE_FAIL
        ):
            exc.details.setdefault("server", name)
            exc.details["phase"] = "handshake"
            return True
        return False

    def _mark_fatal(self, name: str) -> None:
        with self._recovery_lock:
            self._fatal_servers.add(name)
        # Reflect the fatal classification in the handle snapshot so
        # readiness() / is_ready() / get_status() can't keep routing
        # work to a server the manager has already given up on. The
        # state is cleared when ``restart_server`` / ``start_server``
        # re-invokes handle.start() / handle.restart().
        handle = self._handles.get(name)
        if handle is not None:
            with handle._lock:
                handle._set_state(
                    ServerState.FAILED,
                    f"server '{name}' marked fatal by recovery classifier",
                )
                handle.info.pid = None

    def _clear_fatal(self, name: str) -> None:
        with self._recovery_lock:
            self._fatal_servers.discard(name)
            self._restart_counts[name] = 0
            self._handshake_fail_streak[name] = 0

    def _check_cached_client_alive(
        self, name: str, *, count_restart: bool = True
    ) -> None:
        """Classify a dead cached subprocess and evict the stale client.

        Shared precondition for every entry point that may reuse a
        long-lived client (``ensure_connected``, ``prompt_once``).
        When the cached client's subprocess has died, the server is
        either (a) already sticky-fatal — raise; (b) recoverably dead
        — bump the counter, close the stale client, return so the
        caller can rebuild; or (c) fresh-fatal — mark fatal and
        raise. A caller with no cached client is a no-op.

        Pass ``count_restart=False`` from health-poll paths (``get_status``,
        ``readiness``) that detect a dead process but do not trigger a
        rebuild themselves.  Charging the restart budget on a pure poll
        would cause the next real recovery to flip the server to
        sticky-fatal one crash earlier than intended.

        Always call *after* the fatal check; ``ensure_connected``
        does so first thing.
        """
        if self.is_fatal(name):
            raise AcpClientError(
                f"server '{name}' is in the fatal recovery state; "
                f"call restart_server('{name}') to clear it",
                code=AcpErrorCode.TRANSPORT_DISCONNECT,
                details={"server": name, "recovery": "fatal"},
            )
        client = self._clients.get(name)
        handle = self._handles.get(name)
        if handle is None:
            return
        if client is None:
            # No cached client (e.g. after prompt_once(stop_process=False)).
            # Still detect a dead process so get_status()/readiness() don't
            # report a stale READY state forever — hosts that gate submissions
            # on readiness() would otherwise route work to a dead server.
            proc = handle._proc
            if proc is not None and proc.poll() is not None:
                active_turn = self._get_active_turn(name) is not None
                classification = self._classify_handle_death(
                    name, during_active_turn=active_turn,
                )
                if classification == "fatal":
                    self._mark_fatal(name)
                    raise AcpClientError(
                        f"server '{name}' died and the classifier flagged "
                        f"the death as fatal (see handle exit code / "
                        f"signal); call restart_server to retry",
                        code=AcpErrorCode.TRANSPORT_DISCONNECT,
                        details={"server": name, "recovery": "fatal"},
                    )
                # Health-poll callers pass count_restart=False, so they
                # don't charge the budget.  Recovery-path callers pass
                # count_restart=True (the default) and must charge it so
                # max_recoverable_restarts is enforced for this workflow too.
                if count_restart:
                    self._note_recovery_attempt(name)
                with handle._lock:
                    handle._set_state(ServerState.CONFIGURED)
                    handle.info.pid = None
            return
        proc = handle._proc
        if proc is None or proc.poll() is None:
            return
        # If a turn slot is still installed for the server, the subprocess
        # died mid-turn. The Week 4 contract guarantees those deaths get
        # one rebuild attempt regardless of the idle-exit cap, so pass
        # ``during_active_turn`` through to the classifier.
        active_turn = self._get_active_turn(name) is not None
        classification = self._classify_handle_death(
            name, during_active_turn=active_turn,
        )
        stale = self._clients.pop(name, None)
        if stale is not None:
            try:
                stale.close()
            except Exception:
                logger.debug(
                    "acp[%s]: error closing dead cached client",
                    name, exc_info=True,
                )
            # Reset the streak so a prior isolated handshake failure cannot
            # combine with a future one across an unrelated subprocess death.
            # Without this, "handshake failure → recoverable death → handshake
            # failure" incorrectly trips sticky-fatal after only one new failure.
            self._handshake_fail_streak[name] = 0
        if classification == "fatal":
            self._mark_fatal(name)
            raise AcpClientError(
                f"server '{name}' died and the classifier flagged "
                f"the death as fatal (see handle exit code / "
                f"signal); call restart_server to retry",
                code=AcpErrorCode.TRANSPORT_DISCONNECT,
                details={"server": name, "recovery": "fatal"},
            )
        if count_restart:
            self._note_recovery_attempt(name)
        # Reflect the eviction in the handle snapshot so get_status() and
        # readiness() don't return a stale READY state while the transport
        # awaits a transparent rebuild on the next prompt submission.
        with handle._lock:
            handle._set_state(ServerState.CONFIGURED)
            handle.info.pid = None

    # ------------------------------------------------------------------
    # Week 3: interaction policy resolution
    # ------------------------------------------------------------------

    def _resolve_interaction_policy(
        self,
        name: str,
        override: Any,
    ) -> InteractionPolicy:
        """Collapse per-call override + server default into one policy.

        Precedence: per-call override > server default. ``None`` means
        "fall back to server default" (``nonInteractivePolicy`` in
        ``.agentao/acp.json``).

        Accepts either :class:`InteractionPolicy` or a bare
        ``Literal["reject_all", "accept_all"]`` string for the override
        form. Any other value raises ``TypeError`` / ``ValueError`` at
        the call site rather than being silently ignored.
        """
        if override is None:
            server_cfg = self._config.servers.get(name)
            if server_cfg is not None:
                return server_cfg.non_interactive_policy
            return InteractionPolicy(mode="reject_all")
        if isinstance(override, InteractionPolicy):
            return override
        if isinstance(override, str):
            if override not in INTERACTION_POLICY_MODES:
                raise ValueError(
                    f"interaction_policy must be one of "
                    f"{sorted(INTERACTION_POLICY_MODES)} or an "
                    f"InteractionPolicy; got {override!r}"
                )
            return InteractionPolicy(mode=override)
        raise TypeError(
            f"interaction_policy must be InteractionPolicy | "
            f"Literal['reject_all','accept_all'] | None; "
            f"got {type(override).__name__}"
        )

    def reset_last_error(self, name: str) -> None:
        """Clear the recorded ``last_error`` / ``last_error_at`` for *name*.

        ``last_error`` is intentionally sticky across successful turns
        (so a consumer that polls once per minute still sees the failure
        context), so this explicit reset is the only way to drop a
        stored error short of a new error overwriting it.

        Also clears the handle-level fallback (``handle.info.last_error``
        / ``handle.info.last_error_at``). Startup and handshake failures
        populate the handle directly without going through
        ``_record_last_error``, and ``get_status()`` falls back to the
        handle value when the manager-side store is empty — so without
        clearing both, the "explicit reset" surface would leave the
        reported ``last_error`` unchanged for exactly the failure modes
        (``start_server`` / ``start_all`` crashes) callers are most
        likely to want to reset after remediation.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        with self._last_errors_lock:
            self._last_errors.pop(name, None)
        with handle._lock:
            handle.info.last_error = None
            handle.info.last_error_at = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_project(cls, project_root: Optional[Path] = None) -> "ACPManager":
        """Load ``acp.json`` and build a manager with handles for every server.

        Args:
            project_root: Forwarded to :func:`load_acp_client_config`.
        """
        config = load_acp_client_config(project_root=project_root)
        return cls(config)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def config(self) -> AcpClientConfig:
        return self._config

    @property
    def server_names(self) -> List[str]:
        return list(self._handles.keys())

    def get_handle(self, name: str) -> Optional[ACPProcessHandle]:
        return self._handles.get(name)

    def get_client(self, name: str) -> Optional[ACPClient]:
        return self._clients.get(name)

    # ------------------------------------------------------------------
    # Bulk lifecycle
    # ------------------------------------------------------------------

    def start_all(self, *, only_auto: bool = True) -> None:
        """Start server subprocesses.

        Args:
            only_auto: If ``True`` (default), only start servers whose
                ``auto_start`` config flag is set.
        """
        for name, handle in self._handles.items():
            if only_auto and not handle.config.auto_start:
                logger.debug("acp: skipping '%s' (autoStart=false)", name)
                continue
            try:
                with self._get_handshake_lock(name):
                    proc = handle._proc
                    proc_alive = proc is not None and proc.poll() is None
                    if handle.info.state is ServerState.FAILED and proc_alive:
                        # Live-fatal: subprocess is alive but stuck in FAILED.
                        # handle.start() is a no-op here, so force a full
                        # restart — mirrors the start_server() recovery path.
                        self._evict_cached_client(name, "start_all/live-fatal")
                        handle.restart()
                        self._clear_fatal(name)
                    else:
                        did_launch = not proc_alive
                        if did_launch:
                            self._evict_cached_client(name, "start_all/dead-proc")
                        handle.start()
                        if did_launch:
                            self._clear_fatal(name)
            except RuntimeError as exc:
                logger.error("acp: %s", exc)

    def connect_server(
        self,
        name: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
    ) -> ACPClient:
        """Start a server, perform ACP handshake, and create a session.

        This is the full ``start → initialize → session/new`` flow.
        If the process is already running, it reuses the existing handle.

        Args:
            name: Server name from config.
            cwd: Working directory for the ACP session.
            mcp_servers: MCP server configs for the session.
            timeout: Per-RPC timeout in seconds.

        Returns:
            The connected :class:`ACPClient`.

        Raises:
            KeyError: If *name* is not configured.
            RuntimeError: If subprocess fails to start.
            AcpRpcError / AcpClientError: If handshake fails.
        """
        # Serialize with concurrent ``prompt_once`` /
        # ``_open_ephemeral_client`` / ``ensure_connected`` calls via
        # the handshake lock — not the (fail-fast) turn lock. Without
        # this, an ephemeral handshake failure on one thread could
        # ``handle.stop()`` the subprocess this call is mid-initializing,
        # and two ``ACPClient`` readers could end up attached to the
        # same stdout. Using the turn lock here instead would be
        # incorrect: it is fail-fast, so direct embedder setup calls
        # would then make concurrent ``send_prompt`` /  ``prompt_once``
        # on other threads spuriously raise ``SERVER_BUSY`` even though
        # no turn is active.
        handshake_lock = self._get_handshake_lock(name)
        with handshake_lock:
            return self._connect_server_locked(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )

    def _connect_server_locked(
        self,
        name: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
    ) -> ACPClient:
        """``connect_server`` body — caller must hold the handshake lock."""
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        # Fatal-state check must come before the cached-client short-circuit:
        # the server can be marked fatal *after* a client was cached (e.g. two
        # consecutive handshake failures), and returning a stale client in that
        # case would let callers bypass the recovery gate entirely.
        if self.is_fatal(name):
            raise AcpClientError(
                f"server '{name}' is in the fatal recovery state; "
                f"call restart_server('{name}') to clear it",
                code=AcpErrorCode.TRANSPORT_DISCONNECT,
                details={"server": name, "recovery": "fatal"},
            )

        # If a concurrent caller already installed a client while we
        # were waiting on the handshake lock, reuse theirs. Constructing
        # a second ``ACPClient`` on the same handle would start a
        # competing reader thread on one stdout stream and overwrite
        # ``self._clients[name]`` with a fresh session — breaking any
        # turn the first caller was about to run. This fires on the
        # ``send_prompt`` / ``send_prompt_nonblocking`` pre-warm path
        # where multiple threads can simultaneously observe "no cached
        # client" and race into ``connect_server``.
        #
        # Before returning the cached client, verify the subprocess is
        # still alive. ``_check_cached_client_alive`` evicts the stale
        # entry (or raises if fatal) when it is not, so we fall through
        # to the normal rebuild path below.
        existing = self._clients.get(name)
        if existing is not None:
            self._check_cached_client_alive(name)
            existing = self._clients.get(name)
            if existing is not None:
                eff_cwd, eff_mcp = self._effective_session_params(
                    name, cwd=cwd, mcp_servers=mcp_servers,
                )
                if self._session_matches(existing, cwd=eff_cwd, mcp_servers=eff_mcp):
                    return existing
                # Session params differ — re-session the alive client rather
                # than constructing a second ACPClient (which would race on
                # stdout). Guard against mid-turn re-sessioning: sending a new
                # session/new while session/prompt is in-flight mutates
                # session_id under the active turn and interleaves handshake
                # traffic with it.
                #
                # Use the server lock as the authoritative "turn active" signal.
                # _active_turns is set inside _run_turn_on_client, which is
                # called *after* the server lock is acquired, so there is a
                # window where the lock is held but _active_turns is empty.
                # blocking=False is required to prevent an ABBA deadlock:
                # send_prompt holds server_lock and waits for handshake_lock
                # (inside ensure_connected); acquiring server_lock here
                # (blocking) while holding handshake_lock would deadlock.
                _server_lock = self._get_server_lock(name)
                if not _server_lock.acquire(blocking=False):
                    raise AcpClientError(
                        f"server '{name}' has an active turn; "
                        f"connect_server cannot re-session a mid-turn client",
                        code=AcpErrorCode.SERVER_BUSY,
                        details={"server": name},
                    )
                _server_lock.release()
                try:
                    existing.create_session(
                        cwd=eff_cwd, mcp_servers=eff_mcp, timeout=timeout,
                    )
                except BaseException as exc:
                    if self._reclassify_as_handshake_fail(exc, name):
                        self._note_handshake_failure_and_maybe_fatal(name)
                    raise
                self._note_handshake_success(name)
                return existing

        # Refuse to attach a new ACPClient while a concurrent prompt_once
        # ephemeral client already owns the handle's stdout — two readers on
        # one pipe would drop or misroute ACP frames.
        with self._ephemeral_lock:
            has_ephemeral = name in self._ephemeral_clients
        if has_ephemeral:
            raise AcpClientError(
                f"server '{name}' has an in-flight ephemeral client; "
                f"connect_server cannot create a second reader on the same transport",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )

        # Track whether this call is starting the process from scratch.  If the
        # server was already running (e.g. pre-warmed via start_server() /
        # start_all()), a handshake failure must not tear it down — client.close()
        # already detaches our subscriber via unsubscribe_stdout(), which is
        # sufficient with the stdout-feeder design.
        _existing_proc = handle._proc
        _we_started = _existing_proc is None or _existing_proc.poll() is not None

        # Start process if not already running. Classify non-AcpClientError
        # failures (e.g. RuntimeError from the process handle) as
        # PROCESS_START_FAIL so embedders can distinguish a bad executable
        # from a rejected handshake.
        try:
            handle.start()
        except AcpClientError:
            raise
        except Exception as exc:
            raise AcpClientError(
                f"failed to start ACP server '{name}': {exc}",
                code=AcpErrorCode.PROCESS_START_FAIL,
                details={"server": name},
                cause=exc,
            ) from exc

        # Build notification callback that routes to inbox + user callback.
        def _on_notification(method: str, params: Any) -> None:
            self._route_notification(name, method, params)
            if self._notification_callback is not None:
                self._notification_callback(name, method, params)

        # Build server-request callback for permission/input requests.
        def _on_server_request(method: str, params: Any, request_id: Any) -> None:
            self._route_server_request(name, method, params, request_id)

        client = ACPClient(
            handle,
            notification_callback=_on_notification,
            server_request_callback=_on_server_request,
        )
        # Re-label initialize()/create_session() failures as HANDSHAKE_FAIL
        # so callers can separate protocol-level setup from ordinary RPC
        # errors on an established session. AcpRpcError keeps its numeric
        # ``code`` contract; we only tag the structured classification.
        # The sticky-fatal accounting lives here (not just in the
        # ``ensure_connected``/``prompt_once`` wrappers) so hosts that
        # call ``connect_server`` directly get the same "2 consecutive
        # handshakes ⇒ fatal" contract as every other entry point.
        try:
            client.start_reader()
            client.initialize(timeout=timeout)
            client.create_session(
                cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )
        except BaseException as exc:
            # client.close() calls unsubscribe_stdout(), which detaches our
            # subscriber from the feeder thread — no stale reader remains.
            # Only stop the subprocess if we started it; a pre-warmed server
            # (started via start_server()/start_all()) must survive a transient
            # handshake failure so the next call can reuse the warm process.
            try:
                client.close()
            except Exception:
                logger.debug(
                    "acp[%s]: error closing partially initialized client",
                    name, exc_info=True,
                )
            if _we_started:
                try:
                    handle.stop()
                except Exception:
                    logger.debug(
                        "acp[%s]: error stopping handle after handshake failure",
                        name, exc_info=True,
                    )
            if self._reclassify_as_handshake_fail(exc, name):
                self._note_handshake_failure_and_maybe_fatal(name)
            raise

        # A successful connect/handshake clears the handshake-failure
        # streak — otherwise an earlier single failure would linger and
        # combine with a later unrelated failure to trip the sticky-fatal
        # "consecutive handshake failures" rule across prewarmed hosts.
        self._note_handshake_success(name)
        self._clients[name] = client
        return client

    def stop_all(self) -> None:
        """Stop all clients and server subprocesses."""
        for name, client in self._clients.items():
            try:
                client.close()
            except Exception as exc:
                logger.error("acp: error closing client '%s': %s", name, exc)
        self._clients.clear()

        for name, client in list(self._ephemeral_clients.items()):
            try:
                client.close()
            except Exception as exc:
                logger.error(
                    "acp: error closing ephemeral client '%s': %s", name, exc
                )
        self._ephemeral_clients.clear()

        for handle in self._handles.values():
            try:
                handle.stop()
            except Exception as exc:
                logger.error(
                    "acp: error stopping '%s': %s", handle.name, exc
                )

    def _evict_cached_client(self, name: str, reason: str) -> None:
        """Close and drop any cached client tied to a soon-to-be-replaced proc.

        Required on every explicit recovery path that spawns a new
        subprocess (``start_server`` restart branch, ``restart_server``,
        ``start_server`` on a dead proc). Otherwise ``_clients[name]``
        keeps pointing at the old stdio transport and the next
        ``send_prompt`` reuses a session bound to the dead process
        before ``_check_cached_client_alive`` has had a chance to
        notice, producing ``TRANSPORT_DISCONNECT`` instead of recovery.
        """
        client = self._clients.pop(name, None)
        if client is None:
            return
        try:
            client.close()
        except Exception:
            logger.debug(
                "acp[%s]: error closing cached client (%s)",
                name, reason, exc_info=True,
            )

    def start_server(self, name: str) -> None:
        """Start a single server by name.

        Explicit start always clears the Week 4 fatal state and
        recovery counters — operators use ``start_server`` /
        ``restart_server`` to acknowledge a fatal-state server is
        ready to be retried.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        with self._get_handshake_lock(name):
            # If the subprocess is still alive but the handle state was
            # forced to FAILED by ``_mark_fatal`` (e.g. repeated handshake
            # failures with a live process), a plain ``handle.start()`` is
            # a no-op and leaves readiness()/get_status() stuck reporting
            # "failed". Force a full restart in that case so callers using
            # the documented ``start_server`` recovery path always observe
            # recovery succeed.
            proc = handle._proc
            proc_alive = proc is not None and proc.poll() is None
            if handle.info.state is ServerState.FAILED and proc_alive:
                self._evict_cached_client(name, "start_server/restart")
                handle.restart()
            else:
                # If there is no live subprocess, any cached client is
                # pinned to a dead transport — evict before ``handle.start``
                # spawns the replacement. On the idempotent "already alive"
                # path there is nothing to evict.
                if not proc_alive:
                    self._evict_cached_client(name, "start_server/dead-proc")
                handle.start()
            self._clear_fatal(name)

    def stop_server(self, name: str) -> None:
        """Stop a single server by name.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        client = self._clients.pop(name, None)
        if client is not None:
            client.close()
        handle.stop()

    def restart_server(self, name: str) -> None:
        """Restart a single server by name.

        Explicit restart clears the Week 4 fatal state and recovery
        counters — this is the operator-action escape hatch out of a
        fatal classification.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        with self._get_handshake_lock(name):
            self._evict_cached_client(name, "restart_server")
            with self._ephemeral_lock:
                stale = self._ephemeral_clients.pop(name, None)
            if stale is not None:
                try:
                    stale.close()
                except Exception:
                    pass
            handle.restart()
            self._clear_fatal(name)

    # ------------------------------------------------------------------
    # Prompt / cancel (Issue 04)
    # ------------------------------------------------------------------

    def ensure_connected(
        self,
        name: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
        _inside_turn: bool = False,
    ) -> ACPClient:
        """Return an existing client, or auto-connect / re-session as needed.

        Session reuse is conditional on matching per-call ``cwd`` and
        ``mcp_servers``. When either differs from the cached session, the
        old client is closed and a fresh session is created.

        Week 4 Issue 16: if the cached client's subprocess has died
        since the last call, classify the death via
        :func:`classify_process_death`. ``recoverable`` deaths (within
        the cap) trigger a lazy rebuild on this call; ``fatal`` deaths
        raise ``AcpClientError(code=TRANSPORT_DISCONNECT)`` and leave
        the server in the sticky fatal state (inspect via
        :meth:`is_fatal` / ``last_error``) until an explicit
        :meth:`restart_server` call clears it.

        Args:
            name: Server name from config.
            cwd: Working directory for the ACP session.
            mcp_servers: MCP server configs for the session.
            timeout: Per-RPC timeout.
            _inside_turn: Internal flag. Set to ``True`` when the caller
                already holds the per-server turn lock. Changes the
                behaviour when an in-flight ephemeral is detected: instead
                of raising ``SERVER_BUSY`` (correct when there is no turn
                lock to protect us), the ephemeral is displaced and its
                reader is closed so this thread can install its own
                long-lived client.

        Returns:
            The connected :class:`ACPClient`.
        """
        # Serialize with ``_open_ephemeral_client`` and concurrent
        # ``connect_server`` on other threads via the handshake lock —
        # the re-session branch below also calls
        # ``client.create_session`` which can race the ephemeral path's
        # ``handle.stop()``. Re-entrant: when this path falls through to
        # ``_connect_server_locked`` below, the same thread can acquire
        # the handshake lock again without self-deadlock.
        handshake_lock = self._get_handshake_lock(name)
        with handshake_lock:
            return self._ensure_connected_locked(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
                _inside_turn=_inside_turn,
            )

    def _ensure_connected_locked(
        self,
        name: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
        _inside_turn: bool = False,
    ) -> ACPClient:
        """``ensure_connected`` body — caller must hold the handshake lock."""
        # Refuse service while sticky-fatal AND classify+evict a dead
        # cached subprocess. Shared with ``prompt_once`` so the same
        # recovery contract applies whether the caller is the
        # long-lived session path or the one-shot path reusing an
        # existing long-lived client.
        self._check_cached_client_alive(name)

        effective_cwd, effective_mcp = self._effective_session_params(
            name, cwd=cwd, mcp_servers=mcp_servers,
        )
        client = self._clients.get(name)

        if client is not None:
            # Fast path: valid session + params match → reuse directly.
            if client.connection_info.session_id and self._session_matches(
                client, cwd=effective_cwd, mcp_servers=effective_mcp,
            ):
                return client
            # Either the params diverged (cwd / mcp_servers override) or
            # a prior ``session/new`` failed and cleared session_id.
            # Either way, reuse the existing transport and (re-)create
            # the session — building a second ACPClient on the same
            # handle would spawn a competing reader thread on the one
            # stdout stream. ``ACPClient.create_session`` clears its own
            # session metadata on failure, so the stale-reuse bug from
            # Codex P1 cannot fire on the next attempt.
            #
            # ``client.create_session`` can fail with exactly the same
            # HANDSHAKE_FAIL-eligible error shapes as the greenfield
            # ``connect_server`` path, so feed it through the same
            # classification + fatal-streak accounting. Without this,
            # a warmed server with repeated ``session/new`` failures
            # (e.g. cwd override the agent keeps rejecting) would
            # never trip the documented sticky-fatal contract.
            # Mirror the SERVER_BUSY guard from ``_connect_server_locked``:
            # a re-session on a live transport while another thread holds
            # the turn lock would inject ``session/new`` handshake traffic
            # into an in-flight prompt's stdout stream.  Use non-blocking
            # acquire to avoid an ABBA deadlock (turn lock → handshake lock
            # is the normal order; we already hold the handshake lock here).
            if not _inside_turn:
                _server_lock = self._get_server_lock(name)
                if not _server_lock.acquire(blocking=False):
                    raise AcpClientError(
                        f"server '{name}' has an active turn; "
                        f"ensure_connected cannot re-session a mid-turn client",
                        code=AcpErrorCode.SERVER_BUSY,
                        details={"server": name},
                    )
                _server_lock.release()
            try:
                client.create_session(
                    cwd=effective_cwd,
                    mcp_servers=effective_mcp,
                    timeout=timeout,
                )
            except BaseException as exc:
                if self._reclassify_as_handshake_fail(exc, name):
                    self._note_handshake_failure_and_maybe_fatal(name)
                raise
            # Successful re-session — treat the same as a successful
            # greenfield handshake and clear the streak so a prior
            # isolated failure cannot combine with a future one.
            self._note_handshake_success(name)
            return client
        # No cached long-lived client.  Before spawning a brand-new ACPClient
        # (which starts a competing reader thread on the handle's stdout),
        # check whether a concurrent ``prompt_once`` has already connected
        # this server via an ephemeral client.
        with self._ephemeral_lock:
            eph_client: Optional[ACPClient] = self._ephemeral_clients.get(name)
        if eph_client is not None:
            if not _inside_turn:
                # We do NOT hold the turn lock, so the ephemeral may belong
                # to an active turn.  Refuse rather than corrupt the session.
                raise AcpClientError(
                    f"server '{name}' has an in-flight ephemeral client; "
                    f"send_prompt is fail-fast",
                    code=AcpErrorCode.SERVER_BUSY,
                    details={"server": name},
                )
            # We hold the turn lock.  A concurrent ``prompt_once`` registered
            # this ephemeral but cannot acquire the turn lock (we own it), so
            # it will call ``_rollback_ephemeral_on_busy`` — which also needs
            # the handshake lock we currently hold.  Displace the ephemeral
            # here (under our handshake lock) so the reader stops before we
            # spawn our own, then fall through to the cold-start connect.
            # ``_rollback_ephemeral_on_busy`` is idempotent (identity check +
            # caught exceptions) so the concurrent rollback is harmless.
            with self._ephemeral_lock:
                if self._ephemeral_clients.get(name) is eph_client:
                    self._ephemeral_clients.pop(name, None)
            try:
                eph_client.close()
            except Exception:
                logger.debug(
                    "acp[%s]: error displacing in-flight ephemeral",
                    name, exc_info=True,
                )
        # Forward to the locked connect body. The handshake RLock is already
        # held by the public ``ensure_connected`` wrapper; reentrance is allowed.
        return self._connect_server_locked(
            name, cwd=effective_cwd, mcp_servers=effective_mcp,
            timeout=timeout,
        )

    def _effective_session_params(
        self,
        name: str,
        *,
        cwd: Optional[str],
        mcp_servers: Optional[List[dict]],
    ) -> tuple[str, List[dict]]:
        """Resolve per-call ``cwd`` / ``mcp_servers`` to concrete defaults.

        ``ACPClient.create_session`` substitutes ``handle.config.cwd`` for
        a ``None`` cwd and ``[]`` for ``None`` mcp_servers, so callers that
        compare ``None`` to a stored value would incorrectly reuse a
        session that was previously mutated by an explicit override.
        Resolve up front so both the match check and the re-session call
        see the same effective values.
        """
        handle = self._handles[name]
        effective_cwd = cwd if cwd is not None else handle.config.cwd
        effective_mcp: List[dict] = (
            list(mcp_servers) if mcp_servers is not None else []
        )
        return effective_cwd, effective_mcp

    @staticmethod
    def _session_matches(
        client: ACPClient,
        *,
        cwd: str,
        mcp_servers: List[dict],
    ) -> bool:
        """Whether ``client``'s session was created with the requested params."""
        info = client.connection_info
        if info.session_cwd != cwd:
            return False
        if info.session_mcp_servers_fingerprint != _fingerprint_mcp_servers(
            mcp_servers
        ):
            return False
        return True

    def send_prompt(
        self,
        name: str,
        text: str,
        *,
        timeout: Optional[float] = None,
        interactive: bool = True,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        interaction_policy: Any = None,
    ) -> Dict[str, Any]:
        """Send a prompt to a server, auto-starting if necessary.

        Args:
            name: Server name.
            text: Plain-text user message.
            timeout: Seconds to wait for the turn (RPC only; the
                per-server lock is acquired in fail-fast mode so callers
                never block waiting for another turn to finish).
            interactive: When ``True`` (default), the CLI interaction
                pipeline handles ``session/request_permission`` and
                ``_agentao.cn/ask_user`` via ``WAITING_FOR_USER``.  When
                ``False``, such requests are auto-rejected and the turn
                ultimately raises :class:`AcpInteractionRequiredError`.
            cwd: Per-call working directory.  Forwarded to
                :meth:`ensure_connected`; if it differs from the cached
                session's ``session_cwd``, a fresh session is created.
            mcp_servers: Per-call MCP server list.  Same session-reuse
                semantics as ``cwd``.
            interaction_policy: Per-call override of the non-interactive
                interaction policy. Accepts :class:`InteractionPolicy`
                or the string ``"reject_all"`` / ``"accept_all"``.
                ``None`` falls back to the server's configured
                ``nonInteractivePolicy``. Only consulted when
                ``interactive=False``.

        Returns:
            The ``session/prompt`` result dict.

        Raises:
            AcpClientError(code=SERVER_BUSY): Another turn is already
                active for this server (single-active-turn contract —
                "no queueing"; see ``docs/features/headless-runtime.md``
                §2). Callers should back off and retry, not block a
                worker thread behind a slow / stuck turn.
            AcpInteractionRequiredError: Non-interactive turn that the
                server tried to interrupt for user input.
            AcpClientError: Timeout or transport failure.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        # ``interaction_policy`` is documented as only applying to
        # non-interactive turns, so skip resolution (including the
        # string/type validation inside ``_resolve_interaction_policy``)
        # when ``interactive=True``. Callers that pass a shared options
        # object regardless of mode should not be rejected for supplying
        # a value that would be ignored anyway.
        resolved_policy: Optional[InteractionPolicy] = None
        if not interactive:
            resolved_policy = self._resolve_interaction_policy(
                name, interaction_policy,
            )

        # Pre-warm a fresh connect OUTSIDE the fail-fast turn lock —
        # that way a pending handshake never holds the turn slot and
        # concurrent prompt callers only observe ``SERVER_BUSY`` when a
        # turn is *actually* running. Fresh connect is safe to do
        # outside the turn lock because it installs a brand-new client;
        # it cannot mutate a client another thread's turn is already
        # using. Re-session on an existing cached client IS unsafe
        # outside the turn lock (it would overwrite the cached
        # ``session_id`` mid-turn), so we defer that to after
        # ``lock.acquire`` via ``ensure_connected``.
        #
        # Both checks — no long-lived client *and* no ephemeral client
        # — run under the handshake lock so a concurrent ``prompt_once``
        # ephemeral in flight cannot slip past us and cause
        # ``_connect_server_locked`` to spawn a second ``ACPClient`` +
        # reader on the same handle's stdout.
        try:
            with self._get_handshake_lock(name):
                with self._ephemeral_lock:
                    has_ephemeral = name in self._ephemeral_clients
                if self._clients.get(name) is None and not has_ephemeral:
                    self._connect_server_locked(
                        name, cwd=cwd, mcp_servers=mcp_servers,
                        timeout=timeout,
                    )
        except Exception as exc:
            self._record_last_error(name, exc)
            raise

        # Fail-fast concurrency: mirror ``prompt_once``'s non-blocking
        # acquire. The Week 1 contract is "single active turn per server,
        # no queueing"; blocking here would hang a worker thread behind
        # a slow or stuck turn instead of letting the caller retry or
        # route elsewhere.
        lock = self._get_server_lock(name)
        if not lock.acquire(blocking=False):
            raise AcpClientError(
                f"server '{name}' has an active turn; send_prompt is fail-fast",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )
        try:
            try:
                # Inside the turn lock now — ``ensure_connected`` can
                # safely re-session the cached client if ``cwd`` /
                # ``mcp_servers`` diverged without corrupting a
                # concurrent turn (there can't be one — we hold the
                # lock). The fast path returns immediately.
                client = self.ensure_connected(
                    name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
                    _inside_turn=True,
                )
                result = self._run_turn_on_client(
                    name, client, text,
                    timeout=timeout,
                    interactive=interactive,
                    policy=resolved_policy,
                )
                self._note_successful_turn(name)
                return result
            except Exception as exc:
                self._record_last_error(name, exc)
                raise
        finally:
            lock.release()

    def _run_turn_on_client(
        self,
        name: str,
        client: ACPClient,
        text: str,
        *,
        timeout: Optional[float],
        interactive: bool,
        policy: Optional[InteractionPolicy],
    ) -> Dict[str, Any]:
        """Common turn runner used by both ``send_prompt`` and ``prompt_once``."""
        if interactive:
            ctx = _TurnContext(
                server=name, interactive=True, effective_policy=policy,
            )
            self._install_turn(name, ctx)
            try:
                return client.send_prompt(text, timeout=timeout)
            finally:
                self._clear_turn(name)
        return self._run_non_interactive_turn(
            name, client, text, timeout=timeout, policy=policy,
        )

    def _run_non_interactive_turn(
        self,
        name: str,
        client: ACPClient,
        text: str,
        *,
        timeout: Optional[float],
        policy: Optional[InteractionPolicy],
    ) -> Dict[str, Any]:
        """Run one non-interactive ``session/prompt`` turn.

        Auto-rejection of inbound ``session/request_permission`` and
        ``_agentao.cn/ask_user`` happens in :meth:`_route_server_request`
        by consulting the installed :class:`_TurnContext`.
        """
        ctx = _TurnContext(
            server=name, interactive=False, effective_policy=policy,
        )
        self._install_turn(name, ctx)

        if timeout is None:
            timeout = client._handle.config.request_timeout_ms / 1000.0

        try:
            rid, slot = client.send_prompt_nonblocking(text)
        except AcpClientError:
            self._clear_turn(name)
            raise

        try:
            if not slot.event.wait(timeout=timeout):
                # Timeout: cancel the turn and raise REQUEST_TIMEOUT.
                # discard_pending_slot is idempotent and raise-free, so
                # a single slow/hung turn cannot poison subsequent calls
                # even if cancel_prompt fails midway (e.g. broken pipe).
                try:
                    client.cancel_prompt(rid)
                except Exception:
                    logger.debug(
                        "acp[%s]: cancel_prompt after timeout raised", name,
                        exc_info=True,
                    )
                finally:
                    client.discard_pending_slot(rid)
                raise AcpClientError(
                    f"timeout waiting for session/prompt response (id={rid})",
                    code=AcpErrorCode.REQUEST_TIMEOUT,
                    details={
                        "server": name,
                        "method": "session/prompt",
                        "request_id": rid,
                        "timeout": timeout,
                    },
                )

            # Collect result — may raise AcpRpcError / transport error.
            result = client.finish_prompt(rid, slot)

            # Cancel wins over a latched interaction error.
            if ctx.cancelled:
                return result
            if ctx.interaction_error is not None:
                raise ctx.interaction_error
            return result
        finally:
            self._clear_turn(name)

    def send_prompt_nonblocking(
        self,
        name: str,
        text: str,
        *,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
    ) -> tuple:
        """Send a prompt without blocking.  Returns ``(client, rid, slot)``.

        The caller polls ``slot.event`` and must finalize the turn via
        :meth:`finish_prompt_nonblocking` or :meth:`cancel_prompt_nonblocking`
        — **not** the raw ``ACPClient`` helpers — so the per-server lock
        and ``_active_turns`` slot are released. An interactive
        :class:`_TurnContext` is installed for the duration of the turn so
        concurrent ``send_prompt``/``prompt_once`` calls honor the same
        single-active-turn contract.

        Blocks while acquiring the per-server lock (parity with
        :meth:`send_prompt`). On any failure before returning, the lock
        and turn slot are rolled back.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        # Fresh connect OUTSIDE turn lock — see ``send_prompt`` for the
        # rationale. Re-session on an existing cached client is deferred
        # to after ``lock.acquire`` so it cannot corrupt another thread's
        # turn. The handshake-locked check against ``_ephemeral_clients``
        # prevents spawning a second ``ACPClient`` onto a handle that a
        # concurrent ``prompt_once`` ephemeral is already using.
        try:
            with self._get_handshake_lock(name):
                with self._ephemeral_lock:
                    has_ephemeral = name in self._ephemeral_clients
                if self._clients.get(name) is None and not has_ephemeral:
                    self._connect_server_locked(
                        name, cwd=cwd, mcp_servers=mcp_servers,
                        timeout=timeout,
                    )
        except Exception as exc:
            self._record_last_error(name, exc)
            raise

        lock = self._get_server_lock(name)
        if not lock.acquire(blocking=False):
            raise AcpClientError(
                f"server '{name}' already has an active turn; send_prompt_nonblocking is fail-fast",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )
        turn_installed = False
        try:
            client = self.ensure_connected(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
                _inside_turn=True,
            )
            ctx = _TurnContext(server=name, interactive=True)
            self._install_turn(name, ctx)
            turn_installed = True
            rid, slot = client.send_prompt_nonblocking(text)
            return client, rid, slot
        except BaseException as exc:
            if turn_installed:
                self._clear_turn(name)
            lock.release()
            if isinstance(exc, Exception):
                self._record_last_error(name, exc)
            raise

    def finish_prompt_nonblocking(
        self,
        name: str,
        client: ACPClient,
        rid: int,
        slot: Any,
    ) -> Dict[str, Any]:
        """Finalize a :meth:`send_prompt_nonblocking` turn on success.

        Releases the per-server lock and clears the turn slot in a
        ``finally``, so callers never leak serialization state even if
        ``finish_prompt`` raises.

        Failures propagate after being recorded in the ``last_error``
        snapshot so ``get_status()`` reports them; successful turns
        reset the recovery counters via ``_note_successful_turn`` for
        parity with ``send_prompt`` / ``prompt_once``.
        """
        try:
            try:
                result = client.finish_prompt(rid, slot)
            except Exception as exc:
                self._record_last_error(name, exc)
                raise
            self._note_successful_turn(name)
            return result
        finally:
            self._clear_turn(name)
            try:
                self._get_server_lock(name).release()
            except RuntimeError:
                # Lock already released (e.g., by a prior cancel path).
                logger.debug(
                    "acp[%s]: server lock was not held on finish", name,
                )

    def cancel_prompt_nonblocking(
        self,
        name: str,
        client: ACPClient,
        rid: int,
    ) -> None:
        """Abort a :meth:`send_prompt_nonblocking` turn.

        Guarantees the client-side pending slot is cleared (via
        :meth:`ACPClient.discard_pending_slot`) and the per-server lock
        is released, even if ``cancel_prompt`` fails mid-transport.
        """
        try:
            try:
                client.cancel_prompt(rid)
            except Exception:
                logger.debug(
                    "acp[%s]: cancel_prompt(nonblocking) raised",
                    name, exc_info=True,
                )
            finally:
                client.discard_pending_slot(rid)
        finally:
            self._clear_turn(name)
            try:
                self._get_server_lock(name).release()
            except RuntimeError:
                logger.debug(
                    "acp[%s]: server lock was not held on cancel", name,
                )

    def prompt_once(
        self,
        name: str,
        prompt: str,
        *,
        cwd: Optional[str] = None,
        mcp_servers: Optional[List[dict]] = None,
        timeout: Optional[float] = None,
        interactive: bool = False,
        stop_process: bool = True,
        interaction_policy: Any = None,
    ) -> PromptResult:
        """Run one ACP prompt turn with deterministic cleanup.

        Intended for daemon / workflow runtimes that want a single
        request/response lifecycle rather than a reusable session.

        Concurrency contract (v1):

        * Acquires the per-server lock in **fail-fast** mode; if another
          turn is already active for this server, raises
          ``AcpClientError(code=SERVER_BUSY)``.
        * If no long-lived client exists for ``name``, builds an
          ephemeral client that is **not** registered in
          ``self._clients`` and does not appear in ``get_status()``.
          On exit the ephemeral client is closed.
        * ``stop_process=True`` (default) stops the server subprocess
          on exit, but only when no long-lived client exists for this
          name (otherwise the subprocess is shared and must survive).
        * If a long-lived client already exists for ``name``, it is
          reused; in that case the process is never stopped by this
          call regardless of ``stop_process``.

        Args:
            name: Server name.
            prompt: Plain-text user message.
            cwd: Per-call working directory.
            mcp_servers: Per-call MCP server configs.
            timeout: Seconds to wait for the turn (RPC only).
            interactive: Default ``False``; see :meth:`send_prompt`.
            stop_process: Stop the subprocess on exit when this call
                owns an ephemeral client.
            interaction_policy: Per-call override of the non-interactive
                interaction policy. Accepts :class:`InteractionPolicy`
                or the string ``"reject_all"`` / ``"accept_all"``.
                ``None`` falls back to the server's configured
                ``nonInteractivePolicy``. Only consulted when
                ``interactive=False`` (the default for ``prompt_once``).

        Returns:
            A :class:`PromptResult` with ``stop_reason``, raw payload,
            session id, and effective ``cwd``.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        # ``interaction_policy`` is documented as only applying to
        # non-interactive turns, so skip resolution when
        # ``interactive=True``. Parity with ``send_prompt``.
        resolved_policy: Optional[InteractionPolicy] = None
        if not interactive:
            resolved_policy = self._resolve_interaction_policy(
                name, interaction_policy,
            )

        # Week 4 recovery contract also applies to the ``prompt_once``
        # reuse path: if the cached long-lived client's subprocess
        # died since the last turn, the caller deserves the same
        # classification + rebuild (or fatal raise) as ``send_prompt``
        # gets via ``ensure_connected``. This runs before any lock so
        # a fatal-state server refuses fast without claiming anything.
        try:
            self._check_cached_client_alive(name)
        except Exception as _exc:
            self._record_last_error(name, _exc)
            raise

        effective_cwd, effective_mcp = self._effective_session_params(
            name, cwd=cwd, mcp_servers=mcp_servers,
        )

        # Ephemeral setup is safe to do OUTSIDE the turn lock — an
        # ephemeral client is specific to this call and not visible to
        # any concurrent turn. Re-sessioning a CACHED client is *not*
        # safe outside the lock (it would overwrite shared session
        # state under another running turn), so we defer the
        # cached-client branch to inside the lock.
        #
        # The check-and-create runs under the handshake lock so a
        # concurrent ``send_prompt`` pre-warm or another ``prompt_once``
        # on another thread cannot race our check and cause two
        # ``ACPClient`` readers to attach to the same handle's stdout.
        #
        # If another thread's ``prompt_once`` already has an ephemeral
        # in flight, we cannot safely spawn a second one (duplicate
        # reader) and cannot borrow theirs (single-use per call). Fail
        # fast with ``SERVER_BUSY`` — ``_record_last_error`` filters it
        # so this doesn't clobber any real failure in the store.
        #
        # process_was_running is sampled inside the handshake lock so
        # that a concurrent start_server() / start_all() cannot start the
        # subprocess in the window between sampling and the lock, which
        # would leave process_was_running=False for a process we did not
        # start and cause the cleanup paths to stop a shared daemon.
        client: Optional[ACPClient] = None
        ephemeral_created = False
        process_was_running = True  # safe default: don't stop anything
        try:
            with self._get_handshake_lock(name):
                process_was_running = (
                    handle._proc is not None and handle._proc.poll() is None
                )
                with self._ephemeral_lock:
                    has_ephemeral = name in self._ephemeral_clients
                if has_ephemeral:
                    raise AcpClientError(
                        f"server '{name}' has an active turn; "
                        f"prompt_once is fail-fast",
                        code=AcpErrorCode.SERVER_BUSY,
                        details={"server": name},
                    )
                if self._clients.get(name) is None:
                    client = self._open_ephemeral_client_locked(
                        name,
                        cwd=effective_cwd,
                        mcp_servers=effective_mcp,
                        timeout=timeout,
                    )
                    ephemeral_created = True
                # Else: a long-lived client already exists (possibly
                # installed by a concurrent pre-warm). Leave ``client``
                # as None; the cached-client branch below (inside the
                # turn lock) handles fast-path reuse / re-session.
        except Exception as exc:
            self._record_last_error(name, exc)
            raise

        lock = self._get_server_lock(name)
        if not lock.acquire(blocking=False):
            # Roll back the ephemeral setup we just did — leaving the
            # reader thread attached to the handle's stdout would break
            # the *next* caller's handshake exactly like the stale-
            # reader bug in ``connect_server`` / ``_open_ephemeral_client``.
            # Closing the client and stopping the handle (when we own
            # it) EOFs the reader cleanly. If the ephemeral was the
            # only user of a pre-existing subprocess we leave that
            # subprocess alone on the rare SERVER_BUSY race — the
            # cached-client path on the next attempt will take over.
            if ephemeral_created and client is not None:
                self._rollback_ephemeral_on_busy(
                    name, client, handle, process_was_running,
                )
            raise AcpClientError(
                f"server '{name}' has an active turn; prompt_once is fail-fast",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )

        try:
            try:
                if not ephemeral_created:
                    # Cached-client path. We hold the turn lock now, so
                    # a re-session on ``existing`` cannot corrupt any
                    # concurrent turn.
                    existing = self._clients.get(name)
                    if existing is None:
                        # Rare: cache was evicted (restart_server etc.)
                        # between the pre-check and here. Fall through
                        # to a full ``ensure_connected`` inside the
                        # turn lock.
                        client = self.ensure_connected(
                            name,
                            cwd=effective_cwd,
                            mcp_servers=effective_mcp,
                            timeout=timeout,
                            _inside_turn=True,
                        )
                    else:
                        # Re-check liveness now that we hold the turn lock:
                        # the process may have died in the window between the
                        # early pre-check and here.
                        self._check_cached_client_alive(name)
                        existing = self._clients.get(name)
                        if existing is None:
                            # Died since the pre-check; rebuild.
                            client = self.ensure_connected(
                                name,
                                cwd=effective_cwd,
                                mcp_servers=effective_mcp,
                                timeout=timeout,
                                _inside_turn=True,
                            )
                        elif existing.connection_info.session_id and self._session_matches(
                            existing, cwd=effective_cwd, mcp_servers=effective_mcp,
                        ):
                            client = existing
                        else:
                            # Re-session the cached client — handshake lock
                            # still serializes against concurrent
                            # connect_server / ephemeral setup on other
                            # threads.
                            with self._get_handshake_lock(name):
                                try:
                                    existing.create_session(
                                        cwd=effective_cwd,
                                        mcp_servers=effective_mcp,
                                        timeout=timeout,
                                    )
                                except BaseException as exc:
                                    if self._reclassify_as_handshake_fail(exc, name):
                                        self._note_handshake_failure_and_maybe_fatal(name)
                                    raise
                                self._note_handshake_success(name)
                            client = existing
                raw = self._run_turn_on_client(
                    name, client, prompt,
                    timeout=timeout,
                    interactive=interactive,
                    policy=resolved_policy,
                )
                self._note_successful_turn(name)
                return PromptResult(
                    stop_reason=raw.get("stopReason", "") if isinstance(raw, dict) else "",
                    raw=raw if isinstance(raw, dict) else {},
                    session_id=client.connection_info.session_id,
                    cwd=client.connection_info.session_cwd,
                )
            except Exception as exc:
                self._record_last_error(name, exc)
                raise
        finally:
            if ephemeral_created and client is not None:
                # prompt_once is always one-shot — never promote the
                # ephemeral client to the long-lived cache, regardless of
                # stop_process or process_was_running.  Close the client to
                # stop its reader thread cleanly; a subsequent connect or
                # prompt_once call on the same server will start a fresh
                # reader.  Only stop the subprocess when stop_process=True
                # AND this call was the one that started it.
                keep_process_alive = not stop_process or process_was_running
                with self._get_handshake_lock(name):
                    with self._ephemeral_lock:
                        if self._ephemeral_clients.get(name) is client:
                            self._ephemeral_clients.pop(name, None)
                    try:
                        client.close()
                    except Exception:
                        logger.debug(
                            "acp[%s]: error closing ephemeral client",
                            name, exc_info=True,
                        )
                    if not keep_process_alive and name not in self._clients:
                        try:
                            handle.stop()
                        except Exception:
                            logger.debug(
                                "acp[%s]: error stopping handle",
                                name, exc_info=True,
                                )
            lock.release()

    def _rollback_ephemeral_on_busy(
        self,
        name: str,
        client: ACPClient,
        handle: ACPProcessHandle,
        process_was_running: bool = False,
    ) -> None:
        """Tear down an ephemeral created before we lost the fail-fast
        turn-lock race in :meth:`prompt_once`.

        Mirrors the cleanup ``_open_ephemeral_client`` does on a
        handshake failure: ``client.close()`` + ``handle.stop()`` so the
        reader thread unblocks on stdout EOF and doesn't leak onto the
        next caller's transport. ``handle.stop()`` is guarded by
        ``name not in self._clients`` AND ``not process_was_running``
        because a long-lived client may have been installed while we were
        handshaking, and a pre-existing subprocess should never be torn
        down by a call that did not start it.

        Runs under the handshake lock so a concurrent
        ``send_prompt`` pre-warm / ``prompt_once`` check on another
        thread can never observe the brief window where the ephemeral
        has been popped but the subprocess is still running — that
        window would let them attach a fresh reader to a stdout that
        is about to EOF.
        """
        with self._get_handshake_lock(name):
            try:
                client.close()
            except Exception:
                logger.debug(
                    "acp[%s]: error closing ephemeral during SERVER_BUSY rollback",
                    name, exc_info=True,
                )
            with self._ephemeral_lock:
                if self._ephemeral_clients.get(name) is client:
                    self._ephemeral_clients.pop(name, None)
            if name not in self._clients and not process_was_running:
                try:
                    handle.stop()
                except Exception:
                    logger.debug(
                        "acp[%s]: error stopping handle during SERVER_BUSY rollback",
                        name, exc_info=True,
                    )

    def _open_ephemeral_client(
        self,
        name: str,
        *,
        cwd: Optional[str],
        mcp_servers: Optional[List[dict]],
        timeout: Optional[float],
    ) -> ACPClient:
        """Build an ACPClient for a single :meth:`prompt_once` call.

        The client is stored in :attr:`_ephemeral_clients` only for the
        duration of the call so the notification / server-request
        callbacks can still resolve a client by server name.

        Handshake lock is held across the whole ``handle.start`` →
        ``initialize`` → ``create_session`` sequence (and the failure
        teardown) so a concurrent ``connect_server`` /
        ``ensure_connected`` on another thread cannot race with
        ``handle.stop()`` below.
        """
        handshake_lock = self._get_handshake_lock(name)
        handshake_lock.acquire()
        try:
            return self._open_ephemeral_client_locked(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )
        finally:
            handshake_lock.release()

    def _open_ephemeral_client_locked(
        self,
        name: str,
        *,
        cwd: Optional[str],
        mcp_servers: Optional[List[dict]],
        timeout: Optional[float],
    ) -> ACPClient:
        """``_open_ephemeral_client`` body — caller holds handshake lock."""
        handle = self._handles[name]
        _existing_proc = handle._proc
        _we_started = _existing_proc is None or _existing_proc.poll() is not None
        try:
            handle.start()
        except AcpClientError:
            raise
        except Exception as exc:
            raise AcpClientError(
                f"failed to start ACP server '{name}': {exc}",
                code=AcpErrorCode.PROCESS_START_FAIL,
                details={"server": name},
                cause=exc,
            ) from exc

        def _on_notification(method: str, params: Any) -> None:
            self._route_notification(name, method, params)
            if self._notification_callback is not None:
                self._notification_callback(name, method, params)

        def _on_server_request(method: str, params: Any, request_id: Any) -> None:
            self._route_server_request(name, method, params, request_id)

        client = ACPClient(
            handle,
            notification_callback=_on_notification,
            server_request_callback=_on_server_request,
        )
        with self._ephemeral_lock:
            self._ephemeral_clients[name] = client
        try:
            client.start_reader()
            client.initialize(timeout=timeout)
            client.create_session(
                cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )
        except BaseException as exc:
            # Shared classification + sticky-fatal accounting — same
            # behaviour as the long-lived ``connect_server`` and
            # cached-client ``ensure_connected`` paths so the
            # "2 consecutive handshakes ⇒ fatal" contract holds
            # whichever API the embedder chose.
            if self._reclassify_as_handshake_fail(exc, name):
                self._note_handshake_failure_and_maybe_fatal(name)
            with self._ephemeral_lock:
                if self._ephemeral_clients.get(name) is client:
                    self._ephemeral_clients.pop(name, None)
            try:
                client.close()
            except Exception:
                pass
            # client.close() calls unsubscribe_stdout() which detaches our
            # subscriber — no stale reader remains on the feeder.  Only stop
            # the subprocess if we started it; a pre-warmed server must survive
            # a bad one-shot call (e.g. invalid per-call session params) so
            # subsequent turns can continue on the existing process.
            if _we_started:
                try:
                    handle.stop()
                except Exception:
                    logger.debug(
                        "acp[%s]: stop handle after ephemeral setup failure raised",
                        name, exc_info=True,
                    )
            raise
        # Successful greenfield handshake in the ephemeral/``prompt_once``
        # path — clear the streak for the same reason the long-lived
        # ``connect_server`` and cached-client re-session paths do.
        # Without this, a prior isolated handshake failure would linger
        # at streak=1 and combine with a future unrelated handshake
        # failure into an incorrect sticky-fatal flip.
        self._note_handshake_success(name)
        return client

    def cancel_turn(self, name: str) -> None:
        """Cancel the active turn on a server, if any.

        Sets the cancellation flag on the active turn context (if any)
        so that a latched non-interactive interaction error is suppressed
        in favor of the cancellation outcome. Sends ``session/cancel`` as
        a notification — does not wait for the per-server lock, so it is
        safe to call while a turn is in flight.

        No-op if the server has no client.
        """
        ctx = self._get_active_turn(name)
        if ctx is not None:
            ctx.cancelled = True
        client = self._client_for(name)
        if client is not None:
            client.cancel_active_turn()

    # ------------------------------------------------------------------
    # Inbox (Issue 05)
    # ------------------------------------------------------------------

    # Map ACP methods to message kinds (used for both notifications and requests).
    _NOTIFICATION_KIND_MAP: Dict[str, MessageKind] = {
        "session/update": MessageKind.RESPONSE,
        "session/request_permission": MessageKind.PERMISSION,
        "_agentao.cn/ask_user": MessageKind.INPUT,
    }

    # Methods that represent server-initiated requests needing a response.
    _SERVER_REQUEST_METHODS: set = {
        "session/request_permission",
        "_agentao.cn/ask_user",
    }

    def _route_notification(
        self, server_name: str, method: str, params: Any
    ) -> None:
        """Convert a raw ACP notification into an :class:`InboxMessage`.

        Also registers pending interactions for permission / input requests
        (Issue 10) and transitions the server state to ``WAITING_FOR_USER``.
        """
        kind = self._NOTIFICATION_KIND_MAP.get(method, MessageKind.NOTIFICATION)

        # Extract display text from params.
        text = _extract_display_text(method, params)

        # Resolve session_id from the client if available.
        client = self._client_for(server_name)
        session_id = ""
        if client is not None:
            session_id = client.connection_info.session_id or ""

        # Extract sessionUpdate kind for render-layer filtering.
        _update_kind = ""
        if isinstance(params, dict):
            _upd = params.get("update")
            if isinstance(_upd, dict):
                _update_kind = _upd.get("sessionUpdate", "")

        msg = InboxMessage(
            server=server_name,
            session_id=session_id,
            kind=kind,
            text=text,
            raw=params if isinstance(params, dict) else None,
            update_kind=_update_kind,
        )
        self.inbox.push(msg)

        # Register pending interactions for permission/input requests.
        if kind in (MessageKind.PERMISSION, MessageKind.INPUT):
            interaction = PendingInteraction(
                server=server_name,
                session_id=session_id,
                kind=(
                    InteractionKind.PERMISSION
                    if kind == MessageKind.PERMISSION
                    else InteractionKind.INPUT
                ),
                prompt=text,
                details=params if isinstance(params, dict) else None,
            )
            self.interactions.register(interaction)

            # Transition server to waiting_for_user state.
            handle = self._handles.get(server_name)
            if handle is not None:
                handle._set_state(ServerState.WAITING_FOR_USER)

    def _route_server_request(
        self, server_name: str, method: str, params: Any, request_id: Any
    ) -> None:
        """Handle a server-initiated JSON-RPC request (has both method and id).

        These are requests like ``session/request_permission`` or
        ``_agentao.cn/ask_user`` where the server expects a response.

        Policy:

        * If the active turn is **non-interactive**, the request is
          auto-rejected and an :class:`AcpInteractionRequiredError` is
          latched onto the turn context.  The server state stays ``BUSY``
          so the caller never observes a durable ``WAITING_FOR_USER``.
        * Otherwise the request is registered with the interaction
          registry as before and the handle transitions to
          ``WAITING_FOR_USER`` for CLI-driven resolution.
        """
        # Non-interactive fast path: auto-reject without touching the
        # interaction registry or the handle state.
        ctx = self._get_active_turn(server_name)
        if (
            ctx is not None
            and not ctx.interactive
            and method in self._SERVER_REQUEST_METHODS
        ):
            self._auto_reject_server_request(
                server_name, method, params, request_id, ctx
            )
            return

        kind = self._NOTIFICATION_KIND_MAP.get(method, MessageKind.NOTIFICATION)

        # Extract display text.
        text = _extract_display_text(method, params)

        # Resolve session_id.
        client = self._client_for(server_name)
        session_id = ""
        if client is not None:
            session_id = client.connection_info.session_id or ""

        # Push to inbox for display.
        msg = InboxMessage(
            server=server_name,
            session_id=session_id,
            kind=kind,
            text=text,
            raw=params if isinstance(params, dict) else None,
            update_kind=method,
        )
        self.inbox.push(msg)

        # Register pending interaction with the RPC request id.
        if method in self._SERVER_REQUEST_METHODS:
            interaction = PendingInteraction(
                server=server_name,
                session_id=session_id,
                kind=(
                    InteractionKind.PERMISSION
                    if kind == MessageKind.PERMISSION
                    else InteractionKind.INPUT
                ),
                prompt=text,
                details=params if isinstance(params, dict) else None,
                rpc_request_id=request_id,
            )
            self.interactions.register(interaction)

            handle = self._handles.get(server_name)
            if handle is not None:
                handle._set_state(ServerState.WAITING_FOR_USER)

    def _auto_reject_server_request(
        self,
        server_name: str,
        method: str,
        params: Any,
        request_id: Any,
        ctx: _TurnContext,
    ) -> None:
        """Respond to a server-initiated request during a non-interactive turn.

        Sends the appropriate reject / error response directly over the
        transport, records the ``request_id`` on the turn context for
        diagnostics, and latches the first interaction error so the
        caller sees it when the outstanding ``session/prompt`` RPC
        completes.
        """
        client = self._client_for(server_name)
        if client is None:
            logger.warning(
                "acp[%s]: auto-reject requested but no client", server_name
            )
            return

        text = _extract_display_text(method, params)
        options: List[Dict[str, Any]] = []
        if isinstance(params, dict):
            raw_opts = params.get("options")
            if isinstance(raw_opts, list):
                options = [o for o in raw_opts if isinstance(o, dict)]

        # Prefer the turn's resolved policy (per-call override, or
        # server default captured at turn start) so that per-call
        # overrides land correctly on the running turn. Fall back to
        # the server config only if the context was created without a
        # policy — in practice this only happens via internal callers.
        if ctx.effective_policy is not None:
            policy_mode = ctx.effective_policy.mode
        else:
            server_cfg = self._config.servers.get(server_name)
            policy_mode = (
                server_cfg.non_interactive_policy.mode
                if server_cfg is not None
                else "reject_all"
            )
        approved = False

        try:
            if method == "session/request_permission":
                if policy_mode == "accept_all":
                    approve_option = _select_approve_option(options)
                    if approve_option is not None:
                        client.send_response(
                            request_id,
                            {
                                "outcome": {
                                    "outcome": "selected",
                                    "optionId": approve_option,
                                }
                            },
                        )
                        approved = True
                    else:
                        # No allow-flavored option; fall through to reject.
                        logger.warning(
                            "acp[%s]: accept_all policy but no allow option "
                            "in %s; rejecting",
                            server_name,
                            method,
                        )
                        reject_option = _select_reject_option(options)
                        if reject_option is not None:
                            client.send_response(
                                request_id,
                                {
                                    "outcome": {
                                        "outcome": "selected",
                                        "optionId": reject_option,
                                    }
                                },
                            )
                        else:
                            client.send_response(
                                request_id,
                                {"outcome": {"outcome": "cancelled"}},
                            )
                else:
                    reject_option = _select_reject_option(options)
                    if reject_option is not None:
                        client.send_response(
                            request_id,
                            {
                                "outcome": {
                                    "outcome": "selected",
                                    "optionId": reject_option,
                                }
                            },
                        )
                    else:
                        # No reject-flavored option in the server's list, and
                        # no options at all is not something we can satisfy
                        # with an optionId. Fall back to "cancelled" outcome
                        # (per ACP protocol) so the server doesn't hang.
                        client.send_response(
                            request_id,
                            {"outcome": {"outcome": "cancelled"}},
                        )
            elif method == "_agentao.cn/ask_user":
                # accept_all cannot fabricate a user answer.
                client.send_error_response(
                    request_id,
                    -32001,
                    "non-interactive turn; no user available",
                )
            else:
                # Unknown method in the allowlist — defensive fallback.
                client.send_error_response(
                    request_id,
                    -32601,
                    f"non-interactive turn cannot service '{method}'",
                )
        except Exception:
            logger.exception(
                "acp[%s]: failed to send auto-reject response to %s",
                server_name,
                method,
            )

        ctx.auto_replied_request_ids.add(request_id)
        if not approved and ctx.interaction_error is None:
            ctx.interaction_error = AcpInteractionRequiredError(
                server=server_name,
                method=method,
                prompt=text,
                options=options,
            )

    def flush_inbox(self) -> List[InboxMessage]:
        """Drain and return all pending inbox messages.

        The CLI calls this at safe idle points to display messages.
        """
        return self.inbox.drain()

    # ------------------------------------------------------------------
    # Interaction bridge (Issue 10)
    # ------------------------------------------------------------------

    def _post_interaction_state(self, name: str) -> ServerState:
        """Pick the right state to land in after resolving an interaction.

        If an active turn still owns the terminal ``READY`` transition,
        the handle should go back to ``BUSY`` (the prompt RPC is still
        in flight). Otherwise nothing else will move it off ``BUSY``, so
        the interaction resolution itself must mark it ``READY``.
        """
        return (
            ServerState.BUSY
            if self._get_active_turn(name) is not None
            else ServerState.READY
        )

    def _send_interaction_response(
        self, interaction: PendingInteraction, result: Dict[str, Any]
    ) -> None:
        """Send a JSON-RPC response back to the server for a resolved interaction.

        If the interaction has no ``rpc_request_id`` (e.g., it came from a
        notification rather than a request), this is a no-op.
        """
        if interaction.rpc_request_id is None:
            return
        client = self._client_for(interaction.server)
        if client is None:
            logger.warning(
                "acp: cannot send response for interaction %s — no client for '%s'",
                interaction.request_id,
                interaction.server,
            )
            return
        try:
            client.send_response(interaction.rpc_request_id, result)
        except Exception as exc:
            logger.error(
                "acp: failed to send response for interaction %s: %s",
                interaction.request_id,
                exc,
            )

    def approve_interaction(
        self,
        name: str,
        request_id: str,
        *,
        always: bool = False,
    ) -> bool:
        """Approve a pending permission interaction.

        Args:
            always: If ``True``, send ``allow_always`` so the server
                remembers the decision for subsequent calls.

        Sends a JSON-RPC response back to the server and transitions
        the server state.  Returns ``True`` if the interaction was found
        and resolved.
        """
        interaction = self.interactions.get(request_id)
        if interaction is None or interaction.server != name:
            return False
        options = _extract_options(interaction)
        preferred_kind = "allow_always" if always else "allow_once"
        option_id = (
            _select_option_by_kind(options, preferred_kind)
            or _select_approve_option(options)
            or preferred_kind
        )
        resolved = self.interactions.resolve(
            request_id, {"outcome": "approved", "optionId": option_id}
        )
        if resolved is not None:
            self._send_interaction_response(resolved, {
                "outcome": {
                    "outcome": "selected",
                    "optionId": option_id,
                },
            })
            handle = self._handles.get(name)
            if handle is not None and handle.state == ServerState.WAITING_FOR_USER:
                handle._set_state(self._post_interaction_state(name))
            return True
        return False

    def reject_interaction(
        self,
        name: str,
        request_id: str,
        *,
        always: bool = False,
    ) -> bool:
        """Reject a pending permission interaction.

        Args:
            always: If ``True``, send ``reject_always`` so the server
                remembers the decision for subsequent calls.

        Sends a JSON-RPC response back to the server and transitions
        the handle from ``WAITING_FOR_USER`` back to ``BUSY`` — the
        outstanding ``session/prompt`` RPC still owns the terminal
        ``READY`` / ``FAILED`` transition.  Returns ``True`` if the
        interaction was found and resolved.
        """
        interaction = self.interactions.get(request_id)
        if interaction is None or interaction.server != name:
            return False
        options = _extract_options(interaction)
        preferred_kind = "reject_always" if always else "reject_once"
        option_id = (
            _select_option_by_kind(options, preferred_kind)
            or _select_reject_option(options)
            or preferred_kind
        )
        resolved = self.interactions.resolve(
            request_id, {"outcome": "rejected", "optionId": option_id}
        )
        if resolved is not None:
            self._send_interaction_response(resolved, {
                "outcome": {
                    "outcome": "selected",
                    "optionId": option_id,
                },
            })
            handle = self._handles.get(name)
            if handle is not None and handle.state == ServerState.WAITING_FOR_USER:
                handle._set_state(self._post_interaction_state(name))
            return True
        return False

    def reply_interaction(
        self, name: str, request_id: str, text: str
    ) -> bool:
        """Reply to a pending input interaction with free-form text.

        Sends a JSON-RPC response back to the server and transitions
        the server state.  Returns ``True`` if the interaction was found
        and resolved.
        """
        interaction = self.interactions.get(request_id)
        if interaction is None or interaction.server != name:
            return False
        resolved = self.interactions.resolve(
            request_id, {"outcome": "answered", "text": text}
        )
        if resolved is not None:
            self._send_interaction_response(resolved, {
                "outcome": "answered",
                "text": text,
            })
            handle = self._handles.get(name)
            if handle is not None and handle.state == ServerState.WAITING_FOR_USER:
                handle._set_state(self._post_interaction_state(name))
            return True
        return False

    # ------------------------------------------------------------------
    # Diagnostics (Issue 07)
    # ------------------------------------------------------------------

    def get_server_logs(self, name: str, n: int = 50) -> List[str]:
        """Return the last *n* stderr lines for a server.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        return handle.get_stderr_tail(n)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> List[ServerStatus]:
        """Return a typed status snapshot for every configured server.

        Returns one :class:`ServerStatus` per server registered with the
        manager. ``has_active_turn`` is derived from the manager's active
        turn slot (not from handle state), so it stays ``True`` during
        in-flight interactions that would otherwise surface as
        ``WAITING_FOR_USER`` on the handle.

        Week 2 adds diagnostic fields additively. The four v1 fields
        (``server``, ``state``, ``pid``, ``has_active_turn``) retain
        their Week 1 semantics. New fields — ``active_session_id``,
        ``last_error``, ``last_error_at``, ``inbox_pending``,
        ``interaction_pending``, ``config_warnings`` — are read from
        the authoritative manager-owned stores (client connection info,
        ``_last_errors``, the shared :class:`Inbox`, the interaction
        registry, and ``_config_warnings``). ``last_error_at`` is a
        timezone-aware UTC ``datetime`` whose value is set at the
        moment the error is stored, not at the moment it was raised.
        """
        result: List[ServerStatus] = []
        with self._active_turns_lock:
            active_names = set(self._active_turns.keys())
        with self._last_errors_lock:
            last_errors = dict(self._last_errors)
        # Inbox counts are derived from a peek snapshot — cheap because
        # the default capacity is small (256) and the filter only reads
        # an immutable ``server`` field on each message.
        inbox_by_server: Dict[str, int] = {}
        for msg in self.inbox.peek():
            inbox_by_server[msg.server] = inbox_by_server.get(msg.server, 0) + 1
        for name, handle in self._handles.items():
            # Eagerly revalidate cached process liveness so a crashed
            # server is never reported as "ready" or "busy" when it is
            # actually dead. The probe runs for READY, BUSY, and
            # WAITING_FOR_USER — all states where the process is expected
            # to be alive but may have silently exited.
            if handle.info.state in (
                ServerState.READY,
                ServerState.BUSY,
                ServerState.WAITING_FOR_USER,
            ):
                proc = handle._proc
                if proc is not None and proc.poll() is not None:
                    try:
                        self._check_cached_client_alive(name, count_restart=False)
                    except AcpClientError:
                        pass
            info = handle.info
            active_session_id: Optional[str] = None
            client = self._clients.get(name)
            if client is not None:
                active_session_id = client.connection_info.session_id or None
            if active_session_id is None:
                with self._ephemeral_lock:
                    eph = self._ephemeral_clients.get(name)
                if eph is not None:
                    active_session_id = eph.connection_info.session_id or None
            err = last_errors.get(name)
            # Fall back to the handle-level error (populated by
            # ``ACPProcessHandle._set_state(FAILED, ...)``) when the
            # manager-side store has nothing. Otherwise startup failures
            # that never hit ``_record_last_error`` — ``start_server`` /
            # ``start_all`` exceptions, crashes before any prompt — leave
            # ``state == "failed"`` with ``last_error == None`` on the
            # typed snapshot, forcing embedders back to ``handle.info``.
            # ``last_error_at`` stays ``None`` in the fallback path because
            # the handle does not record a distinct error timestamp.
            if err:
                last_error_msg: Optional[str] = err[0]
                last_error_ts: Optional[datetime] = err[1]
            else:
                last_error_msg = info.last_error
                last_error_ts = None
            result.append(ServerStatus(
                server=name,
                state=info.state.value,
                pid=info.pid,
                has_active_turn=name in active_names,
                active_session_id=active_session_id,
                last_error=last_error_msg,
                last_error_at=last_error_ts,
                inbox_pending=inbox_by_server.get(name, 0),
                interaction_pending=len(self.interactions.list_pending(server=name)),
                config_warnings=list(self._config_warnings.get(name, [])),
            ))
        return result

    # ------------------------------------------------------------------
    # Week 2: readiness classifier
    # ------------------------------------------------------------------

    def readiness(
        self, name: str,
    ) -> Literal["ready", "busy", "failed", "not_ready"]:
        """Return a typed readiness classification for *name*.

        Consumers that only want to know whether to submit a new turn
        should call this rather than string-matching on ``state``. The
        mapping is intentionally coarse and stable; it **is not** a
        replacement for the raw ``state`` — use ``get_status()`` for
        diagnostics.

        Returned values:

        - ``"ready"`` — server is up and has no active turn; safe to
          submit a new ``prompt_once`` / ``send_prompt``.
        - ``"busy"`` — a turn is in flight (manager slot occupied) or
          the handle is in ``BUSY`` / ``WAITING_FOR_USER``.
        - ``"failed"`` — server requires explicit recovery before it will
          accept turns again. This covers: sticky-fatal servers
          (``is_fatal()`` is True), servers whose process never launched
          (e.g. missing executable), and servers whose process has already
          exited while the handle is in ``FAILED``.
        - ``"not_ready"`` — server is still coming up or winding down
          (``CONFIGURED`` / ``STARTING`` / ``INITIALIZING`` /
          ``STOPPING`` / ``STOPPED``), or the handle is in a transient
          ``FAILED`` state with a live process that will self-resolve on
          the next call (e.g. a recoverable ``session/new`` error with a
          bad ``cwd`` override).

        Raises :class:`AcpServerNotFound` if *name* is not configured.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        state = handle.info.state
        if state is ServerState.FAILED:
            # Sticky-fatal: manager gave up; requires explicit recovery.
            with self._recovery_lock:
                is_sticky = name in self._fatal_servers
            if is_sticky:
                return "failed"
            # Process never launched (e.g. missing executable) or has
            # already exited — durable failure, not a transient per-call
            # error that can self-resolve on the next send_prompt.
            proc = handle._proc
            if proc is None or proc.poll() is not None:
                return "failed"
            # Live process in FAILED: transient per-call error (e.g. a
            # bad cwd override on session/new) that will self-resolve.
            return "not_ready"
        with self._active_turns_lock:
            has_active = name in self._active_turns
        if has_active or state in (
            ServerState.BUSY,
            ServerState.WAITING_FOR_USER,
        ):
            # Revalidate: the subprocess may have died mid-turn. If so,
            # report the real state rather than leaving callers stuck on
            # "busy" forever.
            proc = handle._proc
            if proc is not None and proc.poll() is not None:
                try:
                    self._check_cached_client_alive(name, count_restart=False)
                except AcpClientError:
                    return "failed"
                return "not_ready"
            return "busy"
        if state is ServerState.READY:
            proc = handle._proc
            if proc is not None and proc.poll() is not None:
                # Idle-crash guard: handle state lingers at READY until
                # ``_check_cached_client_alive`` runs. Eagerly classify
                # and evict the stale client here so a subsequent
                # ``prompt_once`` / ``ensure_connected`` triggers a
                # transparent restart rather than hitting the dead
                # transport. Fatal crashes raise → "failed". Recoverable
                # crashes evict + bump the counter and return "not_ready"
                # so callers know not to submit; a prompt submission
                # (``prompt_once`` / ``send_prompt``) will restart the
                # server automatically.
                try:
                    self._check_cached_client_alive(name, count_restart=False)
                except AcpClientError:
                    return "failed"
                return "not_ready"
            return "ready"
        return "not_ready"

    def is_ready(self, name: str) -> bool:
        """Shortcut for ``readiness(name) == "ready"``."""
        return self.readiness(name) == "ready"
