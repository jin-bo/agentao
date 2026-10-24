"""Never send an MCP request its caller has stopped waiting for (#571).

A request can sit between the SDK and the network for a long time: its auth
flow may wait for a shared OAuth refresh, or for one after a 401. A timeout
or cancel ends the caller, not the SDK task that sends the POST, so without
this the server still ran a tool call the model was already told failed.

How it works:

* The caller's side (``McpClient._send_on``) runs each request in a task that
  carries a :class:`Dispatch` in :data:`current_dispatch`, and marks it
  settled once it no longer waits — answered, timed out, cancelled, closed.
* :class:`SettledGuard` wraps the connection's HTTP transport, the one point
  every send and resend passes. A JSON-RPC *request* whose dispatch is
  settled gets a local ``202 Accepted`` marked :data:`NOT_SENT` and never
  reaches the network. Notifications pass, so ``notifications/cancelled``
  still goes out.

Why a local response and not an exception: mcp 2.x runs each POST as a child
of the transport's task group and catches nothing around the send, so any
exception there — ``CancelledError`` included — cancels the whole group and
fails every other request on the connection. A ``202`` is an answer both
transports already handle: Streamable HTTP resolves the request with an
error (its waiter is gone), SSE expects one for every message.

mcp 1.x runs each POST in the transport's own context, not the sender's, so
the guard sees no dispatch there and sends as before. The fix holds on 2.x
(``_compat.SENDS_IN_SENDER_CONTEXT``).
"""

from __future__ import annotations

import contextvars
import json
import logging
from typing import Any, Optional

from ._compat import httpx_for_mcp

logger = logging.getLogger(__name__)

#: ``Response.extensions`` key on the local answer to a request not sent.
NOT_SENT = "agentao_mcp_not_sent"


class Dispatch:
    """Whether the caller of one MCP request still waits for its answer."""

    __slots__ = ("settled",)

    def __init__(self) -> None:
        self.settled = False


#: The :class:`Dispatch` of the request being sent, ``None`` outside one.
current_dispatch: "contextvars.ContextVar[Optional[Dispatch]]" = contextvars.ContextVar(
    "agentao_mcp_dispatch", default=None
)


def not_sent(response: Any) -> bool:
    """Whether ``response`` is the guard's local answer, not the server's."""
    return bool(getattr(response, "extensions", {}).get(NOT_SENT))


def _is_request(request: Any) -> bool:
    try:
        message = json.loads(request.content)
    except (ValueError, TypeError, RuntimeError):
        # Not a JSON body, or a streamed one: not a message this guards.
        return False
    return isinstance(message, dict) and "id" in message and "method" in message


class SettledGuard(httpx_for_mcp.AsyncBaseTransport):
    """An HTTP transport that drops requests whose caller stopped waiting."""

    def __init__(self, inner: Any, server_name: str) -> None:
        self._inner = inner
        self._server_name = server_name

    async def handle_async_request(self, request: Any) -> Any:
        dispatch = current_dispatch.get()
        if dispatch is not None and dispatch.settled and _is_request(request):
            logger.info(
                f"MCP '{self._server_name}': request not sent, its caller stopped waiting"
            )
            return httpx_for_mcp.Response(202, request=request, extensions={NOT_SENT: True})
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def guard_client(client: Any, server_name: str) -> Any:
    """Wrap ``client``'s transports in a :class:`SettledGuard`; return ``client``.

    ``create_mcp_http_client`` takes no ``transport=``, so the guard replaces
    the built client's ``_transport`` and each proxy mount (``_mounts``: a
    proxy from the environment sends through a mount, not ``_transport``).
    Without them (a future httpx), the client is returned unguarded, with a
    warning: it then behaves as before.
    """
    inner = getattr(client, "_transport", None)
    mounts = getattr(client, "_mounts", None)
    if not isinstance(inner, httpx_for_mcp.AsyncBaseTransport) or not isinstance(mounts, dict):
        logger.warning(
            f"MCP '{server_name}': HTTP client has no transport to guard; "
            "a request cancelled while it waits may still be sent (#571)"
        )
        return client

    def wrap(transport: Any) -> Any:
        if transport is None or isinstance(transport, SettledGuard):
            return transport
        return SettledGuard(transport, server_name)

    client._transport = wrap(inner)
    client._mounts = {pattern: wrap(transport) for pattern, transport in mounts.items()}
    return client
