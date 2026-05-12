---
name: kanban-tasks
description: >
  使用 agentao-kanban 看板进行任务管理（拆任务 / 状态流转 / 后台服务 / 看产物）。
  本技能负责：① 初始化 .kanban/ 项目 ② 把需求拆成原子卡片入板
  ③ 推动状态流转 INBOX → READY → DOING → REVIEW → DONE
  ④ 启停 daemon 与 Web UI ⑤ 察看卡片状态与产物。
  触发关键词（中/英）：看板 / kanban / 任务管理 / task board / 拆任务 / 任务卡 / 排期 / WIP /
  dispatcher / orchestrator。
  复合触发短语（一键启停）：
    • "启动 kanban"      → daemon（mock 执行器，后台）+ Web UI（自动开浏览器）
    • "启动 kanban 真跑" → daemon（--executor agentao，真调 sub-agent）+ Web UI
    • "停止 kanban"      → 先停 Web UI，再停 daemon
---

# Kanban 任务管理 · 操作指南

底层是本仓库虚拟环境中以 editable 模式安装的 `agentao-kanban`（console scripts: `kanban`, `kanban-mcp`）。
所有命令必须用 `uv run kanban …`，不要直接 `python -m kanban`，也不要 `pip install`。

## 心智模型（先看这里再动手）

- **状态机**：卡片在 `INBOX → READY → DOING → REVIEW → DONE` 中流转，外加旁路状态 `BLOCKED`。
  唯一合法的状态写入者是 orchestrator；CLI 的 `move` 是给人/Claude 用的运营手段。
- **板的位置**：`kanban init` 会在当前目录创建 `.kanban/`（项目根标记）和 `workspace/board/`（卡片
  与事件文件）。从该目录或任意子目录运行 `kanban …` 都能自动找回这块板，**不要带 `--board`**。
- **执行器**：默认 `mock`（离线状态机，CI 安全），加 `--executor agentao` 才会真正调用四个角色
  sub-agent。除非用户明确要求"真跑"，**默认保持 mock**。
- **写锁**：所有写命令尊重 `.daemon.lock`。daemon 跑着的时候不要再用 `--force` 写卡（仅应急）。
- **工作区铁律**：板一旦由 `kanban init` 落到 `workspace/board/`，就不要把卡片相关材料散落到仓库根；
  生成的笔记/数据按 `AGENTAO.md §Workspace` 入 `workspace/docs/`、`workspace/data/` 等。

## 前置自检（开工前一次）

```bash
uv run kanban --help >/dev/null    # 确认 CLI 可用
uv run kanban doctor               # 板 + 环境体检（无板时会提示先 init）
```

如果 `doctor` 报 stale lock / 缺失目录，运行 `uv run kanban doctor --fix`。

## 工作流

### Step 1 — 把用户的需求拆成原子卡片

在动 CLI 之前，**先在对话中用一个简短表格列出你打算建的卡**，让用户确认或调整：

| # | title | goal | priority | depends_on | acceptance(关键 1-3 条) |
|---|---|---|---|---|---|

拆卡原则（按本项目 `AGENTAO.md` 的 Code Review 思路：Survey → Classify → Prescribe → Verify）：

1. **原子性**：一张卡 = 一个可独立完成、可独立验证的产出。"重构 X 模块"不是卡，"把 X.foo 拆成两个
   纯函数并迁移调用点"才是卡。
2. **依赖显式化**：只有真依赖（B 必须等 A 的产出）才用 `--depends`。同主题不等于依赖。
3. **acceptance 是验收凭据**：每张卡至少 1 条 `acceptance`，写成可被人/CI 直接判定通过/失败的语句
   （"`uv run pytest tests/foo` 全绿"、"`workspace/reports/x.md` 存在且包含章节 …"）。
4. **优先级保守用**：默认 `MEDIUM`。`CRITICAL` 仅留给阻断其他卡或线上故障类。

### Step 2 — 初始化（仅当 `.kanban/` 不存在）

```bash
uv run kanban init                 # 干净起步：建 .kanban/ + workspace/board/
# 或带示例卡先看效果：
uv run kanban init --demo
```

如果用户说"先看看 kanban 怎么跑"，跑：

```bash
uv run kanban init --demo
uv run kanban demo                 # 把 4 张示例卡推到 DONE
uv run kanban list
```

### Step 3 — 建卡

每张卡用一条命令；`--acceptance` 与 `--depends` 可重复：

```bash
uv run kanban card add \
  --title "把 ingest.py 拆成 reader/parser 两个纯函数" \
  --goal  "降低 ingest.py 圈复杂度并允许独立单测" \
  --priority MEDIUM \
  --acceptance "uv run pytest tests/ingest 全绿" \
  --acceptance "ingest.py 中无对全局状态的读写"
```

记下输出里的 `card_id`。后续 `--depends <card_id>` 即指向它。

补充上下文 / 验收（可选，但建卡时建议立即补全；注意 `--path` 是命名参数，不是位置参数）：

```bash
# context（可重复加；--kind 默认 required）
uv run kanban card context add  <card_id> --path path/to/ref.py:42-88 \
    [--kind required|optional] [--note "..."]
uv run kanban card context list <card_id>
uv run kanban card context rm   <card_id> --path path/to/ref.py:42-88

# acceptance（add 追加 / edit 进 $EDITOR / rm 按 1-based 索引 / clear 清空）
uv run kanban card acceptance add   <card_id> --item "uv run pytest tests/foo 全绿"
uv run kanban card acceptance edit  <card_id>
uv run kanban card acceptance list  <card_id>
uv run kanban card acceptance rm    <card_id> 2
```

### Step 4 — 入队与跟进

```bash
uv run kanban list                 # 看全板（按状态分组）
uv run kanban show <card_id>       # 看单卡（含 context_refs / acceptance）
uv run kanban move <card_id> ready # 入待办
uv run kanban events <card_id>     # 看这张卡的事件流
```

被卡住时显式记录原因，不要直接放着不动（`reason` 是**位置参数**，不是 `--reason`；
`requeue` / `unblock` 的 `--to` 默认 `inbox`）：

```bash
uv run kanban block   <card_id> "等用户确认 schema"
uv run kanban unblock <card_id> [--to ready]    # 默认回 inbox
uv run kanban requeue <card_id> [--to ready] [--note "失败现场已修"]
```

### Step 5 — 推动执行（默认离线安全）

两种节奏，按需选一种：

```bash
# A) 一步一步走，便于讲解：
uv run kanban tick

# B) 一直跑到 idle（全部进 DONE 或全部 BLOCKED 时停）：
uv run kanban run
```

需要常驻 dispatcher（多卡并行 / 真 sub-agent）或浏览器看板时，见
**「后台服务（daemon & web UI）」**一节。

**只有用户明确要"真跑"时才加 `--executor agentao`**，例如：
`uv run kanban --executor agentao run`。否则一律保持默认 mock。

### Step 6 — 复盘

执行结束后按 **「察看卡片状态 / 产物」** 一节系统回看：先 `kanban events <id>` 看事件流，
再 `kanban traces <id> --latest` 看 agent 原始响应，必要时启动 Web UI（见
**「后台服务（daemon & web UI）」**）做可视化浏览。

> ⚠️ `kanban traces` 必须带 `<card_id>`（位置参数）；没有"列全部 trace"的子命令。
> 要全板复盘，遍历各卡或直接看 `<board>/traces/`。

把可分享的复盘写到 `workspace/reports/`，把临时数据放 `workspace/data/`。

## 后台服务（daemon & web UI）

两个常驻进程，**职责互不重叠**：

| 服务 | 进程职责 | 是否写板 | 默认端口 |
|---|---|---|---|
| `kanban daemon` | 调度 + 执行卡片，按 `.daemon.lock` 串行写板 | 是 | — |
| `kanban web` | 读板渲染浏览器看板 | 否（除非加 `--enable-writes`） | 8000 |

约定（让启停脚本是幂等的）：

- PID / 日志统一放在 board 目录下：
  - daemon → 由 CLI 自己管理 `<board>/.daemon.lock` 与 `<board>/daemon.log`。
  - web → 我们手动管 `<board>/.web.pid` 与 `<board>/web.log`。
- 默认 board 路径 `workspace/board/`；若用户在其他目录 `kanban init`，把下方命令里的
  `workspace/board` 替换为实际 `<board>`，或先 `cd` 到 `.kanban/` 所在目录再执行。

### 一键启停（"启动 kanban" / "启动 kanban 真跑" / "停止 kanban"）

复合触发短语，**优先于**单独启停 daemon / web UI 的子节：

| 用户说 | 动作（按顺序） |
|---|---|
| **"启动 kanban"** | ① `daemon --detach`（默认 mock 执行器） ② Web UI 后台启动 + 探活 + 自动开浏览器 |
| **"启动 kanban 真跑"** | ① `--executor agentao daemon --detach`（**真调** sub-agent） ② Web UI 同上 |
| **"停止 kanban"** | ① 停 Web UI（避免它再轮询已经撤掉的板） ② `daemon stop` |

启停脚本（`BOARD/PORT/URL` 共用，三段是同一组变量）：

```bash
BOARD=workspace/board
PORT=8000
URL="http://127.0.0.1:${PORT}/"
```

**▶ 启动 kanban（mock 执行器）**

```bash
# 1) Daemon — 后台 fork
uv run kanban daemon --detach

# 2) Web UI — 后台启动，幂等，端口就绪后自动开浏览器
if [ -f "$BOARD/.web.pid" ] && kill -0 "$(cat "$BOARD/.web.pid")" 2>/dev/null; then
  echo "web already running (pid $(cat "$BOARD/.web.pid"))"
else
  nohup uv run kanban web --host 127.0.0.1 --port "$PORT" \
    > "$BOARD/web.log" 2>&1 &
  echo $! > "$BOARD/.web.pid"
fi
for _ in $(seq 1 30); do
  curl -fsS "$URL" >/dev/null 2>&1 && break
  sleep 0.5
done
open "$URL"   # macOS;Linux 用 xdg-open;WSL 用 wslview

# 3) 验证
uv run kanban daemon status
uv run kanban list
```

**▶ 启动 kanban 真跑**（与上完全一致，**只把第 1) 步换掉**；要先和用户确认）

```bash
# 1) Daemon — 真调四个角色 sub-agent
uv run kanban --executor agentao daemon --detach
# 2)、3) 同 "启动 kanban"
```

> 触发"真跑"前的检查清单：
> - 用户**已明确**要真跑（不要从"启动 kanban"擅自升级到 `--executor agentao`）；
> - 板里有 `READY`/`INBOX` 的卡，且 acceptance / context 写齐；
> - 知晓真跑会消耗 LLM 配额、会真改 worktree 内文件。

**▶ 停止 kanban**

```bash
# 1) 先停 Web UI（幂等）
if [ -f "$BOARD/.web.pid" ]; then
  PID=$(cat "$BOARD/.web.pid")
  kill "$PID" 2>/dev/null && \
    for _ in $(seq 1 10); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
  rm -f "$BOARD/.web.pid"
  echo "web: stopped"
else
  echo "web: not running"
fi

# 2) 再停 daemon
uv run kanban daemon stop || echo "daemon: not running"
```

> 注意：执行器（mock vs `agentao`）由**启动 daemon 时**决定，重启 daemon 才能切换。
> 想从 mock 切到真跑，必须 "停止 kanban" → "启动 kanban 真跑"。

### Daemon · 启 / 停 / 体检

```bash
# 后台启动（fork 出去；CLI 自带 --detach）
uv run kanban daemon --detach

# 状态：running / stale / stopped
uv run kanban daemon status

# 跟踪日志
uv run kanban daemon logs -f

# 优雅停止（SIGTERM 给 .daemon.lock 里记录的 pid）
uv run kanban daemon stop

# 极少数情况下 daemon 已死但 lock 残留：
uv run kanban doctor --fix
```

并发 / 多 worker / 真 sub-agent 等高级用法（按需加，**默认不加**）：

```bash
# 多 worker（一个 scheduler + 多个 worker）
uv run kanban daemon --detach --role scheduler --max-claims 4
uv run kanban daemon --detach --role worker --worker-id w1
uv run kanban daemon --detach --role worker --worker-id w2

# 真跑（调用四个角色 sub-agent，需用户明确同意）
uv run kanban --executor agentao daemon --detach
```

### Web UI · 启 / 停（启动后自动打开浏览器）

`kanban web` 自身没有 `--detach`，所以由我们用 `nohup` 后台化并自管 PID。
启动后用 `curl` 探活，端口起来后再 `open` 浏览器，保证用户落地不是空白页。

**启动（推荐：默认只读、loopback、自动开浏览器）：**

```bash
BOARD=workspace/board
PORT=8000
URL="http://127.0.0.1:${PORT}/"

# 已经在跑就直接开浏览器，避免端口冲突
if [ -f "$BOARD/.web.pid" ] && kill -0 "$(cat "$BOARD/.web.pid")" 2>/dev/null; then
  echo "web already running (pid $(cat "$BOARD/.web.pid"))"
else
  nohup uv run kanban web --host 127.0.0.1 --port "$PORT" \
    > "$BOARD/web.log" 2>&1 &
  echo $! > "$BOARD/.web.pid"
fi

# 等端口就绪（最多 ~15s），就绪后开浏览器
for _ in $(seq 1 30); do
  curl -fsS "$URL" >/dev/null 2>&1 && break
  sleep 0.5
done
open "$URL"   # macOS;Linux 用 xdg-open;WSL 用 wslview
```

**状态：**

```bash
BOARD=workspace/board
if [ -f "$BOARD/.web.pid" ] && kill -0 "$(cat "$BOARD/.web.pid")" 2>/dev/null; then
  echo "web: running pid=$(cat "$BOARD/.web.pid")  log=$BOARD/web.log"
else
  echo "web: not running"
  [ -f "$BOARD/.web.pid" ] && rm "$BOARD/.web.pid"   # 清掉残留 pid
fi
```

**停止（幂等）：**

```bash
BOARD=workspace/board
if [ -f "$BOARD/.web.pid" ]; then
  PID=$(cat "$BOARD/.web.pid")
  kill "$PID" 2>/dev/null && \
    for _ in $(seq 1 10); do kill -0 "$PID" 2>/dev/null || break; sleep 0.2; done
  kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null   # 兜底强杀
  rm -f "$BOARD/.web.pid"
  echo "web: stopped"
else
  echo "web: not running"
fi
```

**写入模式（仅在用户明确要"用浏览器建卡"时再开）：**

```bash
# 只在 loopback 上开放 POST /api/cards
nohup uv run kanban web --host 127.0.0.1 --port 8000 --enable-writes \
  > workspace/board/web.log 2>&1 &
echo $! > workspace/board/.web.pid
```

绑非 loopback（远端访问）时还要加 `--allow-remote-writes`，且**必须**和用户确认后再做——
那等于把板的写入面暴露到网络上。

### 常见坑（后台服务专属）

- **端口被占**：`lsof -nP -iTCP:8000 -sTCP:LISTEN`。换端口（`--port 8765`）或杀掉占用进程。
- **`web` 起来但浏览器空白**：通常是探活早于路由就绪。把 `seq 1 30` 调到 `seq 1 60`，或先
  `tail workspace/board/web.log` 看启动日志。
- **`.web.pid` 指向已死进程**：`status` 块里的 `kill -0` 检查会发现并清理；如果你跳过了脚本，
  手动 `rm workspace/board/.web.pid`。
- **daemon 写不动板**：先 `kanban daemon status`；若 `stale` 用 `kanban doctor --fix`；如果只是
  本地误锁，`kanban --force …` 仅作应急。
- **同时跑 daemon + web**：完全 OK，两者职责不冲突；web 默认只读，daemon 才写。

## 察看卡片状态 / 产物

把"卡现在什么状态"和"卡到底产出了什么"分开看。两类问题对应不同的入口。

### A. 状态视图

| 想看什么 | 命令 | 备注 |
|---|---|---|
| 全板按状态分组 | `uv run kanban list` | 无 `--json` |
| 单卡详情（goal / priority / depends / context_refs / acceptance / status） | `uv run kanban show <id>` | 加 `--json` 喂下游 |
| 结构化事件流（含状态迁移、claim、artifact 快照） | `uv run kanban events <id>` | `--role planner\|worker\|reviewer\|verifier` 过滤；`--limit N`；`--json` |
| 活跃执行 claim（v0.1.2 并发） | `uv run kanban claims [<id>]` | `--json` |
| 在线 worker（多 worker 模式） | `uv run kanban workers` | — |
| 看 acceptance / context 是否齐全 | `uv run kanban card acceptance list <id>` / `card context list <id>` | — |

最常用的三连：

```bash
uv run kanban show   <id>                 # 这张卡是什么、配置齐了吗
uv run kanban events <id> --limit 20      # 最近发生了什么
uv run kanban claims <id>                 # 现在谁在跑（如有）
```

### B. 产物视图（"这张卡到底产出了什么"）

产物分布在 **4 个位置**，按卡的当前状态决定先去哪里看：

| 卡的状态 | 产物在哪 | 怎么看 |
|---|---|---|
| **DOING**（worktree 在线） | `workspace/worktrees/<card-id>/`（分支 `kanban/<card-id>`） | `uv run kanban worktree list`<br>`uv run kanban worktree diff <id>` |
| **DONE / BLOCKED**（worktree 已 detach） | `workspace/raw/<card-id>/artifacts-<ts>/` | `ls -dt workspace/raw/<id>/artifacts-*/ \| head -1` |
| 任意状态：agent 原始响应 | `<board>/traces/...` | `uv run kanban traces <id> [--latest] [--role …]` |
| 任意状态：结构化事件 | `<board>/events.log` | `uv run kanban events <id>` |

工作流要点（来自 `kanban-cli-guide.md §9.4`）：

- 产物**先**落到 live worktree；卡进入 `DONE` / `BLOCKED` 时 `WorktreeManager.detach()` 会把
  `gitignored` 路径下的产出抢救到 `workspace/raw/<card-id>/artifacts-<ts>/`，并写一条
  `worktree.artifacts_saved` 事件（含快照路径），`events`/Web UI 都能看到。
- 默认每卡保留最近 **5 份快照、上限 500 MiB**；环境变量 `KANBAN_ARTIFACTS_MAX_BYTES` 可调。
- 如果 worker 自报"已写文件"但卡完成后找不到，**第一站**永远是 `workspace/raw/<id>/artifacts-*/`。

### C. 三个最常被问的问题（一行流）

```bash
# "这张卡现在做到哪了？"
uv run kanban show <id> && uv run kanban events <id> --limit 20

# "worker 改了哪些文件？"
#   DOING：
uv run kanban worktree diff <id>
#   DONE/BLOCKED（找最新一份 artifact 快照并展开看）：
SNAP=$(ls -dt workspace/raw/<id>/artifacts-*/ 2>/dev/null | head -1) && \
  echo "$SNAP" && find "$SNAP" -maxdepth 3 -type f | head -50

# "agent 的原始输出是什么？"
uv run kanban traces <id> --latest
uv run kanban traces <id> --role worker     # 只看 worker 角色的全部 transcript
```

### D. 盘点 / 脚本化（多卡批量查看）

```bash
# 当前所有活跃 worktree（隐含：哪些卡在 DOING）
uv run kanban worktree list

# 某卡最近一份 artifact 快照路径
ls -dt workspace/raw/<id>/artifacts-*/ 2>/dev/null | head -1

# JSON 输出（喂下游脚本 / 喂 LLM 总结）
uv run kanban show   <id> --json
uv run kanban events <id> --json --limit 100
uv run kanban claims --json

# 全板事件复盘（最近 N 条；不带 card_id 即全板）
uv run kanban events --limit 200
```

> 注意：`kanban list` 目前**没有** `--json`；要程序化全板视图，遍历 `show --json` 或解析 `workspace/board/`
> 下的 Markdown frontmatter。

## 常见坑

- **"empty board"**：通常是 `.kanban/` 不存在且当前目录没有 `workspace/board/`。先 `kanban init`。
- **写命令报锁**：daemon 在跑。要么停 daemon (`daemon stop`)，要么真的应急再加 `--force`。
- **状态卡死在 BLOCKED**：用 `kanban events <id>` 找到原因，修后 `kanban unblock` 或 `requeue`。
- **多个卡片同主题**：考虑合并而非加 `--depends`；依赖只表达"必须先完成"，不表达"相关"。
- **改了 acceptance 之后**：用 `kanban card acceptance edit`，不要手改 `workspace/board/` 里的
  Markdown frontmatter（除非你确定 daemon 停了且板状态干净）。

## 与本仓库其他约定的关系

- **引用规范**：在向用户报告进度或写复盘时，遵循 `AGENTAO.md §Evidence Conventions`——每条事实
  要带 `path:line` 或 `[tool: kanban events <id>]` 这类 marker；推断结论标 `(unverified)` /
  `(inferred from X)`。
- **分类标签**：复盘里给问题打 `[CRITICAL]` / `[WARNING]` / `[SUGGESTION]` / `[NITPICK]`。
- **隐私**：板上的卡片内容默认按机密处理，不要把 `workspace/board/` 里的原文发到外部。

## 最小骨架命令清单（贴心备忘）

板与卡：

```bash
uv run kanban init                                  # 起板
uv run kanban card add --title T --goal G \
  --acceptance "..." [--depends ID] [--priority HIGH]
uv run kanban list
uv run kanban show <id>
uv run kanban move <id> ready
uv run kanban tick                                  # 单步推
uv run kanban run                                   # 跑到 idle
uv run kanban block   <id> "reason"                 # reason 是位置参数
uv run kanban unblock <id> [--to ready]             # 默认回 inbox
uv run kanban events  <id>
uv run kanban doctor [--fix]
```

后台服务（**复合触发优先；详见「一键启停」**）：

```bash
# 用户说 "启动 kanban"      → daemon (mock) + Web UI（自动开浏览器）
# 用户说 "启动 kanban 真跑" → daemon (--executor agentao) + Web UI
# 用户说 "停止 kanban"      → 先停 Web UI，再停 daemon

# 单独操作 Daemon
uv run kanban daemon --detach                       # 启（mock）
uv run kanban --executor agentao daemon --detach    # 启（真跑）
uv run kanban daemon status                         # 看
uv run kanban daemon logs -f                        # 跟踪
uv run kanban daemon stop                           # 停

# 单独操作 Web UI（详细启停见「Web UI · 启 / 停」一节）
nohup uv run kanban web --host 127.0.0.1 --port 8000 \
  > workspace/board/web.log 2>&1 & \
  echo $! > workspace/board/.web.pid && \
  for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:8000/ >/dev/null 2>&1 && break; sleep 0.5; done && \
  open http://127.0.0.1:8000/                       # 启 + 自动开浏览器
kill "$(cat workspace/board/.web.pid)" && rm workspace/board/.web.pid   # 停
```

察看状态 / 产物：

```bash
# 状态
uv run kanban show <id> [--json]                    # 单卡详情
uv run kanban events <id> [--limit N] [--role R] [--json]
uv run kanban claims [<id>] [--json]                # 谁在跑
uv run kanban workers                               # 多 worker 模式

# 产物
uv run kanban worktree list                         # DOING 时的活跃 worktree
uv run kanban worktree diff <id>                    # DOING 时 worker 的改动
ls -dt workspace/raw/<id>/artifacts-*/ | head -1    # DONE/BLOCKED 后的快照
uv run kanban traces <id> --latest [--role R]       # agent 原始 transcript
```
