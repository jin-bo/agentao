# 常见坑

## 板 / 锁

**"empty board" / 找不到卡** — 通常是 `.kanban/` 不存在且当前目录没有 `workspace/board/`：

```bash
ls -d .kanban workspace/board 2>&1 || true
uv run kanban init        # 没板就 init
```

**写命令报锁** — daemon 在跑。要么 `kanban daemon stop`，要么真的应急再加 `--force`：

```bash
uv run kanban daemon status
uv run kanban daemon stop                          # 优雅
uv run kanban --force move <id> ready              # 应急；不推荐常态用
```

**stale lock**（daemon 已死但 `.daemon.lock` 残留）：

```bash
uv run kanban doctor --fix
```

**状态卡死在 BLOCKED**：

```bash
uv run kanban events <id> --limit 50               # 找原因
uv run kanban requeue <id> --to ready --note "失败现场已修"
# 或
uv run kanban unblock <id> [--to ready]            # 默认回 inbox
```

## 后台服务

**端口被占用**：

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
PORT=8765 bash $SKILL_DIR/scripts/kanban-up.sh     # 换端口
```

**`web` 起来但浏览器空白**：通常是探活早于路由就绪。先看启动日志：

```bash
tail -f workspace/board/web.log
```

如果脚本里探活超时（`seq 1 60` = ~30s）仍空白，把循环加大或先确认端口没被防火墙拦。

**`.web.pid` 指向已死进程**：`kanban-status.sh` 会自动清理；手动也行：

```bash
rm -f workspace/board/.web.pid
```

**daemon 写不动板**：

```bash
uv run kanban daemon status            # running / stale / stopped
uv run kanban doctor --fix             # 清 stale lock
# 仅本地误锁应急：uv run kanban --force ...
```

**同时跑 daemon + web** 完全 OK：两者职责不冲突，web 默认只读。

**执行器切换不生效**：执行器在 daemon **启动时定型**。从 mock 切到真跑：

```bash
bash $SKILL_DIR/scripts/kanban-down.sh
bash $SKILL_DIR/scripts/kanban-up-real.sh
```

## 卡片配置

**改 acceptance / context** — 用 CLI，不要手改 `workspace/board/cards/*.md` 的 frontmatter（除非确认 daemon 停了且板状态干净）：

```bash
uv run kanban card acceptance edit <id>            # 开 $EDITOR
uv run kanban card context add  <id> --path ref:1-50
```

**多卡同主题但不真依赖** — 不要乱加 `--depends`，依赖只表达"必须先完成"。同主题考虑合并卡或并列建。

**位置参数 vs 命名参数**（最容易踩）：

| 命令 | 类型 |
|---|---|
| `kanban block <id> "reason"` | reason 是**位置** |
| `kanban unblock <id> [--to ready]` | `--to` 是**命名**，默认 inbox |
| `kanban requeue <id> [--to ready] [--note "..."]` | 都是**命名** |
| `kanban traces <id> --latest` | `<id>` **必填**位置 |
| `kanban card context add <id> --path X --kind Y` | `--path` 是**命名**，不是位置 |

## 产物找不到

**worker 自报"已写文件"但 DONE 后找不到** — **第一站永远是 `workspace/raw/<id>/artifacts-*/`**，但更直接：

```bash
uv run kanban result <id>     # 自动指向最新 artifact 快照路径
```

**artifact 被截断 / 部分缺失** — 是触发了 500 MiB 上限或单卡 5 份保留：

```bash
KANBAN_ARTIFACTS_MAX_BYTES=$((2 * 1024 * 1024 * 1024))    # 加到 2 GiB
# 写入 .envrc / shell rc 让它对未来执行生效；正在跑的 daemon 不会重读
```

denylist (`node_modules/` / `__pycache__/` / `.venv/` / `dist/` / `target/`) 内文件**不计入**，也**不会**复制到快照。

## CLI 输出不符合预期

**`kanban list` 没 `--json`** — 已知设计，要程序化全板视图遍历 `show --json` 或解析 `workspace/board/cards/*.md`。

**`kanban traces` 提示要 `<card_id>`** — 是位置参数；没有"列全部 trace"的子命令，要全板复盘看 `<board>/traces/`。
