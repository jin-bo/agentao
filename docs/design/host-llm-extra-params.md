# Host LLM extra-params passthrough: `extra_params` (v1)

**Status:** **Design — not yet implemented.** Surfaced by the goose 2026-06-13 pull reverse-review (finding "B"): the LLM request kwargs are a closed set, so a host cannot reach `reasoning_effort` / `top_p` / `seed` / `response_format` or any provider-specific field.
**Audience:** agentao maintainers building the host LLM-config surface; reviewers of the implementing PR.
**Companions:**
- `docs/design/host-llm-extra-params.zh.md` — Chinese version
- `docs/design/embedded-host-contract.md` — the host-contract stability boundary (where this design belongs)
- `docs/design/host-tool-injection.md` / `.zh.md` — sibling host-injection primitive (same "thread an explicit kwarg through the construction paths, defer the settings.json layer" shape)
- `docs/reference/configuration.md` — §2 (`.env`), where the `LLM_EXTRA_PARAMS` env var is documented (settings.json file layer is deferred — see §2 / §8)
- `agentao/llm/client.py` — `__init__` (`90-100`), `chat()` kwargs (`318-330`), `chat_stream()` kwargs (`450-462`), `reconfigure()` (`270-282`) — the main change sites
- `agentao/agent.py` — `_build_llm_client` `llm_kwargs` (`665-676`)
- `agentao/embedding/factory.py` — `discover_llm_kwargs()` (`57-82`)

---

## 1. Problem: agentao has no LLM extra-params surface

The OpenAI-compatible request is assembled into a **closed `kwargs` dict** in two places:

- `chat()` — `client.py:318-330`
- `chat_stream()` — `client.py:450-462`

Both build exactly `{model, messages, temperature?, tools?, tool_choice?, max_tokens|max_completion_tokens?}` and pass it to the SDK — non-streaming via `client.chat.completions.with_raw_response.create(**kwargs)` (`client.py:339`), streaming via `client.chat.completions.create(**kwargs)` (`client.py:613`). (The two methods differ; implementers must spy the *right* one per path — see §9.) The constructor (`client.py:90-100`) exposes only `api_key / base_url / model / temperature / max_tokens / log_file / logger` — there is **no field** for additional request parameters.

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
- Add a `.agentao/settings.json :: llm.extra_params` file layer — **deferred** (see §8). `settings.json` today is runtime-mode + builtin-agents only (`configuration.md:70`); `_load_settings` (`factory.py:37`) feeds no LLM config, and there is **no** "env > settings" LLM precedence rule to slot into. `host-tool-injection.md` set the precedent of deferring a settings file layer until a concrete need appears (gap≠need). v1's two surfaces are the constructor kwarg and the `LLM_EXTRA_PARAMS` env var.
- Introduce a runtime mutation surface — see §8 (deferred `/param`).

## 3. Core mechanism

### 3.1 New field

In `LLMClient.__init__` (`client.py:90-100`), after `max_tokens`:

```python
extra_params: Optional[Dict[str, Any]] = None,
...
# Host-supplied passthrough merged into every request (minus reserved keys).
# Explicit isinstance guard: bare ``dict(extra_params or {})`` would silently
# accept a list-of-pairs (``[("x", 1)]``) and raise ValueError (not TypeError)
# on other malformed shapes — fail fast with a clear contract instead.
if extra_params is not None and not isinstance(extra_params, dict):
    raise TypeError("LLMClient.extra_params must be a dict or None.")
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

## 4. Constructor signature & wiring (two construction paths)

Same shape as `temperature` / `max_tokens`:

| Path | Change |
|---|---|
| **Embedded host** | `Agentao(..., extra_params={"reasoning_effort": "high"})` → thread into `_build_llm_client` `llm_kwargs` (`agent.py:665-676`), mirroring the existing `temperature` / `max_tokens` conditionals. **Plus the guard in §4.1.** |
| **CLI / env** | `discover_llm_kwargs()` (`factory.py:57`) reads `LLM_EXTRA_PARAMS` as a JSON **object**, parsed inside a `try/except`: malformed JSON → `warn + skip`. **It must also reject valid-but-non-object JSON** — `LLM_EXTRA_PARAMS=[]` / `"x"` / `3` parse fine but are invalid config; require `isinstance(parsed, dict)` and treat a non-object the same as malformed (warn + skip), so the **env-warning policy** governs the env path rather than a confusing downstream `TypeError` at construction (§3.1). **Note:** this is *intentionally more tolerant* than the existing `LLM_TEMPERATURE` / `LLM_MAX_TOKENS`, which call `float()` / `int()` directly and **raise** on malformed values (`factory.py:79-82`); `build_from_environment` only dodges that today by skipping discovery entirely when `llm_client` is supplied (`factory.py:134`). The `try/except` + `isinstance` check must be added explicitly — neither is inherited from a pre-existing tolerance. |

### 4.1 Constructor mutual-exclusion guard (required)

`extra_params` must be added to the raw-LLM-config set in the constructor's mutual-exclusion guard (`agent.py:284`), which today only lists `(api_key, base_url, model, temperature, max_tokens)`:

```python
if llm_client is not None and any(
    v is not None for v in (api_key, base_url, model, temperature, max_tokens, extra_params)
):
    raise ValueError("Agentao(): pass either llm_client= or "
                     "api_key/.../extra_params, not both.")
```

**Why this is mandatory, not cosmetic:** `_resolve_llm_client()` returns an injected `llm_client` immediately and untouched (`agent.py:655`). If the implementer only follows the "thread into `_build_llm_client`" note, then `Agentao(llm_client=client, extra_params={...})` is a **silent no-op** — the build path never runs. A host that injects its own `LLMClient` must pass `extra_params=` to *that* client directly; the guard makes the mistake loud instead of silent, consistent with "a fully-constructed object always wins over its raw-config sibling" (`agent.py:280`).

## 5. `reconfigure()` / model-switch semantics

`reconfigure()` (`client.py:270-282`) **preserves `self.extra_params`** — they are instance-level host config, not model-detected quirks (those are the latches reset in `reset_capability_latches()`).

**Documented caveat:** a model-specific param (e.g. `reasoning_effort` on a model that does not support it) is the **host's responsibility** to clear on switch. This matches the existing contract: temperature is auto-latched off *only* on provider rejection; agentao does not pre-validate per-model param applicability. If real pain appears, a future enhancement can drop known model-specific keys on switch — out of v1 scope (gap≠need).

## 6. Precedence & reserved-key protection (summary)

1. Structural keys (`model`, `messages`, `stream`, `stream_options`, `tools`, `tool_choice`) — always client-owned.
2. Managed keys (`temperature`, `max_tokens`/`max_completion_tokens`) — client-owned via latches.
3. Everything else — host-owned via `extra_params`, merged last.
4. A reserved key appearing in `extra_params` is **dropped and warned**, never silently applied.

## 7. Edge cases

- **Logging (explicit v1 change — not free, must redact)**: `_log_request` (`agentao/llm/_logging.py:23`) logs a **fixed field set** — `model`, `temperature`, `max_tokens`, `messages`, `tools` — *not* arbitrary request kwargs, so passthrough params do **not** appear automatically. v1 adds one line logging the merged non-reserved extra params, but values must be **recursively redacted**: replace the value of any key whose name matches `authorization` / `api[-_]?key` / `token` / `secret` / `password` / `cookie` (case-insensitive, at any nesting depth) with `***` before logging — e.g. `Extra params: {'reasoning_effort': 'high', 'extra_headers': {'Authorization': '***'}}`. This is **mandatory, not cosmetic**: `extra_params` explicitly supports the SDK `extra_headers` / `extra_body` escape hatches (§1), so `extra_params={"extra_headers": {"Authorization": "Bearer …"}}` would otherwise leak a live credential into `agentao.log` — a class of secret the current logger deliberately keeps off the kwargs path entirely (`api_key` lives on the client, never in logged kwargs). The redactor is a small recursive helper in `_logging.py`; tested for nested `extra_headers.Authorization` (§9).
- **Back-compat**: omitting `extra_params` yields byte-identical request kwargs; existing tests untouched.
- **Type safety**: a non-dict `extra_params` raises `TypeError` at construction via the **explicit** `isinstance` guard in §3.1 — *not* via `dict(extra_params or {})` alone, which would accept a list-of-pairs and raise `ValueError` on other shapes.

## 8. Deferred

Two surfaces are intentionally out of v1 scope:

- **`settings.json :: llm.extra_params` file layer.** There is no LLM-config block in `settings.json` today (`_load_settings` at `factory.py:37` feeds runtime mode + builtin agents only) and no "env > settings" LLM precedence to extend. Adding one is a *broader* decision than this feature — it would establish `settings.json` as a general LLM-config layer (model / temperature / max_tokens could reasonably follow). Defer until a concrete "CLI user wants persistent `extra_params` in a file" need appears, and design the whole LLM-settings block then, not a one-off `extra_params` key. (Same posture as `host-tool-injection.md` deferring `tool_options`/settings.)
- **Runtime mutation (`/param`).** A setter — `LLMClient.update_extra_params(**kw)` plus a CLI `/param set seed 42` / `/param show` — is out of v1. The listed use cases (`reasoning_effort`, `top_p`, `seed`, `response_format`) are static per session and fully served by the construction-time paths. Build the runtime surface when a concrete "change a param mid-session" need appears.

## 9. Test plan

- `extra_params` merges into both `chat()` and `chat_stream()` — spy the **correct** SDK method per path: `with_raw_response.create` (non-streaming, `client.py:339`) and `create` (streaming, `client.py:613`); assert the key is present.
- A reserved key (`messages` / `temperature` / `model`) in `extra_params` is dropped + warned; structural kwargs intact.
- `reconfigure()` preserves `extra_params`; `reset_capability_latches()` still clears the latches.
- **Constructor guard (F1):** `Agentao(llm_client=<client>, extra_params={...})` raises `ValueError` — not a silent no-op.
- **Type guard (F4):** `LLMClient(extra_params=[("x", 1)])` raises `TypeError`; `extra_params=None` is accepted.
- **Env tolerance (F3):** `discover_llm_kwargs()` parses valid `LLM_EXTRA_PARAMS` JSON object; malformed JSON → key omitted + warned, no raise.
- **Env non-object (P2):** `LLM_EXTRA_PARAMS=[]` / `"x"` / `3` (valid JSON, non-object) → key omitted + warned via the env policy, **not** a construction-time `TypeError`.
- **Logging redaction (F5/P1):** with `extra_params={"reasoning_effort":"high","extra_headers":{"Authorization":"Bearer x"}}`, `_log_request` shows `reasoning_effort` but the nested `Authorization` value is `***`; with no `extra_params` the line is absent.
- Back-compat: no `extra_params` → request kwargs identical to current (golden-dict assertion).

## 10. Change sites / blast radius

| File | Change |
|---|---|
| `agentao/llm/client.py` | add field + explicit `isinstance` guard (§3.1) + `_build_request_kwargs`; route `chat()` / `chat_stream()` through it; preserve in `reconfigure()` |
| `agentao/llm/_logging.py` | log the merged extra params in `_log_request`, **values recursively redacted** for sensitive keys (§7) |
| `agentao/agent.py` | add `extra_params` kwarg; thread into `_build_llm_client`; **add to the mutual-exclusion guard** (§4.1) |
| `agentao/embedding/factory.py` | parse `LLM_EXTRA_PARAMS` JSON in a `try/except` + `isinstance(parsed, dict)` (warn + skip on malformed **or** non-object) |
| `docs/reference/configuration.md` | document the `LLM_EXTRA_PARAMS` env var (§2) — **not** a settings field (deferred, §8) |
| `tests/test_llm_client_*.py` | new coverage per §9 |

Net: the `_build_request_kwargs` extraction **removes** the duplicated closed dict — a simplification, not just an addition.
