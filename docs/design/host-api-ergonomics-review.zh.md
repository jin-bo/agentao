# 宿主 API 易用性评审：嵌入能不能更简单？

**状态：** 评审，2026-10-06。**2026-10-06 已决定：** F1 走 (a) 路线，只改文档（见 §3 F1 的*决定*）；流式文本进入稳定契约（F2）；新增稳定类型只从 `agentao.host` 导出（F3）。F1(a) 和 F3 第 0 步**已在 PR #423 实施**（只改文档和示例导入）；F4 已实现（2026-10-07，见 F4 的*已完成*）；第 2 步（字符串模式与导出）、第 3 步（`astream`）和第 4 步（F8）都已合并，见本行末尾。证据引用自 `main` @ `2750e16`。**2026-10-06 按评审意见修订：** F2 收缩为最小的 `astream` 并写明生命周期约束，`saas-assistant` 替换 transport 的写法记为缺陷；F4 去掉线程池选项；F6 暂缓；§4 重新排序。**按复审意见第二次修订：** F2 的关闭顺序改为先解除待处理的队列写入，提前退出必须用 `aclosing`，并按 token 身份把流绑定到本轮。**第三次修订（2026-10-06），收窄导出：** 不导出 `PermissionMode`，去掉 `TurnFinished`；新增导出为 `CancellationToken`、`TextDelta`、`TurnOutcome`；已决定 `Agentao(permission_mode=...)`。新增 F7：保留所有导出，指南分层介绍，加 `__dir__`。**第 2 步已实现（2026-10-06）：** 静默启动，构造时不接受 `"plan"`，replay 起始姿态留作后续；已作为 PR #426 合并。**同类对照（2026-10-06）：** §7 评审了一份参照 Pydantic AI 和 Strands 的建议。据此给 F2 加上“增量用于显示，结果以 outcome 为准”的规则，新增 F8（宿主自身方法的返回类型标注）和 F9（函数工具适配器，排在 `astream` 之后，按需做）。评审还发现，只做审批的宿主已经可以用 `SdkTransport(confirm_tool=...)`，并扩充了 §5。这次对照新增的内容（F2 的两条新要点、F8、F9、§7）引用的是 `main` @ `ef8a2d2`；其余内容仍引用 `2750e16`，那里 `agent.py` 的行号更小，例如 `active_permissions()` 在 `2750e16` 是 `:1036`，在 `ef8a2d2` 是 `:1086`。**第 3 步已实现（2026-10-06）：** 按 F2 的设计实现 `astream`，另加 `max_iterations=`；见 F2 的*实现中做出的决定*。已作为 PR #428 合并。**第 4 步已实现（2026-10-06）：** F8 的类型标注，并把类型门禁扩展到宿主对这些方法的使用；见 F8 的*实现中做出的决定*。已作为 PR #432 合并。**F7 的指南分层已完成（2026-10-07）：** 只改文档；见 F7 的*已完成*。**F6 的别名已废弃（2026-10-07）：** 见 F6 的*已完成*；构造函数部分保持不动。
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
| F2 | 流式文本不在契约内；所有聊天类示例都导入了内部接口，`saas-assistant` 每次请求替换 transport，会把事件送错地方 | 最小的 `Agentao.astream()`：先产出 `TextDelta`，最后产出 `TurnOutcome`；通过订阅接入；增量用于显示，`TurnOutcome.text` 才是结果 | 纯新增；审计 schema 不变 |
| F3 | 导入分散在 8 个模块；`set_permission_mode` 的参数类型不公开；示例里有错误导入 | 修正示例导入；字符串模式（不导出枚举）；`Agentao(permission_mode=...)`；从 `agentao.host` 导出 `CancellationToken` | 纯新增 |
| F4 | 不支持 `with` / `async with`，每个宿主都写 `try/finally close()` | `__enter__/__exit__`；`aclose()` 即 `asyncio.to_thread(close)`；宿主先结束自己的轮次。**2026-10-07 已完成**；`aclose()` 最终在自己的线程上运行（见 F4 的*已完成*） | 纯新增 |
| F5 | `chat()` 返回字符串不代表模型真的回答了 | 由 F2 的结束事件覆盖；`chat()` 不改 | 不适用 |
| F6 | 观察者别名重复；构造函数 32 个参数 | 别名**已于 2026-10-07 废弃**（在之后的次版本删除）；构造函数不动 | 只发警告；删除属于破坏性变更 |
| F7 | 指南把 `agentao.host` 写成扁平且不完整的一行；`dir()` 看不到懒导出的工具类型 | 保留所有导出；指南按层次介绍；加 `__dir__` | 纯新增 |
| F8 | 指南最先介绍的两个宿主方法 `events()` 和 `active_permissions()` 没有返回类型标注 | 给 `Agentao` 面向宿主的方法补标注 | 纯新增 |
| F9 | 宿主工具哪怕只是一个普通函数，也得写一个类 | 一个薄的函数 → `Tool` / `AsyncToolBase` 适配器，排在 `astream` 之后，按需做 | 纯新增 |

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

**这不是运行时的 bug。** ASK 即批准是有记录的决定：CLAUDE.md 写着 "`NullTransport`'s approve-everything stays as the headless-host default"；后台子代理已经在用"ASK 即拒绝"的 transport。确定的缺陷在文档。默认值本身怎么定，由维护者决定，决定见下文（*决定*：走 (a) 路线）。

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

**已在 PR #423 实施。** 除上面列出的各项外，还修正了本评审没有列出的两处说法：`embedding.md` §2 说 `permission_engine`"默认是一个宽松的引擎"（实际默认是 `None`）；开发者指南的构造函数参考（中英文）把工厂建的引擎写成了 `Agentao(...)` 的默认值。开发者指南本来就写明了 `NullTransport` 会自动批准，缺口在 `docs/guides/`。上面"文档怎么写的"几条描述的是那个 PR 之前的文档。

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
- **首版产出项：** 先是 `TextDelta(text)`，最后是本轮的 `TurnOutcome`，没有别的。工具和权限事件继续用现有的 `events()`。推理内容等有宿主提出需求再加。
- **最后一项直接是 `TurnOutcome`**（2026-10-06 修订；原来是只有一个字段的 `TurnFinished(outcome)` 包装）。迭代器产出 `TextDelta | TurnOutcome`；用 `isinstance` 就能区分，不需要事件基类，也不需要另一套 schema。
- **前提：`TurnOutcome` 必须能轻量导入。** 实测 `from agentao import TurnOutcome` 会加载 `agentao.runtime.chat_loop` 和 `agentao.llm.client`。这个类本身（`runtime/outcome.py`）只导入 `dataclasses` 和 `typing`；开销来自 `agentao/runtime/__init__.py`，它会立即导入 `chat_loop`、`llm_call`、`tool_runner` 和 `turn`。懒导出避不开这一点，因为导入 `agentao.runtime.outcome` 总会先执行包的 `__init__`。所以要把定义移到一个轻量模块，`agentao.runtime.outcome` 和顶层 `agentao` 继续重新导出**同一个类**，身份比较和现有导入都不受影响。这样 `agentao/__init__.py` 注释里说的"不用加载 LLM 栈就能导入"才真正成立；目前并不成立。
- **不进审计 schema。** `TextDelta` 和 `TurnOutcome` 从 `agentao.host` 导出，但不属于 `HostEvent` 联合，不投影进 replay，也不进 `docs/schema/host.events.v1.json`。文本已经通过内部事件流进入 replay；`astream` 是一个投递接口，不是新的审计记录。
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
  - *异常：* 本轮抛出的异常，在已经入队的事件交付完之后，由迭代器抛出。只有本轮正常返回时才产出 `TurnOutcome`，作为最后一项，包括 `chat()` 正常返回的无答案结果和 `status="cancelled"` 结果。（第 3 步评审时改正：`status="error"` 的一轮会抛异常，所以不产出结果；`last_turn` 会记下它。）
  - *提前关闭*（`aclose()`、任务被取消），按以下顺序：
    1. **标记流已关闭，并解除待处理的队列写入。** 在流的锁内设置 `closed`，并取消所有待处理的写入。此后监听器直接丢弃事件，不再写入。`EventStream` 已经用这套机制解决了同样的卡死问题（`host/events.py:76-81`、`:296-320`）：复用它的订阅者机制或沿用同样的模式即可，不新增调度层。
    2. **触发本轮的 token。**
    3. **有时限地等待本轮清理完成**，做法与 `arun` 相同（`_await_turn_cleanup`，`agent.py:101`）。
    4. **无论发生什么，都在 `finally` 里取消订阅。**

    顺序很重要。在满队列上阻塞的生产者卡在队列写入里，而不是在 token 检查点上；如果先触发 token，工作线程仍会卡住，第 3 步就会一直等它。取消订阅只是移除监听器，解除不了已经在进行的写入。漏掉第 4 步则订阅会泄漏，因为 transport 强引用监听器（`transport/base.py:51-56`）。
  - *`break` 不等于关闭。* 用 `break` 跳出 `async for` 不会执行异步生成器的 `finally`。在 CPython 上实测，只要还有变量持有这个生成器，它直到事件循环关闭时（`asyncio.run` 关闭异步生成器那一步）才执行；没有任何引用的生成器在被垃圾回收时就会关闭（第 3 步评审时重新实测）。在此之前本轮会继续运行，生产者也可能卡在满队列上。所以 API 文档必须要求：宿主提前退出时显式关闭流：

    ```python
    from contextlib import aclosing  # Python 3.10+

    async with aclosing(agent.astream(prompt)) as stream:
        async for ev in stream:
            if isinstance(ev, TextDelta):
                send(ev.text)
            if should_stop():
                break  # 退出时 aclosing 会调用 aclose()
    ```
- **增量用于显示，结果以 outcome 为准**（2026-10-06 新增，见 §7 同类对照）。所有 `TextDelta` 拼起来**不保证**等于 `TurnOutcome.text`，文档必须写明。本轮里**每一次** LLM 调用都会逐块发出 `LLM_TEXT`（`runtime/llm_call.py:147-152`），包括以工具调用结束的那次，所以“我先看一下文件”这类说明文字会作为增量到达，却不在最终文本里。反过来也成立：`TurnOutcome.text` 可能是没有任何增量带过的字符串，例如 `[No response]` 占位、harness 的中止说明或 `[LLM API error: …]`（`outcome.py:3-7`）。宿主边收边显示增量；要保存或据以行动的结果取 `TurnOutcome.text`，并先用 `is_answer` 检查。重试不会让文本重复：只有在还没显示任何内容时才重试（`llm_call.py:143-144`、`:155-157`）。
- **为什么用 `aclosing`，而不是原生的 `async with`。** Pydantic AI 的 `run_stream_events()` 本身就是异步上下文管理器，流以 `AgentRunResultEvent` 结束（2026-10-06 已对照其文档核实）。`astream` 用标准库的 `contextlib.aclosing` 包住异步生成器，得到同样有作用域的生命周期，不需要第二种对象类型；最后的 `TurnOutcome` 起的就是 Pydantic 结果事件的作用。以后若要返回一个既是异步迭代器、又是异步上下文管理器的对象，仍可作为纯新增的改动加入。
- **实现位置：** 在运行时之上，即 `arun()` 加一个订阅，chat 循环不改。

**实现中做出的决定（2026-10-06）：**
- **`TurnOutcome` 放在 `agentao/outcome.py`**，这是一个只依赖标准库的模块，与 `cancellation.py` 并列，并列入导入分层测试的叶子模块清单。`agentao.runtime.outcome` 和顶层 `agentao` 重新导出同一个类。`TextDelta` 在 `agentao/host/stream.py`。流本身在 `agentao/runtime/astream.py`；`Agentao.astream` 是普通方法，先检查 transport，再返回异步生成器，所以没有 `subscribe()` 的 transport 在调用时就抛 `TypeError`。
- **结果在 `TURN_END` 时截取，而不是事后读 `last_turn`。** `run_turn` 在 token 仍然装着、锁仍然持有时设置 `_last_turn_outcome` 并发出 `TURN_END`，所以监听器拿到的是本轮的结果。在 `arun()` 返回后再读 `last_turn`，在共用的 agent 上会与后来的轮次竞争，正是 §7 指出的情形。
- **`ReplayAdapter` 会被拆开**，找到真正投递事件的 transport，流订阅在那里。
- **按 context variable 绑定本轮，而不是 `agent._current_token`**（评审中改动）。两个 agent 可以共用一个 transport，这时另一个 agent 的事件会在本 agent 的 token 装着时到达监听器；实测它的文本混进了流里。现在 `run_turn` 从 `TURN_BEGIN` 之前到 `TURN_END` 之后，把本轮以 `(agent, token)` 绑定在 `cancellation._CURRENT_TURN` 上，监听器在发出事件的线程上下文里比较 `current_turn()`：两半都要比，因为宿主可能把同一个 token 传给另一个 agent 的嵌套轮次。它与工具调用用的 `_CURRENT_TOKEN` 分开，后者含义不变。
- **在宿主事件循环自己的线程上发出事件时，安排队列写入而不阻塞**（评审中发现，实测卡死）。宿主异步工具的协程带着本轮的上下文运行在宿主事件循环上；如果它通过 transport 发出文本，阻塞写入会等待正被它阻塞的那个循环。最后的排空会等这些安排好的写入完成。与 `EventStream.publish` 的规则相同。
- **关闭时取消 `arun` 任务**：先释放待处理的队列写入，再取消任务；任务会触发 token，并执行 `arun` 自己的有界清理等待。这一步同时完成了关闭顺序的第 2、3 步。
- **新增 `max_iterations=`**，仅限关键字参数，含义和默认值与 `arun` 相同。改用 `astream` 的示例都传了它；不加的话，`astream` 会成为唯一不支持它的入口。
- **示例已迁移。** `saas-assistant` 不再替换 transport：它在每个会话的锁里流式转发 `astream()` 的增量，SSE 载荷保持不变（`llm_text` / `chunk`；`done` 带 `reply`，另加 `status`、`is_answer` 和 `incomplete_reason`）。工具事件不再在这条流里，它们在 `agent.events()` 上。`data-workbench` 和 `batch-scheduler` 改为通过 `astream()` 读取文本，不再用 `EventType.LLM_TEXT`。

**决定（维护者，2026-10-06）：流式文本进入稳定契约**，形式就是上面的最小版本：`astream` 先产出 `TextDelta`，最后产出 `TurnOutcome`，不进审计 schema。这改变了 `host-api.md` 原先不放 assistant 文本的做法，但只限文本增量；工具原始 I/O 仍不放。`astream` 落地时，凡是说文本不在契约内的地方都要同步更新：`host-api.md`（`:27` 的范围说明）、`agentao.host` 的 docstring，以及 `docs/design/embedded-host-contract.md:28-31`。

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
- 两个示例导入了 `agentao.transport.events.EventType`，它属于"禁止导入"一侧。这两处要等 F2 落地后才能改。（已在第 3 步改掉。）

**提议：**
0. 已经有稳定出处的导入现在就改（`Tool` 改从 `agentao.host` 导入），与 F1 的文档修改一起做。
1. **公开入口统一用字符串表示模式。** `set_permission_mode` 同时接受 `"read-only"`、`"workspace-write"`、`"full-access"`、`"plan"`，与 `ActivePermissions.mode` 和 `PermissionDecisionEvent.mode` 的取值相同（都是 `Literal[...]`，`host/models.py:58`、`:167`）。入口先校验字符串，再转换成内部枚举；未知字符串抛异常。原来传枚举的写法照样能用。**不导出 `PermissionMode`**（2026-10-06 修订）：导出它会让公开契约里同一个值有两种写法。
   - **返回值不变，文档必须如实说明：** `set_permission_mode()` 仍然返回切换前的模式，类型是内部枚举 `PermissionMode`（`Optional[PermissionMode]`），不是字符串。文档要写明这一点，不能声称"统一用字符串"；改返回值本身就是一项兼容性变更，这里不做。
2. **`Agentao(permission_mode=...)`**——2026-10-06 已决定（见下面的*构造时设定权限姿态*）。只需要模式的宿主不必导入任何权限相关的东西。
3. **从 `agentao.host` 导出 `CancellationToken`。** `cancellation.py` 只导入标准库，所以在 `agentao/host/__init__.py` 里直接导入，`import agentao.host` 仍然不会加载运行时（`tests/test_import_layering.py:477`）。为什么需要它：简单的异步调用可以通过取消任务来结束本轮，`arun()` 已经会转发这个取消；其他场景（独立的停止按钮、跨任务取消、多个调用共用一个取消信号、同步宿主从别的线程取消 `chat()`）需要显式传 token。
4. ~~F2 落地后，把示例都改成用稳定导入。~~ 已在第 3 步完成。

**构造时设定权限姿态——2026-10-06 已决定：`Agentao(permission_mode=...)`。** 这不是 F1 里被否决的 (b)：宿主不传这个参数时，什么都不变。
- **默认 `permission_mode=None`：** 不创建引擎，与现在完全相同（F1 的决定不变）。
- **显式传入模式时：** 在构造任何东西之前校验 `"read-only"`、`"workspace-write"` 或 `"full-access"`，再创建引擎 `PermissionEngine(project_root=working_directory, rules=[])`。不隐式加载任何权限文件；需要 `~/.agentao/permissions.json` 的宿主用 `build_from_environment`，或者自己构造引擎。**不接受 `"plan"`**（2026-10-06 已决定，见下文）。
- **agent 静默地以该模式启动**（2026-10-06 修订；初稿是通过 `apply_permission_mode` 应用并带 `cause="host-init"`）。`runtime/permission_mode.py::_set_initial_permission_mode` 在 `_wire_tooling` 之后直接设置引擎的预设和工具执行器的只读标志，不发任何事件——既不发 `PERMISSION_MODE_CHANGED`，也不发 `READONLY_MODE_CHANGED`。起始状态不是一次切换：发事件会凭空造出一次从 `workspace-write` 的转换，会在构造函数返回之前就到达宿主的 transport，而且仍然赶不上只能稍后才开始的 replay。构造之后的每次 `set_permission_mode()` 照旧记录。
- **构造阶段的辅助函数改为私有**（2026-10-07 决定）：`_parse_permission_mode`、`_parse_construction_permission_mode`、`_CONSTRUCTION_PERMISSION_MODES` 和 `_set_initial_permission_mode`。它们是参数处理和只供构造路径使用的状态写入，不是宿主能力；宿主把模式作为字符串传给 `Agentao(permission_mode=)`、`build_from_environment(permission_mode=)` 或 `set_permission_mode()`。改名不保留旧名别名：它们从未进入任何发布版（在 v0.5.11 之后才加入）。`apply_permission_mode` 保持原名，它是 CLI、ACP 和 `Agentao` 共用的运行时入口。
- **2026-10-06 已决定：同时传入 `permission_mode=` 和 `permission_engine=` 时抛 `ValueError`**，这样两者之间就没有需要定义和说明的优先级规则。与 `llm_client=` 和原始 LLM 配置互斥的规则一致。自带引擎的宿主在引擎上设好模式，或者构造后再调 `set_permission_mode`。
- **实现中做出的决定（2026-10-06）：**
  - **`permission_mode=` 不接受 `"plan"`**，`Agentao()` 和 `build_from_environment()` 都一样，并且在打开任何资源之前就拒绝。PLAN 预设和 `PlanSession` 是两套状态，只设预设会让模型被拒绝，却没有 plan 提示告诉它正在规划。内部的 `PLAN` 模式和现有的枚举调用不变，`set_permission_mode("plan")` 仍然接受；这次不新增进入 plan 模式的接口。
  - **对调用方只有一条规则：你传入的引擎与模式互斥。** `build_from_environment(permission_mode=)` 把模式应用到工厂从权限文件加载的那个引擎上——那是工厂内部的引擎，不是调用方传入的——所以用户的规则保留，同样静默启动。这是第二轮审查发现的：原来直接转发这个参数，会让工厂总是报错。
  - **参数类型保持 `str`**（也接受枚举），在运行时校验。宿主通常从配置、请求或环境变量拿到模式，要求 `Literal` 会让它们先做类型收窄。
  - **后续单独做：把起始权限姿态写进 replay。** 在开始录制时，把当时的权限姿态加进 `session_started`，这样 replay 记录的是实际的起始状态，也能覆盖构造后、`start_replay()` 之前改了模式的情况。现在 `session_started` 只有 `session_id`、`cwd` 和 `model`，所以任何 agent 的起始姿态都进不了 replay，注入引擎的也一样。
- `PermissionEngine` 仍然不进 `agentao.host`（`host/__init__.py:23-24`、`host-api.md:10-11`）。需要 `rules=` 的宿主仍从 `agentao.permissions` 构造它，见 `embed-for-agents.md` §1。

**决定（维护者，2026-10-06）：新增的稳定类型只从 `agentao.host` 导出**，因为它有类型门禁。不另加顶层出口。

### F4. 没有上下文管理器形式的生命周期

`agent.py` 里没有 `__enter__` / `__exit__` / `__aenter__` / `aclose`（grep 无结果）。每个示例都写 `try/finally: agent.close()`。异步宿主写 `await asyncio.to_thread(agent.close)`，见 `saas-assistant/app/main.py`、`embed-for-agents.md` §2 和 `embedding.md`。

**提议：** 加 `__enter__/__exit__`（调用 `close()`），再加 `aclose()` 和 `__aenter__/__aexit__`。都是纯新增。
- `aclose()` 就是 `await asyncio.to_thread(self.close)`，即指南现在推荐的写法，不新增调度机制。
- 前提写进文档，不在代码里强制：宿主关闭前先结束自己正在运行的轮次。
- 不用 `agentao-arun-*` 线程池。早先的草稿说放进这个池会"排在正在运行的轮次后面"，这是错的：共享的多线程池不保证这种顺序，只要有空闲线程，`close()` 就会立即和本轮并行执行。

**已完成（2026-10-07）。** 按提议实现，只有一处改动（`aclose()`，见下）：
- `__enter__` / `__aenter__` 返回 agent 本身，用绑定到 `Agentao` 的 `TypeVar` 标注，所以子类在块内保持自己的类型。
- `__exit__` 调用 `close()`，`__aexit__` 等待 `aclose()`。两者都不吞掉块内的异常。
- `aclose()` 最终**没有**用 `asyncio.to_thread(close)`，这是评审中发现后改的：`to_thread` 被取消时，若其任务还排在繁忙的默认执行器后面，就根本不会运行，agent 会一直开着。`aclose()` 改为在自己的线程上运行 `close()`（`agentao-aclose`，非守护线程），并在线程启动前标为运行中，所以取消 `await` 只停止等待；会记录一条警告，说明 `close()` 仍在运行。这也让事件循环的默认执行器留给 httpx 使用。
- `close()` 加了锁：被取消的 `aclose()` 还在收尾时，第二次 `close()` 会等待，而不是同时执行清理。
- `close()` 本来就可以重复调用（结束 replay、断开 MCP、关闭记忆库各自都是幂等的），所以在块内仍调用 `close()` 的宿主不受影响。
- 前提（先结束 agent 的轮次）写进文档，不在代码里强制。
- `tests/test_agentao_context_manager.py` 覆盖两种写法、出错路径以及 `aclose()` 运行所在的线程。`tests/test_host_typing.py` 检查严格类型检查下宿主对 `with` / `async with` / `aclose()` 的使用，其标注检查也覆盖了这四个双下划线方法。
- 生命周期页、API 参考、两个 README、`embedding.md`、`embed-for-agents.md` 以及第 6 部分按请求创建 agent 的模式（资源与并发，模式 A）的主要示例改用 `with` / `async with`。agent 生命周期超出一个代码块的页面（按会话缓存），以及其他按请求创建 agent 的示例（第 4 部分、第 7 部分、`examples/`），保留 `try/finally`，用 `close()` 关闭，异步代码里用 `await agent.aclose()`。

### F5. 返回字符串不代表模型回答了

`chat()` / `arun()` 返回 `str`。模型是否真的回答了，要看 `agent.last_turn` 上的 `TurnOutcome.status` / `incomplete_reason`（`outcome.py:30-52`）。指南 §6.1 专门为此提醒。

**提议：** `chat()` 不改。改返回类型是破坏性的，而且指南已经说明了这一点。F2 的 `astream` 最后一项就是 `TurnOutcome`，结果会和文本一起带回来。

### F6. 别名重复；构造函数参数多

- **别名。** `add_event_observer` / `remove_event_observer`（`agent.py:1010-1016`）是 `add_host_event_observer` / `remove_host_event_observer` 的别名。仓库里还剩一处调用：`cli/run.py:743`。**暂缓。** 收益有限，而且先废弃再删除公开名字不算纯新增，不该放进"纯新增"的 PR。以后如果要做：先改掉这处调用，加 `DeprecationWarning`，在之后的某个次版本删除。
  - **已完成（2026-10-07）：前两步。** `cli/run.py`（`:743` 及对应的 `remove`，在 `:817`）和模仿它的两个测试桩改用 `host_event` 的名字；两个别名在调用方所在行发出 `DeprecationWarning`，其余行为不变。删除仍待之后的某个次版本。
- **构造函数。** `Agentao.__init__` 有 32 个参数：5 个位置参数，27 个仅限关键字参数。LLM 有两种配置方式：直接传原始配置参数，或者传 `llm_client=`，二者互斥（`_validate_construction_args`）。**不建议改动：**
  - 仅限关键字已经限制了误用风险；
  - 拆成配置对象会牵动所有文档、示例和测试，却不修复任何缺陷；
  - 指南已经以其中一种写法为主。
  - **文档调整（2026-10-07）。** 签名不变（加入 `permission_mode=` 后共 33 个参数）。开发者指南的构造器参数页（中英文，§2.2）现在开头先给出全部 33 个参数的分组一览，并补上了此前漏掉的六个（`max_tokens`、`prompt_cache`、`prompt_cache_ttl`、`compaction_controller`、`plan_session`、`enable_builtin_agents`）。同时更正了最小调用：不传 `llm_client=` 时，`api_key`、`base_url`、`model` 三个都必填，而且直接调用 `Agentao(...)` 不读任何环境变量，所以原来那个三参数示例会抛 `ValueError`。

### F7. 常见任务只应接触少量名字

评审结论（2026-10-06）：**现有导出里没有值得现在删除或迁移的名字。** 最有效的简化，是让宿主完成常见任务时只需接触少量接口，而不是缩短完整导出清单。

**现状：**
- 指南把 `agentao.host` 写成扁平的一行（`embedding.md:728-731`），而且这一行不完整：漏了 `Tool`、`AsyncToolBase`、`RegistrableTool`、`StreamSubscribeError` 和 `SubagentUsage`。
- 两份嵌入指南都没说什么时候用 `Tool`、什么时候用 `AsyncToolBase`；这个说明只在开发者指南的 5.1（`developer-guide/en/part-5/1-custom-tools.md`）。
- `dir(agentao.host)` 看不到三个懒导出的工具类型。实测 `Tool`、`AsyncToolBase` 和 `RegistrableTool` 都不在其中，因为 `agentao.host` 定义了 `__getattr__`，却没有 `__dir__`。顶层 `agentao` 已经有 `__dir__`（`agentao/__init__.py:85`）。

**提议：保留所有导出，指南按层次介绍。**

| 导出 | 宿主在哪里接触它 |
|---|---|
| `Tool`、`AsyncToolBase`、`RegistrableTool` | 指南写明如何选择：同步工具继承 `Tool`，异步工具继承 `AsyncToolBase`，`RegistrableTool` 用于类型标注（例如传给 `extra_tools=` 的列表）。不新增统一基类。 |
| `EventStream` | 主要给运行时用。宿主指南直接展示 `agent.events()`，不要求宿主自己构造它。 |
| `RFC3339UTCString`、`SubagentUsage` | 只在完整参考里出现；最小接入示例不导入它们。 |
| `export_host_event_json_schema`、`export_host_acp_json_schema` | 放在参考文档的 schema 导出说明里，普通宿主接入指南不展示。 |
| 三种事件、`HostEvent`、`ActivePermissions`、`StreamSubscribeError` | 职责清楚，保持现状。 |

**实现：** 给 `agentao.host` 加一个 `__dir__()`，把懒导出的名字也列进去，做法与 `agentao/__init__.py:85` 相同，让交互式发现能看到它们。沿用现有的懒导出机制，调用时不导入任何东西。

**已完成（2026-10-07，只改文档）：**
- `embedding.md` §7 新增一张表，把 17 个名字按任务分组：观察、流式文本与结果、取消、编写宿主工具（同步用 `Tool`，异步用 `AsyncToolBase`，`RegistrableTool` 只用于类型标注）、第二个 `events()` 迭代器（`StreamSubscribeError`），以及仅供参考（`EventStream`、`RFC3339UTCString`、两个 schema 导出函数；`Agentao` 把 `EventStream` 设为私有，宿主通过 `events()` 和 `add_host_event_observer()` 接触它）；`SubagentUsage` 归在观察一组，是终态子 Agent 事件 `usage` 的类型。分层说的是宿主在哪里需要某个名字，不是它有多稳定。
- `embed-for-agents.md` §3 保留可直接复制的导入代码块，在其中加上 `SdkTransport`（即下文 §7 记录的缺口），并按任务何时需要列出其余名字。
- `host-api.md`（中英两份）的*公共导出*表补上原来缺的两行：`EventStream` 和 `StreamSubscribeError`。
- `embedding.md` 里那份平铺的 `agentao.host` 列表在 *From 0.3.1* 下，是记录那个版本新增内容的迁移说明，所以不动。开发者指南附录 A 列出全部 17 个名字，作为完整参考本该如此。两份嵌入指南都没有中文版。
- 开发者指南（中英两份）原来有七页仍在教 `from agentao.tools.base import Tool`，正是 F3 第 0 步已让示例弃用的路径；现在都改从 `agentao.host` 导入 `Tool`（同一个类）。附录 A 的 `__all__` 列表保留 `agentao.tools.base`，并注明宿主应走 `agentao.host`，与它对 `CancellationToken` 的写法一致。
- 同一轮还更正了这些页面里关于 replay 的说法：配置在 `.agentao/settings.json` 的 `replay` 块里（没有 `replay.json`）；只有调用 `start_replay()` 且 replay 已开启（由该块开启，或显式传入 `ReplayConfig(enabled=True)`）时才记录；在 `build_from_environment()` 下 `replay_config=None` 当时会从磁盘读取，所以要关闭得传 `ReplayConfig(enabled=False)`。后续修复让显式传 `None` 就关闭 replay，与 `bg_store=None`、`sandbox_policy=None` 一致，指南也已相应更新。`events()` 只允许一个消费者的规则改为按 `session_id` 过滤条件说明，在第二个迭代器第一次迭代时抛出。

### F8. 宿主自身的方法有一部分没有类型标注

`agentao/py.typed` 已随包发布，类型门禁是 `mypy --strict --package agentao.host`（`.github/workflows/ci.yml:54-55`）。它覆盖契约里的类型，不覆盖 `Agentao` 的方法。用 `inspect.signature` 遍历 `Agentao` 的公开成员，测得（2026-10-06）：
- 没有返回类型标注：`events()`（`agent.py:1068`，返回 `EventStream.subscribe(...)`，这是一个异步迭代器，类型用的是私有联合类型 `_PublishedEvent`（`host/events.py:53-57`、`:328-331`），它的公开名字是 `HostEvent`）和 `active_permissions()`（`:1086`，返回 `ActivePermissions`），正是嵌入指南最先介绍的两个方法；另有 `add_message`、`clear_history`，以及 `memory_manager` 和 `compaction_coordinator` 两个属性；
- 参数没有标注：`__init__` 的 `transport`，以及四个观察者方法的 `callback`。

`chat()`、`arun()`、`add_tool()`、`close()`、`last_turn` 和 `set_permission_mode()` 都有标注。所以宿主的类型检查器从指南展示的第一个调用起就看到 `Any`。

**提议：** 给面向宿主的方法补标注，先做 `events()` 和 `active_permissions()`。用 `TYPE_CHECKING` 从 `agentao.host` 导入，`agent.py` 不增加运行时导入。是否扩展类型门禁、检查宿主对这些方法的使用（例如通过 `tests/test_host_typing.py` 的下游消费者），留到实现时决定。`compaction_coordinator` 这类内部访问器可以标注也可以不动，但不会因此进入契约。

**实现中做出的决定（第 4 步）：**
- **所有公开成员都补了标注**，不只是上面六个：`events() -> AsyncGenerator[HostEvent, None]`（生成器类型，严格模式的宿主才能对它用 `aclosing()`）、`active_permissions() -> ActivePermissions`；四个观察者方法的参数是 `Callable[[HostEvent], object]`，两个 `add_*` 方法原样返回传入的回调并保留其自身类型（一个以该可调用类型为上界的 `TypeVar`）；`add_message`、`clear_history`、`__init__` 返回 `None`；`transport` 是 `Optional[CoreTransport]`；两个属性分别返回 `MemoryManager` 和 `CompactionCoordinator`。这些名字都在 `TYPE_CHECKING` 下导入，`agent.py` 运行时不会多加载任何模块（只在 `typing` 的导入里加了 `Callable` 和 `TypeVar`）。给两个属性加标注只是让它们有类型，并不把它们升进契约。
- **`transport=` 的类型不是 `Transport`。** `Transport` 把 `subscribe()` 声明为协议成员，用它标注会让 mypy 拒绝只实现运行时所调用的四个方法的自定义 transport，而这样的 transport 除了 `astream()` 之外的所有 API 都能用（Codex 评审发现）。现在这四个方法单独构成协议 `CoreTransport`（`agentao/transport/base.py`），`Transport` 在它之上加 `subscribe()`，`Transport` 本身不变。宿主用法测试用这样的 transport 构造 `Agentao`。`CoreTransport` 从 `agentao.transport` 导出，和 `Transport`、`NullTransport`、`SdkTransport` 放在一起，不从 `agentao.host` 导出：F3 的规则针对稳定数据类型，而 transport 类型都不在那个接口面上，所以 `agentao.host` 的 17 个名字不变。`agent.transport` 属性也随之标为 `CoreTransport`（原来是 `Any`），严格模式的宿主要订阅，就在它自己构造的 transport 对象上调用 `subscribe()`，而不是通过 `agent.transport`。`isinstance(t, Transport)` 检查能让类型检查器通过，但在运行时证明不了什么：`ReplayAdapter` 能通过它，可被包装的 transport 没有 `subscribe()` 时，它的 `subscribe()` 什么也不注册；一个显式继承 `Transport`、却没有定义 `subscribe()` 的类也能通过，它继承的是协议里的空桩方法（`/code-review` 发现）。`runtime/astream.py` 的 `resolve_subscribe` 正因此对这两种情况都做了检查。
- **`compact(reason=)` 的类型收窄了**，从 `str` 改成 `Literal["manual_cli", "api_overflow", "compression_threshold"]`，即它的 docstring 列出的三个原因，命名为 `ManualCompactionReason`，放在 `agentao/compaction/types.py` 里 `CompactionReason` 旁边（宿主文档本来就让宿主到那里找压缩相关类型），并有测试保证它始终是子集（`/code-review` 发现）。最初试过整个 `CompactionReason` 字面量类型，评审时被否决：它还包含 `microcompact_threshold` 和 `api_overflow_after_compression`，这两个属于别的阶梯，而 `compact()` 总是执行 `full` 压缩。宿主传一个普通 `str` 变量现在会得到 mypy 错误；运行时行为不变。
- **顺带修了一处运行时缺陷**（更正指南里 `ask_user` 签名时由 `/code-review` 发现）：内置 `ask_user` 工具和子代理的提问是经由一个 `**kw` lambda 调用 transport 的，这层包装让 `invoke_ask_user_callback` 看不到方法的真实签名，于是 `ask_user` 只接受问题的 transport 会抛 `TypeError`。现在签名检查直接针对 transport 方法本身（`tests/test_ask_user_transport_signature.py`）。
- **门禁也检查宿主的用法。** `tests/test_host_typing.py` 新增第二个下游消费者，调用这些方法，每个结果都经过带类型的 `return` 返回，用 `mypy --strict --follow-imports=silent` 运行：`agent.py` 本身不受 `--strict` 约束，所以它自己的错误不报告；但只要有 `Any` 到达宿主，就会被 `warn_return_any` 拦下，没有标注的方法会被 `disallow_untyped_calls` 拦下。另一个测试用 `inspect.signature` 遍历 `Agentao` 的公开成员，新增成员没有标注也会失败。两个测试在改动前的 `agent.py` 上都会失败。
- **测试套件里补全凭据的 autouse fixture**（`tests/conftest.py`）原来把 `Agentao.__init__` 换成一个裸的 `(*args, **kwargs)` 垫片。现在它用 `functools.wraps`，在套件里 `inspect.signature(Agentao.__init__)` 读到的是真实签名。
- `mypy agentao/agent.py` 报的错比以前少（从 36 个降到 20 个），没有新增；本来会新增一个，因为 `_compaction_coordinator` 初始化为没有类型的 `None`，所以现在给它标注了 `Optional[CompactionCoordinator]`。

### F9. 宿主工具必须写成类

Agentao 和它的指南都没有把普通函数变成工具的办法（grep `from_function`、`function_tool`、`FunctionTool` 无匹配）。宿主哪怕只是“查订单”，也要写一个 `Tool` 子类，提供 `name`、`description`、`parameters`（手写的 JSON schema）和 `execute`。Pydantic AI 和 Strands 都能从函数签名和 docstring 构造工具。

**提议，暂缓到 `astream` 之后，按需做：** 一个薄适配器，把同步或异步函数变成普通的 `Tool` 或 `AsyncToolBase`。它不是第二套工具体系：
- 结果通过 `add_tool` / `extra_tools=` 注册，走同一个注册表、规划器、权限引擎、事件和执行器。
- 参数 schema 由类型标注生成。`pydantic>=2` 已是核心依赖（`pyproject.toml:37`）。
- **安全属性显式给出，默认按失败关闭处理。** `Tool` 的默认值是 `requires_confirmation=False` 和 `is_read_only=False`（`tools/base.py:112-131`）。适配器保留这些默认值，绝不根据函数名或签名推断只读。它也不设置 `copies_to_subagents`，所以函数工具只有在宿主声明后才会传给子代理。
- 需要状态、资源或生命周期的工具仍然写成类。

## 4. 建议顺序

已按评审修订。每一步单独一个 PR。

1. ~~**F1(a) 文档，加上示例里现有的错误导入**（F3 第 0 步）。只改文档和示例。~~ **已在 PR #423 完成。**
2. ~~**字符串形式的权限模式、`Agentao(permission_mode=...)`，以及从 `agentao.host` 导出 `CancellationToken`**（F3 第 1–3 步），加上 F7 的 `__dir__`。~~ **2026-10-06 完成，已作为 PR #426 合并**，经过五轮 `/code-review --fix`；见*实现中做出的决定*。F4 之后已实现（2026-10-07）。
3. ~~**最小的 `astream`**（F2）：先把 `TurnOutcome` 移到轻量模块，再实现 `TextDelta` + 最后的 `TurnOutcome`（都从 `agentao.host` 导出），通过订阅接入，遵守上面的生命周期约束。然后把示例从 `SdkTransport` / `EventType` 上移走，并修好 `saas-assistant` 的替换写法（F3 第 3 步）。~~ **2026-10-06 已实现，已作为 PR #428 合并**；见 F2 的*实现中做出的决定*。

4. ~~**给面向宿主的方法补返回类型标注**（F8）。小改动，纯新增。~~ **2026-10-06 已实现，已作为 PR #432 合并**；见 F8 的*实现中做出的决定*。
5. **函数工具适配器**（F9），等宿主提出需要时再做。

第 2 步完成后 F4 单独做；现已实现（2026-10-07，见 F4 的*已完成*）。F7 的 `__dir__` 已随第 2 步完成；F7 的指南分层已完成（2026-10-07，只改文档）；见 F7 的*已完成*。F6 的别名已废弃（2026-10-07），删除留到之后的次版本；构造函数部分不提议改动。

## 5. 有意不提议的

- 把构造函数拆成配置对象（F6）。
- 改 `chat()` 的返回类型（F5）。
- 把目标 / 持续执行循环移进 harness。这仍然是宿主的事（`embed-for-agents.md` §7b；`docs/design/codex-goal-mechanism-review.md` §11）。
- 来自同类对照（§7），不提议：
  - 用于组装 agent 的通用 Capability 或 Plugin 框架；
  - 在 `Agentao(...)` 和 `build_from_environment(...)` 之上再加 `HarnessClient`、`HostAgent` 或 Builder；
  - 接受 `bool | str | dict | Manager` 的参数，或给 `enabled_tools` / `disable_tools` / `extra_tools` 用混合映射语法；
  - 泛型 `RunResult[T]`，或把模型的结构化输出绑在运行结果上；
  - 单独的审批回调类型，或审批的暂停／恢复、持久化状态机；
  - 类型化的 `deps` 或按调用计的用量预算。

## 6. 请维护者决定的问题

1. ~~**F1：** "ASK 即批准"是不是长期的无界面默认？如果不做 (c)，还要不要做 (b)？~~ **已于 2026-10-06 答复：走 (a) 路线。** "ASK 即批准"继续作为无界面默认，也不加默认 engine。
2. ~~**F2：** assistant 文本要不要进稳定契约？~~ **已于 2026-10-06 答复：进**，形式为先 `TextDelta`、后 `TurnOutcome`，不进审计 schema。
3. ~~**F3：** 懒导出放在 `agentao.host` 还是顶层 `agentao`？~~ **已于 2026-10-06 答复：只放在 `agentao.host`。**
4. ~~**F3：** 宿主不导入 `PermissionEngine` 时怎样设定权限姿态？~~ **已于 2026-10-06 答复：`Agentao(permission_mode=...)`**，默认 `None`。同时传入 `permission_mode=` 和 `permission_engine=` 时抛 `ValueError`（2026-10-06 已决定）。

**第 2–3 步之后 `agentao.host` 的导出：** 现有 14 个名字不变，再加 `CancellationToken`、`TextDelta` 和 `TurnOutcome`。现有 14 个一个都不删：它们都在有类型门禁的稳定接口上，删掉任何一个都会破坏宿主，而换不来实际的简化。简化体现在指南怎样介绍它们（F7）。

## 7. 同类对照：Pydantic AI 与 Strands（2026-10-06）

一份建议把宿主接口与 Pydantic AI（`Agent(..., capabilities=[...])`）和 Strands harness SDK（`create_harness(...)`，返回普通的 `strands.Agent`）做了对照。它的结论是：值得借鉴的是常见接入路径短、一次调用的结果完整、流式调用有明确的结束边界；不需要通用的 Capability 或 Plugin 框架。本评审同意这个结论。

**核实情况。**
- Pydantic AI 的 `run_stream_events()` 的用法是 `async with … as events: async for event in events`，流以 `AgentRunResultEvent` 结束（其 agent 文档，2026-10-06 抓取）。
- 关于 Strands 的说法（工厂返回 `Agent`、`AgentResult`、`interventions`、多形态参数）此处**没有**重新核实；下面的结论都不依赖它们。
- 关于 Agentao 的每一条都已对照代码核实，证据见下表所指的各条发现。

| # | 建议 | 结论 | 位置 |
|---|---|---|---|
| 1 | 保留工厂和显式构造，都返回 `Agentao`；不加 client、包装层或 Builder | 同意；现状即如此 | §5 |
| 2 | 流式调用要有明确的结束边界：`aclosing`、`TextDelta \| TurnOutcome`、不要 `TurnFinished` | 同意；已在设计中。新增一条规则：增量用于显示，`TurnOutcome.text` 才是结果 | F2 |
| 3 | 结果与调用绑定；保留 `chat()/arun() -> str`；`astream` 交付 `TurnOutcome`；运行状态与结构化输出分开推进 | 同意。现状即如此：F5 和 F2 | F2、F5 |
| 4 | 简单形式加高级注入，两者互斥；不用多形态参数 | 同意；这正是第 2 步的规则（`permission_mode=` 与 `permission_engine=` 互斥） | F3、§5 |
| 5 | 在现有工具路径上加函数工具适配器 | 同意，排在 `astream` 之后、按需做，安全属性显式给出并失败关闭 | F9 |
| 6 | 区分权限姿态与宿主审批；以后可加薄的审批回调适配器 | 适配器已经存在，见下文 | §7 |
| — | 给宿主常用方法补类型标注（来自建议末尾的顺序） | 同意；已测得缺口 | F8 |

**关于第 3 条：把结果绑定到调用。** 每个 agent 只有一个调用方时，`agent.last_turn` 是对的：轮次锁防止了重叠（`runtime/turn.py:72-100`）。多个调用方共用一个池化 agent 时，在 `chat()` 返回之后、调用方读取之前，另一个请求的轮次可能已经替换了 `last_turn`。`astream` 把结果放在流里送达，消除了这个空档。返回 `TurnOutcome` 的非流式入口，如建议所说，等宿主需要时再加。

**关于第 6 条：只做审批的宿主已经有薄适配器。** `SdkTransport` 的每个回调都是可选的（`transport/sdk.py:76-82`）。只需要审批的宿主传 `SdkTransport(confirm_tool=my_policy)`，别的都不用传。它不需要 `on_event`，因为 `agent.events()` 仍通过 transport 的订阅工作。没有其他回调时，`ask_user` 回答不可用，达到最大迭代次数时本轮停止（`transport/sdk.py:101-131`）。`embed-for-agents.md` §1 为失败关闭的情形展示的就是这个形式（`confirm_tool=lambda *_: False`）。契约正如建议所说：`permission_mode`（或引擎）决定姿态，剩下的 ASK 由 transport 回答。当时还剩一个文档缺口：指南 §3 的稳定导入列表列了 `NullTransport`，没有列 `SdkTransport`，而 §1 和 `host-api.md` 都在用它。这是指南分层（F7）时要修的文档问题，不是新 API。（已在 F7 中修复，2026-10-07。）

**本评审之后的顺序：** `astream`（第 3 步），然后 F8，F9 按需做。（第 3 步和 F8 之后已分别作为 PR #428、#432 合并；见 §4。）F4 仍然独立。不删除任何现有导出；新的稳定数据类型继续放在 `agentao.host`；不引入 Capability、Plugin 或构造配置框架。
