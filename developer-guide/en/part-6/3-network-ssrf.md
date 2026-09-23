# 6.3 Network & SSRF Defense

> **What you'll learn**
> - Why agents are perfect SSRF probes and what the default blocklist covers
> - The four-layer network defense: domain rules → redirect blocking → egress firewall → DNS rebinding
> - Production patterns: VPC egress allowlist, redirect-disabled `web_fetch`

Three agent tools reach the network: `web_fetch`, `web_search`, and anything via MCP. This section shrinks their attack surface to the minimum necessary.

## Three network layers

```
   LLM → web_fetch / web_search / MCP
                │
                ▼
  ┌──────────────────────────────┐
  │ Layer 1: PermissionEngine    │  .github.com allow / 127.0.0.1 deny
  │          allowlist/blocklist  │
  └──────────────────────────────┘
                │
                ▼
  ┌──────────────────────────────┐
  │ Layer 2: HTTP client (httpx) │  TLS, timeout, redirect policy
  └──────────────────────────────┘
                │
                ▼
  ┌──────────────────────────────┐
  │ Layer 3: Network boundary     │  VPC / egress rules / firewall
  │          (infrastructure)     │
  └──────────────────────────────┘
```

## Layer 1 · Domain rules (permission engine)

The SSRF blocklist in WORKSPACE_WRITE is worth **keeping + extending** in every project:

```json
{
  "tool": "web_fetch",
  "domain": {
    "blocklist": [
      "localhost",
      "127.0.0.1",
      "0.0.0.0",
      "169.254.169.254",   // AWS/GCP metadata
      ".internal",
      ".local",
      "::1"                 // IPv6 localhost
    ]
  },
  "action": "deny"
}
```

**Production extensions**:

```json
{
  "tool": "web_fetch",
  "domain": {
    "blocklist": [
      "localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254",
      "::1", ".internal", ".local",
      // Your internal subnets — literal string matches only
      "10.", "172.16.", "192.168.",
      // Your company internal domains
      ".corp.your-company.com",
      ".internal.your-company.com",
      // Cloud provider metadata
      "metadata.google.internal",
      "metadata.azure.com"
    ]
  },
  "action": "deny"
}
```

⚠️ **Limitation**: `_extract_domain` parses URL hostnames and does string prefix/exact matching. Attackers can bypass with **decimal IPs** (e.g. `http://2130706433` = `127.0.0.1`), **IPv6 forms**, or **DNS rebinding**. Production **must** add Layer 3 (infra-level isolation) as a safety net.

### Conservative allowlist mode

Safer approach: **default deny**, explicit allow:

```json
{
  "rules": [
    {"tool": "web_fetch", "domain": {"allowlist": [".your-docs-site.com", ".github.com"]}, "action": "allow"},
    {"tool": "web_fetch", "action": "deny"}
  ]
}
```

Customer-facing products should generally default to blocklist; internal tools may use a looser allowlist.

## Layer 2 · HTTP client behavior

Agentao's `web_fetch` uses `httpx` through `agentao/security/url_policy.py::guarded_get_async`:

- 30 s per-operation timeout, and a 60 s ceiling on the whole fetch (every hop plus the body) — a server that keeps trickling bytes resets the per-operation timeout on each read, so only the ceiling ends it
- Up to 20 redirects, chased by agentao rather than httpx: **each hop is resolved and re-validated** against the SSRF policy (and `AGENTAO_WEB_FETCH_ALLOW_CIDRS`), and a hop's body is released unread
- The final body is read up to **5 MiB decoded**; a declared `Content-Length` over that is refused before any byte is read, and a larger body — declared or not, compressed or not — ends the fetch with an error and no fallback. The Jina fallback reads under the same cap and ceiling. The Playwright fallback is not covered: its page lives in the browser process
- TLS verification on
- Customizable User-Agent

**Security note**: redirect re-validation is per hop, but it re-runs the **SSRF policy**, not the permission engine's domain rules — a 302 from an allowed domain to a public host your rules would have asked about is followed. If that matters, the override below or Layer 3 is the answer.

You can **override `web_fetch`** with your own stricter version:

```python
from agentao.tools.base import Tool
import httpx

class StrictWebFetchTool(Tool):
    @property
    def name(self) -> str:
        return "web_fetch"

    def execute(self, url: str, **kw) -> str:
        with httpx.Client(follow_redirects=False, timeout=5.0) as client:
            resp = client.get(url)
            if resp.status_code // 100 == 3:
                return "Redirects are disabled for security. URL: " + url
            return resp.text[:50000]   # cap length
    # ...other methods
```

Inject it via `extra_tools=` — a same-named entry **replaces** the built-in silently, and the tool gets the same capability binding (`working_directory` / `filesystem` / `shell`) as built-ins:

```python
agent = Agentao(..., extra_tools=[StrictWebFetchTool()])   # replaces built-in web_fetch
```

Prefer this over poking `agent.tools.register(StrictWebFetchTool())` after construction: the low-level path skips capability binding (the tool goes "bare") and only warns on the collision. See [5.1](/en/part-5/1-custom-tools). Schema replacement is **defense in depth, not the boundary** — Layer 3 below is what actually stops egress.

## Layer 3 · Infrastructure isolation

This is the safety net — **even if all higher layers fail, the agent can't reach the forbidden thing**.

### Container network options

```bash
# Totally offline: agent can only talk to local services (e.g. MCP stdio)
docker run --network=none agent-image

# Custom net: egress allowlist only
docker run --network=custom-egress-only agent-image
```

### VPC egress allowlist

Put your agent container in a security group that only allows egress to:

- LLM APIs (OpenAI / Anthropic official IP ranges)
- Required MCP SSE endpoints
- Allowlisted documentation sites (`.github.com`, `.pypi.org`, etc.)

Deny everything else. Even if rules are bypassed, LLM-induced requests can't reach internal services.

### DNS-level filtering

Put internal domains on a DNS blocklist. The agent can't even resolve them.

## MCP server networks

MCP servers are often **higher risk than `web_fetch`** — they have their own credentials and access:

```json
{
  "mcpServers": {
    "database": {
      "command": "...",
      "env": {"DB_URL": "postgres://..."}   // DB access
    }
  }
}
```

**Controls**:

1. **Per-tenant MCP instances** — credentials isolated per tenant (see [5.3](/en/part-5/3-mcp))
2. **MCP subprocesses in their own net namespace** — on Linux: `unshare -n` or a container
3. **Rule-gate MCP tools too**:

```json
{
  "rules": [
    {"tool": "mcp_database_query", "args": {"sql": "^SELECT "}, "action": "allow"},
    {"tool": "mcp_database_*", "action": "deny"}
  ]
}
```

## ACP mode network considerations

Agentao as an ACP server **does not listen on any port** — stdio only. Good news:

- Hosts don't open inbound ports for the agent
- Attack surface is limited to **egress**

The agent still makes outbound LLM / web_fetch / MCP SSE calls — same layered policies apply.

## Audit logging

Every network call should log:

```python
def on_event(ev):
    if ev.type == EventType.TOOL_COMPLETE and ev.data["tool"] in {"web_fetch", "web_search"}:
        audit_log.info("network_call", extra={
            "tool": ev.data["tool"],
            "status": ev.data["status"],
            "duration_ms": ev.data["duration_ms"],
            "call_id": ev.data["call_id"],
            # URL cached from TOOL_START
        })
```

`agentao.log` already records tool call args — see [6.5 Secrets](./5-secrets-injection#four-log-scrubbing) for scrubbing.

## ⚠️ Common pitfalls

::: warning Don't ship without these
- ❌ **Allowlist without blocklist** — `*.example.com` allowed, but `169.254.169.254` not denied; agent reaches metadata IP via redirect
- ❌ **Trusting the LLM not to hit internal** — system prompts won't survive prompt injection; always enforce at rule layer
- ❌ **Unprotected redirects in a custom `web_fetch`** — the built-in re-validates every hop; an override using plain `httpx` with `follow_redirects=True` does not

Each pitfall below has the full fix.
:::

### ❌ Allowlist without blocklist

```json
{"tool": "web_fetch", "domain": {"allowlist": [".github.com"]}, "action": "allow"}
// No blocklist → other URLs fall to default ASK → user may click to allow internal access
```

Always pair with explicit blocklist + default deny.

### ❌ Trusting the LLM not to hit internal

Prompt injection can **trick** the LLM into any URL. Don't rely on LLM common sense — rely on rules and infrastructure.

### ❌ Unprotected redirects

The built-in `web_fetch` blocks `https://good.com` → 302 → `http://169.254.169.254/` at the hop. An override that calls `httpx` with `follow_redirects=True` follows it — disable redirects there, or chase them through `guarded_get` / `guarded_get_async` (pass `max_body_bytes=` too; the default is unbounded).

## TL;DR

- **The default SSRF blocklist already covers** localhost, `127.0.0.1`, `169.254.169.254` (cloud metadata), RFC1918 private ranges. Don't disable it.
- Layer 4 (rule engine) is **app-side**; Layer 7 (VPC / egress firewall) is **infra-side** — you need both. Apps can be tricked; infra is the hard wall.
- **Always disable HTTP redirects** in production `web_fetch` overrides — `https://good.com` → 302 → cloud-metadata IP is the classic bypass.
- For internal APIs the agent legitimately needs, write explicit allowlist domains; never widen the global blocklist.

→ [6.4 Multi-Tenant & Filesystem](./4-multi-tenant-fs)
