"""Unit tests for the outbound-URL SSRF policy.

Covers every bypass class that the static PermissionEngine string blocklist
cannot catch and that ``validate_outbound_url`` / ``guarded_get`` close:
IP-literal encodings (decimal/short/v4-mapped-v6), hostname normalization
(trailing dot / case), local & internal names, single-label hosts, embedded
credentials, non-http schemes, DNS rebinding (name resolves to a private
address), and redirect hops into the internal network.

Resolution and HTTP are faked so the suite is hermetic — no network. The
fake resolver maps hostnames to IP strings; numeric/short host forms
(``127.1``, ``2130706433``) are validated through that resolver exactly as
``socket.getaddrinfo`` would resolve them at runtime.
"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import threading
import time

import pytest

from agentao.security import UrlPolicyError, guarded_get, validate_outbound_url
from agentao.security import url_policy


@pytest.fixture
def fake_resolver(monkeypatch):
    """Install a hostname -> [ip, ...] resolver in place of getaddrinfo."""
    table: dict[str, list[str]] = {}

    def _resolve(host, port):
        return {ipaddress.ip_address(ip) for ip in table.get(host, [])}

    monkeypatch.setattr(url_policy, "_resolve_host_addresses", _resolve)
    return table


# --------------------------------------------------------------------------
# IP literals — no resolution needed
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.2/",            # loopback range, not just .1
        "http://0.0.0.0/",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://10.0.0.5/",             # private
        "http://[::1]/",                # v6 loopback
        "http://[::ffff:127.0.0.1]/",   # v4-mapped v6 loopback
    ],
)
def test_blocks_non_public_ip_literals(url):
    with pytest.raises(UrlPolicyError):
        validate_outbound_url(url)


def test_allows_public_ip_literal():
    validate_outbound_url("http://8.8.8.8/")  # no raise


@pytest.mark.parametrize(
    "url",
    [
        "http://[64:ff9b::169.254.169.254]/",  # NAT64 -> metadata
        "http://[64:ff9b::7f00:1]/",           # NAT64 -> 127.0.0.1
        "http://[2002:a9fe:a9fe::]/",          # 6to4 -> 169.254.169.254
    ],
)
def test_blocks_ipv6_with_embedded_private_ipv4(url):
    # is_global is True for these v6 forms; a NAT64/6to4 gateway would route
    # them to the embedded (private/metadata) IPv4.
    with pytest.raises(UrlPolicyError):
        validate_outbound_url(url)


def test_allows_ipv6_translation_embedding_public_ipv4():
    # NAT64/6to4 wrapping a *public* v4 is a legitimate public target.
    validate_outbound_url("http://[64:ff9b::8.8.8.8]/")  # no raise
    validate_outbound_url("http://[2002:0808:0808::]/")  # 6to4 -> 8.8.8.8


# --------------------------------------------------------------------------
# Hostname classification — no resolution needed
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://LOCALHOST/admin",       # case
        "http://localhost./admin",      # trailing dot (FQDN form)
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://foo.internal/",
        "http://foo.local/",
        "http://svc.cluster.local/",
        "http://myinternalbox/secret",  # single-label
    ],
)
def test_blocks_local_and_internal_names(url):
    with pytest.raises(UrlPolicyError):
        validate_outbound_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "http://user:pass@example.com/",  # embedded credentials
        "http://",                        # no host
    ],
)
def test_blocks_bad_scheme_or_credentials(url):
    with pytest.raises(UrlPolicyError):
        validate_outbound_url(url)


# --------------------------------------------------------------------------
# Resolution-dependent — DNS rebinding and numeric host forms
# --------------------------------------------------------------------------

def test_blocks_name_resolving_to_loopback(fake_resolver):
    fake_resolver["evil.example.com"] = ["127.0.0.1"]
    with pytest.raises(UrlPolicyError):
        validate_outbound_url("http://evil.example.com/")


def test_blocks_name_resolving_to_metadata(fake_resolver):
    fake_resolver["rebind.example.com"] = ["169.254.169.254"]
    with pytest.raises(UrlPolicyError):
        validate_outbound_url("http://rebind.example.com/")


def test_blocks_numeric_host_forms_via_resolution(fake_resolver):
    # urlparse/ip_address don't parse these as literals; getaddrinfo does.
    fake_resolver["127.1"] = ["127.0.0.1"]
    fake_resolver["2130706433"] = ["127.0.0.1"]
    with pytest.raises(UrlPolicyError):
        validate_outbound_url("http://127.1/")
    with pytest.raises(UrlPolicyError):
        validate_outbound_url("http://2130706433/")


def test_allows_public_name(fake_resolver):
    fake_resolver["example.com"] = ["93.184.216.34"]
    validate_outbound_url("http://example.com/")  # no raise


def test_blocks_unresolvable_name(fake_resolver):
    # Empty resolution -> reject rather than fall through.
    with pytest.raises(UrlPolicyError):
        validate_outbound_url("http://nope.example.com/")


def test_blocks_when_any_resolved_address_is_private(fake_resolver):
    # Mixed result set: one public, one loopback -> blocked.
    fake_resolver["mixed.example.com"] = ["93.184.216.34", "127.0.0.1"]
    with pytest.raises(UrlPolicyError):
        validate_outbound_url("http://mixed.example.com/")


def test_unresolvable_or_invalid_host_fails_closed():
    # A real getaddrinfo call (no fake_resolver): an over-long DNS label raises
    # UnicodeError (IDNA), not gaierror — must be caught and treated as "no
    # address" -> UrlPolicyError, never an escaping raw exception.
    with pytest.raises(UrlPolicyError):
        validate_outbound_url("http://" + "a" * 64 + ".invalid/")


def test_explicit_port_zero_is_not_treated_as_default(fake_resolver):
    # Regression: `parsed.port or default` would swallow an explicit :0.
    # Validation should still classify by the resolved address, not crash.
    fake_resolver["example.com"] = ["93.184.216.34"]
    validate_outbound_url("http://example.com:0/")  # no raise


# --------------------------------------------------------------------------
# guarded_get — per-redirect-hop re-validation
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code, location=None):
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self.closed = False

    def close(self):
        self.closed = True


class _FakeClient:
    """Returns queued responses keyed by requested URL."""

    def __init__(self, responses):
        self._responses = dict(responses)
        self.requested: list[str] = []

    def get(self, url, headers=None):
        self.requested.append(url)
        return self._responses[url]


def test_guarded_get_returns_final_non_redirect(fake_resolver):
    fake_resolver["example.com"] = ["93.184.216.34"]
    client = _FakeClient({"http://example.com/": _FakeResponse(200)})
    resp = guarded_get(client, "http://example.com/")
    assert resp.status_code == 200
    assert client.requested == ["http://example.com/"]


def test_guarded_get_follows_public_redirect(fake_resolver):
    fake_resolver["a.example.com"] = ["93.184.216.34"]
    fake_resolver["b.example.com"] = ["93.184.216.35"]
    client = _FakeClient(
        {
            "http://a.example.com/": _FakeResponse(302, "http://b.example.com/"),
            "http://b.example.com/": _FakeResponse(200),
        }
    )
    resp = guarded_get(client, "http://a.example.com/")
    assert resp.status_code == 200
    assert client.requested == ["http://a.example.com/", "http://b.example.com/"]


def test_guarded_get_blocks_redirect_to_internal(fake_resolver):
    # Public first hop 302-redirects to the cloud metadata endpoint.
    fake_resolver["a.example.com"] = ["93.184.216.34"]
    client = _FakeClient(
        {
            "http://a.example.com/": _FakeResponse(
                302, "http://169.254.169.254/latest/meta-data/"
            ),
        }
    )
    with pytest.raises(UrlPolicyError):
        guarded_get(client, "http://a.example.com/")


def test_guarded_get_resolves_relative_redirect(fake_resolver):
    fake_resolver["a.example.com"] = ["93.184.216.34"]
    client = _FakeClient(
        {
            "http://a.example.com/start": _FakeResponse(302, "/next"),
            "http://a.example.com/next": _FakeResponse(200),
        }
    )
    resp = guarded_get(client, "http://a.example.com/start")
    assert resp.status_code == 200
    assert client.requested[-1] == "http://a.example.com/next"


def test_guarded_get_redirect_budget(fake_resolver):
    fake_resolver["loop.example.com"] = ["93.184.216.34"]
    # Always redirects to itself -> exhausts the budget.
    client = _FakeClient(
        {"http://loop.example.com/": _FakeResponse(302, "http://loop.example.com/")}
    )
    with pytest.raises(UrlPolicyError):
        guarded_get(client, "http://loop.example.com/", max_redirects=3)


# --------------------------------------------------------------------------
# Opt-in CIDR allowlist (AGENTAO_WEB_FETCH_ALLOW_CIDRS)
# --------------------------------------------------------------------------

_FAKE_IP_RANGE = (ipaddress.ip_network("198.18.0.0/15"),)


def test_read_allow_cidrs_empty_when_unset(monkeypatch):
    monkeypatch.delenv("AGENTAO_WEB_FETCH_ALLOW_CIDRS", raising=False)
    assert url_policy.read_allow_cidrs_setting() == ()


def test_read_allow_cidrs_parses_list_and_bare_ip(monkeypatch):
    monkeypatch.setenv(
        "AGENTAO_WEB_FETCH_ALLOW_CIDRS", "198.18.0.0/15, 10.1.2.3  192.168.0.0/16"
    )
    nets = url_policy.read_allow_cidrs_setting()
    assert ipaddress.ip_network("198.18.0.0/15") in nets
    assert ipaddress.ip_network("10.1.2.3/32") in nets  # bare IP -> /32
    assert ipaddress.ip_network("192.168.0.0/16") in nets


def test_read_allow_cidrs_skips_invalid_tokens(monkeypatch):
    monkeypatch.setenv("AGENTAO_WEB_FETCH_ALLOW_CIDRS", "198.18.0.0/15, not-an-ip, ::1/999")
    nets = url_policy.read_allow_cidrs_setting()
    assert nets == (ipaddress.ip_network("198.18.0.0/15"),)


def test_allowlist_permits_otherwise_blocked_literal():
    # 198.18.0.0/15 is reserved (is_global=False) → blocked by default…
    with pytest.raises(UrlPolicyError):
        validate_outbound_url("http://198.18.0.114/")
    # …but permitted when explicitly allowlisted.
    validate_outbound_url("http://198.18.0.114/", allow_networks=_FAKE_IP_RANGE)  # no raise


def test_allowlist_is_scoped_metadata_still_blocked():
    # Allowlisting the fake-IP range must NOT permit the metadata endpoint.
    with pytest.raises(UrlPolicyError):
        validate_outbound_url(
            "http://169.254.169.254/", allow_networks=_FAKE_IP_RANGE
        )


def test_allowlist_can_permit_metadata_if_explicitly_listed():
    # The operator's explicit choice is honored (host owns its endpoint).
    net = (ipaddress.ip_network("169.254.169.254/32"),)
    validate_outbound_url("http://169.254.169.254/", allow_networks=net)  # no raise


def test_allowlist_applies_to_resolved_hostname(fake_resolver):
    fake_resolver["proxied.example.com"] = ["198.18.0.114"]
    with pytest.raises(UrlPolicyError):
        validate_outbound_url("http://proxied.example.com/")
    validate_outbound_url(
        "http://proxied.example.com/", allow_networks=_FAKE_IP_RANGE
    )  # no raise


def test_allowlist_v4_cidr_does_not_crash_on_v6_address(fake_resolver):
    # Version-mismatched compare must be skipped, not raise; the v6 loopback
    # stays blocked because it isn't in the (v4) allowlist.
    fake_resolver["v6.example.com"] = ["::1"]
    with pytest.raises(UrlPolicyError):
        validate_outbound_url(
            "http://v6.example.com/", allow_networks=_FAKE_IP_RANGE
        )


def test_allowlist_matches_embedded_ipv4_in_nat64():
    # The effective (embedded) IPv4 is matched, so a NAT64 form embedding an
    # allowlisted IPv4 is permitted.
    validate_outbound_url(
        "http://[64:ff9b::198.18.0.114]/", allow_networks=_FAKE_IP_RANGE
    )  # no raise


def test_guarded_get_allowlist_threads_to_redirect_hop(fake_resolver):
    fake_resolver["a.example.com"] = ["93.184.216.34"]
    fake_resolver["proxied.example.com"] = ["198.18.0.114"]
    client = _FakeClient(
        {
            "http://a.example.com/": _FakeResponse(302, "http://proxied.example.com/"),
            "http://proxied.example.com/": _FakeResponse(200),
        }
    )
    # Without the allowlist the internal redirect hop is blocked…
    with pytest.raises(UrlPolicyError):
        guarded_get(client, "http://a.example.com/")
    # …with it, the hop is permitted and the chase completes.
    client2 = _FakeClient(
        {
            "http://a.example.com/": _FakeResponse(302, "http://proxied.example.com/"),
            "http://proxied.example.com/": _FakeResponse(200),
        }
    )
    resp = guarded_get(client2, "http://a.example.com/", allow_networks=_FAKE_IP_RANGE)
    assert resp.status_code == 200


def test_webfetch_tool_wires_allowlist_into_description(monkeypatch):
    from agentao.tools.web import WebFetchTool

    monkeypatch.delenv("AGENTAO_WEB_FETCH_FALLBACK", raising=False)
    monkeypatch.setenv("AGENTAO_WEB_FETCH_ALLOW_CIDRS", "198.18.0.0/15")
    tool = WebFetchTool()
    assert tool._allow_cidrs == (ipaddress.ip_network("198.18.0.0/15"),)
    assert "SSRF allowlist active" in tool.description
    assert "198.18.0.0/15" in tool.description


# --------------------------------------------------------------------------
# Async surface — same policy, off the loop thread
#
# The async twins exist because `getaddrinfo` blocks: on an event-loop thread a
# slow resolver stalls every other task, including the timers a caller relies on
# for its own deadline. These tests assert the pair cannot diverge — same
# verdicts, same per-hop re-validation — plus the two properties only the async
# form has: the lookup does not run on the loop thread, and every way it can go
# wrong arrives as a rejection.
# --------------------------------------------------------------------------


class _FakeAsyncResponse(_FakeResponse):
    def __init__(self, status_code, location=None):
        super().__init__(status_code, location)
        self.closed = False

    async def aclose(self):
        self.closed = True


class _FakeAsyncClient:
    """Async twin of `_FakeClient`."""

    def __init__(self, responses):
        self._responses = dict(responses)
        self.requested: list[str] = []

    async def get(self, url, headers=None):
        self.requested.append(url)
        return self._responses[url]


def test_validate_async_accepts_what_the_sync_form_accepts(fake_resolver):
    fake_resolver["example.com"] = ["93.184.216.34"]
    asyncio.run(url_policy.validate_outbound_url_async("http://example.com/"))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:127.0.0.1]/",
        "ftp://example.com/",
        "http://user:pw@example.com/",
        "http://localhost/",
        "http://single-label/",
    ],
)
def test_validate_async_rejects_what_the_sync_form_rejects(url):
    """The async form delegates rather than reimplementing, so this is a
    regression guard on that delegation, not a second copy of the policy."""
    with pytest.raises(UrlPolicyError):
        validate_outbound_url(url)
    with pytest.raises(UrlPolicyError):
        asyncio.run(url_policy.validate_outbound_url_async(url))


def test_validate_async_resolves_off_the_loop_thread(monkeypatch):
    """A blocking resolver on the loop thread stalls the whole loop."""
    seen: dict[str, object] = {}

    def _resolve(host, port):
        seen["thread"] = threading.current_thread()
        return {ipaddress.ip_address("93.184.216.34")}

    monkeypatch.setattr(url_policy, "_resolve_host_addresses", _resolve)

    async def drive():
        await url_policy.validate_outbound_url_async("http://example.com/")
        return threading.current_thread()

    loop_thread = asyncio.run(drive())
    assert seen["thread"] is not loop_thread


def test_validate_async_keeps_the_loop_responsive_and_fails_closed(monkeypatch):
    """A stalled lookup must not freeze the loop, hold up shutdown, or pass."""
    stall = 3.0

    def _stalling_resolver(host, port):
        time.sleep(stall)
        return {ipaddress.ip_address("93.184.216.34")}

    monkeypatch.setattr(url_policy, "_resolve_host_addresses", _stalling_resolver)

    async def drive():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        spinner = asyncio.create_task(ticker())
        try:
            with pytest.raises(UrlPolicyError):
                await url_policy.validate_outbound_url_async(
                    "http://slow.example.com/", timeout=0.05
                )
        finally:
            spinner.cancel()
        return ticks

    started = time.monotonic()
    ticks = asyncio.run(drive())
    elapsed = time.monotonic() - started

    assert ticks > 0, "the event loop was blocked by the resolver"
    # `asyncio.run` joins the *default* executor at loop shutdown, so
    # `asyncio.to_thread` would pay the full stall here even though `wait_for`
    # already returned. A daemon thread is abandoned instead.
    assert elapsed < stall / 2, f"shutdown waited on the stalled lookup ({elapsed:.2f}s)"


def test_validate_async_refusal_over_the_thread_cap_is_a_rejection(monkeypatch):
    """Over the process-wide cap the check is refused — and a refusal blocks."""
    monkeypatch.setattr(url_policy, "_POLICY_THREAD_HARD_CAP", 0)

    with pytest.raises(UrlPolicyError) as excinfo:
        asyncio.run(url_policy.validate_outbound_url_async("http://example.com/"))
    assert "refusing" in str(excinfo.value)


def test_a_failed_thread_start_does_not_leak_a_cap_slot(monkeypatch):
    """`Thread.start()` can fail — at the OS thread limit, for one.

    The slot is reserved before the thread exists, and the runner's `finally` is
    the only thing that releases it. If `start()` raises, that `finally` never
    runs, so each failure would burn a slot permanently: the cap fills up and
    every later check is refused for the life of the process, long after the
    pressure cleared. Fail closed on the call, not on all future ones.
    """
    before = url_policy._live_policy_threads

    class _RefusingThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(url_policy.threading, "Thread", _RefusingThread)

    for _ in range(3):
        with pytest.raises(UrlPolicyError) as excinfo:
            asyncio.run(url_policy.validate_outbound_url_async("http://example.com/"))
        assert "can't start new thread" in str(excinfo.value)

    assert url_policy._live_policy_threads == before, (
        "a failed thread start leaked a slot from the process-wide cap"
    )
    assert url_policy._live_policy_threads >= 0, (
        "the counter went negative — the cap is now permanently disabled, which "
        "is a fail-*open*: nothing else bounds the abandoned lookup threads"
    )

    # And the very next check, with threads working again, must go through.
    monkeypatch.undo()
    monkeypatch.setattr(
        url_policy, "_resolve_host_addresses",
        lambda host, port: {ipaddress.ip_address("93.184.216.34")},
    )
    asyncio.run(url_policy.validate_outbound_url_async("http://example.com/"))


def test_validate_async_converts_an_unexpected_resolver_error(monkeypatch):
    """Fail closed: anything the resolver throws becomes a rejection, never a
    pass, and never an exception shape the caller has to know about."""
    def _explode(host, port):
        raise OSError("resolver blew up")

    monkeypatch.setattr(url_policy, "_resolve_host_addresses", _explode)
    with pytest.raises(UrlPolicyError) as excinfo:
        asyncio.run(url_policy.validate_outbound_url_async("http://example.com/"))
    assert "resolver blew up" in str(excinfo.value)


def test_guarded_get_async_returns_final_non_redirect(fake_resolver):
    fake_resolver["example.com"] = ["93.184.216.34"]
    client = _FakeAsyncClient({"http://example.com/": _FakeAsyncResponse(200)})
    resp = asyncio.run(url_policy.guarded_get_async(client, "http://example.com/"))
    assert resp.status_code == 200
    assert client.requested == ["http://example.com/"]


def test_guarded_get_async_follows_public_redirect(fake_resolver):
    fake_resolver["a.example.com"] = ["93.184.216.34"]
    fake_resolver["b.example.com"] = ["93.184.216.35"]
    first = _FakeAsyncResponse(302, "http://b.example.com/")
    client = _FakeAsyncClient(
        {
            "http://a.example.com/": first,
            "http://b.example.com/": _FakeAsyncResponse(200),
        }
    )
    resp = asyncio.run(url_policy.guarded_get_async(client, "http://a.example.com/"))
    assert resp.status_code == 200
    assert client.requested == ["http://a.example.com/", "http://b.example.com/"]
    # The abandoned hop must be released — `aclose`, not the sync `close`.
    assert first.closed is True


def test_guarded_get_async_blocks_redirect_to_internal(fake_resolver):
    """The hole the whole per-hop design exists to close."""
    fake_resolver["a.example.com"] = ["93.184.216.34"]
    fake_resolver["internal.example.com"] = ["10.0.0.5"]
    client = _FakeAsyncClient(
        {"http://a.example.com/": _FakeAsyncResponse(302, "http://internal.example.com/")}
    )
    with pytest.raises(UrlPolicyError):
        asyncio.run(url_policy.guarded_get_async(client, "http://a.example.com/"))
    assert "http://internal.example.com/" not in client.requested


def test_guarded_get_async_resolves_relative_redirect(fake_resolver):
    fake_resolver["a.example.com"] = ["93.184.216.34"]
    client = _FakeAsyncClient(
        {
            "http://a.example.com/start": _FakeAsyncResponse(302, "/next"),
            "http://a.example.com/next": _FakeAsyncResponse(200),
        }
    )
    resp = asyncio.run(
        url_policy.guarded_get_async(client, "http://a.example.com/start")
    )
    assert resp.status_code == 200
    assert client.requested[-1] == "http://a.example.com/next"


def test_guarded_get_async_redirect_budget(fake_resolver):
    fake_resolver["loop.example.com"] = ["93.184.216.34"]
    client = _FakeAsyncClient(
        {"http://loop.example.com/": _FakeAsyncResponse(302, "http://loop.example.com/")}
    )
    with pytest.raises(UrlPolicyError):
        asyncio.run(
            url_policy.guarded_get_async(
                client, "http://loop.example.com/", max_redirects=3
            )
        )


def test_guarded_get_async_threads_the_allowlist_to_every_hop(fake_resolver):
    fake_resolver["a.example.com"] = ["93.184.216.34"]
    fake_resolver["proxied.example.com"] = ["198.18.0.42"]
    client = _FakeAsyncClient(
        {"http://a.example.com/": _FakeAsyncResponse(302, "http://proxied.example.com/")}
    )
    with pytest.raises(UrlPolicyError):
        asyncio.run(url_policy.guarded_get_async(client, "http://a.example.com/"))

    client2 = _FakeAsyncClient(
        {
            "http://a.example.com/": _FakeAsyncResponse(302, "http://proxied.example.com/"),
            "http://proxied.example.com/": _FakeAsyncResponse(200),
        }
    )
    resp = asyncio.run(
        url_policy.guarded_get_async(
            client2, "http://a.example.com/", allow_networks=_FAKE_IP_RANGE
        )
    )
    assert resp.status_code == 200


def test_redirect_decision_is_shared_by_both_surfaces():
    """`_redirect_target` is the single answer to "is this a hop?".

    Two copies of that predicate is how per-hop re-validation silently stops
    covering a status one side forgot.
    """
    assert "_REDIRECT_STATUSES" in inspect.getsource(url_policy._redirect_target)
    for fn in (url_policy.guarded_get, url_policy.guarded_get_async):
        body = inspect.getsource(fn)
        assert "_redirect_target(" in body, f"{fn.__name__} bypasses the shared helper"
        # Not a count over the whole module — a mention in a comment elsewhere
        # is not a defect. What matters is that neither chase decides for itself.
        assert "_REDIRECT_STATUSES" not in body
        assert "301" not in body and "302" not in body


def _redirect_handler(request):
    import httpx

    if request.url.path == "/start":
        return httpx.Response(302, headers={"location": "https://8.8.8.8/next"})
    return httpx.Response(200, text="final")


def test_both_surfaces_chase_a_redirect_through_the_real_httpx():
    """Drive the chase with real `httpx.Response` objects, not the fakes above.

    The fakes implement `close` / `aclose` by construction, so they can only
    confirm that the code calls *something*. What they cannot answer is whether
    httpx tolerates being asked to release a response `client.get()` has already
    read to completion — `Response.aclose()` raises `RuntimeError` outright when
    handed a sync stream, and a redirect hop is the only place either function
    releases anything. A public IP literal is used so no DNS is involved.
    """
    import httpx

    async def via_async():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(_redirect_handler),
            follow_redirects=False,
            timeout=5.0,
        ) as client:
            return await url_policy.guarded_get_async(client, "https://8.8.8.8/start")

    with httpx.Client(
        transport=httpx.MockTransport(_redirect_handler),
        follow_redirects=False,
        timeout=5.0,
    ) as client:
        sync_response = guarded_get(client, "https://8.8.8.8/start")

    async_response = asyncio.run(via_async())

    assert sync_response.status_code == async_response.status_code == 200
    assert sync_response.text == async_response.text == "final"


def test_async_call_sites_match_the_real_httpx():
    """Probe the installed httpx rather than trusting the fakes above.

    Hand-written fakes answer whatever the code asks them, so they can only
    confirm what the author already believed. `guarded_get_async` depends on
    three things being true of the real client: `AsyncClient(...)` takes the
    kwargs `web_fetch` passes, `.get()` is awaitable, and a response is released
    with `aclose()` — the sync `close()` would be a silent connection leak.
    """
    import httpx

    params = inspect.signature(httpx.AsyncClient.__init__).parameters
    assert "follow_redirects" in params
    assert "timeout" in params
    assert inspect.iscoroutinefunction(httpx.AsyncClient.get)
    assert inspect.iscoroutinefunction(httpx.Response.aclose)
    # And the sync pair still exists, since `guarded_get` remains supported.
    assert not inspect.iscoroutinefunction(httpx.Client.get)
