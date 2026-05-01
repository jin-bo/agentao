# Recipes — common tasks, 1 click to the answer

> Each recipe is **a task you actually want to do** mapped to the canonical chapter(s) where the pattern lives. If your task isn't here, the [full table of contents](/en/) probably has it.

## I want to…

### …expose my business API as an agent tool

→ **[5.1 Custom Tools](/en/part-5/1-custom-tools)** — `Tool` subclass with name / description / parameters / execute; production template at the end. **TL;DR**: return a JSON string, set `requires_confirmation=True` for side effects, write the description as if for the LLM.

### …stream agent output to a browser

→ **[4.4 Streaming UI](/en/part-4/4-streaming-ui)** — SSE and WebSocket templates, thread / event-loop bridge with `loop.call_soon_threadsafe`, keep-alive frames. Pair with **[2.7 FastAPI / Flask](/en/part-2/7-fastapi-flask-embed)** for a copy-paste production endpoint.

### …add a "Stop" button that cancels mid-chat

→ **[2.6 Cancellation & Timeouts](/en/part-2/6-cancellation-timeouts)** — `CancellationToken`, wiring to client-disconnect events, hard wall-clock with `asyncio.wait_for`. `chat()` returns `"[Cancelled: <reason>]"` — don't catch an exception.

### …show a tool-confirmation modal in my web UI

→ **[4.5 Tool Confirmation UI](/en/part-4/5-tool-confirmation-ui)** — sync→async bridge with `asyncio.run_coroutine_threadsafe`, web modal pattern, "allow once" vs "always allow" UX. Pair with **[5.4 Permission Engine](/en/part-5/4-permissions)** so 90% of safe calls bypass the modal.

### …pool agents per `(tenant_id, session_id)`

→ **[2.3 Lifecycle](/en/part-2/3-lifecycle)** for the lock + thread pattern; **[6.7 Resource Governance](/en/part-6/7-resource-concurrency)** for TTL + LRU eviction; **[7.1 SaaS Assistant](/en/part-7/1-saas-assistant)** for the integrated FastAPI example.

### …persist a conversation across pod restarts

→ **[2.4 Session State & Persistence](/en/part-2/4-session-state)** — what's on the instance, what to serialize (`agent.messages`), how to restore via `add_message(role, content)` before the next `chat()`.

### …switch model at runtime (cheap vs. expensive routing)

→ **[2.5 Runtime LLM Switch](/en/part-2/5-runtime-llm-switch)** — `set_provider` / `set_model`; routing patterns for cheap-then-expensive, primary-with-fallback, A/B.

### …give each tenant their own credentials or MCP token

→ **[2.2 Constructor: extra_mcp_servers](/en/part-2/2-constructor-reference#tier-2-common-production-params-8-more)** for per-session MCP injection. **[6.4 Multi-Tenant & Filesystem](/en/part-6/4-multi-tenant-fs)** for the tenant isolation rules. **[7.1 SaaS Assistant](/en/part-7/1-saas-assistant)** ties them together.

### …block SSRF or lock down `web_fetch`

→ **[6.3 Network & SSRF Defense](/en/part-6/3-network-ssrf)** — default blocklist coverage, the `.github.com` (suffix) vs `github.com` (exact) rule, redirect-disabling pattern. **Don't disable the default blocklist** — extend it.

### …drive Agentao from Node / Go / Rust / IDE

→ **[Part 3 · ACP Protocol](/en/part-3/)** — start with [3.1 Quick Try](/en/part-3/1-acp-tour#quick-try-in-60-seconds) for a 60-second taste, then [3.3 Host as ACP Client](/en/part-3/3-host-client-architecture) for the full client architecture (TS + Go skeletons).

### …keep memory tenant-isolated

→ **[5.5 Memory System](/en/part-5/5-memory)** for scopes (project + user) and graceful degradation. **[6.4 Multi-Tenant & Filesystem](/en/part-6/4-multi-tenant-fs)** for the cross-tenant pitfalls. Either disable user-scope or key entries by `tenant_id+user_id`.

### …deploy via Docker without bloating runtime

→ **[6.8 Deployment](/en/part-6/8-deployment)** — multi-stage Dockerfile (build with `uv`, ship only the venv), `StatefulSet` + PVC + `sessionAffinity` for sticky sessions, canary by dimension.

### …keep my host code working across Agentao releases (audit pipeline / observability)

→ **[4.7 Embedded Harness Contract](/en/part-4/7-harness-contract)** — the `agentao.harness` package is the **stable**, schema-snapshotted host API. Use `agent.events()` (async pull iterator) for audit / SIEM / billing, and `agent.active_permissions()` for policy-snapshot UIs. Don't touch internal `AgentEvent` from production code.

Two runnable starting points: [`examples/harness_events.py`](https://github.com/jin-bo/agentao/blob/main/examples/harness_events.py) (~50 lines, prints to stdout) and [`examples/harness_audit_pipeline.py`](https://github.com/jin-bo/agentao/blob/main/examples/harness_audit_pipeline.py) (full SQLite audit loop).

### …embed Agentao in a Jupyter notebook

→ **[`examples/jupyter-session/`](https://github.com/jin-bo/agentao/tree/main/examples/jupyter-session)** — one `Agentao` per kernel; `agent.events()` drives `IPython.display`. Includes a `session.ipynb` you can open immediately and a passing test suite. Pair with **[1.3 Integration Modes](/en/part-1/3-integration-modes)** for the in-process SDK background.

### …build a Slack or WeChat bot

→ **[`examples/slack-bot/`](https://github.com/jin-bo/agentao/tree/main/examples/slack-bot)** uses `slack-bolt` `app_mention` → one turn, with channel-scoped permissions. **[`examples/wechat-bot/`](https://github.com/jin-bo/agentao/tree/main/examples/wechat-bot)** is the WeChat polling-daemon equivalent with contact-scoped permissions. Both are minimum-shape (offline smoke, no API key).

### …get hermetic pytest fixtures for Agentao

→ **[`examples/pytest-fixture/`](https://github.com/jin-bo/agentao/tree/main/examples/pytest-fixture)** ships drop-in `agent` / `agent_with_reply` / `fake_llm_client` fixtures. Hermetic, no `OPENAI_API_KEY` needed. Pair with [Appendix F.8](/en/appendix/f-faq#f-8-development-testing) for the assertion patterns.

## Don't see your task?

- **All runnable examples** — [`examples/README.md`](https://github.com/jin-bo/agentao/blob/main/examples/README.md) lists every sample with stack, run command, and what it shows.
- **By role**: see the "Pick your starting point" table on the [home page](/en/).
- **By search**: VitePress search (top-right) is local + full-text.
- **Stuck**: [Appendix F · FAQ & Troubleshooting](/en/appendix/f-faq) is organized by symptom.
