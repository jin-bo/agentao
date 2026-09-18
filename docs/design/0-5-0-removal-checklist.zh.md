# 0.5.0 向后兼容面移除清单

**状态：** **评审草稿 —— 未授权。** 尚未删除任何代码。盘点对照 `main@2f51570`，
2026-09-18；下面每一行都是在代码里读到的，不是照抄弃用注释。

**两条次序规则，都是承重的。**

1. **先发 0.4.26。** 它已经带着一条用户可见的修复（#283 —— 配额耗尽的 429 不再重试四次）。
   现在改版本号，等于把「升级拿修复」变成「为了拿修复必须接受破坏性发布」。
2. **`chore: open 0.5.0.dev0` 必须和移除同批落地，不能单独落。** 单独改号会让
   `__version__` 宣称 0.5.0，而 shim 还在、它们自己的 `DeprecationWarning` 还在说
   "will be removed in 0.5.0" —— 仓库自相矛盾，`grep 0.5.0` 也不再是一份能用的工作清单。
   §A–§F 全部跟着号一起走。

**估工之前先看 §C、§D、§E。** 有三行看着像删除，其实都是今天仍有活调用方的迁移：
压缩协调器在调 §E 的两个委派，CLI 在读 §D 的三个私有视图，而 §C 的 `thinking_callback`
静默决定着一段提示词是否发出。

---

## A. `agentao/harness/` —— 弃用别名包

0.4.2 改名为 `agentao.host`。七个文件全删（**158 行**）。每个文件都是星号再导出加若干旧名别名：

| 文件 | 行数 | 它维持的旧名 |
|---|---|---|
| `__init__.py` | 66 | `HarnessEvent`、`export_harness_event_json_schema`、`export_harness_acp_json_schema`；并发出 `DeprecationWarning` |
| `events.py` | 4 | 仅再导出 |
| `models.py` | 13 | `HarnessEvent` |
| `projection.py` | 24 | `HarnessToolEmitter`、`HarnessPermissionEmitter`、`HarnessSubagentEmitter` |
| `protocols.py` | 4 | 仅再导出 |
| `replay_projection.py` | 27 | `HarnessReplaySink`、`harness_event_to_replay_*`、`replay_payload_to_harness_event` |
| `schema.py` | 20 | `export_harness_*` |

`harness/__init__.py:55-57` 把意图写得很直白：这些别名与 shim「ship and die together」。

随后删掉 `agentao/host/__init__.py:30-35` 那段描述别名的文字。

## B. `agentao/session.py` —— 一个 shim **加**一处配套收紧

`agentao/session.py`（**105 行**）不是一句 docstring 注记，而是一个真的弃用模块：
再导出 `agentao.embedding.sessions` 并在导入时告警（`:38-44`）。

除了旧导入路径，它存在还有一个理由：在委派之前替 `project_root` 提供隐式的
`Path.cwd()` 兜底。`embedding/sessions.py` **只为这个迁移窗口**保留 `project_root` 可选，
并在两处写明 —— `:131`（`save_session`）与 `:450`（`load_session`）：
*"Optional during the 0.4.x migration window; will become required in 0.5.0."*

所以这是**一处配套变更，不是两处**：

**按签名识别入口，不要按注记识别。** 只有两个带迁移注记，而 `embedding/sessions.py` 里有
**九个**函数接受 `project_root: Optional[Path] = None`，cwd 兜底只存在于其中一个：

| 行 | 函数 | 有注记？ |
|---|---|---|
| `:52` | `_session_dir` —— **`root = project_root if ... else Path.cwd()`，唯一的兜底** | 无 |
| `:124` | `save_session` | 有 |
| `:177` | `persist_agent_session` | **无** |
| `:341` | `_resolve_session_file`（私有） | 无 |
| `:388` | `load_session_record` | **无** |
| `:440` | `load_session` | 有 |
| `:464` | `list_sessions` | 无 |
| `:521` | `delete_session` | 无 |
| `:554` | `delete_all_sessions` | 无 |

- [ ] 删除 `agentao/session.py`（**105 行**）
- [ ] **删掉 `_session_dir` 函数体里的兜底**，不只是它的参数默认值：`:54` 是
      `root = project_root if project_root is not None else Path.cwd()`。只去掉默认值，
      那个 `else` 分支依然可达，显式传 `project_root=None` 仍然落到 cwd。要让参数必填
      **并且**让 `None` 报错 —— 只收紧公开签名等于把失败推到内部，而不是移除它
- [ ] 把上表七个公开入口的 `project_root` 全部改为必填，不只是那两个写了注记的
- [ ] 逐一核查这七个入口在仓内的调用方，看有没有依赖默认值的
- [ ] 同时测试**省略该参数**与**显式传 `project_root=None`** —— 当前签名接受后者并静默
      当作 cwd

**这是唯一一行会改变「没有使用任何弃用名字」的调用方行为的变更。** 它需要自己的测试和
自己的迁移说明段落，不能跟着删别名那批悄悄过去。

## C. `Agentao.__init__` 的八个回调

在 `agentao/agent.py` 里是**五个分散区域**，不是一处：

| # | 位置 | 内容 |
|---|---|---|
| C1 | `:104-113` | 八个 `*_callback: Optional[Callable[...]] = None` kwarg |
| C2 | `:219-223` | "Deprecated args … scheduled for removal in 0.5.0" 那段 docstring |
| C3 | `:301-308` | 收集它们交给 transport 解析的那个 dict |
| C4 | `:781-810` | `_has_legacy` 检测 + 点名全部八个的 `DeprecationWarning` |
| C5 | `:813-820` | 八个 `self.<name> = callbacks[...]` 属性赋值 |

配套、同批（`runtime/tool_runner.py:74-82` 写的是 "not before"）：

- [ ] `runtime/tool_runner.py:74-82` —— 四个**接受但忽略**的 kwarg
      （`confirmation_callback`、`step_callback`、`output_callback`、
      `tool_complete_callback`），从不存储，留着只是让既有调用方不撞 `TypeError`

**子代理工厂传了其中五个，这会阻断每一次 spawn。**
`agents/tools/_wrapper.py` 构造每个子代理时传了 `transport=transport`（`:997`），
**紧随其后的五行**（`:998-1002`）又传了五个回调 kwarg：
`confirmation_callback=`、`step_callback=`、`output_callback=`、
`tool_complete_callback=`、`ask_user_callback=`。删掉 C1 会让每一次子代理 spawn 抛
`TypeError: unexpected keyword argument` —— 前台后台都一样，而且**与取值无关**：参数一旦
消失，`None` 也是未知 kwarg。这是仓内第一方代码，所以它是前置条件，不是下游迁移：

- [ ] 把前台那几个回调折进工厂本来就在构造的 transport —— 即 `:942` 处现在
      `transport = None` 的位置改用 `build_compat_transport(...)`
- [ ] 后台分支的 `SdkTransport(confirm_tool=lambda *_: False)`（`:946`）原样保留 ——
      那个拒绝确认就是成文的后台姿态
- [ ] 构造时只传 `transport=`，不再传任何回调 kwarg
- [ ] 验收：前台和后台子代理都能 spawn、发出各自的生命周期事件、确认行为不变
      （后台仍然拒绝）

**一个弃用 kwarg 驱动着一段提示词，而且没有别的东西设置它。**
`agent.py:823` 把 `callbacks["thinking_callback"] is not None` 读进
`_has_thinking_handler`，`prompts/builder.py:133` 据此决定是否加入 Reasoning Requirement
一节。删掉 C1–C5 会让这一节对所有宿主静默消失 —— `build_compat_transport()` **保不住它**，
因为它产出的是 transport，从不碰这个标志。删之前必须先定去向：

- [ ] 要么从 transport 推导（当前 transport 是否处理 reasoning 输出？），要么改成一个显式
      构造参数
- [ ] 要么明确宣布这条条件行为取消，该段落改为无条件加入或删除
- [ ] 无论哪种都要有迁移前后的提示词验收 —— 这一节存在与否是可观测的，而它现在依赖一个
      即将消失的 kwarg

**保留 `agentao/embedding/compat.py`。** 它自己的 docstring（`:1`）就称自己是
"public migration surface"，`agent.py:230` 把宿主指向它，`CLAUDE.md:405` 更把它点名为
这次移除**对应的**成文迁移出口。一个无法改接到 `AgentEvent` 的宿主靠它构造 transport。
只需改它的 docstring：`:9` 现在写的是 "Until 0.5.0 they remain accepted on
`Agentao.__init__`"，这句会不再成立。

## D. 四个私有 replay 视图 —— **阻塞：先迁移 CLI**

`agent.py:1072`、`:1076`、`:1080`、`:1084` 以属性视图形式暴露 `_replay_recorder` /
`_replay_adapter` / `_host_replay_sink` / `_replay_config`，注明"scheduled for removal in 0.5.0"。docstring 说
留着是因为"Tests and CLI code still reach for"它们 —— **而这至今仍然成立**：

| 读取方 | 行 | 读的是 |
|---|---|---|
| `cli/replay_commands.py` | `:129` | `cli.agent._replay_config` |
| `cli/replay_commands.py` | `:220` | `cli.agent._replay_config` |
| `cli/replay_commands.py` | `:240` | `getattr(cli.agent, "_replay_recorder", None)` |

- [ ] 把这三处迁到 `agent.replay_manager.config` / `.recorder`（并显式处理无 manager 的
      情况 —— CLI 现在依赖的正是这些属性的兜底）
- [ ] 然后删掉那四个属性和 `:1066-1070` 的段落注释

**不要把这一行扩大。** `agent.py:1047-1058` 的注释明确写着：**公开**的 replay 方法
（`start_replay` / `end_replay` / `reload_replay_config`）是 LIVE API，由
`cli/session.py`、`cli/commands/sessions.py`、`cli/replay_commands.py`、
`acp/session_new.py`、`acp/session_load.py` 实际调用，**不**在移除范围内。只删私有视图。

## E. replay 可观测性委派 —— **压缩引擎在调用其中两个**

转发到 `agentao.replay.observability` 的委派是 **3 个，不是 4 个，也不连续**：
`_latest_session_summary_id`（`agent.py:1143`）、`_emit_context_compressed`（`:1196`）、
`_emit_session_summary_if_new`（`:1219`）。

**`:1137-1141` 那段段落注释是错的，信它会打断运行时。** 它写着这些委派"remain for tests
that patch them on the agent"。其中两个是压缩协调器的活调用：

| 调用方 | 行 | 调用 |
|---|---|---|
| `compaction/coordinator.py` | `:238` | `agent._emit_session_summary_if_new(...)` |
| `compaction/coordinator.py` | `:719` | `agent._emit_context_compressed(...)` |

先删方法会让每一次**成功**压缩在收尾时抛 `AttributeError` —— 而且只有会话长到触发压缩
才会暴露。

- [ ] 先把 `coordinator.py:238` 与 `:719` 改为直接调用
      `agentao.replay.observability`（与 `runtime/chat_loop` 现有做法一致）
- [ ] 再迁移那些在 agent 上 patch 这些方法的测试
- [ ] 然后删掉这三个委派，并改正那段注释
- [ ] 补一个测试：迁移后**成功的完整压缩且产生了新摘要**时仍然发出这两个事件。验收要这样
      限定 —— `_emit_session_summary_if_new` 名字里就带条件，所以微压缩或没有新摘要的那次
      不应被要求发出 `SESSION_SUMMARY_WRITTEN`

## F. 两个陷阱

- **`output_callback` 是两个不同的东西。** §C 里那个弃用构造 kwarg 与
  `Tool.output_callback`（`tools/base.py:29`）无关，后者是活的：由
  `runtime/tool_executor.py:417,462` 按调用重新绑定，并在
  `agents/tools/_wrapper.py:291` 为子代理副本清空。在 `agentao/` 里按名字扫一遍删，
  会打断工具输出的流式路径。`confirmation_callback` / `step_callback` /
  `tool_complete_callback` 同理 —— 它们同时出现在 §C 的区域**和**
  `runtime/tool_runner.py` 活着的签名区域里。
- **`agentao/tool_runner.py`（24 行）是另一个 shim** —— `agentao.runtime.tool_runner` 的
  旧模块路径。它**没有**标记 0.5.0，不在本次范围内。它只在 §H 里有影响。

## G. 测试 —— 至少八个文件，分三类

在 `tests/` 里 grep `*_callback=` 会命中三种完全不同的东西，其中只有一种在范围外。
动手之前先分类。

**(a) 弃用名字 / kwarg —— 需要迁移：**

| 文件 | 位置 | 内容 |
|---|---|---|
| `tests/test_host_typing.py` | `:224-256` | 断言 `agentao.harness` 告警并逐名再导出 `agentao.host` —— 删掉，保留 `agentao.host` 的断言 |
| `tests/test_session.py` | `:23` | 从 `agentao.session` 导入 —— 改指向 `agentao.embedding.sessions`，并传 `project_root`（§B） |
| `tests/test_tool_confirmation.py` | `:50`、`:84` | `Agentao(confirmation_callback=...)`，并回读旧属性 |
| `tests/test_reliability_prompt.py` | `:12`、`:18`、`:83`、`:92`、`:151` | `Agentao(thinking_callback=...)` —— 正是 §C 那段提示词的测试 |
| `tests/test_system_prompt_sections.py` | `:19`、`:25` | `Agentao(thinking_callback=...)` —— 同上 |

**(b) replay 私有视图（§D）—— 与 CLI 一起迁移：**

| 文件 | 位置 |
|---|---|
| `tests/test_replay.py` | `:575`、`:588`、`:618-619`、`:671-672` |
| `tests/test_host_to_replay_projection.py` | `:358`、`:373`（`_host_replay_sink`） |
| `tests/test_agent_subsystems_optional.py` | `:60`（`_replay_config`） |

**(c) 保留、不要动 —— 这些都不是弃用的构造 kwarg：**

| 文件 | 位置 | 为什么留 |
|---|---|---|
| `tests/test_transport.py` | `:173` | `build_compat_transport(step_callback=...)` —— 该面在 0.5.0 之后仍存在（§C） |
| `tests/test_subagent_tool_call_id.py` | `:75` | `AgentToolWrapper(step_callback=...)` —— 是 **wrapper 自己的**构造参数，不是 `Agentao` 的 |
| `tests/test_subagent_tool_call_id.py` | `:89` | `build_compat_transport(...)`，同一个存活的面 |

所有 `tool.output_callback` 的用法都是活的工具回调（§F）。§C 落地后 wrapper 层的旧注释
可以顺手更新措辞，但断言本身成立 —— 那里真正该测的是 §C 的工厂迁移，不是这两行。

新增：

- [ ] `import agentao.harness` 抛 `ModuleNotFoundError`
- [ ] `import agentao.session` 抛 `ModuleNotFoundError`
- [ ] `embedding/sessions.py` 每个入口不传 `project_root` 抛 `TypeError`，显式传
      `project_root=None` 也抛（§B）
- [ ] 构造 kwarg 删除之后，`build_compat_transport()` 仍接受全部八个名字

## H. lint gate 的论证正建立在这个包上

`docs/design/lint-gate.md:20-36` 用 `F821` 在其中静默失效的**8 个星号导入模块**来论证
选上 `F405`，并列出了它们。今天实测：**8 个里有 7 个是 `agentao/harness/*`**；
执行 §A 之后只剩 `agentao/tool_runner.py` 一个。

- [ ] 围绕那个存活模块重写 "Why `F405` is in the list"，或者直接写明这条规则是为下一个
      星号导入 shim 留着的
- [ ] 同步 `lint-gate.md` 与 `lint-gate.zh.md` 里的模块清单（zh 孪生在 `:29-32` 有同一份）
- [ ] 复查 `F401` 豁免的论证 —— 它把 `agentao.harness` 当作典型例子

规则本身应当保留，变的只是它写出来的依据。

## I. 活文档 10 份，另外 22 份是记录

- [ ] `CLAUDE.md` —— `agentao/harness/` 那行子包表、`agentao.harness → agentao.host`
      那条 gotcha、"8 legacy callbacks" 那条 gotcha
- [ ] `developer-guide/{en,zh}/part-2/2-constructor-reference.md`
- [ ] `developer-guide/{en,zh}/part-4/7-host-contract.md`
- [ ] `developer-guide/{en,zh}/appendix/a-api-reference.md`
- [ ] `developer-guide/{en,zh}/part-4/3-sdk-transport.md` —— 写了「transport 与遗留回调
      混用」的注意事项，zh 孪生 `:171` 还写着那八个回调「仍被接受」并经
      `build_compat_transport()` 自动转换
- [ ] `docs/guides/embed-for-agents.md`
- [ ] 新增 `docs/migration/0.4.x-to-0.5.0.md` **及其 `.zh.md` 孪生** —— **2026-09-18
      已定：两份都写。** 先例 `docs/migration/0.3.x-to-0.4.0.md` 是 en-only，但那是缺口
      而不是规矩：CLAUDE.md 要求 `docs/` 下成对，而迁移指南恰恰是读者「因为自己的东西坏了」
      才会去翻的那一份文档。0.3.x 那份先例原样留着 —— 它记录的是一场已经结束的迁移 ——
      所以 `docs/migration/` 之后会是一份成对的指南加一份 en-only 的历史文档。

**不要改**：`docs/releases/v0.4.*.md`、`docs/design/*`（`lint-gate` 除外，见 §H）、
`docs/migration/0.3.x-to-0.4.0.md`，以及 `CHANGELOG.md` 的历史条目 —— 这 22 份记录的是
写下时为真的事。

## J. 发布机制

- [ ] `agentao/__init__.py:10` → `0.5.0.dev0`（单一来源；`pyproject.toml` 是
      `dynamic = ["version"]` + `[tool.hatch.version] path = "agentao/__init__.py"`）
- [ ] `CHANGELOG.md` `[Unreleased]` → `_Targeting 0.5.0._`，并加一个 **Removed** 段
- [ ] `uv run python -m pytest tests/` 与 `uv run ruff check .`（必需 CI 检查）
- [ ] `uv build` 后跑 `uv run python -m pytest -m slow`（干净安装 smoke，需要
      `dist/*.whl`，同样是必需检查）
- [ ] `agentao/embedding/__init__.py:10-14` —— 仍写着旧的 `agentao.session` 导入路径
      「remains as a deprecation shim until 0.5.0」；§B 会删掉那条路径，这句话跟着一起删
- [ ] 最后 `grep -rn "0\.5\.0" agentao/` 除版本号外不应有任何命中

## 实测背景

- **跑道**：`agentao.harness` 自 0.4.2（发布于 **2026-05-01**）弃用 → 24 个发布
  （v0.4.2 … v0.4.25）、约 4.5 个月，每一个都发 `DeprecationWarning`。
- **仓内非测试代码 import 这些别名的**：零处。只有 docstring 提到。
- **外部 dependents**：上次量到 **0**（2026-08-14）。
- **规模**：158 行别名包 + 105 行 `session.py` + 分布在 5 个区域的 8 个 kwarg +
  4 个属性 + **3** 个委派 + `tool_runner` 里 4 个被忽略的 kwarg。
- **工作量不在删除本身 —— 其中三行根本是运行时迁移，不是移除。** §E 必须先挪走
  `compaction/coordinator.py` 的两处活调用（`:238`、`:719`）；§D 必须先挪走
  `cli/replay_commands.py` 的三处活读取；§C 必须决定 `_has_thinking_handler` 从哪来，
  否则一段提示词会静默消失。除此之外：§B（跨九个签名的配套 API 收紧）、§H（失去依据的
  lint-gate 论证）、§G（测试文件分三类）和 §I（十份活文档加一份迁移指南）。
- **三轮评审，每一轮都查出同一种形状的缺陷**：某一行读起来像删除，其实有活调用方。
  §E 的段落注释声称那些委派只为测试而存在，而 `compaction/coordinator.py` 在调其中两个；
  §C 的 `thinking_callback` 静默把着一段提示词；§C 的子代理工厂传了其中五个 kwarg，
  一删就每次 spawn 抛 `TypeError`。§B 的迁移注记只出现在九个 `project_root` 签名里的两个上。
- **而且能找出它们的检索方式每轮都在变宽。** 读弃用注释什么也找不到 —— 错的正是那些注释。
  grep **符号的调用方**查出了 §E 和 §B。子代理工厂需要换一个问法：**谁在构造 `Agentao`**
  —— 那些 kwarg 在 spawn 路径附近根本没有按名字被引用过，只是被一路传下去。移除一个构造面时，
  要 grep 构造器的调用点，不只是它的参数名。

英文孪生：`docs/design/0-5-0-removal-checklist.md`。
