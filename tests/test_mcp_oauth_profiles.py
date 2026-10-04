"""MCP OAuth credential profiles: two accounts logged in to one URL at once.

``oauth.profile`` names a separate stored credential for the same server URL.
A server without one keeps the record it had before profiles existed, under the
same file name, so an upgrade logs nobody out.

The end-to-end tests run the installed SDK's real transport and login provider
over the mocked socket in ``tests/support/oauth_server.py``, like
``test_mcp_oauth.py``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time

import pytest

from agentao.mcp.client import McpClientManager, ServerStatus
from agentao.mcp.config import McpOAuthConfigError, resolve_oauth
from agentao.mcp.oauth_store import (
    OAuthRecord,
    OAuthRuntime,
    RecordStore,
    canonical_server_url,
    credential_key,
)
from tests.support.oauth_server import BASE, MCP_URL, FakeLoginUI, FakeOAuthOrigin, patched


@pytest.fixture
def origin():
    return FakeOAuthOrigin()


@pytest.fixture
def runtime(tmp_path):
    return OAuthRuntime(tmp_path / "mcp-oauth", token_timeout=5.0)


def _record(access: str, *, profile=None) -> OAuthRecord:
    return OAuthRecord(
        server_url=MCP_URL,
        issuer=BASE,
        token_endpoint=f"{BASE}/token",
        access_token=access,
        client_info={"client_id": "seed", "token_endpoint_auth_method": "none"},
        expires_at=time.time() + 3600,
        profile=profile,
    )


def _profiled(profile: str) -> dict:
    return {"url": MCP_URL, "timeout": 10, "oauth": {"profile": profile}}


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class TestStore:
    def test_no_profile_keeps_the_file_name_logins_were_stored_under(self, tmp_path):
        """An upgrade must find every existing login where it already is."""
        store = RecordStore(tmp_path)
        url = "HTTPS://MCP.Example:443/mcp"
        expected = hashlib.sha256(canonical_server_url(url).encode("utf-8")).hexdigest()
        assert store.path(url).name == f"{expected}.json"
        assert store.lock_path(url).name == f"{expected}.json.lock"
        assert credential_key(url) == canonical_server_url(url)

    def test_each_profile_is_its_own_record(self, tmp_path):
        store = RecordStore(tmp_path)
        store.save(_record("default-token"))
        store.save(_record("work-token", profile="work"))
        store.save(_record("personal-token", profile="personal"))

        assert store.load(MCP_URL).access_token == "default-token"
        assert store.load(MCP_URL, "work").access_token == "work-token"
        assert store.load(MCP_URL, "personal").access_token == "personal-token"
        assert len({store.path(MCP_URL, p) for p in (None, "work", "personal")}) == 3

        assert store.delete(MCP_URL, "work") is True
        assert store.load(MCP_URL, "work") is None
        assert store.load(MCP_URL).access_token == "default-token"
        assert store.load(MCP_URL, "personal").access_token == "personal-token"

    @pytest.mark.parametrize("target", [None, "personal"])
    def test_a_record_moved_to_another_credentials_file_is_ignored(self, tmp_path, target):
        """The record names its profile, so a hand-copied file cannot hand one
        account's token to another — the same guard the URL has."""
        store = RecordStore(tmp_path)
        store.save(_record("work-token", profile="work"))
        store.ensure_root()
        shutil.copy(store.path(MCP_URL, "work"), store.path(MCP_URL, target))
        assert store.load(MCP_URL, target) is None

    def test_a_profile_is_compared_exactly(self, tmp_path):
        store = RecordStore(tmp_path)
        store.save(_record("work-token", profile="work"))
        assert store.load(MCP_URL, "Work") is None

    def test_no_url_spells_another_urls_profiled_key(self):
        assert "\n" in credential_key(MCP_URL, "work")
        assert credential_key(MCP_URL, "work") != credential_key(f"{MCP_URL}?profile=work")

    @pytest.mark.parametrize("bad", ["", 3, ["work"]])
    def test_a_record_with_a_malformed_profile_is_unreadable(self, tmp_path, bad):
        store = RecordStore(tmp_path)
        store.save(_record("work-token", profile="work"))
        path = store.path(MCP_URL, "work")
        data = json.loads(path.read_text())
        data["profile"] = bad
        path.write_text(json.dumps(data))
        assert store.load(MCP_URL, "work") is None

    def test_a_record_without_the_field_reads_as_the_default_credential(self, tmp_path):
        """What every record written before profiles existed looks like."""
        store = RecordStore(tmp_path)
        store.save(_record("default-token"))
        path = store.path(MCP_URL)
        data = json.loads(path.read_text())
        del data["profile"]
        path.write_text(json.dumps(data))
        assert store.load(MCP_URL).access_token == "default-token"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_a_profile_is_accepted(self):
        assert resolve_oauth(_profiled("work")) == {"profile": "work"}

    @pytest.mark.parametrize("bad", ["", "   ", " work", "work ", 3, True, ["work"]])
    def test_a_malformed_profile_fails_closed(self, bad):
        with pytest.raises(McpOAuthConfigError, match="oauth.profile"):
            resolve_oauth({"url": MCP_URL, "oauth": {"profile": bad}})


# ---------------------------------------------------------------------------
# End to end: two entries, one URL
# ---------------------------------------------------------------------------


def _token(header):
    return header.split(" ", 1)[1] if header else None


def test_two_profiles_on_one_url_stay_logged_in_as_two_accounts(runtime, origin):
    manager = McpClientManager(
        {"work": _profiled("work"), "personal": _profiled("personal")},
        oauth_runtime=runtime,
    )
    with patched(origin):
        manager.connect_all()
        assert manager.get_client("work").status == ServerStatus.NEEDS_AUTH
        assert manager.get_client("personal").status == ServerStatus.NEEDS_AUTH

        assert manager.login("work", FakeLoginUI(origin)) == ServerStatus.CONNECTED
        assert manager.login("personal", FakeLoginUI(origin)) == ServerStatus.CONNECTED
        work = runtime.store.load(MCP_URL, "work")
        personal = runtime.store.load(MCP_URL, "personal")
        assert work.access_token != personal.access_token
        # The second login did not replace the first.
        assert runtime.store.load(MCP_URL) is None

        origin.mcp_auth_headers.clear()
        assert manager.call_tool("work", "echo", {}) == "ok"
        assert {_token(h) for h in origin.mcp_auth_headers} == {work.access_token}
        origin.mcp_auth_headers.clear()
        assert manager.call_tool("personal", "echo", {}) == "ok"
        assert {_token(h) for h in origin.mcp_auth_headers} == {personal.access_token}

        # Logging out of one profile leaves the other logged in and connected.
        assert manager.logout("personal") is True
        assert runtime.store.load(MCP_URL, "personal") is None
        assert runtime.store.load(MCP_URL, "work").access_token == work.access_token
        origin.mcp_auth_headers.clear()
        assert manager.call_tool("work", "echo", {}) == "ok"
        assert {_token(h) for h in origin.mcp_auth_headers} == {work.access_token}
        manager.disconnect_all()


def test_a_profile_does_not_borrow_the_urls_default_login(runtime, origin):
    """An entry with a profile starts logged out even when the URL's default
    credential exists: falling back to it would act as the wrong account."""
    access, _ = origin.issue("seed")
    runtime.store.save(_record(access))
    manager = McpClientManager(
        {"default": {"url": MCP_URL, "timeout": 10}, "work": _profiled("work")},
        oauth_runtime=runtime,
    )
    with patched(origin):
        manager.connect_all()
        assert manager.get_client("default").status == ServerStatus.CONNECTED
        assert manager.get_client("work").status == ServerStatus.NEEDS_AUTH

        assert manager.login("work", FakeLoginUI(origin)) == ServerStatus.CONNECTED
        # The default entry still uses the credential it had.
        origin.mcp_auth_headers.clear()
        assert manager.call_tool("default", "echo", {}) == "ok"
        assert {_token(h) for h in origin.mcp_auth_headers} == {access}

        assert manager.logout("work") is True
        assert runtime.store.load(MCP_URL).access_token == access
        manager.disconnect_all()


def test_a_login_written_by_another_process_wakes_only_its_own_profile(runtime, origin):
    """A server waiting for login reconnects once its own credential file
    changes (``agentao mcp login`` from a shell, while the REPL runs), and not
    when another profile's does."""
    manager = McpClientManager(
        {"work": _profiled("work"), "personal": _profiled("personal")},
        oauth_runtime=runtime,
    )
    with patched(origin):
        manager.connect_all()
        assert manager.get_client("personal").status == ServerStatus.NEEDS_AUTH

        work_token, _ = origin.issue("seed")
        runtime.store.save(_record(work_token, profile="work"))
        out = manager.call_tool("personal", "echo", {})
        assert out.startswith("MCP auth error"), out

        personal_token, _ = origin.issue("seed")
        runtime.store.save(_record(personal_token, profile="personal"))
        origin.mcp_auth_headers.clear()
        assert manager.call_tool("personal", "echo", {}) == "ok"
        assert personal_token in {_token(h) for h in origin.mcp_auth_headers}
        manager.disconnect_all()
