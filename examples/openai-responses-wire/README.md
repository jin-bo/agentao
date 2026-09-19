# OpenAI Responses wire · `api_format="openai-responses"`

Agentao speaks OpenAI Chat Completions by default. This sample embeds it over
OpenAI's **Responses API** instead — one keyword at construction — and shows
what differs on that wire. No API key needed for the smoke: the tests put a
scripted socket under the **real** `openai` SDK, so the request bodies they
read are what the SDK actually serialized.

## Try it

```bash
cd examples/openai-responses-wire
uv sync --extra dev
PYTHONPATH=. uv run pytest tests/ -v

# live, one turn:
OPENAI_API_KEY=sk-... uv run python -m src.wire "Say hello in five words."
```

## The whole change

```python
agent = Agentao(
    working_directory=workdir,
    api_key=api_key,
    base_url="https://api.openai.com/v1",   # what Chat Completions takes
    model="gpt-5.4",
    api_format="openai-responses",          # keyword-only; sub-agents inherit it
)
```

`api_format` is **never inferred** — not from the URL, the provider name or
the model name. The same key and base URL keep working on Chat Completions
until you say otherwise. From the environment the same switch is
`{PROVIDER}_API_FORMAT=openai-responses`. No new dependency: it is the
`openai` SDK Agentao already installs.

## What differs on this wire

| | Chat Completions | `openai-responses` |
|---|---|---|
| Route | `{base_url}/chat/completions` | `{base_url}/responses` |
| Thinking depth | `extra_body={"reasoning_effort": "high"}` | `extra_body={"reasoning": {"effort": "high"}}` — a top-level `reasoning_effort` is rejected |
| Reasoning text | `reasoning_content`, where a provider sends it | a **summary**, and only when asked: `{"reasoning": {"summary": "auto"}}` |
| Reasoning across requests | dropped | kept: encrypted items ride the assistant message and go back with the next request |
| Server-side state | none | none — `store: false`, no `previous_response_id` |

History does not change shape: `agent.messages` stays OpenAI-style dicts on
every wire, so session files, replay and compaction are the same. Stateless is
deliberate — compaction rewrites history, `/clear` wipes it and replay replays
it, and a conversation kept by the provider would diverge from all three. The
code is `src/wire.py`; the scripted socket is `tests/conftest.py`.

## Usage, not an invoice

`usage_report(agent)` returns the four quantities a price list needs.
`prompt_tokens` is the **whole** input; `cache_read_tokens` and
`cache_creation_tokens` are parts *of* it that the provider bills at other
rates (the write count needs `openai` 3.x, which is where the SDK started
reading it). Agentao reports the quantities and applies no prices.
