# 5.3 MCP Server Integration

> **What you'll learn**
> - When MCP is the right answer vs. a custom Tool
> - The three transport types Agentao supports (stdio, Streamable HTTP, SSE)
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

**Locations** (user wins on name collision; project file is **add-only**):

```
~/.agentao/mcp.json               ← user-level (cross-project; authoritative for any name it declares)
<working_dir>/.agentao/mcp.json   ← project-level (may declare new names only — cannot override user-scope names)
```

::: tip Why add-only?
A project `mcp.json` checked into git could otherwise silently redirect a known server name (e.g. `github`) to a different transport or endpoint. Collisions log a warning and skip the project entry. Rename the project entry or remove the user-scope one to resolve.
:::

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
      "type": "sse",
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

### Streamable HTTP transport (remote service, default for a bare `url`)

| Field | Required | Purpose |
|-------|----------|---------|
| `url` | ✅ | Streamable HTTP endpoint URL |
| `type` | ❌ | `"http"` (aliases: `"streamable-http"` / `"streamable_http"`); this is the default when a bare `url` is given |
| `headers` | ❌ | HTTP headers; supports `${VAR}` expansion |
| `timeout` | ❌ | Seconds, default 60 |
| `trust` | ❌ | Same as above |

### SSE transport (remote service, legacy)

| Field | Required | Purpose |
|-------|----------|---------|
| `url` | ✅ | SSE endpoint URL |
| `type` | ✅ | Must be `"sse"` — SSE is the legacy transport (deprecated in MCP spec 2025-03-26). A bare `url` now defaults to Streamable HTTP, so SSE **must** be selected explicitly. |
| `headers` | ❌ | HTTP headers; supports `${VAR}` expansion |
| `timeout` | ❌ | Seconds, default 60 |
| `trust` | ❌ | Same as above |

All three transports also take two keys for the features below:

| Field | Default | Purpose |
|-------|---------|---------|
| `resources` | `true` | Any other value hides this server from the resource tools and `/mcp resources`, and refuses it by name |
| `skills` | `false` | Only `true` turns on the MCP Skills extension for this server |

::: info Streamable HTTP is supported
Agentao's MCP client imports `stdio_client`, `sse_client`, **and** `streamable_http_client` (plus `create_mcp_http_client`), so all three transports connect. `type` selects the transport (`"stdio"` / `"http"` / `"sse"`); if omitted it's inferred — `command` → stdio, a bare `url` → **Streamable HTTP**. Legacy SSE requires an explicit `"type": "sse"`. An unknown `type` or a missing required key fails closed (`McpTransportConfigError`). The ACP handshake advertises `mcpCapabilities.http: true`.
:::

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

## MCP resources

A server that declares the `resources` capability also exposes data the model can read. When at least one connected server does, three read-only tools are registered (names and shapes follow codex and pi, so models already know them):

| Tool | Parameters | Returns |
|---|---|---|
| `list_mcp_resources` | `server?`, `cursor?` | JSON `{server?, resources: [{server, uri, name, …}], nextCursor?, errors?}` |
| `list_mcp_resource_templates` | `server?`, `cursor?` | JSON `{server?, resourceTemplates: [{server, uriTemplate, name, …}], nextCursor?, errors?}` |
| `read_mcp_resource` | `server`, `uri` (both required) | the contents |

- Without `server`, a listing walks every server; one server's failure goes into `errors`. A `cursor` needs a `server`.
- Text comes back as text. A binary resource is saved under `<working_directory>/.agentao/tool-outputs/` (0600) and its path returned; a blob over 10 MiB is reported, never decoded.
- They are read-only: allowed in read-only and plan mode, never confirmed. A permission rule naming one still applies, and `disable_tools` / `enabled_tools` accept the three names.
- `"resources": false` on a server hides it from all three (and refuses it by name).
- A tool result's `resource_link` keeps its URI and, when the model can follow it, names `read_mcp_resource` with the server.

The same operations are public on the manager, for a host building its own picker:

```python
page = agent.mcp_manager.list_resources("docs")            # ResourcePage
templates = agent.mcp_manager.list_resource_templates("docs")
read = agent.mcp_manager.read_resource("docs", "report://q3")  # ResourceRead (text or base64 blob)
```

Each raises `agentao.mcp.resources.McpResourceError` (with a `kind`) instead of returning partial data. In the CLI, `/mcp resources [server]` lists them without spending a model turn.

## MCP skills

A server can also publish Agent Skills through the MCP Skills extension (`io.modelcontextprotocol/skills`). It is **off by default**: set `"skills": true` on the server in `mcp.json`. It needs mcp 2.x and a server on protocol 2026-07-28 or later.

- **Catalogue.** At connect the server's skills are listed (no file is fetched). Each one is named `mcp:<server>:<SKILL.md URI>` — the identity spelled out, so two same-named skills never collide — and appears in the system prompt in its own section, labelled as written by that server.
- **Activation.** `activate_skill("mcp:docs:skill://pdf-processing/SKILL.md")` asks the user once per session, then fetches `SKILL.md` and verifies its size, digest and frontmatter against the server's entry. The body reaches the model inside `<mcp-skill server="…" uri="…">`. A skill missing from the listing can still be loaded by that name.
- **Files.** `read_skill_file(skill, path)` reads a file of an active MCP skill from that skill's own server, verified, and refuses a path that is not in the manifest. A `read_mcp_resource` inside a loaded skill's directory is verified the same way.
- **Gates.** While any MCP skill is loaded (until `/clear`, not merely until deactivation), `run_shell_command`, spawning a sub-agent that can run shell commands, and `read_mcp_resource` on another server are asked, in every permission mode. They only tighten: a `deny` rule or read-only mode still denies.
- **Sub-agents** share the session's loaded skills, so the gates hold inside them too.
- **Hosts with no person to ask.** Activation and every gated call need a person. `NullTransport`, `SdkTransport` with no `confirm_tool`, and `build_compat_transport` with no `confirmation_callback` refuse them. A host that remembers "always allow" must read `agentao.transport.gate_note()` (4.5).
- **Session restore.** `restore_agent_skills` never re-activates an MCP skill, withholds MCP skill content from the restored messages, and turns the gates back on (2.4).
- **Limits.** At most 512 files and 16 MiB per skill. A `SKILL.md` over 100,000 bytes is listed as unavailable, because an active skill's `SKILL.md` is sent with every request. `read_skill_file` returns at most 30,000 characters per call, with `offset` and `limit` to page.
- **Where the content is kept.** Skill content is never saved to `.agentao/tool-outputs/`. Replay files and `agentao.log` keep tool results verbatim, MCP skill content included, and a later `read_file` of them is not gated.

A host reads the same data from the manager:

```python
agent.mcp_manager.skill_servers()                 # servers that passed the gate
entries, unavailable, problem = agent.mcp_manager.skill_listing("docs")
entry = agent.mcp_manager.get_skill("docs", "skill://pdf-processing/SKILL.md")  # skills/get
```

`/skills` lists MCP skills per server, with the reason any of them cannot be loaded; `/mcp list` shows each opted-in server's skill count, or why it has none.

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

### MCP tool annotations: `readOnlyHint` / `destructiveHint`

If a server declares standard MCP `ToolAnnotations`, Agentao consults them — **but only when `trust: true`** (per the MCP spec: clients must not make tool-use decisions on annotations from untrusted servers).

| `trust` | annotation | effect |
|---|---|---|
| `false` (default) | any | hints ignored — server may lie; confirmation always required |
| `true` | `readOnlyHint: true` | `is_read_only=True` (read-only mode permits the call), no confirmation |
| `true` | `destructiveHint: true` | confirmation required — overrides the trust default |
| `true` | none / neither | current trusted behavior — no confirmation |

This is a **security-positive** wiring: hints can *add* friction (a trusted server flagging an op as destructive triggers confirmation) but never *remove* friction on the untrusted path. Hosts can read the raw dict via `McpTool.mcp_annotations` for richer projection.

## ⚠️ Common pitfalls

::: warning Don't ship without these
- ❌ **Server fails but the agent keeps going silently** — no surfaced error in `agent.chat()` if MCP init fails
- ❌ **Tool name too long** — Agentao cuts it and adds a hash; permission rules must use the registered name
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

Some MCP servers have long tool names. With the `mcp_{server}_` prefix, they may exceed OpenAI's function-call name length (64 chars). Agentao cuts such a name and appends an 8-character hash of the original server and tool names (the same on every connect), so the tool still works — but a permission rule or sub-agent `tools:` entry must use the registered name, which `agentao.log` records (`Registered MCP tool: …`).

### ❌ `trust: true` too permissive

Don't lightly set `trust: true` on servers that can write/delete — you bypass all safety prompts. Only for pure-read servers or servers with their own robust permission layer.

## TL;DR

- Use **MCP** to consume an existing third-party tool ecosystem (GitHub, filesystem, Postgres, Slack…); use **custom Tool** for your own business logic.
- Three transports: **stdio** subprocess, **Streamable HTTP** URL (default for a bare `url`), or legacy **SSE** URL (`"type":"sse"`).
- Per-tenant tokens: pass `extra_mcp_servers` at construction (`{name: {command, args, env}}`); merges over `.agentao/mcp.json`. Project `.agentao/mcp.json` is **add-only** — it cannot override a user-scope name.
- Tool naming: `mcp_{server}_{tool}` — auto-prefixed to avoid name collisions across servers. A server or tool name with non-ASCII characters gets the same 8-character hash suffix as an over-long one (`查询` and `搜索` would otherwise both become `__`), so its registered name changes on upgrade. Two tools that still map to one name (`my-srv` and `my_srv`) are not both registered: the first keeps it, the other is left out with an error in `agentao.log`.
- Never set `trust: true` on write-capable servers — it bypasses confirmation entirely.
- A server's **resources** become three read-only tools (`"resources": false` hides them). Its **skills** are off until `"skills": true`, and while one is loaded, shell and cross-server reads ask in every mode.

→ Next: [5.4 Permission Engine](./4-permissions)
