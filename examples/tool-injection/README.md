# Tool injection — Jina-backed `web_fetch` / `web_search`

Demonstrates the two host-facing tool-injection surfaces of the embedded
contract, both swapping a built-in for a [Jina](https://jina.ai) backend by
*name* (same name + parameter schema = drop-in, so the model's existing tool
calls keep working):

| Surface | When | Tool | Jina endpoint | API |
|---|---|---|---|---|
| `Agentao(extra_tools=[...])` | **construction** | `web_fetch` | `https://r.jina.ai/{url}` (Reader) | `make_agent()` |
| `agent.add_tool(...)` | **runtime** | `web_search` | `https://s.jina.ai/{query}` (Search) | `inject_search_at_runtime()` |

Runtime-added tools become model-visible on the **next** `chat()` / `arun()`
call (the schema is snapshotted per call, never mid-turn). Both tools inherit
the same working-directory / filesystem / shell capability binding as
built-ins, and a same-named entry overrides the built-in silently.

## Run the smoke tests (offline, no API key)

```bash
cd examples/tool-injection
uv sync --extra dev
PYTHONPATH=. uv run pytest tests/ -v
```

The tests drive both tools through an `httpx.MockTransport`, so they assert the
correct Jina endpoint + auth header are used without any network call.

## Use it for real

```python
from src.jina_tools import make_agent, inject_search_at_runtime

# r.jina.ai web_fetch injected at construction; pass a real LLM client or
# api_key=/base_url=/model= to drive turns.
agent = make_agent(".", jina_api_key="jina_...")        # JINA_API_KEY also read from env

# ...later, once a Jina key is available, add s.jina.ai web_search at runtime:
inject_search_at_runtime(agent, jina_api_key="jina_...")
```

`JINA_API_KEY` (optional) is sent as `Authorization: Bearer <key>` for higher
rate limits. See [`docs/design/host-tool-injection.md`](../../docs/design/host-tool-injection.md)
and [`docs/api/host.md`](../../docs/api/host.md).
