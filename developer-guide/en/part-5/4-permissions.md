# 5.4 Permission Engine

**The PermissionEngine is Agentao's first line of defense.** It makes a rule-based decision **before** the tool actually runs, automatically handling "clearly safe" and "clearly dangerous" requests and leaving only edge cases to `confirm_tool()` ([Part 4.5](/en/part-4/5-tool-confirmation-ui)).

## Two-layer defense model

```
                ┌────────────────────────┐
 LLM → tool ──► │ 1. PermissionEngine    │──┬─ ALLOW → execute
                │   (JSON rules + preset)│  ├─ DENY  → refuse
                └────────────────────────┘  └─ ASK   ──┐
                                                       ▼
                                            ┌────────────────────┐
                                            │ 2. confirm_tool()   │
                                            │   (user UI prompt)  │
                                            └────────────────────┘
```

Layer 1 is **zero-latency** (rule match); layer 2 is **seconds** (user clicks). Good rules mean 90% of tool calls don't disturb the user at all.

## PermissionDecision

Source: `agentao/permissions.py:11-14`

```python
class PermissionDecision(Enum):
    ALLOW = "allow"   # execute
    DENY  = "deny"    # refuse (agent sees "cancelled" synthetic result)
    ASK   = "ask"     # route to confirm_tool
```

If no rule matches (`decide()` returns `None`), the agent falls back to the tool's own `requires_confirmation`.

## PermissionMode: four presets

Source: `agentao/permissions.py:17-21`

| Mode | Writes | Shell | Web | Best for |
|------|--------|-------|-----|----------|
| `READ_ONLY` | Deny | Deny | Deny | Read-only exploration, audit |
| `WORKSPACE_WRITE` | Allow | Rule-gated | Allowlist/blocklist/ask | **Default for production** |
| `FULL_ACCESS` | Allow | Allow | Allow | Dev / fully trusted |
| `PLAN` | Deny (only plan_*) | Deny (only git etc.) | Allowlist | Internal to Plan mode |

**Switching modes**:

```python
from agentao.permissions import PermissionMode

agent.permission_engine.set_mode(PermissionMode.READ_ONLY)
```

Switchable at runtime — takes effect on the next tool call.

## Rule JSON format

**Locations**:

```
~/.agentao/permissions.json           ← user-level
<cwd>/.agentao/permissions.json       ← project-level (higher priority)
```

**Structure**:

```json
{
  "rules": [
    {"tool": "read_file", "action": "allow"},
    {"tool": "write_file", "args": {"path": "^/tmp/"}, "action": "allow"},
    {"tool": "write_file", "action": "ask"},
    {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf"}, "action": "deny"},
    {"tool": "*", "action": "ask"}
  ]
}
```

**Evaluation order** (first match wins):

| Mode | Order |
|------|-------|
| FULL_ACCESS / PLAN | Preset rules → project JSON → user JSON |
| Others | Project JSON → user JSON → preset rules |

## Rule fields

### `tool` — tool name

```json
{"tool": "write_file"}       // exact
{"tool": "mcp_github_*"}      // wildcard prefix
{"tool": "*"}                 // all
```

### `args` — regex on arguments

Keys are arg names, values are Python regexes. **All** keys must match for the rule to fire:

```json
{
  "tool": "write_file",
  "args": {
    "path": "^/tmp/safe-dir/"     // path must start with /tmp/safe-dir/
  },
  "action": "allow"
}
```

Multiple args must all match:

```json
{
  "tool": "run_shell_command",
  "args": {
    "command": "^docker ",         // starts with docker
    "cwd": "^/var/app/"            // and cwd under /var/app/
  },
  "action": "allow"
}
```

### `domain` — URL host (for web_fetch etc.)

```json
{
  "tool": "web_fetch",
  "domain": {
    "allowlist": [".github.com", ".docs.python.org", "r.jina.ai"]
  },
  "action": "allow"
}
```

**Match semantics**:

| Pattern | Meaning |
|---------|---------|
| `.github.com` | **Suffix match**: matches `github.com` and `api.github.com`; does NOT match `notgithub.com` |
| `github.com` | **Exact**: only matches `github.com` itself |

A single domain rule can list both `allowlist` and `blocklist` — a hit on either counts (so to split allow/deny, write two rules, not one).

The built-in WORKSPACE_WRITE preset already ships a reasonable set (see below).

### `action`

```json
{"action": "allow"}   // default if omitted
{"action": "deny"}
{"action": "ask"}
```

## Preset cheat sheet

The WORKSPACE_WRITE preset (source `agentao/permissions.py:68-118`):

```json
[
  {"tool": "write_file", "action": "allow"},
  {"tool": "replace", "action": "allow"},
  // Read-only shell allowlist (git status/log/diff, ls, cat, echo, pwd, which, head, tail, …)
  {"tool": "run_shell_command", "args": {"command": "^(git (status|log|...)|ls\\b|cat\\b|...)"}, "action": "allow"},
  // Dangerous-command denylist (rm -rf, sudo, mkfs, dd if=)
  {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf|sudo\\s|mkfs|dd\\s+if="}, "action": "deny"},
  {"tool": "run_shell_command", "action": "ask"},
  {"tool": "web_fetch", "domain": {"allowlist": [".github.com", ".docs.python.org", ".wikipedia.org", "r.jina.ai", ".pypi.org", ".readthedocs.io"]}, "action": "allow"},
  {"tool": "web_fetch", "domain": {"blocklist": ["localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", ".internal", ".local", "::1"]}, "action": "deny"},
  {"tool": "web_fetch", "action": "ask"},
  {"tool": "google_web_search", "action": "ask"}
]
```

**SSRF protection**: the blocklist contains common internal / metadata addresses to prevent the agent from being prompt-injected into fetching internal services. **Extend** rather than shrink this list in production.

## Programmatic customization

Pass your own engine to the agent:

```python
from agentao import Agentao
from agentao.permissions import PermissionEngine, PermissionMode

engine = PermissionEngine(project_root=Path("/data/tenant-a"))
engine.set_mode(PermissionMode.WORKSPACE_WRITE)
# Add rules dynamically (e.g. based on tenant plan)
engine.rules.insert(0, {
    "tool": "mcp_slack_*",
    "action": "ask" if tenant.free_tier else "allow",
})

agent = Agentao(
    working_directory=Path("/data/tenant-a"),
    permission_engine=engine,
)
```

## Common templates

### Template A · Strict production (customer product)

```json
{
  "rules": [
    {"tool": "read_file", "action": "allow"},
    {"tool": "glob", "action": "allow"},
    {"tool": "grep", "action": "allow"},

    {"tool": "write_file", "args": {"path": "^/workspace/"}, "action": "allow"},
    {"tool": "write_file", "action": "deny"},

    {"tool": "run_shell_command", "action": "deny"},

    {"tool": "web_fetch", "domain": {"allowlist": [".your-company.com"]}, "action": "allow"},
    {"tool": "web_fetch", "action": "deny"},

    {"tool": "*", "action": "ask"}
  ]
}
```

### Template B · Dev sandbox

```json
{
  "rules": [
    {"tool": "run_shell_command", "args": {"command": "^docker |^npm |^python |^node "}, "action": "allow"},
    {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf /|sudo|mkfs"}, "action": "deny"},
    {"tool": "run_shell_command", "action": "ask"}
  ]
}
```

### Template C · CI / unattended

CI has no one to click "allow", so rules must be explicitly allow/deny — no "ask":

```json
{
  "rules": [
    {"tool": "write_file", "args": {"path": "^/tmp/ci/"}, "action": "allow"},
    {"tool": "read_file", "action": "allow"},
    {"tool": "glob", "action": "allow"},
    {"tool": "grep", "action": "allow"},
    {"tool": "*", "action": "deny"}
  ]
}
```

## Coordination with `confirm_tool`

When `decide()` returns `ASK`, the agent calls `transport.confirm_tool(...)`. Your UI only handles **edge cases** — no popup per tool call.

**Verify rules work**:

```python
from agentao.permissions import PermissionDecision

for tool, args in [
    ("write_file", {"path": "/tmp/safe.txt", "content": "..."}),
    ("write_file", {"path": "/etc/passwd", "content": "..."}),
    ("run_shell_command", {"command": "rm -rf /"}),
    ("web_fetch", {"url": "http://127.0.0.1:8080"}),
]:
    dec = engine.decide(tool, args)
    print(f"{tool}({args}) → {dec}")
```

Turn this into a unit test — guarantee the rules you expect to hit are hit before deploying.

## Common pitfalls

### ❌ Wrong rule order

```json
[
  {"tool": "write_file", "action": "ask"},
  {"tool": "write_file", "args": {"path": "^/tmp/"}, "action": "allow"}
]
```

The first rule matches every `write_file`, so the second is **never evaluated**. Put specifics first:

```json
[
  {"tool": "write_file", "args": {"path": "^/tmp/"}, "action": "allow"},
  {"tool": "write_file", "action": "ask"}
]
```

### ❌ No catch-all

Without `{"tool": "*", ...}`, unmatched tools fall back to each tool's `requires_confirmation` — the behavior may surprise you. **Prefer an explicit catch-all in production**.

### ❌ Unescaped regex

In JSON, backslashes double: `"rm\\s+-rf"`.

### ❌ `"github.com"` thinking it's a suffix match

Missing the leading dot means **exact**. For subdomain matches write `".github.com"`.

→ Next: [5.5 Memory System](./5-memory)
