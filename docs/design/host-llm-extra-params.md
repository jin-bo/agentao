# Host LLM request passthrough: `extra_body` (v1)

**Status:** **Design — not yet implemented.** Surfaced by the goose 2026-06-13 pull reverse-review (finding "B"): the LLM request kwargs are a closed set, so a host cannot reach `reasoning_effort` / `top_p` / `seed` / `response_format` or any provider-specific field.
**Mechanism note:** an earlier draft proposed a top-level-merge `extra_params` dict. A reverse review (verified against `openai 2.24.0`) found `.create()` has **no `**kwargs`** — unknown top-level keys raise `TypeError`, and the SDK's blessed escape hatch for arbitrary body fields is **`extra_body`**. v1 therefore forwards `extra_body` verbatim instead of merging keys top-level. See §2 for the full rationale.
**Audience:** agentao maintainers building the host LLM-config surface; reviewers of the implementing PR.
**Companions:**
- `docs/design/host-llm-extra-params.zh.md` — Chinese version
- `docs/design/embedded-host-contract.md` — the host-contract stability boundary (where this design belongs)
- `docs/design/host-tool-injection.md` / `.zh.md` — sibling host-injection primitive (same "thread an explicit kwarg through the construction paths, defer the settings.json layer" shape)
- `docs/reference/configuration.md` — §2 (`.env`), where the `LLM_EXTRA_BODY` env var is documented (settings.json file layer is deferred — see §8)
- `agentao/llm/client.py` — `__init__` (`def` at `90`), `chat()` kwargs (`318-330`) + non-streaming `with_raw_response.create` (`339`), `chat_stream()` kwargs (`450-462`) + streaming `create` (`613`), `reconfigure()` (`def` at `251`) — the main change sites
- `agentao/llm/_logging.py` — `_log_request` (`def` at `23`)
- `agentao/agent.py` — `_build_llm_client` `llm_kwargs` (`665-676`), mutual-exclusion guard (`284`)
- `agentao/embedding/factory.py` — `discover_llm_kwargs()` (`57-82`)

---

## 1. Problem: agentao has no LLM request-passthrough surface

The OpenAI-compatible request is assembled into a **closed `kwargs` dict** in two places:

- `chat()` — `client.py:318-330` → `client.chat.completions.with_raw_response.create(**kwargs)` (`client.py:339`)
- `chat_stream()` — `client.py:450-462` → `client.chat.completions.create(**kwargs)` (`client.py:613`)

Both build exactly `{model, messages, temperature?, tools?, tool_choice?, max_tokens|max_completion_tokens?}`. The constructor (`client.py:90`) exposes only `api_key / base_url / model / temperature / max_tokens / log_file / logger` — there is **no field** for additional request parameters.

Consequently a host cannot set any of:

- `reasoning_effort` (o-series / gpt-5 reasoning depth)
- `top_p`, `seed` (reproducibility), `response_format` (JSON mode / schema)
- `frequency_penalty`, `presence_penalty`, `stop`, `logprobs`, …
- any **provider-specific** body field (`top_k`, `repetition_penalty`, vendor extensions, …)

The only workaround today is to **subclass `LLMClient` and override `chat()`/`chat_stream()`**, or monkeypatch — both reach into runtime internals and are off the `agentao.host` contract. This is a missing **harness primitive**, parallel to the tool-injection gap closed in `host-tool-injection.md`.

## 2. Scope decision: forward `extra_body`, not a top-level-merge dict

**v1 ships a single `extra_body: dict`** that is forwarded verbatim to `.create()` as the SDK's own `extra_body` request option — *not* a dict whose keys are merged into the top-level request kwargs.

**Why `extra_body`, not top-level merge (verified against `openai 2.24.0`):**

| Reason | Detail |
|---|---|
| `.create()` rejects unknown top-level keys | The SDK signature has **no `**kwargs`** (`inspect.signature(...).VAR_KEYWORD` is absent). A flat unknown key — `top_k`, `repetition_penalty`, any provider extension, or a *new* OpenAI param on the pinned `openai>=1.0.0` floor — raises `TypeError: unexpected keyword argument`, crashing **every** call. Top-level merge only works for params the installed SDK already types. |
| `extra_body` is the SDK-blessed escape hatch | `extra_body` (a typed `.create()` param, present since 1.x) is merged into the JSON request body by the SDK, **bypassing the typed signature**. It works uniformly for *both* SDK-known params (`reasoning_effort`/`top_p`/`seed`/`response_format` all reach real OpenAI fine via `extra_body`) and arbitrary provider-specific fields. |
| No reserved-key machinery | `extra_body` is namespaced — it is a single `.create()` argument, not a set of top-level keys — so there is **no** per-request collision check against `messages`/`tools`/etc., and no `_RESERVED_PARAMS` set to maintain. (The one residual overlap — `extra_body` contents that the SDK merges *into the body* and could shadow a structural body field — is handled by a cheap **construction-time** warn, §3.3, not a hot-path check.) |
| Smaller change | `extra_body` is already a valid key for `.create(**kwargs)`, so it rides through both existing call sites inside the `kwargs` dict with **zero** call-site signature changes (§3.2). |

**v1 explicitly does not:**
- Merge per-param keys into the top-level request kwargs (the rejected earlier draft — see Mechanism note).
- Validate body *values* — the host is configuring its **own** LLM endpoint; the SDK / provider validates. (Not the "no silent third-party proxy" case — passthrough is the host's explicit intent, not a redirected destination.)
- Ship **`extra_headers`** — **deferred** (see §8). Headers are the credential vector and deserve a deliberate logging/redaction design; the body params in finding "B" are fully served by `extra_body` alone.
- Add a `.agentao/settings.json :: llm.extra_body` file layer — **deferred** (see §8). `settings.json` today is runtime-mode + builtin-agents only (`configuration.md:70`); `_load_settings` (`factory.py:37`) feeds no LLM config, and there is **no** "env > settings" LLM precedence rule to slot into. v1's two surfaces are the constructor kwarg and the `LLM_EXTRA_BODY` env var.
- Introduce a runtime mutation surface — see §8 (deferred `/param`).

## 3. Core mechanism

### 3.1 New field

In `LLMClient.__init__` (`client.py:90`), after `max_tokens`:

```python
extra_body: Optional[Dict[str, Any]] = None,
...
# Host-supplied request-body passthrough, forwarded verbatim to .create().
# Explicit isinstance guard: bare ``dict(extra_body or {})`` would silently
# accept a list-of-pairs (``[("x", 1)]``) and raise ValueError (not TypeError)
# on other malformed shapes — fail fast with a clear contract instead.
if extra_body is not None and not isinstance(extra_body, dict):
    raise TypeError("LLMClient.extra_body must be a dict or None.")
self.extra_body: Dict[str, Any] = dict(extra_body or {})
```

`None`/empty → not forwarded → behaviour byte-identical to today (back-compat).

### 3.2 Forwarding (rides inside the existing kwargs dict)

`extra_body` is itself a valid `.create()` argument, so it is added to the request `kwargs` dict and flows through **both** existing call sites — `with_raw_response.create(**kwargs)` (non-streaming) and `_consume_stream` → `create(**kwargs)` (streaming) — with **no** call-site or `_consume_stream` signature changes. The closed dict is currently **duplicated** in `chat()` and `chat_stream()` (the reason the gap was easy to miss); extract one builder and add the single line there:

```python
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
    if self.extra_body:                 # omitted when empty → byte-identical to today
        kwargs["extra_body"] = self.extra_body
    return kwargs
```

- `chat()` → `_build_request_kwargs(..., stream=False)`
- `chat_stream()` → `_build_request_kwargs(..., stream=True)`

`_emit_nonstreaming` (`client.py:671`) delegates to `chat()`, so it is **not** a third site — only two callers to convert. Extracting the builder is the altitude cleanup (de-dups the closed dict); the feature itself is just the `extra_body` field + the one line above.

### 3.3 Structural-overlap guard (construction-time, warn-once)

`extra_body` is merged **into the request body** by the SDK, so a key inside it could shadow a structural body field (`model`, `messages`, `stream`, `stream_options`, `tools`, `tool_choice`, `temperature`, `max_tokens`, `max_completion_tokens`). This is the host's explicit, namespaced choice — not silently mixed with normal kwargs — so v1 does **not** reject it. But because shadowing `messages` would be nasty to debug, the constructor emits a **one-time** warning (not a per-request check — that would spam the hot path) if `extra_body` shares any key with that structural/managed set.

### 3.4 Interaction with the existing one-shot latches

`chat()`/`chat_stream()` keep their in-`except` fix-ups verbatim:
- `max_tokens` → `max_completion_tokens` rename on provider error.
- temperature-rejected → set `omit_temperature` and retry.

These mutate the top-level `kwargs`; `extra_body` is a separate nested object the latches never touch, so they cannot collide. (If a host *deliberately* puts `temperature`/`max_tokens` inside `extra_body`, the §3.3 warning fires and the SDK's last-wins body merge applies — the host's choice.)

## 4. Constructor signature & wiring (two construction paths)

Same shape as `temperature` / `max_tokens`:

| Path | Change |
|---|---|
| **Embedded host** | `Agentao(..., extra_body={"reasoning_effort": "high"})` → thread into `_build_llm_client` `llm_kwargs` (`agent.py:665-676`), mirroring the existing `temperature` / `max_tokens` conditionals. **Plus the guard in §4.1.** |
| **CLI / env** | `discover_llm_kwargs()` (`factory.py:57`) reads `LLM_EXTRA_BODY` as a JSON **object**, parsed inside a `try/except`: malformed JSON → `warn + skip`. **It must also reject valid-but-non-object JSON** — `LLM_EXTRA_BODY=[]` / `"x"` / `3` parse fine but are invalid config; require `isinstance(parsed, dict)` and treat a non-object the same as malformed (warn + skip), so the **env-warning policy** governs the env path rather than a confusing downstream `TypeError` at construction (§3.1). **Note:** this is *intentionally more tolerant* than the existing `LLM_TEMPERATURE` / `LLM_MAX_TOKENS`, which call `float()` / `int()` directly and **raise** on malformed values (`factory.py:79-82`); `build_from_environment` only dodges that today by skipping discovery entirely when `llm_client` is supplied (`factory.py:134`). The `try/except` + `isinstance` check must be added explicitly — neither is inherited from a pre-existing tolerance. |

### 4.1 Constructor mutual-exclusion guard (required)

`extra_body` must be added to the raw-LLM-config set in the constructor's mutual-exclusion guard (`agent.py:284`), which today only lists `(api_key, base_url, model, temperature, max_tokens)`:

```python
if llm_client is not None and any(
    v is not None for v in (api_key, base_url, model, temperature, max_tokens, extra_body)
):
    raise ValueError("Agentao(): pass either llm_client= or "
                     "api_key/.../extra_body, not both.")
```

**Why this is mandatory, not cosmetic:** `_resolve_llm_client()` returns an injected `llm_client` immediately and untouched (`agent.py:655`). If the implementer only follows the "thread into `_build_llm_client`" note, then `Agentao(llm_client=client, extra_body={...})` is a **silent no-op** — the build path never runs. A host that injects its own `LLMClient` must pass `extra_body=` to *that* client directly; the guard makes the mistake loud instead of silent, consistent with "a fully-constructed object always wins over its raw-config sibling" (`agent.py:280`).

## 5. `reconfigure()` / model-switch semantics

`reconfigure()` (`client.py:251`) **preserves `self.extra_body`** — it is instance-level host config, not a model-detected quirk (those are the latches reset in `reset_capability_latches()`).

**Documented caveat — no auto-recovery (honest about the asymmetry):** unlike `temperature`, which **auto-recovers** via the one-shot `omit_temperature` latch when a model rejects it, a stale `extra_body` field (e.g. `reasoning_effort` after switching to a non-reasoning model) has **no latch** — *every* subsequent call hard-fails with a provider 400 until the host clears it. So this does **not** fully match the temperature precedent; it is strictly less forgiving. v1 makes it the **host's responsibility** to drop model-specific `extra_body` keys on switch. A future enhancement could add a "drop-on-reject" latch for `extra_body` keys, mirroring `omit_temperature` — out of v1 scope (gap≠need), but called out so the severity is not understated.

## 6. Precedence summary

1. Structural/managed body fields (`model`, `messages`, `stream`, `stream_options`, `tools`, `tool_choice`, `temperature`, `max_tokens`/`max_completion_tokens`) are set by the client from the normal request build.
2. `extra_body` is forwarded as a separate `.create()` argument; the SDK merges it **into the body**. On a key conflict the SDK's body merge is last-wins (`extra_body` shadows the client's field) — flagged once at construction (§3.3), never silently mixed into top-level kwargs.

## 7. Edge cases

- **Logging (explicit v1 change — not free, must redact)**: `_log_request` (`agentao/llm/_logging.py:23`) logs a **fixed field set** — `model`, `temperature`, `max_tokens`, `messages`, `tools` — *not* arbitrary request kwargs, so `extra_body` does **not** appear automatically. v1 logs `kwargs.get("extra_body")` as a single dedicated field (no "subtract a reserved set" guessing — it is one known key), with values **recursively redacted**: replace the value of any key whose name (lower-cased) is exactly one of `authorization`, `api_key`, `apikey`, `api-key`, `token`, `access_token`, `secret`, `password`, `cookie` — at any nesting depth — with `***` before logging. **Exact key-name match, not substring**, so a benign `max_tokens`-style or `*_tokens` key is not over-redacted. Rationale: `extra_body` can nest provider credentials (some gateways accept an API key in the body), and the current logger deliberately keeps `api_key` out of the log; logging `extra_body` raw would reintroduce that leak. The redactor is a small recursive helper in `_logging.py`; tested for a nested credential key (§9).
- **Back-compat**: empty/omitted `extra_body` is not added to `kwargs`, so request kwargs and logs are byte-identical to today; existing tests untouched.
- **Type safety**: a non-dict `extra_body` raises `TypeError` at construction via the **explicit** `isinstance` guard in §3.1 — *not* via `dict(extra_body or {})` alone, which would accept a list-of-pairs and raise `ValueError` on other shapes.

## 8. Deferred

Three surfaces are intentionally out of v1 scope:

- **`extra_headers` passthrough.** Same threading as `extra_body`, but headers are the **credential vector** (`Authorization`, `x-api-key`, gateway routing tokens) and need a deliberate logging policy (log header **names only**, never values). Finding "B" is body params, fully served by `extra_body`. Add `extra_headers` when a concrete gateway/auth need appears, and design its redaction then.
- **`settings.json :: llm.extra_body` file layer.** No LLM-config block exists in `settings.json` today (`_load_settings` at `factory.py:37` feeds runtime mode + builtin agents only) and no "env > settings" LLM precedence to extend. Adding one is a *broader* decision — it would establish `settings.json` as a general LLM-config layer (model / temperature / max_tokens could reasonably follow). Defer until a concrete "CLI user wants persistent `extra_body` in a file" need appears, and design the whole LLM-settings block then. (Same posture as `host-tool-injection.md` deferring `tool_options`/settings.)
- **Runtime mutation (`/param`).** A setter — `LLMClient.update_extra_body(**kw)` plus a CLI `/param set seed 42` / `/param show` — is out of v1. The use cases (`reasoning_effort`, `top_p`, `seed`, `response_format`) are static per session and fully served by the construction-time paths.

## 9. Test plan

- `extra_body` is forwarded to both `chat()` and `chat_stream()` — spy the **correct** SDK method per path: `with_raw_response.create` (non-streaming, `client.py:339`) and `create` (streaming, `client.py:613`); assert the call received `extra_body=<the dict>`.
- **Back-compat:** empty/omitted `extra_body` → `extra_body` key absent from the `.create()` call; request kwargs identical to current (golden-dict assertion).
- **Structural-overlap warn (§3.3):** `extra_body={"messages": [...]}` → one warning at construction; **no** per-request warning (assert the warning fires once, not per call).
- **Type guard (§3.1):** `LLMClient(extra_body=[("x", 1)])` raises `TypeError`; `extra_body=None` is accepted.
- **Constructor guard (§4.1):** `Agentao(llm_client=<client>, extra_body={...})` raises `ValueError` — not a silent no-op.
- **Env tolerance:** `discover_llm_kwargs()` parses a valid `LLM_EXTRA_BODY` JSON object; malformed JSON → key omitted + warned, no raise.
- **Env non-object:** `LLM_EXTRA_BODY=[]` / `"x"` / `3` (valid JSON, non-object) → key omitted + warned via the env policy, **not** a construction-time `TypeError`.
- **Logging redaction:** with `extra_body={"reasoning_effort":"high","api_key":"sk-x"}` (and a nested case), `_log_request` shows `reasoning_effort` but the credential value is `***`; a benign `*_tokens`-style key is **not** redacted; with no `extra_body` the line is absent.
- **reconfigure():** preserves `extra_body`; `reset_capability_latches()` still clears the latches.

## 10. Change sites / blast radius

| File | Change |
|---|---|
| `agentao/llm/client.py` | add `extra_body` field + explicit `isinstance` guard (§3.1) + construction-time structural-overlap warn (§3.3); add the one `kwargs["extra_body"]` line via `_build_request_kwargs` (§3.2); preserve in `reconfigure()` |
| `agentao/llm/_logging.py` | log `kwargs.get("extra_body")` in `_log_request`, **values recursively redacted** (exact key-name match) for sensitive keys (§7) |
| `agentao/agent.py` | add `extra_body` kwarg; thread into `_build_llm_client`; **add to the mutual-exclusion guard** (§4.1) |
| `agentao/embedding/factory.py` | parse `LLM_EXTRA_BODY` JSON in a `try/except` + `isinstance(parsed, dict)` (warn + skip on malformed **or** non-object) |
| `docs/reference/configuration.md` | document the `LLM_EXTRA_BODY` env var (§2) — **not** a settings field (deferred, §8) |
| `tests/test_llm_client_*.py` | new coverage per §9 |

Net: no `_RESERVED_PARAMS` set, no per-request collision/warn loop, no `_consume_stream` signature change — the feature is the `extra_body` field plus one line in the (de-duplicated) request builder. The `_build_request_kwargs` extraction **removes** the duplicated closed dict — a simplification, not just an addition.
