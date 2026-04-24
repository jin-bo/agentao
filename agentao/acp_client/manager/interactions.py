"""Server-initiated request routing and user-driven interaction resolution.

Provides :class:`InteractionsMixin` for :class:`ACPManager`. Handles the
notification / server-request path that feeds the inbox + interaction
registry, and the approve / reject / reply surface consumed by the CLI.

Also owns the per-call :class:`InteractionPolicy` resolution shared
between ``send_prompt`` and ``prompt_once``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..client import AcpInteractionRequiredError
from ..inbox import InboxMessage, MessageKind
from ..interaction import InteractionKind, PendingInteraction
from ..models import (
    INTERACTION_POLICY_MODES,
    InteractionPolicy,
    ServerState,
)
from .helpers import (
    _extract_display_text,
    _extract_options,
    _select_approve_option,
    _select_option_by_kind,
    _select_reject_option,
    logger,
)
from .turns import _TurnContext


class InteractionsMixin:
    """Inbox / interaction routing for :class:`ACPManager`."""

    # ------------------------------------------------------------------
    # Policy resolution (shared by send_prompt / prompt_once)
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

    # ------------------------------------------------------------------
    # Notification / server-request routing (Issue 05)
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
