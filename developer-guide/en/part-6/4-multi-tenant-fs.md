# 6.4 Multi-Tenant & Filesystem Isolation

> **What you'll learn**
> - The cross-tenant bleed pattern (and why it's almost always a config issue, not a bug)
> - The three FS isolation layers: `working_directory` → user namespace → container/VM
> - A pre-launch checklist to confirm you're tenant-safe before going live

The most common security incident in multi-tenant agent embeddings: **cross-tenant data bleed**. Root cause is usually **sharing `working_directory`** or **sharing process-level resources**, not a code vulnerability.

## The golden rule: one session = one directory = one instance

```
❌ Wrong: shared cwd
┌─────────────────────────────────┐
│   Process                        │
│  ┌───────────┐  ┌───────────┐   │
│  │ agent_A   │  │ agent_B   │   │
│  │  ↓        │  │  ↓        │   │
│  │ Path.cwd()│◄─┤ Path.cwd()│   │
│  └───────────┘  └───────────┘   │
│        └────────────────┘        │
│  Read each other's .agentao/memory.db │
└─────────────────────────────────┘

✅ Right: explicit isolation
┌─────────────────────────────────┐
│   Process                        │
│  ┌───────────┐  ┌───────────┐   │
│  │ agent_A   │  │ agent_B   │   │
│  │ cwd=/A    │  │ cwd=/B    │   │
│  └───────────┘  └───────────┘   │
│      ↓              ↓            │
│  /data/tenant-A  /data/tenant-B │
└─────────────────────────────────┘
```

**Mandatory**: always pass `working_directory=Path(...)` explicitly. Don't omit, don't trust the default.

## Directory layouts

### Layout A · One directory per tenant

```
/data/
├── tenant-acme/
│   ├── AGENTAO.md            ← acme's project doc
│   ├── .agentao/
│   │   ├── memory.db          ← memory
│   │   ├── mcp.json           ← MCP config (add-only — see below)
│   │   └── sandbox.json       ← sandbox rules
│   ├── skills/                ← skills
│   └── workspace/             ← agent-writable temp area
├── tenant-globex/
│   └── ...
```

Construction:

```python
agent = Agentao(working_directory=Path(f"/data/tenant-{tenant.id}"))
```

**Benefits**: directory-anchored config (memory, MCP add-on servers, skills, sandbox, project doc) is auto-isolated — no tenant_id filtering logic in code.

::: warning Permissions don't live here
There is **no `permissions.json`** in the per-tenant directory. The engine deliberately ignores `<wd>/.agentao/permissions.json` (a checked-in allow-rule could escalate any tenant). For per-tenant policy, build a `PermissionEngine` programmatically and inject it:

```python
engine = PermissionEngine(
    project_root=Path(f"/data/tenant-{tenant.id}"),
    user_root=None,  # do not load any file
)
engine.add_loaded_source(f"injected:tenant-{tenant.id}")
# ... apply tenant-specific rules via your own layer ...
agent = Agentao(
    working_directory=Path(f"/data/tenant-{tenant.id}"),
    permission_engine=engine,
)
```

Project `mcp.json` is loaded as **add-only**: a tenant directory may declare new MCP servers, but cannot override a user-scope entry with the same name (collisions warn and skip).
:::

### Layout B · Ephemeral workdir

Create a fresh temp directory per session, clean up on exit:

```python
from pathlib import Path
from tempfile import mkdtemp
import shutil

def make_session_workdir(tenant_id: str, user_id: str) -> Path:
    root = Path(mkdtemp(prefix=f"agentao-{tenant_id}-{user_id}-"))
    template = Path(f"/data/tenant-{tenant_id}/template")
    (root / "AGENTAO.md").write_text((template / "AGENTAO.md").read_text())
    shutil.copytree(template / "skills", root / "skills")
    return root

def cleanup_session_workdir(workdir: Path):
    shutil.rmtree(workdir, ignore_errors=True)
```

**Benefits**: session over → files gone. **Cost**: config loaded per session, slightly slower.

## The user-level memory trap

```python
# What the factory wires by default — note the user_store path.
from agentao.memory import MemoryManager, SQLiteMemoryStore
agent._memory_manager = MemoryManager(
    project_store=SQLiteMemoryStore.open_or_memory(
        working_directory / ".agentao" / "memory.db"
    ),
    user_store=SQLiteMemoryStore.open(
        Path.home() / ".agentao" / "memory.db"   # ← process-global!
    ),
)
```

Even with `working_directory` isolated, `~/.agentao/memory.db` is **process-global** — two tenants' agents read/write the same user-level memory DB.

**Solution A · Capability injection** (preferred for multi-tenant in-process):

```python
from pathlib import Path
from agentao import Agentao
from agentao.memory import MemoryManager, SQLiteMemoryStore

agent = Agentao(
    working_directory=tenant_dir,
    filesystem=YourTenantFS(),          # agentao.capabilities.FileSystem impl
    memory_manager=MemoryManager(
        project_store=SQLiteMemoryStore.open_or_memory(
            tenant_dir / ".agentao" / "memory.db"
        ),
        # user_store=None disables the process-global ~/.agentao/memory.db
    ),
)
```

Constructor injection replaces private attribute mutation (`agent._memory_manager = …`) — inject once at construction, no shared mutable state between tenants.

The `FileSystem` Protocol (`agentao.capabilities.FileSystem`) covers all file and search tool IO. Any compliant implementation works as a drop-in: a Docker-exec remote that delegates reads/writes into a container, an in-memory virtual filesystem for test isolation, or an audit proxy that logs every access before delegating to the real disk — without changing any tool code.

**Solution B · One process per tenant** (strongest isolation, highest cost):

Use ACP — each tenant gets its own Agentao subprocess. Cleanest isolation; most resource-intensive.

**Solution C · Mutate HOME per tenant** (discouraged):

```python
import os
os.environ["HOME"] = f"/data/tenant-{tenant.id}/home"
agent = Agentao(working_directory=...)
```

Affects the whole process's `Path.home()` — only works with one tenant per process (ACP subprocess model). **Do not use in multi-tenant in-process deployments.**

## Write boundaries

By default the agent can write anywhere the permission rules allow. **Multi-tenant production** should restrict:

```json
{
  "rules": [
    {
      "tool": "write_file",
      "args": {"path": "^/data/tenant-${TENANT_ID}/"},
      "action": "allow"
    },
    {"tool": "write_file", "action": "deny"}
  ]
}
```

Pair with the **sandbox** (6.2) for defense in depth.

### Dynamic rule generation

Rule JSON doesn't support variable expansion. For per-tenant `${TENANT_ID}` injection, use **programmatic permissions**:

```python
import re

engine = PermissionEngine(project_root=workdir)
engine.rules.insert(0, {
    "tool": "write_file",
    "args": {"path": f"^{re.escape(str(workdir))}/"},
    "action": "allow",
})
engine.rules.append({"tool": "write_file", "action": "deny"})

agent = Agentao(working_directory=workdir, permission_engine=engine)
```

## MCP cross-tenant pollution

A single MCP server instance **should not be shared** across tenants — it may cache data, have connection pools, bind a single credential.

**Correct pattern**: per-tenant MCP subprocess:

```python
agent = Agentao(
    working_directory=workdir,
    extra_mcp_servers={
        "github": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": tenant.github_token},   # per tenant
        },
    },
)
```

`agent.close()` disconnects MCP subprocesses on shutdown.

If the host owns the MCP lifecycle entirely — managing server startup order, shared transport pools, or custom auth — pass a fully-built `McpClientManager` via `mcp_manager=` instead:

```python
from agentao.mcp import McpClientManager

manager = McpClientManager(...)   # built and connected by the host
agent = Agentao(
    working_directory=workdir,
    mcp_manager=manager,           # agent does not start/stop subprocesses
)
```

`mcp_manager=` and `extra_mcp_servers=` are mutually exclusive; passing both raises `ValueError`.

## Warm state: logs & temp files

### agentao.log

Defaults to `<working_directory>/agentao.log` — auto-isolated. **Don't** redirect it back to a global path (e.g. `/var/log/agentao.log`) or you mix tenants' logs.

### Python temp files

The LLM may invoke `tempfile.mkdtemp()` → `/tmp` by default, **visible across tenants**. Production recommendations:

- Mount per-container isolated `/tmp` (`--tmpfs /tmp`)
- Force `TMPDIR=<working_directory>/tmp` in the agent's environment

### MCP subprocess cwd

MCP subprocesses inherit the parent's cwd by default. If `working_directory` isn't threaded through, cross-tenant bleed happens. Agentao auto-merges session cwd into `extra_mcp_servers`, but **when you write your own MCP server** respect the incoming environment.

## Tenant boundaries for DB / API calls

Not an Agentao issue, but critical: **your custom tools** (Tools calling DB/API) must carry their own `tenant_id` guard — never trust LLM-provided args.

```python
class GetUserTool(Tool):
    def __init__(self, db, tenant_id):
        super().__init__()
        self.db = db
        self.tenant_id = tenant_id   # bound at construction

    def execute(self, user_id: str, **kw) -> str:
        # ✅ Use tenant_id from construction, not kwargs
        user = self.db.get_user(user_id, tenant_id=self.tenant_id)
        ...
```

**Bind `tenant_id` to the Tool instance**, don't expose it to the LLM — prompt injection can't escalate.

## Self-check

Before deployment, answer "if two tenants use the product simultaneously, could they…":

- [ ] Read each other's AGENTAO.md? (check `working_directory`)
- [ ] Read each other's memory? (check project + global memory DB paths)
- [ ] Read each other's permission rules? (check `PermissionEngine.project_root`)
- [ ] Read each other's skills? (check SkillManager's 3 layers)
- [ ] Share an MCP server process? (per-session `extra_mcp_servers`?)
- [ ] Share `/tmp`? (container / isolation)
- [ ] Cross-tenant queries from business tools? (tenant_id guard in Tool)
- [ ] Mixed logs? (agentao.log path)

## TL;DR

- **One session = one `working_directory` = one `Agentao` instance.** Never share an agent across tenants — even briefly.
- Three layers of isolation, stack as needed: per-session `working_directory`, OS user namespace, container/VM.
- Memory user-scope (`~/.agentao/memory.db`) is **process-global** — disable it in multi-tenant or key memories by `tenant_id+user_id`.
- Custom Tools that capture `tenant_id` at construction must be **created per session**, not pulled from a process-wide pool.

→ [6.5 Secrets & Prompt Injection](./5-secrets-injection)
