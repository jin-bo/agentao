# 后台子代理：唤醒空闲的 CLI

**状态：** A、run 宿主改动与 C 已于 2026-09-26 **实施**（未发布，0.5.6 周期），host-api 配方同步补上；B 仍是单独的、未获授权的后续项。
**锚点：** agentao `main@22104ff`；codex `30fc6864cc1`（2026-09-23）；goose `9adae14b6`
（2026-09-25）；gemini-cli `87de0b6369`（2026-09-24）；pi-mono `d5629e204`（2026-09-24）。
**相关：** `codex-subagent-v2-vs-agentao.zh.md` §4.2（本文回答的轮询观察项）与 §3（并发上限 P1，
本文**有意不并入**）。

## 1. 结论

卡住的会话已在 `../dstation/agentao.log` 中找到（§8）。父代理用
`run_shell_command("sleep 300; …")` 等 3 个后台子代理；08:46:34 第三次相同的 shell 调用触发
死循环检测，**不是** `check_background_agent` 的轮询。最后一个子代理 09:04:02 完成，父代理直到
用户 11:32:03 输入 `continue` 才继续。前台子代理会在工具调用内部返回，不属于这次情况。

1. **没有东西唤醒空闲的父代理。** 任务完成时只是往队列里放一条通知，要等父代理发出下一次 LLM 请求
   才会被取出。如果父代理这一轮已经结束，CLI 就停在 `prompt()`，直到用户输入。
2. **现有状态工具不能在轮内等待。** 它立即返回，启动文案又鼓励重复查询。这次模型改用 shell
   `sleep`，反复调用后触发死循环检测并结束本轮。

方案：先交付 A 和 C，修复已观察到的 CLI 问题；同时在单轮的 `agentao run` 宿主关闭后台启动。
然后把 B 作为独立的跨宿主工作项实施，先通过取消与 ACP 客户端超时验证（§6.2）。

- **A（harness）.** 明确告诉模型不要用 shell `sleep` 或反复查询干等；无其他工作时结束本轮。
- **B（harness，独立工作项）.** 给 `check_background_agent` 加可取消、有上限的 `wait_seconds`，
  供 CLI、ACP 和嵌入式宿主在一轮内等待。ACP 无法使用 CLI 的空闲唤醒，所以是优先推进 B 的
  主要理由。
- **C（host：CLI）.** 交互式 CLI 空闲且有通知待注入时自动唤醒；同样的做法写成示例，给嵌入式宿主
  参考。
- **Run 宿主.** 构造时传 `bg_store=None`，防止单轮进程启动可能在本轮结束后被终止的后台工作。

首批交付无需新工具、新运行时概念或公开的宿主 API。B 只扩展现有工具，不增加“等任意一个/等全部”。

## 2. 两个机制

### 2.1 没有唤醒（主要）

- 任务进入终态时，`update()` 往队列里放一条
  `Background agent '…' (ID: …) completed.`（`agentao/agents/bg_store.py:432-445`）。
- **唯一**的消费方是 `_inject_background_notifications`
  （`agentao/runtime/chat_loop/_runner.py:1217`）。它在构建请求时于 `:1230` 取出队列，追加一条
  `<system-reminder>` 用户消息。
- 所以只有**再发生一次 LLM 请求**，通知才能到达模型。这次是死循环检测中止了父代理本轮；随后
  CLI 阻塞在 `cli._prompt_session.prompt(...)`（`agentao/cli/input_loop.py:63`）。
- 状态栏会跳到 ✓（`input_loop.py:108-128`），但没有任何东西开启新的一轮。

### 2.2 轮内无法等待（次要）

- `check_background_agent` 无论任务处于什么状态都立即返回
  （`agentao/agents/tools/_bg_tools.py:59-120`）。
- `run_in_background` 的描述明确说要轮询（`agentao/agents/tools/_wrapper.py:658`）；启动消息
  （`:1545`）也让模型调用 `check_background_agent` 取结果。
- 任何相同工具调用重复到**第 3 次**都会触发死循环检测，这次的 shell `sleep` 也一样
  （`agentao/runtime/tool_planning.py:36`、`:487-501`）。计数在整个 `chat()` 内累计，不要求连续。
  如果换着参数重复调用，则可能耗尽 `max_iterations`，CLI 随后询问用户是否继续
  （`agentao/cli/transport.py:234`）。

## 3. 参考项目的做法

| | 父代理怎么等子代理 | 父代理空闲时子代理完成，会发生什么 |
|---|---|---|
| codex | `wait_agent(timeout_ms)`：默认 30 秒，下限 10 秒，另有硬上限（`codex-rs/core/src/tools/handlers/multi_agents_common.rs:19-21`、`core/src/config/mod.rs:255`） | **什么都不触发。** 完成消息以 `trigger_turn: false` 发送（`codex-rs/core/src/agent/control.rs:494`），或用 `inject_fragment_without_turn` 注入（`:512`） |
| goose | `load(source: task_id)` **阻塞**到任务完成（`crates/goose/src/agents/platform_extensions/summon.rs:711`） | 什么都不触发。每轮追加一段状态（`get_moim`） |
| gemini-cli | 子代理只有同步模式（`packages/core/src/agents/agent-tool.ts:235`） | 不适用 |
| pi-mono | 子代理示例在**一次阻塞的工具调用**里跑 `parallel` / `chain`（`packages/coding-agent/examples/extensions/subagent/index.ts:164`、`:219`） | 不适用 |

两点解读：

- **B 有两个先例**：codex 的 `wait_agent` 和 goose 的阻塞 `load`，都在 harness 层。这次 shell
  `sleep` 说明模型想在轮内等待；A 加 C 能处理 CLI 的空等，B 则让 CLI、ACP 和嵌入式宿主按需在轮内等待。
- **C 在四个 harness 里都没有先例。** codex 是刻意不开新一轮的。这和 §5 的判断一致：唤醒是宿主的
  决定，不是 harness 的。

## 4. 回答 `codex-subagent-v2-vs-agentao.zh.md` §4.2

§4.2 记下“无 park 原语 → 只能轮询”，并问阻塞等待如何处理取消、前台阻塞、执行线程和宿主续跑。
B 用有界、检查令牌的工具以及 §6.2 的并行批次中断处理回答前三项。`arun()` 的本轮本来就会在整轮
占一个 worker；B 不另占共享的 `arun` worker，也不用事件循环的默认执行器。并行批次会用自己的
短生命周期线程池。宿主续跑是另一件事：C 让 CLI 在空闲且有待送通知时开新一轮（§6.3）。

## 5. 分层：每一步落在宿主与 harness 分界线的哪一侧

| 步骤 | 改动位置 | 层 | 首批交付 |
|---|---|---|---|
| A：文案 | `agents/tools/_wrapper.py` | Harness | 是，适用于暴露后台启动的宿主 |
| B：`wait_seconds` | `agents/bg_store.py`、`agents/tools/_bg_tools.py`、`runtime/tool_executor.py` | Harness | CLI、ACP、嵌入式宿主共用的独立后续项 |
| C：空闲唤醒 | `cli/input_loop.py`、私有 store 查看方法；host-api 示例 | Host | CLI 代码唤醒；嵌入式宿主得到示例 |
| Run 宿主 | `cli/run.py` 的工厂参数 | Host | 关闭后台启动 |

**轮内等待属于 harness 工具。** 暴露后台启动的宿主都能使用 B；ACP 无法使用 CLI 的空闲唤醒，
所以是优先推进 B 的理由。
`agentao run` 无须靠 B 保证安全：该宿主可直接从工具 schema 中移除后台启动。

| 宿主 | 首批交付后 |
|---|---|
| 交互式 CLI | 先得到 A 加 C：空闲时结束本轮，有待送完成通知时续跑；随后 B 可在必须先拿到结果时作一次有界轮内等待 |
| 嵌入式宿主 | 先得到 A 加可选择采用的 host-api 续跑示例；随后也可使用 B 轮内等待 |
| ACP | 先得到 A；随后 B 可在客户端允许长轮次时让父代理在一轮 prompt 内等待 |
| `agentao run` | 用 `bg_store=None` 隐藏后台启动；前台子代理仍在本轮返回 |

**唤醒是宿主的事。** 在没人请求的时候开新的一轮，属于会话生命周期，由宿主负责：

- ACP 规定一轮只能由客户端发起，harness 在那里本来就做不到；
- codex 的 harness 刻意不做（`trigger_turn: false`），goose 也不做；
- 嵌入式宿主可能不希望模型在没人触发时自己跑起来，比如计费、UI 还没准备好，或者有按轮计的配额。

所以 harness 永远不会自己开新的一轮，它只负责把“任务完成了”这个事实提供出来。

**宿主做判断所需的信息，harness 已经提供了，不需要新 API：**

- `Agentao.events()`（`agentao/agent.py:861`）会发出 `SubagentLifecycleEvent`。
- **后台**任务的事件带 `parent_task_id`（即它的 `agent_id`，`_wrapper.py:1426`）；前台路径派生时
  不带这个字段（`:720`）。
- 终态的 `phase` 是 `completed` / `failed` / `cancelled`。
- **在会入队通知的终态路径上，产生顺序有保证：** 通知先进队列，终态事件后发出。
  - 正常路径：`_wrapper.py:1481` 的 `bg_store.update()` 在 `:1502-1509` 的
    `_terminal_subagent_event` 之前；
  - 异常路径同样的顺序；
  - 取消一个还没开始的任务时，通知在 `cancel()` 里入队（`agentao/agents/bg_store.py:518-522`），
    早于 worker 发出 `cancelled`。
- 这**不保证**宿主收到事件时通知仍在队列中：正在运行的父代理可能已取走通知；新会话也可能清空或
  抑制通知。宿主须串行驱动自己的轮次、核对原会话，并把事件当作判断是否需要续跑的信号。

**CLI 不用这个事件，是出于实际考虑。** `events()` 是异步迭代器，而且有背压：消费得慢，就会阻塞产生
事件的一方（`agent.py:862-874` 的文档说明）。CLI 如果订阅，就得另起一个循环把所有事件都消费掉。
C 改为读取 CLI 本来每秒就在读的 store（状态栏用的就是它）。这是 **CLI 内部的捷径，不属于宿主契约**，
也不写进 `host-api.md`（§6.3）。

## 6. 方案

### 6.1 A（harness）：文案

- `_wrapper.py:658` 写着 "poll"；`:1545` 的启动消息要求调用 `check_background_agent`。
  两处改成大意：*"Do not wait with sleep or repeated status checks. Continue other work;
  if there is nothing else to do, end this turn. A background agent update can be read when
  this session next runs. Use `check_background_agent(agent_id=…)` only to inspect status
  when needed."* 不要承诺每个宿主都会自动开启下一轮。
- `check_background_agent` 的描述目前是 "Check"，本来没有 "poll"；保持如实描述。B 实施时再
  在这里区分一次有界等待与反复立即查询。

### 6.2 B（独立的跨宿主工作项）：有界轮内等待

- CLI 中 B 和 C 同时存在：父代理必须在本轮先拿到子代理结果才能继续时用 B；可以先结束本轮、完成后
  再继续时由 C 唤醒。启用后台启动的 ACP 与嵌入式宿主使用同一个 B 工具。
- 给 `check_background_agent` 增加可选整数参数 `wait_seconds`，默认 `0`，一次只等一个
  `agent_id`。候选上限为 **30 分钟**。store 可用任务锁上的 `threading.Condition` 等待终态，
  最迟每 0.5 秒检查本轮取消令牌；先释放 Condition 锁，再用 `get()` 重读任务记录：共用持久化
  文件的其他 store 完成任务时，不会唤醒本 store 的 Condition。未知或不属于当前项目的 id 应
  立即返回。默认值沿用现有结果格式；超时就报告任务仍在运行，提示模型结束本轮或取消子代理，
  不要再次发出相同的等待调用。
- 等待返回终态结果后，接受下一次 LLM 请求再次注入简短完成预览。目前立即调用
  `check_background_agent` 查到终态时也会这样；消除重复需要较大的通知队列改造。
- **取消等待，不取消子代理。** ACP 的 `session/cancel` 由另一条 dispatcher 线程直接设置本轮令牌
  （`acp/session_cancel.py:137`）；等待工具可在一次检查间隔内返回。子代理仍会运行，只有调用
  `cancel_background_agent(agent_id)` 才会停止它。
- **CLI 并行批次的 Ctrl+C：** 在 `runtime/tool_executor.py:231-248` 的
  `ThreadPoolExecutor` 上下文**内部**加中断处理，包住提交任务和 `as_completed`。捕获
  `KeyboardInterrupt` 后先取消本轮令牌，再重新抛出，让等待 worker 不会使线程池退出时等满
  `wait_seconds`；同批里不响应取消的其他工具仍可能拖慢退出。现有的 `turn.py:189-190`
  随后照常结束本轮。此路径须测试；Windows 上阻塞的
  `as_completed` 能否被 Ctrl+C 打断仍未核实。
- **不额外占用 `arun` worker：** `arun()` 本来就在整轮占一个 worker（`agent.py:1253-1304`）。
  单工具等待用本轮线程，并行批次用该批次自己的线程池；不占事件循环的默认执行器。
- **死循环保护不变。** 等不同 id 属于不同调用。一次足够长的等待可覆盖本次会话中子代理剩余的
  7–17 分钟工作。但如果从启动时立刻等待，子代理也可能超过 30 分钟；超时后应结束本轮或取消
  任务，不能重复等到第三次触发保护。这是有界等待，不保证所有子代理都能在同一轮完成。
- **ACP 验收门槛：** 用目标客户端实测长 prompt 是否有自身超时，以及如何显示进度。执行器
  已绑定工具的 `output_callback`，ACP 也已把 `TOOL_OUTPUT` 转成带节流的 `tool_call_update`。
  每次更新会重发此前累积的全部内容，因此进度应低频发送，例如每分钟一行；不另造进度 API。
  客户端验证通过前，30 分钟仍是候选上限。

### 6.3 C（host）：唤醒空闲的宿主

**CLI，写进代码**（`cli/input_loop.py` 加一个私有的 store 快照方法）：

- 在 `get_user_input` 现有的 `_ticker` 循环里（`input_loop.py:55-58`，每秒一次），以下条件
  **全部**满足时唤醒：
  - 输入框为空；
  - 没有暂存的图片；
  - 不在 plan 模式；
  - 没有正在跑的 `/goal` 循环（它本来就会驱动新的一轮）；
  - 有**新**的待注入通知。
- 增加私有 store 快照方法，在 `_notify_lock` 下返回 `(队列非空, 入队序号)`，不取走通知。
  `push_notification()` 和 `_push_task_notification()` 每次真正入队都让只增不减的序号加一；
  被抑制的通知不计数，取走通知和重置会话也不重置序号。`list()` 里的终态记录**不能**代替快照：
  `update()` 先改状态、后入队；重置会话也可能让通知不再入队。
- CLI 跨多次 prompt 保存 `last_auto_wake_sequence`。只有队列非空、且入队序号比上次自动唤醒
  时更大，才退出 prompt 唤醒；实际因 `_BG_WAKE` 退出时记下该序号。如果续跑轮在
  `_runner.py:423` 取通知之前就返回（如 `UserPromptSubmit` 在 `:309-310` 拦截），未变化的队列
  不会再触发自动续跑。后续用户轮仍能取走它；有新通知入队时可再唤醒一次。
- ticker 看到通知后，用 `app.loop.call_soon_threadsafe(...)` 安排回调。回调在 prompt 事件循环线程
  中，调用 `app.exit(result=_BG_WAKE)` **之前再次检查**输入框、暂存图片、plan 状态和新通知序号。
  如果用户已开始输入，就保留输入框。`Application.exit` 本身不是线程安全的。
- `run_loop` 收到 `_BG_WAKE` 时：
  - 在现有的空输入跳过逻辑之前处理这个标记；
  - 打印一行暗色的 `⟳ background agent finished — continuing`；
  - 用一条固定消息跑一轮普通的对话，比如
    `[Background agent finished — review the update and continue]`；
  - 现有的取出逻辑（`_runner.py:1230`）会把结果带进这一轮；
  - 轮次处理不做其他改动。
- **关闭开关：** CLI 设置项 `settings.json` 里的 `background_agents.auto_wake`，默认 `true`。
  本提案决定交互式 CLI 默认开启。不加斜杠命令。
- 按待送通知**批次**唤醒，不等全部已启动任务完成。新一轮的下一次 LLM 请求会取走当时积累的通知；
  若该轮结束后又有任务完成，可再次唤醒。运行中的轮次可能自己取走通知，也可能把多个完成消息留到
  下一次唤醒；**不保证每个任务各开启一轮**。
- **连续唤醒上限：** 用户两次提交之间最多连续唤醒 3 次。唤醒开的那一轮可能又启动后台子代理，
  它完成后再次唤醒；这个上限防止无人值守的会话在模型不断委派时一直花费轮次。第 3 次唤醒那一轮
  结束时打印一行暗色的"已暂停"提示；用户提交任何一行（包括空行）都会清零计数。此后排队的通知
  随用户的下一条消息交给模型。这是首批交付之后补的（#351 评审的后续）。

**单轮 `agentao run` 宿主**（`cli/run.py`）：调用 `build_from_environment` 时传
`bg_store=None`。工厂默认会创建 store（`embedding/factory.py:264-268`）；显式 `None` 会使
子代理工具 schema 隐藏 `run_in_background`（`agents/tools/_wrapper.py:645-653`）。`run.py` 在本轮
结束后不等待，后台 worker 又是 daemon 线程（`_wrapper.py:1539`），进程退出时可能切断它们。
这是读代码发现的风险，尚未在单轮运行中复现。

**嵌入式宿主，只补文档**（`docs/reference/host-api.md` 及 `.zh.md`）：

- 加一小段示例：收到带 `parent_task_id` 的终态 `SubagentLifecycleEvent` 时，只有原会话仍有效、
  没有轮次运行、当前轮尚未处理这次完成，宿主才安排续跑。事件只是信号，**不能证明**通知仍在队列
  （§5）。通过宿主正常的轮次驱动器调用 `chat()`，不要在事件回调中直接调用。
- 要不要唤醒，由宿主自己决定。重置后终态事件仍可能到达，但通知已被静默。
- 这一项**不带任何 harness 代码改动**。

## 7. 不在范围内

- **等任意一个 / 等全部：** 即 codex V2 的邮箱式等待。只有单 id 等待确实不够用时再考虑。
- **后台并发上限：** `codex-subagent-v2-vs-agentao.zh.md` §3 的 P1，仍未授权，是另一项决定。
- **由 harness 发起的任何一轮：** 在任何传输方式下，运行时都不自动续跑（§5）。
- **ACP 自动唤醒：** 一轮由客户端发起；客户端可参考嵌入式宿主示例，本提案不加服务端主动发起的
  prompt。
- **让等待不受死循环检测约束：** 目前没有证据支持改动这道保护。

## 8. 会话证据与决定

- **Q1 已由卡住的会话回答。** 08:46:34 父代理对 shell `sleep` 命中死循环检测；三个子代理分别
  在 08:53:53、08:56:01、09:04:02 结束。父代理直到用户 11:32:03 输入 `continue` 才读到
  三条通知并继续。A/C 处理这段 CLI 空等；B 为 CLI、ACP 和嵌入式宿主增加按需轮内等待。

  来源是仓库外、会轮转的本地文件 `../dstation/agentao.log`（2026-09-26）。以下摘录省略无关行：

  ```text
  08:46:34 Doom-loop detected: run_shell_command called 3+ times with identical args
  08:53:53 Reached final response in iteration 56
  08:56:01 Reached final response in iteration 35
  09:04:02 Reached final response in iteration 55
  11:32:03   Message 107 [tool]:
        [Doom-loop detected] Tool 'run_shell_command' was called 3 times with identical arguments. Execution stopped to prevent an infinite loop. Please try a different approach or tool.
  11:32:03   Message 108 [assistant]:
        part-02 已开始落盘（125/127 行，仍在写入中）。继续等待最后三片。
  11:32:03   Message 109 [user]:
        continue
  11:32:03   Message 110 [user]:
        Background agent update:
        Background agent 'generalist' (ID: 053a1038) completed.
        Background agent 'generalist' (ID: 638e9198) completed.
        Background agent 'generalist' (ID: 83cb4e27) completed.
  ```

  父代理和子代理共用一个 logger，`req_N` 编号会交错。11:32 父代理请求中的第 107、108 条消息
  把 08:46 的失败定位到父代理；它结束本轮后，三个子代理才分别给出最终回复。本地日志原行号
  （1105、9160、10623、13984、13993–14043）只作辅助锚点。
- **默认值：** 交互式 CLI 的 `auto_wake=true`：状态栏本来就提示任务完成，自动续跑让等待流程
  不需再等用户发一条消息。嵌入式宿主自己决定是否采用示例。

## 9. 测试

- **store（A/C）：** 快照反映 `update()` 入队、取走通知、`start_new_conversation()` 清空
  后的实际队列；两个入队路径只在真正入队时递增序号，抑制通知或取走通知均不递增。
  只有终态记录而没有通知时，CLI 不唤醒。
- **B：** 验证完成、未启动即取消、超时、本轮取消（含 ACP `session/cancel`）、
  `wait_seconds=0` 的输出不变，以及 CLI 并行批次 Ctrl+C 在线程池退出前取消令牌。
  确认取消等待不影响子代理继续运行；另一 store 完成的任务能在检查间隔内被读到，未知或不属于
  当前项目的 id 立即返回，终态结果之后可能再次注入简短预览。固定 30 分钟上限前，实测目标
  ACP 客户端的长轮次超时和进度展示。
- **顺序（新增回归测试）：** 新增会送达通知的各终态路径中“通知先于终态事件”的测试；另覆盖父代理在
  嵌入式宿主处理事件之前取走通知的情况，防止示例误以为队列必定非空。
- **CLI（host）：**
  - 有待注入的通知且输入框为空时触发唤醒；
  - 一条完成通知就能唤醒，不等其他运行中的任务；轮内积累的通知由该轮或下一次唤醒处理；
  - `UserPromptSubmit` 在取通知前拦截自动续跑轮次时，出现新通知之前不再次唤醒；
  - ticker 检查后、事件循环回调前开始输入时，以用户输入为先，输入框不退出；
  - 正在输入、plan 模式、`/goal` 期间，以及 `auto_wake: false` 时不触发；
  - 连续唤醒 3 次后停止（抛异常的唤醒轮也计数），用户提交任何一行都会清零；
  - `_BG_WAKE` 绕过空输入跳过逻辑，恰好跑一轮。
- **Run 宿主：** 断言其工厂收到 `bg_store=None`，且子代理工具 schema 没有
  `run_in_background`。
- **文案：** 检查两处面向模型的启动文字；不为固定某一个动词单独写测试。

A/C 与 Run 宿主的限制是用户可见的改动：在 `CHANGELOG.md` 的 `[Unreleased]` 加简短条目；
`agentao run` 的限制写在 **Changed** 下（daemon 后台任务会在进程退出时中断，无需迁移）。
host-api 中英双版补充示例。实施顺序：先 A 加 Run 宿主修改，再 C，最后宿主示例；
B 作为独立的跨宿主后续项，单独补条目和测试。
