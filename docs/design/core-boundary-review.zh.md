# Core 边界审查（对照 codex，2026 年 5 月）

**状态：** 决策记录。2026-05-07 起草，经过四轮把 agentao 的 `agentao/*` 包结构对照 OpenAI codex 最近解耦后的 `codex-rs/core` crate 反复核对，每条结论都跟当前代码 grep 验证过。
**读者：** 准备做下一轮"哪些东西该出 core"重构的 agentao 维护者。
**对应：** `core-boundary-review.md`（英文版）。
**方法：** codex 现状摸底 → agentao core 审计 → 用真实 import 反向核对 → 对两个被标出的子包（`plugins/`、`session.py`）做深入调研 → 修订优先表。
**相关：**
- `docs/design/embedded-host-contract.md` —— 锁定本文遵循的"嵌入式 harness"定位。
- `docs/design/path-a-roadmap.md` —— 整体路线图；本文只覆盖一个切片。

---

## TL;DR

按 ROI 排序，**4 件事不该留在 agentao core**。前几轮意见里有 3 件经查证是错的，从待办清单退役。

**做（按顺序）：**

> **进度，2026-05-07：** 项目 #1 已落地（commit `0310eda`），#3（session.py 搬迁，commit `838a952`）已落地。#2（构造函数 callback 收紧）已落地（commit `e467c95`）。#4（权限引擎 API 重新设计）已落地（commit `0bb4a06`）。#5a（plugin validator/resolver 拆分）已落地（commit `c600cd4`）。表格保留原状以备追溯，已加 ✅ 标记。

1. ✅ **已完成。** **`replay/` 改成 `Transport` 订阅者。** 完全外移（让 replay 从 core facade 上彻底消失）涉及四类构件：
   - **顶层 import** —— `agent.py:25,31,36` 共 3 条语句，10 个名字。
   - **构造期 surface** —— 删 `agent.py:91` 的 `replay_config` 构造参数，以及 `agent.py:339-345` 初始化的 4 个实例属性（`_replay_recorder`、`_replay_adapter`、`_host_replay_sink`、`_replay_config`）。改由工厂层（`embedding/`）把 `ReplayRecorder` 注册成 Transport subscriber。
   - **6 个 facade 方法** —— 3 个 lifecycle 在 `agent.py:516-528`（`start_replay`、`end_replay`、`reload_replay_config`），3 个 observability 在 `agent.py:557-583`（`_latest_session_summary_id`、`_emit_context_compressed`、`_emit_session_summary_if_new`）。同时 `close()` 里 `agent.py:502` 的 `self.end_replay()` 改成 Transport 级别的 teardown hook。
   - **runtime 调用 / 属性读** —— `runtime/chat_loop.py` 6 处 `agent._emit_*(...)` 改走统一事件 emit；2 处属性读（`turn.py:82` 读 `agent._replay_adapter`、`llm_call.py:59` 读 `agent._replay_config.capture_flags`）改走 Transport 契约。

   3–5 天。构造期/状态删除不是廉价工——`turn.py:82` 当前直接驱动 `replay_adapter.begin_turn()` / `end_turn()`，把这些读改成 Transport 事件之后 recorder 才能自己持有状态，但迁移必须**一个 PR 落地**，否则中途 replay 就坏掉。

   **实际落地（2026-05-07）：**
   - `Transport.subscribe(listener)` 加入 Protocol（可选方法）；新的 `EventBroadcaster` helper 由 `NullTransport` / `SdkTransport` / `ACPTransport` 组合复用。
   - 新增 `EventType.TURN_BEGIN` / `EventType.TURN_END` 两类事件。runtime `turn.py` 通过 `agent.transport.emit(...)` emit；当 `ReplayAdapter` 已经 splice 进 transport 时，`_mirror` 把它们翻译成 `begin_turn`/`end_turn` 写入 recorder。manager 内部不需要单独的 listener 簿记，splice 本身就完成了路由。
   - 新增 `agentao/replay/manager.py::ReplayManager`：拥有 recorder + adapter + host_replay_sink + config；`start(session_id)` / `end()` / `reload_config()` lifecycle。
   - `agent.py`：4 个内部属性收敛为 1 个（`replay_manager`）；4 个只读 `@property` 视图（`_replay_recorder`、`_replay_adapter`、`_host_replay_sink`、`_replay_config`）作为 deprecation shim 保留；6 个 facade 方法（`start_replay`、`end_replay`、`reload_replay_config`、`_latest_session_summary_id`、`_emit_context_compressed`、`_emit_session_summary_if_new`）作为薄 delegate 保留（route 到 manager + observability 辅助函数）；`close()` 改为调用 `self.replay_manager.end()`。10 个顶层 replay import 改成 `TYPE_CHECKING` 限定。
   - `runtime/llm_call.py:59` 通过 `agent.replay_manager.config.capture_flags` 读取 `capture_flags`（无 manager 时返回 `{}`）。
   - `runtime/chat_loop.py` 5 处 `agent._emit_*` 调用未改 —— 它们走 agent 的 deprecation shim，shim 内部 lazy import `replay/observability.py`。文档"统一事件 emit"的意图已达成（shim 已经在 `transport.emit(...)`）。
   - `embedding/factory.py` 把 `replay_config` 从 `overrides` 里 pop 出来（不再走 ctor kwarg），然后构造完 agent 后 `agent.replay_manager = ReplayManager(agent, config=replay_config)`。
   - CLI 调用方（`cli/session.py`、`cli/replay_commands.py`、`cli/commands.py:635-637`）和 4 个测试文件不需改 —— 走 back-compat shim/property。迁移到直接调 manager 的方法是机会主义清理，不是必须。

   **测试：** 2549 通过、2 跳过，无回归。

   **没有发生的事：** 文档原想象 recorder 直接成为 inner transport 的纯 subscriber。实际上当前 transport-wrap（`ReplayAdapter` 包住 `agent.transport`）功能上就是一个在 inner emit *之后*运行的 subscriber——把它改造成 `transport.subscribe(listener)` 监听者，要么得重写 `_mirror` 全部 487 行、要么搞双层 wrap，而测试面用 `ReplayAdapter(transport, rec)` 直接构造。落地的设计保留 `ReplayAdapter` 作为翻译单元，`Transport.subscribe()` 留给*未来*非 replay 观察者使用（当前没有消费者）。
2. ✅ **已完成。** **`Agentao.__init__` 的 callback 签名收紧。** 把 8 个 deprecated callback（常见的 7 个 + `on_max_iterations_callback`）从公开构造函数移走，统一走 `embedding/compat.py`。`build_compat_transport` 已经在 `transport/sdk.py:82`，这是 API 边界收紧，不是物理搬家。1 天。

   **实际落地（2026-05-07）：**
   - 新增 `agentao/embedding/compat.py`：作为公开的迁移入口模块，从 `transport/sdk.py` 重导出 `build_compat_transport`（实现没有物理搬家，符合文档原意）。模块 docstring 写明推荐迁移路径——首选直接构造 `SdkTransport`，否则调用 `embedding.compat.build_compat_transport(...)` 把旧 8-callback 包成 transport 后通过 `transport=` 传给 `Agentao(...)`。
   - `Agentao.__init__` 仍接受 8 个 deprecated kwargs（向后兼容，0.5.0 移除），但**只要任意一个被设置就 emit 一次 `DeprecationWarning`**：列出全部 8 个名字，并指向 `embedding.compat.build_compat_transport`。预先构造 transport 的（推荐路径）不会触发 warning。内部仍然调用 `build_compat_transport`，保证现有测试 / CLI 不需要改。
   - docstring 加上迁移配方和 0.5.0 移除说明。
   - `transport/__init__.py` 和 `transport/sdk.py` 不变 —— `build_compat_transport` 在 `agentao.transport`（旧路径）和 `agentao.embedding.compat`（推荐路径）下都可 import。

   **测试：** 2549 通过、2 跳过。4 个走旧 callback 路径的测试（`test_tool_confirmation.py`、`test_reliability_prompt.py`）现在会触发新 `DeprecationWarning`（pytest warnings summary 中可见），但不 fail——它们本来就是测 deprecated 路径，与 0.5.0 kwarg 移除一起再迁移。

   **没有发生的事：** 文档 "API tightening" 的最终意图是从签名上彻底删掉这些 kwargs，但那是硬 breaking change，要等 0.5.0。这次 PR 让 deprecation 真正生效（warning + 明确迁移目标）；签名手术与 `harness/` / `agentao/session.py` 一起在 0.5.0 alias-removal release 里完成。
3. **`agentao/session.py` → `agentao/embedding/sessions.py`。** 纯磁盘持久化（305 行，`.agentao/sessions/*.json` 的 save/load/list/delete + 轮转）。`agent.py` / `runtime/` 都不 import 它。**生产侧 5 处 import + 7 处调用**总计；其中 **6 处需要新增显式 `project_root` plumbing**：`cli/session.py:55`、`cli/commands.py:532,560,567,575,590,609`。第 7 处 `acp/session_load.py:176` 已经在传 `project_root=cwd`（`cwd` 是 L160 `_parse_cwd(...)` 得到的局部变量），只需把 import path 切到新模块。**测试 4 处**：`tests/test_session.py:10,11,131`、`tests/test_acp_multi_session.py:80`、`tests/test_acp_session_load.py:55`、`tests/test_acp_mcp_injection.py:42`。迁移顺序：**(1) 一次变更里同时新增 `agentao/embedding/sessions.py` 并把 `agentao/session.py` 替换为包装 shim** —— 旧路径在整个迁移期间通过 shim 保持可用。(2) 更新生产 caller 显式向新路径传 `project_root`。(3) **然后才**在新路径上把 `project_root` 改为必填、删 `Path.cwd()` fallback（shim 上的 fallback 保留到 0.5.0）。测试侧 import 改写延后到 0.5.0 删 shim 时与 `harness/` 别名一起处理。1 天。
4. **权限文件 I/O 上移 `embedding/`。** ✅ **已落地。** 这是 engine API 重新设计，不是搬代码。`PermissionEngine.__init__` 之前自己调 `self._load_rules()` 读 `<user_root>/permissions.json`。4 处构造点都传 `project_root` + `user_root` 并依赖 engine 自己加载：`embedding/factory.py:141`、`agents/tools.py:585`、`acp/session_new.py:306`、`acp/session_load.py:199`。

   **实际落地（2026-05-07）：**
   - 新增 `agentao/embedding/permission_loader.py`，对外暴露 `load_permission_rules(*, project_root, user_root) -> (rules, sources)`，作为公开迁移面——一方调用方与嵌入 host 都通过它来预加载。
   - `PermissionEngine.__init__` 增加两个 keyword-only kwargs：`rules: Optional[List[Dict]]` 与 `loaded_sources: Optional[List[str]]`。当传入 `rules` 时（推荐路径），引擎**完全不读盘**，直接使用调用方预加载结果。
   - `_load_rules` 与 `_load_file` 已从 `PermissionEngine` 删除——文件 I/O 在物理上离开 `agentao/permissions.py`（同时清掉了不再用的 `json`、`logging` 导入）。老式 `PermissionEngine(project_root=..., user_root=...)` 形式仍然兼容：当 `rules is None` 时，构造器懒加载 `agentao.embedding.permission_loader.load_permission_rules` 并使用其返回。懒加载保证 `permissions.py` 模块加载图不依赖 `embedding/` 任何子包。
   - `embedding/__init__.py` 重新导出 `load_permission_rules`，让 host 可以从 `agentao.embedding` 直接导入。
   - 4 处一方调用点都改为先预加载：`embedding/factory.py`、`agents/tools.py`（子 agent 权限设置）、`acp/session_new.py`、`acp/session_load.py`。每处都用 `rules=` / `loaded_sources=` 构造引擎，所以生产时永远不走老式 auto-load 路径。
   - 测试保留老式 auto-load 路径——117+ 个测试位点用 `PermissionEngine(project_root=tmp_path, ...)` 当便利构造，并不测试文件加载本身，因此**没有**加 DeprecationWarning（加了反而会变成无意义噪声）。一个测试（`tests/test_active_permissions.py::test_active_permissions_does_not_re_read_disk`）原本 spy `engine._load_file` 来验证热路径无重复读盘，因为 `_load_file` 已删除，spy 改为 `agentao.embedding.permission_loader.load_permission_rules`；测试意图（active_permissions 不重复读盘）保持不变。

   **测试：** 2549 通过，2 跳过，无回归。

   **没做的部分：** 老式 auto-load 构造路径（`PermissionEngine(project_root=..., user_root=...)` 不传 `rules=`）保留，没有硬性 deprecate。把它收紧成硬错误属于未来 0.5.0 的 API 收尾，与其他 API 手术一起做；现在通过懒加载委托到 `embedding/permission_loader.py` 已经满足边界意图（文件 I/O 出 core），同时不会破坏 117+ 测试位点和已发布 example 中的便利写法。

**推迟（需要更深拆解或等 wheel 拆分阶段）：**

5. **`plugins/` 部分外移。** `plugins/models.py` + `hooks.py` **该留 core**（runtime 依赖它们）。`plugins/manager.py` + `manifest.py` + `diagnostics.py` 的 import 图里 `runtime/` 摸不到，可以外移。但 `plugins/skills.py` 和 `plugins/agents.py` 里**混了**runtime-path 的 validator 和 CLI-only 的 resolver，必须先拆开。2–3 天，敏感。
6. **`acp/` 拆 wheel。** 依赖方向是单向的：ACP 会 import core（`acp/models.py:25` 和 `acp/session_new.py:43` 都 `from agentao.agent import Agentao`，惰性 / TYPE_CHECKING），但 **core 不反向 import ACP**——grep `agent.py` / `runtime/` / `tools/` 找 `acp` 零命中。拆 wheel 安全，因为 ACP 可以作为下游 wheel 依赖 `agentao-core`；这是发包问题，不是 API 问题。
7. **`harness/` 别名删除。** 已经排在 0.5.0。

**从清单移除（事实错误）：**

- "host/ 和 harness/ 重复" —— 实为已规划好的 0.4.2→0.5.0 rename shim（`host/__init__.py:30-34` 写得很清楚）。
- "build_compat_transport 在 cli/" —— 已经在 `transport/sdk.py:82`。剩下的问题是签名，不是位置（由 #2 覆盖）。
- "MCP list ops 下放 `MCPRegistry.list_servers()`" —— 伪耦合。agentao 没有任何 LLM-facing 的 MCP list 工具；`/mcp list` 当前已经直调 `mcp_manager.get_server_status()`（`cli/commands.py:331`）。换成 `list_servers()` 反而会丢失连接状态和已注册 tool 数。codex 的 `#21281`（MCP enumeration → app-server）不能直接搬到 agentao——codex 那边有 LLM-callable 的 list op 要砍，agentao 从来就没有这个症状。详见 §4 行 G。

---

## 1. 方法论，以及它一直跑偏的原因

最初这份意见是经过三轮才到能用的形态：

- **第 1 轮**：列出 codex core 做的所有事，对照 agentao 的目录树，标出看起来不对的。产出一份 6 项的优先表。
- **第 2 轮（反向评审）**：同伴把第 1 轮逐条对照真实代码 fact-check，发现 6 处事实错误——其中 2 项工作已经做过、1 项被当成腐坏代码的其实是有文档的 shim、callback 数数错了、文件位置说错了、还有 1 项被标的子包（`acp/`）实际 core 一行都不 import。
- **第 3 轮**：修订优先表，框架保留，错的删掉。
- **第 4 轮（本文）**：对第 2 轮指出的两个盲点（`plugins/`、顶层 `session.py`）做带 grep 验证的深入调研。

**回写到 memory 的教训：** `feedback_core_boundary_review.md` —— 在这个 codebase 里，**有意为之的结构都是有文档的**。下次审查必须先 grep 反向引用、读每个子包的 `__init__.py` 头部 docstring，再下结论说"腐坏""重复""错位"。

---

## 2. Codex 现状（解耦后，2026 年 5 月）

codex 的 `codex-rs/core` crate 收敛成了**推理循环 + tool dispatch + 策略生成**。其余全部出 core，进 sibling crate。

| 留在 core | 推到 sibling |
|---|---|
| Turn loop / 推理状态机（`session/`、`codex_thread.rs`） | 消息历史 → `message-history` crate (#21278) |
| Tool 注册 + dispatch + 50+ handlers（`tools/`） | 线程命名 → `app-server` (#21260) |
| Approval 请求生成（`guardian/`） | `ListSkills` / `ListModels` op 删除 (#21282 / #21276) |
| Exec policy 解析 + sandbox 翻译 | MCP server 枚举 → `app-server` (#21281) |
| System prompt 组装（`context/`、`context_manager.rs`） | 插件加载 → `core-plugins`（观察） |
| Multi-agent 编排（LLM-callable） | Skill 加载 → `core-skills`（观察；watcher 半留半走） |
| MCP tool call dispatch（薄壳） | 线程摘要生成 → `app-server`（观察） |
| **Memory 注入 / session 重建**（memories pipeline 仍在 core，见 [`codex-rs/core/src/memories/README.md`](https://github.com/openai/codex/blob/main/codex-rs/core/src/memories/README.md)，标题就是 "Memories Pipeline (Core)"） | ~~持久化 memories → `memories` crate~~ —— **撤回**：早期草稿在没有 PR 引用的情况下列了这一条；复核时 codex main 仍把 memories pipeline + state DB 留在 core 里。`memories-mcp` 适配器（#20622）确实存在，但那是同一 core pipeline 的 MCP 外壳，不是迁移 |

**来源说明：** 带 `(#NNNNN)` 的行引用了已验证的 codex PR。标 **（观察）** 的行是 2026-05-07 从 commit message 和 crate 边界推断出的趋势，作为论据再用之前要重新对照 codex `main` 核一遍。memories 那行用删除线保留，方便后人看到撤回了什么、为什么撤回。

一句话规则：**core 产生事件；sibling 负责持久化、枚举、摘要、渲染**。

下面用这把尺子量 agentao。

---

## 3. Agentao core 审计（已验证）

agentao 没有显式 `core/` 目录；事实上的 core 入口是 `agentao/agent.py`（765 行）。逐子包对照：

| 子包 | codex 类比 | 结论 |
|---|---|---|
| `runtime/` (turn, chat_loop, llm_call, tool_executor) | core ✅ | 留 |
| `tools/` (handler 实现) | core ✅ | 留 |
| `tooling/` (registry、MCP/agent 注册 helper) | core ✅ | 留 |
| `capabilities/` (FileSystem/Shell/MCPRegistry 注入协议) | core ✅（公共注入面） | 留 |
| `permissions.py` + `permissions_hardline.py` | core ✅（engine），文件 I/O ❌ | engine 留；加载移走（#4） |
| `prompts/` (系统提示构造) | core ✅ | 留 |
| `plan/PlanSession` | core ✅（LLM-callable） | 留 |
| `agents/AgentManager` | core ✅（LLM-callable subagent） | 留 |
| `skills/SkillManager` | core ✅（注入），加载下放 | 留 |
| `memory/MemoryManager` | TYPE_CHECKING 注入式，已经可换实现 | 留 |
| `mcp/McpClientManager` | core 薄壳，agentao 不存在 LLM-facing list op | 留（详见 §4 行 G） |
| `host/` (events、schema、projection) | core ✅（公共合约包，0.4.2 从 `harness/` rename 而来） | 留 |
| `harness/` | Deprecation shim，0.5.0 删除（`__init__.py:1-25`） | #7（已计划） |
| `replay/`（顶层 import —— `agent.py:25,31,36` 共 3 条 import 语句，闭合 `)` 在 L30/L35/L40；构造参数 L91；4 个实例属性 L339-345；6 个 facade 方法 L516-583；`close()` teardown L502） | App-server 等价位；不该在 core | #1 |
| `acp/` + `acp_client/` | App-server 等价位 | 推迟（#6）；ACP import core（惰性 / TYPE_CHECKING `Agentao`），core 不反向 import ACP |
| `cli/` (22 文件 / 7175 行) | tui crate 等价位；逻辑上 OK，签名上经 8 个 callback 耦合 | #2 |
| `embedding/` (`build_from_environment`) | 工厂层，不是 core | 维持独立 |
| `sandbox/profiles` | 小；codex 类比是 `codex-sandboxing` crate | 推迟 |
| `security/` | 待审；codex 把它分散在 guardian + sandboxing | 开放问题 |
| `plugins/` | codex `core-plugins` 类比；部分重叠，见 §5 | #5 |
| `session.py` (顶层) | `message-history` crate 类比；纯磁盘持久化 | #3 |

---

## 4. 第 2 轮的事实纠正（避免被反复 relitigate）

第 1 轮优先表里以下 6 条经事实核对是错的，从清单退役：

| # | 第 1 轮的说法 | 实际情况 | 处置 |
|---|---|---|---|
| A | `host/` 和 `harness/` 重复或未对齐 | `host/__init__.py:30-34` 写明 0.4.2 rename，`harness/__init__.py:1-25` 是带 `DeprecationWarning` 的 shim，0.5.0 删除 | 错警；rename 是有意的 |
| B | `build_compat_transport` 在 `cli/`，应挪到 embedding | 已经在 `transport/sdk.py:82` | 真正的问题是签名（#2），不是位置 |
| C | `replay/` "在 core"，因为 import + chat_loop 订阅它 | 真实耦合涉及四类：(1) 顶层 import —— `agent.py:25,31,36` 共 3 条 import 语句（闭合 `)` 在 L30/L35/L40），(2) `Agentao.__init__` `replay_config` 参数 + 4 个属性 L91/339-345 + `close()` `end_replay()` L502，(3) 6 个 facade 方法 L516-583，(4) `chat_loop` 6 处 `_emit_*` 调用 + 2 处属性读（`turn.py:82`、`llm_call.py:59`） | #1 是 3–5 天；构造期/状态删除必须和 runtime 迁移一个 PR 落 |
| D | `__init__` 有 7 个 deprecated callback | 实际是 8 个（漏了 L71/247/260 的 `on_max_iterations_callback`） | 数错了一个；#2 工作项仍成立 |
| E | `acp/` 与 core 逻辑耦合 | 方向要分清：`acp/models.py:25` 和 `acp/session_new.py:43` import `agentao.agent.Agentao`（惰性 / TYPE_CHECKING），所以 ACP → core 是真实依赖。**core → ACP** 才是空的（grep `agent.py` / `runtime/` / `tools/` 找 `acp` 零命中），这正是 wheel 拆分要的方向 | 优先级降到"wheel 拆分阶段"；TL;DR 措辞已收紧 |
| F | CLI 是 18 文件 / 6057 行 | 实际 22 文件 / 7175 行（漏了 `commands_ext/` 子目录） | 数数错误 |
| G | "MCP list ops 是 LLM-facing 的；CLI/ACP 直调 `MCPRegistry.list_servers()` 即可去掉" | grep `tools/`、`tooling/` 全部 LLM 工具注册路径 → 不存在 LLM-facing 的 MCP list 工具。`/mcp list` 当前已经直调 `mcp_manager.get_server_status()`（`cli/commands.py:331`），不经 LLM。`MCPRegistry.list_servers()`（`mcp/registry.py:45`）只返回配置；换过去会丢失连接状态和每 server 的 tool 数。codex `#21281`（MCP enumeration → app-server）是在删 codex 那边*真实存在*的 LLM op；agentao 从来没这个症状 | 伪耦合——把 codex 标题硬套到 agentao 而没核 agentao 实际入口。从优先表整条删除 |
| H | "replay/ 在 `Agentao` 上有 4 个 facade 方法" | 实际 6 个：3 个 lifecycle（`start_replay` / `end_replay` / `reload_replay_config`，L516-528）+ 3 个 observability（`_latest_session_summary_id` / `_emit_context_compressed` / `_emit_session_summary_if_new`，L557-583） | 漏数 2 个；#1 工作面不变但描述要校准 |
| I | "session.py 改造涉及五处 import" | 生产 5 处 import + 7 处调用总计；其中 6 处需要新增 `project_root` plumbing（`cli/session.py:55`、`cli/commands.py:532,560,567,575,590,609`），第 7 处 `acp/session_load.py:176` 已经在传 `project_root=cwd`，只需切 import path；**测试 4 个文件**（`test_session.py`、`test_acp_multi_session.py`、`test_acp_session_load.py`、`test_acp_mcp_injection.py`）。Deprecation shim 用包装函数保留旧的宽松签名并委托给 `embedding.sessions` | 重新估时为 ~1 天（原"半天"）；按 call site 显式传 `project_root` 是工作量大头；测试 import 改写延后到 0.5.0 与 `harness/` 别名一起 |
| J | "权限文件 I/O 是半天的工厂层搬移" | `PermissionEngine.__init__` 自己调 `self._load_rules()` 并读 `<user_root>/permissions.json`。4 处 caller 都按 `(project_root, user_root)` 构造（`embedding/factory.py:141`、`agents/tools.py:585`、`acp/session_new.py:306`、`acp/session_load.py:199`）。搬移要求构造函数改造 + 4 处 caller 一起改 | engine API 重新设计而非外观搬动；改为 1–1.5 天，风险中等 |

---

## 5. 深入调研：`plugins/`

### 体量

9 文件，3360 行。`hooks.py` 单文件 1236 行。顶层 `__init__.py` 只 re-export 数据类（`LoadedPlugin`、`PluginManifest`、`PluginAgentDefinition`、…）。`PluginManager` 和 dispatcher 不在 `__all__`。

### 反向 import 全图（grep 验证）

| 调用方 | 引入 | 耦合 |
|---|---|---|
| `runtime/chat_loop.py:26` | `from ..plugins.models import StopHookResult`（**模块顶层**） | 🔴 硬运行时 |
| `runtime/chat_loop.py:582,656,726` | lazy `plugins.hooks`（lifecycle dispatch） | 🔴 每轮 |
| `runtime/tool_executor.py:102,586,608,632` | lazy `plugins.hooks`（每次 tool 调用） | 🔴 每次 tool |
| `agents/manager.py:106-107` | lazy `plugins.agents`、`plugins.models` | 🟡 init-time |
| `skills/manager.py:377-378` | lazy `plugins.skills`、`plugins.models` | 🟡 init-time |
| `cli/session.py:72,91` | lazy `plugins.hooks`（SessionStart/End） | ⚪ CLI |
| `cli/subcommands.py:250-411` | lazy `plugins.{manager, manifest, skills, agents, mcp, diagnostics, hooks}` | ⚪ CLI |
| `cli/entrypoints.py:44,66` | lazy `plugins.hooks` | ⚪ CLI |

### 与 codex 对照

codex 把 plugin **加载**（manifest 解析、市场同步、安装）和 plugin **hook dispatch**（lifecycle 事件触发、信任元数据强制）拆分。加载去了 `core-plugins`；hook dispatch **留在 core**（#19905 compact lifecycle hooks、#20321 hook trust metadata）。

按这个模子量 agentao：

- `models.py`（322 行）—— `runtime/chat_loop.py:26` 需要 `StopHookResult`。**留**。
- `hooks.py`（1236 行）—— 每次 tool 调用、每轮 chat loop 都要。**留**。
- `manager.py`（522 行）—— `PluginManager` 发现/加载。只在 init 时被调，外加 CLI。**可外移**。
- `manifest.py`（476 行）—— `PluginManifestParser`。同 manager。**可外移**。
- `diagnostics.py`（74 行）—— CLI-only。**该外移**。
- `mcp.py`（144 行）—— 只有 `resolve_plugin_mcp_servers` / `merge_plugin_mcp_servers`，**没有 `validate_no_external_collisions`**。唯一 consumer 是 `cli/subcommands.py:318`（`/plugins sync`）。纯 CLI/loader。**该外移**。
- `skills.py`（369 行）、`agents.py`（190 行）—— **混着**。每个里都有 `validate_no_external_collisions`（被 `skills/manager.py`、`agents/manager.py` 在 agent init 时调用，runtime 路径）+ `resolve_plugin_entries` / `resolve_plugin_agents`（CLI-only）。不能整体外移。

### 建议

不是快速可完成的事。两阶段拆：

**阶段 5a（敏感，2–3 天）：** ✅ **已完成。** 在 `plugins/skills.py` 和 `plugins/agents.py` 内部把 validator（runtime 路径）和 resolver（CLI）分开。Validator 留 core；resolver 外移到新的 `plugins/resolvers/` 或 embedding。`plugins/mcp.py` 不需要做这层拆分——它没有 validator 面。

**实际落地内容（2026-05-07）：**
- 新增 `agentao/plugins/resolvers/` 包：`resolvers/skills.py` 收纳 `resolve_plugin_entries` 加 8 个私有 helper（`_resolve_skills`、`_parse_skill_md`、`_resolve_commands`、`_scan_commands_dir`、`_md_file_to_entry`、`_metadata_to_entry`、`_check_internal_collisions`、`_parse_yaml_frontmatter`）；`resolvers/agents.py` 收纳 `resolve_plugin_agents` 加 4 个私有 helper。`__init__.py` 重导出 `resolve_plugin_entries` / `resolve_plugin_agents`，模块 docstring 把 runtime/loader 拆分原由和 5b 外移计划写清。
- `agentao/plugins/skills.py` 和 `agentao/plugins/agents.py` 瘦身成只剩 validator——每个模块只导出一个 `validate_no_external_collisions` 函数。模块 docstring 注明 runtime 调用路径（`SkillManager.register_plugin_skills` / `AgentManager.register_plugin_agents`）并指向 resolver 包。
- 顺手删了死代码：旧 `plugins/skills.py` 里定义的 `PluginSkillCollisionError` 全仓库 grep 无任何引用——拆分时直接删掉，不带进任一侧。
- `agentao/cli/subcommands.py` 的两处 CLI import 站点（`_plugin_list_cli` 和 `_load_and_register_plugins`）改指 `..plugins.resolvers.skills` / `..plugins.resolvers.agents`。Runtime 调用方（`agentao/skills/manager.py:378`、`agentao/agents/manager.py:106`）继续从 `agentao.plugins.skills` / `agentao.plugins.agents` 导入 `validate_no_external_collisions`——这两个路径现在已经是纯 validator，不再加载任何 resolution 代码。
- 三个测试文件迁移 import：`tests/test_plugin_skills.py` 和 `tests/test_plugin_agents.py` 把 import 拆向 resolver 模块和 validator 模块；`tests/test_plugin_loader.py` 改了两处 `resolve_plugin_entries` import。
- `plugins/skills.py` / `plugins/agents.py` **没有**留向后兼容 shim。Resolver 和 validator 都是包内私有面（不在 `agentao.plugins.__all__` 里），调用方全是一方代码——加重导出 shim 只会变成"形状不对的噪声"。

**测试：** 2549 通过、2 跳过。无回归。

**没做的事：** 阶段 5b 仍待执行——`manager.py`、`manifest.py`、`diagnostics.py`、`mcp.py` 加新的 `resolvers/` 包还没真的搬到 `embedding/plugins/`。本轮拆分是让 5b 变成纯机械搬迁的前置（resolver 不再混 validator），实际搬迁按优先表的安排另起 PR。

**阶段 5b（机械，1 天）：** 5a 落地后，把 `manager.py` + `manifest.py` + `diagnostics.py` + `mcp.py` + 新 resolver 一起外移到 `agentao-plugins-loader/`（或 `embedding/plugins/`）。`runtime/` 和 `agent.py` 一行 import 都不用改。

**为什么推迟：** validator/resolver 拆分要小心穿过 `SkillManager.__init__` / `AgentManager.__init__`。在赶进度时改这块容易在没明显测试信号的情况下破坏 plugin 发现路径。#1–#4 ROI 更高，先落地。

---

## 6. 深入调研：顶层 `session.py`

### 是什么

`agentao/session.py`（305 行，纯 I/O）：

- `save_session` → 写 `.agentao/sessions/{ts}.json`
- `load_session` / `list_sessions` / `delete_session` / `delete_all_sessions`
- `strip_system_reminders`、`format_session_time_local` 工具函数
- `_MAX_SESSIONS = 10` 自动轮转

### 反向 import 全图（grep 验证）

过滤掉 `plan/.session` 和 `cli/.session` 噪音（不同模块）后，顶层 `agentao/session.py` 真实消费者：**生产 5 处 + 测试 4 处**。

**生产（5）：**

| 调用方 | 用法 |
|---|---|
| `acp/session_load.py:68` | `from agentao.session import load_session`（模块顶层） |
| `cli/session.py:52` | `from ..session import save_session`（在 `on_session_end` 内 lazy import） |
| `cli/commands.py:519` | `from ..session import (delete_all_sessions, delete_session, format_session_time_local, list_sessions)`（`/sessions` 内 lazy） |
| `cli/commands.py:588` | `from ..session import list_sessions, load_session`（`/resume` 内 lazy） |
| `cli/replay_commands.py:22` | `from ..session import strip_system_reminders`（模块顶层） |

**测试（4）：**

| 调用方 | 用法 |
|---|---|
| `tests/test_session.py:10,11,131` | `import agentao.session as session_module`；`from agentao.session import (...)`；`from agentao.session import _rotate_sessions` |
| `tests/test_acp_multi_session.py:80` | `from agentao.session import save_session` |
| `tests/test_acp_session_load.py:55` | `from agentao.session import save_session` |
| `tests/test_acp_mcp_injection.py:42` | `from agentao.session import save_session` |

九处全部位于 CLI / ACP / 测试，**`agent.py` 和 `runtime/*.py` 都不 import 它**。grep 验证。Deprecation shim 在原路径暴露包装函数，保留旧的宽松签名（`project_root: Optional[Path] = None`）并在缺省时补 `Path.cwd()` 后委托给 `embedding.sessions.*`，9 处运行时继续可用；3 个 ACP 测试 import 改写延后到 0.5.0 与 `harness/` 别名一起处理。**`tests/test_session.py` 是唯一例外**：它的 `isolated_session_dir` fixture monkeypatch 旧模块上的私有 `_session_dir`，而包装 shim 机制在结构上无法跨模块边界转发对私有 helper 的 monkeypatch（shim 的 wrapper 调 `embedding.sessions.save_session`，后者按词法作用域查找自己模块里的 `_session_dir`）。该 fixture 的 patch 目标必须**立即**改成 `agentao.embedding.sessions._session_dir`，是单行改动；公开签名和断言不变。

### 和 `host/` 的 session 概念有重叠吗？

没有。`host/` 里 30+ 处 `session_id` 都是事件流过滤和 projection 链路用的相关性 ID（`host/events.py:73,84,89`、`host/projection.py:115-296`、`host/models.py:83,98,111`）。它们是**身份标识**，不是持久化。两层用同一个词指代不同概念，没有冲突；应该在 `docs/api/host.md` 加一句澄清，避免后人混淆。

### 和 codex 的对照

codex 通过 #21278 把消息历史外移（独立 `message-history` crate），通过 `thread-store` 处理线程元数据。agentao 的 `session.py` 大致是这两件事的并集——消息历史 *加上* title/timestamp/active-skills，每个 session 一个 JSON。

正是 codex 推出 core 的那种"事务性持久化"。

### 额外发现：`Path.cwd()` fallback

`session.py:29`：传入 `project_root=None` 时通过 `Path.cwd()` 兜底。这是**全局状态读取**——发生在本应纯函数的代码里——和嵌入式 harness "无全局状态"原则冲突（见 `project_agentao_embedded_harness` memory）。fallback 是历史 CLI 兼容性留下的，搬移时该顺手删。

### 建议

挪到 `agentao/embedding/sessions.py`。**顺序很重要**——如果先把 `project_root` 改成必填、再去更新 CLI/ACP 调用点，每次 save / list / resume / delete 都会立即 TypeError：

1. **一次变更**：新增 `agentao/embedding/sessions.py`（305 → 删 cwd fallback 后约 280 行）**并把 `agentao/session.py` 替换为下面第 3 步描述的包装 shim**。这次变更后两个文件同时存在，旧 import 路径不会断窗。新路径在这一步保持 `project_root` 可选，便于后续迁移再收紧。
2. 改五处生产 import，**并在每处显式向新路径传入 `project_root`**（shim 只服务于外部用户和测试）：
   - `cli/session.py:55` —— `save_session(..., project_root=cli.agent.working_directory)`（在 `on_session_end` 里）。
   - `cli/commands.py:532, 560, 590` —— `list_sessions(project_root=cli.agent.working_directory)`（`/sessions` 和 `/resume`）。
   - `cli/commands.py:567` —— `delete_all_sessions(project_root=cli.agent.working_directory)`。
   - `cli/commands.py:575` —— `delete_session(sub_arg, project_root=cli.agent.working_directory)`。
   - `cli/commands.py:609` —— `load_session(match["id"], project_root=cli.agent.working_directory)`。
   - `acp/session_load.py:176` —— 已经传 `project_root=cwd`（`cwd` 是 L160 `_parse_cwd(params.get("cwd"))` 得到的局部变量）；除了把 import 路径改到 `agentao.embedding.sessions`，调用本身不需要改。
   - `cli/replay_commands.py:22` —— 只 import `strip_system_reminders`（纯函数，没有 `project_root`），除了 import 路径外不需要改。
3. 在原位 `agentao/session.py` 留 deprecation shim 并发 `DeprecationWarning`。**形似 `harness/` → `host/`，但机制不同**：纯 `from agentao.embedding.sessions import *` re-export 会继承新路径上的「`project_root` 必填」签名，把现有 caller 全打挂。正确做法是 shim 里写包装函数，**保留旧的宽松签名**（`project_root: Optional[Path] = None`），在每个包装函数内部做 `project_root = project_root or Path.cwd()`，再委托给 `agentao.embedding.sessions.{save,load,list,delete,...}_session(...)`。这样外部用户和 4 个测试文件继续可用，又不让 `Path.cwd()` 这个全局污染回新路径。
4. 测试位点：3 个 ACP 测试（`tests/test_acp_multi_session.py`、`tests/test_acp_session_load.py`、`tests/test_acp_mcp_injection.py`）只调公开函数，通过 shim 继续可用，0.5.0 删 shim 时再迁移。**`tests/test_session.py` 是例外** —— 它的 `isolated_session_dir` fixture monkeypatch 私有 `_session_dir` helper，包装 shim 机制无法跨模块边界转发对私有 helper 的 patch（shim 的 wrapper 调 `embedding.sessions.save_session`，后者按词法作用域查找自己模块的 `_session_dir`）。该 fixture 的 patch 目标必须**立即**改成 `agentao.embedding.sessions._session_dir`；断言和公开签名不变。
5. **然后**才在 `embedding/sessions.py` 这一新 API 上把 `project_root` 改为必填（无默认），并在新路径上删掉 `Path.cwd()` fallback。shim 上的 fallback 保留到 0.5.0。
6. 0.5.0 时和 `harness/` 别名一起把 `agentao/session.py` 删了；把 4 个测试文件迁到 `agentao.embedding.sessions` 并显式传 `project_root`。

**工作量：** 1 天（从"半天"上调——按 call site 显式传 `project_root` 是工作量的大头）。**ROI：** 高——每删掉一个"顶层在主包里、但 core 不 import"的模块，core 边界就清晰一分。

---

## 7. 修订后的优先表

| # | 动作 | 工作量 | 风险 | ROI |
|---|---|---|---|---|
| 1 | replay → Transport subscriber：删 10 个顶层 import 名 + **构造参数 `replay_config` + 4 个实例属性（L91/339-345）** + **6 个 facade 方法**（L516-583）+ `close()` teardown（L502），改写 `chat_loop` 6 处 `agent._emit_*(...)`，迁移 2 处属性读（`turn.py:82`、`llm_call.py:59`）；recorder 由 `embedding/` 工厂作为 Transport subscriber 装配 | 3–5 天 | 中 | 🟢 高 |
| 2 | 收紧 `Agentao.__init__`：8 个 deprecated callback 移到 `embedding/compat.py` | 1 天 | 低 | 🟢 高 |
| 3 | `session.py` → `embedding/sessions.py`；按 call site 显式传 `project_root`（生产 7 处调用中 6 处需要新 plumbing，第 7 处 ACP load 只需切 import path）；shim 保留 `Path.cwd()` fallback + 可选 `project_root` 直到 0.5.0；新路径上删除两者 | 1 天 | 低 | 🟢 高 |
| 4 | Permissions 文件 I/O 上移 `embedding/`——**engine API 重新设计**（构造函数改造 + 4 处 caller 更新） | 1–1.5 天 | 中 | 🟡 中 |
| 5a | ✅ `plugins/skills.py`、`plugins/agents.py`——拆 validator/resolver | 2–3 天 | 中 | 🟡 中 |
| 5b | 5a 落地后外移 `plugins/{manager, manifest, diagnostics, resolvers}` | 1 天 | 低 | ⚪ 长期 |
| 6 | `acp/` 拆 wheel | — | — | ⚪ 长期（无逻辑耦合） |
| 7 | 0.5.0 删 `agentao.harness/` 别名 | 半小时 | 零 | ⚪ 已计划 |

从优先表删除的项：

- **MCP list ops**（早期草稿的 #4）—— 伪耦合。agentao 没有 LLM-facing MCP list 工具；`/mcp list` 当前已经直调 `mcp_manager.get_server_status()`。详见 §4 行 G。

仍待审的开放问题：

- `agentao/security/` —— 里面到底是什么？codex 把它分到 `guardian/` + `codex-sandboxing` 是否更干净？
- `agentao/sandbox/profiles` —— 小到可以不动，但值得确认没有"应该在 capabilities/"的协议。

---

## 8. 建议执行顺序

三批：

**Batch A（高 ROI，约 5–7 天，每条可独立 PR）：** #1、#2、#3（顺序无所谓，互不依赖）。#1 占了大头——但和早先的圈定不同，构造期 / 状态删除必须和 runtime 迁移**一个 PR 一起出**（否则 replay 会中途坏掉），所以"先做 chat_loop 那一刀"对这一项**不可行**。

**Batch B（engine 公面变更，1–1.5 天）：** 单独做 #4。构造函数签名同时触及 embedding/factory、agents/tools、ACP 两条 session 路径——独立 PR 隔离做，并给"直接构造 `PermissionEngine`"的嵌入 host 留迁移说明。

**Batch C（敏感，独立 PR 周期）：** #5a 然后 #5b。**不要**和上面混。

#6、#7 是机会主义——等 wheel 拆分或发版周期顺手做。

---

## 9. 本文有意不覆盖的内容

- **`Agentao` 类本身要不要改名 `Core` 并瘦身。** 765 行的 facade 是真的，但大部分行是构造时的 wiring；缩面是 #1–#5 的下游产物，不是独立任务。
- **多 agent 定位。** codex 的 spawn/wait/close 原语和 agentao 的嵌入式 harness 故事不匹配。前几轮已经决了，本文不复议。
- **Memory MCP 化。** codex 出了 `memories-mcp`(#20622)。对 agentao 来说，只有"跨 host 共享 memory"成为真实诉求时才有意义；当前不是缺口。
- **Goal/budget 工具。** codex 有，agentao 故意没有（host 拥有 budget）。在 borrow review 里决了。

这些不在本文范围，让本文保持是一份**边界审查**，不是路线图。
