# Fix: sub-agent construction missing `working_directory`

> **Status: IMPLEMENTED.** Landed in the working tree (2026-06-04). Production changes:
> `agentao/agents/tools/_wrapper.py` (added `from pathlib import Path`; new required
> `working_directory: Path` param after `llm_config_getter`; threaded into the `Agentao(...)`
> call in `_run_sync`) and `agentao/agents/manager.py::create_agent_tools` (passes
> `working_directory=self.project_root`). Regression test
> `tests/test_agent_subsystems_optional.py::test_sub_agent_construction_inherits_working_directory`
> plus two updated direct-construction tests; full suite green (2938 passed). Codex review: clean.
> The line numbers cited below are pre-implementation; the design rationale is retained as a record.

## Context

When a parent agent spawns a sub-agent (e.g. `data-analyst`), `AgentToolWrapper._run_sync`
constructs `Agentao(...)` at `agentao/agents/tools/_wrapper.py:361` **without** passing
`working_directory`. Since 0.3.0 `working_directory` is a required keyword-only parameter
(`agentao/agent.py:73`, after `*`, no default). Therefore every sub-agent invocation raises:

```
TypeError: Agentao.__init__() missing required keyword argument: 'working_directory'
```

The sub-agent should run in the **same** working directory as its parent.

## Why it stayed latent (verified)

- **conftest does not mask it.** `tests/conftest.py:40-46` monkeypatches `Agentao.__init__`,
  but only to backfill LLM credentials — it then calls the *real* `_orig_init`, which still
  requires `working_directory`.
- **No test reaches construction.** The sub-agent test files only assert on schema
  (`tests/test_agent_subsystems_optional.py` builds a wrapper and reads `.parameters`) or on
  event emission (`tests/test_host_subagent_events.py:180` uses `AgentToolWrapper.__new__(...)`
  to bypass `__init__` and drives `_spawn_subagent_event` / `_terminal_subagent_event`
  directly). None call `.execute()` / `_run_sync`, the only path that constructs `Agentao`.

## Design decision: static value, sourced from `AgentManager.project_root`

Two choices, both settled:

1. **Static value, not a getter.** `working_directory` is frozen at construction
   (`agent.py:181-187`), so there is nothing to read live. The existing getters
   (`readonly_mode_getter`, `permission_mode_getter`, `llm_config_getter`, …) all exist for
   *liveness* — values that mutate after registration. The call site already passes frozen
   scalars (`max_context_tokens`, `sandbox_policy`) as plain values; `working_directory`
   belongs in that group. A getter here would be ceremony that falsely signals runtime
   mutability.

2. **Source it from `AgentManager.project_root`, not the call site.** The manager is already
   constructed with the parent's working directory — `agent.py:541-543` passes
   `project_root=self._working_directory`, and `manager.py:25` stores it as
   `self.project_root = (project_root or Path.cwd()).expanduser().resolve()`. Since the parent
   freezes its own dir the same way (`agent.py:181-187`, `.expanduser().resolve()` — idempotent
   on an already-resolved path), `AgentManager.project_root` **is exactly** `agent.working_directory`.
   So `create_agent_tools` can pass `working_directory=self.project_root` from its own state.
   This keeps the production change to **two files** and matches the manager's existing
   responsibility (it already uses `self.project_root` to load project agents in
   `_load_definitions`). The earlier draft threaded the value through a third file
   (`tooling/agent_tools.py:63`) and added a pure pass-through parameter to
   `create_agent_tools`; both are avoidable.

## Verified facts (grep-confirmed)

- `agent.py:73` — `working_directory: Path` is keyword-only + required.
- `agent.py:181-187` — `self._working_directory` is frozen (`.expanduser().resolve()`) early in
  `__init__`, before tool wiring.
- `agent.py:541-543` — `AgentManager(project_root=self._working_directory, …)`.
- `agent.py:545` — `self._register_agent_tools()` runs **during construction**, delegating to
  `register_agent_tools(self)` (`agent.py:980` → `tooling/agent_tools.py:20`). Sub-agent tools
  are already on `agent.tools` once the constructor returns — callers never register them by hand.
- `agent.py:870` — public `working_directory` property exists on `Agentao`.
- `manager.py:25` — `self.project_root = (project_root or Path.cwd()).expanduser().resolve()`.
- `manager.py:144` / `:172` — `create_agent_tools` constructs each `AgentToolWrapper(...)`.
- `_wrapper.py:361` — `Agentao(...)` kwargs omit `working_directory` (the *only* construction
  site in `agents/`; only reads `sub_agent.working_directory` at lines 406/410). Both
  foreground and background spawns route here — `_launch_background` (line 467) calls
  `_run_sync` (line 492).
- `tooling/agent_tools.py:63-93` — call site passes `max_context_tokens` / `sandbox_policy`
  as plain values (the static-value idiom this fix follows); it does **not** change.

## Approach (static value, 2 production files)

1. **`agentao/agents/tools/_wrapper.py::__init__`** — add a required `working_directory: Path`
   parameter, store as `self._working_directory`. **Position matters:** the current signature
   (`_wrapper.py:44-63`) has only three no-default parameters (`definition`, `all_tools`,
   `llm_config_getter`) before the first defaulted one (`bg_store=None`); everything from
   `bg_store` through `sandbox_policy` / `subagent_emitter` carries a default. A *required*
   (no-default) parameter placed near `sandbox_policy` would be a non-default argument following
   defaulted ones → `SyntaxError`. So insert `working_directory: Path` **after
   `llm_config_getter` and before `bg_store=None`** — the last no-default slot. The call site
   (`manager.py`) passes it by keyword, so positional order at the call is unaffected. The module has
   `from __future__ import annotations` (`_wrapper.py:18`) and **no** `from pathlib import Path`
   today (`_wrapper.py:20-29` imports only `threading` / `time` / `uuid` / typing), so the
   annotation is fine at def time — but a test resolves it eagerly (see Test impact). **Add
   `from pathlib import Path` to the top of `_wrapper.py`** as part of this edit.
2. **`_wrapper.py::_run_sync`** — pass `working_directory=self._working_directory` into the
   `Agentao(...)` call at line 361.
3. **`agentao/agents/manager.py::create_agent_tools`** (line 172) — pass
   `working_directory=self.project_root` into `AgentToolWrapper(...)`. **No new parameter** on
   `create_agent_tools`, and `tooling/agent_tools.py` is untouched.

Items 1–2 edit `_wrapper.py`; item 3 edits `manager.py` → **two production files total.**
Leaving `create_agent_tools`'s signature unchanged also keeps `tests/test_async_tool.py:263`
(`get_type_hints` on `create_agent_tools`) passing without edits.

## Test impact (must-fix, not optional)

Adding a **required** `working_directory` to `AgentToolWrapper.__init__` breaks two tests that
construct the wrapper directly with no working dir:

- `tests/test_agent_subsystems_optional.py:96`
- `tests/test_agent_subsystems_optional.py:115`

Both already take a `tmp_path` fixture — pass `working_directory=tmp_path`.

`tests/test_async_tool.py:270` runs `get_type_hints(AgentToolWrapper.__init__,
globalns=vars(wrapper_mod))`. Because of `from __future__ import annotations`, the
`working_directory: Path` annotation is stored as the *string* `"Path"` and only resolved when
`get_type_hints` forces it — against the wrapper module's globals. `Path` is **not** in
`_wrapper.py`'s globals today, so without the new `from pathlib import Path` import (Approach
step 1) this test raises `NameError`. The import is therefore mandatory, not cosmetic. (The
assertion itself only checks the `all_tools` hint, so no test edit is needed once `Path`
resolves.)

## Verification

Add one focused regression test that targets the construction kwarg, *not* a sub-agent handle:
the constructed sub-agent is a local inside `_run_sync` and never returned, and calling
`.execute()` for real would run a full LLM turn (network).

**Order matters — build the real parent *before* patching.** The patch target
(`agentao.agent.Agentao`) is the same symbol the parent is built from, so patching first turns
the parent into the recorder too. The sequence:

1. **Build the real parent first.** `Agentao(working_directory=tmp_path,
   enable_builtin_agents=True)`. The constructor registers sub-agent tools (`agent.py:545`); do
   **not** call `register_agent_tools` by hand. `enable_builtin_agents` defaults to `False`
   (`agent.py:94`), so without it the manager loads **zero** definitions and there is no
   sub-agent tool to invoke (alternatively, drop a definition in `tmp_path/.agentao/agents/*.md`).
2. **Fetch the sub-agent tool** off `agent.tools` and keep a handle to it.
3. **Now patch `agentao.agent.Agentao`** with a recorder that captures construction kwargs and
   raises a sentinel (so the turn never runs). `Agentao` is imported *locally* inside
   `_run_sync` (`from ...agent import Agentao`, line 309), so the local rebind picks up the
   patch on the *next* call — patch `agentao.agent.Agentao`, **not**
   `agentao.agents.tools._wrapper.Agentao`, which doesn't exist at module scope.
4. **Call the tool's `.execute(task="x")`** and assert the recorder captured
   `working_directory == tmp_path` (and that no `TypeError` is raised).

Run: `uv run python -m pytest tests/test_host_subagent_events.py tests/test_async_tool.py
tests/test_agent_subsystems_optional.py` plus the new test.
