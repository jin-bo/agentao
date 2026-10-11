# 子 Agent 事件桥：回调出去，事件回来

**状态：** 提案，**未授权**。§5（方案 A）是一个小的缺陷修复，不依赖其余部分。§6（方案 B）是重构，用户看不到任何变化；只在下次因别的原因打开这块代码时再做。
**锚点：** agentao `main@60fcca9`（2026-10-11）。下文行号均以该提交为准。
**相关：** `0-5-0-removal-checklist.zh.md` §C（子 Agent 工厂今天为什么用 `build_compat_transport`）；`host-tool-injection.zh.md`；`embedded-host-contract.md`（公共事件 `SubagentLifecycleEvent`，本文不改它）。

## 1. 概要

前台子 Agent 通过六个旧式回调向父 Agent 报告进度。在子 Agent 一侧，`build_compat_transport` 把子 Agent 的 `AgentEvent` *转成*这些回调；在父 Agent 一侧，`tooling/agent_tools.py` 再把回调*转回* `AgentEvent`，发到父 transport 上。为了让信息熬过这一来一回，回调层上搭了两个私有约定：哨兵名 `__agent_start__` / `__agent_end__`，以及塞进工具参数里的 `__call_id__` 键。

一份外部评审（2026-10-10）建议去掉这段往返。读代码后发现，真正的往返比评审说的窄（§3），并且找到一个已确认的缺陷（§4.1）。本文记录事实、任何改动都必须守住的不变量（§7），以及两个方案：

- **A —— 补上生命周期缺口（建议现在做）。** 子 Agent 抛异常时发了 `AGENT_START`，却永远不发 `AGENT_END`。改动只在 `_wrapper.py` 几行，与任何重构无关。
- **B —— 用事件汇（event sink）替换回调桥（暂缓）。** 包装器交给父 Agent 一个转发函数，而不是六个回调。它去掉哨兵、`__call_id__` 夹带和约 85 行转换代码，行为不变。只值得和这一块的其他工作一起做。

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

后台子 Agent 用的是没有事件处理器的 `SdkTransport(confirm_tool=lambda *_: False)`，所以它的事件一个也到不了父 Agent（`_wrapper.py:613-616`）。

公共宿主事件 `SubagentLifecycleEvent` 走的是另一条路（`subagent_emitter`，`_wrapper.py:390-430`），不在本文范围内。

## 3. 真正往返的是什么

评审说 `__agent_start__`、`__agent_end__` 和 `__call_id__` 都流经子 transport。其实只有一个。

| 项 | 路径 | 是否往返？ |
|---|---|---|
| `__agent_start__` / `__agent_end__` | 包装器 → 父 `step_callback` → `AgentEvent` | **否。** 是回调 API 上的单向哨兵。`build_compat_transport` 里 `AGENT_START` → `"__agent_start__"` 的映射（`sdk.py:251-277`）**对子 Agent 是死代码**：子 Agent 从不拿到 agent 工具（CLAUDE.md，*Sub-agents execute from one registry*），所以从不发 `AGENT_START`。那个分支只服务 `build_compat_transport` 的外部使用者。 |
| `__call_id__` | 子 `TOOL_START` → `step_callback` 的 args → 在 `agent_tools.py:84` 被弹出 | **是。** 事件 → 回调 → 事件。 |
| 步骤 / 确认标签、`confirm_tool` 前缀 | 子事件 → 包装器变换 → 父 | 不是往返，但是任何设计都必须保留的真实变换（§7）。 |
| `TOOL_OUTPUT` / `TOOL_COMPLETE` 的 `call_id`、`status`、`error` | `_accepted_meta_keywords` 签名检查（`sdk.py:142-181`）→ 关键字参数 → 父 `AgentEvent` | **是。** 事件 → 回调 → 事件。今天是对的，靠签名内省维持。 |

所以去掉往返，意味着改 `AgentManager.create_agent_tools` 与父 Agent 之间的回调接口（`agents/manager.py:137-160`）。这是**内部**接口：`AgentManager`、`AgentToolWrapper`、`SubagentProgress` 没有出现在任何参考文档或指南里，`Agentao` 也是自己构造 `AgentManager`（`agent.py:885`）。`build_compat_transport` 本身是公开的（0.5.0 的迁移入口），保留不动。

## 4. 阅读中的发现

### 4.1 前台子 Agent 抛异常时缺 `AGENT_END`（已确认）

`execute()` 在 `_run_sync` 之前发 `__agent_start__`，只在它正常返回后才发 `__agent_end__`。两个 `except` 分支（`_wrapper.py:326-341`）发了公共的终态 `SubagentLifecycleEvent` 然后重新抛出，但不发 `__agent_end__`。

探测（`60fcca9`）：让包装器的 `llm_config_getter` 返回 `{}`，`_run_sync` 抛出 `KeyError('api_key')`。步骤回调只收到 `['__agent_start__']`，别的什么都没有。

按消费方看影响：
- **CLI：** `_on_agent_end` 不会运行，所以带运行状态和统计的收尾分隔线不会打印（`cli/display.py:676`）。
- **ACP：** 客户端收到 `[sub-agent started: name]`，却永远收不到 `[sub-agent finished: …]`。
- **回放：** 记下了 `SUBAGENT_STARTED`，没有 `SUBAGENT_COMPLETED`，且这一项会在本会话剩余时间里一直留在 `_subagent_stack` 上（`replay/adapter.py:555-590`）。

可触发的原因包括：子 Agent 构造失败（模型配置错误），以及在 turn 之外抛出的取消（`_wrapper.py:327-331` 的注释）。

### 4.2 子 Agent 的 `call_id` 被转发进父 Agent 的 id 作用域（假设，未复现）

`runtime/identity.py` 规定 `tool_call_id` 的唯一性作用域是 `(session_id, turn_id, tool_call_id)`，并且不假设 provider 的 id 全局唯一。桥把子 Agent 的 provider id 原样转发进父 Agent 的 turn，而 CLI 和 ACP 按 `call_id` 来定位工具行。如果某个 provider 给父调用和子调用发了同一个 id（例如顺序编号的 `call_0`），子 Agent 的 `TOOL_COMPLETE` 就可能关掉父 Agent 的 `agent_*` 那一行。我没有找到会这样做的 provider，所以这是设计风险，不是缺陷。要修就放在方案 B 里（§6.3）。

### 4.3 工具名在开始与完成时不一致（仅记录）

`TOOL_START` 带的是标签 `[name n/max] tool`；`TOOL_OUTPUT` 和 `TOOL_COMPLETE` 带的是裸的 `tool`。三个消费方都优先按 `call_id` 定位（`cli/display.py:460-471`），所以今天无害。只有将来按名称定位的新消费方才会受影响。

## 5. 方案 A —— 补上生命周期缺口

在 `execute()` 的两条抛异常路径上发 `AGENT_END`：`state="cancelled"` 或 `"failed"`，失败时 `error=type(exc).__name__`。最干净的写法是用 `try/finally` 保证恰好发一次结束，这样以后新增的抛异常路径也不会重新打开这个缺口。

- **测试：** 把 §4.1 的探测写成回归测试（`_run_sync` 抛异常时，恰好一次开始、一次结束，顺序正确），再加一个取消的变体。
- **规模：** 一个函数；不改接口；CHANGELOG 在 *Fixed* 下加一条。

它不依赖方案 B，B 也应当保留同样的保证。

## 6. 方案 B —— 事件汇桥

### 6.1 形状

`create_agent_tools` 从父 Agent 拿三样东西，而不是六个回调加 `confirmation_event_callback`：

```python
event_sink: Callable[[AgentEvent], None]        # agent.transport.emit
confirm_tool: Callable[[str, str, dict], bool]  # agent.transport.confirm_tool
ask_user: Callable[..., str]                    # transport_ask_user(agent)，不变
```

前台子 Agent 拿到 `SdkTransport(on_event=forward, confirm_tool=prefixed_confirm, ask_user=ask_user, …)`，其中 `forward` 是一个**白名单**：

| 子事件 | 转发为 |
|---|---|
| `TURN_START` | 原样；除非带 `display_reset`，否则推进 turn 计数器 |
| `TOOL_START`、`TOOL_CONFIRMATION` | `data["tool"]` 替换为 `[name n/max] tool` 标签 |
| `TOOL_OUTPUT`、`TOOL_COMPLETE` | 原样（`call_id`、`status`、`duration_ms`、`error` 不动） |
| 其他一切 | **丢弃**（§7，不变量 6） |

包装器自己通过 `event_sink` 发 `AgentEvent(AGENT_START | AGENT_END)`，哨兵和 `SubagentProgress` 就退出了内部路径。

### 6.2 能去掉什么

- `tooling/agent_tools.py:59-112`：重建事件的三个父侧回调（54 行），改为直接传 `agent.transport.emit`。
- `__call_id__` 的注入（`sdk.py:217-226`）和弹出（`agent_tools.py:84`）。
- `_wrapper.py` 里的哨兵常量与调用（`261-262`、`300-304`、`347-365`）。
- 子 Agent 对 `_accepted_meta_keywords` 签名内省的依赖。

**不**去掉的：`build_compat_transport`、它的 `AGENT_START` 映射和 `SubagentProgress` 为外部调用者保留。要不要弃用它们是另一个决定。

### 6.3 可选：给子 Agent 的 id 加命名空间

转发集中到一处之后，§4.2 有个现成的修法：把 `call_id` 转发成 `f"{parent_call_id}/{child_call_id}"`。这需要包装器知道自己所在的父调用 id，而 `ToolRunner` 今天并不传（它只注入取消令牌）。只有 §4.2 真被复现时才做；它会改变 ACP 客户端看到的 id。

### 6.4 成本

- 去掉约 85 行生产代码（`agent_tools.py` 54 行，`_wrapper.py` 约 25 行哨兵代码，`sdk.py` 5 行）；转发函数新增约 30 行。
- `tests/test_subagent_tool_call_id.py`（310 行）用 `build_compat_transport` 和 `_make_prefixed_step_callback` 搭夹具，并按关键字名伪造 `create_agent_tools`。它大部分要针对新接缝重写，风险就在这里：重写后的测试必须仍能在旧测试钉住的那些 bug 上失败。它是唯一给 `AgentToolWrapper` 传回调的测试；另外三个测试构造包装器时不传回调，`tests/test_async_tool.py` 读取 `create_agent_tools` 的类型注解。
- 用户看不到变化，所以不需要 CHANGELOG 条目；没有双语文档提到这个 API。

## 7. 任何改动都必须守住的不变量

| # | 不变量 | 由谁钉住 |
|---|---|---|
| 1 | 每个子工具行在开始、确认、输出、完成之间保持自己的 `call_id`，包括同名的并行批次。 | `test_same_name_parallel_completions_keep_distinct_call_ids`、`test_tool_output_carries_call_id`、`test_confirmation_event_keeps_subagent_call_id` |
| 2 | `TOOL_COMPLETE` 转发真实的 `status` 和 `error`；缺 id 时退回用工具名。 | `test_completion_forwards_real_status_and_error`、`test_completion_without_call_id_falls_back_to_name` |
| 3 | 确认开启时带的 `[name n/max]` 标签与随后的 `TOOL_START` 相同；被拒绝的确认（`display_reset`）不推进计数器。 | `test_confirmation_opening_carries_the_subagent_label`、`test_a_rejected_confirmation_does_not_advance_the_turn_label` |
| 4 | 只有在转发开始事件时才转发 `TOOL_CONFIRMATION`，否则 ACP 会打开一个没人关闭的调用。 | `test_confirmation_is_not_forwarded_without_a_step_callback` |
| 5 | `confirm_tool` 以带 `[name]` 前缀的名称到达父 transport，MCP Skills 门禁的说明和 CLI 提示走的路径与今天相同。 | 未钉住 —— 前缀目前没有测试；随 B 补上 |
| 6 | 子 Agent 发出的其他事件都不到达父 Agent：没有 `THINKING` 或 `LLM_TEXT`（不显示子 Agent 的推理），没有 `TURN_BEGIN` / `TURN_END` / `LLM_CALL_*` / `TOOL_RESULT`（否则回放会把子 Agent 的 turn 记成父 Agent 的），没有权限或记忆事件。今天成立，是因为 `build_compat_transport` 忽略未列出的类型；B 必须把它写成显式白名单。 | 未钉住 —— 随 B 补测试 |
| 7 | 后台子 Agent 拒绝所有确认，且不发任何事件。 | 拒绝：`test_background_subagent_approval.py::test_a_call_that_needs_confirmation_runs_only_where_someone_can_approve_it`；"不发事件"：未钉住 |
| 8 | 每个 `AGENT_START` 之后恰好跟一个 `AGENT_END`（方案 A 之后）。 | 由方案 A 补上 |

## 8. 建议

1. **现在做方案 A。** 这是已确认的缺陷，有三处可见症状，修复只涉及一个函数。
2. **方案 B 暂缓**，等下次因别的原因打开这块代码时再做（例如改子 Agent 进度的显示方式，或出现第二个子事件消费方）。单独做的话，是用约 55 行的净减少换一次有真实回归风险的测试重写。
3. **§4.2 作为观察中的风险保留。** 只有复现了才动手。
