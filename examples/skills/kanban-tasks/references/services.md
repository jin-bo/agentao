# 后台服务详解（daemon + Web UI）

两个常驻进程，**职责互不重叠**：

| 服务 | 进程职责 | 是否写板 | 默认端口 |
|---|---|---|---|
| `kanban daemon` | 调度 + 执行卡片，按 `.daemon.lock` 串行写板 | 是 | — |
| `kanban web` | 读板渲染浏览器看板（轮询） | 否（除非 `--enable-writes`） | 8000 |

约定：

- daemon 自管 `<board>/.daemon.lock` 和 `<board>/daemon.log`。
- web 没有 `--detach`，由 `scripts/kanban-up.sh` 用 `nohup` 后台化，PID 落在 `<board>/.web.pid`，日志落在 `<board>/web.log`。
- 默认 board 路径 `workspace/board/`。如果用户在别处 `init`，要么 `cd` 到 `.kanban/` 所在目录，要么在脚本里 `BOARD=<path>` 覆盖。

## 日常用脚本（推荐）

```bash
$SKILL_DIR/scripts/kanban-up.sh         # daemon (mock) + Web UI + 自动开浏览器
$SKILL_DIR/scripts/kanban-up-real.sh    # daemon (--executor agentao) + Web UI
$SKILL_DIR/scripts/kanban-down.sh       # 先停 web 再停 daemon
$SKILL_DIR/scripts/kanban-status.sh     # 一屏看 board/daemon/web/cards
```

环境变量覆盖：`BOARD=path/to/board PORT=8765 bash $SKILL_DIR/scripts/kanban-up.sh`。

## 真跑（`--executor agentao`）前置检查

1. 用户**已明确**要真跑（不要从"启动 kanban"擅自升级）。
2. 板里有 `READY` / `INBOX` 卡，且 acceptance / context 写齐。
3. 知晓真跑会消耗 LLM 配额、会真改 worktree 内文件。
4. 执行器**在 daemon 启动时定型**——切换必须 `kanban-down.sh` → `kanban-up-real.sh`。

## Web UI 写入模式

默认只读。仅在用户明确"用浏览器建卡"时再开 `--enable-writes`，并且：

- **绑 loopback**：只在本机可访问。直接 `--enable-writes` 即可。
- **绑非 loopback / 暴露到网络**：还要加 `--allow-remote-writes`，且**必须**和用户单独确认——这是把板的写入面暴露出去，不要随手开。

```bash
# 仅本机用浏览器建卡
nohup uv run kanban web --host 127.0.0.1 --port 8000 --enable-writes \
  > workspace/board/web.log 2>&1 &
echo $! > workspace/board/.web.pid
```

## 多 worker（v0.1.2 并发）

一个 scheduler + 多个 worker，scheduler 持锁分发，worker 不持锁只执行：

```bash
uv run kanban daemon --detach --role scheduler --max-claims 4
uv run kanban daemon --detach --role worker --worker-id w1
uv run kanban daemon --detach --role worker --worker-id w2

uv run kanban claims          # 看活跃 claim
uv run kanban workers         # 看在线 worker
uv run kanban recover         # 一次性 runtime 恢复（清残留 claim 等）
```

默认 `--role all`（scheduler + worker 同进程），日常足够。

## Multi-backend 执行器（profile 路由）

`--executor multi-backend` 走 `<cwd>/.kanban/agent_profiles.yaml`（fallback 到 `docs/agent_profiles.sample.yaml`）。每张卡按 profile + backend（`subagent` / `acp`）执行。`RouterPolicy` 可让 `kanban-router` agent 从角色候选中选 profile；router 不会覆盖卡片 pin / planner 推荐，任何失败都会降级到角色默认。

```bash
uv run kanban --executor multi-backend run
KANBAN_ROUTER=off uv run kanban --executor multi-backend run    # 关 router
uv run kanban profiles                       # 看当前 profile 配置
```

## 端口与日志

| 文件 | 内容 |
|---|---|
| `<board>/daemon.log` | daemon 全部 stdout/stderr |
| `<board>/web.log` | web UI 全部 stdout/stderr |
| `<board>/.daemon.lock` | TOML（pid / started_at），daemon 写 |
| `<board>/.web.pid` | nohup 后台化的 web pid，由脚本写 |

> 端口被占用：`lsof -nP -iTCP:8000 -sTCP:LISTEN`。换端口：`PORT=8765 bash $SKILL_DIR/scripts/kanban-up.sh`。
