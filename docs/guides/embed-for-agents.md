# Embedding Agentao — Guide for Coding Agents

**Audience:** a coding agent (Claude Code, Codex, Cursor, …) tasked with
embedding the Agentao runtime into *another* project. You are reading
this because someone asked you to "add Agentao" / "embed the agent" /
"drive Agentao from our app."

**This is a distilled playbook, not the full reference.** The
authoritative sources are linked inline; open them only when a step
below points you there.

- Full embedding reference: [`docs/guides/embedding.md`](embedding.md)
- Stable host API: [`docs/reference/host-api.md`](../reference/host-api.md)
- Design / stability boundary: [`docs/design/embedded-host-contract.md`](../design/embedded-host-contract.md)
- Non-Python hosts (stdio): [`docs/guides/acp.md`](acp.md)
- Working examples: [`examples/`](../examples/)

> ⚠️ This doc is for embedding Agentao **into other projects**. It is
> NOT the same as the repo's `CLAUDE.md` (which guides agents editing
> *this* repo) or `AGENTAO.md` (instructions the running agent reads).

---

## 0. First decide: do you even want in-process embedding?

Pick the surface before writing any code.

| Host situation | Use | Doc |
|---|---|---|
| Host is a **Python** process (webapp, worker, notebook, plugin) | In-process embedding (`Agentao(...)`) | this doc |
| Host is **not Python** (editor, IDE extension, Electron, non-Python sandbox) | ACP stdio server: `agentao --acp --stdio` | [`ACP.md`](acp.md) |
| You only need **non-interactive automation** (run a prompt, get exit code) | `agentao run --prompt "..."` | [`docs/reference/configuration.md`](../reference/configuration.md), `agentao/cli/run.py` |
| You want Agentao to **drive other agents** (Claude Code, Codex) as backends | ACP client | [`features/acp-client.md`](acp-client.md) |

If the answer is not "in-process Python," stop reading this file and go
to the linked doc. The rest of this guide assumes in-process embedding.

---

## 1. Then decide: factory vs. pure injection

There are exactly two supported construction paths. Do not invent a
third (no module-level singletons, no `Agentao()` with no args — that
raises `TypeError` since 0.3.0).

| You want… | Use | Reads env/disk/cwd? |
|---|---|---|
| Drop-in CLI parity — read `.env`, `~/.agentao/`, `<wd>/.agentao/` | `build_from_environment(...)` | **Yes** |
| Deterministic, side-effect-free, host owns every input (multi-tenant, containers, tests) | `Agentao(...)` (pure injection) | **No** |

**Default recommendation for embedding into a host app: pure
injection.** It is the only form that gives the host full control and
no surprise disk reads. Use the factory only for scripts/notebooks that
genuinely want to inherit local config.

### Factory skeleton

```python
from pathlib import Path
from agentao.embedding import build_from_environment

agent = build_from_environment(working_directory=Path("/srv/myapp/run-1"))
reply = agent.chat("Summarize today's logs.")
agent.close()
```

### Pure-injection skeleton (copy this for host integration)

```python
from pathlib import Path
from agentao import Agentao
from agentao.llm import LLMClient
from agentao.transport import NullTransport

agent = Agentao(
    working_directory=Path("/srv/myapp/run-1"),   # REQUIRED, frozen at construction
    llm_client=LLMClient(
        api_key="sk-...",
        base_url="https://api.openai.com/v1",
        model="gpt-5.4",
    ),
    transport=NullTransport(),
)
try:
    reply = agent.chat("Summarize today's logs.")
finally:
    agent.close()                                 # ALWAYS close
```

Required args (pure injection):

- `working_directory` — absolute path; frozen at construction. A later
  `os.chdir` in the host has no effect.
- `llm_client` **or** the trio `api_key` + `base_url` + `model`.
  Missing both → `ValueError`.

Everything else (`permission_engine`, `memory_manager`, `mcp_registry`,
`transport`) has a safe default. See the table in
[`EMBEDDING.md` §2](embedding.md#2-pure-injection-construction).

---

## 2. Async hosts

If the host already runs an event loop (FastAPI, aiohttp, a bot, an IDE
plugin), use `arun()` — never call the sync `chat()` on the loop thread.

```python
async def handle(req):
    agent = build_from_environment(working_directory=Path(req.workdir))
    try:
        return await agent.arun(req.prompt)
    finally:
        await asyncio.to_thread(agent.close)
```

`arun()` runs the sync loop in a worker thread. To cancel, pass a
`CancellationToken` into the call and trip it from elsewhere; the
worker checks it at every tool boundary and LLM chunk. Cancelling the
awaiting `asyncio` task (timeout / client disconnect) also forwards.

```python
from agentao.cancellation import CancellationToken

token = CancellationToken()
task = asyncio.create_task(agent.arun(req.prompt, cancellation_token=token))
# elsewhere: token.cancel("client-disconnect")
```

---

## 3. The import rules (get these wrong and you couple to internals)

These are the lines most likely to drift. Follow them exactly.

✅ **DO import from these stable surfaces:**

```python
from agentao import Agentao
from agentao.embedding import build_from_environment
from agentao.llm import LLMClient
from agentao.transport import NullTransport
from agentao.host import (                      # observability contract
    ActivePermissions, HostEvent,
    ToolLifecycleEvent, SubagentLifecycleEvent, PermissionDecisionEvent,
)
from agentao.host.protocols import (            # capability injection
    FileSystem, ShellExecutor, MemoryStore, MCPRegistry,
)
```

❌ **DO NOT import these from a host** (internal; may move any release):

- `agentao.capabilities.*` for the *protocol types* — import the
  Protocols from `agentao.host.protocols`. (The reference *impls* like
  `LocalFileSystem` do live in `agentao.capabilities` and are fine to
  use as defaults to wrap.)
- `agentao.transport.AgentEvent`, `Transport.emit(...)` — internal
  event shape; use `agent.events()` instead.
- `agentao.runtime.*`, `agentao.host.projection.*` — runtime-private.

⚠️ **Rename trail (don't use the old names):**

- `agentao.harness` → **`agentao.host`** (deprecated alias since 0.4.2,
  removed in 0.5.0). Import from `agentao.host`.
- `allow_all_tools` flag is **gone**. Use permission modes / the
  permission engine instead (see §5).

---

## 4. Capability injection (redirect IO without subclassing)

To route filesystem / shell / memory / MCP through your own backend
(Docker exec, remote sandbox, audit proxy, Postgres, …), pass a
duck-typed object matching the Protocol. No inheritance — PEP 544.

```python
from agentao.host.protocols import FileSystem      # the contract
from agentao.capabilities import LocalFileSystem    # default impl to wrap

class AuditedFileSystem:                            # duck-types FileSystem
    def __init__(self, inner): self.inner = inner
    def read_bytes(self, p): log("read", p); return self.inner.read_bytes(p)
    # ... implement the remaining methods, each delegating to self.inner

agent = build_from_environment(
    working_directory=workdir,
    filesystem=AuditedFileSystem(LocalFileSystem()),
    shell=my_remote_shell_executor,
)
```

The four protocols: `FileSystem`, `ShellExecutor`, `MemoryStore`,
`MCPRegistry`. Full method signatures: `agentao/host/protocols.py` and
[`EMBEDDING.md` §3](embedding.md#3-capability-injection).

### 4.1 Path-domain write boundary (declare some subpaths read-only)

A common host need: the agent's `working_directory` is writable, but
some subpaths must be **deterministically read-only** (e.g. `raw/`,
config files). A `FileSystem` wrapper enforces that on the same
`filesystem=` injection point — no tool code changes. This is the
`PolicyFileSystem` interim recipe; the full version + rationale is in
[`docs/design/host-fs-policy.md`](../design/host-fs-policy.md).

```python
from pathlib import Path
# wrap any FileSystem impl (LocalFileSystem, your AuditedFileSystem, …)

def _effective_target(raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():                 # FileSystem is absolute-only (see below)
        raise PermissionError(f"non-absolute path reached FS: {raw}")
    t = p.parent.resolve(strict=False) / p.name      # parent chain (..-safe)
    if t.is_symlink():                               # open() follows a leaf symlink
        t = t.resolve(strict=False)
    return t

def _under(root: Path, target: Path) -> bool:
    root = root.resolve()
    return target == root or root in target.parents

class PolicyFileSystem:                     # duck-types FileSystem
    def __init__(self, inner, working_directory: Path, immutable=()):
        self._fs = inner
        self._wd = working_directory.resolve()        # cwd is implicitly writable
        self._immutable = tuple(Path(m).resolve() for m in immutable)
    def write_text(self, path, data, *, append=False):
        t = _effective_target(str(path))
        if not _under(self._wd, t):
            raise PermissionError(f"outside working_directory: {t}")
        if any(_under(m, t) for m in self._immutable):   # immutable wins, leaf-safe
            raise PermissionError(f"immutable: {t}")
        return self._fs.write_text(path, data, append=append)
    def __getattr__(self, name): return getattr(self._fs, name)   # reads pass through

from agentao.capabilities import LocalFileSystem
agent = build_from_environment(
    working_directory=kb,
    filesystem=PolicyFileSystem(LocalFileSystem(), kb,
                                immutable=[kb / "raw", kb / "AGENTAO.md"]),
)
```

**Constraints you must respect (each one is a real footgun):**

- **Restrict only, never expand.** Built-in `write_file` / `replace` run
  a single-root `PathPolicy` check *before* the capability
  (`file_ops.py:197,368`), so the wrapper sits downstream. It can carve
  **read-only subpaths out of cwd** (works, zero change), but it
  **cannot** authorize writes to roots *outside* cwd — those are rejected
  upstream. Multi-root-outside-cwd needs a host `extra_tool` or an
  agentao change (see the design doc).
- **Absolute paths only.** The `FileSystem` protocol accepts absolute
  paths; relative resolution is the *tool's* job (`Tool._resolve_path`).
  Built-ins already hand `write_text` an absolute path. A custom
  `extra_tool` must call `self._resolve_path(file_path)` *before*
  `self.filesystem.write_text(...)` — the `is_absolute()` guard turns a
  miss into a loud refusal instead of a silent process-cwd write.
- **Test the leaf-dereferenced target, not the literal path.** A symlink
  `scratch/link → raw/secret` must be denied; resolving the leaf
  (`_effective_target`) is what makes "immutable wins" hold. Do **not**
  reuse `PathPolicy.contain_file` per root as a membership test — it
  short-circuits on the parent and is fail-open for this case.
- **Hot-swap by mutating the instance, not replacing it.** Tools capture
  `agent.filesystem` at registration, so reassigning it is invisible;
  mutate a field on the live wrapper instead.

Status: proposal-stage / demand-gated. Covers the within-cwd read-only
facet today; shell writes are out of scope (shell uses the `ShellExecutor`
capability, not `FileSystem`).

---

## 5. Permissions (do not bypass — gate instead)

Tools with `requires_confirmation=True` (shell, web, writes, deletes)
are gated by `PermissionEngine`. From a host you set the posture, you
do not disable the engine.

- Modes (`agentao.permissions.PermissionMode`): `read-only`,
  `workspace-write` (default), `full-access`, `plan`. Set with
  `agent.permission_engine.set_mode(PermissionMode.WORKSPACE_WRITE)`.
- Rules come from `.agentao/permissions.json` (project) +
  `~/.agentao/permissions.json` (user). The engine does no file I/O;
  `agentao.embedding.permission_loader.load_permission_rules()` reads
  them.
- Inspect what *will* apply: `agent.active_permissions()` →
  `{mode, rules, loaded_sources}` (JSON-safe snapshot).
- Layering host policy? Tag provenance with
  `agent.permission_engine.add_loaded_source("injected:<name>")`.

For untrusted prompts / multi-tenant, also wire `sandbox_policy`
(see §7).

---

## 6. Observability (watch the agent without touching internals)

Stream lifecycle events with the async iterator `agent.events()`:

```python
async for ev in agent.events():                 # session_id=None → all sessions
    if isinstance(ev, ToolLifecycleEvent):
        print(ev.tool_name, ev.phase, ev.outcome)
    elif isinstance(ev, PermissionDecisionEvent):
        print("perm", ev.tool_name, ev.outcome, ev.matched_rule)
    elif isinstance(ev, SubagentLifecycleEvent):
        print("subagent", ev.child_session_id, ev.phase)
```

Delivery contract (full version in [`api/host.md`](../reference/host-api.md)):

- Same-session ordering guaranteed; cross-session global ordering not.
- Within a `tool_call_id`: `PermissionDecisionEvent` precedes
  `ToolLifecycleEvent(phase="started")`.
- **No replay** — events before your first subscription are dropped.
- Host-pulled backpressure (bounded queue; producer blocks, never grows
  unbounded). One public stream consumer per `Agentao` instance (MVP).

For streaming assistant *text/tokens* (not on the stable contract),
you must consume the internal `Transport` — accept that it may change
between releases.

---

## 7. Opt-in subsystems

Default `None` on bare construction; the factory wires them from disk
unless overridden. Enable deliberately:

| Subsystem | Enable when | Reads |
|---|---|---|
| `sandbox_policy` | untrusted prompts, multi-tenant, CI eval | `<wd>/.agentao/sandbox.json` |
| `replay_config` | reproducing flake, A/B prompt diffs | `<wd>/.agentao/replay.json` |
| `bg_store` | long-lived server surviving restarts | `<wd>/.agentao/bg/` |
| `enable_builtin_agents=True` | want `codebase-investigator` / `generalist` delegation tools | `<wd>/.agentao/settings.json :: agents.enable_builtin` |

---

## 8. Logging — silence it from the host

By default `Agentao(...)` writes `<wd>/agentao.log` and raises the
`"agentao"` package logger to DEBUG. To stop both, inject a logger:

```python
import logging
quiet = logging.getLogger("myhost.agentao")
quiet.addHandler(logging.NullHandler())
quiet.propagate = False
agent = Agentao(api_key=..., base_url=..., model=...,
                working_directory=workdir, logger=quiet)
```

⚠️ Passing only `log_file=None` skips the file but **still** elevates
the package logger to DEBUG. To leave the host's logging untouched you
must pass `logger=`. ([`EMBEDDING.md` §2](embedding.md#2-pure-injection-construction))

---

## 9. Integration checklist (run through this before you call it done)

- [ ] Picked the right surface (§0) — in-process Python, else go to ACP/run.
- [ ] Construction uses `build_from_environment` **or** pure `Agentao(...)`; no no-arg `Agentao()`.
- [ ] `working_directory` is an explicit absolute path.
- [ ] LLM creds supplied (`llm_client` or `api_key`+`base_url`+`model`).
- [ ] Every code path calls `agent.close()` (use `try/finally`).
- [ ] Async host uses `arun()`, not `chat()` on the loop thread.
- [ ] Imports come from `agentao`, `agentao.embedding`, `agentao.host`, `agentao.host.protocols` only — no `agentao.runtime.*` / `AgentEvent` / `agentao.harness`.
- [ ] Permission posture set explicitly; untrusted input → `sandbox_policy`.
- [ ] Secrets come from the host (env/secret manager), not hard-coded.
- [ ] Logging handled (`logger=` if the host owns logging).
- [ ] Pin/declare dependency on a compatible Agentao version; note `pydantic>=2` is required.

---

## 10. Verify

Minimal smoke test the integration should pass:

```python
agent = Agentao(working_directory=Path("/tmp/agentao-smoke"),
                api_key=..., base_url=..., model=..., transport=NullTransport())
try:
    out = agent.chat("Reply with the single word: ok")
    assert "ok" in out.lower()
finally:
    agent.close()
```

For the host-facing API and event contract specifics, the canonical
reference is [`docs/reference/host-api.md`](../reference/host-api.md); for everything not
covered here, [`docs/guides/embedding.md`](embedding.md).
