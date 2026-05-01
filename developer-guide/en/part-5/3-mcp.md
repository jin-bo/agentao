# 5.3 MCP Server Integration

> **What you'll learn**
> - When MCP is the right answer vs. a custom Tool
> - The two transport types Agentao supports (stdio + SSE; **not** HTTP)
> - Multi-tenant patterns: per-session `extra_mcp_servers`, env-var expansion, `trust:` caveats

**MCP (Model Context Protocol)** is the de-facto standard for tool interoperability. Agentao is an MCP client — it can connect to any MCP-compliant server (GitHub, filesystem, Postgres, Slack, Jira, your own…), and every tool the server exposes shows up in the agent's registry as `mcp_{server}_{tool}` automatically.

## What MCP is good for

| Use case | Suggested MCP server |
|----------|---------------------|
| Read/write files / code repos | `@modelcontextprotocol/server-filesystem` |
| GitHub issues/PRs | `@modelcontextprotocol/server-github` |
| Database queries | `@modelcontextprotocol/server-postgres` |
| Slack / Linear / Jira | Official or community |
| Your internal tools | Write your own (see end of section) |

Win: **no `Tool` subclass needed** — the community has already written and maintains these.

## Two configuration modes

### Mode A · JSON config file

**Locations** (project overrides user):

```
~/.agentao/mcp.json               ← user-level (cross-project)
<working_dir>/.agentao/mcp.json   ← project-level (higher priority)
```

**Format**:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/code"],
      "env": {},
      "trust": false,
      "timeout": 60
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
      }
    },
    "analytics-sse": {
      "url": "https://mcp.your-company.com/sse",
      "headers": {
        "Authorization": "Bearer ${ANALYTICS_TOKEN}"
      },
      "timeout": 30
    }
  }
}
```

### Mode B · Programmatic (preferred for embedding)

Pass `extra_mcp_servers` to the constructor — **bypass JSON files entirely** and build configs per session/tenant:

```python
from agentao import Agentao

agent = Agentao(
    working_directory=Path(f"/tmp/tenant-{tenant.id}"),
    extra_mcp_servers={
        "github-per-tenant": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": tenant.github_token},
        },
    },
)
```

Merge rule: **same-name entries override** those from `.agentao/mcp.json`.

## Configuration fields

### Stdio transport (subprocess)

| Field | Required | Purpose |
|-------|----------|---------|
| `command` | ✅ | Executable (`npx`, `python`, absolute path) |
| `args` | ❌ | Command-line arguments |
| `env` | ❌ | Additional env vars; supports `$VAR` / `${VAR}` expansion |
| `cwd` | ❌ | Subprocess working directory |
| `timeout` | ❌ | Init timeout in seconds, default 60 |
| `trust` | ❌ | Skip confirmation when true |

### SSE transport (remote service)

| Field | Required | Purpose |
|-------|----------|---------|
| `url` | ✅ | SSE endpoint URL |
| `headers` | ❌ | HTTP headers; supports `${VAR}` expansion |
| `timeout` | ❌ | Seconds, default 60 |
| `trust` | ❌ | Same as above |

⚠️ **HTTP is not supported**: Agentao's MCP client only imports `stdio_client` and `sse_client`; `http`-type MCP servers can't connect (ACP handshake also advertises `mcpCapabilities.http: false`).

## Env var expansion

```json
"env": {
  "TOKEN": "${MY_TOKEN}",     // ${...} form
  "REGION": "$AWS_REGION"     // $... form
}
```

Expansion happens **when the config is loaded** — i.e. at agent construction. Expanded literals are passed to the subprocess env.

Unset variables become empty strings (no error).

## MCP tool naming

Each tool discovered from an MCP server becomes an Agentao `Tool` with a **prefixed name**:

```
Server: "github"
MCP tool: "create_issue"
Agentao name: "mcp_github_create_issue"
```

Characters outside `[a-zA-Z0-9_]` become underscores.

So:

- Don't name your own tools with the `mcp_` prefix (avoid confusion)
- Permission rules can match by prefix: `{"tool": "mcp_github_*", ...}`

## Debugging

```python
# List all discovered tools
for t in agent.tools.list_tools():
    if t.name.startswith("mcp_"):
        print(t.name, "—", t.description[:60])

# Check MCP manager state
if agent.mcp_manager:
    print(f"{len(agent.mcp_manager.clients)} server(s) connected")
```

`agentao.log` records:
- MCP server start success/failure
- Every tool discovered
- Tool call arguments and results

## Write your own MCP server (3 minutes)

Minimal MCP server in Python:

```python
# my_mcp_server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-internal-tools")

@mcp.tool()
def get_user_info(user_id: str) -> str:
    """Query internal user info by ID."""
    return my_backend.get_user(user_id).to_json()

@mcp.tool()
def send_notification(user_id: str, message: str) -> str:
    """Send an in-app notification to a user."""
    my_backend.notify(user_id, message)
    return "ok"

if __name__ == "__main__":
    mcp.run()   # stdio by default
```

Then in `.agentao/mcp.json`:

```json
{
  "mcpServers": {
    "internal": {
      "command": "python",
      "args": ["/path/to/my_mcp_server.py"]
    }
  }
}
```

After restarting, the agent auto-discovers `mcp_internal_get_user_info` and `mcp_internal_send_notification`.

## Multi-tenant strategy

Typical MCP usage in production SaaS:

| Server | Who writes it | Scope |
|--------|---------------|-------|
| Official/open-source (github, filesystem, postgres) | User / ops configures | Global or project JSON |
| Your own business MCP | You (Python/Node) | One instance per tenant, started via `extra_mcp_servers=` |
| Tenant-provided MCP | Tenant (via SaaS console) | Stored in DB, translated to `extra_mcp_servers=` at agent construction |

**Security essentials**:

- **Never** write tenant tokens/secrets into JSON files — inject via env var or `extra_mcp_servers`' `env`
- MCP subprocesses **inherit the parent process env** — make sure other tenants' creds aren't present
- One subprocess per session prevents cross-tenant state leakage

## Coordinating with the permission engine

MCP tools default to **needing confirmation** (equivalent to `requires_confirmation=True`), unless configured with `trust: true`:

```json
{
  "mcpServers": {
    "trusted-internal": {
      "command": "...",
      "trust": true     ← tools execute without confirm_tool
    }
  }
}
```

Or control with fine-grained permission rules:

```json
{
  "rules": [
    {"tool": "mcp_github_get_*", "action": "allow"},
    {"tool": "mcp_github_delete_*", "action": "deny"},
    {"tool": "mcp_github_create_*", "action": "ask"}
  ]
}
```

Full permission details: [5.4](./4-permissions).

## ⚠️ Common pitfalls

::: warning Don't ship without these
- ❌ **Server fails but the agent keeps going silently** — no surfaced error in `agent.chat()` if MCP init fails
- ❌ **Tool name too long** — provider truncation breaks function calling
- ❌ **`trust: true` set too permissively** — bypasses every safety prompt

Each pitfall below has the full fix.
:::

### ❌ Server fails but the agent keeps going silently

Agentao's MCP init is **fault-tolerant** — one failing server only logs a warning and doesn't block construction. Always check `agentao.log`:

```
MCP: failed to start 'github': ...
MCP: 12 tools from 2 server(s)       ← but you expected 3
```

Verify the expected number of servers are up before shipping.

### ❌ Tool name too long

Some MCP servers have long tool names. With the `mcp_{server}_` prefix, they may exceed OpenAI's function-call name length (64 chars). If the LLM refuses a tool, check the length.

### ❌ `trust: true` too permissive

Don't lightly set `trust: true` on servers that can write/delete — you bypass all safety prompts. Only for pure-read servers or servers with their own robust permission layer.

## TL;DR

- Use **MCP** to consume an existing third-party tool ecosystem (GitHub, filesystem, Postgres, Slack…); use **custom Tool** for your own business logic.
- Two transports: **stdio** subprocess or **SSE** URL. **HTTP is not supported.**
- Per-tenant tokens: pass `extra_mcp_servers` at construction (`{name: {command, args, env}}`); merges over `.agentao/mcp.json`.
- Tool naming: `mcp_{server}_{tool}` — auto-prefixed to avoid name collisions across servers.
- Never set `trust: true` on write-capable servers — it bypasses confirmation entirely.

→ Next: [5.4 Permission Engine](./4-permissions)
