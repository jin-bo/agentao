# Part 2 · Python In-Process Embedding

The **shortest integration path** for Python hosts: `from agentao import Agentao` and drive the runtime with method calls — no protocol overhead.

::: info Key terms in this Part
Five vocabulary items you'll see throughout — bookmark [Appendix G](/en/appendix/g-glossary) for the full glossary.
- **Agentao instance** — one constructor call → one stateful session; `close()` is mandatory · [§2.3](/en/part-2/3-lifecycle), [G.1](/en/appendix/g-glossary#g-1-core-concepts)
- **Working directory (cwd)** — file-tool root, MCP cwd, `AGENTAO.md` lookup; frozen at construction · [§2.2](/en/part-2/2-constructor-reference), [G.1](/en/appendix/g-glossary#g-1-core-concepts)
- **Transport** — push-style callback (`emit(event)`) for streaming UI · [§2.7](/en/part-2/7-fastapi-flask-embed), [G.2](/en/appendix/g-glossary#g-2-extension-points)
- **CancellationToken** — host-side handle to abort an in-flight `chat()` · [§2.6](/en/part-2/6-cancellation-timeouts), [G.1](/en/appendix/g-glossary#g-1-core-concepts)
- **extra_mcp_servers** — per-session MCP injection (different tenants → different tokens) · [§2.2](/en/part-2/2-constructor-reference#tier-2-common-production-params-8-more)
:::

## Coverage

- [**2.1 Install & Import**](./1-install-import) — version pinning, extras, lazy loading
- [**2.2 Constructor Reference**](./2-constructor-reference) — every parameter, working-directory freeze, session-scoped MCP, production template
- [**2.3 Lifecycle**](./3-lifecycle) — `chat()` / `clear_history()` / `close()`, runtime model swap, concurrency patterns, full FastAPI example
- [**2.4 Session state**](./4-session-state) — the four state buckets, persist/restore recipe, memory auto-restore
- [**2.5 Runtime LLM switch**](./5-runtime-llm-switch) — `set_provider()` / `set_model()`, routing patterns, fallback chain
- [**2.6 Cancellation & timeouts**](./6-cancellation-timeouts) — `CancellationToken`, disconnect wiring, hard timeouts, `max_iterations`
- [**2.7 FastAPI / Flask embedding**](./7-fastapi-flask-embed) — production templates with SSE, session pool, auth, cancellation

## Before you start

Make sure you've read:

- [1.2 Core Concepts](/en/part-1/2-core-concepts) — Agent / Tool / Transport / Working Directory vocabulary
- [1.3 Integration Modes](/en/part-1/3-integration-modes) — confirm Python SDK is the right pick
- [1.4 Hello Agentao](/en/part-1/4-hello-agentao#example-a-python-sdk-20-lines) — 20-line runnable skeleton

## Mental model

> An agent is not a function call — it is a **stateful process component**.
> One `Agentao(...)` == one session.
> Many sessions → many instances.
> `close()` is mandatory, not polite.

→ [Start with 2.1 →](./1-install-import)
