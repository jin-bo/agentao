# 1.3 Integration Modes

> **What you'll learn**
> - The two stable embedding paths and their trade-offs
> - A decision tree to pick one in 30 seconds
> - When (and how) to compose both in one process

Agentao offers **two stable embedding paths**. Pick based on your host language, isolation needs, and distribution model.

## Mode A · Python In-Process SDK

```
┌─────────────────────────────────────┐
│  Your Python process                 │
│                                     │
│   ┌─────────────┐     import        │
│   │ Your code   │◄─────────────────┤
│   └──────┬──────┘                   │
│          │                          │
│          ▼                          │
│   ┌─────────────┐                   │
│   │  Agentao()  │   Same proc, heap │
│   └─────────────┘                   │
└─────────────────────────────────────┘
```

**How it works**: `from agentao import Agentao` gives you the runtime; you drive it via method calls.

**Strengths**
- ✅ Zero protocol overhead — it's just function calls
- ✅ Events are native Python objects
- ✅ Easiest debugging — set a breakpoint inside the agent loop

**Trade-offs**
- ⚠️ Shares memory, crashes, and dependencies with your process
- ⚠️ Python-only
- ⚠️ Dep-lock: you must be compatible with Agentao's `openai` / `mcp` / `httpx` versions

## Mode B · ACP Protocol (Cross-Language / Cross-Process)

```
┌─────────────────────────┐       stdio NDJSON         ┌────────────────────────┐
│  Your host (any lang)   │   JSON-RPC 2.0, full-duplex│  Agentao subprocess    │
│                         │◄──────────────────────────►│  agentao --acp --stdio │
│  Node / Go / Rust / …   │                           │                        │
└─────────────────────────┘                           └────────────────────────┘
```

**How it works**: Agentao runs as a subprocess and speaks NDJSON (one JSON per line) JSON-RPC 2.0 over stdio. The host sends `initialize` / `session/new` / `session/prompt` and receives `session/update` / `session/request_permission` notifications.

**Strengths**
- ✅ Language-agnostic: Node / Go / Rust / Java / anything that can spawn a subprocess
- ✅ Process isolation: a crash doesn't kill the host; can sandbox or hot-upgrade
- ✅ Standard protocol — interoperates with Zed, Claude Code, and other ACP clients

**Trade-offs**
- ⚠️ Serialization + stdio round-trips
- ⚠️ Slightly harder to debug (read stdio traces)
- ⚠️ Tool confirmation needs an explicit UI bridge

## Comparison matrix

| Dimension | Python SDK | ACP |
|-----------|-----------|-----|
| Host language | Python only | Any |
| Process isolation | None (same proc) | Yes (subprocess) |
| Latency | ~0 ms (function call) | 1–5 ms (stdio + JSON) |
| Crash blast radius | Host dies too | Subprocess restart |
| Dependency conflicts | Possible | None (isolated env) |
| Debuggability | High | Medium |
| Tool confirm UI | Direct callback | Via `session/request_permission` |
| Streaming events | Python objects | JSON notifications |
| Protocol openness | Internal | Public ACP spec |
| Best for | SaaS backends, batch jobs, Python data services | IDE plugins, non-Python products, multi-tenant isolation |

## Decision tree

```
Is your host written in Python?
 ├─ Yes ─┬─ Need process isolation / multi-tenant safety?
 │       │   ├─ Yes → ACP
 │       │   └─ No  → Python SDK (recommended)
 │       └─ Frequent load/unload of agents (e.g. serverless)?
 │           ├─ Yes → ACP (cold-start cost acceptable)
 │           └─ No  → Python SDK
 └─ No (Node/Go/IDE/...) → ACP (only option)
```

## Hybrid mode

The two modes **compose**. A common pattern:

- Your Python backend embeds Agentao via SDK for the main flow
- Inside that flow, you use `ACPManager` to call **another** ACP server (e.g. a dedicated code reviewer) as a subprocess

This way long-lived state stays in the SDK side while one-off heavy work is delegated to isolated ACP subprocesses.

### Headless runtime use

When the embedder drives `ACPManager` unattended — CI workers, batch jobs, queue consumers — treat it as a **headless runtime**.

The key clarification is that **Headless Runtime is not a third integration mode and not a separate protocol**. It is still the ACP path above; the host is simply using `ACPManager` as an unattended, pollable runtime with no human in the loop.

Practical mental model:

- **Transport is unchanged**: ACP subprocess + stdio + JSON-RPC
- **Host object is unchanged**: still `ACPManager`
- **Only the operating assumption changes**: no one clicks approvals, no one answers questions, and the host owns polling, admission control, and retries

In practice, keep four rules in mind:

- Submit turns only through `prompt_once()` or `send_prompt()`
- Do not depend on `send_prompt_nonblocking*`; they are internal / unstable
- One server allows only one active turn at a time; collisions become `SERVER_BUSY`
- Read `get_status()` / `readiness()` first for gating, then `last_error` for diagnosis

The contract (public entry points, typed `get_status()`, single-active-turn concurrency, error classification) is pinned in [`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md); the runnable sample is [`examples/headless_worker.py`](../../../examples/headless_worker.py).

## How the rest of the guide is organized

- **Part 2** — Python SDK deep-dive
- **Part 3** — ACP (both Agentao-as-server and Agentao-as-client)
- **Parts 4 – 8** — Cross-cutting content for both modes

## TL;DR

- **Python host? → SDK** unless you need process isolation or multi-tenant safety.
- **Non-Python host? → ACP** (only option). Same for IDE plugins.
- **Both can compose** — your Python backend uses SDK for the main flow and `ACPManager` to call other ACP servers.
- **Headless runtime is not a third mode** — it's `ACPManager` driving an ACP server with no human in the loop.

Next: [1.4 Hello Agentao in 5 min →](./4-hello-agentao)
