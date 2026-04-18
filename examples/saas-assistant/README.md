# Blueprint A · SaaS Assistant

A minimal FastAPI service that embeds Agentao behind a Bearer-token auth layer, streams the reply over SSE, serializes turns per session, and exposes a cancel endpoint.

Corresponds to [Part 7.1 of the developer guide](../../developer-guide/en/part-7/1-saas-assistant).

## What it demonstrates

- **Session pool** — one `Agentao` per `(tenant_id, session_id)` pair, cached in memory
- **SSE streaming** — events relayed from `SdkTransport(on_event=…)` → asyncio queue → `StreamingResponse`
- **Custom tools** — `ListProjectsTool` (read-only) and `CreateTaskTool` (`requires_confirmation=True`) wrapped around an in-memory product store
- **Cancellation wiring** — client-disconnect watcher + explicit `/cancel` endpoint both fire the same `CancellationToken`
- **Per-tenant isolation** — `working_directory=/data/tenants/{tenant}/{session}` keeps memory/logs scoped

## How to run

```bash
cp .env.example .env                  # add OPENAI_API_KEY
uv sync
uv run python tests/smoke.py          # 1-second import + route check
uv run uvicorn app.main:app --reload
```

Send a request:

```bash
curl -N -X POST http://127.0.0.1:8000/chat/s-1 \
     -H "Authorization: Bearer dev-alice" \
     -H "Content-Type: application/json" \
     -d '{"message":"List my active projects"}'
```

Expected: SSE frames stream (`data: {"type":"llm_text","chunk":"…"}`), and the stream ends with `event: done\ndata: {"reply":"…"}`.

Cancel a running turn:

```bash
curl -X POST http://127.0.0.1:8000/chat/s-1/cancel \
     -H "Authorization: Bearer dev-alice"
```

Tear down the session (closes the agent, releases any MCP subprocesses):

```bash
curl -X DELETE http://127.0.0.1:8000/session/s-1 \
     -H "Authorization: Bearer dev-alice"
```

## File map

| Path | Role |
|------|------|
| `app/main.py` | FastAPI app with `/chat`, `/cancel`, `/session`, and lifespan cleanup |
| `app/tools.py` | `ListProjectsTool` + `CreateTaskTool` backed by an in-memory store |
| `app/auth.py` | Mock Bearer-token → `User` resolver (`dev-alice`, `dev-bob`, `dev-carol`) |
| `tests/smoke.py` | Import + route assertion, runs offline |
| `.env.example` | Template for `OPENAI_API_KEY` |
| `data/tenants/{tenant}/{session}/` | Per-session workdirs created at runtime (git-ignored) |

## Not included

- Real JWT / OAuth — replace `app.auth.current_user`
- Real backend API calls — swap `_PROJECTS` / `_TASKS` for `httpx.Client`
- Frontend — the guide's [React snippet](../../developer-guide/en/part-7/1-saas-assistant#frontend-skeleton) consumes the `/chat` SSE stream directly
- TTL / LRU eviction — add a background task or use the pattern from [Part 6.7](../../developer-guide/en/part-6/7-resource-concurrency)
