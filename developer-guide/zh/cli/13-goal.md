# 13. 长任务目标

`/goal` 把 CLI 变成一个长任务循环。你只声明一次目标，Agentao 就一轮接一轮地驱动 agent 朝它推进，不用你反复输入"继续"。agent 报告目标**完成**（complete）或**受阻**（blocked）、**时间或轮次预算**触顶、或者 agent 不再有进展时，循环停止。

## 模型

```
/goal <目标> ──→ [active] ── 循环：跑一轮 agent，然后检查 ──┐
                    ▲                                       │
                    │  /goal resume       agent: update_goal(complete) ──→ [complete]
                    │                     agent: update_goal(blocked)  ──→ [blocked]
               [paused] ←── Ctrl-C        连续 3 轮无进展              ──→ [blocked]
                                          预算触顶 → 1 轮收尾          ──→ [limit_reached]
```

这个循环归 CLI（宿主）所有。harness 本身没有"目标"的概念。CLI 用三个普通原语搭出这个循环：用 `agent.chat()` 驱动一轮、每轮发一条消息、注入一个工具。想要同样能力的宿主也照这个办法搭，见 [Part 4.8 · 编排续航](/zh/part-4/8-orchestration-continuation)。

## 设置目标

```text
> /goal 把所有调用点迁出已废弃的 auth API，并让测试全绿
Goal set: 把所有调用点迁出已废弃的 auth API，并让测试全绿
budget: 0/25 turns, 0s/2h
```

循环立即开始。每一*轮*都是一次完整的 agent 轮次：读文件、改文件、跑测试。第一轮把你的目标原样作为消息发出。之后每轮发一段续航提示，它重述目标，并告诉 agent 怎样报告结果。

目标运行期间，CLI 给 agent 加一个工具：`update_goal`。agent 达成完整目标时用 `status="complete"` 调用它，没有你就无法继续时用 `status="blocked"` 调用它。循环结束时 CLI 移除这个工具。`update_goal` 算只读工具，所以在 `read-only` 模式下目标也能正常收尾。

如果已有 `active` 或 `paused` 的目标，新的 `/goal <目标>` 会先询问再替换（按 `1` 确认）。已停止的目标（`complete`、`blocked`、`limit_reached`）直接替换，不询问。

用 `/image` 暂存的图片会随目标的第一轮一起发出。

### 循环何时停止

每轮结束后循环检查一次目标。以下情况会停止：

- agent 调用 `update_goal(status="complete")`：目标达成。
- agent 调用 `update_goal(status="blocked")`：agent 需要你，比如缺凭证，或者有个该由你拍板的决定。
- 预算触顶：agent 获得最后一轮**收尾**，总结进展和剩余工作，然后循环停止。
- **连续三轮没有进展**：CLI 把目标标为 `blocked`。
- 你按下 Ctrl-C：目标转为 `paused`（见[用 Ctrl-C 中断](#用-ctrl-c-中断)）。
- 某一轮抛出错误：目标转为 `paused`，CLI 显示错误。

### 无进展停止

一轮在两种情况下算**无进展**：

- 没有产出回答（`no_output` 或 `reasoning_only`），也没有调用工具。
- 被插件 hook 结束（`hook_stop`），也没有调用工具。这是 `UserPromptSubmit` hook 拦截或停止了续跑提示，模型根本没被调用。
- 在 provider 侧失败（`llm_error`）。同一轮里此前调过工具也不改变这一点 — 一个持续失败的 provider 并没有在干活。

其他任何一轮都把计数清零：给出了回答的轮、调用了工具的轮、被 harness 在上限处叫停的轮（`max_iterations`、`doom_loop`、`length_truncated`）。被叫停的轮做过事，不算空转。工具已经运行过的 `hook_stop` 轮（`PostToolUse` hook 返回 `continue: false`）同样清零。

预算限制的是目标能*做*多少；这条规则限制的是它能*什么都不做*多久。它是 `--unbounded` 下唯一仍然生效的停止条件。触发时，CLI 会说明这几轮没有进展并给出原因，而不会说"需要你的输入"，因为 agent 什么也没问。检查模型或 provider（`hook_stop` 时检查你的 `UserPromptSubmit` hook），然后 `/goal resume`。

## 预算

一个目标有两个相互独立的上限，先触顶者生效。

```text
/goal <目标> --for 30m              # 活跃挂钟时间上限
/goal <目标> --turns 10             # 续航轮次上限
/goal <目标> --for 1h --turns 20    # 两个上限都设
/goal <目标> --unbounded            # 不设上限，连默认值也不用
```

参数可以放在这一行的任何位置，其余文字都是目标。

- **`--for <时长>`** 限制**活跃**挂钟时间。格式：`90s`、`30m`、`2h`、`1h30m`。每一段都必须带单位，所以单独的 `30` 会报错。总时长必须大于零。目标暂停期间的时间不计入。
- **`--turns <n>`** 限制轮次数。`n` 必须是正整数。

::: warning `--turns` 不是 `max_iterations`
`max_iterations` 限制的是*一轮之内*的工具调用循环。`--turns` 限制的是目标循环在*外层*调用 `chat()` 的次数。两者互不相干。
:::

省略某个上限时，使用 `.agentao/settings.json` 里的默认值（见[设置](#设置)）。`--unbounded` 同时关掉两个默认值。

循环在每一轮**之前**检查预算。已经开始的轮一定会跑完，所以一个很长的轮可能超出时间上限。收尾轮在触顶之后运行，不计入轮次。

为什么时间默认值大（`120m`）而轮次默认值小（`25`）？两个上限防的是不同的风险。`--turns` 是防止 agent 卡住空转的主力。`--for` 只防挂钟层面的问题，比如某个工具挂住。时间默认值定在 25 轮通常所需时间之上，这样在每轮都很慢的目标上，它不会盖过轮次上限。

## 子命令

```text
/goal                                    显示当前目标（状态、已用 / 上限）
/goal show                               同 /goal
/goal budget [--for <d>] [--turns <n>]   设置或替换当前目标的上限
/goal budget --clear                     去掉两个上限（同 --unbounded）
/goal pause                              暂停目标
/goal resume                             恢复已暂停或受阻的目标
/goal edit <新目标>                      修改目标文字
/goal clear                              删除目标
```

- **`/goal budget`** 只改你给出的那几个上限。对 `limit_reached` 的目标，它还会重新启动循环，前提是新上限留有余量。例如 25 轮上限触顶后，`/goal budget --turns 50` 会提高上限并继续。如果新上限仍然用完了，CLI 会保存它们，并告诉你该提高哪一个。
- **`/goal resume`** 重启 `paused` 或 `blocked` 的目标。它也能重启磁盘上仍是 `active` 的目标 — 那是之前的进程在运行中被杀掉留下的。它不能重启 `limit_reached` 的目标，那要用 `/goal budget` 或 `/goal clear`。`complete` 是终态。
- **`/goal edit`** 保留状态、上限，以及已用的时间和轮次。

`show`、`pause`、`resume`、`clear` 不带参数。如果后面跟了文字，整行就被当成新目标。所以 `/goal clear out the temp files` 是设置一个目标，而不是删除目标。

## 状态

| 状态 | 含义 | 接下来 |
|---|---|---|
| `active` | 循环正在驱动该目标 | agent 完成或标记受阻，或某个上限触顶 |
| `paused` | 你暂停了它，时间不计入 | `/goal resume` |
| `blocked` | agent 需要你的输入，或连续 3 轮无进展 | 解决问题后 `/goal resume` |
| `complete` | 目标达成，终态 | `/goal clear`，或设置新目标 |
| `limit_reached` | 某个预算上限触顶 | `/goal budget …` 继续，或 `/goal clear` |

谁设置哪个状态：

- **agent** 通过 `update_goal` 设置 `complete` 和 `blocked`，且只能在目标为 `active` 时设置。其他状态下这个调用什么也不改，并向模型返回一条错误。
- **你**负责暂停、恢复、编辑、调整预算和清除。
- **循环**设置 `limit_reached`。连续三轮无进展时它也会设置 `blocked` — 这是 agent 无法自己报告的唯一情形，因为一个什么都不回答的模型也调不了 `update_goal`。

## 设置

在 `.agentao/settings.json` 中设置默认值：

```jsonc
"goal": {
  "enabled": true,               // false：/goal 不能启动或恢复循环
  "default_max_turns": 25,       // 省略 --turns 时使用。0 = 不限轮次
  "default_time_budget": "120m"  // 省略 --for 时使用。"" = 不限时间
}
```

| 键 | 默认值 | 说明 |
|---|---|---|
| `goal.enabled` | `true` | 设为 `false` 时，设置、恢复、或通过调整预算重新启动都会被拒绝。`/goal show`、`pause`、`edit`、`clear` 仍可用。 |
| `goal.default_max_turns` | `25` | `0` 或负数表示不限轮次。不是整数的值回落到 `25`。 |
| `goal.default_time_budget` | `"120m"` | 格式同 `--for`。空值表示不限时间。无法解析的值回落到 `120m`。 |

用 `--for`、`--turns` 或 `/goal budget` 给出的上限会覆盖这些默认值。完整 schema 见 [configuration.md §3](https://github.com/jin-bo/agentao/blob/main/docs/reference/configuration.md)。

plan 模式开启时，目标也不能启动或恢复，因为 agent 只会规划、不会执行。先用 `/plan implement` 或 `/plan clear` 退出 plan 模式。

## 持久化与重启

目标保存在工作目录下的 `.agentao/goal.json`，同一时间只有一个目标。CLI 在每一轮之后、以及每个修改目标的子命令之后写这个文件。

目标能跨重启保留，但 CLI 不会自己重启循环。启动 `agentao`，用 `/goal` 查看目标停在哪里，再用 `/goal resume` 继续。

文件缺失或无法读取时，CLI 视为"没有目标"。你可以手动删除这个文件，效果等同 `/goal clear`。如果 CLI 写不了这个文件（只读目录或磁盘已满），它会警告一次；目标照常运行，但不能跨重启保留。

## 用 Ctrl-C 中断

目标运行时按 **Ctrl-C** 会**暂停**目标：当前轮停止，CLI 把目标保存为 `paused`。暂停期间时间不计入。之后用 `/goal resume` 继续。

## 目标、plan 模式与 Stop 钩子

- `/goal` **不**建在插件 `Stop` 钩子及其 `force_continue` 信号之上。那条路径的重入上限很小，而且会注入一条你看得见的消息。`/goal` 运行自己的循环，有自己的停止条件。
- plan 模式（`/plan implement`）只把计划交给 agent 一次；`/goal` 则跨多轮持续驱动 agent。两者相互独立，且 plan 模式开启时不能运行目标。

## 常见坑

- **`--turns 10` 不等于"10 次工具调用"**。每一轮可以调用很多次工具，上限是 `max_iterations`。
- **不带单位的时长会报错**。`--for 30` 是错的，要写 `--for 30m`。
- **`/goal clear <文字>` 会设置新目标**。要删除目标，只输入 `/goal clear`，后面什么都不跟。
- **`resume` 解除不了预算停止**。`limit_reached` 的目标需要用 `/goal budget` 给出更多余量，或者 `/goal clear`。
- **时间上限在轮与轮之间检查**。一个很长的轮可能跑过 `--for`。

## 下一步

| 想要… | 去读 |
|-------|------|
| 在自己的宿主里搭同样的循环 | [Part 4.8 · 编排续航](/zh/part-4/8-orchestration-continuation) |
| 先规划任务，再让目标去执行 | [4. Plan 模式](./4-plan-mode) |
| 不带写入和 shell 工具运行目标 | [3. 权限与模式](./3-permissions-modes) |
| 在 CI 里跑一次非交互轮次 | [12. 非交互入口](./12-non-interactive) |

---

::: info 这一章在体系里的位置
`/goal` 是 CLI 功能，不是 harness 功能。循环是 `agentao/cli/input_loop.py` 里的 [`run_goal_continuation`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/input_loop.py)。目标记录及其状态机在 [`agentao/cli/goal_state.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/goal_state.py)。注入的工具是 [`agentao/tools/goal.py`](https://github.com/jin-bo/agentao/blob/main/agentao/tools/goal.py)。设计记录见 [`docs/design/codex-goal-mechanism-review.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/codex-goal-mechanism-review.md)。
:::

::: tip 真相源头
命令语法：`/help`。行为：[`agentao/cli/commands/goal.py:handle_goal_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands/goal.py)。时长解析：[`agentao/cli/duration.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/duration.py)。
:::
