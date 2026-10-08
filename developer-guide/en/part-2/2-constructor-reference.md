# 2.2 Constructor Reference

> **What you'll learn**
> - The **smallest call that works**: four parameters, or two with a pre-built `llm_client=`
> - **All 33 parameters on one map**, grouped by what they configure
> - The **ones you'll typically pass in production** (transport, permissions, MCP, …)
> - The **advanced injection surface** for hosts that need full control
> - The two factory paths (`Agentao(...)` direct vs. `build_from_environment(...)`) and when to pick each

## Two construction paths

| Path | When to use |
|------|-------------|
| **`Agentao(...)` directly** | You want explicit control — no env / disk side effects at construction time. The body of this page covers this. |
| **`agentao.embedding.build_from_environment(...)`** | Your host follows the same project-directory conventions as the CLI. Reads `.env`, `permissions.json`, `mcp.json`, memory roots and builds `Agentao` for you. See [§ Factory](#factory-path-build-from-environment). |

Both produce an `Agentao` instance. Pick one — don't mix.

---

## All 33 parameters at a glance

`Agentao.__init__` takes 33 parameters. Only `working_directory` has no default. The first five (`api_key`, `base_url`, `model`, `temperature`, `max_tokens`) may be passed by position; every other one is keyword-only. Below they are grouped by what they configure, so you can skip the groups you don't need.

| Group | Parameters | Covered in |
|-------|------------|------------|
| **Session root** (required) | `working_directory` | Tier 1 |
| **Model**, raw settings | `api_key`, `base_url`, `model` (all three required without `llm_client`); `temperature`, `max_tokens`, `api_format`, `extra_body`, `prompt_cache`, `prompt_cache_ttl` | Tiers 1–3 |
| **Model**, pre-built | `llm_client` (replaces the whole raw-settings row; passing both raises `ValueError`) | Tier 2 |
| **Permissions and interaction** | `transport`, `permission_mode` *or* `permission_engine`, `plan_session` | Tiers 2–3 |
| **Context and compaction** | `max_context_tokens`, `compaction_controller` | Tiers 2–3 |
| **Tools** | `extra_tools`, `disable_tools` *or* `enabled_tools` | Tier 2 |
| **MCP** | `mcp_manager`, *or* `extra_mcp_servers` and `mcp_registry` (these two combine) | Tiers 2–3 |
| **Prompt** | `project_instructions` | Tier 2 |
| **Bring your own object** | `logger`, `memory_manager`, `skill_manager`, `filesystem`, `shell` | Tier 3 |
| **Opt-in subsystems** (off by default) | `bg_store`, `sandbox_policy`, `replay_config`, `enable_builtin_agents` | Tier 3 |

Parameters joined by *or*, and the two Model rows, are mutually exclusive: passing both sides raises `ValueError`; see [Mutual-exclusion rules](#mutual-exclusion-rules).

---

## Tier 1 · The smallest call that works

**Four parameters.** Everything else has a default.

```python
from pathlib import Path
from agentao import Agentao

agent = Agentao(
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
    model="gpt-5.4",
    working_directory=Path("/tmp/my-session"),
)
```

| Param | Type | Why |
|-------|------|-----|
| `api_key` | `str` | LLM credential |
| `base_url` | `str` | The endpoint: OpenAI, DeepSeek, a Gemini gateway, vLLM, … There is no built-in default |
| `model` | `str` | Model id |
| `working_directory` | `Path` | The session's project root. **Frozen at construction** — file / shell / memory all resolve against it |

Without `llm_client=`, leaving out any of `api_key`, `base_url` or `model` raises `ValueError`. A direct `Agentao(...)` **reads no environment variables**: `OPENAI_API_KEY` and the rest are read only by `build_from_environment()` (see [Factory path](#factory-path-build-from-environment)). The other smallest call is `Agentao(llm_client=my_client, working_directory=...)`, with the credentials on the client.

::: warning Don't skip `working_directory`
In a Web server / multi-tenant process, `Path.cwd()` is **process-global** — concurrent sessions would cross-contaminate. Since 0.3.0 the keyword is required; calls without it raise `TypeError` from Python signature dispatch.
:::

---

## Tier 2 · Common production params

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

`PermissionEngine(project_root=workdir)`, with no `rules=` and no `user_root=`, loads no rule file: a `<workdir>/.agentao/permissions.json` is never loaded, only warned about. So the two `engine` lines above give the same rules as passing `permission_mode="workspace-write"` instead of `permission_engine=engine`. Build an engine yourself when you have rules for it: `rules=[...]`, or `user_root=` to load `<user_root>/permissions.json`.

| Param | Type | Default | What it does |
|-------|------|---------|--------------|
| `max_tokens` | `int \| None` | `None` — the client's 65,536 | Per-call output cap |
| `temperature` | `float \| None` | `None` | Sampling temperature. `None` sends no `temperature`, so the provider's default applies (0.5.7; was `0.2`) |
| `extra_body` | `Dict[str,Any]` | `None` | Forwarded verbatim to the LLM `.create()` as the SDK's `extra_body` — the escape hatch for params the closed request build does not expose (`reasoning_effort` / `top_p` / `seed` / `response_format` / provider-specific fields). **Keyword-only.** Sub-agents inherit it; logged with credential keys redacted. **Mutually exclusive** with `llm_client`. See below |
| `api_format` | `str` | `"openai-completions"` | The wire protocol spoken to `base_url` (0.5.0): `"openai-completions"`, `"anthropic-messages"` (Anthropic's Messages API over the official SDK) or `"openai-responses"` (OpenAI's Responses API, 0.5.3). **Keyword-only**, configured and never inferred from the URL or the model name, changed afterwards only by `set_provider(..., api_format=)`, inherited by sub-agents, and **mutually exclusive with `llm_client=`**. An unknown value raises `ValueError`. On `anthropic-messages`, `base_url` is the API root, `temperature` is not sent, and extended thinking goes through `extra_body`. Env equivalent: `{PROVIDER}_API_FORMAT` — see [Appendix B](/en/appendix/b-config-keys) |
| `transport` | `CoreTransport` | `NullTransport()` | UI bridge: events + confirm + ask_user + max-iter fallback. See [Part 4](/en/part-4/) |
| `permission_engine` | `PermissionEngine` | `None` — no engine, no rule evaluated (`build_from_environment` builds one rooted at `working_directory`) | Rule-based gating; modes and `set_permission_mode` need one. See [5.4](/en/part-5/4-permissions) |
| `permission_mode` | `str` | `None` — builds nothing | `"read-only"` / `"workspace-write"` / `"full-access"` (`"plan"` is refused): builds `PermissionEngine(project_root=working_directory, rules=[])` in that mode, reading no rule file, and starts in it without emitting an event. Mutually exclusive with `permission_engine`. See [5.4](/en/part-5/4-permissions) |
| `max_context_tokens` | `int` | `200_000` | Triggers conversation compression beyond this |
| `extra_mcp_servers` | `Dict[str,Dict]` | `None` | Per-session MCP servers without touching `.agentao/mcp.json`. Same-name keys override. Useful for per-tenant tokens |
| `extra_tools` | `Sequence[Tool]` | `None` | Inject / replace tools (instances; register last, same name overrides a built-in). See [5.1](/en/part-5/1-custom-tools) |
| `disable_tools` | `Iterable[str]` | `None` | Skip these built-ins by name (unknown name → `ValueError`). **Mutually exclusive with `enabled_tools`** |
| `enabled_tools` | `Iterable[str]` | `None` | Allowlist of agentao-owned tools to keep; `None`=off, any iterable incl. `set()`=on. **Mutually exclusive with `disable_tools`** |
| `llm_client` | `LLMClient` | (constructed from credentials) | Inject a pre-built client to fully control logger / log file. **Mutually exclusive** with every raw model setting: `api_key` / `base_url` / `model` / `temperature` / `max_tokens` / `extra_body` / `prompt_cache` / `prompt_cache_ttl` / `api_format` (a host with its own client passes them to that client) |
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
| `memory_manager` | The default `MemoryManager` opening `<wd>/.agentao/memory.db`. Yours to close: `agent.close()` leaves it open |
| `skill_manager` | The bundled-skill auto-discovery scan |
| `mcp_manager` | `.agentao/mcp.json` discovery + lifecycle. Yours to disconnect: `agent.close()` leaves it connected. **Mutually exclusive with `extra_mcp_servers=` and `mcp_registry=`** |
| `mcp_registry` | `load_mcp_config(...)` source. Use `InMemoryMCPRegistry` for programmatic registration. **Mutually exclusive with `mcp_manager=`** |
:::

::: details Opt-in subsystems — `bg_store`, `sandbox_policy`, `replay_config`, `enable_builtin_agents`
**Default is `None` (or `False`) = fully disabled.** Pay zero cost if you don't use them.

| Param | When `None` |
|-------|-------------|
| `bg_store` | Background-task tools (`check_background_agent`, `cancel_background_agent`) are not registered; sub-agent tool schemas drop the `run_in_background` field; `/agent bg\|dashboard\|cancel\|delete\|logs\|result` CLI subcommands no-op with a warning |
| `sandbox_policy` | Shell runs without macOS `sandbox-exec` wrapper |
| `enable_builtin_agents` (`False`) | The built-in sub-agents (`codebase-investigator`, `generalist`) are not registered as delegation tools. The factory reads `agents.enable_builtin` from `settings.json` |
| `replay_config` | The `replay` block of `<wd>/.agentao/settings.json` is not read at construction; no `ReplayManager` is attached until `start_replay()` / `reload_replay_config()` creates one. `start_replay()` alone creates it with replay off (it returns `None`); `reload_replay_config()` reads that block but starts nothing: a `start_replay()` after it records only if the block has `enabled` true |
:::

::: details Logger injection — `logger`
Pass `logger=app.logger` to skip Agentao's package-root level / handler mutation in `LLMClient.__init__`. Your logging stack stays untouched, and the default rolling `<wd>/agentao.log` file is **not** created (the file-handler branch is skipped along with the rest of the mutation).

To control the file-handler axis independently, build `LLMClient` yourself and pass `log_file=None` (skip file) or `log_file="custom.log"` (redirect). Note: passing only `log_file=None` *without* `logger=` still elevates `getLogger("agentao")` to `DEBUG`. See [6.6 Observability → Take over Agentao's logger](/en/part-6/6-observability#take-over-agentaos-logger) for the full matrix and a fully-silent recipe.
:::

::: details LLM request passthrough — `extra_body`
Agentao builds the OpenAI-compatible request from a **closed set** of fields (`model` / `messages` / `temperature` / `tools` / `max_tokens`). When you need a param that set does not expose — `reasoning_effort`, `top_p`, `seed`, `response_format`, or any **provider-specific** body field (`top_k`, `repetition_penalty`, vendor extensions) — pass `extra_body`. It is forwarded verbatim to the SDK's `.create(extra_body=...)`, which merges it into the JSON request body, bypassing the typed signature.

```python
agent = Agentao(
    api_key="sk-...",
    base_url="https://api.openai.com/v1",
    model="gpt-5.4",
    working_directory=workdir,
    extra_body={"reasoning_effort": "high", "seed": 7},
)
```

- **Keyword-only.** Unlike `api_key` / `temperature`, `extra_body` is declared after `*`, so it never shifts the legacy positional arguments.
- **The host owns the values.** Agentao does not validate them — the SDK / provider does. You are configuring *your own* endpoint, so this is not third-party proxying.
- **Sub-agents inherit it**, the same way they inherit `temperature` / `max_tokens`.
- **No auto-recovery on model switch.** Unlike `temperature` (which auto-drops when a model rejects it), a stale `extra_body` key — e.g. `reasoning_effort` after switching to a non-reasoning model — makes every later call 400 until you clear it. Dropping model-specific keys on switch is the host's responsibility.
- **Logged with credentials redacted.** If `extra_body` nests credential-like keys (`api_key`, `authorization`, `x-api-key`, …), their values are masked (`***`) in `agentao.log`.
- **CLI / factory path:** set the `LLM_EXTRA_BODY` env var to a JSON **object** (e.g. `LLM_EXTRA_BODY='{"reasoning_effort":"high"}'`). Malformed or non-object values warn and are skipped; empty is treated as unset. See [Appendix B](/en/appendix/b-config-keys).
- **Mutually exclusive with `llm_client=`.** A host injecting its own `LLMClient` passes `extra_body=` to *that* client's constructor instead.

`extra_headers` and a `settings.json` file layer are intentionally deferred; see `docs/design/host-llm-extra-params.md`.
:::

::: details Rarely set — `prompt_cache`, `prompt_cache_ttl`, `compaction_controller`, `plan_session`
| Param | Default | What it does |
|-------|---------|--------------|
| `prompt_cache` | `None` (off) | `"anthropic"` puts explicit prompt-cache breakpoints on each request (at most 3). Off by default because endpoint support is not verified. Never inferred from the URL or model name. Env equivalent: `LLM_PROMPT_CACHE`, read only by the factory |
| `prompt_cache_ttl` | `None` (provider default, `"5m"`) | `"5m"` or `"1h"`. Ignored while `prompt_cache` is off. Env equivalent: `LLM_PROMPT_CACHE_TTL` |
| `compaction_controller` | `None` | A synchronous callable that allows, cancels, or supplies the summary for a compaction. Fail-open: a raise counts as allow. See [`host-api.md`](https://github.com/jin-bo/agentao/blob/main/docs/reference/host-api.md) |
| `plan_session` | `None` (a fresh `PlanSession`) | Plan-mode state. The CLI passes its own; an embedded host normally leaves it alone |
:::

::: details The 8 legacy callbacks (removed in 0.5.0)
Pre-0.2.10 API. They warned through 0.4.x and are **no longer parameters**: passing one raises `TypeError`. Either go through `Transport`, or keep the callbacks and wrap them — `agentao.embedding.compat.build_compat_transport(...)` takes the same eight names and is not deprecated: `Agentao(transport=build_compat_transport(confirmation_callback=...), ...)`.

| Removed param | Replacement |
|---------------|-------------|
| `confirmation_callback` | `SdkTransport(confirm_tool=...)` |
| `step_callback` | `on_event=` + `TOOL_START` / `TURN_START` |
| `thinking_callback` | `on_event=` + `THINKING` |
| `ask_user_callback` | `SdkTransport(ask_user=...)` |
| `output_callback` | `on_event=` + `TOOL_OUTPUT` |
| `tool_complete_callback` | `on_event=` + `TOOL_COMPLETE` |
| `llm_text_callback` | `on_event=` + `LLM_TEXT` |
| `on_max_iterations_callback` | `SdkTransport(on_max_iterations=...)` |

⚠️ **Only the first five parameters are positional** (`api_key`, `base_url`, `model`, `temperature`, `max_tokens`). The callbacks used to sit between `max_context_tokens`, `permission_engine`, `transport` and `plan_session`, so those four are keyword-only now — a call still passing a callback sixth is a `TypeError`, not a silent re-binding. Full guide: [`docs/migration/0.4.x-to-0.5.0.md`](https://github.com/jin-bo/agentao/blob/main/docs/migration/0.4.x-to-0.5.0.md).
:::

---

## Mutual-exclusion rules

Violating any of these raises `ValueError` at construction:

| Cannot combine | Reason |
|----------------|--------|
| `llm_client=` + any of `api_key` / `base_url` / `model` / `temperature` / `max_tokens` / `extra_body` / `prompt_cache` / `prompt_cache_ttl` / `api_format` | The injected client is already the credential source — pass those to that client directly instead |
| `mcp_manager=` + `extra_mcp_servers=` | Per-session merge needs a manager Agentao constructs |
| `mcp_manager=` + `mcp_registry=` | Registry is the config source; manager is the construction outcome |
| `permission_engine=` + `permission_mode=` | One is an engine, the other builds one — set the mode on your engine, or call `set_permission_mode()` after construction |

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
        base_url=os.environ["OPENAI_BASE_URL"],
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
- **0.5.0** — The 8 legacy callback kwargs (`confirmation_callback`, `step_callback`, `thinking_callback`, `ask_user_callback`, `output_callback`, `tool_complete_callback`, `llm_text_callback`, `on_max_iterations_callback`) were **removed** from the `Agentao(...)` signature, and every parameter after `max_tokens` became keyword-only. Use `transport=SdkTransport(...)`, or wrap the old callbacks with `agentao.embedding.compat.build_compat_transport(...)`.
- **0.4.x** — The 8 legacy callbacks emitted a single `DeprecationWarning` per construction (from 0.4.5).
- **0.3.4** — Capability protocols (`FileSystem`, `ShellExecutor`) re-exported on `agentao.host.protocols`. Always import from there, not internal `agentao.capabilities.*`.
- **0.3.0** — `working_directory=` became a required keyword (calls without it raise `TypeError`). `mcp_registry=` introduced as a stable config-source surface; default `FileBackedMCPRegistry` matches the pre-#17 disk read.
- **0.2.16** — Explicit-injection surface added (`memory_manager`, `skill_manager`, `mcp_manager`, `filesystem`, `shell`, …); `replay_config`, `sandbox_policy`, `bg_store` defaulted to `None`.
- **0.2.10** — Decoupled core runtime; the 8 legacy callbacks remain accepted via `build_compat_transport()`.

End-to-end embedding patterns: [`docs/guides/embedding.md`](https://github.com/jin-bo/agentao/blob/main/docs/guides/embedding.md).
:::

## TL;DR

- **33 parameters, one required**: `working_directory` (Path, frozen at construction). The [map above](#all-33-parameters-at-a-glance) groups the rest by purpose.
- **Smallest working call**: `api_key` + `base_url` + `model` + `working_directory`, or `llm_client` + `working_directory`. A direct `Agentao(...)` reads no environment variables.
- **Typical production additions**: `transport`, `permission_mode` (or `permission_engine`), `max_context_tokens`, `extra_mcp_servers`, `project_instructions`, `temperature`.
- **Everything else is opt-in or advanced** — capability protocols, custom managers, prompt caching, compaction control, sandbox / replay / background / sub-agent subsystems.
- **Two factories**: `build_from_environment()` for CLI conventions; direct `Agentao(...)` for explicit control. Don't mix.

→ Next: [2.3 Lifecycle](./3-lifecycle)
