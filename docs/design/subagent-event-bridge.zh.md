# 子 Agent 事件桥：回调出去，事件回来

**状态：** **已实施，随 0.5.13 发布。** A 在 #514、#515，B 在 #517（2026-10-11）。决策（rev 2）：两个方案都实施，分两步走——先 A，后 B。rev 3 完成对 rev 2 的复审；rev 4 记录 A 的落地情况（§5），rev 5 记录 B 的落地情况（§6.4）。
**锚点：** agentao `main@60fcca9`（2026-10-11）。下文行号均以该提交为准，即 A 落地之前。
**相关：** `0-5-0-removal-checklist.zh.md` §C（子 Agent 工厂今天为什么用 `build_compat_transport`）；`host-tool-injection.zh.md`；`embedded-host-contract.md`（公共事件 `SubagentLifecycleEvent`，本文不改它）。
**修订记录：** rev 1（`af7b014`）把 B 暂缓，并提出给子 Agent 的 id 加命名空间。rev 2 采纳先 A 后 B，删掉 id 命名空间，公开的 `build_compat_transport` 一行不删（rev 1 错把其中一部分算作可删），并把字段默认值、异常范围和事件顺序写死。rev 3 要求父侧入口在每次调用时读取 `agent.transport`（rev 2 传的是绑定方法，会绕过回放对 transport 的替换），并把 `tests/test_subagent_cancelled_terminal_state.py` 加入测试迁移范围。rev 4 标记 A 已实施：#514 发出结束事件，#515 把其中为 0 的计数（§5 中划掉的一句）换成真实值或省略。rev 5 标记 B 已实施，并写明现在钉住不变量 4–7、9、11 的测试。

## 1. 概要

前台子 Agent 通过六个旧式回调向父 Agent 报告进度。在子 Agent 一侧，`build_compat_transport` 把子 Agent 的 `AgentEvent` *转成*这些回调；在父 Agent 一侧，`tooling/agent_tools.py` 再把回调*转回* `AgentEvent`，发到父 transport 上。为了让信息熬过这一来一回，回调层上搭了两个私有约定：哨兵名 `__agent_start__` / `__agent_end__`，以及塞进工具参数里的 `__call_id__` 键。

一份外部评审（2026-10-10）建议去掉这段往返。读代码后发现，真正的往返比评审说的窄（§3），并且找到一个已确认的缺陷（§4.1）。两步都采纳：

- **A —— 补上生命周期缺口。** 子 Agent 抛异常时发了 `AGENT_START`，却永远不发 `AGENT_END`。修复只在 `AgentToolWrapper.execute()` 内（§5）。
- **B —— 直接转发事件。** 内部子 Agent 路径不再经过回调桥。包装器给子 Agent 一个现有的 `SdkTransport`，其 `on_event` 是一个局部转发函数，父 Agent 把自己的 `emit` 传进来（§6）。目的是减少转换环节，并把哪些字段传递、哪些事件丢弃写成明确规则。做它的理由不是行数。

A 落地并验证之后再开始 B，这样出了回归能定位到是哪一步。

## 2. 今天的路径

```
子 Agentao ──AgentEvent──▶ SdkTransport(on_event=_on_event)        # transport/sdk.py:211（build_compat_transport）
                              │ TURN_START        → step_callback(None, {...})
                              │ TOOL_START        → step_callback(tool, args + {"__call_id__": id})   # sdk.py:217-226
                              │ TOOL_CONFIRMATION → confirmation_event_callback(event)（仅当有 step_callback）
                              │ TOOL_OUTPUT       → output_callback(tool, chunk, call_id=)
                              │ TOOL_COMPLETE     → tool_complete_callback(tool, call_id=, status=, …)
                              │ 其他一切          → 丢弃
                              ▼
   AgentToolWrapper 加前缀：步骤标签 "[name n/max] tool"          # _wrapper.py:1254 _make_prefixed_step_callback
                              确认标签，同一个计数器              # _wrapper.py:1278
                              confirm_tool 名称 "[name] tool"     # _wrapper.py:621-624
                              ▼
父 tooling/agent_tools.py:59-112 重建 AgentEvent(TURN_START | AGENT_START | AGENT_END | TOOL_START | TOOL_OUTPUT | TOOL_COMPLETE)
                              ▼
父 transport ──▶ CLI 显示（cli/display.py）· ACP（acp/transport.py）· 回放（replay/adapter.py）
```

包装器自己的生命周期**不**经过子 transport。`execute()` 直接用 `"__agent_start__"` / `"__agent_end__"` 和一个 `SubagentProgress` 调父 Agent 的 `step_callback`（`_wrapper.py:300-304`、`347-365`），父 Agent 再把它们映射回 `AGENT_START` / `AGENT_END`（`agent_tools.py:65-81`）。

后台子 Agent 用的是没有事件处理器的 `SdkTransport(confirm_tool=lambda *_: False)`，所以它的*进度*事件一个也到不了父 Agent（`_wrapper.py:613-616`）。它仍然通过自己的路径发公共的 `SubagentLifecycleEvent`（`_wrapper.py:1122` 起）。

公共宿主事件 `SubagentLifecycleEvent` 走的是另一条路（`subagent_emitter`，`_wrapper.py:390-430`），不在本文范围内。

## 3. 真正往返的是什么

评审说 `__agent_start__`、`__agent_end__` 和 `__call_id__` 都流经子 transport。其实只有一个。

| 项 | 路径 | 是否往返？ |
|---|---|---|
| `__agent_start__` / `__agent_end__` | 包装器 → 父 `step_callback` → `AgentEvent` | **否。** 是回调 API 上的单向哨兵。`build_compat_transport` 里 `AGENT_START` → `"__agent_start__"` 的映射（`sdk.py:251-277`）对子 Agent 永远不会执行：子 Agent 从不拿到 agent 工具（CLAUDE.md，*Sub-agents execute from one registry*），所以从不发 `AGENT_START`。那个分支服务的是 `build_compat_transport` 的外部使用者。 |
| `__call_id__` | 子 `TOOL_START` → `step_callback` 的 args → 在 `agent_tools.py:84` 被弹出 | **是。** 事件 → 回调 → 事件。 |
| 步骤 / 确认标签、`confirm_tool` 前缀 | 子事件 → 包装器变换 → 父 | 不是往返，但是 B 必须保留的真实变换（§7）。 |
| `TOOL_OUTPUT` / `TOOL_COMPLETE` 的 `call_id`、`status`、`error` | `_accepted_meta_keywords` 签名检查（`sdk.py:142-181`）→ 关键字参数 → 父 `AgentEvent` | **是。** 事件 → 回调 → 事件。今天是对的，靠签名内省维持。 |

所以去掉往返，意味着改 `AgentManager.create_agent_tools` 与父 Agent 之间的回调接口（`agents/manager.py:137-160`）。这是**内部**接口：`AgentManager`、`AgentToolWrapper`、`SubagentProgress` 没有出现在任何参考文档或指南里，`Agentao` 也是自己构造 `AgentManager`（`agent.py:885`）。

`build_compat_transport` 是**公开的**（0.5.0 的迁移入口），它的调用者能观察到的行为也是公开的：`step_callback` 参数里的 `__call_id__` 键、回调签名检查、生命周期映射，以及它构造的 `SubagentProgress`。这些全部保留。

## 4. 阅读中的发现

### 4.1 前台子 Agent 抛异常时缺 `AGENT_END`（已确认）

`execute()` 在 `_run_sync` 之前发 `__agent_start__`，只在它正常返回后才发 `__agent_end__`。两个 `except` 分支（`_wrapper.py:326-341`）发了公共的终态 `SubagentLifecycleEvent` 然后重新抛出，但不发 `__agent_end__`。

探测（`60fcca9`）：让包装器的 `llm_config_getter` 返回 `{}`，`_run_sync` 抛出 `KeyError('api_key')`。步骤回调只收到 `['__agent_start__']`，别的什么都没有。

按消费方看影响：
- **CLI：** `_on_agent_end` 不会运行，所以带运行状态和统计的收尾分隔线不会打印（`cli/display.py:676`）。
- **ACP：** 客户端收到 `[sub-agent started: name]`，却永远收不到 `[sub-agent finished: …]`。
- **回放：** 记下了 `SUBAGENT_STARTED`，没有 `SUBAGENT_COMPLETED`，且这一项会在本会话剩余时间里一直留在 `_subagent_stack` 上（`replay/adapter.py:555-590`）。

可触发的原因包括：子 Agent 构造失败（模型配置错误），以及在 turn 之外抛出的取消（`_wrapper.py:327-331` 的注释）。

### 4.2 子 Agent 的 `call_id` 被转发进父 Agent 的 id 作用域（观察中的风险，未复现）

`runtime/identity.py` 规定 `tool_call_id` 的唯一性作用域是 `(session_id, turn_id, tool_call_id)`，并且不假设 provider 的 id 全局唯一。桥把子 Agent 的 provider id 原样转发进父 Agent 的 turn，而 CLI 和 ACP 按 `call_id` 来定位工具行。如果某个 provider 重复使用 id，子 Agent 的 `TOOL_COMPLETE` 可能关掉别的行：父 Agent 的 `agent_*` 那一行，或者同一个子 Agent 前一个 turn 里的某一行——因为父 Agent 在自己的一个 turn 里会看到子 Agent 所有 turn 的事件。

目前没有找到会这样做的 provider。A 和 B 都不改 id：不给 `ToolRunner` 加参数、不引入 id 映射表、不改变 ACP 客户端看到的 id。如果哪天复现了冲突，怎么修另行决定。（rev 1 提出的 `parent_call_id/child_call_id` 前缀覆盖不了同一个子 Agent 内的情况，所以删掉。）

### 4.3 工具名在开始与完成时不一致（仅记录）

`TOOL_START` 带的是标签 `[name n/max] tool`；`TOOL_OUTPUT` 和 `TOOL_COMPLETE` 带的是裸的 `tool`。三个消费方都优先按 `call_id` 定位（`cli/display.py:460-471`），所以今天无害。B 保持原样。

## 5. A —— 补上生命周期缺口

范围：只改 `AgentToolWrapper.execute()`，不改任何公共宿主事件。

- **每个已发出 `AGENT_START` 的前台运行，都恰好发一次 `AGENT_END`。** 覆盖正常返回、普通异常和 `AgentCancelledError`。
- **正常返回：** 不变。沿用现有的 `_terminal_state()` 分类和统计字段，包括正常返回的取消结果。
- **`AgentCancelledError`：** `state="cancelled"`，不填 `error`（与正常返回的取消同一规则：状态本身已说明情况）。
- **其他 `Exception`：** `state="failed"`，`error=type(exc).__name__`。
- **不属于 `Exception` 的 `BaseException`**（`KeyboardInterrupt`、`SystemExit`）：不发 `AGENT_END`。这与现有 `except` 的覆盖范围完全一致，也与公共终态事件一致——后者在这些情况下同样不发。结束事件和公共事件保持同步。
- **抛异常路径上没有 `stats`。** 构造或运行抛异常时，`_run_sync` 没有返回值。这些路径上绝不能读 `stats`。~~计数取 `SubagentProgress` 的默认值（0）。~~ 实际落地（#515）：0 会被当成真实计数显示，所以子 Agent 开始运行后才抛异常时，`turns`、`tool_calls`、`tokens`（历史估算）和 `duration_ms` 从子 Agent 的历史和计时读出，口径与 `stats` 相同。子 Agent 没有运行时它们为 `None`，`AGENT_END` 省略这些键。`build_compat_transport` 的 step 回调仍读到 0。
- **顺序：每条路径上都是先 `AGENT_END`，后公共终态事件。** 这就是今天正常路径的顺序（`_wrapper.py:347` 在 `367` 之前）。抛异常路径上，在 `except` 分支里先发 `AGENT_END`，再调 `_terminal_subagent_event`。
- **结束事件不能掩盖任何东西。** 发送 `AGENT_END` 不能替换原始异常，也不能让公共终态事件被跳过。CLI 和 ACP 的 transport 在 `emit` 里已经吞掉异常（`cli/transport.py:181`、`acp/transport.py:246`），但嵌入宿主的 transport 未必如此。所以发送时要包一层 `try/except Exception` 并记日志。

发送放在现有的 `except` 分支里，或放进它们共同调用的一个小的局部函数。不新增生命周期管理器、状态机或全局配对机制。

**实际落地：** `AgentToolWrapper._send_agent_end` 在三条路径上做受保护的发送。测试：`tests/test_subagent_agent_end_on_raise.py`。公共宿主事件不变，但 `AGENT_END` 的载荷变了：计数可能缺失（开发者指南第 4.2 节）。

## 6. B —— 直接转发事件

### 6.1 接缝

内部工厂（`AgentManager.create_agent_tools`、`AgentToolWrapper.__init__`）用三个入口替代现有的进度回调组（`step_callback`、`output_callback`、`tool_complete_callback`、`confirmation_event_callback`、`confirmation_callback`、`ask_user_callback`）：

```python
event_sink: Callable[[AgentEvent], None]
confirm_tool: Callable[[str, str, dict], bool]
ask_user: Callable[..., str]
```

父 Agent 传入的函数要在**每次调用时**读取 `agent.transport`，与今天 `tooling/agent_tools.py` 的做法完全相同：

```python
event_sink=lambda event: agent.transport.emit(event),
confirm_tool=lambda *a, **kw: agent.transport.confirm_tool(*a, **kw),
ask_user=transport_ask_user(agent),
```

不要传绑定方法 `agent.transport.emit` / `agent.transport.confirm_tool`。启动回放会把 `agent.transport` 换成 `ReplayAdapter`（`replay/manager.py:106-108`），结束回放又把内层 transport 换回来（`replay/manager.py:168-171`），两者都发生在 agent 工具注册之后。绑定方法会固定在注册时的那个 transport 上，于是之后的子 Agent 事件会绕过后来启动的回放，或继续发往已被移除的适配器。`transport_ask_user` 出于同样的原因，已经是每次调用时读取 transport。不需要新增 getter 接口或代理类。

这些仍然是回调：`event_sink` 接收完整事件，`confirm_tool` / `ask_user` 返回用户的回答。"去掉回调桥"指的是去掉"事件转成旧式回调再转回事件"这层转换，不是去掉所有回调。

内部工厂和包装器一次切换到新入口。不会有新旧内部接口并存的阶段，也不在两者之间加适配层。公开兼容接口的弃用不在本次范围内。

### 6.2 前台转发函数

前台子 Agent 使用现有的 `SdkTransport(on_event=forward, confirm_tool=prefixed_confirm, ask_user=ask_user)`。`forward` 是包装器里的局部函数，只处理以下事件：

| 子事件 | 转发为 |
|---|---|
| `TURN_START` | 沿用今天的显示重置语义：转发 `{"display_reset": True}` 或 `{}`；只有不带 `display_reset` 的事件推进轮次计数。 |
| `TOOL_START` | `tool` 换成 `[name n/max] tool` 标签；`args` 和 `call_id` 保留。 |
| `TOOL_CONFIRMATION` | 使用与随后 `TOOL_START` 相同的标签；其余数据和 `call_id` 保留。 |
| `TOOL_OUTPUT` | `tool`、`chunk`、`call_id` 保留。 |
| `TOOL_COMPLETE` | `tool`、`call_id`、`status`、`duration_ms`、`error` 保留。 |
| 其他事件 | **丢弃**（§7，不变量 6）。 |

`forward` 改写标签或补默认值时，用复制出来的 `data` 字典构造新事件。`AgentEvent` 是可变的 dataclass（`transport/events.py:67`），而子 Agent 自己的 transport 会把同一个对象交给它的订阅者，所以不能原地修改。

`prefixed_confirm` 给工具名加上 `[name]` 前缀再调父 Agent 的 `confirm_tool`，与今天 `_wrapper.py:621-624` 的做法相同。`SdkTransport` 在跑满迭代上限时默认返回 `{"action": "stop"}`，与 `build_compat_transport` 相同，所以子 Agent 跑满上限后仍会停止。

包装器自己通过 `event_sink` 发 `AGENT_START` 和 `AGENT_END` 两个 `AgentEvent`，载荷与今天 `agent_tools.py:65-81` 构造的相同，包括 #515 的规则：未知的计数省略。哨兵和 `SubagentProgress` 退出内部路径。公共 `SubagentLifecycleEvent` 继续走自己的路径。

后台子 Agent 继续使用没有事件处理器、拒绝所有确认的 `SdkTransport(confirm_tool=lambda *_: False)`。"不发事件"指的是子 transport 的进度事件；后台运行的公共 `SubagentLifecycleEvent` 通知不变。

### 6.3 字段默认值：完全沿用今天的规则

`forward` 保留父侧桥今天施加的默认值。B 不借机统一这些边界行为。

| 事件 | 字段 | 今天的规则 | 位置 |
|---|---|---|---|
| `TOOL_START` | `call_id` | 缺失**或为空**时回退到**标签** `[name n/max] tool`。（`build_compat_transport` 只注入为真的 id，父侧用 `or name`，而 `name` 就是标签。） | `sdk.py:224`、`agent_tools.py:84` |
| `TOOL_OUTPUT` | `call_id` | 只有 `None` 才回退，回退到**裸**工具名；空字符串保留。 | `agent_tools.py:97-101` |
| `TOOL_COMPLETE` | `call_id` | 同 `TOOL_OUTPUT`。 | `agent_tools.py:103-112` |
| `TOOL_COMPLETE` | `status` | `None` → `"ok"`。 | 同上 |
| `TOOL_COMPLETE` | `duration_ms` | `None` → `0`。 | 同上 |
| `TOOL_COMPLETE` | `error` | 保留；缺失时为 `None`。 | 同上 |

这些回退在真实路径上都不会触发。`normalize_tool_call_id`（`runtime/identity.py:51`）保证每个规划出的调用都有非空 id，执行器也总会发 `status`、`duration_ms` 和 `error`（`runtime/tool_executor.py:296-304`、`683-688`）。这些规则只对构造出来的事件有意义，所以测试保留现有用例即可，不需要新增。

### 6.4 B 改什么、保留什么

从**内部**路径上去掉：
- `tooling/agent_tools.py:59-112`：重建事件的三个父侧回调，改为 §6.1 里每次调用时读取 transport 的 `event_sink` lambda。
- `_wrapper.py` 里的哨兵常量与调用（`261-262`、`300-304`、`347-365`）。
- 子 Agent 对 `build_compat_transport`、`__call_id__` 键和 `_accepted_meta_keywords` 的依赖。

**不变：** `sdk.py` 里的整个 `build_compat_transport`，包括 `__call_id__` 注入、签名检查、`AGENT_START` / `AGENT_END` 映射，以及它构造的 `SubagentProgress`。外部调用者依赖这些代码，所以都不算 B 的删除收益。

测试：`tests/test_subagent_tool_call_id.py` 用 `build_compat_transport` 和 `_make_prefixed_step_callback` 搭夹具，并按关键字名伪造 `create_agent_tools`。它的夹具改为经过实际的生产转发函数（§8）。`tests/test_subagent_cancelled_terminal_state.py` 也依赖旧入口：它的 `_watch()` 直接设置 `wrapper._step_callback`，`_agent_end()` 按哨兵 `__agent_end__` 读取结束事件。它的夹具改为捕获 `AgentEvent`，保留原有的取消状态和结束次数断言。另外三个测试构造 `AgentToolWrapper` 时不传回调，`tests/test_async_tool.py` 读取 `create_agent_tools` 的类型注解。

**实际落地（B，#517）：** `AgentToolWrapper._child_transport` 构造子 transport，`_make_forwarder` 是白名单转发函数；`AGENT_START` / `AGENT_END` 经 `event_sink` 发出（`_send_agent_end` 沿用 #515 的规则）。`tooling/agent_tools.py` 传三个每次调用都读取 transport 的 lambda；`SubagentProgress` 现在只由 `build_compat_transport` 构造。`tests/test_subagent_tool_call_id.py` 用 `register_agent_tools` 实际传入的入口驱动生产代码里的 `_child_transport`。

## 7. 不变量

| # | 不变量 | 由谁钉住 |
|---|---|---|
| 1 | 每个子工具行在开始、确认、输出、完成之间保持自己的 `call_id`，包括同名的并行批次。 | `test_same_name_parallel_completions_keep_distinct_call_ids`、`test_tool_output_carries_call_id`、`test_confirmation_event_keeps_subagent_call_id` |
| 2 | `TOOL_COMPLETE` 转发真实的 `status`、`duration_ms` 和 `error`；缺失字段取 §6.3 的默认值。 | `test_completion_forwards_real_status_and_error`、`test_completion_without_call_id_falls_back_to_name` |
| 3 | 确认开启时带的 `[name n/max]` 标签与随后的 `TOOL_START` 相同；被拒绝的确认（`display_reset`）不推进计数器。 | `test_confirmation_opening_carries_the_subagent_label`、`test_a_rejected_confirmation_does_not_advance_the_turn_label` |
| 4 | 只有在转发开始事件时才转发 `TOOL_CONFIRMATION`，否则 ACP 会打开一个没人关闭的调用。B 里前台子 Agent 两者都转发，后台都不转发。 | `tests/test_subagent_tool_call_id.py`：`test_a_foreground_child_without_a_sink_forwards_nothing`、`test_a_background_child_sends_the_parent_nothing_and_refuses` |
| 5 | `confirm_tool` 以带 `[name]` 前缀的名称到达父 transport，MCP Skills 门禁的说明和 CLI 提示走的路径与今天相同。 | `test_confirmation_and_questions_reach_the_parent_transport`、`test_a_rejected_confirmation_does_not_advance_the_turn_label` |
| 6 | 子 Agent 发出的其他事件都不到达父 Agent：没有 `THINKING` 或 `LLM_TEXT`（不显示子 Agent 的推理），没有 `TURN_BEGIN` / `TURN_END` / `LLM_CALL_*` / `TOOL_RESULT`（否则回放会把子 Agent 的 turn 记成父 Agent 的），没有权限或记忆事件。 | `test_events_outside_the_whitelist_stay_with_the_child` |
| 7 | 后台子 Agent 拒绝所有确认，其子 transport 不向父 Agent 发任何进度事件。它的公共 `SubagentLifecycleEvent` 不受影响。 | 拒绝：`test_background_subagent_approval.py::test_a_call_that_needs_confirmation_runs_only_where_someone_can_approve_it`；"不发进度事件"：`test_a_background_child_sends_the_parent_nothing_and_refuses` |
| 8 | 正常返回、`Exception` 和 `AgentCancelledError` 时，每个 `AGENT_START` 之后恰好跟一个 `AGENT_END`；它在公共终态事件之前；发送失败不掩盖任何东西。 | `tests/test_subagent_agent_end_on_raise.py`（#514） |
| 9 | 子 Agent 跑满迭代上限后停止。 | `test_running_out_of_turns_stops` |
| 10 | 公开的 `build_compat_transport` 对外部调用者保持原有契约。 | `tests/test_transport.py` |
| 11 | 在 agent 工具注册之后替换父 transport（启动或结束回放），子 Agent 的事件和确认仍到达新的 transport。 | `test_a_transport_replaced_after_registration_gets_the_events` |

## 8. 验收与测试

**A：**
- 为普通异常、构造失败（§4.1 的探测）和抛出的 `AgentCancelledError` 各加回归测试。每个都检查开始与结束事件的次数、顺序和状态，检查 `AGENT_END` 在公共终态事件之前，并检查原始异常继续传播。
- 加一个发送 `AGENT_END` 时抛异常的测试：原始异常仍然传播，公共终态事件仍然发出。
- 保留现有的"正常返回的取消"测试。
- CHANGELOG 在 *Fixed* 下加一条。

**B：**
- 把 `tests/test_subagent_tool_call_id.py` 的夹具改为经过实际的生产转发函数，并保留所有行为断言：
  - 同名并行工具在开始、确认、输出、完成之间保持各自的 `call_id`；
  - 真实的状态、错误、耗时，以及 §6.3 的默认值不变；
  - 确认和开始的标签一致，拒绝确认不推进轮次；
  - `confirm_tool` 仍以 `[name]` 前缀到达父 transport；
  - 白名单以外的事件不进入父 transport；
  - 后台子 Agent 始终拒绝确认，其子 transport 不向父 Agent 发任何东西；
  - 子 Agent 跑满迭代上限后停止；
  - 公开的 `build_compat_transport` 保持旧契约；
  - 注册之后替换父 Agent 的 `agent.transport`，子 Agent 的事件和确认到达新的 transport。
- 把 `tests/test_subagent_cancelled_terminal_state.py` 的 `_watch()` 夹具改为捕获 `AgentEvent`；保留取消状态和恰好一次结束的断言。
- 保留现有的 ACP 输出断言和真实 `ToolRunner` 的拒绝确认测试，不要换成只检查转发函数内部的测试。
- B 不需要 CHANGELOG 条目（没有行为变化）。

**验证顺序：** 先跑相关回归测试，再跑默认测试集和 `ruff check .`。

实现只复用现有的 transport、事件类型和状态分类。不新增桥接类、事件总线、路由配置或通用字段规范化框架。
