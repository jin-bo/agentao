# Code Mode / PTC —— 路线对比与 Agentao 决策记录

**状态：** 决策记录（初稿）。2026-07-09 起草，基于对 `../codex/`（`code-mode*` crates）、
`../hermes-agent/`（`tools/code_execution_tool.py`）、`../goose/`
（`platform_extensions/code_execution.rs`）三套实现的 grep 验证式通读，以及对 agentao
现状的对照。**这是一份决策记录，不是已批准的方案。** 是否落地、以何种形态落地，是维护者的
判断；本文只呈现证据、路线差异与启动条件。

**读者：** 关注"多步工具链压缩为一次推理轮次 / 中间结果不进上下文"这类能力的 agentao
维护者。

**配套：** 英文版 `code-mode-ptc-review.md` **待补**。英文版不是实现前置条件，可在方案
确定后再同步。

**相关：**
- `tool-search.zh.md` —— 按需加载的工具发现（deferred tools）。与 Code Mode **互补但
  不同**：tool-search 解决"工具目录膨胀占 prompt/上下文"；Code Mode 解决"多往返 +
  中间大输出进上下文"。tool-search 更轻、更对症，且尚未实现 —— 是更靠前的一步。
- `host-tool-injection.zh.md` —— 宿主显式工具注入契约。若做 Code Mode，沙箱内可暴露
  的工具集应沿用同一姿态（显式 allow-list，默认最小）。
- `permission-hardening-plan.zh.md` / `host-fs-policy.zh.md` —— Code Mode 会开出一个
  新的、绕过常规工具审批的执行面，必须接回 `PermissionEngine` 与沙箱边界。

**锚点：** agentao `fix/test-retriever-time-bomb`@`a794fab`（2026-07-09，与 `main` 无
相关差异）；codex `main`@`2b44896c5a`；hermes `main`@`79f127480`；goose `main`@`b7eb1e973`
（含 #10214）。逐文件的通读清单与源码行号见**附录**。

---

## TL;DR / 当前决定

> Agentao 当前没有足够的真实需求证据支持立即实现 Code Mode。现阶段不立项，优先完成
> tool-search 并观察多工具往返与大中间结果是否成为稳定瓶颈。
>
> 若需求触发，可优先实验 Python 子进程方案，但必须先验证专用沙箱、不可绕过的整脚本审批、
> 嵌套工具派发入口、取消和有界输出。验证完成前不确定最终运行时路线。

**PTC = "Programmatic Tool Calling"，与 "Code Mode" 是同一件事**：给模型一个"执行代码"
工具，让它写一段脚本、脚本里调用宿主的真实工具，而不是每个工具发一次 JSON function-call。
收益一致：把 N 个工具调用 + 之间的处理逻辑压缩进**一次推理轮次**，且**只有脚本的最终输出
回到上下文，中间结果永不进上下文窗口**。

---

## 1. 是否现在做

Code Mode 的收益随"每轮多工具往返 + 大中间输出"规模增长。agentao 当前约 15 个原生工具 +
轻量 MCP，痛感未必到位。**这是需求门 —— 痛不痛由维护者与真实使用判断**，不该由本文单方面
宣布。触发信号可能是：接多个 MCP server 后出现"抓 N 页 / 处理 N 文件 / 带条件重试"这类
脚本化工作流的真实、反复出现的需求。

**同侪佐证（需求门尚未普遍触发）：**

- goose 已实现 code-mode，却**默认关闭**（`default_enabled:false`，`code-mode` Cargo
  feature 后）。
- gemini-cli **干脆没做**，只靠常规 per-tool 调用 + 一个普通 `shell` 工具（`shell` 不是
  工具桥，也无"中间结果不进上下文"设计）。

连成熟厂商 CLI 都把它当可选 / 实验 / 或不做 —— 佐证 Code Mode 是"按需上"而非标配
（gap≠need）。

---

## 2. 三种路线的关键差异

三个参照实现走了**三条不同的隔离路线**（外加 gemini-cli 作为"干脆不做"的第四个数据点）。
下表只列决策相关的差异，机制细节见附录：

| | **codex（`exec`）** | **hermes（`execute_code`）** | **goose（`code_execution`）** |
|---|---|---|---|
| 语言 | JavaScript | **Python** | **TypeScript**（async `run()`） |
| 运行时 | 全新 **V8 isolate** | **真正的 OS 子进程**（`subprocess.Popen`） | **进程内嵌 Deno/V8**（经 `pctx` crate） |
| 权限模型 | 唯一出口是注入的 `tools` 对象；工具调用照常经宿主 | 桩 → RPC → 宿主 `handle_function_call`（复用普通工具调用的审批路由）；整脚本 spawn 前再过一次审批门 | 外层 `execute_typescript` 走一次常规权限判断；内部回调不再逐工具审批 |
| 能力边界 | 无 Node / 文件 / 网络 / console —— 语言层就够不到外部 | 本地后端文件 / 网络开放（靠环境擦洗 + 输出脱敏兜底）；远程后端由容器隔离 | 仅注册当前已启用扩展的工具；可通过回调触达文件、网络或 shell 类工具 |
| 隔离 | 默认独立进程（hosted），失败回退进程内 V8 | 进程级（本地）/ 容器（远程） | 没有独立进程或容器边界；裸 TypeScript 是否具有直接文件 / 网络能力，不能仅从 Goose 集成层断言 |
| 工具桥 | 全局 `tools` 对象 | 代码生成 `hermes_tools.py` 桩 → Unix socket / TCP RPC | 回调注册表：pctx 生成 TS 桩 → 回调 → `dispatch_tool_call` |
| 运行控制 | cell-id `wait` 流式；`exit`/`notify` | 一次性返回 stdout（无流式）；300s 超时 + 协作式中断 | 超时、取消、嵌套调用取消传播 |
| 默认状态 | 内建 | 内建（cron / 无人会话默认禁用） | **默认关闭** |

> 注：goose 的 `execute_bash` 应称为 pctx 提供的**元工具**，不能直接等同于 Goose 的宿主
> shell 工具。

三条路线可归纳为一句话：**codex 约束解释器**（语言层够不到外部）、**hermes 约束进程 +
事后兜底**（真子进程 + 环境擦洗 + 输出脱敏 + 整脚本审批）、**goose 约束时间维**（超时 +
取消，但无空间隔离）。它们对"信任模型"的选择不同，没有唯一正解。

---

## 3. Agentao 当前缺少什么

- 工具都是**独立的 function-calling 工具**；MCP 工具注册为 `mcp_{server}_{tool}` 直接
  暴露给模型（`agentao/mcp/tool.py`、`agentao/tools/base.py::ToolRegistry`）。
- **没有** Code Mode / PTC / 代码执行工具。

有一些相邻地基，但都需要改造，**不是现成可复用**：

- `ToolRunner`（`runtime/tool_runner.py`）是 plan→execute→format→sanitize 的**批量**
  工具调用管线（`execute(tool_calls, …)` 收一组调用），**不是现成的单工具 RPC 派发
  接口** —— 接 Code Mode 需要新建一层 `(tool_name, args) → result` 派发适配。
- `PermissionEngine` 存在，但**整脚本一次性审批**不是它现成的用法，需要新增一条评估路径。
- `AsyncToolBase` + `CancellationToken` 提供超时 / 取消原语（这一层相对现成）。
- `sandbox-exec`（macOS）profile 机制存在，但**当前三个 profile 都是
  `(allow file-read*)`（任意文件读取），不能直接用于模型生成的 Python** —— 需要另写更严
  的专用 profile。
- **当前模型上下文输出没有通用密钥脱敏**（现有 redaction 只在 recorder / 宿主摘要路径，
  不覆盖回给模型的工具输出）。

**因此 Python 子进程只是"架构候选"，不是已验证路线。** 上述每一项都要先验证再谈落地。

---

## 4. 满足什么条件后启动实验

需求门触发后，第一步不是搭生产架构，而是一个受限的验证实验。

> 首次实验仅验证：
>
> 1. 专用沙箱能否阻止读取 workspace 外的敏感文件。
> 2. 整脚本审批能否避免被普通 allow 规则绕过。
> 3. 嵌套调用能否复用权限、事件和取消语义。
> 4. 超时或取消能否终止完整进程树。
> 5. stdout/stderr 能否在固定内存上限内收集。
>
> 实验只暴露少量只读工具，不支持无人值守，不处理流式与跨平台。

（其中第 4 项可复用 `capabilities/process.py::run_captured` 的进程树 kill / 超时硬化，
见 CLAUDE.md "Common gotchas"；这是唯一相对现成的一环。）

---

## 5. 开放问题

1. **是否存在稳定的真实需求。**（需求门 —— 维护者与真实使用判断，见 §1。）
2. **能否建立可信的执行边界。**（专用沙箱 + 整脚本审批 + 无人值守默认拒绝；跨平台隔离
   是现实成本项 —— `sandbox-exec` 仅 macOS，Linux 需另找 bwrap/namespaces/容器。）
3. **是否存在可复用的嵌套工具派发契约。**（把沙箱内工具调用桥回 `PermissionEngine` /
   事件 / 取消 —— 目前 `ToolRunner` 是批量入口，需要新建单工具派发适配。）

---

## 6. 结论

> **当前决定：暂不实现。**
> 先完成 tool-search 并收集多工具任务数据。出现稳定需求后，再进行受限的 Python 子进程
> 实验。实验通过前，不承诺 RPC 结构、跨平台支持、流式协议或生产化路线。

若/当决定推进，Python 子进程 + 桥回现有派发链是与 agentao 架构最近的**候选**方向，但这只是
起点假设，须经 §4 实验验证，而非本文的结论。

**本文状态：分析初稿，未批准、未实现。**

---

## 附录 A：codex Code Mode 实现要点（JS / V8）

通读 `code-mode-protocol/src/description.rs`、`code-mode/src/lib.rs`、
`core/src/tools/code_mode/`。

**工具面：** `exec`，把模型写的 JS 在**全新 V8 isolate**里当 async module 执行。所有
可用工具挂在全局 `tools` 对象上：`await tools.exec_command(...)`、
`await tools.mcp__ologs__get_profile(...)`（工具名规范化为 JS 标识符）。

**沙箱本质 = 约束解释器：** 描述原文 *"Runs raw JavaScript — no Node, no file system,
no network access, no console."* isolate 没有任何环境绑定，模型代码唯一能触达外部世界的
通道就是注入的 `tools` 对象。生成代码在语言层就够不到文件 / 网络。

**进程模型：** `code-mode: move to hosted mode by default` + `fall back to using in
process v8 if we fail to resolve external process` —— 默认把 V8 跑在**独立进程**
（`ProcessOwnedCodeModeSession`），失败才回退进程内。即便 V8 被攻破也多一层进程边界。

**辅助与流式：** `exit()`、`text()`/`image()`、`store()/load()`（同一 session 跨多次
`exec` 存取中间值）、`notify()`、`setTimeout`、`yield_control()`、`ALL_TOOLS`；长任务
`exec` 返回 `Script running with cell ID ...`，用 `wait` + cell-id 恢复 / 流式取新增输出。

**MCP 结果类型：** `description.rs` 内嵌一段 `MCP_TYPESCRIPT_PREAMBLE`（`CallToolResult`
/ `ContentBlock` 等 TS 类型），让模型知道 MCP 工具返回值的结构。

**crate 布局：** `code-mode`（V8 运行时）、`code-mode-host`（宿主协议：帧编解码、cell、
in-flight/session 上限）、`code-mode-protocol`（wire 类型 + 描述）、
`core/src/tools/code_mode`（工具集成：exec/wait handler、response adapter）。

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

只把脚本 **stdout** 回给模型（中间工具结果永不进上下文）：

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

## 附录 D：gemini-cli —— "干脆不做"的第四个数据点

gemini-cli 没有 Code Mode —— 只有常规 per-tool JSON 调用 + 一个普通 `shell` 工具。
`shell` 不是工具桥，也无"中间结果不进上下文"设计。作为一个成熟厂商 CLI 的选择，它是 §1
"Code Mode 按需上而非标配"的佐证之一。

---

## 附录 E：codex 与 hermes 的安全模型对比

codex 与 hermes 都把嵌套工具调用桥回宿主的**同一个**派发器，所以"逐工具审批"这条轴上二者
**对称**：

- codex：脚本里的 `tools.*` 调用经 `handle_tool_call_with_source(call, ToolCallSource::CodeMode{…})`
  （`core/src/tools/code_mode/mod.rs:304`）走**普通工具用的同一个 `ToolRouter`/`ToolCallRuntime`**；
  `ToolCallSource::CodeMode` 只是溯源标签，非提权。
- hermes：桩调用经 RPC 回 `handle_function_call`（`tools/code_execution_tool.py:588-590`），
  用 `propagate_context_to_thread` 让审批上下文跨线程存活（`:1267-1271`，否则沙箱内工具调用
  会静默自动批准）。

真正的差异是**一个架构事实**，它连带决定了其余所有防御层：

> **codex 脚本没有任何环境能力**：isolate 在 `code-mode/src/runtime/globals.rs::install_globals`
> 里删除 `console`/`Atomics`/`SharedArrayBuffer`/`WebAssembly`，只注入 `tools`/`ALL_TOOLS`/
> 定时器/`text` 等辅助。脚本触达外部世界的**唯一**通道是 `tools.*`，而每个 `tools.*` 都过
> `ToolRouter`。
>
> **hermes 本地后端脚本是真 OS 子进程，拥有完整 fs/网络**，能用 `subprocess`/`os.system`/
> `open()`/`ctypes` **绕过** `tools.*` 桥，直接摸文件和网络。

### E.1 对比表（grep 验证）

| 维度 | **codex Code Mode** | **hermes PTC（本地后端）** |
|---|---|---|
| 约束脚本的是什么 | **V8 isolate 本身**（语言层：无 fs/网络/console/process） | **OS 进程边界 + 环境擦洗 + 输出脱敏**；无进程内沙箱 |
| 脚本能否绕过单工具审批 | **不能** —— 唯一能力是 `tools.*`，逐个过 `ToolRouter` | **能** —— `open()`/`subprocess` 直达 fs/网络 |
| 嵌套工具调用治理 | 同直接调用的 `ToolRouter`（`ToolCallSource::CodeMode` 仅溯源，`mod.rs:304`） | 同 `handle_function_call`（审批上下文跨线程存活，`:1267-1271`） |
| 整脚本审批门 | **不需要**（脚本除工具外做不了任何事） | **必需**：`check_execute_code_guard`（`tools/approval.py:2953`），spawn 前审全脚本 |
| 密钥暴露面 | **近乎零**（isolate 无 env/fs） | 子进程 env（`_scrub_child_env :146-207` 擦洗）+ 磁盘凭证（靠输出脱敏 `:1505` 兜底） |
| 进程隔离深度 | 默认 hosted 独立进程 + 失败回退进程内 V8（2 层） | 子进程（本地）/ 容器（远程，容器即隔离） |
| 资源 / DoS | `yield_time_ms`、hosted 进程、输出 token 预算 | 300s 超时、每脚本 50 次工具调用、stdout 50KB / stderr 10KB |
| 无人值守 | **无需特殊 carve-out**（不授予额外能力，照常走审批策略） | cron / 无人会话**默认禁用** `execute_code` |
| 工具暴露 | 已启用工具全挂 `tools` | `SANDBOX_ALLOWED_TOOLS`（7 个，`:62-70`）∩ 会话启用 |

### E.2 谁更强，以及在哪弱

- **codex 结构上限更高**：把"能力受限解释器"作为**主边界**，脚本不可能绕过审批，密钥暴露面
  接近零，且整脚本审批门与无人值守特判在 codex 里是"不适用"而非"待补"。**代价**：(1) 安全性
  押在 V8 isolate 健全性上（V8 逃逸是历史攻击面，由 hosted 独立进程回退作第二道防线）；
  (2) 脚本**结构上无法**做本地计算 —— 想 `import pandas` 读个 CSV 也得走工具，表达力受限。
- **hermes 主边界更弱**（本地后端 fs/网络全开），用**更厚的纵深防御**补偿。**弱点**在于这些
  都是**补偿性控制**：审批会疲劳 / 误批；脱敏是**尽力而为的模式匹配**（脚本可编码密钥躲过，
  或经一个已获批工具外泄）；env 擦洗只覆盖环境变量，不覆盖磁盘上的 `~/.aws`、`~/.ssh`。**但**
  hermes 远程后端（容器）把主边界换回强隔离，且它换来了 codex 没有的**本地计算表达力**
  （`import pandas`、相对路径）。

**一句话：** codex = "脚本什么都做不了，除非调用受审批的工具"（语言即沙箱）；hermes = "假设
脚本敌对，包住爆炸半径"（进程 + 审批 + 脱敏）。前者上限更高、更简洁但更死板；后者更灵活但
安全靠一摞补偿控制。

### E.3 对 agentao 的含义

这正是 §2 归纳的"codex 约束解释器 vs hermes 约束进程 + 事后兜底"。它解释了 §4.3 候选路线的
关键取舍：agentao 若走 hermes 式子进程，**核心补强是给它一个 codex 式的主边界** —— 但用
**OS 强制的 `sandbox-exec`** 替代语言层 isolate，把隔离从"事后脱敏"抬到"内核强制"。§3 已验证
的现实约束仍在：当前三个 profile 都是 `(allow file-read*)`，且模型输出无通用密钥脱敏 ——
这两块基石不补齐，hermes 式子进程在 agentao 上就仍是 hermes 那种"弱主边界 + 补偿控制"姿态，
拿不到 codex 那级的结构性保证。因此本附录的结论回流到 §4 实验项 1（专用沙箱）与项 2（不可
绕过的整脚本审批）—— 二者正是把 agentao 从 hermes 姿态推向 codex 姿态的两块最小拼图。
