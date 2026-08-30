# Claude hook 契约合规 —— 一层版本化兼容层

> **⚠️ 已实施并合入** —— PR #199、`18fb628`（2026-08-30）。它要收口的偏离编目在
> `hooks-three-way-claude-codex-agentao.zh.md`（rev 5），那份文档仍是纯分析。
> **尚未发布**：它落在 0.4.21 开发周期内，不在任何已发布版本里。

**状态：** **已实施**（rev 24，2026-08-30）—— §8 的七个步骤与十个设计门全部完成。每个门结在哪里见 §0。
rev 23 的正文不变；这是同一份文档，记上了结案、勾掉了步骤。下文每一处 `file.py:line` 引用依然是对
**实施前**锚点 `main@10b5fb8` 解析的：它们是那个缺口的证据，不是收口它的那份代码的地图。

**原为：** 计划，rev 23（2026-08-29），经二十一轮维护者评审。**已授权实施。** rev 23 是门槛结案版：
维护者做出了本文保留的四个决定，其余由对真实 `claude` 二进制的一次探测定案 —— §0 逐条记录每项结案及其
改动。随之动了六格表格；除此之外，这套设计与评审在 rev 22 放行的那份一致。
**来源：** 维护者对九条偏离的处置意见，在此逐条重述并补上每个选择在代码里实际会带出的后果。凡本文与
该方向有分歧之处均就地写明。
**锚点：** agentao `main@10b5fb8`；Claude Code hooks 参考**抓取于 2026-08-28 19:29**，来源
`code.claude.com/docs/en/hooks.md`（2026-08-26 的锚点是 `docs.claude.com/en/docs/claude-code/hooks`，现在
301 跳到前者；`.md` 兄弟路径直接给出页面源文本，本文引文均取自它）—— **295,595 字节，sha256
`c984f918cf93f75bd84bc7ea4c63006ca0624f3ddde1431d625af4933def5179`**，页面记录 56 个事件。同一次抓取时
changelog 头部为 **2.1.251**（`code.claude.com/docs/en/changelog.md`），而它新增的内容**并不在**抓到的页
面里 —— 见 §3，这正是标签改成 profile、而不是产品版本号的原因。OpenAI codex hooks 参考
`<https://developers.openai.com/codex/hooks>` 抓取于 2026-08-26。
**实测行为：** `docs/reference/hooks-probe-2.1.251.zh.md` —— 真实的 `claude` 2.1.251 在那些参考文档
无法了结的行上到底做了什么（§0）。
**孪生：** `hooks-claude-contract-conformance-plan.md`。
**相关：** `hooks-three-way-claude-codex-agentao.zh.md`（证据来源；九条即其 §5.1–§5.10）。

### 修订历史

每轮一行。发现记在这里，它产出的设计在所指的那一节。这张表存在有两个理由。**有五轮是翻案** —— rev 3、6、10、11、14 各自推翻了更早一轮做过的决定，而一个决定
被做出、推翻、再做出，缺了推动它的那一轮就显得任意。以及**有九轮发现的是上一版自己的新内容违反了本文早就
有的某条规则** —— rev 9，以及 rev 13 到 20 连续未断。每一次被违反的是哪条规则都能点名，这才让这个数可核、
而不是一种情绪：rev 13–17 见下面的表；**rev 18** 发现 rev 17 那个闩构造违反了 §12 自己长期写着的规则 ——
一个不用真正触发被测物就能通过的测试不是测试；**rev 19** 发现 rev 18 弱化了测试却把承诺留在原地，而这正是
§2.5 早就在 G6 上用过的兜底模式的反面；**rev 20** 发现 rev 19 那份接缝清单里的一个选项，不满足 rev 19
*自己刚写下*的成对规则。（rev 3 与 rev 4 是更宽松的近亲，不计入：rev 2 那个 `contract` 门控推翻的是 rev 2
自己的*论证*、而不是一条写明的规则；rev 4 的两条是 rev 3 的回归。）这是本计划最容易犯的失效模式，也是
§5.1 现在写明「一个字段的状态一变，就要重跑所有以旧状态为键的检查表」的原因。**这个连续段止于 20。**
产出 rev 21 的那一轮抓到的是一个过期的统计、而不是一条被违反的规则 —— rev 20 带进来的东西没有和本文
既有的任何一处冲突 —— 再下一轮则是零发现。

| rev | 发现 | 头条 | 落在 |
|---|---|---|---|
| 2 | 4 P1、2 P2 | rev 1 只规划了 **wire** 契约、从未看**配置**契约：一份 Claude 形状的 `hooks.json` 解析出**零条规则**。这是第 0 条偏离，在对照文档那九条的上游 | §2 |
| 3 | 5 P1 | 自伤：rev 2 把官方形状的解析门控在一个「拷贝来的 Claude 文件根本没有」的 `contract` 键上 —— 仍然零条规则。改为**形状自动识别** | §2.2 |
| 4 | 3 P1、2 P2 | 其中两条是 rev 3 自己的回归：新 `resolve()` 只返回一个裁定、**把所有正交通道都丢了**；以及把 exit 2 当成布尔量，而参考文档给它三种结果 | §4.2 |
| 5 | 3 P1、3 P2 | `resolve()` 在看退出码**之前**就把纯文本 stdout 当成模型 context，并且不查能力表就应用 `continue:false` | §4.2 |
| 6 | 5 P1、2 P2 | stdout 状态机把*解析*失败和 *schema* 失败并成了一种。以及 rev 5 的「第十一条偏离」**撤回** —— 参考文档同一页自相矛盾（`sh -c` vs `shell` 字段默认 `"bash"`） | §4.2、§2.4 |
| 7 | 4 P1、3 P2 | 最小的那条 P2 牵出最大的发现：重抓后发现契约是**按版本分岔**的，而 rev 6 的头号修正实现的正是 v2.1.248 **之前**那一支 | §3、§4.2 |
| 8 | 4 P1、2 P2 | 「声称一个面却不列举它」，同一个错误犯了两次：§1 承诺全契约，而**九个输出字段**无处安放；`@2.1.248` 这个标签是从一个下界推出来的 | §5.1、§3 |
| 9 | 5 P1、1 P2 | 其中三条是 rev 8 的新内容违反 rev 8 的新规则 —— `reject` 是配置期动词，输出字段只能是 `accept` 或 `ignore` | §1、§5.1 |
| 10 | 6 P1、1 P2 | 我判错了一次「规范已定案」：参考文档在**全局**决策表里点名了 `PostToolUseFailure`，逐事件小节却没提。方法规则补上限定词 —— **沉默不是覆盖** | §5.1 |
| 11 | 3 P1、1 P2 | **一处编造的引文。** §4.4 关于 `updatedInput` 那句不在快照里（`grep -c` = 0），而它支撑的那个行为（保留原输入）本身不安全 | §4.4 |
| 12 | 2 P1、3 P2 | 缝在每张单看都完整的表之间：「通用」字段并不通用；rev 2 的 `hookSpecificOutput.agentao` 命名空间从来没有任何地方实现过 | §5.1、§3.3 |
| 13 | 3 P1、2 P2 | rev 12 的新内容对上本文早就有的检查表：一个字段从散文被提升成表格行，而「每个 `accept` 都欠三样东西」从没回头审过它 | §5.2.2 |
| 14 | 2 P1、1 P2 | rev 13 自己新铺的那条路由，建在一张它从没读过的**第三张**全局表上：`hooks.md:1009` 把 `SessionStart` 标为 *"Context only … No blocking or decision control"*。而它确实该留的那条 `PostToolUse` 停止，停在 worker 里、离能接住它的地方隔着三层调用栈 | §5.1、§5.2.2 |
| 15 | 2 P1、2 P2 | rev 14 把两行**逆着**参考文档定了下来，却没留回头路：探测能把任一行翻过来，而文档只写了「把断言反过来」—— 于是有了 **翻案清单**（G7）。它的窄读法那一支还把两条轴混了（给一个 `discarded` 字段出诊断），并且用一个被 8-worker 执行器否掉的事实去论证批次策略 | §5.1、G7、§5.4 |
| 16 | 1 P1、1 P2 | rev 15 的翻案清单**预填了它自己那个探针存在着要取得的答案**：把 `PostToolUse` 的「反馈而非停止」横移到了 `PostToolUseFailure` 上，而两节之外的 §11 第 9 问又说认了它会**停掉**一轮。它们共享的那一行钉的是 **wire 形状**、不是效果 —— 该行九个事件里的四个，效果彼此互不相容 | §5.1、G7 |
| 17 | 1 P1、1 P2 | rev 16 那条「结束这一轮」的分支进不了控制格：第 1 序写着**只有** `continue:false`、G9 照抄一遍、而 §12 又把该分支绑死在 rank 2 —— 三处互相冲突。**「只有」是清点、不是规则**；第 1 序收的是*结束处理*这个类别，一行存疑的靠归一化成 `Stop(reason)` 进场。它那条排队兄弟测试也是有竞争的 | §5.4、G9、§12 |
| 18 | 1 P2 | 排队兄弟测试仍然写不出来：闩住七个 worker 证明的是 *hook 执行期间*的占用，而停止要等 dispatcher 返回才可观察、`_execute_one` 下一行就释放了 worker。**那个位置没有任何接缝**，所以 G2 要负责加一个 —— 否则断言降级到批次结果层，并写明它可能空过 | §12、G2 |
| 19 | 1 P2 | rev 18 弱化了**测试**、却把**承诺**留在原地：一条接缝可选的排队兄弟规则，实现可以违反它并照样通过全部验收。G2 现在选的是一**对** —— 保证与接缝同进退，或者两者皆无、由 §1 记下「停止那一刻」未定义。隔壁 G6 早就用过这个模式（弱化承诺、不是弱化测试） | §12、G2 |
| 20 | 1 P2 | rev 19 自己那份接缝清单又把洞带了回来：它写的是「让 executor**／cap** 可注入」，而一个光秃秃的可配置 `max_workers` 只限并发量，对「停止变为可观察」与「队尾出队」之间那一刻给不了测试任何控制权。cap 可以顺带有，但它永远不是接缝 | G2 |
| 21 | 1 P3 | 自伤轮次的统计自己停止了自我计数：仍写着「六轮、自 rev 13 起未断」，而 rev 18、19、20 三条记的恰恰都是这个模式。现在是九轮，并逐轮点名被违反的是哪条规则，让这个数可核 —— **一个关于失效模式的统计，并不豁免于该失效模式** | 本节 |
| 23 | —— | **门槛结案，实施获授权。** 不是一轮评审：维护者定了 G2/G6（取弱化支）、G8（不做执行前校验器）与 G7 的存证问题，另由对 `claude` 2.1.251 的一次探测了结两行存疑、G5 的成文歧义与 G8 的翻案项。六格表格随之改动；两行存疑现在都是**实测**的 —— 一行证实窄读法，一行推翻它 | §0、§2.4、§5.1、§5.2、§5.4、§7 |
| 24 | —— | **已实施。** §8 的七个步骤分九个提交落地，十个门按 §0 记录的方式收口 —— 四个靠维护者决定、五个靠探测真实的 `claude` 2.1.251、一个取计划自己的提案。其中三次探测**修正了计划**：matcher 是锚定而非非锚定（§2.3）；`PostToolUseFailure` 认 `decision`（§5.1）；`SessionStart` / `SessionEnd` 的 matcher 比的是 `source` / `reason` | §8 全部 |
| 22 | 零 | **通过。** 没有 P1、P2、P3 —— 二十一轮里第一次零发现。rev 22 是记账：状态行、本行，以及「自伤连续段止于 20」这一句。§1–§12 一个字没动，所以过了评审的那份就是盘上这份 | 本节、§1 |

这些轮次里长出两条过程规则，本文遵守：**每个补丁跑完都要重新 grep 确认**（rev 4 的英文补丁曾整块静默没
执行，孪生因此在五处漂移），以及**每一条引文都从存档快照复制粘贴、并回头 `grep -F` 复验**（rev 11）。

---

## 0. 门槛结案

这里没有任何新设计。每一行都是本文有意留开的一个门槛，以及了结它的那个决定 —— 要么是维护者的裁决，要么
是一次测量。**凡结案改动了某张表的，以表为准、本节只是索引**；§12 的测试跟着表走。

测量来自 `docs/reference/hooks-probe-2.1.251.zh.md`：真实的 `claude` 2.1.251，以 headless 方式跑在一次性
项目目录里，每个目录自带 `.claude/settings.json`。那份文档载有方法、原始观察，以及每条结论**不能**证明
什么。它还记下了本次探测在得出真结果之前先得出的两个假结果 —— 两者都源于对照组自身没有做可达性检查。

| 门槛 | 由谁了结 | 结果 | 改动 |
|---|---|---|---|
| **G2** | 维护者 | **取第 (ii) 支：放弃排队 sibling 的保证。** 不建测试接缝。§1 记下「停止那一刻队尾是否执行」未定义；§12 的那条测试缩到两种情况下都成立的不变式 —— 每个 plan 仍产出结果与 `role:"tool"` 消息 | §1、§12、G2 |
| **G6** | 维护者 | **取兜底支：「所有匹配的 handler 都被提交」。** 不是「都启动」。不做逐 dispatch 准入；在 `SessionEnd` 的共享预算下排队的那个可能永远不跑 —— 这一点写明，而不是靠工程绕开。声明顺序平手规则不受影响 | §2.5、§1、G6 |
| **G8**（校验器） | 维护者 | **不做执行前输入校验。** 不提 `jsonschema`，不加 `Tool.preflight()`。§4.4 的第 2 步连同它的两条测试一并删除，§1 写明被缩小的承诺：agentao 不在执行前按 schema 拒绝工具输入，所以上游那条「输入不合法则不触发 hook」在这里没有对应物 | §1、§4.4、§12 |
| **G7**（存证） | 维护者 | **只留 provenance 表。** 那 295 KB 的参考页面不入库；评审者拿到的是 §3 那张表加这份探测文档。§11 q6 据此结案，并重述代价：引文靠 `hooks.md:<行号>` 可定位，但无法逐字节复取 | §3、§11 |
| **G5**（shell） | **探测** | **agentao 的 `/bin/sh` 基线是合规的**，且 `shell` 按「忽略并出诊断」处理、而不是拒绝。2.1.251 用 `sh` 执行命令 hook（`$0` = `/bin/sh`、`posix on`），也不兑现显式的 `"shell": "bash"`。参考文档的自相矛盾由实测了结；第 10 条偏离降为 P3，其前提被撤回。**这个门另外两半在实施时按 §9 自己的条件关掉了**：`_paths.py` 替换全部三个占位符、把 `${CLAUDE_PLUGIN_DATA}` 落在 `~/.agentao/plugin-data/<plugin>`，dispatcher 也在 `shell=True` 那支旁边加了 `args` 的 exec 形式分支 | §2.4、§7、§9 |
| **G7**（`SessionStart`） | **探测** | **`continue:false` 被丢弃 —— 窄读法得到证实。** hook 跑了、会话开了、turn 跑完了，`stopReason` 哪儿都没出现。翻案清单里「若它认这个停止」那一支**不触发**，§12 那条非停止测试现在钉的是一次测量、而不是一种读法 | §5.1、§12 |
| **G7**（`PostToolUseFailure`） | **探测** | **`decision:"block"` 被认 —— 窄读法被推翻**，且探针四问全部有答案：reason 以独立一行到达**模型**，其前**保留原始错误**，且 **turn 继续**。所以效果是反馈并继续：§5.4 那条有条件的第 2 序行变为无条件，第 1 序不受影响。一次对照运行证明起作用的是被识别的字段、不是原始 stdout —— 一个无关键到达模型 0 次 | §5.1、§5.2、§5.4、§12 |
| **G8**（非法改写） | **探测** | **计划的选择正是上游的做法。** 不符合工具 schema 的 `updatedInput` 会以 `tool_use_error` 被拒，**原输入从不执行**。它作为合规落地，而不是作为「偏离安全」的书面声明。注意 agentao 学不来的那一半：校验器既已放弃，agentao 无从*察觉*这种不匹配，于是改写后的调用会到达工具并在那里失败。真正要紧的结果相同 —— 原输入从不执行 —— 差别在错误呈现面，§1 记下它 | §4.4、§1 |
| **G7**（输入矩阵） | **探测**，部分 | 捕获了六份真实 stdin payload。它们**确认**了 §5.3 的形状 —— `permission_mode` 在四个事件上有、在 `SessionStart` / `SessionEnd` 上没有，`prompt_id` 在首次输入前缺席，`agent_id` / `agent_type` 处处缺席，`tool_response` 是结构化对象 —— 而把那些*决定*留着：`transcript_path` 由 agentao 从哪里取、`permission_mode` 怎么映射。两条是新事实：上游把 `background_tasks: []` / `session_crons: []` 发成「存在且为空」，以及 `permission_mode` 在同一会话内取值不同（`UserPromptSubmit` 上是 `auto`、工具事件上是 `default`）—— 记为观察，不作规则 | §5.3 |
| **G4** | 实施时按计划的提案取定 | **Tier 1 = 每次调用每条流 8 MiB**，在共享 runner 上是 opt-in，所以其他调用方的失败模式一点不变；超限即杀进程树、该 hook 失败 —— 因为在 JSON 中途被切断的输出没有任何决定可贡献。**Tier 2 = 每个通道 10,000 字符** —— 上游自己的数字，按字符而不是 token，这样这条界限不随所配模型而变。溢出落 `.agentao/hook-outputs/`，文件 `0600`，字节落盘前先脱敏，按时龄（7 天）与数量（200）清理。落盘失败会**上报** —— 它抄的那个 tool-output sink 并不上报 | §6、第 1 步 |
| **G10** | 实施时按计划的提案取定 | **会话级、加锁、以内容派生的 rule key 为键** —— 绝不用 `id(rule)`，它每次 reload 都变、会把一切静默重播一遍。陷阱在 dispatcher 作用域：它每次分派都新建 —— **九**处构造点、分布在六个文件里，其中两处**在池 worker 内**（早先几版写「六处」，那是在数文件）—— 所以挂在它身上的状态既去不了重、还会边去重边竞争。插件 reload 与 `/clear` 时调 `clear_session()`，这样改好的 hook 会重新出声、没改的继续闭嘴 | §4.2、第 2 步 |
| **G3** | **探测** | **`*` 是通配符；其余一切都是锚定全匹配。** 七个探测点与 `re.fullmatch` 完全一致，其中两个推翻了本计划一直带着的**非锚定**读法：`ead` 匹配不上 `Read`，`Rea\|Wri` 也不行。所以修法是复用 agentao 已有的 `_regex_match_full` 外加 `*` 特判，而不是新写三路求值器 —— 而 §2.3 的头条依然成立，因为 `toolName` 走的是 `_glob_match`。**一次追加运行（探测 §G3b）补上了通配符的第二种拼法**：`""` 同样触发，而 `re.fullmatch("", …)` 不匹配，所以把通配符写成它的配置会不带告警地解析、然后永不触发 | §2.3、第 3 步 |
| **G7**（输入侧） | 实施时按计划的规则取定 | **`transcript_path` 发显式 `null`** —— agentao 没有持续写入的 transcript，而一个内容落后于会话的路径比一个 hook 能判断的 null 更糟；用 `null` 而非省略，是因为参考文档把它标为八个事件全required，省略会让取值直接抛异常。**`prompt_id` 省略** —— 逐 turn 的 id 不是 prompt id，挪用它等于编造一种不成立的关联。**`permission_mode` 能映射就映射、不能就省略** —— `plan`→`plan`、`full-access`→`bypassPermissions`；`workspace-write` 不是 `acceptEdits`、`read-only` 没有对应物，所以字段缺席，而不是把 agentao 自己的词表发出去。**`tool_response` 保持字符串**，作为写明的类型分歧。三个私有字段在 profile 模式**去掉**、v1 保留 | §5.3、第 3 步 |
| **G1**（会话事件） | 实施时按计划的提案取定 | **一个结果类型，加上每个 surface 各自的路由。** `LifecycleHookResult` 把 `user_notices` / `model_contexts` / `stop_reason` 从那四个「只返回 attachment」的生命周期分派里带出来。交互式：CLI 消费它过去在裸 `except: pass` 里丢掉的那个返回值。Headless：`SessionEnd` 现在在 `_emit` **之前**分派，通知搭 `RunResult.warnings`（本来就会序列化）—— 旧顺序下 headless 用户根本没有路径。路由里 tool worker 那一半（`PostToolUse*` 的停止）属第 4b 步 | §5.2、§5.2.1、第 4 步 |
| **G2**（停止路由） | 计划的决定 + 第 (ii) 支 | **裁定搭 `ToolExecutionResult` 回家**，在 `ToolRunner` 上按 **plan 顺序**仲裁，并经原有的 `_resolve_stop_hook` 路径结束这一轮（新增 `hook_stop` 这个 incomplete 取值）—— 所以 `agentao run` 不需要为它单开退出码。以 `runner.last_hook_stop` 暴露、而不是加第三个元组元素：`execute` 的二元组有一批与 hook 无关的测试调用者，而 `Agentao.last_turn` 是本仓库现成的先例。两处接缝都按**字符串**读、绝不按真值 —— `MagicMock` runner 对任何属性都有应答，在这里按真值判断会让桩去结束 turn。反馈（`additionalContext`、exit-2 stderr）以 `<system-reminder>` 拼在**被保留的**结果旁边，正是探测 §C 测到的形状 | §5.2.2、第 4 步 |
| **G1**（传输面） | 取计划里更省的那个选项 | **扩展 `PLUGIN_HOOK_FIRED` 载荷**，不新开事件类型：字段是 `user_notices`，要渲染 hook 通知的宿主从这里读。两个一方 surface **不依赖**它 —— 它们直接路由（§5.2.1）—— 这正是省事那个选项站得住的原因 | §5.2.1、第 5 步 |
| **G8**（生命周期） | 计划的顺序，去掉校验器 | **前半段落地**：引擎已经 DENY 的调用在 profile 下仍然触发 hook —— 观察与权限是两回事，裁定保持 DENY。`agentao-v1` 保留跳过。**后半段是重入**：`updatedInput` 替换参数，按**将要真正执行的内容重判**，两个裁定取更严的那个 —— 所以 hook 的 `allow` 抬不动重算出来的 DENY，Phase 2 确认框展示的也是改写后的输入。`defer` 降级为 `deny` 并点名该取值；exit 2 拒绝该调用；`continue:false` 结束的是**这一轮**、不是这次调用；`additionalContext` 拼在结果旁边而不是写日志。按维护者决定不做校验步骤（§1） | §4.4、第 6 步 |
| **G9** | 计划的设计，一处偏离 | **按契约分组、运行、合并一次。** 四个带决策的分派全部分组；v1 的短路**只**结束 v1 那一组 —— 这才是要紧的性质，因为它的副作用正是「所有 handler 都要跑」这条规则存在的理由。合并与分组无关，走该事件自己的格，理由平手在**获胜类别之内**按声明顺序、绝不按分组顺序。**与计划正文有一处偏离**：两组是先后运行而不是并发，因为 G6 取了它写明的兜底支、没有 hook 池可用。先后运行只会*延迟*profile 组，压不掉它 | §9 |

---

## 1. 采纳的产品承诺

替换掉 `agentao/plugins/hooks/_alias.py:5` 里那句 —— *"a hook script written against Claude Code can
run under Agentao without modification"*：

> **Agentao 实现 Claude Code hook 契约的一个*声明式 profile*。事件更少、字段被逐条列举；**在 profile
> 之内**，每个事件都遵守文档化契约 —— 在配置上，也在 wire 上。profile 之外的一切都被列出来，而不是被
> 静默丢掉。**

每一句都是承重的。**「在配置上，也在 wire 上」**：少了它，这个承诺在最要紧的那个方向上不可证伪 —— 一份
配置解析不出来的 hook 根本走不到 wire 契约。**「字段被逐条列举」**：「每个事件都遵守文档化契约」是关于那
些事件**全部**契约的断言，而拿参考文档对 agentao 的八个事件清扫一遍，就有九个输出字段是这套设计表达不了
的（§5.1）—— 这么大的承诺不会因为发现第九个字段而被收窄，它必须被一份清单替换掉。

所以 profile 就是承诺，它有三部分，各有自己的表：

| 部分 | 表 | 声明了什么 |
|---|---|---|
| 事件 | §5.1 | 那八个，以及另外 48 个不在其中 |
| 执行上下文 | 本节 | **仅主线程。** agentao 的子 agent 里不会触发任何 hook —— 子 agent 不带 plugins 构造（`agents/tools/_wrapper.py:513`），`_plugin_hook_rules` 默认 `[]`（`agent.py:532`）。第 18 条偏离（§7）；这也是 `agent_id` / `agent_type` 是 **forbidden** 而不是条件字段的原因（§5.3） |
| handler | §2.4 | 只有 `type: command`；`prompt` / `http` / `agent` / `mcp_tool` 是 **profile 排除项**，带告警拒绝 |
| 字段 | §5.1（输出）、§5.3（输入） | 每个**输出**字段标 **accept / ignore** —— 而一个被 accept 的字段还带一个逐事件的**投递**取值：**honored / discarded**（§5.1 那张矩阵）。每个**输入**字段标 **required / conditional / forbidden** |
| 字段的**取值** | §5.1 | 当一个字段的枚举比 agentao 实现的宽时，**取值**自带处置：**accept / ignore / 降级为 X**。绝不用「reject」—— 那是下面第三条规则 |

有三条规则让 profile 是诚实的，而不是一种把靶子缩小的手法：

- **被排除的字段是被忽略，不是错误。** 一个发出了 agentao 没实现的合法字段的 hook，它**已实现**的那些字
  段仍然必须被认。坑在于「把不认识的键当成 schema 失败」的解析器：输出于是变成 `schema_invalid`，对完全
  合法的 hook 输出弹出用户可见的 `hook error`，同时把同一个对象里 agentao 认识的字段一起丢掉。所以 schema
  校验只针对**已声明字段的取值**（§4.2），不认识的键一律忽略、并给出一次点名它的诊断。
- **`reject` 是配置期的动词，对输出字段没有意义。** handler 的 `type`、`shell` 的取值、`async` 开关：这些
  在解析期就到手，拒绝那条**规则**是自洽的，因为什么都还没跑。而一个字段出现在 hook 的 stdout 里时，进程
  已经退出了，没有「规则」可拒；拒绝**结果**就会丢掉同一个 JSON 对象里所有兄弟字段 —— 而那恰恰是第一条规
  则禁止的。「把 `watchPaths` 在解析期拒掉」是最容易写出的形式，而它两半都不可能成立：配置解析器看不到
  stdout 字段，运行时也没法只丢一个字段而不丢整个对象。所以 §5.1 的输出列只有两个取值，`reject` 留在
  §2.4 的配置列里。
  **这条规则管到取值，不只是字段。** 一个 agentao 兑现不了的**取值**，处境和一个兑现不了的字段完全一样：
  拒绝整个对象会把作者其余字段一起丢掉。所以取值只能是 `accept`、`ignore`、或**降级到一个点名的替代值**
  —— 而降级必须说明降到哪、为什么，因为把一种权限结论悄悄换成另一种，是这三者里最糟的结果。
- **不存在静默排除。** agentao 忽略的字段都要出现在 §5.1 里并写明理由 —— 就像
  `SUPPORTED_HOOK_TYPES_BY_EVENT` 今天已经把「被丢弃的规则」暴露成解析器警告那样（`models.py:217`）。

**profile-1 明确不承诺的三件事**，由 §0 的门槛结案带来，而不是日后被发现。它们列在这里，因为另一种做法
就是 §1 第三条规则禁止的静默丢弃；每条都点名产生它的那个决定：

- **hook 停掉一轮之后，排队中的兄弟工具调用是否执行，未定义**（G2）。agentao 承诺的是*批次结果* ——
  每个 plan 都产出结果与 `role:"tool"` 消息 —— 对「停止变为可观察」与「队尾出队」之间那一刻不作承诺。
- **所有匹配的 handler 都被*提交*，但不保证都启动**（G6）。在 `SessionEnd` 的共享 1.5 秒预算下，排队的那个
  可能永远不跑。这比参考文档的并行条款弱，比今天的串行短路强。
- **agentao 不在执行前按 schema 校验工具输入**（G8），所以参考文档那条「输入不合法则不触发 hook」在这里
  没有对应物 —— 根本不存在可供它描述的那次拒绝。可见后果在改写路径上：上游会用 `tool_use_error` 拒绝一个
  schema 不合法的 `updatedInput`，而 agentao 把它交给工具、由工具按自己的方式失败。两种情况下原输入都不会
  执行；不同的是错误呈现面。

这把事件数差距（对照文档 §0：31 / 11 / 8）正式划出范围，把配置形状正式划**进**范围，并把字段差距**列举
出来** —— 而不是靠一句话糊过去、或者一轮评审发现一个。

---

## 2. 配置契约

Claude Code 的 `hooks.json` 嵌套四层：**event → matcher group → `hooks[]` → handler**，而且 matcher
是**字符串**。agentao 的解析器直接从事件数组里读 handler（`_parser.py:102`，`entry.get("type", "")`），
并且对非对象 matcher 直接拒绝（`_parser.py:152-164`）。

用参考文档自己的形状实测：

```python
from agentao.plugins.hooks import ClaudeHooksParser
P = ClaudeHooksParser()
def show(label, raw):
    rules, warns = P.parse_dict(raw, plugin_name="p")
    print(f"{label:22} rules={len(rules)}")
    for w in warns:
        print(f"{'':22} warn: {w.message}")

show("official shape", {"hooks": {"PreToolUse": [
    {"matcher": "Bash",
     "hooks": [{"type": "command", "command": "jq -r '.tool_input.command'"}]}]}})
show("agentao shape", {"hooks": {"PreToolUse": [
    {"type": "command", "command": "x", "matcher": {"toolName": "Bash"}}]}})
show("string matcher only", {"hooks": {"PreToolUse": [
    {"type": "command", "command": "x", "matcher": "Bash"}]}})
```

```
official shape         rules=0
                       warn: Unknown hook type '' under 'PreToolUse' — skipped
agentao shape          rules=1
string matcher only    rules=0
                       warn: Hook rule under 'PreToolUse' has non-object matcher of type str; matcher must be an object like {"trigger": "manual|auto"} — rule skipped.
```

matcher group 被当成 handler 读，它缺失的 `type` 是 `""`，规则被丢弃。这是**第十条偏离**，位于九条的
上游：对照文档量的是 hook 在 stdin 收到什么、能在 stdout 打什么，从未问过 hook 到底有没有被注册上。

### 2.1 决定：解析官方形状

评审给出的另一条路 —— 把承诺缩窄为「经 agentao 自有配置注册之后的 handler wire 契约」—— 诚实，但会把
§1 掏空。用户拷贝一个 Claude Code hook，拷的就是那段 `hooks.json`；如果恰恰这一样东西拷不过来，它背后
的 wire 合规就没剩多少价值。

所以：解析器接受 **event → matcher group → `hooks[]` → handler**、matcher 为**字符串**，同时也继续接受
今天的扁平形状。嵌套只多一层。

### 2.2 哪种形状是哪种：靠识别，不靠声明

**不要把官方形状的解析门控在 `contract` 键上。** 那是最自然的设计，也恰好废掉了整件事的目的：一份从
Claude Code 配置里拷出来的文件**没有 `contract` 键** —— 它是 Claude 的文件，不是 agentao 的 —— 门控在它
上面，拷贝来的文件仍然解析出零条规则，而那正是 §2 要收口的缺陷。

两种形状在每个条目上是互斥的，所以去识别它们：

| 条目含有 | 形状 | `contract` 缺省时取 |
|---|---|---|
| `hooks`（列表）、无 `type` | 官方 matcher group | agentao 当前所带的最新 `claude-code@profile-N` |
| `type`、无 `hooks` | agentao 扁平 handler | `agentao-v1` |
| **两者都有** | 歧义 | **禁用该文件** |
| **两者都没有** | 未定 | 不投票；按该文件的契约解析，由那套契约**逐规则**报告 |

**这里的每一种*形状*失败都是文件级的。** 歧义条目、混用两种形状的文件、以及与显式 `contract` 冲突的形状，
都**整份禁用**并告警。一份被静默解析了一半的文件比一份被拒绝的更糟 —— 半份 hook 配置不是配置，而逐条目
拒绝恰恰就是制造出半份配置的那条路。

**但「两个键都没有」不是形状失败**，把它当成形状失败，破坏掉的承诺比守住的更重。一个既没有 `type` 也没有
`hooks` 的条目什么主张都没提；它是一个**畸形 handler**，而被 §3 冻结的 `agentao-v1` 一直是逐规则报告它
（"Unknown hook type ''"）、其兄弟照常工作。把两者并成一类，会让一个打错字的条目停掉同一份 v1 文件里其余
所有 hook。所以上面那张表由三个取值变成四个，规则也比「有歧义即致命」更窄：**只有自相矛盾才致命。**

**显式**的 `contract` 仍然优先，且与之不符的形状是拒绝、不是强行迁就。而两个失败方向并不对称：

- `contract` **缺省** → 识别。这就是拷贝文件那个场景，它必须能工作。
- **显式但未知**（`claude-code@profile-99`，或者写错了）→ **禁用该文件**、告警、不从它加载任何规则。
  「回退到 `agentao-v1`」在这里是错的：作者点名了一套 agentao 没有的语义，而拿*另一套*语义去
  跑他的 hook 是一次静默的误解。回退到冻结行为，是键**缺省**时的正确答案，不是键**写错**时的。

### 2.3 字符串 matcher 不是 dict matcher 换个写法

最省事的实现 —— 把 `"Bash"` 翻译成 `{"toolName": "Bash"}` 再复用现有 matcher —— 是错的，而且错得很安静。

- agentao 对 `toolName` 走 glob（`_matchers.py:15`）：`*` 匹配一切，否则**精确相等**。
- agentao 对 `trigger` 走锚定的 fullmatch 正则（`_matchers.py:30`）。
- Claude 的 matcher 是一个字符串，而它的求值是**实测**出来的、不是推断的：`*` **与 `""`** 都是通配符，
  其余一切都是**锚定全匹配**（`docs/reference/hooks-probe-2.1.251.zh.md` §G3 与 §G3b —— 分两次运行，
  因为空字符串不在头七个里）。

这次测量修正了本节。早先的版本依据 codex 的实现与参考文档的措辞，说上游用的是**非锚定**正则；七个探测点
给出了相反结论，其中两个是决定性的：`ead` **匹配不上** `Read`，`Rea|Wri` 也匹配不上 —— 这两个非锚定搜索
都会触发。七个点与 `re.fullmatch` 完全一致。

头条在修正之后依然成立，值得把两件事分开：**字符串 matcher 仍然不是「换个写法的 dict matcher」**，因为
agentao 把 `toolName` 送进的是 `_glob_match`、不是它那条锚定正则路径。`"Edit|Write"` 在那里是一个不含 `*`
的字面串、按相等比较，什么都匹配不到。一个翻译层会把规则注册进去、然后永远不触发，这比拒绝它更糟。

变的是**代价**：`claude-code` 模式不需要新写一个三路求值器，它需要的是 agentao 已经有的那个锚定全匹配
（`_regex_match_full`）外加对 `*` 与 `""` 的特判 —— `*` 不是合法正则、不能直接透传，而 `""` 在
`fullmatch` 下什么都匹配不到、上游却当「全匹配」。**G3** 据此结案（§0）。

### 2.4 handler 字段矩阵

嵌套、matcher 和 `${CLAUDE_PLUGIN_ROOT}` 并不等于「配置契约」：参考文档定义了五个通用 handler 字段、外加
五个 command hook 专属字段。要么下面这张矩阵就是承诺，要么承诺缩窄成「所列子集」。**这张矩阵就是承诺。**

| 字段 | 参考文档 | agentao 今天 | `claude-code` 模式 |
|---|---|---|---|
| `type` | 5 种 | `command`、`prompt`；`http`/`agent` 解析时拒绝 | **只接受** `command`；`prompt`（见下）、`http`、`agent`、`mcp_tool` 一律**拒绝**并告警 |
| `matcher` | 字符串、三路 | dict、两个键 | **接受**字符串（§2.3） |
| `timeout` | 按**类型**：`command` / `http` / `mcp_tool` 为 600、`prompt` 为 30、`agent` 为 60；`UserPromptSubmit` 把 command 的默认值降到 **30**；`SessionEnd` 的 handler 共享 **1.5 秒**预算，可被 settings 文件里更长的逐 hook `timeout` 抬高 —— 但 *"Timeouts set on plugin-provided hooks don't raise the budget"* | **一律 60**（`_parser.py:141`） | **接受**，并采用参考文档的逐事件默认值。agentao 的 hook 全部来自插件，所以那条 `SessionEnd` 预算是它**无法**从配置里抬起来的 —— 这正是 §2.5 的「全都启动」保证恰恰在这个事件上最吃紧的原因 |
| `command` | `args` 缺省时走 shell 形式 | 恒 `shell=True`（`_dispatcher.py:353`） | 接受（不变） |
| `args` | **exec 形式** —— 无 shell，每个元素即一个参数 | **`ParsedHookRule` 里根本没有**（`models.py:237`） | **接受。** 不是可选项：参考文档要求作者**只要**用到路径占位符就设 `args`，所以少了它，§7.1 只是半个特性 |
| `shell` | `bash` \| `powershell` | 无 —— `shell=True` 意味着 `/bin/sh`（`_dispatcher.py:353`） | **忽略**该字段并出一条诊断。**实测**：Claude Code 2.1.251 用 `sh` 执行命令 hook（`$0` = `/bin/sh`、`posix on`），并且**也不**兑现显式的 `"shell": "bash"`（`docs/reference/hooks-probe-2.1.251.zh.md` §A）。拒绝该*规则*会让一个在上游能跑的 hook 失效 —— 正是 §1 要防的那个倒退方向。`"powershell"` 仍未测；agentao 没有 Windows CI job |
| `async` / `asyncRewake` | 后台执行、exit-2 唤醒 | 无后台 runner | **拒绝**并告警 |
| `if` | 一条权限规则模式，尽力而为 | 无 | **拒绝**并告警。原理上够得着 —— agentao 有带模式匹配的权限引擎 —— 但它是一个带自己那套 Bash 子命令语义的子特性，不是一个接上去就完事的字段。§11 记录理由，处置归本表 |
| `statusMessage` | spinner 文案 | 无 | **忽略** —— 纯装饰，对契约无影响 |
| `once` | 仅 skill frontmatter | agentao 没有 skill hooks | **忽略** —— 按构造不适用 |

**这张矩阵就是权威。** 本计划任何其他小节若给出这些字段的处置，那都是对本表的引用、不是独立决定。处置若
要改，先改这里 —— 早期版本就因为在两个地方各决定一次而漂移过两回（`if` 在这里「待决」而在 §11「拒绝并告
警」；§7.1 只列了两个路径变量而这里是三个）。

路径占位符是**三个**，而第三个是最容易被漏掉的那个：`${CLAUDE_PROJECT_DIR}`、`${CLAUDE_PLUGIN_ROOT}`、
`${CLAUDE_PLUGIN_DATA}`，它们既替换进 `command`、**也替换进每个 `args` 元素**，并作为环境变量导出到被
spawn 的进程上。§7.1 覆盖导出；`${CLAUDE_PLUGIN_DATA}` 还额外需要一个 agentao 没有的「逐插件数据目录」
—— 那是一个决策，不是一次替换。

#### shell：两处确定的缺口，加一处文档自身的歧义

有两个很诱人的判定，而两个都不对：`shell:"bash"` 是空操作（「bash 本来就是默认」），以及由此推出 POSIX
基线不合规。**参考文档没有把这个问题定下来** —— 它在同一页上说了两件不同的事：

- §"Exec form and shell form"：*"**Shell form** runs when `args` is absent. The `command` string is
  passed to a shell: `sh -c` on macOS and Linux…"*
- §"Command hook fields" 里 `shell` 那一行：*"Shell to use for this hook … **Defaults to `"bash"`**,
  or to `"powershell"` on Windows when Git Bash isn't installed."*

一个默认值为 `"bash"` 的字段，和一个被记为 `sh -c` 的 shell form，不可能同时描述「字段未设置」这同一种情
况。而 agentao 的 `shell=True` 在 POSIX 上给的是 `/bin/sh`（`_dispatcher.py:353`），所以这条基线合不合
规，完全取决于哪一句是权威 —— 本计划没有资格替它选。**由 G5 来定**：要么钉住带日期快照的读法，要么对一个
真实的 Claude Code 安装做探针；单凭文档解决不了。

有两处缺口**是确定的**，且与答案无关：

- **`shell` 字段根本不被认。** 今天显式写 `shell: "bash"` 什么都不会变，而在 `/bin/sh` 下，一个要了 bash
  的 hook 拿不到 bash。这正是 §2.4 那一行要拒绝的东西。
- **Windows。** Python 的 `shell=True` 跑的是 `cmd.exe`，它既不是 Git Bash 也不是 PowerShell，而那是参考
  文档在 Windows 上的两种 shell。agentao 没有 Windows CI job，所以两个方向都未经测试 —— 这也正是 codex
  借鉴评审里被判为真正头条的那个缺口。

等 G5 定了之后，修法就是一个参数 —— 在现有调用上加 `executable=…`，或改成显式的 `[shell, "-c", cmd]`
向量 —— 外加一个「所选 shell 不存在时怎么办」的决定。它留在 G5 和 exec 形式一起，因为是同一处代码。

#### 为什么 `claude-code` 模式要拒绝 `prompt`

参考文档的 `prompt` hook **是要调用模型的**：提示词里带 `$ARGUMENTS`，它会被替换成该 hook 的 JSON 输
入，模型据此评估，模型回的 JSON 才被解析成这个 hook 的决策。

agentao 的 `prompt` hook 不调用任何模型。`_run_prompt_hook` 把 `{userMessage}` 替换进提示词串，然后把
**替换结果**append 进 `additional_contexts`（`_dispatcher.py:603`）—— 提示词文本本身成了模型 context。
方向是反的：上游是把提示词发**给**模型再读回决策；agentao 是把提示词注入**进**对话。

所以这不是同一个特性没做完，而是**顶着同一个 `type` 的另一个特性**。在 `claude-code` 模式下接受一个
Claude 的 `prompt` hook，等于把一句评估指令（"Evaluate if Claude should stop: $ARGUMENTS"）连同未被替
换的 `$ARGUMENTS` 一起粘进对话当 context。拒绝并告警才是诚实的答案。

它在 `agentao-v1` 下**完整保留** —— 那里它是 agentao 自己的扩展，并且是有文档的。对照文档 §7 第 5 条把
它列为 agentao 领先 codex 的一处，那一条依然成立、无需改动，它的括号里本来就写着「是模板展开而非模型调
用」。做一个真正的 prompt runner 是另一个特性，不是一次合规修复。

### 2.5 串行短路是语义偏离

参考文档：*"All matching hooks run in parallel. If you define the same handler in more than one
settings file, it runs once. A plugin's or skill's copy of the same handler stays separate."*
agentao 的 hook 全部来自插件，所以那条去重条款在这里永远不适用，而并行那条永远适用。agentao 是串行、
**并且在第一个阻断处就停** —— 四处：
`PreToolUse` 在首个 `deny`（`_dispatcher.py:117`）、`Stop` 在阻断或继续（`:156`）、`PreCompact` 在首个
`cancel`（`:193`）、`UserPromptSubmit` 在阻断（`:497`）。

这是一处**语义**偏离，不是性能选择 —— 它曾被当成后者、列在「不许回退」里。一个会往审计 sink 写日志、通知
服务、或写标记文件的第二条匹配 hook，在前一条阻断之后**根本不会跑** —— 它的副作用就是不发生，而且没有任
何东西告诉作者。于是拷一份两条 hook 的配置过来，即使两条在 wire 上都完全合规，可观察行为也不一样。

**在 `claude-code` 模式下，所有匹配的 handler 在有界并发下启动，聚合放在之后。**「全都启动」是一个保
证、不是一个愿望，而光有池上限提供不了它 —— 超出上限就排队，而排队的 handler 在共享 deadline 下可能永远
跑不到。因此逐事件的 handler 数要被限住（门槛 G6）。**限在哪**，是最容易做错的地方，而且会错两处、彼此
独立。

*限额属于合并点，不属于解析期。*「加载期」听起来就是解析该文件的时候 —— 可规则是逐文件解析、
之后才拼起来的：`resolve_all_hook_rules` 遍历每个插件、每个 `hook_specs` 条目，各自解析成一个列表，再
extend 进同一个扁平结果（`_user_turn.py:28-59`）。两个插件各带三个 `SessionEnd` handler，就是一个事件上
六个 handler，而两个文件都没超过任何限额。所以限额要作用在**合并后的**列表上，并在告警里点名撞上限的那
几个插件 —— 逐文件检查约束不了运维实际装出来的任何东西。

*而且光限住配置也仍然兑现不了这个保证，因为 dispatch 之间也在互相抢。* 一批工具调用跑在一个 8 worker 的线程池上
（`tool_executor.py:189`），而每个 worker 在自己内部触发自己的 `PostToolUse` / `PostToolUseFailure`
dispatch（`tool_executor.py:463-472`）。八次各自合规的 dispatch 打到同一个共享 hook 池上，排队方式和单次
dispatch 内部超额 handler 排队完全一样。所以准入的单位是**一次 dispatch**、不是一个 handler：一次
dispatch 要么一次性拿到它全部 handler 的容量，要么在任何一个启动之前先等 —— 也就是按该事件（现已有界的）
handler 数开一个逐 dispatch 的 executor，外面再套一个限制总线程数的全局天花板。如果这套机制被判为太重，
替代方案是把承诺弱化成「所有匹配 handler 都被**提交**」，并明说在 `SessionEnd` 共享的 1.5 秒预算下排队的
那个可能永远不会跑。这是诚实的，代价是恰好在那个 deadline 短到能看出差别的事件上放弃了这次修复。两者都归
G6。

**去掉短路却保留串行，是对不上的**，而这值得写明，因为它看起来是最省事的修法。§2.4 的 timeout 行给
`SessionEnd` 的 handler **共享 1.5 秒预算**，串行时第一个就能把它耗完，第二个根本起不来。「所有 handler
都跑」与共享 deadline 在串行下不可能同时满足。要么并发进入范围，要么 timeout 那一行和「全跑」的承诺一起
撤回 —— 而撤回会像把配置形状门控在 `contract` 上一样，把 §1 掏空。

代价是真实的，也该写明：

- **每个匹配 hook 都会被 spawn**，即便结论已知。短路的存在部分正是为了在结果已定之后停止继续 fork
  （`PreCompactHookResult` 的 docstring 明说了这一点）。
- **第四个线程池。** `CLAUDE.md` 记录了三个刻意互不争用的池 —— `agentao-arun-*`、
  `agentao-web-html-*`、以及留给 httpx 的 loop 默认池 —— 并明确警告不要把它们合并。hook 需要自己命名的
  池和自己的上限，不能借用。
- **聚合必须不再依赖完成顺序。** 「首个 deny 获胜」现在指的是首个**被执行**的；并发之后它会变成首个
  **完成**的，于是胜出的 `reason` 会逐次变化。合并规则要改成与顺序无关（**任一** hook deny 即 deny），
  而任何平手裁决 —— 该呈现哪条 reason —— 按**声明顺序**、绝不按完成顺序。这是设计门槛 **G6**。

`agentao-v1` 的串行短路 dispatch 保持不变。

---

## 3. 契约版本

**文件级，解析后落到每条规则上** —— 不是「逐规则」，那说过头了：一个文件一个契约，这个值被复制到它产出的
每一条 `ParsedHookRule` 上，好让 dispatcher（它在每个决策点上手里拿的都是规则、从来不是文件）能据此行动。
本计划**不**提供 handler 级覆盖；将来若真要，字段已经在对的层上了。

```json
{
  "contract": "claude-code@profile-1",
  "hooks": { "PreToolUse": [ { "matcher": "Bash", "hooks": [ { "type": "command", "command": "…" } ] } ] }
}
```

| 取值 | 含义 |
|---|---|
| `agentao-v1` | 今天的**契约面**，冻结。扁平 handler 列表、dict matcher、`{event,data}` 信封、顶层 `blockingError` / `preventContinuation` **外加嵌套的 `hookSpecificOutput.compactionDecision`**（§3.3 —— 这三个并**不**都在顶层，而本计划这么断言了十版），`suppressOutput` 控制 `<stop-hook>` 回显、串行短路 dispatch、`Stop` 重入上限 3。「冻结」覆盖到哪为止，见下文。 |
| `claude-code@profile-<n>` | Claude 契约的 **agentao profile** —— §1 逐条列举的事件、handler 与字段集合，连同它的配置形状、matcher 语义、输入 payload 与输出字段。每个 profile 都带上它所依据的那份上游文档的来源戳（见下）。 |
| `claude-code` | 指向 agentao 当前所带的最新 profile 的别名。方便，而且**按设计会漂移**；需要稳定性的插件请钉带编号的那种写法。 |
| 缺省 | **由文件形状识别**（§2.2）—— 官方嵌套 ⇒ 最新 profile，扁平 ⇒ `agentao-v1`。新生成的插件仍然显式写带编号的 `claude-code@profile-…`。 |
| 显式但未知 | **该文件被禁用**并告警。不是回退 —— 见 §2.2。 |

**「冻结」不覆盖什么。**「今天的行为，冻结」太宽了：§8 的第 1 步会改掉**每一个** hook 的截断、预览文本和
落盘路径，而且是在 `contract` 被解析之前两步 —— 按设计它必须如此，因为内存上限不可能是逐文件的可选项
（§6）。与其把第 1 步重新贴成「不可见」，不如把承诺限定住：

标签带一个快照，是因为上游契约在动：就在对照文档的锚点与本计划初稿之间，参考文档的 `permissionDecision`
多了 `defer`，`prompt_id` 作为一个按版本分岔的通用字段出现。一个裸 `claude-code` 若默默表示「agentao 今天
实现了什么就是什么」，会让插件行为变成 agentao 版本的函数，而文件里没有任何东西记录这一点。

**标签点名的是 agentao 的 profile，因为日期和产品版本号都立不住。** 一个日期（`claude-code@2026-08-26`）
记录的只是某人抓取某个网页的那一天。一个产品版本号（`claude-code@2.1.248`）看起来更好，因为参考文档确实
在行为条款里点名产品版本 —— 本计划依赖的文字里就有四处：

| 版本 | 它分岔的行为 |
|---|---|
| v2.1.199 | 被标记 `requiresUserInteraction` 的 MCP 工具，不再能被 hook 的 `"allow"` 自动放行 —— 带不带 `updatedInput` 都不行 |
| v2.1.212 | 输入字段里出现 `modelsUsed` |
| v2.1.214 | exit 2 **加上** schema 不合法的 JSON 仍然阻断，并用 stderr 当理由。之前：算非阻断错误，动作继续 |
| v2.1.248 | 解析不成 JSON 的 stdout 是一条 `hook error` 通知，且**不会**作为 context 加入。之前：当纯文本 |

后两条对 §4.2 是承重的，而本计划确实在不知道存在分岔的情况下实现过其中一条的「之前」那一支 —— 这正是把
这些门槛列成表、而不是逐条去碰的原因。**但「拿页面里提到的最新版本号给快照命名」并不成立。** 一句
`Before vX` 只从**下方**给出边界 —— 它说的是「就那一条而言，本页描述的是 2.1.248 之后的行为」—— 对上方一
个字都没说。新增功能是不带门槛发布的，因为它没有「之前的行为」可以对比。

同一次抓取，实测：

| 问题 | 答案 |
|---|---|
| changelog（`changelog.md`）里的最新版本 | **2.1.251**，日期 2026-08-28 |
| 2.1.251 给 hooks 加了什么 | `PreModelSwitch` / `PostModelSwitch` 事件；`SessionStart` resume 输入新增会话陈旧度与重新 cache 成本 |
| 抓到的页面里有吗？ | **没有** —— `grep -c 'PreModelSwitch'` → `0`；`staleness` / `re-cache` 也都没有 |

所以那个页面不是 2.1.251 的文档，而把它叫 2.1.248 又断言了一个没有依据的上界 —— 它同样可能带着 2.1.249
或 2.1.250 里一处不带门槛的改动。它就是一件东西：**2026-08-28 19:29 被服务出来的那一版页面**，它与任何
一个发布出来的二进制之间的关系，两个方向都未经验证。

有两条诚实的出路，本计划取第一条：

1. **用 agentao 自己给 profile 命名。** `claude-code@profile-1` 是一个由 agentao 列举（§1）、实现并测试的
   集合，并盖上它所依据之物的来源戳：

   | 来源戳字段 | profile-1 的取值 |
   |---|---|
   | 来源 | `code.claude.com/docs/en/hooks.md` |
   | 抓取时间 | 2026-08-28 19:29 |
   | 字节数 / sha256 | 295,595 / `c984f918cf93f75bd84bc7ea4c63006ca0624f3ddde1431d625af4933def5179` |
   | 抓取时 changelog 头部 | 2.1.251 —— **而页面里还没有它新增的 hook 内容** |
   | 依赖的行为门槛 | v2.1.214、v2.1.248（§4.2） |
   | **一天后**的线上页面 | 297,440 字节，sha256 `b727657a202f472207b60fd443aa5542d8c6e1f8b9aef79689c8ec917cf19e6a`（2026-08-29） |

   **锚点在一天之内就漂了，而这次实测正是本节全部论证的依据。** 2026-08-29 重抓与原快照相差 **19 行**，全
   部落在 `SessionStart` 的输入小节：那四个 resume 陈旧度字段（`seconds_since_last_response`、
   `context_tokens`、`prompt_cache_likely_expired`、`estimated_cache_write_usd`），页面自己写着它们
   *"require Claude Code v2.1.251 or later"* —— 正是 §3 在 08-28 那次抓取里记为**缺席**、而 changelog 头部
   已经是 2.1.251 的那批新增。也就是说页面在一夜之间追上了它自己的 changelog，而一个 `claude-code@2.1.251`
   的标签会提前一天做出这个断言。与本计划相关的部分一个字都没变：Decision-control 表、通用字段规则、以及每
   一条 `continue` / `decision` 条款在两次抓取里逐字节相同。profile-1 仍然钉在 `c984f918…`；这处 diff 记在
   这里，好让下一位评审不必重新推一遍。

   agentao 能为自己实现的东西背书；它背书不了 Anthropic 的版本语义，而一个假装能背书的标签，与 §1 那句
   旧措辞是同一种越界，只是低了一层。
2. **继续追产品版本号**，那需要装上那个确切版本的 CLI 并逐条探它的行为 —— 一套真实的探针工程、真实的成
   本，也是唯一能让这个名字站得住的东西。真做出来之后，`claude-code@2.1.248` 才有意义，而 profile-1 成为
   它的别名。

产物问题仍归 G7：一个只能解析成一个活 URL 的 profile 不是快照。要么把抓到的参考文档存进仓库
（`docs/reference/snapshots/`，约 290 KB 的上游文字 —— 这是一个值得先问清楚再做的再分发问题），要么仓库里
只记上面那张来源戳表、存档放在仓库之外。让这两条中的任何一条可核验的，都是那个哈希。

默认值最早也要到下一个 major 才翻转。**不做双形状 payload** —— 同时发两套字段等于造出第三种契约，
matcher 还得去猜作者到底想用哪套。

### 3.1 它落在哪，以及唯一没地方放的那处

`contract` 成为 `ParsedHookRule` 的一个字段（`models.py:237`），挨着 `plugin_name`。

麻烦在于：`parse_dict` 写的是 `hooks_dict = raw.get("hooks", raw)`（`_parser.py:66`）—— 它**既**接受
包装形状、**也**接受裸的事件字典。在裸形状下，顶层的 `"contract"` 键不是元数据；它会被当成事件名解析，
降级成一条「不支持的事件」警告。所以：

- `contract` 只从包装形状里读。
- 裸形状放不下这个键 —— 但它照样走**形状识别**（§2.2），所以一份裸的官方形状字典不会被困在
  `agentao-v1` 上。
- 两个入口（`parse_file` `:28`、`parse_dict` `:44`）都要把解析出的值挂到它们产出的每条规则上，下游不
  再二次推导。
- 未知的 `contract` 取值 → **禁用该文件**（§2.2）。只有**缺省**才落到识别那条路上。

### 3.2 payload 是在选规则之前就构造好的

`_dispatch_user_prompt_submit` 为整个事件构造一份 payload（`_hook_dispatch.py:44`）再交给 dispatcher，
dispatcher *之后*才去选规则。逐规则 contract 把这个顺序反过来了。最省的正确写法：把一个按事件的
builder 闭包传进 dispatch，按 contract 记忆化 —— 最坏每事件构造两次，而不是每规则一次。

这同时能退掉 `_matches` 的形状嗅探（`_dispatcher.py:313,323`）：它今天要读两种布局，只是因为它无从知道
自己拿到的是哪一种。

### 3.3 agentao 自有字段不在 profile-1 内

最自然的设计 —— `claude-code` 模式下 agentao 自有输出键「移到 `hookSpecificOutput.agentao` 之下」，
`agentao-v1` 下保持顶层 —— 在本计划里挂了九版，而**没有任何地方实现过这个命名空间**：`ParsedHookOutput`
上没有字段（§4.1）、能力表里没有行（§5.1）、没有消费者（§5.2）、两个 handler 同时设它时没有聚合规则。按
§4.2 的「不认识的键」规则，`hookSpecificOutput.agentao` 就只是一个未识别键 —— 收进 `unknown_fields`、被
忽略、被诊断。**这个承诺从写下那天起就是空的**，而 §5.1 那条「每个 `accept` 欠三样东西」的检查，只要这个
命名空间曾被填进它本该待的那张表，就能抓到它。

而且它不只是「没实现」。在 `Stop` 上，参考文档的 `decision:"block"` 意思是**继续对话**，agentao 的
`blockingError` 意思是**结束这一轮**（`_runner.py:964` vs `:984`，§5.4）—— 于是同一份输出里同时装着两个相
反的控制，而 §5.4 那张格帮不上忙：它是跨*规则*合并的，这里是一条规则自己的输出。所以支持这个命名空间，除
了补齐上面四样，还要额外加一条**输出内部**的优先级规则 —— 而这是一个还没有人提过需求的特性。

**所以 profile-1 没有它。** `claude-code` 模式下 agentao 自有的控制键就是不可用；想要 `blockingError`、
`preventContinuation` 或 `compactionDecision` 的 hook 声明 `agentao-v1`，在那里它们和今天一模一样地工作。
这个命名空间可以在后续 profile 里回来，而它的价码现在写下来了：一个字段、一行能力、一个消费者、一条聚合
规则，外加一条输出内部的优先级规则。

**顺带更正一处事实。** §3 的 `agentao-v1` 行和本节都说这三个键在 v1 里是顶层的。有两个是 ——
`blockingError` 与 `preventContinuation` 读自顶层（`_output_parsing.py:65`）—— 但 **`compactionDecision`
不是**：它读自 `hookSpecificOutput.compactionDecision`（`_dispatcher.py:226-229`）。v1 本来就混着两种形
状，这也算一个同向的小论据：「给 agentao 自有键划命名空间」这个想法，连在 v1 内部都没有被一致地执行过。

**不变的是：**对照文档 §4 的结论 —— `PreCompact` 取消**根本没有可收敛的事实标准**：参考文档要的是 exit 2
或顶层 `decision:"block"`，codex 用的是 `continue:false`，而参考文档明写该事件会*丢弃*这个字段。在
`claude-code` 模式下 agentao 跟参考文档走、不跟 codex 走；在 `agentao-v1` 里 `compactionDecision` 照常工
作，所以既有的控制面（`CLAUDE.md`，「The control plane has two layers and one merge rule」）原样不动。

---

## 4. 归一化的解析结果

诊断成立，而且代码本身说明了为什么。`_parse_command_output`（`_output_parsing.py:26`）直接往运行时字段
里写，并且在 `blockingError`（`:65`）、`preventContinuation`（`:77`）、`additionalContext`（`:90`）之后
各自**提前 return**。两个后果：

- 一个 hook 同时输出多个可识别键时，只有第一个生效。
- 参考文档的优先级规则 —— `continue` *"takes precedence over any event-specific decision fields"* ——
  靠再加 `if` 分支是实现不了的，因为优先级要求**先把所有字段解析完**再做决定。

**名字要叫 `ParsedHookOutput`，不能叫 `HookOutcome`：** `_HookOutcome` 已经被占用
（`runtime/chat_loop/_outcomes.py:13`），是 UserPromptSubmit 的 dispatch 结论。

### 4.1 通用字段 + 逐事件 typed union

单一的 `control: allow | block | stop | continue` 不够用：它表达不了 `permissionDecision`
（`allow`/`deny`/`ask`/`defer`），也没有位置放 `updatedToolOutput` —— 那会与 §10 第 3 条相矛盾，那里
`ask` 是一条不许回退的领先项（`models.py:302` 今天就支持；codex 拒绝它）。

**是两个类型，不是一个。** 一个 hook 的 JSON *说了什么*，和运行时*做了什么*，是两个不同的对象；后者是前
者加上退出码的函数：

```
ParsedHookOutput            # 一个 hook 的 stdout 声称了什么 —— 只是解析结果，什么都还没裁决
├── universal
│   ├── continue_processing : bool          # 顶层 `continue`；压过事件专属 decision、
│   │                                        #   自己被 exit 2 压过，且只在能力表允许的事件上
│   │                                        #   才生效 —— 见 §4.2
│   ├── stop_reason         : str | None
│   ├── system_message      : str | None
│   ├── terminal_sequence   : str | None     # 第五个通用字段；§5.1 定它 accept 还是
│   │                                        #   ignore，但它必须能被解析出来
│   └── suppress_output     : bool           # claude-code 模式下 inert
├── additional_context : list[str]           # hookSpecificOutput.additionalContext —— 八个事件里有
│                                            #   六个带它，所以它不是事件专属的；§5.2 负责逐事件
│                                            #   路由，这里只负责装着它
├── unknown_fields : list[str]               # agentao 没实现的键 —— 只留名字，供那条一次性
│                                            #   诊断使用（§4.2）。它们的出现永远不是错误，见 §1
├── plain_text : str | None                  # 状态为 "plain" 时的 stdout（§4.2）
└── decision : 以下之一
    ├── PreToolUseDecision   { permission: allow|deny|ask|defer|None, reason, updated_tool_input }
    ├── PostToolUseDecision  { block: bool, reason, updated_tool_output }
    ├── UserPromptSubmitDecision { block: bool, reason, suppress_original_prompt: bool }
    │                                        # 这个 flag 会被解析，但在 profile-1 里不被执行
    │                                        #   （§5.1）—— 「表示出表格随后不兑现的东西」，与
    │                                        #   `defer` 采用的是同一条纪律
    ├── BlockDecision        { block: bool, reason }   # Stop、PreCompact，以及
    │                                        #   PostToolUseFailure —— 它的处置**存疑**（§5.1）。
    │                                        #   类型可以装下一个 profile 暂不兑现的 decision；
    │                                        #   这正是解析层与处置层要分开的理由
    └── SessionStartDecision { reload_skills: bool }   # 解析，profile-1 里忽略（§5.1）
        # SessionEnd 仍是纯 context：它根本没有任何决策控制

ResolvedHookOutput          # resolve() 的返回值 —— 运行时站点真正消费的东西
├── control : Allow | Block(reason) | Stop(reason) | PermissionDecision(...) | None
├── user_notices[]        → 给人看
├── model_contexts[]      → 模型的 context 通道
├── tool_contexts[]       → 注入到 tool result 旁边
├── updated_tool_input / updated_tool_output
└── diagnostics[]         → 警告、解析失败、限额提示
```

`defer` 即便 agentao 不实现也要带上（参考文档标注它仅 `-p`）。类型必须能**表示**能力表随后要**降级或不兑
现**的东西：一个解析不出来的值，没法带理由地降级，只能被悄悄丢掉 —— 而「把它拒掉」正是 §1 第三条规则对输
出字段所禁止的，所以 §5.1 把 `defer` 降级为 `deny` 并在理由里说明。同一条分离也覆盖存疑的
`PostToolUseFailure` decision，以及「解析了但不兑现」的 `suppressOriginalPrompt` / `reloadSkills`：**解析
层刻意比处置层宽**，好让处置能在后续 profile 里改变，而不必改解析器。

`additional_context` 放在 union 之上而不是之内，是因为八个事件里有六个带它 —— `SessionStart`、
`UserPromptSubmit`、`PreToolUse`、`PostToolUse`、`PostToolUseFailure`、`Stop`。它是整份契约里被用得最多的
一条通道，而「两半都不放它」是最容易犯的错：那样 §4.2 的 `absorb_channels()` 就没有可读的类型化字段。这个
值**去哪儿**是逐事件的（§5.2 的消费者表）—— 模型 context、tool context、或哪儿都不去 —— 但那是路由，不是
解析。

### 4.2 优先级是一个函数，不是字段顺序

`continue` **并不是**简单的「优先级最高」。参考文档说的比这窄，而且它上面还压着另一条：

- `continue: false` *"Takes precedence over any event-specific decision fields"* —— 压过 `decision`、
  压过 `permissionDecision`。**不是**压过一切。
- exit 2，在可阻断的事件上：*"exit 2 blocks whether or not you print JSON: even a JSON
  `permissionDecision` of `\"allow\"` can't override it"* —— 而且 Claude 仍然会**读**那段 JSON，有阻断
  理由就用它，没有就用 stderr。

所以顺序是 **exit 2 → `continue` → 事件 decision**，它没法表达成 dataclass 里的字段顺序。

有两件事是第一版实现一定会做错的。第一，**exit 2 不是布尔量。** 参考文档给了它三种结果，而且三种在
agentao 的八个事件上都活着：

| exit 2 的结果 | 事件（agentao 八个之中） |
|---|---|
| **阻断** | `PreToolUse`、`UserPromptSubmit`、`Stop`、`PreCompact` |
| **stderr → 模型**（工具已经跑过了） | `PostToolUse`、`PostToolUseFailure` |
| **stderr → 只给用户** | `SessionStart`、`SessionEnd` |

只测一个 `blocks_on_exit_2` 谓词、其余一律掉进纯 stdout 分支，会恰好在 §5.2 承诺 stderr 抵达模型的那两个
事件上把它静默丢掉。

第二，**控制结论不等于整个结果。** 只返回 `Block` / `Stop` / `decision`，会把 `systemMessage`、
`additionalContext`、`tool_contexts`、`updatedToolOutput` 和 `diagnostics` 一起丢掉 —— 那些正是 §4.1 存在
着要分开的通道。它们与结论正交：一个既阻断、又发用户通知的 hook，两件事都做。

所以 `resolve()` **先**按退出码分支，不这么做就会带出两个顺序错误。一是在看退出码之前就把纯文本 stdout
当成模型 context —— 可参考文档把这件事门控在 **exit 0** 上（*"The exceptions are
`UserPromptSubmit`, `UserPromptExpansion`, and `SessionStart`, where Claude Code adds plain-text
stdout as context"*，§"Exit code 0"），于是一个以 exit 1 失败并打印诊断的 `SessionStart` hook，那段诊断
会被注入模型 context。二是无条件执行 `continue:false`，而参考文档在大约十来个事件上丢弃该字段，包括
`SessionEnd`、`PreCompact`、`PostCompact` —— 能力表本来就知道这件事，只是没被查询。

分支结构是 `{0, 2, 其他}` × **五种** stdout 状态 —— 不是初读那一页时看起来的三种或四种。按 2026-08-28
抓到的版本，它是按字符串的**两端**决定要不要尝
试 JSON 的，而且把解析失败当成错误、不当成文本：

| 状态 | 由什么产生 | 去哪儿 |
|---|---|---|
| `empty` | stdout 什么都没有 | 哪儿都不去 —— 但见下面非 0/2 的那条通知 |
| `plain` | 去掉空白后，不是「以 `{` 开头**且**以 `}` 结尾」的 stdout —— *"Starts with `{` but doesn't end with `}`: Claude Code treats it as plain text"*、*"Starts with anything else: Claude Code treats it as plain text, a JSON array or a quoted JSON string included"*。另外还包括：多行输出、每行各自都是合法 JSON、且没有任何一行设置了 JSON output 字段 | 仅 **exit 0** 时进模型 context，且仅在 `UserPromptSubmit` / `SessionStart` 上 |
| `parse_error` | 以 `{` 开头、以 `}` 结尾，但解析不了 —— 或者上述多行情形中**有**某一行设置了字段 | 一条**用户可见**的 `hook error` 通知、携带解析报错，在除 2 以外的任何退出码下；并且 *"On the events that add plain-text stdout as context, Claude Code doesn't add the text"* |
| `schema_invalid` | 能解析成对象，且某个**已声明**字段的取值过不了校验 —— 不认识的键永远落不到这里（§1，以及下文） | 同一条用户通知，携带校验信息；*"the action proceeds"* —— **但 exit 2 例外，它照样阻断** |
| `valid` | 解析并校验都通过 | 通道与 decision，在**任何**退出码下 |

这张表里有三个坑，每一个都能落到一句原文上：

- **`[` 开头永远不是 JSON。**「不以 `{`/`[` 开头」是写 `plain` 行时最自然的写法，而它暗示数组会被解析。参考
  文档把 JSON 数组和被引号包起来的 JSON 字符串**点名**划到纯文本那一侧。
- **门是两端。** 一个被断掉的管道截成半截的 `{"decision":` 是*纯文本*：它根本到不了解析器，所以也不是解析
  失败。
- **解析失败不是文本，而且这一条按版本分岔。** 直觉读法 —— 一个以 `{` 开头、解析失败的字符串按首字符规则
  算纯文本 —— 曾经是对的，而说这话的那句（*"If it isn't valid JSON, Claude Code treats it as plain
  text"*）**已经不在参考文档里了**。取而代之的是：*"when Claude Code tries to parse your stdout as JSON
  and can't, it reports a non-blocking error on every exit code other than 2 … On the events that add
  plain-text stdout as context, Claude Code doesn't add the text. **Before v2.1.248**, Claude Code
  treated that stdout as plain text."* 在本计划瞄准的快照下（§3），它是一条通知、那段文本被扣住不进
  context —— §12 的测试断言的就是这个方向，而 pre-2.1.248 那个断言正是最容易被重新推导出来的错误。

**不认识的键不是 schema 失败。** agentao 的 profile（§1）在这八个事件上比参考文档的字段集少九个（§5.1），
所以一个在上游完全合法的 hook 会经常发出 agentao 没实现的键。用封闭 schema 去校验，会把每一个这样的键都变
成 `schema_invalid`，而上表又把它送去一条用户可见的 `hook error` —— 等于 agentao 在告诉作者「你正确的 hook
坏了」，同时把同一个对象里它**认识**的字段一并丢掉。所以：校验只作用于**agentao 已声明字段的取值**；未声明
的键收进 `unknown_fields`（§4.1），在控制层面被忽略，并按**每（规则，字段）一次**作为 `diagnostics[]` 冒出
来 —— 作者仍然知道这个字段没生效，而不会每次调用都被通知一遍。*一次*需要一个归属者，而最显然的那个是错
的：`PluginHookDispatcher` 在**六**处被重新构造 —— `cli/session.py:79,96`、`_hook_dispatch.py:47,124`、
`tool_runner.py:275`、`tool_executor.py:662,685` —— 最后两处还跑在池 worker **里面**，所以 dispatcher 级的
状态既去不了重、还会一边去重一边竞态。门槛 **G10** 管这件事：一个**会话级、带锁的注册表**，键是*稳定*的规
则键（`plugin_name` + 源文件或内联下标 + 事件 + matcher + handler 下标 —— 绝不能用 `id(rule)`，它在 reload
后就变了），插件 reload 时清空好让改好的 hook 重新播报，随会话一起清空。往两个方向做错，机制就反过来了：
要么每次调用都吵，要么彻底沉默。这是 §5.3 输入侧规则（绝不编造）与 `SUPPORTED_HOOK_TYPES_BY_EVENT` 既有纪
律（`models.py:217`）在输出侧的孪生。

还有两条更小的规则，都由上游写明。非 0/2 退出配 `plain` 或 `empty` stdout 是一条**用户**通知，参考文档连措
辞都规定了：*"followed by the first line of stderr, prefixed with `Failed with non-blocking status
code:`"*（§"Other exit codes"）。以及 schema 失败是一条用户通知、不是一行内部日志 —— **外加一个 exit-2 限
定**，它很容易在正文里被丢掉而代码里恰好还留着：*"A hook that exits 2 while printing JSON that fails JSON
output schema validation still blocks: Claude Code uses stderr as the blocking reason and records the
validation failure in the debug log. Before v2.1.214, Claude Code treated that combination as a
non-blocking error and the action proceeded."*

```python
def resolve(event, returncode, stdout, stderr, table) -> ResolvedHookOutput:
    out = ResolvedHookOutput()
    # state: "empty" | "plain" | "parse_error" | "schema_invalid" | "valid"。
    # 非 "valid" 时 ``parsed`` 为 None；``failure`` 只在两种失败状态下携带解析/
    # 校验报错，其余为 None —— 解析失败时根本没有对象可以挂这条消息。
    parsed, state, failure = parse_stdout(stdout)

    # 1. 通道。合法 JSON 在任何退出码下都生效。解析或 schema 失败在除 2 以外的
    #    任何退出码下都是一条用户可见通知；退出码为 2 时由下面那段接管，理由取
    #    stderr。
    if state == "valid":
        out.absorb_channels(parsed, table, event)     # system_message、additional_context、updated_*
    elif state in ("parse_error", "schema_invalid") and returncode != 2:
        out.user_notices.append(f"{event} hook error: {failure}")
    elif state == "plain" and returncode == 0:
        # 纯文本只在 exit 0 时是 context，且只在事件允许的地方。
        table.plain_text_channel(event, stdout, into=out)   # UPS / SessionStart 之外是空操作

    # 2. exit 2 —— 三种结果（见上表）。
    if returncode == 2:
        kind = table.exit2(event)          # "block" | "model_feedback" | "user_notice" | "ignore"
        reason = parsed.blocking_reason if state == "valid" else None
        if reason is None:
            reason = stderr                # JSON 有阻断理由就用它，没有才用 stderr
        if kind == "block":
            out.control = Block(reason)
            return out                     # JSON 盖不住 exit-2 的阻断
        if kind == "model_feedback":
            out.model_contexts.append(reason)
        elif kind == "user_notice":
            out.user_notices.append(reason)

    # 3. 其他非零退出 + 没有可用 JSON = 一条用户看得见的非阻断错误。
    elif returncode != 0 and state in ("plain", "empty"):
        first_line = stderr.splitlines()[0] if stderr.strip() else ""
        out.user_notices.append(
            f"{event} hook error: Failed with non-blocking status code: {returncode} {first_line}"
        )

    # 4. 来自 JSON 的控制结论，仅当 exit 2 没有把它定下来时。
    if state == "valid":
        if parsed.universal.continue_processing is False and table.honors_continue(event):
            out.control = Stop(parsed.universal.stop_reason)
        else:
            out.control = table.apply(event, parsed.decision)
    return out
```

这个顺序买到五件事，后四件在更早的版本里都做错了：合法 JSON 在**任何**退出码下都生效（*"Claude Code
reads JSON output fields from stdout on every exit code, not just 0"*）；纯文本**只在 exit 0** 时进模型；
而*解析失败*永远进不了模型；`continue` 要过 `table.honors_continue(event)`，不是到处都触发；以及**有四种
失败形态是送到用户那里的**、不是送进日志 —— 解析不了的 JSON、schema 不合法的 JSON、没有 JSON 的非 0/2 退
出、以及那些「exit 2 结果为用户通知」的事件上的 stderr。参考文档确认这是**一条**通道而不是三条：对 `SessionStart`，exit-2 的 stderr *"renders in the
transcript as a `<hook name> hook error` notice, the same way a non-blocking error does"*，而且 Claude
看不到它。

`reason` 的兜底之所以摊开写、而不是折成一个条件表达式，是因为紧凑写法
`parsed.blocking_reason if parsed else None or stderr` 的结合方式是
`(parsed.blocking_reason) if parsed else (None or stderr)` —— 于是一个 exit 2、带 JSON 但没有 blocking
reason 的 hook，会带着 `reason=None` 阻断，永远走不到 stderr。

agentao 已经在一个地方有了这个形状：`_run_stop_command_hook` 刻意在解析 JSON **之前**检查
`proc.returncode == 2`，好让 stdout 里的 `continue:false` 无法反制它（`_dispatcher.py:562`）。那就是先
例 —— 它只需要从「某一个事件的特例」变成「规则」。

因此矩阵（§12）必须覆盖**组合**，而不是逐字段断言：exit 2 × `continue:false`/`true`/缺省 ×
`allow`/`block`/缺省，逐事件。

### 4.3 `user_notices` 其实已经建了一半

`StopHookResult.system_message` **是存在的**（`models.py:372`），解析器也在给它赋值
（`_output_parsing.py:180-182`）—— 而 `agentao/` 里没有任何地方读它。全仓唯一的读者是一个测试
（`tests/test_hooks_stop_suppress_output_and_system_message.py:37`）。然后同一个解析器*又*把这个字符串
append 进了 `additional_contexts`（`:183`），也就是模型通道。

所以 `systemMessage` 那条是**停掉双写 + 给已有字段接个消费者**，不是「造一条通道」。

### 4.4 `PreToolUse` 的生命周期：它什么时候触发，以及 `updatedInput` 这次重新进入

两半、同一条顺序：**这个 hook 到底什么时候触发**，以及它返回改写**之后**发生什么。agentao 在其中一处做错
了，另一处则是碰巧做对的。

参考文档把它写在 `PostToolUseFailure` 的一条注记里：

> *"This event doesn't fire for tool calls rejected before execution: an unknown tool name, input that
> fails schema or tool-specific validation, or a permission denial. Validation rejections are returned
> as `tool_use_error` results and happen before hooks run, so they fire neither `PreToolUse` nor
> `PostToolUseFailure`. **Permission denials fire `PreToolUse`** but not this event."*

两条规则，方向相反：

| 被拒原因 | 上游触发 `PreToolUse`？ | agentao 今天 |
|---|---|---|
| 未知工具名 | **不触发** | **碰巧合规** —— `ToolPlanner` 在 plan 存在之前就 append 一条错误结果并 `continue`（`tool_planning.py:438-451`），所以根本没有 plan 走到 phase 1.5 |
| 输入过不了 schema / 工具自定义校验 | **不触发** | **没有任何检查能触发这条规则** —— 见下。agentao 在执行前确实不校验工具输入，但不是因为没有 schema |
| 权限拒绝 | **触发** | **不合规** —— `_apply_pre_tool_use_hooks` 会跳过任何 decision 不是 `ALLOW`/`ASK` 的 plan（`tool_runner.py:277-279`，注释原话「An already-DENY plan can't be made "more denied"; skip the fork」） |

只要 hook 只能*收紧*裁定，这个跳过就是个正当优化：调用既然已经被拒，hook 说什么都改不了结果，何必 fork。
一旦契约要求 hook 必须**观察**到这次调用，它就不再正当了。注册在 `PreToolUse` 上的审计 hook、通知 hook 或
指标 hook，永远看不到被拒的调用 —— 而那恰恰是这类 hook 存在的目标群体 —— 并且没有任何东西告诉它的作者。
这与 §2.5 的短路是同一个缺陷、低一层：只盯着裁定，忘了副作用。

**`claude-code` 模式删掉这个跳过；`agentao-v1` 保留它**（今天的行为，冻结 —— §3）。代价要写明、而不是等
着被发现：每一次被拒的工具调用现在都会 fork 一个 hook 进程，于是一个大量拒绝的会话要为此付费，而第一层
输出限额（§6）同样适用于这些运行。

**第 2 步需要一个不存在的校验器，和一份存在的 schema。**「不合法 ⇒ 不触发 hook」加上「今天没有东西可以拿
来校验」是一条永远触发不了的规则，而后半句根本不成立：`Tool.parameters` 是一个返回 JSON Schema 的抽象属性
（`tools/base.py:106-109`），每个注册工具都有 —— 那正是 registry 转成 provider function-calling schema 的
东西。缺的是那次**检查**：`ToolPlanner.plan()` 解析出工具（`tool_planning.py:436-437`）之后直接走
`_decide`（`:453`）。

所以 G8 欠一个 **pre-hook 校验器**，外加关于它的三件事：

- **它跑在参数修复之后，不是之前。** planner 本来就会修复畸形参数（`tool_planning.py:426-434`）和工具名
  （`:438-451`）；对修复前的文本做校验，会拒掉今天能被成功修好的调用。校验是修复之后的最终裁定，而失败是
  拒绝、不是又一次修复尝试。
- **它对今天能跑到工具里的调用是一次行为变更。** 一个 `execute()` 容忍缺省可选项或松散类型的工具，今天能
  跑；有了严格校验就跑不了。这在合规侧正是目的（上游在 hook 之前就返回 `tool_use_error`），但它同时是一片
  真实的回归面，所以校验器随生命周期一起落在第 6 步，而不是更早地悄悄落地。
- **它的依赖是个决定，而最显然的兜底方案已被推翻。** `jsonschema` 4.26.0 已经在 `uv.lock` 里，但那是**传
  递**依赖，提成直接依赖是一次供应链决定。诱人的替代方案 ——「只校验 agentao 自家 schema 实际用到的那个子集
  （`type`、`required`、`enum`、`properties`）」—— 对树内现有工具就已经太小了。`todo.py:33-52`
  嵌了 `array → items → object`，里面还有自己的 `properties` 和一个下沉两层的 `enum`；而 **MCP 工具是把第
  三方的 schema 原样透传的**（`mcp/tool.py:72-80`）—— agentao 无法约束那里会出现什么。一个不完整的校验器
  会放过非法的嵌套输入并报告成功，那比不校验更糟。**G8 要么上真正的校验器，要么砍掉第 2 步。**
- **工具自定义校验需要一个并不存在的接口。** §12 要一个「输入过不了**工具自定义**检查就不触发 hook」的测
  试，而现在没有任何东西能让它失败：`Tool` 只声明 `parameters`（`tools/base.py:106-109`），没有校验入口，
  所以今天工具自己的参数检查发生在 `execute()` 里 —— 在 hook 之后、也在权限裁定之后。这个接口必须是**纯
  的**：一个可选的 `preflight(args) -> str | None`，返回一条消息、不产生任何副作用，默认 `None` 从而不影响
  现有工具。G8 要么加它、要么把第 2 步的承诺收窄成「只做 schema 校验」并删掉那条测试 —— 唯独不能一边留着
  承诺和测试、一边两个都无从满足。

于是完整顺序如下。第 1–3 步和第 10 步是最容易被略掉的那一半，因为它们讲的是 hook 何时触发、而不是它返回了什么：

| # | 步骤 | 规则 |
|---|---|---|
| 1 | 解析工具 | 未知 ⇒ 错误结果，**不触发 hook** |
| 2 | **按工具自己的 JSON Schema** 校验原始输入 | 不合法 ⇒ `tool_use_error` 形状的结果，**不触发 hook**、不执行 |
| 3 | 计算引擎裁定 | `_decide`（`tool_planning.py:453`） |
| 4 | **dispatch `PreToolUse`** | **无论裁定是什么**，包括 `DENY` |
| 5 | 聚合改写 | 见下文 §4.4 的冲突规则 |
| 6 | 校验改写后的输入（agentao 自己的步骤 —— 没有上游依据，见上） | 不合法 ⇒ **拒绝该调用**，发用户通知。**不是**「保留原输入」：那会执行 hook 正想替换掉的东西。**G8** 探测之后可以翻案 |
| 7 | 对改写后的输入**重判** | 裁定必须描述「将要跑的东西」 |
| 8 | 取交集 | {重判结果, hook 自己的} 取更严；原本就是 `DENY` 的仍然是 `DENY` |
| 9 | 确认 | 针对**改写后**的输入 |
| 10 | 执行 | 不重新 dispatch hook |

第 8 步带着那条让旧跳过看起来安全的不对称：hook 在一次被拒的调用上**被征询**，但它仍然掀不翻这个拒绝。观
察权与裁决权是两件事 —— 把它们混成一件，产出的就是那个跳过。

本节其余部分就是第 5–9 步，以及它们为什么不能只是「一个字段加一个 sink」：问题出在 agentao **什么时候**判
权限。

`ToolPlanner.plan()` 为每个调用算出权限裁定并挂到 plan 上（`tool_planning.py:453` → `_decide`）。
`ToolRunner` 是在那**之后**、在 phase 1.5 才 dispatch `PreToolUse` hook 的（`tool_runner.py:194-203`）。
这个顺序是刻意的，而且在今天是安全的 —— 因为 hook 只能把裁定往一个方向推。
`_apply_pre_tool_use_hooks`（`tool_runner.py:255`）只能 deny 或降级成 ask，而它调用点上的注释就写明了
这种不对称：hook 的 `allow`「is a no-op — it never downgrades an engine deny/ask or a tool's own
`requires_confirmation` ask」（`tool_runner.py:200`）。hook 返回什么，都改不了那份裁定所依据的参数。

`updatedInput` 改的恰恰就是这个，而参考文档对它的作用范围写得很明白：*"Modifies the tool's input
parameters before execution. **Replaces the entire input object**, so include unchanged fields
alongside modified ones."* 一个把无害的 `Bash` 命令改写成 `rm -rf /` 的 hook，会把一条权限引擎从未见过的
命令交给 executor，而它携带的是按原参数算出来的 `ALLOW` —— 硬线 shell 扫描器（`permissions_hardline/`）
是跑在那份裁定**里面**的，不在它下游。把这个字段规划成「存起来 + 配个 sink」，等于把这个洞一起发出去。

上游没有这个问题，而且说了两遍：

- 关于 `permissionDecision` —— *"Deny and ask rules are still evaluated regardless of what the hook
  returns"*；
- 关于同族的 `PermissionRequest.updatedInput` —— *"The modified input is re-evaluated against deny
  and ask rules"*。

**这里曾挂着第三条引文、挂了四版，而它并不存在。** 那句写的是 *"Claude Code validates the updated input
against the tool schema and rejects it if it doesn't match, showing an error in the transcript"*，而对着
§3 盖了戳的那份快照，`grep -c "validates the updated input"` 返回 **0**。它来自一次在原始 `.md` 尚未存档之
前做的**摘要式**抓取。这里如实记下而不是悄悄删掉，是因为它可被重新推导出来：那句话恰恰是读者预期这一页会
说的。由它的不存在带出两件事：那个**校验步骤**（下面第 2 步）没有上游依据，是 agentao 自己的选择，如实标
注；而它的**失败分支** —— 原本由那句编造的话提供 —— 必须靠决定，而不是靠引用。

快照里真正存在的、最接近的一句在**输出侧**，而且不能拿来顶替：*"For built-in tools, a value that doesn't
match the tool's output schema is ignored and the original output is used. MCP tool output is passed
through without schema validation."* 那说的是 `updatedToolOutput`，仅限内置工具，而且那条通道上的「原值」
是一个**已经存在**的结果。把它横移到输入侧，等于**去执行 hook 正想替换掉的那条命令** —— 所以本计划不这么
做。

所以 `updatedInput` 不是一个待承载的字段，而是一次重新进入，本计划欠的是那套顺序。这是**门槛 G8**，卡第
6 步 —— 而且 G8 现在覆盖上面那完整的十步，不只是改写那一段：

1. **聚合。** 各匹配 handler 的改写按 §2.5 的顺序无关规则合并，平手按声明顺序裁决。两个 handler 对同一个
   调用给出不同改写，是一个现在就该定名的冲突、不是将来才发现的意外：本计划提议**拒绝该调用**并给诊断，
   因为一次被静默丢弃的改写，就是一个自以为已经消过毒的 hook。替代方案 —— 声明顺序里最后一个赢、或第一个
   赢 —— 更便宜，但都把那种自以为是留在原地。
2. **校验。** 用工具的参数 schema 校验合并后的输入。不匹配就：**拒绝该调用**，并发一条点名校验失败的
   `hook error` 用户通知。**不是**「拒绝这次改写、保留原输入」—— 那是本计划四个版本里的规则，依据是一句并
   不存在于参考文档的话（见上），而且抛开出处不谈，这个行为本身就不安全。hook 改写一个输入是有原因的；如果改写不可用，可
   选的结果只有两个：*执行 hook 已经否掉的那个*，或者*什么都不执行*，而只有后者能被本节关于「改写冲突」的
   同一套论证支持。**G8** 管这次翻案：去探 Claude Code 实际怎么做；如果它确实回退到原输入，那就把它作为一
   处**写明的、偏离安全侧的 profile 偏离**采纳，而不是让它当默认值。无论哪种，都绝不执行工具没有声明过的
   形状。
3. **重判。** 对改写后的参数重新跑一次 `_decide`。这是整个门槛的意义所在 —— 裁定必须是「实际会被执行的东
   西」的函数。
4. **取交集，绝不向上放宽。** 重判出的裁定与 hook 自己的 `permissionDecision` 取**更严**的那个。hook 的
   `allow` 抬不动一个重新算出来的 `DENY`；而按 `CLAUDE.md` 的三级优先级，只读模式预设更是谁都抬不动 ——
   它在引擎被查询之前就短路了。
5. **对改写后的输入重新确认。** Phase 2 的提示必须展示将要执行的东西，这也是参考文档自己给出的配对用法：
   *"Combine with `\"allow\"` to auto-approve, or `\"ask\"` to show the modified input to the user."*
   一个展示改写前参数的确认框，收上来的是对另一件事的同意。
6. **不要重新 dispatch。** 改写后的输入不再重新进入 `PreToolUse`。hook 对一次调用只看一次。

有两处邻接项从这里继承。`updatedToolOutput`（G2）需要第 2 步那种校验，而它**没有可校验的 schema** ——
见 §5.3 的 `tool_response` 行。以及参考文档 v2.1.199 的那条规则（§3）在上一层是同一个形状：有些工具调用，
hook 的 `allow` 根本无权自动放行。

---

## 5. 两张表

### 5.1 能力表 —— 以及它声明出来的输出 profile

`event × 字段或退出码 → accept | ignore | reject | block | feedback`，挨着
`SUPPORTED_HOOK_TYPES_BY_EVENT`（`models.py:217`）放 —— 那本来就是一张 event×能力表，而且它的 docstring
里已经写明了正确的纪律：一条「解析时判为支持、dispatch 时被静默丢弃」的规则必须**以解析器警告的形式暴露
出来**。新表，同一条纪律。

两个 peer 都是这个结构 —— 参考文档是「通用字段 + 逐事件例外」，codex 自己的参考文档同样如此。对照文档
§9.2 把这条记成了一条长期方法教训：全局表不是契约，逐事件的那一节才是。

**这次清扫。** 这份设计花了七版、每轮评审补一个字段，于是 §1 承诺了一份没有人列举过的契约。下面是参考文档
为 agentao 那八个事件定义的**全部**输出面，一次扫完。这一扫带出九行，而其中只有四行是有人点过名的 —— 这个
差额就是「要清扫、不要打补丁」的论据，也是「新字段先进这张表、再进别处」的理由。

| 字段 | 事件 | 上游含义 | profile-1 |
|---|---|---|---|
| `continue` / `stopReason` | 通用，**但有逐事件例外** | 停止处理；给用户的消息 | **在认它的事件上 accept** —— 见下面那张矩阵 |
| `systemMessage` | 通用，**但有逐事件例外** | 给**用户**的警告 | **在认它的事件上 accept** → `user_notices`（§4.3）；这个字段的逐事件例外最容易漏，因为参考文档只在两个事件的小节里写了它 |
| `suppressOutput` | 通用 | 文档明写 **inert** | profile-1 里 `ignore`；`agentao-v1` 里仍生效（§11 第 1 问） |
| `terminalSequence` | 通用 | 由 Claude Code 代 hook 发出的 OSC/BEL 序列 —— 限 OSC `0`/`1`/`2`/`9`/`99`/`777` 与 BEL，出现别的就整条忽略 | `ignore` —— agentao 的 CLI 没有一条归 hook 用的终端写入通路，而那份 allowlist 是一条安全边界，本计划不会闭着眼睛实现它。列出来、不静默；**G7** 可以把它翻成 accept，因为它要的传输通道正是 `user_notices` 需要的那条（G1） |
| `hookSpecificOutput.hookEventName` | 凡是用到 `hSO` 的地方 | *"It requires a `hookEventName` field set to the event name"* —— 整个嵌套对象的**判别字段** | **accept，而且它是唯一一个「取值」可以正当地校验失败的输出字段。** 缺失或对不上 ⇒ **整个对象** `schema_invalid`，顶层字段一并作废。「而顶层字段照常生效」是最诱人的那种放宽，它既与 resolver 矛盾（`parse_stdout` 在非 `valid` 时返回 `parsed=None`，`absorb_channels` 只在 `valid` 时跑，§4.2），也与参考文档矛盾 —— 后者是按整个对象描述校验的（*"a parsed object that fails schema validation"*）。「部分有效」是一个自洽的替代方案，但那是一处 **profile 偏离**、需要在本表里单独占一行。清扫时漏掉这个字段，会让解析器去读一个写给别的事件的 `hSO` 块 |
| `hSO.additionalContext` | 8 中 6 | 给模型的 context | **accept**（§4.1） |
| `decision` / `reason` | UPS、PostToolUse、Stop、PreCompact、**PostToolUseFailure** | 阻断 + 理由 | 五个**全部 accept** —— 但 `"block"` 在两个 Post* 事件上都不表示*停止*。`PostToolUse`：*"adds the `reason` next to the tool result. Claude still sees the original output"*（`hooks.md:1933`）。`PostToolUseFailure`：**实测**为同一形状 —— reason 进模型、原始错误保留、turn 继续（`docs/reference/hooks-probe-2.1.251.zh.md` §C）。两者都是反馈通道；只有 `continue:false` 才停 |
| `permissionDecision` / `permissionDecisionReason` | PreToolUse | allow/deny/ask/defer | 字段 **accept**；**取值**各自带处置（§1）：`allow` / `deny` / `ask` 接受，**`defer` 降级为 `deny`**，并在 `permissionDecisionReason` 里点名这个未实现的取值，外加每（规则，字段）一条诊断。不是「带理由拒绝」，那违反 §1 第三条规则 —— 而且运行时本来也兑现不了：上游的 `defer` 是 *"exits gracefully so the tool can be resumed later"*，一整套可恢复生命周期（会话留在磁盘上等待恢复，hook 还能再 defer 一次），这边没有对应物 —— agentao 既没有地方停放待决调用，也没有 `tool_deferred` 结果。`deny` 是保守的降级：工具不跑，而且模型被告知原因。替代方案是 `ask`，它更接近原意但在非交互运行里不可用；**G7** 选一个。降级发生在 `resolve()` 里，所以 §5.4 那张格只会看到 `allow` / `deny` / `ask` |
| `updatedInput` | PreToolUse | 替换整个输入对象 | **accept**，经 §4.4 的重新进入（G8） |
| `updatedToolOutput` | PostToolUse | 替换 tool result；*"must match the tool's output shape"* | **accept，待 G2** —— agentao 没有工具输出 schema（§5.3） |
| `updatedMCPToolOutput` | PostToolUse | 只作用于 MCP 工具的变体；参考文档自己说优先用 `updatedToolOutput` | `ignore` —— 一个字段的第二种拼法，而第一种拼法本身已经卡在 G2 上 |
| `classifierContext` | PostToolUse | 写给 **auto 模式分类器**的短注记，不是给模型的；按调用聚合封顶 2,000 字符；v2.1.236+ | `ignore` —— agentao 没有 auto 模式分类器，压根没有消费者可路由。这一行正好演示 profile 的作用：字段合法、这里实现不了，而且绝不能变成一条 `hook error` |
| `sessionTitle` | SessionStart、UserPromptSubmit | 设置会话标题，等同 `/rename` | 暂 `ignore` —— agentao 的会话有 id、没有标题（`embedding/sessions.py`）；加标题是产品决定，不是合规修复。**G7** 记录它 |
| `reloadSkills` | SessionStart | SessionStart hook 跑完后重新扫描 skill 目录 | **profile-1 里 `ignore`。** 标成 `accept` 看着很对 —— 毕竟有 `SkillManager`、也有 `reload_skills()` —— 而它建立在两个错误前提上。(1) **sink 不等价。** `SkillManager` 扫的是 `~/.agentao/skills`、`<cwd>/.agentao/skills` 与内置树（`skills/manager.py:25,35,110`）；而 Claude 作者写的 hook 装进的是 `.claude/skills` 与命令目录，于是接受这个字段等于去重扫一棵那个 hook 从没写过的树、还报告成功 —— 一处静默的语义偏离，正是 §1 存在要防的。(2) **那条路径上没有锁。** `reload_skills()`（`:480`）不加锁；CLAUDE.md 说的 `filelock` 在 `skills/registry.py:66-75`，是另一个组件，所以「重扫与 hook 还在写文件并发」这件事没有定义好的结果。诊断会点名目录不匹配，好让作者不用猜。**G7** 管两条出路：让发现逻辑认识 `.claude/skills` 树然后接受，或者接受并写明扫的是另一棵树 |
| `initialUserMessage` | SessionStart | 在 `-p` 下成为会话的第一个 turn | profile-1 里 `ignore`；它要在 spec 的 prompt 之前往 `agentao run` 里插一个 turn，那是 `run.py` 流水线的改动、自带一套顺序问题（§5.2，而且它落在 G1 那个问题的旁边） |
| `watchPaths` | SessionStart | 供 `FileChanged` 监视的路径 | `ignore` —— `FileChanged` 不在 agentao 的八个事件里，接受它等于什么都没武装。**不是「解析期拒绝」**（§1 第三条规则）：它是 stdout 字段、配置解析器看不到，而丢掉结果会把紧挨着的 `systemMessage` 一起带走 |
| `suppressOriginalPrompt` | UserPromptSubmit | 阻断消息里不带原始 prompt 文本 | **profile-1 里 `ignore`。** 接受它的理由 ——「否则会泄漏作者要求隐藏的 prompt」—— 漏了一件事：**agentao 的阻断消息里本来就没有 prompt** —— 它是 `f"[Blocked by hook] {blocking_error}"`（`_hook_dispatch.py:73`）。取 `true` 时可观察结果本来就一致；只有 `false` 才有差别，而那是针对一条并不存在的消息。要兑现这个字段，得**先**把 prompt 加进阻断消息、好让这个开关有东西可关 —— 那是为了支持一个开关而改动今天面向用户的输出。**G7** 可以走那条路；profile-1 不走。顺带堵住一个坑：最直觉的那个测试（「`true` 时断言消息里没有 prompt」）今天**在完全不解析这个字段的情况下也会通过** |

**「通用」并不通用。** 参考文档在引入这些字段的同一句里就说了：*"Every event accepts them, but some events
discard them or deliver `systemMessage` somewhere other than the transcript. Each event's section says
so."* agentao 的八个事件里有两个是被点名的例外 —— 所以把这两个字段一律标成 `accept`，会把一个 `PreCompact`
hook 的 `systemMessage` 送到参考文档说根本看不到它的用户那里。一个只出现在散文里的谓词不是机制，这张表才
是：

| 事件 | `continue` | `stopReason` | `systemMessage` | `suppressOutput` | `terminalSequence` |
|---|---|---|---|---|---|
| `SessionStart` | **discarded —— 实测**（见下） | **丢弃** | 认 | 不适用 —— 已 ignore | 不适用 —— 已 ignore |
| `UserPromptSubmit` | 认 | 认 | 认 | 不适用 —— 已 ignore | 不适用 —— 已 ignore |
| `PreToolUse` | 认 | 认 | 认 | 不适用 —— 已 ignore | 不适用 —— 已 ignore |
| `PostToolUse` | 认 | 认 | 认 | 不适用 —— 已 ignore | 不适用 —— 已 ignore |
| `PostToolUseFailure` | 认 | 认 | 认 | 不适用 —— 已 ignore | 不适用 —— 已 ignore |
| `Stop` | 认 | 认 | 认 | 不适用 —— 已 ignore（`agentao-v1` 里仍生效 —— §11 第 1 问） | 不适用 —— 已 ignore |
| `PreCompact` | **丢弃** | **丢弃** | **丢弃** | 不适用 —— 已 ignore | 不适用 —— 已 ignore |
| `SessionEnd` | **丢弃** | **丢弃** | **丢弃** | 不适用 —— 已 ignore | 不适用 —— 已 ignore |

*"Claude Code discards a PreCompact hook's `systemMessage` and `continue` fields"*；`SessionEnd`
*"hooks have no decision control … Claude Code discards their JSON output fields, such as
`systemMessage`"*。

**`SessionStart` 是第三行存疑的，而找到它顺带改掉了本节自己写下的一条规则。** 余下六个事件里有五个的小节
没有任何例外陈述，所以它们继承全局那一行。`SessionStart` 的小节同样没有 —— 但它被点名写在**第三张**全局表
里，而通用字段那条规则和它自己的小节都没有指向那张表：

> *"| SessionStart, SubagentStart | **Context only** | `hookSpecificOutput.additionalContext` adds
> context for Claude. SessionStart also accepts `initialUserMessage`, `watchPaths`, `sessionTitle`,
> and `reloadSkills`. **No blocking or decision control**"*（`hooks.md:1009`）

这一行对 `continue` 是决定性的、对 `systemMessage` 则只字未提，差别在这张表自己的分类法里：`continue:
false` **就是**它的决策模式之一 —— `TeammateIdle, TaskCompleted` 那一行的模式原文就是 *"Exit code or
`continue: false`"*，而 `TaskCreated` 那一行写的是 *"`continue: false` is ignored"*。`systemMessage` 从头到
尾没出现在这张表里；它是用户通知、不是决策控制，它的例外住在逐事件小节里（`hooks.md:717`）。

与之相对的是一个真实的反向信号，这也是这一行标**存疑**而不是定案的原因：**其它每一个丢弃 `continue` 的事
件都在自己的小节里说了**，一共十五处，其中还包括那些*同样*落在这张表「无决策控制」行里的事件 ——
`SessionEnd`（`:3029`）、`Setup`（`:1227`）、`InstructionsLoaded`（`:1264`）、`Notification`（`:2249`）、
`PostCompact`（`:2973`）。上游每次都写两句，唯独这里没有。所以要么 `SessionStart` 真的认一个没人记录过的
停止，要么是它的小节漏了十四个邻居都有的那句话。

**profile-1 取窄读法 —— `SessionStart` 上 `continue:false` 是 `discarded`** —— 当初依据的是 §11 第 9 问
那条代价不对称的论证（认一个没有文档依据的停止，等于让 hook 能拒绝启动一个上游本会启动的会话；不认一个没人
要求过的停止，代价为零），此后**已被实测证实**：一个打印 `{"continue": false, "stopReason": …}` 的
`SessionStart` hook 跑完之后，会话照常开始、turn 跑完，输出里哪儿都没有那个 reason
（`docs/reference/hooks-probe-2.1.251.zh.md` §B、§0）。所以「别处写了十五次」的那句话确实是这个事件的小节漏
了，起作用的是 Decision-control 那一行。该行不再存疑；仍未测的是 `systemMessage` 在这里是否**也**被丢弃 ——
探测用的传输面看不到它，§5.1 的矩阵按参考文档自己的措辞保留 `honored`。

用 `discarded` 这个词是精确的，它顺带定下一件本来会悬着的事：**窄读法这一支是静默的，不出诊断。** 诊断属
于 `ignore` 那条轴，报告的是 *agentao* 的能力缺口；而 `continue` 在字段表里是 `accept`、在另外五个事件上有
消费者，所以它在这里的缺席是一次逐事件的投递结果，适用投递轴的静默规则。**存疑**并不改变这一点 —— 出诊断
会对那些「按 agentao 没取的那种读法本来正确」的 hook 报警，正是静默规则要避免的「给正确代码打标」。存疑的
行要不要另给一次性提示，是 **G7** 的一个子问题，而不是在这里把两条轴混起来的许可。

**一行存疑的，欠一张翻案清单。** 两行存疑的都是**逆着**参考文档更宽的那句话定的，而探测可以把任一行翻过
来。只记一句「把断言反过来」，正是上一版把 `SessionStart` 那条路由规划好、又删掉、却没留下任何「怎么装回
去」的原因。所以每一行存疑的都要一次性列出：探测若走向另一边，哪些小节要一起改 —— `SessionStart` 的列在下
面的 **G7** 里，`PostToolUseFailure` 的列在 §5.4 那张格与 §12 里。

**这条规则的代价。** 本节早先的版本说过：对 `continue` 而言逐事件的沉默**就是**继承，理由是「全局表已经断
言该字段适用」。全局表有**三张** —— JSON 输出字段（`:904`）、Decision control（`:1005`）、以及逐事件小节
—— 而那条规则是从其中两张推出来的。更正后的形式：**一个事件继承某个通用字段，前提是没有任何一张全局表把
它排除在外。** Decision-control 表把 `SessionStart` 排除在决策控制之外，而 `continue` 正是它枚举的模式
之一。

对代码有两个后果。`absorb_channels` 必须像 `resolve()` 处理 `continue` 那样去查表处理 `systemMessage` ——
各一个谓词，`honors_continue(event)` 与 `honors_system_message(event)`，都由上面这张矩阵供给，而不是写死在
resolver 里。以及 `terminalSequence` 是唯一一个在「丢弃事件」上仍然生效的字段（*"the field works on events
that discard `systemMessage` and `continue`"*）—— 尽管 profile-1 忽略它，这一点仍值得记下：如果 G7 将来接
受它，它**不**继承上面这两行的例外。

**是两条轴，不是四个取值。** 上面那些格子里有四个词（`honored`、`discarded`，以及两种「不适用」），而
§1 与本节都说这个模型只有**两个**取值。它们不是四种处置，是两条轴，而这张矩阵从头到尾只在变其中一条：

- **profile 处置** —— `accept` 或 `ignore`，在上面那张字段表里一次定死，不逐事件变。`ignore` 会按每
  （规则，字段）产出一条诊断（§4.2）。`suppressOutput` 与 `terminalSequence` 在 profile-1 里是 `ignore`，
  这就是它们那两列写「不适用」的原因：投递这条轴根本轮不到跑，它们的诊断来自字段表、不来自这里。
  （`suppressOutput` **另外**还被上游写明是 inert —— 那是关于参考文档的事实，不是第三种处置；它在
  `agentao-v1` 里仍然生效，§11 第 1 问。`terminalSequence` 则是 G7 可能翻转的那一行，一旦翻转，它在**八个
  事件上全部** `honored`，例外行也不例外。）
- **投递** —— `honored` 或 `discarded`，而且**只对被 accept 的字段成立**。这条轴才是这张矩阵存在的理由。

**丢弃是静默的 —— 不出诊断。** 这个 hook 对上游是合规的：同样的输出在 Claude Code 上也什么都不做，所以
在这里出诊断等于给正确的代码打标。一次性诊断注册表（§4.2、G10）最受不了的恰恰是「被训练成可以无视」。
`ignore` 是相反的情形 —— 它报告的是 agentao 的能力缺口，所以要报一次。§12 把**两个方向**都钉住，因为
「不出诊断」正是那种实现漂移了却什么都测不出来的断言。

注意**字段表**最后一列**不是**重要性排序。它只有**两个**取值（§1 第三条规则）：`accept` 指有消费者的字段，`ignore`
指字段被解析、不起作用、并按每（规则，字段）产生一条诊断（§4.2）。两者都永远不会变成给用户看的
`hook error`，因为它们都不是 hook 作者的错。`reject` 留在 §2.4 —— 在那里，一条规则还可以在任何东西开跑
之前被拒掉。

**唯一那一行存疑的。** `PostToolUseFailure` 的逐事件小节确实存在，也确实只列了 `additionalContext` ——
但**全局** Decision-control 表把这个事件点名列了进去：

> *"UserPromptSubmit, UserPromptExpansion, PostToolUse, **PostToolUseFailure**, PostToolBatch, Stop,
> SubagentStop, ConfigChange, PreCompact | Top-level `decision` | `decision: "block"`, `reason`."*
> （`hooks.md:999`）

同一页上关于同一个字段的两条规范性陈述。**并没有第三个数据点**，而最像是的那个恰恰无效：
`PostToolUseFailure` 的 exit-2 行（*"Shows stderr to Claude; the tool already failed"*）读起来像支持窄读
法，可 `PostToolUse` 的 exit-2 行是逐字同形的（*"Shows stderr to Claude; the tool already ran"*，
`hooks.md:854-855`），而那个事件**确实**支持 `decision:"block"`。exit-2 stderr 是一条独立的反馈通道，对
「支不支持 decision」两个方向都没有信息量 —— 值得写出来，因为那正是读者会去找平局裁决的第一个地方。这是这
份快照产出的第二处自相矛盾；第一处是 `sh -c` 与 `shell` 默认 `"bash"`（§2.4），撤回进 G5 而不是从文档里硬
判。这里适用同一个答案，而那条长期方法规则需要补上这一例揭示的限定词：

> **逐事件小节在它「说了不一样的话」时才覆盖全局表。逐事件小节的沉默不是覆盖。**

`PostToolUse` 的小节是显式收窄了全局那行；`PostToolUseFailure` 的小节只是没提这个字段，而「没提」既可以解
释成「继承全局行」，也可以解释成「没有这个能力」。

**而且就算取宽读法，它也不告诉你 `block` 在那里*做什么* —— 那一行钉的是形状，不是语义。** 该行成员的效果
彼此互不相容，而且可以逐条核实：在 `UserPromptSubmit` 上，block 是 *"blocks prompt processing and erases
the prompt"*；在 `Stop` 上是 *"prevents Claude from stopping, continues the conversation"*；在 `PreCompact`
上是 *"blocks compaction"*（`hooks.md:845,847,866`）；而在 `PostToolUse` 上是 *"adds the `reason` next to
the tool result. Claude still sees the original output"*（`:1933`）—— 加注并继续。**该行九个事件里的四个，
四种互不相容的结果** —— 另外五个在这里根本没核过，这反而让论点更强而不是更弱。所以
「在这一行里」只说明 wire 形式是顶层 `decision` / `reason` 这一对，对**理由送到哪里、原始失败是否保留、这
一轮是否继续**一个字都没说。而 `PostToolUseFailure` 自己那一节只定义了 `additionalContext`、根本没有
`decision`（`:2043-2046`），所以也没有第二个来源可以读出效果。

**G7 探的是四件事、不是一件，而四件都有了答案**（`docs/reference/hooks-probe-2.1.251.zh.md` §C、§0）：
(1) `decision` **确实**被认；(2) `reason` 以带标签的独立一行到达**模型**；(3) 其前**保留原始错误**；
(4) **turn 继续**。所以 `hooks.md:999` 的宽读法在这个事件上是对的，本计划坚持了七版的窄读法**被推翻** ——
profile-1 认这个 `decision`，作为反馈。

**这些答案不许是什么，以及让它们成为证据的那个对照组。** (2)–(4) 不得从 `PostToolUse` 预填，所以是测出来
的；它们测回来*与* `PostToolUse` 相同，是一个结果，而不是翻案清单所禁止的那个假设。另外，同一事件用一个
无法识别的键做的对照运行显示它到达模型 **0** 次 —— 正是这一点把「该字段被认」和「hook 的 stdout 会在模型
那儿回显」分开；没有它，这条发现测的就是另一套机制。

**唯一从来就不冲突的一点。** 该事件**自己**的 exit-2 行写着它**不能阻断** —— *"Shows stderr to Claude;
the tool already failed"*，而那张表的开场就是「有些事件代表的是*已经发生、或无法阻止*的事情」
（`:838,855`）。测量与它一致：这里什么也没被阻止，因为 `block` 在这里是加注、不是停止。但把那一行当作对
(1) 的回答，仍然是 §5.1 撤回过一次的那种推理 —— 它约束的是*效果*，不是「认不认」。

§4.1 的 `BlockDecision` 继续列着 `PostToolUseFailure`，现在对应的是 profile **确实**兑现的一个 decision；
「解析层比处置层宽」这条分离在 `defer` 上依然值回票价。

**这张表里每一个 `accept` 都欠三样东西**：`ParsedHookOutput` 上的一个字段（§4.1）、消费者表里的一行
（§5.2）、以及多个 handler 都设置它时的聚合规则。本计划发出去过一个三样都没有的 `accept`，还有一行停在「建
议」——那压根不是处置。以后每当有一行从 `ignore` 变成 `accept`，就把这三样当检查表 —— 而且只要一个字段的
*状态*发生任何变化就要重跑一遍，§5.2.2 那个缺口正是这样熬过了一整版。而按 §11 第 4 问，那本身就是一次
profile 升版。

### 5.2 event × 输出 → 运行时消费者表

能力表说的是什么被*接受*。它并不会凭空造出一个地方来放这个值。八个事件里有三个走
`_dispatch_lifecycle`（`_dispatcher.py:267`）—— 纯副作用、只返回 attachment，根本没有结果对象，也就没有
东西可消费。三个都很容易漏掉，因为 dispatcher 的调用点看上去都是完整的。

| 事件 | 参考文档定义的输出 | 今天的 sink | 需要补 |
|---|---|---|---|
| `SessionStart` | 纯 stdout（**仅 exit 0**）与 `hSO.additionalContext` → 模型 context；exit-2 的 stderr → **用户**；`continue:false` **不认**（§5.1 —— `hooks.md:1009`）；`initialUserMessage`、`sessionTitle`、`watchPaths`、`reloadSkills` 在 profile-1 里一律**忽略**（§5.1） | **无** —— `_dispatch_lifecycle`，且 `cli/session.py:81` 把 dispatcher 返回值丢掉了 | 模型 context 注入**以及**一条用户通知 sink。`_dispatch_lifecycle` 只返回 attachment（`_dispatcher.py:66,267-288`），所以消费这两者仍然需要一个返回值 —— 但**不需要控制结果**：profile-1 在这里不认停止。没有重扫 sink：`reloadSkills` 被忽略，profile-1 里没有任何东西路由到 `SkillManager` |
| `SessionEnd` | JSON 输出被丢弃（**agentao 在这一半是合规的**）—— 但 exit-2 的 stderr → **用户** | **两个界面上都没有** —— `cli/session.py:87` 把返回值丢掉，而 `agentao run` 在 dispatch 之前就已经把结果输出完了（`run.py:814,815`） | 一条用户通知 sink，**并且每个界面各有一条路由**（§5.2.1）。「什么都不用补、已经合规」只对 *JSON* 那一半成立 |
| `UserPromptSubmit` | `decision:"block"`+`reason`、`hSO.additionalContext`、`continue`、exit 2；`suppressOriginalPrompt` 在 profile-1 里**忽略**（§5.1） | 部分（`_hook_dispatch.py`） | 接上缺的三条通道。**`suppressOriginalPrompt` 没有路由** —— 解析并给诊断，没有任何东西消费它，因为 agentao 的阻断消息里本来就没有 prompt 可关 —— 给一个已 `ignore` 的字段留着「要加路由」，正是这里要防的漂移 |
| `PreToolUse` | `permissionDecision`、`updatedInput`、`hSO.additionalContext`、**`continue:false`** | decision 有；context 解析后只写日志 | `tool_contexts` sink；`updated_tool_input` **外加那套重判顺序** —— sink 是其中小的那一半（§4.4，G8）。还有 turn 级的停止路由（§5.2.2），它**不是**权限裁定：`continue:false` 结束整个 turn，`deny` 只挡一次调用 |
| `PostToolUse` | `decision:"block"`+`reason`（**是反馈、不是停止** —— 见下）、`hSO.additionalContext`、`updatedToolOutput`、exit 2 → 反馈、**`continue:false`**（真正的停止） | **无**（`_dispatch_lifecycle`，`_dispatcher.py:120`） | 结果对象 + 拼接进 tool result —— 外加一个决定：`updatedToolOutput` 到底替换什么，因为 agentao 的工具输出是字符串、没有 schema（§5.3）。**是两个 sink 不是一个：** `decision:"block"` 把 `reason` 附在被保留的结果旁边、这一轮继续；`continue:false` 结束这一轮（§5.2.2） |
| `PostToolUseFailure` | `hSO.additionalContext`、exit-2 stderr → 模型、**`continue:false`**，以及 **`decision:"block"` → 模型反馈**（实测） | **无**（`_dispatch_lifecycle`） | 结果对象 + 模型反馈；它**无条件**携带一个 turn 级 `Stop`（通用字段行，§5.1）。`decision` 的落脚点由实测确定、而不是从 `PostToolUse` 继承：`reason` 以独立一行到达**模型**，其前**保留原始错误**，且 **turn 继续**（`docs/reference/hooks-probe-2.1.251.zh.md` §C） |
| `Stop` | `decision`、`hSO.additionalContext`、`continue`、exit 2 | 基本齐 | `user_notices` 消费者；由 `hSO` 触发的 continuation |
| `PreCompact` | exit 2、顶层 `decision:"block"` | agentao 自创拼法 | 参考文档的拼法（§3.3） |

`SessionEnd` 这一行值得读两遍。它的 *JSON* 那一半确实合规 —— 参考文档给该事件的是
无决策控制、并丢弃其 JSON 输出，所以 agentao 的纯副作用路径是对的，「给每个 lifecycle 事件都补个结果对
象」在这里会错。但 exit 2 是与 JSON 并列的另一条通道，而在 `SessionEnd` 上它意味着 *stderr 展示给用户*
（§4.2）。agentao 没有承接它的 sink：`dispatch_plugin_session_start` 与 `dispatch_plugin_session_end`
都在一个裸的 `try/except: pass` 里把 dispatcher 的返回值扔了（`cli/session.py:81,87`），所以就算
dispatcher 产出了它，下游也没有东西能消费。

§5.3 是这张表在输入侧的孪生。

本计划的立场：现有的 `logger.warning` **不是**用户通道 —— 它不是用户在正常会话里看得见的界面，而把一条
日志当成契约 sink，正是当初 `systemMessage` 送错通道的成因（§4.3）。它需要一条真正的 sink，而且就是
`user_notices` 需要的那一条，因此归 **G1**，并在第 4 步与其他 lifecycle sink 一起落地。

#### 5.2.1 有 sink 不等于有路由

G1 决定的是**传输形状** —— 新事件类型，还是扩展 `PLUGIN_HOOK_FIRED` 的 payload。这是必要的，
但不够 —— 因为在最需要 hook 通知的那个界面上，等 `SessionEnd` 真正触发时，已经没有任何东西能承载它了：

```
run.py:770   agent.remove_event_observer(_on_event)   # 观察者已摘掉
run.py:771   transport_unsubscribe()
   …
run.py:814   _emit(result, output_format)             # 整个 run 的输出在这里写出
run.py:815   dispatch_plugin_session_end(...)         # ……然后 SessionEnd 才跑
```

基于事件的传输一到就是死的（没有任何订阅者）；基于返回值的传输则在唯一会打印的那一步之后才拿到东西。于
是一个 `SessionEnd` hook 的 exit-2 stderr —— §4.2 明确要求它送达用户 —— 到 `agentao run` 用户那里**根本
没有路**。交互式界面是同一个形状、早一步发生：`dispatch_plugin_session_end` 在一个裸的 `try/except: pass`
里把返回值扔了（`cli/session.py:87`）。

**所以 G1 欠的是「每个界面一条路由」，不只是一种形状**，而 headless 那条的 wire 形式参考文档已经给了：
`systemMessage` 在 `--output-format stream-json` 下 *"can arrive as an `SDKInformationalMessage`"*。本计划
的提案：

- **`agentao run`：** 把 `SessionEnd` 的 dispatch 挪到 `_emit` **之前**，并把它的通知挂到 `RunResult`
  上 —— `warnings[]` 本来就存在、本来就会被序列化（`run.py:812`），于是这是「两行挪位 + 一个字段」，可以在
  `_run_pipeline` 这一层测。会话反正是要结束的；变的只是通知有没有和它所属的那份结果一起输出。
- **交互式 CLI：** 在 `cli/session.py:87` 消费 dispatcher 的返回值，再走 G1 为 `user_notices` 选定的那条
  通道渲染。
- 无论选哪种，测试都必须是端到端的（§12）—— resolver 级的测试会通过，而特性并不存在。

同一个顺序问题也管着 `initialUserMessage`（§5.1）：它必须落在第一个 turn 构造**之前**，而不是之后 ——
那是这个缺陷在 `SessionStart` 那一侧的镜像。

#### 5.2.2 「停止」也是一条路由

§5.1 那张矩阵说八个事件里有**五个**认 `continue: false`。上面那张表只给其中两个配了路由 ——
`UserPromptSubmit` 与 `Stop`。这个缺口在本计划里熬过了一整版，原因值得记下来：字段和聚合规则都在
（`ParsedHookOutput` 上的 `continue_processing` / `stop_reason`，§4.1；§5.4 那张格的第 1 档），所以 §5.1
那条「每个 `accept` 都欠三样东西」的检查表，对任何没有在该字段从散文搬进表格之后重跑一遍的人来说，看上去
都是满足的。缺的是三个事件上的消费者。

**机械的那一半。** `dispatch_post_tool_use` 与 `dispatch_post_tool_use_failure` 从
`_dispatch_lifecycle` 返回 `list[HookAttachmentRecord]`（`_dispatcher.py:126,134,267-288`），于是
`resolve()` 算出来的 `ResolvedHookOutput.control = Stop(reason)` 在调用点就被丢掉。`PreToolUse` 是第三
个，而它不一样：`dispatch_pre_tool_use_decision` **确实**返回结果对象（`PreToolUseHookResult`），那里缺的
是一个控制**分支**，不是一个类型。（`SessionStart` 本来也在这份名单上，直到 profile-1 对 `hooks.md:1009`
取了窄读法 —— §5.1。它仍然需要有人消费它的返回值，用于 exit-2 用户通知和 `additionalContext`，但不用于控
制。）

**语义的那一半**，这一半没有哪个门槛能丢给实现者，因为 *"stops processing entirely"* 在会话的不同位置意思
并不一样：

| 事件 | `continue:false` 停掉的是什么 | 界面上的行为 |
|---|---|---|
| `UserPromptSubmit` | 这一轮，在第一次模型调用之前 | 已经是第 5 步的通道；现成的提前返回路径就是它的形状（`_hook_dispatch.py:75`） |
| `PreToolUse` | **整轮**，不只是这次调用 | 这是最容易被悄悄实现成 `deny` 的那一个 —— 见下 |
| `PostToolUse`、`PostToolUseFailure` | 这一轮，在 tool 结果已经记录之后 | 工具已经跑完了，所以这是停止、不是回滚 —— 而且它要跨过三层调用栈才能被人接住 |
| `Stop` | 这一轮 | 今天就有（`_runner.py:964-981`） |
| `SessionStart`、`PreCompact`、`SessionEnd` | 什么都不停 —— 不认／丢弃（§5.1） | 没有路由，而这是合规、不是缺口 |

**停止不是 deny。** 在 `PreToolUse` 上，它们是 §4.1 那个 `control` union 里不同的分支、对用户是不同的结果
—— 一个结束整轮，另一个挡下一次调用、让模型换个办法 —— 所以「反正那里已经有个裁定字段了」就把
`continue:false` 折进权限裁定，正是 §1 存在着要防的那种语义偏离。

**而 `PostToolUse` 的 `decision:"block"` 同样不是停止。** 参考文档原文：*"`"block"` adds the `reason` next
to the tool result. Claude still sees the original output; to replace it, use `updatedToolOutput`"*
（`hooks.md:1933`）。它是一条**反馈**通道 —— 原始结果被保留、理由附在旁边、这一轮继续走向下一次模型调
用。只有 `continue:false` 结束这一轮。因此这两者需要两个 sink、两个测试（§12），而「block」这个词本身就是
坑：它读起来像*阻止*，实际是*加注*。

##### `PostToolUse` 的停止要跨三层调用栈 —— 还有一条不能破的不变式

这一段正是「给这个事件补个结果类型」那种门槛盖不住的部分。`PostToolUse` 与 `PostToolUseFailure` 的 hook 是
**在 tool worker 内部**触发的：`execute_batch` 把 plan 跑在一个 8 worker 的池上（`tool_executor.py:189`），
每个 worker 自己 dispatch 自己的 hook（`:462`）。再往上，`ToolRunner.execute` 只返回
`(doom_triggered: bool, result_messages: list)`（`tool_runner.py:238,249`），而 chat loop 读的正好就是这两
个值（`_runner.py:773`）。一个在 worker 里产生的 `Stop`，要爬三层，而三层里没有任何一层有通道。

所以 **G2** 欠的是四个决定，不是一个结果类型：

1. **汇聚路径。** `ToolExecutionResult` 上的 `Stop` → 由 `execute_batch` 收集 → 由 `ToolRunner.execute`
   暴露（加第三个返回值，或用一个结果对象取代这个 tuple）→ 由 chat loop 在 `doom_triggered` 旁边处理。
2. **同批次里的兄弟调用 —— 以及它们真实所处的状态。** 跑完的只有**触发 hook 的那一个**。`execute_batch`
   把**全部** plan 一次性提交给一个 8 worker 的池，而每个 worker 在自己那个工具返回的当下就地 dispatch 自
   己的 hook（`tool_executor.py:189-200,462-470`），所以某个 `PostToolUse` hook 在跑的时候，最多有七个兄弟
   正在执行中，超过八个调用时还有别的**排在队里**。「工具早就跑完了」只对其中一个成立，拿它当政策依据是错
   的。本计划仍然提案**让这一批跑完再停**，但依据换成站得住的那条：它是唯一能在不加任何新机制的前提下保住
   下面那条不变式的选项。另一条路 —— 取消排队中的、并中断正在跑的兄弟调用 —— 既要接上取消链路，**又**要为
   每一个被取消的 plan 合成结果。**上游到底怎么做是未知的**，所以 G2 要么去探，要么把这个选择声明成一处写
   明的 profile 偏离；它唯一不能做的，是把这个省事选项说成是被迫的。
3. **两条路都不许破的不变式。** `format_batch` 逐 plan 产出且只产出一条 tool 消息，并直接按
   `exec_results[plan.tool_call_id]` 取值（`tool_result_formatter.py:113-128`）。一个没有结果条目的 plan
   会 `KeyError`；一个没有消息的 plan 会让一条带 `tool_calls` 的 assistant 消息没有对应的 `role:"tool"`
   应答 —— 严格的 API 会直接拒。**无论停不停，每个 plan 都仍然产出一个结果和一条消息。**
4. **多个工具同时停时，呈现哪个 `stopReason`。** 按 **plan 顺序** —— 也就是 `_plans` 里的顺序、模型自己
   给出的 tool-call 顺序 —— 绝不按完成顺序，与 §5.4 的声明顺序裁决和 §2.5 的确定性规则一致。

**headless 的退出码**是一个决定、不是细节：`agentao run` 公布了一张固定的表（`0` 正常、`1` 运行时、`2` 用
法、`3` 权限/交互、`4` 迭代上限、`130` 被中断 —— `CLAUDE.md`「Running」），每一档都已经有 CI 脚本在上面分
支。而 hook 在轮内发起的停止，和任何别的提前返回一样结束这一轮，因此走普通的 turn-outcome 映射、不需要自
己的码；**G2** 确认这一点 —— 这也是本节在 `SessionStart` 退出之后唯一变简单的地方。

### 5.3 输入字段来源矩阵

第 1 条偏离（§7）在第 3 步里只是一格：「Claude 输入序列化」。这一格背后是对照文档那个三层结论 —— 信封
6/8、事件专属字段 7/8、通用字段 8/8，**没有任何一个事件端到端合规**
（`hooks-three-way-claude-codex-agentao.zh.md` §5.9）。把信封拍平、把键改名，是容易的那一层，也是唯一
容易估价的那一层。难的那一层是：有若干字段**根本没有值可序列化**，而一份计划不能承诺一个它填不出来的
payload。

**矩阵是逐事件的，不是逐字段的**，每一格都是 **required** / **conditional** / **forbidden** 三者之一。以
字段为行、带一列「事件」的表会藏住两样东西：一个漏了事件的事件清单（参考文档的 `Stop` 输入同样带
`permission_mode`，而那里恰恰是 agentao 硬编码不在枚举里的 `"workspace-write"` 的地方，`_payload.py:144`），
以及 *forbidden* 本身 —— 逐字段的清单压根表达不了它，而 agentao 在两个事件上确实在发上游没有定义的字段。

**通用字段。** ✓ = 参考文档给该事件的示例带它；— = 不带。

| 事件 | `session_id` | `transcript_path` | `cwd` | `hook_event_name` | `permission_mode` | `prompt_id` | `effort` | `agent_id` | `agent_type` |
|---|---|---|---|---|---|---|---|---|---|
| `SessionStart` | ✓ | ✓ | ✓ | ✓ | — | 条件 | — | **forbidden** | **forbidden** |
| `SessionEnd` | ✓ | ✓ | ✓ | ✓ | — | 条件 | — | **forbidden** | **forbidden** |
| `UserPromptSubmit` | ✓ | ✓ | ✓ | ✓ | ✓ | 条件 | — | **forbidden** | **forbidden** |
| `PreToolUse` | ✓ | ✓ | ✓ | ✓ | ✓ | 条件 | 条件 | **forbidden** | **forbidden** |
| `PostToolUse` | ✓ | ✓ | ✓ | ✓ | ✓ | 条件 | 条件 | **forbidden** | **forbidden** |
| `PostToolUseFailure` | ✓ | ✓ | ✓ | ✓ | ✓ | 条件 | 条件 | **forbidden** | **forbidden** |
| `Stop` | ✓ | ✓ | ✓ | ✓ | **✓ —— 最容易被漏掉的那一格** | 条件 | 条件 | **forbidden** | **forbidden** |
| `PreCompact` | ✓ | ✓ | ✓ | ✓ | **— 而 agentao 照发**（`_payload.py:175`） | 条件 | — | **forbidden** | **forbidden** |

`session_id`、`cwd`、`hook_event_name` 在八个事件上都是 **required**，而且三个今天都在手上（`cwd` 只是三
个工具事件上没给；`hook_event_name` 就是信封里 `event` 键改个名）。问题全在另外两列：

- **`transcript_path` —— 八个事件全都 required，而 agentao 没有值可给。** 硬编码 `None`
  （`_payload.py:142,173`）。`.agentao/sessions/*.json` 只在保存点写、不是连续写
  （`embedding/sessions.py`）；`.agentao/replays/*.jsonl` 只在 replay 打开时存在。**G7 定：** 要么做一份
  连续写入的 transcript（新组件，自带脱敏问题），要么发一个显式 `null` 并写进 §1 的 profile。绝不能指向一
  个内容滞后于会话的文件 —— 读到过期 transcript 的 hook，比读到 `null` 然后分支的 hook 处境更糟。
- **`prompt_id` —— conditional。** 参考文档：当前正在处理的那条 prompt 的 UUID，*"Absent until the
  first user input"*，v2.1.196+，并且刻意等于 OpenTelemetry 的 `prompt.id`，好让 hook 输出与遥测对得上。
  agentao 有一个逐 turn 的 id（`agent._current_turn_id`，在 `TURN_BEGIN` 时快照），但那是 *turn* id，而参
  考文档把 `turn_id` 给的是另一个事件 —— 拿一个当另一个用，等于凭空造出一条并不成立的关联。**G7：** 要么
  真发一个 prompt id，要么不发这个字段。「首次输入之前不存在」是一条要测的真实条件，不是脚注。
- **`effort` —— 同时受两个条件约束。** *"Present for events that fire within a tool-use context, such
  as `PreToolUse`, `PostToolUse`, `Stop` … when the current model supports the effort parameter"*，形状
  是 `{"level": "low"|"medium"|"high"|"xhigh"|"max"}`。agentao **有**来源：`/thinking` 会把
  `reasoning_effort` 写进在用 client 的 `extra_body`（`cli/commands/provider.py`）。它同样带着和
  `permission_mode` 同族的枚举问题：agentao 接受 `minimal` 与 `off`，上游两个都没有。**G7：** 能映射的映射
  （`low`/`medium`/`high`），取值是 `minimal`/`off`/未设置时就不发这个字段 —— 绝不能把 `off` 硬凑成某个
  level，那等于告诉 hook「思考是开着的」。
- **`agent_id` 与 `agent_type` —— 两个都 forbidden，但理由不同，这正是它们分成两列而不是一列的原因。**
  上游是分开的：`agent_id` *"Present only when the hook fires inside a subagent call"*，而
  `agent_type` 是 *"Present when the session uses `--agent` **or** the hook fires inside a subagent"* ——
  一个用具名 agent 启动的主线程会话也带它。所以下面那条「子 agent」论证完整覆盖 `agent_id`，对
  `agent_type` 只覆盖一半。另一半是：**agentao 根本没有具名 agent 会话模式** —— 任何入口都没有 `--agent`
  （`cli/entrypoints.py`、`cli/run.py`），也没有任何地方设置会话级的 agent 名字。所以两者都 forbidden，而且是在**通用字段
  矩阵**里 —— 那是它们唯一的家，下面那张事件专属表两个都不列，这是刻意的：一个字段挂在两张表上，正是一处
  反向标注能熬过一整版的原因 —— `agent_type` 曾待在那张表的 *forbidden* 列里、顶着一个写着「agentao 今天在
  发」的表头，而对一个「forbidden 恰恰因为 agentao 从不发它」的字段来说，这句话正好说反。将来若真做了具名 agent
  模式，`agent_type` 会**先**在主线程上变成条件字段、早于子 agent hook 出现 —— 两者是独立移动的，这正是现
  在拆成两列的原因。
  上游在 *"when the hook fires inside a subagent call"* 时给它们。顺着追下去发现：agentao 的子 agent 是一
  个全新的 `Agentao(...)`，**不传 `plugins=`**（`agents/tools/_wrapper.py:513`），而
  `_plugin_hook_rules` 默认为 `[]`（`agent.py:532`）—— 于是**在 agentao 的子 agent 里根本不会触发任何
  hook**。这两个字段取不到，是因为那些事件在那里从不发生。这是范围决定、不是序列化决定，而「§1 的事件清
  单」盖不住它 —— 那份清单是按事件**名**划范围的，没有执行上下文这一维。§1 现在带着一维：**profile-1 仅主
  线程。**
  第 18 条偏离（§7）记录这处缺口；将来若要做子 agent hook，得先让插件规则进到子 agent 的构造里，这两个字
  段跟着它一起落地，而不是提前。
- **`permission_mode` —— conditional，而且同时错在三个方向上。** agentao 的词表是 `read-only` /
  `workspace-write` / `full-access` / `plan`，参考文档的是 `default` / `plan` / `acceptEdits` / `auto` /
  `dontAsk` / `bypassPermissions`。(i) 在欠它的那五个事件上，agentao 给出的是枚举之外的取值，于是按官方取
  值分支的 hook 一个分支都命不中。(ii) 在 `Stop` 上这个值甚至不是从会话里读的 —— `build_stop` 的参数默认
  就是 `"workspace-write"`（`_payload.py:144`），一个常量。(iii) 在 `PreCompact` 上这个字段是 **forbidden**
  的，而 agentao 在发。G7 要么钉一张映射表（唯一精确的一条是 `plan`→`plan`；`full-access` 接近
  `bypassPermissions`；`workspace-write` **不是** `acceptEdits`），要么不发这个字段 —— 而无论哪条，都要把
  它从 `PreCompact` 上摘掉。

**事件专属字段。**

| 事件 | Required | Conditional | Forbidden | 来源 |
|---|---|---|---|---|
| `SessionStart` | `source` | `model`、`session_title` | —— | `source` 在两个 dispatch 点都推得出来（`cli/session.py:104`、`cli/run.py:691`）—— `startup`/`resume`/`clear`/`compact`/`fork` 对应的是 agentao 不同的命令。`model` 从 LLM client 拿 |
| `SessionEnd` | `reason` | —— | —— | 在 `cli/session.py:108`、`cli/run.py:815` 推得出来；取值 `clear`/`resume`/`logout`/`prompt_input_exit`/`other`，`other` 是诚实的兜底 |
| `UserPromptSubmit` | `prompt` | —— | —— | 就是把 `userMessage` 改个名 |
| `PreToolUse` | `tool_name`、`tool_input`、`tool_use_id` | —— | —— | `tool_use_id` **有、只是没接** —— 归一化后的 `plan.tool_call_id`（`tool_runner.py:160-188`） |
| `PostToolUse` | `tool_name`、`tool_input`、`tool_response`、`tool_use_id` | `duration_ms` | —— | `duration_ms` **有、只是没接**（`tool_executor.py:426`）。难的是 `tool_response`：这边是**字符串**（`_payload.py:100`），上游传的是工具的结构化输出对象 |
| `PostToolUseFailure` | `tool_name`、`tool_input`、`tool_use_id`、`error` | `is_interrupt`、`duration_ms` | —— | `is_interrupt` 可从 cancellation token 推出 |
| `Stop` | `stop_hook_active`、`last_assistant_message` | `background_tasks`、`session_crons` | **`turn_end_reason`**（`_payload.py:147`） | 两个 conditional 指的是 agentao 没有的功能 —— 按 §1 的 profile 不发 |
| `PreCompact` | `trigger`、`custom_instructions` | —— | **`compaction_type`、`reason`**（`_payload.py:178-179`），外加 `permission_mode` | agentao 自己的 compaction 词表，跑在一个扁平的 Claude 形状 payload 上 |

**forbidden 这一列里装着两种不同的条目**，而把它们在表头里混为一谈正是要避免的错误。列里所有**加粗**的，
都是 agentao *今天在发*、而上游在该事件上并没有定义的字段，`_payload.py` 的引用就是证据；不加粗的则是
「forbidden 但并没有在发」。就目前而言这张表里只剩第一种，第二种由通用字段矩阵承载。今天有三个私有字段搭在两个扁平 Claude 形状 payload 上。
`claude-code` 模式下它们被**移除**；`agentao-v1` 下保留。如果其中哪个确实需要送到上游侧，就套用 §3.3 在输
入侧的对应做法 —— 收进一个 `agentao` 子对象，绝不与文档化的键平级裸放。这是 G7 的决定，形状和输出侧那条已
定的命名空间规则完全一样。

**`tool_response` 仍然是最可能逼着 profile 缩窄的那一行。** 上游传结构化对象（写文件是
`{filePath, success}`）；agentao 的工具返回 `str`，且不声明输出 schema。把字符串包进一个自创对象是第三种
契约；直接发字符串则是一处需要写明的类型偏离。同一个决定也卡着 `updatedToolOutput`（G2），后者的参考语义
是 *"must match the tool's output shape"* —— 而这个 shape agentao 没有。

**G7** 收口：`transcript_path`、`permission_mode` 映射、`tool_response`、那三个私有输入字段的处置、取不到
的字段是缺席还是显式 `null`，以及它从 §5.1 继承的两行（`terminalSequence`、`sessionTitle`）。它卡第 3 步。
这张矩阵强制的规则只有一句：**agentao 取不到的字段要么缺席、要么写进文档，绝不能编 —— 而上游没有定义的字
段，压根就不该发出去。**

### 5.4 混装契约的 dispatch

§2.5 让 `claude-code` 规则走「全启动 + 有界并发」，让 `agentao-v1` 规则保持串行短路。§3 又把契约定为**文件
级**。两条放在一起，同一个事件的规则列表里就可能同时装着两种 —— `resolve_all_hook_rules` 把每个插件的每个
hook spec 拼成一个扁平列表（`_user_turn.py:28-59`），而没有任何地方对它排序或分组。此前每一版都在按「一个
会话只会有一种模式」来描述这两种模式，而全文唯一处理过的混装情形只有 `Stop` 上限（§10 第 2 条）。

于是有四个问题在计划里没有答案，而且全都是可观察的：

1. **两组会交错吗？** 如果 dispatch 只是遍历一个合并列表，那么一条 v1 规则的短路会中止这次遍历 —— 连带中
   止它后面那些 Claude 规则的**执行**，而那正是 §2.5 存在要提供的唯一保证。
2. **v1 的短路能不能压掉一条 Claude 规则的副作用？** 同一个缺陷，换成作者视角的说法。
3. **两组的结论、reason、改写、context 怎么合并？** 组内 §2.5 已经说了：任一 deny 即 deny，平手按声明顺
   序。跨组则没说。
4. **当好几条 `Stop` 规则各自产生了 continuation 时，「产生本次 continuation 的那条规则」是哪条**，好用它
   的契约去挑上限（§10 第 2 条）？

**本计划的答案 —— 分组、并发跑、只合并一次。门槛 G9。**

- **按契约分组，不按位置。** 每个事件两组。`claude-code` 组按 §2.5 的有界并发跑，带全启动保证；
  `agentao-v1` 组照今天那样串行并短路。
- **两组之间是并发的**，而 v1 的短路**只结束 v1 那一组**。让 v1 先跑，它的短路就会拖住并压掉 Claude 规
  则；让 Claude 先跑，只会拖慢 v1 的短路，无害但也无意义。并发既正确又最简单，而且它意味着：装一份 v1 文
  件在旁边，改变不了那份 Claude 文件所观察到的东西。
- **一次合并，与组无关 —— 但合并跑在一张格上，不是跑在「deny」上。**「任一规则 deny 即 deny」会把 §4.1
  刻意分开的四种控制类型压平。`continue:false` 停的是整轮；`decision:"block"` 停的是一
  次动作；`PreToolUse` 还有 `ask`；而在 `Stop` 上两套契约的含义**正好相反** —— v1 的 `blockingError` 结束
  这一轮并返回（`_runner.py:964-981`），profile 的 `decision:"block"` 却是**继续**（`:984`）。「deny」不是
  这些东西的公分母。合并改跑下面那张格。
- **`reason` 的平手裁决只在胜出类别内部排序。**「合并列表上声明顺序的赢家」会让一个 `Stop` 的
  `stopReason` 被当成某个 `deny` 的理由呈现出来。声明顺序只在产生了**胜出控制**的那些规则之间挑
  一个，别的都没有资格。context、通知、诊断是正交的，无论谁赢都按声明顺序全部拼接（§4.2 的通道／结论分
  离）。
- **改写不会跨组**，因为 `updatedInput` 只存在于 Claude profile 里，v1 规则根本没有这个字段。所以能出现的
  冲突只有 §4.4 那一种。
- **`Stop` 上限跟着 reason 的平手裁决走。** 合并后活下来的那个 continuation 就是声明顺序的赢家，用它那条
  规则的契约挑上限 —— 8 或者 3。这样「归属」与用户实际看到的那条 continuation reason 是一致的。保守的替代
  方案是对贡献规则的上限取 `min()`：它永远不会放宽 v1 作者的预期，但只要有任何一条 v1 的 `Stop` 规则也在
  continuation，`claude-code` 的合规就破了。G9 选一个；§11 第 5 问记录这个取舍。

**合并用的那张格。** 先跨类排序：

| 序 | 控制 | 来自哪里 |
|---|---|---|
| 1 | `Stop(reason)` | **「结束处理」这个类别，不论由什么产生。** 今天恰好只有一样东西产生它：`continue:false`，且事件认它（§5.1）。参考文档本来就把 `continue` 排在事件 decision **之上**（在单个 hook 内），跨 hook 用同一顺序是唯一自洽的做法。「只有 `continue:false`」是**对今天什么能到达这一序的清点、不是对什么*可以*到达的规则** —— 把它写成规则，正是 `PostToolUseFailure` 那张翻案清单与本表自相矛盾的原因 |
| 2 | 事件自己的 decision | 按该事件自己的格合并，见下 |
| 3 | `Allow` / 无 | 没有规则提出任何要求 |

**exit 2 不从第 1 序进场。** 把第 1 序写成「`continue:false` …… 或 exit-2 阻断」错了两处：§4.2 自己的 resolver 把 exit 2 映射成 `Block(reason)`、不是 `Stop`；而且 exit 2 是**逐事件**的结果、不
是全局中止。它在 `PreToolUse` 上阻断这次工具调用、在 `PreCompact` 上阻断压缩，而在 `Stop` 上阻断的是*停
止* —— 也就是这一轮**继续**。把那个叫「结束处理」，恰好在两套契约唯一冲突的那个事件上把它讲反了。所以
exit 2 **先**经 `table.exit2(event)` 归一化 —— `block` / `model_feedback` / `user_notice` / `ignore`
（§4.2）—— 归一化出的 block 再以**该事件自己的类别**从第 2 序进场，而不是当成一个通用的 stop。在 `Stop`
上，这次归一化的结果是**继续**；这也是为什么 §4.2 里 `Block(reason)` 只是 resolver 层的名字、不是合并层
的类别。

**同一条归一化，也是一行存疑的入口 —— 而探测已经从这扇门走过去了。** 第 1 序收的是*效果*为「结束处理」
的任何东西，判定成员资格的唯一标准就是效果。`PostToolUseFailure` 的 `decision:"block"` 曾是候选：它若结束
这一轮，`resolve()` 就会像经 `table.exit2(event)` 归一化 exit 2 那样把它归一化成 `Stop(reason)` 送进第 1
序，下面那条 rank-2 行则应删除而不是保留。**实测下来它是加注并继续**
（`docs/reference/hooks-probe-2.1.251.zh.md` §C），所以它留在第 2 序、第 1 序不受影响 —— 正是翻案清单预留
的那个结果，而且是探出来的、不是选出来的。这扇门对下一行存疑的仍然敞着：成员资格永远由效果决定，而不是由
某个字段出现在哪张表里决定。

然后是第 2 序、逐事件：

| 事件 | 格 | 说明 |
|---|---|---|
| `PreToolUse` | **`deny > ask > allow`** | 上游自己写明的多 hook 优先级是 `deny > defer > ask > allow`；而 `defer` 在 `resolve()` 里就已经**降级成 `deny`**（§5.1），根本到不了合并层，合并器也就不需要为它留分支。本行与 G9 必须写同一组取值，否则实现者无从判断要不要处理 `defer` |
| `UserPromptSubmit`、`PostToolUse`、`PreCompact` | `block > none` | 只有一个轴，所以扁平的「任一 deny 即 deny」在这里碰巧是对的 —— 也只在这里对 |
| `PostToolUseFailure` | `block > none` | **生效。** 探测发现该事件认顶层 `decision`，且其效果是*反馈并继续*，所以它在第 2 序合并、第 1 序不受影响（`docs/reference/hooks-probe-2.1.251.zh.md` §C）。两个 profile handler 同时返回 `block` 就按这一行合并，平手按声明顺序。它的 `continue:false` 那一支仍走通用字段行在第 1 序合并 |
| `Stop` | **`end-turn > continue > none`** | 两套契约唯一正面冲突的地方。「结束」压过「继续」，因为它是另一方撤不回的那个结果，也因为 agentao 自己的代码本来就是这个顺序（`_runner.py:964` 在走到 `:984` 之前就 return 了）。被这样丢掉的 continuation 要变成一条点名了失败规则的 `user_notices` —— 静默丢弃，正是作者最后得出「我的 hook 有时候不触发」这个结论的原因 |

这些都不贵 —— 一次分组加一次合并 —— 但每一部分都是可观察的，所以它是门槛而不是实现细节；§12 也因此多出
逐决策事件的混装测试 —— `PreToolUse`、`PostToolUse`、`Stop`、`UserPromptSubmit`、`PreCompact` 与
`PostToolUseFailure`。这个集合被弄错过两次：漏掉 `PostToolUse`，而本节自己那张格就把它列在
`block > none` 那一行；以及把 `PostToolUseFailure` 挂在 G7 上，那是按错误的轴 gate —— G7 管的是它**事件级
的 `decision`**，而 `continue:false` 是从*通用*字段那一行（§5.1 的矩阵）到达它的，与 G7 怎么定无关。它无条件
进入这个集合，只有它的 `decision` 那一支受 gate。

**而且测试形状是逐事件的，不是一个模板。** 要求每一个被列事件都有「一条会阻断的 v1 规则」，这在 `PostToolUse` 与 `PostToolUseFailure` 上造不出来：`agentao-v1` 把这两个都走
`_dispatch_lifecycle`（`_dispatcher.py:126,134`），根本不给它们任何 stdout 决策面，也就没有一个 v1 结论去
和 Claude 结论合并。这两个事件上的混装场景仍然真实、仍然值得一个测试 —— 只是断言不同：v1 规则贡献一个
**可观察的副作用**、Claude 规则贡献控制，要成立的是 v1 那条**跑了**、而 profile 的控制**生效了** —— 而在
`PostToolUse` 上，「生效了」是**两种不同的观察**，因为 `decision:"block"` 保留结果并让这一轮继续，
`continue:false` 则结束它（§5.2.2）。§12 两种形状都写了，`PostToolUse` 上的两个分支也都写了。

---

## 6. 输出限额 —— 两层，以及为什么一层不够

评为 **P0**，排在几条 P1 合规项之上，也高于对照文档给它的 P2（§5.3）。这次上调是可靠性论证，不是合规
论证。

一层不够。只限*解析出来的字符串*本身是对的 —— 在解析前截断原始 stdout 会破坏 JSON 控制通道、把一条
`decision:"block"` 变成解析不了的文本。那个推理今天依然对，而且依然**不够** —— 它保护的是模型上下文，
仅此而已。`run_captured` 在任何解析器存在之前，就通过两个 pipe 和 `communicate()`
（`capabilities/process.py:214`）把整个 stdout 与 stderr 读进内存。一个输出数 GB 的 hook 会在语义限额
够得着之前就把内存吃光。

**第一层 —— 原始，位于子进程边界。有界的内存缓冲，不落盘。**「边读 pipe 边 spool 落盘」是最显然的设计，
而它与 §6.1 所倚赖的「落盘前脱敏」这个属性无法共存。`scan_and_redact` 接收的是完整字符
串（`security/secret_scan.py:108`）；逐块扫描会漏掉任何跨块的 token 和所有多行密钥，而为了扫描把整份缓
冲下来，又恰好把第一层要约束的那部分内存还了回去。原始明文落盘还需要为「从未脱敏过的内容」单独定一套
磁盘策略。

所以：按字节上限增量读 pipe，超限就**杀掉进程树并判该 hook 失败**、给出诊断。不是截断 —— 一个输出在
JSON 中途被砍断的 hook 没有任何有意义的决定可贡献，假装它有，等于把一次资源失败变成一次静默的语义失败。
`communicate()` 没法加上限（`capabilities/process.py:214`），所以这是对共享 runner 的一次真实改动（或写
一个 hook 专用的兄弟函数），并且对该 runner 的其他调用方必须默认**关闭** —— `search_file_content` 与插件
hook dispatcher 都走它（`CLAUDE.md`，Common gotchas）。

**第二层 —— 语义，而且单位是通道、不是字段。** 只点三个**字段** —— `additionalContext`、
`systemMessage`、以及「纯 stdout 当 context」那条路（`_output_parsing.py:49`）—— 结果覆盖不住它自己的
§4.2。`resolve()` 会在 `PostToolUse` / `PostToolUseFailure` 上把 exit-2 的 stderr append 进
`model_contexts`，在 `SessionStart` / `SessionEnd` 上 append 进 `user_notices`，而 `Stop` 的 `reason` /
`stopReason` 会成为下一轮的输入。每一个都是 hook 写的字符串、都能抵达模型或用户界面，却一个都不经过那三
个具名字段；而第一层的上限是一个*内存*上限，比上下文预算高出好几个数量级，约束不了它们。参考文档自己那
句话按构造就不是穷举：*"Hook output strings, **including** `additionalContext`, `systemMessage`, and
plain stdout, are capped at 10,000 characters."*

所以限额作用在 **`ResolvedHookOutput` 的通道**上，也就是 `resolve()` 往里填的地方：`model_contexts[]`、
`tool_contexts[]`、`user_notices[]`，以及 continuation / `stop_reason` 那个字符串。凡是离开 resolver 走
向模型、用户界面或下一轮的字符串都受限；resolver 内部的一律不受限。

上限取值归 G4，而上游不止一个数：hook 输出字符串是 10,000 字符；另有一条 **2,000** 字符的上限落在
`classifierContext` 上 —— 那是一个 agentao 并未实现的通道 —— 且它是 *"shared across every hook that
responds to that call"*。此处引用它是为了形状而不是数值：上游是逐通道设限的，而且其中一条限额是**按调用
聚合**的、不是逐 hook 的。codex：约 2,500 token，逐 handler 可配，`0` 关闭
（`hooks/src/output_spill.rs:12`）。**只有第二层的内容会落盘**，而它是一个已解析的字符串，因此可以整体
脱敏之后再落地。

### 6.1 落盘策略 —— 复用已经存在的那个 sink

`.agentao/tool-outputs/` 已经在给大块 tool result 做这件事（`runtime/tool_result_formatter.py:33`），而且
它用先例回答了五个问题里的三个：用头/尾预览而不是尾部截断、**在字节落盘之前**经共享凭证扫描器做脱敏
（`:69`，`security/secret_scan.py:16`）、以及在 replay 事件上暴露 `disk_path` 使完整输出可回捞。

在一个兄弟目录 `.agentao/hook-outputs/` 里复用这套形状。先例**没有**解决、必须由本计划决定的部分（设计
门槛 G4，§9）：

- **创建时 `0600`。** 现有 sink 没设。hook 输出比 tool 输出更可能带凭证 —— hook 是用户脚本，可能回显自己
  的环境。
- **配额与清理。** `tool-outputs/` 也同样没有。要么是每会话上限 + 按时间裁剪，要么这个目录会无界增长。
- **写失败。** 现有 helper 常被以为会退到那个 80,000 字符的遗留上限；它不会。`except` 只写了一条日志
  （`tool_result_formatter.py:92`），函数照样返回由 `TOOL_OUTPUT_SAVE_THRESHOLD` —— 40,000 字符
  （`:29`）—— 构造的头/尾摘录，所以 `MAX_TOOL_RESULT_CHARS`（`:36`）是**另一个**分支，这条路根本走不
  到。要照抄的行为因此是「保住摘录、丢掉可回捞的副本」；而在这里，失败还必须出现在 `diagnostics[]` 里，
  不能被吞掉。

OpenAI 自己的 hooks 参考也点出了同一个风险 —— 落盘会把 hook 输出写到磁盘上。写入路径上的脱敏是 agentao
已经有的答案，只是要接到新 sink 上，而不是重新推导一遍。

---

## 7. 处置

优先级采用维护者的判断。「原」列只在与对照文档排序不同处给出，因为其中两项移动的理由**不是**合规。

| # | 偏离 | 处置 | 优先级 | 原 |
|---|---|---|---|---|
| 0 | **Claude 形状的 `hooks.json` 解析出零条规则**（§2） | `claude-code` 模式下解析官方嵌套 + 字符串 matcher。 | **P1** | *对照文档里没有* |
| 1 | stdin 契约在全部 8 个事件上都有差异（对照 §5.9） | `claude-code` 事件一律 flat snake_case，绝不两套并发 —— 并按 **§5.3 的字段矩阵**逐字段定来源，以及哪些字段 agentao 根本取不到（门槛 G7）。 | **P1** | P1 |
| 2 | `UserPromptSubmit` 丢弃全部四种输出通道（§5.1） | 四种全支持。 | **P1** | P1 |
| 3 | `systemMessage` 送进模型通道（§5.2） | 走 `user_notices`。字段已存在（§4.3）。 | **P1** | P1 |
| 4 | `Stop` 的 `hSO.additionalContext` 不继续对话（§5.8） | 转成 continuation，纳入重入上限 —— `claude-code` 下是 **8**，`agentao-v1` 下是 3（§10 第 2 条）。 | **P1** | P2 |
| 5 | hook 输出无上限（§5.3） | **两**层 + 落盘（§6）。 | **P0** | P2 —— 因可靠性上调 |
| 6 | `PreToolUse` 的 `additionalContext` 只写日志（§5.4） | 经 `tool_contexts` 注入。 | **P2** | P2 |
| 7 | `continue:false` 仅 `Stop` 生效（§5.5） | 按能力表处理 —— **不是**全局开关 —— **并且按 §5.2.2 那张路由表**：开关只是一半 —— 五个「认」它的事件里有两个没有能承载停止的结果对象，而且它们的 hook 跑在 tool worker 里、离能处理它的地方隔着三层调用栈（`_dispatcher.py:267-288`、`tool_executor.py:462`、`tool_runner.py:249`）。 | **P2** | P2 |
| 8 | exit 2 仅 `Stop` 生效（§5.6） | 按能力表：block / feedback / ignore。 | **P2** | P3 |
| 9 | 没有 `${CLAUDE_PLUGIN_ROOT}`（§5.7） | 占位符替换**与**环境变量导出都要补 —— **三个**占位符全补（§2.4）。 | **P1**，低成本 | P3 —— 因成本上调 |
| 10 | **`shell` 字段不被认**（§2.4） | **忽略该字段并出诊断** —— 而且前提被撤回：2.1.251 自己也不认它、且用 `sh` 执行命令 hook，所以 agentao 的 `/bin/sh` 基线是**合规的**（`docs/reference/hooks-probe-2.1.251.zh.md` §A）。参考文档的自相矛盾由实测了结，而不是靠挑一句话。 | **P3** | *对照文档里没有* |
| 11 | **Windows 上跑的是 `cmd.exe`** —— 既不是 Git Bash 也不是 PowerShell（§2.4） | 不在本文范围内；agentao 没有 Windows CI job，所以任何方向的断言都未经测试。记录在此以免被重新发现。 | *注记* | *对照文档里没有* |
| 12 | **`Stop` 重入上限这边是 3，快照里是 8** | 按契约取值：`claude-code` 下 8，`agentao-v1` 下 3。它读起来像一条「要保住的领先项」，其实是一处偏离（§10 第 2 条）。 | **P2** | *在对照文档的表里，但不属于那九条* |
| 13 | **`updatedInput` 会绕过已经算好的权限裁定**（§4.4） | 聚合 → 校验 → **重判** → 取交集 → 重新确认。门槛 G8，卡第 6 步。 | **P1** | *对照文档里没有* |
| 14 | **九个 profile 字段无处安放**，而且一个合法字段会引出 `hook error`（§5.1、§4.2） | 列举 profile；不认识的键**忽略并给诊断**，绝不算 schema 失败。 | **P1** | *对照文档里没有* |
| 15 | **已被拒的调用不触发 `PreToolUse`**（`tool_runner.py:277`） | `claude-code` 下无论裁定如何都 dispatch；`agentao-v1` 保留跳过（§4.4）。 | **P1** | *对照文档里没有* |
| 16 | **混装契约的 dispatch 没有定义**（§5.4） | 按契约分组、两组并发、只合并一次。门槛 G9。 | **P2** | *对照文档里没有* |
| 17 | **`permission_mode` 是个枚举外的常量，还搭在没定义它的 `PreCompact` 上**（§5.3） | 映射或不发；`claude-code` 模式下摘掉那三个 agentao 私有输入字段。 | **P2** | *对照文档 §5.9，改成逐事件重述* |
| 18 | **agentao 的子 agent 里根本不触发任何 hook** —— 子 agent 不带 plugins 构造（`agents/tools/_wrapper.py:513`），`_plugin_hook_rules` 默认 `[]`（`agent.py:532`） | 不在 profile-1 内，写进 §1 的事件清单里、而不是等着被发现。它是 `agent_id` / `agent_type` 没有来源的原因（§5.3），而它本身比引出它的那两个字段大得多。 | *注记* | *对照文档里没有* |

### 7.1 第 9 条是 quick win，但有一个坑

`plugin.root_path` 在解析规则的地方本来就在手上（`_user_turn.py:42`），只是没往下传；把它和 `contract`
一起挂到 `ParsedHookRule` 上，`_run_subprocess`（`_dispatcher.py:331`）里的替换与导出就都是小事。

坑在于：`_run_subprocess` **不传** `env=` 给 `run_captured`，而这恰恰是 hook 子进程能拿到
`build_child_env()`、provider 凭证被剥掉的原因（`capabilities/process.py:200`）—— 对照文档 §7 第 1 条，
agentao 领先两个 peer 的五处之一。把导出写成 `env={...}` 或 `env=os.environ | {...}`，都会悄无声息地删掉
它。

唯一正确的写法是 `env=build_child_env({...})` —— overrides 按构造就是在剥离**之后**才应用的
（`capabilities/process.py:92`）。键是 **§2.4 的那三个** —— 在这里只带两个是很容易发生的：
`CLAUDE_PROJECT_DIR`、`CLAUDE_PLUGIN_ROOT`、`CLAUDE_PLUGIN_DATA`。§2.4 是权威，本节只说*怎么*导出。
而且必须有一个测试钉住「改完**之后**」子进程里仍然没有 provider key，不能只钉改之前。

---

## 8. 实施顺序

每一步一个 PR，**并且自带本步的测试**。把测试全部推到最后一步，会让五个 PR 的安全边界落在一个
未来的 PR 上。只有跨事件的 golden 与矩阵覆盖才属于最后。

只有**第 2 步**是行为保持的。第 1 步不是 —— 尽管它看起来只是管道活：它的截断、预览文案与落盘路径按设计就是可观察
的，把它们说成不可见，等于把一处面向用户的变更藏在重构标签底下。

| # | 步骤 | 行为 | 门槛 |
|---|---|---|---|
| 1 | 输出限额：第一层有界缓冲 + 第二层语义限额 + 落盘（§6） | **可观察** | G4 |
| 2 | `ParsedHookOutput` **与** `ResolvedHookOutput`（§4.1）+ profile 与能力表（§5.1）+ 消费者表（§5.2）+ 诊断 registry（§4.2），先描述今天 | 保持 | **G10** |
| 3 | 形状识别 + `contract`（一个**版本号**，§3）+ 官方配置形状 + handler 字段矩阵（§2.4）+ 按**字段矩阵**（§5.3）做 Claude 输入序列化 + 三个路径占位符 | 可观察 | G3、G5、**G7** |
| 4 | 运行时 sink **及其逐界面路由**（§5.2.1）：`SessionStart`/`SessionEnd` 的生命周期用户通知 sink、`agentao run` 里 dispatch 挪到 emit 之前、`SessionStart` context、`PostToolUse` / `PostToolUseFailure` 结果对象**以及从 tool worker 里出来的停止路径**，还有三个缺路由的「认」事件上的 `continue:false` 路由（§5.2、§5.2.2） | 可观察 | G1、G2 |
| 5 | `UserPromptSubmit` 四通道、`systemMessage` → `user_notices`、`Stop` continuation | 可观察 | G1 |
| 6 | `PreToolUse` 的 `tool_contexts`、`resolve()` 在**五种** stdout 状态上的优先级（§4.2）、`continue:false`、exit 2、`PreToolUse` 的完整生命周期（含「已 DENY 也要 dispatch」与 `updatedInput` 的重判，§4.4）—— 表驱动 | 可观察 | **G8** |
| 6b | `claude-code` 模式下让**所有**匹配 handler 在有界并发下跑，聚合与顺序无关（§2.5）；按契约分组与那一次合并（§5.4） | 可观察 | G6、**G9** |
| 7 | 跨事件 golden payload + event × 字段 × 退出码矩阵 | 仅测试 | —— |

是依赖序，不是排期。第 5 步需要 2 和 3；第 6 步需要 2 和 4；第 4 步需要 2。

**全部落地**（rev 24）—— 按的就是这个依赖序，但装在**一个** PR 里（#199 / `18fb628`，12 个提交），
而不是上面第一行设想的七个。实施与本文正文有三处出入，记在 §0。

第 1 步是唯一**不**按契约划分的一步，而且是刻意的：它落在 `contract` 被解析之前两步，而内存上限不可能做
成逐文件的可选项。这正是 §3 写明的那处划界 —— `agentao-v1` 冻结的是契约面，不是资源包络。

---

## 9. 设计门槛

> **截至 rev 24 十条全部关闭 —— §0 是索引，也是权威。** 下面的内容作为「每个门当初要定什么」的记录保留：
> 「卡第 N 步」是历史陈述，各条上的结案标记停在 rev 23（最后一版写过它们的修订）。看条目是问题，看 §0 是
> 答案。

每一条都必须在依赖它的那一步**之前**关闭。它们是门槛而不是待决问题，恰恰因为那些步骤已经把它们当成依赖
在用了。

- **G1 —— `user_notices` 的 transport 形状*以及它在每个界面上的路由*。** 卡第 **4 与 5** 步 —— 第 4 步要
  它承接 `SessionStart`/`SessionEnd` 的 exit-2 sink（§5.2），第 5 步要它承接 `systemMessage`。agentao 只发
  一个 `PLUGIN_HOOK_FIRED` 携带 verdict 与计数，其 docstring 也写明 hook 输出在这一层「neither known nor
  stored」（`_hook_dispatch.py:52-53`）。一条面向用户的通知通道需要的不止是一个计数。定：新事件类型，还是
  扩展 payload —— **然后**按 §5.2.1 定每个界面在哪儿渲染它：`agentao run` 在 `run.py:814` 就把整个输出写完
  了，而 `SessionEnd` 到 `:815` 才跑，观察者更是早在 `:770` 就摘掉了，所以只定传输形状会让 headless 用户完
  全没有路。headless 那条的 wire 形式，参考文档给的是 `--output-format stream-json` 下的
  `SDKInformationalMessage`。
- **G2 —— 生命周期事件的结果类型，以及「从 tool worker 里出来」的停止路由（§5.2.2）。** **已定（rev 23）：取第 (ii) 支** —— 放弃排队 sibling 的保证，也不建接缝；§1 记下「停止那一刻队尾是否执行」**未定义**，§12 的那条测试缩到两种情况下都成立的不变式。下面关于结果类型、聚合路径与 `stopReason` 平手规则的内容全部照旧。卡第 4 步。
  `SessionStart`、`PostToolUse` 与 `PostToolUseFailure` 今天都只是 lifecycle：三个都从
  `_dispatch_lifecycle` 返回 `list[HookAttachmentRecord]`（`_dispatcher.py:66,126,134,267-288`），于是
  hook 决定的任何事都在调用点被丢掉。`SessionStart` 只需要用返回值承接 exit-2 用户通知与
  `additionalContext` —— profile-1 在那里不认停止（§5.1）。另外两个需要一条**跨三层调用栈的控制路径**，而
  这正是「补个结果类型」不够用的地方：hook 跑在 worker 内（`tool_executor.py:189,462`），
  `ToolRunner.execute` 只返回 `(bool, list)`（`tool_runner.py:238,249`），chat loop 读的正好就是那两个
  （`_runner.py:773`）。六个决定：(a) 每个的结果类型；(b) **汇聚路径** worker → `execute_batch` →
  `ToolRunner.execute` → chat loop；(c) **并行批次里的兄弟调用** —— 跑完的只有*触发*的那个，因为全部
  plan 是一次性提交给 8 worker 池的、每个 worker 在自己完成时就地 dispatch
  （`tool_executor.py:189-200,462-470`），所以最多七个兄弟正在执行中、还可能有排队的；提案是*让这一批跑完
  再停*，因为它是唯一能在不加新机制的前提下保住 (d) 的选项，而 G2 要么**去探上游怎么处理排队中的兄弟调
  用**、要么把这个选择声明成一处写明的偏离。**接缝不能脱离承诺单独变成可选项**：今天没有任何东西能观察到
  「停止已挂上 `ToolExecutionResult`」与「worker 的 future 完成」之间那一刻，所以一条没有接缝的排队兄弟
  *规则*，是任何验收都执行不了的规则。两者要一起动，G2 选的是**成对的方案、不是零件** —— **(i) 保留这条保证并把接缝做出来**，或者 **(ii) 放弃这条保证**。
  **可配置的 `max_workers` 不是那个接缝。** 把 `8` 变成可注入，限住的只是并发量：发出停止的那个任务，它的
  future 该什么时候完成还是什么时候完成，它的 worker 紧接着照样把队尾取走 —— 测试对*停止变为可观察*与*队尾
  出队*之间那一刻，依然没有任何控制权。「保留保证、只注入 cap」就是 (i) 存在着要禁止的那个执行不了的组合，
  只是换了个名字。接缝只能是两样东西之一：在**「把停止挂到 `ToolExecutionResult` 上」与「worker 的 future
  完成」之间发一个测试可见的回调／事件**，或者一个由测试驱动的**可控 executor／准入闸门**，由它决定何时释
  放 worker、何时放队尾进来。cap 这个旋钮可以顺带有，用来把批次做小；但它永远不是接缝。
  取 (ii) 时：§1 的 profile 补一行，写明 agentao 只保证*批次结果*，而「停止之后排队中的兄弟到底跑不跑」是**未定
  义**的 —— 按 §1 第三条规则**列出来**，而不是静默丢掉。这正是隔壁 G6 用过的模式：当「所有匹配 handler 都
  启动」所需的机制显得太重时，兜底方案弱化的是**承诺**（改成「所有 handler 都被*提交*」），不是测试
  （§2.5）。只弱化测试，留下的是一条实现可以违反、却照样通过验收的规则；(d) 那条不变式：**每个 plan 仍然产出一个结果和一条 tool 消
  息**，因为 `format_batch` 是逐 plan 按 `exec_results[plan.tool_call_id]` 取值的
  （`tool_result_formatter.py:113-128`），而一条带 `tool_calls` 却没有对应 `role:"tool"` 应答的 assistant
  消息会被严格 API 拒掉；(e) 多个工具同时停时**哪个 `stopReason` 胜出** —— plan 顺序，绝不是完成顺序；
  (f) **`continue:false` 在每个触发点各终止什么**，按 §5.2.2 那张表，其中 `PreToolUse` 那一行最容易被悄悄
  做成 `deny`，因为那里已经有一个带裁定字段的结果对象。尤其 `updatedToolOutput` 必须在模型看到之前拼接进
  tool result —— 那是 `ToolRunner` format 阶段的事、不是 hook 包内部的事 —— 而且它与 `decision:"block"`
  是**两个不同的 sink**，后者保留原输出、只附上一条理由（§5.2.2）。
- **G3 —— Claude 的 matcher 语义。** 卡第 3 步。钉死三路求值（§2.2），并对照 codex 的实现验证 —— 它三路
  都有。
- **G4 —— 限额单位、上限、落盘策略。** 卡第 1 步。第一层的字节上限；第二层的单位（字符不需要 tokenizer，
  token 才是这份预算真正保护的东西）；以及 §6.1 的权限/配额/清理/失败处理。第一层不再落盘，所以
  本会引出的「临时明文文件」问题是被**关闭**了，而不是被回答了。
- **G6 —— hook 并发上限、溢出处理与合并的确定性。** **已定（rev 23）：取兜底支** —— 承诺是「所有匹配的 handler 都被**提交**」，而不是「都启动」，且在 `SessionEnd` 的共享预算下，排队的那个可能永远不跑。不做逐 dispatch 的准入控制。(c) 的声明顺序平手规则不受影响、仍然必须。卡第 6b 步。是三个决定，不是一个。(a) 池的名字与
  上限 —— 第四个池，不能碰 `CLAUDE.md` 记录的那三个。(b) **超出上限之后怎么办。** 光有 cap 并不能兑现
  「所有匹配 handler 都启动」：超出之后它们排队，而在 `SessionEnd` 共享的 1.5 秒预算下，排队的 handler
  可能根本不会启动 —— 那正是这项改动要修的那个失败。本计划的提案是**给每个事件的 handler 数设一
  个等于池上限的限额，并作用在合并后的规则列表上** —— 不是逐文件，逐文件约束不了任何东西，因为
  `resolve_all_hook_rules` 事后会把每个插件的每个 hook spec 拼起来（`_user_turn.py:28-59`）—— 超限的配置
  在加载时就带告警拒绝，告警里点名撞上限的插件。而且因为 dispatch 之间也在互相抢（8 个工具 worker 各自
  触发自己的 `PostToolUse`，`tool_executor.py:189,463`），准入单位是**一次 dispatch**：一个事件的全
  部 handler 在任何一个启动之前一次性拿到容量。如果这套机制太重，退路是把承诺弱化成「所有 handler 都被
  *提交*」，并接受 `SessionEnd` 的共享预算可能在某个排队者身上耗尽。(c) 让聚合出的 `reason` 可复现的平手
  裁决 —— 按声明顺序，绝不按完成顺序。
- **G7 —— profile 的两张字段矩阵（§5.1 输出、§5.3 输入）。** **部分结案（rev 23）。** 两行存疑均已实测（§0）：`SessionStart` 丢弃 `continue:false` —— 窄读法被证实；`PostToolUseFailure` **认** `decision`，效果是反馈并继续 —— 窄读法被推翻。存证问题已定：**只留 provenance 表，不入库**。下面输入侧那些行**被修正但未结案** —— 探测捕获了六份真实 payload（§0），这确认了矩阵的形状，但没有决定 agentao 从哪里取值。卡第 3 步。输入侧：`transcript_path` 指向什
  么（或者就保持 `null`）；`permission_mode` 的映射表或不发这个字段，**包括把它从 `PreCompact` 上摘掉**；
  `tool_response` 是包成一个自创对象、还是作为一处写明的偏离继续发字符串、还是等真正的工具输出 schema；那
  三个 agentao 私有输入字段（`turn_end_reason`、`compaction_type`、`reason`）的处置 —— 丢掉，还是收进一个
  `agentao` 子对象；以及取不到的字段是缺席还是显式 `null`。输出侧：§5.1 留白的那几行 —— `terminalSequence`（它要的传输通道正是 G1 在定的那条）、
  `sessionTitle`（那是产品决定、不是合规决定）、**两行存疑的** —— `PostToolUseFailure` 的 `decision` 与
  `SessionStart` 的 `continue:false`（探真实 CLI，或者明确宣布这是刻意的 profile 偏离；单凭文档两者都判不
  了，§5.1）、**`reloadSkills`**（让发现逻辑认识
  `.claude/skills` 树然后接受，或者接受并写明扫的是另一棵树）、**`suppressOriginalPrompt`**（先把 prompt
  加进阻断消息好让这个开关有东西可关，或者维持忽略），以及 **`defer` 的降级目标**（按提案是 `deny`，也可
  以是 `ask`）。它同时承接 §3 的来
  源产物决定 —— 把抓到的参考文档存进仓库，还是只记那张来源戳表、存档放在别处。

  **翻案清单。** 一行存疑的，是**逆着**参考文档更宽的那句话定下来的，所以探测可以把它翻过来 —— 而「把断言
  反过来」不是一份计划。每一支各自一次性列出要改什么：

  **两次探测都已回来**（§0）。两行按原样保留、只标上结果，因为一份事后再读的翻案清单，正是下一行存疑的
  该怎么规划的范本。

  | 探测若发现…… | 就要一起改这些 |
  |---|---|
  | **`SessionStart` 认 `continue:false`** —— ❌ **未触发**；实测为 `discarded` | §5.1 矩阵那一格由 `discarded` 改回 `honored`；§5.2 的 `SessionStart` 行在通知与 context 两个 sink 之外**再加一个控制结果**；§5.2.2 的路由表补回这里删掉的那一行 —— *整个会话，在它第一个 turn 之前* —— 并带上**两个界面的语义**（交互式：先渲染通知、然后不进入输入循环就退出；`agentao run`：一个 turn 都不跑、`RunResult` 带上理由）以及一个 **headless 退出码**（轮内停止不需要它，因为那些走普通的 turn-outcome 映射）；**G2** 多出第七个决定（`SessionStart` 的结果类型与那个退出码 —— 之前的提案是 `3`，另一个是 `1`）；**第 4 步**补回该路由；§12 把「不停止测试」替换成**两个界面**的端到端停止测试，而它按今天的代码必然失败，因为 `cli/session.py:81` 把 dispatcher 的返回值丢了 |
  | **`PostToolUseFailure` 认 `decision`** —— ✅ **触发**，且 (2)–(4) 的答案选中了下面的*反馈*那一支 | §5.1 那一行去掉「存疑」—— **但那只定下了探针的第 (1) 问。** 这一支其余部分要在拿到 (2)–(4) 的答案**之后**写，而不是之前：§5.2 那一行填上探到的 `reason` 通道（模型／用户／只进 transcript）、原始错误是否保留在旁边、以及这一轮是否继续；**§5.4 会按两种互斥方式之一改动，由探针来选** —— 若 (2)–(4) 描述的是*反馈／逐事件*效果，那条条件的 `block > none` 行就直接变成无条件；若描述的是*结束这一轮*，那条行就被**删掉**，改由 `resolve()` 把这个 block 归一化成 `Stop(reason)`、从 **rank 1** 进场，而这同时还要改第 1 序的来源清单、**G9** 的那个括号、以及产出和读取它的 resolver 与消费者；§12 补上它已经预留的 `decision` 分支，**外加一个多 handler 聚合测试**，因为一个 `accept` 一旦被兑现就欠一条聚合规则（§5.1）。这一行**不许**做的是把 `PostToolUse` 的语义横移过来：它们共享的那一行钉的是形状、不是效果（§5.1） |

  两张清单都不是凭空加活：它们列的正是当初写好、又因为取了窄读法而被删掉或降为条件的那些小节。把它们写下
  来，就是为了不必再把那次删除重新推导一遍。
- **G9 —— 混装契约的 dispatch 与控制格（§5.4）。** 卡第 6b 步。分组方式、两组是否并发、**逐事件的控制格
  及其跨类优先级** —— `Stop`（「结束处理」这个类别；今天只由 `continue:false` 到达，exit 2 要先经事件表归一
  化，而一行存疑的若被 G7 探出效果是结束这一轮也可以加入，§5.4）压过事件
  decision、`PreToolUse` 上在 `resolve()` 已把 `defer` 降级之后是 `deny > ask > allow`、以及 `Stop` 上
  `end-turn > continue`（两套契约在这里含义相反）—— `reason` 平手裁决**只在胜出类别
  内部**，还有多条规则都产生 continuation 时用哪条契约的 `Stop` 上限。本计划提议：两组并发、上面那张格、
  上限的替代方案是 `min()`。
- **G10 —— 诊断 registry（§4.2）。** 卡第 2 步 —— `diagnostics[]` 在那里第一次有生产者。归属者（会话级，
  不是 dispatcher 级 —— 它每次分派都新建，九处构造点分布在六个文件里，其中两处在池 worker 内）、那把锁、能扛过插件重载的**稳定规则键**，
  以及重载与 `/clear` 时的生命周期。东西很小，但它决定了这个机制是「一条有用的一次性提示」还是「每次调用
  刷屏」或者「彻底沉默」。
- **G8 —— `PreToolUse` 的生命周期（§4.4）。** **部分结案（rev 23）。** 改写非法那一支已**实测**：上游拒绝该调用、原输入从不执行，所以计划的选择是作为合规落地、而不是「偏离安全」。**执行前校验器按维护者决定放弃** —— 不提 `jsonschema`、不加 `Tool.preflight()`，下面顺序里的第 2 步删除，§1 记下被缩小的承诺。仍然开放的是生命周期的其余部分。卡第 6 步。十个步骤，而最容易被略掉的是前半段：**hook 什么时候
  触发**（未知工具、输入校验失败时**不**触发；权限拒绝时**一定**触发 —— 也就是在 `claude-code` 模式下删掉
  `tool_runner.py:277` 那个跳过、在 `agentao-v1` 里保留它），然后才是聚合、按工具 schema 校验、**重判**、
  取交集（绝不向上放宽）、对改写后的输入重新确认、不重新 dispatch —— 外加两个 handler 改写同一个调用时的
  冲突规则。少了后半段，`updatedInput` 就是一条把参数洗过一个已经算完的裁定的路；少了前半段，每一个审计
  hook 都恰好对它存在意义所在的那些调用是瞎的。G8 同时管第 2 步依赖的那个 **pre-hook 校验器**：它相对参数
  修复站在哪、它会让今天能成功的哪些调用挂掉、要不要把 `jsonschema` 提成直接依赖（子集兜底已被推翻 ——
  §4.4），以及是加上工具自定义校验所需的那个纯 `Tool.preflight()` 接口，还是把第 2 步收窄成只做 schema
  校验。**以及一次不合法的*改写*该怎么办**（§4.4 第 6 步）：本计划拒绝该调用，因为另一条路会去执行 hook 正
  在替换的那个输入。原本用来定这件事的那句话根本不在参考文档里，所以 G8 用探测来定 —— 如果 Claude Code 确
  实回退到原输入，那就把它作为一处**写明的、偏离安全侧的偏离**采纳，而不是让它当默认值。
- **G5 —— `${CLAUDE_PLUGIN_DATA}`、exec 形式与 shell。** **shell 那一半已由实测结案（rev 23）**：2.1.251 用 `sh` 执行命令 hook 且忽略显式 `shell`，所以 agentao 的基线合规、该字段按「忽略并出诊断」处理（§2.4、§0）。不需要改 `executable=`。`${CLAUDE_PLUGIN_DATA}` 与 exec 形式**已在实施时关闭**（§0）。曾卡第 3 步。该占位符需要一个 agentao 没有的逐
  插件数据目录（位置、创建、生命周期）；`args` 需要在 `ParsedHookRule` 上加字段，并在
  `_dispatcher.py:353` 处加一条 exec 形式分支 —— 那里今天是无条件 `shell=True`；同一处还要决定
  `executable="/bin/bash"`（§2.4「基线 shell」）以及系统上没有 bash 时的兜底。

---

## 10. 不许回退的东西

agentao 领先两个 peer 的五处（对照文档 §7）。每一条都需要一个能扛过这轮改动的测试，而第一条正被第 3 步
实实在在地威胁着：

1. **provider 凭证被从 hook 子进程剥掉** —— §7.1。真正有风险的就是这条。
2. **`Stop` 重入上限这件事本身要在**（`_runner.py:157`，`stop_reentry_cap: int = 3`）。**上限本身是领先项，它取值 3 不是。** 快照里的数是 **8**：*"Claude Code overrides the hook and ends
   the turn after 8 consecutive blocks"*，而 `additionalContext` 触发的 continuation 走的是 *"the same
   loop protections … namely the `stop_hook_active` input and the 8-consecutive-continuation cap"*。
   在 `claude-code` 标签下继续用 3，等于让第 4 次到第 8 次重入在两个工具上行为不同 —— 正是 §1 存在要收
   口的那一类偏离，已作为第 12 条偏离列入 §7。所以上限按契约取值：`claude-code` 下 **8**，`agentao-v1`
   下 **3**。不许回退的是「上限存在」以及「第 4 条那个新的 continuation 来源落在上限之内、而不是绕开
   它」。
   上限挂在 `ChatLoopRunner` 上、按 turn 计；契约挂在规则上、按文件计。本计划提议：只保留一个计数器，拿
   它去和**产生本次 continuation 的那条规则**所属契约的上限比较 —— 于是纯 `agentao-v1` 的装法仍然是 3，
   混装的会话也不会为了从没提过要求的 hook 而被悄悄放宽。更简单的替代方案是「一个 turn 一个上限，取所装
   契约里的最大值」，代价是一份 `claude-code` 文件会替同一个 turn 里的 `v1` hook 抬高天花板。§11 把这个
   选择记为待决。
3. **`permissionDecision:"ask"`** —— 这边支持（`models.py:302`），codex 拒绝。能力表不得照抄 codex 那一
   行，而 §4.1 的 union 存在的意义就是让类型装得下它。
4. **`PostToolUseFailure`** 这边有、codex 没有。保留该事件；它既要一行能力，也要一个 sink（§5.2）。
5. **`prompt` handler 真能跑**（仅 `UserPromptSubmit`）。`SUPPORTED_HOOK_TYPES_BY_EVENT`
   （`models.py:217`）已经编码了这个限制；新表不得与之矛盾。

还有一条 `agentao-v1` 的保证该放在这里、而不是放进领先项：**`tool_runner.py:277-279` 那个 DENY 跳过在
`agentao-v1` 里保留。** `claude-code` 模式会删掉它（§4.4），所以测试必须把两半都钉住 —— 一个被拒的调用在
profile 下要 fork hook、在 v1 下不要。没有这个测试，两种模式会在有人「顺手简化」那个分支的第一时间静默合
流。

注意这份清单里刻意**没有**的东西：「串行、短路的 dispatch」—— 它读起来像一条领先项，其实不是。**在
`claude-code` 模式下执行是有界并发的**（§2.5）—— 短路**和**串行顺序在那里都是
合规偏离，只有 `agentao-v1` 保留它们。保持声明顺序的是**聚合与 `reason` 的平手裁决**，不是执行。

仍然不在范围、且不变的是：逐 hook 的信任哈希与 `HookRunSummary` 可观测性 —— 那是 codex 在结构上领先的
两条轴（对照文档 §6）。hook**执行**的有界并发不是在采纳 codex 的模型，那是参考文档本来的规定。

---

## 11. 其余待决问题

不是门槛 —— 可以在触及它们的那一步里决定。

1. **`claude-code` 模式下 `<stop-hook>` 回显没有抑制器 —— 已定案。** 今天由 `suppressOutput` gate
   （`_runner.py:1045`）；严格模式下让它 inert 在合规上是对的，所以 profile-1 里**回显是无条件的**。
   诱人的备选 ——「由 `hookSpecificOutput.agentao.suppressOutput` 接手」—— 并不可用：§3.3 已经把那个命名空
   间从 profile-1 里删掉了，而一条待决问题不能给设计小节删掉的面续命，何况那
   个面要存在还得配齐一个字段、一行能力表、一个消费者、一条聚合规则和一条输出内优先级规则。仍然开放的是
   **这个功能**、不是这个拼法：如果将来真需要在 Claude 形状的 hook 下抑制这条回显，它会连同命名空间一起、
   按 §3.3 那张价目表、在后续 profile 里到来（第 4 问）。`agentao-v1` 不受影响。
2. **`agentao-v1` 是冻结、还是会漂移？** §3 里写的是冻结，而「冻结」被限定在**契约面**上 —— 资源
   限额被明确划在外面。仍然开放的是契约那一半：如果将来某个字段只落在 `claude-code` 上，`v1` 的文件会静
   默地拿不到它 —— 现在说清比将来逐字段发现便宜。
3. **`if` 字段。** 它的处置已经定了，并且归 §2.4 —— **拒绝并告警**。这里剩下的开放问题只有「以后要不要
   做」：agentao 有带模式匹配的权限引擎（`permissions.py`），所以它够得着，但它自带一套 Bash 子命令语义、
   且按设计失败时放行。它不是一个接上去就完事的字段，而是一个等人提需求时再排期的子特性。
4. **profile 需要不止一个取值吗？** 在真的需要第二个之前只发一个 `claude-code@profile-1`，能让机制保持诚
   实，又不必第一天就维护两套契约。与之相关的问题是：什么时候**必须**升 profile —— 一个字段从 `ignore` 变
   成 `accept`，对一个本来就在发它的 hook 来说就是可观察行为的变化，所以那是一个新 profile，不是一次补丁。
5. **混装契约的会话里，`Stop` 用哪个上限？** §10 第 2 条提议用「产生本次 continuation 的那条规则所属契约
   的上限」，替代方案是「一个 turn 取最大值」。两者都站得住，唯一站不住的是不说。
6. **存档下来的参考文档放不放进这个仓库？—— 现在有实测了。** §3 要求 profile 的来源能解析成某个不可变的
   东西，而锚点**在 24 小时内就漂了**（`c984f918…` → `b727657a…`，§3）。今天仓库里没有任何东西持有被钉住
   的那份字节，所以一位想对着 profile-1 复核某条引文的评审**做不到**：去抓那个 URL 拿到的是另一个文件。把
   约 290 KB 上游文字放进 `docs/reference/snapshots/` 是最直接的答案，同时是一个再分发问题；只记那张来源戳
   表（URL、抓取时间、sha256、changelog 头部）是轻量的答案，但它**依赖仓库之外那份拷贝一直活着** —— 而这
   次漂移刚刚让这个假设变贵了。**已定（rev 23）：只留来源戳表，不入库**（§0）。代价是写明、而不是消掉 ——
   每一条引文都带着它的 `hooks.md:<行号>`，所以重抓之后能*定位*某条引文，但没法对着 profile-1 逐字节复核。
7. **值不值得为 Claude Code 建一套真实探针？** §3 的第二条出路 —— 它是唯一能让一个标签诚实地写上产品
   版本号的东西，代价是装一个 CLI 加一套行为测试。**rev 23 起已付掉一半**：CLI 装好了，一组探测也记录在案
   （`docs/reference/hooks-probe-2.1.251.zh.md`），但那是一份**记录**、不是可重跑的套件 —— 它会过期，
   不会跟随。在另一半到位之前，`profile-N` 才是准确的名字，而来源戳表承载真正已知的那部分。
8. **`reloadSkills` 落在 profile-1 还是 profile-2？** 以「消费者已经建好」为由接受它看着站得住，但那个消
   费者扫的是**另一棵树**（`~/.agentao/skills`，不是 `.claude/skills`），而且重载路径上没有锁，所以
   profile-1 忽略它。真正待决的是：skill 发现逻辑要不要认识 `.claude` 树 —— 那是一个远超 hook 范围的兼容
   特性 —— 还是「接受并写明差异」就够了。**G7。**
9. **`PostToolUseFailure` 的 `decision` 到底认不认，认了又做什么？—— 已结案（rev 23）。**
   **认，而且是加注**：reason 到模型、原始错误留在旁边、这一轮继续（§0）。关于*认不认*这份快照两种话都说
   了，关于*做什么*一个字都没说 —— 再怎么重读它也读不出这个答案，因为那条全局行钉的是 wire 形状，而该行成
   员的效果彼此互不相容。答案到手之后有两点值得留着。当初管着这段空窗期的「代价不对称」论证（不认一个它确
   实定义了的 `decision`，最多少一条 `additionalContext` 已经覆盖的反馈通道；认了它，就等于绑定到一个叫不
   出名字的效果上）是一条正确的**临时**规则、而不是正确的**长期**规则 —— 它一直是「无知时的稳妥做法」，不
   是一条发现。另外，本条早先的版本断言「认了会停掉一轮上游本会继续的对话」：那预填了 G7 存在着要取得的答
   案、和两节之外的翻案清单自相矛盾，**而且在事实上是错的** —— 这一轮会继续。

---

## 12. 验证

- **8 份 golden stdin payload**，每事件一份，按契约模式逐字节断言。对照文档 §9.1 的探针已经跑遍全部八条
  dispatch 路径，天然就是生成器。这些 golden 还必须钉住 **§5.3 的那条规则**：agentao 取不到的字段按 G7
  的决定要么缺席要么为 `null`，绝不能是一个看起来很像的编造值 —— `tool_use_id` 与 `duration_ms` 要在且要
  对，`transcript_path` 只能是 G7 选定的那个值、不能是别的，`source` / `reason` 要携带真实原因而不是一个
  常量。**forbidden 那一列同样是断言**：`Stop` 上不许有 `turn_end_reason`，`PreCompact` 上不许有
  `compaction_type` / `reason` / `permission_mode`，而欠 `permission_mode` 的那五个事件上它必须在、且取值
  在枚举内 —— 一个只检查「字段在不在」的 golden，今天在 `Stop` 上会通过，因为那里的值是常量
  `"workspace-write"`（`_payload.py:144`）。
- **golden 配置文件** —— **不带 `contract` 键**的官方嵌套形状（也就是拷贝文件那个场景，§2.2）、扁平形
  状、一份混用两种形状的文件（整份拒绝）、以及一份显式写了未知 contract 的文件（被禁用）。§2 的探针就是
  种子，而它当前从官方形状里解析出的是 `0` 条。
- **一张优先级矩阵**，而不是逐字段断言：`{0, 2, 其他}` × `{valid、schema_invalid、parse_error、plain、
  empty}`（§4.2 的五态）的完整网格，再叉乘 `continue:false`/`true`/缺省 与 `allow`/`block`/缺省，逐事
  件，跑在 `resolve()` 上。
  它必须覆盖 exit 2 的**全部三种**结果 —— 包括一个 stdout 为空、以 2 退出的 `PostToolUse` hook，断言它的
  stderr 送达了**模型**；一个 `SessionStart` hook，断言它的 stderr 送达了**用户**而没有进模型；以及一个
  以 **1** 退出并打印纯文本的 `SessionStart` hook，断言那段文本**没有**变成模型 context。
- **两个通用字段例外测试**（§5.1 那张矩阵）：一个 `PreCompact` hook 同时设 `systemMessage`
  与 `continue:false`，断言**两个都不生效** —— 没有用户通知、也不停止 —— 而同样的输出放在 `Stop` 上两个都
  生效；再对 `SessionEnd` 做同一对断言，那里整份 JSON 都被丢弃。第一条断言正是「`systemMessage` 没有逐事
  件门」的设计会挂掉的那条。**两个测试还都要断言不产生任何诊断**（§5.1 的两条轴）：
  丢弃是静默的，因为这个 hook 对上游合规；而「静默」正是那半会漂移却测不出来的东西 —— 一个把丢弃走成
  `ignore` 路径的实现，本条其余断言全都能过。它的镜像在下面那条前向兼容测试里：被 `ignore` 的字段必须产出
  且只产出一条。
- **一个通道正交性测试**：一个 hook 同时阻断并设置 `systemMessage`，断言阻断与用户通知都存活
  （§4.2 的合并 —— 一个只返回结论的 `resolve()` 会丢掉的那些通道）。
- **每个 stdout 状态各一个测试**（§4.2 的**五**种），而且第一个断言的是 v2.1.248 之后那一支：一个以 `{` 开
  头、以 `}` 结尾却解析失败的字符串，产出一条**用户通知**，并且在 exit 0 时**不会**成为
  `UserPromptSubmit` 的 context。断言相反方向，就是 2.1.248 之前的行为。另外还要：一个以 `{` 开
  头但**不**以 `}` 结尾的字符串仍是纯文本、并在 exit 0 时进 context；一个 `[` 开头的数组同样是纯文本；一
  个 schema 不合法的对象产出一条**用户**通知、同时动作继续，而**同一个对象配 exit 2** 时以 stderr 为理由
  阻断（v2.1.214）；以及一个 exit 1、stdout 为空的 hook 产出一条带 **stderr 首行**的用户通知。
- **一个 `SessionEnd` exit-2 的端到端测试** —— 不是 resolver 单测：一个真的以 2 退出的 hook，经
  `dispatch_plugin_session_end` 跑通，断言 stderr 抵达了用户 sink。那条路今天把 dispatcher 的返回值丢掉了
  （`cli/session.py:87`），所以一个 resolver 级的测试会通过、而特性并不存在。
- **一个「所有 handler 都跑」的测试**：两个匹配 hook、第一个阻断，断言第二个在 `claude-code` 模式下**仍
  然执行**、在 `agentao-v1` 下**仍然不执行**（§2.5）—— 外加一个确定性测试：无论谁先完成，聚合出的
  `reason` 都是声明顺序上的那个赢家。
- **一个合并限额测试**（§2.5、G6）：**两个插件**，各自都不超过逐事件上限，但合并后的 `SessionEnd`
  handler 数超限 —— 断言拒绝发生在合并处、且告警里带上两个插件名。单文件的测试在实现错误的情况下照样会
  过。
- **一个并发 dispatch 测试**：一批足够大的工具调用，让它们的 `PostToolUse` dispatch 互相重叠，断言每次
  dispatch 的每个 handler 都启动了（§2.5 的准入规则）—— 如果 G6 选了退路，就改为断言那条写明的弱承诺。
  它必须能在「只有逐 dispatch 上限、没有全局准入」的实现上失败。
- **一个 `updatedInput` 重判测试**（§4.4、G8），也就是那个安全用例：一个 `PreToolUse` hook 把已放行的
  `Bash` 参数改写成会被硬线扫描器拒绝的那种，断言该调用**被拒绝且从未执行** —— 以及它的镜像：一次通不过工具参数 schema 的改写。那一个是
  **曾经**按 G8 分支，现在已定案：探测发现上游拒绝该调用、**且原输入从不执行**（§0），正是本计划自己的默
  认，所以测试无条件断言它。有一件事它不能断言 —— 上游的*错误呈现面*：执行前校验器既已放弃，agentao 察觉
  不到这种不匹配，改写后的调用会到达工具并在那里失败。承载安全性质的那条断言是：**原始**参数从未抵达
  executor。再加一个确认测试，断言 Phase 2 的提示展示的
  是**改写后**的输入；以及一个「不重新 dispatch」测试，断言 hook 只触发一次。
- **一个 `Stop` 上限测试**：`claude-code` 下连续 8 次 continuation 被认，`agentao-v1` 下是 3（§10 第 2
  条），混装契约那种情况按 G9 的决定钉死。
- **profile 前向兼容测试**（§1、§4.2、§5.1），也就是封闭 schema 解析器会挂掉的那一类：一个同时发出 `terminalSequence`
  与 `systemMessage` 的 hook，断言那条通知被送达、那个不认识的字段被忽略、**没有任何 `hook error` 到达用
  户**、并且有一条诊断点名了这个字段；同一条规则的第二次调用再断言那条诊断**不重复**。**`watchPaths` 用
  的是同一个测试，而不是解析期拒绝测试** —— 解析器根本做不到那件事（§1 第三条规则），真正要断
  言的是紧挨着它的 `systemMessage` 照样送达。然后是改了处置的那几行。**其中两个是被忽略的字段** ——
  `suppressOriginalPrompt` 与 `reloadSkills` —— 各自断言**解析了、给了诊断、但不执行**：没有任何东西消费那
  个 flag，也不触发任何重扫（诊断里点明目录不匹配）。**第三个是被降级的取值，断言的东西不一样**：`defer`
  **是**被执行了的 —— 它变成 `deny`、理由里点名这个未实现的取值、工具从未运行。把三个一起说成「解析
  但不执行」，紧接着又要求 `defer` 拦下工具，这两句不可能同时成立。写这几个测试时有两个坑：一个 `suppressOriginalPrompt: true` 的测试如果断言的是「阻断消息里没有 prompt」，它今天**在完全
  不解析这个字段的情况下也会通过**（`_hook_dispatch.py:73` 本来就不含 prompt），所以断言必须落在*解析与诊
  断*上、不是落在那条消息上；以及一个 `hookEventName` 写着**别的事件**的 `hSO` 块必须让**整个对象**
  `schema_invalid` —— 里面的顶层 `systemMessage` **不**存活（§5.1 的 `hookEventName` 行）。
- **诊断 registry 测试**（§4.2、G10），这是这个机制最容易被静默做错的地方：同一条规则在一个会话里被 dispatch
  两次，即使两次的 dispatcher 对象不同，也只产生**一条**诊断；两个**并发**的工具事件产生一条、不是两条；
  而插件重载之后同一条规则会重新播报。
- **四个 `PreToolUse` 生命周期测试**（§4.4、G8），也就是参考文档写得很明确而 agentao 没做到的那处：一个权
  限引擎**已经拒绝**的调用仍然触发 hook，且之后裁定仍是 `DENY`（`claude-code`），而同样的情形在
  `agentao-v1` 下**不**触发；一个因未知工具名被拒的调用**完全不**触发 hook。**那两条校验器测试删除**
  （§0）：既然没有执行前校验，就不存在可供它们观察的那次拒绝，「输入过不了 schema 就不触发 hook」也就没有
  东西能让输入失败。§1 用一条写明的「不承诺」取代它们 —— 这正是「列出非承诺、而不是悄悄删掉一条测试」的
  意义所在。
- **混装契约测试**（§5.4、G9），逐个决策事件各一个 —— `PreToolUse`、**`PostToolUse`**、`Stop`、
  `UserPromptSubmit`、`PreCompact` 与 **`PostToolUseFailure`**，最后这个是无条件的：`continue:false` 是
  从 §5.1 通用字段那一行到达它的，与它事件级的 `decision` 无关；而那个 `decision` 现在也被兑现了（§0），
  所以**两支都不再受门控** —— 这个区分只作为「探测之前它为什么就在这个集合里」的理由留存。**两种形状，不是一个模板。** 在两套契约都带决策的那四个事件上（`PreToolUse`、
  `UserPromptSubmit`、`Stop`、`PreCompact`）：一条 v1 规则阻断、一条 Claude 规则在声明顺序上排它后面，断
  言那条 Claude 规则**仍然执行了**、合并后的结论是那个 deny、并且被呈现的 `reason` 是声明顺序的赢家，与哪
  一组先完成无关。在只有 profile 带决策的那两个上（`PostToolUse`、`PostToolUseFailure`），这套设置造不出
  来 —— `agentao-v1` 把两者都走 `_dispatch_lifecycle`（`_dispatcher.py:126,134`）、不给任何 stdout 决策面
  —— 于是改成：v1 规则贡献一个**可观察的副作用**（它写的一个文件，或它的 attachment 记录），Claude 规则贡
  献控制。**`PostToolUse` 上要分两个分支，因为它那两种控制含义相反**（§5.2.2）：用 `decision:"block"` 时，
  断言原始工具输出**被保留**、`reason` 送到**模型**那里、并且这一轮**继续**走向下一次模型调用；用
  `continue:false` 时，断言这一轮**结束**、不再有下一次模型调用。`PostToolUseFailure` 上 `continue:false`
  那支和 `decision` 那支现在**都是无条件的**：探测把 `decision` 打开了（§0），所以那个双 handler 测试随之
  落地 —— 两个 profile 的 `PostToolUseFailure` handler 都返回 `block`，断言合并后的结论、以及呈现的
  `reason` 是声明顺序的赢家（§5.1 对每一个被兑现的 `accept` 所要求的聚合规则）。它在 **rank 2** 合并、走
  §5.4 那条现已无条件的 `block > none` 行，因为探针 (2)–(4) 的答案把它的效果归入反馈类；测试直接断言那些
  答案：`reason` 到了模型、原始错误留在旁边、之后确实还有一次模型调用。无论哪一支，v1 那条都必须**跑了**，并且把 v1 规则放在声明顺
  序的*前面*，好让「遇到 profile 的控制就短路」的实现挂在这条上。「profile 的控制生效了」本身不是断言 ——
  它正是这两个分支要区分开的那个东西。拿单一模板去套这两个事件，得到的是一个写不出来的测试。然后是这张格
  存在的理由那三个，「任一 deny 即 deny」一个都表达不了：**`continue:false` 对 `block`**（stop 压过它，呈
  现的理由是 stop 那条）；**`ask` 对 `allow`**（`PreToolUse` 上 ask 活下来 —— `deny > ask > allow`，`defer`
  在合并之前就已降级）；以及 **v1 的 `blockingError` 对 Claude 的 continuation**（`Stop` 上），断言这一轮结束、被丢掉
  的 continuation 变成一条点名了它那条规则的用户通知、并且呈现的 `reason` 是结束那条的、不是 continuation
  那条的。
- **一个跨 worker 边界的 `PostToolUse` 停止测试**（§5.2.2、G2），端到端而不是跑在 resolver 层，而且要用
  **两个完成顺序可交换的工具**：两次调用在同一批里，hook 挂在*声明顺序靠后*的那一个上、发出
  `{"continue": false, "stopReason": "…"}`。断言 (a) 这一轮结束、不再有下一次模型调用；(b) **两个**
  tool-call id 在历史里都仍有一条 `role:"tool"` 消息、且按 plan 顺序排列 —— 这正是 `format_batch` 逐 plan
  维持的不变式（`tool_result_formatter.py:113-128`），也正是中途中断会破坏的那条；(c) 呈现的理由是那条停止
  规则的；(d) 两个 worker 以相反顺序完成时结果完全相同 —— 这一条是按完成顺序实现的版本会挂掉的断言。按今
  天的代码写，它在 worker 之上的任何一层都过不了：`ToolRunner.execute` 只返回 `(bool, list)`
  （`tool_runner.py:249`），而 chat loop 只读那两个（`_runner.py:773`）。
  **但两个调用并不能覆盖 G2 真正新增的那两条规则**，所以还要跟两个测试：
- **一个排队兄弟测试 —— 连同它的保证一起放弃（G2 取第 (ii) 支，§0）。** 落地的是两种情况下都成立的那条
  不变式：每个 plan 都产出结果与 `role:"tool"` 消息。下面的分析保留，因为它记录的是这个保证**为什么被放弃
  而不是被悄悄弱化**，也因为它描述的那个接缝，是将来某一版若要恢复该保证必须先建的东西。
  **原文如下 —— 而它按今天的执行器根本写不出来，这本身就是结论。**「九个短工具、停止挂在靠前的那
  个上」是一处竞争：停止那个工具的 worker 会被释放，可能在断言跑到之前就把第 9 个 plan 取走，于是测试通
  过、却根本没有任何东西排过队。**把 plan 2–8 闩住并不能解决它。** `PostToolUse` hook 确实是在 worker 自
  己那个任务*内部*跑的（`tool_executor.py:468-471`），因此 hook 执行期间八个 worker 确实都忙着 —— 但那个
  `Stop` 要等 dispatcher 解析完输出并返回之后，才对这个 worker 之外的任何东西可观察，而 `_execute_one` 紧
  接着就在 `:473` 返回、把 worker 放回池里。「停止已存在」与「worker 取走 plan 9」之间的这段间隔，测试线程
  进不去。本文早先的版本把那个闩的构造说成是确定的；它不是，而它看起来确定的原因，是它证明的是另一件事 ——
  *hook 执行期间*的占用，对*停止被观察时*队列里还有没有东西，什么都没说。

  这条测试真正需要的，是**在「把停止挂到 `ToolExecutionResult` 上」与「worker 的 future 完成」之间的一个同
  步点**，而这样的接缝并不存在：池是内联构造的、cap 是字面量（`ThreadPoolExecutor(max_workers=8)`，
  `:189`），`execute_batch` 既不接受 executor 也不接受 cap，执行器在那个位置也没有暴露任何回调
  （`output_callback` 是*工具*的流式输出属性，不是这里的接缝）。所以 **G2** 的 (c) 要在两种生产改动里选一
  个：在「挂上」与「返回」之间发一个测试可见的回调／事件，或者让 executor / 准入可注入、由测试决定何时释放
  worker。两者都不大；但都不是白来的，而其中之一是这条测试能存在的前提。

  **而接缝本身不是一个可以单独放弃的东西** —— 它只能和它所执行的那条规则一起放弃。本文早先的版本保留了排
  队兄弟这条保证，却允许 G2 不加接缝、把这条测试降级成一个「可能空过」的批次结果断言。那是一条没有任何验收
  会去执行的规则：一个在计划要求「让它跑」时却取消了排队兄弟的实现，能通过本文件里的每一条测试。所以 G2 选
  的是一**对**（G2 的 (c)）：要么这条保证成立、接缝随它一起落地，而本条测试跑它的非空过形态 —— 批次、停
  止，外加一个能证明「停止变为可观察时尾部仍在排队」的同步点；要么这条保证被放弃，§1 记下只承诺批次结果、
  而「停止那一刻排队与否」**未定义**，本条也随之缩到仍然可测的那部分：**每个 plan 仍产出一个结果和一条
  `role:"tool"` 消息** —— 它不需要接缝，也是两种情况下都成立的那条不变式。本计划不会发出去的是第三种组
  合：**有承诺、没接缝。**
- **一个停止仲裁测试。** **两个**工具各返回**不同**的 `stopReason`，把完成顺序交换后跑两遍，断言两次呈现
  的都是 **plan 顺序**的赢家。上面那个单停止测试挂不倒「按完成顺序」的实现，因为只有一个停止就无从仲裁 ——
  能挂倒它的是这一条。
- **它在 `PreToolUse` 上的伴生测试**：同样的输出结束的是**这一轮**，并且不会被记成一次权限 `deny` —— 工具
  不跑、不产生 `DENY` 裁定、这一轮的结果是那个 `stopReason` 而不是一条「工具被挡」的消息。
- **一个 `SessionStart` 不停止测试**（§5.1，`hooks.md:1009`）：一个 `SessionStart` hook 发出
  `{"continue": false, "stopReason": "…"}`，断言会话**照常启动**、第一个 turn 照跑，**并且不产生任何诊断**
  —— `discarded` 是一次投递结果、适用静默规则（§5.1），出诊断会把它错报成 agentao 的能力缺口。配套再断言同
  一份输出里的 `systemMessage` **确实**被投递，这正是这两个字段在该事件上的分野。测试必须说明自己钉的是哪
  一种读法 —— 而现在它钉的是一次**实测**、不是一个选择：2.1.251 启动了会话、跑完了这一轮，那个 reason 哪儿
  都没出现（§0）。翻案清单继续留档，是给下一行存疑的用的，不是给这一行的。
- **一个 headless 的 `SessionEnd` 通知测试**（§5.2.1），跑在 `_run_pipeline` 这一层而不是 resolver 层：一个
  以 2 退出的 `SessionEnd` hook，在 `agentao run --output-format json` 下断言它的 stderr 进到了被输出的
  `RunResult` 里。按今天的顺序写，这个测试是失败的 —— 因为 `_emit` 在 `run.py:814`、dispatch 在 `:815`。
- **一个通道限额测试**（§6）：一个 exit 2、**stderr** 超过第二层上限的 `PostToolUse` hook，断言送往模型
  的那个字符串被限住并落盘 —— 这正是「只点三个具名字段」的限额覆盖不到的场景（§6）。
- **表驱动**的 event × 字段 × 退出码测试，跑在 §5.1 那张表上 —— 于是加一行是改数据，缺一行是测试失败而
  不是静默。
- **一个命名空间缺席测试**（§3.3）：一个 `claude-code` hook 发出的 `hSO` 对象里**同时带
  `"hookEventName": "Stop"` 与** `agentao.blockingError`，断言这一轮**没有**被阻断、有一条诊断点名了这个
  未识别键，而同样内容在 `agentao-v1` 下（顶层 `blockingError`）**确实**阻断 —— 用它钉住「这个扩展不在
  profile-1 内」，而不是半吊子地存在着。那个判别符不是装饰：不带它，这个对象会先撞上 §5.1 的
  `hookEventName` 规则、整个 `hSO` 变成 `schema_invalid`，于是测试通过、量的却是完全另一套机制 —— 这一轮
  没被阻断是因为对象被整个否掉了，不是因为那个命名空间不认识。第二条断言把这层差别钉住：同一个对象里作为
  **兄弟**的 `additionalContext` 仍然被投递。
- **每条被保留的领先项各一个反向测试**（§10），凭证剥离排第一。
- `uv run python -m pytest tests/` 与 `uv run ruff check .` —— lint gate 是必需的 CI 检查，pytest 全绿并
  不够（`CLAUDE.md`，「Testing」）。
