# Agentao — `acp_client/` 子包审计

**状态:** 评审记录。2026-07-23 以"六月同等标准"对 `agentao/acp_client/` 子包做的
四维审计——这是 ACP **客户端**(Agentao **向外**连接它以子进程 stdio 方式拉起的
项目本地 ACP server)。**本文是有证据支撑的发现清单 + 优先级建议,不是已批准的
计划。** 该子包**不在** 2026-06-19 `optimization-opportunities-review.md` 的审计
范围内(那一轮覆盖 runtime、打包、CLI 和 ACP **服务端**);本文补上这个缺口。
**读者:** Agentao 维护者。
**配套:** `acp-client-audit.md`(权威英文版)。
**相关:**
- `optimization-opportunities-review.md` —— 六月审计,本文沿用其方法与分层
  (立即修的 bug vs 顺手合并、`gap ≠ need`、已核实的非问题)。
- `acp-server-conformance-review.md` —— ACP **服务端**,本文是其客户端镜像。
- `core-boundary-review.md` —— 渲染/展示是否该住在 core(AC5)遵循它记录的边界。

**方法:** 四路并行取证——**复杂度/结构**、**正确性/并发/资源安全**、**重复/复用**、
**契约/边界/安全**,每条发现必须引用 `file:line`,以 `grep`/阅读佐证,而非凭直觉,
符合本仓"先证据后建议、gap ≠ need"的纪律。最高影响、最不可逆的论断(两个并发 bug、
进程 kill 局限、死代码删除、渲染/注入面)在落文档前**已逐一对源码独立复读**。代码
引用锚定 `main`@`278e92a`(2026-07-23)。行号会漂移——动手前重新 grep。

---

## 摘要(TL;DR)

`acp_client/` 是一个**构建良好、防御式编码**的子包——19 文件 / ~5.8k LOC,11 个测试
文件覆盖,manager 有意的 mixin 分解,对拉起的子进程正确剥离了 provider 凭据,并且有一
套确实很难的并发契约(单活跃 turn 锁 + handshake 锁 + sticky-fatal 恢复)——**大体是
对的**。**没有安全漏洞,也没有架构性紧急问题。** 四个审计员最强的信号是**在少数几项上
收敛**,而不是一长串缺陷。

发现分为三类有边界的工作:

1. **立即做、低风险(AC1、AC4)。** 一个平凡的并发 bug(`stop_all` 迭代活字典——
   一行 `list()` 修复)+ 三处会被误读为"活行为"的死/仅测试代码。都安全、微小、能真正
   减少困惑。
2. **尽快做、小改动(AC2)+ 记录性局限(AC3)。** 恢复计数器的锁不对称(AC2)是个小的
   正确性修复;进程树 kill 缺口(AC3)是**已文档化**的局限,其修复非即插即用(需要
   改 spawn 侧)——记录、可选。
3. **顺手/较大(AC5–AC8)。** 对服务器可控展示文本做终端转义 + 尺寸加固(AC5),以及两个
   可维护性项:复制在 5 处入口的 handshake"sticky-fatal 舞蹈"(AC6,**危险的那种**
   重复)和 261 行的 `prompt_once`(AC7)。这些承载真正的长期成本,但行为风险也最大——
   Tier-3、可选。

> **优先级排序是维护者的判断。** 本文提供 grep 核实的证据和*建议*次序;不替维护者裁定
> 什么值不值得花时间。

> **范围诚实——并发发现(AC1/AC2)只在已文档化的多线程嵌入面上触发。** 交互式 CLI 里
> `get_status`/`stop_all`/`send_prompt` 单线程运行,永不竞争。但该模块被明确文档化为
> 支持并发 daemon/workflow 嵌入(`turns.py` 关于监控线程 + 工作线程的 docstring),而且
> **每个客户端的 reader 线程已经在与主线程并发地跑回调**,所以对那个面而言竞争是真实的,
> 不是假想。

---

## 基线指标

| 指标 | 值 | 解读 |
|---|---|---|
| 源文件 / LOC | 19 / ~5,785 | 最大的从未被审计的子包 |
| 引用它的测试文件 | 11(`test_acp_client_*` ×7 + 4) | 覆盖良好 |
| 最大文件 | `manager/turns.py`(892) | turn 编排 + ephemeral |
| 最大方法 | `manager/turns.py::prompt_once`(261,`452–712`) | **全仓最长方法** |
| 子进程 env 凭据剥离 | 有(`process.py:124 build_child_env`) | 安全正向(见 N1) |
| 远程/URL 传输 | 无(纯 stdio) | 消除 SSRF 面(见 N2) |

---

## Tier 1 —— 立即做,低风险

### AC1 —— `stop_all()` 迭代活 `_clients` 字典 → 并发嵌入下 `RuntimeError` + 子进程泄漏 · MEDIUM(bug——立即修,平凡)
`lifecycle.py:75`:`for name, client in self._clients.items():` 迭代**活**字典——而紧邻
的下一块(`:82`)却防御式拷贝了 `list(self._ephemeral_clients.items())`。这个不对称就是
线索。`_clients` 会从**无锁**且可并发到达的调用点被 `pop`:`_check_cached_client_alive`
(`recovery.py:376`)在**不持** handshake 锁的情况下被文档自称"只读"的访问器 `get_status`
(`status.py`)、`readiness`(`status.py`)和 `prompt_once` 前置检查(`turns.py:525`)调用;
`stop_server`(`lifecycle.py:171`)和 `_evict_cached_client`(`lifecycle.py:114`)也在该路径
上 `pop`。

- **失败场景:** 监控线程对一个子进程在 `READY` 状态下已死的 server 轮询
  `readiness()`/`get_status()`;其 `_check_cached_client_alive` 执行
  `self._clients.pop(name)`(`recovery.py:376`),**恰在**另一线程处于 `stop_all()` 的
  `for … in self._clients.items()` 之中 → `RuntimeError: dictionary changed size during
  iteration`。`stop_all` 中途中止;崩溃点之后的每个 handle 都拿不到 `handle.stop()` →
  **ACP 子进程泄漏。**
- **修复(单点、平凡):** 迭代快照——`for name, client in list(self._clients.items()):`——
  与下一行的 ephemeral 块一致。PR 只改这一个方法。

### AC4 —— 三处死/仅测试代码被误读为活行为 · LOW(快赢)
每一处都经 grep 核实**零生产调用/写入**;三者都仅由测试维持存活,读者会把惰性机制当真:

- **`interaction.py:56` `deadline_at` + `:122` `expire_overdue`** —— `deadline_at` 除
  `None` 默认外**从未被赋值**(grep:只有 docstring `:43`、字段默认 `:56`、以及 `:134–135`
  两处*读取*)。故 `deadline_at is not None` 守卫在生产中**恒为 False**,`expire_overdue()`
  是保证的空操作——整套 deadline/过期机制是惰性的,只被手动设字段的测试接线。
- **`turns.py:760` `_open_ephemeral_client`**(取锁包装)—— 零生产调用;`prompt_once` 直接
  调 `_open_ephemeral_client_locked`(`:789`)于 `turns.py:575`。其余提及都是注释。只有一个
  测试把它 monkeypatch 成 spy——而它连真实的 `_locked` 路径都拦不到。
- **`recovery.py:166` `_note_handshake_failure`**(裸版,非 `_and_maybe_fatal`)—— 零生产
  调用(grep 遍 `agentao/`);只有测试用它模拟 streak。
- **修复:** 删掉两个死 helper,并对 `deadline_at` 要么接线要么删除过期机制。删除会触及引用
  它们的测试——作为一次小而边界清晰的清理,不要和行为改动捆绑。

---

## Tier 2 —— 小的正确性修复 + 一个记录性局限

### AC2 —— 恢复计数器写入未持 `_recovery_lock`;"只读"status 访问器在锁外改动恢复状态 · LOW–MEDIUM(bug——立即修,小)
与 AC1 同根(轮询路径上的无锁改动),但是独立缺陷。`recovery.py:389` 写
`self._handshake_fail_streak[name] = 0` **未持** `_recovery_lock`——而对该 map 的**其余每一处**
写入都持锁(`recovery.py:164,168,184,200,300`;例如 `_clear_fatal:296–300` 把同样的 `= 0`
写入包在 `with self._recovery_lock` 里)。外层 `_check_cached_client_alive` 还会 `pop`
`_clients`、关闭 client、调 `_mark_fatal`、改 handle 状态——全都从文档自称"只读"的
`status.py` 访问器无锁可达。

- **失败场景:** 线程 A 在 `_note_handshake_failure_and_maybe_fatal` 里对 streak 做持锁的
  读-改-写(读 `1` → 写 `2` → 在 `>1` 触发 fatal);线程 B(对一个 `READY` 中已死 server 的
  无锁 `get_status` 轮询)在 A 的读与写**之间**执行 `:389` 的裸 `= 0`,因为 B 从不去争 A 持有
  的锁。这次重置丢失或覆盖 A 的自增 → "连续 2 次 handshake 失败 ⇒ sticky-fatal"的账目错位,
  使 server 提前一次崩溃就 fatal,或该 trip 时不 trip。影响有界(恢复计数器差一),但一个被
  文档化为"只读"的方法(`status.py`)悄悄关 client、逐出 `_clients`,本身就是意外。
- **修复(小):** 对 `:389` 写入加 `_recovery_lock`(并顺带审计 `_check_cached_client_alive`
  里其他改动是否同样需要)。或者——这是维护者判断,不是被迫的唯一路径——给 status 访问器一个
  无锁**只读**存活探针,把逐出/标记推迟到下一次真正的恢复调用,让"只读"名副其实。窄锁修复改动更
  小;探针拆分契约更干净。

### AC3 —— 进程 teardown 只回收直接子进程;SIGKILL 时孙进程被孤立 · LOW–MEDIUM(已文档化局限——记录、可选)
`process.py:127` 拉起 server 时**没有** `start_new_session=True` / 进程组;`_stop_unlocked`
逐级升级 `terminate()`(`:234`)→ `kill()`(`:240`),都只对直接子进程发信号。`acp_client/`
从不使用本仓的 `capabilities/process.py::kill_process_tree`(killpg / `taskkill /T`)。

- 这在代码里**有明确文档**(`process.py:209–216`):设计**刻意**偏好优雅的 stdin-EOF 路径,
  正是为了让 server 自己回收它的 MCP/shell 孙进程——"这里的 `terminate()` 做不到"。所以是
  *已知*局限,不是疏漏。
- **失败场景:** 一个既忽略 stdin-EOF **又**忽略 SIGTERM 的 server 被 SIGKILL;它的孙进程
  (它自己的 MCP-stdio / shell 子进程)被孤立并存活。反复 `restart_server`/`stop_all` 会累积。
- **修复非即插即用。** `kill_process_tree` 用 `killpg(proc.pid)`,只有子进程是会话/组 leader 时
  才能隔离后代——所以正确修复必须**既**以 `start_new_session=True` spawn,**又**把 kill 路径切到
  树回收器。两个选项,维护者定:(a) 做这个 spawn+kill 改动并走 `kill_process_tree`,或
  (b) 保留已文档化的"优雅优先"设计,接受 SIGKILL 尾部泄漏这个罕见、已记录的边界。记录,不排期。

---

## Tier 3 —— 顺手加固 + 较大重构(可选)

### AC5 —— 服务器可控展示文本未做终端转义清理;agent 分片尺寸无界 · LOW–MEDIUM(顺手加固)
渲染器把**第三方** ACP server 的输出打到用户终端(`render.py`)。两个缺口:

- **终端转义注入。** plain 回退路径(`render.py:117`,`sys.stdout.write(render_all_plain(...))`)
  **原样**写服务器文本——`render_plain:74` 只做 `\n`→`\n  `,不剥离转义序列。Rich 路径转义的是
  Rich *markup*(`rich.markup.escape`,`:185,195,197`),那处理的是 `[bold]` 之类标记,**不是**
  裸 ANSI/终端控制序列(`\x1b[…`、OSC 设标题、剪贴板)。源文本完全服务器可控
  (`agent_message_chunk` → `helpers.py`)。恶意/被攻陷的 server 可向终端注入光标/屏幕/标题操纵;
  plain 路径是最明确的载体。
- **尺寸无界(DoS)。** stdout 读取(`process.py` 行迭代)与 `agent_message_chunk`/
  `agent_thought_chunk` 文本(`helpers.py` **不截断**返回,而其他每种 kind 都截到 40–120 字符)
  都无长度上限。server 发一条多 GB 的单行会强制无界内存缓冲。
- **修复(顺手):** 在**两条**路径展示前剥离/替换 C0/C1 控制字节(保留 `\n`/`\t`),并对读取的
  每行/每片长度设上限。下次动 `render.py`/`helpers.py` 时并入。(信任姿态:同一 server 已作为
  env 被剥离的子进程运行——这是对*输出*通道的纵深防御,不是头号漏洞。)
- **状态:已实现**(两条路径都做转义清理 + 每片尺寸上限);readline 级上限**延期**(需重写
  帧解析)。详见下方"实现状态"。

### AC6 —— re-session + sticky-fatal 的 handshake"舞蹈"复制在 5 处入口 · MEDIUM(危险重复——合并)
分类*原语*已被提取(`_reclassify_as_handshake_fail` / `_note_handshake_failure_and_maybe_fatal`
/ `_note_handshake_success`),但**包裹 `create_session`/`initialize` 的那套序列**——
`try … except BaseException: if reclassify: note-fatal; raise` + 末尾 `note-success`——被手工内联在
**5 处**(grep 确认):`connection.py:170–173`、`:263–271`、`:400–406`、`turns.py:662–665`、`:840–869`。

- **为何是危险的那种:** 代码自己反复注释,*每个* handshake 入口"都必须翻转 sticky-fatal……
  否则选了某个 API 的 host 会悄悄退出恢复契约"。这个不变量**正因为被复制而脆弱**——第 6 个入口
  忘了这套舞蹈就会悄悄破坏恢复,而没有测试强制包装存在。
- **修复:** 一个 `_handshake_guarded(fn, name)` 包装/上下文管理器,跑 setup 调用、失败时
  reclassify+maybe-fatal、成功时 `note-success`;5 处都调它。这是针对一个正确性相邻不变量的真正
  合并——值得单独做,而非仅顺手。两个相关闭包(`connection.py` 里 ≈ `turns.py:_open_ephemeral_client_locked`
  的 client-callback 构造)可随之并入。

### AC7 —— `prompt_once` 是 261 行、深度约 8、混 7 个关注点的方法 · HIGH 维护成本(重构,可选)
`turns.py:452–712` —— 全仓最长方法(span 已确认;下一方法 `_rollback_ephemeral_on_busy` 起于
`:714`)。它交织:policy 解析 + handle 查找、恢复前置检查、handshake 锁下的 ephemeral 建立、
fail-fast 取 turn 锁 + 回滚、一个**重实现 `_ensure_connected_locked` 的 cached-client 复用/re-session
分支**(这是 AC6 那 5 份复制之一)、turn 运行 + `PromptResult` 构建、以及深度嵌套的 `finally`
ephemeral 拆除。深度 8 的"re-session-在-`finally`-内"区域,正是并发回归最可能藏身处。

- **可提取块(由 AC6 的 helper 挑重担):** `_setup_ephemeral_or_defer(...)`、
  `_reuse_cached_client_for_prompt_once(...)`(→ 共享的 `_handshake_guarded`)、
  `_teardown_ephemeral_after_prompt(...)`。拆分后核心路径降到约 120 行。
- **约束:** handshake 锁/turn 锁的次序、fail-fast 的 `_rollback_ephemeral_on_busy` 门,以及
  `finally` 拆除里"别停一个活赢家正用的 proc"守卫(`turns.py:751`)是承重的。机械重构,须保并发
  测试全绿。

### AC8 —— 一簇 LOW、无漂移的内部重复 · LOW(仅顺手)
记录下来免得下一轮重新发现;**没有一处有危险漂移**,故只在文件已打开时并入:

- `manager/interactions.py`:`approve_interaction`(`:412–453`)≈ `reject_interaction`(`:455–498`)
  互为镜像;`{"outcome":{"outcome":"selected","optionId":…}}` 信封手搭 5 次(`:277,297,314,443,488`)。
- `manager/helpers.py`:`_select_approve_option`(`:218`)≈ `_select_reject_option`(`:131`)——
  同 3 遍扫描,只差目标字面量;`_opt_id` 闭包定义 3 次。
- `client.py`:`send_prompt`(`:519`)重实现 `send_prompt_nonblocking`(`:622`)的发送半段 +
  `finish_prompt`(`:679`)。
- `models.py`:`InteractionPolicy.mode` 校验 3 次(`:206`、`:367`、`_parse…:274`);
  `max_recoverable_restarts` 边界查 2 次(`:347`、`from_dict:466`)。
- **修复:** 参数化 helper(`_select_option(...)`、`_selected(option_id)`,让 `from_dict` 走
  `__post_init__`)。顺手;**不要**为删这些而制造共享 API。
- **状态:部分实现**——无漂移的净收益已落地(`helpers.py` 的 `_select_option` + `_opt_id`;
  `interactions.py` 的 `_selected_outcome` + `_resolve_and_respond`)。`models.py` 的校验去重与
  `client.py` 的 `send_prompt` 重叠**有据跳过**(前者是随上下文变化的错误消息 UX;后者在并发敏感
  路径上)。详见下方"实现状态"。

---

## 已核实的非问题(查过、清白——不要"修")

- **N1 —— 子进程 env 确实被剥离(安全正向)。** `process.py:124`
  `build_child_env(self.config.env)` 在 spawn 前丢弃 `HARNESS_ENV_KEYS`(provider 凭据);唯一的
  `Popen`(`:127`)用它。第三方 ACP server **不会**继承 `OPENAI_API_KEY` 等。有明确文档
  (`:119–124`,"与 MCP server 相同的信任位置")。
- **N2 —— 无 SSRF 面。** `AcpServerConfig` 只接受 `command/args/env/cwd`;在 `acp_client/` grep
  `url|https?|headers|authorization|bearer|sse|streamable|websocket` → **无传输匹配**。纯 stdio;
  `url_policy.py` 不适用(仅在将来加 URL 传输时作为观察项)。
- **N3 —— `agentao.log` 中密钥已脱敏。** `agentao.acp_client` logger 是 `agentao` 的子 logger,
  其文件 handler 带 `_RedactingFormatter`;debug 参数转储与子进程 stderr 都被脱敏。config 从不记录
  env *值*(错误用 `type(...).__name__`)。无手搓脱敏(正确——grep `secret_scan|redact` → 无匹配;
  这里不需要)。
- **N4 —— ephemeral open/rollback/busy 竞争防御良好。** 追踪了双 prompt 交错:handshake 锁序列化
  open/connect/rollback;`has_ephemeral` fail-fast 竞争的 `prompt_once`;`_rollback_ephemeral_on_busy`
  以 `name not in self._clients and not process_was_running`(`turns.py:751`)守拆除,故活赢家在用的
  proc 绝不被拆。无 TOCTOU。
- **N5 —— 并发 `_send` 不会破坏 NDJSON 帧。** 每帧是单次 `stdin.write(line + "\n")`,`BufferedWriter`
  的 `write()` 在其内部锁下原子;并发 cancel/response/notify 以整行交错,绝不逐字节。
- **N6 —— 正常错误路径无 turn-slot / proc 泄漏。** `_active_turns` 在每条路径 `finally` 清理;失败
  connect / 坏 handshake 仅在*本次*调用启动了该 proc 时(`_we_started` 门)才调 `handle.stop()`,故
  预热 server 能挺过坏 handshake。`prompt_once` 的 ephemeral 在 `finally` 被 pop+close(`turns.py:683–712`)。
- **N7 —— 边界干净。** 无 core 模块 eager import `acp_client`;包外唯一引用是两处 docstring 提及,以及
  CLI 入口里**惰性、函数内** import(`cli/acp_inbox.py`、`cli/commands_ext/acp.py`)。渲染/展示住在
  `acp_client/` 内(轻微分层气味)但自足——`client.py`/`process.py`/`manager/*` 从不 import `render`,
  故 core 不会把展示拉进来。
- **N8 —— `config.py` 未复制**六月审计标记的全仓"读-json-吞异常"惰性惯用法;它在 `OSError` 与
  `JSONDecodeError` 两处都**抛** `AcpConfigError` 并带精确消息。设计上更严。
- **N9 —— manager 的 mixin 拆分是有意的,不是 god-class。** lifecycle/connection/turns/interactions/
  status/recovery 的拆分在模块 + `__init__` docstring 有文档;共享 `self.*` 状态是有文档的契约。

---

## 建议次序

> **这是最初的提案。** 实际落地情况(以及 AC4 的偏离)见下方**实现状态**——它取代本排序。

1. **PR 1 —— 立即做、低风险。** AC1(`stop_all` 快照——一行)+ AC4(删三处死代码)。除消除一个崩溃
   和死代码外无行为改变。
2. **PR 2 —— 小的正确性。** AC2(给恢复计数器写入加锁,或拆只读探针——维护者定)。单一、边界收紧。
3. **记录、可选。** AC3(做 spawn+kill-tree 改动 *或* 接受已文档化局限)——一个刻意决定,不是排期工作。
4. **Tier 3 —— 可选、更高价值/风险。** AC6(sticky-fatal 舞蹈是唯一值得单独做的合并——一个正确性相邻
   不变量),然后 AC7(`prompt_once`,由 AC6 的 helper 降险)。AC5 加固与 AC8 簇在这些文件下次被动时
   顺手并入。

AC3 的 spawn 模型改动与 AC5 的输出加固姿态是 harness-vs-product 判断题,归维护者。

---

## 附录 —— 每条发现如何核实

| 发现 | 核实 |
|---|---|
| AC1 | 读 `lifecycle.py:73–97`(`:75` 活 `.items()` vs `:82` `list(...)`);追踪 `_clients.pop` 点——`recovery.py:376`、`lifecycle.py:114/171`——从 `status.py` 无锁可达 |
| AC2 | 读 `recovery.py:296–407`;`_clear_fatal:300` 对同样的 `= 0` 写入持 `_recovery_lock`,`:389` 不持;grep 所有 `_handshake_fail_streak` 写入确认不对称 |
| AC3 | 读 `process.py:100–260`:`:127` `Popen` 无 `start_new_session`;升级 `terminate:234`/`kill:240`;局限注释 `:209–216`;对比 `capabilities/process.py::kill_process_tree` |
| AC4 | grep `deadline_at`(除 `:56` 默认外无赋值)、`_open_ephemeral_client\b`(只有注释 + def)、`_note_handshake_failure\b`(无生产调用) |
| AC5 | 通读 `render.py`:plain `sys.stdout.write` `:117`,`escape` 处理 markup 而非 ANSI `:185/195/197`;`helpers.py` agent 分片不截断 |
| AC6 | grep `_reclassify_as_handshake_fail`/`_note_handshake_success` → 5 处调用簇(`connection.py:170/263/400`、`turns.py:662/840`);对应 `create_session`/`initialize` 点 |
| AC7 | `awk` 方法 span → `prompt_once` `452–712`(261 行),下一 def `_rollback_ephemeral_on_busy` 在 `:714` |
| N1–N9 | 读 `build_child_env`;grep url/SSRF 传输(无);logger 父子关系 + `_RedactingFormatter`;追踪 ephemeral 竞争交错;NDJSON 原子写推理;`_we_started` 门;`agentao/` 全局 `import.*acp_client` |

---

## 实现状态(2026-07-23 → 2026-07-24)

**已落地:AC1(崩溃修复)、AC2(加锁修复)、AC3(进程树 kill)、AC4′(测试正确性修复)、
AC6(handshake 舞蹈合并)、AC7(`prompt_once` 拆分)。AC4 的"死代码"经直接核查被重新界定,
并刻意未删除**——直接阅读与"死代码,删掉"的框定相矛盾,于是诚实的选择是保留代码并记录原因
(与六月审计 T1.4 回滚同样的保守反射——不要因为某表面看似死就提前终结一个契约)。AC5 与
AC8 仍记录待办(顺手项)。

- **AC1 —— 已实现。** `lifecycle.py::stop_all` 现在迭代 `list(self._clients.items())`
  (快照),与其正下方的 `_ephemeral_clients` 块一致。新增回归测试:
  `tests/test_acp_client_process.py::TestACPManager::test_stop_all_survives_client_removed_mid_iteration`
  —— 一个 `close()` 会 pop 兄弟条目的假 client 确定性复现迭代中改动。**已证明有牙:**
  回退这一行修复会让测试精确抛出 `RuntimeError: dictionary changed size during iteration`
  于 `lifecycle.py:80`;带修复时,acp_client + headless 全套绿(194 passed)。随后一次 `xhigh`
  workflow 代码评审指出该测试的显式断言 `mgr._clients == {}` 由 `stop_all` 无条件的 `clear()`
  保证,故补上 `client_a.closed and client_b.closed` 断言,证明每个快照 client 确实被 close(而
  非仅被丢弃)。(评审的另一候选——两个 close 循环现在是复制粘贴——被 verify 阶段驳回:它们清的
  是不同的 dict。)

- **AC2 —— 已实现。** `recovery.py::_check_cached_client_alive` 现在把 streak 重置写入
  (`self._handshake_fail_streak[name] = 0`)包在 `with self._recovery_lock:` 里,与其余每处
  streak 改动一致(`_clear_fatal` / `_note_handshake_success` /
  `_note_handshake_failure_and_maybe_fatal`)。已确认 `_mark_fatal` 与 `_note_recovery_attempt`
  本就持锁,故 `:389` 是唯一无锁的恢复字典写入。选了窄锁方案而非只读探针拆分(改动更小)。绿:
  embedding + headless 套件(117 passed)。

- **AC3 —— 已实现。** `process.py::ACPProcessHandle.start` 现在以 `start_new_session=True`
  (POSIX)/ `CREATE_NEW_PROCESS_GROUP`(Windows)spawn,强制停止升级链的最终 SIGKILL 改走
  `capabilities/process.py::kill_process_tree` 而非 `self._proc.kill()`,使无响应 server 的
  MCP/shell 孙进程被回收而非孤立。优雅 stdin-EOF 路径与子进程作用域的 SIGTERM 中间阶段不变
  (server 在干净关停时仍自己回收孙进程),只有最后手段变为整树作用域。两半必须一起改,因为
  `killpg(proc.pid)` 只有在子进程是组 leader 时才安全。两个升级超时提为模块常量
  (`_TERMINATE_STOP_TIMEOUT` / `_KILL_STOP_TIMEOUT`)以便测试提速。两个新测试:快速验证子进程是
  自己进程组 leader,以及一个集成测试验证忽略信号的 server 的孙进程被回收——**已证明有牙**
  (改回 `self._proc.kill()` 会让孙进程存活、测试失败)。后续评审发现首版只在 server **挺过**
  SIGTERM 时才回收整树;而**死于** SIGTERM 且不回收自己子进程的 server 仍会孤立它们。已修:让整树
  回收在 SIGTERM 阶段后**无条件**执行(组已空时为 no-op),并为"死于 SIGTERM"路径加了第二个有牙
  测试。评审还指出两个**接受的权衡**,因需改动本子包之外、留作记录性后续:(1)`start_new_session`
  使 server 脱离 agentao 的控制终端,终端关闭的 `SIGHUP` 不再回收它——agentao **自身**异常终止时的
  健壮清理需要 CLI 层的 `SIGHUP`/`SIGTERM` handler 调 `stop_all()`(交互式 CLI 目前只靠 `finally`
  清理);(2)Windows 上强制 kill 现走 `taskkill /F /T`(≤5 秒)而非瞬时 `proc.kill()`,故在已然退化
  的强制路径上 handle 锁多持有一小会儿。两者都是正确的进程组整树回收所固有的。

- **AC4′ —— 已实现。** `test_acp_client_embedding.py`(无 session 的 cached 复用 re-session 测试)
  现在 spy 于 `_open_ephemeral_client_locked`——`prompt_once` 真正调用的方法——而非从不被调用的
  `_open_ephemeral_client` 包装,故 `called["ephemeral"] == 0` 现在真正断言"cached 复用路径上未
  创建 ephemeral",而非恒真。仍绿。

- **AC4 —— 重新界定,未删除。** 逐一对着调用方阅读三处表面,发现没有一处是纯 rot:
  - `interaction.py deadline_at` / `expire_overdue` —— 一个**设计了但未接线的功能**
    (交互 deadline → 默认动作),带完整 docstring 和独立测试
    (`test_acp_client_cli.py::test_expire_overdue`)。接线 vs 删除是**维护者的产品决定**,
    不是清理——保留。
  - `recovery.py:166 _note_handshake_failure` —— 作为干净的**测试接缝**:
    `test_headless_runtime.py:1207,1247` 用它模拟"streak = 1",不必去戳私有
    `_handshake_fail_streak` dict。删了会逼那些测试直接改内部状态——降级。保留。
  - `turns.py:760 _open_ephemeral_client`(冗余取锁包装)—— 这里真正的缺陷**不是**死包装,
    而是一个**空断言**:`test_acp_client_embedding.py:945–953` 对 `_open_ephemeral_client`
    做 spy 并断言它"MUST NOT fire",但生产走 `_locked` 路径,故该断言恒真、什么也没守。
    **子发现(AC4′,已实现,见上):** 把 spy 改指向 `_open_ephemeral_client_locked`,让测试
    真正断言"cached 路径上不会创建 ephemeral client"。这是测试正确性修复(行为有意义),与任何
    代码删除无关。

- **AC6 —— 已实现。** 新增 `RecoveryMixin._handshake_guarded(name, *, on_failure=None)`
  上下文管理器,统管 sticky-fatal 舞蹈(失败:reclassify → maybe-fatal → raise;成功:
  note-success)。全部 **5** 处复制均改走它:`connection.py` ×3(`_connect_server_locked`
  cached 复用 + greenfield、`ensure_connected` re-session)与 `turns.py` ×2(`prompt_once`
  cached re-session、`_open_ephemeral_client_locked` greenfield)。站点2、5 通过 `on_failure`
  钩子保留各自清理(关半建 client、停自启 proc)。**顺序细节(被 xhigh 代码评审抓到):** 两个
  greenfield 站点的"记账 vs 清理"顺序**相反**,而 `_mark_fatal`(→ handle 状态 `FAILED`)与
  `handle.stop()`(→ `STOPPED`)都写 `handle.info.state`,故顺序决定 host 经 `get_status()` 看到的
  终态。因此 CM 加了 `cleanup_before_accounting` 标志;每个站点传入复现其原有顺序的值(站点2 →
  `FAILED`,站点5 → `STOPPED`),使合并真正行为保持。第 6 个入口再也无法漏掉记账。绿:acp_client +
  headless 全套。

- **AC7 —— 已实现。** `prompt_once` 从 **261 → 148 行**(代码体约 95,其余是 docstring),提取三个
  内聚 helper:`_setup_ephemeral_or_defer`(handshake 锁下的 ephemeral 设置/延迟 →
  `(client, ephemeral_created, process_was_running)`)、`_reuse_cached_client_for_prompt_once`
  (cached 复用/re-session 分支,从深度 8 嵌套扁平化为早返回)、`_teardown_ephemeral_after_prompt`
  (`finally` 拆除)。承重不变量都保住了:turn 锁无条件的 `lock.release()` 仍在 `prompt_once` 的
  `finally`;handshake 锁/turn 锁次序、fail-fast 的 `_rollback_ephemeral_on_busy` 门、"别停活赢家
  在用的 proc"守卫均原样保留(逐字搬进 helper)。绿:252 个 acp_client + headless 测试。

- **AC5 —— 已实现(输出加固),一个子项延期。** 新增 `render._sanitize_terminal_text`(剥离 C0
  含 ESC、DEL、C1 控制字符;保留 `\n`/`\t`),接入**两条**展示路径:plain 的 `render_plain` 回退
  (那条原样 `sys.stdout.write` 的载体)与 Rich 路径(agent-Markdown 累积 + 前缀行分支)。只剥 ESC
  字节即可让每个 CSI/OSC 序列失效——这是标准、稳健的做法——残留的 `[2J` 之类是惰性文本;我们
  **不**去 fragile-parse 整段序列。新增每片上限 `helpers._cap_chunk`(`_MAX_CHUNK_DISPLAY_CHARS
  = 256 KiB`)于 `agent_message_chunk` / `agent_thought_chunk`(唯二不截断返回的 kind),使被攻陷
  server 无法在 inbox + Markdown 累积器里强制多 GB 缓冲;上限远高于任何真实流式增量,故绝不触碰
  合法内容。+9 个有牙测试(`TestTerminalSanitization`/`TestChunkCap`,含 Rich 路径 OSC-设标题/BEL
  剥离)。**延期(已记录):** `process.py` 的 readline 级硬上限——多 GB 缓冲发生在 C 层
  `for raw_line in stdout` 内、任何 Python 检查看到该行之前,故真正的上限需要一个带*丢弃超大帧*
  策略的定长帧 NDJSON 读取器(行内截断会破坏一个合法的超大 JSON-RPC 帧)。那是客户端最热路径上的
  帧解析重写;鉴于子进程已 env 被剥离、展示缓冲现已被 `_cap_chunk` 限住,留作记录性后续,而不在
  "顺手"名义下仓促发布。

- **AC8 —— 部分实现(无漂移的净收益),两项有据跳过。** 已做:(a)`helpers.py`——提取模块级
  `_opt_id` 与参数化的 `_select_option(options, *, canonical_kind, kind_prefix, hints)`;三份复制
  (`_select_reject_option`、`_select_approve_option`,以及 `_select_option_by_kind` 里内联的
  `_opt_id`)都改走它。公开名 `_select_approve_option` / `_select_reject_option` /
  `_select_option_by_kind`(在 `manager/__init__.__all__` 导出、被 `test_acp_client_embedding.py`
  引用)保留为薄包装——行为保持。(b)`interactions.py`——手搭 5 次的
  `{"outcome":{"outcome":"selected","optionId":…}}` 信封现为 `_selected_outcome(option_id)`,而
  `approve`/`reject`/`reply_interaction` 相同的 resolve → 发响应 → 从 `WAITING_FOR_USER` 转移
  尾段现为 `_resolve_and_respond(...)`。**有据跳过:**(c)`models.py` 的 3× mode / 2× 重启边界
  校验是**刻意的随上下文变化的消息**——`from_dict` / `_parse_non_interactive_policy` 在配置加载时
  发出 *JSON 字段名* 错误(`'maxRecoverableRestarts'`、迁移提示),而 `__post_init__` 在 dataclass
  构造时发出 *Python 属性* 错误;这些串是用户可见 UX(且部分被测试断言),合并会降级消息而非仅去重
  ——正是"无危险漂移 → 留着"那类。(d)`client.py` 的 `send_prompt` 与 `send_prompt_nonblocking` +
  `finish_prompt` 重叠位于并发测试守护的 JSON-RPC 发送/响应关联路径上,是四者中风险最高、回报
  最低(LOW)的,按审计自己"不要为删这些而制造共享 API"的指引,留作记录。既有覆盖
  (`TestSelectRejectOption`、`test_acp_client_cli.py` 里 `result["outcome"]["optionId"]` 信封断言、
  `test_headless_runtime.py` 的 `accept_all` 自动拒绝测试)验证了这些合并是行为保持的。

- **AC5/AC8 xhigh 代码评审轮(实现后)** —— 对 AC5/AC8 diff 的第二次 workflow 评审发现
  **9 个不同缺陷,全部已修**(AC1/2/3/6/7 的 diff 另行评审为干净)。要害几处是我引入的:
  1. **清理器漏了交互展示路径**(最高严重度)。`flush_to_console` 跳过 PERMISSION/INPUT 转交
     `_handle_inline_interaction`(`cli/commands_ext/acp.py`),后者 `console.print` 服务器的
     `toolCall.title` / `rawInput` / `content` / `interaction.prompt` **未清理**——正是 AC5 针对的
     交互通道。修复:在第二个展示边界清理每个服务器可控字段(+3 个测试把 ESC/OSC 打进假 console)。
  2. **`_cap_chunk` TypeError 回归** —— `len()`/切片假设 `str`;敌意 server 发来 JSON 数字的
     `content.text` 会抛 `TypeError`(被 client 通知 try/except 吞掉 → 消息与 host 回调静默丢失),
     而改动前直接返回该值。修复:加 `isinstance(text, str)` 直通守卫。
  3. **清理器漏了 Unicode 双向覆盖控制符**(Trojan-Source / CVE-2021-42574):U+202A–202E /
     U+2066–2069 / U+200E/200F/061C 无需 ESC 字节即可重排文本。加入 `_BIDI_CONTROLS`;刻意保留
     ZWJ/ZWNJ/BOM(emoji / 阿拉伯-印度文合法)。
  4. **上限的内存声明夸大** —— `_cap_chunk` 只限住*展示*串;`InboxMessage.raw = params` 仍留全量。
     订正注释把声明缩到仅展示路径,并把真正的 payload / 进程读上限指向延期的 readline 帧上限。
  5. **上限漏了同类服务器文本** —— 权限 `toolCall.title` 与 `ask_user` `question`/`message` 无上限
     进入 `InboxMessage.text`。现改走 `_cap_chunk`。
  6. **跨分片累积无界** —— 每片上限不限住交给 RichMarkdown 的 `agent_text_parts` 拼接(每次 flush 约
     256×256 KiB)。加 `render._MAX_AGENT_RENDER_CHARS = 1 MiB` 限住聚合。
  7. **非 ASCII 省略号** —— 截断标记的 `…` 在非 UTF-8 stdout 的 plain 路径可能 `UnicodeEncodeError`。
     改为 ASCII `...`。
  8. **debug log-only 分支** —— 未清理地记录服务器 `msg.text[:200]`(DEBUG 流 handler 会回显转义)。
     现在记录前先清理。
  9. **`_select_option_by_kind` 复制了 `_select_option` 的 pass 1** —— 提取共享的 `_first_id_by_kind`。
     修复后全套绿。

**净结果:** 三个真缺陷已处理(AC1 崩溃、AC2 锁、AC3 孤立孙进程),配有"证明有牙"/行为保持的
覆盖;两个 Tier-3 重构在并发测试护航下落地(AC6 handshake 舞蹈合并、AC7 `prompt_once` 拆分);
AC5 输出加固已发布(转义清理 + 分片上限;readline 级上限作为帧解析重写延期);AC8 的无漂移合并
已落地(`_select_option`、`_selected_outcome`、`_resolve_and_respond`),两个消息-/并发-敏感项留作
记录。AC4 的"死代码"桶仍降级为一个已实现的测试正确性项(AC4′)+ 一个维护者产品决定。
