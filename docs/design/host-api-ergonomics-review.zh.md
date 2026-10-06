# 宿主 API 易用性评审：嵌入能不能更简单？

**状态：** 评审，2026-10-06。下面每一项都是**提议**，都未获批准，也未实施。证据引用自 `main` @ `2750e16`。
**读者：** 决定改动嵌入式宿主接口的 agentao 维护者，以及后续 PR 的评审者。
**相关文档：**
- `docs/design/host-api-ergonomics-review.md`：英文版，内容相同
- `docs/design/embedded-host-contract.md`：稳定边界画在哪里、为什么这样画
- `docs/reference/host-api.md`：现有的稳定契约
- `docs/guides/embed-for-agents.md`、`docs/guides/embedding.md`：告诉宿主该怎么写的指南

## 1. 问题与方法

问题是：面向宿主的 API 能不能更简单？本评审把**宿主实际要写的代码和导入**与**稳定契约覆盖的范围**做对比。依据是 `agentao/agent.py`、`examples/` 下的 Python 示例和两份嵌入指南。每条发现都给出所依据的 `文件:行号`。

**先说结论：** 主要成本不在构造函数参数多，而在于**默认行为、文档和示例三者不一致**，而且宿主最常见的需求（流式聊天界面）落在稳定契约**之外**。

## 2. 汇总

| # | 发现 | 提议 | 兼容性 |
|---|---|---|---|
| F1 | 无界面宿主会批准所有 ASK，文档没说；没有 engine 的 agent 也切换不了模式 | (a) 改文档；(b) 默认建 engine；(c) 由维护者决定无界面默认 | (a) 无影响；(b) 行为变更；(c) 破坏性 |
| F2 | 流式文本不在契约内，所有聊天类示例都导入了内部接口 | `Agentao.astream()`，产出一个小的公开事件联合 | 纯新增；schema 快照会变大 |
| F3 | 导入分散在 8 个模块；`set_permission_mode` 的参数类型不公开 | 接受字符串模式；公开 `PermissionMode` / `CancellationToken` | 纯新增 |
| F4 | 不支持 `with` / `async with`，每个宿主都写 `try/finally close()` | `__enter__/__exit__`、`aclose()`、`__aenter__/__aexit__` | 纯新增 |
| F5 | `chat()` 返回字符串不代表模型真的回答了 | 由 F2 的结束事件覆盖；`chat()` 不改 | 不适用 |
| F6 | 观察者别名重复；构造函数 32 个参数 | 别名标记废弃；构造函数不动 | 仅废弃 |

## 3. 发现

### F1. 无界面宿主会批准所有 ASK，文档没说

**实际行为。** `runtime/tool_planning.py::_decide`（`:654-689`）里，engine 给出 `DENY` 或 `ALLOW` 就是最终结果。engine 给出 `ASK` 或没有匹配规则时，交给工具自己的 `requires_confirmation`，再交给 `transport.confirm_tool`。宿主不传 transport 时，构造函数用 `NullTransport()`（`agent.py:371`）。它的 `confirm_tool` 对所有确认都返回 `True`，只有 MCP Skills 闸门发起的确认除外（`transport/null.py:29-34`）。

所以在无界面宿主里，**"ask" 就等于"允许"**，有没有 engine 都一样。在 `workspace-write` 预设（`permissions.py:508-554`）下，这包括：
- 只读白名单以外的 shell 命令；
- 访问未列入名单域名的 `web_fetch`，以及 `web_search`；
- 写入 `.git/`、`.agentao/` 和像凭证文件的路径。这些写入是预设特意设成 ASK 的，即使在这个模式下也一样。

仍然会拦下调用的只有：
- 只读模式；
- engine 的 `DENY`：预设里的 shell 拒绝规则和 `web_fetch` 域名黑名单，**都只在有 engine 时生效**；
- hardline 命令底线；
- MCP Skills 闸门；
- `web_fetch` 自己的 `url_policy` 校验。

**没有 engine 时更弱：**
- 预设里的 DENY 规则一条都不会执行。
- `set_permission_mode()` 抛 `ValueError`（`agent.py:1598-1618`、`runtime/permission_mode.py`）。
- `active_permissions()` 仍然报告 `mode="workspace-write"`，来源是 `default:no-engine`（`agent.py:1036-1058`），但实际上没有任何规则在执行。

**文档怎么写的：**
- 指南里"宿主集成照抄这个"的骨架（`embed-for-agents.md:67-86`）传了 `transport=NullTransport()`，没传 engine。
- 同一份指南 §5 又说默认是 `workspace-write`，并让你用 `agent.set_permission_mode(...)` 设置。在这个骨架上，这个调用会抛异常。
- `embedding.md:193` 只写了默认 transport 是 `NullTransport()`，没说它怎么回答确认。
- 在两份指南和 `host-api.md` 里 grep "approve"、"auto-approve"，找不到任何地方说明 ASK 会变成允许。

`build_from_environment` 会建 engine（`embedding/factory.py:250-256`），但默认 transport 同样是 `NullTransport`，所以这条路径上 ASK 也会被批准。

**这不是运行时的 bug。** ASK 即批准是有记录的决定：CLAUDE.md 写着 "`NullTransport`'s approve-everything stays as the headless-host default"；后台子代理已经在用"ASK 即拒绝"的 transport。确定的缺陷在文档。默认值本身怎么定，由维护者决定。

**可选做法，不互斥：**
- **(a) 只改文档。** 在 `embed-for-agents.md` §1/§5 和 `embedding.md` §2 写明：`NullTransport` 对所有 ASK 都回答"是"。给出默认拒绝的写法 `SdkTransport(confirm_tool=lambda *_: False)`。修改 §5，不再对没有 engine 的骨架承诺 engine 的默认值。不改代码。
- **(b) 默认建 engine。** `permission_engine=None` 时构造 `PermissionEngine(project_root=working_directory)`。engine 不做文件 I/O，所以纯注入仍然没有副作用。效果：预设里的 DENY 规则生效，`set_permission_mode` 可以用，`active_permissions()` 报告的是真实规则。这是行为变更：一些原本允许的调用会被拒绝，来源标签也会变。需要写 CHANGELOG，并更新中英文两份文档。
- **(c) 把无界面默认改成"ASK 即拒绝"。** 这对所有依赖现有行为的无界面宿主都是破坏性的。只有维护者明确决定才做，并且要写迁移说明。

**建议：** 现在做 (a)；(b) 单独提一个 PR；(c) 等维护者决定。

### F2. 流式文本不在契约内

**契约。** `agentao.host` 的 docstring 和 `host-api.md:27` 说，assistant 文本和推理内容只能通过内部的 `Transport` / `AgentEvent` 拿到。而指南 §3 又把 `agentao.transport.AgentEvent` 和 `Transport.emit` 列在"禁止导入"里。

**实际做法。** 聊天类示例都越过了这条线：
- `saas-assistant/app/main.py:33,140-143`、`data-workbench/src/workbench.py`、`batch-scheduler/src/daily_digest.py` 都导入了 `SdkTransport`，其中两个还导入了 `EventType.LLM_TEXT`（`transport/events.py:23`）。
- `saas-assistant` 在每次请求时给池里的 agent 重新赋值 `agent.transport = SdkTransport(...)`（`main.py:143`）。

每个示例都重写了同一套管道：
1. 工作线程上的回调；
2. `loop.call_soon_threadsafe`；
3. `asyncio.Queue`；
4. 消费者；
5. 断开连接时触发 `token.cancel` 的监视器。

**提议：`Agentao.astream(prompt, *, images=None, cancellation_token=None)`。** 它返回一个异步迭代器，产出 `agentao.host` 里公开的一个小的封闭事件联合：
- `TextDelta`；
- 可选的 `ReasoningDelta`；
- 现有的 `ToolLifecycleEvent` / `PermissionDecisionEvent`；
- `TurnFinished(outcome: TurnOutcome)`。

它可以基于已有的 `SdkTransport.subscribe`（`transport/sdk.py:99`）和 `arun()` 实现，不改运行时。关闭迭代器就触发本轮的 token，和取消一个 `arun()` 任务的效果一样。

**代价与待定点：**
- 新事件类型会按快照策略进入 `docs/schema/host.events.v1.json`。
- `host-api.md` 当初是有意不放文本的（载荷大小、工具原始 I/O）。本提议只带文本增量，不带工具原始 I/O，但 assistant 文本该不该进稳定契约，要由维护者决定。

### F3. 导入分散；公开方法的参数类型不公开

**分散。** 一个典型宿主要从 `agentao`、`agentao.embedding`、`agentao.llm`、`agentao.transport`、`agentao.permissions`、`agentao.cancellation`、`agentao.host` 和 `agentao.host.protocols` 导入。示例里按 `from … import` 行数统计，最多的是：

| 导入 | 行数 |
|---|---|
| `agentao`（`Agentao`） | 13 |
| `agentao.embedding` | 6 |
| `agentao.permissions` | 6 |
| `agentao.llm` | 4 |

**缺口：**
- `Agentao.set_permission_mode(mode: PermissionMode)` 是公开方法，但 `PermissionMode` 既不在指南 §3 的稳定清单里，也不在 `host-api.md` 里（grep 无结果）。有三个示例从 `agentao.permissions` 导入它。
- 两个示例从 `agentao.tools.base` 导入 `Tool`，没有用已公开的 `agentao.host.Tool`。
- 两个示例导入了 `agentao.transport.events.EventType`，它属于"禁止导入"一侧。

**提议：**
1. `set_permission_mode` 同时接受模式的字符串值（`"read-only"`、`"workspace-write"`、`"full-access"`、`"plan"`），按 `PermissionMode` 校验，未知字符串抛异常。
2. 把 `PermissionMode` 和 `CancellationToken` 列入文档里的稳定接口。两个模块都很轻：`permissions.py` 只导入标准库和 `permissions_hardline`，`cancellation.py` 只导入标准库。通过 `agentao.host` 的 PEP 562 `__getattr__` 懒导出，`test_import_agentao_host_stays_off_the_runtime_stack`（`tests/test_import_layering.py:477`）仍然能守住分层。
3. F2 落地后，把示例都改成用稳定导入。

待定点：这些名字放在 `agentao.host`（有类型门禁），还是顶层 `agentao`（写起来更短）。

### F4. 没有上下文管理器形式的生命周期

`agent.py` 里没有 `__enter__` / `__exit__` / `__aenter__` / `aclose`（grep 无结果）。每个示例都写 `try/finally: agent.close()`。异步宿主写 `await asyncio.to_thread(agent.close)`，见 `saas-assistant/app/main.py`、`embed-for-agents.md` §2 和 `embedding.md`。

**提议：** 加 `__enter__/__exit__`（调用 `close()`），再加 `aclose()` 和 `__aenter__/__aexit__`。都是纯新增。

待定点：`aclose()` 在哪个线程上执行 `close()`。用 `to_thread` 与指南现在的建议一致；用 `agentao-arun-*` 池会排在正在运行的轮次后面。

### F5. 返回字符串不代表模型回答了

`chat()` / `arun()` 返回 `str`。模型是否真的回答了，要看 `agent.last_turn` 上的 `TurnOutcome.status` / `incomplete_reason`（`runtime/outcome.py:22-37`）。指南 §6.1 专门为此提醒。

**提议：** `chat()` 不改。改返回类型是破坏性的，而且指南已经说明了这一点。F2 的 `TurnFinished` 会把结果和文本一起带回来。

### F6. 别名重复；构造函数参数多

- **别名。** `add_event_observer` / `remove_event_observer`（`agent.py:1010-1016`）是 `add_host_event_observer` / `remove_host_event_observer` 的别名。仓库里还剩一处调用：`cli/run.py:743`。提议：先改掉这处调用，加 `DeprecationWarning`，在之后的某个次版本删除。
- **构造函数。** `Agentao.__init__` 有 32 个参数：5 个位置参数，27 个仅限关键字参数。LLM 有两种配置方式：直接传原始配置参数，或者传 `llm_client=`，二者互斥（`_validate_construction_args`）。**不建议改动：**
  - 仅限关键字已经限制了误用风险；
  - 拆成配置对象会牵动所有文档、示例和测试，却不修复任何缺陷；
  - 指南已经以其中一种写法为主。

## 4. 建议顺序

1. **一个小 PR：** F1(a) 文档、F3(1) 字符串模式、F4 上下文管理器、F6 别名废弃。除文档外都是纯新增。
2. **F1(b) 默认 engine：** 单独一个 PR，含 CHANGELOG，并更新中英文文档。
3. **F2 `astream`：** 先定事件联合和 schema 问题，再实现，然后改示例并完成 F3(3)。
4. **F1(c)：** 只在维护者明确决定后做。

## 5. 有意不提议的

- 把构造函数拆成配置对象（F6）。
- 改 `chat()` 的返回类型（F5）。
- 把目标 / 持续执行循环移进 harness。这仍然是宿主的事（`embed-for-agents.md` §7b；`docs/design/codex-goal-mechanism-review.md` §11）。

## 6. 请维护者决定的问题

1. **F1：** "ASK 即批准"是不是长期的无界面默认？如果不做 (c)，还要不要做 (b)？
2. **F2：** assistant 文本要不要进稳定契约？用哪个 schema 版本？
3. **F3：** 懒导出放在 `agentao.host` 还是顶层 `agentao`？
