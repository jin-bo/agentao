# 2.2 Constructor Reference

> **What you'll learn**
> - The **3 parameters you must pass** and why each is required
> - The **8 you'll typically pass in production** (transport, permissions, MCP, …)
> - The **advanced injection surface** for hosts that need full control
> - The two factory paths (`Agentao(...)` direct vs. `build_from_environment(...)`) and when to pick each

## Two construction paths

| Path | When to use |
|------|-------------|
| **`Agentao(...)` directly** | You want explicit control — no env / disk side effects at construction time. The body of this page covers this. |
| **`agentao.embedding.build_from_environment(...)`** | Your host follows the same project-directory conventions as the CLI. Reads `.env`, `permissions.json`, `mcp.json`, memory roots and builds `Agentao` for you. See [§ Factory](#factory-path-build-from-environment). |

Both produce an `Agentao` instance. Pick one — don't mix.

---

## Tier 1 · The minimum (3 params)

**You must pass these. Everything else has a sensible default.**

```python
from pathlib import Path
from agentao import Agentao

agent = Agentao(
    api_key="sk-...",
    model="gpt-5.4",
    working_directory=Path("/tmp/my-session"),
)
```

| Param | Type | Why required |
|-------|------|--------------|
| `api_key` | `str` | LLM credential. Or set env `OPENAI_API_KEY`, or pass a pre-built `llm_client=` |
| `model` | `str` | Model id. Or env `OPENAI_MODEL` |
| `working_directory` | `Path` | The session's project root. **Frozen at construction** — file / shell / memory all resolve against it |

> Set `base_url` too if your endpoint isn't OpenAI (DeepSeek, Gemini gateway, vLLM, …). Or env `OPENAI_BASE_URL`.

::: warning Don't skip `working_directory`
In a Web server / multi-tenant process, `Path.cwd()` is **process-global** — concurrent sessions would cross-contaminate. Since 0.3.0 the keyword is required; calls without it raise `TypeError` from Python signature dispatch.
:::

---

## Tier 2 · Common production params (8 more)

These cover most production embeddings:

```python
from agentao import Agentao
from agentao.transport import SdkTransport
from agentao.permissions import PermissionEngine, PermissionMode

engine = PermissionEngine(project_root=workdir)
engine.set_mode(PermissionMode.WORKSPACE_WRITE)

transport = SdkTransport(on_event=..., confirm_tool=..., ask_user=...)

agent = Agentao(
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
    model="gpt-5.4",
    temperature=0.1,
    working_directory=workdir,
    transport=transport,
    permission_engine=engine,
    max_context_tokens=128_000,
    extra_mcp_servers={...},
)
```

| Param | Type | Default | What it does |
|-------|------|---------|--------------|
| `base_url` | `str` | OpenAI's | Switch to any OpenAI-compatible endpoint |
| `temperature` | `float` | `0.2` | Sampling temperature |
| `transport` | `Transport` | `NullTransport()` | UI bridge: events + confirm + ask_user + max-iter fallback. See [Part 4](/en/part-4/) |
| `permission_engine` | `PermissionEngine` | factory builds one rooted at `working_directory` | Rule-based gating. See [5.4](/en/part-5/4-permissions) |
| `max_context_tokens` | `int` | `200_000` | Triggers conversation compression beyond this |
| `extra_mcp_servers` | `Dict[str,Dict]` | `None` | Per-session MCP servers without touching `.agentao/mcp.json`. Same-name keys override. Useful for per-tenant tokens |
| `llm_client` | `LLMClient` | (constructed from credentials) | Inject a pre-built client to fully control logger / log file. **Mutually exclusive** with `api_key` / `base_url` / `model` / `temperature` |
| `project_instructions` | `str` | (read from `<wd>/AGENTAO.md`) | Pass AGENTAO.md content directly — skips the disk read |

::: tip Async hosts use `arun()`
`agent.chat(...)` is synchronous. Async hosts call `await agent.arun(user_message)`, which bridges through `loop.run_in_executor`. Cancellation, replay, and `max_iterations` semantics are identical across both surfaces.
:::

---

## Tier 3 · Advanced injections

Most embeddings never need these. Expand only what applies to you.

::: details Capability protocols — `filesystem`, `shell`, `mcp_registry`, `memory_manager`
Four host→Agentao injection slots cover every IO surface tools touch. Replace any one to route IO through Docker exec, virtual filesystems, audit proxies, plugin-driven MCP discovery, or a remote memory backend. The defaults match Agentao's pre-0.2.16 byte-for-byte behavior.

| Slot | Protocol | Default | Bound at |
|------|----------|---------|----------|
| `filesystem` | `FileSystem` | `LocalFileSystem` | Tool registration → `tool.filesystem` on every file/search tool |
| `shell` | `ShellExecutor` | `LocalShellExecutor` | Tool registration → `tool.shell` on the shell tool |
| `mcp_registry` | `MCPRegistry` | `FileBackedMCPRegistry` | `Agentao.__init__` reads `list_servers()` once during MCP init |
| `memory_manager` (wraps `MemoryStore`) | `MemoryStore` | `SQLiteMemoryStore` under `<wd>/.agentao/memory.db` | Held on `agent._memory_manager`; the `save_memory` tool delegates here |

```python
from agentao import Agentao
from agentao.host.protocols import FileSystem, ShellExecutor, MCPRegistry, MemoryStore
from agentao.memory import MemoryManager

agent = Agentao(
    working_directory=workdir,
    filesystem=MyDockerExecFileSystem(...),         # FileSystem
    shell=MyAuditingShellExecutor(...),             # ShellExecutor
    mcp_registry=MyPluginMCPRegistry(...),          # MCPRegistry
    memory_manager=MemoryManager(                   # MemoryStore wrapped in a manager
        project_store=MyRedisMemoryStore(...),
    ),
)
```

Always import the **protocols** from `agentao.host.protocols` (public surface). Default impls live in `agentao.capabilities` and `agentao.memory`. `None` for any slot means *fall back to the local default*, not *disable*; to disable a capability, inject an implementation that raises on call.

::: tip Runnable end-to-end example — [`examples/protocol-injection/`](https://github.com/jin-bo/agentao/tree/main/examples/protocol-injection)
Replaces all four slots with small adapters (in-memory FS, audit-logging shell, dict-backed memory store, programmatic MCP registry) and asserts each one is consulted via 6 smoke tests. No `OPENAI_API_KEY` required. Run with `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/`.
:::

Multi-tenant FS isolation: [6.4](/en/part-6/4-multi-tenant-fs).
:::

::: details Memory / Skills / MCP managers — `memory_manager`, `skill_manager`, `mcp_manager`, `mcp_registry`
Inject pre-built managers when you don't want Agentao to construct them from defaults — typically because the manager is shared across many sessions, or you want programmatic config rather than disk lookups.

| Param | Replaces |
|-------|----------|
| `memory_manager` | The default `MemoryManager` opening `<wd>/.agentao/memory.db` |
| `skill_manager` | The bundled-skill auto-discovery scan |
| `mcp_manager` | `.agentao/mcp.json` discovery + lifecycle. **Mutually exclusive with `extra_mcp_servers=` and `mcp_registry=`** |
| `mcp_registry` | `load_mcp_config(...)` source. Use `InMemoryMCPRegistry` for programmatic registration. **Mutually exclusive with `mcp_manager=`** |
:::

::: details Opt-in subsystems — `bg_store`, `sandbox_policy`, `replay_config`
**Default is `None` = fully disabled.** Pay zero cost if you don't use them.

| Param | When `None` |
|-------|-------------|
| `bg_store` | Background-task tools (`check_background_agent`, `cancel_background_agent`) are not registered; sub-agent tool schemas drop the `run_in_background` field; `/agent bg\|dashboard\|cancel\|delete\|logs\|result` CLI subcommands no-op with a warning |
| `sandbox_policy` | Shell runs without macOS `sandbox-exec` wrapper |
| `replay_config` | No `<wd>/.agentao/replay.json` read; agent uses a no-op recorder |
:::

::: details Logger injection — `logger`
Pass `logger=app.logger` to skip Agentao's package-root level / handler mutation in `LLMClient.__init__`. Your logging stack stays untouched.
:::

::: details Legacy 8 callbacks (still accepted, deprecated)
Pre-0.2.10 API. Internally wrapped via `build_compat_transport()` into an `SdkTransport`. New code should go straight through `Transport`.

| Legacy param | Replacement |
|--------------|-------------|
| `confirmation_callback` | `SdkTransport(confirm_tool=...)` |
| `step_callback` | `on_event=` + `TOOL_START` / `TURN_START` |
| `thinking_callback` | `on_event=` + `THINKING` |
| `ask_user_callback` | `SdkTransport(ask_user=...)` |
| `output_callback` | `on_event=` + `TOOL_OUTPUT` |
| `tool_complete_callback` | `on_event=` + `TOOL_COMPLETE` |
| `llm_text_callback` | `on_event=` + `LLM_TEXT` |
| `on_max_iterations_callback` | `SdkTransport(on_max_iterations=...)` |

⚠️ Mixing `transport=` with legacy callbacks silently **ignores** the legacy ones. Pick one path.
:::

---

## Mutual-exclusion rules

Violating any of these raises `ValueError` at construction:

| Cannot combine | Reason |
|----------------|--------|
| `llm_client=` + any of `api_key` / `base_url` / `model` / `temperature` | The injected client is already the credential source |
| `mcp_manager=` + `extra_mcp_servers=` | Per-session merge needs a manager Agentao constructs |
| `mcp_manager=` + `mcp_registry=` | Registry is the config source; manager is the construction outcome |

---

## Factory path: `build_from_environment()`

When your host follows CLI conventions (project-rooted `.env`, `.agentao/` configs, memory dirs):

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

1. Resolves `working_directory` (defaults to `Path.cwd()`) and freezes it
2. Calls `load_dotenv()` against `<wd>/.env` if present, else process-wide
3. Reads `LLM_PROVIDER` and matching `*_API_KEY` / `*_BASE_URL` / `*_MODEL` env vars
4. Builds a `PermissionEngine`, `MemoryManager`, and `FileBackedMCPRegistry` rooted at `wd`
5. Forwards everything explicitly to `Agentao(...)` — **caller `**overrides` win** over auto-discovered values

This is **the only place in the codebase that reads env / dotenv / `.agentao/*.json` at startup**. Hosts that don't want any of that should construct `Agentao` directly.

---

## Full production template

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

Or, if your host already follows CLI conventions:

```python
from agentao.embedding import build_from_environment

agent = build_from_environment(
    working_directory=tenant_workdir,
    transport=transport,
    max_context_tokens=128_000,
)
```

---

::: info Version note
- **0.3.4** — Capability protocols (`FileSystem`, `ShellExecutor`) re-exported on `agentao.host.protocols`. Always import from there, not internal `agentao.capabilities.*`.
- **0.3.0** — `working_directory=` became a required keyword (calls without it raise `TypeError`). `mcp_registry=` introduced as a stable config-source surface; default `FileBackedMCPRegistry` matches the pre-#17 disk read.
- **0.2.16** — Explicit-injection surface added (`memory_manager`, `skill_manager`, `mcp_manager`, `filesystem`, `shell`, …); `replay_config`, `sandbox_policy`, `bg_store` defaulted to `None`.
- **0.2.10** — Decoupled core runtime; the 8 legacy callbacks remain accepted via `build_compat_transport()`.

End-to-end embedding patterns: [`docs/EMBEDDING.md`](https://github.com/jin-bo/agentao/blob/main/docs/EMBEDDING.md).
:::

## TL;DR

- **3 you must pass**: `api_key`, `model`, `working_directory` (Path, frozen at construction).
- **8 you'll typically pass**: + `base_url`, `temperature`, `transport`, `permission_engine`, `max_context_tokens`, `extra_mcp_servers`, `llm_client`, `project_instructions`.
- **Everything else is opt-in or advanced** — capability protocols, custom managers, sandbox / replay / background subsystems.
- **Two factories**: `build_from_environment()` for CLI conventions; direct `Agentao(...)` for explicit control. Don't mix.

→ Next: [2.3 Lifecycle](./3-lifecycle)
