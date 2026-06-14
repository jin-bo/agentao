"""SSRF policy for outbound web tools.

The PermissionEngine domain blocklist (``permissions.py``) is a static,
plan-phase **string** check on the URL the model passes. By design it does
no I/O, so it cannot catch:

* hostnames that *resolve* to a private/loopback/link-local address — i.e.
  DNS rebinding, or a public name pointed at ``127.0.0.1`` /
  ``169.254.169.254`` (cloud metadata);
* alternate encodings of a blocked address (``2130706433`` and ``127.1``
  both resolve to ``127.0.0.1``; v4-mapped / 6to4 / NAT64 IPv6 forms such as
  ``[::ffff:127.0.0.1]`` and ``[64:ff9b::169.254.169.254]``);
* a **redirect hop** to an internal target after an allowed first URL.

This module is the execute-phase complement. It normalizes the hostname,
rejects local/internal names, and — for non-literal hosts — resolves the
host and rejects any non-global address. It is applied to the initial URL
*and every redirect hop* (see :func:`guarded_get`). The two layers are
defense in depth: the static blocklist stays in the PermissionEngine (fast,
no I/O, runs before the tool); resolution lives here, in the tool.

Scope is deliberately narrow:

* The legitimate-private-target escape hatch is the PermissionEngine
  **allowlist**, not a flag here — there is intentionally no per-call
  bypass.
* Resolution uses :func:`socket.getaddrinfo`, the same resolver ``httpx``
  uses to connect, so the address we validate is the address the fetch
  connects to. A residual TOCTOU window remains (DNS could change between
  this check and the connection); closing it fully would require pinning
  the resolved IP and connecting to it with a ``Host`` header. We match the
  resolve-then-check level rather than pin, which already closes every
  string-bypass and redirect vector above.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING, Optional
from urllib.parse import urljoin, urlparse

if TYPE_CHECKING:
    import httpx

_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_DEFAULT_PORTS = {"http": 80, "https": 443}

# Exact hostnames that must never be reached even though they may resolve to
# a public-looking address (or not resolve at all on the host running us).
_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}

# Suffixes for cloud/k8s/mDNS-internal names. These overlap the
# PermissionEngine blocklist (``.internal`` / ``.local``) on purpose — this
# module must stand alone when a host has customised or dropped the presets.
_LOCAL_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".cluster.local",
)

# Bound the manual redirect chase. Matches httpx's own prior default of 20 so
# a legitimate long redirect chain (CDN / auth flows) isn't turned into a hard
# error; each hop costs a DNS round-trip in validation, which 20 bounds.
_MAX_REDIRECTS = 20

# NAT64 well-known prefix (RFC 6052 / 6147). An address in this range embeds an
# IPv4 in its low 32 bits that a NAT64 gateway routes to — including
# 169.254.169.254 (cloud metadata) or 127.0.0.1.
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


class UrlPolicyError(ValueError):
    """Raised when an outbound URL is rejected by the SSRF policy."""


def _parse_ip_literal(value: str) -> Optional[_IPAddress]:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _embedded_ipv4(address: _IPAddress) -> Optional[ipaddress.IPv4Address]:
    """Return an IPv4 embedded in an IPv6 address via a translation mechanism.

    Covers v4-mapped (``::ffff:a.b.c.d``), 6to4 (``2002::/16``) and NAT64
    (``64:ff9b::/96``); returns ``None`` for a plain IPv4/IPv6 address.

    Without this, an IPv6 literal can smuggle a private/loopback IPv4 past an
    ``is_global`` check: the IPv6 form reports ``is_global=True`` while a
    NAT64/6to4 gateway on the host network routes it to the embedded IPv4.
    """
    if not isinstance(address, ipaddress.IPv6Address):
        return None
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in _NAT64_PREFIX:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def _is_disallowed(address: _IPAddress) -> bool:
    """True if ``address`` is not a globally routable public address.

    An IPv6 address carrying an embedded IPv4 (v4-mapped / 6to4 / NAT64) is
    judged by that embedded IPv4 — where a translator actually routes it — so a
    loopback or metadata target can't be smuggled through the v6 form.
    """
    embedded = _embedded_ipv4(address)
    if embedded is not None:
        return not embedded.is_global
    return not address.is_global


def _normalized_hostname(hostname: str) -> str:
    # Trailing dot ("localhost.") is a fully-qualified form that bypasses an
    # exact-string blocklist but resolves identically; case is irrelevant.
    return hostname.rstrip(".").lower()


def validate_outbound_url(url: str) -> None:
    """Reject loopback / private / link-local / internal HTTP(S) targets.

    Raises :class:`UrlPolicyError` for a non-http(s) scheme, embedded
    credentials, a missing/invalid host or port, a local/internal hostname,
    a single-label hostname (resolves via the host's search domain — never a
    real public target), an IP literal that is not globally routable, or a
    hostname that resolves to any non-global address.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS:
        raise UrlPolicyError(f"only http/https URLs are allowed: {url!r}")
    if parsed.username or parsed.password:
        raise UrlPolicyError("URLs with embedded credentials are not allowed")

    raw_host = parsed.hostname
    if not raw_host:
        raise UrlPolicyError(f"URL has no host: {url!r}")
    hostname = _normalized_hostname(raw_host)

    literal = _parse_ip_literal(hostname)
    if literal is not None:
        if _is_disallowed(literal):
            raise UrlPolicyError(
                f"target is a non-public address: {hostname}"
            )
        return

    if hostname in _LOCAL_HOSTNAMES or any(
        hostname.endswith(suffix) for suffix in _LOCAL_HOST_SUFFIXES
    ):
        raise UrlPolicyError(f"local/internal hostnames are not allowed: {hostname}")
    if "." not in hostname:
        raise UrlPolicyError(f"single-label hostnames are not allowed: {hostname}")

    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise UrlPolicyError(f"invalid port in URL: {url!r}") from exc
    # ``or`` would treat an explicit ``:0`` as absent; use ``is not None``.
    port = explicit_port if explicit_port is not None else _DEFAULT_PORTS[scheme]

    addresses = _resolve_host_addresses(hostname, port)
    if not addresses:
        raise UrlPolicyError(f"host did not resolve: {hostname}")
    blocked = sorted({str(a) for a in addresses if _is_disallowed(a)})
    if blocked:
        rendered = ", ".join(blocked[:3]) + (", ..." if len(blocked) > 3 else "")
        raise UrlPolicyError(
            f"{hostname} resolves to non-public address(es): {rendered}"
        )


def _resolve_host_addresses(host: str, port: int) -> set[_IPAddress]:
    """Resolve ``host`` to its concrete IP addresses via ``getaddrinfo``."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        # gaierror (an OSError subclass), socket.timeout, and IDNA UnicodeError
        # (e.g. a DNS label >63 chars) all mean "no usable address" — fail
        # closed by returning none so the caller rejects the URL.
        return set()
    addresses: set[_IPAddress] = set()
    for info in infos:
        sockaddr = info[4]
        candidate = sockaddr[0] if sockaddr else None
        if not isinstance(candidate, str):
            continue
        parsed = _parse_ip_literal(candidate)
        if parsed is not None:
            addresses.add(parsed)
    return addresses


def guarded_get(
    client: "httpx.Client",
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    max_redirects: int = _MAX_REDIRECTS,
) -> "httpx.Response":
    """GET ``url`` with SSRF validation on the initial URL and every hop.

    ``client`` MUST be constructed with ``follow_redirects=False`` so this
    function controls the chase: each ``Location`` is resolved against the
    current URL and re-validated before the next request. The final
    non-redirect response is returned (caller handles status / body).

    Raises :class:`UrlPolicyError` on a disallowed target (initial or any
    hop) or when the redirect budget is exhausted.
    """
    current = url
    for _ in range(max_redirects + 1):
        validate_outbound_url(current)
        response = client.get(current, headers=headers)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if location:
                response.close()
                current = urljoin(current, location)
                continue
        return response
    raise UrlPolicyError(f"too many redirects ({max_redirects}) for {url!r}")
