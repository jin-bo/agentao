# 子代理运行期安全计划 —— 引擎按身份继承、registry 按来源重建、MCP 所有权与取消

> ⚠️ **状态：** 本计划原是 `powershell-support-plan` 的 **PR-0**，2026-09-03 按原文拆出（rev 25），与
> PowerShell 无关，也不等 PowerShell。**引擎那一半是一处已实测、早于该计划的活缺陷（证据 §2.12：子代理
> 没有权限引擎，`rm -rf /` 判 ASK 而三条 transport 自动批准），应立即修。MCP 那一半（所有者线程、租约、
> token → task 集合、调用上下文）未授权实施** —— rev 20 在那里发现一句有保证、没机制的话（§3 第 4 件）。
> 这两半在同一个 PR 里，因为工厂重建 registry 时必须决定 MCP 工具以什么形式进入子代理；先发引擎那一半、
> 把 MCP 视图留到后一个 PR 是允许的，§5 写明了切法。

**日期：** 2026-09-03
**Anchors:** agentao `main@3537753`（2026-09-01）。
**证据：** 本文写「§2.x」时，指 `docs/reference/powershell-support-evidence.zh.md` 的同号小节 ——
§2.6、§2.8、§2.9、§2.12–§2.19 是本计划的全部实测依据。
**评审记录：** `docs/design/powershell-support-review-log.zh.md`；本计划的历史与 PowerShell 计划共用那张
修订表（rev 2、4、5、7–11、20、22–24 的行都点到 PR-0）。
**PowerShell 计划对本计划的依赖：** `docs/design/powershell-support-implementation.zh.md` 的 PR-1 依赖
本计划（子代理必须按身份持有父级的 shell spec），见规范 `powershell-support-spec.zh.md` §6。

## 0. 不变量索引

规则 ID 只在本文定义一次；本文之外只引用 ID。定义正文是 §2–§4 的原文（自 rev 24 原样移入，未改写）；
本表是索引，不是第二份定义。

| ID | 不变量 | 定义所在 | 门槛 |
|---|---|---|---|
| **SUB-01** | 子代理由内部工厂 `Agentao._for_subagent(parent, definition)` 构建，不经公开构造参数；`permission_engine`、父级那一个有效的 `filesystem` 与 `shell`、`working_directory` **按身份**共享 | §2 第 1 项 | G00、G13b |
| **SUB-02** | registry 针对子代理重跑 `register_builtin_tools`，逐来源、从父级**活** registry 与定义白名单求交重建；`ToolRegistry.register` 增加仅关键字 `origin`（默认 `host`），替换记下顶掉了什么 | §2 第 2 项与表 | G00、G17 |
| **SUB-03** | 未实现 `ToolForkable` 的宿主工具在子代理中缺席，**且它占的名字也缺席**；`mcp_*` 仅经作用域视图、绝不经 `enabled_tools`/`remove_tool` | §2 第 2 项的表 | G00 |
| **SUB-04** | agent 工具仅当定义**显式点名**时注册；`None` 白名单蕴含每一个非 agent 来源、不蕴含任何 agent 来源；`agent_manager = None` 删除 | §2 第 2 项的表 | G22 |
| **SUB-05** | 工厂跳过 `__init__` 的注册过程，删除四处事后赋值（`sub_agent.tools =`、`tool_runner._permission_engine =`、读文件、`engine.set_mode`）；保留 `set_readonly_mode(True)` | §2 第 3、4 项 | G00 |
| **MCP-01** | `McpClientManager` 持有一个**所有者线程**运行 loop，每处同步桥接改为 `run_coroutine_threadsafe(...).result(timeout)`；`scoped(names) -> McpToolView` 是非所有的只读视图 | §3 首段 | G00 |
| **MCP-02** | `bg_store` 在 token 旁记**线程**；子代理执行体包进 `try/finally`，`finally` 关掉子代理并注销其视图 | §3 第 1、2 件 | G00 |
| **MCP-03** | 租约只是一次在飞行中的调用，逐调用取得、`finally` 释放，不论调用方是谁；agent 生命期是视图的注册，不是租约 | §3 第 3 件 | G00 |
| **MCP-04** | 取消经**调用上下文**（显式参数或 `contextvar`）抵达那次调用，绝不经工具实例上的可变属性；manager 以 token 为键登记 **task 集合**；订阅与登记原子（`add_done_callback` 在已取消时立即回调）；`finally` 按「注销回调 → discard → 删空键 → 释放租约」清登记 | §3 第 4 件 | G00 |
| **MCP-05** | 父级 `close()` 只走一条序列且**取消排在等待之前**：`CLOSING` → 拒绝新租约 → 取消每个 token → 同一 deadline 下等租约归零并 join 已记录线程 → 断开并停 loop；超时的 `result(timeout)` 不是取消，仍须经 loop 调度 `task.cancel()` | §3 第 5 件与末两段 | G00 |
| **MCP-06** | `close()` 只拆这个 agent 自己拥有的东西；引擎、fs、shell、MCP manager 与 `bg_store` 按身份共享，子代理的 `close()` 只注销自己的视图、一样都不碰 | §3 「`close()` 只拆…」段 | G00 |
| **ENG-01** | 引擎状态是一份不可变记录：值本身冻结（规则归一化为元组），每个修改者在一把 `threading.Lock` 下加载、赋新记录；每个读者不持锁读一次 `self._state` | §4 第 1、3、4 点 | G19 |
| **ENG-02** | 不把后备对象交出去：`rules` 与 `active_mode` 复制后返回，构造函数收下的列表改不动政策 | §4 第 2 点 | G19 |
| **ENG-03** | `_active_cache` 不在记录里，按记录身份作键、随记录丢弃 | §4 第 5 点 | G19 |
| **ENG-04** | `PermissionDecisionDetail` 携带它据以裁定的记录；宿主投射报告裁定自己的快照 | §4 第 6 点 | G19 |
| **ENG-05** | 写者锁只由修改者取、不在工具执行内部取；与 runner 的逐工具锁永不嵌套 | §4 末段 | G19 |

## 1. 缺陷

证据 §2.12 实测：wrapper 用固定关键字列表构造子代理，不传 `permission_engine=`；runner 把 `None` 复制进
planner，而 wrapper 事后那句 `tool_runner._permission_engine = engine` 写的是 planner 从不读的属性。结果
是子代理对 `rm -rf /` 判 ASK，而 `NullTransport`、`SdkTransport` 与 CLI `full-access` 三条路径自动批准
（§2.6）。§2.13–§2.19 逐一记录了「从磁盘重建」「按白名单重建」「事后赋值」「共享实例」「共享 MCP loop」
为什么都不是修法。

## 2. 决策 —— 子代理由内部工厂按父级活状态构建

**PR-0 —— 子代理由内部工厂 `Agentao._for_subagent(parent, definition)` 构建，不经公开构造参数。**
§2.16 表明 `enabled_tools=` / `extra_tools=` / `remove_tool` 每一个都有一道守卫或一种语义，会把
这种用法打败。该工厂：

1. **按身份共享能力**：`permission_engine`、父级那一个有效的 `filesystem` 与 `shell`、
   `working_directory`（按解析后的值比较）。
2. **针对子代理重跑真正的注册通道，registry 记录来源。** 「按类重建的实例快照」这条路不存在
   （§2.19）：六个内置工具的依赖来自 agent，所以唯一正确的构造就是已经存在的那一条 ——
   `register_builtin_tools(sub_agent)`，把子代理的 `_disable_tools` 设为父级已禁用的名字**加上**定义
   白名单之外的每一个内置名，而这正是该通道本就认的那一道过滤
   （`agentao/tooling/registry.py:135-136`）。于是每一项依赖都是子代理自己的：它的 transport 撑起
   `AskUserTool`，它的 `todo_tool` 是它自己的列表。同时 `ToolRegistry.register` 增加 `origin`
   —— `builtin | host | mcp | agent | plan` —— 与实例并存（`agentao/tools/base.py:209`）；替换还要
   记下它顶掉了什么，下表第四行才可判定。**它是仅关键字参数，默认 `host`，不是必填。** 在仓的注册点
   都显式传它 —— `_bind_and_register`（`agentao/tooling/registry.py:80`）、MCP
   （`agentao/tooling/mcp_tools.py:144`）、agent 工具（`agentao/tooling/agent_tools.py:104`）、plan
   工具（`agentao/cli/app.py:336`），以及子代理自己的 registry 与 `CompleteTaskTool`
   （`agentao/agents/tools/_wrapper.py:465-466`）—— 但 `agent.tools.register(...)` 是宿主已经在用、
   本仓示例也在调的一条路（`examples/ticket-automation/src/triage.py:199-202`），改成必填就会在那个
   唯一声称「无用户可见变化」的 PR 里造成用户可见的破坏。`host` 同时也是 fail-closed 的默认值：未归类
   的工具就是宿主工具，而不能 fork 的宿主工具在每个子代理里都缺席。逐来源，读自父级*活* registry 并与定义白名单求交：

   | 父级中的来源 | 子代理中 |
   |---|---|
   | 内置，存在且在白名单内 | 由 `register_builtin_tools(sub_agent)`（`agentao/tooling/registry.py:83`）以子代理自己的依赖构造 |
   | 内置，被父级禁用或移除，或不在白名单内 | **缺席** —— 它的名字加入子代理的 `_disable_tools` |
   | 实现了 `ToolForkable` 的宿主工具（`extra_tools` 或 `add_tool`） | `fork_for_agent()` 的新实例，以 `_bind_and_register`（`agentao/tooling/registry.py:77-80`）绑定 |
   | 未实现 `ToolForkable` 的宿主工具 | **缺席，且它占的名字也缺席** —— 替换了 `read_file` 却不能 fork 的宿主，不会在底下拿回内置 `read_file`；一次点名警告 |
   | agent 工具 | 仅当定义**显式点名**时，经 `_register_agent_tools` 针对*子代理*重新注册，使 wrapper 捕获子代理的 getter（§2.17）；否则**一个都不注册** —— 工厂跳过 `_register_agent_tools()`，`agent_manager = None`（`agentao/agents/tools/_wrapper.py:541`）删除。**「在白名单内」在这里不够：** `tools:` 键缺席意为*全部工具*（`agentao/agents/manager.py:57`），而内置 generalist 恰好省略了它（`agentao/agents/definitions/generalist.md:1-4`），把 `None` 白名单读成「全部」就会把 `agent_generalist` 交给它自己，恰好恢复那句赋值本要防的递归。`None` 白名单蕴含每一个**非 agent** 来源，不蕴含任何 agent 来源 |
   | `mcp_*` | 仅当白名单点名时，经下述作用域 MCP 视图；绝不经 `enabled_tools` 或 `remove_tool`，它们的守卫（`agentao/agent.py:489`、`agentao/agent.py:953`）原样保留 |
   | 仅限 plan | 永不 |

   最后加入 `CompleteTaskTool()`。结果是 runner 与 planner 同样持有的那一份 registry（门槛 17）。
3. **跳过** `__init__` 的内置、MCP 与 agent 注册过程；事后不向 `sub_agent.tools` 赋值
   （`agentao/agents/tools/_wrapper.py:538` 删除）、不向 `tool_runner._permission_engine` 赋值
   （`agentao/agents/tools/_wrapper.py:570` 删除）、不读文件（`agentao/agents/tools/_wrapper.py:559-562`
   删除），`engine.set_mode(mode)`（`agentao/agents/tools/_wrapper.py:569`）删除。
4. **保留** `set_readonly_mode(True)` —— runner 自己的字段（`agentao/runtime/tool_runner.py:106-109`），
   规划时读取；以参数传 `project_instructions` 与 `skill_manager`
   （`agentao/embedding/factory.py:146-148`）；保留 `llm.omit_temperature` 并注释点名其读者。

## 3. 决策 —— MCP 所有权：一个所有者线程，一个非所有的视图

**MCP 所有权：一个所有者线程，一个非所有的视图。** 在桥接处加锁是选错了器械 —— 它只把调用者串行
化，每次拿到锁的仍可能是另一个 OS 线程在驱动 loop（§2.18）。`McpClientManager` 改为持有一个**所有者
线程**，由它创建并运行 loop，每处同步桥接改为
`asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)`，取代裸的 `run_until_complete`
（`agentao/mcp/client.py:999`）。在此之上再增加 `scoped(names) -> McpToolView`，一个只暴露白名单工具
的、覆盖父级连接的只读视图。工厂注册的就是该视图；它不持有每 agent 状态，且**非所有** —— 子代理的
`close()` 既不断开共享 manager，也不停掉 loop（`agentao/agent.py:1015-1017`）。

**另一向是所有者先关，而光有租约做不到。** 后台子代理跑在一条 daemon 线程上
（`agentao/agents/tools/_wrapper.py:761`），它的句柄在 `start()` 处就被丢掉，而 `bg_store` 逐 agent
登记的是一个 `CancellationToken`（`agentao/agents/bg_store.py:490`、
`agentao/agents/bg_store.py:494`）而不是线程 —— 进程里没有任何东西 join 得了它，manager 更没法 join
一个没人记录过的东西。也没有释放点：正常返回是 `return result, stats`
（`agentao/agents/tools/_wrapper.py:653`），它不关任何子代理。五件事，全都是扩展已有的东西，而不是
在旁边另建一套：

1. `bg_store` 在它已持有的 token 旁边再记**线程**，`cancel`（`agentao/agents/bg_store.py:380`）语义
   不变。
2. 子代理执行体包进 `try/finally` —— 前台后台都是 —— `finally` 里关掉子代理，同时注销它的视图。
3. **租约只是一件事：一次在飞行中的调用。** 它不是 agent 的生命期，两者行为不同。每次 MCP 调用为
   自己的时长取一份租约、在 `finally` 里释放，不论调用方是谁 —— 后台子代理、
   前台 turn，还是嵌入宿主自己的线程。agent 的生命期是*视图*的注册，那不是租约。
4. **取消一个 token 必须真的到达那次调用，而今天没有任何东西把它送过去。**
   `McpTool.execute()` 是同步的，只收 `**kwargs`（`agentao/mcp/tool.py:118`），而执行器只把 token
   注入「本来就带着那个属性」的工具 ——
   `if cancellation_token and hasattr(tool, "_cancellation_token")`
   （`agentao/runtime/tool_executor.py:351-352`）—— 在树内只有 `AgentToolWrapper` 带
   （`agentao/agents/tools/_wrapper.py:220`）。`McpTool` 没有这个属性，于是被取消的 `bg_store`
   token 根本到不了所有者 loop 上那个协程：第 5 步的取消是空操作，接着整份 deadline 都花在等一份
   没人要求它释放的租约。所以同步桥接改为接收一个**调用上下文**、由它携带 token —— **显式参数或
   `contextvar`，而不是执行器已经在写的那个可变属性。** 那个属性挂在工具实例上，执行器只在 token 为真
   时才写（`agentao/runtime/tool_executor.py:351-352`），而逐工具的那把锁只在*一个 batch 之内*串行化
   （`agentao/runtime/tool_executor.py:200-202`）—— 于是一次不带 token 到来的调用（宿主直接调
   `ToolRunner.execute()` 就是这种），读到的是上一次调用留在那里的东西。残留的若是一个**已取消**的
   token，后果不是无害而是最坏：`add_done_callback` 对已取消的 token 立即回调，新调用一登记就被取消。
   上下文是逐调用、逐 worker 线程的，留不下东西 —— 而 manager 把该租约的 `asyncio.Task` 登记进**以这个
   token 为键的一个集合**，不是「一个 token 一个 task」。**一个 token 覆盖的是一整批：**
   `execute_batch(plans, *, cancellation_token=None, …)`
   （`agentao/runtime/tool_executor.py:188-192`）对批里的每个 plan 只收一个 token，于是一个子代理
   发出的 N 个并行 MCP 调用，就是 N 个 task 挂在同一个键下，而用 `dict` 只会留下最后一个 —— 取消之后
   还剩 N−1 个调用在跑，而 manager 以为自己已经取消了它们。取消 token 会取消集合里的**每一个** task，
   每个都**经 loop**（`loop.call_soon_threadsafe(task.cancel)`，绝不在调用线程里直接
   `task.cancel()`），而每个被取消的协程在自己的 `finally` 里释放自己那份租约，那就是第 5 步等的确认。
   **那个 `finally` 同时把登记表清空，顺序只有一种：** 注销取消回调 → 把该 task 从它的集合里
   `discard` → 集合空了就删掉这个键 → 释放租约。一个只进不出的 manager 会按调用累积 task 引用，在
   长寿命的所有者上这是泄漏、不是裁定错误，而门槛 0 断言正常完成与被取消两条路径上登记表都为空。
   上下文是逐调用的，所以这一套并不依赖「每个 agent 注册的是**它自己的** `McpToolView` 实例」（上面
   那个作用域视图）—— 它确实如此，而属性那种写法本来要依赖这一点。
   **而且这次订阅与登记是原子的，不是排在登记之后：** `CancellationToken.add_done_callback` 在 token
   已被取消时立即回调，并交回一个给 `finally` 用的注销句柄（`agentao/cancellation.py:97-105`），于是
   落在「租约已取、task 未登记」之间的取消照样取消得到。此前那个回答 —— 名下没有 task 的 token 是一次
   还没开始的调用，而 `CLOSING` 拒绝新租约 —— 只覆盖 `close()`：普通的 `bg_store.cancel()` 根本不进
   `CLOSING`，没有原子订阅的话，稍后登记的那个 task 会继续跑下去。
5. 父级 `close()` 只走一条序列，而且**取消排在等待之前**：`CLOSING` → 拒绝新租约 →
   **取消每一个 token**（依第 4 件，这会取消登记在它名下的每一个 task）→ 在**同一个** deadline 下既等
   活动租约计数归零、也 join 每一条已记录的线程 → 断开并停掉 loop，放弃了什么就记日志。顺序是要紧
   的：一次长 MCP 调用只有被取消才会释放租约，所以先等计数下
降，就是把整份预算花在等一件根本没被要求停下来的事。排空租约是主要的等待，join 线程是次要的：前台或
宿主线程的调用持有租约却不在任何线程集合里，只等 `bg_store` 那些线程的设计，恰恰会在它不知道的那些调
用方底下断开连接。

**`close()` 只拆这个 agent 自己拥有的东西，而子代理对交给它的东西一无所有。** 那个 store 是父级的，
构造时传进来，好让子代理的 registry 服务得了 `check_background_agent` —— *"Inherit the parent's
background-task store"*（`agentao/agents/tools/_wrapper.py:522-527`）。没有这条规则，上面第 2 步就
不是修复而是缺陷：子代理的 `finally` 去跑第 5 步，会取消掉它的**兄弟**，还会去 join 它自己正跑在
上面的那条线程。所以所有权在构造时记录 —— 引擎、filesystem、shell、MCP manager 与 `bg_store` 全都是
*按身份共享*的，子代理的 `close()` 只注销自己的视图、冲掉自己的状态，一样都不碰它们 —— 它没有租约可释放，因为租约就是一次在
飞行中的调用，而 `close()` 不是。只有拥有者的
`close()` 才跑第 5 步。门槛 0 断言仍在运行的兄弟不受影响。

**而 manager 分段关，因为「取消」不等于「拒绝」。** 一条熬过 join 预算的线程仍然活着，否则它会在
manager 认定收工之后再申请一份新租约。`McpClientManager` 先进入 `CLOSING`，在该状态下拒绝每一次新的
租约申请；然后才取消、等完预算、断开。

而超时的 `result(timeout)` **不是**取消 —— 协程仍在所有者 loop 上跑 —— 所以超时路径还要经由该 loop
调度 `task.cancel()`，这正是 `agentao/tools/web.py:634-639` 在自己的线程移交上早已遵守的规则。门槛 0
与回调、todo 检查一并核查两个方向。

## 4. 决策 —— 引擎：一把写者锁、无锁读者、携带快照的裁定

**引擎：一把写者锁、无锁读者、以及携带其快照的裁定。** 把引擎的可变字段坍缩成一份原子交换的记录，
修好的是撕裂读（`agentao/permissions.py:597-598` 写，`agentao/permissions.py:702`、
`agentao/permissions.py:705`、`agentao/permissions.py:712` 读），**不是**丢失更新：两个修改者加载
同一份旧记录、各自赋一份新的，输家的改动被丢掉 —— 并发 `set_mode` 下 `add_run_rules` 的 deny。于是：

- **状态里的值是不可变的，不只是被整体换掉。** 只做到原子记录还不够：`_mode_rules` **就是**模块级
  preset 列表本身而非它的副本（`agentao/permissions.py:598`），`add_run_rules` 又原地 extend 那些活
  列表（`agentao/permissions.py:633`、`agentao/permissions.py:635`）—— 于是持有记录的无锁读者仍然
  别名着另一个线程正在增长的列表，而拿到 `rules` 的调用者能改掉进程内每一个引擎的 preset。规则在
  校验器边界归一化为冻结值，状态持有它们的元组，每个修改者都新建元组。
- **不把后备对象交出去。** `rules` 与 `active_mode` 作为兼容属性保留，但复制后交出 —— 引擎自己就有
  它们的读者（`agentao/permissions.py:810`），外面还有不知道的 —— 于是无论调用者改的是交给它的东西，
  还是它传给构造函数的那个列表（`agentao/permissions.py:579`），都改不动任何政策。
- **每个修改者**（`set_mode`、`add_run_rules`、`add_loaded_source` 及任何宿主 setter）在一把
  `threading.Lock` 下运行，在锁内加载当前记录、在释放前赋值新记录。
- **每个读者**（`decide_detail`、`active_permissions`）不持锁加载 `self._state` 一次，只从那份记录读。
- **`_active_cache` 不在记录里。** 一份在更新记录安装之后才写回的缓存推导会复活旧政策。缓存按记录
  身份作键，随记录一起丢弃。
- **`PermissionDecisionDetail` 携带它据以裁定的记录。** 宿主投射在之后的另一时刻调
  `active_permissions()` 来构建事件（`agentao/host/projection.py:245`）；现在它报告裁定自己的快照，
  于是事件点名的 mode 与规则集就是产出裁定的那份。

**为什么这把写者锁不会与 runner 那把锁形成死锁：** runner 的逐工具锁在*执行*期间持有
（`agentao/runtime/tool_executor.py:405`）；`decide_detail` 是*规划*调用，不取锁；写者锁只由修改者
取，而没有任何修改者会在工具执行内部被调用。两族锁永不嵌套。
## 5. PR-0

| PR | 内容 | 用户可见 | 依赖 |
|---|---|---|---|
| **PR-0** | （门槛 0、19、22）**`Agentao._for_subagent`：父级引擎、一个有效 fs/shell、每次注册都记 `origin`、以针对子代理重跑 `register_builtin_tools` 的方式重建 registry、`ToolForkable`、MCP 所有者线程 + 非所有的作用域视图、不注册 agent 工具；引擎状态在一把写者锁后面不可变、裁定携带其快照；投射报告裁定的快照**（§2.12–§2.19） | 否 —— 关上一处活绕过 | — |

**PR-0 不需要 PowerShell 计划的任何东西** —— 一个内部工厂、registry 上的一个来源字段、一个协议、一个视图、
一个所有者线程、一把锁、一份「token → task **集合**」登记表连同喂给它的调用上下文，以及裁定详情上的一个
字段。反向依赖只有一条：PowerShell 计划的 PR-1 要求子代理按身份持有父级的 shell spec（SUB-01），所以那边
的阶梯把 PR-0 列为前置。

**允许的切法：** 引擎那一半（SUB-01–SUB-05、ENG-01–ENG-05；门槛 G00 里不涉及 MCP 的断言、G13b、G17、
G19、G22）可先发；MCP 那一半（MCP-01–MCP-06；G00 里的 MCP 断言）后发。切开发时，工厂在第一段里对
`mcp_*` 的处理是**缺席**（与不可 fork 的宿主工具同一行），而不是共享父级实例 —— 共享实例正是 §2.15/§2.18
说不能做的事。

## 6. 门槛

- **G00 · PR-0 的探针**（§2.12）经 `NullTransport` 返回 DENY，前台与后台都如此；带内存 deny、run-scope
   deny 与 `enable_hardline=False` 的父级产出的子代理三者都遵守；readonly 父级产出 readonly 子代理；
   子代理的引擎、filesystem 与 shell 按身份是父级的，工具不是父级的实例；后台子代理跑过工具后，父级的
   `output_callback` 与 todo 列表未变；父级禁用的内置工具在子代理中缺席；白名单外的可 fork 宿主工具
   缺席；替换了 `read_file` 的不可 fork 宿主工具让子代理没有 `read_file`；定义未点名任何 agent 工具的
   子代理有零个 `agent_*` 工具；父级与后台子代理并发调用同一 MCP 服务器都正确完成。**子代理的
   `ask_user` 抵达子代理的 transport、它的 `todo_write` 写它自己的列表 —— 那六个由 agent 提供依赖的
   内置工具是从子代理构造的（§2.19）；每个注册的工具都带 `origin`，替换了内置的宿主工具记下它顶掉了
   什么；子代理的 `close()` 之后父级的 MCP 连接与 loop 仍活着，**且仍在运行的兄弟子代理毫发无损 ——
   它一个 token 都不取消（除了自己的），一条线程都不 join，尤其不 join 它自己正跑在上面的那条**；
   在飞行中的那次调用发生在**前台 turn 或宿主自己的线程**（它不在任何线程集合里）时同样成立：
   `close()` 等租约排空，那次调用跑完；
   经裸 `agent.tools.register(tool)`
   注册的工具照样注册成功，来源为 `host`。还有另一向：后台子代理正处在一次长 MCP 调用中时关闭
   父级，会先取消并 join 它再断开，而超时的 `result(timeout)` 不会把协程留在所有者 loop 上。
   **而且断言取消是*到达*了，不只是被发出了：** 后台子代理正处在一次长 MCP 调用中，它的 `bg_store`
   token 被取消，登记在其名下的 task 在所有者 loop 上被取消，它的 `finally` 释放租约，之后 loop 上
   没有活着的 task —— 只检查 `close()` 有返回的测试，在今天的代码上也会通过，而今天 `McpTool` 根本
   收不到那个 token（`agentao/mcp/tool.py:118`、`agentao/runtime/tool_executor.py:351-352`）。
   **两道 barrier，测的都是这份登记表的形状，而不是它存不存在：** 一个子代理在一批里发出**两个**并行
   MCP 调用 —— 一个 token、两个 task —— 两个都被取消、两份租约都被释放，这是「一个 token 一个 task」
   的登记表过不了的；以及一个在自己的 task 登记**之前**就被取消的 token，照样取消得到那个 task ——
   断言方式是把登记卡在一个 barrier 上、直到 `cancel()` 返回之后，走的是根本不进 `CLOSING` 的普通
   `bg_store.cancel()` 那条路径。**

- **G13b · 子代理按身份持父级引擎**（原门槛 13 的后半；前半「快照抵达每个 root」留在 PowerShell 门槛矩阵 G13）。

- **G17 ·** 构造、`add_tool`、`remove_tool` 之后 registry 身份成立。

- **G19 · 并发、多写者与不可变性：** 后台子代理在紧循环里裁定的同时，父级从三个线程交错执行
    `set_mode` ×1000、`add_run_rules` ×100（**每次一条互不相同的 deny**）、`active_permissions`
    ×1000；之后那 100 条 deny 一条不少，每次裁定携带的快照内部一致，每个投射出的事件点名的是其自身
    裁定的快照。另有一组不开线程的：改动传给构造函数的列表、`rules` 返回的列表、返回的
    `ActivePermissions`、以及裁定携带的快照，之后每一次裁定都不变；随后新建的第二个引擎的 preset
    也未被改动。

- **G22 · 递归与缺省白名单：** 内置 generalist（`agentao/agents/definitions/generalist.md:1-4`）的定义
    没有 `tools:` 键，它的子代理拥有每一个非 agent 工具、以及零个 `agent_*` 工具，因而无法生成
    自身；显式点名了某个 agent 工具的定义只拿到那一个。

## 7. 待决问题与会改变本计划的事

1. **裸 `Agentao(...)` 该不该构造默认引擎？**（原 §9 q7）
2. **`_for_subagent` 该不该成为公开的 `Agentao.fork(...)`？** 自己生成子代理的宿主有 wrapper 曾有的
   同一个问题。（原 §9 q8）

会改变本计划的事：**某个 MCP 工具包装被发现持有每 agent 状态** —— 门槛 G00 的并发调用检查是它浮现的地方。
