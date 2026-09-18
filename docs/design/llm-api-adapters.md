# Multi-wire-API support: `anthropic-messages`, `openai-responses` and `gemini-api`

**Status:** **Stage 0 implemented and shipped in 0.4.26. Stage 1 implemented
2026-09-18 (shipped in 0.5.0), against a scripted socket and then a live endpoint
(*Live results*, below); the provider-switch piece of stage 3 followed the same day. The rest of
stages 2–3 is proposed and not authorized. rev 15 (2026-09-18).** §2.3's
stage 0a and 0b are on `main`; stage 1 adds the adapter seam under `LLMClient`
and one second wire, `anthropic-messages`, selected at startup or by a provider
switch. §12.1 — whether
stage 0 makes the adapters unnecessary — was **not** answered by measurement
first: the maintainer authorized stage 1 with stage 0's billed comparison still
owed, and that debt carries over (see *What stage 1 landed*, last paragraph).
This document otherwise records the seam, the three options for where to put it,
the recommendation, the verified translation hazards inside agentao's own
history format, and the staged plan.

**What stage 0 landed** (`prompts/builder.py`, `agent.py`, `runtime/chat_loop/_runner.py`,
`runtime/llm_call.py`, `context_manager.py`, `llm/_cache_control.py`, `llm/client.py`,
`embedding/factory.py`; `tests/test_volatile_tail_request.py`,
`tests/test_prompt_cache_breakpoints.py`):

- **0a** — skills, active-skill bodies, todos, `<memory-context>` and the plan
  prompt moved out of the system message into one request-only `<system-reminder>`
  tail, appended at a single choke point (`_call_llm_with_overflow_recovery`'s
  `_send`, the only caller of `agent._llm_call`) so all seven assembly sites keep
  building the persistent prefix and none can lose or duplicate a tail. The
  Tier-1 anchor correction landed with it:
  `record_api_usage(prompt_tokens, len(persistent), tail_tokens=est(T))`, with the
  §11 drift gate as a test over 12 turns of deliberately varied tail size.
- **0b** — opt-in explicit `cache_control` (`LLM_PROMPT_CACHE=anthropic`,
  `prompt_cache=`), at most 3 breakpoints with the 4th slot reserved, copy-on-mark,
  applied in `_build_request_kwargs` (below replay) and per call (so the
  summarizer is not marked). Off by default: endpoint acceptance is still
  unverified, which is the one thing this design said must not be assumed.

**What stage 0's own gate still needs:** measured cache-hit rates and cost, 0a
against the pre-0a build and 0b against 0a, on a real endpoint. The local
measurement so far is structural, not billed: the system message is 2,350 tokens
and now byte-identical turn to turn, against a re-sent tail of ~1.8k tokens of
which 1,779 is the available-skills catalogue. That ratio is repo-specific and is
the number to check first — see §13's "Stop at stage 0".

**Follow-up, 0.4.27 — the catalogue went back into the prefix.** It was the
tail's dominant item and it did not need to be volatile: it was volatile only
because it listed the *inactive* skills, so each activation rewrote it. It now
lists every enabled skill, active or not, and sits in the system message ahead
of `<memory-stable>`; it changes only with the enabled set, an event that
already rewrites `activate_skill`'s enum in the tools block. pi-mono
(`system-prompt.ts`) and gemini-cli (`promptProvider.ts`) list the same way and
neither removes a skill once used; codex appends a `developer` update only when
the catalogue changes. Re-measured here with 14 skills on disk: system message
~5.2k tokens, tail empty. What remains in the tail and is large is an **active
skill's body** — ~4.1k tokens per request for one skill — and that one is not a
free move: in the prefix it costs one whole-history cache miss per activation,
so it depends on when in a session skills get activated, which is unmeasured.
Record the history length at each activation when the billed measurement runs.

**What stage 1 landed** (`llm/_api_format.py`, `llm/_openai_completions.py`,
`llm/_anthropic_messages.py`, `llm/client.py`, `llm/_stream_response.py`,
`runtime/chat_loop/_serialize.py`, `runtime/chat_loop/_runner.py`,
`runtime/model.py`, `context_manager.py`, `agent.py`, `embedding/factory.py`,
`agents/tools/_wrapper.py`, `cli/commands/provider.py`;
`tests/test_llm_api_extraction_noop.py`, `tests/test_anthropic_messages_adapter.py`,
`tests/test_anthropic_messages_runtime.py`, `tests/support/anthropic_wire.py`):

- **The seam.** `LLMClient` keeps the retry/backoff loop, logging and token
  totals; an adapter owns the request shape, the wire call, the stream loop, the
  one-shot request repairs and the retry classification. The Chat Completions
  path moved into `OpenAICompletionsAdapter` statement for statement, and the
  request it builds is held **byte-identical** to a capture taken at
  `main@a2c8c6d`, before the extraction (§10's regression evidence, same PR).
  The working interface is `create_client / build_request / log_view / send /
  new_accumulator / consume_stream / repair_request / classify_retry`
  (`+ reset_latches`). §5.1's `describe_error` and `purge_keys` were **not**
  built as methods: overflow detection and limit parsing were already
  string-based and already matched Anthropic's text, and the purge is a list in
  `runtime/model.py`. Both still settle with the first follow-on adapter.
- **§12's open questions, as decided here.** (2) The SDK is a **core dependency**
  (`anthropic>=1.6.0`, beside `openai`), by the maintainer's decision — a first
  draft shipped it as an extra. It is imported lazily, so the default wire
  never loads it, and being core is also what makes CI run the adapter's tests
  rather than skip them. (3) Sub-agents inherit `api_format`, for `extra_body`'s reason:
  same endpoint. (4) **Log, don't prompt.** A provider switch carries the
  protocol (rev 13): the purge runs and reports its count to `agentao.log`, as
  it does on every model switch, which already drops the same signed blocks
  irrecoverably with no prompt. The one thing a wire change adds is that
  `extra_body` was written for the other protocol, and the CLI names its keys.
- **Config.** `{PROVIDER}_API_FORMAT` and keyword-only `Agentao(api_format=)` /
  `LLMClient(api_format=)`; unknown and not-yet-implemented values fail closed
  and list the valid ones (§9).

**Where the implementation departs from the text below, and why.** Each of
these was found by running the real `anthropic` SDK (1.6.0) rather than by
reading the protocol:

1. **`chat()` and `chat_stream()` both run over the streaming transport.** The
   SDK refuses a non-streaming request whose `max_tokens` implies more than ten
   minutes (`_base_client.py::_calculate_nonstreaming_timeout`: anything above
   ~21,333). agentao's default is 65,536, and the summarizer calls `chat()` with
   no cap at all, so a true non-streaming path would have raised `ValueError`
   on the first compaction. `chat()` consumes the stream with no callback; §10's
   "streaming/non-streaming parity" is therefore by construction, and is still
   tested.
2. **`temperature` is not sent, so §6.8 is moot.** `messages.create` has no
   `temperature` / `top_p` / `top_k` parameter in this SDK — passing one is a
   `TypeError` before anything reaches the network. `LLM_TEMPERATURE` and
   `/temperature` have no effect on this wire; a gateway that takes one gets it
   through `extra_body`. A test pins the `TypeError`, so the day a release
   brings the parameter back, the suite says so.
3. **The block carrier is written at two of the six sites, and the tuple in
   §5.1 did not grow.** The tool-call message and the final response record the
   model's own output and carry `anthropic_thinking_blocks`. The four synthetic
   finals (max-iterations, length abort, hook stop, doom loop) are a *second*
   assistant message built from a response whose blocks are already on the
   first; repeating a signed block there records one model output twice. They
   keep the truncated display copy, as before.
4. **Thinking leads its turn; interleaving with text is not preserved.** An
   OpenAI-shaped dict has one `content` string and one `tool_calls` list, so
   `[thinking, text, thinking, tool_use]` has no representation. Blocks go out
   first, in order, then text, then calls — the placement the API's own rule
   names (with thinking on, the assistant turn of a tool loop must start with a
   thinking block). This is §4's stated ceiling, met.
5. **An `error` event inside a stream is classified by its body.** It arrives
   on an HTTP 200, the SDK raises a bare `APIStatusError` with
   `status_code == 200`, and a status-only table calls it permanent. Overload
   is the common one and usually precedes any content.
   `overloaded_error` / `rate_limit_error` / `api_error` / `timeout_error` are
   retried when nothing has been shown to the host.
6. **Three smaller additions.** A one-shot repair adopts the output cap a model
   states (`max_tokens: N > M`) — the default 65,536 can exceed a model's cap,
   and the message format was confirmed live (*Live results*); an
   id from another provider's session (`functions.read_file:0`) is rewritten
   on the outbound copy only, through a **one-to-one map built over the whole
   request** — the rewrite alone is lossy (`call.1` and `call:1` collide, as do
   ids differing past the 64th character), and a duplicate `tool_use` id is a
   400 on every later request because the ids are in history; and
   Anthropic's second overflow message (`input length and max_tokens exceed
   context limit: A + B > C`) joined the detection and limit tables, because on
   this wire it is the overflow met *first*.

7. **Two things the first review changed.** §6.6's "fail loudly on anything
   but a data URL" became a pass-through for `http(s)` image URLs
   (`source: {type: "url"}`): the part is persisted, so raising was permanent —
   every later request raised too, with nothing short of `/clear` to recover.
   Anything else still raises. And the `extra_body` shadow warning
   (`host-llm-extra-params.md` §3.3) reads its key set from the adapter: on
   this wire `system` is structural — in `extra_body` it silently replaces the
   whole system prompt — while `temperature` and `thinking` are not, since
   `extra_body` is how a host is meant to send them here.

**What stage 1's gate still needs.** Everything above was verified against the
real SDK over a scripted socket: request bodies are the JSON the SDK serialized,
events and exceptions are the SDK's own. At the time **nothing had been run
against a live endpoint**, so four things were asserted from documentation and peer
implementations rather than observed — that thinking-first ordering is
accepted, that the synthetic user turn in front of an assistant-first history
is accepted, the wording of the output-cap rejection, and that a breakpoint
hoisted onto a `tool_result` is honoured. The **cache-benefit check** in §10's
gate was not done either, and needed three arms on one endpoint: stage 0a
alone, 0b over Chat Completions, and the native wire. Both debts are paid below.

**Live results (2026-09-18, `api.anthropic.com`, `claude-sonnet-5`, through
`LLMClient` and this adapter, about a dozen small requests).** The four
assertions above are now observations: (1) the rejection reads `max_tokens:
1000000 > 128000, which is the maximum allowed number of output tokens for
claude-sonnet-5`, the repair latched 128000 and the re-send succeeded — and the
default 65,536 is *under* this model's cap, so the repair does not fire here;
(2) the synthetic user turn is accepted; (3) a signed block sent back at the
head of its turn inside a tool loop is accepted, and a control with a corrupted
signature is a 400 (`Invalid signature in thinking block`), so the API does
check; (4) a breakpoint on a `tool_result` wrote 13,007 cache tokens on the
first request and read 13,007 on the second. Three things nobody asserted:
**this model rejects `thinking.type.enabled`** and wants `{"thinking": {"type":
"adaptive"}, "output_config": {"effort": ...}}` — every example here said the
old form, now corrected; its **thinking text comes back empty** (0 characters
beside a 504-character signature), so the display copy is empty too; and an
*edited* thinking text under an intact signature was accepted, so "the signature
covers the text" was a claim, not a fact — the sanitizer exemption stands on the
protocol's as-it-arrived rule instead. Thinking **mid-turn** (`[text, thinking,
text, tool_use]` answered by a `tool_result`, the shape same-role merging can
produce) was accepted as well, so merging needs no guard.

**The three-arm cache comparison (same day, same endpoint and model).** One
scripted session — 7 user turns, 13 requests, real tool calls over three seeded
files, prompt growing from ~9.6k to ~17.9k tokens — run three times, each arm
isolated from the others' caches by a nonce in the first tool definition.

| Arm | Wire | Breakpoints | Prompt tokens | Cache written | Cache read | Input cost, in uncached-token units |
|---|---|---|---|---|---|---|
| A — 0a only | Chat Completions (`/v1/`) | none | 176,166 | not reported | not reported | 176,166 |
| B — 0a + 0b | Chat Completions (`/v1/`) | 3 `cache_control` markers | 178,920 | not reported | not reported | 178,920 |
| C — native | `anthropic-messages` | 3, native | 177,771 | 17,902 | 159,843 | **38,388** (−78%) |

Cost units are `uncached + 1.25 × written + 0.1 × read`. On the native wire
every request after the first read its whole previous prefix back (request 13:
17,807 of 17,904 tokens) and 26 tokens in the whole session were billed at the
full rate. **Arms A and B cannot be told apart from here**: Anthropic's
OpenAI-compatible endpoint accepted the `cache_control` markers without error
but its `usage` carries no cache fields at all (`prompt_tokens_details` is
null), so whether it honoured them is not observable from the response — only
from the bill. What the numbers do settle is §12.1 as far as this endpoint goes:
**on Anthropic's own API the caching win is reachable, and measurable, only on
the native wire.** 0b's value is for third-party gateways that both honour the
markers and report them, which is still unmeasured, and is why it stays off by
default.

**Reproducing it, and what is still open.** That comparison was run by hand and
its script was not kept. It now is: `scripts/measure_prompt_cache.py` runs the
same three arms (or a subset, against any endpoint), isolates each arm with a
nonce in the first tool definition, and reads the per-request cache counts off
`LLM_CALL_COMPLETED` — which carries them since 0.5.1. It sends nothing without
`--yes`, and renders an endpoint that reports no cache fields as *"not reported
— see the bill"*, never as a zero. Three questions remain, and none of them is
answered by re-running the table above:

1. **A versus B on Anthropic's compatible endpoint** is on the bill and nowhere
   else. Only the account holder can read it.
2. **0b on a gateway that honours the markers and reports them**: `--arms a,b
   --base-url-compat <gateway>/v1`. Unrun — it needs such a gateway.
3. **Whether an active skill's body belongs in the prefix.** `--activate-skill
   NAME --at-turn K` records the history size at the activation, which is the
   input this needs. The trade, from the price model rather than from a run:
   in the tail the body `S` is sent uncached on each of the `N` requests that
   follow; in the prefix it is written once and read after, but the activation
   rewrites the system prompt `P` and the history `H` behind it instead of
   reading them. With write 1.25 and read 0.1 the prefix wins when
   `N > (1.15 × (P + H) / S + 1.15) / 0.9` — about **6** further requests for
   this repo's `P` ≈ 5.2k, `S` ≈ 4.1k at `H` = 10k, about **19** at `H` = 50k.
   That is a bound to test, not a finding: it ignores the 5-minute expiry and
   any compaction in between, both of which favour the tail.

**rev 15 — what changed:** The adapter adopts the provider's Models API
(`GET /v1/models/{id}`): `max_tokens` seeds the output-cap latch before any
rejection, `max_input_tokens` is a third, narrowing-only term of the effective
context window, and `capabilities` feeds `/thinking`, which on this wire now
writes `output_config.effort` (levels from `capabilities.effort`, else the five
the API accepts; effort alone turns adaptive thinking on, observed). Asked on
the send path before the request is built — so the log shows what was sent —
with a definite answer final per model and a transient failure retried once,
re-asked after a switch, and inert on an endpoint without the
route — observed both ways (`api.anthropic.com` adopts 128,000 / 1,000,000; a
compatible gateway answers 404 and nothing changes). This is the one place the
design's "no model catalogue" rule (§9) bends, and only this far: the *provider*
states the limits of the model the user already named; agentao still ships no
table and infers nothing from a name.

**rev 14 — what changed:** *Live results* above, and the thinking examples.

**rev 13 — what changed:** The first use of the second wire was a `/provider`
to a block on it, which stage 1 refused. The provider-switch piece of stage 3 is
now built and nothing else of that stage is: `LLMClient.reconfigure`,
`Agentao.set_provider` and `runtime/model.py::set_provider` take `api_format=`
(`None` keeps the wire), `/provider` passes the target block's
`{PROVIDER}_API_FORMAT`, and ACP passes the resolver's optional `api_format` —
omitted means the default wire, as an unset variable does, never "the session's
current one", or a resolver that marks only its Anthropic provider could not
switch back. `MODEL_CHANGED` carries `api_format_changed`. A
wire change swaps the adapter — a fresh one, so its latches go with it — and
joins §8's clear-on-switch family on its own, even with the model name and URL
unchanged: purge, token anchor, observed limit, capability latches, explicit
cache breakpoints. `extra_body` is kept, as on every switch, and its structural
overlap is re-read against the new adapter (`system` is inert on one wire and the
whole system prompt on the other). The value is resolved before anything is
mutated and a failed client build rolls `reconfigure` back, so a refused switch
leaves the session on the provider it had. `/model` and ACP `session/set_model` stay within a wire, and
per-model overrides (§9) are not built. Tested in both directions off the socket
(`tests/test_anthropic_messages_runtime.py`).

**rev 12 — what changed:** Stage 1 was implemented; the two blocks above record
what landed, where it departs from this text and why, and what its gate still
needs. §12.2 and §12.3 are decided (core dependency; inherit). The design text below is
otherwise unchanged from rev 10.

**rev 11 — what changed:** Stage 0 was implemented; the status block above records
what landed, where, and what its gate still needs. The design text below is
unchanged from rev 10 — it is the record the implementation was built from, and
the one stage 1 would be judged against.

**rev 10 — what changed:** Verified a second corroborating implementation, gemini-cli
(`9450ade79`, `@google/genai` 1.30.0): one GenerateContent protocol serving three access
paths (API key, `vertexai: true`, Code Assist `v1internal:streamGenerateContent`), which
corroborates §3's `api`/`provider` split, with Code Assist explicitly out of scope.
`grep interactions.create` is empty in both peers, so appendix C still has no
corroborating implementation. Appendix B.3 gains two measured rules: **a missing signature
is itself a 400** (gemini-cli substitutes a placeholder for each message's first
functionCall, and agentao's compaction, `/resume` and minimal-history paths all produce
exactly that history) and **signatures do not cross endpoints** (Genai → Vertex fails),
the latter also recorded in §8 and now the technical reason §9 excludes Vertex. B.5's
warning is generalized: the two implementations write the same field oppositely, so check
the destination field's meaning before copying.

**rev 9 — what changed:** Verified that both Google adapters in the local pi-mono
checkout (`5a3a03a7f`) drive GenerateContent's native streaming (`generateContentStream`
on `@google/genai` 2.21.0), so they corroborate **appendix B**, while its ten-value
`KnownApi` has no Interactions adapter — appendix C has no corroborating implementation,
an asymmetry now recorded in §3. §2.2's cost references are measured per file (the
earlier "~900 lines" was wrong). Appendix B.3 gains two details from that implementation
(a signature is not thinking; a stream may carry the signature only on a block's first
delta) and B.5 gains a do-not-copy warning: pi-mono's `input` is uncached input, so
copying its line under-reports the Tier-1 anchor.

**rev 8 — what changed:** Three closures. Appendix C.5 names the termination
events (`interaction.completed` / `error` / `done`; `step.stop` is not a finishing
signal); §9 states that `gemini-api` stays out of the published value range until
§3 settles the transport; and the sunk-cost warning in §2.2 and appendix C's
preamble now covers appendices B and C symmetrically, with §12.1 able to cancel
both.

**rev 7 — what changed:** Add appendix C for Interactions stateless step replay,
tool pairing, dedicated thought signatures, native streaming and usage acceptance.
Appendices B/C supply the two candidates for §3; §12.1 still precedes comparison.
No new configuration value or authorization to implement both Google transports.

**Origin:** the pi-mono pull review of 2026-09-17 (session record — pi-mono
`400d6905c..5a3a03a7f`; not landed as a doc), whose headline finding was that
agentao invests in half of prompt caching and skips the other halves. Two of the
three remedies there are reachable only over a wire protocol agentao does not
speak. The evidence for those findings is restated inline here (§1, §2.3) rather
than cited, so this doc stands on its own.

**Audience:** agentao maintainers deciding whether to widen the LLM boundary;
reviewers of any implementing PR.

**Companions:**
- `docs/design/llm-api-adapters.zh.md` — Chinese twin.
- `docs/design/host-llm-extra-params.md` — the sibling primitive (`extra_body`);
  the same "closed request kwargs" observation, solved for the body rather than
  for the protocol.
- `docs/design/embedded-host-contract.md` — why `agent.messages` cannot change shape.
- `docs/design/compaction-orchestration-plan.md` — the compaction shape rules
  that constrain any translation layer.
- `docs/design/tool-search.md` — a recorded decision whose stated blocker this
  design would remove (§2.2).

**Anchors — agentao (verified at `main@592f028`):**
- `agentao/llm/client.py` — `_build_request_kwargs` (`389-424`), `chat()` wire call
  (`455`), `chat_stream()` wire call (`717`), `_is_gemini` (`515-527`),
  `reset_capability_latches` (`375-387`), client construction (`219-223`, `366-370`).
- `agentao/llm/_stream_response.py` — the duck-type contract, module docstring (`1-16`).
- `agentao/runtime/llm_call.py` — the observability wrapper (whole file).
- `agentao/runtime/chat_loop/_runner.py` — request assembly (`342-346`).
- `agentao/runtime/chat_loop/_serialize.py` — `_serialize_tool_call` (`38-73`).
- `agentao/runtime/tool_result_formatter.py` — tool-result message shape (`232-237`).
- `agentao/context_manager.py` — mid-history summary message (`1071-1078`),
  minimal-history head repair (`1185-1195`), `role: "tool"` split rules (`799-820`).
- `agentao/tools/base.py` — `to_openai_format` (`170-179`, `365-...`).
- `agentao/embedding/factory.py` — `LLM_PROVIDER` resolution (`68-85`).

**Anchors — pi-mono (`5a3a03a7f`, used as a corroborating implementation, not as a spec):**
- `packages/ai/src/types.ts:17-27` — the ten-value `KnownApi` union.
- `packages/ai/src/compat.ts:180-191,244-266` — registry + dispatch on `model.api`.
- `packages/ai/src/api/anthropic-messages.ts:1226-1241,1275-1308,1385-1394,1081-1104` —
  message conversion, the pending-system-message queue, tool-result grouping, cache markers.
- `packages/ai/src/api/openai-responses-shared.ts:328-350,480-515,533-548` —
  `function_call_output`, the composite tool-call id, reasoning persistence.
- `packages/ai/src/api/openai-responses.ts:318,322,353` — `store: false`,
  `max_output_tokens` floor, `include: ["reasoning.encrypted_content"]`.
- `packages/ai/src/api/openai-completions.ts:1081-1135,1632` — Anthropic-style
  `cache_control` **over the Chat Completions wire**. See §2.3; this is the cheap path.
- `packages/ai/src/api/google-generative-ai.ts:100`, `google-vertex.ts:109` — both Google
  adapters call `client.models.generateContentStream(params)` on the official
  `@google/genai` SDK **2.21.0**. **They corroborate appendix B, not appendix C**: the
  ten-value `types.ts:17-27` union carries `google-generative-ai` and `google-vertex`,
  and no Interactions adapter.
- `packages/ai/src/api/google-shared.ts:112-145,235-273` — signature semantics
  (`isThinkingPart`, `retainThoughtSignature`) and streaming retention; 515 lines shared
  by both adapters.
- `packages/ai/src/api/google-generative-ai.ts:231-240` — usage mapping. Note its `input`
  is the **uncached** part (`promptTokenCount - cachedContentTokenCount`), not agentao's
  `prompt_tokens`; see appendix B.5.

**Anchors — gemini-cli (`9450ade79`, a second corroborating implementation, official
`@google/genai` SDK 1.30.0):**
- `packages/core/src/core/contentGenerator.ts:285-312`, `:392` — routing by auth method;
  Vertex is the same SDK plus `vertexai: true`, not a different wire protocol.
- `packages/core/src/code_assist/server.ts:73-74,93` — Google-account login goes through
  Code Assist at `https://cloudcode-pa.googleapis.com` + `v1internal`, i.e.
  `v1internal:streamGenerateContent`. **An internal API, explicitly out of agentao's scope.**
- `packages/core/src/core/geminiChat.ts:110,1259-1310,1850-1864` —
  `SYNTHETIC_THOUGHT_SIGNATURE = 'skip_thought_signature_validator'` and
  `ensureActiveLoopHasThoughtSignatures`; see appendix B.3.
- `packages/core/src/config/config.ts:1580-1589`, `geminiChat.ts:1241-1257` —
  `stripThoughtsFromHistory()` on an auth switch: Genai signatures sent to Vertex fail.
- `packages/core/src/agent/event-translator.ts:468-470` — `inputTokens:
  promptTokenCount` with **no** cache subtraction, the opposite of pi-mono; see appendix B.5.
- `grep interactions.create|previous_interaction_id` over the repo: zero hits.

---

## 1. What agentao speaks today, and where the seam already is

agentao speaks exactly one wire protocol: **OpenAI Chat Completions**. There are
two call sites, `client.py:455` (non-streaming, `with_raw_response.create`) and
`client.py:717` (streaming, `create(stream=True)`), both fed by one closed kwargs
builder, `_build_request_kwargs` (`client.py:389-424`), which emits
`{model, messages, stream?, stream_options?, temperature?, tools?, tool_choice?,
max_tokens|max_completion_tokens?, extra_body?}`.

Thirty-two files under `agentao/` reference the OpenAI message shape
(`tool_calls` / `"function"` / `finish_reason` / `chat.completions`). They fall
into three groups, and the distinction is the whole design:

| Group | Files (examples) | What they actually depend on |
|---|---|---|
| **Wire** | `llm/client.py`, `llm/_logging.py` | The HTTP request/response bytes |
| **Response shape** | `runtime/llm_call.py`, `runtime/chat_loop/_runner.py`, `runtime/sanitize.py` | `response.choices[0].message.{content,tool_calls,reasoning_content}`, `response.usage`, `response.model` |
| **History shape** | `context_manager.py`, `compaction/`, `runtime/tool_*`, `session.py`, `replay/`, `acp/`, `cli/` | `agent.messages` being a list of OpenAI-shaped dicts |

**The load-bearing observation: the response shape is already an interface, not a
type.** `llm/_stream_response.py` is a hand-built duck-type that reconstructs a
`ChatCompletion` from SSE deltas, and its module docstring (`:1-16`) enumerates
the exact attribute surface downstream code touches. Nothing outside `llm/`
requires an OpenAI SDK object — only an object with those attributes. A second
wire protocol can therefore reuse the entire runtime **if its adapter returns the
same duck-type**.

The history shape is the opposite: it is load-bearing everywhere, it is persisted
to session files and replay files, it crosses the ACP boundary, and
`docs/reference/host-api.md:202` tells hosts to read `agent.messages` directly.
It is a contract, not an implementation detail.

## 2. Is this worth doing? — three honest sub-questions

### 2.1 What the new protocols actually buy

| Want | Chat Completions today | Needs `anthropic-messages` | Needs `openai-responses` | `gemini-api` |
|---|---|---|---|---|
| Explicit prompt-cache breakpoints (`cache_control`, 1h retention) | not exposed by Anthropic's OpenAI-compat endpoint | yes — native | n/a | n/a |
| Signed thinking blocks round-tripped intact | agentao truncates `reasoning_content` to 500 chars (`_serialize.py:20`) and purges on switch | yes | n/a | n/a |
| Reasoning **preserved** across turns, without server-side state (`reasoning.encrypted_content`) | no equivalent — dropped | n/a | yes | n/a |
| Thinking budget / effort as a first-class param | only via `extra_body` `reasoning_effort`, no auto-recovery | yes (`thinking.budget_tokens` / effort) | yes (`reasoning.effort`) | pending §3 transport choice |
| `prompt_cache_key`, server-side conversation state | `extra_body` can set the key; no state | n/a | yes | n/a |
| Gemini signature fidelity + incremental output | All models matched by `_is_gemini` bypass streaming (`client.py:515-527,559-561`); no incremental text | n/a | n/a | Native streaming and signed-part round-trip from v1; transport choice gated by §3 |

`gemini-api` adds a native boundary for Gemini parts, thought signatures and
streaming (appendix B), with a separate integration point for `cachedContent`
resources. The existing compatibility path bypasses streaming to preserve
`thought_signature` (`client.py:559-561`); the native adapter must preserve
signatures while delivering incremental output. Explicit cache-resource creation,
invalidation and cost require separate acceptance, not stage 0b's `cache_control` markers.

**One recorded decision this would reopen.** `docs/design/tool-search.md` rejected
pi-mono's transcript-carried tool activation on the grounds that it "needs a
provider-native load point agentao's chat-completions path lacks — so it **does
not lower agentao's cost**". Anthropic Messages and OpenAI Responses supply such a load point. That
does not resurrect the proposal on its own (its driver was judged provider-led,
not tool-list bloat), but the stated blocker would no longer be the reason.

### 2.2 What it costs

A complete adapter owns request/response translation, usage and stop-reason
mapping, retry classification, context-overflow parsing
(`parse_observed_context_limit`), capability latches, and its own streaming event
state machine and accumulator. Measured per file in the corroborating
implementation (pi-mono `5a3a03a7f`, before tests): `anthropic-messages.ts` 1,520;
`openai-responses.ts` 397 plus `openai-responses-shared.ts` 793 shared across several
Responses adapters; the Gemini transport is `google-generative-ai.ts` 470 plus
`google-shared.ts` 515 shared by both Google adapters (`google-vertex.ts` adds 553 and
reuses the same shared translation). After #283 agentao's entire `llm/` is
**1,527 lines**. These are full-implementation cost references, not a stage-1 estimate,
and a shared file serves several adapters rather than belonging to one.

Stage 1 must ship a native streaming path for the new protocol: deliver text
deltas through `on_text_chunk` as they arrive, then return the complete duck-type
response when the stream ends. Cache measurement alone does not require streaming,
but the first-version user experience requires incremental output. Its event state
machine, accumulator, cancellation and error handling therefore belong in the
stage-1 cost. The Gemini non-streaming bypass at `client.py:559-561` remains an
existing provider-specific exception.

Gemini also requires pricing the migration cost of a Legacy transport. Neither
appendix B nor appendix C is a sunk-cost reason to proceed: having written out
both candidate rule sets is not a reason to keep the old transport, nor a reason
to pick one of the two for implementation. §3's transport comparison is a
prerequisite for stage 2, and is itself gated on §12.1.

### 2.3 The cheap path that gets most of the caching win — verify this first

`packages/ai/src/api/openai-completions.ts:1632` sets
`cacheControlFormat = "anthropic"` when `provider === "openrouter" && model.id.startsWith("anthropic/")`,
and `applyAnthropicCacheControl` (`:1081-1089`) then injects
`cache_control: {type: "ephemeral", ttl?: "1h"}` at **three** breakpoints —
the system prompt, the last tool definition, and the last conversation message —
**over the ordinary Chat Completions wire.**

Stage 0 has two steps: stabilize the prefix for every provider, then validate
explicit markers on one endpoint.

**0a: move volatile blocks below history.** `prompts/builder.py:95-107` puts
skills, todos, dynamic_recall and plan inside system, which is `messages[0]`.
Changing them invalidates the prefix covering the whole history for both explicit
breakpoints and OpenAI/DeepSeek implicit prefix caching.
`context_manager.py:320-327` already documents system being rebuilt each turn
with volatile content, as a bounded token-estimation skew. This fix needs no new
knob or Anthropic endpoint but requires the Tier-1 anchor correction below; actual cache
hits and cost savings still need measurement.

**Choose a temporary user tail message at request assembly**, wrapping current
volatile content in `<system-reminder>`. Assemble stable system + history + one
temporary tail on each request. Do not copy `_runner.py:342`'s date/time pattern
of persisting the reminder inside a user message: todos snapshots must not pile
up in the transcript. The tail lives only in the newly built
`messages_with_system`, never in `agent.messages`. Tool loops, compaction and
overflow retries must rebuild it without losing or duplicating it or splitting
a tool-call/result pair.

**Wire all seven rebuilds.** `_runner.py` assigns `messages_with_system = [`
at `:344,551,717,876,1078,1154,1209`; background injection called at `:393`
uses the `:1154` site, not an eighth site. Centralize request assembly in one
helper used by all seven: build `persistent = [S] + H`, then append current tail
T only to the request. Cover tool loops, compaction, overflow retries and
background notifications without missing or duplicating the tail.

Both existing `<system-reminder>` patterns persist: date/time (`:342`) and
background notifications (`:1146-1156`), the latter appending a user message.
0a introduces the first non-persisted message in these seven assembly paths,
a **new lifecycle invariant**: the tail is request-only while notifications
remain persisted. Do not conflate them.

**Anchor the persistent prefix, not request length.** Threshold estimation
(`:386`) and anchor recording (`:427-428`) currently use the same list. Recording
the length of `[S]+H+[T]` makes the next slice skip the first new history message
Δ[0] and count the new tail again. Ignoring local encoding error, with at least
one new history message:

```text
truth - estimate ≈ tokens(S') - tokens(S) + tokens(Δ[0]) - tokens(T)
```

When the old tail exceeds the first new history message, this systematically
overestimates and triggers compaction early; the reverse can underestimate.
The error is bounded and non-accumulating but recurs each turn, potentially at
whole-tail scale rather than the old difference between two system prompts.
Use the following calculation, freezing `est(T)` including message-envelope
cost at send time:

```python
record_api_usage(prompt_tokens - est(T), len(persistent))
next_estimate = anchor + est(new_persistent_messages) + est(T_next)
```

The estimation entry point must also separate persistent prefix and current tail;
changing only `message_count` is insufficient. Rewriting the persistent prefix
still invalidates the anchor. Tier-1 now means **actual usage minus a local
estimate**: tail estimation errors contaminate the anchor, with residual error
including changes in tail-estimation error across turns. Over N consecutive
turns (N ≥ 10), vary tail size with fixed history growth and record
`estimate - actual_prompt_tokens`. Require no systematic error growth with tail
size and report local-estimation error bounds. Checking that new messages are
not skipped is insufficient.

**0b: explicit `cache_control`, opt-in, one named endpoint.** No adapter is
needed. **SDK pass-through is verified:** locally, openai 2.24.0's
`maybe_transform(body, CompletionCreateParamsNonStreaming)` preserves unknown
`cache_control` keys on message dicts, content parts and tool dicts unchanged.
No SDK escape hatch is needed. **Endpoint acceptance remains unverified:**
pi-mono's provider/model-prefix gate is not an agentao endpoint acceptance test.
Name and verify one endpoint before enabling markers; do not send them blindly.
Place the conversation breakpoint at the end of stable history, excluding 0a's
volatile temporary tail.

**Copy-on-mark is mandatory.** `_runner.py:344-346` only concatenates shallowly;
`chat()` and `_build_request_kwargs` do not copy. In-place markers would enter
`agent.messages`, session files, replay, ACP `session/load` and compaction, and
old breakpoints would accumulate. Shallow-copy only marked message/tool dicts;
for a content-part marker, copy the owning message, content list and target part
along that path, leaving other objects shared and read-only. Copies are request
only and never flow back into history or canonical tool schemas. Anthropic's
[official caching documentation](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
allows up to 4 breakpoints; this design budgets **at most 3 explicit markers
plus 1 slot reserved for automatic caching** (checked 2026-09-18). With automatic
caching enabled, its stated failure condition is: “If 4 explicit block-level
breakpoints already exist, the API returns a 400 error (no slots left for
automatic caching).” Four explicit markers alone are not illegal, and this plan
does not enable automatic caching by default. Use fewer markers when tools or
history are absent; rebuild every request without accumulation and count any
caller-provided markers within the budget. §5.3's native carriers must persist,
the opposite lifecycle from cache markers.

## 3. Vocabulary: `api` is not `provider`

pi-mono keeps two orthogonal axes — `api` (ten wire protocols) and `provider`
(~40 credential/endpoint namespaces), mapped many-to-one. agentao already has
`LLM_PROVIDER` (`embedding/factory.py:68-85`) but it means **only** the
credential namespace: it selects `{PROVIDER}_API_KEY` / `_BASE_URL` / `_MODEL`.

**Do not overload `LLM_PROVIDER` with protocol selection.** A user with
`LLM_PROVIDER=ANTHROPIC` pointing at an OpenAI-compatible gateway is a legitimate
and currently-working configuration; making the name imply the wire would break
it silently. The new knob is a separate axis — §9.

**Keep `gemini-api` as the native adapter identifier, but do not freeze its
first transport.** Google's OpenAI-compatible interface remains
`openai-completions`. Directly opened on 2026-09-18, both thought-signature and
caching guide titles carry **Gemini Generate Content API (Legacy)**; the API
reference does not. The [official Interactions overview](https://ai.google.dev/gemini-api/docs/interactions-overview)
says Interactions reached GA in June 2026, is recommended for new projects and
is supported by Python `google-genai` ≥ 2.3.0. It also says GenerateContent remains
fully supported and lists explicit caching as unavailable in Interactions.
Neither instability nor missing SDK support justifies the older transport;
Legacy alone is not a published shutdown date.

**No explicit cache does not mean no cache.** The same overview's Limitations
notes server-side implicit caching via `previous_interaction_id`; Best practices
explicitly supports implicit caching in **both stateful and stateless modes**.
Conversation chaining aids hits; it is not the only route. Apply appendix A.3's
history rule: compare with `store=false`, full-history replay and no server-side
id chain. Stateless Interactions caching remains eligible. Adopting a chain would
require reopening A.3 consistently for compaction, `/clear` and replay, not a
Gemini exception. State ownership constrains architecture but does not decide
cache availability; measure explicit-resource versus stateless-implicit benefits.

**This axis split has corroboration.** gemini-cli (`9450ade79`) serves three access
paths over **one** GenerateContent protocol: an API key through
`models.generateContent(Stream)`; Vertex through the same SDK plus `vertexai: true`
(`contentGenerator.ts:392`); and Google-account login through Code Assist's
`v1internal:streamGenerateContent` (`server.ts:73-74,93`). One wire protocol, three
credential/endpoint arrangements — exactly the two axes this section keeps apart. Code
Assist is an internal API and stays out of agentao's scope; Vertex is therefore not
"another protocol" but another provider block (§9 still excludes it from v1, and now for
a technical reason too — see appendix B.3 on cross-endpoint signature portability).

**The two candidates carry asymmetric implementation risk — a different question
from transport lifetime.** Both local corroborating implementations use GenerateContent
and **neither uses Interactions**: pi-mono (`5a3a03a7f`) drives `generateContentStream`
from both Google adapters (`@google/genai` 2.21.0), and gemini-cli (`9450ade79`) reaches
GenerateContent on all three access paths (`@google/genai` 1.30.0); `grep
interactions.create` is empty in both. So appendix B's native streaming and per-part
signature replay have **two readable corroborating implementations**, while appendix C
still rests on primary documentation alone. This does not reduce the Legacy
transport's migration cost and is not a reason to choose it (§2.2's sunk-cost
warning applies equally), but the comparison must record it: the two candidates
do not carry the same number of unknowns, so they do not carry the same
verification cost.

**Withdraw the unconditional exclusion of Interactions.** Appendix B's
`generateContent` / `streamGenerateContent` remains a candidate only for a
measured explicit-cache-resource need. If the need is only signature fidelity and
incremental output, that rationale does not apply: evaluate the recommended
Interactions API first. Only after §12.1 establishes a remaining benefit worth
implementing, compare stateless full
history replay, signatures, tools, native streaming, cache cost and subsequent
migration cost on target-model/SDK fixtures. Settle the transport and revise
the selected appendix B/C before authorizing implementation; without those results, there is
no basis to claim the old transport is cheaper. See the
[official migration guide](https://ai.google.dev/gemini-api/docs/migrate-to-interactions).
Vertex AI credentials/deployment configuration and Live API remain outside v1;
do not silently mix transports under an already-published `api_format` contract.

## 4. Three options for the seam

| | **A — adapter under `LLMClient`** | **B — neutral internal message model** | **C — host injects a whole client** |
|---|---|---|---|
| Canonical history | stays OpenAI dicts | new neutral type | stays OpenAI dicts |
| Files changed | `llm/` + config + `/model` surface | ~32 files, session + replay + ACP formats | ~0 |
| Host contract (`agent.messages`) | unchanged | **broken** | unchanged |
| Session / replay files | unchanged | migration needed | unchanged |
| Who writes the adapters | agentao | agentao | every host, again |
| Fidelity ceiling | limited by what an OpenAI dict can carry (§5.3) | highest | per-host |
| pi-mono equivalent | — | this is what pi-mono did | — |

**Recommendation: A.** B is the better architecture in the abstract and is what
pi-mono chose — but pi-mono chose it *before* accumulating a compaction engine,
a replay format, an ACP session-load path and a documented host contract on top
of the OpenAI shape. agentao did not, and the migration cost is now concentrated
in exactly the subsystems that are hardest to test. C is not a design; it is a
decision to ship nothing.

**A's ceiling is real and should be stated up front:** an OpenAI-shaped dict is
the interchange format, so anything the target protocol needs that an OpenAI dict
cannot express must ride as an extra key on that dict. agentao already does this
twice (`reasoning_content`, `thought_signature` — `_serialize.py:23-36`,
`_serialize_tool_call`'s `model_dump()` comment), so the pattern is established
rather than invented here. But every such key must then be purged on a switch
(`runtime/model.py::purge_thinking_artifacts`) and excluded from outbound
sanitization, and that list grows per adapter.

## 5. Option A in detail

### 5.1 The contract (a sketch, not something to freeze)

One implementation cannot tell you which of these methods is the contract and
which is an accident of the first protocol. Treat the shape below as a working
sketch for stage 1 and settle it with the first follow-on adapter (§10, stage 2).

Four operations plus `api` suffice. Each adapter constructs and owns its SDK
client; `LLMClient` does not pass an SDK client into it.

```python
class LLMApiAdapter(Protocol):
    api: str  # openai-completions | anthropic-messages | openai-responses | gemini-api

    def chat(self, *, model, messages, tools, max_tokens,
             temperature, extra_body) -> Any: ...  # duck response
    def stream(self, *, model, messages, tools, max_tokens, temperature,
               extra_body, on_text_chunk, cancellation_token) -> Any: ...  # duck response
    def describe_error(self, exc) -> ErrorDescription: ...
    def purge_keys(self) -> tuple[str, ...]: ...
```

`describe_error` combines retry classification and context-limit parsing,
retaining the retry decision, status/reason and optional provider-asserted limit;
`ErrorDescription`'s representation is not frozen. `stream` is required and
called through `LLMClient.chat_stream`: emit text deltas through the callback,
return the same complete duck-type as `chat` on completion, and propagate
cancellation. SDK event parsing and accumulation remain adapter internals.
`build_request`, `send` and `to_duck_response` are also internal steps, not
contract members.

`LLMClient` preserves `chat`, `chat_stream`, `model`, `temperature`, `max_tokens`,
`extra_body`, `reconfigure` and `reset_capability_latches`, plus externally read
`.logger` (13 sites in `context_manager.py`), `.total_prompt_tokens` /
`.total_completion_tokens` (`agent.py:1262-1263`, `:1415-1416`), and `.api_key` /
`.base_url` (`agent.py:834-835`, used to construct sub-agent configuration).
It delegates protocol-specific work. A replacement client class would have to
replicate this entire compatibility surface, strengthening the case for A.

**One thing above `llm/` must change.** An adapter can only *return* a richer
response; it does not decide what is written to history. The runner hand-builds
the assistant dict at **six** sites (`_runner.py:573, 623, 735, 820, 839, 943`),
copying `content` / `tool_calls` / `reasoning_content` and nothing else, and every
one of them then calls `_attach_reasoning` (`:576, 630, 738, 823, 842, 944`),
which **truncates to 500 characters** (`_serialize.py:20`). A `signature` or a
`reasoning_item` handed back by an adapter is dropped there, silently, and a
truncated signed block is rejected by the provider on the next request. Reasoning
fidelity is therefore decided at the **history write**, not at the adapter, and
§5.3's carrier keys are inert until that write changes.

`_attach_reasoning` must gain a second, non-truncated carrier: a **list of native
blocks in order**, not one string plus a signature — Anthropic returns multiple
`thinking` / `redacted_thinking` blocks per turn, and flattening them loses both
the ordering and the per-block signature. The display copy stays as it is.

**The save logic stays in that one function, but the value has to be threaded to
it — the six sites do not inherit it for free.** `_attach_reasoning` receives a
*string* today (`_serialize.py:23`), so the block list has to reach every call
site the same way `reasoning_content` does:

- **three origination points** read it off the response — `:568-569`, `:614`,
  `:898` — and each needs a second read for the blocks;
- **one intermediate contract**: `:614`'s value is returned as the third element
  of a tuple (`:640`, arity documented at `:603`) and unpacked at `:684` and
  `:787`, which feed three of the six writes (`:738`, `:823`, `:842`). That tuple
  grows an element, and both unpack sites change with it.

None of this needs a new abstraction, and it is not "just change the helper".

### 5.2 What stays exactly where it is

- `agent.messages` — OpenAI dicts. Session files, replay files, ACP `session/load`,
  `cli/display.py`, the compaction engine: the **shape** is unchanged, and the
  additive carrier key from §5.1/§5.3 rides along in the persisted files the same
  way `reasoning_content` already does. What must not happen is a *different*
  message model; an extra key on an assistant dict is not one.
- `to_openai_format()` (`tools/base.py:170-179`) stays the canonical tool schema.
  Adapters that need a different shape (Responses flattens the nested `function`
  object) translate *from* it. Do not add a second schema producer.
- `runtime/llm_call.py`'s event payloads. `tools_hash` reads
  `t["function"]["name"]` (`llm_call.py:52-57`) — it must keep hashing the
  canonical schema, not the translated one, or the same tool set hashes
  differently per api and replay comparisons break.
- The redacting log formatter. It is a `Formatter` on the handler
  (`llm/client.py`, see CLAUDE.md § Logging), so it covers new adapters for free.

### 5.3 The carrier problem, named

These native-block carrier keys must persist. §2.3's `cache_control` exists only
in request copies; the two must not share a lifecycle.

| Protocol needs | Carried on the OpenAI dict as | Must also be added to |
|---|---|---|
| Anthropic thinking block + `signature` | separate ordered native-block list with per-block signatures (§5.1); `reasoning_content` remains the display copy | `purge_thinking_artifacts`, sanitize allowlist |
| Responses `reasoning` item + `encrypted_content` | a `reasoning_item` key on the assistant message | same |
| Responses dual ids (`call_id` **and** item `id`) | see appendix A.2 — **do not invent a second id field** | — |

Gemini likewise needs an ordered native-parts carrier preserving each part's
`thoughtSignature` (appendix B). `_serialize_tool_call` preserves tool-call extras
but cannot preserve signatures on non-tool parts; tool-call `thought_signature`
alone does not establish fidelity. The persisted representation must be JSON
serializable: losslessly encode SDK byte signatures and restore them on replay.
Include the carrier in sanitization exemptions and switch purging.

If Interactions is selected, carry ordered model steps (appendix C.3/C.4), not
GenerateContent parts. The same persistence and purge obligations apply; tag
native payloads by transport so they cannot be replayed on the wrong wire.

## 6. Translation rules — `anthropic-messages`

Each rule below names the agentao code that forces it. pi-mono line references
are corroboration that the rule is real, not a spec citation.

1. **Lift the system message.** agentao sends the system prompt as `messages[0]`
   (`_runner.py:342-346`); Anthropic takes a top-level `system` parameter.
   Mechanical.

2. **Mid-history `role: "system"` messages have no target — and agentao emits one.**
   `context_manager.py:1071-1078` injects the compaction summary as a
   `role: "system"` message *in the middle of history*. Anthropic has no
   mid-conversation system message. pi-mono hit the same wall and solved it with
   a deferred queue: `anthropic-messages.ts:1237-1241` explains that a system
   message between a `tool_use` and its `tool_result` is *rejected*, so updates
   are held in `pendingSystemMessages` and flushed at the next user-message
   boundary. agentao must do the same, or fold the summary into the following
   user message. **This is the single most likely source of a silent 400 in a
   naive implementation**, because it only appears after the first compaction.

3. **Do not rely on an assistant-first history being accepted.**
   `_minimal_history_start` (`context_manager.py:1185-1195`) deliberately steps
   back to the assistant message that opened a tool run, so after the last
   overflow rung `messages[0]` can be an assistant message. The adapter must
   detect this and prepend a synthetic user turn (or refuse to cut there). The
   existing test for the rung must be extended, not trusted. This is a conservative
   compatibility rule pending an assistant-first probe on the target endpoint,
   not a claim that the protocol universally requires a user-first history.

4. **Coalesce tool results.** agentao appends one `{"role": "tool", "tool_call_id",
   "name", "content"}` message per result (`tool_result_formatter.py:232-237`).
   Anthropic wants all results for one assistant turn as `tool_result` blocks
   inside a **single** `user` message (`anthropic-messages.ts:1385-1394`).
   Group consecutive `role: "tool"` runs; preserve order.

   Background notifications already persist a user message after tool results
   (`_runner.py:1146-1156`); this predates 0a. In outbound copies, coalesce following
   consecutive user content into the same user turn: all `tool_result` blocks
   first, then notification and temporary-tail text in original order. Never
   write the coalesced copy back to history. Anthropic's
   [Messages reference](https://platform.claude.com/docs/en/api/messages/create)
   accepts and combines consecutive same-role turns; explicit normalization fixes
   block ordering. Fixture: parallel results + notification + temporary tail,
   also covering §6.2's mid-history summary without splitting call/result pairs.

5. **`tool_call_id` round-trips byte-for-byte.** Already an agentao invariant
   (CLAUDE.md § unicode tags: ids are exempt from stripping). Anthropic's
   `tool_use_id` maps 1:1 — no composite needed, unlike Responses (appendix A.2).

6. **Images.** agentao emits `{"type": "image_url", "image_url": {"url": "data:<mime>;base64,<data>"}}`
   (`_runner.py:326-330`) → Anthropic's `{"type":"image","source":{"type":"base64","media_type","data"}}`.
   Parse the data URL. **agentao's own path never produces anything else**:
   `chat(images=...)` requires `data` + `mimeType` and raises otherwise
   (`_runner.py:318-330`), so a remote URL can only reach history from a host
   writing `agent.messages` directly. Anthropic does accept a remote image
   (`source: {type: "url", url: ...}` — Anthropic's vision docs, *URL-based image
   example*), so a pass-through is available whenever that case needs to be
   supported. v1 handling data URLs only is therefore a stated **scope** limit,
   not a property of the protocol: fail loudly on anything else rather than
   dropping it silently.

7. **Thinking blocks.** Anthropic returns `thinking` and `redacted_thinking`
   blocks with a signature that must be returned verbatim on the next request.
   agentao truncates `reasoning_content` to 500 chars for history
   (`_serialize.py:20`) — **that truncation is incompatible with signature
   round-trip** and must be bypassed for this adapter, with the untruncated text
   carried separately from the display copy. A truncated signed block is worse
   than no block: the provider rejects it.

8. **`temperature` is incompatible with extended thinking** (corroborated at
   `anthropic-messages.ts:1104`). agentao already has an `omit_temperature` latch
   (`client.py:375-387`) — reuse it rather than adding a parallel flag.

9. **Cache breakpoints.** Same three positions as §2.3: system, last tool,
   last stable-history message, excluding the temporary tail and following
   copy-on-mark. Gate behind a config flag; default off until measured.

10. **`stop_reason` → `finish_reason`.** `end_turn`→`stop`, `max_tokens`→`length`,
    `tool_use`→`tool_calls`, `stop_sequence`→`stop`. Note
    `_StreamAccumulator.finish_reason_reported` (`_stream_response.py:50-61`)
    exists precisely to distinguish "provider said stop" from agentao's fallback —
    set it honestly.

11. **Usage.** Anthropic's `input_tokens` counts only the **uncached** portion,
    so the mapping must *fold the cache fields in*:

    ```text
    prompt_tokens = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
    ```

    Keep the two cache fields as well, additively, for cost reporting — but they
    are extra, not a substitute. Getting this wrong is not a reporting nit:
    `record_api_usage(prompt_tokens)` (`context_manager.py:289-299`) is the
    **Tier-1 anchor**, and `_threshold_token_estimate` (`:312-328`) takes it as
    the true size of the already-sent prefix and locally estimates only the
    messages appended since. Mapping `prompt_tokens = input_tokens` alone would
    under-report the prefix by exactly the cached amount — *most* of it, on a
    working cache — so compaction would fire late or not at all and the turn
    would meet the provider's context limit instead. The failure scales with how
    well the caching in §2.3/§6.9 works, which is the opposite of what a cache
    feature should do. (That docstring's own warning — "do not 'fix' it by
    trusting the anchor harder" — is about a bounded, self-healing skew; this
    would be an unbounded one.)

    This is the opposite of Gemini in appendix B.5: add cached input here,
    never add it twice there. Golden fixtures must reach `record_api_usage` and
    assert the resulting anchor (subtracting the fixed tail estimate under §2.3),
    not just compare mapped usage fields.

## 7. Later protocol (moved to appendix)

`openai-responses` and `gemini-api` are later adapters. Responses translation
and composite-id rules are in appendix A; GenerateContent rules are in appendix B,
and Interactions rules in appendix C. Compare B/C under §3 before choosing Gemini's transport.

## 8. Cross-cutting surfaces that grow an `api` dimension

| Surface | Change |
|---|---|
| `runtime/model.py::purge_thinking_artifacts` | must purge the union of every adapter's carrier keys (§5.3), and still run on **every** switch. Gemini's per-part signature carrier is the measured evidence: signatures do not cross endpoints, which is why gemini-cli calls `stripThoughtsFromHistory()` on an auth switch (appendix B.3) — so the purge must trigger on model **or** endpoint, never on the model name alone |
| `runtime/model.py::set_model` / `set_provider` | an api change is a switch: clear latches, encoding, token anchor, observed limit — same family |
| `context_manager.parse_observed_context_limit` | overflow error shapes differ per protocol; it is explicitly "provider-asserted, refuses what it is not certain of" — keep that posture per adapter |
| `llm/_retry.py::_classify_retry` | status codes (`RETRYABLE_STATUS_CODES`, `:26`) **plus**, since #283, one exact-match string check on the 429 branch: `_is_quota_exhausted` (`:111-123`) reads `exc.code` / `exc.type` against `QUOTA_EXHAUSTED_CODES` (`:40-46`) — **OpenAI's codes**. Both halves are api-specific: Anthropic's SDK raises its own exception types, and that code set would silently never match, so a quota-exhausted 429 would go back to being retried four times. Classification belongs to the adapter, not to one shared table. Separately: 520/524 are still absent from `RETRYABLE_STATUS_CODES` (pi-mono `e5d18382a`) — independent of this design |
| `/model`, `/provider`, ACP `session/set_model` | must be able to name the api; an api switch must emit `MODEL_CHANGED` |
| `llm/client.py::_is_gemini` (`515-527`) | **leave it where it is.** It is the ancestor of this design in spirit, but Gemini here speaks Chat Completions over an OpenAI-compatible endpoint — this is a *provider* quirk on an existing wire (pi-mono would call it `compat`), not a wire protocol. Promoting it to an adapter would collapse the very `api`/`provider` distinction §3 exists to keep apart |

Dispatch `gemini-api` by explicit `api_format` before entering the Chat
Completions `_is_gemini` bypass. The native adapter does not inherit that path's
non-streaming restriction.

## 9. Config surface

Use **`{PROVIDER}_API_FORMAT`**: protocol selection follows the credential/endpoint
block rather than global request preferences (`embedding/factory.py:103-115`,
`.env.example:3-5`). One base URL can serve multiple protocols, so configure it
explicitly; infer it from neither URL nor provider name. Per-model overrides wait
until stage 3.

```bash
LLM_PROVIDER=ANTHROPIC
ANTHROPIC_API_FORMAT=anthropic-messages   # unset → openai-completions
```

Native Gemini configuration example (transport gated by §3; enabled when implemented; compatible
endpoints still select `openai-completions`):

```bash
LLM_PROVIDER=GEMINI
GEMINI_API_KEY=...
GEMINI_API_FORMAT=gemini-api
```

- Values: `openai-completions|anthropic-messages|openai-responses|gemini-api`, enabled as
  implemented. Unknown or unimplemented values fail closed and list currently
  valid values. The default preserves existing Chat Completions behaviour.
  **`gemini-api` does not enter the published value range until §3 settles the
  transport** — one value cannot mean two wires in sequence; confirm the spelling
  against whichever transport is chosen.
- `Agentao(..., api_format=...)` must be keyword-only, after `*`, to preserve
  legacy positional arguments.
- `_API_FORMAT` names a global protocol; the prefix only identifies its provider
  block. `ANTHROPIC_API_FORMAT=openai-completions` is valid (`.env.example:18`).
  Credential scrubbing uses exact names (`capabilities/process.py:125`) and does
  not strip this key.
- No new provider layer in `settings.json` for one knob; env and constructor suffice.
- **No model catalogue**: users specify the api. Do not add models.dev and
  model-name prefix heuristics.

## 10. Staged plan

**The main path is stage 0 → one adapter.** Either part of stage 0 may close
the gap and end the work. Only remaining native-cache or thinking needs justify
`anthropic-messages` in stage 1. Responses and Gemini rules stay in appendices A/B/C. Add later adapters one at a
time as needed; dynamic switching and per-model overrides remain deferred.

| Stage | Content | Gate |
|---|---|---|
| **0a** | Provider-neutral volatile ordering fix: stable system + history + a request-only temporary user tail (§2.3), with no new knob | Compare implicit-prefix cache hits and actual cost against today; verify no persisted tail, no missing/duplicate tail on request rebuild, and N ≥ 10 consecutive turns of estimate − actual prompt_tokens without systematic drift with tail size. Stop here if benefits suffice |
| **0b** | Opt-in explicit `cache_control` for one named, supported endpoint; SDK pass-through is verified. Copy-on-mark on the stable prefix, rebuilt per request, never persisted | Compare hits and actual cost against 0a; multi-turn checks that inputs stay unchanged and markers neither accumulate nor exceed 3 explicit markers, reserving 1 automatic-cache slot. **If stage 0 closes the gap, stop; build nothing below** |
| **1** | Extract the existing path and add one `anthropic-messages` adapter, **startup selection only, streaming and non-streaming in v1**. Official SDK; history writes (§5.1), usage mapping (§6.11); its own event state machine and accumulator, delivering text deltas through the callback | Extraction no-op evidence; golden fixtures both ways; §6.2 / §6.3 / §6.7 tests; complete signed-thinking round-trip; streaming/non-streaming response parity, intact tool arguments and usage, cancellation and error handling; cache-benefit checks |
| **2** | Add `gemini-api` (candidate appendices B/C) and `openai-responses` (appendix A) one at a time as needed; settle the shared interface with the first follow-on adapter. Every new protocol ships with native streaming | Bidirectional fixtures, streaming parity, cancellation and errors; pass §12.1's remaining-benefit gate before Gemini's §3 transport/lifetime comparison; skip the comparison if stage 0 suffices; protocol-specific gates in appendices A/B/C |
| **3** | Dynamic switching via `/model`, `/provider`, ACP `session/set_model`; per-model overrides (§9); cross-cutting purge (§8) | Switching clears the state listed in §8 |
| — | Docs twins, `CHANGELOG.md`, `docs/reference/configuration.md` | Ship with the stage that changes behaviour |

**Regression evidence for the extraction is required; a separate PR is only the
cheapest way to get it.** Refactoring `LLMClient` into an adapter and adding the
first adapter in one change makes "did the extraction alter today's behaviour?"
unanswerable by inspection — but a byte-equality test on `_build_request_kwargs`
against the pre-change output answers it just as well, and can live in the same
PR. Take whichever is cheaper; do not take neither.

## 11. Test plan

- **Stage 0 acceptance**: compare 0a against today and 0b against 0a, reporting
  cache hits and actual cost separately. Check no persisted tail, correct rebuilds
  through tools/compaction/retries, and N ≥ 10 turns of `estimate - actual_prompt_tokens` while varying tail size,
  requiring no systematic drift and reporting local tail-estimation error. Across
  multiple 0b turns, inputs stay unchanged, markers exist only in request copies,
  and their count neither accumulates nor exceeds 3 explicit markers, leaving
  1 slot for automatic caching. Keep the SDK pass-through
  probe separate from endpoint acceptance.
- **Stage 1 streaming and non-streaming acceptance**: verify text deltas reach
  the callback before stream completion and the final duck-response matches the
  non-streaming equivalent. Cover fragmented tool arguments, thinking signatures,
  complete usage accumulation, cancellation, mid-stream errors and resource
  closure. Partial responses must not be reported as complete successes, and
  retries must not duplicate text already delivered.

- **Golden fixtures, both directions, per adapter**: a fixed `agent.messages` list
  (including: a compaction summary mid-history, a batch of three tool results, an
  image part, an assistant-first history from the minimal-history rung) → the
  exact request body; and a recorded provider response → the exact duck-type.
- **Usage acceptance reaches the anchor**: run fixtures through response mapping,
  tail-estimate subtraction and `record_api_usage`; assert anchor tokens and
  persistent-prefix length. Protocol-specific fixtures belong in §6 and
  appendices A/B/C rather than adding one list here per protocol.
- **Build inputs from the real SDK models, never `MagicMock`.** This is a repeat
  lesson, not a precaution: `MagicMock` answers `hasattr` for any name, so it
  satisfies every capability probe and hides exactly the breaks an adapter layer
  exists to catch (see the mcp 2.x compat work).
- **One conformance suite, run against every implemented adapter**, asserting the
  duck-type surface enumerated in `_stream_response.py:1-16`. An adapter that
  cannot pass it is not done.
- **Stage 1 extraction no-op test**: test the openai-completions adapter's own
  `_build_request_kwargs` directly: same inputs → byte-identical request to the
  pre-extraction output.

## 12. Open questions for the maintainer

1. **Does §2.3 make §6/appendices A/B/C unnecessary?** If the cheap path delivers the caching
   win, reassess the remaining signature-fidelity and Gemini incremental-output
   benefits independently; cache hits do not fix the current streaming bypass.
   This should be measured before stage 1 is authorized.
2. **How is the vendor SDK packaged?** §10 settles *that* it is the official
   SDK — hand-rolling `httpx` against the REST endpoint buys a maintenance
   surface and loses the SDK's own compat work, which the mcp 1.x/2.x episode
   priced. What is open is packaging: a new optional extra (consistent with the
   `[cli]` / library-only split) or a core dependency. An extra keeps a bare
   `pip install agentao` unchanged, at the cost of one more install path to
   document and test.
3. **Do sub-agents inherit the api?** They inherit `extra_body` today
   (`agents/tools/_wrapper.py`). Same answer presumably, but it must be explicit.
4. **What happens to history on an api switch mid-session?** The purge
   (§8) removes carrier keys, but an Anthropic-signed thinking block and a
   Responses reasoning item are both *unrecoverable* once dropped. Is a switch
   allowed to silently degrade the transcript, or should it warn?

## 13. What would flip the verdict

- **Re-select the Gemini transport** if GenerateContent receives a deprecation
  or shutdown plan, target models require Interactions, or measurement shows no
  need for its distinct caching capability. Stop targeting the older transport
  and apply §3. Today's Legacy label and migration guide already require the
  pre-implementation comparison; do not wait for shutdown to price migration.

- **Option A → B** if any adapter's native semantics cannot fit OpenAI dicts
  with bounded carrier keys, or fail fidelity tests through sanitization,
  compaction and session/replay. Adding `gemini-api` tests this ceiling; protocol
  count alone does not require a history-model migration.
- **Stop at stage 0** if measured cache-hit rates close the gap (§12.1).
- **Reopen the model catalogue decision (§9)** only with evidence that users are
  mis-selecting the api often enough to matter — not because peers have one.

## Appendix A. Translation rules — `openai-responses`

1. **`messages` → `input` items.** Roles map, but tool calls and results become
   top-level items (`function_call`, `function_call_output`), not message fields
   (`openai-responses-shared.ts:328-350`).

2. **The dual-id problem, and the rule that follows from it.** Responses carries
   both a `call_id` (correlates the output) and an item `id`. agentao's history
   has exactly one id slot, and that id must round-trip byte-for-byte. pi-mono's
   answer is a composite: ``id = `${item.call_id}|${item.id}` ``
   (`openai-responses-shared.ts:488`), split back on the way out
   (`:334`, `const [callId] = msg.toolCallId.split("|")`).
   **Adopt the composite; do not add a second id field to the history dict** — a
   new key would have to survive sanitize, compaction, replay and session load,
   and `tool_call_id` matching is what the compaction pairing rules key on
   (`context_manager.py:1219-1221`). If the composite is adopted, add a test that
   a `|` appearing inside a provider's own `call_id` is handled (split on the
   **last** separator, or escape).

3. **Stay stateless in v1: `store: false`** (`openai-responses.ts:318`) and
   `include: ["reasoning.encrypted_content"]` (`:353`). Do **not** adopt
   `previous_response_id`. agentao's history is the single source of truth —
   compaction rewrites it, `/clear` wipes it, replay replays it. A server-side
   conversation id would silently diverge from all three, and the divergence
   would surface as the model remembering something the user cleared.
   Note what stateless does **not** mean: with `store: false` the encrypted
   reasoning items must be **sent back** in the next request's input, which is
   what §5.3's carrier key is for. The saving is that the provider holds no
   conversation, not that less goes over the wire.

4. **Tool schema is flattened** — Responses puts `name`/`parameters` at the item
   level, not nested under `function`. Translate from `to_openai_format()`
   (§5.2); do not fork the schema producer.

5. **`max_output_tokens` has a floor** (16, per `openai-responses.ts:32`).
   agentao passes `max_tokens` straight through today; clamp in the adapter.

6. **Reasoning items must be replayed.** `openai-responses-shared.ts:533-548`
   documents that Azure can omit `encrypted_content` from the per-item event and
   only include it in the terminal response — so the adapter must backfill from
   the terminal payload, not assume the item event carried it. This is exactly
   the class of bug that a single-provider test would never catch.

7. **Streaming is an event-typed stream** (`response.output_item.added`,
   `response.function_call_arguments.delta`, …), not chat deltas. The accumulator
   in `_stream_response.py` is Chat-Completions-shaped
   (`tool_call_key()` reasons about the `index` field, `:71-96`); the Responses
   adapter needs its own accumulator that produces the same duck-type, **not** a
   modification of that one. Keep `_StreamAccumulator` exactly as it is — its
   indexless-provider handling was earned by a real bug (goose #10023).

**Protocol-specific acceptance:** composite ids (including a native id containing
`|`), terminal reasoning backfill, stateless full-history replay and streaming
terminal/non-streaming parity; shared acceptance remains in §11.

## Appendix B. Translation rules — `gemini-api` GenerateContent candidate

This appendix has a readable corroborating implementation: pi-mono `5a3a03a7f`'s
`google-generative-ai.ts` / `google-vertex.ts` drive the same transport's native
streaming (see the anchors above). A corroborating implementation shows a rule is real;
it is not a spec, and it does not decide §3. These GenerateContent candidate rules
require the §3 transport decision; they are not an Interactions specification.
Scope: Gemini Developer API `generateContent` and SSE `streamGenerateContent`,
using the official Google Gen AI SDK (`google-genai`). These are design rules,
not a claim that an adapter or endpoint integration test already exists.

1. **Messages and tools.** Translate history to `contents` with `user` / `model`
   roles and ordered `parts`; lift the initial system prompt to `systemInstruction`.
   Translate canonical tool schemas to `functionDeclarations`, calls to
   `functionCall`, and results to `functionResponse`. Preserve native call ids;
   if absent, assign stable local ids and retain an unambiguous pairing map.
   Same-name parallel calls must not be paired by name alone. Keep execution in
   agentao's tool loop; do not enable a second SDK-managed execution loop.
   [GenerateContent reference](https://ai.google.dev/api/generate-content).

2. **History repairs.** Test the existing mid-history summary and assistant-first
   overflow history (§6.2/§6.3) against this protocol independently. Keep summaries
   at their chronological boundary, folding into an appropriate user content;
   do not promote them into the stable system prefix or split call/result groups.
   Coalesce following notification/tail user parts after all function-response
   parts in the outbound copy, preserving order; test consecutive user inputs
   against the chosen endpoint. Notifications remain persisted, while 0a's tail
   remains request-only.

3. **Signatures.** Preserve signed parts and their order, including empty-text
   parts carrying only a signature. Do not merge or truncate signed parts.
   SDK signature handling assumes full native responses are retained; agentao's
   dict history requires the explicit §5.3 carrier and persistence tests. Feed
   display text separately from the native replay representation.

   Two details from the corroborating implementation. First, **a signature is not
   thinking**: `thought: true` is the only thinking marker, and a signature can ride on
   **any** part (text, `functionCall`, …), which is why pi-mono's `isThinkingPart` reads
   `thought` alone (`google-shared.ts:112-131`) — treating every signed part as thinking
   routes ordinary text into the reasoning display and truncation path. Second, **a
   stream may carry the signature only on a block's first delta** and omit it afterwards:
   the accumulator retains the last non-empty signature within the block and **never
   moves or merges signatures across parts** (`retainThoughtSignature`,
   `google-shared.ts:133-145`). The second is the same class as appendix A.6's Azure
   omission of `encrypted_content`, and a single-provider test never catches it.

   **A missing signature is itself a 400, and agentao's history will be missing one.**
   gemini-cli says so directly: for requests to validate, the **first** functionCall in
   every model turn inside the active loop must carry a `thoughtSignature` or the API
   returns 400; when it is absent it substitutes the placeholder
   `SYNTHETIC_THOUGHT_SIGNATURE = 'skip_thought_signature_validator'`, and only for each
   message's first functionCall (`geminiChat.ts:110,1259-1310`). Three agentao paths
   produce exactly that history: compaction rewrites it, `/resume` restores an older
   session file written before the carrier existed, and minimal-history cuts into a
   tool-call run. **This is the likeliest silent 400 on the Gemini transport**, the same
   class as §6.2; the adapter must substitute a placeholder in the outbound copy or fail
   explicitly, never send it quietly.

   **Signatures do not cross endpoints.** gemini-cli calls `stripThoughtsFromHistory()`
   on an auth switch, and `config.ts:1580-1589` gives the reason: Genai and Vertex have
   incompatible encryption, so history carrying Genai signatures fails against Vertex.
   The carrier therefore belongs in §8's purge union, and agentao's trigger (model **or**
   endpoint change) is already the right shape — this is the evidence that it must cover
   the new carrier, and the technical reason §9 keeps Vertex out of v1: same protocol,
   different endpoint, and the carried signature does not travel.
   [Thought-signature rules](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures).

4. **Streaming and errors.** Implement the native accumulator in the first
   version. Emit visible text incrementally while retaining calls, signatures
   and usage through stream completion. Map finish/block reasons explicitly;
   no candidate or a blocked response must not become a fabricated successful
   empty answer. Test cancellation and mid-stream failure. `describe_error`
   classifies Google SDK exceptions, including quota and asserted context limits;
   do not apply OpenAI's error-code table.

5. **Usage.** Set `prompt_tokens = promptTokenCount`: it already includes cached
   tokens. Preserve `cachedContentTokenCount` separately. Map completion usage to
   `candidatesTokenCount + thoughtsTokenCount` (absent optional counts are zero);
   keep reported totals and detail fields for reconciliation. Do not sum repeated
   cumulative usage snapshots across chunks.
   Unlike §6.11, do not add cache input again. Fixtures assert the final
   `record_api_usage` anchor, including §2.3 tail subtraction, not only fields.
   **The two corroborating implementations write this oppositely, so neither line can be
   copied.** pi-mono's `input` means *uncached* input, so it writes
   `promptTokenCount - cachedContentTokenCount` (`google-generative-ai.ts:231-240`);
   gemini-cli's `inputTokens` means *total* input, so it writes `promptTokenCount`
   directly and reports `cachedTokens` beside it (`event-translator.ts:468-470`). Both are
   self-consistent — the same field name means different things in the two — and together
   they confirm from a second and third independent source that `promptTokenCount` is the
   cache-inclusive total. agentao's `prompt_tokens` wants that **total**, so copying
   pi-mono's line under-reports the cached prefix in the Tier-1 anchor: the same failure
   direction §6.11 warns about, and worse the better the cache works. Check the
   destination field's meaning before copying, not its name.
   [UsageMetadata](https://ai.google.dev/api/generate-content#UsageMetadata).

6. **Caching.** Explicit caching creates resources referenced by `cachedContent`;
   it is not Anthropic's per-block `cache_control`. Initial native transport can
   work without explicit resource management. Enabling it requires a separate
   decision on creation, TTL, reuse and invalidation after compaction or model
   changes, plus measured storage and request costs. Resource handles belong to
   adapter runtime state, not canonical history; session reload must work without
   an old handle. Implicit caching remains compatible with stage 0a.
   [Gemini context caching](https://ai.google.dev/gemini-api/docs/generate-content/caching).

- **Gemini acceptance**: same-name parallel tool-call pairing, mid-history
  summaries, compacted history starts, signature-only empty-text parts, lossless
  signature persistence through session/replay, and terminal streaming usage.
  Verify that cached tokens are not added twice to `promptTokenCount`. Run each
  adapter's protocol-specific fixtures against that adapter.


## Appendix C. Translation rules — Google Interactions API

**No corroborating implementation.** Neither local implementation uses Interactions:
pi-mono (`5a3a03a7f`) has no Interactions adapter in its ten-value `KnownApi` and reaches
GenerateContent from both Google adapters, and gemini-cli (`9450ade79`) reaches
GenerateContent on all three of its access paths. Every rule below therefore comes from
primary documentation with no readable working implementation behind it — an asymmetry
recorded in §3.

This is the other `gemini-api` transport candidate, alongside GenerateContent
in appendix B. It supplies a design for §3's comparison, not authorization to
implement both or a new configuration value. Pass §12.1 first, then choose the
transport before publishing the adapter: if §12.1 shows stage 0 closed the gap,
neither appendix B nor appendix C is built. Use the official `google-genai` SDK;
v1 covers model text/image input, client-executed tools and native streaming.
Managed agents, provider-hosted tools and background execution are outside v1.
Sources below were checked on 2026-09-18; target-endpoint fixtures remain pending.

1. **Stateless by construction.** Every call, including tool continuations and
   retries, explicitly sets `store=false` and omits `previous_interaction_id`.
   Rebuild `input` from current canonical history; resend system instructions,
   tools and generation settings. Compaction, `/clear` and replay remain in
   control of context. Measure stateless implicit caching; explicit caching is
   unavailable in this candidate. Introducing a conversation chain requires
   reopening appendix A.3, not silently changing this adapter's behavior.
   [Interactions overview](https://ai.google.dev/gemini-api/docs/interactions-overview).

2. **Translate steps, not GenerateContent parts.** Map user messages to
   `user_input`, assistant text to `model_output`, and the initial system prompt
   to `system_instruction`. Preserve mid-history summaries at their chronological
   boundary and keep the temporary tail request-only (§2.3). Traverse returned
   `steps` in order: `.output_text` alone can omit text before intervening non-text
   steps. Build the usual duck response while retaining the native sequence for
   replay. Reject unsupported content explicitly rather than losing it.
   [Migration guide](https://ai.google.dev/gemini-api/docs/migrate-to-interactions).

3. **Tools and history ownership.** Translate canonical schemas into Interactions
   function declarations. Map `function_call.id` to the canonical tool-call id,
   and tool results to `function_result.call_id`, with `name` and typed `result`
   content. Preserve ids byte-for-byte and disambiguate same-name parallel calls.
   Stateless continuation requires the user input, all returned model steps and
   the function results. Persist the ordered model-step carrier through §5.1;
   when it is present, emit it once rather than duplicating reconstructed text
   and calls from display fields. Detect history edits that invalidate a carrier;
   never let a stale carrier override compaction. agentao executes the tools.
   [Stateless function calling](https://ai.google.dev/gemini-api/docs/function-calling#stateless-function-calling).

4. **Thinking has its own step.** Preserve complete `thought` steps, including
   `signature` and an absent or empty `summary`; do not flatten them onto function
   calls or treat them as GenerateContent `thoughtSignature` parts. The native
   carrier is JSON-serializable, ordered and untruncated; its display summary can
   remain bounded. Wire it through all §5.1 write sites, sanitization exemptions,
   session/replay and `purge_keys()`. Unknown native step types must fail explicitly
   until their replay semantics are supported.
   [Interactions thinking](https://ai.google.dev/gemini-api/docs/thinking).

5. **Native streaming in v1.** Use `interactions.create(..., stream=True)` and
   accumulate `step.start`, `step.delta`, `step.stop` by step index. Emit visible
   text through `on_text_chunk`; retain argument and signature deltas separately.
   A step stopping is not the interaction finishing: termination is decided by
   `interaction.completed`, `error` or `done` alone — `step.stop` is not a
   finishing signal (the same stream also carries `interaction.created` and
   `interaction.status_update`, eight event types in all; checked 2026-09-18).
   Reconcile terminal status and usage before returning the duck response; do
   not append terminal text a second time. Test cancellation, truncated streams
   and errors after output; never retry by blindly replaying text already emitted.
   [Streaming interactions](https://ai.google.dev/gemini-api/docs/streaming).

6. **Status, errors and usage.** `requires_action` with supported function calls
   maps to `tool_calls`; a completed response maps according to its actual finish
   information. Failure or an unfinished interaction must not become a successful
   empty answer. `describe_error` owns SDK exception classification and asserted
   context-limit extraction. Preserve `total_input_tokens`, `total_cached_tokens`,
   `total_output_tokens`, `total_thought_tokens`, `total_tool_use_tokens` and
   `total_tokens`. Candidate mapping is input → prompt, output + thought →
   completion, cached/tool-use counts retained as details. This is not a verified
   endpoint mapping: freeze it only after cache-hit/miss and thinking fixtures
   establish inclusion semantics. Do not infer them from appendix B's field names
   or sum repeated cumulative stream usage. Assert the resulting §2.3 anchor,
   including tail subtraction; missing usage must not manufacture a zero anchor.
   [Interactions API reference](https://ai.google.dev/api/interactions-api).

**Protocol-specific acceptance:** stateless multi-turn tool execution with all
model steps preserved once; parallel same-name calls; signature-only thoughts;
text before and after non-text steps; summary/notification/temporary-tail order;
compaction, `/clear` and session reload without an interaction id; streamed versus
non-streamed parity; cancellation and mid-stream failure; cache-hit/miss usage
and final anchor values. Pin the tested SDK/API schema and target model, record
unsupported cases, and use these results with appendix B in §3's comparison.
