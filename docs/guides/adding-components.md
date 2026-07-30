# Adding New Components

How to add a new **tool** or a new **skill** to the Agentao codebase.

This is about extending Agentao itself. If you are an embedding host injecting tools at
construction time instead, see [../design/host-tool-injection.md](../design/host-tool-injection.md).

## A tool

1. Create `agentao/tools/<module>.py` and implement `Tool` (or `AsyncToolBase` for async).
   Both live in `agentao/tools/base.py`; `RegistrableTool = Tool | AsyncToolBase`.
   `web_fetch` (`agentao/tools/web.py`) is the built-in `AsyncToolBase` to copy from.
2. Set `requires_confirmation=True` for anything dangerous: arbitrary shell, network
   requests, file writes, deletions. This is what routes the tool through
   `PermissionEngine` — see [tool-confirmation.md](tool-confirmation.md).
3. Register in `agentao/tooling/registry.py::register_builtin_tools()`.

Note that `agent.py::_register_tools()` is a thin delegation — the real wiring lives in
`register_builtin_tools()`, so registering in `agent.py` is the wrong place.

### If the tool is async

`async_execute` runs **on the host's event loop** (bridged there by
`runtime/tool_executor.py::_run_async_tool`), which means two rules:

- **Never block it.** Any CPU-bound or blocking-syscall stretch belongs on a
  thread. `web_fetch` measures this: with a 2.2MB DOM parsed inline, a 10ms
  heartbeat task on the loop ticks **0** times.
- **Own the thread, don't orphan it.** Cancelling a thread hand-off cancels only
  the awaiter — the worker runs to completion regardless. Cancel it if it has not
  started, otherwise wait for it, and keep that wait under
  `tool_executor._ASYNC_CANCEL_ACK_TIMEOUT_S`: past that the dispatcher reports
  `TOOL_COMPLETE` and moves on, so a longer cleanup detaches exactly the work it
  was trying to account for. `web.py::_in_worker` is that pattern.

Use your own bounded `ThreadPoolExecutor`, not `asyncio.to_thread` — the loop's
default executor is shared with code you don't control (`loop.getaddrinfo`, and
so every `httpx.AsyncClient` hostname connect), and a bounded pool of your own
also caps how much of that work can pile up.

## A skill

1. Create `skills/<my-skill>/SKILL.md` with YAML frontmatter (`name:`, `description:` —
   the trigger text the model sees).
2. Optionally add `references/*.md` files. These load only on activation, which keeps the
   always-resident cost to just the name and description.
3. Restart the agent or run `/skills reload`.

For discovery, activation, and the full frontmatter schema, see [skills.md](skills.md).
