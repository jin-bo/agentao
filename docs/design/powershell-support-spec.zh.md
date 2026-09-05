# PowerShell 支持规范 —— 让 shell 地板认方言

> ⚠️ **仅设计，未授权实施。** 本文是**规范**：只写现在必须怎样。它不讲历史、不讲同行项目怎么做、不讲
> 上一版为什么错 —— 那些在评审记录与证据文件里。**每条规则只在本文 §2 定义一次，带一个稳定 ID；本文其余
> 部分与文件集里的其他文件只引用 ID，不转述。** 改一条规则就是改本文 §2 的那一行；`scripts/check_design_set.py`
> 核每个 ID 恰好定义一次、每个引用存在、每个 ID 至少有一条门槛与一个 PR。

**日期：** 2026-09-05
**状态：** **rev 45** —— PR-1、PR-2、PR-3、PR-5 已实施、PR-4 实施中（实现文件 §5、§5.1–§5.4）；拆分版（rev 25）经十七轮完整安全评审后的修订（其间两次**结构修订**：rev 32 契约外移为可类型检查的模块、规则拆成一 ID 一 MUST；rev 42 给契约里有实体的函数补上行为测试。两次都无语义偏离，见评审记录），以 rev 24（冻结于 commit `e01293f`）
为底；**相对 rev 24 的每一处语义偏离都列在评审记录的 rev 25 至 rev 46 行**，不在这里转述。四十轮评审、三百零一条发现的记录在评审记录文件。**独立于本规范的 PR-0**（子代理没有权限引擎，一处已实测的活缺陷）
已拆出为 `subagent-runtime-safety-plan.zh.md`，其引擎那一半应立即修。
**Anchors:** agentao `main@3537753`（2026-09-01）；codex `openai/codex@b7cd519c76`（2026-08-31）；
pi-mono `@853a80d26`（2026-08-28）。本文自身不含 `file:line` 引文；引文全部在证据文件。
**文件集：** `powershell-support-spec.zh.md`（本文，规范）· `powershell-support-contracts.py`（数据契约与流水线，可类型检查，本文 §3 至 §5 的正文）· `powershell-support-implementation.zh.md`
（PR 阶梯与模块归属）· `powershell-support-gates.zh.md`（门槛矩阵）·
`../reference/powershell-support-evidence.zh.md`（证据）· `powershell-support-review-log.zh.md`（评审记录）·
`subagent-runtime-safety-plan.zh.md`（原 PR-0）。
**约定：**
- 规则 ID 形如 `族-NN`。本文的族：`TOOL` `SPEC` `LAUNCH` `ENV` `IMG` `LADDER` `CFG` `TOK` `LOWER` `WRAP`
  `NAME` `EFF` `CMD` `BASH`。子代理计划的族：`SUB` `MCP` `ENG`。
- 不带「本文」前缀的「§2.x」「§3.x」与「证据 §4」一律指证据文件的小节；指本文自己的节一律写「本文 §N」；
  「PR-N」指实现文件的阶梯；「Gnn」「Gnn-mm」指门槛矩阵的行。
- 本文 §2 各表「规则」列的每一句都是 MUST；「为什么」只允许一句，长论证在证据文件与评审记录。
- **一行一条规则。**「规则」格不超过 900 字节、不超过三个句号（`scripts/check_design_set.py` 核）；一条规则要说多件事时拆成子规则
  `族-NNa`、`族-NNb`……紧跟母行，母行只留判据或核心 MUST。子规则是母规则的成员：引用母 ID 覆盖全部子规则，门槛与 PR 点名
  母 ID 即视为覆盖子规则（`--list` 列出只靠母 ID 覆盖的子规则，好逐步点得更准）；契约文件的锚点按母 ID 核。
- **不透明（opaque）= 地板返回 `hardline:…-opaque` ⇒ DENY**（TOOL-03）。「放行」= 地板不拒绝，交给权限
  规则与工具自己的确认设置。

---

## 1. 状态、范围、威胁模型

**范围。** 在 Windows 上用 PowerShell 跑模型的 shell 命令，以及 `agentao/permissions_hardline/_scanner.py`
必须先变成什么样。注入能力、子代理路径及其并发、registry 来源、MCP 所有权、两个 composition root、宿主
工具替换、地板*与子进程*两侧的解释器与裸词解析、shell profile、继承来的函数、根本不碰 `PATH` 的名字重绑，
以及 Windows 命令行序列化都在范围内；shell 工具的**两个交付面**（前台与 `is_background=true` 的后台）同样在内。**不在范围内：** WSL；macOS/Linux 上的 PowerShell；以及本设计只
收窄、不关闭的两处竞态（本文 §7）。子代理与 MCP 的并发与所有权在子代理计划（本文 §6）。

**威胁模型。**

| 一侧 | 内容 |
|---|---|
| 不可信输入 | 模型写出的 body；工作树里的任何文件与二进制；**工作目录本身**（Windows 的 DLL 搜索顺序含当前目录，LAUNCH-09）；子进程继承的**整个**环境 —— 不只 `PATH` 条目、`BASH_ENV`、`ENV`、`BASH_FUNC_*`、`SHELLOPTS`，还有每一个按键改变可信程序行为的变量（`GIT_CONFIG_*`、`NODE_OPTIONS`、`PYTHONPATH`、`LD_PRELOAD` 一类），因为宿主的环境可以被工作区影响（direnv、IDE 的终端环境、devcontainer）（ENV-06）；机器 `PATH` 上任何用户可写的目录；CurrentUser 模块目录；在「解析」与「spawn」之间被写入的配置或映像 |
| 可信输入 | 用户级 `permissions.json` 的 `shell` 块；宿主的构造参数；「子进程主体写不了」的目录（IMG-01）；宿主信任的代码签名；宿主的 identity allowlist（**仅作附加**，IMG-03）；`enable_hardline=False`（仅构造参数，不在任何 `permissions.json` 里）会关掉整个地板的**裁定** —— 那是宿主的信任决定，子代理按身份继承它（SUB-01）；LAUNCH-08 的计量与 LAUNCH-09 的编码不在此列，「这条命令行拼不出来」不是政策问题，关掉它们等于拿一个 DENY 换一个编码异常 |
| 执行主体 | 子进程将要以之运行的那个 token。提权运行的 agentao 自己就是管理员，于是可信集为空、阶梯走空（IMG-01、LADDER-03）—— 那是裁定，不是例外 |
| 守卫的资产 | 地板的 18 类不可恢复操作（§3.5）扩展到 PowerShell 与 cmd；「未被确立为惰性的程序不得**启动**」（EFF-04）；「解释器与裸词在地板的环境里解析」（ENV-01）。**封闭集关掉的是启动哪个程序，不是那个程序跑了什么：** 一个可信工具链按设计执行工作树内容（`git` 的 hooks、`npm` 的 scripts、`make`、`pytest`、`cargo build`）是产品目的（本文 §7.1）；那些代码做什么，只有 18 类地板管得着，而且它管的是命令文本，不是子进程的行为 |
| 明写不关闭的残留 | 会话配置 TOCTOU；解释器替换（本文 §7；门槛 G21 的两支刻画性探针）；**前奏切到工作目录之后还剩多少，按 rung 未定** —— Windows 加载器在 `LoadLibrary` **发生时**才用当前目录，而 PowerShell 的位置是运行空间状态、未必改得动进程的当前目录（证据 §3.22 (f)），所以 LAUNCH-09 关掉的确定是「进程创建到前奏切目录」这一段，之后各级各自剩多少由探针 G21-17、G21-18 实测落定（本文 §7.1）；**launcher 安装根在 spec 构造之后变得可写**（内容未改）—— IMG-02 把进程内条目的映像半绑到那个**已认证的** launcher 映像，认证在构造时一次完成，而 spawn 前的重哈希只比内容（IMG-07）、执行器的复核只比文件系统身份与内容身份（LAUNCH-01d），三者都不重看访问掩码；被放行的可信程序从**自己的**配置根读到的东西（`~/.gitconfig` 的 `core.fsmonitor`）—— ENV-06 让环境不能再重定向这个根，但一段惰性的写入可以写它，与工作树 hooks 同类 |

**今天 → 目标。** 目标列只写 ID；定义在本文 §2。

| | 今天 | 目标 |
|---|---|---|
| 模型可见工具 | `run_shell_command` | 同名，名字受守护（TOOL-01） |
| Windows 上的方言 | 经 `%COMSPEC% /c` 的 `cmd.exe` | 阶梯 `pwsh` → `powershell.exe` → Git Bash → `cmd`（LADDER-01） |
| 地板的门 | 工具名 | 工具名 + 随调用传入的方言与 rung，判定用的那一个 spec 贯穿到启动（TOOL-04、SPEC-01、SPEC-02、SPEC-08） |
| 分析模式 | 对原始文本做正则 | regex（posix、cmd）或 lowered（powershell）（TOK-01、LOWER-01、CMD-01） |
| 可运行目标 | 任何东西 | 封闭可运行集，两半独立（IMG-02、IMG-03、IMG-04、NAME-01–03） |
| 无法分析的输入 | 不匹配即放行 | 不透明 ⇒ DENY（TOOL-03、SPEC-01、SPEC-02） |
| 子进程环境 | 继承 | 封闭透传集 + 取值检查 + 过滤 PATH、`PATHEXT` 与各级钉值（ENV-01–06）；解释器不以工作目录启动（LAUNCH-09） |
| cmd 启动 | `%COMSPEC% /c` | 单字符串 + `executable=`（LAUNCH-03） |
| 后台执行 | 独立的 `Popen(shell=True)` 路径 | 与前台同一份请求、同一套复核（LAUNCH-01） |
| 子代理 | 没有引擎 | 按身份持父级引擎/fs/shell（子代理计划 SUB-01） |

---

## 2. 强制不变量

### 2.1 `TOOL` —— 工具与规则标注

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **TOOL-01** | 模型可见的 shell 工具只有一个，名字保持 `run_shell_command`。该名字下注册的任何工具 —— 构造时与 `add_tool(replace=True)` 之后 —— 都必须实现 `ShellSpecProvider`，否则注册失败并点名 | 地板按名把门，名字是它唯一的钩子；不实现 provider 的替换工具会让地板拿不到方言 | §2.2、§2.14、证据 §4 |
| **TOOL-02** | 权限规则增加可选 `dialect` 字段，取值 `posix`、`cmd`、`powershell`、`*`。带 `args.command` 条件而无标注的 shell 规则是 `unspecified`：在 POSIX 与 cmd 上照旧生效；PowerShell rung 遇到它时 spec 构造失败，逐条点名并列出全部四个标签。标注是方言、不是 rung：`dialect: "posix"` 同时覆盖 `git_bash` 与 `system_posix` 两级 | 一条为 bash 写的正则套在 PowerShell 文本上，既放行不了也拒绝不了正确的东西 | 证据 §4 |
| **TOOL-03** | DENY 是地板唯一的裁定；不透明永远是 DENY，永远不是 ASK；地板的 DENY 不可被 `allow:*` 遮蔽 | 三条 transport 自动批准 ASK | §2.6 |
| **TOOL-04** | 地板对 `run_shell_command` 的门 = 工具名 **加** 随调用传入的 `ShellSpec`：`_decide` 从该调用的工具实例（`ShellSpecProvider`）读 spec，传给 `decide_detail`，后者转给 `hardline_check`；`PermissionEngine(` 的 150 处调用点不改 | 引擎在 agent 之前建，方言是执行器的性质、不是引擎的构造参数 | §2.9、证据 §4 |

### 2.2 `SPEC` —— `ShellSpec`

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **SPEC-01** | `ShellDialect` 只有 `POSIX`、`POWERSHELL`、`CMD`、`UNKNOWN`。`UNKNOWN` 与地板不认识的任何取值 ⇒ 在匹配任何规则之前返回 `hardline:unknown-dialect-opaque` | 宿主 executor 正是无标注方言进来的地方；回退到 POSIX 扫描器等于用错的模式报一个干净的地板 | §2.9、证据 §4 |
| **SPEC-02** | `rung` 是 spec 的第二个字段，取值 `pwsh`、`powershell`、`cmd`、`legacy_cmd`、`git_bash`、`system_posix`（`legacy_cmd` 只在翻转前合法，LADDER-05）。合法配对是枚举的（本文 §3 `LEGAL_PAIRS`），在 spec **构造时**校验，失败点名那个配对；漏到地板的非法配对或不认识的 rung ⇒ 在匹配任何规则之前返回 `hardline:unknown-rung-opaque` | 方言选分析方式，rung 决定封闭集政策是否生效；「不认识 → `system_posix`」会整个绕过封闭集 | §3.13 |
| **SPEC-03** | `policy_enabled` 不是一个可以与 rung 各说各话的存储字段：构造时校验三条交叉不变量 —— `policy_enabled ⇔ rung ∉ {system_posix, legacy_cmd}`；`policy_enabled ⇒ launcher 与 pinned_env 都在`；`¬policy_enabled ⇒ 两者都是 None`，任一不成立即拒绝构造；漏到地板的按 SPEC-02 的 `hardline:unknown-rung-opaque` 拒（本文 §4 的 `validate` 在准备环境之前跑）。`system_posix` 与 `legacy_cmd` 的封闭集政策**关闭**（TOK-02、EFF-*、IMG-02、BASH-01 都不生效，裁定与今天相同）—— 前者直到本文 §7 q4 定案，后者直到 PR-7 删除它；`git_bash` 政策开，它的语法闸是 BASH-01 | 在 Linux 上三者都是每一位现有用户的行为变更，默认值要被选出来、不是继承下来 | §3.5 |
| **SPEC-04** | `filesystem_is_local: bool`，字段缺席即 `false`。「本机」只有一个意思：子进程打开的那条路径就是地板 stat 过的那条路径；同一宿主上的容器、chroot 与 mount namespace 都不算 | 在错的文件系统上做的检查不是检查 | §2.9 |
| **SPEC-04a** | 这个字段由执行器经 oracle 显式声明（本文 §3 `target_filesystem_is_local()`），本文 §3 的两个 spec 构造器都写入它的答案并进 `fingerprint` 投影；线上缺席仍读作 `false`，答不出（该方法返回 `None`）也读作 `false`（那是更严的一侧：非本机要 oracle 答每一问）；**「答不出」要有落脚处** —— 签名写 `-> bool` 的话，一个答不出的 oracle 只剩抛异常一条路，而那会在政策关闭的两级都还没选出来之前就穿出 `select_rung`。 | 没有一处写入它的构造器，等于这个字段恒为假 —— SPEC-04 想区分的「真正的本机」永远造不出来 | §2.9 |
| **SPEC-05** | **一个 oracle 绑定一个执行主体**：它的每一个与主体有关的答案（基础环境、PATH 条目、映像解析、发现、钉值环境）都是**对那个主体**答的，接口逐个显式收 `subject`，收到与它绑定的主体不同的值 ⇒ 拒绝作答、该 rung 未认证。 | 裸词搜索是目标机上的一次文件系统操作，不是地板手里的一个事实；在地板机器上跑的预检核对的是错的机器 | §3.13 |
| **SPEC-05a** | **目标的项目根也由执行器给**（`target_project_root()`）：远端工作树的路径与宿主的不同，拿宿主的项目根去查「值落在项目根内」会放过目标上正落在项目根里的授权值。 | — | §3.13 |
| **SPEC-05b** | 非本机执行器欠三段义务：**解析**（IMG-06 的每一问、含 NAME-* 的裸词搜索，都针对目标作答）、**证明**（答案绑定目标的主体、目标的环境、子进程实际会打开的映像）、**启动**（LAUNCH-01 的请求原样运行）。**阶梯与预检也在目标上跑：** IMG-05 的发现、IMG-07 的身份读取、IMG-08 的三来源读取、IMG-09 的预检、ENV-06 钉值那一类的求值（`target_pinned_env`），以及 `launch()` 前的重哈希与重读，全部经 oracle 的对应方法（本文 §3 `IdentityOracle`）在目标上完成，spec 由此在构造时拿到目标侧的身份、预检结果与钉值环境（SPEC-07）。 | — | §3.13 |
| **SPEC-05c** | oracle 缺席或缺任一方法 ⇒ 该执行器声明的 rung **未认证**：launcher 映像未证明、`closed_env_established` 为假，于是每一个需要映像的命令词 —— 含映像半绑定到解释器的进程内条目（IMG-02）—— 不透明；只有政策关闭的 rung（SPEC-03）照旧运行 | — | §3.13 |
| **SPEC-07** | `ShellSpec` 构造后**不可变，且是整张对象图深度不可变**：它自己、`LauncherIdentity`、`ResolvedImage`、`PinnedEnv`、`env_passthrough` 与其中每一个容器都是冻结的（元组 / frozenset / 冻结记录 / `FrozenEnv`），任何一层的赋值抛错。**浅冻结等于没冻结** —— 构造后改 `spec.pinned_env.temp` 或往 `env_passthrough` 里追加一个键，provider 持有的仍是同一个对象、`fingerprint` 也不变（SPEC-08 因此看不出来），而子进程环境已经变了；`plan.decided`（SPEC-08a）同理 —— 它整个是冻结记录，`child_env` 与 `attested_images` 是其中的冻结值。 | 一份可变的 spec 在多线程读者之间就是 ENG-01 刚为引擎修掉的那种撕裂读 | §2.15 |
| **SPEC-07a** | 预检结果、launcher 身份与 `fingerprint` 都在构造时写入，构造发生在每一项预检**之后**、一次完成。`fingerprint` 是**规范投影**的哈希，投影逐字段定义在本文 §3 —— 它排除 `fingerprint` 自身与 `identity_oracle`（一个运行期对象，没有规范序列化形式），其余每个字段按声明顺序、以其规范形式（路径为规范化绝对路径，集合先排序）编码。 | — | §2.15 |
| **SPEC-07b** | 重解析只在三个事件上发生 —— 构造、`add_tool(replace=True)` 换入新的 `ShellSpecProvider`、宿主显式调用重解析 —— 每次产生**新对象**，工具实例原子地换引用；TOOL-04 每次调用读一次那个引用。后台子代理按身份共享同一个对象（SUB-01），所以不可变是它不撕裂的全部理由 | — | §2.15 |
| **SPEC-08** | **判定用的那一个 spec 对象贯穿整次调用：** TOOL-04 为一次调用读到的 `ShellSpec` 引用记在该调用的 `ToolCallPlan.decided.spec` 上；执行阶段构造 `LaunchRequest` 用的是那个对象、不是工具实例此刻的引用，请求携带它的 `spec_fingerprint`；`launch()` 时工具实例的当前引用与记录上的不是同一对象 ⇒ 拒绝该次调用，理由 `launch-spec-changed`（LAUNCH-01 的 DENY 通道），既不按新 spec 启动、也不按旧 spec 启动 | 判定与启动之间发生重解析时，按 PowerShell 分析、按另一 rung 启动的调用是一次没被判定过的启动 | §2.9 |
| **SPEC-08a** | **判定固定的不只是 spec，还有这次调用的输入：** `decide()` 把它逐字节扫过的那段 body、判定用的规范化工作目录、算出的 `ChildEnv` 与证明集，连同那个 spec 一起写进冻结记录 `ToolCallPlan.decided`（本文 §3 `DecidedCall`），一次写入；hook 改写输入后的重判（本文 §6，G08-02）重新判定并**整体替换**这个记录，不逐字段改；除判定与重判这两处，没有第三处写这个字段（G01-12 按 grep 断言）。 | — | §2.9 |
| **SPEC-08b** | **`launch()` 不另收 body、也不另收工作目录**（本文 §3 的签名里没有这两个参数）：启动的文本与工作目录只能是 `plan.decided` 里判定过的那两份，执行路径绝不回头去读工具此刻的参数；记录缺席（这个 plan 没经过判定）⇒ 拒绝该次调用，记录在场而它冻着的裁定是 DENY ⇒ 同样拒绝；`launch()` 也不另收 oracle —— 重哈希与重读只由这份 spec 冻住的那一个作答（SPEC-05）。 | 启动处能再收一次文本，就是一条判定 `Get-Date`、启动另一段危险文本的通道 —— 长度检查、映像证明与环境里的路径检查全都对着已经作废的输入 | §2.9 |
| **SPEC-08c** | **`decide()` 进门先把 `plan.decided` 作废：** 它有若干条**早退** —— provider 给的是 `Exhausted`、`validate()` 失败、外部输入答不出 —— 每一条都不写新记录，于是不先作废就等于把**上一次调用**判过的 body、工作目录与环境原样留给 `launch()`，而那次的裁定可能是放行、spec 对象还是同一个（SPEC-08 的比对因此看不出来）。这个字段在 `decide()` 之外零处赋值。 | SPEC-08a 的「整体替换」只在**写得成**的那条路径上成立；hook 改写出一段判不下去的输入时，早退正是它最可能走的那条路 | §2.9 |
| **SPEC-06** | spec 携带 PowerShell rung 的预检结果 `closed_env_established: bool`（IMG-09 写入）。`_decide` 跑的时候它是手里的值，不是将来的一次观测 | 地板在任何子进程存在之前裁定，事后子进程报告什么都改不了已给出的裁定 | §3.13 |

### 2.3 `LAUNCH` —— 启动请求与命令行

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **LAUNCH-01** | agentao 构造启动请求，执行器**原样**运行。请求是可判别的：`PosixLaunch`（`executable`、`argv`、`env`、`cwd`）或 `WindowsLaunch`（`application_name`、`command_line`、`env`、`cwd`）。 | 单一 `argv` 表达不了 cmd 行的「字符串 + `executable=`」；不带主体与映像，执行器可以一边照办命令行、一边启动别的东西；而一份没有复核义务的证据，执行器可以原样收下再打开别的文件 | §3.12、证据 §4 |
| **LAUNCH-01a** | 外加 `execution_subject`、`attested_images`（证明步骤解析出的规范映像 —— 它是交给执行器复核的**证据**，不是本机的强制手段：子进程自己解析后续命令词，强制靠 ENV-01）、`spec_fingerprint`（SPEC-08）、`workdir`（本次调用的工作目录，**规范化绝对路径**；交给方言的那份编码形态是命令行的一部分，不是这个字段）、`attested_images` 的**复核义务**（下条）与 `env`（**完整**的子进程环境，由 agentao 按 ENV-01–06 从基础环境算出 —— 本机的基础环境是剥离了 provider 凭据的进程环境，非本机的是 oracle 答出的目标基础环境 —— 执行器原样设定；设定不了整份 ⇒ 拒绝，不是尽力而为）。macOS 的 `sandbox-exec` 包装是 agentao 写进 `argv` 的一部分。 | — | §3.12、证据 §4 |
| **LAUNCH-01b** | `launch()` 阶段的拒绝（重哈希、重读、超长）经与地板相同的 DENY 事件浮出，reason 按本文 §3 词表，绝不是一个模型会重试的工具错误。 | — | §3.12、证据 §4 |
| **LAUNCH-01c** | **政策关闭的两级（`legacy_cmd`、`system_posix`）走 `LegacyLaunch`：** 逐字段等价于今天的 `ShellRequest`（命令字符串、今天的环境、本次调用的工作目录），不带 launcher 身份、不带 `attested_images`、不算 `ChildEnv` —— LADDER-05 与 SPEC-03 承诺它们与今天逐段相同，下面这条复核义务因此**只约束政策开启的 rung**。 | — | §3.12、证据 §4 |
| **LAUNCH-01d** | **对政策开启的 rung，执行器的复核是 MUST，不是「供参考」：** spawn 之前，它必须对**直接目标**（`application_name` / `executable`，以及 argv 里以路径点名的每一个映像）按 `attested_images` 里同一 `canonical_path` 的条目复核文件系统身份与内容身份；任一项不一致、或该目标在证明集里没有条目、或它复核不了 ⇒ **拒绝启动**并按 LAUNCH-01 的 DENY 通道报 `launch-attest`，绝不「尽力而为地启动」。复核覆盖不了子进程自己解析出来的后续命令词 —— 那由 ENV-01 与子进程环境负责，SPEC-05 的「证明绑定实际打开的映像」就是靠这两条合起来兑现的 | — | §3.12、证据 §4 |
| **LAUNCH-01e** | **前台与后台是同一份请求的两个交付面：** `ShellExecutor` 的 `run` 与 `run_background` 都只收 `LaunchRequest`，两者做同一套 spec 核对、重哈希、重读与 LAUNCH-01d 的复核，用同一份 `env` 与同一个起始目录；`is_background` 只选最后一步调哪个方法（本文 §3 `deliver`），选不了另一条启动路径；**政策开启的 rung 上两个交付面都不再出现 `shell=True` 与 `resolve_shell_executable()`**，政策关闭的两级按 LAUNCH-01c 与今天逐段相同、但两面共用**同一处**启动点，不是各自一处（G24-19）。 | 今天后台是一条独立的 `Popen(shell=True, executable=…)`：只改前台就成了「按 PowerShell 判定、按 cmd 启动」，而后台子进程照旧继承整份环境 | §3.12、证据 §4 |
| **LAUNCH-02** | `pwsh` / `powershell.exe`：`"<path>" -NoProfile -NonInteractive -Command "<前奏>; <body>"`，不传 `-ExecutionPolicy Bypass`，`Popen(list, shell=False)`，前奏与 body 是**一个**元素、绝不拆到多个参数。G18 的哨兵断言子进程收到的 body 与地板扫过的逐字节相同；G18 红 ⇒ 该 rung 改用 LAUNCH-03 的「单字符串 + `executable=`」形式；两种形式都红 ⇒ `-EncodedCommand <base64(UTF-16LE(前奏; body))>`，它逐字节可靠，代价是 EDR 启发式常把它当恶意，选它时把这一代价写进发布说明 | Windows 上列表形式一律被 `list2cmdline` 再序列化一次，「不重新加引号」只能靠哨兵核验 | §3.12 |
| **LAUNCH-03** | `cmd`：单一字符串 `"<path>" /d /e:on /v:off /s /c "cd /d "<W>" \|\| exit 98 & <body>"`（`<W>` 按 LAUNCH-09；是 `\|\| exit 98 &`，不是 `&&`），`Popen(..., executable=<path>)` 设 `lpApplicationName`；body 绝不再次加引号 | `/s` 剥外层引号，`/d` 跳过 AutoRun，`/e:on /v:off` 钉住状态 | §3.12 |
| **LAUNCH-04** | Git Bash：`"<path>" --noprofile --norc -p -c "cd -P -- '<W>' \|\| exit 98; <body>"`（`<W>` 按 LAUNCH-09，单引号字面量、内嵌 `'` 写作 `'\''`；是 `\|\| exit 98;`，不是 `&&`；引号内是**一个**元素），长选项在前，`shell=False`，环境按 ENV-03 与 ENV-06，`MSYS_NO_PATHCONV=1` | `-p` 挡继承函数与 `SHELLOPTS`、覆盖 `BASH_ENV` 与 `ENV`，但只护它启动的那个进程；顺序反了报 `invalid option` | §3.16 |
| **LAUNCH-05** | 前奏逐字节固定，四段，拼法在 LAUNCH-05a。`<E>`、`<V>`、`<H>`、`<C>` 是预检记录的 edition、version、`$PSHOME` 与生效控制台会话配置名，`<W>` 是本次调用的工作目录（LAUNCH-09），各以单引号 PowerShell 字面量代入、内嵌 `'` 双写；前四个无法这样编码 ⇒ 拒绝该 rung，`<W>` 无法编码 ⇒ 拒绝该次调用，都不换转义方式 | 守卫是同一个参数的后半截，没有任何 body 字节能抢在它前面运行 | §3.13、§3.20 |
| **LAUNCH-05a** | 四段，按序：(1) 身份守卫 `if ($PSVersionTable.PSEdition -ne '<E>' -or $PSVersionTable.PSVersion.ToString() -ne '<V>' -or [System.IO.Path]::GetFullPath($PSHOME) -ne '<H>' -or <C-check>) { exit 97 }` —— 只用 Core 与 .NET 静态方法，**不用 `Get-Item`**（它属 Management，而第 3 段正是让它不可用的那一步）；(2) `try { Import-Module -Name Microsoft.PowerShell.Management, Microsoft.PowerShell.Utility -ErrorAction Stop } catch { exit 97 }`；(3) `$PSModuleAutoLoadingPreference='None'; if ($PSModuleAutoLoadingPreference -ne 'None') { exit 97 }`；(4) `try { Set-Location -LiteralPath '<W>' -ErrorAction Stop } catch { exit 98 }` | 守卫在最前、切目录在最后：守卫失败时工作树一个字节都没被碰。先关门再用 Management 的命令是跑不通的 —— 实测见证据 §3.20a | §3.20 |
| **LAUNCH-06** | `<C>` 不得悄悄省略：找不到能在子进程内报出生效控制台会话配置的表达式时，除非预检在三个来源（IMG-08）都没发现配置，否则拒绝该 rung；`<C>` 不能用 `$PSHOME` 顶替 | 安装目录替一个 endpoint 名字作证是作不了的 | §3.20 |
| **LAUNCH-07** | 前奏一个字节都不改动 body 文本，也不在 body 外面套任何会改变它如何被求值的构造；守卫（LAUNCH-05）通过之前 body 一个字节都不跑。地板的保证是它扫过了那段 body，而前奏是地板从不改动的文本 | 一段第一条语句带副作用的 body 在前奏之后必须产生同样的副作用 | §3.13 |
| **LAUNCH-07a** | 前奏**确实**改变启动状态；改变限于逐级列出的这几项，此外不得再有 —— PowerShell 级：`Microsoft.PowerShell.Management` 与 `Microsoft.PowerShell.Utility` 被导入（LAUNCH-05 第 2 段）、`$PSModuleAutoLoadingPreference` 置 `None`（ENV-05）、当前位置切到 `<W>`、前奏自身留下的 `$?` 与 `$Error`；`cmd` 级：当前驱动器与目录、`ERRORLEVEL`，以及 `/d /e:on /v:off /s` 钉住的那些状态；`git_bash` 级：`$PWD`、`$OLDPWD` 与 `$?`。 | 「不扰动 body 的语义」按字面读，前奏自己每一条都违反它；一条写不出反例的 MUST 挡不住任何东西 | §3.13、§3.20 |
| **LAUNCH-08** | **本条只约束政策开启的 rung**（`legacy_cmd` 与 `system_posix` 不受约束 —— LADDER-05 与 SPEC-03 承诺它们与今天逐段相同，本文 §4 的 `floor` 因此在 `policy_enabled` 闸之后才做这道检查；翻转后 `cmd` 级受约束）。组装出的命令行（含前奏、参数与 body）超过平台上限 ⇒ 在任何分析之前拒绝，理由 `launch-oversize`；**绝不截断**。 | 截断落在 cmd `/s` 的「首尾引号」之间时，地板看到的结构与 cmd 执行的结构不同；差一个 code unit 的上限在边界上就是 `CreateProcessW` 失败；而按整块环境比 cmd 的 8191，一条超长的 `PATH` 混在小变量里就量不出来 | §3.12、§3.21、§3.22 |
| **LAUNCH-08a** | 上限按平台与 **rung** 自己的单位量，不用 `len()`：Windows 量的是**序列化后**交给 `CreateProcessW` 的那个字符串（列表形式先经 `list2cmdline`）的 UTF-16 code unit 数，非 BMP 字符计 2，**含结尾 NUL 不得超过 32767，即正文至多 32766**；**翻转后的 `cmd` 级另有 cmd.exe 自己的 8191 字符上限，取两者的小者**。 | — | §3.12、§3.21、§3.22 |
| **LAUNCH-08b** | **三套计量，各管各的，绝不相加**（定义在本文 §3）：**(i) Windows 命令行** `createprocess_units` —— 交给 `CreateProcessW` 的那个字符串的 UTF-16 code unit，**含结尾 NUL ≤ 32767**（正文至多 32766）；它只约束命令行，**环境不计入**（Windows 的环境变量另有各自 32767 字符的上限，§3.22）。**(ii) cmd 那一级** `cmd_line_chars` —— 命令行正文的字符数 **≤ 8191，不含结尾 NUL**（上游说的是「能在命令提示符里用的字符串最长 8191 个字符」，§3.22），与 (i) 同时成立、取先触发的那个；cmd 另**逐条**丢弃超过 8191 字符的继承环境变量，所以每一条 `键=值` 各自过这道闸。 | — | §3.12、§3.21、§3.22 |
| **LAUNCH-08c** | **(iii) POSIX** `execve_total_units` —— 全部 argv 与 envp 的字节数之和、每条含结尾 NUL、另计每条一个指针的开销（指针那一项是**推理**，PR-4 按目标内核核），比 `sysconf(ARG_MAX)`；再加逐条的 `MAX_ARG_STRLEN = PAGE_SIZE * 32`（**运行期查 `sysconf`，不写死**：4 KiB 页 131072，16 KiB 页 524288），`-c <body>` 只是其中一条。**理由按它是谁分：** argv / 命令行超限报 `launch-oversize`，环境某一条超限报 `launch-env-oversize`。 | — | §3.12、§3.21、§3.22 |
| **LAUNCH-08d** | 这道守卫要拿到最终命令行，所以它读本次调用的工作目录与算好的环境（本文 §4 的 `floor` 收这两样）；组装后的命令行含前奏与参数，PR-4 才存在，之前这道守卫退化为 body 长度（同一单位） | — | §3.12、§3.21、§3.22 |
| **LAUNCH-08e** | **未配对的代理项在计量之前就拒：** body、子进程环境的每个键与值、以及 `<W>`（LAUNCH-09b）里出现落单的 `U+D800`–`U+DFFF` 码位 ⇒ 拒绝该次调用，理由 `lone-surrogate`；三套计量都要先编码，而 UTF-16 与 UTF-8 都编不出它。 | 工具参数是 JSON，一个 `\ud800` 转义解码后原样留在字符串里；不先拒，本文 §4 的 `floor` 会在**任何分析之前**抛 `UnicodeEncodeError` —— 一个异常不是 DENY 通道上的裁定，它绕过本文 §3 的理由词表与 TOOL-03 的「地板的 DENY 不可被规则遮蔽」 | §3.12、§3.21 |
| **LAUNCH-09** | 阶梯各级（`pwsh`、`powershell`、`git_bash`、`cmd`）的子进程以 **launcher 自己所在的目录**为启动目录（`Popen(cwd=)`；该目录已过 IMG-01），绝不以工作目录启动。 | Windows 的标准 DLL 搜索顺序在 `PATH` 之前含当前目录（Safe DLL search mode 下排在系统目录之后）；解释器缺失或按需探测的依赖若被工作树里的同名 DLL 顶上，代码在前奏之前就跑了，而 IMG-01 只护应用目录那一半、ENV-01 只护 `PATH` 那一半 | §3.21 |
| **LAUNCH-09a** | 前奏中切到本次调用的工作目录 `<W>` 的那条语句紧接在 body 之前 —— PowerShell 级排在 LAUNCH-05 的守卫**之后**，`cmd` 与 `git_bash` 级没有守卫、它就是第一条（PowerShell `Set-Location -LiteralPath`、bash `cd -P --`、cmd `cd /d`，各按 LAUNCH-02 至 LAUNCH-05 的拼法），切换失败 ⇒ 子进程以退出码 98 结束、body 一字节不跑 —— 三种拼法都是 `cd … \|\| exit 98` 再接 body，**不是** `cd … && <body>`：`&&` 比 `;` 与 `&` 结合得紧，`cd` 失败时 body 的第二条命令照跑（cmd rung 上 UNC 工作目录因此退出 98，不再像今天那样被 cmd 静默换到系统目录）。 | — | §3.21 |
| **LAUNCH-09b** | `<W>` 无法按该方言的字面量规则编码（cmd：含 `"` `%` `^` `&` `\|` `<` `>` 与 CR、LF 任一 —— 换行把 `/c` 字符串切开，其后的文本作为另一条命令跑在 `/s` 的首尾引号之外，也跑在地板分析过的那份结构之外；任何方言含 NUL 同样拒）⇒ 拒绝该次调用，理由 `launch-cwd`。 | — | §3.21 |
| **LAUNCH-09c** | **本条确定关掉的只有一段窗口：从进程创建到前奏切目录之前。** 之后还剩多少**按 rung 未定，本文不预设** —— Windows 加载器在 `LoadLibrary` **发生时**才用当前目录，而 `Set-Location` 改的是 PowerShell 运行空间的位置、未必是进程的当前目录（证据 §3.22 (f)），`cmd` 的 `cd /d` 与 bash 的 `cd` 改的则是进程自己的；各级实际剩多少、被放行的工具链以什么当前目录启动，由探针 G21-17 与 G21-18 实测后写回本条与本文 §1、§7.1。 | — | §3.21 |
| **LAUNCH-09d** | 启动级缓解不可用：`SetDllDirectory("")` 要由**进程自己**在初始化早期调用并作用于整个进程，父进程替不了子进程调（§3.22） | — | §3.21 |
| **LAUNCH-09e** | **PowerShell 级的 `<W>` 另拒四个非 ASCII 的单引号：** 前奏用 `Set-Location -LiteralPath '<W>'`，字面量以单引号定界，而 PowerShell 的词法把 `‘`（U+2018）、`’`（U+2019）、`‚`（U+201A）、`‛`（U+201B）与 ASCII `'` 一同当作单引号定界符；`<W>` 只对 ASCII `'` 做双写，含另外四个任一 ⇒ 拒绝该次调用，理由 `launch-cwd`（LAUNCH-09b）。 | 一个名字里带 `’` 的工作目录能闭合那条字面量、把其后文本接进**前奏**（`C:\’; Start-Process calc; Write-Output ‘`），而地板只扫 body、不扫 agentao 自己生成的前奏 —— LOWER-01 一步都到不了这里。这四个能否同样靠双写转义**未实测**，所以取拒绝那一侧：一个带排印撇号的工作目录被拒是一次 `launch-cwd`，猜错则是一条注入 | §3.21 |

### 2.4 `ENV` —— 子进程环境

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **ENV-01** | 每一级子进程的 `PATH` = 过滤后的 PATH：只留「子进程主体写不了」的目录（IMG-01 同一谓词），剔除空的、相对的、工作目录与项目根内的条目 —— 每个条目先经 `oracle.canonicalize()` 归一（IMG-06）、答不出就剔除；由 agentao 自己搜索，绝不用 `shutil.which` | 把被剔目录留在子进程 PATH 里，等于让子进程解析地板刚拒绝的东西；`which` 在 Windows 上先搜当前目录；PATH 条目是环境里的原始字符串，而 §3 的 `path_within()` 收的是两条**已规范化**的路径 —— 不归一，`..`、短名与符号链接就能绕开那两道包含判定 | §3.11、§3.13 |
| **ENV-01a** | **过滤后的 PATH 每次调用只算一次：** `decide()` 求出这一份条目序列之后，判定期的裸词解析（NAME-02、NAME-03，以及 IMG-02 映像半要的那次 `resolve`）与子进程 `PATH` 都用**它**，两处谁都不重算、也不现问环境（本文 §3 `filtered_path_entries` 求出它，`resolve()` 与 `child_env()` 的签名各收它一次）。过滤谓词就是 IMG-01 的那一个，所以这一步收 oracle：访问掩码与 reparse 只有它答得出，非本机时更是（SPEC-05）。 | 各算各的两份会分岔：两次求值之间 PATH 变了、或过滤依赖本次调用的 cwd 与项目根，判定证明的映像就不是子进程会打开的那个 —— 而 LAUNCH-01d 的复核只覆盖直接目标，覆盖不了子进程自己解析出来的命令词 | §3.11、§3.13 |
| **ENV-02** | `PATHEXT=.COM;.EXE`，每一级都设；bash 忽略它，统一起见照设 | 对 cmd 与 PowerShell 的外部命令发现关掉 `.cmd`/`.bat`；PowerShell 是否先于 `PATHEXT` 找 `name.ps1` **未核实**（G21-13）—— 若是，`.ps1` 只靠「可信目录里没有攻击者的文件」挡住 | §3.13 |
| **ENV-03** | **保留键，每一级：** `BASH_ENV`、`ENV`、每一个 `BASH_FUNC_*` 条目、`SHELLOPTS`、`BASHOPTS`、`PATH`、`PATHEXT`、`PSModulePath`、`NoDefaultCurrentDirectoryInExePath`、`ComSpec` 与 `MSYS_NO_PATHCONV` 由 agentao 决定 —— 要么按 ENV-01、ENV-02、ENV-04、ENV-05、ENV-06 与 LAUNCH-04 钉值，要么**不出现**在子进程环境里 —— 任何来源（含 ENV-06 的用户扩展）都透传不进它们；不只 bash rung，因为被放行的可信命令在任何 rung 上都可能再起一个 bash（Git for Windows 的 `!` 别名与 hook 经它自带的 `sh.exe`） | `-p` 只护一个进程，环境贯穿整棵树：可信 `git` 经 `/bin/sh -c` 跑别名，那个 bash 就从继承环境导入 `BASH_FUNC_git%%` | §3.14、§3.16 |
| **ENV-04** | cmd rung：`NoDefaultCurrentDirectoryInExePath=1` | cmd 裸词先搜当前目录 | §3.13 |
| **ENV-05** | PowerShell rung：可信表要的两个模块由前奏**显式导入**、随后才关掉自动加载（LAUNCH-05 的第 2、3 段 —— 先关门什么都跑不了，实测见证据 §3.20a）；`PSModulePath` 仍钉死 —— 只含满足 IMG-01 的安装根模块目录 —— 作纵深防御、不作机制 —— 启动会重组它，交进去的值是输入不是设置 | 模块集合钉不住；CurrentUser 模块目录在工作树之外，自动加载会在 PATH 之前先搜它 | §3.13 |
| **ENV-06** | **子进程环境是封闭集，且分成三类，判据是「这个键的值是不是一条路径」（三类的成员逐一列在本文 §3 `ChildEnv`）。** | 效果表只量命令行（EFF-01），而 `GIT_CONFIG_GLOBAL`、`GIT_CONFIG_COUNT` 配 `core.fsmonitor`、`NODE_OPTIONS=--require`、`PYTHONPATH`、`LD_PRELOAD` 让命令行惰性的可信程序从环境里拿到要跑的代码；把配置根交给环境也一样 —— `XDG_CONFIG_HOME` 指向主体可写的目录，`git status` 就从那里读 `git/config`，那是**工作树之外**的一条路径，工作树检查看不见它 | §3.21、§3.22 |
| **ENV-06a** | **(1) 钉值** —— 每一个路径值的键，值**由 agentao 从操作系统与执行主体求出**，绝不从基础环境抄：系统根与程序目录从系统 API 求，**这一类须过 IMG-01**，任一项不过 ⇒ **拒绝该 rung**（不是移除那个键：少一个系统根的环境不是「更安全的环境」，是一个没人验证过的环境）；用户身份目录（家目录、`APPDATA` 一类、临时目录）从主体自己的 profile 求，**这一类按定义主体可写、不过 IMG-01** —— 钉值挡的是「环境把这个根指到别处」，不是「主体写这个根里的文件」，后者与工作树 hooks 同类，记在本文 §1 的残留行。 | — | §3.21、§3.22 |
| **ENV-06b** | **非本机时这两类值全部由 oracle 的 `target_pinned_env(subject)` 在目标上求出、随 spec 冻结**（SPEC-05、SPEC-07）：地板机器的系统根与家目录不是目标机的，POSIX 的 `getpwuid` 也答不出 `TMPDIR`。**钉值是固定字段、不是任意映射**（本文 §3 `PinnedEnv`）：键集封闭，多一个键即拒绝该 rung；每个字段有自己的形态（绝对目录 / 盘符 `C:` / 根相对路径 / 绝对文件），形态不符即拒绝；系统那一类**在构造 spec 之前**逐项过 IMG-01，profile 那一类只查形态。答不出、多键、形态不符、IMG-01 不过 ⇒ 该 rung 未认证；再加 ENV-01、ENV-02、ENV-04、ENV-05、LAUNCH-04 的钉值与 `ComSpec`。 | — | §3.21、§3.22 |
| **ENV-06c** | **(2) 透传（值检查）** —— 只有**非路径的描述性键**（用户名、机器名、处理器、区域、终端）可以从基础环境抄，值必须匹配该键登记的形状，不匹配就移除、不改写；代理四键是这一类里唯一的例外，它们的值按 URL 解析，**留在默认集里是一次明写的选择：代理改变流量去哪，不改变子进程跑什么**。 | — | §3.21、§3.22 |
| **ENV-06d** | **(3) 移除** —— 每一个把「去哪里读配置或信任什么」交给环境的键：`XDG_CONFIG_HOME` 一族（消费者缺席时按 `HOME` 推默认，移除零成本）、`SSL_CERT_FILE`/`SSL_CERT_DIR`（是信任根，不是便利项），以及不在 (1)(2) 里的所有键。用户级 `shell.env_passthrough` 或构造参数可以点名把某个键加回 (2)，值同样过检查；授权的单位是**字面键名**，含 `*` 的模式条目一律丢弃并诊断一次（一条 `*` 会把整份继承环境放回来，正是本条关掉的那条链）；ENV-03 的保留键在任何来源下都加不回来。 | — | §3.21、§3.22 |
| **ENV-06e** | **键先按目标平台的规则归一再做集合运算**（Windows 大小写不敏感 —— agentao 交给 `Popen(env=)` 的映射在 Windows 上按大写折叠，`Path` 与 `PATH` 是同一个键）：折叠后碰撞且取值不同 ⇒ 该键移除并诊断一次，绝不由字典顺序偶然决定。取值检查是本文 §3 的 `value_ok(key, value, spec, inputs)` —— 键决定形状，`spec.target_platform` 决定路径列表分隔符，`inputs` 给出工作目录与目标项目根 | — | §3.21、§3.22 |
| **ENV-06f** | **钉值答不出等于拒绝该 rung，不等于「少一个键」：** `PinnedEnv` 的每个字段在它所属的目标平台上都必须答得出（本文 §3 `shapes_ok` 与形态检查同一处判），Windows 目标上 WOW64 那三个键（`ProgramFiles(x86)`、`ProgramW6432`、`CommonProgramFiles(x86)`）除外 —— 它们由 WOW64 设、32 位 Windows 上根本不存在，缺席是平台事实而不是「答不出」；POSIX 目标上 `TMPDIR` 同样必答。 | ENV-06a 的理由逐字适用：少一个系统根的环境不是「更安全的环境」，是一个没人验证过的环境。而 `ChildEnv` 对 `None` 的处理是「这个键不出现」，于是不写这一条，「oracle 答不出」就静默地变成了「不设这个键」 | §3.21、§3.22 |
| **ENV-06g** | **两类的判据是「有没有规则依赖这个目录的内容」，不是这个键叫什么：** 系统那一类是解释器与工具链**从中加载**或**从中读配置**的根（系统根、程序目录、`ProgramData`、`ComSpec`），须过 IMG-01；profile 那一类是主体自己的数据目录，按定义主体可写、只查形态。`PUBLIC`（`C:\Users\Public`）属**后者** —— 它是共享的用户数据目录、设计上人人可写，本规范没有一处从它加载或读配置。 | 把一个设计上可写的目录放进「须过 IMG-01」那一类，IMG-06a 的目录判据里 `FILE_ADD_FILE` 一条就足以让 `attested_spec` 拒掉**每一个**政策开启的 rung，翻转之后每次 shell 调用 DENY —— 一个凑数的环境键把整条阶梯关掉，而它的内容没有任何规则读 | §3.21、§3.22 |

### 2.5 `IMG` —— 可信映像、解释器身份与封闭可运行集

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **IMG-01** | 可信根谓词只有一个：**子进程将要以之运行的那个 token，不能修改、删除或替换这条路径，也不能修改、删除、替换或重命名从它到卷根的任何一个祖先目录。** 「仅管理员可写」不是这条规则；「能不能」的访问掩码语义与 reparse 处理在 IMG-06。每一个候选根都答「能」时，可信集为**空**，该 rung 被拒绝（LADDER-03）。POSIX 主机上的等价谓词（root 所有、既非组可写也非全局可写）随 本文 §7 q4 一起定 | 提权运行的 agentao 自己就是管理员；一个谓词服务三个消费者 —— 解释器选择、IMG-02 的映像半、ENV-01 | §3.13；提权态本身是推理，未实测 |
| **IMG-02** | 可运行集按方言封闭，由两个互相独立的条件封闭：**名字** —— 归一后的命令词在该方言的可信表里有条目，并带 EFF-01 的标志；**映像** —— 子进程将要打开的那个文件落在可信根内（IMG-01）。**进程内条目**（cmdlet、function、指向它们的 alias、cmd 内部命令、bash 内建与关键字）没有自己的文件：它们的映像半是该 rung **已认证的 launcher 映像**（`ShellSpec.launcher`，IMG-07）—— 每一个政策开启的 rung 都有这个身份，不只 PowerShell；launcher 未认证（SPEC-05）⇒ 这一半不成立。指向外部程序的 alias 的映像半是其目标的。缺任一半 ⇒ **这一条命令**不透明，不只是其后 | 有名字没映像是被拷进工作树的 `git.exe`；有映像没名字是可信目录里没人分类过的程序；`Get-Date` 的字节就在解释器里，对它做一次 PATH 搜索要么恒失败、要么被实现悄悄跳过 | §3.13 |
| **IMG-03** | 宿主 identity allowlist 是压在位置**之上的附加条件**，永不替代位置。它的两种形式不是一回事：**content pin**（绝对路径 + 内容哈希）测「正是这个文件被换掉了」；**publisher trust**（宿主信任的签名）只证「此刻在那里的文件是可信发布者签的」。两者都不能放行一个被位置拒掉的文件 | body 内 `Copy-Item .\evil.exe <路径>; <那个词>` 不需要竞态就击破哈希，而往文件系统路径的 `Copy-Item` 在 EFF-05 下是惰性的 | §3.13 |
| **IMG-03a** | **判定期生效的那一份 allowlist 与 spec 一起冻结，并进 `fingerprint` 投影**（本文 §3 `ShellSpec.allowlist`）：IMG-03 的加签条件只有这一个来源，判定与启动之间没有第二处可读的「当前配置」。 | 靠一个 `policy_of(spec)` 从 spec 反查生效的块，等于给 allowlist 留一个不在指纹里的隐式来源 —— 两份只在一条 pin 上不同的配置产出同一个指纹，SPEC-08 于是看不出配置在判定与启动之间被换过，而 IMG-03 恰恰是「正是这个文件被换掉了」这一问 | §3.13 |
| **IMG-03b** | **`PublisherTrust` 条目只有一个消费点，判据是逐字相等的签名者：** `host_identity_ok` 先问宿主自己的信任存储（本文 §3 `publisher_trusted`），再问映像的**有效**签名者（`image_signer` —— 链验不过或没有签名即答不出），签名者与 allowlist 里某条 `PublisherTrust` 的 `signer` 逐字相等才成立，最后才是「绝对路径 + 内容哈希」那条。 | 不给 oracle 一问「这个映像的签名者是谁」，`PublisherTrust` 就没有任何一处读得到它 —— 往 allowlist 里加一个受信发布者与留空完全等价，而 IMG-03 把它写成 allowlist 的两种形式之一；并进 `publisher_trusted(path)` 也不行，那是宿主信任存储的裁定，答不出「配置点名的是哪一个」 | §3.11、证据 §4 |
| **IMG-04** | 显式 `.exe`/`.com` 路径归一到 basename 作为命令词（5a）；其它扩展名（5b）、无扩展名路径（5c）、`-File`（5d）⇒ 不透明。**工作树永远不是可信根** | 静态路径不等于不可变字节 | §3.9 |
| **IMG-05** | 解释器发现分两档，不对称。**(a) 自动：** 已知绝对安装位置，目录满足 IMG-01，且映像在**任何启动之前**通过宿主侧身份检查 —— 宿主信任的签名，或 allowlist 里「绝对路径 + 内容哈希」的一条。 | 一个程序不能靠「把它跑起来」认证：跑起来正是这道检查要门住的事件 | §3.3、§3.11、证据 §4 |
| **IMG-05a** | **(b) 显式：** 用户 `shell.path`，绝对且在项目根之外，是一次明写的信任授权：**免签名，不免位置** —— 它所在目录链同样要过 IMG-01，否则解释器加载闭包里**应用目录**那一半（本文 §7.1；当前目录那一半归 LAUNCH-09，`PATH` 那一半归 ENV-01）落在主体可写的目录里，一条惰性的 `Copy-Item` 就能换掉 launcher 旁边的任何 DLL，而重哈希只覆盖 launcher。 | — | §3.3、§3.11、证据 §4 |
| **IMG-05b** | 用户范围的安装（scoop、winget 用户范围、Store 的 app execution alias）由此被拒，答案在 q12。过滤后的 PATH 命中**不是**候选 | — | §3.3、§3.11、证据 §4 |
| **IMG-06** | 映像的四问走一个**宿主侧** identity oracle（可注入）：主体能否修改/删除/替换某路径或其目录；某路径在命令将要运行的那台机器上解不解析得到；某映像带不带宿主信任的签名；某映像的内容哈希。Windows 上它对子进程 token 读 ACL、并读 Authenticode；非本机时它是执行器自己的（SPEC-05），并另答目标的基础环境与 PATH 条目（ENV-06 从它算出 LAUNCH-01 的完整 `env`、ENV-01）；测试里注入。 | 第二问不是装饰：NAME-* 靠搜索过滤后的 PATH，那是目标机上的文件系统操作；有了 oracle，G04 的正例在 ubuntu 上是桩、在 Windows 上是真的 | §3.13 |
| **IMG-06a** | **「能替换」的语义是访问掩码，不是「可写」一个词：** 文件上 `WRITE_DATA`、`APPEND_DATA`、`DELETE`、`WRITE_DAC`、`WRITE_OWNER` 任一，或所有权；目录上 `FILE_ADD_FILE` 与 `FILE_DELETE_CHILD`、`DELETE`、`WRITE_DAC`、`WRITE_OWNER` 任一，或所有权；从映像到卷根的每一个祖先都求值；链上的 junction、symlink 与 app execution alias 先解析到目标，目标与别名所在目录**都**要过。 | — | §3.13 |
| **IMG-06b** | 路径先规范化 —— 8.3 短名、大小写、尾随点与空格、`\\?\` 前缀展开；NTFS ADS 一律拒绝 —— 规范化后的路径才用于 IMG-03 的 `entry_for` | — | §3.13 |
| **IMG-06c** | reparse 的解析是**三态**，不是「一条路径或 `None`」：不是 reparse ／ 解析到了目标 ／ 解析失败（权限、离线卷、损坏的 reparse 数据），失败一律拒绝该链（本文 §3 `ReparseResult`）。沿链递归带**这一趟 reparse 遍历的入口集**与深度上限（本文 §3 `MAX_REPARSE_DEPTH`），成环或超限 ⇒ 拒绝；那个集合装的**不是**「权限已经查过的祖先」—— 混成一个，一条指向自己父目录的可信 junction 就会被判成环而被拒。 | 把「答不出」和「不是 reparse」压成同一个 `None`，那条从没查过的链就按查过了放行；而一对互指的 junction 会让 IMG-01 的求值递归到栈溢出，那不是拒绝 | §3.13 |
| **IMG-07** | **每一个政策开启的 rung** 在 spec 构造时绑定 `LauncherIdentity`（规范化绝对路径 + 内容哈希，本文 §3），spawn 前立刻重哈希；它是 LAUNCH-09 起始目录与 IMG-02 进程内条目映像半的依据，`cmd` 与 `git_bash` 同样要有。**PowerShell rung 另绑定实测的解释器身份** `(edition, version)`，**从映像里、在宿主侧读**（PE 版本资源或安装清单），绝不取自子进程的 `$PSVersionTable`；身份不属于 **NAME-02 的实测命令表**（与 CFG-02a 读的 edition 表不是一张）⇒ 该 rung 的裸词全部不透明、rung 本身不拒（NAME-02） | 版本资源可信是覆盖映像的签名买来的；哈希只覆盖 launcher，安装根可写就绕过它 —— 所以身份真正靠的是 IMG-01 加签名；而没有 launcher 身份的 rung 既构造不出启动请求，也说不出它的内建命令绑在谁身上 | §3.20、证据 §4 |
| **IMG-08** | 任何启动之前先从磁盘读**三个来源**的配置：解析出的 `$PSHOME` 下那份 AllUsers `powershell.config.json`、用户 profile 下那份 CurrentUser，以及优先于两者的 Group Policy。生效控制台会话配置不是默认 ⇒ 拒绝该 rung。 | 去问解释器它的会话配置是什么，等于先跑了那份配置 | §3.20 |
| **IMG-08a** | `$PSHOME` 是宿主侧解析出的安装根（正在执行的 `System.Management.Automation.dll` 所在目录），解析不出 ⇒ 拒绝，绝不退回 launcher 所在目录。spawn 前立刻重读三个来源。 | — | §3.20 |
| **IMG-08b** | **edition 5.1（`powershell.exe`）没有 `powershell.config.json`，也没有控制台会话配置：** 它的三来源只剩 Group Policy，LAUNCH-06 的「三来源都没发现配置」按构造成立 —— 这是推理，G21-14 在 Windows job 上核 | — | §3.20 |
| **IMG-09** | 只在 (a) 或 (b) 认证过映像之后，阶梯才用同一段前奏配一段 body 启动候选解释器做预检：body 报告自动加载偏好，并把身份字段作为对宿主已认证映像的**一致性核对**再报一遍，绝不作为来源。结果写入 SPEC-06 的字段 | 一次启动确立不了被启动者的任何事；预检只是核对 | §3.13 |

### 2.6 `LADDER` —— 阶梯

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **LADDER-01** | 顺序 `pwsh` → `powershell.exe` → Git Bash（仅当 `shell.allow_git_bash`）→ `cmd`。解析器缺失使 PowerShell 不可选。每一级都要过 IMG-05、IMG-01 与 IMG-07（政策开启的每一级都有 `LauncherIdentity`），PowerShell 级另加 IMG-07 的实测身份与 IMG-08 | 带开关的那一级排在 `cmd` 之上，因为每个受支持的 Windows 上都有 `cmd.exe`，排在它之下不可达 | §2.1 |
| **LADDER-02** | `allow_git_bash` 默认 `false`，只在用户级 `shell` 块或构造 spec 里（CFG-01）；在末级被选定**之前**读；开关开而找不到 Git Bash ⇒ `cmd` | 守在 `cmd` 之下的开关是死代码，而门槛会绿在生产环境走不到的路径上 | §3.13 |
| **LADDER-03** | 阶梯走空（每一级被拒）⇒ 工具**仍然注册**，`ShellSpecProvider` 暴露的不是 spec 而是 `Exhausted(reason)` 状态；TOOL-04 读到它时在任何方言与 rung 检查**之前**返回 `hardline:no-trusted-rung-opaque:<原因>`（原因是走空的那条：每一级被 IMG-01 拒、显式 `shell.path` 被 IMG-05 (b) 拒、显式来源缺字段（CFG-02）……）；不注销工具，不退回 `%COMSPEC% /c` 加惰性地板；显式来源被拒时同样进入这个状态，不落到 `auto` | 注销藏起理由；退回是实现者最顺手、也最弱的一种；把走空塞进 `rung` 的取值里，它就排在把它判成未知 rung 的检查之后 | §2.4 |
| **LADDER-05** | 翻转之前（PR-1 至 PR-6）Windows 的默认执行器报 `CMD × legacy_cmd`：`%COMSPEC% /c`、今天的环境、今天的（空转的，§2.4）regex 地板，裁定与 `main@3537753` 逐段相同；它不是阶梯的一级，阶梯只在翻转后运行。PR-7 删除这个取值，之后报它的 spec 按 SPEC-02 拒绝 | 没有这个值，翻转前的 Windows 要么在 PR-2 就被翻转（`CMD × cmd`），要么每次调用 DENY（`UNKNOWN`），要么用 POSIX 模式扫 cmd 文本报干净地板（`system_posix`）—— 三条各违反一条规则 | §2.1、§2.4 |
| **LADDER-04** | 翻转（PR-7）的前提：G09 的三个桶降级率经接受、`ruff` 绿；Git Bash rung 只在 G20 绿时启用，红则关着这一级发布 | 不为 Windows 声称没在 Windows 上测过的东西 | §2.5 |

### 2.7 `CFG` —— 配置

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **CFG-01** | shell 配置是用户级或宿主的，永远不是工作区的；项目级 `permissions.json` 继续被忽略 | 那是信任边界：一条签入仓库的规则不能给 agent 用户没批准的能力 | §2.10、证据 §4 |
| **CFG-02** | 按来源整体取胜：构造参数（`shell=` 执行器，或 `shell_dialect=` / `shell_path=`）> 用户级 `permissions.json` 的 `shell` 块 > `auto`（LADDER-01）。高来源提供整份 spec，更低来源被忽略。显式来源要么给 `path` **与** `dialect` 两者、要么都不给：只给其一 ⇒ 拒绝并点名缺的那个，进入 LADDER-03 的走空状态。 | 两个来源各出一半 spec，谁都说不清生效的是什么；重命名过的 launcher 推不出方言，`powershell` 一个方言定不了 rung；Linux 宿主连 Windows 目标时按宿主导出，显式 POSIX shell 会落到 `system_posix`，把封闭集政策整个关掉 | §2.11、证据 §4 |
| **CFG-02a** | `rung` **不是**配置字段，按固定表从（方言、**目标平台**、映像身份）导出 —— 目标平台由 oracle 的 `target_platform()` 给出，本机执行器答本机，非本机执行器答那台目标机（SPEC-05），**绝不用宿主平台**：`cmd` 在 Windows 目标上 → `cmd`、在 POSIX 目标上**拒绝该来源**（cmd.exe 不在 POSIX 上存在，而政策关闭的 `legacy_cmd` 是翻转前 Windows 的默认、不是 POSIX 的一级）；`posix` 在 Windows 目标上 → `git_bash`（显式来源不需要 `allow_git_bash`，但 LADDER-04 同样约束它：该级关着发布时显式来源也被拒）、在 POSIX 目标上 → `system_posix`；`powershell` 由 IMG-07 从映像读出的 edition 定 —— Core → `pwsh`、Desktop → `powershell`，身份读不出或 edition 不是这两个之一 ⇒ 拒绝该来源。 | — | §2.11、证据 §4 |
| **CFG-02b** | 一个只带 `allow_git_bash`、`allowlist` 或 `env_passthrough`、不带 `path`/`dialect` 的块**不是**整份 spec：它参数化 `auto`，阶梯照跑（LADDER-02） | — | §2.11、证据 §4 |
| **CFG-02c** | **显式来源导出的一级政策关闭时，用户点名的那个可执行文件仍随 spec 冻结**（本文 §3 `ShellSpec.explicit_shell`，并进 `fingerprint` 投影），启动时用它代替今天求出的那一个；政策开启的一级上该字段恒为 `None`，由 launcher 说了算。 | 这一支只在 POSIX 目标 + `posix` 方言上出现（CFG-02a → `system_posix`），而那条路径上的 `path` **已经**过了 IMG-01 的可信根链、IMG-05a 的位置检查与 IMG-07 的身份读取 —— 丢掉它，`/bin/bash` 与 `/bin/zsh` 产出同一份 spec、同一个指纹，CFG-02 的「高来源提供整份 spec」就悄悄变成了「除解释器之外的整份 spec」 | §2.11 |
| **CFG-03** | 一份不可变的 `PermissionConfig { rules, sources, shell }` 穿过每个 composition root（embedding factory、ACP `session_new`、ACP `session_load`）；子代理工厂不读任何文件 | shell 块今天没有穿过任一 root 的通路 | §2.9、§2.11 |

### 2.8 `TOK` —— token 与不透明

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **TOK-01** | `Token` 是 `Literal(text)` 或 `Dynamic(kind)`；不透明是 token 与 AST 节点 kind 的属性，按方言分 | 一个 `Option<Vec<Vec<String>>>` 承载不了「这个词是动态的」 | §3.9、证据 §4 |
| **TOK-02** | PowerShell：命令词 `Dynamic` ⇒ 不透明；命令词在表内但谓词读取位置 `Dynamic` ⇒ 不透明。POSIX/bash 同（`system_posix` 按 SPEC-03）。CMD：**任何**位置的**任何** `Dynamic`（`%VAR%`、`%1`…`%9`、`%*` 读行时；`%A` 按 FOR 迭代；`!VAR!` 在 `/v:on` 下执行时）⇒ 不透明，且任何控制结构或分组 ⇒ 不透明（CMD-01） | 三种方言的展开语义不同：PowerShell 展开后是一个参数，bash 按 IFS 拆，cmd 读行时就替换 | §3.9、§3.13 |

### 2.9 `LOWER` —— PowerShell 降级流水线（规则 0）

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **LOWER-01** | 十步按序，任一步失败 ⇒ 不透明：**1** Unicode 语法别名（弯引号、短破折号、长破折号）；**2** `--flag=value` 单字节遮蔽（不是拒绝）；**3** 树含 ERROR 或缺失节点；**4** `#Requires`（左去空白转小写后以 `#requires` 开头的 `comment`）；**5** 节点 kind（LOWER-02）；**6** 非空；**7** 字面 argv 降级，逐 command 节点（引号与反引号只在运行期取值静态可知时解码；拼接元素、空词、形如 `-Path:x` 的 attached parameter value、非规范的数字打头裸词都拒）；**8** 源码保真（LOWER-03）；**9** `using` 声明；**10** 空命令或空词 | 第 2 步只有一个字节宽，正是为了第 8 步还能拿区间和原始源码比对 | §3.19 |
| **LOWER-02** | 节点 kind 闸门是二值的（`ACCEPTED` / `REFUSED`），裁定单位是节点不是命令。接受清单恰为 21 个 kind：`program` `statement_list` `pipeline` `pipeline_chain` `pipeline_chain_tail` `command` `command_name` `command_elements` `command_argument_sep` `command_parameter` `generic_token` `array_literal_expression` `unary_expression` `expression_with_unary_operator` `string_literal` `verbatim_string_characters` `expandable_string_literal` `integer_literal` `decimal_integer_literal` `empty_statement`，以及 `comment`（**只因第 4 步已经跑过**）。其余每一个具名 kind ⇒ 不透明，含 `assignment_expression`、`variable`、成员调用与 scriptblock body。清单钉在语法 pin 上，语法升级改名 ⇒ fail closed | `$Function:git = { … }` 不形成命令词、不传任何参数，命令级规则永远看不到它 | §3.4、§3.17 |
| **LOWER-03** | 源码保真是一次**有状态走查**（`can_chain`、`needs_command`、`paren_depth`），分隔符**按位置**放行，收尾条件是「区间全部消耗 ∧ ¬needs_command ∧ paren_depth = 0」；`#` 只在 token 边界起注释 | 字符集合规定不了行为：孤立的 `)` 属于任何许可集却必须被拒 | §3.19、证据 §4 |
| **LOWER-04** | codex 的 `powershell_lowering.json` 全部 68 例是门槛：44 条 `null` 行不透明且**逐条断言失败在哪一步**；24 条非 `null` 行断言**整个降级出的 argv 与 `expected` 相等** | 只要求「降级成功」，错的引号、错的转义或切错的参数边界都能过 | §3.19 |

### 2.10 `WRAP` —— 包装、求值器、名字表达式、生成进程者

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **WRAP-01** | 包装体 = 启动另一个解释器并把 body 交给它的命令 —— `bash -c`、`sh -c`、`pwsh -Command`、`powershell -EncodedCommand`、`cmd /c` 一类，即今天 `_SHELL_SCRIPT_WRAPPER` 覆盖的那一类 —— 按被调方的方言重新进入（规则 1）。**重新进入买到的是一次拒绝，不是一次放行：** 由子进程启动的 `pwsh`、`powershell`、`cmd` 或 shell 一条 LAUNCH/ENV 保证都不带，所以嵌套的解释器启动**本身**不透明（规则 2）；解析照跑，好按它自己的理由拒掉危险的嵌套 body | 每条 D4 保证都是 agentao 写出来的那条命令行的性质，子进程写的命令行没有 | §3.10、证据 §4 |
| **WRAP-02** | PowerShell 启动面按 PowerShell 自己的前缀匹配解析：`-Command`/`c`、`-CommandWithArgs`/`cwa` → 重新进入；`-EncodedCommand`/`e`、`-ec` → 解码后重新进入；`-File`/`f` → 不透明；`nop` `nol` `noni` `noe` `ex` `w` → 消费，其中 `ex`（`-ExecutionPolicy`）与 `w`（`-WindowStyle`）各消费其后一个值；**其它任何东西** → 不透明 | 启动器 `MatchSwitch` 按前缀匹配 | §3.10、证据 §4 |
| **WRAP-03** | `cmd` 被分析（CMD-01），不被跳过 | | §3.6 |
| **WRAP-04** | `command_name_expr` 的四种形态：**4a** 求值器源码 —— 只有不含 `Dynamic` token 的字面字符串按本方言当作 body 重新进入（走 `Invoke-Expression` 一类条目的 `executes_input` 字面串分支）；**4b** 字面名字重组；**4c** 脚本块就地；**4d** 运算符之下的路径 ⇒ 不透明。**可达性：** LOWER-02 的接受清单不含 `command_name_expr` 与 `command_invokation_operator`，所以 PowerShell 的 `& …` 与 `. …` 形式在 LOWER-01 第 5 步就已不透明 —— 4b、4c、4d 是第 5 步之后的纵深，可达的理由是第 5 步，门槛按那个理由断言（G04-29）。要让 4b、4c 真正运行，须把这两个 kind 加进 LOWER-02 并限定字面形态，那是对 codex 清单的偏离，**未采纳** | 四种形态是四种不同的东西；走不到的分支不是防线 | §3.4、§3.8 |
| **WRAP-05** | 生成进程的命令一律不透明（规则 7）：`Start-Process`/`saps`/`start`、`Invoke-Item`/`ii`、cmd `start`、`Start-Job`/`sajb`、`Invoke-Command`/`icm` 的每一个远端参数集（`-ComputerName` `-Session` `-ConnectionUri` `-VMId` `-VMName` `-ContainerId` `-HostName` `-SSHConnection`），以及尾置的 `&` 作业运算符。重新进入保留，用于按目标自己的理由拒绝；cmd `start` 的语法（可选带引号标题、开关、目标）为拒绝而保留 | 前三者经 ShellExecute 解析（当前目录、关联、PATH），不是 NAME-02 的解析器；`-UseNewEnvironment` 在被放行的 body 里装回过滤前的用户 PATH；`-Credential`/`-Verb RunAs` 改掉 IMG-01 所依据的主体；其余在另一个进程或机器上运行、不带前奏 | §3.6、§3.13 |
| **WRAP-06** | 生成进程者的目标遵守 IMG-04 与其方言的裸词规则（5f）—— 用于拒绝的理由归属，不用于放行 | 门槛要逐个理由钉格 | §3.6 |
| **WRAP-07** | 以参数为命令的**前缀运行者** —— `timeout`、`nice`、`env`、`nohup`、`sudo`、`command`、`exec`、`xargs`、`watch`、`find … -exec` —— 不是 WRAP-01 的包装体，也不重新进入：它们是可信表条目，其 `execution_triggers`（EFF-08）是「argv 尾部整个是一条命令」—— 于是永远带 `executes_input`，这条命令自身不透明；它们运行地板未降级的参数，按惰性定义（EFF-01）标不了惰性 | 把 `timeout` 标成惰性就放过了 `timeout 5 ./evil`；这是 q9 的代价，明写 | §3.15 |

### 2.11 `NAME` —— 裸词解析

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **NAME-01** | cmd 裸词（5e）：内部命令表 → 匹配；否则经过滤 PATH 按 `PATHEXT` 搜到 `.exe`/`.com` → 按 IMG-02；否则不透明 | cmd 当前目录优先，ENV-04 关掉它 | §3.13 |
| **NAME-02** | PowerShell 裸词（5g）：按 PowerShell 自己的优先级 **alias → function → cmdlet → 外部程序**解析。**实测解释器身份**那一张表 —— 在钉住的启动状态里以 `Get-Command -All` 量出的全部 alias、function 与 cmdlet，每条带 kind、alias 带目标 —— 里第一个匹配的 kind 就是该词的条目：一个 function 遮蔽同名 cmdlet 或外部程序时条目是那个 function，它要有自己的 EFF-08 登记，否则不透明；表内没有 ⇒ 经过滤 PATH 搜到 `.exe`/`.com` → 按 IMG-02；否则不透明。该表**在钉住的启动状态里量**（`-NoProfile`、自动加载关闭、会话配置默认），每一条在该状态下验证可解析。整条规则以 SPEC-06 与「该身份的实测命令表在场」两者为条件，缺任一 ⇒ 每个 PowerShell 裸词不透明，rung 仍按 IMG-04 服务显式路径 | 跨两个 edition 的表要么信任一个根本没有的名字、要么漏掉它确实有的；开着自动加载量出的表放行子进程随后 command-not-found 的东西；function 排在 cmdlet 之前，`mkdir`、`more`、`help` 在 `-NoProfile` 下就是 function | §3.13、§3.21 |
| **NAME-03** | bash 裸词（5h）：在 PATH 搜索之前解析掉的词（alias、关键字、function、内建、命令哈希）判不透明，除非它在该 rung 的惰性内建集（EFF-01）。走到 PATH 搜索的词按 bash 自己的规则经过滤 PATH 解析（精确文件名、任何可执行文件），再按 basename 对 POSIX 表匹配；找不到 ⇒ 不透明。没有扩展名约束。Windows POSIX 层上无扩展名 `git` 与 `git.exe` 的优先级**留空**，由 G20 实测后写进本条 | bash 在搜 PATH 之前就把三类重绑解析完了 | §3.15 |

### 2.12 `EFF` —— 效果标志（规则 6）

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **EFF-01** | 方言可信集里每一条 —— NAME-01 的内部命令、NAME-02 的 cmdlet、function 与别名、POSIX 表、每一级的内建集 —— 都带一**组**标志，结合它拿到的参数判定，标志不互斥：*（无）* **惰性** —— 不写任何环境变量、不绑定任何名字、不改变 provider 驱动器，也不把**命令行供给的**任何输入（参数、管道输入、字面串、被点名的文件）当代码运行；`rebinds_after`；`executes_input`；`rebinds_caller`。惰性说的是**这条命令行**：一个按设计执行工作树内容的可信工具链（`git commit` 跑 hooks）在此定义下仍可为惰性，那是本文 §1 划出的边界；改变当前位置不在定义里，因为相对路径永不可信（IMG-04）、cmd 的当前目录搜索已被 ENV-04 关掉 | 一张闭表加一句「表外不是修改者」是黑名单：`printf -v PATH`、`read PATH`、`hash -p` 直接穿过 | §3.15 |
| **EFF-02** | 后果：惰性 → 受信任，再无下文；`rebinds_after` → 本 body 内其后每条不透明；`executes_input` → **这条命令自身**不透明，唯一例外是**本方言求值器**（EFF-07 标 `=` 的条目）拿到不含 `Dynamic` 的字面字符串时按本方言重新进入（WRAP-04 4a），重新进入的结果按 EFF-03 并入本条；**文件目标一律不透明**、路径长什么样都一样，也不做任何递归读取；`rebinds_caller` → 按 EFF-03 传播 | 静态路径不等于不可变字节：`Set-Content safe.ps1 evil; . .\safe.ps1` 在一个 body 内 | §3.15 |
| **EFF-03** | 递归分析**只**发生在 EFF-02 的字面串重新进入上，返回本文 §3 的 `Analysis`（裁定、退出态、证明集）—— 裁定之外还有这段字面 body 退出时有没有留下被重绑的名字；调用方的条目带 `rebinds_caller` 时把退出态并入自己的状态，使调用点之后的每条命令不透明，不带时丢弃它；重新进入有深度上限（本文 §3 `MAX_ANALYSIS_DEPTH`），超限 ⇒ 不透明、理由 `reenter-depth`，因为一段自嵌套的字面串会让分析递归到栈溢出，而栈溢出不是一次拒绝。文件形式（`source ./x`、`. .\x.ps1`、`Import-Module .\x.psm1`）与子进程形式（`bash ./x`）都不递归：前者按 EFF-02 自身不透明，后者只适用 `executes_input` | `iex 'Set-Alias git C:\evil.exe'; git status` 的重绑发生在被重新进入的字面 body 里，单看 `iex` 那条什么都不透明；而 `source ./safe.sh` 的内容在地板读它与子进程读它之间可以换掉 | §3.15 |
| **EFF-04** | 命令词根本解析不到任何条目 ⇒ **这一条**不透明，其后每条也不透明。没有任何东西隐含地带 `executes_input`；可信表之外的每一个程序都是 DENY，直到有人带着它的效果加一行 | 只污染后继是一行就能利用的洞：单命令脚本没有后继可污染 | §3.15 |
| **EFF-05** | PowerShell：参数只要点名了非文件系统的 provider 驱动器 —— 匹配 `^[A-Za-z][A-Za-z0-9]*:` 且不是盘符路径 —— 不论 cmdlet 是什么，该命令**不透明**，理由 `EFF-05`；在查表之后、标志判定之前判（本文 §4），不是一个效果标志 | 一条规则关掉 `Env:`、`Alias:`、`Function:`、`Variable:` 与注册表驱动器；往 `C:\` 的 `Copy-Item` 仍是惰性；「非惰性」若不是一个裁定，`EffectFlag` 里就没有值能表达它 | §3.15 |
| **EFF-06** | 惰性断言所依赖的任何位置上出现 `Dynamic` token ⇒ 不透明（TOK-02） | | §3.9 |
| **EFF-08** | 可信表是**数据，不是代码**：每条条目登记 `execution_triggers`（哪些参数形状让它把命令行供给的东西当代码运行 —— `git -c core.pager=`、`git --exec-path=`、`python -c`、`node -e`、`explorer <.lnk>`）、`rebind_triggers`、`caller_scope`、`predicate_positions`，每一项带来源；`flags(args)` 由这些字段推出，没有别的来源。每条条目都要登记它的触发集合，**空集是合法的登记 —— 那就是惰性**（`Get-Date`、`pwd`）；不能进表的是「触发集合从没被考虑过」的条目，不是触发集合为空的条目 | 一个函数形式的表无从评审，而 q9 的每一条都是一份需要有人核验的断言 | §3.15 |
| **EFF-07** | 逐方言的 `executes_input` 集合（`+` 表示同时 `rebinds_caller`；`=` 表示输入语言就是本方言，是 EFF-02 唯一允许按 WRAP-04 4a 重新进入的**本方言求值器**，其余条目没有字面串例外）：PowerShell `Import-Module`/`ipmo`+、`Invoke-Expression`/`iex`+=、作用于路径的 `.`+、`Add-Type`+、作用于路径的 `&`、`-File`；cmd `call <file>`+、`start <file>`（两者在 CMD-01 与 WRAP-05 之下到不了本条，同 WRAP-04 的可达性，保留为纵深 —— q11 里 `call` 那一半因此是死问题）；bash `.`/`source`+、`eval`+=（`git_bash` 上 BASH-01 先于本条拒掉 `eval`，保留为纵深）；以及任何被喂了脚本路径的解释器。 | 往清单里加一种形式不改变任何行为，只是加一条规则本就通过的测试 | §3.15 |
| **EFF-07a** | PowerShell 里「作用于路径的 `.` 与 `&`」两条在 LOWER-02 之下到不了本条（WRAP-04 的可达性），保留为纵深。 | — | §3.15 |
| **EFF-07b** | 枚举出来的修改形式（cmd `set`/`path`/`setx`/`call set`/`for /f … do set`；PowerShell `$env:`/`*-Item`/`Set-Content`/`[Environment]::SetEnvironmentVariable`/`Set-Alias`/`New-Alias`/`Set-Variable`/`New-Item -Path Function:`；bash `PATH=`/`export`/`declare -x`/`env PATH=`/`printf -v`/`read`/`hash -p`/`alias`/函数定义/`BASH_ENV=`/`ENV=`）是**门槛用例，不是规则** | — | §3.15 |

### 2.13 `BASH` —— git_bash 那一级的语法闸

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **BASH-01** | `git_bash` 那一级有一道与 LOWER-02 同强度的语法闸，先于任何命令级规则。body 先按 bash 的引用规则（单引号、双引号、反斜杠）切成简单命令，分隔符是 `;`、`&&`、`\|\|`、`\|`、`\|&`、`&` 与换行，切分失败 ⇒ 不透明。以下任一出现在**任何位置** ⇒ 整段 body 不透明：命令替换 `$(…)` 与反引号；进程替换 `<(…)`、`>(…)`；参数展开 `${…}` 与算术展开 `$((…))`；函数定义；`{ }` 分组与 `( )` 子 shell；关键字 `if` `then` `elif` `else` `fi` `for` `while` `until` `do` `done` `case` `esac` `select` `function` `coproc` `time` `!` `[[`；heredoc `<<` 与 herestring `<<<`；指向 `/dev/tcp`、`/dev/udp` 的重定向；`trap`、`exec`、`eval`（后二者也在 EFF-07）。未加引号且会改变 argv 的展开另按 BASH-01a | PowerShell 的封闭性一半来自 kind 闸，cmd 的来自 CMD-01；没有这一条，`echo $(curl … \| sh)` 在惰性的 `echo` 上通过封闭集，而代码在 `echo` 之前就跑了 | §3.15、§3.17 |
| **BASH-01a** | **会改变 argv 的未引用展开，出现在任何位置 ⇒ 整段 body 不透明：** 花括号展开（`{a,b}`、`{1..9}`）、路径名展开（`*`、`?`、`[…]`）、波浪号展开（词首的 `~`、`~user`），以及未加引号的 `$VAR`、`$@`、`$*`（分词之后还要再做一次路径名展开）。加了引号的 `"$VAR"` 恰好是一个 argv 条目，仍是 `Dynamic` token，按 TOK-02。 | 效果表逐 token 匹配参数形状，而一次展开能把一个字面 token 变成 N 个参数：`git {-c,core.fsmonitor=./evil} status` 在表里只是一个普通字面词，`execution_triggers` 一条都不命中；`*` 与 `?` 展开成什么由工作树里的文件名决定 | §3.15、§3.17 |

### 2.14 `CMD` —— cmd 方言

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **CMD-01** | `cmd` 是 regex 方言。`if`、`else`、`for`、`do`、`goto`、`call` 任一，或任何语法有效的分组括号 ⇒ body 不透明；引号内或 `^` 转义的括号是字面量。交付项：§3.6 的 CMD 行、§3.5 中每个有 cmd 拼法的类、NAME-01 的内部表、`start` 的语法（WRAP-05）、TOK-02 的 cmd 行、EFF-07 的 cmd 行 | 变量形式读行时或执行时展开，控制流改变哪一行被读 | §3.6、§3.12 |

---

## 3. 数据契约

数据契约的正文是 `powershell-support-contracts.py`（本文件集的一员）：那里的每个类型与函数就是本节。它不被 `agentao` 导入，由 `tests/test_design_set.py` 用 `mypy --strict` 检查；`scripts/check_design_set.py` 核本文 §2 的每个**母** ID 在那里至少锚一次（`# 族-NN` 注释；子规则的锚点计入它的母 ID，子规则自身不单独要求），核 `ENV-*` 点名的每个键出现在那里。落地的 dataclass 与 Protocol 镜像它；字段名由 G01、G24 的测试钉住。本文其余处写「本文 §3 `X`」，指的就是契约文件里的 `X`。

契约文件按段落对应本文的族：`ShellSpec` / `validate` / `fingerprint_projection`（SPEC）；`ResolvedImage` / `LauncherIdentity` / `InterpreterIdentity` / `IdentityOracle` / `trusted_root_chain` / `trusted_image`（IMG）；`PinnedEnv` / `EnvInputs` / `child_env`（ENV；规范散文里叫它 `ChildEnv`）；`AttestedLaunch` / `LegacyLaunch` 与 `createprocess_units` / `cmd_line_chars` / `execve_total_units`（LAUNCH）；`Token` / `EffectFlag` / `TrustedEntry` / `Analysis`（TOK、EFF）；`ShellBlock` / `PermissionConfig`（CFG）。只有 `raise Unspecified(...)` 的函数是接缝：签名与类型是规范，身体由它点名的规则规定。

**地板返回的 reason 词表。** 门槛按 reason 区分理由，不只按裁定。

| reason | 规则 |
|---|---|
| `hardline:unknown-dialect-opaque` | SPEC-01 |
| `hardline:unknown-rung-opaque` | SPEC-02 |
| `hardline:no-trusted-rung-opaque:<原因>` | LADDER-03（provider 处于 `Exhausted`；先于其余每一条） |
| `hardline:<dialect>-opaque:<原因>` —— `<原因>` 是 LOWER-01 的步骤号、产生不透明的规则 ID、IMG-02 的哪一半，或该规则登记的具名理由（`rebinds_after`、`executes_input`、`nested-launch`、`reenter-depth`） | 其余每一种不透明 |
| `hardline:<dialect>-opaque:launch-<原因>` —— `oversize`、`rehash`、`reread`、`spec-changed`、`cwd`、`env-oversize`、`attest` | 启动阶段的拒绝：LAUNCH-08、IMG-07、IMG-08、SPEC-08、LAUNCH-09，以及执行器复核失败的 `attest`（LAUNCH-01 规定它们走地板的 DENY 通道）。`oversize`、`env-oversize` 与 `cwd` 在判定时就算得出来，本文 §4 的 `floor` 提前发它们；其余四个（`rehash`、`reread`、`spec-changed`、`attest`）只有 `launch()` 与执行器能发 |
| `hardline:<class> …` | 危险表命中：§3.5 的 18 类与 §3.6 的 Windows 类，与今天的拼法相同 |

---

## 4. 权限判定流水线

地板在 `PermissionEngine.decide_detail` 内部、任何规则匹配之前运行；它的 DENY 不可被规则遮蔽
（TOOL-03）。`_decide` 的三层（read-only 预设 → 引擎 → 工具自身的 `requires_confirmation`）不变。

流水线的正文是契约文件的 `decide`、`floor`、`analyse_body`（以及它们调用的接缝函数）；每个分支旁的 `# 族-NN` 注释标出那条规则在哪一步产生裁定，`validate` 是 SPEC-01 / SPEC-02 / SPEC-03 的 fail-closed 校验点。

**顺序为什么是这个顺序。** `Exhausted` 在一切之前，因为它不是一个 rung、放在配对检查之后就永远到不了（LADDER-03）；方言、rung 与 SPEC-03 三条交叉不变量的 fail-closed 校验（`validate`）在**任何 oracle 调用与环境计算之前**，因为一份漏进来的坏 spec 应当产出 SPEC-01 / SPEC-02 的 reason，而不是先去问 oracle、或在解引用 `None` 时抛异常；`policy_enabled`
的闸在任何查表之前，因为 `system_posix` 的每一次查表都是一次 Linux 上的行为变更（SPEC-03）；
LAUNCH-08 的长度守卫在分析之前，因为截断后的结构不是被分析的结构；LOWER-01 与 BASH-01 在任何命令级规则之前，因为它们拒掉的东西从不形成命令；`rebinds_after` 的检查在循环
顶部，因为它说的是**后继**；EFF-05 在查表之后、标志之前，因为它不论 cmdlet 是什么、却要保住 EFF-04 对表外词的理由；`executes_input` 在标志判定之后，因为它说的是**自身**；递归只从字面串重新进入那一个点发起，并把退出态与证明集一起带回来（EFF-03、LAUNCH-01）。

---

## 5. 各 rung 的启动矩阵

启动矩阵的正文是契约文件的 `select_rung`、`attested_spec`、`legacy_spec` 与 `launch`；下表按 rung 汇总每一级引用的规则与门槛。

| rung | 发现与身份 | 启动前必须成立 | 命令行（LAUNCH） | 环境（ENV） | 封闭集政策（SPEC-03） | 门槛 |
|---|---|---|---|---|---|---|
| `pwsh` | IMG-05 (a)/(b)；IMG-07 读 `LauncherIdentity` + `(edition, version)`；IMG-08 三来源配置 | 解析器在场；会话配置默认；`$PSHOME` 解析得出；重哈希与重读通过；plan 上的 spec 仍是 provider 的（SPEC-08） | LAUNCH-02，前奏按 LAUNCH-05、LAUNCH-06、LAUNCH-07；起始目录与 `<W>` 按 LAUNCH-09；G18 红则改 LAUNCH-03 形式 | ENV-01、ENV-02、ENV-03、ENV-05、ENV-06 | 开 | G10、G18、G21、G23 |
| `powershell` | 同上；表按 edition 分（NAME-02） | 同上 | 同上 | 同上 | 开 | G10、G21 |
| `git_bash` | IMG-05；IMG-07 读 `LauncherIdentity`；仅当 `allow_git_bash`（LADDER-02） | G20 绿（LADDER-04）；重哈希通过 | LAUNCH-04；起始目录与 `<W>` 按 LAUNCH-09 | ENV-01、ENV-02、ENV-03、ENV-06 | 开 | G07、G11、G20 |
| `cmd` | IMG-05；IMG-07 读 `LauncherIdentity` | IMG-01 与 IMG-05 通过（`cmd` 也可能被拒）；重哈希通过 | LAUNCH-03；起始目录与 `<W>` 按 LAUNCH-09 | ENV-01、ENV-02、ENV-03、ENV-04、ENV-06 | 开 | G06、G10、G18 |
| `legacy_cmd` | 翻转前的 Windows 默认（LADDER-05）；不是阶梯的一级；无 launcher 身份、无钉值环境 | — | `LegacyLaunch`：`%COMSPEC% /c`，今天的启动 | 今天的环境（不算 `ChildEnv`） | **关**（PR-7 删除） | G10、G11 |
| `system_posix` | 现有 POSIX 主机的那个 shell；无 launcher 身份、无钉值环境 | — | `LegacyLaunch`：今天的启动 | 今天的环境（不算 `ChildEnv`） | **关**（q4） | G07 |
| *（走空）* | 每一级被拒，或显式来源被拒 —— provider 处于 `Exhausted(reason)` | — | 不启动 | — | — | G25 |

**Git Bash 那一级最弱**（NAME-03：裸词解析是 bash 自己的，`PATHEXT` 收不窄它；MSYS2 下的路径翻译在这里未测），
所以 LADDER-02 让它默认关、只放用户级，LADDER-04 让 PR-7 只在 G20 绿时开。这里不再复述那两条。

---

## 6. 跨计划依赖：子代理与 MCP

本规范不定义任何子代理或 MCP 规则；它们在 `subagent-runtime-safety-plan.zh.md`（`SUB-*`、`MCP-*`、`ENG-*`）。
两处依赖：

- **PR-1 依赖那边的 PR-0（SUB-01）。** 子代理必须按身份持有父级那一个有效的 `shell`，于是子代理的
  `run_shell_command` 暴露的 `ShellSpec` 与父级相同，TOOL-04 在子代理里读到的是同一份方言与 rung。
  没有 PR-0，子代理没有引擎，本规范的每一条在子代理里都不生效 —— 这就是 G08 要经「PowerShell 子代理」
  断言不透明被拒的原因。
- **CFG-03 与 SUB-01 共用同一份不可变 `PermissionConfig`。** 子代理工厂不读文件（CFG-03），因为它按身份
  拿到父级的引擎与配置快照（SUB-01、ENG-04）；G13 断言快照抵达每个 root，G13b（子代理计划）断言子代理
  持有的是父级那一份。

- **hooks 计划的 G8：`PreToolUse` 可以改写输入，而已被引擎拒绝的调用仍会触发 hook。** 地板的裁定必须落在改写后的**最终**文本上：改写成不透明文本的调用 DENY，改写成放行文本的调用不得沿用改写前的裁定（G08-02）；重判重新读 spec 并重新记到 plan 上（SPEC-08）。

MCP 的取消（MCP-04）与本规范无交集：shell 工具不是 MCP 工具，它的终止走 `LocalShellExecutor` 与 `kill_process_tree` 的进程树 kill。

---

## 7. 非目标、什么会改变本规范、待决问题

### 7.1 非目标

- **一个 `powershell` 工具。** **`cmd` 在 PowerShell 之上。** **macOS/Linux 上的 PowerShell。**
- **审计任何地板没有降级的文件。**
- **审计可信工具链按设计执行的工作树内容** —— `git` 的 hooks、`npm`/`cargo`/`make`/`pytest` 的脚本与配置。封闭集保证的是**启动的程序**可信（EFF-01 的惰性定义只说命令行），不保证那个程序之后跑的代码可信；后者是 18 类地板之外的产品目的。
- **为 shell、裸词、子进程或启动文件解析信任任何工作区文件或二进制。**
- **在 agent 之间共享工具实例或 MCP 工具对象** —— 共享能力与作用域视图，绝不共享对象（子代理计划）。
- **子代理专属的权限模式。** **`rebind()` API。**
- **给 bash 一个基于扩展名的闭集。** bash 没有 `PATHEXT`；NAME-03 如此说明。
- **关闭 POSIX 间接缺口** —— q4。
- **关闭会话配置的 TOCTOU。** 在阶梯解析与 spawn 之间装上的控制台会话配置，其启动脚本跑在那段本应
  拒绝它的前奏之前。这个窗口靠「spawn 前立刻重读三个来源」（IMG-08）收窄，没有关闭；G21 的探针 (a)
  测它。
- **认证解释器的加载闭包。** 预检哈希的是 launcher；`System.Management.Automation.dll` 以及该进程
  加载的其他一切都在这个哈希之外，而在 Windows 上那个程序集所在的目录*就是* `$PSHOME`（§3.20）。
  闭包有三处来源，三条规则各管一处，**三条的覆盖面各不相同**：应用目录（安装根）靠 IMG-01 加签名 —— 一个
  可写的安装根就把身份打破，设计**拒绝**这样的安装根、不声称覆盖它，G23 断言这次拒绝；`PATH` 靠 ENV-01，
  整个进程有效；**当前目录只靠 LAUNCH-09 关掉启动期那一段** —— 加载器在 `LoadLibrary` 发生时才用当前目录
  （§3.22）；前奏切目录之后各级还剩多少**按 rung 未定**（PowerShell 的位置是运行空间状态，未必改得动进程当前目录，
  §3.22 (f)），由 G21-17、G21-18 实测落定。启动级缓解在这里不可用：
  `SetDllDirectory("")` 要由进程自己在初始化早期调用、且作用于整个进程，父进程替不了子进程调。所以
  「前奏之后的延迟加载」与「可信工具链自己的进程」是**范围待测**的残留，记在本文 §1 的残留行；G21-17 与 G21-18
  各测一半，测出来是什么就写回这里。
- **从解释器内部认证这个解释器。** 位于解析路径上、且与记录的 edition、version、`$PSHOME` 与内容哈希
  全部相符的替换体测不出来，而且它在守卫被解析之前就握有控制权。这个窗口靠「spawn 前立刻重新哈希」
  （IMG-07）收窄，没有关闭；G21 的探针 (b) 测它。这两条正是「范围」不再不加限定地写「启动文件」的原因。

### 7.2 什么会改变本规范

- **`tree-sitter-powershell` 不再提供 wheel。** **实测的 Windows 用户数为零。** **不透明桶不可用。**
- **PowerShell、cmd、bash 或 Windows 改变本规范钉住的任何语义** —— `MatchSwitch`、命令优先级、
  `PATHEXT`、`Start-Process`、profile、`/s`、`start`、分组、`BASH_ENV`、
  `NoDefaultCurrentDirectoryInExePath`、`lpApplicationName`、DLL 搜索顺序、`CreateProcessW` 的命令行上限、
  `GIT_CONFIG_*` 与 `NODE_OPTIONS` 一类环境注入面。
- **agentao 采纳工作区信任模型。**

### 7.3 待决问题

编号沿用拆分前的 §9，好让「q4」「q12」这些引用不变；q7 与 q8 已随 PR-0 移入子代理计划。
**q2、q3、q9、q11 是 PR-2 之前的决策门，q4 是第五道，q13 与 q14 是 PR-4 之前的两道**（实现文件 §3）。

**2026-09-05 定案（用户，PR-1 落地时）—— 五道门关掉四道，q11 与 q14 仍开：**

| 问 | 定案 | 它约束什么 |
|---|---|---|
| q4 | **`system_posix` 维持政策关闭。** TOK-02、EFF-\*、IMG-02 只在 Windows 的政策开启级生效 | PR-2 不碰 POSIX 主机的任何现有行为；`rung` 字段就是让这条随时可翻的东西。**这也是 PR-1 已落地的形态** |
| q9 | **惰性集取「最小 + 常用只读工具链」这一档**：`git status`/`log`/`diff`、`ls`、`cat`、`grep`、`python -c`、`node -e` 一类 | 每一条都是一份要有人核验的断言，所以逐条进表、逐条给出核验依据；不取「除已知危险外全惰性」那一档 —— 那会把封闭集变回黑名单 |
| q2、q3 | **只收物理破坏的对应物**：`format`、`diskpart clean`、`cipher /w`、BitLocker 擦除、`vssadmin delete shadows`。**不收** codex 的「带 URL 的启动」类，**也不收**凭据/令牌库那一类 | 危险表的判据是「不可恢复的丢失」，URL 启动不是；把它收进来会拒掉普通的「打开一个链接」 |
| q13 | **要求工具链装在主体写不了的前缀下。** 不引入逐路径的用户信任授权 | 不新开一条进入可信集的例外，也不继承 IMG-05 (b) 那处 TOCTOU。代价明说：开发者机器上 `uv`、python.org 的 Python、scoop 的 shim 按现装法进不了可信集，要重装到别处 |
| q11 | **仍开着。** `call` 与 `start` 的 `rebinds_caller` 作用域无文档，只能实测 | 这不是一道偏好题，是一次测量：要在 Windows 上跑探针（门槛矩阵 G11 一族）。在它定案之前，cmd 的这两个形式取**保守读法**（当作会重绑调用方作用域），因为猜错的那一侧是漏判 |

1. **哪种降级分布可接受？**
2. **`cryptsetup luksFormat` 的 Windows 对应物。**
3. **codex 的「带 URL 的启动」类。**
4. **`system_posix` 那一级该不该采纳 TOK-02、EFF-* 与 IMG-02？** 在 Linux 上三者都是对每一位现有用户的
   行为变更，而 EFF-04 尤其会让一个未识别的命令词**拒掉它所在的那次调用**，而不只是污染今天地板放行的
   整段脚本。`rung` 字段（SPEC-02、SPEC-03）正是让这个问题保持开着、而不是靠发布来回答它的东西。
5. **hook payload 需要方言作为一个字段吗？**
6. **干脆保留 `run_shell_command`？**（TOOL-01 目前保留；备选是 `_PLAN_ONLY_TOOLS` 模式）
7. → 子代理计划 §7。
8. → 子代理计划 §7。
9. **惰性集值得做多宽？** 最小的那个安全，也会拒掉很多；每加一条都是一份需要有人核验的断言。
   自 EFF-04 起这决定的是「什么能跑」，不只是「什么会污染后继」。
10. **还有什么先于 kind 闸门、而 codex 自己也没找到？** LOWER-01 照着 codex 的流水线走，也就只和
    codex 自己的覆盖面一样好 —— 它接受清单上的注释写着那些拒绝要维持到逐 kind 的降级语义被审过为止，
    所以它的闸门是别人为另一套政策画下的底线。
11. **cmd 里哪些 `rebinds_caller` 形式带哪种作用域？** PowerShell 与 bash 那几个有充分文档，`call` 与
    `start` 没有，而这个标志值多少钱，全看它那张逐方言表值多少钱。
12. **用户自装的工具链怎么才跑得起来？** allowlist 降级为附加条件之后（IMG-03），`uv`、python.org 的
    Python、scoop 的 shim —— 它们**按设计**就装在用户可写的前缀下 —— 进不了可信集：过滤后的 PATH 会丢掉
    它们的目录，而 allowlist 也不再能单独成立。可选项是：宿主把它们装到「该 agent 主体写不了」的根下；
    由用户做一次逐路径的显式信任授权，就像 `shell.path` 对解释器那样 —— 那是**照 IMG-05 (b) 档形状写的、
    有文档的例外**，不是进入可信集的第二条路，而且它同样带着那处 TOCTOU，明说出来、不是默默继承；注意 (b) **不免位置**（IMG-05），所以这个选项对用户可写前缀里的工具链同样不成立，除非它明写「免位置」并把加载闭包落在主体可写目录里的后果一并接受；或者
    接受这次拒绝。在开发者自己的机器上，这几乎就是他跑的全部东西，所以这是一个有用户可见答案的决定，
    不是一条脚注。
13. **工具链的环境变量怎么才到得了子进程？** ENV-06 的三分法已定（配置根与信任根不透传、路径值一律钉值、
    代理留在默认集），剩下的是它的用户可见代价：`VIRTUAL_ENV`、`JAVA_HOME`、`CARGO_HOME`、`GOPATH`、每一个
    `*_HOME` 都带路径值，于是都要一次用户级 `env_passthrough` 授权，而在开发者自己的机器上这几乎是他跑的
    全部东西 —— 与 q12 是同一个问题的两半。可选项是：给这些键一份「值须落在主体写得了、但不在工作树里的
    目录」的宽松形状；或让宿主一次性授权一组；或接受逐键授权。**不可选的是把它们放回默认透传集** —— 那正是
    这一版关掉的那条链。
15. **用户写的 allowlist 路径谁来规范化？** IMG-03 的 content pin 是「绝对路径 + 内容哈希」，IMG-06b 说
    「规范化后的路径才用于 `entry_for`」—— 那说的是**映像**那一侧。pin 那一侧是人写进用户级 `shell.allowlist`
    的字符串，而读它的加载器（CFG-01、CFG-03）没有 oracle，规范化不了。于是一条 `c:\...` 或带 `..` 的 pin
    **静默不生效**：`trusted_image` 找不到它于是不加条件，`host_identity_ok` 找不到它于是少一条放行路径。
    可选项是：查找时按目标平台的路径规则比（那要 `HashPin.matches` 同时改，否则同一对路径两种比法 ——
    找得到却匹配不上，「没被 pin」就变成「不可信」）；或在 `attested_spec` 里用 oracle 规范化整份 allowlist
    再冻进 spec（多一轮 oracle 往返，且答不出的条目要有归宿）；或保留逐字相等并在加载期对**看起来非规范**的
    pin 发一次诊断。本文取第三种的前半 —— 逐字相等，两处同一条规则 —— 诊断未实现。

14. **出厂 Windows 上，`C:\ProgramData` 过得了 IMG-01 吗？** 它与 `ALLUSERSPROFILE` 按 ENV-06g 的判据留在系统那一类
    （工具链确实从 `ProgramData` 读配置），于是 ENV-06a 要求它们逐项过 IMG-01，而 IMG-06a 的目录判据含 `FILE_ADD_FILE`。
    **本文不预设答案**（方法规则 9）：出厂 ACL 有没有给 `BUILTIN\Users` 在 `C:\ProgramData` 下建条目的权限，
    只能在真机上量 —— `icacls`，再加一次对**子进程 token** 的 `AccessCheck`，因为 IMG-01 的主语是那个 token 而不是当前用户。
    若答案是「有」，ENV-06a 现在的写法会拒掉每一个政策开启的 rung，可选项是：按 ENV-06g 重新归类这两个键；
    或保留在系统那一类、接受阶梯在这些机器上走空并让 LADDER-03 说明原因；或对它们单独改用「祖先链过 IMG-01、
    目录自身只查存在与所有权」的弱谓词并写明后果。探针 G23-12 取答案，PR-4 之前定。
