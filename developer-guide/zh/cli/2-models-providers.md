# 2. 模型与 Provider

CLI 不必重启就能切模型、切 provider、调温度。三个命令搞定全部：`/model` `/provider` `/temperature`。

## "Provider" 是什么

一个 **provider** 就是一组 (`API_KEY`, `BASE_URL`, `MODEL`) 三元组 — 一份凭证指向一个 OpenAI 兼容端点，并附带一个默认模型。Provider 名字是任意的，来自你的 `.env` 文件。

约定是 `XXXX_API_KEY` / `XXXX_BASE_URL` / `XXXX_MODEL`，其中 `XXXX` 是 provider 名（大写）：

```bash
# .env

# 默认 provider（旧的写法只用 OPENAI_*）
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4

# 想加多少加多少
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

只有当某个 provider 的**三个 env var 都齐全**时它才出现在列表里。`GEMINI_API_KEY` 设了但 `GEMINI_BASE_URL` 没设，`GEMINI` 就不会显示。

## `/provider` — 列出或切换

```text
> /provider
```

列出所有检测到的 provider，当前使用的标 ✓，并打印用法提示。

```text
> /provider GEMINI
```

一次性切换凭证 + base URL + 默认模型。**对话历史保留** — 只换 LLM 客户端。下一轮就发到新 provider。

常见错误：

| 提示 | 怎么修 |
|---|---|
| `No providers found in .env` | 至少配一组 `XXXX_API_KEY` + `XXXX_BASE_URL` + `XXXX_MODEL` |
| `No API key found for provider 'GEMINI'` | `GEMINI_API_KEY` 没设 |
| `No base URL configured for provider 'GEMINI'` | `GEMINI_BASE_URL` 没设 |
| `No model configured for provider 'GEMINI'` | `GEMINI_MODEL` 没设 |

## `/model` — 在当前 provider 里列 / 切模型

```text
> /model
```

显示当前模型，然后查询 provider 的 `/models` 端点，分组列出：

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

清单是从 provider 实际拉的，不是硬编码的，所以反映的是你这个端点真实暴露出来的模型。切换：

```text
> /model claude-sonnet-4-6
```

`/model` 的作用域是**当前** provider。要的模型不在这里，先 `/provider` 切过去再 `/model`。

::: tip 跨厂商的模型名
一些 provider 用 OpenAI 兼容 API 转发 Claude 模型。如果你在非 Anthropic provider 上看到 `claude-*` 也是正常的 — 那是 provider 在做代理。
:::

## `/temperature` — 采样温度

```text
> /temperature        # 看当前值
Temperature: 1.0
> /temperature 0.2    # 设置
Temperature changed from 1.0 to 0.2
```

范围：`0.0` 到 `2.0`。低 = 更确定，高 = 更发散。各 provider 默认值不同（聊天典型为 1.0）。

修改是**会话级**。重启 CLI 会回到 provider 默认值。要持久化默认温度，写到 `.env`：

```bash
LLM_TEMPERATURE=0.3
```

需要 CLI 没有对应命令的请求参数 —— `reasoning_effort`、`top_p`、`seed`、`response_format`，或某个 provider 专有字段？把 `LLM_EXTRA_BODY` 设成 JSON 对象；它会被原样转发给 LLM `.create()`（并被子 agent 继承）：

```bash
LLM_EXTRA_BODY='{"reasoning_effort":"high"}'
```

解析 / 脱敏细节见 [附录 B](/zh/appendix/b-config-keys)。

## 什么时候该切什么

| 情况 | 怎么做 |
|---|---|
| 同任务，想用更聪明的模型 | `/model <更大>`（不换 provider） |
| 同任务，想省钱 | `/model <更小>` |
| 换厂商 | `/provider <NAME>`（历史保留，凭证切换） |
| 输出太随机 / 开始幻觉 | `/temperature 0.2` |
| 输出太死板 / 重复 | `/temperature 1.2` |
| 会话进行中 cost 在飙升 | 下一轮前 `/model` 切到小一号 |

## 容易踩的坑

- **工具调用进行中切换**：agent 还在迭代工具时切了模型或 provider，下一次迭代就用新客户端。多数场景没事，但不同厂商之间 tool-call 格式有差异，可能让中间一轮乱掉 — 看到这种情况切完后直接 `/clear`。
- **历史会跨切换保留**：通常你就要这个，但意味着新模型会看到上一个模型留下的 tool-use 痕迹。有些模型对消息格式要求严，会报错。
- **"列得出来" 不等于 "调得通"**：`/model` 列的是 provider `/models` 上报的全部内容，里面有 embedding 模型、你没权限的 fine-tune 之类，下一轮会失败。看到错误换一个就行。

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 切完模型后保证它别在新模型上跑危险工具 | [3. 权限与模式](./3-permissions-modes) |
| 切到小模型后 context 爆了怎么救 | [7. 上下文与状态](./7-context-status) |
| 跑本地模型 | 按上面写好 `LOCAL_*` env，再 `/provider LOCAL` |

---

::: info 这一章在体系里的位置
CLI 用的是 `cli.agent.set_provider(api_key, base_url, model)` 和 `cli.agent.set_model(name)` — 这两个都是 `Agentao` 实例的公开方法，嵌入式宿主也可以调，效果完全一致（同一份内存里的会话）。嵌入式 API 的等价用法和线程注意事项见 [Part 2.5 · 运行时切换 LLM](/zh/part-2/5-runtime-llm-switch)。
:::

::: tip 真相源头
命令语法：`/help` 与 [`agentao/cli/help_text.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/help_text.py)。Provider 发现逻辑：[`agentao/cli/commands.py:_list_providers_from_env`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py)。
:::
