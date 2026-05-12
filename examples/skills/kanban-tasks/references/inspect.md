# 看状态 / 看产物

把"卡现在什么状态"和"卡到底产出了什么"分开看。两类问题对应不同入口。

## 90% 场景：一条命令搞定

```bash
uv run kanban result <id>                # 状态 / summary / 分支 / artifacts 路径 / transcripts 路径
uv run kanban result <id> --json         # 喂下游脚本
```

`kanban result` 是产物侧的**唯一推荐入口**。下面的细分入口是放大镜，按需用。

## A. 状态视图

| 想看什么 | 命令 | 备注 |
|---|---|---|
| 全板按状态分组 | `uv run kanban list` | 没有 `--json`；要程序化遍历 `show --json` |
| 单卡详情（goal / priority / depends / context_refs / acceptance / status） | `uv run kanban show <id> [--json]` | 喂下游用 `--json` |
| 结构化事件流（状态迁移、claim、artifact 快照） | `uv run kanban events <id>` | `--role …` / `--limit N` / `--json` |
| 全板事件 | `uv run kanban events --limit 200` | 不带 `<id>` |
| 当前活跃 claim | `uv run kanban claims [<id>] [--json]` | v0.1.2 并发 |
| 在线 worker | `uv run kanban workers` | 多 worker 模式 |
| acceptance / context 是否齐全 | `uv run kanban card acceptance list <id>` / `card context list <id>` | — |

最常用三连：

```bash
uv run kanban show   <id>                 # 这张卡是什么、配置齐了吗
uv run kanban events <id> --limit 20      # 最近发生了什么
uv run kanban claims <id>                 # 现在谁在跑（如有）
```

## B. 产物视图（"这张卡到底产出了什么"）

产物分布在 4 个位置，但**不要手动找**——`kanban result` 会按卡当前状态给出对应路径：

| 卡的状态 | 产物在哪 | 直接看 |
|---|---|---|
| **DOING**（worktree 在线） | `workspace/worktrees/<card-id>/`（分支 `kanban/<card-id>`） | `uv run kanban worktree diff <id>` |
| **DONE / BLOCKED**（worktree 已 detach） | `workspace/raw/<card-id>/artifacts-<ts>/` | `kanban result <id>` 给最新一份 |
| 任意状态：agent 原始响应 | `<board>/traces/...` | `uv run kanban traces <id> --latest` |
| 任意状态：结构化事件 | `<board>/events.log` | `uv run kanban events <id>` |

机制要点：

- 产物**先**落到 live worktree。
- 卡进入 DONE / BLOCKED 时 `WorktreeManager.detach()` 把 gitignored 路径下的产出抢救到 `workspace/raw/<card-id>/artifacts-<ts>/`，并写一条 `worktree.artifacts_saved` 事件（含快照路径）；events / Web UI 都能看到。
- 默认每卡保留**最近 5 份快照、上限 500 MiB**；超过的文件按顺序跳过、已复制的保留。
- 调上限：`KANBAN_ARTIFACTS_MAX_BYTES`（环境变量；字节数）。
- denylist：`node_modules/` / `__pycache__/` / `.venv/` / `dist/` / `target/` 不计入。

## C. 三个最常被问的问题（一行流）

```bash
# "这张卡现在做到哪了？"
uv run kanban show <id> && uv run kanban events <id> --limit 20

# "这张卡产出了什么？"
uv run kanban result <id>

# "agent 原文是什么？"
uv run kanban traces <id> --latest [--role worker|planner|reviewer|verifier]
```

## D. 多卡批量盘点

```bash
uv run kanban worktree list                       # 当前活跃 worktree（隐含：哪些卡在 DOING）
uv run kanban events --limit 200                  # 全板最近事件
uv run kanban claims --json                       # 喂下游

# 程序化全板视图（弥补 list 没有 --json）
for id in $(ls workspace/board/cards/*.md | xargs -n1 basename | sed 's/\.md$//'); do
  uv run kanban show "$id" --json
done | jq -s '.'
```
