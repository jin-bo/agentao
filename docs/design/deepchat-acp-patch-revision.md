# DeepChat ACP Integration Patch — Revision Plan

**Status:** Design record. Drafted 2026-05-29. Implementation in progress —
PR-1/PR-2/PR-3 and the core ACP rework PR-4/PR-5/PR-6 (B1/B2/B3) all landed
(see PR sequencing); PR-7 (retire legacy model methods) deferred to a later
release.
**Audience:** Agentao maintainers; the DeepChat/TensorChat integration fork owner.
**Related docs:** `docs/design/embedded-host-contract.md`,
`docs/architecture/embedding-vs-acp.md` (if present),
`agentao/embedding/factory.py`, `agentao/acp/`.

## Scope (read this first)

A ~405 KB local-changes patch (`agentaolocal-changes.patch`, 56 files,
+4448 / −6153) wires Agentao up as an **ACP subprocess backend for a
DeepChat / TensorChat (Electron) desktop chat UI**. This document is the
triage + revision plan for that patch: what to upstream into Agentao
core, what to rework, what to keep in the fork, and what to reject.

The patch is **not** a single feature. It bundles four unrelated
concerns: (1) genuine harness capabilities, (2) one wrongly-exposed
capability, (3) DeepChat-only glue and packaging, and (4) repo
deletions that regress current `main`. The plan separates them.

**This plan's actionable scope on agentao `main` is the A-series, the
B-series, and D1.** The disposition table below is *patch triage*, not a
main work-list: only the A groups (upstream), the B groups (rework +
upstream), and **D1 (restore the deleted `acp_client` tests)** are
`main` actions. The C groups and D3 are **fork-keep / drop**
verdicts — they describe what does *not* come into `main`, and the
fork-side ones are collected as **advisory recommendations** under
*Suggestions for the DeepChat fork*. The core redesign and the PR
sequencing are the actual `main` work; this plan does **not** implement
the fork-side items. agentao `main` only ships the seams the fork
consumes.

The single load-bearing design decision is **credential handling for
runtime provider/model switching**: the patch sends `apiKey` / `baseUrl`
over the ACP wire (and into `agentao.log`). This document replaces that
with the ACP-standard `session/set_config_option` mechanism plus a
server-side, host-injectable `provider_resolver`.

## Background — what the patch does

| Theme | Files (representative) |
|---|---|
| ACP handlers grow a DeepChat wire shape (`modelId`, `_meta`, `apiKey`, `baseUrl`) | `agentao/acp/session_set_model.py`, `session_set_mode.py`, `models.py`, `server.py`, `transport.py` |
| Multimodal (image) input through the turn | `agentao/agent.py`, `agentao/runtime/turn.py`, `agentao/runtime/chat_loop/_runner.py`, `agentao/llm/client.py` |
| Structured `ask_user` (options/header/multiple/custom) | `agentao/tools/ask_user.py`, `agentao/tools/base.py` |
| Second, parallel ACP transport implementation | `agentao/transport/acp.py` (new), `transport/acp_server.py` (new) |
| PyInstaller binary packaging, 6-platform CI | `run.py`, `pyinstaller.spec`, `scripts/build_binaries.sh`, `.github/workflows/build-matrix.yml` |
| `$HOME`-based path resolution | `agentao/paths.py` + callers |
| Deletions | `tests/test_acp_client_*.py`, `docs/dev-notes/*` |

## Verified findings (boundary analysis)

All claims below were grep-verified against the working tree and the ACP
spec (`agentclientprotocol.com`), not asserted from memory. See the
Appendix for the evidence.

1. **`reconfigure()` already exists in core** (`agentao/llm/client.py:233`,
   `reconfigure(api_key, base_url, model)`). Runtime provider switching is
   already a harness primitive; the patch only *calls* it from a new wire
   shape. No new capability is needed — only a safe way to trigger it.

2. **Server-side credential resolution already exists**
   (`agentao/embedding/factory.py:71-77`): `LLM_PROVIDER` selects a
   provider, then `{PROVIDER}_API_KEY` / `_BASE_URL` / `_MODEL` are read
   from the environment. Secrets already resolve server-side at
   construction time. The wire has never needed to carry a key.

3. **ACP has no model-selection method.** Verified against the spec:
   there is no `session/set_model`, `session/select_model`, or
   `session/list_models`. Model selection is handled by the generic
   **`session/set_config_option`** (with `category: "model"`) or by
   agent-internal mechanisms. Agentao's existing `session/set_model` /
   `session/list_models` are therefore **already non-standard
   extensions** sitting in the bare `session/` namespace — inconsistent
   with `ask_user`, which correctly uses the vendor prefix
   `_agentao.cn/ask_user`.

4. **`session/set_mode` with `modeId` *is* the ACP standard.** The spec's
   field is `modeId` (not `mode`), with `availableModes` /
   `currentModeId`. The patch's `modeId` change actually moves *toward*
   the standard; Agentao's pre-patch `mode` field was the non-standard
   one.

5. **`agentao/acp_client/` and its tests are live in `main`.** The
   patch's deletion of `tests/test_acp_client_*` is a regression against
   current coverage, not cleanup.

6. **`agentao/transport/` has no ACP implementation today.** The new
   `transport/acp.py` + `acp_server.py` are a *second*, parallel ACP impl
   duplicating the complete `agentao/acp/` server package — fork debt.

7. **Full private-schema inventory on `main`.** An audit of
   `agentao/acp/` against the spec found these non-standard surfaces
   *already in `main`* (independent of the patch):

   | Private surface | schema | Verdict |
   |---|---|---|
   | `session/set_model` | `AcpSessionSetModelRequest/Response` (`model`/`contextLength`/`maxTokens`) | Non-standard method in the **bare `session/` namespace** — masquerades as standard. |
   | `session/list_models` | `AcpSessionListModelsRequest/Response`, `AcpModelInfo` (`extra="allow"`) | Non-standard method, same namespace problem. |
   | `_agentao.cn/ask_user` | `AcpAskUserParams`, `AcpAskUserAnswered/Cancelled` | Non-standard but **correctly vendor-prefixed** and advertised in `initialize` — the model citizen. |
   | `initialize` response `extensions: [...]` | `AcpInitializeExtension` | **Non-standard top-level field.** ACP advertises extensions through **`_meta`**, not a top-level `extensions` array. |
   | `session/set_mode` `mode` field | `AcpSessionSetModeRequest` (`mode: Literal["read-only","workspace-write","full-access","plan"]`) | **Two deviations:** the ACP field is `modeId` (not `mode`), and the value is wrongly pinned to Agentao **permission presets**, conflating ACP's "mode" (a UI/behavioural selector) with permission posture. |

   For contrast, these are confirmed **standard** and need no change:
   `protocolVersion`, `agentCapabilities` (`loadSession` /
   `promptCapabilities` / `mcpCapabilities`), `agentInfo`
   (`name`/`title`/`version`), `authMethods`, all `session/update`
   variants (`agent_message_chunk`, `agent_thought_chunk`, `tool_call`,
   `tool_call_update`, `user_message_chunk`), the `stopReason` enum
   (`end_turn` / `cancelled` / `max_turn_requests` / `refusal`), and
   `session/new` · `prompt` · `cancel` · `load` · `request_permission`.

## Disposition

Legend: ✅ upstream · 🔧 rework then upstream · 🟠 keep in fork · ❌ drop ·
🚫 reject (regression)

| Group | Disposition | Action |
|---|---|---|
| **A1 — Multimodal image input** (`agent.py`, `runtime/turn.py`, `runtime/chat_loop/_runner.py`, `llm/client.py` logging, `cli/display.py`, `tests/test_logging.py`) | ✅ | Extract as a standalone PR. Image data arrives as standard ACP content blocks, so it is already decoupled from DeepChat. The logging change (summarize multimodal parts instead of dumping base64) ships with it. |
| **A2 — Structured `ask_user`** (`tools/ask_user.py`, `tools/base.py`, `cli/app.py`) | ✅ | Upstream (Decision #1), but the callback contract must stay **backward-compatible**: `ask_user_callback` is a deprecated 1-arg `Callable[[str], str]` (`agent.py:52`), so adding `options`/`header`/`multiple` naively `TypeError`s embedded hosts passing `lambda q: ...`. Keep the 1-arg form working (variadic / new optional structured callback), host-agnostic shape (not DeepChat option-cards). Add a unit test. |
| **A3 — `$HOME` path robustness** (`paths.py` + `memory/storage.py`, `skills/manager.py`, `llm/client.py` fallback, `tests/test_memory_store.py`) | ✅ | Small PR. Confirm the fallback when `$HOME` is unset. |
| **B1 — Secret-wire fix (PR-4, core)** (`acp/session_set_model.py`, `models.py`, `server.py`, `transport.py`, `initialize.py`, `schema.py`, `session_new.py`, `test_acp_set_model.py`) | ✅ done (`#56`) | **Drop** `apiKey`/`baseUrl`/`modelId`/`_meta`. Add `session/set_config_option` (`configId="model"` only; single `category:"model"` option, `provider/model` value) + injectable `provider_resolver` (server-side secret; **handler whitelist + `extra="forbid"` rejects `apiKey`/`baseUrl`/`_meta`**). **Add `_agentao.cn/set_model`** (`{sessionId, model}`, free-form, secret-free; shares the core code path — Decision #4) and **keep the existing `session/set_model` unchanged** as a one-release compatibility alias — its current shape `{sessionId, model?, contextLength?, maxTokens?}` is already `extra="forbid"` and secret-free; PR-4 simply **does not adopt the patch's `modelId`/`apiKey`/`baseUrl`/`_meta` additions** to it (CHANGELOG-deprecate; retires with `list_models` in PR-7). Default catalog = the **single current env** `provider/model` (model from live `agent.llm.model`); richer catalog host-injected. **Keep `session/list_models` as a compatibility endpoint** in this PR. See "Core redesign". |
| **B2 — `session/set_mode` field (PR-5, separate)** (`acp/session_set_mode.py`, `schema.py`, `test_acp_set_mode.py`) | ✅ done (`#57`) | Minimal: rename `mode` → **`modeId`** and **accept unknown values** (always persist; map to a preset only on match) — so DeepChat's `code`/`ask` aren't rejected. **Deferred** (Decision #6 — decoupling is a large refactor): the permission-axis split *and* `availableModes`/`currentModeId` + `current_mode_update`. Not in the model/provider PR. |
| **B3 — `initialize` `extensions` array → `_meta` (PR-6, low priority)** (`acp/initialize.py`, `acp/schema.py`) | ✅ done (`#58`) | **Decision #5: move under `_meta`** (spec-clean). agentao's own client doesn't read `extensions`; only the schema snapshot + `test_acp_schema.py` change. Its own small PR; **not** bundled into the secret-wire fix; schedule last. Snapshot bump (`docs/schema/host.acp.v1.json`). |
| **B4 — Retire the legacy model methods (PR-7, later)** | 🔧 | After a host consumes the standard `configOptions` path: remove **both** compatibility endpoints together — `session/list_models` **and** the `session/set_model` name alias (the canonical `_agentao.cn/set_model` stays). Direction is standard-alignment; staged across releases. |
| **C1 — Duplicate ACP transport** (`transport/acp.py`, `transport/acp_server.py`, `transport/__init__.py`, `transport/sdk.py`) | ❌ | Drop the whole group. `agentao/acp/` is already a complete server package. |
| **C2 — PyInstaller packaging** (`run.py`, `pyinstaller.spec`, `scripts/build_binaries.sh`, `.github/workflows/build-matrix.yml`, `pyproject.toml`) | 🟠 | Keep in the DeepChat fork. It conflicts with Agentao's embedded-library positioning (`pip install agentao`). Upstream only if the project decides to ship binaries — a separate product decision. |
| **C3 — skill-creator HTML drift** (`skills/skill-creator/assets/eval_review.html`, `eval-viewer/viewer.html`) | ❌ | Unrelated front-end drift (no ACP/provider references). Remove from the patch. |
| **D1 — `tests/test_acp_client_*` deletions** | 🚫 | Reject. `acp_client` is live in `main`; restore the tests. |
| **D2 — `docs/dev-notes/*` deletions** | ⚪ | Neutral. If intended, do it as a separate housekeeping PR, not bundled with features. |
| **D3 — New Chinese fork notes** (`docs/agentao/*.md`) | ⚪ | Keep in the fork; do not upstream. |

## Core redesign — provider/model switching over ACP

### Decision

The ACP wire carries an **identifier**, never a secret. Credentials are
resolved server-side through a host-injectable `provider_resolver`. This
is **not** guaranteed by the protocol — JSON-RPC params can carry extra
fields — so it is an **implementation requirement**: the
`set_config_option` handler parses *only* `configId` / `value`, the
request schema is `extra="forbid"`, and any `apiKey` / `baseUrl` /
`_meta.*` credential field is rejected, not honoured.

### Single `model` option with a `provider/model` value

One config option, not two. The `value` encodes both axes as
`provider/model`. This is chosen for **atomicity and simplicity**, not
for standards-compliance: at the wire level, both one combined option and
two separate `provider` + `model` options are equally ACP-compliant (ACP
leaves `value` an opaque agent-defined string and permits multiple
options). The merge wins because one `set_config_option` call resolves to
exactly one valid `(provider, model)` pair and one atomic
`reconfigure()` — no invalid intermediate state, no cross-option
dependency needing a `config_option_update` round-trip just to re-filter
models after a provider change. A *weak* conceptual nudge also points the
same way: ACP's `category` enum is `{mode, model, thought_level}` — it has
`model` but **no `provider`**, so the spec's mental model is "pick a
model," not "pick a provider then a model."

`provider/model` is an **Agentao value convention, not an ACP standard** —
document it as such on the option.

### Wire shape

`session/new` (and `session/load`) advertise the option:

```json
{
  "configOptions": [
    { "id": "model", "name": "Model", "category": "model", "type": "select",
      "currentValue": "anthropic/claude-opus-4",
      "options": [
        { "value": "openai/gpt-4o",           "name": "GPT-4o" },
        { "value": "anthropic/claude-opus-4", "name": "Claude Opus 4" },
        { "value": "azure-openai/gpt-4o",     "name": "GPT-4o (Azure)" }
      ] }
  ]
}
```

Client switches (one atomic call):

```json
{ "sessionId": "s1", "configId": "model", "value": "openai/gpt-4o" }
```

### Handler sketch

```python
def handle_set_config_option(server, params):
    session = require_active_session(server, params, METHOD_SET_CONFIG_OPTION)
    if params["configId"] != "model":
        raise JsonRpcHandlerError(INVALID_REQUEST, f"unknown configId {params['configId']!r}")
    # schema is extra="forbid"; only configId/value reach here — no apiKey/_meta.
    value = params["value"]                         # "openai/gpt-4o"
    provider_id, _, model_id = value.partition("/")  # split on the FIRST "/"
    with hold_idle_turn_lock(session, METHOD_SET_CONFIG_OPTION):
        if model_id:                                 # provider/model form
            creds = server.provider_resolver(provider_id)   # server-side secret
            session.agent.llm.reconfigure(
                api_key=creds["api_key"], base_url=creds.get("base_url"),
                model=model_id)                      # one atomic switch
        else:                                        # bare value, no provider prefix
            session.agent.set_model(value)           # model-only, keep provider
        return {"configOptions": _current_config_state(session)}
```

Three value rules:
1. **Split on the first `/`** (`partition`, not `split`). Provider ids are
   slash-free; model ids are not (`huggingface/meta-llama/Llama-3` →
   provider `huggingface`, model `meta-llama/Llama-3`).
2. **Bare value (no `/`)** = model-only, keep the current provider —
   preserves the "switch model without re-stating the endpoint" case.
3. **Same model on different endpoints** = distinct entries
   (`openai/gpt-4o` vs `azure-openai/gpt-4o`). This is how "switch
   provider, same model name" is modelled — no separate provider axis.

### `provider_resolver` seam — and where the catalog comes from

Two separate concerns, do not conflate them:

- **Credential resolution** — `provider_resolver(provider_id) -> {"api_key",
  "base_url"}`. Two paths only: **host-injected** resolver, or — when none
  is injected — the **default**, which resolves the **single current**
  provider from the existing `factory.py` env (`LLM_PROVIDER` + its
  `{PROVIDER}_*` vars). The default accepts **only** `provider_id ==
  LLM_PROVIDER`; **any other `provider_id` → `INVALID_REQUEST`**. It does
  **not** scan the environment for a provider list and it does **not**
  fabricate a `{PROVIDER}_*` lookup for an arbitrary id — that would be the
  "guess the provider list" trap. Multi-provider switching requires a
  host-injected resolver (paired with a host-injected catalog).
- **Model catalog** — the `options` advertised in the `configOptions`
  `model` option. The **default catalog is a single entry**: provider from
  `LLM_PROVIDER`, model from the **live `agent.llm.model`** (the value
  resolved at construction) — **not** a re-read of `{PROVIDER}_MODEL`, so a
  missing `{PROVIDER}_MODEL` env var does not block advertising the current
  model. Agentao is **single-provider today** (`LLM_PROVIDER` selects one at
  construction; there is no `providers.json` and no registry), so a default
  agent honestly advertises exactly one option, and `set_config_option` is a
  no-op switch until a host enriches it. A **multi-entry catalog must be
  host-injected** — the implementation must **never scan env or guess the
  provider list**.
- **No `.agentao/providers.json`.** A new secret-at-rest config format is
  orthogonal to "keep secrets off the wire" and only widens the attack
  surface. A host wanting a richer secret store (e.g. the OS keychain) or a
  multi-provider catalog injects its own resolver / catalog — that is
  exactly what these seams are for.
- Both seams are host-injectable, consistent with the embedded-harness
  principle: capability injection, no globals, host override.
  **Initial implementation: constructor kwargs only** — no new host
  protocol is required; a broader host-contract surface can follow later
  if a host actually needs it.

### Retiring the legacy model methods — direction, but staged

**Standard alignment is the direction**: ACP has no model methods, so
`session/list_models` / `session/set_model` should eventually retire in
favour of `set_config_option` + the standard `config_option_update`
notification. But the implementation is **staged across releases** — this
patch must not become a one-shot migration. Both legacy methods stage the
**same way** (compat endpoint now, remove together in PR-7); they are not
treated asymmetrically. The principle:

> Add the standard path first; keep the legacy methods as **thin
> compatibility endpoints** (no new logic); implement push refresh only at
> an existing trigger; remove the legacy methods in the next release after
> clients migrate.

Concretely:

1. **Add** `set_config_option(configId="model")` + advertise
   `configOptions` in `session/new` and `session/load`. This is the only
   new **standard** surface in the core PR; PR-4 also adds **one vendor
   compatibility method** — `_agentao.cn/set_model` — for free-form model
   entry (below), but no other standard method.
2. **Keep `session/list_models` as a compatibility endpoint** — it stays
   exactly as it is today (per-session cache + `warning` fallback). **Do
   not** rewrite it onto `config_option_update`, and **do not** add a
   delegating-shim layer yet. Likewise **keep the existing `session/set_model`
   unchanged** as a one-release alias: its current shape `{sessionId, model?,
   contextLength?, maxTokens?}` is already `extra="forbid"` and secret-free
   (the `model`/`contextLength`/`maxTokens` knobs stay — do **not** shrink it
   to `modelId`). PR-4's only job here is to **not adopt the patch's
   secret-bearing additions** (`modelId`/`apiKey`/`baseUrl`/`_meta`). A client
   that was sending those credential fields must re-shape regardless — that is
   the security fix. DeepChat's free-form "type any model" need is served by
   the new `_agentao.cn/set_model`, whose adapter maps DeepChat's UI `modelId`
   → the `model` field.
3. **Return, do not push, in PR-4.** A *successful* `set_config_option`
   switch returns the current `configOptions` state **in its response**.
   PR-4 emits **no** `session/update` / `config_option_update` notification
   — building a push *system* to replace a manual-refresh button is YAGNI,
   and an "echo push" only adds a notification-test surface. A real
   `config_option_update` push lands later, only at an existing refresh
   trigger.
4. **Retire both legacy methods in a later release** — `session/list_models`
   **and** the `session/set_model` alias, together — once a host actually
   consumes the standard `configOptions` path (the canonical
   `_agentao.cn/set_model` stays). Wire deprecation signal at that point =
   CHANGELOG note + then `-32601 method not found` (neither was advertised in
   `initialize`, so a Python `DeprecationWarning` is invisible to a wire
   client and meaningless).

This keeps the standard-alignment goal without turning the DeepChat
secret-wire fix into a big protocol migration.

### Free-form model entry: `_agentao.cn/set_model` (decided, Decision #4)

DeepChat sends a **free-form** model string (in the patch, a `modelId`
field, any non-empty string, no whitelist; passed straight to
`agent.set_model()` — verified). A `select`-only `set_config_option`
**cannot** express that, so the free-form path must survive. The decision
(Decision #4): add the vendor method **`_agentao.cn/set_model` with the
minimal payload `{sessionId, model}`** — free-form, secret-free (no
`apiKey`/`baseUrl`/`_meta`; provider unchanged, model-only switch). It
deliberately reuses the **`model`** field name (matching the core
`session/set_model` and the `set_config_option` value), so DeepChat's
adapter just maps its UI `modelId` → `model`; one field name across all
three surfaces, no `modelId` alias on the wire. The vendor prefix also
fixes finding-#7's sin (a non-standard method in the bare `session/`
namespace), consistent with `_agentao.cn/ask_user`.

Two coexisting model-set paths, by design:
- **`session/set_config_option(model)`** — standard, `select` from the
  advertised catalog (carries `provider/model`; can switch provider via the
  resolver). For spec-following clients.
- **`_agentao.cn/set_model`** — vendor, `{sessionId, model}` free-form
  string, model-only (keep provider). For DeepChat's "type any model" UX.

Plus the unchanged **`session/set_model`** (`{sessionId, model?,
contextLength?, maxTokens?}`) as a one-release compatibility alias.

All three **share one core code path** (`reconfigure` / core
`set_model()`) — no logic fork, or the entries drift. DeepChat needs the
free-form vendor method to keep working, so the vendor method (and the
shared core) land in **PR-4**, not later; the alias is simply the
status-quo method left in place.

## PR sequencing

All of the following land in agentao `main`. Fork-side items
(PyInstaller, the duplicate transport, fork notes, DeepChat client
adaptation) are **not** numbered PRs here — they are collected under
*Suggestions for the DeepChat fork*, below.

**Prerequisite (not a numbered PR) — restore / refuse deletion of the
`acp_client` tests** (D1). ✅ **Done** — verified the `tests/test_acp_client_*`
suite is live on `main`; the patch's deletion was never applied, so the
regression guard was already in place before the extraction PRs landed.

1. **PR-1 — Multimodal image input** (A1). ✅ **Done** — merged in `#53`
   (`feat(multimodal): image input across engine, ACP, and CLI`).
   Self-contained, clearly upstreamable; landed first.
2. **PR-2 — Structured `ask_user`** (A2). ✅ **Done** — merged in `#54`
   (squash `4292e4a`). Backward-compatible callbacks confirmed: the
   structured hints (`header`/`options`/`multiple`/`allow_custom`) are
   forwarded only to callbacks whose signature accepts them (via the
   shared `invoke_ask_user_callback` introspection helper), so legacy
   1-arg `Callable[[str], str]` callbacks — the deprecated
   `ask_user_callback` ctor arg, `SdkTransport(ask_user=...)`, a directly
   constructed `AskUserTool`, and 1-arg replay inner transports — keep
   working. Tested (`tests/test_ask_user_structured.py`).
3. **PR-3 — `$HOME` path robustness** (A3). ✅ **Done** — merged in `#55`
   (squash `0b8b4f4`). Added `agentao.paths.user_home()` and routed the
   scattered `Path.home()` sites through it; the no-home fallback is a
   private, per-user, ownership-validated temp dir (cached per process).
   Tested (`tests/test_paths.py`).
4. **PR-4 — Minimal core ACP model-switching fix** (B1). ✅ **Done** —
   merged in `#56` (squash `c4fee7e`). The core provider/model surface and
   nothing beyond it:
   - Reject the patch's `apiKey`/`baseUrl`/`modelId`/`_meta` additions.
   - Add `session/set_config_option` for `configId="model"` only
     (`provider/model` value; bare value keeps current provider).
   - **Add `_agentao.cn/set_model`** (`{sessionId, model}`, free-form,
     secret-free) — the vendor free-form path; shares the core code path.
     **Leave the existing `session/set_model` unchanged** as a one-release
     compatibility alias (`{sessionId, model?, contextLength?, maxTokens?}`,
     already `extra="forbid"`/secret-free; retires with `list_models` in
     PR-7) — do **not** shrink it to `modelId`.
   - Server-side `provider_resolver`; reject `apiKey`/`baseUrl`/`_meta` via
     handler whitelist + `extra="forbid"`. Default resolver accepts **only**
     the current `LLM_PROVIDER`; any other `provider_id` → `INVALID_REQUEST`.
   - `session/new` **and `session/load`** advertise `configOptions` with the
     **single current env** `provider/model`; richer catalog is
     host-injected.
   - Return the current `configOptions` in the `set_config_option`
     **response only** — **no** `config_option_update` notification in this
     PR.
   - **Keep `session/list_models` as a compatibility endpoint** — no
     rewrite, no removal in this PR.
5. **PR-5 — `set_mode` field fix** (B2, minimal). ✅ **Done** — merged in
   `#57` (squash `e1f0283`). `mode` → `modeId`, accept unknown values (so
   `code`/`ask` aren't rejected; persisted on the session and echoed back).
   Permission-axis split + `current_mode_update` deferred to their own
   design.
6. **PR-6 — `initialize.extensions` → `_meta`** (B3). ✅ **Done** — merged
   in `#58` (squash `005a77e`). Moved the array under
   `_meta["_agentao.cn/extensions"]` (vendor-namespaced); dropped the
   top-level `extensions` field; regen'd the schema snapshot. Low priority.
7. **PR-7 (later release) — retire the legacy model methods** once a host
   consumes the standard `configOptions` path: remove `session/list_models`
   **and** the `session/set_model` name alias together. The canonical
   `_agentao.cn/set_model` (PR-4) stays.
8. **Cleanup — `docs/dev-notes`** (D2). Separate housekeeping PR, if the
   deletion is intended at all. (The `acp_client` test restore is **not**
   here — it is the prerequisite above.)

## Suggestions for the DeepChat fork (advisory — outside `main` scope)

The items below are **recommendations for the DeepChat / TensorChat fork
owner**, not work this plan implements. They sit on the fork side of the
embedded-harness boundary; agentao `main` only provides the seams they
consume (the `provider_resolver` / catalog injection points, the
secret-free wire, and the vendor `_agentao.cn/set_model` method that PR-4
ships).

> **This section is non-normative for agentao `main`.** Do not create
> upstream PRs from these bullets unless an item is explicitly promoted
> into the A/B/D1 scope later.

- **Keep PyInstaller packaging & 6-platform CI on the fork** (C2). It
  conflicts with agentao's library-first positioning (`pip install
  agentao`); upstream only if the project later makes a separate decision
  to ship binaries.
- **Keep the Chinese fork notes on the fork** (`docs/agentao/*.md`, D3).
  Not upstreamed.
- **Drop the duplicate ACP transport** (C1). Target `agentao/acp/`
  directly instead of carrying a second `transport/acp*.py` server; the
  duplicate is fork debt with no upstream path.
- **Drop the skill-creator HTML drift** (C3). Unrelated front-end change;
  don't carry it on the integration branch.
- **Adapt the model-switch UI to the secret-free wire.** Map the fork's UI
  `modelId` → the wire **`model`** field; never put `apiKey` / `baseUrl` /
  `_meta` on the wire (agentao rejects them). Credentials resolve
  server-side via `provider_resolver`.
  - Free-form "type any model" → call the vendor `_agentao.cn/set_model`
    (`{sessionId, model}`).
  - Catalog-driven selection → call the standard
    `session/set_config_option` (`configId="model"`, value
    `provider/model`).
- **Migrate off the legacy methods.** Once the fork consumes the standard
  `configOptions` path, agentao retires `session/list_models` and the
  `session/set_model` alias (PR-7). That retirement is **gated on this
  migration** — the fork's move is the trigger.
- **Inject a multi-provider resolver + catalog if you need real provider
  switching.** agentao's default is single-provider (current
  `LLM_PROVIDER`). A fork wanting multiple providers injects its own
  `provider_resolver` + catalog through the **constructor kwargs /
  injection seam** (no new host protocol to design) — no
  `providers.json`, no env-scanning on the agentao side.

## Decisions (resolved)

All six were researched with cited evidence (see Appendix). Resolutions:

1. **Upstream target → A1/A2/A3 and the B-series core fixes go to
   `agentao/main`; fork packaging (C2) and fork notes (D3) stay out.**
   This is *not* "upstream the whole patch" — only the harness-capability
   and ACP-correctness groups land in `main`. A1 (image input)
   and A3 (`$HOME`) are LOW-risk: `add_message` widening to
   `Union[str, List]` is backward-compatible (`agent.py:724`,
   `messages: List[Dict]` at `:259`), host contract doesn't expose the
   messages list, and the `$HOME` fallback already exists
   (`llm/client.py:211`). A2 (structured `ask_user`) upstreams **only with
   a backward-compatible callback** — `ask_user_callback` is a deprecated
   1-arg callback (`agent.py:52`), so naive new params break embedded
   hosts. That constraint is in PR-2, not a blocker to the decision.
2. **PyInstaller → keep in fork.** Conflicts with the library-first
   positioning (single console script `pyproject.toml:63`; `CLAUDE.md:51`
   "embedded harness"; `pip install agentao` = library). Shipping binaries
   is a separate product decision; not bundled here.
3. **Retirement → `session/list_models` and the `session/set_model` alias
   retire together (PR-7).** Zero internal blockers: agentao's own
   `acp_client` does **not** call these wire methods, and the CLI `/model` /
   `/sessions resume` use the **core** `agent.set_model()` /
   `list_available_models()` (`cli/commands/provider.py:87,120`), not the
   wire methods. PR-4 adds the vendor `_agentao.cn/set_model` (Decision #4);
   the existing `session/set_model` stays **unchanged** one release as a
   compatibility alias so existing `session/set_model` callers are not broken,
   then retires alongside `list_models`. Retirement is gated only on DeepChat
   migrating to `configOptions`.
4. **Free-form model entry → YES, via `_agentao.cn/set_model`.** DeepChat
   sends a free-form model string (no whitelist; patch passes it straight to
   `agent.set_model()`), which a `select`-only `set_config_option` cannot
   express. Resolution: add the vendor method `_agentao.cn/set_model`
   (`{sessionId, model}`, secret-free), coexisting with the standard `select`
   path; the **existing** `session/set_model` (`{sessionId, model?,
   contextLength?, maxTokens?}`, already secret-free) stays **unchanged** one
   release as a compatibility alias. DeepChat's adapter maps its UI `modelId`
   → the `model` field — one field name, no `modelId` on the wire. Lands in
   PR-4 (DeepChat needs it to keep working). See "Free-form model entry"
   above.
5. **`initialize.extensions` → move under `_meta` (PR-6, low priority).**
   agentao's own `acp_client` doesn't read `extensions` (0 refs); only the
   schema snapshot (`docs/schema/host.acp.v1.json`) + `test_acp_schema.py`
   are affected. Small change (add `_meta`, regen snapshot). Non-blocking;
   schedule last.
6. **`set_mode` permission coupling → do NOT split now.** Decoupling is a
   **large refactor**: `_PRESET_RULES[mode.value]` lookup
   (`permissions.py:385`), rule-evaluation order keys off the mode
   (`:482,570-577`), sub-agent propagation passes the `PermissionMode` enum
   (`_wrapper.py:525`), CLI behaviour branches on it. This round: minimal
   `mode`→`modeId` + accept-unknown only (PR-5). Full axis split is a
   separate, deferred design — not required by DeepChat.

## Appendix — verification evidence

- `agentao/llm/client.py:233` — `def reconfigure(self, api_key, base_url=None, model=None)`.
- `agentao/embedding/factory.py:71-77` — `LLM_PROVIDER` + `{PROVIDER}_API_KEY/_BASE_URL/_MODEL`.
- `agentao/acp/protocol.py:47-61` — method constants; `_agentao.cn/ask_user` is the only vendor-prefixed one; `set_model`/`list_models` sit in the bare `session/` namespace.
- ACP spec (`agentclientprotocol.com`): no `session/set_model`; model selection via `session/set_config_option` (`{sessionId, configId, value}`), `ConfigOption{id,name,description,category,type,currentValue,options}`, `category ∈ {mode, model, thought_level}` (has `model`, **no `provider`**), `type` only `select`, `value` an opaque agent-defined string; `session/set_mode` field is `modeId` with `availableModes` / `currentModeId`; `initialize` response standard fields are `protocolVersion` / `agentCapabilities` / `agentInfo` / `authMethods` — **no top-level `extensions` array** (extensions go under `_meta`).
- ACP `session/update` variants include **`config_option_update`** (refreshed `configOptions`) and **`current_mode_update`** (`currentModeId`); `session/new` / `session/load` responses carry initial `configOptions` / `modes` — the standard dynamic-refresh path that supersedes `list_models`.
- No `providers.json` anywhere in `agentao/` or `docs/` (grep: 0 hits). `factory.py` is **single-provider**: `LLM_PROVIDER` selects one provider at construction; no multi-provider registry exists.
- `agentao/acp/schema.py:87-115` — `AcpInitializeExtension` + top-level `extensions` field on `AcpInitializeResponse`. `agentao/acp/initialize.py:140-145` — advertises `_agentao.cn/ask_user` in that array.
- `agentao/acp/schema.py:306-320` — `AcpSessionSetModeRequest.mode: Literal["read-only","workspace-write","full-access","plan"]` (non-standard `mode` field, pinned to permission presets).
- Working tree: `agentao/acp_client/` and `tests/test_acp_client_*.py` present on `main`; `agentao/transport/` has no `acp.py` / `acp_server.py`; `agentao/tools/ask_user.py` `execute(self, question)` is plain-text on `main`.
- Decision research (3 Explore agents, 2026-05-29):
  - **#3 consumers:** `agentao/acp_client/` has **0 refs** to `list_models`/`set_model`; CLI uses **core** `agent.set_model()`/`list_available_models()` (`cli/commands/provider.py:87,120`, `sessions.py:124`), not the wire methods. Only `tests/test_acp_session_set_model.py` + external clients affected.
  - **#4 free-form:** patch passes DeepChat's `modelId` straight to `agent.set_model()` with no whitelist (patch hunk `session_set_model.py` lines 538-539, 589-591); current `session_set_model.py:52` only checks non-empty. `select` cannot express this.
  - **#1 A2 risk:** `ask_user_callback` is one of the 8 deprecated constructor callbacks, typed `Callable[[str], str]` (`agent.py:52`); also surfaces via `transport/sdk.py:72-75`. Adding params breaks 1-arg host callbacks.
  - **#5 extensions:** `acp_client/` 0 refs to `extensions`; snapshot `docs/schema/host.acp.v1.json` + `tests/test_acp_schema.py:102-125` cover it; `AcpInitializeResponse` is `extra="forbid"` with no `_meta` field today.
  - **#6 mode coupling:** `_PRESET_RULES[mode.value]` (`permissions.py:385`), rule-order branches on `active_mode` (`:482,570-577`), sub-agent propagation passes `PermissionMode` enum (`agents/tools/_wrapper.py:525`), CLI branches on `current_mode` (`cli/input_loop.py`). Decoupling = large refactor.
