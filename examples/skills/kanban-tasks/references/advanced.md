# 进阶用法

## Multi-backend 执行器（profile 路由）

`--executor multi-backend` 把卡按 `agent_profiles.yaml` 路由到不同 backend。

- 配置文件：`<cwd>/.kanban/agent_profiles.yaml`，缺省回退 `docs/agent_profiles.sample.yaml`。
- Backend：`subagent`（agentao 角色子 agent）/ `acp`（Anthropic Claude Pro 协议）。
- `RouterPolicy`：可让 `kanban-router` agent 从角色的候选 profile 中挑一个。**永远不会**覆盖卡片 pin（`card.agent_profile`）或 planner 推荐；任何失败（disabled / 缺 spec / parse error / timeout）都降级到角色默认。

```bash
uv run kanban --executor multi-backend run
uv run kanban profiles                                  # 看当前 profile 配置
KANBAN_ROUTER=off uv run kanban --executor multi-backend run    # 关 router
```

每角色单独开关 router 见 config 顶层的 `router:` 段。

## 并发（v0.1.2）

```bash
# 一个 scheduler 持锁分发，多个 worker 不持锁只执行
uv run kanban daemon --detach --role scheduler --max-claims 4
uv run kanban daemon --detach --role worker --worker-id w1
uv run kanban daemon --detach --role worker --worker-id w2

uv run kanban claims              # 当前 claim
uv run kanban workers             # 在线 worker
uv run kanban recover             # 清孤儿 claim、stale 状态等
```

默认 `--role all`（scheduler + worker 同进程，单卡串行）。**只有需要"多卡并行"或"真跑+其它人手动 tick"时才拆**。

`legacy-serial` 是 v0.1.2 之前的串行 tick 路径，仅作回退使用。

## Worktree

- 默认 `--worktree auto`：板在 git 仓库内时启用，外面禁用并打印一行 stderr 警告。
- 强制：`--worktree`（要求板在仓库内，否则退出）/ `--no-worktree`（关闭隔离）。
- 分支命名：`kanban/<card-id>`。
- live 路径：`workspace/worktrees/<card-id>/`。
- 卡 detach 时（DONE / BLOCKED）：`WorktreeManager.detach()` 把 gitignored 路径产物抢救到 `workspace/raw/<card-id>/artifacts-<ts>/`，写 `worktree.artifacts_saved` 事件，再 `git worktree remove`。

artifact 抢救参数：

| 项 | 默认 | 调整 |
|---|---|---|
| 每卡保留份数 | 5 | 代码常量 |
| 总大小上限 | 500 MiB | `KANBAN_ARTIFACTS_MAX_BYTES`（字节） |
| denylist | `node_modules/` `__pycache__/` `.venv/` `dist/` `target/` | 代码常量 |

per-file 累加：到上限后剩余文件**跳过**，已复制保留——不会让你的 `.zip` 半截损坏。

## 环境变量速查

| 变量 | 作用 |
|---|---|
| `KANBAN_ARTIFACTS_MAX_BYTES` | worktree detach 时 artifact 总大小上限（字节，默认 500 MiB） |
| `KANBAN_ROUTER` | `off` 关掉 multi-backend 的 RouterPolicy |
| `BOARD` | 脚本里覆盖 board 路径（默认 `workspace/board`） |
| `PORT` | 脚本里覆盖 web 端口（默认 8000） |
| `EDITOR` | `card edit` / `card acceptance edit` 用的编辑器 |

## Mock 执行器细节

`MockAgentaoExecutor` 是确定性状态机：每卡 `INBOX → READY → DOING → REVIEW → DONE`，不调任何 LLM。

适用：

- CI（不消耗配额）。
- 教学 / 演示。
- 给新接入的 store / executor 做基线测试。

不适用：要真改文件、真跑 review / verify 的场景——用 `--executor agentao` 或 `multi-backend`。

## 真跑（`--executor agentao`）

四角色 sub-agent：`planner` / `worker` / `reviewer` / `verifier`，定义在 `.agentao/agents/kanban-<role>.md`。每次 `run()` 各角色一个 `agentao.Agentao` 实例，解析尾部 ```json 围栏（`{ok, summary, output[, acceptance_criteria][, blocked_reason]}`）。任何异常 → 卡进 `BLOCKED`。

每条事件携带 `prompt_version` / `duration_ms` / 完整 raw 响应，便于审计。

## MCP

```bash
uv run kanban mcp install                  # 把 kanban-mcp 注册到已知客户端
uv run kanban mcp --help                   # 列子命令
```

MCP server 暴露看板的工具集供其它 LLM 客户端使用；通常给 Claude Desktop / Cursor 用，不在本 skill 主流程里。
