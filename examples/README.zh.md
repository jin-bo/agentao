# Agentao 集成示例

> English: [README.md](./README.md)

[开发者指南第 7 部分](../developer-guide/zh/part-7/)的可运行伴随项目。每个子目录都是自给自足的工程——`cd` 进去、装依赖、跑起来。

## 经典嵌入形态（P0.6 · 离线 smoke 在 CI 跑）

六个最小形态样例，全部对接 fake LLM 端到端运行，**无需 API key**。每个都有自己的 `pyproject.toml`、≤ 50 行的 README，以及 `tests/` smoke 套件。

| 目录 | 宿主形态 | Smoke 测试 |
|------|---------|-----------|
| [`fastapi-background/`](./fastapi-background/) | FastAPI 路由 + asyncio 后台任务；每请求一个 `Agentao` | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |
| [`pytest-fixture/`](./pytest-fixture/) | 即插即用的 `agent` / `agent_with_reply` / `fake_llm_client` fixture | `uv sync --extra dev && uv run pytest tests/` |
| [`jupyter-session/`](./jupyter-session/) | 每个 kernel 一个 `Agentao`；`events()` 驱动展示 | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |
| [`slack-bot/`](./slack-bot/) | slack-bolt `app_mention` → 单轮对话；按频道作权限隔离 | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |
| [`wechat-bot/`](./wechat-bot/) | 微信轮询守护进程 → 单轮对话；按联系人作权限隔离 | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |
| [`protocol-injection/`](./protocol-injection/) | `agentao.host.protocols` 全部四个槽位都被替换（内存 FS、审计型 shell、可编程 MCP 注册表、字典 MemoryStore） | `uv sync --extra dev && PYTHONPATH=. uv run pytest tests/` |

## 完整蓝图（接真 LLM、端到端栈）

| # | 目录 | 蓝图 | 技术栈 | 启动 |
|---|------|------|--------|------|
| A | [`saas-assistant/`](./saas-assistant/) | SaaS 助手 API | Python · FastAPI · SSE | `uv run uvicorn app.main:app --reload` |
| B | [`ide-plugin-ts/`](./ide-plugin-ts/) | IDE / 编辑器插件 | TypeScript · VS Code | `npm install && npm run compile` |
| C | [`ticket-automation/`](./ticket-automation/) | 工单自动化分诊 | Python · 自定义 tool + skill | `uv run python -m src.triage "工单文本"` |
| D | [`data-workbench/`](./data-workbench/) | 数据分析工作台 | Python · DuckDB · matplotlib | `uv run python -m src.workbench` |
| E | [`batch-scheduler/`](./batch-scheduler/) | 夜间定时任务 | Python · cron / CronJob | `uv run python -m src.daily_digest` |

## 单文件演示

| 文件 | 演示内容 | 启动 |
|------|---------|------|
| [`headless_worker.py`](./headless_worker.py) | `ACPManager` 驱动一个内联的 mock ACP server（成功 / interaction-required / cancel 三条路径）。Week 1 回归基线 fixture，对应 [`docs/features/headless-runtime.md`](../docs/features/headless-runtime.md)。 | `uv run python examples/headless_worker.py` |
| [`host_events.py`](./host_events.py) | 公开的 harness 契约（自 0.3.1 起）：`agent.events()` 异步迭代器 + `agent.active_permissions()` 快照，与 `agent.arun(...)` 通过 `asyncio.gather` 并跑。详见 [`docs/api/host.md`](../docs/api/host.md)。 | `OPENAI_API_KEY=sk-... uv run python examples/host_events.py` |
| [`host_audit_pipeline.py`](./host_audit_pipeline.py) | 端到端的多租户审计管线：把 `agent.events()` 写入本地 SQLite 的 `agent_audit` 表，会话开始时 pin 一份 `active_permissions()` 快照，对话结束后 dump 整张表。配套 [`developer-guide §4.7`](../developer-guide/zh/part-4/7-host-contract.md)。 | `OPENAI_API_KEY=sk-... uv run python examples/host_audit_pipeline.py` |

## Persona 画廊（仅 `AGENTAO.md`，无代码）

不是集成示例——而是一组**真实使用过**的 `AGENTAO.md`（项目级提示词配置）。挑一份扔到你的项目根目录里改成自己的就行。详见 [`personas/`](./personas/README.zh.md)。

## Skills 画廊（`SKILL.md` + 辅助脚本，即插即用的能力包）

宿主无关的 skill，让嵌入的 agent 在运行时激活——和上面那些「宿主集成」样例正好互补：那边教你「壳怎么搭」，这里给你「料怎么放」。当前包含：`zootopia-ppt`、`pro-ppt`、`ocr`。把整个目录拷贝到 `~/.agentao/skills/`（全局）、`<项目>/.agentao/skills/`（项目级）或 `<项目>/skills/`（仓库根）任一位置，`SkillManager` 都会自动发现。详见 [`skills/`](./skills/README.zh.md)。

> Skills 也可以**就近**和宿主示例放在一起——前提是它只在那个宿主的工具 / 输出协议下有意义。仓库里已有三个蓝图这么做：[`data-workbench`](./data-workbench/.agentao/skills/)（`duckdb-analyst` + `matplotlib-charts`）、[`ticket-automation`](./ticket-automation/.agentao/skills/)（`support-triage`）、[`batch-scheduler`](./batch-scheduler/.agentao/skills/)（`daily-digest`）。上面那个画廊收的则是「宿主无关」的另一半。

## 约定

- **依赖独立**——每个项目都有自己的 `pyproject.toml` 或 `package.json`，互不共享；安装时**进到那个目录里**装。
- **能 mock 就 mock**——外部系统（CRM、Jira、RSS）一律用内存 fake 桩起来，除了 `OPENAI_API_KEY` 不需要别的凭证就能跑。
- **代码与文档同源**——示例里的代码片段都是从 Part 7 对应章节里**逐字搬来的**。如果发现不一致，**以指南为准**。
- **`.env.example`**——拷成 `.env`，填上 `OPENAI_API_KEY` 再运行。

## 环境要求

- Python ≥ 3.10，Python 示例需要 [`uv`](https://github.com/astral-sh/uv)
- Node ≥ 20 和 `npm`，用于 TypeScript 示例
- 一把可用的 `OPENAI_API_KEY`（或兼容的 provider——见各 README）

## 不包含的内容

- CI 接线——这些是参考工程，不是测试 harness
- 蓝图 B 的 VS Code Marketplace 发布
- 蓝图 E 的 `cronjob.yaml` 真正部署到 Kubernetes

哪个蓝图在干净环境里跑不通，到 Agentao 仓库提 issue 即可。
