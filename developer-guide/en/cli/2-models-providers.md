# 2. Models & Providers

The CLI lets you switch model, provider, and sampling temperature without restarting. Three commands cover everything: `/model`, `/provider`, `/temperature`.

## The provider concept

A **provider** is a (`API_KEY`, `BASE_URL`, `MODEL`) triple — one set of credentials pointing at one OpenAI-compatible endpoint with one default model. Provider names are arbitrary; they come from your `.env` file.

The convention is `XXXX_API_KEY` / `XXXX_BASE_URL` / `XXXX_MODEL`, where `XXXX` is the provider name in upper case:

```bash
# .env

# Default provider (legacy: just OPENAI_*)
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4

# Add as many as you want
GEMINI_API_KEY=...
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
GEMINI_MODEL=gemini-2.5-pro

DEEPSEEK_API_KEY=sk-...
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

LOCAL_API_KEY=any-string
LOCAL_BASE_URL=http://localhost:8000/v1
LOCAL_MODEL=qwen2.5-72b
```

A provider is only listed if **all three** of its env vars are set. If `GEMINI_API_KEY` exists but `GEMINI_BASE_URL` is missing, `GEMINI` doesn't appear.

## `/provider` — list or switch

```text
> /provider
```

Lists every detected provider, marks the active one with ✓, prints usage hint.

```text
> /provider GEMINI
```

Switches credentials + base URL + default model in one shot. The conversation history is preserved — only the LLM client changes. The next turn goes to the new provider.

Common errors:

| Message | Fix |
|---------|-----|
| `No providers found in .env` | Set at least one `XXXX_API_KEY` + `XXXX_BASE_URL` + `XXXX_MODEL` triple |
| `No API key found for provider 'GEMINI'` | `GEMINI_API_KEY` is missing |
| `No base URL configured for provider 'GEMINI'` | `GEMINI_BASE_URL` is missing |
| `No model configured for provider 'GEMINI'` | `GEMINI_MODEL` is missing |

## `/model` — list or switch model on the current provider

```text
> /model
```

Shows the current model, then queries the provider for the full model list and groups output:

```text
Current Model: gpt-5.4

Available Models:

  Claude:
    • claude-haiku-4-5
    • claude-sonnet-4-6
    • claude-opus-4-7

  OpenAI GPT:
    • gpt-5.4 ✓
    • gpt-5.4-mini

  Other:
    • o3
    • text-embedding-3-small
```

The list comes from the provider's `/models` endpoint, so it reflects what the provider actually exposes — not a hardcoded table. Switch with:

```text
> /model claude-sonnet-4-6
```

`/model` is scoped to the **current** provider. If the model you want isn't available there, switch provider first with `/provider`.

::: tip Cross-provider model names
Some providers expose Claude models behind OpenAI-compatible APIs. If `/model` lists `claude-*` while you're on a non-Anthropic provider, that's intentional — the provider is proxying.
:::

## `/temperature` — sampling temperature

```text
> /temperature        # show current
Temperature: 1.0
> /temperature 0.2    # set
Temperature changed from 1.0 to 0.2
```

Range: `0.0` to `2.0`. Lower = more deterministic, higher = more creative. Defaults are provider-specific (typically 1.0 for chat).

The change is **per session**. Restarting the CLI resets to the provider default. If you want a persistent default, set it in `.env`:

```bash
LLM_TEMPERATURE=0.3
```

Need a request param the CLI has no command for — `reasoning_effort`, `top_p`, `seed`, `response_format`, or a provider-specific field? Set `LLM_EXTRA_BODY` to a JSON object; it is forwarded verbatim to the LLM `.create()` (and inherited by sub-agents):

```bash
LLM_EXTRA_BODY='{"reasoning_effort":"high"}'
```

See [Appendix B](/en/appendix/b-config-keys) for parsing/redaction details.

## When to switch what

| Situation | What to do |
|-----------|-----------|
| Same task, want a smarter model | `/model <bigger>` (stays on provider) |
| Same task, want a cheaper model | `/model <smaller>` |
| Switch to a different vendor | `/provider <NAME>` (history kept, credentials swap) |
| Outputs feel too random / hallucinatory | `/temperature 0.2` |
| Outputs feel too rigid / repetitive | `/temperature 1.2` |
| Cost is exploding mid-session | `/model` to a smaller variant before the next turn |

## Pitfalls

- **Switching mid-tool-call**: if you switch model or provider while the agent is still iterating tools, the next iteration uses the new client. Mostly fine, but tool-call format differences between providers can cause a single garbled turn — use `/clear` after switching if you see this.
- **Conversation history is preserved across switches**: this is usually what you want, but it means the new model sees the previous model's tool-use traces. Some models are stricter than others about message format mismatches.
- **Models you can name aren't always models you can call**: `/model` lists what the provider's `/models` endpoint reports; some entries (embedding models, fine-tunes you don't have access to) will fail at the next turn. Check the error and pick another.

## Where to go next

| Want to… | Read |
|----------|------|
| Make sure the agent doesn't run dangerous tools on the new model | [3. Permissions & Modes](./3-permissions-modes) |
| Recover from a context blow-up after switching to a smaller model | [7. Context & Status](./7-context-status) |
| Run a local model | Set `LOCAL_*` env vars as shown above; same `/provider LOCAL` flow |

---

::: info Where this fits
The CLI uses `cli.agent.set_provider(api_key, base_url, model)` and `cli.agent.set_model(name)` — both are public methods on the `Agentao` instance. An embedding host can call them too, with the same effect on the same in-memory session. See [Part 2.5 · Runtime LLM Switch](/en/part-2/5-runtime-llm-switch) for the embedded-API equivalents and the threading caveats.
:::

::: tip Authoritative help
Command syntax: `/help` and [`agentao/cli/help_text.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/help_text.py). Discovery logic for providers: [`agentao/cli/commands.py:_list_providers_from_env`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py).
:::
