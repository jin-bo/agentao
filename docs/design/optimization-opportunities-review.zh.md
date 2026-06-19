# Agentao — 优化机会评审

**状态：** 评审记录。2026-06-19 起草，基于对 `agentao` 运行时、打包、CLI/ACP
表面的多维度审计。**这是一份带实证的发现清单 + 优先级改进提案，不是已批准的计划。**
它记录代码库在哪些地方存在局部性能成本、打包死重量、或易漂移的重复代码——并
*交由维护者判断*：哪些现在修、哪些收敛、哪些保持不动。
**读者：** Agentao 维护者。
**对照件：** `optimization-opportunities-review.md`。
**相关：**
- `core-boundary-review.md` — core/host 包边界审计；本文多条发现遵守其记录的边界
  （例如不要把展示逻辑拉进 core）。
- `path-a-roadmap.md` — embed-first 策略；打包类发现（精简核心安装、惰性导入）服务于
  「library-only 安装要轻」的承诺。
- `acp-server-conformance-review.md` — ACP handler 收敛发现
  （`require_active_session`）是它已锚定的清理工作的延伸。

**方法：** 四路并行的取证（复杂度 / 每轮性能 / 重复 / 打包），每一路都被要求引用
`file:line`，并用 `grep`/通读而非直觉来支撑每条结论——符合本仓库
*先实证后建议、gap ≠ need* 的纪律。影响最大且最不可逆的几条（死 extras、`tiktoken`
归属、死构造参数、ACP helper 采用情况）在落档前做了独立复核。代码引用锚定
`main`@`08c23db`（2026-06-19）。行号会漂移——动手前请重新 grep。

---

## TL;DR

Agentao 是一个**成熟、维护良好的代码库，没有成堆触手可及的技术债**。262 个源文件
/ ~54.5k 行，测试比近 1:1（195 个测试文件 / ~54.8k 行），仅 4 个 TODO 标记（其中
3 个是误报），惰性导入边界干净（`import agentao` 实测**不加载任何**重模块）。
**不存在架构性紧急问题。**

这里的「优化」指三类具体、有界的工作：

1. **第一档 — 快赢**（低风险）：删除死的打包 extras、删除死的构造参数、修一处会误导
   未来清理者删掉在用 API 的注释。（曾列在此处号称"每轮性能收益"的两处 compaction token
   改动，已在**反向评审中降级**——Tier-1 token anchor 已让稳态轮次很便宜，它们只是冷路径
   的健壮性微调。见 T1.2 / T1.3。）
2. **第二档 — 两个潜在 bug + 顺手收敛**（bug 现在修，其余只在顺路时做）：T2.3
   （settings.json 的 cwd bug）和 T2.5（ACP per-session-cwd 绕过）是单点修复。其余重复
   （T2.1 / T2.2 / T2.4 / T2.6）是针对*已存在* helper 的收敛——值得在碰到这些文件时
   **顺手**收，但**不**作为独立的平台化任务排期。
3. **第三档 — 较大重构**（价值真实但成本高，按需）：一个真正臃肿的方法
   （`_execute_one`，249 行）、一个住在 core 里的展示方法、一个深层嵌套的恢复方法。

建议起点：**先做低风险清理**（T1.1 `google`、T1.4 死参数、T1.5 注释），再做**第二档
bug 修复**作为单点改动（T2.3 settings.json 读+写 cwd、T2.5 ACP cwd 绕过）。T1.2 / T1.3
是**可选的冷路径微调**（反向评审已拿掉"运行时性能"PR——见下方说明）；第三档保持记录，
不进入近期路线。

> **优先级排序由维护者拍板。** 本文提供 grep 验证过的证据和*建议*顺序，
> 不单方面断定哪些值得、哪些不值得维护者的时间。

> **反向评审（2026-06-19）。** 初稿后，对每条可执行结论都做了对照源码的证伪。净变化：
> **T1.2 与 T1.3 从 HIGH/MEDIUM 降为 LOW。** Tier-1 token anchor
> （`_threshold_token_estimate` + `_runner.py:268` 的 `record_api_usage(.., message_count)`）
> 已让稳态轮次只数新增的几条 message，所以逐字符慢循环是冷路径成本（会话冷启动 +
> compaction 之后），**不是**每轮成本——本次评审**没有任何可量化的每轮性能收益**。T2.5
> （ACP cwd 绕过）经核实是真 bug。其余发现维持。

---

## 基线指标

| 指标 | 数值 | 解读 |
|---|---|---|
| 源文件 / 行数 | 262 / ~54.5k | — |
| 测试文件 / 行数 | 195 / ~54.8k | 测试比近 1:1（健康） |
| `TODO`/`FIXME`/`XXX`/`HACK` | 4（3 个误报：`XXXX` provider 占位符） | 实质零债务标记 |
| 弃用标记 | 31 | 活的、有文档的向后兼容 shim（非腐烂） |
| 90 天提交数 | 316 | 活跃开发 |
| `import agentao` 重模块 | 0 | openai/mcp/httpx/jinja2/rich/bs4/tiktoken 全 `loaded=False` |

结论：通常表征疏于维护的廉价信号都不存在。下列发现是残余的，不是系统性的。

---

## 第一档 — 快赢（低风险）

### T1.1 — 5 个打包 extras 是死重量（`google` 明确可删）· HIGH
`pyproject.toml:48-52`。5 个 extras 在 `agentao/` 已发布代码中**零导入**（按实际导入名
grep 验证——`google.genai`、`Crypto`、`fitz`/`pdfplumber`、`pandas`/`openpyxl`、`PIL`）：

```
pdf    = ["pymupdf>=1.27.1", "pdfplumber>=0.11.9"]   # 0 导入
excel  = ["pandas>=2.0", "openpyxl>=3.1.5"]          # 0 导入
image  = ["Pillow>=10.0.0"]                          # 0 导入
crypto = ["pycryptodome>=3.23.0"]                    # 0 导入
google = ["google-genai>=1.0.0"]                     # 0 导入
```

- `google-genai` **明确可删**：Gemini 路径完全走 OpenAI-compat——`llm/client.py:493-495`
  按 `"googleapis.com" in base_url` 和 `model.startswith("gemini")` 分支，从不导入该
  SDK。这个 extra 白白拖进 grpc/protobuf。从 extras 和 `[full]` 中删除。
- `pdf` / `excel` / `image` / `crypto` 是**判断题**（harness-vs-product 边界）：它们可能是
  *故意*提供给*用户 skill*（会 shell 出去 / 跑 Python）用的便利依赖。但当前它们
  **没有任何 in-tree 消费者、也没有声明用途**。决策：要么删除，要么在开发者指南里写明
  「供 skill 便利使用」。目前它们是无说明的死重量，还撑大了 `[full]` 闭包和
  `tests/test_dependency_split.py` 的 122 包基线。

**修法：** 删 `google`；其余四个由维护者决定删或文档化；之后刷新
`tests/data/full_extras_baseline.txt`（`uv build && uv run pytest
tests/test_dependency_split.py -m slow`）。

### T1.2 — token 估算兜底是纯 Python 逐字符循环——但 Tier-1 anchor 让它不在稳态热路径上 · LOW（从 HIGH 降级）
`context_manager.py:40-54`（`_heuristic_token_count` 用 `for ch in text` 逐字符迭代，
只对*单个*超过 `_FAST_PATH_CHARS = 100_000` 的字符串走快路径）。因为 `estimate_tokens`
是逐条 `sum(_count_message_tokens(m) ...)`（`context_manager.py:181-188`），长 **message
列表**里的较短字符串确实会落到逐字符循环——机制成立。

> **反向评审更正（2026-06-19）。** 初稿评为 HIGH（"每轮、默认装机"）。这是错的。全历史
> 估算**不在**稳态路径上：`_threshold_token_estimate`（`context_manager.py:124-152`）复用
> 上次 API 响应的真实 `prompt_tokens`（Tier-1 anchor），本地只数**自那以后新增的
> message**（`messages[n:]`）。anchor 在*每次* LLM 调用后都会重新变暖——`_runner.py:268`
> 传 `record_api_usage(prompt_tokens, len(messages_with_system))` 带上 message 数，且跨轮
> 持续。所以每轮的阈值检查只数新增的几条，**不是**整段历史。全 O(字符) 估算只在**冷路径**
> 跑：会话第一次检查，以及 compaction 让 anchor 失效（`invalidate_token_anchor`）之后。
> （"已验证非发现"一节本就写明 anchor 让稳态便宜——原 HIGH 评级与之自相矛盾。）

- **影响：** ~13.5ms 的全历史估算（子 agent 实测；400 条 / ~800KB、无 tiktoken）是冷路径
  成本，约每会话一次 + 每次 compaction 一次——**不是每轮**。稳态暖路径约 0.14ms。
- **修法（可选、小健壮性）：** 把 `_FAST_PATH_CHARS` 快路径扩展到按累计 message/列表长度
  触发，让*冷路径*和 compaction 后的估算以 O(消息) 近似。便宜安全，但价值低——anchor 已让
  常见路径很快。**不**构成把 `tiktoken` 升核心的理由（那只增大默认安装、加模型映射维护，
  且对稳态没收益）。

### T1.3 — 两个 compaction 阈值每轮重复算两遍同一个估算 · LOW（从 MEDIUM 降级，仅冷路径）
`needs_microcompaction`（`context_manager.py:231-237`）和 `needs_compression`
（`:227-229`）各自调 `_threshold_token_estimate(messages)`，两者每轮迭代都跑
（`_maybe_microcompact` / `_maybe_full_compress`，`_compaction.py:29,60`）。两次调用冗余
——输入相同、结果相同、互斥区间（55–65% vs >65%）。

> **反向评审更正（2026-06-19）。** 从 MEDIUM 降级。在 anchor 暖路径（稳态——见 T1.2）每次
> 调用只数 `messages[n:]`，所以这点重复合计约 0.14ms，无关紧要。翻倍只在罕见的冷路径上
> 可见，把一次全估算变成两次。

**修法（最小、琐碎）：** 每轮迭代只算一次 `_threshold_token_estimate(messages)`，把 int
喂进两个判定——`run()` 里的局部变量，或给两个判定加可选 `tokens=` 参数。**不要**引入决策
对象 / 状态机：这只是一次冗余调用，不是缺了抽象。

### T1.4 — `ToolRunner.__init__` 上 4 个死的弃用参数 · MEDIUM（纯噪音）
`tool_runner.py:60-64`。`confirmation_callback`、`step_callback`、`output_callback`、
`tool_complete_callback` **声明了但从不存储、从不引用**（body 65-90 行一个都没存），
且**无调用方传入**——唯一生产构造点（`agent.py:587`）和全部 5 个测试构造点都不传。
区别于 `Agentao.__init__` 那些会发 `DeprecationWarning` 并喂给 `build_compat_transport`
的活 shim，这 4 个是无警告、无存储、无使用的惰性签名噪音。

- 它们位于 keyword-only `*` 之后，只能按关键字传，grep 显示无人传。可直接删除。

**修法：** 删掉这 4 个参数，以及若因此不再使用的类型导入。

### T1.5 — 在用的 replay API 上挂着误导性的「remove in 0.5.0」注释 · LOW（防雷）
`agent.py:963-1009` 标题写「back-compat shims (remove in 0.5.0)」，但
`start_replay` / `end_replay` / `reload_replay_config` 是 **CLI+ACP 在用的活 API**：
`cli/session.py:29-30,46`、`cli/commands/sessions.py:144-146`、`cli/replay_commands.py:194`、
ACP `session_new.py:415` / `session_load.py:342`。只有 `_replay_recorder` /
`_replay_adapter` / `_host_replay_sink` 这三个 *property view* 才是真正可删的面向测试 shim。
笼统的「remove in 0.5.0」会误导未来清理者删掉在用接口、搞坏 CLI/ACP。

**修法：** 重组注释，把三个 `*_replay()` 委托方法标为受支持表面，只让三个私有 property
view 带删除注记。（注释/结构修复，**不要**删方法。）

---

## 第二档 — 两个潜在 bug + 顺手收敛

**现在修（单点 bug 修复——修 bug，不修抽象）：** T2.3 和 T2.5 各自纠正一个真实的 cwd /
工作目录缺陷。每个 PR 只圈定修复本身。

**仅顺手（T2.1 / T2.2 / T2.4 / T2.6）：** 这些是针对*已存在* helper（或一行）的收敛，
属于可维护性的锦上添花，不是性能/正确性问题——*在你下次本就要编辑那个文件时*再顺手收。
**不要**为了消除重复而制造新的公共/共享 API；那正是把优化清单做成重构路线的起点。

### T2.1 — 3 个 ACP handler 仍内联 `require_active_session()` 已集中的 session 门禁 · MEDIUM（顺手）
`_handler_utils.py:52-81` 是为此专建的 helper；其 docstring（`:10-12`）自己点名了落后者。
5 个较新 handler 已采用（`session_set_mode`、`session_set_model`、`session_list_models`、
`session_set_config_option`、`agentao_set_model`——已确认）。仍在手写同一个 4 步门禁的：
`session_cancel.py:112-129`、`session_prompt.py:235-253`、`session_load.py:157-166`。
逐字复制的 `_parse_session_id`（cancel `:82-84`、prompt `:100-101`、load `:109-111`）
就是该 helper 里的 sessionId 子句。

- `session_cancel` / `session_prompt` 可安全直接替换到**已有** helper——下次编辑 ACP
  handler 时顺手做。`session_load` 用的是 get-not-require（它要创建），需要一个*新*的更薄
  helper 变体；除非那个文件本就在改，否则别动（别为去重而加 API）。

### T2.2 — `LLM_PROVIDER` + `{PREFIX}_API_KEY/_BASE_URL/_MODEL` 方案在 4 个模块重复实现，绕过 `discover_llm_kwargs()` · MEDIUM（顺手）
`factory.py:81-88` 是规范读取器；其 docstring（`:77-79`）明确要求 peer/测试调用它而非重写
prefix 方案。内联重实现于：`cli/diagnostics/collectors.py:47-50`、
`acp/session_set_config_option.py:86,93-100`（自己的 docstring 都承认
*"Mirrors factory.discover_llm_kwargs"*）、`cli/app.py:57`、
`cli/commands/provider.py:25,51,57,64`。默认 `"OPENAI"` 字面量和 `.strip().upper()`
大小写处理现在散在 5 处。

- **修法（顺手）：** 如果/当这些模块被碰到时，从 factory 导出 `resolve_provider_env()`，
  让三个取值站点走它。一个小的新导出，单看价值不高——不作为排期任务。
  （`provider._list_providers_from_env` *扫描全部* `*_API_KEY` 键，形状确实不同——保留。）

### T2.3 — `cli/app.py` 用 cwd 相对而非解析后的 project root *读和写* `.agentao/settings.json` · MEDIUM（bug — 现在修）+ 可选后续
可执行项是一个 **横跨 settings 往返两端的 bug**：`cli/app.py:140`（`_load_settings`）
**和** `cli/app.py:149`（`_save_settings`）都用 **cwd 相对** 的
`Path(".agentao") / "settings.json"` 而非解析后的 project root。`factory.py:37-45` 和
`replay/config.py:107-115` 则从解析后的根读取*同一文件*。于是从子目录启动的 CLI 会把
`mode` 持久化到 cwd 下的 `.agentao/settings.json`（甚至会 `mkdir` 一个），而 factory 永远
读不到——**读和写都**与 factory 冻结的 `working_directory` 契约不一致。

- **修法（单点）：** 让 `_load_settings()` **和** `_save_settings()` **都**用解析后的
  project root——最好是现有的 `replay/config.py::settings_path()`。只修读会留下写路径不
  一致、bug 没闭环。PR 只圈定这两个方法；**不要**捆绑重构。
- **可选后续——独立、降范围、不要并入 bug PR：** 读 → `json.loads` → `isinstance dict`
  → 吞异常的惯用法复制于 `mcp/config.py:61-68`、`embedding/plugins/manager.py:422-430`、
  `agents/store.py:23-32`、`skills/registry.py:54-61`、`embedding/plugins/manifest.py:77-83`、
  `skills/manager.py:143-149`。一个共享 `read_json_object(path)` *可以*收敛这些，但那是
  横向收敛，只在这些文件本就被碰时才值得做——此处仅记录，**不排期**。

### T2.4 — CLI 子命令派发前导块 + 「Unknown subcommand」页脚复制 5+ 次 · LOW/MEDIUM（仅顺手）
这是**显示一致性 / 轻微漂移**，不是性能或正确性——评为 LOW/MEDIUM，且明确**不**作为独立
任务。`args.strip()` → `split(None, 1)` → `sub`/`rest` 前导块（`commands/mcp.py:17-20`、
`commands/sessions.py:31-34`、`commands/permission.py:38-40`、`commands_ext/acp.py:35-38`、
`commands_ext/agents.py:121-123`）已分叉（`permission.py:39` 多了 `.lower()`；`mcp.py:20`
少了余项 `.strip()`）；`Unknown subcommand:` 页脚复制于 `commands/mcp.py:94`、
`commands/sessions.py:86`、`commands_ext/acp.py:73`、`commands_ext/memory.py:196`
（`permission.py:131` 已分叉）。

- **修法（顺手）：** *下次碰这些命令 handler 时*，把一个小的 `split_subcommand()` /
  `unknown_subcommand()` helper 抽进 `_globals.py`。不要仅为消除重复就制造公共 CLI helper
  API——这里的漂移是表面的。

### T2.5 — `CodebaseInvestigatorTool` 绕过基类路径解析器——潜在 ACP cwd bug · MEDIUM（bug — 现在修）
`resolve → exists() → "Directory ... does not exist"` 守卫复制于 `search.py:158-161`、
`search.py:352-355`、`file_ops.py:445-448`、`agents.py:88-90`。前三处用基类解析器
（`_resolve_path`/`_resolve_directory`，`base.py:61/82`）；`agents.py:89` 用裸
`Path(directory).expanduser()`，因此**忽略了 `base.py:78-80` 存在的目的——session
`working_directory` 绑定**（ACP per-session-cwd 守卫）。

- **修法（单点，修 bug）：** 让 `agents.py` 走**已有**的基类解析器（`_resolve_directory`，
  `base.py:82`）而非裸 `Path(...).expanduser()`。仅此就堵住 cwd 绕过*并*去掉重复守卫——
  无需新 helper。（一个共享的 `_resolve_existing_directory()` 还能跨四处去重消息，但那是
  顺手的部分，不是 bug 修复。）

### T2.6 — `tools/search.py` 的 Python 兜底重实现了它自己的 `_format_grep_output` · LOW（顺手、琐碎）
`search.py:87-101` 的 helper 被两条快路径使用；慢速 Python 兜底在 `search.py:425-433`
重新手写了完全相同的「No matches / Found N match(es) / cap-100 + '… and X more'」逻辑
（同文件、字节级相同契约）。

- **修法（顺手、琐碎）：** 让兜底走**同文件里已有的** `_format_grep_output(...)` helper——
  无新 API、单文件。下次打开 `search.py` 时顺手做。

---

## 第三档 — 较大重构（价值真实但成本高；按需）

### T3.1 — `ToolExecutor._execute_one` 是个 249 行、混了 6 个关注点的方法 · HIGH 维护成本
`tool_executor.py:177-426`。一个方法处理 TOOL_START emit、DENY 分支、CANCELLED 分支、
token 传播、host `started` emit、执行前取消检查、sandbox profile 注入、`output_callback`
接线的 execute/try/except/finally、async-cancel 短路、host terminal emit（4 路 redaction）、
post-tool hook 派发——有 5 个 `return call_id, ToolExecutionResult(...)` 出口加 async 短路。
短路守卫（DENY/CANCELLED/pre-cancel）可干净抽成 `_short_circuit_result(...)`；sandbox 解析
（`:290-304`）和 host-terminal-emit 块（`:377-409`）自成一体。拆分后核心路径降到约 120 行。

- **约束：** TOOL_START/TOOL_COMPLETE 配对、「host `started` 仅在 ALLOW 时触发」的顺序、
  以及每个 `call_id` 单次 emit 保证，对 ACP/replay/CLI 都是载荷。任何抽取必须保序。

### T3.2 — `Agentao.get_conversation_summary` 是住在 core 里的 64 行展示方法 · LOW/MEDIUM
`agent.py:1188-1250`。纯字符串拼装的 CLI/状态显示，伸手进 `context_manager`、
`memory_manager`、`skill_manager`、`todo_tool`、`mcp_manager`、`llm` 只为拼文本。
这是展示关注点，更适合 `runtime/summarize(agent)`（对齐已确立的 `run_turn(agent, …)` /
`run_llm_call(agent, …)` facade 模式）或 CLI 层。

- **约束：** 公开方法——保留薄 `agent.get_conversation_summary()` facade 委托给抽出的函数
  （与 `_build_system_prompt` 同模式）。
- 遵守 `core-boundary-review.md`：展示逻辑不应住在 core。

### T3.3 — `_call_llm_with_overflow_recovery` — 110 行、三层嵌套 try/except、7 个近似 return · MEDIUM
`_runner.py:697-806`。image-fallback → 上下文溢出 `full` 压缩 → `minimal_history` 是线性
升级，却写成右漂嵌套，含 3 个字面相同的成功构造和 3 个近似的错误构造。写成一小串
`_attempt(...)` 步骤更自然。

- **约束：** 升级顺序、`is_context_too_long_error` 门控、以及 `_emit_context_compressed` /
  `_emit_session_summary_if_new` 副作用顺序都是行为。机械重构，但须保现有测试绿。

---

## 已验证的非发现（已查清——不要「修」）

记录在此，免得未来某次扫描把有意设计重新当问题标记（本仓库文化是 *gap ≠ need*）：

- **`import agentao` 干净。** openai SDK、mcp、jieba、tiktoken、bs4、jinja2 全惰性 / 延迟；
  `from agentao import Agentao` 不加载任何重模块。
- **AGENTAO.md 不会每轮重读** — 构造时加载一次（`agent.py:504`）；builder 用缓存的
  `agent.project_instructions`。
- **内存召回不是 O(n²)** — 倒排索引 + `write_version` 脏标门控（`retriever.py:300-319`）；
  token bundle 已缓存。
- **context manager 不会每轮重 tokenize 整个历史** — Tier-1 API 锚点复用真实
  `prompt_tokens`（`context_manager.py:124-152`）。
- **`requires-python = ">=3.10"` 被遵守** — 无 3.11+ 特性（`tomllib`、`ExceptionGroup`、
  `Self`、`StrEnum`、`TaskGroup`、`datetime.UTC` 全无）。
- **无「导入但未声明」依赖。** 每个导入都对应已声明依赖（`pygments` 0 导入但是
  `cli/__init__.py:81` 里有意的存在性探针）。
- **`Agentao.__init__`（232 行）** 已分解为约 9 个 `_init_*`/`_resolve_*`/`_validate_*`
  helper，顺序有文档——再拆只增间接、不减真复杂度。
- **`chat_loop/` mixin、`harness→host` 别名、`Agentao` 遗留回调** 都是有意、有文档的 shim，
  非债务。
- **`write_session_update` 信封集中化成立** — 三个 session/update emit 站点全走它
  （commit `c6d6406` 完好）。
- **残留的 `subprocess.run` 直调**（`cli/input_loop.py` 剪贴板、`sandbox/policy.py`
  preflight）是无捕获管道孙进程风险的短小命令——正确地不在 `run_captured` 范围内。
- **`tiktoken` 只有一个考量点**：见 T1.2——那是归属问题，不是导入 bug。

---

## 建议执行顺序

反向评审拿掉了初稿的"运行时热路径性能 PR"（T1.2/T1.3 只在冷路径——见反向评审说明）。剩下：

1. **PR 1 — 低风险清理（明确的赢面）。** T1.1 删 `google` extra（外加 pdf/excel/image/crypto
   的删或文档化决定）、T1.4 死 `ToolRunner` 参数、T1.5 replay 注释。无运行时行为变化；为
   extras 刷新 `full_extras_baseline.txt`。
2. **第二档 — 单点 bug 修复。** T2.3（settings.json 读+写 cwd bug，无 helper）和 T2.5
   （ACP cwd 绕过，走已有解析器）。两条都关闭真实缺陷。收敛类（T2.1 / T2.2 / T2.4 / T2.6）
   是**顺手**项——只在那些文件因别的原因被碰时再收。不要为消除重复而平台化。
3. **可选、低价值 — 冷路径 token 微调。** T1.2 / T1.3 只帮冷 / compaction 后路径；要做就
   作为一个极小的独立改动，绝不当"性能"PR，且 `tiktoken` 打包保持原样。
4. **第三档 — 记录，不进入近期路线。** T3.1（`_execute_one`）有唯一真实的持续维护成本但
   风险也最高（ACP emit 保序契约）；若要做，务必在其现有测试护航下进行。T3.2 / T3.3
   锦上添花。

pdf/excel/image/crypto 的「删除还是文档化」是属于维护者的 harness-vs-product 边界判断。

---

## 附录 — 每条发现的验证方式

| 发现 | 验证 |
|---|---|
| T1.1 | `grep -rE "from google\|import Crypto\|import fitz\|pdfplumber\|import pandas\|import openpyxl\|from PIL" agentao/` → 0 命中；通读 `llm/client.py:493-495` Gemini-over-OpenAI 路径 |
| T1.2 / T1.3 | 通读 `context_manager.py`（`_heuristic_token_count`、`_threshold_token_estimate`、anchor）+ `_compaction.py`；**反向评审：** 确认 `_runner.py:268` 用 `message_count` 给 anchor 变暖，故慢循环仅冷路径 → 两条降为 LOW |
| T1.4 | 通读 `tool_runner.py:60-90`；`grep "ToolRunner("` 跨 `agentao/` + `tests/`——无调用方传那 4 个参数 |
| T1.5 | `grep "start_replay\|end_replay\|reload_replay_config"` → 在用的 CLI + ACP 调用方 |
| T2.1 | `grep "require_active_session" agentao/acp/` — 5 采用、3 落后 |
| T2.2 | `grep "LLM_PROVIDER\|discover_llm_kwargs"`；通读每个重实现站点 |
| T2.3 | 通读 `app.py` 的 `_load_settings` **和** `_save_settings`——读写均 cwd 相对；与 `factory.py` / `replay/config.py` 的解析根读取对比 |
| T2.4–T2.6 | 通读每对引用片段；确认漂移 / 单文件重复 |
| 非发现 | `-X importtime` 验证惰性导入；通读锚点/倒排索引代码；`grep` 3.11+ 特性 |

---

## 实现状态（2026-06-19）

Tier-1 与 Tier-2 全部一次性落地（Tier-3 仅记录、未做）。默认测试套件全绿。实现期间有两条按源码细读做了修正——记于此，上面的提案需结合这些修正阅读：

- **T1.1** — 五个 extras（`pdf` / `excel` / `image` / `crypto` / `google`）从
  `[project.optional-dependencies]` 与 `[full]` 删除。`full_extras_baseline.txt`
  以新 `[full]` wheel 安装后重新 freeze 生成（122 → 106；移除的 16 个是被删
  extras 的独有传递闭包——注意 `Pillow` **保留**，因 `crawl4ai` 传递依赖它，恰好
  印证 `image` extra 冗余）。`uv.lock` 同步更新。
- **T1.2 — 实现与提案不同（且更优）。** 没有采用建议的累计 `len/4` 捷径（那会
  **低估** CJK 并延迟压缩），而是把 `_heuristic_token_count` **向量化**：用
  `len(text.encode("ascii", "ignore"))`（C 级）替代 Python 逐字符循环来做 ASCII/CJK
  拆分。公式仍是 `ASCII×0.25 + CJK×1.3`，钉死的测试值不变、无精度回退——只是去掉了
  O(chars) 循环。
- **T1.3 / T1.5 / T2.2 / T2.3 / T2.5 / T2.6** — 按描述落地。T1.3 通过可选
  `tokens=` 参数让两个压缩谓词每轮共享一次 `_threshold_token_estimate`；可证 fire/no-op
  决策完全一致（区间互斥，且 microcompaction 只会降 token）。
- **T1.4 — 已回退。** Codex review 指出删除 `ToolRunner` 的 4 个弃用回调 kwargs 会提前
  结束其向后兼容窗口：外部 host/测试若仍直接用这些参数构造 `ToolRunner` 会触发
  `TypeError`。已恢复为“接受但忽略”的 no-op，并按与 `Agentao.__init__` 同类遗留回调
  相同的兼容政策排期 0.5.0 移除，而非现在。净效果：`tool_runner.py` 无改动。
- **T2.1 — “safe drop-in”仅对 `session_cancel` 成立。** 细读发现 `session_prompt`
  对 `require_active_session` **并非**行为保持（它在 session 查找**之前**解析 prompt，
  且把 agent 缺失的 session 映射为 `INTERNAL_ERROR` 而非 `INVALID_REQUEST`），
  `session_load` 用 get-not-require。故在 `_handler_utils.py` 新增薄 `resolve_session()`
  （envelope + 查找，不做 liveness 检查）；`require_active_session()` 复用它，**仅**
  `session_cancel` 路由过去（错误码/消息已核字节一致）。另两个有意保留以维持线协议。
- **T2.4** — `split_subcommand()` / `unknown_subcommand()` 进 `_globals.py`；5 处
  dispatch preamble 与 4 处 byte-identical footer 路由过去。关键字参数
  （`default` / `lower` / `strip_rest`）保留各 handler 既有漂移；`/sandbox` 带前缀的
  footer 保留自己的消息。

### Code-review 跟进（2026-06-19）

工作流驱动的对抗式 code review 发现（并已修复）首轮整合引入的三处行为回归：

- **T2.6 修正。** 把纯 Python 搜索 fallback 路由经 `_format_grep_output` **并非**
  无害：其 `.splitlines()` 会按行内嵌的 Unicode 行分隔符（U+2028/U+2029/VT/FF/NEL）
  重切匹配行，其 skip 过滤又对 `:` 截断后的路径再过滤（误删合法的
  `build:notes.txt` 匹配）。修复：抽出 `_format_match_lines(lines, pattern)`（对**列表**
  执行"No matches / Found N / cap-100"契约）；fallback 直接调它（不重切、不重过滤），
  `_format_grep_output` 为 git-grep / rg 快路径复用它。
- **T2.2 收窄。** `resolve_provider_name()`（会 upper）保留给 `factory` / `collectors`
  / `app`（它们本就是 `.strip().upper()`，完全等价），但 `acp/session_set_config_option.py`
  **回退**为直接读 `LLM_PROVIDER`：其 accept/reject 比较需要原始 casefold，经
  `.upper().lower()` 往返会错误拒绝 case 非幂等的非 ASCII provider 名（`ß` / `ı` / 连字）。
- **T1.2 改精确。** 向量化计数改用整数算术（`(ascii×25 + cjk×130) // 100`），替代
  `int(ascii×0.25 + cjk×1.3)`——后者浮点累加漂移会在非 ASCII 文本上 +1。

review 同时标出但**保留**的预期正确改动：T2.5 的 `agents.py` 目录解析切换（即 bug 修复
本身），以及 T1.3 的每轮共享估算（已证决策一致；不变量在调用处注释说明）。
