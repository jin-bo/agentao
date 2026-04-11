"""ACP client manager — registry of per-server process handles and clients.

:class:`ACPManager` is the single entry point for the CLI and agent layers to
start, stop, query, and communicate with project-local ACP servers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .client import ACPClient
from .config import load_acp_client_config
from .inbox import Inbox, InboxMessage, MessageKind
from .interaction import InteractionKind, InteractionRegistry, PendingInteraction
from .models import AcpClientConfig, AcpServerConfig, ServerState
from .process import ACPProcessHandle

logger = logging.getLogger("agentao.acp_client")


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

        for name, server_cfg in config.servers.items():
            self._handles[name] = ACPProcessHandle(name, server_cfg)

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
            raise KeyError(f"no ACP server named '{name}'")

        # Start process if not already running.
        handle.start()

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
        client.start_reader()
        client.initialize(timeout=timeout)
        client.create_session(
            cwd=cwd, mcp_servers=mcp_servers, timeout=timeout
        )

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
            raise KeyError(f"no ACP server named '{name}'")
        handle.start()

    def stop_server(self, name: str) -> None:
        """Stop a single server by name.

        Raises:
            KeyError: If *name* is not a configured server.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise KeyError(f"no ACP server named '{name}'")
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
            raise KeyError(f"no ACP server named '{name}'")
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
        """Return an existing client, or auto-connect if needed.

        This is the lazy counterpart of :meth:`connect_server`.  If the
        server already has a live client with a session, it is returned
        directly.  Otherwise the full ``start → initialize → session/new``
        flow is executed transparently.

        Args:
            name: Server name from config.
            cwd: Working directory for the ACP session (used only on first connect).
            mcp_servers: MCP server configs (used only on first connect).
            timeout: Per-RPC timeout.

        Returns:
            The connected :class:`ACPClient`.
        """
        client = self._clients.get(name)
        if client is not None and client.connection_info.session_id:
            return client
        return self.connect_server(
            name, cwd=cwd, mcp_servers=mcp_servers, timeout=timeout
        )

    def send_prompt(
        self,
        name: str,
        text: str,
        *,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send a prompt to a server, auto-starting if necessary.

        Args:
            name: Server name.
            text: Plain-text user message.
            timeout: Seconds to wait for the turn.

        Returns:
            The ``session/prompt`` result dict.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise KeyError(f"no ACP server named '{name}'")
        client = self.ensure_connected(name, timeout=timeout)
        return client.send_prompt(text, timeout=timeout)

    def send_prompt_nonblocking(
        self,
        name: str,
        text: str,
        *,
        timeout: Optional[float] = None,
    ) -> tuple:
        """Send a prompt without blocking.  Returns ``(client, rid, slot)``.

        The caller polls ``slot.event`` and calls
        ``client.finish_prompt(rid, slot)`` when ready.
        """
        handle = self._handles.get(name)
        if handle is None:
            raise KeyError(f"no ACP server named '{name}'")
        client = self.ensure_connected(name, timeout=timeout)
        rid, slot = client.send_prompt_nonblocking(text)
        return client, rid, slot

    def cancel_turn(self, name: str) -> None:
        """Cancel the active turn on a server, if any.

        No-op if the server has no client or no active turn.
        """
        client = self._clients.get(name)
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
        client = self._clients.get(server_name)
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
        We route them through the same inbox/interaction pipeline as
        notifications, but also store the ``rpc_request_id`` so we can
        send a JSON-RPC response when the user resolves the interaction.
        """
        kind = self._NOTIFICATION_KIND_MAP.get(method, MessageKind.NOTIFICATION)

        # Extract display text.
        text = _extract_display_text(method, params)

        # Resolve session_id.
        client = self._clients.get(server_name)
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

    def flush_inbox(self) -> List[InboxMessage]:
        """Drain and return all pending inbox messages.

        The CLI calls this at safe idle points to display messages.
        """
        return self.inbox.drain()

    # ------------------------------------------------------------------
    # Interaction bridge (Issue 10)
    # ------------------------------------------------------------------

    def _send_interaction_response(
        self, interaction: PendingInteraction, result: Dict[str, Any]
    ) -> None:
        """Send a JSON-RPC response back to the server for a resolved interaction.

        If the interaction has no ``rpc_request_id`` (e.g., it came from a
        notification rather than a request), this is a no-op.
        """
        if interaction.rpc_request_id is None:
            return
        client = self._clients.get(interaction.server)
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
        option_id = "allow_always" if always else "allow_once"
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
                handle._set_state(ServerState.BUSY)
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
        the server state.  Returns ``True`` if the interaction was found
        and resolved.
        """
        interaction = self.interactions.get(request_id)
        if interaction is None or interaction.server != name:
            return False
        option_id = "reject_always" if always else "reject_once"
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
                handle._set_state(ServerState.READY)
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
                handle._set_state(ServerState.BUSY)
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
            raise KeyError(f"no ACP server named '{name}'")
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
