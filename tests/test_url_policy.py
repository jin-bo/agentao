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

import ipaddress

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
