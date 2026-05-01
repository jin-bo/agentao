# Embedded Harness Addendum: AsyncTool

**Date:** 2026-04-29
**Status:** Current-state addendum. The only outstanding P0 decision documented here is `AsyncTool`; everything else listed under "Already in master" is shipped and not re-opened.
**Related docs:** `docs/implementation/EMBEDDED_HARNESS_IMPLEMENTATION_PLAN.md`,
`docs/implementation/EMBEDDED_HARNESS_CONTRACT_IMPLEMENTATION_PLAN.md`,
`docs/design/embedded-host-contract.md`
**Companion:** `EMBEDDED_HARNESS_PROTOCOL_PLAN.zh.md`

---

## Already in master (verified, not re-opened by this addendum)

- `Agentao.arun()` async public surface — `agentao/agent.py:556`. Bridges to sync `chat()` via `loop.run_in_executor(None, self.chat, ...)` (`agentao/agent.py:580`).
- `Agentao.__init__(*, working_directory: Path)` is required, keyword-only — `agentao/agent.py:74`. Docstring states "required since 0.3.0; was a deprecated optional in 0.2.16."
- Factory: `agentao/embedding/factory.py::build_from_environment` owns env / dotenv / cwd / home discovery and forwards explicit subsystems to `Agentao(...)`.
- `MemoryStore` Protocol — `agentao/capabilities/memory.py:33`, re-exported through `agentao/capabilities/__init__.py`. `MemoryManager.__init__` accepts a `MemoryStore` instance directly (`agentao/memory/manager.py:57`).
- `FileSystem` / `ShellExecutor` capabilities and lazy `LocalFileSystem` / `LocalShellExecutor` defaults in `Tool` — `agentao/tools/base.py:32-44`, `agentao/capabilities/{filesystem,shell}.py`.
- `BackgroundTaskStore`, `SandboxPolicy`, `ReplayConfig`, `MCPRegistry` are all explicit-injection kwargs on `Agentao.__init__` (`agentao/agent.py:83-91`); the factory wires CLI defaults from `<wd>/.agentao/*`.

This addendum does not re-define these and does not add new "PR 3b / PR 5b" tightening items. Any residual non-factory fallbacks (e.g. `LLMClient` log-file resolution at `agentao/llm/client.py:208,216`) are out of scope here and tracked, if at all, in their own issues.

---

## Non-Goals

These remain out of scope and do not block AsyncTool:

- `Run`, `RunStatus`, `Run.events()`, multi-subscriber fan-out, event backpressure.
- `StructuredEventSink` separate from the existing sync `Transport.emit`.
- `AsyncTransport`.
- `LLMCapability`, streaming `LLMDelta`, provider-normalized reasoning deltas.
- `ToolGovernanceResult` beyond the existing confirmation flow.
- `MetacognitiveBoundary` runtime protocol — design captured in `docs/design/metacognitive-boundary.md`; implementation deferred.
- A public `AgentaoContext` / run-context object exposed to tools — deferred until the first concrete `AsyncTool` consumer needs run-local metadata. AsyncTool ships without a `ctx` parameter.
- Migrating `McpTool` (currently `McpTool(Tool)`, sync→async bridge through `McpClientManager`) to AsyncTool. Natural follow-up once at least one in-tree `AsyncTool` consumer exists.
- Converting `agentao.tools.base.Tool` itself into a structural `Protocol`. It remains a base class.
- Re-opening the `MemoryStore` Protocol contract.

---

## AsyncTool

The only remaining P0-relevant protocol decision: **how an async-executing tool fits the existing tool runtime without breaking the registry, the planner, or host-loop-affine resources.**

### Why a concrete base class, not a `Protocol` of the metadata-only surface

The current runtime does not consume tools through `name/description/parameters/execute` only. It also reads:

- `tool.to_openai_format()` — `ToolRegistry.to_openai_format(plan_mode=...)` iterates and calls each tool's method (`agentao/tools/base.py:182`).
- `tool.is_read_only` — `ToolCallPlanner._decide` consults it for `readonly_mode` (`agentao/runtime/tool_planning.py:225`); `ToolExecutor` echoes it in deny messages (`agentao/runtime/tool_executor.py:152`).
- `tool.working_directory`, `tool.output_callback`, `tool.filesystem`, `tool.shell` — set/read by `Agentao` at registration time and inside execution (`agentao/tools/base.py:17-44`).

A Protocol that only declared `name / description / parameters / requires_confirmation / async_execute` would let an `AsyncTool` fixture register into `ToolRegistry` and crash on the first schema export or readonly_mode pass — before `async_execute` is ever called.

P0 therefore ships AsyncToolBase as a true drop-in sibling. To avoid cargo-culting `Tool`'s surface (and silently drifting from it), we extract a shared private base `_BaseTool` carrying every non-execute concern. `Tool` and `AsyncToolBase` both inherit it; the two leaf classes differ only in `execute` vs `async_execute`.

### Shape

`agentao/tools/base.py` — extract the shared base:

```python
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..capabilities import FileSystem, ShellExecutor


class _BaseTool(ABC):
    """Internal: every non-execute concern shared by sync and async tools.

    Carries slots, capability accessors, path-resolution helpers, the
    full metadata surface, and the OpenAI schema serializer. Not a
    public class — callers register :class:`Tool` or
    :class:`AsyncToolBase` instances; ``RegistrableTool`` (below) is
    the public union type.
    """

    def __init__(self) -> None:
        self.output_callback: Optional[Callable[[str], None]] = None
        self.working_directory: Optional[Path] = None
        self.filesystem: Optional["FileSystem"] = None
        self.shell: Optional["ShellExecutor"] = None

    # --- capability accessors (unchanged from current Tool) ----------
    def _get_fs(self) -> "FileSystem":
        if self.filesystem is None:
            from ..capabilities import LocalFileSystem
            self.filesystem = LocalFileSystem()
        return self.filesystem

    def _get_shell(self) -> "ShellExecutor":
        if self.shell is None:
            from ..capabilities import LocalShellExecutor
            self.shell = LocalShellExecutor()
        return self.shell

    # --- path policy (unchanged from current Tool) -------------------
    def _resolve_path(self, raw: str) -> Path: ...
    def _resolve_directory(self, raw: str) -> Path: ...

    # --- metadata ----------------------------------------------------
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]: ...

    @property
    def requires_confirmation(self) -> bool:
        return False

    @property
    def is_read_only(self) -> bool:
        return False

    def to_openai_format(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Tool(_BaseTool):
    """Sync tool. Existing public class; subclasses unchanged."""

    @abstractmethod
    def execute(self, **kwargs: Any) -> str: ...


class AsyncToolBase(_BaseTool):
    """Async sibling of :class:`Tool`. Same surface, ``async_execute``."""

    @abstractmethod
    async def async_execute(self, **kwargs: Any) -> str: ...
```

`Tool`'s public surface is preserved by-construction: every helper and accessor it exposed before (`_get_fs`, `_get_shell`, `_resolve_path`, `_resolve_directory`, the slots, the metadata properties, `to_openai_format`) now lives on `_BaseTool` and is inherited unchanged. **No existing `Tool` subclass needs to be edited.** This is the load-bearing reason for the extraction: it is the only way to give `AsyncToolBase` "exactly Tool's surface" without copy-paste drift.

Rules:

- Existing `Tool.execute(**kwargs)` remains valid and unchanged.
- `AsyncToolBase` is additive — no existing sync tool migrates in P0.
- An `AsyncToolBase` instance registers into the existing `ToolRegistry` directly. No adapter, no wrapper.
- Sync `Tool.execute(**kwargs)` **MUST NOT** receive run-context kwargs from any dispatcher (this is a hard invariant on the dispatcher side, not a soft preference). AsyncTool dispatch and sync Tool dispatch are two separate code paths.

### Type boundary

Allowing `AsyncToolBase` into the registry requires updating annotations along the registration / planning / execution path so type checkers and readers see the actual contract. Concretely a new alias:

```python
# agentao/tools/base.py
RegistrableTool = Tool | AsyncToolBase
```

and propagate:

- `ToolRegistry.tools: Dict[str, RegistrableTool]`,
- `ToolRegistry.register(tool: RegistrableTool) -> None`,
- `ToolRegistry.get(...) -> RegistrableTool`,
- `ToolRegistry.list_tools() -> List[RegistrableTool]` (`agentao/tools/base.py:174`),
- `ToolCallPlan.tool: RegistrableTool` (`agentao/runtime/tool_planning.py:75`),
- sub-agent surfaces that flow through the same registry: `AgentManager.create_agent_tools(..., all_tools: Dict[str, RegistrableTool], ...)` (`agentao/agents/manager.py:144`) and `AgentToolWrapper.__init__(..., all_tools: Dict[str, RegistrableTool], ...)` (`agentao/agents/tools.py:235`; class defined at `:221`),
- any executor / formatter signature that currently reads `plan.tool: Tool`.

Without this, the duck-typed dispatch works at runtime but every annotation lies, and IDE/`mypy` flag every `tool.async_execute` access on AsyncTool subclasses. The alias keeps the union opt-in: code that genuinely only deals with sync tools (e.g. inside `Tool.execute`-only helpers) keeps the narrower `Tool` annotation.

### Dispatch — host-loop-aware bridge

The naive "per-call `asyncio.run()` on the chat thread" is **not** suitable as the only path. `arun()` already isolates the host's main loop from the chat thread by running `chat()` inside `loop.run_in_executor(None, ...)` (`agentao/agent.py:580`). A fresh loop created on the chat thread cannot touch resources bound to the host loop — e.g. an `aiohttp.ClientSession`, an async DB client, an `anyio` task group, anything created on the host loop. Async tools that hold such resources are common, not exotic. Deferring this would be a foreseeable trap.

P0 therefore captures the host loop at `arun()` entry and threads it down to the dispatcher:

1. `Agentao.arun(...)`: capture `host_loop = asyncio.get_running_loop()` before scheduling `run_in_executor`. Make `host_loop` available to the chat runtime — concretely either as a new field on the `CancellationToken` already threaded into `chat()`, or as a dedicated `runtime_loop` kwarg threaded through `ChatLoopRunner` → `ToolRunner` → `ToolExecutor`. Either way, only the dispatcher needs to read it.

2. `ToolExecutor`, when dispatching an `AsyncToolBase` instance:

   ```python
   # ToolRunner.execute and ToolExecutor.execute_batch both accept
   # ``cancellation_token=None`` (see agentao/runtime/tool_runner.py:108,
   # agentao/runtime/tool_executor.py:68). The dispatcher must therefore
   # tolerate a None token without an AttributeError. Two equivalent
   # patterns; pick one in the implementation PR:
   #
   #   (a) normalize at entry:  token = token or CancellationToken()
   #   (b) gate the callback:   remove = (token.add_done_callback(...)
   #                                      if token is not None
   #                                      else (lambda: None))
   #
   # The snippet below uses (b) so callers that explicitly pass None
   # don't allocate a token they never use.

   if runtime_loop is not None and runtime_loop.is_running():
       # Async path: arun() captured a host loop. Run the coroutine
       # ON the host loop so loop-affine resources keep working;
       # the chat-thread blocks on the future for the result.
       fut = asyncio.run_coroutine_threadsafe(
           tool.async_execute(**args), runtime_loop
       )
       remove = (
           token.add_done_callback(lambda: fut.cancel())
           if token is not None
           else (lambda: None)
       )
       try:
           result = fut.result()  # blocks; fut.cancel() unblocks via CancelledError
       finally:
           remove()
   else:
       # Sync path: no host loop captured. Supports only
       # loop-independent async tools (see "Sync path scope" below).
       result = asyncio.run(tool.async_execute(**args))
   ```

3. `ToolRunner`'s public surface stays sync. The `asyncio` bridge logic lives only in `ToolExecutor` (or a small helper called from it). Sync `Tool` execution is untouched.

#### Cancellation status mapping

When `fut.cancel()` is invoked from the token callback, `fut.result()` raises `concurrent.futures.CancelledError` — that is the exception class used by `concurrent.futures.Future.result()`, and it inherits from `concurrent.futures._base.Error` → `Exception` (unchanged across 3.8+). It is **not** the same class as `asyncio.CancelledError`; since Python 3.8 those have been distinct (`asyncio.CancelledError` was moved to inherit from `BaseException` and lives in `asyncio.exceptions`, while `concurrent.futures.CancelledError` continues to inherit from `Exception`). Implementers must catch `concurrent.futures.CancelledError` explicitly — catching `asyncio.CancelledError` would not match the bridge's failure mode.

The current `ToolExecutor` execution body wraps the tool call in `except Exception as exc: ... status = "error"` (`agentao/runtime/tool_executor.py:243-251`). Because `concurrent.futures.CancelledError` *is* an `Exception`, leaving it to fall through that handler would silently misclassify token-driven cancels as `status="error"`. The async dispatch helper must therefore catch it **before** that handler and route through the **existing** cancelled path — `ToolExecutor` already produces `ToolExecutionResult(..., status="cancelled", ...)` and emits `TOOL_COMPLETE` with `status="cancelled"` for permission-deny / readonly-mode cancellations (`agentao/runtime/tool_executor.py:164,175,192`). AsyncTool cancellation reuses that contract.

#### Where the cancel branch lives in `_execute_one` — and why it needs an explicit ack

The branch is **not** a "returns a string" helper that nests inside the existing `try / except Exception / status / _emit_complete / return` tail of `_execute_one`. Doing so would either double-emit `TOOL_COMPLETE` or land the cancel as `status="error"`. The dispatcher's cancel branch must short-circuit the entire success/error tail: emit `TOOL_COMPLETE` with `status="cancelled"` exactly once, and return the `(call_id, ToolExecutionResult)` tuple `_execute_one` itself returns.

There is also a subtler timing issue. `concurrent.futures.Future.cancel()` flips the future to `CANCELLED` synchronously and `fut.result()` then raises `concurrent.futures.CancelledError` on the worker thread. But the underlying asyncio task on the host loop has only **started** processing the cancellation at that point — its `try / finally` cleanup, including any `aiohttp.ClientSession.close()`, async DB rollback, or `anyio.CancelScope.__aexit__`, runs asynchronously *after* the dispatcher already saw `CancelledError`. Returning `status="cancelled"` immediately is therefore racy: the acceptance assertions "coroutine received `CancelledError`" and "no future left dangling on the host loop" would pass only when the test happens to drain the loop in time.

The dispatcher therefore wraps the call in a thin coroutine whose own `finally` signals a `threading.Event` once the user coroutine has actually finished cleanup. The cancel branch waits on that ack with a bounded timeout before emitting `TOOL_COMPLETE`:

```python
import concurrent.futures
import threading

# Inside _execute_one, the AsyncTool dispatch path:

ack = threading.Event()

async def _bridged():
    try:
        return await tool.async_execute(**args)
    finally:
        # Runs after the user coroutine's own try / finally cleanup
        # on the host loop. Signals the worker thread that it is safe
        # to surface the cancel to TOOL_COMPLETE.
        ack.set()

fut = asyncio.run_coroutine_threadsafe(_bridged(), runtime_loop)
remove = (
    token.add_done_callback(lambda: fut.cancel())
    if token is not None
    else (lambda: None)
)

# Bounded ack timeout. Long enough for typical async cleanup
# (closing client sessions, rolling back transactions); short
# enough that a stalled host loop cannot hang the worker
# indefinitely. 5s is a starting point — tune in implementation.
_ASYNC_CANCEL_ACK_TIMEOUT_S = 5.0

try:
    try:
        result_text = fut.result()
        # Success: _bridged()'s finally has already run, so ack is set.
    except concurrent.futures.CancelledError:
        # Token-driven cancel. Wait for _bridged()'s finally to run on
        # the host loop so the user coroutine's cleanup has completed
        # before we report TOOL_COMPLETE. Proceed (with a logged
        # warning) if the loop is wedged past the timeout, so a stuck
        # host cannot hang the worker indefinitely.
        if not ack.wait(timeout=_ASYNC_CANCEL_ACK_TIMEOUT_S):
            self._logger.warning(
                "AsyncTool %s: cancel ack timeout after %.1fs; emitting "
                "TOOL_COMPLETE without confirmed coroutine cleanup.",
                fn, _ASYNC_CANCEL_ACK_TIMEOUT_S,
            )
        duration_ms = round((time.monotonic() - t0) * 1000)
        # Match the existing executor convention of putting a short
        # human-readable reason in the error field (cf. ":162").
        # ``token.reason`` is already set by Agentao.arun() / chat
        # loop callers (e.g. "async-cancel", "user-cancel").
        reason = token.reason if token is not None and token.reason else "async-cancel"
        self._emit_complete(fn, call_id, "cancelled", duration_ms, reason)
        return call_id, ToolExecutionResult(
            fn_name=fn, result="Tool execution cancelled.",
            status="cancelled", duration_ms=duration_ms, error=reason,
        )
    # Success: fall through to the existing status / _emit_complete /
    # post-tool hook tail. Other exceptions (TypeError, RuntimeError,
    # etc.) propagate to the existing `except Exception` handler and
    # land as status="error" exactly as for sync tools.
finally:
    remove()
```

Three properties this gives us:

1. **No double-emit.** `TOOL_COMPLETE` is emitted exactly once per call: success goes through the existing tail, cancel goes through this branch, errors go through the existing `except Exception` handler.
2. **Cleanup-ordered cancel report.** By the time `TOOL_COMPLETE(status="cancelled")` is emitted, the user coroutine's own `finally` has run on the host loop (or the bounded timeout elapsed with a logged warning). This is what makes the "no dangling future" / "coroutine received `CancelledError`" assertions stable rather than flaky.
3. **Error-field convention preserved.** Existing executor cancelled paths put a short reason in the `error` field (`agentao/runtime/tool_executor.py:162` uses `"denied by permission engine"`); AsyncTool token cancels mirror that with `token.reason` (typically `"async-cancel"` from `Agentao.arun()` at `agentao/agent.py:585`, or `"user-cancel"` for synchronous CLI Ctrl+C), falling back to `"async-cancel"` if the token has no reason set.

The chat loop already treats `token.is_cancelled` as the trigger to unwind the turn (raising `AgentCancelledError` at the `chat()` boundary).

#### Cancellation — concrete mechanism

The current `CancellationToken` (`agentao/cancellation.py:15-51`) is a `threading.Event` wrapper exposing `is_cancelled` / `check()` / `cancel()` only. There is no callback / watcher API, and `arun()` only forwards `asyncio.CancelledError` into `token.cancel("async-cancel")` (`agentao/agent.py:585`). A dispatcher blocked in `fut.result()` therefore never observes the token flip on its own — the previous draft's "dispatcher calls `fut.cancel()`" sentence was unimplementable.

This addendum makes a small, contained change: extend `CancellationToken` with a callback registry.

```python
# agentao/cancellation.py
class CancellationToken:
    ...
    def add_done_callback(self, fn: Callable[[], None]) -> Callable[[], None]:
        """Register fn to run synchronously when ``cancel()`` is called.

        If the token is already cancelled, ``fn`` runs immediately on the
        calling thread before this method returns.

        Returns an unregister callable so callers can detach the callback
        once their critical section ends. Idempotent; calling the
        unregister callable twice is a no-op.
        """
```

Implementation note: callback list guarded by an internal `threading.Lock`; `cancel()` snapshots the list under the lock and invokes callbacks outside it to avoid re-entrancy. Callback exceptions are caught and logged so one misbehaving callback cannot block another.

With that primitive, the dispatcher snippet above is implementable as written. No polling. No new long-lived loop. The change is local to `cancellation.py` plus the dispatcher; no other call site is forced to adopt the API.

#### Sync path scope (narrowed)

The sync `chat()` fallback (`asyncio.run(tool.async_execute(...))`) supports **only loop-independent async tools** — async tools that create and tear down all their loop-bound resources within a single `async_execute` call (e.g. a self-contained `httpx.AsyncClient` opened and closed inside the coroutine).

It does **not** support tools that hold long-lived loop-affine resources (e.g. an `aiohttp.ClientSession` cached on the tool instance, an async DB pool created at registration time, an `anyio` task group spanning calls). A sync host that needs such tools must either:

- call `agent.arun(...)` from an event loop it controls (so `arun()` captures it as `runtime_loop`), or
- wait for explicit host-loop injection on the sync path (deferred — out of P0 scope; tracked under "Future Protocol Work").

`AsyncToolBase` subclasses that hold loop-affine state should document this constraint and (optionally) `assert asyncio.get_running_loop() is self._bound_loop` inside `async_execute` to fail loudly when invoked through the sync fallback.

This design is mandatory at P0, not a P1 follow-up. Without it, the first real async tool that holds loop-affine resources via `arun()` would break immediately.

### Acceptance (for this addendum only)

This addendum is satisfied when:

- An `AsyncToolBase` concrete subclass can be registered into `ToolRegistry` without an adapter, and survives `ToolRegistry.to_openai_format(plan_mode=...)` and `ToolCallPlanner._decide`'s `is_read_only` read. (Test: register, call those two paths, assert no AttributeError.)
- The type boundary is published: `RegistrableTool = Tool | AsyncToolBase` exists, and every surface listed in the "Type boundary" section uses it. The project does not currently configure `mypy` / `pyright` (verified in `pyproject.toml` dev deps), so verification is two lighter checks:
  - (a) `python -m compileall agentao/tools/base.py agentao/runtime/tool_planning.py agentao/runtime/tool_executor.py agentao/agents/manager.py agentao/agents/tools.py` succeeds.
  - (b) A unit test uses `typing.get_type_hints` to assert each of the following resolves to `RegistrableTool` (or `Dict[str, RegistrableTool]` / `List[RegistrableTool]` as appropriate):
    - `ToolRegistry.register["tool"]`,
    - `ToolRegistry.get["return"]`,
    - `ToolRegistry.tools` — note that the current code keeps `self.tools` only as an instance assignment (`agentao/tools/base.py:145`), so `get_type_hints(ToolRegistry)` cannot see it. The implementation PR must hoist the annotation to class body (`class ToolRegistry: tools: Dict[str, RegistrableTool]` plus the existing `__init__` assignment) so this check is reachable.
    - `ToolRegistry.list_tools["return"]`,
    - `ToolCallPlan` field `tool` (via `get_type_hints(ToolCallPlan)["tool"]`),
    - `AgentManager.create_agent_tools["all_tools"]`,
    - `AgentToolWrapper.__init__["all_tools"]`.

  Adding mypy is explicitly out of scope for this addendum; if a later PR introduces it, this acceptance step can be tightened.
- `_BaseTool` extraction preserves `Tool`'s surface byte-equivalent: every existing `Tool` subclass (file_ops, shell, web, search, memory, agents, skill, etc.) imports and runs without code changes. Verified by `pytest tests/` continuing to pass with no edits to existing tool modules.
- Public import surface published: `agentao/tools/__init__.py` re-exports `AsyncToolBase` and `RegistrableTool` alongside the existing `Tool` / `ToolRegistry` (`agentao/tools/__init__.py:3`), and `__all__` lists them. A smoke test asserts `from agentao.tools import AsyncToolBase, RegistrableTool` succeeds, that `RegistrableTool` is the alias defined in `agentao.tools.base`, and that an in-test `AsyncToolBase` subclass can be instantiated. (`_BaseTool` is intentionally *not* re-exported — it stays private.)
- `CancellationToken.add_done_callback(...)` exists and (a) runs the callback synchronously when `cancel()` is called from another thread, (b) runs immediately if the token is already cancelled, (c) returns an idempotent unregister callable. Covered by direct unit tests on `cancellation.py`.
- One end-to-end test via `await agent.arun(...)` exercises an `AsyncToolBase` whose `async_execute` asserts `asyncio.get_running_loop() is host_loop` (i.e. the coroutine actually ran on the host loop, not on a fresh chat-thread loop).
- One end-to-end test via sync `agent.chat(...)` exercises a **loop-independent** `AsyncToolBase` and verifies the fresh-loop fallback path completes successfully. The test docstring explicitly notes the sync-path scope limitation.
- A cancellation test along the `arun()` path: register an `AsyncToolBase` whose `async_execute` `await`s on an `asyncio.Event`, and whose `try/finally` records a `cleanup_ran = True` flag; cancel the host task (or call `token.cancel("async-cancel")` directly); assert (a) the coroutine receives `asyncio.CancelledError`, (b) the user coroutine's `finally` ran (`cleanup_ran is True`) **before** `TOOL_COMPLETE` is observed (this is what the dispatcher's ack mechanism guarantees), (c) the `run_coroutine_threadsafe` future is cancelled (dispatcher catches `concurrent.futures.CancelledError`), (d) the recorded `ToolExecutionResult.status` is `"cancelled"`, the emitted `TOOL_COMPLETE` event carries `status="cancelled"` (not `"error"`) and `error="async-cancel"` (matching `token.reason`), (e) `TOOL_COMPLETE` is emitted **exactly once** for the cancelled call (no double-emit from falling through the success tail), (f) the chat call unwinds, (g) no task is left pending on the host loop after the test drains it.
- An ack-timeout test: register an `AsyncToolBase` whose `async_execute` `finally` deliberately blocks indefinitely (or longer than the configured `_ASYNC_CANCEL_ACK_TIMEOUT_S`); cancel; assert the dispatcher logs the timeout warning, still emits `TOOL_COMPLETE(status="cancelled", error="async-cancel")` exactly once, and unwinds the chat call. This pins down the "stalled host loop must not hang the worker" property.
- No dispatcher passes `ctx` / run-context kwargs to sync `Tool.execute()`. Verified by a unit test that registers a sync `Tool` whose `execute()` raises on unknown kwargs.

The broader P0 acceptance criteria remain in `EMBEDDED_HARNESS_IMPLEMENTATION_PLAN.md`.

---

## Future Protocol Work

Driven by concrete consumers beyond the current CLI/ACP/examples surface, future P1/P2 work may introduce:

- a public `AgentaoContext` / run-context object once the first `AsyncToolBase` genuinely needs run-local metadata,
- migration of `McpTool` to `AsyncToolBase` (removes the `McpClientManager` sync bridge),
- a dedicated `AsyncToolRunner` if the chat-thread blocking model becomes a bottleneck,
- run lifecycle objects, structured event streams, async transport,
- host-injected LLM capabilities,
- richer governance results,
- metacognitive boundary injection (design captured in `docs/design/metacognitive-boundary.md`),
- additional public contracts layered on top of the existing `MemoryStore` Protocol.
