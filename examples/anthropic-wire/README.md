# Anthropic native wire · `api_format="anthropic-messages"`

Agentao speaks OpenAI Chat Completions by default. This sample embeds it over
Anthropic's own **Messages API** instead — one keyword at construction — and
shows the three things that differ on that wire. No API key needed for the
smoke: the tests put a scripted socket under the **real** `anthropic` SDK, so
the request bodies they read are what the SDK actually serialized.

## Try it

```bash
cd examples/anthropic-wire
uv sync --extra dev
PYTHONPATH=. uv run pytest tests/ -v

# live, one turn:
ANTHROPIC_API_KEY=sk-ant-... uv run python -m src.wire "Say hello in five words."
```

## The whole change

```python
agent = Agentao(
    working_directory=workdir,
    api_key=api_key,
    base_url="https://api.anthropic.com",   # the API root — no /v1
    model="claude-sonnet-5",
    api_format="anthropic-messages",        # keyword-only; sub-agents inherit it
)
```

`api_format` is **never inferred** — not from the URL, the provider name or
the model name. An Anthropic model behind an OpenAI-compatible gateway is a
working setup and stays on Chat Completions until you say otherwise. From the
environment the same switch is `{PROVIDER}_API_FORMAT=anthropic-messages`.

## What differs on this wire

| | Chat Completions | `anthropic-messages` |
|---|---|---|
| `base_url` | ends in `/v1` | the API root; the SDK appends `/v1/messages` |
| Thinking depth | `extra_body={"reasoning_effort": "high"}` | `extra_body={"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}}` — `reasoning_effort` is rejected |
| `temperature` | sent | **never sent** (the SDK has no such parameter) |
| Prompt cache | automatic, or `prompt_cache="anthropic"` | `prompt_cache="anthropic"` places native breakpoints |

History does not change shape: `agent.messages` stays OpenAI-style dicts on
every wire, so session files, replay and compaction are the same. The code is
`src/wire.py`; the scripted socket is `tests/conftest.py`.

## Usage, not an invoice

`usage_report(agent)` returns the four quantities a price list needs.
`prompt_tokens` is the **whole** input; `cache_read_tokens` and
`cache_creation_tokens` are parts *of* it that the provider bills at other
rates. Agentao reports the quantities and applies no prices.
