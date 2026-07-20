# Interactive CLI host injection: `agent_factory`

**Status:** **Implemented** 2026-07-19 for [issue #132](https://github.com/jin-bo/agentao/issues/132) — the seam (§3), the post-condition checks (§3.1), and the transport guard (§3.2, resolving Q3) all landed. Still open: Q1 (type-alias export — currently module-internal), Q2 (parameter naming — currently `agent_factory`), Q4 (`main()` error rendering — unchanged), Q5 (`agentao run` — out of scope). The decision proposed here is a keyword-only `agent_factory` seam on `AgentaoCLI` and `cli.main()`; it does not extend plugins or introduce a global tool registry.
**Audience:** maintainers and Python hosts that reuse Agentao's interactive CLI while supplying a host-configured `Agentao` runtime.
**Companions:**
- `docs/design/cli-host-agent-factory.zh.md` — Chinese version
- `docs/design/host-tool-injection.md` — landed construction-time `extra_tools` / `disable_tools` contract
- `docs/design/runtime-tool-injection.md` — landed runtime `add_tool` / `remove_tool` contract
- `docs/reference/host-api.md` — stable in-process host API
- `agentao/acp/session_new.py` — existing `agent_factory` dependency-injection precedent

---

## 1. Problem

Agentao has a working host tool-injection contract, but a Python host that embeds
the **interactive CLI** cannot reach it through a supported API.

Verified on `main@8266de1`:

| Fact | Evidence | Result |
|---|---|---|
| `Agentao` accepts `extra_tools`, `disable_tools`, and `enabled_tools` | `agentao/agent.py:53-104` | Direct embedding works |
| `build_from_environment(..., **overrides)` forwards constructor overrides | `agentao/embedding/factory.py:124-127,253-262` | Factory embedding works |
| `Agentao.add_tool()` is the runtime dual | `agentao/agent.py:853` | Works only after the host obtains the instance |
| `AgentaoCLI.__init__()` accepts no host injection | `agentao/cli/app.py:43` | The interactive CLI closes the construction seam |
| `AgentaoCLI` calls the factory with a fixed kwarg set | `agentao/cli/app.py:84-88` | `extra_tools` cannot be forwarded |
| `main()` constructs `AgentaoCLI()` and immediately runs it | `agentao/cli/entrypoints.py:29,74-78` | It neither accepts a builder nor exposes the agent before the first turn |
| The CLI's own test suite already patches a dead name | `agentao/cli/app.py:30`, `tests/test_menu_confirmation.py:12` | The silent-failure mode below is not hypothetical — it is present in-repo |

The current workaround patches module globals. It is fragile because
`cli/app.py:33` imports the factory by name:

```python
from ..embedding import build_from_environment
```

Replacing `agentao.embedding.build_from_environment` later does not update the
already-bound `agentao.cli.app.build_from_environment` name. A host must know the
CLI's internal import topology, mutate process-global state, and update the patch
whenever another bound import site is added. A missed site fails silently: the CLI
still starts, but the host's tools are absent.

That silent-failure mode is already demonstrable inside this repository.
`agentao/cli/app.py:30` imports `Agentao` and never references it — the import is
dead, left over from an earlier construction path. The interactive CLI tests
(`tests/test_menu_confirmation.py`, `tests/test_status_pause.py`,
`tests/test_clear_resets_confirm.py`, `tests/test_readchar_confirmation.py`)
patch exactly that name via `patch('agentao.cli.app.Agentao')`, but construction
goes through `build_from_environment`, which imports `Agentao` locally from
`..agent`. The patch therefore intercepts nothing; those tests build a real
runtime and pass anyway (`uv run python -m pytest tests/test_menu_confirmation.py`
→ 7 passed). A patch-based seam that stopped working produced no failure signal
at all. That is the outcome this design is meant to make impossible for hosts.

This is an API-boundary gap, not a failure in tool validation or registration.
The existing construction-time and runtime injection tests pass; the missing
piece is a stable way to control the runtime constructed by the interactive CLI.

## 2. Goals and non-goals

### Goals

1. Let a Python host reuse the stock interactive CLI with a host-configured
   `Agentao` instance.
2. Preserve CLI-owned lifecycle objects: the CLI transport, `PlanSession`, and
   context limit must still be supplied to runtime construction.
3. Keep default `agentao` console behavior unchanged.
4. Keep injection per CLI instance, with no process-global registry or patch.
5. Reuse an existing repository pattern rather than introduce a second
   dependency-injection vocabulary.

### Non-goals

- No JSON/settings representation of Python tool implementations.
- No plugin-manifest `tools` field and no change to plugin trust semantics.
- No change to `extra_tools`, `add_tool`, MCP, plan-tool, or plugin precedence.
- No general-purpose post-construction callback pipeline.
- No change to `agentao run` in this issue; see §8.
- No guarantee that an arbitrary object pretending to be `Agentao` works with
  the CLI. The factory returns a real, CLI-compatible `Agentao` runtime.

## 3. Decision: inject the agent factory

Add a keyword-only `agent_factory` parameter to both public interactive entry
points:

```python
# agentao/cli/app.py
AgentFactory = Callable[..., Agentao]


class AgentaoCLI:
    def __init__(self, *, agent_factory: Optional[AgentFactory] = None):
        ...
        factory = (
            build_from_environment
            if agent_factory is None
            else agent_factory
        )
        self.agent = factory(
            transport=self,
            max_context_tokens=context_limit,
            plan_session=self._plan_session,
        )
```

```python
# agentao/cli/entrypoints.py
def main(
    resume_session: Optional[str] = None,
    *,
    agent_factory: Optional[AgentFactory] = None,
):
    ...
    cli = AgentaoCLI(agent_factory=agent_factory)
```

`None`, rather than the factory function as the signature default, keeps default
resolution explicit and avoids capturing a replaceable callable in a default
argument. The supported extension mechanism is still the parameter, not patching
that module name. This deliberately diverges from the ACP precedent cited in
§5.1, which uses `agent_factory: AgentFactory = default_agent_factory`
(`agentao/acp/session_new.py:302,475`); the shape is the same, the default is
not, and the divergence is a considered choice rather than an oversight.

The factory is called with exactly the CLI-owned construction kwargs:

| Kwarg | Owner | Required behavior |
|---|---|---|
| `transport` | `AgentaoCLI` | Forward to `Agentao` so prompts, streaming, permission requests, and events use the interactive CLI |
| `max_context_tokens` | CLI environment policy | Forward as the runtime context limit |
| `plan_session` | `AgentaoCLI` | Forward so runtime and CLI plan state share one object |

A host normally composes the environment factory with `functools.partial`:

```python
from functools import partial

from agentao.cli import main
from agentao.embedding import build_from_environment

main(
    agent_factory=partial(
        build_from_environment,
        extra_tools=[NewsSearchTool(), PublishTool()],
        disable_tools={"web_search"},
    )
)
```

The same seam also supports existing constructor contracts without growing the
CLI signature once per contract:

```python
factory = partial(
    build_from_environment,
    working_directory=host_project_root,
    filesystem=host_filesystem,
    shell=host_shell,
    extra_tools=host_tools,
)
cli = AgentaoCLI(agent_factory=factory)
cli.run()
```

`llm_client=` is deliberately absent from this example: it works for the main
runtime but is not inherited by sub-agents, so `/agent` bypasses it. See §11 Q7.

The host-supplied factory must accept the three keyword arguments above. It may
accept `**kwargs`, wrap `build_from_environment`, or construct `Agentao`
directly. Ignoring or replacing CLI-owned dependencies is outside the contract;
the CLI does not silently repair a non-conforming result.

### 3.1 Post-conditions on the returned runtime

The kwarg table above is the *input* half of the contract. The CLI also has hard
requirements on what comes back, because it binds several attributes off the
returned agent immediately after construction. A factory that wraps
`build_from_environment` satisfies all of these for free; a factory that
constructs `Agentao` directly — which §3 explicitly permits — must not omit them.

| Required on the returned agent | Consumed at | Failure if absent |
|---|---|---|
| `working_directory` | `app.py:317` | `.agentao/` reads/writes target the wrong root |
| `permission_engine`, **non-`None`** | `app.py:326,374` | `AttributeError: 'NoneType' object has no attribute 'set_mode'` during CLI init |
| `tools` (a `ToolRegistry`) | `app.py:336-337` | `plan_save` / `plan_finalize` cannot be registered; plan mode is broken |
| `tool_runner` | `app.py:340,382` | Session id and read-only mode cannot be bound |
| `_plan_session`, **identical to the CLI's** | `agent.py:503`, `agent.py:1126` | `/plan` switches the CLI but not the runtime; the model never sees `plan_save` / `plan_finalize` and cannot finish the plan |
| assignable `_session_id` | `app.py:339` | Events carry the construction-time UUID instead of the CLI session id |
| `messages`, `memory_manager`, `context_manager`, `skill_manager`, `clear_history`, `get_current_model` | `input_loop.py`, `commands/sessions.py` | Slash commands (`/clear`, `/new`, `/sessions`, `/replay`, `/model`) raise mid-session, after `on_session_end()` has already fired |

`permission_engine` is the sharp edge: `build_from_environment` always builds
one, so the requirement is invisible until a host constructs `Agentao(...)`
itself and leaves it at its `None` default. Without a check the resulting error
surfaces after step 3 of §4 and names neither the factory nor the missing kwarg.

**Implemented** as `agentao/cli/app.py::_check_agent_postconditions`, called
immediately after the factory returns. Missing attributes are reported together
in one `TypeError` rather than one per run. The checks run on the default
(`agent_factory=None`) path as well — they are cheap probes and make stock
startup a live regression test for the contract hosts are held to.

**What these checks are not.** They are `hasattr` / `is` probes, not proof of a
working runtime. A `Mock` or any `__getattr__`-backed proxy satisfies every
attribute probe vacuously, and `_session_id` assignability is not checked at all
(a proxy that stores the write on itself accepts it silently — the failure is
the wrong-value row in the table above, not an exception). Closing that would
require `isinstance(agent, Agentao)`, which would also forbid the wrapper and
proxy runtimes this seam exists to serve. Left open as Q6 rather than decided
unilaterally.

### 3.2 Overriding a CLI-owned kwarg fails silently

The recommended `functools.partial` composition has one footgun worth stating
outright, and it comes in two independent directions that need two different
mitigations.

**Direction 1 — the host's value is discarded.** `partial` keywords are
*overridden* by call-time keywords, so
`partial(build_from_environment, transport=host_transport)` does not raise: the
CLI's `transport=self` wins, the host's transport is dropped, and the runtime is
bound correctly to the CLI. Every post-condition then passes. **Nothing
downstream can detect this** — a post-construction check structurally cannot,
because the resulting object is indistinguishable from a correct one. The same
applies to a pre-bound `max_context_tokens` (the host's limit is silently
ignored) and `plan_session`.

Mitigation: `_reject_prebound_kwargs` inspects the factory *before* calling it
and raises `TypeError` if a `functools.partial` pre-binds `transport`,
`max_context_tokens`, or `plan_session`. Only `partial` is inspected, because
that is the shape this API documents and recommends.

**Direction 2 — the CLI's value is discarded.** A wrapper that rebinds
`kwargs["transport"]` before delegating returns a runtime wired to something
other than the CLI. Streaming output, permission prompts, and events never reach
the terminal; the CLI appears to hang rather than reporting a violation.

Mitigation, **decided (Q3): the CLI checks it.** `_check_agent_postconditions`
requires the CLI to be *reachable* from the runtime's transport — directly, or
through a chain of wrappers exposing the wrapped transport as `inner`.

Reachability rather than identity, because wrapping is agentao's own convention:
`ReplayManager.start()` does exactly this — `agent.transport =
ReplayAdapter(agent.transport, recorder)` (`agentao/replay/manager.py:104-107`,
with `ReplayAdapter.inner` at `agentao/replay/adapter.py:48-51`). A strict `is`
test would reject a factory that enables recording before returning, and would
reject a host tee adapter, for no safety gain: what actually breaks the CLI is a
transport with *no path back to it at all*.

The check covers **both** transport fields. `ToolRunner` captures the transport
at construction and routes permission prompts through its own `_transport` copy,
so a runtime whose two fields disagree hangs at the first confirmation even
though `agent.transport` looks right — which is why `ReplayManager` sets both.

## 4. Lifecycle and precedence

The change only replaces the callable used at the existing construction point.
Startup order remains:

1. Initialize CLI state and its `PlanSession`, and provisionally read the saved
   permission mode from the process cwd.
2. Reject a factory that pre-binds a CLI-owned kwarg (§3.2), then invoke
   `agent_factory(transport=self, max_context_tokens=..., plan_session=...)`.
3. Validate the returned runtime (§3.1), then bind `_project_root` and
   `permission_engine` from it. **If the factory supplied its own
   `working_directory`, re-read the saved mode** — step 1 read it from a
   different `.agentao/settings.json`, and without the re-read the project's
   saved posture is ignored at startup and then overwritten by the next
   `/mode`. This ordering hazard did not exist before the seam: the CLI passed
   no `working_directory`, so the two roots were guaranteed equal.
4. Register CLI-owned `plan_save` / `plan_finalize` tools.
5. Bind the CLI session id to the agent and tool runner.
6. Load CLI plugins.
7. Create the prompt session and enter the input loop.

Consequences:

- `extra_tools` supplied by the host are registered during `Agentao`
  construction and are visible on the first turn.
- Existing `extra_tools` validation and capability binding remain authoritative;
  the CLI does not register the tools itself.
- Plan tool names remain reserved by the existing runtime guard, so a host
  factory cannot replace the CLI plan-state tools through `extra_tools`.
- Plugin loading order is unchanged. Plugin agents use namespace-qualified
  runtime names (`<plugin>:<agent>`), and this design does not redefine plugin
  collision or precedence semantics.
- A factory is invoked once per `AgentaoCLI` instance. Two CLI instances may use
  different factories and **host tool sets** without shared mutable registration
  state. This isolation claim is scoped to tools built through the factory:
  inline plugin directories remain process-global
  (`agentao/cli/entrypoints.py:373` writes `_globals._plugin_inline_dirs`), so
  plugin-contributed tools are still shared across instances in one process.
  This design does not change that.

## 5. Why this shape

### 5.1 It matches the ACP precedent

`agentao/acp/session_new.py` already defines `AgentFactory = Callable[...,
Agentao]` and accepts it in `handle_session_new()` / `register()`. The ACP handler
owns transport and permission/session state, then supplies those objects to the
injected factory. The interactive CLI has the same ownership problem and should
use the same dependency-injection shape.

Two intentional deviations: the CLI defaults the parameter to `None` rather than
to the default factory (§3), and the two `AgentFactory` aliases describe
**different call contracts** — ACP's is `(cwd, client_capabilities, transport,
permission_engine, mcp_servers, model)`, the CLI's is `(transport,
max_context_tokens, plan_session)`. Reusing the same alias name for an
incompatible signature is a real confusability risk; see §11 Q1.

### 5.2 It resolves the construction cycle

Passing a pre-built `Agentao` object is the wrong seam. The CLI transport is the
`AgentaoCLI` instance itself, and the plan session is created by that instance.
Neither exists before CLI construction. A factory delays runtime construction
until the CLI-owned dependencies exist.

### 5.3 It does not mirror every runtime kwarg

A dedicated `extra_tools=` argument would solve issue #132 narrowly, but
`disable_tools`, `enabled_tools`, `filesystem`, `shell`, `llm_client`, and future
host contracts would remain unreachable. Repeating the `Agentao` constructor on
`AgentaoCLI` creates two public signatures that drift. The factory forwards the
existing contract instead.

### 5.4 It preserves ownership boundaries

An untyped `agent_overrides: Mapping[str, Any]` was considered and rejected. It
would require merge-precedence rules for `transport`, `plan_session`, and
`max_context_tokens`, and would turn misspelled constructor names into a
stringly-typed CLI surface. A callable has an explicit responsibility: consume
the CLI-owned dependencies and return the runtime.

## 6. Compatibility and failure behavior

- Both new parameters are keyword-only and optional: existing Python and console
  callers are source-compatible.
- With `agent_factory=None`, the exact existing
  `build_from_environment(transport=..., max_context_tokens=...,
  plan_session=...)` path runs.
- `resume_session` keeps its existing meaning and positional compatibility.
- `AgentaoCLI(...)` lets a factory exception propagate to its caller, as
  construction errors do today.
- `main(...)` keeps the existing top-level exception handling and fatal-error
  rendering. This design does not change exit codes or make `main()` return the
  agent. Note what that inherits: `agentao/cli/entrypoints.py:82-84` catches
  bare `Exception` and prints a single `Fatal error: {e}` line with no traceback
  before `sys.exit(1)`. A `TypeError` from a host factory with a wrong signature
  is therefore reported as one line of text with no frame pointing at the host's
  own code — poor ergonomics for a surface whose entire audience is embedders.
  See §11 Q4.
- The factory is instance-scoped. No locking or global cleanup is required.
- The seam becomes a documented public CLI-embedding API and therefore requires
  normal deprecation discipline if its call contract changes.

## 7. Rejected alternatives

| Alternative | Decision |
|---|---|
| Add only `extra_tools=` to `AgentaoCLI` / `main()` | Rejected as the primary design: fixes one constructor contract and guarantees signature repetition later |
| Pass a pre-built `Agentao` | Rejected: cannot naturally receive `transport=self` and the CLI-owned `PlanSession` |
| Return the agent from `main()` | Rejected: `main()` returns only after the interactive loop ends, too late for first-turn injection |
| Add a post-build `configure_agent(agent)` callback | Rejected: duplicates the runtime's construction-time contract and makes ordering relative to plan tools/plugins a new API |
| Global entry point or registry | Rejected: process-global mutation, discovery/ordering concerns, and poor multi-instance isolation |
| Extend plugin manifests with tools | Rejected: much larger code-loading, trust, permissions, packaging, and namespace surface |
| Document monkey-patching as supported | Rejected: coupled to import topology, non-local, unsafe for multiple instances, and silently breakable |
| Use MCP as the workaround | Rejected as an equivalent: valid for remote/process tools, but adds transport and lifecycle costs and does not replace in-process `extra_tools` |

## 8. Scope boundary: `agentao run`

`agentao/cli/run.py:527` also constructs an agent through a fixed
`build_from_environment(**factory_kwargs)` call. That automation entry point has
the same abstract extensibility question, but a different public surface:
`RunSpec`, exit envelopes, signal handling, and non-interactive transport.

Issue #132 is triggered by a thin host embedding the **interactive CLI**. This
design deliberately does not thread a Python callable through `execute()` or
`_execute_with_args()`. If a real host needs to embed `agentao run`, add the same
factory seam in a separate design with automation-specific tests rather than
quietly expanding this change.

ACP needs no corresponding change: it already exposes `agent_factory`.

## 9. Implementation surface

| Change | Location | Status |
|---|---|---|
| `AgentFactory` alias, optional keyword-only `agent_factory`, factory call, and `_check_agent_postconditions` | `agentao/cli/app.py` | landed |
| Optional keyword-only `agent_factory` forwarded to `AgentaoCLI` | `agentao/cli/entrypoints.py` | landed |
| Focused factory-seam and post-condition tests | `tests/test_cli_agent_factory.py` | landed (12 tests) |
| Migrate off the unsupported `build_from_environment` patch seam | `tests/test_clear_resets_confirm.py` | landed (5 tests) |
| Export `AgentFactory` for typing, or keep it internal | `agentao/cli/__init__.py` | **deferred — Q1** |
| Document the programmatic interactive-CLI example | `docs/reference/` or the embedding guide | **pending** |

No changes are required in `Agentao`, tool registries, plugin models, MCP, or the
`agentao.host` exports.

Two incidental cleanups fall out of the change and should land with it rather
than separately:

- `agentao/cli/app.py:30`'s currently-dead `from ..agent import Agentao` becomes
  live as the referent of `AgentFactory = Callable[..., Agentao]`.
- The interactive CLI tests listed in §1 can migrate from
  `patch('agentao.cli.app.Agentao')` — which intercepts nothing today — to an
  injected fake factory, making them test what they claim to test.

## 10. Test matrix and acceptance criteria

### Tests

1. A recording factory receives `transport is cli`, the CLI's exact
   `_plan_session`, and the environment-derived context limit.
2. A partial factory forwarding `extra_tools` makes the tool visible before the
   first `cli.run()` turn.
3. `main(agent_factory=factory)` forwards the identical callable to
   `AgentaoCLI` and preserves resume behavior.
4. `AgentaoCLI()` with no factory takes the current default path.
5. Two CLI instances with different factories expose different *host* tool sets
   with no leakage. Plugin-contributed tools are out of scope for this
   assertion (§4).
6. A factory exception follows the existing direct-constructor and `main()`
   error paths.
7. A factory that returns a runtime built without a `permission_engine` fails
   with a diagnosable error rather than a bare `NoneType` `AttributeError`
   (§3.1) — or, if no guard is added, the post-conditions are documented and
   this test is dropped deliberately.
8. Existing interactive CLI, resume, plan, plugin, and status tests remain green.
   This is a weak signal on its own: per §1 those tests currently pass while
   patching a name that intercepts nothing, so "still green" does not establish
   that the seam is exercised. Criterion 1 and the migrated tests in §9 are what
   actually cover it.

### Acceptance criteria

- A downstream host can delete all monkey-patching and launch the stock
  interactive CLI with `extra_tools` through a documented callable.
- The injected tools are visible on the first model turn and retain the existing
  capability binding and validation behavior.
- Default console startup is behaviorally unchanged.
- The fix adds no global registry, plugin feature, config format, or new tool
  precedence rule.

## 11. Open review questions

1. **Export the type alias, and under what name?** Two questions, one decision.
   `AgentFactory` can stay internal with only the callable contract documented,
   or become a lazy `agentao.cli` export for typing ergonomics. Exporting it
   *under that name* would put two public `AgentFactory` aliases with
   incompatible call contracts in the tree (§5.1). Options: keep it internal;
   export it as `CliAgentFactory`; or export `AgentFactory` from `agentao.cli`
   and accept that the qualified module path is the disambiguator. Preference:
   internal, or `CliAgentFactory` if exported.
2. **Parameter naming:** `agent_factory` matches ACP and is preferred.
   `runtime_factory` is more explicit but would create two names for the same
   pattern. (Note this is orthogonal to Q1 — the *parameter* can match ACP even
   if the *type alias* does not.)
3. ~~**Guard the transport post-condition?**~~ **Resolved: check added**, as
   chain reachability over both `agent.transport` and
   `agent.tool_runner._transport`. Enabling it immediately surfaced five tests
   in `tests/test_clear_resets_confirm.py` that patched
   `agentao.cli.app.build_from_environment` and returned a bare `Mock()` —
   they now build a real runtime through `agent_factory=`. That is §1's
   argument reproduced in miniature: an unsupported seam that had been passing
   silently failed loudly the moment a contract check existed.
4. **Factory errors under `main()`:** should `main()` special-case a factory
   exception with a traceback or an embedder-oriented message (§6)? Doing so
   changes `main()`'s error rendering, which §2 lists as out of scope — but the
   status quo gives embedders a one-line report for their own bug.
5. **Follow-up for `agentao run`:** only open after a concrete programmatic
   automation host appears; do not fold it into issue #132 by default.
6. **Require `isinstance(agent, Agentao)`?** The post-conditions are `hasattr`
   probes, so a `Mock` or `__getattr__` proxy passes them vacuously (§3.1).
   An `isinstance` gate would close that and matches non-goal 6 ("the factory
   returns a real, CLI-compatible `Agentao` runtime") — but it forbids the
   proxy and wrapper runtimes the seam exists to serve, and forces every test
   double to be a real runtime. Currently **not** enforced; the attribute list
   is the compromise.
7. **`llm_client=` is not sub-agent-safe.** An injected client is used by the
   main runtime but not inherited by sub-agents: `AgentToolWrapper` re-resolves
   the LLM from raw `api_key` / `base_url` / `model` scalars and builds a stock
   client, so `/agent <name> <task>` bypasses a host's proxy, auth, and
   instrumentation — and a duck-typed client lacking `api_key` raises there.
   Pre-existing, not caused by this seam, but it makes `llm_client=` an
   over-claim for CLI hosts. Fixing it means threading the client through
   sub-agent construction, which is a separate change.

