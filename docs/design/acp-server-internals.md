# ACP server internals

**Status:** internal note for contributors. Public protocol behaviour is in the developer guide: [Part 3.2 · Agentao as an ACP Server](../../developer-guide/en/part-3/2-agentao-as-server.md) and [Appendix C · ACP message fields](../../developer-guide/en/appendix/c-acp-messages.md). Chinese twin: [acp-server-internals.zh.md](acp-server-internals.zh.md).

This note covers how `agentao/acp/` is built: the process layout, the stdout guard, the concurrent dispatcher and its shutdown order, the file layout, the tests and how to add a method handler. The module docstrings are the detailed reference. Read them before you change a rule here.

## Process layout

```
                   ┌────────────────────────────────────────────────┐
                   │                AcpServer (server.py)           │
       stdin ───►  │   read loop ── classify request / response     │
                   │        │                       │               │
                   │        ▼ submit                ▼ route         │
                   │   ThreadPoolExecutor(8)   _pending_requests    │
                   │   handlers: initialize,   (server → client     │
                   │   session/new, /prompt,    requests waiting    │
                   │   /cancel, /load, …        for an answer)      │
                   │        │                                       │
                   │        ▼ write under write lock                │
       stdout ◄────┤   JSON-RPC responses + notifications           │
                   │                                                │
                   │   sessions: AcpSessionManager                  │
                   │     └─ AcpSessionState ─ Agentao runtime (×N)  │
       stderr ◄────┤   sys.stdout → stderr, logger on stderr        │
                   └────────────────────────────────────────────────┘
```

`agentao --acp` goes through `agentao/cli/_light.py::run_acp_mode` (no `[cli]` extras needed) to `agentao/acp/__main__.py::main`. `main()` forces UTF-8 stdio, builds the server, registers every handler and runs the read loop. `--resume` becomes a `ResumeDirective` (`models.py`) on the server. The first `session/new` consumes it through `session_load.resume_session_on_new`.

## Stdout hygiene

When `AcpServer()` is built with no `stdin` / `stdout` argument (the production path), it:

1. Keeps the real `sys.stdout` as its private output handle. All JSON-RPC writes go through it, under one lock.
2. Sets `sys.stdout = sys.stderr`, so a stray `print()` anywhere in the process lands on stderr.
3. Adds a stderr `StreamHandler` to the `agentao` logger if it has no handler yet, so INFO logs are visible before `LLMClient` sets up logging.

Tests pass explicit streams and get none of this, so they do not mutate global state. `tests/test_acp_cli_entrypoint.py` checks that stdout carries only ACP messages.

## Concurrent dispatch

Handlers run on a `ThreadPoolExecutor(max_workers=8)` (`thread_name_prefix="acp-handler"`). A synchronous dispatcher would deadlock: `confirm_tool` blocks on a `session/request_permission` answer, and that answer arrives on the read loop. The read thread only parses, submits requests and routes responses to `_pending_requests`.

Two consequences:

- Responses to concurrent requests can leave in any order. Clients match by id.
- `AcpServer.call()` waits with **no timeout**. Only an answer or `_cancel_all_pending_requests` (connection close) ends the wait. `session/cancel` does not touch pending server → client requests.

### Shutdown order

`AcpServer.run()`'s `finally` block runs four steps. The order is load-bearing:

1. **Cancel pending server → client requests.** A worker inside `confirm_tool` wakes with `PendingRequestCancelled` and rejects the tool.
2. **Trip every active turn's cancel token** (`AcpSessionManager.cancel_all_active_turns`). A worker inside a model call or a tool has no other stop signal. This step does not disconnect MCP, because the worker may still use it.
3. **Drain the executor** with `shutdown(wait=True)`. Steps 1 and 2 give every worker a stop signal, but the signal is cooperative and the drain has no timeout: a worker inside a blocking call that does not check its token (a sync tool's `execute()`, for example) still delays shutdown until that call returns. `run()` returning is then a happens-before barrier for all handler writes.
4. **Close all sessions** (`AcpSessionManager.close_all`). MCP disconnects last, after every worker has unwound.

Draining before step 1 or 2 hangs the process. `tests/test_acp_request_permission.py` covers the order.

## File layout

```
agentao/acp/
├── __main__.py                 # main(): UTF-8 stdio, register handlers, run
├── protocol.py                 # METHOD_*, ACP_PROTOCOL_VERSION, error codes
├── server.py                   # AcpServer, JsonRpcHandlerError, dispatch, call()
├── session_manager.py          # AcpSessionManager: thread-safe registry, close_all
├── models.py                   # AcpSessionState, AcpConnectionState, ResumeDirective
├── schema.py, schema_export.py # pydantic wire models, JSON Schema export
├── _handler_utils.py           # shared session/* guards, hold_idle_turn_lock
├── initialize.py               # initialize + AGENT_CAPABILITIES
├── llm_auth.py                 # Terminal Auth method, per-session LLM resolution
├── session_new.py              # session/new, agent_factory seam, cwd/mcpServers parsing
├── session_prompt.py           # session/prompt, ContentBlock parser, stop reasons
├── session_cancel.py           # session/cancel (idempotent)
├── session_load.py             # session/load, startup resume, history hydration
├── session_set_config_option.py, session_set_mode.py,
│   agentao_set_model.py, session_set_model.py, session_list_models.py
├── mcp_translate.py            # ACP mcpServers → internal MCP config
├── transport.py                # ACPTransport.emit(): event → session/update
├── _transport_interaction.py   # confirm_tool, ask_user, permission options
├── _transport_replay.py        # replay_history for session/load
├── _transport_helpers.py       # tool kind map, diffs, plan mapping, notices
├── _tool_call_content.py       # streamed-output buffer (FLUSH_CHARS, MAX_CHARS)
└── _lifecycle.py               # SessionStart / SessionEnd hooks for ACP
```

## Tests

All ACP server tests are `tests/test_acp_*.py`. The `test_acp_client_*.py` files test the ACP **client** (`agentao/acp_client/`), not this server.

| Area | Files |
|------|-------|
| Framing, error codes, schema | `test_acp_protocol.py`, `test_acp_schema.py` |
| Handshake, Terminal Auth | `test_acp_initialize.py`, `test_acp_terminal_auth.py` |
| Session registry, multi-session isolation | `test_acp_session_manager.py`, `test_acp_multi_session.py` |
| `session/new`, MCP injection | `test_acp_session_new.py`, `test_acp_mcp_injection.py` |
| `session/prompt`, stop reasons | `test_acp_session_prompt.py`, `test_acp_stop_reason.py` |
| Event mapping, tool content | `test_acp_transport.py`, `test_acp_tool_kind.py`, `test_acp_tool_call_diff.py`, `test_acp_tool_call_content.py` |
| Permissions, `ask_user` | `test_acp_request_permission.py`, `test_acp_permission_order.py`, `test_acp_ask_user.py` |
| Cancel | `test_acp_session_cancel.py` |
| Load, resume, skills | `test_acp_session_load.py`, `test_acp_session_load_skills.py`, `test_acp_resume_on_startup.py` |
| Model / mode switches | `test_acp_set_config_option.py`, `test_acp_session_set_model.py` |
| Lifecycle hooks | `test_acp_session_lifecycle_hooks.py` |
| CLI entry, stdout hygiene | `test_acp_cli_entrypoint.py` |

```bash
uv run python -m pytest tests/ -k test_acp_
```

## Adding a method handler

Follow the pattern of the existing `session_*.py` modules:

1. Add the method name to `protocol.py` as a `METHOD_*` constant.
2. Write `handle_<method>(server, params, *, <deps>) -> dict`. Use `_handler_utils.require_active_session` for the usual `session/*` guards, and `hold_idle_turn_lock` if the method must not run during a turn.
3. Validate params. Raise `TypeError` for a shape error (→ `-32602`). Raise `JsonRpcHandlerError(code, message)` for anything else. Any other exception becomes `-32603`.
4. Add `register(server, ...)`, which calls `server.register(METHOD_X, lambda params: handle_<method>(server, params, ...))`.
5. Call `register(server)` in `__main__.py::main()`.
6. Test the handler directly and over the wire. Inject a fake agent through the `agent_factory` keyword that `session_new` exposes, so the test does not need the LLM stack.
7. Document the method in developer guide 3.2 and Appendix C, in both languages.
