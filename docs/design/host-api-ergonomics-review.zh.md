# 宿主 API 易用性评审：嵌入能不能更简单？

**状态：** 评审，2026-10-06。**2026-10-06 已决定：** F1 走 (a) 路线，只改文档（见 §3 F1 的*决定*）；流式文本进入稳定契约（F2）；新增稳定类型只从 `agentao.host` 导出（F3）。实施尚未获批，本文内容都还没有实现。证据引用自 `main` @ `2750e16`。**2026-10-06 按评审意见修订：** F2 收缩为最小的 `astream` 并写明生命周期约束，`saas-assistant` 替换 transport 的写法记为缺陷；F4 去掉线程池选项；F6 暂缓；§4 重新排序。**按复审意见第二次修订：** F2 的关闭顺序改为先解除待处理的队列写入，提前退出必须用 `aclosing`，并按 token 身份把流绑定到本轮。
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
| F1 | 无界面宿主会批准所有 ASK，文档没说；没有 engine 的 agent 也切换不了模式 | **已决定：(a) 只改文档。** 不采用 (b) 和 (c) | 无影响 |
| F2 | 流式文本不在契约内；所有聊天类示例都导入了内部接口，`saas-assistant` 每次请求替换 transport，会把事件送错地方 | 最小的 `Agentao.astream()`：只有 `TextDelta` + `TurnFinished`，通过订阅接入 | 纯新增；审计 schema 不变 |
| F3 | 导入分散在 8 个模块；`set_permission_mode` 的参数类型不公开；示例里有错误导入 | 修正示例导入；接受字符串模式；在 `agentao.host` 公开 `PermissionMode` / `CancellationToken` | 纯新增 |
| F4 | 不支持 `with` / `async with`，每个宿主都写 `try/finally close()` | `__enter__/__exit__`；`aclose()` 即 `asyncio.to_thread(close)`；宿主先结束自己的轮次 | 纯新增 |
| F5 | `chat()` 返回字符串不代表模型真的回答了 | 由 F2 的结束事件覆盖；`chat()` 不改 | 不适用 |
| F6 | 观察者别名重复；构造函数 32 个参数 | **暂缓**；构造函数不动 | 不适用 |

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

**决定（维护者，2026-10-06）：走 (a) 路线。** 只改文档，运行时保持现状：
- `NullTransport` 仍然批准所有 ASK；
- 没有 engine 的 `Agentao(...)` 不会自动建默认 engine；
- 没有 engine 时 `set_permission_mode()` 仍然抛 `ValueError`。

(a) 要交付的内容：
- `embed-for-agents.md` §1、§5 和 `embedding.md` §2 写明：`NullTransport` 对所有 ASK 都回答"是"；并列出仍会拦下调用的机制（即上面那几条）。
- 在骨架旁边给出默认拒绝的写法 `SdkTransport(confirm_tool=lambda *_: False)`。
- §5 不再对没有 engine 的骨架承诺 `workspace-write` 默认值和能用的 `set_permission_mode`。改为说明：宿主如果需要权限模式或预设里的 DENY 规则，应传入 `permission_engine=PermissionEngine(project_root=...)`，或者使用 `build_from_environment`。
- 说明 `active_permissions()` 的 `default:no-engine` 来源表示"没有任何规则在执行"。
- 凡是有中英文双版本的文档都同步修改；同时检查各示例的 README 里有没有同样的承诺。

不采用 (b) 和 (c)。如果以后要重新考虑其中任何一项，需要在这里记录新的决定。

### F2. 流式文本不在契约内

**契约。** `agentao.host` 的 docstring 和 `host-api.md:27` 说，assistant 文本和推理内容只能通过内部的 `Transport` / `AgentEvent` 拿到。而指南 §3 又把 `agentao.transport.AgentEvent` 和 `Transport.emit` 列在"禁止导入"里。

**实际做法。** 聊天类示例都越过了这条线。`saas-assistant/app/main.py`、`data-workbench/src/workbench.py`、`batch-scheduler/src/daily_digest.py` 都导入了 `SdkTransport`，后两个还导入了 `EventType.LLM_TEXT`（`transport/events.py:23`）。每个示例都重写了同一套管道：
1. 工作线程上的回调；
2. `loop.call_soon_threadsafe`；
3. `asyncio.Queue`；
4. 消费者；
5. 断开连接时触发 `token.cancel` 的监视器。

**`saas-assistant` 每次请求替换 transport 的写法是错的，不只是用了内部接口。** 它在每次请求时给池里的 agent 赋值 `agent.transport = SdkTransport(...)`（`main.py:143`）。这是在核实评审第 1 条时发现的。
- `agent.transport` 只是引用之一。工具执行器持有自己的引用（`runtime/tool_runner.py:80`）：`TOOL_CONFIRMATION` 由它发出，`confirm_tool` 由它调用（`:344-349`），`TOOL_START` 也经它发出（`runtime/tool_executor.py:301`）。replay 安装时会**同时**替换这两处（`replay/manager.py:104-107`），而示例只换了一处。结果是：它的 SSE 流收不到任何工具事件，工具确认仍然发给构造 agent 时的那个 transport。
- 开启 replay 时，这次替换会把 `ReplayAdapter` 从 `agent.transport` 上摘掉。这一轮的 LLM 事件不再被记录，工具事件却仍被记录。
- 替换发生在 `async with lock` 之前（`main.py:143` 对比 `:151`）。同一个会话键上的第二个请求，会把第一轮的事件改送进第二个请求的队列。
- 它通过 `asyncio.to_thread` 执行 `agent.chat`，也就是用了事件循环的默认线程池，而 `arun()` 特意避开了它（`agent.py:64`，`_get_arun_pool`）。

所以这里的任何设计都不能要求、也不能鼓励替换 transport。

**提议：最小的 `Agentao.astream(prompt, *, images=None, cancellation_token=None)`。** 已按评审收缩范围。
- **首版事件：** 只有 `TextDelta(text)` 和 `TurnFinished(outcome: TurnOutcome)`。工具和权限事件继续用现有的 `events()`。推理内容等有宿主提出需求再加。
- **不进审计 schema。** 这两个类型放在 `agentao.host`，但不属于 `HostEvent` 联合，不投影进 replay，也不进 `docs/schema/host.events.v1.json`。文本已经通过内部事件流进入 replay；`astream` 是一个投递接口，不是新的审计记录。
- **通过订阅现有 transport 接入，绝不替换它。** `Transport.subscribe` 是可选的：实现"可以省略这个方法；使用方应先 `getattr(transport, "subscribe", None)`"（`transport/base.py:40-49`）。`NullTransport`、`SdkTransport`、ACP 的 transport 和 `ReplayAdapter` 都实现了它；`ReplayAdapter` 会转发给内层 transport，内层没有时返回一个空操作（`replay/adapter.py:231-244`）。由此有两点：
  - 如果当前 transport 没有 `subscribe`，`astream` 在本轮**开始之前**抛 `TypeError`，并写明 transport 的类名。不退回到替换 transport 的做法，因为替换会改变由谁回答确认、replay 记录什么。
  - 只检查属性是否存在还不够。`ReplayAdapter` 总是有 `subscribe`，但内层没有时它返回的空操作看起来和真的取消订阅函数一样，事件却永远不会到达。检查必须落到内层 transport 上：要么 `astream` 拆开 adapter 去看，要么 adapter 报告自己是否转发了。具体选哪种在实现时决定；无论哪种，这种情况都按上一条同样拒绝。
- **收到谁的文本。** 子代理用的是各自的 transport（`agents/tools/_wrapper.py:614-636`），所以在父 agent 的 transport 上订阅，只会收到父 agent 自己的文本。
- **生命周期约束：**
  - *不重叠运行：* 已经强制执行。`run_turn` 以不等待的方式获取 `agent._turn_lock`，拿不到就抛 `TurnInProgressError`（`runtime/turn.py:72-100`）。`astream` 继承这一点，但拒绝发生在**工作线程开始执行本轮时**，而不是调用 `astream` 时。`arun()` 要经过 `agentao-arun-*` 线程池，池子忙时，第二个请求可能先等一个空闲线程，然后才失败。
  - *只绑定本轮：* 光靠轮次锁，不能保证被拒绝的请求看不到其他轮次的文本。`astream` 在本轮开始前就已订阅，如果不加过滤，第二个流可能先收到第一轮的文本，之后才被拒绝。事件本身不带轮次 id，所以按 token 身份绑定：
    - `astream` 总是为本轮新建自己的 `CancellationToken`。调用方传入的 token 通过 `add_done_callback`（`cancellation.py:102`）关联到它，流结束时解除关联；调用方的 token 从不直接用作本轮的 token。否则同一个 token 被两次调用共用时，会同时匹配两轮。
    - 监听器只在 `agent._current_token is` 这个 token 时才转发事件。`run_turn` 在拿到轮次锁之后才设置 `_current_token`（`runtime/turn.py:139`），结束时清空（`:341`）。监听器在生产者线程上被同步调用（`SdkTransport.emit`，`transport/sdk.py:91-97`），所以检查时看到的正是正在发事件的那一轮。
    - 被 `TurnInProgressError` 拒绝的请求，它的 token 从未被设置为当前 token，所以不会交付任何其他轮次的数据。
  - *队列：* 有界，容量和满队列规则都与 `events()` 相同（`host/events.py:60`；队列满时生产者等待）。消费者停止读取会拖慢本轮，而不是让内存增长。代价是：流关闭时，生产者可能正**阻塞在队列写入里**，下面的关闭顺序必须处理这种情况。
  - *异常：* 本轮抛出的异常，在已经入队的事件交付完之后，由迭代器抛出。只有本轮正常返回时才产出 `TurnFinished`，包括 `chat()` 正常返回的 `status="error"` / `"cancelled"` 结果。
  - *提前关闭*（`aclose()`、任务被取消），按以下顺序：
    1. **标记流已关闭，并解除待处理的队列写入。** 在流的锁内设置 `closed`，并取消所有待处理的写入。此后监听器直接丢弃事件，不再写入。`EventStream` 已经用这套机制解决了同样的卡死问题（`host/events.py:76-81`、`:296-320`）：复用它的订阅者机制或沿用同样的模式即可，不新增调度层。
    2. **触发本轮的 token。**
    3. **有时限地等待本轮清理完成**，做法与 `arun` 相同（`_await_turn_cleanup`，`agent.py:101`）。
    4. **无论发生什么，都在 `finally` 里取消订阅。**

    顺序很重要。在满队列上阻塞的生产者卡在队列写入里，而不是在 token 检查点上；如果先触发 token，工作线程仍会卡住，第 3 步就会一直等它。取消订阅只是移除监听器，解除不了已经在进行的写入。漏掉第 4 步则订阅会泄漏，因为 transport 强引用监听器（`transport/base.py:51-56`）。
  - *`break` 不等于关闭。* 用 `break` 跳出 `async for` 不会执行异步生成器的 `finally`。在 CPython 上实测，它直到事件循环关闭时（`asyncio.run` 关闭异步生成器那一步）才执行。在此之前本轮会继续运行，生产者也可能卡在满队列上。所以 API 文档必须要求：宿主提前退出时显式关闭流：

    ```python
    from contextlib import aclosing  # Python 3.10+

    async with aclosing(agent.astream(prompt)) as stream:
        async for ev in stream:
            if isinstance(ev, TextDelta):
                send(ev.text)
            if should_stop():
                break  # 退出时 aclosing 会调用 aclose()
    ```
- **实现位置：** 在运行时之上，即 `arun()` 加一个订阅，chat 循环不改。

**决定（维护者，2026-10-06）：流式文本进入稳定契约**，形式就是上面的最小版本：通过 `astream` 提供 `TextDelta` + `TurnFinished`，不进审计 schema。这改变了 `host-api.md` 原先不放 assistant 文本的做法，但只限文本增量；工具原始 I/O 仍不放。`astream` 落地时，同步更新 `host-api.md`（包括 `:27` 的范围说明）和 `agentao.host` 的 docstring。

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
- 两个示例导入了 `agentao.transport.events.EventType`，它属于"禁止导入"一侧。这两处要等 F2 落地后才能改。

**提议：**
0. 已经有稳定出处的导入现在就改（`Tool` 改从 `agentao.host` 导入），与 F1 的文档修改一起做。
1. `set_permission_mode` 同时接受模式的字符串值（`"read-only"`、`"workspace-write"`、`"full-access"`、`"plan"`），按 `PermissionMode` 校验，未知字符串抛异常。
2. 把 `PermissionMode` 和 `CancellationToken` 列入文档里的稳定接口。两个模块都很轻：`permissions.py` 只导入标准库和 `permissions_hardline`，`cancellation.py` 只导入标准库。通过 `agentao.host` 的 PEP 562 `__getattr__` 懒导出，`test_import_agentao_host_stays_off_the_runtime_stack`（`tests/test_import_layering.py:477`）仍然能守住分层。
3. F2 落地后，把示例都改成用稳定导入。

**决定（维护者，2026-10-06）：新增的稳定类型只从 `agentao.host` 导出**，因为它有类型门禁。不另加顶层出口。

### F4. 没有上下文管理器形式的生命周期

`agent.py` 里没有 `__enter__` / `__exit__` / `__aenter__` / `aclose`（grep 无结果）。每个示例都写 `try/finally: agent.close()`。异步宿主写 `await asyncio.to_thread(agent.close)`，见 `saas-assistant/app/main.py`、`embed-for-agents.md` §2 和 `embedding.md`。

**提议：** 加 `__enter__/__exit__`（调用 `close()`），再加 `aclose()` 和 `__aenter__/__aexit__`。都是纯新增。
- `aclose()` 就是 `await asyncio.to_thread(self.close)`，即指南现在推荐的写法，不新增调度机制。
- 前提写进文档，不在代码里强制：宿主关闭前先结束自己正在运行的轮次。
- 不用 `agentao-arun-*` 线程池。早先的草稿说放进这个池会"排在正在运行的轮次后面"，这是错的：共享的多线程池不保证这种顺序，只要有空闲线程，`close()` 就会立即和本轮并行执行。

### F5. 返回字符串不代表模型回答了

`chat()` / `arun()` 返回 `str`。模型是否真的回答了，要看 `agent.last_turn` 上的 `TurnOutcome.status` / `incomplete_reason`（`runtime/outcome.py:22-37`）。指南 §6.1 专门为此提醒。

**提议：** `chat()` 不改。改返回类型是破坏性的，而且指南已经说明了这一点。F2 的 `TurnFinished` 会把结果和文本一起带回来。

### F6. 别名重复；构造函数参数多

- **别名。** `add_event_observer` / `remove_event_observer`（`agent.py:1010-1016`）是 `add_host_event_observer` / `remove_host_event_observer` 的别名。仓库里还剩一处调用：`cli/run.py:743`。**暂缓。** 收益有限，而且先废弃再删除公开名字不算纯新增，不该放进"纯新增"的 PR。以后如果要做：先改掉这处调用，加 `DeprecationWarning`，在之后的某个次版本删除。
- **构造函数。** `Agentao.__init__` 有 32 个参数：5 个位置参数，27 个仅限关键字参数。LLM 有两种配置方式：直接传原始配置参数，或者传 `llm_client=`，二者互斥（`_validate_construction_args`）。**不建议改动：**
  - 仅限关键字已经限制了误用风险；
  - 拆成配置对象会牵动所有文档、示例和测试，却不修复任何缺陷；
  - 指南已经以其中一种写法为主。

## 4. 建议顺序

已按评审修订。每一步单独一个 PR。

1. **F1(a) 文档，加上示例里现有的错误导入**（F3 第 0 步）。只改文档和示例。
2. **字符串形式的权限模式，加上从 `agentao.host` 稳定导出 `PermissionMode` / `CancellationToken`**（F3 第 1–2 步）。纯新增。
3. **最小的 `astream`**（F2）：`TextDelta` + `TurnFinished`，通过订阅接入，遵守上面的生命周期约束。然后把示例从 `SdkTransport` / `EventType` 上移走，并修好 `saas-assistant` 的替换写法（F3 第 3 步）。

F4 可以并入第 2 步，也可以单独做；它很小，而且是纯新增。F6 暂缓。

## 5. 有意不提议的

- 把构造函数拆成配置对象（F6）。
- 改 `chat()` 的返回类型（F5）。
- 暂不废弃观察者别名（F6）。
- 把目标 / 持续执行循环移进 harness。这仍然是宿主的事（`embed-for-agents.md` §7b；`docs/design/codex-goal-mechanism-review.md` §11）。

## 6. 请维护者决定的问题

1. ~~**F1：** "ASK 即批准"是不是长期的无界面默认？如果不做 (c)，还要不要做 (b)？~~ **已于 2026-10-06 答复：走 (a) 路线。** "ASK 即批准"继续作为无界面默认，也不加默认 engine。
2. ~~**F2：** assistant 文本要不要进稳定契约？~~ **已于 2026-10-06 答复：进**，形式为 `TextDelta` + `TurnFinished`，不进审计 schema。
3. ~~**F3：** 懒导出放在 `agentao.host` 还是顶层 `agentao`？~~ **已于 2026-10-06 答复：只放在 `agentao.host`。**
