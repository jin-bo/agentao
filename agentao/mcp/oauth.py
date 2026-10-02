"""OAuth for remote MCP servers (docs/design/mcp-oauth.md).

Two halves, deliberately unequal:

* **Steady state** — :class:`StoredTokenAuth`, an ``httpx.Auth`` (``httpx2``
  on mcp 2.x) attached to every OAuth-eligible URL connection. It attaches the
  stored access token, refreshes it shortly before expiry or once on a 401,
  and records a *verdict* the client reads at its two exits. It never
  constructs the SDK's ``OAuthClientProvider``, so it has no code path to a
  browser, a callback or a terminal (§5.2).
* **Login** — :func:`login`, explicit and rare, runs the SDK provider once
  against a host-supplied UI and writes the record.

Why the verdict is a recorded value and not an exception: by the time an auth
failure leaves the SDK's transport, mcp 2.0 has replaced it with
``"Server returned an error response"`` — no status, no headers — so the
auth object is the only place the 401 is still visible (§5.3 step 2).
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import itertools
import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import Any, AsyncGenerator, Dict, Optional, Protocol, Tuple

from ._compat import (
    SDK_BINDS_ISSUER,
    AuthBase,
    httpx_for_mcp,
    make_callback_result,
    provider_context,
)
from .oauth_store import OAuthRecord, OAuthRuntime

logger = logging.getLogger(__name__)

#: Refresh this long before the stored expiry (§5.3 step 1).
REFRESH_SKEW_S = 60.0

#: Outer bound on a whole login; the UI has its own shorter wait (§7: 300 s).
LOGIN_TIMEOUT_S = 600.0

NEEDS_AUTH = "needs_auth"
REFRESH_FAILED = "refresh_failed"

_BEARER_CHALLENGE = re.compile(r"(?i)(?:^|,)\s*bearer\b")
_INSUFFICIENT_SCOPE = re.compile(r'(?i)\berror\s*=\s*"?insufficient_scope"?')
_SCOPE_PARAM = re.compile(r'(?i)\bscope\s*=\s*"([^"]*)"')
#: Which auth flow (one per HTTP request) is running. A context variable, so a
#: verdict reached inside the shielded refresh task — created from the flow,
#: hence carrying its context — still names the request it belongs to.
_current_flow: "contextvars.ContextVar[Optional[int]]" = contextvars.ContextVar(
    "agentao_mcp_oauth_flow", default=None
)

#: Token-endpoint errors that mean the grant itself is dead (RFC 6749 §5.2).
_REJECTED_GRANT_ERRORS = ("invalid_grant", "invalid_client", "unauthorized_client")


class OAuthLoginError(RuntimeError):
    """A login could not complete; the message says what to do."""


@dataclass(frozen=True)
class AuthVerdict:
    """What the auth object concluded about the server's last refusal.

    ``kind`` is :data:`NEEDS_AUTH` (a login is required) or
    :data:`REFRESH_FAILED` (a refresh failed for a reason a login would not
    fix — the network, a 5xx; reported as an ordinary error). ``stamp`` is the
    record file's mtime when the verdict was reached, so a caller can tell
    whether a login has happened since.
    """

    kind: str
    message: str
    stamp: Optional[float] = None
    #: When it was reached (the auth object's counter) and by which request.
    #: A request's success clears only verdicts older than the request, or its
    #: own — never one a *concurrent* request reached while it was in flight,
    #: which that request's caller has yet to read.
    gen: int = field(default=0, compare=False)
    owner: Optional[int] = field(default=None, compare=False)
    #: The credential itself was refused (a 401 after recovery, a rejected
    #: grant, a logout) — as opposed to a scope refusal, which says nothing
    #: about whether the token is good for other calls.
    credential_refused: bool = field(default=False, compare=False)


def login_hint(server_name: str) -> str:
    return (
        f"MCP server '{server_name}' needs login — run '/mcp login {server_name}' "
        f"(or 'agentao mcp login {server_name}' from a shell)"
    )


def has_bearer_challenge(response: Any) -> bool:
    return bool(_BEARER_CHALLENGE.search(response.headers.get("www-authenticate", "")))


def _insufficient_scope(response: Any) -> Optional[str]:
    header = response.headers.get("www-authenticate", "")
    if not _INSUFFICIENT_SCOPE.search(header):
        return None
    match = _SCOPE_PARAM.search(header)
    return match.group(1) if match else ""


def _token_http_client(timeout: float) -> Any:
    """The client a refresh's token request goes through.

    Not the MCP connection's client: that one follows redirects (a token POST
    must not be re-sent to another origin) and carries a read timeout sized
    for tool calls. A module-level function so tests can replace the socket.
    """
    return httpx_for_mcp.AsyncClient(
        timeout=httpx_for_mcp.Timeout(timeout), follow_redirects=False
    )


def _login_http_client(headers: Dict[str, str], timeout: float, auth: Any) -> Any:
    """The client a login's request goes through: the SDK's own factory."""
    from mcp.client.streamable_http import create_mcp_http_client

    return create_mcp_http_client(
        headers=headers, timeout=httpx_for_mcp.Timeout(timeout), auth=auth
    )


class StoredTokenAuth(AuthBase):  # type: ignore[misc,valid-type]
    """Steady-state auth for one connection (docs/design/mcp-oauth.md §5.3).

    Built once per connect, so its verdict starts clear on every new session.
    Without a record it sends no ``Authorization`` and only observes: a 401
    carrying a ``Bearer`` challenge means the server wants a login.
    """

    def __init__(self, server_name: str, server_url: str, runtime: OAuthRuntime):
        self.server_name = server_name
        self.server_url = server_url
        self.runtime = runtime
        self.verdict: Optional[AuthVerdict] = None
        # The record file's stamp for each credential this object has used, so
        # a refusal is recorded against the credential that was refused — not
        # against whatever a login wrote while the request was in flight. The
        # ``None`` key is the stamp read when no usable record was found.
        self._stamps: Dict[Optional[str], Optional[float]] = {}
        self._record: Optional[OAuthRecord] = self._load()
        self._gen = 0
        self._flows = itertools.count(1)

    # -- verdicts ---------------------------------------------------------

    def _load(self) -> Optional[OAuthRecord]:
        """Load the record, remembering the stamp of the file it came from.

        The stamp is read *before* the file: a write in between leaves the
        stamp older than the content, which can only cause one extra
        reconnect later, never a missed one.
        """
        store = self.runtime.store
        stamp = store.stamp(self.server_url)
        record = store.load(self.server_url)
        self._stamps[record.access_token if record is not None else None] = stamp
        return record

    def _current(self) -> Optional[OAuthRecord]:
        """The cached record, reloaded first if the file changed or went away.

        One ``stat`` per request. Without it a logout — from another manager,
        another server alias on the same URL, or another process — left this
        connection attaching a token that may still be valid, and a login as
        a different account left it acting as the previous one.
        """
        cached = self._record
        key = cached.access_token if cached is not None else None
        if key not in self._stamps or self.runtime.store.stamp(self.server_url) != self._stamps[key]:
            self._record = self._load()
        return self._record

    def _refused_here(self, flow: int) -> bool:
        verdict = self.verdict
        return verdict is not None and verdict.owner == flow and verdict.kind == NEEDS_AUTH

    def _set_verdict(
        self, kind: str, message: str, stamp: Optional[float], *, credential_refused: bool = False
    ) -> None:
        # A refusal outranks a failed refresh, the same way it outlives a
        # success (see ``_succeeded``): the refused request's caller may not
        # have read it yet, and a concurrent request whose refresh merely
        # failed says nothing about that refusal.
        current = self.verdict
        if kind == REFRESH_FAILED and current is not None and current.kind == NEEDS_AUTH:
            return
        self._gen += 1
        self.verdict = AuthVerdict(
            kind,
            message,
            stamp,
            gen=self._gen,
            owner=_current_flow.get(),
            credential_refused=credential_refused,
        )

    def _needs_auth(
        self, refused: Optional[str], message: Optional[str] = None, *, scope: bool = False
    ) -> None:
        """Record that the credential ``refused`` (``None``: no credential) was refused.

        The verdict carries that credential's stamp, so a record written since
        — a login, here or in another process — reads as a change and is tried.
        """
        self._set_verdict(
            NEEDS_AUTH,
            message or login_hint(self.server_name),
            self._stamps.get(refused),
            credential_refused=not scope,
        )

    def _known_refused(self, record: Optional[OAuthRecord]) -> bool:
        """Whether ``record`` is the very credential a refusal was recorded against.

        Then neither its token nor its refresh token is worth sending again —
        until the file changes (a login), which gives it a different stamp.
        """
        verdict = self.verdict
        return (
            record is not None
            and verdict is not None
            and verdict.kind == NEEDS_AUTH
            and verdict.credential_refused
            and verdict.stamp is not None
            and verdict.stamp == self._stamps.get(record.access_token)
        )

    def _refresh_failed(self, why: str) -> None:
        self._set_verdict(
            REFRESH_FAILED,
            f"MCP server '{self.server_name}': refreshing its OAuth token failed ({why})",
            None,
        )

    def _succeeded(self, flow: int, started_at: int) -> None:
        """A request got through: drop a verdict this success disproves.

        That is a verdict older than the request, or one the request reached
        itself (a refresh that failed before a retry that worked). A verdict a
        concurrent request reached meanwhile stays: its caller has not read it
        yet, and this success says nothing about that request.

        Only a ``REFRESH_FAILED`` verdict is ever cleared this way. A
        ``NEEDS_AUTH`` refusal stays until a new connect rebuilds this object:
        its caller may still be reading the error body when another request
        starts and succeeds, and once read it moves the whole server to
        ``NEEDS_AUTH`` anyway, so nothing is left for it to mislabel.
        """
        verdict = self.verdict
        if (
            verdict is not None
            and verdict.kind == REFRESH_FAILED
            and (verdict.gen <= started_at or verdict.owner == flow)
        ):
            self.verdict = None

    # -- the httpx flow ---------------------------------------------------

    @staticmethod
    def _attach(request: Any, record: Optional[OAuthRecord]) -> Optional[str]:
        if record is None:
            return None
        request.headers["Authorization"] = f"Bearer {record.access_token}"
        return record.access_token

    async def async_auth_flow(self, request: Any) -> AsyncGenerator[Any, Any]:
        flow = next(self._flows)
        started_at = self._gen
        _current_flow.set(flow)
        record = self._current()
        known_refused = self._known_refused(record)
        if known_refused:
            # Refused already, and nothing has been written since: asking the
            # token endpoint again would only spend another request on a grant
            # the server rejected, and the old token belongs to a dead
            # credential. Send none; the 401 says the rest.
            record = None
        # At most one token request per request: a 401 after a refresh that
        # already failed here only re-reads the record, so an unreachable
        # token endpoint is not asked twice for the same request.
        tried_refresh = False
        if record is not None and record.refresh_token and record.expires_within(REFRESH_SKEW_S):
            tried_refresh = True
            refreshed = await self._refresh(record, forced=False)
            if refreshed is not None:
                record = refreshed
            elif self._refused_here(flow) or self._known_refused(record):
                # Terminal: logged out while we waited, or the grant was
                # rejected. The old token may still be inside its validity
                # window, but it belongs to a credential that is gone or
                # revoked — send none. Only a *transient* failure keeps it.
                record = None
        sent = self._attach(request, record)
        response = yield request

        if response.status_code == 401:
            challenged = has_bearer_challenge(response)
            retry = await self._recover(sent, may_refresh=not tried_refresh)
            if retry is not None:
                sent = self._attach(request, retry)
                response = yield request
            if response.status_code == 401:
                if sent is not None or challenged or has_bearer_challenge(response):
                    # A transient refresh failure of this request stays one:
                    # the 401 is the expired token it could not replace.
                    # This request's own verdict stands: a transient refresh
                    # failure stays one (the 401 is the expired token it could
                    # not replace), and a refusal it reached already carries
                    # the stamp of the credential that was refused.
                    # A request sent without a token because its credential
                    # was already refused leaves that refusal (and its stamp)
                    # as it is.
                    #
                    # Nor does a request that sent no token over an existing
                    # refusal: its 401 is that refusal's consequence, and
                    # re-recording it under "no credential" would drop the
                    # refused credential's stamp — after which the next request
                    # no longer recognises it and sends it again.
                    own = self.verdict
                    explained = sent is None and own is not None and own.kind == NEEDS_AUTH
                    if (own is None or own.owner != flow) and not known_refused and not explained:
                        self._needs_auth(sent)
                return
            # The retry's answer is classified like a first answer: a refreshed
            # token can still be refused for scope (403), or succeed.

        if response.status_code == 403:
            scope = _insufficient_scope(response)
            if scope is not None:
                wanted = f" scope '{scope}'" if scope else " more scope"
                self._needs_auth(
                    sent,
                    scope=True,
                    message=f"MCP server '{self.server_name}' requires{wanted}, which this "
                    "version of agentao cannot request; logging in again asks only "
                    "for what the server's 401 challenge names, so it may not clear "
                    "this error"
                )
            return

        if response.status_code < 400:
            self._succeeded(flow, started_at)

    async def _recover(self, sent: Optional[str], *, may_refresh: bool = True) -> Optional[OAuthRecord]:
        """After a 401: a token someone else wrote, else one refresh, else ``None``."""
        latest = self._load()
        if latest is None:
            self._record = None
            return None
        if self._known_refused(latest):
            return None  # the credential this connection already saw refused
        if latest.access_token != sent:
            # Rotated by another request or process, or a login landed.
            self._record = latest
            return latest
        if not latest.refresh_token or not may_refresh:
            return None
        refreshed = await self._refresh(latest, forced=True)
        if refreshed is not None and refreshed.access_token != sent:
            return refreshed
        return None

    # -- refresh ----------------------------------------------------------

    async def _refresh(self, seen: OAuthRecord, *, forced: bool) -> Optional[OAuthRecord]:
        try:
            result = await self.runtime.exclusive(
                self.server_url, lambda: self._refresh_locked(seen, forced=forced)
            )
        except Exception as e:  # lock timeout and the like: an ordinary failure
            self._refresh_failed(str(e) or type(e).__name__)
            return None
        if result is not None:
            self._record = result
        return result

    async def _refresh_locked(self, seen: OAuthRecord, *, forced: bool) -> Optional[OAuthRecord]:
        """Runs holding the record's locks, shielded from the caller's cancel."""
        store = self.runtime.store
        # Under the record's lock nobody else writes, so stamp and content agree.
        current = self._load()
        if current is None:
            self._needs_auth(None)  # logged out meanwhile
            return None
        if self._known_refused(current):
            # A request that waited for this lock behind the one whose grant
            # was just rejected: the same dead credential, not worth a second
            # token request. The earlier refusal stands as it is.
            return None
        if current.access_token != seen.access_token:
            return current  # another request or process already rotated it
        if not forced and not current.expires_within(REFRESH_SKEW_S):
            return current
        if not current.refresh_token:
            self._needs_auth(current.access_token)
            return None

        data: Dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": current.refresh_token,
        }
        if current.resource:
            data["resource"] = current.resource
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        _apply_client_auth(current.client_info, data, headers)

        async def exchange() -> Any:
            async with _token_http_client(self.runtime.token_timeout) as http:
                response = await http.post(current.token_endpoint, data=data, headers=headers)
                await response.aread()
                return response

        try:
            # An elapsed-time bound, not only httpx's per-operation timeouts: a
            # server trickling its answer would otherwise hold the record lock
            # past the bound ``wait_critical`` and the lock waiters rely on.
            response = await asyncio.wait_for(exchange(), timeout=self.runtime.token_timeout)
        except asyncio.TimeoutError:
            self._refresh_failed(f"no answer within {self.runtime.token_timeout:g}s")
            return None
        except Exception as e:
            self._refresh_failed(type(e).__name__)
            return None

        if response.status_code in (400, 401):
            error = _json_field(response, "error")
            if error in _REJECTED_GRANT_ERRORS:
                logger.info(f"MCP '{self.server_name}': token refresh rejected ({error})")
                self._needs_auth(current.access_token)
                return None
        if response.status_code != 200:
            self._refresh_failed(f"HTTP {response.status_code}")
            return None

        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            self._refresh_failed("the token endpoint's response was not a token")
            return None
        # An omitted ``token_type`` reads as Bearer, as the SDK's ``OAuthToken``
        # does at login; only a named other type is refused.
        token_type = body.get("token_type", "Bearer")
        if not isinstance(token_type, str) or token_type.lower() != "bearer":
            self._refresh_failed(f"unsupported token type {token_type!r}")
            return None

        merged = merge_token_response(current, body)
        store.save(merged)
        self._stamps[merged.access_token] = store.stamp(self.server_url)
        logger.info(f"MCP '{self.server_name}': OAuth token refreshed")
        return merged


def merge_token_response(record: OAuthRecord, body: Dict[str, Any], now: Optional[float] = None) -> OAuthRecord:
    """Fold a refresh response into a record — merged, never replaced (§5.3 step 3).

    An omitted ``refresh_token`` keeps the old one (RFC 6749 §6: the server
    need not rotate); an omitted ``scope`` keeps the granted one (§5.1); an
    omitted ``expires_in`` means the expiry is unknown.
    """
    expires_at: Optional[float] = None
    expires_in = body.get("expires_in")
    try:
        if expires_in is not None and not isinstance(expires_in, bool):
            expires_at = (now if now is not None else time.time()) + float(expires_in)
    except (TypeError, ValueError):
        expires_at = None
    refresh_token = body.get("refresh_token")
    scope = body.get("scope")
    return replace(
        record,
        access_token=body["access_token"],
        token_type="Bearer",
        expires_at=expires_at,
        refresh_token=refresh_token if isinstance(refresh_token, str) and refresh_token else record.refresh_token,
        scope=scope if isinstance(scope, str) else record.scope,
    )


def _apply_client_auth(client_info: Dict[str, Any], data: Dict[str, str], headers: Dict[str, str]) -> None:
    """Client authentication at the token endpoint, as the SDK does it."""
    import base64
    from urllib.parse import quote

    client_id = client_info.get("client_id")
    secret = client_info.get("client_secret")
    method = client_info.get("token_endpoint_auth_method")
    if client_id:
        data["client_id"] = client_id
    if method == "client_secret_basic" and secret and client_id:
        credentials = f"{quote(client_id, safe='')}:{quote(secret, safe='')}"
        headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode()
    elif method == "client_secret_post" and secret:
        data["client_secret"] = secret


def _json_field(response: Any, name: str) -> Optional[str]:
    try:
        body = response.json()
    except ValueError:
        return None
    value = body.get(name) if isinstance(body, dict) else None
    return value if isinstance(value, str) else None


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class OAuthLoginUI(Protocol):
    """What a host supplies so a login can reach a browser (§5.1).

    ``prepare`` binds the callback listener and returns the actual redirect
    URI; ``open`` shows the user the authorization URL; ``wait`` returns
    ``(code, state, iss)`` from the redirect; ``close`` releases the listener
    and is called on every exit.
    """

    async def prepare(self, preferred_port: Optional[int]) -> str: ...

    async def open(self, authorization_url: str) -> None: ...

    async def wait(self) -> Tuple[str, Optional[str], Optional[str]]: ...

    async def close(self) -> None: ...


class _LoginStorage:
    """The SDK's ``TokenStorage`` for one login.

    ``get_tokens`` always answers ``None``: loading the old token would let the
    handshake succeed with it and the login silently do nothing (§5.5 step 3).
    """

    def __init__(self, client_info: Any):
        self.client_info = client_info
        self.tokens: Any = None

    async def get_tokens(self) -> Any:
        return None

    async def set_tokens(self, tokens: Any) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> Any:
        return self.client_info

    async def set_client_info(self, client_info: Any) -> None:
        self.client_info = client_info


def _port_of(uri: Any) -> Optional[int]:
    from urllib.parse import urlsplit

    try:
        return urlsplit(str(uri)).port
    except ValueError:
        return None


def _stored_redirect_uris(client_info: Dict[str, Any]) -> list:
    return [str(u) for u in (client_info.get("redirect_uris") or [])]


def choose_registration(
    settings: Dict[str, Any], stored: Dict[str, Any], redirect_uri: str
) -> Optional[Dict[str, Any]]:
    """Which client registration to offer the SDK, decided before discovery (§5.5 step 2).

    Returns the client-information dict to offer, or ``None`` to let the SDK
    register afresh. Raises :class:`OAuthLoginError` when a configured client
    was registered with a different redirect URI.
    """
    client_id = settings.get("client_id")
    if client_id:
        if (
            stored.get("client_id") == client_id
            and _stored_redirect_uris(stored)
            and redirect_uri not in _stored_redirect_uris(stored)
        ):
            raise OAuthLoginError(
                f"the callback address changed to {redirect_uri}, which client "
                f"'{client_id}' was not registered with; set 'oauth.callback_port' "
                "in this server's config to the port it was registered with"
            )
        secret = settings.get("client_secret")
        return {
            "client_id": client_id,
            **({"client_secret": secret} if secret else {}),
            "redirect_uris": [redirect_uri],
            # RFC 7591's default for a confidential client; a public one has none.
            "token_endpoint_auth_method": "client_secret_basic" if secret else "none",
        }
    if not stored or not stored.get("client_id"):
        return None
    if redirect_uri not in _stored_redirect_uris(stored):
        return None
    # Offered only where the SDK will check it against the current AS, and only
    # when it carries an issuer: the SDK reads an empty issuer as matching every
    # authorization server (§3.1 caveats).
    if not SDK_BINDS_ISSUER or not stored.get("issuer"):
        return None
    return stored


async def login(
    name: str,
    config: Dict[str, Any],
    settings: Dict[str, Any],
    ui: OAuthLoginUI,
    runtime: OAuthRuntime,
    *,
    timeout: float = LOGIN_TIMEOUT_S,
) -> OAuthRecord:
    """Run the SDK's authorization-code flow once and commit the record (§5.5).

    The only place an ``OAuthClientProvider`` is constructed (§5.2).
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata

    from .client import _MCP_CONTENT_TYPES, _with_default_user_agent
    from .config import resolve_transport

    url = config["url"]
    transport = resolve_transport(config)
    store = runtime.store
    existing = store.load(url)
    stored_info = dict(existing.client_info) if existing else {}

    preferred_port = settings.get("callback_port")
    if preferred_port is None and stored_info:
        uris = _stored_redirect_uris(stored_info)
        preferred_port = _port_of(uris[0]) if uris else None

    try:
        redirect_uri = await ui.prepare(preferred_port)
        offered = choose_registration(settings, stored_info, redirect_uri)
        storage = _LoginStorage(
            OAuthClientInformationFull.model_validate(offered) if offered else None
        )

        metadata: Dict[str, Any] = {
            "redirect_uris": [redirect_uri],
            "client_name": "agentao",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        if "application_type" in OAuthClientMetadata.model_fields:
            metadata["application_type"] = "native"

        async def redirect_handler(authorization_url: str) -> None:
            await ui.open(authorization_url)

        async def callback_handler() -> Any:
            code, state, iss = await ui.wait()
            return make_callback_result(code, state, iss)

        kwargs: Dict[str, Any] = {}
        if "timeout" in inspect.signature(OAuthClientProvider.__init__).parameters:
            kwargs["timeout"] = timeout
        provider = OAuthClientProvider(
            server_url=url,
            client_metadata=OAuthClientMetadata.model_validate(metadata),
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            **kwargs,
        )

        headers = _with_default_user_agent(config.get("headers"))
        status = await asyncio.wait_for(
            _authorized_request(url, transport, headers, provider, _MCP_CONTENT_TYPES),
            timeout=timeout,
        )
        if storage.tokens is None:
            if 200 <= status < 300:
                raise OAuthLoginError(f"MCP server '{name}' did not ask for authorization")
            raise OAuthLoginError(f"MCP server '{name}' answered HTTP {status} without starting a login")
        if status in (401, 403):
            raise OAuthLoginError(
                f"MCP server '{name}' still refused the new token (HTTP {status})"
            )

        context = provider_context(provider)
        metadata_obj = context["oauth_metadata"]
        issuer = context["auth_server_url"] or (
            str(metadata_obj.issuer) if metadata_obj is not None else None
        )
        if not context["token_endpoint"]:
            raise OAuthLoginError("the authorization server published no token endpoint")
        client_info = context["client_info"] or storage.client_info
        tokens = storage.tokens
        record = OAuthRecord(
            server_url=url,
            issuer=issuer,
            token_endpoint=context["token_endpoint"],
            access_token=tokens.access_token,
            token_type="Bearer",
            resource=context["resource"],
            token_endpoint_auth_methods=list(
                getattr(metadata_obj, "token_endpoint_auth_methods_supported", None) or []
            ),
            client_info=client_info.model_dump(mode="json", exclude_none=True) if client_info else {},
            expires_at=context["token_expiry_time"],
            refresh_token=tokens.refresh_token,
            scope=tokens.scope,
        )

        async def commit() -> None:
            store.save(record)

        await runtime.exclusive(url, commit)
        logger.info(f"MCP '{name}': OAuth login stored")
        return record
    finally:
        try:
            await ui.close()
        except Exception as e:  # pragma: no cover - a UI's own cleanup failing
            logger.warning(f"MCP '{name}': closing the login UI failed: {e}")


async def _authorized_request(
    url: str, transport: str, headers: Dict[str, str], provider: Any, content_types: Tuple[str, ...]
) -> int:
    """One request through the provider; its 401 is what starts the flow.

    Only the status is read — the body is never consumed — so a server that
    answers in SSE mode does not hold the login open.
    """
    async with _login_http_client(headers, 30.0, provider) as http:
        if transport == "sse":
            async with http.stream("GET", url, headers={"Accept": "text/event-stream"}) as r:
                return r.status_code
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "agentao-login", "version": "1"},
            },
        }
        async with http.stream(
            "POST", url, json=body, headers={"Accept": ", ".join(content_types)}
        ) as r:
            session = r.headers.get("mcp-session-id")
            status = r.status_code
        if session and 200 <= status < 300:
            try:  # best effort: the login opened a session nobody will use
                await http.delete(url, headers={"mcp-session-id": session})
            except Exception:
                pass
        return status


async def logout(url: str, runtime: OAuthRuntime) -> bool:
    """Delete the record under its lock; an in-flight refresh finishes first."""

    async def remove() -> bool:
        return runtime.store.delete(url)

    return await runtime.exclusive(url, remove)
