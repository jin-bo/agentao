# FastAPI · Background Task

The minimum 1-route shape: each `POST /run` enqueues an Agentao turn
to an asyncio background task. Per-request agent, per-request
cancellation token, no shared mutable state. For multi-tenant SaaS
shapes (per-tenant pool, SSE), see `examples/saas-assistant/` instead.

## Run (with a real key)

```bash
cd examples/fastapi-background
uv sync
OPENAI_API_KEY=sk-... uv run uvicorn app.main:app --reload

# In another terminal:
curl -X POST http://127.0.0.1:8000/run \
  -H 'content-type: application/json' \
  -d '{"prompt":"summarize today"}'
# → {"job_id":"a1b2c3d4"}
curl http://127.0.0.1:8000/run/a1b2c3d4
# → {"status":"ok","result":"..."}
```

## Smoke test (no key needed)

```bash
cd examples/fastapi-background
uv sync --extra dev
uv run pytest tests/ -v
```

The fixture in `tests/test_smoke.py` overrides `llm_client_factory`
with a `MagicMock` so the test runs offline.

## What this demonstrates

- **Per-request `Agentao(...)`** — `app.main._run_turn` constructs and
  closes the agent inside the background task; nothing leaks across
  requests.
- **`llm_client=` injection seam** — production reads from env;
  tests pass a fake. No conditional code paths.
- **Cancellation reaches the chat token** — `POST /run/{id}/cancel`
  fires `CancellationToken.cancel()`; the in-flight `arun()` task is
  also cancelled at the asyncio layer.
