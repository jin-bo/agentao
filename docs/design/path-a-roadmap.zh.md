# Path A Roadmap — 嵌入优先的路线图（2026-Q2）

**Status:** 战略决策记录。锁定 2026-04-30，经过 5 轮内部评审收敛。
**Audience:** Agentao 维护者与战略评审者。
**Related docs:**
- `docs/design/embedded-harness-contract.md` — 嵌入契约设计依据
- `docs/design/metacognitive-boundary.md` — 可注入元认知边界
- `docs/EMBEDDING.md` — 嵌入模式实操
- `docs/api/harness.md` — `agentao.harness` 公共 API 参考

---

## 1. Problem：为什么需要这份路线图

Agentao 在 0.3.1 落地了 `agentao.harness` 公共契约后，下一步路线图经过多轮"和竞品比"的评审，**逐渐偏离了自身的嵌入定位**——AGENTS.md、`agentao serve` daemon、跨平台 sandbox、bench 平台等条目陆续被加入 P0/P1，但其中超过半数实际服务的是 CLI 用户、远程部署者或营销叙事，**而不是嵌入主体**（README 自我宣称的"local-first, private-first, embeddable AI agents"）。

经第 4 轮逆向评审定位漂移识别 + 第 5 轮操作层堵 bug 后，本文固化"路径 A：被 Python 项目作为依赖嵌入"为唯一战略锚点，给出对应的瘦身路线图。

## 2. Decision：路径 A 锁定

### 2.1 成功画面（12 个月）

12 个月后 agentao 成功的标志是**被引用**，可通过下列指标观测：

| 指标 | 6 个月目标 | 12 个月目标 | 数据源 |
|---|---:|---:|---|
| PyPI weekly downloads | 500 | 2,000 | pypistats.org |
| GitHub real dependents（lighthouse adoption） | 3 | 15 | github.com/jin-bo/agentao/network/dependents |
| `agentao` 出现在他人 `pyproject.toml` | 5 仓 | 30 仓 | grep.app + sourcegraph |
| 嵌入相关 issue ÷ CLI 相关 issue | ≥ 1:1 | ≥ 2:1 | 手工 label |
| `agentao.harness` 公共 API break 次数 | 0 | 0 | `tests/test_harness_schema.py` |
| 下游嵌入示例 mypy strict 通过率 | 100% | 100% | example 仓 CI |

### 2.2 反指标（出现以下信号说明走偏到 B/C 路径）

- ❌ Stars 飙升但 dependents 没动 → 错误观众
- ❌ "请加 X CLI 命令" issue 多于"请暴露 X 嵌入接口"
- ❌ Twitter/HN 流量大但 PyPI downloads 不涨

### 2.3 明确不做的事（路径 A 视角下）

下列条目均服务次要人群或非产品需求，**移到 P2 或独立项目**：

- ✗ TUI 美化（opencode 152k stars 已占位）
- ✗ VSCode 扩展（Cline/Roo 已占位）
- ✗ Hosted SaaS（Anthropic/OpenHands 已占位）
- ✗ Rust/Go 重写（违背"嵌入 Python 宿主"主轴）
- ✗ AGENTS.md → 嵌入用户用 `Agentao(project_instructions=...)`，不读它
- ✗ `agentao serve` daemon → 与定位"in-process harness"形态冲突
- ✗ 跨平台强 sandbox → 嵌入主体已有 host 进程隔离
- ✗ 双语 SWE-bench → 应剥离到独立仓 `agentao-bench`
- ✗ A2A 网关 → 等协议稳定 + 真实需求出现再说
- ✗ Wasm sandbox → 同跨平台 sandbox

## 3. P0：嵌入摩擦的最小可发版集合

P0 的目标不是"加新能力"而是"降低嵌入摩擦"，让嵌入主体愿意把 `agentao` 写进自己的 `pyproject.toml`。

### 3.1 P0 工作项

| ID | 内容 | 类型 | 估时 |
|---|---|---|---:|
| P0.1 | `py.typed` 标记 + wheel force-include | additive | 1h |
| P0.2 | README 顶部翻转：embed-first 30 行示例打头，CLI 下移 | additive | 4h |
| P0.3 | clean-install + 嵌入构造 smoke CI（`pip install . && python -c "from agentao import Agentao; Agentao(project_instructions='hi')"`) | additive | 1h |
| P0.4 | 公开 harness API typing gate：`agentao.harness`、`Agentao.events()`、`active_permissions()`、能力注入参数对下游 strict type checker 友好 | additive | 3-5d |
| P0.5 | Lazy imports 全包改造（31 处 eager → lazy）：`tools/web.py:10` `bs4`、`memory/retriever.py:11` `jieba`、cli/* 全部 | additive | 1w |
| P0.6 | 嵌入示例 ×4：FastAPI background task / pytest fixture / Jupyter session / Slack bot | additive | 3-5d |
| P0.7 | 反退化测试：多 `Agentao()` 同进程不串状态 / 完整能力注入 / `arun` + `events` + `cancel` 并发 / 无 host logger 污染 / clean-install smoke | additive | 1w |
| P0.8 | JSONL audit sink：扩展 `agentao/replay/schema.py` v1.2 接纳 `tool_lifecycle` / `subagent_lifecycle` / `permission_decision` 三类 kind | additive | 3-5d |
| P0.9 | 依赖切分：core 6 项 + `[cli]` / `[web]` / `[i18n]` / `[full]` extras | **break** | 2-3d |
| P0.10 | `agentao` console script 加 friendly 缺包错误（"`pip install 'agentao[cli]'`"）+ 0.3.x → 0.4.0 迁移文档 | break-mitigation | 1-2d |

### 3.2 release 拆分

P0 跨三个 release 落地。**不一次性发 0.4.0**——拆分降低风险、保持版本号节奏、让 PyPI 信号可归因。

```
0.3.3  ─────────────────────────────────  Day 1（半天可 ship）
       P0.1  py.typed
       P0.2  README embed-first
       P0.3  clean-install smoke CI
       全部 additive，0 break

0.3.4  ─────────────────────────────────  Week 2-4
       P0.4  Public API typing gate
       P0.5  Lazy imports（内部重构，对用户不可见）
       P0.6  4 个嵌入示例
       P0.7  反退化测试
       P0.8  JSONL audit sink
       仍然 additive，0 break

0.4.0  ─────────────────────────────────  Week 5-8
       P0.9  依赖切分
       P0.10 console script friendly 错误 + 迁移文档
       唯一的 break，已被前两个 release 充分预备

总周期 ≈ 2 个月；版本号节奏：patch → patch → minor break。
```

### 3.3 0.4.0 迁移友好兜底

`pyproject.toml` 加一个 meta-extra：

```toml
[project.optional-dependencies]
full = ["agentao[cli,web,i18n]"]
```

迁移指引一句话：

> **0.4.0 break change**：依赖被切分。0.3.x 的"包含 CLI/中文检索/web 抓取"行为对应：
> ```
> pip install 'agentao[full]'    # 等价于 0.3.x
> pip install agentao            # 现在只装 6 个核心嵌入依赖
> ```

愿意手动加 `[full]` 的 CLI 用户 0 痛感；不升级的用户原地停 0.3.x 也能继续。

## 4. P1：嵌入主体真实扩展（3-6 个月）

仅当 P0 落地且 PyPI dependents 出现增长（≥ 3 个 lighthouse adopter）后再启动：

| ID | 内容 | 触发条件 |
|---|---|---|
| P1.1 | usage/cost callback：`on_usage_event(tokens, cost, model)` 让 host 接计费 | 任何嵌入主体提需求 |
| P1.2 | OTel exporter（在 P0.8 JSONL 之上） | 出现第一个企业用户 + 真实拓扑 |
| P1.3 | `agentao-skill-pack` bundle 格式：SKILL.md + tool manifest + boundary schema + permission profile | 2-3 个 adopter 表达"想带 skills 出货" |

**关键纪律**：P1 是需求驱动的，不是日历驱动的。**没有 lighthouse 需求佐证就不开始**——否则就是凭空设计。

## 5. P2：外部需求拉式触发

下列条目保留位置但**不主动推**，仅当外部需求明确出现时再考虑：

- AGENTS.md 支持（含 nested lookup 优先级链）
- `agentao serve` 长驻 daemon（WebSocket + SSE + HTTP 控制面）
- Sandbox backend interface（macos-sandbox-exec / linux-bubblewrap / nsjail / windows-noop）
- A2A/ACP 网关（等 LF A2A v1.0 + 真实需求）
- Wasm tool sandbox（仅插件子集）
- 双语 coding bench（独立仓 `agentao-bench`）

## 6. Day-1 行动清单

**今天可以执行的纯加法工作**（0.3.3 全部内容）：

```bash
# 1. py.typed (1h)
touch agentao/py.typed
# 改 pyproject.toml [tool.hatch.build.targets.wheel] force-include 加入 py.typed

# 2. README 翻转 (4h)
# README.md / README.zh.md 顶部加 30 行嵌入示例
# 现有 "Quick Start" CLI 内容下移到 "## CLI Quickstart" 二级标题

# 3. clean-install smoke CI (1h)
# .github/workflows/ci.yml 加 job：
#   pip install . && python -c "from agentao import Agentao; Agentao(project_instructions='hi')"

# 4. version + CHANGELOG
# agentao/__init__.py: __version__ = "0.3.3"
# CHANGELOG.md 新增 [0.3.3] 段，标 [Added]

# 5. ship
uv build && twine upload dist/*
```

**本周接下来要做的非代码工作**（最高 ROI）：

> 选 1 个 lighthouse 候选项目（中文社区 FastAPI 工具 / pytest plugin 仓库 / Jupyter 数据科学工作流），**亲自给对方提 PR**：`feat: optional agentao integration for X`。
>
> 一个 lighthouse adoption 比 100 个 stars 价值高——它制造出 `agentao` 在别人 `pyproject.toml` 里的第一行，让 GitHub dependents 图开始增长。

## 7. 失败模式与护栏

### 7.1 失败模式

**模式 1：Star 增长但 dependents 不动。**
偶然 Twitter/HN 火了之后，会有诱惑去优化 CLI 体验响应那波流量。**必须抵抗**——每次 stars 突增立刻看 PyPI dependents，没动就说明流量是错误观众。

**模式 2：P0 全做完但 6 个月没人嵌入。**
代码层完美的嵌入契约是必要不充分条件。如果 P0 做完 6 个月仍然 0 个 lighthouse adopter，问题是**分发不是技术**——所有 P1 应该停止，时间转到：写嵌入实战博客（中文 V2EX/掘金 + 英文 dev.to）+ 主动联系 5-10 个候选项目维护者 + 中文 AI Agent 社群推 demo。

### 7.2 硬护栏

**每月例行检查 `pypistats.org/agentao` 和 GitHub dependents。连续 3 个月都没动 → 路径 A 失败，必须开会重选 B/C 还是退**。

这不是悲观，是诚实的护栏——5 轮评审让我们意识到没有护栏的方向感都是幻觉。

## 8. 决策溯源

本文档是 5 轮内部评审收敛的结果：

| 轮 | 主要修正 |
|---|---|
| 1 | 起点报告：定位 + 8 条粗糙演进方向 |
| 2 | 4 条事实纠错 + 路线图重排 |
| 3 | 5 条战术修正 + 架构 interface |
| 4 | **战略转向**：定位漂移识别，A/B/C 选择，9 项 → 5 项瘦身 |
| 5 | 5 条实操修正：lazy import / console script / OTel 推迟 / skill-pack lighthouse-gated / mypy strict 范围 |

第 4 轮逆向评审是关键转折点——**让我们意识到三轮"打磨"改进的是错误目标**。第 5 轮把工程层 bug 堵住后，路线图固化为本文。

后续不再启动新一轮战略评审，**进入执行阶段**。如有重大外部信号（如 PyPI dependents 3 月连续不增、出现颠覆性外部生态变化），再启动单独的 review-and-pivot doc。

---

## 9. P0 实施细节

第 1–8 节是锁定的战略。本节是可执行的细化清单：每项的范围、目标文件、具体改动、验收标准、测试。文件引用以 2026-04-30 工作树为准；行号可能漂移——动手前请重新 grep。

每项格式：**Goal → Files → Changes → Accept → Tests → Risk**。

### 9.1 P0.1 — `py.typed` 标记（1h, additive）

- **Goal:** 让下游项目里的 mypy/pyright 识别 agentao 的类型注解，而不是把它当未注解的第三方包跳过。
- **Files:**
  - 新增：`agentao/py.typed`（空文件，PEP 561 标记）
  - 改：`pyproject.toml` 的 `[tool.hatch.build.targets.wheel]` `force-include`（当前只包含 `skills/skill-creator`）
- **Changes:**
  - `touch agentao/py.typed`
  - 扩展 `force-include`：加入 `"agentao/py.typed" = "agentao/py.typed"`，确保 wheel 打包不会丢（hatch 默认排除 dotfile + 非 Python 文件）。
- **Accept:**
  - `uv build && unzip -l dist/agentao-*.whl | grep py.typed` 返回 `agentao/py.typed`。
  - 下游 `mypy --strict` 在 `from agentao import Agentao` 上不再报 `Skipping analyzing "agentao": module is installed, but missing library stubs`。
- **Tests:** CI smoke 加一行：`python -c "import importlib.resources, agentao; assert importlib.resources.files('agentao').joinpath('py.typed').is_file()"`。
- **Risk:** 无，纯元数据。

### 9.2 P0.2 — README 嵌入优先翻转（4h, additive）

- **Goal:** 访问者看到的前 30 行是「在 Python 项目里嵌入」，而不是「装 CLI」。这是路径 A 下杠杆最高的「marketing as code」。
- **Files:** `README.md`、`README.zh.md`。
- **Changes:**
  - 在当前 `## Quick Start`（`README.md:21`）之上插入新 `## Embed in 30 lines` 段，包含：
    1. 一行安装：`pip install agentao`（**不**带 `[full]`——0.4.0 之后嵌入用户要的是最小核心）
    2. 最小 pure-injection 片段（镜像 `docs/EMBEDDING.md` 的「Pure-injection」块，**不要**用 env-discovery 的——pure injection 才是路径 A 的北极星）
    3. 一行链接到 `docs/EMBEDDING.md` 与 `docs/api/harness.md`
  - 把现有 `## Quick Start` 内容下移到嵌入段后面的 `## CLI Quickstart` 二级标题。
  - `README.zh.md` 完全镜像。
- **Accept:**
  - 横幅之后的第一个非 banner 标题是 `## Embed in 30 lines`。
  - 片段在只装了 `pip install agentao` 的全新 venv 里可以直接复制运行（先本地、然后 CI smoke）。
- **Tests:** P0.3 的 CI smoke 直接执行 README 里那段代码——飘移会让 CI 红。
- **Risk:** 老 CLI 用户可能不满。缓解：`## CLI Quickstart` 只在下面一屏；项目描述仍写「embeddable AI agents」。

### 9.3 P0.3 — clean-install + 嵌入构造 smoke（1h, additive）

- **Goal:** 每个 PR 都验证「`pip install agentao` 之后能在最小环境**构造**（不是只 import）`Agentao(...)`」。
- **Files:** `.github/workflows/ci.yml` 现有 `smoke` job（约 80–130 行）只 import 了 `Agentao` 但没真正构造。
- **Changes:** 在「Import check — package and public API」之后追加一步：
  ```yaml
  - name: Embedded-construct smoke (no env, no network)
    env:
      OPENAI_API_KEY: ""
      OPENAI_BASE_URL: ""
      OPENAI_MODEL: ""
    run: |
      python -c "
      from pathlib import Path
      from agentao import Agentao
      a = Agentao(
          working_directory=Path('.'),
          api_key='dummy', base_url='http://localhost:1', model='dummy',
          project_instructions='hi',
      )
      a.close()
      print('Embedded construct OK')
      "
  ```
  2026-04-30 已验证：`Agentao.__init__` 校验 `api_key`/`base_url`/`model` 非空，但**不**拨号——传 dummy 字符串能干净构造，无网络调用。这一步因此一次性证明两个不变量（无 env-discovery、无隐式网络）。
- **Accept:** 在没有除 `PATH` 之外环境变量的全新 runner 上 smoke job 绿。
- **Tests:** 这就是测试本身。同时在 `tests/test_imports.py` 镜像一份，让本地 dev 能比 CI 早一步发现退化。
- **Risk:** 如果未来改动让 `LLMClient.__init__` 真的开连接，这段会在 `http://localhost:1` 上挂或失败。那个失败模式正是 canary——刻意保留这个不可路由 URL，让退化大声响。

### 9.4 P0.4 — 公开 harness API typing gate（3-5d, additive）

- **Goal:** 下游用 `mypy --strict` 跑 `agentao.harness` 零报错。harness 是兼容性边界，**必须**对 strict type checker 干净。
- **Files:**
  - 审计：`agentao/harness/__init__.py`、`agentao/harness/models.py`、`agentao/harness/events.py`、`agentao/harness/projection.py`、`agentao/harness/schema.py`
  - 审计：`Agentao.events()` 与 `Agentao.active_permissions()` 的返回类型（`agentao/agent.py`）
  - 审计：`Agentao.__init__` 里所有能力注入 kwargs（从 `agentao/agent.py:75` 起的那段——`llm_client`, `logger`, `memory_manager`, `skill_manager`, `project_instructions`, `mcp_manager`, `mcp_registry`, `filesystem`, `shell`, `bg_store`, `sandbox_policy`, `replay_config`）
- **Changes:**
  1. `mypy --strict --package agentao.harness` 跑出来的每个错误都修（不能 `# type: ignore`）。
  2. 公开签名里所有 `Any` 替换为具体的 `Protocol`/Pydantic 类型。能力 protocol 已在 `agentao/capabilities/` 下；把公共部分从 `agentao.harness` 重新导出，让宿主只走一条 import 路径。
  3. 新增 `agentao/harness/protocols.py`，重新导出 `FileSystem`、`MCPRegistry`、`ShellExecutor`（当前在 `agentao.capabilities` 下），嵌入用户不必摸 `agentao.capabilities.*`。
  4. `docs/api/harness.md` 的 import 示例同步更新。
- **Accept:**
  - `uv run mypy --strict --package agentao.harness` 退出码 0。
  - 下游示例仓（在 P0.6 中创建）开 `strict = true` 跑 wheel 通过。
- **Tests:**
  - `tests/test_harness_schema.py` 增加运行时断言：`agentao.harness.__all__` 与 `docs/api/harness.md` 列出的集合一致（drift 检测）。
  - 新 `tests/test_harness_typing.py`：subprocess 跑 `mypy --strict` 一段 import 全部公开面的小脚本；dev 组没装 mypy 时跳过。
- **Risk:** 给能力 protocol 加类型可能要碰 `agentao/capabilities/*.py`。改动保持加法——别为了类型而把现有运行时类型收窄到破坏内部消费者。

### 9.5 P0.5 — 全包 lazy imports（1w, additive）

- **Goal:** 全新环境下 `import agentao` 不导入 `bs4`/`jieba`/`openai`/`rich`/`prompt_toolkit`/`readchar`/`filelock`。从不用 CLI 或 web tools 的嵌入宿主不为这些 wheel 付钱。
- **Files（2026-04-30 已核实的 eager imports）：**
  - `agentao/llm/client.py:10` — `from openai import OpenAI`
  - `agentao/tools/web.py:9-10` — `import httpx`、`from bs4 import BeautifulSoup`
  - `agentao/memory/retriever.py:11` — `import jieba`
  - `agentao/skills/registry.py:9` — `from filelock import FileLock`
  - `agentao/display.py:34-37` — `rich.{console,padding,syntax,text}`
  - `agentao/cli/_globals.py:6-7` — `rich.{console,theme}`
  - `agentao/cli/app.py:22-26` — `prompt_toolkit.*`
  - `agentao/cli/input_loop.py:13-15` — `readchar`、`prompt_toolkit`、`rich.markdown`
  - `agentao/cli/transport.py:8` — `readchar`
  - `agentao/cli/entrypoints.py:13-14` — `rich.{panel,prompt}`
  - `agentao/cli/commands.py:10-11`、`commands_ext/{acp,memory,agents,crystallize}.py`、`replay_render.py`、`replay_commands.py`、`ui.py`、`_utils.py`、`subcommands.py` — 散见 `rich`/`prompt_toolkit`/`readchar`
  - 路线图所引「31 处」是估数；上述清单是 ~20 个 distinct 顶层 import。把 ~20 视为下界，每次重构 PR 前重新审计；**不要碰**清单之外的文件。
- **Changes:**
  - 顶层导入的*第三方*库：下沉到函数/类作用域，或用 `TYPE_CHECKING` 包起来。
  - 包内 CLI 代码：`rich`/`prompt_toolkit`/`readchar` 收敛到 `agentao/cli/*` 边界（已经 95% 是这样），并保证非 CLI 模块不反向引用 `agentao.cli`。
  - 新增执行型测试：`tests/test_no_cli_deps_in_core.py` 用 `ast` 走 `agentao/` 下除 `agentao/cli/` 之外的所有 `.py`，遇到 `rich`/`prompt_toolkit`/`readchar`/`filelock` 引用即 fail。这是廉价的回归护栏。
  - 新增导入耗时测试：`tests/test_import_cost.py` 子进程跑 `python -X importtime -c "import agentao"`，断言上述第三方模块**不**出现在输出里。这是 P0.5 成功的规范不变量。
- **Accept:**
  - 只装 agentao 核心依赖（P0.9 后）的 venv，`python -c "import agentao; from agentao import Agentao"` 成功。
  - `python -X importtime -c "import agentao" 2>&1 | grep -E "bs4|jieba|openai|rich|prompt_toolkit|readchar|filelock"` 无输出。
- **Tests:** 上述两个新测试；现有套件保持绿。
- **Risk:** 热路径里的 lazy import 会增加每次调用开销。缓解：在*模块*边界懒，不在每次调用懒——用 `_X = None; def get_x(): global _X; if _X is None: import x as _X; return _X` 模式做模块级单例。

### 9.6 P0.6 — 4 个嵌入示例（3-5d, additive）

- **Goal:** 4 个可运行示例项目，每个演示一种典型嵌入形态。示例是一等公民：每个都带自己的 `pyproject.toml`、`README.md` 和 CI 步骤。
- **现有资产（不要重复造）：** `examples/harness_events.py`、`examples/headless_worker.py`、`examples/batch-scheduler/`、`examples/data-workbench/`、`examples/ide-plugin-ts/`、`examples/saas-assistant/`、`examples/ticket-automation/`。可作积木，但都不是我们要的「最小宿主单文件」样本。
- **Files（新增）：**
  - `examples/fastapi-background/` — FastAPI 路由把 Agentao 任务塞到后台；演示每请求一个 `Agentao(working_directory=...)`、transport 注入、客户端断连时 `arun()` 取消。与 `examples/saas-assistant/`（多租户 SaaS）区分——这个是 1 路由的最小样本。
  - `examples/pytest-fixture/` — `pytest` fixture 每用例 yield 一个带 fake `LLMClient`（复用 `tests/support/`）的 `Agentao`，下游测试套件可直接 copy-paste。
  - `examples/jupyter-session/` — 一个 `.ipynb`：内核生命周期内构造一次 Agentao，演示 `events()` 驱动 Jupyter widget。
  - `examples/slack-bot/` — slack-bolt 应用：每个 `app_mention` 映射成一次 Agentao turn，`permission_engine` 由 Slack channel 白名单注入。
- **Changes:**
  - 每个示例：`README.md`（≤ 50 行）、`pyproject.toml`（依赖来自 PyPI 的 `agentao`，**不**用 editable install）、可运行命令。
  - `examples/README.md` 加一张表，映射「宿主形态 → 示例目录」。
- **Accept:**
  - 每个示例的 `README.md` 给出可端到端跑通的命令，对 fake LLM 即可（不需要真实 API key）。
  - CI 矩阵新增 `examples` job，4 步分别 `pip install` 每个示例到全新 venv 并跑 smoke。
- **Tests:** 上述 CI 步骤*就是*测试。示例目录内**不**写单测。
- **Risk:** 示例比核心代码更易飘移。缓解：示例 `pyproject.toml` 锁 `agentao` 版本；改公开 API 的同一个 release PR 里同步 bump。

### 9.7 P0.7 — 嵌入契约反退化测试（1w, additive）

- **Goal:** 嵌入契约承诺的每条性质都至少有一个测试，破坏时大声 fail。
- **现有测试（审计；不要重复造）：** `test_harness_event_stream.py`、`test_active_permissions.py`、`test_harness_permission_events.py`、`test_harness_subagent_events.py`、`test_harness_tool_events.py`、`test_harness_schema.py`、`test_filesystem_capability_swap.py`、`test_mcp_registry_swap.py`、`test_shell_capability_swap.py`、`test_memory_store_swap.py`、`test_skill_manager_injection.py`、`test_mcp_manager_injection.py`、`test_llm_client_logger_injection.py`、`test_factory_build_from_environment.py`、`test_async_chat.py`、`test_no_subsystem_fallback_reads.py`、`test_per_session_cwd.py`。
- **新增测试：**
  1. `tests/test_multi_agentao_isolation.py` — 同进程构两个 `Agentao()`，各跑一 turn，断言：消息历史、skill 激活、权限状态、MCP 工具集、记忆写、replay 记录都不串。
  2. `tests/test_arun_events_cancel.py` — 启动 `agent.arun(prompt)`，另一 task 上挂 `events()` 订阅者，途中 `cancel()`，断言：取消传到工具层、events 流干净排空、无孤儿 asyncio task。
  3. `tests/test_no_host_logger_pollution.py` — `import agentao` 前后、`Agentao(...)` 构造前后采集 root logger 的 handlers/filters/level，断言 agentao 一个都不动。这是宿主最看重的性质。
  4. `tests/test_clean_install_smoke.py` — P0.3 那个 CI 步骤的本地镜像；subprocess `pip install dist/*.whl` 到 tmp venv 再跑嵌入片段。
- **Accept:** 4 个新测试都过；全套件绿。
- **Tests:** 不适用（这些*就是*测试）。
- **Risk:** 测试 4 需要网络或预构建 wheel artifact。打 `pytest -m slow` mark，CI 里只在打 wheel 的同一个 job 里跑。

### 9.8 P0.8 — harness 生命周期事件的 JSONL audit sink（3-5d, additive）

- **Goal:** JSONL replay 格式可承载 `tool_lifecycle`、`subagent_lifecycle`、`permission_decision`，让嵌入宿主有单一审计产物（而不是 replay + harness events 两套并行）。
- **Files:**
  - `agentao/replay/events.py` — 声明新 `EventKind` 常量与 v1.2 词表分区（`V1_2_NEW`, `V1_2`）
  - `agentao/replay/schema.py` — 扩展 `_kinds_for_version("1.2")`，发布 `schemas/replay-event-1.2.json`
  - `scripts/write_replay_schema.py` — bump 到生成 v1.2 文件
  - `agentao/harness/projection.py` — 加一个 sink，把每个 `HarnessEvent` 翻译成对应的 `ReplayEvent`，在 recorder 接好时投递
  - `agentao/replay/recorder.py` — 在允许集中接受新 kinds
- **Changes:**
  - schema 版本到 `1.2`。v1.0 / v1.1 schema 冻结，继续校验旧 replay——`docs/replay/schema-policy.md` 的向后兼容承诺保持。
  - 三个新 kind 的 payload 形状借用 `agentao/harness/models.py`（已是 Pydantic）——用 `model_json_schema()` 生成 JSON-Schema 片段，作为 `_kind_variant` 的每个 kind 变体内嵌进去。
- **Accept:**
  - `uv run python scripts/write_replay_schema.py` 产生 `schemas/replay-event-1.2.json`；`--check` 模式在 CI 通过（drift 检测已在 `.github/workflows/ci.yml:30` 接好）。
  - 来回测试：发出一个 `tool_lifecycle` harness 事件 → recorder 写 JSONL → reader 解析 → projection 还原成 `ToolLifecycleEvent` 与原 Pydantic 模型一致。
- **Tests:** 扩展 `tests/test_replay_schema.py` 与 `tests/test_event_schema_version.py`；新增 `tests/test_harness_to_replay_projection.py`。
- **Risk:** Pydantic 派生 schema 与手写 JSON Schema 风格可能漂移。缓解：在 `agentao/replay/schema.py` 留一个共享 helper，让 harness 与 replay 共用同一个 emitter。

### 9.9 P0.9 — 依赖切分为核心 + extras（2-3d, **break**）

- **Goal:** `pip install agentao` 装的是「能构造 `Agentao()` 并对 OpenAI 兼容 endpoint 调 `chat()`」的最小集。CLI/web/i18n 变成可选 extras。
- **Files:** `pyproject.toml` 的 `[project] dependencies` 与 `[project.optional-dependencies]`。
- **现状：** `dependencies` 列了 13 个包。已经有 `pdf`/`excel`/`image`/`crypto`/`google`/`crawl4ai`/`tokenizer` 加 `full` meta-extra——保留并加 3 个新。
- **目标核心（6 项）：**
  - `openai>=1.0.0`
  - `httpx>=0.25.0`
  - `pydantic>=2`
  - `pyyaml>=6.0.3`
  - `mcp>=1.26.0`
  - `python-dotenv>=1.0.0`（核心，因为某些嵌入路径还会读 `.env`；如果 P0.5 让那条路径懒加载，0.4.1 再降级到 extra）
- **新增 extras：**
  - `cli = ["rich>=13.0.0", "prompt-toolkit>=3.0.52", "readchar>=4.2.1", "pygments>=2.16.0"]`
  - `web = ["beautifulsoup4>=4.12.0"]`
  - `i18n = ["jieba>=0.42.1"]`
  - 扩展 `full = ["agentao[cli,web,i18n,pdf,excel,image,crypto,google,crawl4ai,tokenizer]"]`，老 `[full]` 用户无感。
- **核心装机不带 extras 的具体校验：** P0.5 必须先落地。如果纯核心 venv 撞到 `rich`/`bs4`/`jieba` 的 `ImportError`，那是 P0.5 的 bug，不是 P0.9 的 bug——回到源头修。
- **Accept:**
  - 全新 venv：`pip install agentao` 后，`python -c "from agentao import Agentao; Agentao(working_directory=__import__('pathlib').Path('.'), project_instructions='hi').close()"` 成功。
  - `pip install 'agentao[full]'` 重现 0.3.x 的依赖闭包（CI 比对 `pip freeze` 输出与签入的基线）。
- **Tests:** `tests/test_dependency_split.py` 与 `tests/data/full_extras_baseline.txt` 比对 freeze。
- **Risk:** 整个 P0 计划里**唯一**的 break。CHANGELOG 用 §3.3 的迁移表说清楚；0.3.4 release notes 里预告。

### 9.10 P0.10 — 缺包友好错误 + 迁移文档（1-2d, break-mitigation）

- **Goal:** 0.3.x → 0.4.0 的用户没加 `[cli]` 就跑 `agentao`，得到一行可执行的错误，而不是糟糕的 `ModuleNotFoundError: rich`。
- **Files:**
  - `agentao/cli/__init__.py`（或 `agentao/cli/entrypoints.py:entrypoint`）——把首次 `rich`/`prompt_toolkit` import 用 try/except 包起来
  - 新增：`docs/migration/0.3.x-to-0.4.0.md`
  - 更新：`CHANGELOG.md` 的 `[0.4.0]` 段、`README.md` 安装段
- **Changes（entrypoint shim 草图）：**
  ```python
  def entrypoint():
      try:
          from agentao.cli.app import run  # imports rich/prompt_toolkit
      except ImportError as e:
          missing = e.name or "a CLI dependency"
          import sys
          sys.stderr.write(
              f"agentao CLI requires extra packages (missing: {missing}).\n"
              f"  pip install 'agentao[cli]'   # CLI only\n"
              f"  pip install 'agentao[full]'  # 0.3.x compatible\n"
          )
          sys.exit(2)
      run()
  ```
- **Accept:**
  - 只装核心的 venv 里，`agentao` 退出码 2 并打印上面那段。
  - 同 venv，`pip install 'agentao[cli]' && agentao --help` 工作。
- **Tests:** `tests/test_cli_missing_dep_message.py` 用 subprocess + venv 验证那段消息。
- **Risk:** shim 本身可能因为有人往 `agentao.cli.__init__` 顶层加了非 CLI 依赖而退化。缓解：P0.5 的执行型测试（`test_no_cli_deps_in_core.py`）盯反向；本方向加一步 CI——核心 only venv 里 import `agentao.cli.entrypoints`，断言走到友好错误分支。

---

## 10. 顺序、依赖、关卡

### 10.1 硬顺序

```
P0.5 (lazy imports)  ─┬─►  P0.9 (依赖切分)  ──►  P0.10 (友好错误)
                      └─►  P0.3 (嵌入 smoke)*

P0.4 (typing gate)   ─►  P0.6 (承诺 mypy strict 的示例)
P0.8 (audit sink)    ─►  （独立；可与他项一起在 0.3.4 发）
P0.1, P0.2, P0.7     ─►  （无硬依赖）
```

\* P0.3 在 0.3.3 干净落地——2026-04-30 已直接验证：传 dummy creds 的裸构造无网络调用。之前担心的「需要先做 `LLMClient` 懒加载」已被实证否定。

### 10.2 每个 release 的过关条件

| Release | tag 之前必须过的关 |
|---|---|
| **0.3.3** | wheel 里有 P0.1 标记；P0.2 README 首段 diff approved；P0.3 smoke 在 Python 3.10/3.11/3.12 全绿 |
| **0.3.4** | P0.7 的 4 个新回归测试全绿；P0.6 至少 1 个示例的下游 mypy strict CI 绿；v1.2 schema 已生成且 `--check` 干净；P0.5 的 `python -X importtime` 不变量绿 |
| **0.4.0** | 全新 venv `pip install agentao`（不带 extras）能构造 `Agentao` 并对 fake LLM 跑通一 turn；`[full]` vs 0.3.x 基线的 freeze 差异仅在补丁级；核心 only venv 验证友好错误 |

任一关卡失败就不发版。**不要**用 `# type: ignore` 或环境变量花招绕过——关卡正是为了挡这些近路。

### 10.3 CHANGELOG 与版本机制

- 0.3.3 仅 `[Added]`（P0.1, P0.3）+ `[Changed]`（P0.2 README 结构）。
- 0.3.4 `[Added]`（P0.4, P0.6, P0.7, P0.8）+ `[Changed]`（P0.5 内部重构、行为不变）。
- 0.4.0 **以 `### Breaking changes` 开头**，含 §3.3 完整迁移表，再写 `[Added]`（P0.10）。
- 版本号在 `agentao/__init__.py` `__version__`（Hatch 读 `[tool.hatch.version] path = "agentao/__init__.py"`）。close release 的同一个 PR 里 bump。

---

## 11. 已完成 vs 净新增

这份盘点告诉执行者*不要*重做什么。已对 2026-04-30 工作树核实。

| 项 | 状态 | 证据 |
|---|---|---|
| P0.1 `py.typed` | **已落地（0.3.3，工作树）** | `agentao/py.typed` 已存在；`pyproject.toml` `force-include` 把它打进 wheel + sdist |
| P0.2 README 嵌入优先 | **已落地（0.3.3，工作树）** | `README.md` / `README.zh.md` 首段是 `## Embed in 30 lines` / `## 30 行嵌入`；CLI 走读放在 `## CLI Quickstart` / `## CLI 快速开始` |
| P0.3 clean-install smoke | **已落地（0.3.3，工作树）** | `.github/workflows/ci.yml` smoke job 用 README 片段原样构造 `Agentao(...)`，并断言 `py.typed` 在安装包内 |
| P0.4 typing gate | **部分** | `agentao/harness/__init__.py` 已导出干净表面；无 `mypy --strict` CI；无 `agentao.harness.protocols` 重导 |
| P0.5 lazy imports | **部分** | `agentao/__init__.py` 已用 PEP 562 懒导出 `Agentao`/`SkillManager`；§9.5 列的 eager 顶层 import 仍在 |
| P0.6 示例 | **部分** | `examples/` 有 5 目录 + 2 单文件，但 FastAPI/pytest/Jupyter/Slack 一个没有，4 个全部净新增 |
| P0.7 回归测试 | **大部分已有** | 列出的 17 个测试已存在；§9.7 的 4 个新测试是缺口 |
| P0.8 audit sink | **部分** | `agentao/replay/events.py` 在 v1.1；v1.2 词表、schema 文件、harness→replay projection 全部净新增 |
| P0.9 依赖切分 | **未做** | `pyproject.toml` `dependencies` 仍捆 13 个包，含 CLI/web/i18n |
| P0.10 友好错误 | **未做** | `agentao/cli/__init__.py` 无 shim；entrypoint 直接 import rich/prompt_toolkit |

净新增工作量加总约 **2 周专注工程**，与 §3.2 的 release 节奏（端到端约 2 个月，含评审、发版仪式、lighthouse 拓展）对得上。
