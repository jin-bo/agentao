# 6.3 Network & SSRF Defense

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

Agentao's `web_fetch` uses `httpx`, defaults:

- 10s timeout
- Follow up to 3 redirects
- TLS verification on
- Customizable User-Agent

**Security note**: redirects let a 302 jump into an internal address, bypassing the hostname check. Production should **disable redirects** or **re-run the domain rule on each hop**. Agentao does not currently re-check across redirects — a known limitation.

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

Registering this **overwrites** the built-in:

```python
agent = Agentao(...)
agent.tools.register(StrictWebFetchTool())   # warning: overwriting
```

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

## Common pitfalls

### ❌ Allowlist without blocklist

```json
{"tool": "web_fetch", "domain": {"allowlist": [".github.com"]}, "action": "allow"}
// No blocklist → other URLs fall to default ASK → user may click to allow internal access
```

Always pair with explicit blocklist + default deny.

### ❌ Trusting the LLM not to hit internal

Prompt injection can **trick** the LLM into any URL. Don't rely on LLM common sense — rely on rules and infrastructure.

### ❌ Unprotected redirects

`web_fetch https://good.com` → 302 → `http://169.254.169.254/` follows. Production should override with redirect-disabled `web_fetch`.

→ [6.4 Multi-Tenant & Filesystem](./4-multi-tenant-fs)
