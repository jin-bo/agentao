# 2.2 Constructor Reference

Full signature (`agentao/agent.py:71-93`):

```python
Agentao(
    api_key:          Optional[str]    = None,
    base_url:         Optional[str]    = None,
    model:            Optional[str]    = None,
    temperature:      Optional[float]  = None,
    # ── The 8 below are deprecated legacy callbacks (still accepted) ──
    confirmation_callback:      Optional[Callable] = None,
    max_context_tokens:         int                = 200_000,
    step_callback:              Optional[Callable] = None,
    thinking_callback:          Optional[Callable] = None,
    ask_user_callback:          Optional[Callable] = None,
    output_callback:            Optional[Callable] = None,
    tool_complete_callback:     Optional[Callable] = None,
    llm_text_callback:          Optional[Callable] = None,
    permission_engine:          Optional[PermissionEngine] = None,
    on_max_iterations_callback: Optional[Callable] = None,
    transport:                  Optional[Transport]        = None,
    plan_session:               Optional[PlanSession]      = None,
    *,
    working_directory:  Optional[Path]                     = None,
    extra_mcp_servers:  Optional[Dict[str, Dict[str, Any]]] = None,
)
```

## LLM credentials (first 4 params)

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `api_key` | `str` | `OPENAI_API_KEY` env | LLM credential |
| `base_url` | `str` | env or vendor default | Switch compatible endpoint (DeepSeek, Gemini gateway, vLLM…) |
| `model` | `str` | env or vendor default | Model id |
| `temperature` | `float` | env `LLM_TEMPERATURE` or `0.2` | Sampling temperature |

**Recommendation**: pass all four explicitly. Skipping env vars makes debugging and auditing cleaner:

```python
agent = Agentao(
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
    model=settings.openai_model,
    temperature=0.1,
    ...
)
```

For multi-tenant apps, each session can carry **different credentials** (e.g. per customer) — see Part 7.2.

## Transport (recommended)

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `transport` | `Transport` | `NullTransport()` | UI interaction + event stream |

One `Transport` covers all interaction: tool confirmations, user asks, event streaming, max-iteration fallback. **Skip it = auto-approve everything, no event listener** (that's `NullTransport`) — fine for headless batch jobs.

```python
from agentao.transport import SdkTransport

transport = SdkTransport(
    on_event=handle_event,
    confirm_tool=ask_approval,
    ask_user=prompt_user,
    on_max_iterations=lambda n, msgs: {"action": "stop"},
)
agent = Agentao(transport=transport, ...)
```

Implementing a custom Transport: see [Part 4](/en/part-4/).

## Deprecated 8 callbacks (legacy)

| Param | Replacement |
|-------|-------------|
| `confirmation_callback` | `SdkTransport(confirm_tool=...)` |
| `step_callback` | `on_event=` + listen for `TOOL_START` / `TURN_START` |
| `thinking_callback` | `on_event=` + `THINKING` |
| `ask_user_callback` | `SdkTransport(ask_user=...)` |
| `output_callback` | `on_event=` + `TOOL_OUTPUT` |
| `tool_complete_callback` | `on_event=` + `TOOL_COMPLETE` |
| `llm_text_callback` | `on_event=` + `LLM_TEXT` |
| `on_max_iterations_callback` | `SdkTransport(on_max_iterations=...)` |

**All 8 still work** — Agentao wraps them in a `build_compat_transport()` shim internally. New code should go straight through `Transport` to avoid two parallel channels.

## Runtime behavior

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `max_context_tokens` | `int` | `200_000` | Triggers compression (Part 7.3) |
| `plan_session` | `PlanSession` | `None` | Enables Plan mode; rarely needed in embedders |
| `permission_engine` | `PermissionEngine` | loaded from `.agentao/permissions.json` | Rule engine (Parts 5.4 / 6.3) |

## Session isolation (critical!)

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `working_directory` | `Path` | `None` (dynamic `Path.cwd()`) | **Must be set explicitly for multi-instance** |

**Why it matters**:
- `None` → agent reads live `Path.cwd()` on every access (CLI behavior; user `cd` is respected)
- `Path(...)` → agent freezes on that directory at construction; file tools, `AGENTAO.md`, `.agentao/` config, shell CWD all resolve against it

In embedded contexts (web server, ACP sessions), `Path.cwd()` is **process-global state** — two concurrent sessions will cross-contaminate. Always pass `working_directory=` explicitly per instance.

```python
# ❌ Bad: sessions share cwd
agent_a = Agentao(...)
agent_b = Agentao(...)

# ✅ Good: each session has its own root
agent_a = Agentao(..., working_directory=Path("/tmp/tenant-a"))
agent_b = Agentao(..., working_directory=Path("/tmp/tenant-b"))
```

## Session-scoped MCP servers

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `extra_mcp_servers` | `Dict[str, Dict]` | `None` | Programmatic MCP server injection |

Add MCP servers to a **single session** without touching the project's `.agentao/mcp.json`. Typical use: swap GitHub-token-per-tenant:

```python
agent = Agentao(
    working_directory=tenant_dir,
    extra_mcp_servers={
        "github-per-tenant": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": tenant.github_token},
        },
    },
)
```

Merge rule: **entries here override** same-named entries in `.agentao/mcp.json`.

## Full example: production embedding template

```python
from pathlib import Path
import os
from agentao import Agentao
from agentao.transport import SdkTransport
from agentao.permissions import PermissionEngine, PermissionMode

def make_agent_for_session(
    tenant_id: str,
    tenant_workdir: Path,
    tenant_token: str,
    on_event,
    confirm_tool,
) -> Agentao:
    # Per-session permission engine (tunable per tenant)
    engine = PermissionEngine(project_root=tenant_workdir)
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)

    transport = SdkTransport(
        on_event=on_event,
        confirm_tool=confirm_tool,
        on_max_iterations=lambda n, _msgs: {"action": "stop"},
    )

    return Agentao(
        # LLM
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL"),
        model="gpt-4o",
        temperature=0.1,
        # Interaction
        transport=transport,
        # Isolation
        working_directory=tenant_workdir,
        # Resource limits
        max_context_tokens=128_000,
        # Security
        permission_engine=engine,
        # Session-scoped MCP
        extra_mcp_servers={
            "gh": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": tenant_token},
            },
        },
    )
```

Next: [2.3 Lifecycle →](./3-lifecycle)
