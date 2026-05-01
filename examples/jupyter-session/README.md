# Jupyter · kernel-lifetime session

One `Agentao` per kernel; subsequent cells reuse it. The same code
that the notebook (`session.ipynb`) imports is exercised by the smoke
test against a fake LLM, so CI runs without any API key.

## Run the notebook

```bash
cd examples/jupyter-session
uv sync --extra notebook
OPENAI_API_KEY=sk-... uv run jupyter notebook session.ipynb
```

Cells:

1. Build the session (one `Agentao` for the kernel lifetime).
2. Run a turn via `await turn(session, "...")`. The helper kicks off
   `agent.arun()` and concurrently drains the first two harness events
   so you can see the lifecycle without blocking the cell.
3. `close_session(session)` at the end (or attach to `atexit`).

## Smoke test (no key needed)

```bash
cd examples/jupyter-session
uv sync --extra dev
uv run pytest tests/ -v
```

`tests/test_smoke.py` patches `Agentao._llm_call` to return a scripted
reply and asserts the same `build_session` / `turn` / `close_session`
helpers wire up correctly.

## What this demonstrates

- **One agent per kernel** — heavyweight setup (skill loading, MCP
  discovery) happens once.
- **`events()` driving display** — `drain_events_into` is the loop
  the notebook would attach to an `ipywidgets.Output`.
- **Top-level `await`** — `arun()` works inside a Jupyter cell
  without `asyncio.run`.
