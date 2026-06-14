# Host LLM extra-params passthrough: `extra_params` (v1)

**Status:** **Design — not yet implemented.** Surfaced by the goose 2026-06-13 pull reverse-review (finding "B"): the LLM request kwargs are a closed set, so a host cannot reach `reasoning_effort` / `top_p` / `seed` / `response_format` or any provider-specific field.
**Audience:** agentao maintainers building the host LLM-config surface; reviewers of the implementing PR.
**Companions:**
- `docs/design/host-llm-extra-params.zh.md` — Chinese version
- `docs/design/embedded-host-contract.md` — the host-contract stability boundary (where this design belongs)
- `docs/design/host-tool-injection.md` / `.zh.md` — sibling host-injection primitive (same "thread an explicit kwarg through the three construction paths" shape)
- `docs/reference/configuration.md` — §2 (`.env`) / §3 (`settings.json`), where the config surface is documented
- `agentao/llm/client.py` — `__init__` (`90-100`), `chat()` kwargs (`318-330`), `chat_stream()` kwargs (`450-462`), `reconfigure()` (`270-282`) — the main change sites
- `agentao/agent.py` — `_build_llm_client` `llm_kwargs` (`665-676`)
- `agentao/embedding/factory.py` — `discover_llm_kwargs()` (`57-82`)

---

## 1. Problem: agentao has no LLM extra-params surface

The OpenAI-compatible request is assembled into a **closed `kwargs` dict** in two places:

- `chat()` — `client.py:318-330`
- `chat_stream()` — `client.py:450-462`

Both build exactly `{model, messages, temperature?, tools?, tool_choice?, max_tokens|max_completion_tokens?}` and pass it to `client.chat.completions.create(**kwargs)`. The constructor (`client.py:90-100`) exposes only `api_key / base_url / model / temperature / max_tokens / log_file / logger` — there is **no field** for additional request parameters.

Consequently a host cannot set any of:

- `reasoning_effort` (o-series / gpt-5 reasoning depth)
- `top_p`, `seed` (reproducibility), `response_format` (JSON mode / schema)
- `frequency_penalty`, `presence_penalty`, `stop`, `logprobs`, …
- the SDK's own `extra_body` / `extra_headers` escape hatch for non-standard providers

The only workaround today is to **subclass `LLMClient` and override `chat()`/`chat_stream()`**, or monkeypatch — both reach into runtime internals and are off the `agentao.host` contract. This is a missing **harness primitive**, parallel to the tool-injection gap closed in `host-tool-injection.md`.

## 2. Scope decision: one generic dict, not four named args

**v1 ships a single `extra_params: dict`**, merged into the request kwargs — *not* per-parameter constructor arguments.

| Why generic | Rationale |
|---|---|
| Covers the four named cases + everything else | `reasoning_effort` / `top_p` / `seed` / `response_format` are just dict keys; so is any future or provider-specific param. |
| No churn as providers add knobs | A new OpenAI param needs zero agentao changes. |
| Escape hatch included | `extra_params={"extra_body": {...}}` forwards arbitrary non-SDK fields via the OpenAI SDK's own mechanism. |
| Matches existing posture | Mirrors how `temperature` / `max_tokens` thread through the construction paths — no new config *layer*, just one more explicit kwarg. |

**v1 explicitly does not:**
- Add named `reasoning_effort=` / `seed=` constructor args (the dict subsumes them; promote to named args only if a param needs agentao-side validation or latch behaviour later).
- Validate param *values* — the host is configuring its **own** LLM endpoint; the SDK / provider validates. (This is not the "no silent third-party proxy" case — passthrough is the host's explicit intent, not a redirected destination.)
- Introduce a runtime mutation surface — see §8 (deferred `/param`).

## 3. Core mechanism

### 3.1 New field

In `LLMClient.__init__` (`client.py:90-100`), after `max_tokens`:

```python
extra_params: Optional[Dict[str, Any]] = None,
...
# Host-supplied passthrough merged into every request (minus reserved keys).
self.extra_params: Dict[str, Any] = dict(extra_params or {})
```

`None` → empty dict → behaviour byte-identical to today (back-compat).

### 3.2 Centralized request-kwargs builder (altitude fix)

The closed dict is currently **duplicated** in `chat()` and `chat_stream()` — which is precisely why the gap was easy to miss. Extract one builder and route both sites through it:

```python
# Keys the client owns structurally or manages via one-shot latches; a host
# cannot override them through extra_params.
_RESERVED_PARAMS = frozenset({
    "model", "messages", "stream", "stream_options",
    "tools", "tool_choice",
    "temperature", "max_tokens", "max_completion_tokens",
})

def _build_request_kwargs(self, messages, tools, max_tokens, *, stream):
    kwargs = {"model": self.model, "messages": messages}
    if stream:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
    if not self.omit_temperature:
        kwargs["temperature"] = self.temperature
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if max_tokens:
        key = "max_completion_tokens" if self._use_max_completion_tokens else "max_tokens"
        kwargs[key] = max_tokens
    # Host passthrough merged last; reserved keys dropped + warned.
    for k, v in self.extra_params.items():
        if k in _RESERVED_PARAMS:
            self.logger.warning("extra_params: ignoring reserved key %r", k)
            continue
        kwargs[k] = v
    return kwargs
```

- `chat()` → `_build_request_kwargs(..., stream=False)`
- `chat_stream()` → `_build_request_kwargs(..., stream=True)`

`_emit_nonstreaming` (`client.py:671`) delegates to `chat()`, so it is **not** a third site — only two callers to convert.

### 3.3 Interaction with the existing one-shot latches

`chat()`/`chat_stream()` keep their in-`except` fix-ups verbatim:
- `max_tokens` → `max_completion_tokens` rename on provider error.
- temperature-rejected → set `omit_temperature` and retry.

Because both managed keys are **reserved**, `extra_params` can never collide with these latches. Reserved-key protection is what makes the merge safe.

## 4. Constructor signature & wiring (three construction paths)

Same three-path shape as `temperature` / `max_tokens`:

| Path | Change |
|---|---|
| **Embedded host** | `Agentao(..., extra_params={"reasoning_effort": "high"})` → thread into `_build_llm_client` `llm_kwargs` (`agent.py:665-676`), mirroring the existing `temperature` / `max_tokens` conditionals. |
| **CLI / env** | `discover_llm_kwargs()` (`factory.py:79`) reads `LLM_EXTRA_PARAMS` as a JSON object → `out["extra_params"] = json.loads(v)`. Malformed JSON → warn + skip, matching the existing `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` tolerance (`factory.py:134`). |
| **Config file** | `.agentao/settings.json :: llm.extra_params` (object), merged by the factory. Precedence: env var > settings.json (existing rule). |

## 5. `reconfigure()` / model-switch semantics

`reconfigure()` (`client.py:270-282`) **preserves `self.extra_params`** — they are instance-level host config, not model-detected quirks (those are the latches reset in `reset_capability_latches()`).

**Documented caveat:** a model-specific param (e.g. `reasoning_effort` on a model that does not support it) is the **host's responsibility** to clear on switch. This matches the existing contract: temperature is auto-latched off *only* on provider rejection; agentao does not pre-validate per-model param applicability. If real pain appears, a future enhancement can drop known model-specific keys on switch — out of v1 scope (gap≠need).

## 6. Precedence & reserved-key protection (summary)

1. Structural keys (`model`, `messages`, `stream`, `stream_options`, `tools`, `tool_choice`) — always client-owned.
2. Managed keys (`temperature`, `max_tokens`/`max_completion_tokens`) — client-owned via latches.
3. Everything else — host-owned via `extra_params`, merged last.
4. A reserved key appearing in `extra_params` is **dropped and warned**, never silently applied.

## 7. Edge cases

- **Logging**: `_log_request` already logs the assembled `kwargs`, so passthrough params (incl. nested `extra_body`) appear in `agentao.log` for free; the stream path still strips `stream`.
- **Back-compat**: omitting `extra_params` yields byte-identical request kwargs; existing tests untouched.
- **Type safety**: coerce `None` → `{}`; a non-dict `extra_params` raises `TypeError` at construction (fail-fast, like the empty-`api_key` guards).

## 8. Deferred: runtime mutation (`/param`)

A runtime setter — `LLMClient.update_extra_params(**kw)` plus a CLI `/param set seed 42` / `/param show` — is **out of v1 scope**. The listed use cases (`reasoning_effort`, `top_p`, `seed`, `response_format`) are static per session and fully served by the construction-time paths. Build the runtime surface when a concrete "change a param mid-session" need appears.

## 9. Test plan

- `extra_params` merges into both `chat()` and `chat_stream()` `create(**kwargs)` (spy the SDK call; assert key present).
- A reserved key (`messages` / `temperature` / `model`) in `extra_params` is dropped + warned; structural kwargs intact.
- `reconfigure()` preserves `extra_params`; `reset_capability_latches()` still clears the latches.
- `discover_llm_kwargs()` parses `LLM_EXTRA_PARAMS` JSON; malformed JSON → skipped, no crash.
- Back-compat: no `extra_params` → request kwargs identical to current (golden-dict assertion).

## 10. Change sites / blast radius

| File | Change |
|---|---|
| `agentao/llm/client.py` | add field + `_build_request_kwargs`; route `chat()` / `chat_stream()` through it; preserve in `reconfigure()` |
| `agentao/agent.py` | add `extra_params` kwarg; thread into `_build_llm_client` |
| `agentao/embedding/factory.py` | parse `LLM_EXTRA_PARAMS` + `settings.json :: llm.extra_params` |
| `docs/reference/configuration.md` | document the env var + settings field |
| `tests/test_llm_client_*.py` | new coverage per §9 |

Net: the `_build_request_kwargs` extraction **removes** the duplicated closed dict — a simplification, not just an addition.
