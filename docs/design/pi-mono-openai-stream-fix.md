# pi-mono OpenAI-compatible stream fix (v0.73.1 / Unreleased)

**Status:** Reference + agentao-side gap analysis. Drafted 2026-05-08 after reviewing four upstream commits in `../pi-mono` between v0.73.0 and v0.74.0.
**Audience:** Agentao maintainers responsible for `agentao/llm/client.py` and anyone considering OpenAI-compatible providers (DeepSeek, Kimi, MiniMax, LM Studio, Xiaomi MiMo, Together, chutes.ai, …).
**Companion:** `pi-mono-openai-stream-fix.zh.md`.
**Method:** Read each upstream commit in full, cross-reference agentao's current streaming path (`agentao/llm/client.py:730-814`), report the gaps as concrete file:line risks rather than generic recommendations.

## TL;DR

pi-mono shipped four related changes that together harden its OpenAI-compatible streaming layer:

1. **`6b271842` — `fix(ai): handle mixed chat completion deltas` (#4228).** Replaces a single-cursor `currentBlock` accumulator with separate per-block accumulators (text / thinking / per-tool-call). Fixes data corruption when one upstream `delta` carries `content` + `reasoning_content` + multiple parallel `tool_calls`.
2. **`31f5c232` — `fix(ai): handle OpenAI Responses reasoning text deltas` (#4191).** Adds `response.reasoning_text.delta` event handling to the Responses API path; falls back from `summary[]` to `content[]` at item-end.
3. **`783e96a1` — `fix(ai): disable OpenAI reasoning where supported`.** Tags GPT-5.x family models with `thinkingLevelMap.off = "none"` so a `reasoning.effort: "none"` value is sent when thinking is disabled, instead of relying on default behavior.
4. **`9eb126e7` — `docs(ai): document interleaved stream events`.** Promotes the contract: stream events for different content blocks are not guaranteed contiguous; consumers must use `contentIndex` as the anchor.

For agentao the most important takeaway is **#1**. Agentao's streaming consumer (`agentao/llm/client.py:774-790`) keys tool calls by `tc_delta.index` only and does not match by `id`; a provider that emits `id` without `index`, that changes `id` across chunks for the same `index`, or that omits `index` entirely for a parallel call will cause silent argument loss or tool-call merging.

Agentao does not currently use the OpenAI Responses API (no `responses.create` in the codebase) so #2 and #3 are advisory. #4 is conceptual — agentao does not surface block-level start/delta/end events to its consumers, so the contract change does not affect its public surface.

## 1. Mixed chat-completion deltas (`6b271842`)

### What pi-mono fixed

Before, `packages/ai/src/providers/openai-completions.ts` tracked a single `currentBlock` cursor for the entire stream:

```ts
let currentBlock: TextContent | ThinkingContent | StreamingToolCallBlock | null;
```

Whenever a delta of a different type arrived, `finishCurrentBlock(currentBlock)` was called and a new block opened. This breaks on three real-world inputs that some OpenAI-compatible servers emit:

| Input shape | Failure mode |
|---|---|
| Same `delta` carries `content` + `reasoning_content` (chutes.ai) | Text block gets `text_end`'d when reasoning arrives, then a new text block on the next text delta — single text fragment fragmented into multiple blocks. |
| Same `delta.tool_calls` array contains two parallel calls (`index: 0` and `index: 1`) | The single cursor flips between them on every chunk, so each tool's `arguments` lose deltas to the other. |
| Tool-call delta arrives with only `id` (no `index`), or the `id` for a given `index` changes mid-stream | Cursor identity check fails, treats it as a new tool, prior tool gets `toolcall_end`'d with partial args. |

The new implementation in `6b271842` replaces the cursor with **four parallel accumulators** (PR title: `separate-accumulators`):

```ts
let textBlock: TextContent | null = null;
let thinkingBlock: ThinkingContent | null = null;
const toolCallBlocksByIndex = new Map<number, StreamingToolCallBlock>();
const toolCallBlocksById   = new Map<string, StreamingToolCallBlock>();
```

Each delta type uses an `ensure*Block()` helper that opens the block on first use and reuses it thereafter. `ensureToolCallBlock()` looks up by `index` first, falls back to `id`, creates if neither hits, and **back-fills both maps** as later chunks reveal the missing key. Once an `id` is established for a given `index`, later id changes on that index are ignored.

End-of-stream changed from "close the current cursor" to **iterate every accumulator**:

```ts
for (const block of blocks) finishBlock(block);
```

### Test coverage

`packages/ai/test/openai-completions-tool-choice.test.ts:555-787` (added in the same commit) exercises a 3-chunk stream that, in the same `delta`, carries text + reasoning + four parallel tool calls (two with `index`, two with `id` only). Asserts:

- `text_start` × 1, `text_end` × 1, `text_delta` × 3 (text not fragmented across reasoning interrupts)
- `thinking_start` / `thinking_end` × 1 each
- `toolcall_start` / `toolcall_end` × 4 each — every parallel tool call survives
- Final `arguments` parsed correctly per tool (`{path: "README.md"}`, `{pattern: "TODO", path: "src"}`, …)
- `partialArgs` / `streamIndex` scratch fields stripped from the final block

### Agentao current state

`agentao/llm/client.py:774-790`:

```python
if delta and delta.tool_calls:
    for tc_delta in delta.tool_calls:
        idx = tc_delta.index
        if idx not in tool_calls_data:
            tool_calls_data[idx] = {"id": "", "name": "", "arguments": ""}
        if tc_delta.id:
            tool_calls_data[idx]["id"] = tc_delta.id
        if tc_delta.function:
            if tc_delta.function.name:
                tool_calls_data[idx]["name"] += tc_delta.function.name
            if tc_delta.function.arguments:
                tool_calls_data[idx]["arguments"] += tc_delta.function.arguments
```

This is correct for OpenAI's first-party Chat Completions API and most well-behaved compatibles, because:

- Text and reasoning are singletons keyed only by type (`content_parts`, `reasoning_parts` lists, `agentao/llm/client.py:734-735`) — pi-mono's text/thinking fragmentation bug never applies.
- Tool calls are keyed by integer `idx` from `tc_delta.index`. OpenAI guarantees `index` on every tool-call delta.

Two latent risks if agentao starts targeting non-first-party servers:

1. **Provider emits `tc_delta.id` without `index`.** `idx = tc_delta.index` becomes `None`; `tool_calls_data[None]` collects all such deltas into one synthetic call. The pi-mono `toolCallBlocksById` map is the canonical fix.
2. **Provider re-uses the same `index` for what is logically a new tool call (or changes `id` mid-stream for the same `index`).** Current code `+=` the `name` and `arguments` strings — a name change becomes string concatenation, an arguments restart becomes invalid JSON. This is the case pi-mono's "ignore later id changes once first id is established" rule defends against.

### Verdict for agentao

**Backlog** until agentao actually ships an OpenAI-compatible provider config that exhibits one of the failure shapes. None of the providers users currently configure (OpenAI proper, generic OpenAI-compatible per `OPENAI_BASE_URL`) is known to send `tool_calls` without `index`. When the provider list grows (DeepSeek thinking models, Kimi K2 P6, MiniMax M2.7, LM Studio), revisit. The fix is ~30 lines: add a parallel `by_id` dict, look up by `index` first then by `id`, back-fill both on partial-key deltas.

## 2. Responses API reasoning text deltas (`31f5c232`)

### What pi-mono fixed

`packages/ai/src/providers/openai-responses-shared.ts` previously only handled `response.reasoning_summary_text.delta`. yaanfpv (#4191) reported that LM Studio and other Responses-API-compatible servers emit `response.reasoning_text.delta` for the same purpose. New branch:

```ts
} else if (event.type === "response.reasoning_text.delta") {
    if (currentItem?.type === "reasoning" && currentBlock?.type === "thinking") {
        currentBlock.thinking += event.delta;
        stream.push({ type: "thinking_delta", contentIndex: blockIndex(), delta: event.delta, partial: output });
    }
}
```

Item-end fallback also changed: when the reasoning item ends, if `summary[]` is empty fall back to `content[]` text, otherwise keep what was streamed:

```ts
const summaryText = item.summary?.map(s => s.text).join("\n\n") || "";
const contentText = item.content?.map(c => c.text).join("\n\n") || "";
currentBlock.thinking = summaryText || contentText || currentBlock.thinking;
```

Previously the line was `currentBlock.thinking = item.summary?.map(...) || ""` which **wiped** any streamed thinking when the item ended without a summary.

### Agentao current state

`agentao/llm/client.py` uses `client.chat.completions.create` only. There is no Responses API consumer in the codebase. Agentao's `reasoning_content` accumulator (`agentao/llm/client.py:771-772`) is the Chat Completions equivalent (DeepSeek / Kimi / MiniMax field).

### Verdict for agentao

**Not applicable today.** If agentao ever adds a Responses API path (e.g., to consume GPT-5.x with native reasoning surfaces), this fix is a known-good reference: handle both `response.reasoning_summary_text.delta` and `response.reasoning_text.delta`, and prefer streamed content over post-hoc summary on item-end.

## 3. Disable reasoning explicitly (`783e96a1`)

### What pi-mono fixed

In `packages/ai/scripts/generate-models.ts`, GPT-5.x family models on the Responses API are now tagged so that "thinking off" maps to the explicit string `"none"`:

```ts
const OPENAI_RESPONSES_NONE_REASONING_MODELS = new Set([
  "gpt-5.1", "gpt-5.2", "gpt-5.3-codex",
  "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.5",
]);

if (model.api === "openai-responses" &&
    model.provider === "openai" &&
    OPENAI_RESPONSES_NONE_REASONING_MODELS.has(model.id)) {
    mergeThinkingLevelMap(model, { off: "none" });
}
```

Combined with the existing thinking-level dispatch, requests with thinking off now send `reasoning.effort: "none"` for these models. Without this, GPT-5.x defaults to an internal reasoning level even when the user has thinking disabled — wasted reasoning tokens billed silently.

### Agentao current state

Agentao does not expose a thinking-on/off toggle and does not send `reasoning.effort`. It also does not call the Responses API. The cost concern only materializes if both are added.

### Verdict for agentao

**Not applicable today.** When (if) agentao adds thinking control, the lookup table above is reusable verbatim for GPT-5.x.

## 4. Interleaved-events contract (`9eb126e7`)

### What pi-mono added

Two lines of README:

> Streaming events for different content blocks are not guaranteed to be contiguous. Providers may emit deltas for text, thinking, and tool calls in the same upstream chunk, and pi may surface corresponding events interleaved … Consumers must use `contentIndex` to associate each delta/end event with its block and must not assume that a block's `*_start`/`*_delta`/`*_end` sequence is uninterrupted by events for other blocks.

This formalizes the design choice in `6b271842`: instead of buffering and re-ordering, pi forwards the upstream interleaving as-is, and `contentIndex` is the disambiguator.

### Agentao current state

Agentao does not emit block-level start/delta/end events. Its host-facing streaming surface is `on_text_chunk(delta_text: str)` — text-only, called per chunk during the loop in `agentao/llm/client.py:764-766`. Reasoning, tool calls, and finish reasons are returned all-at-once at the end of the streaming loop via `_StreamResponse`. There is no public per-block sequencing contract to break.

### Verdict for agentao

**Not applicable today.** If agentao later adds a richer streaming event surface (per-block start/delta/end on `EventStream`), adopt the same interleaving contract from day one — buffering-and-reordering is a trap, not a feature.

## Cross-reference table

| pi-mono commit | agentao file:line | Risk today | Action |
|---|---|---|---|
| `6b271842` mixed deltas | `agentao/llm/client.py:774-790` | Low — only triggers on non-first-party servers that emit `tool_calls` without `index` or change `id` mid-stream | Backlog. Revisit when adding a provider known to misbehave. |
| `31f5c232` Responses reasoning_text | (no consumer) | None | Reference for future Responses API support. |
| `783e96a1` disable reasoning | (no consumer) | None | Reference for future thinking-toggle support. |
| `9eb126e7` interleaved contract | (no public block events) | None | Reference for future event-stream redesign. |

## Honest meta-note

The pi-mono `separate-accumulators` rewrite is technically clean and the test it ships is the kind of test agentao should write *before* it adopts the change, not after. The current single-`index`-keyed accumulator in agentao is **not buggy** — it is **narrower in compatibility surface**. Borrowing the rewrite preemptively would be over-engineering; keep it on the backlog and let the trigger be a real provider that breaks.
