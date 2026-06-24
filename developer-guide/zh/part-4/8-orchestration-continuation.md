# 4.8 编排续航 —— 在你的 Host 里实现长任务目标

> **本章你会学到**
> - **为什么**"持续工作直到任务完成"是 **host** 的职责,而不是 harness 的功能
> - harness 已经给你的**三个原语** —— 驱动一轮、注入每轮消息、注入工具
> - 一个带时间/轮次预算的 **host 自有续航循环**,端到端(约 40 行)
> - **为什么不用** `force_continue`(插件 `Stop` 钩子),以及边界在哪

Agentao 的 CLI 自带 `/goal` 命令:一次声明目标,它就会跨多轮持续驱动 agent,
直到目标被报告完成或受阻、或预算触限。本章讲的是**它背后的模式**,好让你在
自己的 host 里复刻同样的能力。CLI 实现(`agentao/cli/commands/goal.py` +
`agentao/cli/input_loop.py`)是范例;设计记录见
[`docs/design/codex-goal-mechanism-review.md`](https://github.com/) §11。

## 4.8.1 harness 没有"goal"—— 也不该有

一次 `agent.chat(msg)` 跑的是一**轮**:模型思考、调用工具(受 `max_iterations`
约束),在对*这条消息*无事可做时返回。harness 层刻意没有"持续干到更大目标达成"
的概念 —— 那会把产品决策(多久?预算多少?何谓*完成*?)烙进运行时。

harness 给的是**三个通用原语**,而"goal"就是 host 把它们组合进一个循环后的产物:

| 原语 | API | 在续航里的角色 |
|---|---|---|
| **驱动一轮** | `agent.chat(message)` / `await agent.arun(...)` | 朝目标推进的一个工作单元 |
| **注入每轮上下文** | 每轮传入的 `message` | 引导下一轮("继续 / 收尾") |
| **注入工具** | `agent.add_tool(tool)` / `agent.remove_tool(name)` | 给 agent 一个发出*完成* / *受阻*信号的途径 |

别无所需。时间与轮次预算纯属 host 记账(`chat()` 之间的墙钟;一个你自增的计数器)。
Token 预算**不**在本模式内 —— Agentao 刻意把 goal 预算限定为时间/轮次,所以
harness 无需任何用量观测原语。

## 4.8.2 续航循环

整个模式就是一个外层 `while`:

```python
import time
from agentao.tools.base import Tool

class UpdateGoalTool(Tool):
    """The agent's ONLY write into goal state: mark complete / blocked."""
    def __init__(self, goal):
        super().__init__()
        self._goal = goal
    @property
    def name(self): return "update_goal"
    @property
    def description(self):
        return ("Call with status='complete' when the objective is fully "
                "achieved, or status='blocked' when you cannot proceed without "
                "the user. Do not mark complete just because a budget is low.")
    @property
    def parameters(self):
        return {"type": "object",
                "properties": {"status": {"type": "string",
                                          "enum": ["complete", "blocked"]}},
                "required": ["status"]}
    def execute(self, status):
        # Active-only guard: a terminal goal is immutable by the agent.
        if goal["status"] != "active":
            return f"ignored: goal is {goal['status']}, not active"
        goal["status"] = status          # 'complete' or 'blocked'
        return f"Goal marked '{status}'."


def run_goal(agent, objective, *, max_turns=25, time_budget_s=7200):
    goal = {"status": "active", "turns": 0, "time": 0.0}

    agent.add_tool(UpdateGoalTool(goal), replace=True)   # inject the write surface
    try:
        while goal["status"] == "active":
            # Budget pre-check → exactly one wrap-up turn, then stop.
            if goal["turns"] >= max_turns or goal["time"] >= time_budget_s:
                goal["status"] = "limit_reached"
                agent.chat(f"You've reached this goal's budget. Do not start new "
                           f"work; summarize progress and remaining work.\n{objective}")
                break

            message = objective if goal["turns"] == 0 else (
                f"Continue working toward this goal. Call update_goal when done "
                f"or blocked.\n<goal>{objective}</goal>")

            t0 = time.monotonic()
            agent.chat(message)                          # drive one turn
            goal["turns"] += 1
            goal["time"] += time.monotonic() - t0

            # The agent may have called update_goal this turn.
            if goal["status"] in ("complete", "blocked"):
                break
    finally:
        agent.remove_tool("update_goal")                 # tool is loop-scoped
    return goal
```

四条不变式让它正确:

1. **首轮用目标本身;之后用续航提示。** 用 `turns == 0` 判断,而非另设标志位。
2. **预算在每轮*之前*检查**,触限产生**恰好一次**收尾轮(让 agent 有机会总结)——
   不是硬切。
3. **注入工具是 agent 唯一的写入口**,且**守卫为 `active`**,这样收尾轮无法
   覆盖终态 `limit_reached`。
4. **工具仅在循环生命周期内注册**(`finally: remove_tool`),goal 之外不可见。

## 4.8.3 预算:时间与轮次防的是不同风险

提供两条轴,**先触发者胜**:

- **轮次** —— 一轮是一次*完整*的 `agent.chat()`(自带内层 `max_iterations` 循环),
  所以轮次上限是**主失控护栏**(打转的 agent)。`25` 已是相当大的工作量。
- **时间** —— 累计 active 墙钟。它只防**墙钟病态**(工具挂起),故应设在轮次上限
  正常完成点*之上*(CLI 默认 `120m`)。时间上限若等于或低于轮次完成点,会在
  迭代慢的任务上悄悄盖过轮次上限。

⚠️ **`turns` 不是 `max_iterations`。** `max_iterations` 约束*一次* `chat()` *内部*的
工具调用循环;轮次上限约束 `chat()` 调用的*次数*。两者正交 —— 都要保留。

## 4.8.4 为什么不用 `Stop` 钩子 / `force_continue`?

Agentao 插件有个 `Stop` 钩子可以重入循环(`StopHookResult.force_continue`)。
对 goal 而言它是**错的工具**:

- 它被**硬上限**(`_stop_reentry_cap`,默认 3)钉死,作为失控护栏 —— 用于"再推一把"
  尚可,持续推进无用;
- 它每次往历史里注入一条**可见的用户消息**。

goal 是 **host 自有**的循环:*你*掌握停止条件、预算与引导。`force_continue` 是
让插件在*单个* host 轮内说"还没完";goal 是 host 驱动*许多*轮。层不同,工具也不同。

## 4.8.5 想要重启续命就持久化

CLI 在每轮后把 goal 写入 `.agentao/goal.json`,使 goal 跨进程重启存活。持久化
完全在 host 侧 —— 上面的 `goal` dict 就变成一个小 JSON 文件;启动时重新载入,
若仍是 `active` 或 `paused` 就提示恢复。harness 不参与。

## 4.8.6 你的 host 落地清单

- [ ] 一个调用 `agent.chat()`(或 `arun`)的外层 `while active` 循环。
- [ ] 每轮消息:首轮用目标,之后用续航提示。
- [ ] 一个注入的 `update_goal` 式工具,**守卫为 active**,循环前加入、`finally` 移除。
- [ ] 每轮前检查预算;触限时**一次**收尾轮。
- [ ](可选)持久化状态以支持重启续命。
- [ ] **不**建在 `force_continue` 上。

→ 范例:`agentao/cli/input_loop.py::run_goal_continuation`。用户指南:
[长任务目标](../../../docs/guides/goal.md)。
