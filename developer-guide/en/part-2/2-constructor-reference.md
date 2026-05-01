# 2.2 Constructor Reference

There are now **two stable construction paths**, picked by what your host already knows:

- **`agentao.embedding.build_from_environment(...)`** — CLI-style auto-discovery: reads `.env`, `LLM_PROVIDER`, `<wd>/.agentao/permissions.json`, `<wd>/.agentao/mcp.json`, memory roots, then constructs `Agentao` for you. Use this when your host follows the same project-directory conventions as the CLI.
- **`Agentao(...)` directly** — explicit injection: you already have an `LLMClient`, `PermissionEngine`, etc. and don't want any env / disk side effects at construction time.

**Important (0.3.0)**: `Agentao()` without `working_directory=` now raises `TypeError` from Python signature dispatch — the soft-deprecation cycle ended. Always pass an explicit `Path`, or go through `build_from_environment()`. End-to-end embedding patterns live in [`docs/EMBEDDING.md`](../../../docs/EMBEDDING.md).

## The factory: `build_from_environment()`

```python
from pathlib import Path
from agentao.embedding import build_from_environment

agent = build_from_environment(
    working_directory=Path("/data/tenant-acme"),
    transport=my_transport,
    max_context_tokens=128_000,
)
```

What it does:

1. Resolves `working_directory` (defaults to `Path.cwd()`) once and freezes the result.
2. Calls `load_dotenv()` against `<wd>/.env` if it exists, else process-wide.
3. Reads `LLM_PROVIDER` and the matching `*_API_KEY` / `*_BASE_URL` / `*_MODEL` env vars.
4. Builds a `PermissionEngine(project_root=wd, user_root=user_root())`, a `MemoryManager` (project store via `SQLiteMemoryStore.open_or_memory(wd / ".agentao" / "memory.db")`, user store via `SQLiteMemoryStore.open(...)` — disabled with a warning if unwritable), and a `FileBackedMCPRegistry(project_root=wd, user_root=user_root())` since #16/#17.
5. Forwards everything explicitly to `Agentao(...)`. **Caller `**overrides` win** over auto-discovered values.

This is the only place in the codebase that reads env / dotenv / `.agentao/*.json` at startup. Hosts that don't want any of that should construct `Agentao` directly.

## `Agentao.__init__` full signature (`agentao/agent.py`)

```python
Agentao(
    api_key:          Optional[str]    = None,
    base_url:         Optional[str]    = None,
    model:            Optional[str]    = None,
    temperature:      Optional[float]  = None,
    # ── Deprecated legacy callbacks (still accepted) ──
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
    working_directory:  Path,                                  # required since 0.3.0
    extra_mcp_servers:  Optional[Dict[str, Dict[str, Any]]] = None,
    # ── Embedded-harness explicit-injection kwargs ──
    llm_client:           Optional[LLMClient]         = None,
    logger:               Optional[logging.Logger]    = None,
    memory_manager:       Optional[MemoryManager]     = None,
    skill_manager:        Optional[SkillManager]      = None,
    project_instructions: Optional[str]               = None,
    mcp_manager:          Optional[McpClientManager]  = None,
    mcp_registry:         Optional[MCPRegistry]       = None,  # 0.3.0+ (#17)
    filesystem:           Optional[FileSystem]        = None,
    shell:                Optional[ShellExecutor]     = None,
    # ── Opt-in subsystems (None = disabled) ──
    bg_store:             Optional[BackgroundTaskStore] = None,
    sandbox_policy:       Optional[SandboxPolicy]       = None,
    replay_config:        Optional[ReplayConfig]        = None,
)
```

## LLM credentials (first 4 params)

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `api_key` | `str` | — (must be supplied or come from `llm_client=`) | LLM credential |
| `base_url` | `str` | — | Switch compatible endpoint (DeepSeek, Gemini gateway, vLLM…) |
| `model` | `str` | — | Model id |
| `temperature` | `float` | `0.2` | Sampling temperature |

Pass all four explicitly, or pass a fully-constructed `llm_client=` (see below). **Mutually exclusive**: passing both `llm_client=` and any of the raw LLM params raises `ValueError`.

For multi-tenant apps, each session can carry **different credentials** (e.g. per customer) — see Part 7.2.

## Embedded-harness explicit injections (0.2.16+)

When you don't want `Agentao()` to construct a subsystem from defaults, inject your own:

| Param | Type | What gets skipped when injected |
|-------|------|---------------------------------|
| `llm_client` | `LLMClient` | No `LLMClient(...)` is constructed; no env reads for credentials |
| `logger` | `logging.Logger` | The package-root level/handler mutation in `LLMClient.__init__` is skipped — your stack stays untouched |
| `memory_manager` | `MemoryManager` | No `<wd>/.agentao/memory.db` open at startup |
| `skill_manager` | `SkillManager` | The bundled-skill auto-discovery scan is skipped — your instance is used verbatim |
| `project_instructions` | `str` | The `<wd>/AGENTAO.md` disk read is skipped — your string is used verbatim |
| `mcp_manager` | `McpClientManager` | No `.agentao/mcp.json` discovery; you own the MCP lifecycle |
| `mcp_registry` (0.3.0+) | `MCPRegistry` | Replaces the implicit `load_mcp_config(...)` source. Default `FileBackedMCPRegistry` matches the pre-#17 disk read; pass an `InMemoryMCPRegistry` for programmatic registration. Mutually exclusive with `mcp_manager=`. |
| `filesystem` | `FileSystem` | File / search tools route through your `FileSystem` (see Part 6.4) |
| `shell` | `ShellExecutor` | The shell tool routes through your `ShellExecutor` |
| `bg_store` (opt-in) | `BackgroundTaskStore` | Background tool persistence. `None` keeps `check_background_agent` etc. unregistered and strips `run_in_background` from sub-agent tool schemas. |
| `sandbox_policy` (opt-in) | `SandboxPolicy` | Shell sandboxing. `None` runs commands without the macOS `sandbox-exec` wrapper. |
| `replay_config` (opt-in) | `ReplayConfig` | Deterministic re-runs. `None` uses a no-op recorder. |

These are the bridges hosts use when they want Agentao's runtime but not its CLI-style implicit reads. Combine freely:

```python
from agentao import Agentao
from agentao.llm import LLMClient
from agentao.capabilities import LocalFileSystem

agent = Agentao(
    working_directory=Path("/srv/agent-workdir"),
    llm_client=LLMClient(
        api_key=secrets.openai_api_key,
        base_url="https://api.openai.com/v1",
        model="gpt-5.4",
        log_file=None,             # don't write a log file
        logger=app.logger,         # use the host's logger
    ),
    skill_manager=preloaded_skill_manager,
    filesystem=LocalFileSystem(),  # or your sandboxed FS
    transport=my_transport,
)
```

### Capability protocols

`FileSystem` / `ShellExecutor` are runtime-checkable `Protocol`s. Since 0.3.4 they are re-exported on the public harness surface — **always import from `agentao.harness.protocols`** rather than reaching into `agentao.capabilities.*` (which is internal and may move). The default `LocalFileSystem` / `LocalShellExecutor` implementations live in `agentao.capabilities`; their behavior matches Agentao's pre-0.2.16 byte-for-byte. Hosts replace the injected protocol to route IO through Docker exec, virtual filesystems, audit proxies, or remote runners.

```python
from agentao.harness.protocols import (
    FileSystem, FileEntry, FileStat,
    ShellExecutor, ShellRequest, ShellResult, BackgroundHandle,
)
from agentao.capabilities import LocalFileSystem, LocalShellExecutor  # default impls
```

See Part 6.4 for the multi-tenant filesystem isolation pattern.

## Transport (recommended)

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `transport` | `Transport` | `NullTransport()` | UI interaction + event stream |

One `Transport` covers all interaction: tool confirmations, user asks, event streaming, max-iteration fallback. **Skip it = auto-approve everything, no event listener** (`NullTransport`) — fine for headless batch jobs.

```python
from agentao.transport import SdkTransport

transport = SdkTransport(
    on_event=handle_event,
    confirm_tool=ask_approval,
    ask_user=prompt_user,
    on_max_iterations=lambda n, msgs: {"action": "stop"},
)
agent = Agentao(transport=transport, working_directory=workdir, ...)
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

**All 8 still work** — Agentao wraps them in a `build_compat_transport()` shim internally. New code should go straight through `Transport`.

## Runtime behavior

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `max_context_tokens` | `int` | `200_000` | Triggers compression (Part 7.3) |
| `plan_session` | `PlanSession` | `None` | Enables Plan mode; rarely needed in embedders |
| `permission_engine` | `PermissionEngine` | `None` (factory builds one rooted at `wd`) | Rule engine (Parts 5.4 / 6.3) |

## Session isolation (critical!)

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `working_directory` | `Path` | — (required since 0.3.0) | **Frozen at construction** |

**Why it matters** (since 0.3.0):
- Required keyword — the soft-deprecation cycle ended. `Agentao()` without it raises `TypeError` from Python signature dispatch.
- The path is `expanduser().resolve()`-d once and frozen. File tools, `AGENTAO.md`, `.agentao/` config, shell CWD all resolve against it. An `os.chdir` in the host has no effect on an already-constructed Agentao.

In embedded contexts (web server, ACP sessions), `Path.cwd()` is **process-global state** — two concurrent sessions would cross-contaminate. The required `working_directory` rules that out.

```python
# ❌ Pre-0.3.0: relied on Path.cwd() — now a TypeError
agent_a = Agentao(...)
agent_b = Agentao(...)

# ✅ Each session has its own root
agent_a = Agentao(..., working_directory=Path("/tmp/tenant-a"))
agent_b = Agentao(..., working_directory=Path("/tmp/tenant-b"))
```

## Session-scoped MCP servers

| Param | Type | Default | Purpose |
|-------|------|---------|---------|
| `extra_mcp_servers` | `Dict[str, Dict]` | `None` | Programmatic MCP server injection |

Add MCP servers to a **single session** without touching the project's `.agentao/mcp.json`. Typical use: per-tenant GitHub token:

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

**Mutually exclusive** with `mcp_manager=`: pass either an already-built manager or the dict to merge, not both.

## Async hosts: `Agentao.arun()`

Sync callers use `agent.chat(user_message)`; async hosts use `await agent.arun(user_message)`.

```python
async def handle_request(request):
    response = await agent.arun(
        request.text,
        cancellation_token=request.cancel_token,
    )
    return {"reply": response}
```

`arun()` bridges the (still-sync) chat loop through `asyncio.get_running_loop().run_in_executor(None, self.chat, ...)`. Cancellation, replay, and `max_iterations` behave identically across both surfaces. The runtime internals stay sync because they are sequential I/O — exposing async all the way down would expand surface without benefit.

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
    engine = PermissionEngine(project_root=tenant_workdir)
    engine.set_mode(PermissionMode.WORKSPACE_WRITE)

    transport = SdkTransport(
        on_event=on_event,
        confirm_tool=confirm_tool,
        on_max_iterations=lambda n, _msgs: {"action": "stop"},
    )

    return Agentao(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL"),
        model="gpt-5.4",
        temperature=0.1,
        transport=transport,
        working_directory=tenant_workdir,
        max_context_tokens=128_000,
        permission_engine=engine,
        extra_mcp_servers={
            "gh": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": tenant_token},
            },
        },
    )
```

Or, when your host already follows the CLI conventions:

```python
from agentao.embedding import build_from_environment

agent = build_from_environment(
    working_directory=tenant_workdir,
    transport=transport,
    max_context_tokens=128_000,
)
```

Next: [2.3 Lifecycle →](./3-lifecycle)
