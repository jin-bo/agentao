# Kanban CLI 速查

> 全部命令以 `uv run kanban …` 开头。`<id>` 接受全 UUID 或前缀（CLI 自动唯一匹配）。
> `--board` 仅在不在板根目录或子目录时才需要；正常情况下**不要带**。

## 板与生命周期

```bash
uv run kanban init                      # 在当前目录建 .kanban/ + workspace/board/
uv run kanban init --demo               # 同上 + 4 张示例卡
uv run kanban demo                      # 把示例卡推到 DONE（演示）
uv run kanban doctor [--fix]            # 板 + 环境体检；--fix 清 stale lock 等
```

## card add / edit / context / acceptance

```bash
# 创建（--acceptance / --depends 可重复）
uv run kanban card add \
  --title "标题" --goal "目标" \
  [--priority LOW|MEDIUM|HIGH|CRITICAL] \
  [--acceptance "..." --acceptance "..."] \
  [--depends <card_id> --depends <card_id>]

# 编辑（开 $EDITOR 改 Markdown frontmatter）
uv run kanban card edit <id>

# context（--path 是命名参数，注意；--kind 默认 required）
uv run kanban card context add  <id> --path path/to/ref.py:42-88 \
    [--kind required|optional] [--note "..."]
uv run kanban card context list <id>
uv run kanban card context rm   <id> --path path/to/ref.py:42-88

# acceptance（rm 用 1-based 索引）
uv run kanban card acceptance add   <id> --item "uv run pytest tests/foo 全绿"
uv run kanban card acceptance edit  <id>
uv run kanban card acceptance list  <id>
uv run kanban card acceptance rm    <id> 2
uv run kanban card acceptance clear <id>
```

## 状态流转

```bash
uv run kanban list                       # 全板按状态分组（**没有 --json**）
uv run kanban show <id> [--json]         # 单卡详情
uv run kanban move <id> ready|doing|review|done|inbox

# 状态名大小写都接受（CLI 标准化为大写）

uv run kanban block   <id> "原因"        # reason 是位置参数，不是 --reason
uv run kanban unblock <id> [--to ready]  # 默认回 inbox
uv run kanban requeue <id> [--to ready] [--note "失败现场已修"]
```

## 推动执行

```bash
uv run kanban tick                       # 单步
uv run kanban run                        # 跑到 idle（全 DONE 或全 BLOCKED）

# 指定执行器（放在子命令前）：
uv run kanban --executor mock          run    # 默认；离线安全
uv run kanban --executor agentao       run    # 真调四角色 sub-agent
uv run kanban --executor multi-backend run    # 走 agent_profiles.yaml
```

## 复盘 / 看产物（首选 result）

```bash
uv run kanban result <id> [--json]       # ★ 状态/summary/分支/artifacts/transcripts 一次给齐
uv run kanban events  <id> [--limit N] [--role planner|worker|reviewer|verifier] [--json]
uv run kanban events  --limit 200        # 全板事件流
uv run kanban traces  <id> --latest [--role R]   # agent 原始 transcript
uv run kanban claims  [<id>] [--json]    # 当前活跃 claim（v0.1.2 并发）
uv run kanban workers                    # 多 worker 模式下的在线 worker
```

> `kanban traces` **必须**带 `<card_id>`（位置参数）；要全板复盘遍历各卡或读 `<board>/traces/`。

## Worktree

```bash
uv run kanban worktree list              # DOING 时活跃的 worktree
uv run kanban worktree diff <id>         # DOING 时 worker 的改动
uv run kanban worktree prune             # 清理过期分支

# DONE/BLOCKED 后产物在 workspace/raw/<id>/artifacts-<ts>/，由 result 直接给路径
```

## Daemon

```bash
uv run kanban daemon                      # 前台跑（Ctrl-C 优雅停）
uv run kanban daemon --detach             # 后台 fork
uv run kanban daemon --once               # 单 tick 即退
uv run kanban daemon status               # running / stale / stopped
uv run kanban daemon logs -f              # tail <board>/daemon.log
uv run kanban daemon stop                 # SIGTERM 给 .daemon.lock 中的 pid
```

多 worker / 高级用法见 `references/advanced.md`。日常用 `scripts/kanban-up.sh`。

## Web UI

```bash
uv run kanban web --host 127.0.0.1 --port 8000        # 只读
uv run kanban web --host 127.0.0.1 --port 8000 --enable-writes        # 允许浏览器建卡
uv run kanban web --host 0.0.0.0 --port 8000 --enable-writes --allow-remote-writes
```

`kanban web` 没有 `--detach`；后台化由 `scripts/kanban-up.sh` 处理。

## MCP

```bash
uv run kanban mcp install                 # 把 kanban-mcp 注册到 MCP 客户端
uv run kanban mcp --help                  # 列子命令
```

## 全局选项

```bash
--board DIR                  # 指定板目录（一般不需要）
--executor mock|agentao|multi-backend
--worktree | --no-worktree   # 默认 auto：在 git 仓库内启用，外面禁用
--force                      # 绕过 daemon 锁；仅应急
```

## 已知小坑（细节见 troubleshooting.md）

- `kanban list` **没有** `--json`；要程序化全板视图，遍历 `show --json` 或读 `<board>/cards/*.md` 的 TOML frontmatter。
- `block` 的 `reason` 是位置参数；`unblock` / `requeue` 的 `--to` 默认 `inbox`。
- `card context add` 的 `--path` 是命名参数（不是位置参数）。
- 执行器在 daemon 启动时定型，要切换必须 stop → start。
