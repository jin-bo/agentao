"""MCP OAuth, PR 1: the auth module and the record store (docs/design/mcp-oauth.md §12).

Every connection here runs the installed SDK's real Streamable HTTP transport
and ``ClientSession`` over a mocked socket (``tests/support/oauth_server.py``);
nothing the SDK parses is a ``MagicMock``. That matters most for the verdict
tests: on mcp 2.0 an auth failure leaves the transport as "Server returned an
error response", and only a real transport shows that.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from agentao.mcp import oauth as oauth_mod
from agentao.mcp._compat import SDK_BINDS_ISSUER
from agentao.mcp.client import McpClient, McpClientManager, ServerStatus
from agentao.mcp.config import McpOAuthConfigError, resolve_oauth
from agentao.mcp.oauth import (
    NEEDS_AUTH,
    REFRESH_FAILED,
    AuthVerdict,
    OAuthLoginError,
    StoredTokenAuth,
    choose_registration,
    merge_token_response,
)
from agentao.mcp.oauth_store import OAuthRecord, OAuthRuntime, RecordStore, canonical_server_url
from tests.support.oauth_server import BASE, MCP_URL, SSE_URL, FakeLoginUI, FakeOAuthOrigin, patched

CONFIG = {"url": MCP_URL, "timeout": 10}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def origin():
    return FakeOAuthOrigin()


@pytest.fixture
def runtime(tmp_path):
    return OAuthRuntime(tmp_path / "mcp-oauth", token_timeout=5.0)


def seed(runtime, origin, *, expired=False, refresh=True, client_info=None, issuer=BASE):
    access, refresh_token = origin.issue("seed")
    if expired:
        origin.valid_tokens.discard(access)
    record = OAuthRecord(
        server_url=MCP_URL,
        issuer=issuer,
        token_endpoint=f"{BASE}/token",
        access_token=access,
        resource=MCP_URL,
        client_info=client_info or {"client_id": "seed", "token_endpoint_auth_method": "none"},
        expires_at=time.time() - 10 if expired else time.time() + 3600,
        refresh_token=refresh_token if refresh else None,
        scope="a",
    )
    runtime.store.save(record)
    return record


async def connect(runtime, config=CONFIG, name="svr"):
    client = McpClient(name, config, oauth=runtime)
    await client.connect()
    return client


# ---------------------------------------------------------------------------
# Config (§5.4)
# ---------------------------------------------------------------------------


class TestResolveOAuth:
    def test_url_server_without_authorization_header_is_eligible(self):
        assert resolve_oauth({"url": MCP_URL}) == {}

    def test_stdio_never(self):
        assert resolve_oauth({"command": "echo"}) is None

    def test_authorization_header_in_any_case_disables(self):
        assert resolve_oauth({"url": MCP_URL, "headers": {"authorization": "x"}}) is None

    def test_explicit_false_disables(self):
        assert resolve_oauth({"url": MCP_URL, "oauth": False}) is None

    def test_settings_pass_through(self):
        cfg = {"url": MCP_URL, "oauth": {"client_id": "c", "callback_port": 8765}}
        assert resolve_oauth(cfg) == {"client_id": "c", "callback_port": 8765}

    @pytest.mark.parametrize(
        "oauth",
        [
            True,
            "yes",
            {"scopes": "a"},  # deliberately not a key: the SDK would ignore it
            {"client_id": ""},
            {"client_secret": "s"},  # a secret with no client id
            {"callback_port": 0},
            {"callback_port": True},
            {"callback_port": "8080"},
        ],
    )
    def test_malformed_fails_closed(self, oauth):
        with pytest.raises(McpOAuthConfigError):
            resolve_oauth({"url": MCP_URL, "oauth": oauth})

    def test_a_malformed_block_fails_the_connect(self, runtime, origin):
        with patched(origin):
            client = run(connect(runtime, {**CONFIG, "oauth": {"scopes": "a"}}))
        assert client.status == ServerStatus.ERROR
        assert "oauth" in client.error_message

    def test_client_secret_is_env_expanded_on_load(self, tmp_path, monkeypatch):
        from agentao.mcp.config import load_mcp_config

        monkeypatch.setenv("AGENTAO_TEST_SECRET", "s3cret")
        project = tmp_path / "proj"
        (project / ".agentao").mkdir(parents=True)
        (project / ".agentao" / "mcp.json").write_text(
            json.dumps({"mcpServers": {"x": {"url": MCP_URL, "oauth": {
                "client_id": "c", "client_secret": "$AGENTAO_TEST_SECRET"}}}})
        )
        configs = load_mcp_config(project_root=project, user_root=tmp_path / "home")
        assert configs["x"]["oauth"]["client_secret"] == "s3cret"


# ---------------------------------------------------------------------------
# Record store (§6.2)
# ---------------------------------------------------------------------------


class TestRecordStore:
    def test_canonical_url(self):
        assert canonical_server_url("HTTPS://Mcp.Example:443/mcp#x") == "https://mcp.example/mcp"
        assert canonical_server_url("http://h:8080/mcp/") == "http://h:8080/mcp/"
        assert canonical_server_url("https://h/mcp") != canonical_server_url("https://h/mcp/")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
    def test_modes(self, runtime, origin):
        seed(runtime, origin)
        path = runtime.store.path(MCP_URL)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_round_trip_and_url_guard(self, runtime, origin):
        record = seed(runtime, origin)
        assert runtime.store.load(MCP_URL) == record
        # A record copied under another URL's name is ignored.
        other = "https://other.example/mcp"
        runtime.store.path(other).write_text(runtime.store.path(MCP_URL).read_text())
        assert runtime.store.load(other) is None

    def test_unreadable_record_is_ignored(self, runtime):
        runtime.store.ensure_root()
        runtime.store.path(MCP_URL).write_text("{not json")
        assert runtime.store.load(MCP_URL) is None

    def test_atomic_write_leaves_old_record_on_failure(self, runtime, origin):
        record = seed(runtime, origin)
        with patch("agentao.mcp.oauth_store.os.replace", side_effect=OSError("killed")):
            with pytest.raises(OSError):
                runtime.store.save(merge_token_response(record, {"access_token": "new-xxxxxxxx"}))
        assert runtime.store.load(MCP_URL) == record
        assert [p.name for p in runtime.store.root.iterdir() if p.name.startswith(".tmp")] == []


# ---------------------------------------------------------------------------
# The invariant (§5.2)
# ---------------------------------------------------------------------------


def test_ordinary_connections_never_construct_the_sdk_provider(runtime, origin):
    def boom(*a, **k):
        raise AssertionError("OAuthClientProvider constructed outside login()")

    seed(runtime, origin, expired=True)
    with patched(origin), patch("mcp.client.auth.OAuthClientProvider.__init__", boom):

        async def scenario():
            client = await connect(runtime)  # startup with an expired token
            assert client.status == ServerStatus.CONNECTED
            await client.disconnect()
            await client.connect()  # reconnect
            origin.revoke_all()  # a tool call whose refresh is rejected
            result = await client.call_tool("echo", {})
            no_record = await connect(OAuthRuntime(runtime.store.root.parent / "empty"))
            return client, result, no_record

        client, result, no_record = run(scenario())
    assert client.status == ServerStatus.NEEDS_AUTH and "needs login" in result
    assert no_record.status == ServerStatus.NEEDS_AUTH


# ---------------------------------------------------------------------------
# First connect, and the verdict through the real transport (§5.3 step 2, §5.4)
# ---------------------------------------------------------------------------


class TestVerdict:
    def test_no_record_and_bearer_challenge_is_needs_auth(self, runtime, origin):
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.NEEDS_AUTH
        assert "/mcp login svr" in client.error_message
        assert origin.mcp_auth_headers[0] is None  # no token sent

    def test_bare_401_stays_error(self, runtime, origin):
        origin.bare_401 = True
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.ERROR

    def test_oauth_false_attaches_nothing(self, runtime, origin):
        seed(runtime, origin)
        with patched(origin):
            client = run(connect(runtime, {**CONFIG, "oauth": False}))
        assert client.status == ServerStatus.ERROR  # 401, no token attached
        assert set(origin.mcp_auth_headers) == {None}

    def test_valid_record_connects_and_preflight_carries_no_auth(self, runtime, origin):
        seed(runtime, origin)
        with patched(origin) as preflight:
            client = run(connect(runtime))
        assert client.status == ServerStatus.CONNECTED
        assert [t.name for t in client.tools] == ["echo"]
        assert preflight and all(
            k.lower() != "authorization" for headers in preflight for k in headers
        )
        assert origin.token_requests == []  # no refresh from the probe, or at all

    def test_mid_session_rejection_is_needs_auth_not_connected(self, runtime, origin):
        seed(runtime, origin)

        async def scenario():
            client = await connect(runtime)
            assert client.status == ServerStatus.CONNECTED
            origin.revoke_all()
            return client, await client.call_tool("echo", {})

        with patched(origin):
            client, result = run(scenario())
        assert result.startswith("MCP auth error:") and "needs login" in result
        assert client.status == ServerStatus.NEEDS_AUTH
        statuses = {"status": client.status.value}
        assert statuses["status"] == "needs_auth"

    def test_needs_auth_does_not_reconnect_until_the_record_changes(self, runtime, origin):
        with patched(origin):

            async def scenario():
                client = await connect(runtime)
                before = len(origin.mcp_auth_headers)
                first = await client.call_tool("echo", {})
                unchanged = len(origin.mcp_auth_headers) == before
                seed(runtime, origin)  # a login landed (any process)
                second = await client.call_tool("echo", {})
                return first, unchanged, second, client

            first, unchanged, second, client = run(scenario())
        assert "needs login" in first and unchanged
        assert second == "ok" and client.status == ServerStatus.CONNECTED

    def test_insufficient_scope_is_needs_auth_with_its_message(self, runtime, origin):
        seed(runtime, origin)

        async def scenario():
            client = await connect(runtime)
            origin.insufficient_scope = True
            return client, await client.call_tool("echo", {})

        with patched(origin):
            client, result = run(scenario())
        assert client.status == ServerStatus.NEEDS_AUTH
        assert "a write" in result and "cannot request" in result
        assert origin.token_requests == []  # no retry loop, no refresh

    def test_a_new_connect_clears_the_verdict(self, runtime, origin):
        with patched(origin):

            async def scenario():
                client = await connect(runtime)
                assert client.status == ServerStatus.NEEDS_AUTH
                seed(runtime, origin)
                await client.connect()
                return client

            client = run(scenario())
        assert client.status == ServerStatus.CONNECTED
        assert client._auth_verdict() is None and client.error_message is None


# ---------------------------------------------------------------------------
# Refresh (§5.3 step 3)
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_restart_with_expired_token_refreshes_once(self, runtime, origin):
        seed(runtime, origin, expired=True)
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.CONNECTED
        assert [r["grant_type"] for r in origin.token_requests] == ["refresh_token"]
        assert origin.token_requests[0]["resource"] == MCP_URL
        assert runtime.store.load(MCP_URL).access_token in origin.valid_tokens

    def test_invalid_grant_is_needs_auth_and_keeps_the_record(self, runtime, origin):
        record = seed(runtime, origin, expired=True)
        origin.token_mode = "invalid_grant"
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.NEEDS_AUTH
        assert runtime.store.load(MCP_URL) == record

    @pytest.mark.parametrize("mode", ["503", "drop"])
    def test_transient_failure_is_an_ordinary_error_and_keeps_the_record(self, runtime, origin, mode):
        record = seed(runtime, origin, expired=True)
        origin.token_mode = mode
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.ERROR
        assert "refreshing its OAuth token failed" in client.error_message
        assert runtime.store.load(MCP_URL) == record

    def test_no_refresh_token_means_needs_auth_once_expired(self, runtime, origin):
        seed(runtime, origin, expired=True, refresh=False)
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.NEEDS_AUTH
        assert origin.token_requests == []

    def test_a_401_triggers_one_refresh_and_a_retry(self, runtime, origin):
        seed(runtime, origin)

        async def scenario():
            client = await connect(runtime)
            origin.revoke_access()  # server-side expiry; refresh token still good
            return client, await client.call_tool("echo", {})

        with patched(origin):
            client, result = run(scenario())
        assert result == "ok" and client.status == ServerStatus.CONNECTED
        assert len(origin.token_requests) == 1

    def test_non_rotating_server_keeps_the_refresh_token(self, runtime, origin):
        seed(runtime, origin, expired=True)
        origin.rotate_refresh = False
        with patched(origin):
            run(connect(runtime))
            first = runtime.store.load(MCP_URL)
            # Expire again; the second refresh must still have a token to spend.
            runtime.store.save(merge_token_response(first, {"access_token": first.access_token, "expires_in": -10}))
            origin.revoke_access()
            client = run(connect(runtime))
        assert client.status == ServerStatus.CONNECTED
        assert len(origin.token_requests) == 2

    def test_merge_rules(self):
        record = OAuthRecord(
            server_url=MCP_URL, issuer=BASE, token_endpoint="t", access_token="old",
            refresh_token="r-old", scope="a b", expires_at=1.0,
        )
        merged = merge_token_response(record, {"access_token": "new"}, now=100.0)
        assert merged.refresh_token == "r-old" and merged.scope == "a b"
        assert merged.expires_at is None  # no expires_in: unknown, use until a 401
        merged = merge_token_response(record, {"access_token": "n", "expires_in": 60, "scope": "a", "refresh_token": "r2"}, now=100.0)
        assert (merged.expires_at, merged.scope, merged.refresh_token) == (160.0, "a", "r2")

    def test_non_bearer_token_type_is_a_failed_refresh(self, runtime, origin):
        record = seed(runtime, origin, expired=True)
        original = origin._token_body
        origin._token_body = lambda a, r: {**original(a, r), "token_type": "mac"}
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.ERROR
        assert runtime.store.load(MCP_URL) == record

    def test_parallel_json_mode_calls_are_not_serialized(self, runtime, origin):
        # The S1 shape: the SDK provider holds its lock until response headers,
        # so two 2-second JSON-mode calls took 4 s. Ours holds nothing across a request.
        seed(runtime, origin)
        origin.tool_delay = 1.0

        async def scenario():
            client = await connect(runtime)
            start = time.monotonic()
            results = await asyncio.gather(client.call_tool("echo", {}), client.call_tool("echo", {}))
            return results, time.monotonic() - start

        with patched(origin):
            results, elapsed = run(scenario())
        assert results == ["ok", "ok"]
        assert elapsed < 1.8


# ---------------------------------------------------------------------------
# Locks, cancellation, shutdown (§5.3 "Where the lock is taken", "Shutdown")
# ---------------------------------------------------------------------------


class TestLocks:
    def test_logout_waits_for_an_in_flight_refresh(self, runtime, origin):
        seed(runtime, origin, expired=True)
        origin.token_delay = 0.5

        async def scenario():
            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            refreshing = asyncio.create_task(auth._refresh(auth._record, forced=False))
            await asyncio.sleep(0.1)
            assert runtime.locked(MCP_URL)
            deleted = await oauth_mod.logout(MCP_URL, runtime)
            refreshed = await refreshing
            again = StoredTokenAuth("svr", MCP_URL, runtime)
            return deleted, refreshed, again

        with patched(origin):
            deleted, refreshed, again = run(scenario())
        assert refreshed is not None  # the refresh finished first...
        assert deleted and runtime.store.load(MCP_URL) is None  # ...and logout won
        assert again._record is None

    def test_a_refresh_after_logout_reports_needs_auth(self, runtime, origin):
        record = seed(runtime, origin, expired=True)

        async def scenario():
            await oauth_mod.logout(MCP_URL, runtime)
            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            result = await auth._refresh(record, forced=True)
            return auth, result

        with patched(origin):
            auth, result = run(scenario())
        assert result is None and auth.verdict.kind == NEEDS_AUTH
        assert runtime.store.load(MCP_URL) is None  # not written back
        assert origin.token_requests == []

    def test_cancelling_the_caller_does_not_abandon_the_rotation(self, runtime, origin):
        seed(runtime, origin, expired=True)
        origin.token_delay = 0.3

        async def scenario():
            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            task = asyncio.create_task(auth._refresh(auth._record, forced=False))
            await asyncio.sleep(0.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            left = await runtime.wait_critical(5)
            return left

        with patched(origin):
            left = run(scenario())
        assert left == 0
        assert runtime.store.load(MCP_URL).access_token in origin.valid_tokens
        assert not runtime.locked(MCP_URL)

    def test_cancel_then_immediate_shutdown_still_writes_the_rotation(self, runtime, origin):
        # The rev-8 finding: ``_shutdown`` waited for calls only, then stopped
        # the loop under the shielded refresh.
        seed(runtime, origin, expired=True)
        origin.token_delay = 1.0
        manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
        with patched(origin):
            # Connect needs no refresh yet: give it a fresh token first.
            fresh = merge_token_response(runtime.store.load(MCP_URL), {"access_token": origin.issue()[0], "expires_in": 3600})
            runtime.store.save(fresh)
            manager.connect_all()
            # Now make the token expire so the next call refreshes slowly.
            runtime.store.save(merge_token_response(fresh, {"access_token": fresh.access_token, "expires_in": 30}))
            client = manager.get_client("svr")
            client._auth._record = runtime.store.load(MCP_URL)

            call = threading.Thread(target=lambda: _swallow(manager.call_tool, "svr", "echo", {}))
            call.start()
            time.sleep(0.3)  # the refresh is in flight
            manager.disconnect_all(timeout=0.0)  # cancel the call at once
            call.join(10)
        record = runtime.store.load(MCP_URL)
        assert record.access_token != fresh.access_token
        assert record.access_token in origin.valid_tokens
        lock = __import__("filelock").FileLock(str(runtime.store.lock_path(MCP_URL)))
        lock.acquire(timeout=0)  # free
        lock.release()

    def test_a_lock_held_elsewhere_does_not_block_other_servers(self, runtime, origin, tmp_path):
        other_url = "https://other.example/mcp"
        runtime.store.ensure_root()
        holder = subprocess.Popen(
            [sys.executable, "-c", textwrap.dedent(f"""
                import time, filelock
                lock = filelock.FileLock({str(runtime.store.lock_path(other_url))!r})
                lock.acquire()
                print("held", flush=True)
                time.sleep(5)
            """)],
            stdout=subprocess.PIPE, text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"
            seed(runtime, origin)

            async def scenario():
                client = await connect(runtime)
                # Waits on the lock the other process holds — polling, so the
                # loop every server shares stays free. Timed from before the
                # waiter starts: a blocking acquire stalls the loop right there.
                start = time.monotonic()
                blocked = asyncio.create_task(_hold(runtime, other_url))
                await asyncio.sleep(0.1)
                result = await client.call_tool("echo", {})
                elapsed = time.monotonic() - start
                blocked.cancel()
                return result, elapsed

            with patched(origin):
                result, elapsed = run(scenario())
            assert result == "ok" and elapsed < 1.0
        finally:
            holder.kill()
            holder.wait()

    def test_two_managers_in_one_process_refresh_once(self, runtime, origin):
        seed(runtime, origin, expired=True)
        origin.token_delay = 0.3
        managers = [
            McpClientManager({"svr": CONFIG}, oauth_runtime=OAuthRuntime(runtime.store.root, token_timeout=5.0))
            for _ in range(2)
        ]
        with patched(origin):
            threads = [threading.Thread(target=m.connect_all) for m in managers]
            for t in threads:
                t.start()
            for t in threads:
                t.join(20)
            statuses = [m.get_client("svr").status for m in managers]
            for m in managers:
                m.disconnect_all()
        assert statuses == [ServerStatus.CONNECTED, ServerStatus.CONNECTED]
        assert len([r for r in origin.token_requests if r["grant_type"] == "refresh_token"]) == 1


async def _hold(runtime, url):
    async def forever():
        await asyncio.sleep(30)

    await runtime.exclusive(url, forever)


def _swallow(fn, *args):
    try:
        fn(*args)
    except Exception:
        pass


def test_two_processes_spend_one_rotating_refresh_token_once(tmp_path):
    """S4: one record, two processes, a token endpoint that rotates on use."""
    state = {"refreshes": 0, "valid": {"refresh-0"}}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            from urllib.parse import parse_qs

            length = int(self.headers.get("content-length", 0))
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            time.sleep(0.3)  # long enough for the second process to arrive
            with lock:
                ok = form.get("refresh_token") in state["valid"]
                if ok:
                    state["refreshes"] += 1
                    n = state["refreshes"]
                    state["valid"] = {f"refresh-{n}"}
            body = (
                {"access_token": f"access-{n}-xxxxxxxx", "token_type": "Bearer",
                 "expires_in": 3600, "refresh_token": f"refresh-{n}"}
                if ok else {"error": "invalid_grant"}
            )
            data = json.dumps(body).encode()
            self.send_response(200 if ok else 400)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = tmp_path / "mcp-oauth"
    try:
        RecordStore(root).save(OAuthRecord(
            server_url=MCP_URL, issuer=BASE,
            token_endpoint=f"http://127.0.0.1:{server.server_port}/token",
            access_token="access-0-xxxxxxxx", refresh_token="refresh-0",
            expires_at=time.time() - 10, client_info={"client_id": "c"},
        ))
        script = textwrap.dedent(f"""
            import asyncio, json
            from agentao.mcp.oauth import StoredTokenAuth
            from agentao.mcp.oauth_store import OAuthRuntime
            auth = StoredTokenAuth("svr", {MCP_URL!r}, OAuthRuntime({str(root)!r}))
            record = asyncio.run(auth._refresh(auth._record, forced=False))
            print(json.dumps({{"token": record and record.access_token,
                               "verdict": auth.verdict and auth.verdict.kind}}))
        """)
        env = {**os.environ, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
        procs = [
            subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, env=env)
            for _ in range(2)
        ]
        outs = [json.loads(p.communicate(timeout=60)[0]) for p in procs]
    finally:
        server.shutdown()
    assert state["refreshes"] == 1
    assert {o["token"] for o in outs} == {"access-1-xxxxxxxx"}
    assert all(o["verdict"] is None for o in outs)


# ---------------------------------------------------------------------------
# Login (§5.5)
# ---------------------------------------------------------------------------


class TestLogin:
    def _manager(self, runtime, config=CONFIG):
        return McpClientManager({"svr": config}, oauth_runtime=runtime)

    def test_login_from_an_empty_store(self, runtime, origin):
        ui = FakeLoginUI(origin)
        manager = self._manager(runtime)
        with patched(origin):
            manager.connect_all()
            assert manager.get_client("svr").status == ServerStatus.NEEDS_AUTH
            status = manager.login("svr", ui)
            manager.disconnect_all()
        assert status == ServerStatus.CONNECTED
        record = runtime.store.load(MCP_URL)
        assert record.token_endpoint == f"{BASE}/token"
        assert record.refresh_token and record.expires_at and record.expires_at > time.time()
        assert record.resource == MCP_URL
        assert record.client_info["redirect_uris"] == ["http://localhost:34567/callback/svr"]
        assert origin.registrations == 1 and ui.closed == 1
        query = __import__("tests.support.oauth_server", fromlist=["query_of"]).query_of(ui.opened[0])
        assert query["redirect_uri"] == "http://localhost:34567/callback/svr"
        assert query["code_challenge_method"] == "S256" and query["resource"] == MCP_URL
        if SDK_BINDS_ISSUER:
            assert record.client_info.get("issuer") == record.issuer

    def test_old_tokens_are_never_loaded(self, runtime, origin):
        seed(runtime, origin)  # a perfectly valid record
        ui = FakeLoginUI(origin)
        manager = self._manager(runtime)
        with patched(origin):
            manager.login("svr", ui)
            manager.disconnect_all()
        assert len(ui.opened) == 1  # still went to /authorize

    def test_the_ui_is_closed_on_failure(self, runtime, origin):
        ui = FakeLoginUI(origin, fail=TimeoutError("no callback"))
        manager = self._manager(runtime)
        with patched(origin):
            with pytest.raises(TimeoutError):
                manager.login("svr", ui)
            manager.disconnect_all()
        assert ui.closed == 1 and runtime.store.load(MCP_URL) is None

    def test_a_stored_registration_is_reused_only_when_issuer_bound(self, runtime, origin):
        manager = self._manager(runtime)
        with patched(origin):
            manager.login("svr", FakeLoginUI(origin))
            manager.login("svr", FakeLoginUI(origin))  # same port
            manager.disconnect_all()
        # 2.x binds the registration to its issuer and reuses it; 1.26 cannot
        # check the issuer after discovery, so it never offers one.
        assert origin.registrations == (1 if SDK_BINDS_ISSUER else 2)

    def test_a_changed_port_registers_again(self, runtime, origin):
        manager = self._manager(runtime)
        with patched(origin):
            manager.login("svr", FakeLoginUI(origin, port=34567))
            second = FakeLoginUI(origin, port=40000)
            manager.login("svr", second)
            manager.disconnect_all()
        assert second.prepared_with == [34567]  # asked for the registered port first
        assert origin.registrations == 2

    def test_login_refused_for_a_server_without_oauth(self, runtime, origin):
        manager = self._manager(runtime, {**CONFIG, "oauth": False})
        with patched(origin):
            with pytest.raises(RuntimeError, match="does not use OAuth"):
                manager.login("svr", FakeLoginUI(origin))
            manager.disconnect_all()

    def test_logout_deletes_and_disconnects(self, runtime, origin):
        seed(runtime, origin)
        manager = self._manager(runtime)
        with patched(origin):
            manager.connect_all()
            assert manager.logout("svr") is True
            status = manager.get_client("svr").status
            manager.disconnect_all()
        assert runtime.store.load(MCP_URL) is None and status == ServerStatus.DISCONNECTED

    def test_no_token_reaches_the_logs(self, runtime, origin, caplog):
        caplog.set_level(logging.DEBUG)
        manager = self._manager(runtime)
        with patched(origin):
            manager.login("svr", FakeLoginUI(origin))
            record = runtime.store.load(MCP_URL)
            runtime.store.save(merge_token_response(record, {"access_token": record.access_token, "expires_in": -1}))
            manager.get_client("svr")._auth._record = runtime.store.load(MCP_URL)
            assert manager.call_tool("svr", "echo", {}) == "ok"  # refreshes
            manager.disconnect_all()
        from agentao.security.secret_scan import redact

        secrets = {r["refresh_token"] for r in origin.token_requests if "refresh_token" in r}
        secrets |= {r["code"] for r in origin.token_requests if "code" in r}
        secrets |= {r["code_verifier"] for r in origin.token_requests if "code_verifier" in r}
        secrets |= origin.valid_tokens
        assert len(secrets) >= 4
        text = "\n".join(redact(r.getMessage()) for r in caplog.records)
        leaked = [s for s in secrets if s in text]
        assert leaked == []


class TestChooseRegistration:
    URI = "http://localhost:1/callback/svr"

    def test_configured_client_is_offered(self):
        offered = choose_registration({"client_id": "c", "client_secret": "s"}, {}, self.URI)
        assert offered["client_id"] == "c" and offered["redirect_uris"] == [self.URI]

    def test_configured_client_with_a_changed_port_fails(self):
        stored = {"client_id": "c", "redirect_uris": ["http://localhost:2/callback/svr"]}
        with pytest.raises(OAuthLoginError, match="callback_port"):
            choose_registration({"client_id": "c"}, stored, self.URI)

    def test_dynamic_registration_with_a_changed_port_is_not_offered(self):
        stored = {"client_id": "d", "issuer": BASE, "redirect_uris": ["http://localhost:2/callback/svr"]}
        assert choose_registration({}, stored, self.URI) is None

    def test_dynamic_registration_with_an_empty_issuer_is_never_offered(self):
        # 2.x reads an empty issuer as matching every authorization server.
        for stored in (
            {"client_id": "d", "redirect_uris": [self.URI]},  # as 1.26 wrote it
            {"client_id": "d", "issuer": None, "redirect_uris": [self.URI]},  # resource-origin fallback
        ):
            assert choose_registration({}, stored, self.URI) is None

    def test_issuer_bound_registration_is_offered_only_where_the_sdk_checks_it(self):
        stored = {"client_id": "d", "issuer": BASE, "redirect_uris": [self.URI]}
        assert (choose_registration({}, stored, self.URI) is not None) is SDK_BINDS_ISSUER

    def test_an_empty_issuer_registration_against_a_new_as_registers_again(self, runtime, origin):
        # Through the real SDK: a stored DCR client with no issuer is not
        # presented, so the login registers afresh.
        runtime.store.save(OAuthRecord(
            server_url=MCP_URL, issuer="https://old-as.example", token_endpoint="t",
            access_token="x", client_info={"client_id": "stale", "redirect_uris": [
                "http://localhost:34567/callback/svr"]},
        ))
        manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
        with patched(origin):
            manager.login("svr", FakeLoginUI(origin))
            manager.disconnect_all()
        assert origin.registrations == 1
        assert all(q.get("client_id") != "stale" for q in origin.authorize_requests)


# ---------------------------------------------------------------------------
# S3: the legacy SSE transport carries the same auth (§9, "Still open")
# ---------------------------------------------------------------------------


class TestSse:
    SSE = {"url": SSE_URL, "type": "sse", "timeout": 10}

    def _seed(self, runtime, origin, **kw):
        record = seed(runtime, origin, **kw)
        moved = OAuthRecord(**{**record.__dict__, "server_url": SSE_URL})
        runtime.store.save(moved)
        return moved

    def test_valid_record_connects_and_calls(self, runtime, origin):
        self._seed(runtime, origin)

        async def scenario():
            client = await connect(runtime, self.SSE)
            result = await client.call_tool("echo", {})
            await client.disconnect()
            return client, result

        with patched(origin):
            client, result = run(scenario())
        assert result == "ok"
        assert origin.mcp_auth_headers and all(h and h.startswith("Bearer ") for h in origin.mcp_auth_headers)

    def test_no_record_is_needs_auth(self, runtime, origin):
        with patched(origin):
            client = run(connect(runtime, self.SSE))
        assert client.status == ServerStatus.NEEDS_AUTH

    def test_expired_record_refreshes_before_the_stream_opens(self, runtime, origin):
        self._seed(runtime, origin, expired=True)

        async def scenario():
            client = await connect(runtime, self.SSE)
            status = client.status
            await client.disconnect()
            return status

        with patched(origin):
            status = run(scenario())
        assert status == ServerStatus.CONNECTED
        assert len(origin.token_requests) == 1


# ---------------------------------------------------------------------------
# Regression tests for the four /code-review findings (2026-10-02)
# ---------------------------------------------------------------------------


class TestReviewFindings:
    def test_a_401_that_recovers_clears_an_earlier_refresh_failure(self, runtime, origin):
        # The refresh fails (503), but meanwhile another process wrote a fresh
        # token; the 401 retry with it succeeds. The connection works, so the
        # failed refresh must not stay on it and label every later error.
        seed(runtime, origin, expired=True)
        origin.token_mode = "503"

        def another_process_refreshed():
            record = runtime.store.load(MCP_URL)
            runtime.store.save(merge_token_response(
                record, {"access_token": origin.issue()[0], "expires_in": 3600}))

        origin.on_refresh = another_process_refreshed

        async def one_request():
            # One request through the auth object, nothing after it: a full
            # connect sends more requests, and any later success would clear
            # the verdict and hide the bug.
            from agentao.mcp._compat import httpx_for_mcp

            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            async with httpx_for_mcp.AsyncClient(
                auth=auth, transport=httpx_for_mcp.MockTransport(origin.handle)
            ) as http:
                response = await http.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            return auth, response.status_code

        with patched(origin):
            auth, status = run(one_request())
        assert status == 200  # the 401's retry used the token the other process wrote
        assert auth.verdict is None

    def test_a_stale_refresh_failure_does_not_block_a_session_reconnect(self, runtime, origin):
        seed(runtime, origin)

        async def scenario():
            client = await connect(runtime)
            client._auth.verdict = AuthVerdict(REFRESH_FAILED, "refreshing failed (earlier)")
            origin.expire_session_once = True  # the server restarted
            return client, await client.call_tool("echo", {})

        with patched(origin):
            client, result = run(scenario())
        assert result == "ok"
        assert client.status == ServerStatus.CONNECTED

    def test_a_refresh_response_without_token_type_is_bearer(self, runtime, origin):
        seed(runtime, origin, expired=True)
        original = origin._token_body

        def no_type(access, refresh):
            body = original(access, refresh)
            body.pop("token_type")
            return body

        origin._token_body = no_type
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.CONNECTED
        assert runtime.store.load(MCP_URL).access_token in origin.valid_tokens

    def test_login_reconnects_under_the_reconnect_lock(self, runtime, origin):
        manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
        seen = []
        real_connect = McpClient.connect

        async def recording_connect(self):
            seen.append((self._reconnect_lock.locked(), self._connect_attempts))
            await real_connect(self)

        with patched(origin), patch.object(McpClient, "connect", recording_connect):
            manager.connect_all()
            manager.login("svr", FakeLoginUI(origin))
            attempts = manager.get_client("svr")._connect_attempts
            manager.disconnect_all()
        assert seen[-1][0] is True  # the login's reconnect held the lock
        assert attempts == seen[-1][1] + 1  # and counted, so a waiting call reuses it


# ---------------------------------------------------------------------------
# Regression tests for the Codex review (2026-10-02, round 1)
# ---------------------------------------------------------------------------


class TestCodexRound1:
    def test_a_concurrent_success_does_not_clear_another_requests_verdict(self, runtime, origin):
        # Two requests share one auth object (parallel tool calls, #241). The
        # fast one is refused for scope; the slow one, started first, succeeds
        # afterwards. The refused request's caller has not read the verdict yet.
        seed(runtime, origin)

        async def scenario():
            from agentao.mcp._compat import httpx_for_mcp

            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            slow_started = asyncio.Event()

            async def handler(request):
                body = json.loads(request.content)
                if body["params"]["name"] == "slow":
                    slow_started.set()
                    await asyncio.sleep(0.3)
                    return httpx_for_mcp.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
                return httpx_for_mcp.Response(
                    403, headers={"www-authenticate": 'Bearer error="insufficient_scope", scope="write"'})

            async with httpx_for_mcp.AsyncClient(
                auth=auth, transport=httpx_for_mcp.MockTransport(handler)
            ) as http:
                def call(name):
                    return http.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                    "params": {"name": name}})

                slow = asyncio.create_task(call("slow"))
                await slow_started.wait()
                refused = await call("deny")
                await slow
            return auth, refused.status_code

        with patched(origin):
            auth, status = run(scenario())
        assert status == 403
        assert auth.verdict is not None and auth.verdict.kind == NEEDS_AUTH
        assert "write" in auth.verdict.message

    def test_a_later_success_still_clears_an_old_verdict(self, runtime, origin):
        # The guard above must not make verdicts permanent.
        seed(runtime, origin)

        async def scenario():
            client = await connect(runtime)
            client._auth._set_verdict(REFRESH_FAILED, "earlier", None)
            result = await client.call_tool("echo", {})
            return client, result

        with patched(origin):
            client, result = run(scenario())
        assert result == "ok" and client._auth_verdict() is None

    def test_insufficient_scope_after_a_401_retry(self, runtime, origin):
        seed(runtime, origin)

        async def scenario():
            client = await connect(runtime)
            origin.revoke_access()  # 401 → refresh → retry ...
            origin.insufficient_scope = True  # ... and the new token lacks scope
            return client, await client.call_tool("echo", {})

        with patched(origin):
            client, result = run(scenario())
        assert len(origin.token_requests) == 1
        assert client.status == ServerStatus.NEEDS_AUTH
        assert "a write" in result and "cannot request" in result

    def test_a_trickling_token_endpoint_is_cut_off_at_the_token_timeout(self, tmp_path, origin):
        runtime = OAuthRuntime(tmp_path / "mcp-oauth", token_timeout=0.5)
        record = seed(runtime, origin, expired=True)
        origin.token_trickle = 0.1  # ~25 chunks: each fast, the whole > 2 s

        async def scenario():
            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            start = time.monotonic()
            result = await auth._refresh(auth._record, forced=False)
            return auth, result, time.monotonic() - start

        with patched(origin):
            auth, result, elapsed = run(scenario())
        assert result is None and auth.verdict.kind == REFRESH_FAILED
        assert elapsed < 1.5
        assert runtime.store.load(MCP_URL) == record and not runtime.locked(MCP_URL)


# ---------------------------------------------------------------------------
# Regression tests for the Codex review (2026-10-02, round 2)
# ---------------------------------------------------------------------------


class TestCodexRound2:
    def test_a_refusal_survives_a_success_that_starts_after_it(self, runtime, origin):
        # Codex's reproduction, through the real transport: the refused call is
        # still reading its 403's body when another call starts and succeeds.
        # That success says nothing about the refusal, whose caller has yet to
        # read it.
        seed(runtime, origin)
        origin.scope_denied_tool = "deny"
        origin.deny_body_delay = 0.3

        async def scenario():
            client = await connect(runtime)

            async def later_success():
                await asyncio.sleep(0.1)  # starts after the 403's headers
                return await client.call_tool("echo", {})

            denied, ok = await asyncio.gather(client.call_tool("deny", {}), later_success())
            return client, denied, ok

        with patched(origin):
            client, denied, ok = run(scenario())
        # mcp 2.x reads the 403's body before failing the call, so the later
        # call succeeds in between — the race this test exists for. mcp 1.x
        # fails the call on the headers, so the server is NEEDS_AUTH already
        # and the later call is answered with the login hint instead.
        assert ok == "ok" or ok.startswith("MCP auth error:")
        assert denied.startswith("MCP auth error:") and "write" in denied
        assert client.status == ServerStatus.NEEDS_AUTH

    def test_a_refusal_survives_at_the_auth_object(self, runtime, origin):
        # The same property without timing: a refusal, then a success that
        # started strictly after it.
        seed(runtime, origin)

        async def scenario():
            from agentao.mcp._compat import httpx_for_mcp

            auth = StoredTokenAuth("svr", MCP_URL, runtime)

            async def handler(request):
                if json.loads(request.content)["params"]["name"] == "deny":
                    return httpx_for_mcp.Response(
                        403, headers={"www-authenticate": 'Bearer error="insufficient_scope"'})
                return httpx_for_mcp.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

            async with httpx_for_mcp.AsyncClient(
                auth=auth, transport=httpx_for_mcp.MockTransport(handler)
            ) as http:
                for name in ("deny", "ok"):
                    await http.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                   "params": {"name": name}})
            return auth

        with patched(origin):
            auth = run(scenario())
        assert auth.verdict is not None and auth.verdict.kind == NEEDS_AUTH

    def test_a_login_during_the_final_retry_is_not_marked_refused(self, runtime, origin):
        # The old credential is refused even after a refresh (the server
        # revoked the client); while the retry is in flight, a login elsewhere
        # writes a new, good credential. The refusal belongs to the token that
        # was sent, so the next call must try the new one, not stay blocked.
        seed(runtime, origin)
        written = {}

        async def scenario():
            client = await connect(runtime)
            origin.revoke_access()
            origin.refreshed_tokens_valid = False

            def on_mcp_auth(token):
                record = runtime.store.load(MCP_URL)
                if token and record and token == record.access_token \
                        and len(origin.token_requests) == 1 and not written:
                    # The retry with the refreshed token: a login lands now.
                    written["token"] = origin.issue()[0]
                    time.sleep(0.01)  # a distinct mtime on coarse filesystems
                    runtime.store.save(merge_token_response(
                        record, {"access_token": written["token"], "expires_in": 3600}))

            origin.on_mcp_auth = on_mcp_auth
            first = await client.call_tool("echo", {})
            second = await client.call_tool("echo", {})
            return client, first, second

        with patched(origin):
            client, first, second = run(scenario())
        assert "token" in written
        assert "needs login" in first  # that request really was refused
        assert second == "ok" and client.status == ServerStatus.CONNECTED

    def test_a_concurrent_success_keeps_another_requests_refresh_failure(self, runtime, origin):
        # The ``gen`` / ``owner`` rule now guards REFRESH_FAILED alone (a
        # NEEDS_AUTH is never cleared by a success): a slow request that
        # started first must not clear a refresh failure another request
        # reached while it was in flight.
        seed(runtime, origin)

        async def scenario():
            from agentao.mcp._compat import httpx_for_mcp

            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            started = asyncio.Event()
            release = asyncio.Event()

            async def handler(request):
                started.set()
                await release.wait()
                return httpx_for_mcp.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

            async with httpx_for_mcp.AsyncClient(
                auth=auth, transport=httpx_for_mcp.MockTransport(handler)
            ) as http:
                slow = asyncio.create_task(http.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}))
                await started.wait()
                auth._set_verdict(REFRESH_FAILED, "another request's refresh failed", None)
                release.set()
                await slow
            return auth

        with patched(origin):
            auth = run(scenario())
        assert auth.verdict is not None and auth.verdict.kind == REFRESH_FAILED


# ---------------------------------------------------------------------------
# Regression test for the Codex review (2026-10-02, round 4)
# ---------------------------------------------------------------------------


def test_a_concurrent_refresh_failure_does_not_overwrite_a_refusal(runtime, origin):
    # One request was refused for scope and its caller has not read the
    # verdict yet; another request on the same connection then fails to
    # refresh (503). The refusal must still be there for its caller.
    seed(runtime, origin, expired=True)
    origin.token_mode = "503"

    async def scenario():
        auth = StoredTokenAuth("svr", MCP_URL, runtime)
        auth._needs_auth(None, "MCP server 'svr' requires scope 'write'")
        refreshed = await auth._refresh(auth._record, forced=False)
        return auth, refreshed

    with patched(origin):
        auth, refreshed = run(scenario())
    assert refreshed is None and len(origin.token_requests) == 1  # it did fail
    assert auth.verdict.kind == NEEDS_AUTH and "write" in auth.verdict.message


# ---------------------------------------------------------------------------
# Regression tests for the Codex review (2026-10-02, round 5)
# ---------------------------------------------------------------------------


class TestCodexRound5:
    def test_a_logout_elsewhere_stops_the_cached_token_at_once(self, runtime, origin):
        # Another manager (its own runtime, the same files) logs out. The token
        # is still valid server-side; this connection must stop sending it.
        seed(runtime, origin)

        async def scenario():
            client = await connect(runtime)
            assert await client.call_tool("echo", {}) == "ok"
            elsewhere = OAuthRuntime(runtime.store.root, token_timeout=5.0)
            await oauth_mod.logout(MCP_URL, elsewhere)
            sent_before = len(origin.mcp_auth_headers)
            result = await client.call_tool("echo", {})
            return client, result, origin.mcp_auth_headers[sent_before:]

        with patched(origin):
            client, result, sent = run(scenario())
        assert sent and all(h is None for h in sent)  # the old token never went out again
        assert "needs login" in result and client.status == ServerStatus.NEEDS_AUTH

    def test_a_login_elsewhere_switches_the_cached_token(self, runtime, origin):
        first = seed(runtime, origin)

        async def scenario():
            client = await connect(runtime)
            assert await client.call_tool("echo", {}) == "ok"
            other_account = origin.issue("other")[0]
            time.sleep(0.01)  # a distinct mtime on coarse filesystems
            runtime.store.save(merge_token_response(first, {"access_token": other_account, "expires_in": 3600}))
            sent_before = len(origin.mcp_auth_headers)
            result = await client.call_tool("echo", {})
            return result, other_account, origin.mcp_auth_headers[sent_before:]

        with patched(origin):
            result, other_account, sent = run(scenario())
        assert result == "ok"
        assert sent == [f"Bearer {other_account}"]


# ---------------------------------------------------------------------------
# Regression tests for the Codex review (2026-10-02, round 6)
# ---------------------------------------------------------------------------


class TestCodexRound6:
    def _expiring(self, runtime, origin):
        """A record inside the refresh window whose token the server still accepts."""
        record = seed(runtime, origin)
        record = merge_token_response(record, {"access_token": record.access_token, "expires_in": 30})
        runtime.store.save(record)
        return record

    def test_a_rejected_grant_stops_the_still_valid_old_token(self, runtime, origin):
        # The access token is still good server-side, but the refresh grant was
        # rejected: the credential is revoked, so it must not be used.
        self._expiring(runtime, origin)
        origin.token_mode = "invalid_grant"
        with patched(origin):
            client = run(connect(runtime))
        assert len(origin.token_requests) == 1
        assert client.status == ServerStatus.NEEDS_AUTH
        assert set(origin.mcp_auth_headers) == {None}  # the old token never went out

    def test_a_logout_during_the_refresh_wait_stops_the_old_token(self, runtime, origin):
        self._expiring(runtime, origin)

        async def scenario():
            from agentao.mcp._compat import httpx_for_mcp

            elsewhere = OAuthRuntime(runtime.store.root, token_timeout=5.0)
            holding = asyncio.Event()

            async def slow_logout():
                holding.set()
                await asyncio.sleep(0.3)
                return elsewhere.store.delete(MCP_URL)

            logout = asyncio.create_task(elsewhere.exclusive(MCP_URL, slow_logout))
            await holding.wait()  # the logout holds the record's lock
            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            sent = []

            async def handler(request):
                sent.append(request.headers.get("authorization"))
                return httpx_for_mcp.Response(401, headers={"www-authenticate": "Bearer"})

            async with httpx_for_mcp.AsyncClient(
                auth=auth, transport=httpx_for_mcp.MockTransport(handler)
            ) as http:
                await http.post(MCP_URL, json={})
            await logout
            return auth, sent

        with patched(origin):
            auth, sent = run(scenario())
        assert sent == [None]
        assert auth.verdict.kind == NEEDS_AUTH
        assert origin.token_requests == []  # it re-read, found nothing, asked nobody

    def test_a_transient_failure_still_keeps_the_old_token(self, runtime, origin):
        # The other half of the rule: a 503 says nothing about the credential.
        record = self._expiring(runtime, origin)
        origin.token_mode = "503"
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.CONNECTED
        assert f"Bearer {record.access_token}" in origin.mcp_auth_headers

    def test_an_invalid_utf8_record_is_unreadable_and_login_replaces_it(self, runtime, origin):
        runtime.store.ensure_root()
        runtime.store.path(MCP_URL).write_bytes(b"\xff\xfe{not utf-8")
        assert runtime.store.load(MCP_URL) is None
        manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
        with patched(origin):
            manager.connect_all()
            first = manager.get_client("svr").status
            status = manager.login("svr", FakeLoginUI(origin))
            manager.disconnect_all()
        assert first == ServerStatus.NEEDS_AUTH
        assert status == ServerStatus.CONNECTED and runtime.store.load(MCP_URL) is not None

    def test_a_refused_credential_is_not_refreshed_again_per_request(self, runtime, origin):
        # Once the grant was rejected, later requests on the connection must
        # not each spend another token request on the same dead credential.
        self._expiring(runtime, origin)
        origin.token_mode = "invalid_grant"

        async def scenario():
            from agentao.mcp._compat import httpx_for_mcp

            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            async with httpx_for_mcp.AsyncClient(
                auth=auth, transport=httpx_for_mcp.MockTransport(origin.handle)
            ) as http:
                for _ in range(3):
                    await http.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
            return auth

        with patched(origin):
            auth = run(scenario())
        assert len(origin.token_requests) == 1
        assert set(origin.mcp_auth_headers) == {None}
        assert auth.verdict.kind == NEEDS_AUTH

    def test_a_scope_refusal_does_not_retire_the_token_for_other_calls(self, runtime, origin):
        # A 403 for scope says the token cannot do *that*; it says nothing
        # about the token. Other requests keep sending it.
        record = seed(runtime, origin)

        async def scenario():
            from agentao.mcp._compat import httpx_for_mcp

            auth = StoredTokenAuth("svr", MCP_URL, runtime)
            sent = []

            async def handler(request):
                sent.append(request.headers.get("authorization"))
                if json.loads(request.content)["params"]["name"] == "deny":
                    return httpx_for_mcp.Response(
                        403, headers={"www-authenticate": 'Bearer error="insufficient_scope"'})
                return httpx_for_mcp.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})

            async with httpx_for_mcp.AsyncClient(
                auth=auth, transport=httpx_for_mcp.MockTransport(handler)
            ) as http:
                for name in ("deny", "other"):
                    await http.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                   "params": {"name": name}})
            return sent

        with patched(origin):
            sent = run(scenario())
        assert sent == [f"Bearer {record.access_token}"] * 2


# ---------------------------------------------------------------------------
# Regression tests for the second /code-review pass (2026-10-02)
# ---------------------------------------------------------------------------


class TestCodeReview2:
    SSE_HINT = "tried as Streamable HTTP"

    def test_a_malformed_oauth_block_gets_no_sse_hint(self, runtime, origin):
        with patched(origin):
            client = run(connect(runtime, {"url": MCP_URL, "oauth": "yes", "timeout": 10}))
        assert client.status == ServerStatus.ERROR
        assert "oauth" in client.error_message and self.SSE_HINT not in client.error_message

    def test_a_failed_refresh_gets_no_sse_hint(self, runtime, origin):
        seed(runtime, origin, expired=True)
        origin.token_mode = "503"
        with patched(origin):
            client = run(connect(runtime))
        assert client.status == ServerStatus.ERROR
        assert "refreshing its OAuth token failed" in client.error_message
        assert self.SSE_HINT not in client.error_message

    def test_another_requests_refresh_failure_does_not_relabel_this_error(self, runtime, origin):
        # The connection carries a REFRESH_FAILED some other call reached; this
        # call fails for its own reason (a JSON-RPC error). It is reported as
        # that, not as a refresh failure.
        seed(runtime, origin)

        async def scenario():
            client = await connect(runtime)

            def another_call_failed_to_refresh(token):
                # While this request is in flight — so its own success does not
                # clear it, exactly as when another call reaches it meanwhile.
                # (Built by hand: the mock handler runs inside this request's
                # own flow, so ``_set_verdict`` would record it as this
                # request's — and its success would rightly clear it.)
                auth = client._auth
                auth._gen += 1
                auth.verdict = AuthVerdict(
                    REFRESH_FAILED, "refreshing its OAuth token failed (HTTP 503)", None,
                    gen=auth._gen, owner=-1,
                )

            origin.on_mcp_auth = another_call_failed_to_refresh
            result = await client.call_tool("boom", {})
            assert client._auth_verdict() is not None  # it really was there
            return result

        with patched(origin):
            result = run(scenario())
        assert "boom: bad arguments" in result
        assert "refreshing" not in result


# ---------------------------------------------------------------------------
# Regression test for the Codex review (2026-10-02, round 7)
# ---------------------------------------------------------------------------


def test_concurrent_waiters_do_not_resubmit_a_rejected_grant(runtime, origin):
    # Three requests enter the refresh window together; the first one's grant
    # is rejected while the other two wait for the lock. They must neither ask
    # the token endpoint again nor send the refused token.
    record = seed(runtime, origin)
    runtime.store.save(merge_token_response(record, {"access_token": record.access_token, "expires_in": 30}))
    origin.token_mode = "invalid_grant"
    origin.token_delay = 0.2

    async def scenario():
        from agentao.mcp._compat import httpx_for_mcp

        auth = StoredTokenAuth("svr", MCP_URL, runtime)
        async with httpx_for_mcp.AsyncClient(
            auth=auth, transport=httpx_for_mcp.MockTransport(origin.handle)
        ) as http:
            await asyncio.gather(*(
                http.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
                for _ in range(3)
            ))
        return auth

    with patched(origin):
        auth = run(scenario())
    assert len(origin.token_requests) == 1
    assert set(origin.mcp_auth_headers) == {None}
    assert auth.verdict.kind == NEEDS_AUTH


def test_a_refused_unexpired_token_is_not_sent_again(runtime, origin):
    # Outside the refresh window the lock-side recheck never runs: only the
    # check at the start of each request keeps a refused, unexpired token from
    # going out again.
    record = seed(runtime, origin)  # an hour left
    origin.revoke_all()  # the server revoked the token and its grant

    async def scenario():
        from agentao.mcp._compat import httpx_for_mcp

        auth = StoredTokenAuth("svr", MCP_URL, runtime)
        async with httpx_for_mcp.AsyncClient(
            auth=auth, transport=httpx_for_mcp.MockTransport(origin.handle)
        ) as http:
            for _ in range(2):
                await http.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        return auth

    with patched(origin):
        auth = run(scenario())
    assert origin.mcp_auth_headers == [f"Bearer {record.access_token}", None]
    assert len(origin.token_requests) == 1 and auth.verdict.kind == NEEDS_AUTH


# ---------------------------------------------------------------------------
# Regression test for the Codex review (2026-10-02, round 8)
# ---------------------------------------------------------------------------


def test_a_record_with_an_unparseable_url_is_unreadable_and_login_replaces_it(runtime, origin):
    record = seed(runtime, origin)
    damaged = {**record.to_json(), "server_url": "https://mcp.example:bad/mcp"}
    runtime.store.path(MCP_URL).write_text(json.dumps(damaged))
    assert runtime.store.load(MCP_URL) is None

    manager = McpClientManager({"svr": CONFIG}, oauth_runtime=runtime)
    with patched(origin):
        manager.connect_all()
        first = manager.get_client("svr").status
        status = manager.login("svr", FakeLoginUI(origin))
        manager.disconnect_all()
    assert first == ServerStatus.NEEDS_AUTH
    assert status == ServerStatus.CONNECTED
    assert runtime.store.load(MCP_URL).server_url == MCP_URL
