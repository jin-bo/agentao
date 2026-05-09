# pi-mono OpenAI 兼容流修复（v0.73.1 / Unreleased）

**状态：** 参考记录 + agentao 侧差距分析。2026-05-08 起草，基于 `../pi-mono` 在 v0.73.0 → v0.74.0 之间的四个相关提交。
**读者：** 维护 `agentao/llm/client.py` 的人，以及任何打算接入 OpenAI 兼容 provider（DeepSeek、Kimi、MiniMax、LM Studio、小米 MiMo、Together、chutes.ai 等）的人。
**配套文档：** `pi-mono-openai-stream-fix.md`（英文版）。
**方法：** 完整阅读每个上游提交，对照 agentao 当前的 streaming 实现（`agentao/llm/client.py:730-814`），把差距落到具体的 file:line 风险，而不是泛泛建议。

## 摘要

pi-mono 用四个互相关联的改动加固了它的 OpenAI 兼容 streaming 层：

1. **`6b271842` — `fix(ai): handle mixed chat completion deltas`（#4228）。** 把单游标 `currentBlock` 累加器换成 text / thinking / per-tool-call 多个独立累加器。修复一个上游 `delta` 同时携带 `content` + `reasoning_content` + 多个并行 `tool_calls` 时的数据损坏。
2. **`31f5c232` — `fix(ai): handle OpenAI Responses reasoning text deltas`（#4191）。** Responses API 路径新增 `response.reasoning_text.delta` 事件处理；item 结束时从 `summary[]` fallback 到 `content[]`。
3. **`783e96a1` — `fix(ai): disable OpenAI reasoning where supported`。** 给 GPT-5.x 系列模型打上 `thinkingLevelMap.off = "none"`，让"思考关闭"时显式发 `reasoning.effort: "none"`，而不是依赖默认行为。
4. **`9eb126e7` — `docs(ai): document interleaved stream events`。** 把契约写进 README：不同 block 的 stream 事件**不保证连续**，消费方必须用 `contentIndex` 作锚。

对 agentao 来说最关键的是 **#1**。agentao 的流式消费方（`agentao/llm/client.py:774-790`）只用 `tc_delta.index` 给 tool call 建索引，不按 `id` 匹配。如果某个 provider 发 `id` 但不发 `index`、对同一个 `index` 中途改 `id`、或对并行调用完全省略 `index`，会导致 args 静默丢失或 tool call 互相合并。

agentao 当前不使用 OpenAI Responses API（代码里没有 `responses.create`），所以 #2 和 #3 仅作参考。#4 是概念性的——agentao 不向消费方暴露 block 级的 start/delta/end 事件，所以契约变更不影响公开接口。

## 1. 混合 chat-completion delta（`6b271842`）

### pi-mono 修了什么

之前 `packages/ai/src/providers/openai-completions.ts` 用单游标跟踪整个流：

```ts
let currentBlock: TextContent | ThinkingContent | StreamingToolCallBlock | null;
```

每次来不同类型的 delta，就 `finishCurrentBlock(currentBlock)` 后开新块。这在某些 OpenAI 兼容服务器发的真实输入上会出错：

| 输入形态 | 失败模式 |
|---|---|
| 同一个 `delta` 同时含 `content` + `reasoning_content`（chutes.ai） | 文本块在 reasoning 来时被强制 `text_end`，下次文本来时又开新文本块——一段连续文本被切成多个 block。 |
| 同一个 `delta.tool_calls` 数组里有两个并行调用（`index: 0` 和 `index: 1`） | 单游标在它们之间反复切换，每个工具的 `arguments` 都会丢一部分给对方。 |
| tool-call delta 只发了 `id` 没有 `index`，或者同一个 `index` 中途换了 `id` | 游标的同一性判断失败，被当成新工具，前一个工具被 `toolcall_end` 时只拿到部分 args。 |

`6b271842` 把游标换成**四个并行累加器**（PR 标题：`separate-accumulators`）：

```ts
let textBlock: TextContent | null = null;
let thinkingBlock: ThinkingContent | null = null;
const toolCallBlocksByIndex = new Map<number, StreamingToolCallBlock>();
const toolCallBlocksById   = new Map<string, StreamingToolCallBlock>();
```

每类 delta 走自己的 `ensure*Block()`：第一次用时建块，之后复用。`ensureToolCallBlock()` 先按 `index` 查、再按 `id` 查、都没命中就建新块，并且**两边映射都登记**；后续 chunk 补全任一键时**回填**另一边映射。一旦 `index` 上确立了 `id`，后续在同一 `index` 上的 `id` 变更被忽略。

流结束时也从"关闭当前游标"改成**遍历所有累加器**：

```ts
for (const block of blocks) finishBlock(block);
```

### 测试覆盖

`packages/ai/test/openai-completions-tool-choice.test.ts:555-787`（同一提交里加的）跑了一个 3 chunk 的流，每个 chunk 在同一个 `delta` 里同时携带 text + reasoning + 4 个并行 tool call（2 个有 `index`，2 个只有 `id`）。断言：

- `text_start` × 1，`text_end` × 1，`text_delta` × 3（文本不会被 reasoning 打断切块）
- `thinking_start` / `thinking_end` 各 1
- `toolcall_start` / `toolcall_end` 各 4——并行 tool call 全保留
- 每个工具最终的 `arguments` 完整 parse（如 `{path: "README.md"}`、`{pattern: "TODO", path: "src"}` 等）
- 完成后 `partialArgs` / `streamIndex` 这些 scratch 字段从 block 上剥掉

### agentao 当前状态

`agentao/llm/client.py:774-790`：

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

对 OpenAI 一方 Chat Completions API 和大多数行为良好的兼容服务器是正确的，因为：

- 文本和 reasoning 是按类型单例累加（`content_parts`、`reasoning_parts` 列表，`agentao/llm/client.py:734-735`）——pi-mono 的 text/thinking 切片 bug 在这里不会触发。
- tool call 按 `tc_delta.index` 整数索引。OpenAI 保证每个 tool-call delta 都带 `index`。

如果 agentao 开始接入非一方服务器，有两个潜在风险：

1. **provider 发 `tc_delta.id` 但不发 `index`。** `idx = tc_delta.index` 变成 `None`，`tool_calls_data[None]` 把所有这类 delta 收进同一个虚拟 call。pi-mono 的 `toolCallBlocksById` map 是规范修法。
2. **provider 在逻辑上是新工具的位置复用同一个 `index`（或同一个 `index` 中途改 `id`）。** 当前代码对 `name` 和 `arguments` 做 `+=`——name 变更变成字符串拼接，arguments 重启变成非法 JSON。pi-mono 的"首次确立 id 后忽略后续 id 变更"规则正好防这个。

### 对 agentao 的处置

**Backlog**，等到 agentao 真的接入了某个表现出上述失败形态的 OpenAI 兼容 provider 再做。当前用户配置的 provider（OpenAI 一方、走 `OPENAI_BASE_URL` 的通用兼容服务）没有已知的"`tool_calls` 不带 `index`"行为。等 provider 列表扩充（DeepSeek 思考模型、Kimi K2 P6、MiniMax M2.7、LM Studio）时再回头看。修法约 30 行：加一个并行的 `by_id` dict，先查 `index` 再查 `id`，partial-key delta 时回填两边。

## 2. Responses API reasoning text deltas（`31f5c232`）

### pi-mono 修了什么

`packages/ai/src/providers/openai-responses-shared.ts` 之前只处理 `response.reasoning_summary_text.delta`。yaanfpv（#4191）报告 LM Studio 等 Responses API 兼容服务器发的是 `response.reasoning_text.delta`。新增分支：

```ts
} else if (event.type === "response.reasoning_text.delta") {
    if (currentItem?.type === "reasoning" && currentBlock?.type === "thinking") {
        currentBlock.thinking += event.delta;
        stream.push({ type: "thinking_delta", contentIndex: blockIndex(), delta: event.delta, partial: output });
    }
}
```

item 结束时的 fallback 也变了：reasoning item 结束时，如果 `summary[]` 为空，fallback 到 `content[]` 文本，否则保留已经流过来的内容：

```ts
const summaryText = item.summary?.map(s => s.text).join("\n\n") || "";
const contentText = item.content?.map(c => c.text).join("\n\n") || "";
currentBlock.thinking = summaryText || contentText || currentBlock.thinking;
```

之前那行是 `currentBlock.thinking = item.summary?.map(...) || ""`——item 结束时没 summary 会**清空**已经流过来的 thinking。

### agentao 当前状态

`agentao/llm/client.py` 只用 `client.chat.completions.create`，整个代码库里没有 Responses API 消费方。agentao 的 `reasoning_content` 累加（`agentao/llm/client.py:771-772`）对应的是 Chat Completions 的字段（DeepSeek / Kimi / MiniMax 风格）。

### 对 agentao 的处置

**目前不适用。** 如果 agentao 之后加 Responses API 路径（比如直接消费 GPT-5.x 的原生 reasoning 接口），这个修法是已知可用的参考：同时处理 `response.reasoning_summary_text.delta` 和 `response.reasoning_text.delta`，item 结束时优先保留流过来的内容而不是用事后 summary 覆盖。

## 3. 显式关闭 reasoning（`783e96a1`）

### pi-mono 修了什么

在 `packages/ai/scripts/generate-models.ts` 里，Responses API 上的 GPT-5.x 模型现在被打上"思考关闭 = 字符串 `"none"`"：

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

加上既有的 thinking-level 派发，思考关闭时这些模型就会发 `reasoning.effort: "none"`。如果不发，GPT-5.x 会按内部默认走某个 reasoning 等级——用户已经把思考关了，但还是被静默扣 reasoning token。

### agentao 当前状态

agentao 没有暴露思考开关，也没发 `reasoning.effort`，并且不调 Responses API。只有这两件事都加了，成本风险才会出现。

### 对 agentao 的处置

**目前不适用。** 等（如果）agentao 加思考控制时，上面这张表可以原样复用给 GPT-5.x。

## 4. 交错事件契约（`9eb126e7`）

### pi-mono 加了什么

README 里两行：

> 不同 content block 的 streaming 事件不保证连续。provider 可能在同一个上游 chunk 里同时发 text、thinking、tool call 的 delta，pi 也会把对应事件交错地透出……消费方必须用 `contentIndex` 把每个 delta/end 事件关联到对应的 block，不能假设一个 block 的 `*_start`/`*_delta`/`*_end` 序列不会被其它 block 的事件打断。

这把 `6b271842` 的设计选择正式写成契约：pi 不缓冲不重排，原样把上游交错透出去，`contentIndex` 是消歧的唯一锚。

### agentao 当前状态

agentao 不发 block 级 start/delta/end 事件。它面向 host 的 streaming 接口是 `on_text_chunk(delta_text: str)`——只针对文本，在 `agentao/llm/client.py:764-766` 的循环里逐 chunk 调用。reasoning、tool call、finish reason 都在流式循环结束时通过 `_StreamResponse` 一次性返回。没有公开的 per-block 序列契约可以被打破。

### 对 agentao 的处置

**目前不适用。** 如果 agentao 之后加更丰富的 streaming 事件接口（在 `EventStream` 上发 per-block start/delta/end），从一开始就采用同样的"原样交错"契约——缓冲并重排是坑，不是特性。

## 交叉引用表

| pi-mono 提交 | agentao file:line | 当前风险 | 行动 |
|---|---|---|---|
| `6b271842` 混合 delta | `agentao/llm/client.py:774-790` | 低——只在非一方服务器发不带 `index` 的 `tool_calls` 或中途改 `id` 时触发 | Backlog。等接入已知会出问题的 provider 时再做。 |
| `31f5c232` Responses reasoning_text | （无消费方） | 无 | 未来支持 Responses API 时的参考。 |
| `783e96a1` 关闭 reasoning | （无消费方） | 无 | 未来加思考开关时的参考。 |
| `9eb126e7` 交错契约 | （无公开 block 事件） | 无 | 未来重做事件流接口时的参考。 |

## 元结论（诚实版）

pi-mono 的 `separate-accumulators` 重写技术上很干净，配套的测试是 agentao 应该在**采纳之前**写的，而不是采纳之后。agentao 现在按单 `index` 索引的累加器**不是有 bug**，而是**兼容面更窄**。提前移植是过度工程；先放进 backlog，让"某个真实 provider 把 agentao 拽崩"成为触发条件。
