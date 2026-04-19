# 2.5 Runtime LLM Switching

One running `Agentao` is bound to one LLM at construction. But you often want to **change which model answers the next turn**: route cheap models for small talk, expensive models for planning, fall back to a secondary provider when the primary times out. This section covers the three public APIs that let you do that without rebuilding the agent.

## 2.5.1 The three methods

```python
# Query
current = agent.get_current_model()   # -> "gpt-5.4"

# Model only — keep the same provider / API key
agent.set_model("gpt-5.4")

# Full swap — new key + endpoint + model
agent.set_provider(
    api_key="sk-deepseek-xxx",
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
)

# List everything the current endpoint advertises
models = agent.list_available_models()  # -> ["gpt-5.4", "gpt-5.4", ...]
```

All four are **synchronous** and cheap (no network traffic on `set_*`). They only mutate in-process state; the next `chat()` call picks up the change.

## 2.5.2 What changes — and what doesn't

| Changes on swap | Survives swap |
|-----------------|---------------|
| LLM client's `model`, `api_key`, `base_url` | `agent.messages` (full history) |
| tiktoken encoding (for token accounting) | Active skills |
| Token accounting cache (`_last_api_prompt_tokens` is invalidated) | Memory (both scopes) |
| `agentao.log` line `"Model changed from X to Y"` | Working directory / transport / MCP |

**Important**: conversation history is **not cleared**. The new model sees all prior turns the old model saw, including every tool-call trace. If you don't want that context bleed, call `clear_history()` **before** swapping.

## 2.5.3 Common routing patterns

### Cheap-fast / expensive-smart router

```python
CHEAP_MODEL     = "gpt-5.4"
SMART_MODEL     = "gpt-5.4"
SMART_KEYWORDS  = ("plan", "design", "refactor", "architecture")

def route(agent: Agentao, user_message: str) -> str:
    wants_smart = any(kw in user_message.lower() for kw in SMART_KEYWORDS)
    target = SMART_MODEL if wants_smart else CHEAP_MODEL
    if agent.get_current_model() != target:
        agent.set_model(target)
    return agent.chat(user_message)
```

### Multi-provider fallback chain

```python
PROVIDERS = [
    {"api_key": os.environ["OPENAI_API_KEY"], "base_url": None,                         "model": "gpt-5.4"},
    {"api_key": os.environ["DEEPSEEK_API_KEY"], "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    {"api_key": os.environ["MOONSHOT_API_KEY"], "base_url": "https://api.moonshot.cn/v1","model": "moonshot-v1-8k"},
]

def chat_with_fallback(agent: Agentao, msg: str) -> str:
    last_err = None
    for p in PROVIDERS:
        try:
            agent.set_provider(**p)
            return agent.chat(msg)
        except Exception as e:      # rate limit, timeout, refusal, ...
            last_err = e
            continue
    raise RuntimeError(f"all providers failed: {last_err}") from last_err
```

Note: this retries **the same turn** on the next provider. Because history is preserved, the second provider sees the same user message + any partial assistant output. That's usually what you want. If the first provider already committed tool calls, it's **not** what you want — in that case wrap with `clear_history()` + rebuild short context first.

### Per-tenant model choice (SaaS)

```python
def build_agent_for(tenant: Tenant) -> Agentao:
    agent = Agentao(
        working_directory=tenant.workdir,
        model=tenant.plan.default_model,     # "gpt-5.4" or "gpt-5.4"
    )
    return agent

# When tenant upgrades mid-session
agent.set_model(tenant.plan.default_model)
```

### A/B test on a live session

```python
shadow_reply = None
if random.random() < 0.05:                 # 5% traffic to challenger
    agent.set_model("gpt-5.4")
    shadow_reply = agent.chat(msg)
    agent.set_model("gpt-5.4")         # restore default
real_reply = agent.chat(msg)
log_ab(real=real_reply, shadow=shadow_reply)
```

⚠️ Calling `chat()` twice per user turn doubles cost and appends **two** assistant responses to history. For proper A/B tests use **two separate `Agentao` instances** and replay the same prompt.

## 2.5.4 `list_available_models()` — when and when not to call

```python
def list_available_models(self) -> List[str]
```

Hits the provider's `/models` endpoint. Useful for:

- Populating a UI dropdown
- Verifying credentials work at construction time (a 401 surfaces here, not three turns later)
- Gating `set_model(...)` to models the endpoint actually supports

Do **not** call it on every turn — it's network I/O. Cache the result.

On failure (network error, bad key), it **raises** `RuntimeError`. Handle it:

```python
try:
    models = agent.list_available_models()
except RuntimeError:
    models = [agent.get_current_model()]  # fall back to whatever is set
```

## 2.5.5 Gotchas

1. **Tokenizer mismatch across providers**
   `set_provider()` rewrites the tiktoken encoding to match the new model name. But OpenAI's tokenizer is still used for non-OpenAI providers (there's no universal tokenizer). Budget estimates on DeepSeek / Moonshot / Qwen are therefore **approximate**. Leave headroom.

2. **Tool-call schema is provider-dependent**
   All Agentao-supported providers must speak the **OpenAI-compatible** tool-calls schema. If you swap to a provider that doesn't, `chat()` will either error ("function not supported") or silently drop the tool call. Test each provider in your fallback chain.

3. **Streaming behavior may differ**
   Some providers send smaller/larger chunks or delay the first token. UI-perceived latency can change noticeably after a swap. Your `Transport.emit(...)` still fires — but chunk boundaries are different.

4. **Context window changes**
   `gpt-5.4` has 128k; `gpt-5.4` has 128k; `deepseek-chat` has 64k. After `set_model("deepseek-chat")`, a long history that fit before may now trigger compaction. This is handled automatically by the context manager — no action needed on your side, but expect a summary block to appear.

5. **Swapping mid-turn is undefined**
   Don't call `set_model()` inside a `chat()` — `chat()` holds a reference to the LLM client and the swap won't apply cleanly until the next turn. If you need it, wrap `chat()` with `clear_history()` + `set_model()` + new `chat()`.

## 2.5.6 Why provider is not a construction arg

You might notice `Agentao(...)` takes `api_key / base_url / model` directly but no abstract `provider=` parameter. That's deliberate:

- Agentao speaks only the OpenAI-compatible protocol
- Any provider (OpenAI, Azure, DeepSeek, Moonshot, Together, local Ollama, vLLM…) that exposes the OpenAI schema is a valid target
- "Switching provider" is just "switching key + base_url + model"

So there's no `OpenAIProvider` / `AnthropicProvider` / `GoogleProvider` abstraction — keep your routing code simple. If you need native Anthropic or Gemini, put them behind an OpenAI-compatible gateway.

---

Next: [2.6 Cancellation & timeouts →](./6-cancellation-timeouts)
