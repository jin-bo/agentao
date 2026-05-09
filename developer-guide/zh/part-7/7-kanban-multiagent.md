# 7.7 蓝图 G · 看板上的多 Agent 调度

::: tip ⚡ 端到端可跑
**产出** —— 衍生项目 `agentao-kanban`，用三个 agentao sub-agent（`planner` / `worker` / `reviewer`）把一个看板跑起来：卡片自动 `INBOX → READY → DOING → REVIEW → DONE`，全程无人值守。（验收已并入 reviewer 角色。）
**技术栈** —— Python · agentao sub-agent（Markdown + YAML frontmatter）· 单写者 orchestrator + dispatcher daemon · 每卡 git worktree · MCP server + FastAPI 看板。
**源代码** —— [`jin-bo/agentao-kanban`](https://github.com/jin-bo/agentao-kanban)（独立仓库，本项目的衍生项目）。
**试一下** —— `uvx --from agentao-kanban kanban init --demo && uvx --from agentao-kanban kanban demo`
:::

::: warning 外部项目
和 7.1–7.6 不同（那六个就在本仓 `examples/` 下），本案例位于**独立仓库**，按自己的节奏发版。本页只锚定**稳定的架构选择**和**它消费的 agentao 接口**；具体的代码路径、文件名和 CLI flag，请以那个仓库的 `README.md` 和 `docs/` 为准。
:::

**场景**：你已经把一块工作切成了"卡片"——Linear 工单、GitHub issue、内部任务。你想要一组分工明确的 agent 自动地规划、执行、评审、验收每张卡，**关键路径上不放人**，同时仍然允许人随时围观或干预。

## 谁 & 为什么

- **产品形态**：一块板上长跑的调度器
- **用户**：盯着板、读交付物的工程 / 运维同学——他们不驱动每一步
- **痛点**：扁平的单 agent 循环（"接着干"）丢掉了天然的 review / verify 关卡，也没地方插"reviewer 用更强的模型"这类决策

## 这个蓝图能展示哪些 agentao 用法

这是回答下面两个问题最有用的样例——本仓内任何一个蓝图单独都答不了：

1. **chat 循环之外的多 sub-agent 编排** —— sub-agent（§3.1、§6）通常被理解为"父 agent 在对话里委托"。看板把它们当成**外部调度器驱动的一等 worker**，状态迁移由 kanban orchestrator 持有，**不**由 LLM 持有。
2. **按角色路由后端（subagent vs. ACP）** —— 每个角色可以由进程内的 agentao sub-agent 提供，也可以由外部 ACP CLI（Claude Code、Codex 等）提供，系统其它部分一无所知。这是 agentao [host-client 架构（§3.3）](/zh/part-3/3-host-client-architecture)落到实处最干净的样本。

## 多 agent 调度器的设计原则

1. **状态只能由一处写** —— 卡片的状态只有 orchestrator 能改。Agent 只返回 *结果*，从不改状态。（呼应 agentao "PermissionEngine 是唯一权威" 的思路，见 [§4.7](/zh/part-4/7-host-contract)。）
2. **角色，而非层级** —— `planner` / `worker` / `reviewer` 是平级的，按卡选用，不是树形调用。Orchestrator 路由，agent 之间不互调。（早期版本另有独立的 `verifier` 角色，现已并入 `reviewer`。）
3. **每张卡独立 worktree** —— 当看板在 git 仓库里时，每张卡分配独立 git worktree + 分支，并发 worker 互不干扰。终态时 detach（释放目录，保留分支）。
4. **结构化事件 + 原始 transcript** —— 每次 agent 运行都同时写一行可机读事件 *和* 完整 LLM transcript。卡卡住时，是"打开 transcript"，不是"加 --verbose 重跑"。
5. **写操作普遍尊重锁** —— CLI、MCP 工具、Web UI 都尊重 `.daemon.lock`。三个入口下，board 仍然只有一个 source of truth。

## 架构

```
                ┌──────────── 人（基本只读） ────────────┐
                │                                       │
        kanban CLI         kanban-mcp (MCP)        kanban web (FastAPI)
                │                  │                    │
                └─────────► BoardStore (.kanban/) ◄──────┘
                                   ▲
                  只读             │  唯一写卡片状态者
                                   │
                            ┌──────┴───────┐
                            │ Orchestrator │   ← 唯一进程，持 .daemon.lock
                            └──────┬───────┘
                                   │ 拉下一张 ready 卡
                                   ▼
                       ┌───── Executor ──────┐
                       │   multi-backend 路由 │
                       └────┬─────┬─────┬─────┘
                            │     │     │     按角色：subagent | ACP CLI
                         planner worker reviewer
                            ▼     ▼     ▼
                  agentao sub-agent（Markdown + YAML）
                          │
                          ▼
              workspace/worktrees/<card>/   ← 每张卡的 git worktree
              workspace/raw/<card>/...      ← transcript + 抢救出的 artifact
              workspace/reports/...         ← 给人看的交付物
```

## 按这个顺序读源码

不去镜像那些会漂的代码——下面这张表把仓库里每块代码对应到本指南的相关章节。

| 在 kanban 仓库里读 | 用到的 agentao 接口 | 本指南对应章节 |
|---|---|---|
| `kanban/agents.py` + `kanban/defaults/*.md` | Sub-agent 定义格式（Markdown + YAML frontmatter） | [§3.1 插件模型](/zh/part-3/)、[§6.x sub-agent](/zh/part-6/) |
| `kanban/orchestrator.py` | 基于 agent result 的单写者状态机 | [§4.7 宿主合约](/zh/part-4/7-host-contract) |
| `kanban/executors/multi_backend.py` + `agent_profiles.yaml` | 角色路由：进程内 sub-agent vs. 外部 ACP CLI | [§3.3 Host-client 架构](/zh/part-3/3-host-client-architecture)、[§3.2 Agentao 作为 ACP server](/zh/part-3/2-agentao-as-server) |
| `kanban/daemon.py` + `runtime/claims/*.json` | 同一块板上的多 worker 并发（O_EXCL CAS 租约） | [§6.7 资源与并发](/zh/part-6/7-resource-concurrency) |
| `workspace/worktrees/<card>/` 机制 | 真实代码库里的"每任务沙箱" | [§6.x 沙箱](/zh/part-6/) |
| `kanban/mcp.py`（`kanban-mcp`） | 把看板暴露为 Claude Code / Codex 的 MCP 工具 | [§5 MCP](/zh/part-5/) |
| `workspace/raw/<card>/<role>-<ts>.md` | 事后审计用的 transcript + 结构化事件 | [§6 可观测性](/zh/part-6/6-observability) |

## 30 秒心智模型

```python
# 伪代码 —— 真实代码在 kanban/orchestrator.py
while True:
    card = board.next_ready()              # 看 WIP、依赖、优先级
    if not card:
        sleep_or_idle(); continue

    role  = card.next_role()               # planner -> worker -> reviewer
    agent = profiles.pick(role, card)      # 选 subagent 还是 ACP backend
    result = agent.run(card, worktree=card.worktree())

    board.commit(card, role, result)       # 状态写入唯一入口
```

仓库里其它内容——claim、lease、重试矩阵、artifact 抢救、Web UI——都是这条主循环之上的**运维脚手架**。

## 为什么要单独立一个样例

本仓内的六个蓝图都在回答 "**怎么把一个 Agentao 实例嵌进我的产品？**"。看板回答的是另一种形态：

> "怎么把**一组分工的 agent** 当成一个系统跑起来——带状态、带重试、带隔离，而 Agentao 只是其中一种 backend？"

如果你在做任何带"工作队列"的产品（CI、批量评估、内容流水线、agentic 重构任务、自主研究），看板的形态比 7.1–7.5 更贴你。

## ⚠️ 陷阱（摘自仓库自己的设计文档）

| 上线第二天的 bug | 根因 | 仓库的修法 |
|---|---|---|
| 两个 daemon 同时写一块板 | 跨进程没有互斥 | `.daemon.lock` + `kanban daemon status` 报 `running / stale / stopped` |
| 并发 worker 互踩工作树 | 共用一个 checkout | 每卡独立 git worktree，终态自动 detach |
| Worktree 删了，交付物也没了 | `git worktree remove` 会把 gitignored 文件一起删 | Artifact 抢救：先把 `workspace/reports/...` 快照到 `workspace/raw/<card>/artifacts-<ts>/` |
| 卡片永远卡在 "review" | 没有租约过期机制 | Claim 带 lease + `kanban recover --stale` |
| Web UI 不小心把写接口暴露到内网 | 全网 bind + 默认开写 | 默认只读；非 loopback bind 又要写，必须显式 `--allow-remote-writes` |

## 入口，不是配方

- **仓库**：<https://github.com/jin-bo/agentao-kanban>
- **一行装好 + demo**：`uvx --from agentao-kanban kanban init --demo && uvx --from agentao-kanban kanban demo`
- **建议先读的设计文档**（在仓库里）：`docs/worktree-isolation-design.md`、`docs/agent-router-design.md`、`docs/agent-profile-acp-design.md`、`docs/v0.1.2-concurrency-plan.md`

本页**有意不**绑定到具体的 kanban 版本。如果某条 CLI flag 或文件名跟仓库对不上，以仓库为准——同时欢迎在本仓提 issue 让我们及时同步。

---

← [7.6 微信智能机器人](./6-wechat-bot) · → [附录](/zh/appendix/)
