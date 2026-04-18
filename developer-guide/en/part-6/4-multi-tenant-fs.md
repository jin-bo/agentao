# 6.4 Multi-Tenant & Filesystem Isolation

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
│   │   ├── permissions.json   ← permission rules
│   │   ├── mcp.json           ← MCP config
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

**Benefits**: all config, permissions, memory, skills are auto-isolated — no tenant_id filtering logic in code.

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
agent._memory_manager = MemoryManager(
    project_root=working_directory / ".agentao",
    global_root=Path.home() / ".agentao",   # ← process-global!
)
```

Even with `working_directory` isolated, `~/.agentao/memory.db` is **process-global** — two tenants' agents read/write the same user-level memory DB.

**Solution A · Disable user-level**:

```python
agent._memory_manager = MemoryManager(
    project_root=workdir / ".agentao",
    global_root=None,    # no user scope
)
```

**Solution B · Change HOME per tenant**:

```python
import os
os.environ["HOME"] = f"/data/tenant-{tenant.id}/home"
agent = Agentao(working_directory=...)
```

Affects the whole process's `Path.home()` — only works with **one tenant per process** (ACP subprocess model).

**Solution C · One process per tenant**:

Use ACP — each tenant gets its own Agentao subprocess. Cleanest isolation, highest cost.

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

→ [6.5 Secrets & Prompt Injection](./5-secrets-injection)
