# 4. Plan 模式

Plan 模式是个"先想再做"的循环。Agent 进入**只读**状态，把计划写到一个文件里，只有你明确批准后才切回执行模式。任务大、风险高、思路不清的时候用它。

## 模型

```
[normal] ── /plan ──→ [plan mode]
                        │  只读 · 草稿写到 .agentao/plan.md
                        │
                        ├── /plan implement ──→ [normal]（恢复原模式，把计划交给 agent）
                        └── /plan clear     ──→ [normal]（恢复原模式，计划归档）
```

Plan 模式是独立的一档权限 — 与 `read-only` `workspace-write` `full-access` 平行。进入时把当前模式存起来，退出时还原。

## `/plan` — 切换 plan 模式

```text
> /plan
[Plan mode ON]  (read-only; LLM will plan, not execute)
Ask what to plan. When done: /plan implement · /plan clear
```

Plan 模式开启时：

- Agent 被强制切到只读 — 不能 `write_file`、`replace`、`run_shell_command`、不能联网
- 不会弹确认 UI（因为危险工具压根够不着）
- Agent 一边思考一边把计划写到 `.agentao/plan.md`
- `/mode` 被**锁住**（会提示 "Cannot change permission mode while in plan mode"）

接下来你用普通对话告诉 agent 该计划什么。多轮对话、追问、修改都可以。计划文件会随着对话演化被改写。

```text
> /plan
[Plan mode ON]
> 我们要加 OAuth 登录。先把 codebase 走一遍提一份计划。
[agent 读文件、思考、写 .agentao/plan.md]
> 复用现有的 session middleware，别再加一份新的。
[agent 重写 .agentao/plan.md]
```

## `/plan`（已开启时）— 查看状态 + 草稿

```text
> /plan          # 已经在 plan 模式
[plan mode is ON]
Saved plan: .agentao/plan.md

# OAuth integration
1. ...
2. ...
```

跟 `/plan show` 一样，外加一句"你还在 plan 模式"的提醒。

## `/plan show` — 显示已保存的计划

```text
> /plan show
```

打印 `.agentao/plan.md`（如果 `/markdown` 开着会渲染成 Markdown）。无论 plan 模式开没开都能用 — `/plan implement` 之后回看以前的计划很方便。

## `/plan implement` — 退出 plan 模式开始执行

```text
> /plan implement
Plan mode OFF. Permission mode: workspace-write

Current plan (.agentao/plan.md):
# OAuth integration
1. ...
2. ...

Ask the agent to implement the plan above.
```

发生了什么：

1. plan 模式标记清除
2. 进入 plan 之前的权限模式被还原
3. 计划内容重新打印一次，让下一条消息有视觉上下文
4. 计划文件**保留** — agent 拿到了，你之后 `/plan show` 也能再看

然后你说"开始"、"实施第一步"之类。Agent 正常调工具，把计划当持久化参考。

## `/plan clear` — 丢弃并归档

```text
> /plan clear
Plan archived and cleared. Plan mode OFF.
```

发生了什么：

1. 当前 `.agentao/plan.md` 被搬到带时间戳的归档（用 `/plan history` 找回）
2. 如果 plan 模式开着，会关掉，原模式还原
3. Agent 失去这份计划 — 下一轮从空开始

什么时候用：

- 觉得计划不对，想从头来
- 一个任务做完，要给下一个计划留个干净起点
- 要关 plan 模式但又不想实施这个计划

## `/plan history` — 浏览历史归档

```text
> /plan history

Plan history (most recent first)

  20260505-2240-oauth
    加 OAuth 登录。复用现有 session middleware。改路由...

  20260505-1830-refactor-tools
    把 ToolRegistry 拆成注册和分发...
```

每条归档显示文件 stem（带时间戳）+ 该计划 `## Context` 段落的摘要。要看全文就直接打开 `.agentao/plan-history/` 下的对应文件。

## 什么时候该用 plan 模式

| 情况 | plan 模式怎么帮你 |
|---|---|
| 任务横跨多个文件 | 先规划能避免工具循环走死路 |
| 副作用让你紧张 | 结构上就是只读的，危险工具根本碰不到 |
| 在用更小 / 更便宜的模型 | plan 模式让模型保持在轨上（不会工具左右横跳） |
| 在评审别人的设计，再决定做不做 | 计划文件是个能分享的产物 |
| 准备跨多次会话实施 | 计划留在 `.agentao/plan.md`，`/clear` 也带不走 |

## 容易踩的坑

- **`/plan implement` 不会自动开干** — 它只是退出 plan 模式 + 打印计划。还得你说一句"开始"才会动。
- **手动改 `.agentao/plan.md`** — 没问题，agent 会重读。但如果你在 agent 思考中途改，会被覆盖。在两轮之间改。
- **plan 模式中 `/clear`** — 新会话从*普通*模式开始（plan 标记是会话级的）。计划文件还在磁盘上，`/plan show` 仍能看。
- **`/plan clear` 和 `/clear` 别混了** — 不同动词。`/clear` 轮换对话；`/plan clear` 归档计划文件。

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 在 plan 中激活某个 skill | [5. Skills 与 Crystallize](./5-skills-crystallize) |
| 把规划好的做法存成可复用的 skill | [5. Skills 与 Crystallize](./5-skills-crystallize) → `/crystallize` |
| 看驱动 plan 模式的 prompt 端实现 | [Part 5.6 · 系统提示定制](/zh/part-5/6-system-prompt) |

---

::: info 这一章在体系里的位置
Plan 模式实现在 [`agentao/plan/controller.py`](https://github.com/jin-bo/agentao/blob/main/agentao/plan/controller.py) 里，嵌入式 API 暴露同样的能力：`agent.enter_plan_mode()` / `agent.exit_plan_mode()` / `agent.show_plan()`。IDE 宿主可以用同一份产物驱动同一套工作流。
:::

::: tip 真相源头
命令语法：`/help`。行为：[`agentao/cli/commands.py:handle_plan_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py)。计划文件默认路径 `.agentao/plan.md`（[`agentao/plan/session.py`](https://github.com/jin-bo/agentao/blob/main/agentao/plan/session.py)）。
:::
