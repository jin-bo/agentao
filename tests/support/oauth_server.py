"""A fake OAuth-protected MCP origin for the MCP OAuth tests (docs/design/mcp-oauth.md §12).

One ``httpx`` mock handler plays every party: the protected MCP endpoint
(JSON-mode Streamable HTTP), Protected Resource Metadata, authorization-server
metadata, dynamic client registration, ``/authorize`` and ``/token``. Only the
socket is replaced — the SDK's transport, ``ClientSession`` and (at login) its
``OAuthClientProvider`` are the real ones, which is where the two SDK majors
differ in how an auth failure surfaces.

:func:`patched` routes the three places agentao opens an HTTP client — the
connection, a refresh's token request, a login's request — through the
handler, and stubs the content-type preflight (it uses plain ``httpx`` on the
real network). It also records whether that preflight saw an ``Authorization``.
"""

from __future__ import annotations

import asyncio
import json
import itertools
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from agentao.mcp._compat import httpx_for_mcp

BASE = "https://mcp.example"
MCP_URL = f"{BASE}/mcp"
SSE_URL = f"{BASE}/sse"


class FakeOAuthOrigin:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.valid_tokens: set = set()
        self.refresh_tokens: Dict[str, str] = {}  # refresh token -> client_id
        self.codes: Dict[str, str] = {}
        self.clients: Dict[str, Dict[str, Any]] = {}
        self._ids = itertools.count(1)
        #: ``ok`` | ``invalid_grant`` | ``503`` | ``drop`` | ``slow``
        self.token_mode = "ok"
        self.token_delay = 0.0
        #: Issue a rotated refresh token on refresh (else omit it).
        self.rotate_refresh = True
        self.expires_in: Optional[int] = 3600
        self.bare_401 = False
        self.insufficient_scope = False
        self.tool_delay = 0.0
        #: Called at the start of each refresh-grant request (a hook for "another
        #: process wrote the record meanwhile").
        self.on_refresh: Optional[Any] = None
        #: Answer the next request carrying a session id with 404, as a
        #: restarted server does for a session it no longer knows.
        self.expire_session_once = False
        #: Send a successful refresh response in small chunks this far apart —
        #: each within any read timeout, the whole well past it.
        self.token_trickle: Optional[float] = None
        #: Whether tokens issued by a *refresh* are accepted by the MCP endpoint
        #: (False: the server has revoked the client, not just the token).
        self.refreshed_tokens_valid = True
        #: Called with the bearer token of each MCP POST, before it is judged.
        self.on_mcp_auth: Optional[Any] = None
        #: Refuse ``tools/call`` of this tool name with a scope 403 whose JSON
        #: body arrives ``deny_body_delay`` seconds after its headers.
        self.scope_denied_tool: Optional[str] = None
        self.deny_body_delay = 0.0
        self.token_requests: List[Dict[str, str]] = []
        self.registrations = 0
        self.authorize_requests: List[Dict[str, str]] = []
        self.mcp_auth_headers: List[Optional[str]] = []
        # Legacy SSE transport: one event queue per open GET stream.
        self._sse_queues: Dict[str, "asyncio.Queue[Optional[bytes]]"] = {}

    # -- helpers for tests -------------------------------------------------

    def issue(self, client_id: str = "seed", *, valid: bool = True) -> Tuple[str, str]:
        n = next(self._ids)
        access, refresh = f"access-{n}-xxxxxxxx", f"refresh-{n}-xxxxxxxx"
        with self.lock:
            if valid:
                self.valid_tokens.add(access)
            self.refresh_tokens[refresh] = client_id
        return access, refresh

    def revoke_access(self) -> None:
        with self.lock:
            self.valid_tokens.clear()

    def revoke_all(self) -> None:
        with self.lock:
            self.valid_tokens.clear()
            self.refresh_tokens.clear()

    def authorize(self, authorization_url: str) -> Tuple[str, Optional[str]]:
        """What a browser would do: follow ``/authorize`` and read the redirect."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorization_url).query).items()}
        self.authorize_requests.append(query)
        code = f"code-{next(self._ids)}-xxxxxxxx"
        self.codes[code] = query.get("client_id", "")
        return code, query.get("state")

    # -- the handler -------------------------------------------------------

    async def handle(self, request: Any) -> Any:
        url = urlsplit(str(request.url))
        path = url.path
        if path == "/mcp":
            return await self._mcp(request)
        if path == "/sse":
            return self._sse_open(request)
        if path == "/messages":
            return await self._sse_post(request)
        if path.startswith("/.well-known/oauth-protected-resource"):
            return self._json(200, {"resource": MCP_URL, "authorization_servers": [BASE]})
        if path.startswith("/.well-known/oauth-authorization-server") or path.startswith(
            "/.well-known/openid-configuration"
        ):
            return self._json(
                200,
                {
                    "issuer": BASE,
                    "authorization_endpoint": f"{BASE}/authorize",
                    "token_endpoint": f"{BASE}/token",
                    "registration_endpoint": f"{BASE}/register",
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        if path == "/register":
            body = json.loads(request.content or b"{}")
            client_id = f"client-{next(self._ids)}"
            self.registrations += 1
            info = {**body, "client_id": client_id, "token_endpoint_auth_method": "none"}
            self.clients[client_id] = info
            return self._json(201, info)
        if path == "/token":
            return await self._token(request)
        return httpx_for_mcp.Response(404)

    @staticmethod
    def _json(status: int, body: Any, headers: Optional[Dict[str, str]] = None) -> Any:
        return httpx_for_mcp.Response(
            status,
            content=json.dumps(body).encode(),
            headers={"content-type": "application/json", **(headers or {})},
        )

    async def _token(self, request: Any) -> Any:
        form = {k: v[0] for k, v in parse_qs((request.content or b"").decode()).items()}
        self.token_requests.append(form)
        if form.get("grant_type") == "authorization_code":
            client_id = self.codes.pop(form.get("code", ""), None)
            if client_id is None:
                return self._json(400, {"error": "invalid_grant"})
            access, refresh = self.issue(client_id)
            return self._json(200, self._token_body(access, refresh))
        if form.get("grant_type") != "refresh_token":
            return self._json(400, {"error": "unsupported_grant_type"})
        if self.on_refresh is not None:
            self.on_refresh()
        if self.token_delay:
            await asyncio.sleep(self.token_delay)
        if self.token_mode == "503":
            return httpx_for_mcp.Response(503)
        if self.token_mode == "drop":
            raise httpx_for_mcp.RemoteProtocolError("connection dropped", request=request)
        with self.lock:
            client_id = self.refresh_tokens.pop(form.get("refresh_token", ""), None)
        if self.token_mode == "invalid_grant" or client_id is None:
            return self._json(400, {"error": "invalid_grant"})
        access, refresh = self.issue(client_id, valid=self.refreshed_tokens_valid)
        if self.token_trickle is not None:
            data = json.dumps(self._token_body(access, refresh)).encode()
            pause = self.token_trickle

            async def trickle():
                for i in range(0, len(data), 8):
                    await asyncio.sleep(pause)
                    yield data[i:i + 8]

            return httpx_for_mcp.Response(
                200, headers={"content-type": "application/json"}, content=trickle()
            )
        if not self.rotate_refresh:
            with self.lock:  # the old refresh token stays usable
                self.refresh_tokens[form["refresh_token"]] = client_id
            return self._json(200, self._token_body(access, None))
        return self._json(200, self._token_body(access, refresh))

    def _token_body(self, access: str, refresh: Optional[str]) -> Dict[str, Any]:
        body: Dict[str, Any] = {"access_token": access, "token_type": "Bearer", "scope": "a"}
        if self.expires_in is not None:
            body["expires_in"] = self.expires_in
        if refresh is not None:
            body["refresh_token"] = refresh
        return body

    def _unauthorized(self, request: Any) -> Optional[Any]:
        auth = request.headers.get("authorization")
        self.mcp_auth_headers.append(auth)
        token = auth[len("Bearer "):] if auth and auth.startswith("Bearer ") else None
        if self.on_mcp_auth is not None:
            self.on_mcp_auth(token)
        if token is not None and token in self.valid_tokens:
            return None
        headers = (
            {}
            if self.bare_401
            else {
                "www-authenticate": 'Bearer resource_metadata="'
                f'{BASE}/.well-known/oauth-protected-resource/mcp", scope="a"'
            }
        )
        return httpx_for_mcp.Response(401, headers=headers)

    def _sse_open(self, request: Any) -> Any:
        refused = self._unauthorized(request)
        if refused is not None:
            return refused
        session = f"s{next(self._ids)}"
        queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()
        self._sse_queues[session] = queue
        queue.put_nowait(f"event: endpoint\ndata: /messages?session_id={session}\n\n".encode())

        async def events():
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        return httpx_for_mcp.Response(
            200, headers={"content-type": "text/event-stream"}, content=events()
        )

    async def _sse_post(self, request: Any) -> Any:
        refused = self._unauthorized(request)
        if refused is not None:
            return refused
        session = parse_qs(urlsplit(str(request.url)).query).get("session_id", [""])[0]
        queue = self._sse_queues.get(session)
        if queue is None:
            return httpx_for_mcp.Response(404)
        message = json.loads(request.content or b"{}")
        reply = await self._rpc(message)
        if reply is not None:
            queue.put_nowait(f"event: message\ndata: {json.dumps(reply)}\n\n".encode())
        return httpx_for_mcp.Response(202)

    async def _rpc(self, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method, request_id = message.get("method"), message.get("id")
        if request_id is None:
            return None
        if method == "initialize":
            result: Any = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-oauth", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            result = {"content": [{"type": "text", "text": "ok"}]}
        else:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": f"Unknown method {method}"}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    async def _mcp(self, request: Any) -> Any:
        if request.method == "GET":
            self.mcp_auth_headers.append(request.headers.get("authorization"))
            return httpx_for_mcp.Response(405)
        if request.method == "DELETE":
            return httpx_for_mcp.Response(200)
        refused = self._unauthorized(request)
        if refused is not None:
            return refused
        if self.expire_session_once and request.headers.get("mcp-session-id"):
            self.expire_session_once = False
            return httpx_for_mcp.Response(404)
        message = json.loads(request.content or b"{}")
        method, request_id = message.get("method"), message.get("id")
        if request_id is None:
            return httpx_for_mcp.Response(202)
        if method == "initialize":
            result: Any = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake-oauth", "version": "1"},
            }
            return self._json(
                200, {"jsonrpc": "2.0", "id": request_id, "result": result},
                headers={"mcp-session-id": "session-1"},
            )
        if method == "tools/list":
            result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            if message.get("params", {}).get("name") == "boom":
                return self._json(200, {"jsonrpc": "2.0", "id": request_id,
                                        "error": {"code": -32602, "message": "boom: bad arguments"}})
            if self.scope_denied_tool and message.get("params", {}).get("name") == self.scope_denied_tool:
                delay = self.deny_body_delay
                body = json.dumps({"jsonrpc": "2.0", "id": request_id,
                                   "error": {"code": -32001, "message": "insufficient scope"}}).encode()

                async def late_body():
                    await asyncio.sleep(delay)
                    yield body

                return httpx_for_mcp.Response(
                    403,
                    headers={"content-type": "application/json",
                             "www-authenticate": 'Bearer error="insufficient_scope", scope="write"'},
                    content=late_body(),
                )
            if self.insufficient_scope:
                return httpx_for_mcp.Response(
                    403,
                    headers={
                        "www-authenticate": 'Bearer error="insufficient_scope", scope="a write"'
                    },
                )
            if self.tool_delay:
                await asyncio.sleep(self.tool_delay)
            result = {"content": [{"type": "text", "text": "ok"}]}
        else:
            return self._json(
                200,
                {"jsonrpc": "2.0", "id": request_id,
                 "error": {"code": -32601, "message": f"Unknown method {method}"}},
            )
        return self._json(200, {"jsonrpc": "2.0", "id": request_id, "result": result})


class FakeLoginUI:
    """Plays the browser: ``open`` follows ``/authorize`` on the fake origin."""

    def __init__(self, origin: FakeOAuthOrigin, port: int = 34567, *, fail: Optional[BaseException] = None):
        self.origin = origin
        self.port = port
        self.fail = fail
        self.prepared_with: List[Optional[int]] = []
        self.opened: List[str] = []
        self.closed = 0
        self._result: Optional[Tuple[str, Optional[str], Optional[str]]] = None

    async def prepare(self, preferred_port: Optional[int]) -> str:
        self.prepared_with.append(preferred_port)
        return f"http://localhost:{self.port}/callback/svr"

    async def open(self, authorization_url: str) -> None:
        self.opened.append(authorization_url)
        code, state = self.origin.authorize(authorization_url)
        self._result = (code, state, None)

    async def wait(self) -> Tuple[str, Optional[str], Optional[str]]:
        if self.fail is not None:
            raise self.fail
        assert self._result is not None
        return self._result

    async def close(self) -> None:
        self.closed += 1


@contextmanager
def patched(origin: FakeOAuthOrigin):
    """Route every agentao HTTP client through ``origin``; record the preflight."""
    transport = httpx_for_mcp.MockTransport(origin.handle)
    preflight_headers: List[Dict[str, str]] = []

    def connection_client(headers=None, timeout=None, auth=None):
        return httpx_for_mcp.AsyncClient(
            headers=headers, timeout=timeout, auth=auth, transport=transport,
            follow_redirects=True,
        )

    def token_client(timeout):
        return httpx_for_mcp.AsyncClient(
            timeout=httpx_for_mcp.Timeout(timeout), transport=transport
        )

    def login_client(headers, timeout, auth):
        return httpx_for_mcp.AsyncClient(
            headers=headers, timeout=httpx_for_mcp.Timeout(timeout), auth=auth,
            transport=transport, follow_redirects=True,
        )

    async def preflight(self, url, headers):
        preflight_headers.append(dict(headers or {}))

    from agentao.mcp.client import McpClient
    from mcp.client import sse as sse_module

    real_sse_client = sse_module.sse_client

    def sse_client(url, headers=None, timeout=5.0, sse_read_timeout=300.0, auth=None):
        # The SDK's own sse_client, with only its HTTP client factory swapped.
        return real_sse_client(
            url, headers=headers, timeout=timeout, sse_read_timeout=sse_read_timeout,
            auth=auth, httpx_client_factory=connection_client,
        )

    with patch("agentao.mcp.client.sse_client", sse_client), patch(
        "agentao.mcp.client.create_mcp_http_client", connection_client
    ), patch(
        "agentao.mcp.oauth._token_http_client", token_client
    ), patch("agentao.mcp.oauth._login_http_client", login_client), patch.object(
        McpClient, "_preflight_content_type", preflight
    ):
        yield preflight_headers


def query_of(url: str) -> Dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


__all__ = ["BASE", "MCP_URL", "SSE_URL", "FakeOAuthOrigin", "FakeLoginUI", "patched", "query_of", "urlencode"]
