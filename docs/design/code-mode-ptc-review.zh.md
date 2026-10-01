# Code Mode / PTC —— 路线对比与 Agentao 决策记录

**状态：** 决策记录（初稿）。2026-07-09 起草，基于对 `../codex/`（`code-mode*` crates）、
`../hermes-agent/`（`tools/code_execution_tool.py`）、`../goose/`
（`platform_extensions/code_execution.rs`）三套实现的 grep 验证式通读，以及对 agentao
现状的对照。**2026-07-11 补入第四套参考实现** `../opencode/`（`packages/codemode/` +
`packages/opencode/src/tool/code-mode.ts`，自建受限解释器路线，见 §2 与附录 F）。**这是一份
决策记录，不是已批准的方案。** 是否落地、以何种形态落地，是维护者的判断；本文只呈现证据、
路线差异与启动条件。**2026-10-01 重新核对 Codex 当前源码**，更新附录 A、E 及相关判断；
hermes、goose、opencode 和 gemini-cli 仍是下述 7 月历史快照，本次未重新核验其最新状态。

**读者：** 关注"多步工具链压缩为一次推理轮次 / 中间结果不进上下文"这类能力的 agentao
维护者。

**配套：** 英文版 `code-mode-ptc-review.md` **待补**。英文版不是实现前置条件，可在方案
确定后再同步。

**相关：**

- `tool-search.zh.md` —— 按需加载的工具发现（deferred tools）。与 Code Mode **互补但
  不同**：tool-search 解决"工具目录膨胀占 prompt/上下文"；Code Mode 解决"多往返 +
  中间大输出进上下文"。二者应分别依据工具声明体积与任务执行数据立项；tool-search
  不是 Code Mode 的硬性前置条件。
- `host-tool-injection.zh.md` —— 宿主显式工具注入契约。若做 Code Mode，沙箱内可暴露
  的工具集应沿用同一姿态（显式 allow-list，默认最小）。
- `permission-hardening-plan.zh.md` / `host-fs-policy.zh.md` —— Code Mode 新增脚本执行面。
  嵌套工具必须接回 `PermissionEngine`；若脚本还能直接访问文件、网络或进程，另需治理这些能力。

**锚点：** agentao `fix/test-retriever-time-bomb`@`a794fab`（2026-07-09，与 `main` 无
相关差异）；Agentao 本次对照 `0705694`；Codex 本次为
`92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2`（提交日期 2026-09-30，工作树干净，
替代原 `2b44896c5a` 快照）；hermes `main`@`79f127480`；goose `main`@`b7eb1e973`
（含 #10214）；opencode `main`@`34e58090`（2026-07-11 pull，含 code-mode 系列
`#34677`→revert `#35077`→`#35079`/`#35085`/`#35185`/`#35192`/`#35574`）。逐文件的通读
清单与源码行号见**附录**。

---

## TL;DR / 当前决定

> Agentao 当前没有足够的真实需求证据支持立即实现 Code Mode。现阶段不立项，先测量
> 工具声明体积、依赖式调用往返与大中间结果，再分别判断 tool-search 和 Code Mode 的需求门。
>
> 若需求触发，同时比较「受限 JS 运行时 + Python 宿主工具桥」与「Python 子进程 + OS 沙箱」。
> 先验证工具权限、取消、资源上限与输出收益；Python 宿主不要求模型脚本也使用 Python。

**本文把 PTC（Programmatic Tool Calling）与 Code Mode 作为同类机制讨论**：给模型一个"执行代码"
工具，让它写一段脚本、脚本里调用宿主的真实工具，而不是每个工具发一次 JSON function-call。
目标是把可预先编排的工具调用及处理逻辑放进一次脚本执行，并让模型只接收筛选后的输出。
收益有条件：并行原生调用本就可在同一推理轮发出；`wait`、审批、错误修复可能增加往返；
脚本若打印全部结果或使用 `notify`/`yield_control`，中间内容仍会进入上下文。详见附录 A.5。

---

## 1. 是否现在做

Code Mode 的收益随"依赖式工具往返 + 大中间输出"规模增长。Agentao 的实际工具集取决于
宿主注入与 MCP 配置，应按具体会话测量。**这是需求门 —— 痛不痛由维护者与真实使用判断**，不该由本文单方面
宣布。触发信号可能是：接多个 MCP server 后出现"抓 N 页 / 处理 N 文件 / 带条件重试"这类
脚本化工作流的真实、反复出现的需求。

**历史同侪状态（7 月快照，不能据此判断 10 月普及率）：**

- goose 已实现 code-mode，却**默认关闭**（`default_enabled:false`，`code-mode` Cargo
  feature 后）。
- opencode 也**默认关闭**（gate 在 `experimentalCodeMode` 运行时 flag 后，
  `tool/registry.ts:113`），且**首版 revert 后才重做**（实验 `#34677` → revert `#35077`
  → 重做成独立 confined-execution 包 `#35079`）—— 既是"可选/实验"信号，也是"naive 首版
  会翻车、值得慎重"的成熟度信号。
- gemini-cli **干脆没做**，只靠常规 per-tool 调用 + 一个普通 `shell` 工具（`shell` 不是
  工具桥，也无"中间结果不进上下文"设计）。

这些历史状态提示需要核验成熟度；是否值得 Agentao 开发，仍取决于本项目的采用者与任务数据。
当前 Codex 已有完整执行链，但启用还取决于模型工具模式、feature 与 host 可用性（附录 A.1）。

---

## 2. 四种路线的关键差异

四个参照实现走了**四条不同的隔离路线**（外加 gemini-cli 作为"干脆不做"的第五个数据点）。
下表只列决策相关的差异，机制细节见附录；Codex 是本次快照，其他列为 7 月历史状态：

| | **codex（`exec`）** | **hermes（`execute_code`）** | **goose（`code_execution`）** | **opencode（`execute`）** |
|---|---|---|---|---|
| 语言 | JavaScript | **Python** | **TypeScript**（async `run()`） | JavaScript（**子集**） |
| 运行时 | 全新 **V8 isolate** | **真正的 OS 子进程**（`subprocess.Popen`） | **进程内嵌 Deno/V8**（经 `pctx` crate） | **自建受限解释器**（`acorn` 解析 + 手写遍历 AST；不嵌引擎、无子进程） |
| 权限模型 | 唯一出口是注入的 `tools` 对象；工具调用照常经宿主 | 桩 → RPC → 宿主 `handle_function_call`（复用普通工具调用的审批路由）；整脚本 spawn 前再过一次审批门 | 外层 `execute_typescript` 走一次常规权限判断；内部回调不再逐工具审批 | 唯一出口是注入的 `tools`；**每次**调用经宿主 `ctx.ask` 逐工具审批（非整段一次授权） |
| 能力边界 | 无 Node、直接文件/网络绑定或 import；环境操作通过工具 | 本地后端文件 / 网络开放（靠环境擦洗 + 输出脱敏兜底）；远程后端按容器/主机配置判断 | 仅注册当前已启用扩展的工具；可通过回调触达文件、网络或 shell 类工具 | 不注入 fs/进程/网络/module；全局作用域为手写白名单 |
| 隔离 | 默认本地独立 host；可选 gRPC 远程 host；无进程内自动回退，混合模式可退普通工具 | 进程级（本地）/ 容器（远程） | 没有独立进程或容器边界；裸 TypeScript 是否具有直接文件 / 网络能力，不能仅从 Goose 集成层断言 | 无进程/容器边界；能力约束依赖自建解释器及工具桥正确性 |
| 工具桥 | 全局 `tools` 对象 | 代码生成 `hermes_tools.py` 桩 → Unix socket / TCP RPC | 回调注册表：pctx 生成 TS 桩 → 回调 → `dispatch_tool_call` | 注入的 `tools.<server>.<tool>` 树（**MCP adapter + OpenAPI adapter**） |
| 运行控制 | cell-id `wait` 流式；`exit`/`notify` | 一次性返回 stdout（无流式）；300s 超时 + 协作式中断 | 超时、取消、嵌套调用取消传播 | `AbortSignal` 协作式取消；`timeoutMs`/`maxToolCalls`/`maxOutputBytes`（**均无默认**，须宿主设定） |
| 默认状态 | host feature 默认开；Code Mode feature 默认关，模型元数据也可选择模式 | 内建（cron / 无人会话默认禁用） | **默认关闭** | **默认关闭**（`experimentalCodeMode` flag 后；首版曾 revert 重做） |

> 注：goose 的 `execute_bash` 应称为 pctx 提供的**元工具**，不能直接等同于 Goose 的宿主
> shell 工具。

Codex 与 opencode 都限制脚本的环境能力，但分别依赖 V8 + 显式绑定和自建解释器。
裸 V8 没有 Node 的 fs/网络 API，删除几个标准全局并不等于「漏删一个就能逃逸」。
自建解释器也需验证宿主对象泄漏、资源耗尽与权限桥，不能由实现形态推出绝对安全或无需纵深防御。
hermes 本地脚本能力更广，另用审批、环境擦洗与脱敏治理；goose 的集成提供超时与取消。
这些轴应分别评估，不能只按语言或进程数量排序。

---

## 3. Agentao 当前缺少什么

- 工具都是**独立的 function-calling 工具**；MCP 工具注册为 `mcp_{server}_{tool}` 直接
  暴露给模型（`agentao/mcp/tool.py`、`agentao/tools/base.py::ToolRegistry`）。
- **没有** Code Mode / PTC / 代码执行工具。

有一些相邻地基，但都需要改造，**不是现成可复用**：

- `ToolRunner`（`runtime/tool_runner.py`）是 plan→execute→format→sanitize 的**批量**
  工具调用管线（`execute(tool_calls, …)` 收一组调用），**不是现成的单工具 RPC 派发
  接口** —— 接 Code Mode 需要新建一层 `(tool_name, args) → result` 派发适配。
- `PermissionEngine` 存在。无论脚本语言，嵌套工具均需复用它；若选择具有直接环境能力的
  Python 子进程，整脚本审批也需单独设计。受限编排运行时不必照搬这道门。
- `AsyncToolBase` + `CancellationToken` 提供超时 / 取消原语（这一层相对现成）。
- `sandbox-exec`（macOS）profile 机制存在，但**当前三个 profile 都是
  `(allow file-read*)`（任意文件读取），不能直接用于模型生成的 Python** —— 需要另写更严
  的专用 profile。
- **模型工具输出没有统一密钥脱敏**。当前大输出落盘副本会脱敏，但回给模型的摘录有意保留原文
  （`agentao/runtime/tool_result_formatter.py:104`）；不能把落盘脱敏当成脚本数据与模型输出的安全边界。

**因此 Python 子进程只是"架构候选"，不是已验证路线。** 上述每一项都要先验证再谈落地。

---

## 4. 满足什么条件后启动实验

需求门触发后，第一步不是搭生产架构，而是一个受限的验证实验。

> 首次实验仅验证：
>
> 1. 受限 JS 能否不绑定文件、网络、进程与模块导入；Python 方案的专用沙箱能否限制敏感读取。
> 2. 嵌套调用能否复用权限、hooks、事件与取消，隐藏或禁止的工具能否保持不可调用。
> 3. Python 方案的整脚本审批能否避免被普通 allow 规则绕过。
> 4. 是否具有独立的执行时限、堆/内存、工具调用次数及输出字节预算；取消能否停止运行时和嵌套调用。
> 5. 仅输出摘要能否降低实际输入 token 与依赖式往返，同时维持任务成功率和宿主审计证据。
>
> 实验只暴露少量只读工具，不支持无人值守，不处理流式与跨平台。

Python 路线还应验证完整进程树清理；进程工具原语可作为起点，不能替代上述端到端验证。

---

## 5. 开放问题

1. **是否存在稳定的真实需求。**（需求门 —— 维护者与真实使用判断，见 §1。）
2. **能否建立可信的执行边界。** Python 路线需要专用 OS 沙箱与环境能力治理；受限 JS
   路线需要核验运行时绑定、工具桥、资源限制与 Python 集成成本。两者均有跨平台和维护成本，
   不应因宿主使用 Python 就排除 JS。当前证据不支持宣布哪条路线已适合 Agentao。
3. **是否存在可复用的嵌套工具派发契约。**（把沙箱内工具调用桥回 `PermissionEngine` /
   事件 / 取消 —— 目前 `ToolRunner` 是批量入口，需要新建单工具派发适配。）

---

## 6. 结论

> **当前决定：暂不实现。**
> 先收集多工具任务数据，分别判断声明体积与中间结果瓶颈。出现稳定需求后，比较受限 JS
> 工具桥与受沙箱约束的 Python 子进程。实验通过前，不承诺运行时、RPC、流式或跨平台路线。

Codex 可借鉴的重点是：工具暴露与可调用范围分离、嵌套调用沿用治理链、计算结果与模型输出
分离、yield 与 terminate 分离。其完整远程 host 与跨 turn cell 架构不是 Agentao 首次实验的必需项。

**本文状态：分析初稿，未批准、未实现。**

---

## 附录 A：Codex 当前 Code Mode（2026-10-01 复核）

以下是本地 `../codex/` 的 `92bc601ad60` 源码观察，不代表所有已发布客户端或模型均已启用。
源码链接固定到该提交；路径均相对 `codex-rs/`。本次静态检查执行链与相关测试，未运行 Rust 测试。

### A.1 已实现什么，如何启用

**已经实现可执行的 Code Mode，不是设计草稿。** `exec` 接收原始 JavaScript，按 async module
执行；支持循环、分支、数据处理、`await` 与 `Promise.all`，通过 `tools.*` 调宿主工具。
`wait` 接续长时间执行的 cell，并可显式终止。TypeScript 声明用于说明参数与返回值，执行输入仍是 JS。

模式有 `Direct`、`CodeMode`（混合）与 `CodeModeOnly`。模型的 `tool_mode` 元数据优先于
feature；没有元数据时再看 `code_mode_only`/`code_mode`，否则使用普通工具。
`code_mode` feature 默认关闭且仍标记 UnderDevelopment；`code_mode_host` 则默认开启且为 Stable。
因此「host 默认开」不能推出「所有模型默认用 Code Mode」。
见 [模式选择](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/tools/mod.rs#L75)、
[feature 默认值](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/features/src/lib.rs#L1080)。

**当前没有自动退回进程内 V8 的生产路径。** 默认 provider 查找独立 `codex-code-mode-host`，
host 禁用或缺失即不可用。混合模式在允许兼容回退时转为 `Direct`；`CodeModeOnly` 或设置
`disable_in_process_fallback=true` 保留 Code Mode 并失败关闭。这一配置名称是历史遗留，
当前控制的是普通工具回退，而非恢复进程内执行。
`InProcessCodeModeSession` 仍存在于 runtime crate，供 host 内部使用和测试，不能据其存在判断自动回退。
见 [provider 选择](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/thread_manager.rs#L557)、
[host 可用性](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode/src/remote_session.rs#L62)、
[回退条件](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/tools/mod.rs#L87)。

### A.2 执行链与进程边界

```text
模型 exec(JS)
  → CodeModeExecuteHandler：解析 pragma、构建当前可调用工具集
  → CodeModeService / CodeModeSession：启动 cell
  → 本地 host（stdio IPC）或远程 host（gRPC）
  → SessionRuntime / CellActor：每个 cell 新建线程、V8 isolate、context
  → tools.* 回调 → delegate → Core dispatch broker
  → ToolCallRuntime → ToolRouter → 原工具实现
  ← JSON 结果 → JS Promise → 脚本过滤/聚合
  ← 显式输出 → exec/wait 输出预算 → 模型
```

当前 crate 职责如下：

| 位置 | 职责 |
|---|---|
| `code-mode-protocol` | exec/wait 描述、工具声明、session/delegate 接口、stdio/gRPC wire 类型 |
| `code-mode` | 本地进程 provider、IPC 客户端与 gRPC 客户端；不再承载 V8 实现 |
| `code-mode-host` | 独立服务进程、会话与 callback 路由、并发/协议限制 |
| `code-mode-runtime` | V8、全局绑定、Promise、cell actor、session store |
| `core/src/tools/code_mode` | 模型工具接入、派发 broker、exec/wait、输出格式与遥测 |

本地 provider 惰性启动并复用 host，host 可承载多个 session；每次 exec 新建 isolate，
并非每个 exec 都新建 OS 进程。app-server 还可通过 `--code-mode-host URL` 选择远程 gRPC host。
见 [provider](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode/src/remote_session.rs#L34)、
[远程 host 选择](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/app-server/src/code_mode_host.rs#L8)、
[isolate 创建](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-runtime/src/runtime/mod.rs#L166)。

**能力边界：** 使用裸 V8 而非 Node；没有直接绑定 `fs`、`fetch`、`process`、`require`。
`install_globals` 删除 `console`、`Atomics`、`SharedArrayBuffer`、`WebAssembly`，注入工具、
输出、store、定时器等辅助；静态与动态 import 都由 loader 拒绝。
数组筛选、排序、聚合和 JSON 运算仍可直接执行。Python/pandas 或文件计算可通过受治理的 shell 等工具完成。
见 [全局绑定](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-runtime/src/runtime/globals.rs#L15)、
[导入拒绝](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-runtime/src/runtime/module_loader.rs#L229)。

独立进程提供故障分离，但本地 spawn 只显示进程组、管道、kill-on-drop 与环境擦洗；
不能据此宣称 code-mode-host 自身受容器、文件系统或网络 OS 沙箱约束。
语言绑定限制也不是 V8/桥接代码无漏洞的证明；工具返回的敏感数据仍可被脚本输出。
见 [host 启动](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode/src/remote_session/connection.rs#L157)。

### A.3 工具发现与工具权限是两条轴

构建脚本工具集时先检查 `is_available_in_code_mode()`、excluded namespace 与名称冲突。
普通工具、MCP 和动态工具可按 exposure 进入脚本；模型直接可见但标为 model-only 的工具不进入。
**Deferred 工具可以省略 prompt 中的完整描述，同时仍挂在 `tools` 上**；`ALL_TOOLS` 提供
`{name, description}` 供脚本筛选。无需先调用 tool_search 激活，已经属于本次获准的嵌套工具集。
这与「搜索后才加入可调用集合」是不同策略。见
[注册与暴露过滤](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/tools/spec_plan.rs#L819)、
[ALL_TOOLS 构建](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-runtime/src/runtime/globals.rs#L76)。

嵌套调用回到普通 `ToolCallRuntime`/`ToolRouter`，带 cell-id、runtime-tool-call-id 与取消 token。
`ToolCallSource::CodeMode` 标注来源，入口没有统一跳过原工具的审批、沙箱与生命周期治理。
实际审批仍由各工具及当前策略决定；并非每次调用必然弹确认。执行器还按工具的并发能力加锁，
因此脚本 `Promise.all` 不会把原本要求串行的工具强行变成并行。exec 禁止递归调用自身。
见 [嵌套派发](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/tools/code_mode/mod.rs#L334)、
[共享执行与并发锁](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/tools/parallel.rs#L122)。

### A.4 输出、状态、错误和取消

| 机制 | 当前语义及边界 |
|---|---|
| `text/image/audio/generatedImage` | 显式输出给模型；没有 `console`，脚本表达式的值不自动作为最终输出 |
| `notify` | 在 exec 结束前另发输出；会把中间内容交给模型 |
| `yield_control` | 提交已积累输出并返回 cell-id，脚本继续运行 |
| `yield_time_ms` | 本次观察等待预算；core 默认 exec 30 秒、wait 10 秒，runtime 独立默认 exec 10 秒；不是 cell 执行期限 |
| `wait` | 返回上次 yield 后的新输出；`terminate:true` 是终止操作 |
| `store/load` | JSON 可序列化值，保存在当前 runtime session；不是持续保留 JS 全局，也不是落盘持久会话 |
| 工具异常 | delegate 错误转为 Promise rejection，可用 try/catch；MCP `isError` 仍可作为结果字段由脚本处理 |
| 脚本失败 | 返回已收集内容与 `Script error`；已执行工具的外部副作用不因脚本失败撤销 |

说明与默认预算见
[工具描述](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-protocol/src/description.rs#L23)、
[exec 默认等待时间](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/config/mod.rs#L1167)。
cell 启动时取得 store 快照，结束时合并本 cell 写过的键；同 session 共享、不同 session 隔离。
**当前完成路径即使携带脚本错误也会提交 store 写入；不能把 store 当成「成功才提交」事务。**
取消则可拒绝完成提交。见
[快照](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-runtime/src/session_runtime/mod.rs#L159)、
[带错误的完成提交](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-runtime/src/cell_actor/mod.rs#L443)。

需要区分 **preempt/yield** 与 **terminate/cancel**：前者停止等待、保留运行 cell；后者取消
callback、通知 V8 `terminate_execution`，可打断 CPU 死循环，并等待 callback 清理。
整个 turn 中断是否主动终止所有活跃 cell 还受 `CodeModeInterrupt` feature 控制，默认关闭。
不能把观察信号取消等同于 cell 已结束。见
[运行时观察](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-runtime/src/service.rs#L65)、
[强制终止](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-runtime/src/cell_actor/mod.rs#L618)、
[turn 中断门控](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/tasks/mod.rs#L926)。

**资源限制不能混为一谈：**

- exec/wait 模型可见输出默认预算 10,000 token，core 对返回内容做截断；这不是运行时内存上限。
- stdio host 有 256 个在途请求、128 个 active cell 与协议帧限制；这些不是每 cell 的总调用次数上限。
- session 协议有 `max_yield_time_ms` 与 `max_heap_size_bytes`，但当前 runtime 构造时明确把 heap
  limit 置为 `None`，V8 用默认 CreateParams；不能宣称传入 heap 字段就已限制 V8 堆。
- 上述执行链未见统一的 cell 总执行期限或累计工具次数预算；需要宿主另行定义，不能用 yield 代替。

见 [模型输出截断](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/tools/code_mode/mod.rs#L316)、
[host 并发限制](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-host/src/lib.rs#L55)、
[runtime 忽略堆限制](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/code-mode-runtime/src/service.rs#L41)。

### A.5 对上下文长度的实际影响

**能缩短，但不会自动缩短。** 收益应分开测量：

1. **声明体积：** deferred 工具不把完整 schema 塞进 exec 描述；脚本只输出筛选后的目录片段。
   全量 `ALL_TOOLS` 若被打印回模型，发现收益会减少；工具数量减少也不等于权限收紧。
2. **中间结果：** 工具结果先作为 JS 数据返回，脚本只 `text` 摘要，原始数据可以保留在 session
   store 中供后续计算。嵌套工具自身仍可能截断；Code Mode 并不保证拿到无限完整原始输出。
3. **依赖式往返：** 分页、过滤后再查询、条件重试等可提前写进脚本，减少模型逐步编排的往返。
   独立工具本可一次并行发出，不能把 N 个工具等同于节省 N 次推理。

可设想：普通路径将多页完整结果传回模型，再由模型计算；Code Mode 把分页和聚合放进脚本，
只输出总数与少量命中。这是机制推断，本文没有实测 token、延迟或成功率，不能承诺节省百分比。

还要区分模型正文、宿主日志与请求元数据：Codex 可记录嵌套调用参数及结果元数据，附到采样/
压缩请求；Guardian 相关测试也保留了专门审核证据。这些通道不能统称「中间结果永不进上下文」，
其计费与模型可见性需依具体 API 确认。见
[请求元数据](https://github.com/openai/codex/blob/92bc601ad60542c92bf0bb1e7a2eb70b84ac49d2/codex-rs/core/src/tools/executed_tool_calls/request_metadata.rs#L11)。

相关测试为这些契约提供源码证据（本次未执行）：

| 测试 | 证明意图 |
|---|---|
| `core/tests/suite/code_mode.rs:4045` | 嵌套工具可并行运行 |
| `core/tests/suite/code_mode.rs:4214` | JS 结果变量与模型历史截断分离 |
| `core/tests/suite/code_mode.rs:5883` | 开启中断 feature 后终止 cell 与嵌套工具 |
| `core/tests/suite/code_mode.rs:7052` | Node REPL 部分证据仅交 Guardian 审核 |
| `code-mode-runtime/src/service_tests.rs:62` | preempt 只 yield，不停止 cell |
| `code-mode-runtime/src/service_tests.rs:528` | store 同 session 共享、跨 session 隔离 |
| `code-mode-runtime/src/service_contract_tests.rs:430` | terminate 返回前取消待完成 callback |

### A.6 Agentao 最值得迁移的契约

**建议作为需求触发后的实验约束，尚未批准实施：**

- 独立表示「模型看见的声明」「脚本可调用集合」「实际权限」，搜索或省略声明不改变审批策略。
  Agentao 当前 chat 在 while 循环前生成工具列表（`agentao/runtime/chat_loop/_runner.py:364`），
  tool-search 若动态加载工具，应在下一次内部模型请求刷新声明。
- 新建单工具派发适配，保留权限、Pre/PostToolUse hooks、事件、调用 ID 与取消；不能直接调用
  工具的裸 execute。尤其要明确 hook 的停止或反馈如何传回脚本和 agent loop。
- 返回给脚本的数据与模型输出分开预算，摘要输出仍需截断；审计记录独立保存。
- 首次只验证单次只读脚本、JSON I/O 与明确时限，不必照搬远程 gRPC、多活跃 cell、跨 turn store。
- 比较两种运行时接入成本；把脚本 token、输出 token、往返、成功率、取消延迟和隔离测试一起测量。
  Agentao 已有 8-worker 工具批量执行（`agentao/runtime/tool_executor.py:231`），基线必须包含现有并行能力。

---

## 附录 B：hermes PTC 实现要点（Python / 子进程 + RPC）

hermes 的路线与 agentao 架构最近，值得细看。以下均带 `tools/code_execution_tool.py`
行号（除非另注）。hermes 的模块 docstring 标题即 *"Code Execution Tool — Programmatic
Tool Calling (PTC)"*（`:3`）；在该仓库里 "Code Mode" 与 "PTC" 都指同一个工具
`execute_code`。

### B.1 执行机制：真子进程，两套后端

- **本地后端**：模型代码原样写入临时目录 `script.py`，以真正的子 Python 进程运行：
  `subprocess.Popen([_child_python, _script_path], stdin=DEVNULL, start_new_session=True, …)`
  （`:1345-1354`）。没有 RestrictedPython、没有 seccomp、没有容器 —— 本地路径的子进程
  拥有完整文件 / 网络访问。隔离仅为进程级 + 环境擦洗 + CWD / 解释器按"模式"选择：
  `strict` 用 `sys.executable`、临时目录；`project`（默认）用用户 venv 的 python、
  会话 CWD（`_get_execution_mode :1657-1682`、`_resolve_child_python :1705-1745`、
  `_resolve_child_cwd :1748-1767`）。
- **远程后端**：`_execute_remote`（`:913-1108`）复用 `terminal`/文件工具用的同一套终端
  环境（Docker/SSH/Modal/Daytona/Singularity/Vercel），把 `script.py` + `hermes_tools.py`
  经 base64 发过去，在容器 / 主机里 `python3 script.py`。这里容器本身就是隔离。
- 平台细节：本地在 POSIX 用 AF_UNIX、Windows 回退回环 TCP（`:1213-1216`、`:1256-1265`）。

### B.2 工具桥：生成桩 → RPC → 同一个宿主派发器（关键）

模型拿不到真实工具对象；hermes 每会话代码生成一个桩模块 `hermes_tools.py`，放到子进程
`PYTHONPATH`（`generate_hermes_tools_module :269-301`；`PYTHONPATH` 设置 `:1314-1322`）：

1. 每个被允许的工具从 `_TOOL_STUBS` 表（`:223-266`）生成一个桩函数，函数体是
   `return _call('search_files', {...})`。
2. `_call(tool_name, args)` 把 `{tool, args, token}` 序列化，经 Unix socket / TCP
   （本地）或原子重命名的请求文件（远程）发送（`:383-408` / `:426-474`）。
3. 父进程跑 RPC 监听线程（`_rpc_server_loop :487-620` / `_rpc_poll_loop :763-911`）：
   校验 token、执行 allow-list 与调用次数上限、剥离被禁的 `terminal` 参数，再经
   **`handle_function_call(tool_name, tool_args, task_id=…)`** 派发（`:588-590` /
   `:870-872`）。

**精妙点：** `handle_function_call` 正是普通 JSON 函数调用用的同一个中央派发器
（`model_tools.py`，也被 `agent/tool_executor.py:1406` 调用）。所以模型脚本里的
`search_files(...)` 命中与普通工具调用完全相同的宿主实现、审批路由、`task_id` 隔离。
RPC 线程用 `propagate_context_to_thread` 包裹（`:1270-1271`），让审批上下文 / 回调跨线程
存活 —— 否则沙箱内工具调用会静默自动批准危险命令（代码里明确注释 `:1267-1269`）。另有
`json_parse`/`shell_quote`/`retry` 三个便捷助手直接内联进桩、不走 RPC。

**暴露哪些工具** = `SANDBOX_ALLOWED_TOOLS`（7 个：`web_search, web_extract, read_file,
write_file, search_files, patch, terminal`，`:62-70`）∩ 会话已启用工具（`:1193-1197`）。

### B.3 结果 / 错误

正常输出通过脚本 **stdout** 回给模型；未打印的中间结果不作为普通工具正文返回，
脚本仍可主动打印它们：

- 后台读线程排空管道防死锁；stdout 用头 + 尾策略（40% 头 / 60% 尾，50KB 上限），
  好让早期日志与最终 `print()` 都存活；stderr 头部 10KB（`MAX_STDOUT_BYTES=50_000`、
  `MAX_STDERR_BYTES=10_000`，`:75-76`）。
- 轮询正常退出 / 超时 / 协作式中断（`:1441-1462`）。
- 输出去 ANSI（`:1495-1497`）+ 密钥脱敏（`redact_sensitive_text(..., code_file=True)`
  `:1505-1507`）后再返回。
- 返回 JSON：`{status, output, tool_calls_made, duration_seconds}`（`:1510-1515`）；
  非零退出把 traceback 追加进 `output`、status=`error`。**无流式** —— 一次性在轮末返回；
  长任务最长 300s，其间周期性"活动触碰"以免 gateway 的不活动超时中途杀掉。

### B.4 安全模型（因为本地后端不是沙箱解释器）

- **环境擦洗** `_scrub_child_env`（`:146-207`）：变量名含
  `KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/AUTH/DSN/WEBHOOK/BEARER/...`
  （`_SECRET_SUBSTRINGS :91-101`）的一律不进子进程；只放行安全前缀 + 一小撮 `HERMES_*`。
  工具经 RPC 触达，不是靠 API key（`:1281-1290`）。
- **输出脱敏兜底**：脚本仍可 `open('~/.hermes/.env')`，故 stdout/stderr 返回前再脱敏。
- **allow-list**：仅 7 个沙箱工具，且与会话启用工具取交集；`terminal()` 剥离
  `background/pty/notify_on_complete/watch_patterns`（`_TERMINAL_BLOCKED_PARAMS :484`）。
- **资源上限**：300s 超时、每脚本最多 50 次工具调用、stdout 50KB / stderr 10KB。
- **RPC 鉴权**：每次运行 32 字节 token，`secrets.compare_digest` 比对；UDS `chmod 0600`；
  Windows TCP 仅绑回环。
- **整脚本审批门** `check_execute_code_guard`（`tools/approval.py:2953-3092+`）：因为脚本
  能用 `subprocess`/`os.system`/`ctypes` 绕过 `terminal()` 的 `DANGEROUS_PATTERNS`，
  所以在 spawn 前对整段脚本过审批，支持 smart-LLM 批准 / 拒绝 / 升级，并在 cron / 无人
  会话中完全禁用 `execute_code`（除非显式配置）。隔离后端（vercel_sandbox、非宿主挂载的
  Docker）跳过审批门 —— 容器已隔离。

---

## 附录 C：goose Code Mode 实现要点（TS / 进程内 Deno-V8）

均带 `crates/goose/src/agents/platform_extensions/code_execution.rs` 行号（+ `mod.rs`
门控、`developer/shell.rs` 取消传播、`documentation/.../code-mode.md`）。

Goose 将 Code Mode 实现为**进程内平台扩展**。模型调用 `execute_typescript`，脚本通过
生成的回调访问当前会话已启用的扩展工具。

权限检查发生在外层 `execute_typescript` 调用。脚本内部回调直接进入 `ExtensionManager`，
不会重新经过 Agent 层的逐工具权限判断。因此它采用"整段代码一次授权"，以保留 PTC 的批处理
收益。

运行时提供超时、取消以及嵌套工具取消传播，但没有独立进程或容器隔离。Code Mode 默认关闭。

这是一种较轻的信任模型，适合接受整段脚本授权的交互式场景；不适合要求每个嵌套工具独立审批
的环境。

**grep 验证的机制细节（供参考，不作强结论）：**

- **形态：** 平台扩展 `code_execution`（UI 名 "Code Mode"），不是单个工具，而是一个
  进程内 MCP server，按"披露风格"（`CODE_MODE_TOOL_DISCLOSURE`，默认 `catalog`）暴露
  一组元工具：`list_functions` / `get_function_details` / `execute_typescript`（fs 风格
  再加 `execute_bash` —— 后者是 pctx 提供的元工具，不等同 Goose 宿主 shell 工具）。
- **运行时：** 进程内嵌 Deno/V8（`deno_core`），经外部 crate `pctx`（"Port of Context"）。
  因 Deno 运行时 `!Send`，所有执行串行在一把进程级 V8 互斥锁后面（`:285-287`）。
- **工具桥：** 每次执行前枚举其它已启用扩展的工具（`get_prefixed_tools_excluding`），
  pctx 据此代码生成带类型的 TS 桩；脚本调用某桩 → Rust 回调 → 回到 goose 常规派发
  `manager.dispatch_tool_call`（`:367-368`）。与 hermes 的 RPC 桩、codex 的 `tools`
  对象是同一思路的第三个变体。
- **#10214（运行控制）：** 此前 `execute_typescript`/`execute_bash` 无超时、无取消，
  一个挂死的脚本会因进程级 V8 锁拖垮所有会话。新增
  `run_in_deno_runtime(timeout, cancellation_token, …)`（`:299-346`）用 `tokio::select!`
  在 300s 超时 / 外部取消 / 正常完成 三臂间选择，并把一个子 `dispatch_token` 传进嵌套
  工具调用（500ms 排空），取消时一路 `start_kill()` 掉派生的 OS 进程
  （`developer/shell.rs`）。
- **默认关闭：** 扩展 `default_enabled:false`（`mod.rs:136`），整特性在 `code-mode`
  Cargo feature 之后。shipped-but-off。

---

## 附录 D：gemini-cli —— "干脆不做"的第五个数据点

gemini-cli 没有 Code Mode —— 只有常规 per-tool JSON 调用 + 一个普通 `shell` 工具。
`shell` 不是工具桥，也无"中间结果不进上下文"设计。作为一个成熟厂商 CLI 的选择，它是 §1
"Code Mode 按需上而非标配"的佐证之一。

---

## 附录 E：Codex 当前实现与 hermes 历史快照的安全取舍

Codex 证据见附录 A；hermes 证据仍为附录 B 的 7 月快照，本次未验证新版。
两者的嵌套工具都回到宿主派发器；需要分别检查脚本的直接环境能力、工具权限和资源限制。

| 维度 | Codex（本次快照） | hermes 本地后端（7 月快照） |
|---|---|---|
| 直接环境能力 | 没有绑定 fs/网络/process/import；保留 JS 计算与显式工具桥 | 普通 Python 可用 open/subprocess 等直接访问环境 |
| 嵌套工具治理 | 回普通 ToolCallRuntime/ToolRouter，保留并发规则、取消与生命周期 | RPC 回 handle_function_call，跨线程传播审批上下文 |
| 整脚本审批 | 本次 exec handler 未见独立整脚本审批；外部操作由嵌套工具治理 | spawn 前执行 check_execute_code_guard |
| 敏感数据 | 无直接 env/fs 绑定，但工具结果、store、输出与审计通道仍可能含敏感内容 | 环境擦洗与输出脱敏；直接文件读取仍需另行限制 |
| 进程边界 | 默认独立 host，可远程部署；未见本地 spawn 自动加 OS 沙箱 | 本地子进程；远程后端按具体容器/主机与挂载配置判断 |
| 资源 | yield 只限制等待；输出有 token 预算；heap 字段未实施；host 有并发限制 | 300 秒、50 次工具调用、stdout/stderr 字节限制 |
| 工具范围 | exposure 过滤、namespace 排除、名称冲突处理后的集合 | 7 工具 allow-list 与会话启用工具交集 |

**对 Agentao 的判断：** 若只需要编排工具和处理 JSON，受限 JS 可以减少直接环境能力，
并不要求 Python 宿主移植成 JS。若需求确实是直接运行 Python 库或工作区程序，则 Python
子进程更符合表达力要求，但必须验证专用沙箱、脚本审批和资源清理。
两种路线都不能由「独立进程」「删除全局」「输出脱敏」单独推出安全保证。
首次实验应按 §4 比较，而非预先宣称 Python 路线最务实或 V8 路线安全上限更高。

---

## 附录 F：opencode Code Mode 实现要点（TS / 自建受限解释器）

2026-07-11 opencode `main`@`34e58090` 新增，与前三者又不同 —— 它**既不嵌 V8**（codex）、
**也不开子进程**（hermes），而是**自己写了一个受限解释器**。均带 `packages/codemode/`
（私有 workspace 包 `@opencode-ai/codemode`）与 `packages/opencode/src/tool/code-mode.ts`
（宿主适配）行号。

**工具面：** `execute` 工具，描述 *"Run a confined orchestration script with access to
connected MCP tools."*（`code-mode.ts:12-17`），模型写一段 JS，脚本里
`await tools.<server>.<tool>(...)`。gate 在 `experimentalCodeMode` 运行时 flag 后，
**默认关闭**（`tool/registry.ts:113`）。历史：实验版 `#34677` → **revert** `#35077` →
重做成独立 confined-execution 包 `#35079` —— naive 首版翻车、重写才干净，是成熟度信号。

**沙箱本质 = 自建受限解释器（不嵌引擎）：** `acorn` 只做**解析**（`interpreter/runtime.ts:1`），
然后**逐节点手写遍历 AST** 执行（3465 行，method-by-method 亲手实现 stdlib）。全程
**无 `eval` / `new Function` / `node:vm` / isolated-vm** —— 解释器从不把代码交给 JS 引擎跑。
全局作用域是一张**手写白名单 `Map`**（`runtime.ts:622-641`）：只绑定 `tools` + 纯数据命名空间
（`Object`/`Math`/`JSON`/`Array`/`String`/`Number`/`Promise`/`console`（桩）/`parseInt`…），
**没有** `process`/`require`/`fetch`/`globalThis`。README 原文：程序运行时 "without receiving
ambient filesystem, process, network, module, or application authority"；`fetch`/`crypto`/
文件句柄默认不存在，须宿主显式开（`packages/codemode/AGENTS.md` Future Design Notes）。

**与 codex 的关键区别（同为"约束解释器"的两条子路线）：**

- **Codex**：裸 V8 + 显式工具/辅助绑定，拒绝模块导入；不是 Node 全能力后再删黑名单。
  当前使用独立 host，没有进程内自动回退，具体边界见附录 A。
- **opencode 历史快照**：白名单作用域与自建 AST 执行，没有把模型代码直接交给底层引擎。
  代价是解释器与 stdlib 的正确性、宿主对象隔离、资源限制及 JS 子集兼容性；这些仍需验证。

**工具桥 + 逐工具审批（非 goose 式整段授权）：** `code-mode.ts` 按 server 把 MCP 工具分组成
`tools.<server>.<tool>` 树（`groupByServer`/`toolTree`，`:36`/`:120`）；可见工具先经权限
ruleset 过滤（`Permission.visibleTools(mcp.tools(), ruleset)`，`:209-210`）；**每次**脚本内
工具调用都经宿主 `input.ctx.ask({ permission: entry.key, … })` 逐工具审批（`:147`）。所以像
codex/hermes 一样，嵌套调用桥回宿主权限系统，**不是** goose 的"整段一次授权"。另有
**OpenAPI adapter**（`codemode/src/openapi/`，`#35192`）按 OpenAPI spec 生成工具，与 MCP
adapter 并列 —— 工具桥同时覆盖 MCP 工具与 OpenAPI operation。

**结果 / 错误 / 资源：** 返回值恒为 JSON-safe 纯数据（`undefined`→`null`）；错误分
program/schema/tool/limit 四类稳定分类，保留 public（模型可见）/private（宿主诊断）双通道
（`codemode.ts:69-74` + AGENTS.md）；`#35180` 让整段脚本 program failure 直接 fail `execute`
工具。资源限额 `timeoutMs`/`maxToolCalls`/`maxOutputBytes` **均无默认值**（absent＝无限），
须宿主逐次传入（`codemode.ts:10-15`）；取消经 `AbortSignal` 协作式中断
（`code-mode.ts:264-266`，`:274` `Effect.raceFirst`）。

**对 Agentao 的含义：** 这是受限编排语言的一种历史实现参考，不证明其无需 OS 纵深防御或
密钥暴露面为零。Agentao 可在 Python 宿主旁接 JS 运行时，或选择专用的受限语言；不必自写
Python AST 解释器才算同类方案。语言兼容、桥接成本与安全边界均应经实验确认，见 §4、附录 A.6。
