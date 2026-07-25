# Agentao — 重构审计与反向评审（2026-07）

**状态:** 评审记录。2026-07-24/25 对全树（58.8k 行 / 268 个模块）做重构审计，随后
对**审计自身的结论**做了一轮对抗性反向评审。三项落地；五项在反向评审推翻或降级其
前提**之后**被否决。**被否决的那半才是本文的实质内容**——它存在的目的是：没有新证据
时不要重提这些提案。
**读者:** Agentao 维护者。
**配套:** `refactor-audit-2026-07.md`（权威英文版）。
**相关:**
- `optimization-opportunities-review.md` —— 2026-06-19 那轮审计。其 Tier 1–2 已随
  v0.4.12 落地；其 **Tier 3 在本文正式否决**，附六月那轮没有收集的 churn 证据。
- `core-boundary-review.md` —— T3.2「把 `get_conversation_summary` 移出 core」所
  依赖的渲染/展示层边界。
- `embedded-host-contract.md` —— v1.2 replay 事件种类存在的理由。

---

## TL;DR

七个候选项。**两项按原样落地，一项收窄后落地，四项否决。** 反向评审改变的结论比原
审计答对的还多——这正是做反向评审的意义。

| # | 项 | 裁决 |
|---|---|---|
| 1 | `/copy` 无界 `subprocess.run` | **已落地**（#139） |
| 2 | `run_loop` if/elif 链 → 调度表 | **已落地**（#140） |
| 3 | replay v1.2 审计事件未渲染 | **落地，范围已修正**（8 个 → 3 个） |
| 4 | 4 条 ruff F821 | **落地，价值已修正**——仅静态卫生 |
| 5 | 六月 Tier 3（三个长函数） | **否决**——无 churn 证据 |
| 6 | mypy 逐包棘轮 | **否决**——89 条里 27 条是 mixin 假阳性 |
| 7 | hardline 正则惰性编译 | **否决**——130ms 启动里的 17ms |

---

## 已落地

### 1 — `/copy` 可能永久挂死 CLI（#139）

`_copy_last_response` 三次调用 `subprocess.run` 且**均无 `timeout=`**。pbcopy 卡住
（pasteboard 服务无响应）会阻塞输入循环，除 SIGINT 外无出路；且裸 `subprocess.run`
违反 `CLAUDE.md` 的 `run_captured` 规则（要杀整棵进程树，而非仅直接子进程）。

改为扁平候选循环：每次尝试经 `run_captured` 限时 5s；超时**或**非零退出现在都会
继续尝试下一个工具，而不是中断整条链（此前 `pbcopy` 失败会直接报 "Copy failed"，
**从不**尝试 `xclip`/`xsel`）。

### 2 — `run_loop` 调度表（#140）

353 行 / **圈复杂度 74**——接近全树次高者的两倍——且无任何测试驱动。31 个分支中
24 个本就是纯委派。结果：**127 行 / cx 26**，调度本身 13 行。

抽出 `commands/skills.py`、`commands/reset.py`（`/clear` 与 `/new` 现共用
`_reset_session(clear_memories=…)`）、`/mode` → `commands/permission.py`。
`/exit` / `/quit` 保留内联——表无法表达的循环控制流——并有测试**钉住它们不在表里**。

**调度表暴露出的漂移:** `/sandbox` 可派发但不在 Tab 补全列表中，尽管 `CLAUDE.md`
把它列为关键命令，用户在提示符下发现不了它。把命令词汇变成一个**值**，才使得测试
能拿它跟 `_utils._SLASH_COMMANDS` 和 `help_text` 对账。

### 3 — v1.2 审计事件已落盘但未渲染

`tool_lifecycle` / `subagent_lifecycle` / `permission_decision` 的存在意义是让嵌入
宿主拥有**一份**审计产物而非两条并行流（`EventKind` 文档字符串，v1.2）。JSONL 侧
是对的，两个 CLI 视图都不对：

- `--raw` → 降级为按字典序的 payload 键名预览。
- **默认 grouped 视图 → 完全丢弃。** `_print_turn` 的事件循环是**允许列表**，未列名
  的 kind 被静默跳过。一个包含"权限拒绝"和"工具失败"的 turn 渲染出来只有
  `user / asst / ok`。

两者现均已渲染，并新增 `tests/test_replay_render_coverage.py`：基于探针的穷尽性
守卫，新增 `EventKind` 而未加摘要时会变红。

> **范围修正。** 审计最初声称有 *8* 个未覆盖的 kind。其中四个
> （`session_ended`、`session_forked`、`session_loaded`、`session_saved`）**全树没有
> 任何发射点**——`session_saved` 在 `EventKind` 自己的文档字符串里就标注为
> "reserved; not emitted in v1"，而 `session_ended` 唯一的命中是 `recorder.py` 里的
> **文档字符串示例**。给它们写渲染分支就是死代码。`turn_started` 有发射但属结构性
> （被 `_group_events_into_turns` 消费）。真实数字是 **3**。

### 4 — 4 条 ruff F821，收窄为静态卫生

`agents/manager.py` 与 `skills/manager.py` 以字符串形式标注插件子系统类型，而真正的
import 只在**函数体内部**（这是刻意的——插件子系统是可选依赖）。模块层从未绑定这些
名字，因此没有任何静态检查器能解析这些注解。

修法是把它们声明进 `TYPE_CHECKING` 块。已验证**不引入运行时 import**
（`agentao.plugins.models` 不进 `sys.modules`）。

> **价值修正。** 审计曾暗示这能恢复运行时内省。**并不能**——已实测：
> `typing.get_type_hints()` 仍抛 `NameError`，因为它按运行时 `__globals__` 解析，而
> `TYPE_CHECKING` 块运行时不存在。全树没有任何代码对这两个方法做内省；schema 生成器
> 作用于 `agentao.host` 的 Pydantic 模型，唯一做内省的测试
> （`test_async_tool.py`）针对的是**另一个方法**且早已用 `localns` 绕过。
> **这是为将来扩大 mypy 门禁做的防护，不是活动缺陷。**

---

## 反向评审后否决

### 5 — 六月 Tier 3：三个长函数

`optimization-opportunities-review.md` 记录了三个"维护成本高"的函数并未实施。它们
在此**正式否决**，不是推迟。

前提是维护成本。12 个月的 churn 数据不支持：

| 文件 | 提交数 | 其中 `fix:` |
|---|---|---|
| `runtime/tool_executor.py`（T3.1，`_execute_one` 249 行） | 7 | 2 |
| `runtime/chat_loop/_runner.py`（T3.3，溢出恢复） | 12 | 3 |

没人改的 249 行函数不产生成本。**长度本身不是重构触发条件**——同一把尺子也把
`agent.py` 那个 231 行的 `__init__` 排除在外（cx **3**，纯顺序装配）。

**T3.2 还栽在第二个独立理由上。** 该提案要把 `Agentao.get_conversation_summary`
当作展示逻辑移出 core。它**有真实调用者**（`cli/ui.py:75`），并且在测试 stub 中作为
宿主提供的方法出现（`test_cli_host_events.py:133`）——它是既有接口的一部分。移走
等于对嵌入宿主的破坏性变更，换来零功能收益。只有当某个宿主真的被它卡住时才重开。

### 6 — mypy 逐包棘轮

审计建议逐包扩大 CI 类型门禁，并称 `agentao.replay`（16 条）是天然切入点。核查后
否决：

- `agentao.replay` —— 16 条里 15 条是 `type-arg` / `no-any-return` 装饰性问题。仅
  1 条真实注解缺陷（`recorder.py:113`，漏了 `Optional[TextIO]`）。
- `agentao.runtime` —— **89 条里 27 条是 mixin 假阳性**（`_CompactionMixin` 访问
  `self._agent`，由具体类提供）。消掉它们意味着写 Protocol 脚手架去哄检查器，而不是
  修任何东西。其余：26 条 `type-arg`、24 条缺注解。

投入产出比差。`mypy --strict` 继续只覆盖 `agentao.host`——那是稳定性边界，也是它真正
产生价值的地方。`recorder.py:113` 那一条可在下次改动该文件时顺手修掉。

### 7 — 全面引入 linter

`uvx ruff check agentao` 报约 2800 条，但这个数字有误导性：926 条是 `List` → `list`
现代化，833 条是 `Optional[X]` → `X | None`，224 条是刻意的 `except Exception` 设计
取舍。高信号子集（`--select F`）是 92 条，其中只有 4 条 F821 有论据支撑——而且见上文
的价值修正。

**不引入 linter，不加 CI 门禁。** 若将来改变主意，从 `--select F` 起步，预期约 88 条
是纯清洁价值。

### 8 — hardline 正则表惰性编译

`permissions_hardline/_patterns.py` 在导入期编译 23 条正则：占 88ms
`import agentao.agent` 中的 17ms，约为 130ms CLI 冷启动的 13%。它是整个导入图里最贵
的单个模块，这正是它看起来像个发现的原因。但它仍然只是 17ms、一次性；对嵌入宿主而言
只在模块导入时付一次。按需再议。

### 9 — 给剩余 5 个未渲染 `EventKind` 补渲染

四个没有发射点；一个（`turn_started`）是结构性的。已在
`tests/test_replay_render_coverage.py` 的 `_NO_SUMMARY_EXPECTED` 中逐条记录理由，并
有配套测试在其中某个**获得**渲染时变红（所以豁免列表不会悄悄过期）。现在补渲染就是
死代码。

---

## 落地后的代码评审（xhigh，2026-07-25）

对三个分支做的对抗性多智能体评审返回 **14 条已确认缺陷**，全部落在本文已声称
"已落地"的工作里。最重要的三条：

- **v1.2 渲染在生产环境根本不生效。** 审计事件携带的是**运行时**的 `turn_id`
  （`runtime/identity.py` 的 uuid4），而 `ReplayAdapter` 为每个 replay turn 生成
  自己的短 id。信封把前者写进了一个按后者分组的文件，于是 `/replay show` 把它们
  渲染成额外的幽灵 turn；而 `SubagentLifecycleEvent` **根本没有 `turn_id` 字段**，
  每次都掉出所有 turn。**该 PR 自己的测试之所以通过，是因为我手工给全部六个事件写死
  了 `turn_id: "t1"`——这是 recorder 产生不出来的形状。** 已修：给 `HostReplaySink`
  加 `turn_id_provider`，信封走 replay 的 id 空间，payload 保留 host 的。
- **一个测试软删了开发者真实的 `~/.agentao/memory.db`。** 让 `_clear_reset` 走真实
  handler 是对的，但 `MemoryManager.clear()` 在 `scope=None` 下会清**用户**存储，
  而该存储解析到 `user_root()`，与 fixture 的 `working_directory` 无关。每次
  `pytest tests/` 都在销毁跨项目用户记忆——静默、全绿。已修：fixture 重定向 `HOME`，
  并加测试断言重定向确实生效。
- **`/copy` 因为改用 `run_captured` 而回归。** 它的管道意味着 `communicate()` 要等
  **每一个后代**关闭写端；`xclip` fork 出的后台选区持有者永远不会关。实测：5.01s
  超时 vs 0.05s。已改为本地 runner，保留进程组与整树 kill，但把 stderr 写进临时文件。

两个教训，指向同一种失效模式——**与代码同源心智模型写出的测试无法证伪它**：

1. v1.2 的测试夹具是我按对 `host/models.py` 的理解构造的，不是从 recorder 取的。
   它编码了与渲染器完全相同的错误假设。
2. 九个 `/copy` 测试全部 stub 掉了 `run_captured`，于是重构真正改变的那个性质
   ——管道/EOF 语义——对测试完全不可见。替换版本会真的拉起 fork 子进程。

另修正：新的穷尽性守卫把 `session_ended` 豁免为"从不发射"，而 `ReplayManager.end()`
会把它写进**每一个**完成的 replay 文件——这个守卫认证了它本该抓住的那个缺陷。豁免
列表现在有测试去 grep 发射点，而不是相信注释。

---

## 已核实的非问题

已对照源码核查并排除——请勿"修复"：

- **`cli/commands` vs `cli/commands_ext`** —— 刻意为之，`commands_ext/__init__.py`
  已注明（"heavier dependencies"），不是历史遗留漂移。
- **跨文件重复代码** —— 用 8 行窗口的归一化检测器扫全树，命中的只有接口签名重复
  （`transport/*.ask_user`，不可避免且显式更清晰）和已经共享 helper 的小响应封装
  （`session_new` / `session_load` 都 import `_session_modes`）。**没有值得抽取的重复。**
- **`agent.py` 1299 行** —— 已拆成 `_init_*` 系列；231 行的 `__init__` 圈复杂度为 3。
- **`acp_client/process.py:236` 裸 `Popen`** —— 长驻服务子进程，正确地**不**走
  `run_captured`。

---

## 方法笔记

**把隐式词汇变成显式的值，漂移就会自己掉出来。** `/sandbox` 补全缺口和 v1.2 渲染
缺口是同一种 bug 形状：一组名字维护在多处，却没有任何东西比对它们。两者都是靠"把这
组名字变成一个值，然后写一个测试"找到的。两个守卫都已验证在还原修复后变红——**一个
从没见过红的穷尽性测试不构成任何证据。**

**反向评审要在动手前做，不是动手后。** 七个项里，反向评审否决了四个，并修正了另外
两个的范围或声称价值。审计的原始产出是错多于对；churn 数据、发射点 grep、以及一次
实际渲染，才是把它们区分开的东西。

**长度不是缺陷。** 被否决的四项里有三项是靠体量或数量论证的（249 行、89 条错误、
2800 条发现）。这些数字没有一个预测到了真实成本。
