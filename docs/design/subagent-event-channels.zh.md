# 子代理事件通道：子代理的事件放在它自己的 session 上

**状态：** 评审完成（rev 3）。§8 第 1 步（#580、#582）、第 2 步（子代理 session id，#583）和第 3 步（转播）均已实现。
**锚点：** agentao `main@ed52674b`（2026-10-24，#579 之后）。codex `816fa702ec6`。pi-mono `136b87e8a`。行号以这些提交为准。
**相关：** `subagent-event-bridge.md`（本文保留的 transport 转发器）；`embedded-host-contract.md`（公开的 host 契约）；#578 / #579（transport 事件上的 `child_task_id`）；#580（`SubagentLifecycleEvent` 上的 `parent_tool_call_id`）。
**修订历史：**
- rev 1：初稿。
- rev 2（对 rev 1 的评审）：
  - 首版回放跳过转播的子代理事件（§5.5）。rev 1 假设前台子代理的行会落在子代理的回放 turn 上。对子代理的并行批次这不成立，因为回放的子代理栈是线程局部的。
  - "`spawned` 先于这次运行的子代理事件"由我们保证，不交给宿主处理（§5.9）。
  - 解除挂载的边界写明：drive 结束、解除挂载、终态事件、close（§5.4.3）。
  - 不变量 1 不再声称转播关闭时事件流不变。生命周期事件会补充关联字段（§7）。
  - 已决定：一个实例级开关、一个私有 session adopter、复用现有 observer 钩子并修改其 docstring。去掉：`relay_to()`、按次开关、订阅时快照、回放分组。
- rev 3（对 rev 2 的复审）：
  - 顺序保证写明适用的订阅：在 `spawned` 之前建立、并持续有效的 `session_id=None` 订阅（§5.9、不变量 5）。
  - 解除挂载的边界只规定必要约束：drive 结束后解除挂载，且先于终态事件和 close。每条路径保留现有的 close 与终态事件的先后（§5.4.3）。rev 2 误称前台已经先发布终态事件再 close。
  - 回放跳过集合的顺序固定：先登记再挂载，先解除挂载再移除登记，每条路径都如此（§5.5）。
  - 三个待定问题已决定（§11）。

## 1. 摘要

前台子代理的事件只有一条路到父代理：transport 转发器把五种事件复制进父代理的 transport 流（`agents/tools/_wrapper.py::_make_forwarder`）。后台子代理完全不发进度。本文提出第二条路：**把子代理的公开 host 事件放进父代理的 `events()` 流，使用子代理自己的 `session_id`。** 父代理的流里只保留这次运行的指针事件（`SubagentLifecycleEvent`）。transport 转发器保持不变，作为 CLI、ACP 和只用 transport 的宿主的兼容路径。

codex 和 pi-mono 用的就是这个模型（§3）。对读 `events()` 的宿主，它消除了混流的三项代价（§2），也给后台子代理提供了进度通道。所需的机制大多已经存在：子代理的 `Agentao` 有自己的 `EventStream`，`EventStream` 有同步的 observer 钩子和 `session_id` 过滤（§4）。

转播可选开启，用一个实例级开关（§5.4）。首版回放不记录转播事件（§5.5）。父代理工具行到这次运行的关联来自 #580。

## 2. 问题

把子代理事件混进父代理的流有以下代价，每一项都在现有代码里。

| # | 代价 | 位置 |
|---|---|---|
| 1 | 消费方必须给子代理事件打标，才能和父代理的事件区分。以前解析 `[name n/max]` 标签，#579 起可以读 `child_task_id`。每个消费方都得知道这个约定。 | `_make_forwarder`；#579 |
| 2 | 子代理由服务商生成的 call id 进入父代理的 id 作用域。`tool_call_id` 只在 `(session_id, turn_id, tool_call_id)` 内唯一。CLI 和 ACP 按 `call_id` 定位行。服务商重复使用 id 时可能关掉错误的行。未复现。 | `subagent-event-bridge.md` §4.2；`runtime/identity.py` docstring |
| 3 | 转发器必须是白名单。转发 `TURN_BEGIN` / `TURN_END` / `LLM_CALL_*` / `TOOL_RESULT` 会让回放把子代理的 turn 记成父代理的。新增事件类型都要决定是否转发。 | `_make_forwarder` docstring；桥接文档不变量 6 |
| 4 | `TOOL_START` 带标签，`TOOL_OUTPUT` / `TOOL_COMPLETE` 带裸名字。只要所有消费方都按 `call_id` 定位就无害。 | 桥接文档 §4.3 |
| 5 | 宿主 sink 处理转发事件时抛异常，事件会悄悄丢失。子代理的 `SdkTransport.emit` 吞掉异常，不打日志。 | `transport/sdk.py:91-96` |
| 6 | 投递在子代理线程上同步进行。sink 慢，子代理就慢。 | `_make_forwarder` |

第 1–4 项来自**同一个命名空间**，而不是投递机制。把直接调用换成队列能解决第 6 项、改变第 5 项，但解决不了第 1–4 项。每个子代理一条独立通道才能解决第 1–4 项。所以本文讨论的是通道布局，不是加不加队列（agentao 已经有队列，见 §4.2）。

## 3. 同类项目

**codex**（`codex-rs/`）：
- 每个子代理是一个独立线程，有自己的 `ThreadId`。`spawn_agent` 立即返回（`core/src/tools/handlers/multi_agents_v2/spawn.rs`）。
- 父代理的 turn 里只有 `SubAgentActivity` 条目：`Started` / `Interacted` / `Interrupted` / `Completed`，带 `agent_thread_id`、`agent_path`，`id` 是父代理工具调用的 `call_id`（`protocol/src/items.rs:361`、`multi_agents_v2.rs:44`）。
- 子代理的结果作为一条不开启新 turn 的信箱消息交给模型（`core/src/agent/control/completion.rs`，`trigger_turn = false`）。尽力投递，有指标。
- 子代理自己的事件留在子线程上。

**pi-mono durable**（`packages/durable/`）：
- 每个后台子代理是一个独立的持久化 conversation（`test/examples/22-subagent-background.ts`）。
- 派生它的工具结果返回 `details: { name, conversationId }`。UI 用 `watchEvents(harness, conversationId)` 订阅。
- 嵌套的工具事件带 `parentToolCallId` / `parentTaskId`（`src/harness/events.ts:98-111`）。
- `watchEvents` 在订阅时给一个快照，溢出时用一个快照替换未投递的批次。它靠这个处理晚订阅。

两家的共同点：父代理的流放指针，子代理的流放细节，指针带父代理的 call id。

## 4. 现状

### 4.1 子代理有自己的 host 事件流

`_build_sub_agent` 构造的是一个完整的 `Agentao`。它的构造函数创建 `self._host_events = EventStream()`（`agent.py:781`），以及绑定到它的三个 emitter（`agent.py:1028-1042`）。所以子代理的 `ToolRunner` 已经为每次子代理工具调用发布 `ToolLifecycleEvent` 和 `PermissionDecisionEvent`，带子代理的 `session_id` 和 `turn_id`。没人订阅，`publish` 就把它们丢掉（`host/events.py`，"No subscriber"）。

### 4.2 `EventStream` 是带 session 过滤的队列

`host/events.py`：
- 每个订阅者有自己的有界 `asyncio.Queue`。
- **不在事件循环上的发布会等到事件进入队列。** 它调用 `put_nowait`，或者调用 `run_coroutine_threadsafe(queue.put(...))` 再等 `fut.result()`（`:313-318`）。所以队列满时发布方会阻塞（宿主拉动的背压）。
- **在事件循环上的发布不等待。** 队列满时它安排一个 put 任务就返回。挂起的 put 超过 `max_queue_size` 时丢弃事件并打 warning。
- `subscribe(session_id=...)` 按 `session_id` 过滤。`None` 订阅该实例拥有的所有 session。每个过滤条件只允许一个订阅者。docstring 原文："Hosts that need per-session iterators should subscribe to all sessions and filter client-side."
- `add_observer(callback)` 在 `publish` 内部同步触发，先于订阅者检查，不管有没有订阅者都会触发。observer 在锁外、按列表快照执行。observer 抛异常时打 WARNING 并被隔离。`Agentao.remove_host_event_observer` 幂等地解除挂载，但不会等待已经取得快照的发布方。
- 保证同一 session 内的顺序，不承诺跨 session 的顺序。

### 4.3 生命周期事件本来就能指向子 session

`SubagentLifecycleEvent` 有 `child_session_id: Optional[str]`（`host/models.py:139`）。`HostSubagentEmitter.spawned()` 接受 `child_session_id=`，并把线上的 `session_id` 设为 `child_session_id or parent_session_id`（`host/projection.py`，注释："so a host that filters by session sees the child's events together"）。目前没有调用方传 `child_session_id`，所以这个字段总是 `None`。

### 4.4 子代理的 session id 生成得晚

子代理的 `_session_id` 是构造函数里新生成的 UUID（`agent.py:779`）。`spawned` 在子代理构建之前发布（`_wrapper.py` 的 `execute()` 和 `_launch_background`）。所以发布 `spawned` 时，子代理的 session id 还不存在。在 `agent.py` 内部，`_session_id` 只通过 emitter 的惰性 provider 读取（`agent.py:1030-1041`）。在 `agent.py` 之外，没有模块直接读 `agent._session_id`（在 `agent.py` 之外 grep `\._session_id`：只有 `MemoryManager` 和 `ToolRunner` 自己的同名属性，是不同的字段）。

### 4.5 回放的子代理栈是线程局部的

`ReplayAdapter` 把子代理栈放在 `threading.local` 里（`replay/adapter.py:44-48`）。`_current_turn_id()` 返回栈顶，栈空时返回父代理的 `_turn_id`。子代理的并行批次在 `ThreadPoolExecutor` 新开的工作线程上运行（`runtime/tool_executor.py:300`），这些线程的栈是空的。那里的 `contextvars.copy_context()` 也带不过去，因为这个栈不是 contextvar。

## 5. 设计

### 5.1 总览

```
父代理 events()  ── ToolLifecycleEvent(session=P, tool_call_id=c1, tool=agent_x)        父代理自己的行
                 ── SubagentLifecycleEvent(session=P, child_task_id=k,
                                           child_session_id=S, parent_tool_call_id=c1,
                                           phase=spawned)                                 指针（#580）
                 ── ToolLifecycleEvent(session=S, turn=t', tool_call_id=x1, ...)          子代理的行（转播）
                 ── PermissionDecisionEvent(session=S, ...)                               子代理的决策（转播）
                 ── SubagentLifecycleEvent(session=P, child_task_id=k, phase=completed)   指针
transport        ── AGENT_START / 转发的子代理事件 / AGENT_END                            不变（§5.6）
```

宿主用已有的 id 把三层连起来：`tool_call_id` → `parent_tool_call_id`，再 `child_session_id` → 子代理事件的 `session_id`。

### 5.2 在 `spawned` 之前生成子代理的 session id

- wrapper 在生成 `child_task_id`（#579）的同一位置、`AGENT_START` 之前，用 `runtime.identity.new_session_id()` 生成 `child_session_id`。后台运行在 `_launch_background` 里、`_spawn_subagent_event` 之前生成。
- 把它传给 `spawned(child_session_id=...)`。
- 通过 **`Agentao` 上的一个私有 adopter** 交给子代理，和 `_adopt_memory_manager` 放在一起。adopter 在构造之后、第一次 `chat()` 之前设置一次 `_session_id`，第二次调用时拒绝。emitter 惰性读取 id（§4.4），所以子代理的每个事件都会用到它。
- 不用构造参数：那会为一个内部需求增加公开 API。

### 5.3 指针事件留在父代理的 session

第 2 步（§8）之前，`spawned()` 把 `session_id` 设为 `child_session_id or parent_session_id`。有了 §5.2，每次运行都会变成子代理的 session，用 `events(session_id=P)` 订阅的宿主就再也看不到运行的开始和结束。所以：

- **改规则：生命周期事件的 `session_id` 永远是父代理的。** 指针由 `child_session_id` 承载。
- 这只改了一条目前没有调用方走到的路径（§4.3），所以现有事件的 `session_id` 都不变。
- 考虑过的替代方案：保留规则，让生命周期事件放在子代理的 session。否决：按父 session 过滤的订阅者会看到父代理的 `agent_*` 行，却看不到这次运行的任何信息。codex 把活动放在父线程，也是这个原因。

### 5.4 把子代理的 host 事件转播到父代理的流（可选开启）

#### 5.4.1 转播

- `_build_sub_agent` 构造子代理之后、drive 开始之前，wrapper 调用 `child.add_host_event_observer(relay)`。`relay(event)` 原样调用父代理的 `self._host_events.publish(event)`。事件本身已经带子代理的 `session_id`。
- **转播哪些事件：** 子代理发布的所有 `HostEvent`，也就是 `ToolLifecycleEvent` 和 `PermissionDecisionEvent`。子代理不能派生子代理，所以不会发布 `SubagentLifecycleEvent`。用一个测试钉住"子代理发布什么就转播什么"，这样新增公开事件类型时不用改 relay。
- wrapper 保留 `relay` 对象，作为 `remove_host_event_observer` 的句柄。

#### 5.4.2 开关

- `Agentao(..., relay_subagent_events: bool = False)`，在 `*` 之后，仅限关键字。每个实例一个开关，没有按次开关。
- 关闭（默认）：不挂 observer，父代理的流里没有任何子 session 事件。
- 开启：用 `session_id=None` 订阅的宿主会收到子代理事件。
- 为什么要可选开启：现在用 `None` 订阅、把所有 `ToolLifecycleEvent` 都算作父代理调用的宿主，开启后会把子代理的调用也算进去。改成默认开启是另一个决定。
- 子代理在派生时从父代理读取这个开关。子代理没有自己的子代理，所以从不转播。

#### 5.4.3 解除挂载的边界

每条路径上的约束：**drive 结束（`_drive_sub_agent` 返回或抛异常）之后解除挂载，并且先于终态 `SubagentLifecycleEvent` 和子代理的 close。** close 与终态事件谁先谁后不属于这个约束，每条路径保留现在的顺序。

- 前台：`_run_sync` 的 `finally` 关闭子代理，`execute()` 在 `_run_sync` 返回之后才发布终态事件。所以现在的顺序是 drive → close → 终态。解除挂载放在这个 `finally` 的开头、`_close_sub_agent` 之前：drive → 解除挂载 → close → 终态。
- 后台：`_launch_background` 的 worker 在 `try` 里发布终态事件，在 `finally` 里关闭。所以现在的顺序是 drive → 终态 → close。解除挂载放在 drive 刚结束之后、发布终态事件之前：一个直接包在 `_drive_sub_agent` 外面的 `try/finally`，不管 drive 正常返回还是抛异常（包括 `BaseException`）都会解除挂载，并且在三条终态路径（完成或未完成、`AgentCancelledError`、`Exception`）执行之前完成：drive → 解除挂载 → 终态 → close。（rev 3 原计划在三条路径上各解除一次，再在外层 `finally` 里做第二次；一个 `try/finally` 以一次解除挂载得到同样的顺序。）
- `remove_observer` 不会等待已经取得 observer 快照的发布方（§4.2）。所以这个边界依赖下面这个事实：**drive 结束时，子代理已经没有任何线程能发布 host 事件。** 依据：
  - 并行批次只有在所有 worker 结束之后才会离开 `with ThreadPoolExecutor(...)` 代码块（`tool_executor.py:300`）；
  - 单个工具在 drive 的线程上内联执行；
  - 异步工具的 worker 在取消后可能还活着（CLAUDE.md："cancels the awaiter, never the worker"），但 host 事件由 executor 在调用线程上发布，不在工具 worker 里。

  用测试钉住结果：终态事件之后没有转播事件（§9）。不额外加同步机制。

#### 5.4.4 背压

`relay` 在子代理的线程上执行（observer 是内联的）。父代理的 `publish` 不在事件循环上时会阻塞等容量。所以宿主消费慢会拖慢子代理，就像现在拖慢父代理一样。后台子代理也可能被阻塞。这是 `events()` 文档写明的契约。

`add_observer` 的 docstring 写着 observer "must be cheap and non-blocking"。relay 会阻塞。这个阻塞和父代理自己的发布点一样，发生在同类线程上（工具 worker 线程）。**修改 docstring：** 向另一个 `EventStream` 转发的内部 relay，在那个流的背压下可以阻塞。对宿主 observer 的规则不变。

### 5.5 首版回放跳过转播事件

`HostReplaySink` 是父代理流上的 observer（`replay/manager.py:119-123`）。开启转播后，它也会看到子代理的事件，但放不对位置：
- 前台：子代理的并行批次从线程池 worker 发布，这些线程的子代理栈是空的（§4.5），所以这些行会落在父代理的 turn 上；
- 后台：行会落在它们到达时恰好打开的父代理 turn 上，可能是之后一个不相干的 turn。

所以**首版不记录转播事件。**
- wrapper 把每次在跑的运行的 `child_session_id` 登记到父代理上的一个集合里。**先登记再挂载 relay，先解除挂载 relay 再移除登记**，这样任何转播事件到达 sink 时，它的 session 都已登记。异常路径按同样的顺序清理。除这个集合外不加管理层。
- `HostReplaySink` 跳过 `session_id` 在这个集合里的事件。
- 不用"跳过 `session_id` 和父代理不同的事件"：父代理的 session id 可能被重新绑定（ACP `session/load`），`projection.py` 已经在防这种情况。
- 回放记录的行和现在完全一样。把子代理的行明确映射到某个回放 turn，等有需求时再做。

### 5.6 transport 转发器保留

`_make_forwarder`、`AGENT_START` / `AGENT_END`、`[name n/max]` 标签和 #579 的 `child_task_id` 都不变。CLI、ACP 和只用 transport 的宿主（#578 的那个宿主）照常工作。本文不弃用转发器。把 CLI 或 ACP 迁到 host 通道不在范围内。

### 5.7 确认和 `ask_user` 保持同步

它们是请求–应答，子代理要等回答。它们继续走 transport 路径（`_child_transport`：带 `[name]` 前缀的 `confirm`，以及 `ask_user`）。转播只携带子代理的 `PermissionDecisionEvent`，那是决策做出之后的报告。

### 5.8 晚订阅

`events()` 没有回放：订阅者只收到订阅之后发布的事件。
- 用 `session_id=None` 订阅一次的宿主，从一开始就能看到每个子代理事件，因为它在运行存在之前就已订阅。**这是读取子代理通道的推荐方式**，也和 `subscribe()` 自己的建议一致（§4.2）。
- 看到 `spawned` 后再用 `session_id=S` 订阅的宿主，可能错过子代理最早的事件。面向宿主的文档要写明。
- 不做订阅时快照。

### 5.9 顺序：`spawned` 先于这次运行的子代理事件

保证的是因果顺序，不是全局顺序，并且只对一类订阅成立：**对在 `spawned` 发布之前已建立、并持续有效的 `session_id=None` 订阅，在不在事件循环上的路径上，一次运行的事件按 `spawned` → 子 session 事件 → 终态事件的顺序到达。** 不承诺不相关 session 之间的顺序，宿主也不需要缓冲协议。

对其他订阅不成立，面向宿主的文档要写明：
- `session_id=S` 的订阅永远收不到 `spawned` 和终态事件，因为它们在父代理的 session 里；
- 在 `spawned` 之后才建立的订阅可能已经错过它（§5.8）。

为什么对这类订阅成立：
- 两条路径都在子代理存在之前发布 `spawned`：前台在同一线程上、`_run_sync` 之前；后台在 `Thread.start()` 之前。
- 不在事件循环上时，`publish` 要等事件进入每个匹配订阅者的队列才返回，队列满时也是如此（§4.2）。每个队列先进先出。子代理事件发布得更晚，所以排在 `spawned` 之后。

限制：**在事件循环上**时 `publish` 不等待，超过上限会丢弃。宿主只有在 `events()` 绑定的事件循环线程上同步调用 `chat()` 才会走到这条路径。面向宿主的文档对不在事件循环上的路径给出保证，并写明这个边界情况。同样的推理，加上 §5.4.3，给出"这次运行的子代理事件先于它的终态事件"。

## 6. 解决了什么，没解决什么

| # | 代价（§2） | 开启转播后，对读 `events()` 的宿主 |
|---|---|---|
| 1 | 打标 | 解决。`session_id` 就是归属。 |
| 2 | id 作用域 | 解决。子代理的 `tool_call_id` 在子代理的 `(session_id, turn_id, …)` 作用域内。 |
| 3 | 白名单 | 对这条通道解决。投影层已经决定了哪些是公开的，不需要按事件类型逐个决定是否转发。 |
| 4 | 两个名字 | 对这条通道解决。`ToolLifecycleEvent.tool_name` 在每个阶段都是裸名字。 |
| 5 | 悄悄丢失 | 改善。observer 抛异常会记日志。队列满时阻塞，或在事件循环上时丢弃并打 warning。 |
| 6 | 同步 | 不变。背压按契约阻塞子代理。 |

对 transport 宿主没有任何变化，#579 仍是他们的工具。回放没有变化（§5.5）。

## 7. 不变量

| # | 不变量 |
|---|---|
| 1 | 转播关闭（默认）时，父代理的 `events()` 流里没有子 session 事件。生命周期事件补充关联字段（`child_session_id`，以及 #580 的 `parent_tool_call_id`）。其他不变。 |
| 2 | 每个 `SubagentLifecycleEvent` 的 `session_id` 都是父代理的，并且每次运行（前台和后台）都设置了 `child_session_id`。 |
| 3 | 每个转播事件上子代理的 `session_id` 等于这次运行的 `child_session_id`。 |
| 4 | 转播开启时，子代理发布的每个 `HostEvent` 原样到达父代理的流，且只到达一次。 |
| 5 | 对在 `spawned` 之前已建立、并持续有效的 `session_id=None` 订阅，不在事件循环上时：一次运行的 `spawned` 先于它的转播事件，它的转播事件先于它的终态事件。 |
| 6 | 每条路径上，解除挂载都在 drive 结束之后，并且先于终态事件和 close。每条路径保留现有的 close 与终态事件的先后。 |
| 7 | 两次运行的 `child_session_id` 不同。 |
| 8 | 回放不记录转播事件。回放的行和转播关闭时相同。 |
| 9 | transport 转发器的输出不变（桥接文档的不变量 1–11 仍然成立）。 |
| 10 | 确认和 `ask_user` 仍然走 transport，带 `[name]` 前缀。 |
| 11 | `publish` 抛异常的 relay 不会让子代理的运行失败（`EventStream.publish` 里的 observer 隔离）。 |

## 8. 计划

1. **#580**（`parent_tool_call_id`、`host.events.v3.json`、回放 1.6）。§5.1 第一层关联的前提。
2. **子代理 session id**（§5.2、§5.3）：在 `spawned` 之前生成、私有 adopter、填上 `child_session_id`、把生命周期事件的 `session_id` 固定为父代理的。不改 schema，可以和 #580 一起发布。
3. **转播**（§5.4、§5.5）：开关、observer、解除挂载边界、回放跳过集合、observer docstring 修改。前台和后台。文档：`docs/reference/host-api.md` 及中文版（一节讲子代理 session，写明 §5.8 和 §5.9 的限制）、开发者指南 part 4.7 和 part 2 构造器参考（中英；part 5 没有讲子代理事件的页面）、`CHANGELOG.md`。

## 9. 测试

- **子代理 session id：** `spawned` 和终态事件带同一个 `child_session_id`；它等于子代理自己的 `ToolLifecycleEvent` 的 `session_id`；前台和后台都覆盖；两次运行不同；生命周期事件的 `session_id` 是父代理的。
- **转播关闭：** `None` 订阅者看不到任何子 session 事件（不变量 1）。
- **转播开启：**
  - 子代理的一次工具调用产生转播的 `started` / `completed` `ToolLifecycleEvent`，带子代理的 `session_id`；
  - 子代理的权限决策被转播；
  - `session_id=P` 的订阅者看不到其中任何一个；
  - relay 能转播它没有专门处理的事件类型（在子代理流上发布一个合成的 `HostEvent`）。
- **顺序：** 对 `None` 订阅者，`spawned` 先于第一个转播事件，最后一个转播事件先于终态事件。队列大小设为 1、消费方比子代理慢，以走到队列满的路径。前台和后台都覆盖。
- **解除挂载：** 终态事件之后没有转播事件，覆盖成功、未完成、异常、抛出的取消，以及每种情况下的后台运行。断言解除挂载先于终态事件和 close，前台和后台都覆盖。
- **跳过集合：** 这次运行的 `child_session_id` 在挂上 relay 之前登记，在解除挂载之后移除，drive 抛异常时也是如此。每次运行结束后，该运行的 `child_session_id` 已从集合中移除；所有运行结束后集合为空，多个后台运行并存时也是如此。
- **抛异常的 relay** 不会让运行失败。
- **开启转播时的回放：** 子代理有一个**并行**批次（只读模式），以及一个后台子代理。回放文件的行和转播关闭时相同。这正好在 rev 1 写错的情况上钉住不变量 8。
- 现有转发器测试（`tests/test_subagent_tool_call_id.py`、`tests/test_subagent_child_task_id.py`）不改动即可通过。
- 驱动真实的子代理 `ToolRunner`，不要用假 emitter：转播的价值就在于它带的是子代理真正发布的东西。

## 10. 非目标

- 不改 transport 转发器、它的白名单和 payload。
- 不做按子代理的 transport 通道（子代理的 `AgentEvent` 全量流）。它会带未经投影的原始 payload（`LLM_CALL_IO`、推理内容），而且订阅者无法在子代理的 transport 存在之前订阅。
- 不为晚订阅做订阅时快照（§5.8）。
- 不加 `EventStream.relay_to()`。修改 docstring 就能覆盖 relay（§5.4.4）。
- 不做按次的转播开关。
- 回放不记录、不分组子代理的行（§5.5）。
- 不保证跨 session 的全局顺序（§5.9）。
- 不改子代理到父代理的结果投递（后台通知保持现状）。
- 不支持嵌套子代理。

## 11. 已决定的问题

1. §5.4.2：开关命名为 `relay_subagent_events`。
2. §5.9：面向宿主的文档统一推荐在任何异步环境中使用 `arun()`。在绑定的事件循环上调用同步 `chat()` 被写明为顺序保证不成立的边界情况。
3. 子代理的汇总信息（计数、usage）继续放在终态 `SubagentLifecycleEvent` 上，和现在一样。它仍是来源，不会把转播的行累加进去。
