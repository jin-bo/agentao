"""ACP client manager — registry of per-server process handles and clients.

:class:`ACPManager` is the single entry point for the CLI and agent layers to
start, stop, query, and communicate with project-local ACP servers.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

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
from .models import AcpClientConfig, AcpServerConfig, PromptResult, ServerState
from .process import ACPProcessHandle

logger = logging.getLogger("agentao.acp_client")


@dataclass
class _TurnContext:
    """Per-turn state tracked by :class:`ACPManager` for non-interactive calls.

    A single slot exists per named server (``ACPManager._active_turns``).
    Created when ``send_prompt`` starts a turn, removed when the turn
    completes. Non-interactive turns consult this context to auto-reject
    inbound server-initiated requests without exposing ``WAITING_FOR_USER``.
    """

    server: str
    interactive: bool
    interaction_error: Optional[AcpInteractionRequiredError] = None
    auto_replied_request_ids: Set[Any] = field(default_factory=set)
    cancelled: bool = False


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
        # threading.Lock, not an asyncio.Lock).
        self._server_locks: Dict[str, threading.Lock] = {}
        self._server_locks_meta = threading.Lock()

        # Single active turn slot per named server.
        self._active_turns: Dict[str, _TurnContext] = {}
        self._active_turns_lock = threading.Lock()

        # Ephemeral clients created by ``prompt_once``. They do NOT appear
        # in ``self._clients`` or ``get_status()``; the separate map only
        # exists so callback routing (notifications, server requests) can
        # still find the active client for a given server name.
        self._ephemeral_clients: Dict[str, ACPClient] = {}
        self._ephemeral_lock = threading.Lock()

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
                handle.start()
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
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

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
        try:
            client.start_reader()
            client.initialize(timeout=timeout)
            client.create_session(
                cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )
        except AcpInteractionRequiredError:
            raise
        except AcpRpcError as exc:
            exc.code = AcpErrorCode.HANDSHAKE_FAIL  # type: ignore[assignment]
            exc.error_code = AcpErrorCode.HANDSHAKE_FAIL
            exc.details.setdefault("server", name)
            raise
        except AcpClientError as exc:
            if exc.code in (
                AcpErrorCode.PROTOCOL_ERROR,
                AcpErrorCode.TRANSPORT_DISCONNECT,
                AcpErrorCode.REQUEST_TIMEOUT,
            ):
                exc.code = AcpErrorCode.HANDSHAKE_FAIL
                exc.details.setdefault("server", name)
            raise

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

    def start_server(self, name: str) -> None:
        """Start a single server by name.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        handle.start()

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

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)
        handle.restart()

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
    ) -> ACPClient:
        """Return an existing client, or auto-connect / re-session as needed.

        Session reuse is conditional on matching per-call ``cwd`` and
        ``mcp_servers``. When either differs from the cached session, the
        old client is closed and a fresh session is created.

        Args:
            name: Server name from config.
            cwd: Working directory for the ACP session.
            mcp_servers: MCP server configs for the session.
            timeout: Per-RPC timeout.

        Returns:
            The connected :class:`ACPClient`.
        """
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
            client.create_session(
                cwd=effective_cwd,
                mcp_servers=effective_mcp,
                timeout=timeout,
            )
            return client
        return self.connect_server(
            name, cwd=effective_cwd, mcp_servers=effective_mcp, timeout=timeout,
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
    ) -> Dict[str, Any]:
        """Send a prompt to a server, auto-starting if necessary.

        Args:
            name: Server name.
            text: Plain-text user message.
            timeout: Seconds to wait for the turn (covers the RPC, not
                time spent waiting on the per-server lock).
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

        Returns:
            The ``session/prompt`` result dict.

        Raises:
            AcpInteractionRequiredError: Non-interactive turn that the
                server tried to interrupt for user input.
            AcpClientError: Timeout or transport failure.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        lock = self._get_server_lock(name)
        lock.acquire()
        try:
            client = self.ensure_connected(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )
            return self._run_turn_on_client(
                name, client, text, timeout=timeout, interactive=interactive,
            )
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
    ) -> Dict[str, Any]:
        """Common turn runner used by both ``send_prompt`` and ``prompt_once``."""
        if interactive:
            ctx = _TurnContext(server=name, interactive=True)
            self._install_turn(name, ctx)
            try:
                return client.send_prompt(text, timeout=timeout)
            finally:
                self._clear_turn(name)
        return self._run_non_interactive_turn(
            name, client, text, timeout=timeout,
        )

    def _run_non_interactive_turn(
        self,
        name: str,
        client: ACPClient,
        text: str,
        *,
        timeout: Optional[float],
    ) -> Dict[str, Any]:
        """Run one non-interactive ``session/prompt`` turn.

        Auto-rejection of inbound ``session/request_permission`` and
        ``_agentao.cn/ask_user`` happens in :meth:`_route_server_request`
        by consulting the installed :class:`_TurnContext`.
        """
        ctx = _TurnContext(server=name, interactive=False)
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

        lock = self._get_server_lock(name)
        lock.acquire()
        turn_installed = False
        try:
            client = self.ensure_connected(
                name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout,
            )
            ctx = _TurnContext(server=name, interactive=True)
            self._install_turn(name, ctx)
            turn_installed = True
            rid, slot = client.send_prompt_nonblocking(text)
            return client, rid, slot
        except BaseException:
            if turn_installed:
                self._clear_turn(name)
            lock.release()
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
        """
        try:
            return client.finish_prompt(rid, slot)
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

        Returns:
            A :class:`PromptResult` with ``stop_reason``, raw payload,
            session id, and effective ``cwd``.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise AcpServerNotFound(name)

        lock = self._get_server_lock(name)
        if not lock.acquire(blocking=False):
            raise AcpClientError(
                f"server '{name}' has an active turn; prompt_once is fail-fast",
                code=AcpErrorCode.SERVER_BUSY,
                details={"server": name},
            )

        client: Optional[ACPClient] = None
        ephemeral_created = False
        # Track whether we started the subprocess so the cleanup path
        # doesn't tear down a shared server that was already running
        # (e.g. via ``start_all()`` / ``start_server()``).
        process_was_running = (
            handle._proc is not None and handle._proc.poll() is None
        )
        effective_cwd, effective_mcp = self._effective_session_params(
            name, cwd=cwd, mcp_servers=mcp_servers,
        )
        try:
            existing = self._clients.get(name)
            if existing is not None:
                # A long-lived client is cached. Reuse its transport —
                # spawning a second ACPClient on the same handle would
                # start a competing reader thread on one stdout stream
                # and misroute replies/requests. If ``session_id`` has
                # been cleared (e.g. a previous ``create_session`` call
                # failed) or the params diverge from the current session,
                # rerun ``session/new`` on this same client.
                if existing.connection_info.session_id and self._session_matches(
                    existing, cwd=effective_cwd, mcp_servers=effective_mcp,
                ):
                    client = existing
                else:
                    existing.create_session(
                        cwd=effective_cwd,
                        mcp_servers=effective_mcp,
                        timeout=timeout,
                    )
                    client = existing
            else:
                client = self._open_ephemeral_client(
                    name,
                    cwd=effective_cwd,
                    mcp_servers=effective_mcp,
                    timeout=timeout,
                )
                ephemeral_created = True

            raw = self._run_turn_on_client(
                name, client, prompt, timeout=timeout, interactive=interactive,
            )
            return PromptResult(
                stop_reason=raw.get("stopReason", "") if isinstance(raw, dict) else "",
                raw=raw if isinstance(raw, dict) else {},
                session_id=client.connection_info.session_id,
                cwd=client.connection_info.session_cwd,
            )
        finally:
            if ephemeral_created and client is not None:
                # When the subprocess must outlive this call — either
                # because the caller asked (``stop_process=False``) or
                # because a shared server was already running before this
                # call — the ephemeral client owns the subprocess's pipes
                # and reader thread. Closing it here would orphan the
                # process and force the next call to spawn a competing
                # ``ACPClient`` on the same handle (duplicate reader,
                # misrouted responses). Promote it to the long-lived
                # cache instead.
                keep_process_alive = not stop_process or process_was_running
                if keep_process_alive:
                    with self._ephemeral_lock:
                        if self._ephemeral_clients.get(name) is client:
                            self._ephemeral_clients.pop(name, None)
                    if name not in self._clients:
                        self._clients[name] = client
                else:
                    # stop_process=True AND this call started the
                    # subprocess — tear everything down.
                    try:
                        client.close()
                    except Exception:
                        logger.debug(
                            "acp[%s]: error closing ephemeral client",
                            name, exc_info=True,
                        )
                    with self._ephemeral_lock:
                        if self._ephemeral_clients.get(name) is client:
                            self._ephemeral_clients.pop(name, None)
                    # Only stop the subprocess if this call started it
                    # (covered by the keep_process_alive branch above).
                    if name not in self._clients:
                        try:
                            handle.stop()
                        except Exception:
                            logger.debug(
                                "acp[%s]: error stopping handle",
                                name, exc_info=True,
                            )
            lock.release()

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
        """
        handle = self._handles[name]
        # Snapshot before start() so the cleanup path below only stops the
        # subprocess if this call started it — not a shared server brought
        # up by start_server()/start_all().
        process_was_running = (
            handle._proc is not None and handle._proc.poll() is None
        )
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
            # Re-classify handshake/session-setup failures as
            # HANDSHAKE_FAIL for embedders branching on ``err.code``.
            if isinstance(exc, AcpRpcError):
                exc.code = AcpErrorCode.HANDSHAKE_FAIL  # type: ignore[assignment]
                exc.error_code = AcpErrorCode.HANDSHAKE_FAIL
                exc.details.setdefault("server", name)
            elif (
                isinstance(exc, AcpClientError)
                and not isinstance(exc, AcpInteractionRequiredError)
                and exc.code in (
                    AcpErrorCode.PROTOCOL_ERROR,
                    AcpErrorCode.TRANSPORT_DISCONNECT,
                    AcpErrorCode.REQUEST_TIMEOUT,
                )
            ):
                exc.code = AcpErrorCode.HANDSHAKE_FAIL
                exc.details.setdefault("server", name)
            with self._ephemeral_lock:
                if self._ephemeral_clients.get(name) is client:
                    self._ephemeral_clients.pop(name, None)
            try:
                client.close()
            except Exception:
                pass
            # Handshake / session setup failed. Only stop the subprocess
            # if this call actually started it — a shared server started
            # via start_server()/start_all() must not be torn down by a
            # transient prompt_once() handshake failure. And if a
            # long-lived client now owns the handle, leave it alone.
            if name not in self._clients and not process_was_running:
                try:
                    handle.stop()
                except Exception:
                    logger.debug(
                        "acp[%s]: stop handle after ephemeral setup failure raised",
                        name, exc_info=True,
                    )
            raise
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

        server_cfg = self._config.servers.get(server_name)
        policy = (
            server_cfg.non_interactive_policy
            if server_cfg is not None
            else "reject_all"
        )
        approved = False

        try:
            if method == "session/request_permission":
                if policy == "accept_all":
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

    def get_status(self) -> List[Dict[str, Any]]:
        """Return a CLI-friendly status snapshot for every server."""
        result: List[Dict[str, Any]] = []
        for name, handle in self._handles.items():
            info = handle.info
            pending_interactions = self.interactions.list_pending(server=name)
            result.append({
                "name": name,
                "state": info.state.value,
                "pid": info.pid,
                "last_error": info.last_error,
                "last_activity": info.last_activity,
                "description": handle.config.description,
                "inbox_pending": self.inbox.pending_count,
                "interactions_pending": len(pending_interactions),
                "stderr_lines": len(handle.get_stderr_tail(200)),
            })
        return result
