# Codex Subagent V2 与 Agentao Subagent 对照

> **⚠️ 本文仅为分析记录，暂不实施。** 第 1 节的「P1 / 观察 / 不建」是**分析结论的优先级排序**，不是工单：
> 截至 2026-08-21，两项 P1 均**未获授权动工**，任何一项开工前需维护者另行拍板。
> 引用本文时请一并引用这一行——它防止下一个人把排序当排期。

**状态：** 仅分析（分析已收敛，实施未授权）。2026-08-21 起草并经两轮评审收敛。**第 1 节是全部结论**，其余各节是它的依据。
**锚点：** codex `openai/codex@2151d3a5b7`（增量 `2230d64464..2151d3a5b7`，487 commit）；agentao `main@c06a143`。英文孪生待写。
**先前记录：** `openworker-borrow-review.zh.md` §1 —— **§2 那条 fail-open 是它首报并已完整定规的 P1，本文不是首报**。另见 `codex-goal-mechanism-review.zh.md`、`subagent-discovery-entrypoint-review.zh.md`。
**已移出：** 项目级 agent 定义的信任线不一致（与 codex 无关）→ `agent-definition-trust-line.zh.md`。
**方法：** 两侧都读源码，每条主张就地附 `file:line`；**§2 的传输选择是实测的**（附可复现脚本），不是读出来的。

---

## 1. 结论表（分析优先级，非排期）

**一句话差异：codex V2 是常驻 actor（跑完仍占并发槽位，靠 LRU 逐出），agentao 是一次性 worker（`chat()` 返回即结束）。** 其余差异几乎全由此派生。

**下表是分析结论的优先级，不代表已排期。**

| 若实施，优先级 | 内容 | 依据 |
|---|---|---|
| **P1（未实施）** | 后台子 agent 的 ASK **固定拒绝**（fail-closed）；前台不动 | §2 |
| **P1（未实施）** | `BackgroundTaskStore` 原子并发上限，满额直接拒绝 | §3 |
| **观察** | 上下文截断是否产生真实失败案例 | §4.1 |
| **观察** | 轮询是否产生可量化的 token / 延迟代价 | §4.2 |
| **不建** | 审批队列、三态审批配置、park / mailbox 子系统、`fork_turns`、residency 逐出、`AgentPath` 全局注册表、agent 间通信、dashboard | §5 |

---

## 2. 【P1，未实施】后台子 agent 的 ASK 固定拒绝

**不是本文首报。** `openworker-borrow-review.zh.md` §1（2026-07-29）已读到行号、定性为「方向选反」、给出最小修复与三类测试，至今未落地。本节只做它没做的：**用实测证明修复该落在哪一侧**——前台不动，后台固定拒绝。

### 实测（8 组：父方 transport × 前/后台）

| 父方 transport | 前台子 agent | 后台子 agent |
|---|---|---|
| `SdkTransport` **带** confirm 回调（交互式 CLI） | ASK **抵达父方回调**（实测 `False`，提示名 `[probe] run_shell_command`） | `NullTransport` → **ASK→True，父方回调根本没被调用** |
| `SdkTransport` **无** confirm 回调 | ASK→True（`sdk.py:104`） | 同上 |
| `NullTransport`（无配置 headless 嵌入） | ASK→True | 同上 |
| `NonInteractiveTransport`（`agentao run`） | **ASK→False**，`rejection` 置位 | **ASK→True，rejection 未置位** |

最后一行最能说明问题：**`agentao run` 这条以 fail-closed 立身的无人值守路径，往下一层就翻了值。**

<details><summary>复现（桩掉 <code>Agentao.chat</code>，只看子 agent 实拿到的 transport）</summary>

```python
import agentao.agent as A
from agentao.agents.tools._wrapper import AgentToolWrapper
from agentao.transport.sdk import SdkTransport          # 换 NullTransport / NonInteractiveTransport 即得其余行

cap = {}
A.Agentao.chat = lambda self, m, **kw: (
    cap.update(t=type(self.transport).__name__,
               ask=self.transport.confirm_tool("run_shell_command", "rm -rf x", {})), "done")[1]

parent = SdkTransport(confirm_tool=lambda *a: False)     # 一个会说"不"的人
w = AgentToolWrapper(
    definition={"name": "probe", "description": "d"}, all_tools={},
    llm_config_getter=lambda: {"api_key": "k", "base_url": "http://127.0.0.1:1/v1", "model": "m"},
    working_directory=__import__("pathlib").Path("."),
    confirmation_callback=lambda *a, **kw: parent.confirm_tool(*a, **kw),   # == agent_tools.py:89
    step_callback=lambda *a, **kw: None, output_callback=lambda *a, **kw: None)

for bg in (False, True):
    w._run_sync("t", suppress_output=bg); print(bg, cap)
# False {'t': 'SdkTransport',  'ask': False}   ← 前台：抵达父方
# True  {'t': 'NullTransport', 'ask': True }   ← 后台：绕过父方
```

</details>

### 由此修正两处（本文初稿与先前记录都说错了一半）

1. **前台路径不是独立的 fail-open。** 唯一的生产构造点 `tooling/agent_tools.py:89` 恒定注入 `confirmation_callback=lambda *a, **kw: agent.transport.confirm_tool(*a, **kw)`，故 `_wrapper.py:506` 的 `not self._confirmation_callback` 分支**无生产调用方**；前台子 agent 的 ASK 一律回到父方 transport。父方自身放行时子 agent 也放行，那是**继承**不是降级——同一个工具，模型在父 turn 里直接调也一样放行。
2. **只有后台是降级。** `suppress_output=True` 一律把全部 legacy 回调置空（`_wrapper.py:502-507`）→ `agent.py:790` 的 `_has_legacy` 为假 → 选中 `NullTransport`（`null.py:28` 返回 `True`）→ `runtime/tool_runner.py:214-235` 的 Phase 2 把 `ASK` 抬成 `ALLOW`。**而选择走后台的是模型自己**（`run_in_background` 是它可设的布尔参数，`_wrapper.py:231-252`）。

### 修复形态（已定案，尚未实施）

> **后台子 agent 无法安全进入交互审批**（后台线程读 stdin 会破坏终端 raw mode），**因此 ASK 固定拒绝**；权限引擎给出的显式 `ALLOW` / `DENY` 不受影响。前台不动——它本就抵达父方。

一行，与 openworker §1.3 定规一字不差，配套三类测试见其 §1.4；顺带覆盖 `:506` 那个不可达分支。**修在子 agent 侧**：`null.py:10-17` 把 auto-approve 定为无配置 headless 嵌入的既定默认，改它波及每个无回调宿主。

headless 宿主的后台子 agent 会因此**严于其父**。这是**合理的最小权限收紧**，行为变化写进 changelog 即可（初稿称其「零安全收益」，措辞不当，已更正）。

**不做**：审批队列、`background_approval: deny|auto|queue` 三态配置、**以及给 transport 加 `can_prompt` 能力位**。最后一条看着更精确，实则不可靠且更贵：

- `Transport` 是 `runtime_checkable` 的**结构化 Protocol**，docstring 明写「实现全部方法不是必须的」（`transport/base.py:9-30`），所以只能 `getattr(transport, "can_prompt", 默认值)` 地读——**默认假**则第三方 transport 静默保留 fail-open（正是要修的 bug），**默认真**则所有未适配实现的行为与固定拒绝完全一致。能力位唯一换回的东西，就是给 headless 嵌入保留自动放行——而那正是本条要去掉的。
- 要覆盖的实现**不止 4 个**：`Sdk` / `Null` / `NonInteractive` / `ACPTransport`（`acp/_transport_interaction.py:71`）/ **包装器** `ReplayTransportAdapter`（`replay/adapter.py:141`，须逐层转发）/ `AgentaoCLI` 自身（`cli/app.py:445`），外加宿主自定义实现。
- 语义本身就不成立：`SdkTransport` 带 confirm 回调只说明「有人处理决定」，不证明「能向人类提问」——`agent_tools.py:89` 注入的那个 lambda 就是个非人类回调。

### 可达性与佐证

只影响 **ASK → ALLOW**（hardline floor 与引擎 `DENY` 不经过确认回调）。需要子 agent 存在才可达：`enable_builtin_agents` 默认 `False`（`embedding/factory.py:54`），但 `.agentao/agents/*.md` **无条件扫描**（`agents/manager.py:43`）、插件 agent 也注册（`cli/subcommands.py:321`）——故「默认零配置装机」不可达，「项目定义了 agent 或装了带 agent 的插件」即可达；`bg_store` 默认创建（`embedding/factory.py:224`）。codex PR #38205 是第二个 peer 的独立佐证：委派会话强制 `never` 并**拒绝**需审批的调用。

---

## 3. 【P1，未实施】后台子 agent 没有并发上限

`_launch_background` 每次起一条 daemon 线程（`_wrapper.py:688,761`），`bg_store.register`（`bg_store.py:256`）无任何容量检查；全树查 `semaphore|max_concurrent|MAX_TASKS` 在 `agentao/agents/` 下**零命中**。**执行器那 8 个 worker 不构成上限**：它只限制**同时执行的工具调用**，不限制**存活的后台线程**——`t.start()` 后工具立即返回（`_wrapper.py:762-764`），daemon 活过那次调用（`tool_executor.py:186`）。

代价不是内存，是两件事叠加：N 条线程各自跑 LLM 调用（成本），且各自**自动批准工具**（§2 的放大器）。一轮连发 20 次 `run_in_background=true` 就是 20 条无人监督的执行流。

codex 侧是 `V2Residency` 槽位制（默认 4 减根即 3，RAII 归还，超容量 LRU 逐出；`agent/control/residency.rs:98,105-155`，`config/mod.rs:224,1507-1521`）。**「逐出到 thread-store 再 resume」那半是云端长驻会话的需要，不抄。**

最小方案：范围限单个 `BackgroundTaskStore`；在 `register()` 现有锁内**原子**统计 pending/running，达上限直接拒绝并返回明确错误；初始默认值定为 **4**（不留待实施时再议）；不建队列、逐出、跨进程配额。测试：成功/失败/取消后槽位可复用，并发 register 不突破上限。

**一处实现陷阱（已 grep 核实）：只能统计 `_owned_ids` 内的任务。** `_tasks` 会并入同一持久化文件里**其他进程**的记录（`bg_store.py:230-234`），全量计数会因别的进程有任务在飞而误拒本进程的启动。

---

## 4. 观察项

### 4.1 上下文传递有损

agentao 给子 agent 的不是历史，是最近 10 条消息压成的文本块，assistant/user 截 400 字符、tool 结果截 300 字符（`_wrapper.py:164,401-440`）；codex 是 `FullHistory` 或 `LastNTurns(n)` 的真实消息项（`fork_turns` **默认 `all`**）。

这是上下文预算取舍，不是缺陷。**不要写成「这类委派失效」**——子 agent 拿到任务文本里的路径完全可以自己 `read_file`。准确表述：父方工具输出超过 300 字符的部分对子 agent 不可见，**可能**影响依赖父级长工具输出的委派；先记录真实失败案例，再决定是否调整摘要上限。（另有一处小失真同样只记录：同时带正文和 `tool_calls` 的 assistant 消息只保留正文，工具名被 elif 链吞掉，`:415-419`。）

**近期不加 `fork_turns`**：它要新增公开参数与上下文模式、抬高 token 成本、扩大敏感数据与提示注入的传播面，还要正确保持 tool-call / tool-result 配对（严格 API 会拒绝孤儿 `role:"tool"` 消息）。

### 4.2 无 park 原语 → 只能轮询

codex 的完成消息会拨动 `InputQueueActivity::Mailbox` watch 通道，父方若正 park 在 `wait_agent` 上会被立刻唤醒（`multi_agents_v2/wait.rs:180-202`），单次最长 park 1 小时、默认 30 秒、期间零 token（`config/mod.rs:225-227`）。agentao 只有后半段：`check_background_agent` 立即返回（`_bg_tools.py:19`），想等只能反复轮询，每次一个完整 LLM 往返。

「要等 10 分钟，codex 花 1 次往返，agentao 花 N 次」**是算术，不是测量**——模型实际会不会去等、等多久，没有数据。而且阻塞等待至少要先回答：用户 steering 与取消如何打断（机制有：`tool_executor.py:306-307` 会把每轮 `CancellationToken` 注入声明了 `_cancellation_token` 的工具，但 `agentao/tools/` 下**零个**内置工具在轮询它，这会是第一个）；前台 chat 请求是否被长期占用；多个 wait 是否占满执行线程；宿主收到 `SubagentLifecycleEvent`（`host/models.py:97`）后是否本就该自己续跑。**等待策略更接近宿主职责。**

**若日后确认痛点，最小演进是给 `check_background_agent` 加一个短时、可取消的 `timeout_ms` 参数**，而不是引入邮箱或独立 park 子系统。

---

## 5. 为什么其余不建

codex V2 的 actor / 邮箱 / 路径注册表 / residency 逐出，前提是**长生命周期的云端 orchestrator + 人类可随时中途 steering**。`wait_agent` 不接 agent id、要能被 `Steered` 唤醒、agent 完成后要常驻占槽——每一条都是这个前提的直接产物。嵌入式 harness 没有这个前提：宿主自己拥有会话生命周期，`agentao.host` 已经把 `SubagentLifecycleEvent` 抛出去了，宿主想做面板就能做。

---

## 6. 平价项 —— 不要重复标记

| codex 的做法 | agentao 现状 |
|---|---|
| #39299 把角色覆盖收敛为封闭 9 字段、不含策略项（`agent/role.rs:37-48`） | 定义 frontmatter 本就没有策略字段，`tools` 只能收窄（`agents/manager.py:57-75`） |
| V2 默认只有根暴露 collab 工具；V1 用 `agent_max_depth`（默认 1） | `agent_manager = None`，硬深度 1 且不可配置（`_wrapper.py:541`）——**更严** |
| turn 快照携带 approval policy / cwd / permission profile | 实时 getter + 重建引擎并透传 `user_root`（`_wrapper.py:541-570`）——**更实时** |
| `TurnItem::CollabAgentToolCall` + `SubAgentActivityItem` 给宿主 | `AGENT_START`/`AGENT_END` + `SubagentLifecycleEvent`（`tooling/agent_tools.py`） |
| 子 agent 状态原样报给父方，由模型自己判断 | `incomplete` 分类 + `max_iterations` 捕获 + 「它没做完」footer（`_wrapper.py:588-660`）——**agentao 单方面更强，codex 无等价物** |

---

## 7. 对照速查（不驱动决策，供下次评审对齐）

| | codex V2 | agentao |
|---|---|---|
| 工具形态 | 单个 `spawn_agent`，参数选角色 | 每定义一个工具 `agent_<name>`（`_wrapper.py:224`）——角色多了压工具目录预算 |
| 身份 | `task_name` → `AgentPath`，全局注册表预留防重名 | 后台 `uuid4()[:8]`；同步路径无 id |
| 生命周期 | 完成后仍常驻占槽，仅**有资格**被 LRU 卸载（要 `Completed\|Errored\|Interrupted` + 无 active turn + 邮箱空，`residency.rs:233-239`；V1 是显式 `close_agent`，`multi_agents_spec.rs:329`） | 跑一次 `chat()` 即销毁（`_wrapper.py:513-536`），记录留在 `bg_store` |
| 结果回传 | 投进父方邮箱，`trigger_turn:false`（`agent/control.rs:565-590`）；回退路径直接 `inject_user_message_without_turn`（`:599`） | `push_notification`（`bg_store.py:241`）→ 父方下一轮由 `_inject_background_notifications` 作为 `role:"user"` 的 `<system-reminder>` 注入（`chat_loop/_runner.py:372,1068-1094`）——**同一个机制**，差别只在有无 park |
| 等待 | V2 `wait_agent` 只收 `timeout_ms`、park 在邮箱上；V1 才是点名 join 回状态表（`multi_agents/wait.rs:274-284`） | 无等待原语 |
| 打断 | `interrupt_agent`，**agent 仍存活可再接任务** | `cancel_background_agent` + `CancellationToken`（`_bg_tools.py:116`） |
| 完成信号 | 子方正常结束一个 turn | 子方**必须**调 `complete_task(result)`（`_complete.py`，控制流走异常） |
| 技能 | role 可携带 `skills` | `SkillManager(skills_dir="/nonexistent")`，一律不继承（`_wrapper.py:540`） |
| 崩溃恢复 | thread-store + rollout，可 `resume_agent` | `bg_store.recover()` 把孤儿重分类为 failed，带跨进程「仅首个 store 重分类」保护（`bg_store.py:29-35,506`） |
| agent 间通信 | `send_message` / `followup_task` 入队 | 无（`grep -rn "send_message\|mailbox\|inbox" agentao/agents/` → 零命中） |
