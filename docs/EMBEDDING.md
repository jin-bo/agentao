# Embedding Agentao

Agentao runs in three deployment shapes: as a CLI binary, as an
`session/*` ACP agent, and as a library inside another host
application. This document is the reference for the third —
**embedding the runtime in your own process**.

It assumes you have read the top-level [`README.md`](../README.md) and
know what skills, transports, and permission engines do. The goal here
is to show how to wire those pieces from a host without reaching into
Agentao internals.

> Audience: developers integrating Agentao into a webapp, batch job,
> IDE plugin, evaluation harness, sandbox runner, or research notebook.

---

## TL;DR

```python
# 1. Drop-in: read .env / cwd / .agentao/* the way the CLI does.
from pathlib import Path
from agentao.embedding import build_from_environment

agent = build_from_environment(working_directory=Path("/srv/myapp/run-1"))
reply = agent.chat("Summarize today's logs.")
agent.close()
```

```python
# 2. Pure-injection: zero env / disk / cwd reads. Host owns every input.
from pathlib import Path
from agentao import Agentao
from agentao.llm import LLMClient
from agentao.transport import NullTransport

agent = Agentao(
    working_directory=Path("/srv/myapp/run-1"),
    llm_client=LLMClient(api_key=..., base_url=..., model=...),
    transport=NullTransport(),
)
reply = agent.chat("Summarize today's logs.")
agent.close()
```

The first form is what the CLI and ACP runtimes use under the hood and
matches existing user expectations (`.env`, `~/.agentao/`,
`<wd>/.agentao/`). The second form is what hosts that ship containers,
sandboxes, multi-tenant deployments, or strict audit trails should
prefer — every byte of context is explicit.

---

## When to use which form

| Situation | Use |
|---|---|
| Drop-in replacement for the CLI in a notebook / script | `build_from_environment` |
| Multi-tenant webapp with one Agentao per request | Pure injection |
| Containerized sandbox, no `.env` shipping | Pure injection |
| IDE plugin where the host already manages state | Pure injection |
| Test harness that wants reproducibility | Pure injection |
| One-off scripts that read your local config | `build_from_environment` |

Mixing is fine: you can use `build_from_environment(...)` and override
specific subsystems via keyword. Anything not overridden falls back to
the env-discovered default.

```python
agent = build_from_environment(
    working_directory=Path("/srv/run-1"),
    permission_engine=my_custom_engine,    # override
    mcp_registry=InMemoryMCPRegistry({...}),  # override
    # bg_store, sandbox_policy, replay_config, llm_client, ... all overridable
)
```

---

## 1. Minimal embedded construction (factory path)

`build_from_environment` is the single entry point that touches
`os.environ`, `Path.cwd()`, `Path.home()`, and `<wd>/.agentao/*` files.
Everything else in `agentao.*` constructs from explicit arguments only.

```python
from pathlib import Path
from agentao.embedding import build_from_environment

agent = build_from_environment(
    working_directory=Path("/srv/myapp"),
    # All overrides are optional and forwarded to Agentao(...).
)
```

What the factory does, in order:

1. Resolves `working_directory` to an absolute path (or
   `Path.cwd()` if you omit it — discouraged for embedded use).
2. Loads `<wd>/.env` via `dotenv` if present, else `.env` in the
   process cwd as a fallback.
3. Discovers LLM credentials from `LLM_PROVIDER` (default `OPENAI`)
   and the provider-prefixed env vars
   (`{PROVIDER}_API_KEY`, `{PROVIDER}_BASE_URL`,
   `{PROVIDER}_MODEL`, plus `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`).
4. Builds `PermissionEngine(project_root=wd, user_root=user_root())`.
5. Builds `MemoryManager` with `SQLiteMemoryStore.open_or_memory(...)`
   for the project DB and `SQLiteMemoryStore.open(...)` for the user
   DB (disabled with a warning if either path is unwritable).
6. Builds `FileBackedMCPRegistry(project_root=wd, user_root=user_root())`.
7. Wires opt-in defaults (`BackgroundTaskStore`, `SandboxPolicy`,
   `replay_config`) — pass `None` for any of them to disable.
8. Reads `<wd>/.agentao/settings.json` for factory-level toggles such
   as `agents.enable_builtin`.
9. Constructs `Agentao(...)` with all of the above as explicit kwargs.

The CLI and ACP `session/new` both go through this factory; their
behaviour is unchanged after embedding.

---

## 2. Pure-injection construction

For hosts that need deterministic, side-effect-free construction
(multi-tenant webapps, container-pinned runtimes, strict-mode tests),
pass everything explicitly. After Issue #16 / #17, the constructor
reads zero environment state.

```python
from pathlib import Path
from agentao import Agentao
from agentao.llm import LLMClient
from agentao.permissions import PermissionEngine
from agentao.memory import MemoryManager, SQLiteMemoryStore
from agentao.mcp import InMemoryMCPRegistry
from agentao.transport import NullTransport

workdir = Path("/srv/myapp/run-1")

agent = Agentao(
    working_directory=workdir,                      # required since 0.3.0
    llm_client=LLMClient(
        api_key="sk-...",
        base_url="https://api.openai.com/v1",
        model="gpt-5.4",
    ),
    permission_engine=PermissionEngine(project_root=workdir),
    memory_manager=MemoryManager(
        project_store=SQLiteMemoryStore.open_or_memory(
            workdir / ".agentao" / "memory.db"
        ),
    ),
    mcp_registry=InMemoryMCPRegistry({
        "my-tool-server": {
            "command": "/usr/local/bin/my-mcp",
            "args": ["--port", "0"],
        },
    }),
    transport=NullTransport(),
)
```

After construction, `agent.chat(prompt)` runs the same way it does
under the CLI — your only ongoing responsibility is `agent.close()`
when the host is done with the session.

### What you have to pass explicitly

| Argument | Required? | Notes |
|---|---|---|
| `working_directory` | **Yes** (since 0.3.0) | Absolute or expandable path. Frozen at construction; an `os.chdir` in the host has no effect on the agent. |
| `llm_client` *or* `api_key`+`base_url`+`model` | **Yes** | The constructor raises `ValueError` if both are missing. |
| `permission_engine` | No | Defaults to a permissive engine. |
| `memory_manager` | No | Defaults to a project-scoped `:memory:`-fallback store. |
| `mcp_registry` | No | Defaults to no MCP servers (the file-backed registry is only wired by the factory). |
| `transport` | No | Defaults to `NullTransport()`. |

---

## 3. Capability injection

Beyond LLM / memory / MCP / permissions, four narrower capabilities
let you intercept IO without subclassing tools. The Protocols are
re-exported on the public harness surface — **always import from
`agentao.harness.protocols`** rather than reaching into
`agentao.capabilities.*`, which is internal and may move:

| Protocol | Default (in `agentao.capabilities`) | Purpose |
|---|---|---|
| `agentao.harness.protocols.FileSystem` | `LocalFileSystem` | Backs `read_file`, `write_file`, `glob`, `search_file_content`, `list_directory`. Inject to redirect through Docker exec, virtual filesystems, audit proxies. |
| `agentao.harness.protocols.ShellExecutor` | `LocalShellExecutor` | Backs `run_shell_command`. Inject to route shell through a remote runner / sandbox. |
| `agentao.harness.protocols.MemoryStore` | `SQLiteMemoryStore` | Persistent-memory storage. Inject to back memory with Redis, Postgres, in-process dict, remote API. |
| `agentao.harness.protocols.MCPRegistry` | `FileBackedMCPRegistry` | Source of MCP server configs. Inject to register servers programmatically. |

```python
from agentao.harness.protocols import FileSystem, ShellExecutor
from agentao.capabilities import LocalFileSystem  # default impl

class AuditedFileSystem:        # duck-types FileSystem
    def __init__(self, inner): self.inner = inner
    def read_bytes(self, p): log("read", p); return self.inner.read_bytes(p)
    # ... 9 more methods, each calling self.inner

agent = build_from_environment(
    working_directory=workdir,
    filesystem=AuditedFileSystem(LocalFileSystem()),
    shell=my_remote_shell_executor,
)
```

All four protocols are `Protocol`s (PEP 544) — no inheritance required;
just match the method signatures. The reference implementations
(`LocalFileSystem`, `LocalShellExecutor`, `SQLiteMemoryStore`,
`FileBackedMCPRegistry`) stay in `agentao.capabilities` because they
are not part of the public host-injection surface.

---

## 4. Async usage

Agentao's `chat()` method is synchronous and runs the LLM/tool loop on
the calling thread. For hosts that already run an event loop
(FastAPI / aiohttp / Discord bots / IDE plugins), use `arun()`:

```python
async def handle_request(req):
    agent = build_from_environment(working_directory=Path(req.workdir))
    try:
        reply = await agent.arun(req.prompt)
        return reply
    finally:
        await asyncio.to_thread(agent.close)
```

`arun()` is a thin wrapper over `chat()` that runs the synchronous
loop in a worker thread — no loop is monopolised. Cancellation is
forwarded via `agent.cancel_current_turn()`; the worker thread checks
the cancellation token cooperatively at every tool boundary and at
each LLM streaming chunk.

---

## 5. Replay, Sandbox, BgStore (opt-in subsystems)

Three subsystems default to `None` (disabled) on bare construction
and are wired by `build_from_environment` from disk only when the
caller does not override them:

| Subsystem | What it does | When to enable |
|---|---|---|
| `replay_config` | Records every LLM/tool turn and lets you re-run sessions deterministically. Reads `<wd>/.agentao/replay.json`. | Debugging non-deterministic flake; A/B comparing prompt changes; reproducing user-reported issues. |
| `sandbox_policy` | Restricts file/shell tool side-effects to a project root with explicit allow/deny rules. Reads `<wd>/.agentao/sandbox.json`. | Multi-tenant deployments; running untrusted prompts; CI evaluation harnesses. |
| `bg_store` | Persists in-flight background tool tasks across restarts so a long-running shell command survives an agent restart. Reads `<wd>/.agentao/bg/`. | Long-lived servers that survive process restarts; production batch workers. |

Each is opt-out under the factory and opt-in under bare construction:

```python
# Factory: enable all three, override the policy
agent = build_from_environment(
    working_directory=workdir,
    sandbox_policy=MyStrictSandbox(project_root=workdir),
    # replay_config / bg_store unspecified → factory defaults
)

# Factory: disable replay specifically
agent = build_from_environment(
    working_directory=workdir,
    replay_config=None,
)

# Bare construction: pass the ones you need; the rest stay None
from agentao.agents.bg_store import BackgroundTaskStore
agent = Agentao(
    working_directory=workdir,
    llm_client=...,
    bg_store=BackgroundTaskStore(persistence_dir=workdir),
)
```

---

## 6. Built-in sub-agents

Project-level agents in `<wd>/.agentao/agents/*.md` are discovered by
default. Built-in agents (`codebase-investigator` and `generalist`) are
disabled by default so the model does not always receive extra
delegation tools.

Enable built-ins for a project through `<wd>/.agentao/settings.json`:

```json
{
  "agents": {
    "enable_builtin": true
  }
}
```

Embedded hosts can override the setting directly:

```python
agent = build_from_environment(
    working_directory=workdir,
    enable_builtin_agents=True,
)

agent = Agentao(
    working_directory=workdir,
    llm_client=...,
    enable_builtin_agents=True,
)
```

---

## 7. Host-facing harness contract

`Agentao(...)` and the embedding factory cover *constructing* an agent.
The **harness contract** covers everything a host needs to *observe* a
running agent without reaching into internals: the active permission
policy, what the agent is doing, what sub-agents it spawned, and why
each capability was allowed or denied.

The stable surface lives in `agentao.harness`. Internal runtime types
(`AgentEvent`, `ToolExecutionResult`, `PermissionEngine`) are deliberately
**not** part of this contract — they may change in any release.

```python
from agentao.harness import (
    ActivePermissions,
    HarnessEvent,
    PermissionDecisionEvent,
    SubagentLifecycleEvent,
    ToolLifecycleEvent,
)
```

### `agent.active_permissions()`

Returns a JSON-safe `ActivePermissions` snapshot of the policy used by
the next tool decision:

```python
snap = agent.active_permissions()
# snap.mode            -> "workspace-write"
# snap.rules           -> [...]
# snap.loaded_sources  -> ["preset:workspace-write",
#                          "project:.agentao/permissions.json",
#                          "user:/Users/me/.agentao/permissions.json",
#                          "injected:host"]
```

Hosts that layer policy on top of the engine call
`agent.permission_engine.add_loaded_source("injected:<name>")` so the
snapshot reflects their provenance. The snapshot is cached; the cache
is invalidated on `set_mode()` and `add_loaded_source()`.

### `agent.events(session_id=None)` — async iterator

Returns an async iterator over `HarnessEvent` (a discriminated union of
`ToolLifecycleEvent`, `SubagentLifecycleEvent`, and
`PermissionDecisionEvent`). Pass `session_id=` to filter; pass `None`
to subscribe to all sessions on this `Agentao` instance.

```python
async def watch(agent):
    async for ev in agent.events():
        if isinstance(ev, ToolLifecycleEvent):
            print(ev.tool_name, ev.phase, ev.outcome)
        elif isinstance(ev, PermissionDecisionEvent):
            print("perm", ev.tool_name, ev.outcome, ev.matched_rule)
        elif isinstance(ev, SubagentLifecycleEvent):
            print("subagent", ev.child_session_id, ev.phase)
```

Delivery semantics (the full contract is in
[`docs/api/harness.md`](api/harness.md)):

- Same-session ordering is guaranteed.
- Within one `tool_call_id`, `PermissionDecisionEvent` is emitted before
  `ToolLifecycleEvent(phase="started")`.
- Cross-session global ordering is not guaranteed.
- **No replay.** Events emitted before the first subscription are
  dropped; a subscriber that starts mid-turn receives only future
  events.
- Backpressure is host-pulled. When the bounded subscription queue is
  full, the producer blocks for matching events — Agentao does not grow
  an unbounded queue.
- Cancelling the iterator releases queue/subscription resources.
- MVP supports one public stream consumer per `Agentao` instance.

### What is *not* on the harness surface

These are deliberately deferred (see
[`docs/design/embedded-harness-contract.md`](design/embedded-harness-contract.md)):

- Public agent graph / descendants store API.
- Host-facing hooks list/disable API.
- Host-facing MCP reload / lifecycle events.
- Local plugin export/import; remote plugin share.
- External session import.
- Generated client SDKs.

The CLI may build on the same events for its own UI, but its stores and
commands are not promoted to the harness API.

### Schema snapshots

Each release ships checked-in JSON schema snapshots:

- `docs/schema/harness.events.v1.json` — events + permissions surface
- `docs/schema/harness.acp.v1.json` — host-facing ACP payloads

`tests/test_harness_schema.py` regenerates the schemas from the
Pydantic models and asserts byte-equality. A model change that shifts
the wire form must update both the model and the snapshot in the same
PR. Adding an optional field is backwards-compatible; removing or
renaming requires a schema version bump.

---

## 8. Migration guide: 0.2.15 → 0.2.16 → 0.3.0 → 0.3.1 → 0.3.3

The embedded-harness epic shipped over four releases (0.2.15 →
0.3.1); 0.3.3 is the first patch in the Path A roadmap and is
additive only. Code that worked on 0.2.15 should land on 0.3.3 with
two mechanical changes; every 0.3.x → 0.3.x step requires no host
code changes.

### From 0.2.15

```python
# 0.2.15 — implicit env / cwd / .agentao reads inside Agentao()
agent = Agentao()
```

This still emits a `DeprecationWarning` on 0.2.16 and raises
`TypeError` on 0.3.0. Replace with one of:

```python
# Option A: factory (matches the old behaviour)
agent = build_from_environment(working_directory=Path.cwd())

# Option B: pure injection (deterministic; preferred for embedded)
agent = Agentao(
    working_directory=Path.cwd(),
    llm_client=LLMClient(api_key=..., base_url=..., model=...),
)
```

### From 0.2.16

The 0.2.16 soft-deprecation cycle warned but did not break:

```python
# 0.2.16 emitted a DeprecationWarning here.
agent = Agentao()                            # working_directory missing
```

On 0.3.0 the same call raises `TypeError` from Python signature
dispatch. Add `working_directory=` and you are done.

### Other 0.3.0 BREAKING signals to watch for

- `MemoryManager(project_root=, global_root=)` is gone — pass
  pre-built stores (`project_store=`, `user_store=`). The factory
  absorbs this internally; only direct callers need the migration.
- `MemoryManager._project_root` / `_global_root` private attributes
  are gone. Read `manager.project_store.db_path` if you previously
  introspected the path.
- `Agentao()` no longer falls back to `Path.cwd()` for the working
  directory. The deprecation warning is gone with the fallback.

The full 0.3.0 changelog block lives in [`CHANGELOG.md`](../CHANGELOG.md).

### From 0.3.0

`0.3.1` is an Added-only patch in the 0.3.x series — strictly
backwards-compatible. **No required code change** to upgrade. Existing
calls into `Agentao(...)`, `build_from_environment(...)`, capability
protocols, and the transport layer keep working unchanged.

### From 0.3.1

`0.3.3` is also an Added-only patch — strictly backwards-compatible.
The only user-visible additions are the PEP 561 `py.typed` marker
(downstream `mypy` / `pyright` now picks up Agentao's type hints) and
the README's new `## Embed in 30 lines` lead section. No host code
change required.

What's available to opt into:

- `agent.events(session_id=None)` — async iterator over `HarnessEvent`
  (`ToolLifecycleEvent` / `SubagentLifecycleEvent` /
  `PermissionDecisionEvent`). Stable host-facing observation surface;
  see [section 7](#7-host-facing-harness-contract) above.
- `agent.active_permissions()` — JSON-safe `ActivePermissions` snapshot
  (`mode`, `rules`, `loaded_sources`).
- `agentao.harness` — public package with `ActivePermissions`,
  `ToolLifecycleEvent`, `SubagentLifecycleEvent`,
  `PermissionDecisionEvent`, `EventStream`, `HarnessEvent`,
  `RFC3339UTCString`, and the schema export helpers.
- New direct dependency: `pydantic>=2`. If your environment already
  pins Pydantic v1, lift the pin before upgrading.

What stays internal (do **not** depend on for forward compatibility):

- `agentao.transport.AgentEvent` and `Transport.emit(...)` — richer but
  may change between releases.
- `agentao.runtime.identity.*` — id helpers; private to the runtime.
- `agentao.harness.projection.*` — internal redaction layer.

---

## Reference

| Area | Location |
|---|---|
| `Agentao(...)` signature | [`agentao/agent.py`](../agentao/agent.py) |
| `build_from_environment(...)` | [`agentao/embedding/factory.py`](../agentao/embedding/factory.py) |
| Capability protocols (public surface) | [`agentao/harness/protocols.py`](../agentao/harness/protocols.py) — re-exports `FileSystem`, `ShellExecutor`, `MCPRegistry`, `MemoryStore` and their value shapes |
| Capability defaults / reference impls | [`agentao/capabilities/`](../agentao/capabilities/) — `LocalFileSystem`, `LocalShellExecutor`, etc. (not part of the public surface) |
| Default IO impls | `LocalFileSystem`, `LocalShellExecutor`, `SQLiteMemoryStore`, `FileBackedMCPRegistry` |
| Host-facing harness contract | [`agentao/harness/`](../agentao/harness/) — public types; full reference at [`docs/api/harness.md`](api/harness.md) |
| Schema snapshots | [`docs/schema/harness.events.v1.json`](schema/harness.events.v1.json), [`docs/schema/harness.acp.v1.json`](schema/harness.acp.v1.json) |
| Working examples | [`examples/`](../examples/) — `data-workbench`, `batch-scheduler`, `ticket-automation`, `saas-assistant`, `headless_worker.py` |
| Transport API | [`docs/ACP.md`](ACP.md) (also covers headless / SDK transport) |
| Permission rules | [`docs/features/TOOL_CONFIRMATION_FEATURE.md`](features/TOOL_CONFIRMATION_FEATURE.md) |
| Logging | [`docs/LOGGING.md`](LOGGING.md) |
| Skills | [`docs/SKILLS_GUIDE.md`](SKILLS_GUIDE.md) |
