# PowerShell 支持规范 —— 让 shell 地板认方言

> ⚠️ **仅设计，未授权实施。** 本文是**规范**：只写现在必须怎样。它不讲历史、不讲同行项目怎么做、不讲
> 上一版为什么错 —— 那些在评审记录与证据文件里。**每条规则只在 §2 定义一次，带一个稳定 ID；本文其余
> 部分与文件集里的其他文件只引用 ID，不转述。** 改一条规则就是改 §2 的那一行；`scripts/check_design_set.py`
> 核每个 ID 恰好定义一次、每个引用存在、每个 ID 至少有一条门槛与一个 PR。

**日期：** 2026-09-03
**状态：** 设计，**rev 25** —— 拆分版。规则语义与 rev 24（冻结于 commit `e01293f`）相同；二十二轮评审、
一百四十三条发现的记录在评审记录文件。**独立于本规范的 PR-0**（子代理没有权限引擎，一处已实测的活缺陷）
已拆出为 `subagent-runtime-safety-plan.zh.md`，其引擎那一半应立即修。
**Anchors:** agentao `main@3537753`（2026-09-01）；codex `openai/codex@b7cd519c76`（2026-08-31）；
pi-mono `@853a80d26`（2026-08-28）。本文自身不含 `file:line` 引文；引文全部在证据文件。
**文件集：** `powershell-support-spec.zh.md`（本文，规范）· `powershell-support-implementation.zh.md`
（PR 阶梯与模块归属）· `powershell-support-gates.zh.md`（门槛矩阵）·
`../reference/powershell-support-evidence.zh.md`（证据）· `powershell-support-review-log.zh.md`（评审记录）·
`subagent-runtime-safety-plan.zh.md`（原 PR-0）。
**约定：**
- 规则 ID 形如 `族-NN`。本文的族：`TOOL` `SPEC` `LAUNCH` `ENV` `IMG` `LADDER` `CFG` `TOK` `LOWER` `WRAP`
  `NAME` `EFF` `CMD`。子代理计划的族：`SUB` `MCP` `ENG`。
- 「§2.x」「§3.x」指证据文件的同号小节；「PR-N」指实现文件的阶梯；「Gnn」「Gnn-mm」指门槛矩阵的行。
- §2 各表「规则」列的每一句都是 MUST；「为什么」只允许一句，长论证在证据文件与评审记录。
- **不透明（opaque）= 地板返回 `hardline:…-opaque` ⇒ DENY**（TOOL-03）。「放行」= 地板不拒绝，交给权限
  规则与工具自己的确认设置。

---

## 1. 状态、范围、威胁模型

**范围。** 在 Windows 上用 PowerShell 跑模型的 shell 命令，以及 `agentao/permissions_hardline/_scanner.py`
必须先变成什么样。注入能力、子代理路径及其并发、registry 来源、MCP 所有权、两个 composition root、宿主
工具替换、地板*与子进程*两侧的解释器与裸词解析、shell profile、继承来的函数、根本不碰 `PATH` 的名字重绑，
以及 Windows 命令行序列化都在范围内。**不在范围内：** WSL；macOS/Linux 上的 PowerShell；以及本设计只
收窄、不关闭的两处竞态（§7）。子代理与 MCP 的并发与所有权在子代理计划（§6）。

**威胁模型。**

| 一侧 | 内容 |
|---|---|
| 不可信输入 | 模型写出的 body；工作树里的任何文件与二进制；子进程继承的环境（`PATH` 条目、`BASH_ENV`、`ENV`、`BASH_FUNC_*`、`SHELLOPTS`）；机器 `PATH` 上任何用户可写的目录；CurrentUser 模块目录；在「解析」与「spawn」之间被写入的配置或映像 |
| 可信输入 | 用户级 `permissions.json` 的 `shell` 块；宿主的构造参数；「子进程主体写不了」的目录（IMG-01）；宿主信任的代码签名；宿主的 identity allowlist（**仅作附加**，IMG-03） |
| 执行主体 | 子进程将要以之运行的那个 token。提权运行的 agentao 自己就是管理员，于是可信集为空、阶梯走空（IMG-01、LADDER-03）—— 那是裁定，不是例外 |
| 守卫的资产 | 地板的 18 类不可恢复操作（§3.5）扩展到 PowerShell 与 cmd；「未被确立为惰性的程序不得运行」（EFF-04）；「解释器与裸词在地板的环境里解析」（ENV-01） |
| 明写不关闭的残留 | 会话配置 TOCTOU；解释器替换（§7；门槛 G21 的两支刻画性探针） |

**今天 → 目标。** 目标列只写 ID；定义在 §2。

| | 今天 | 目标 |
|---|---|---|
| 模型可见工具 | `run_shell_command` | 同名，名字受守护（TOOL-01） |
| Windows 上的方言 | 经 `%COMSPEC% /c` 的 `cmd.exe` | 阶梯 `pwsh` → `powershell.exe` → Git Bash → `cmd`（LADDER-01） |
| 地板的门 | 工具名 | 工具名 + 随调用传入的方言与 rung（TOOL-04、SPEC-01、SPEC-02） |
| 分析模式 | 对原始文本做正则 | regex（posix、cmd）或 lowered（powershell）（TOK-01、LOWER-01、CMD-01） |
| 可运行目标 | 任何东西 | 封闭可运行集，两半独立（IMG-02、IMG-03、IMG-04、NAME-01–03） |
| 无法分析的输入 | 不匹配即放行 | 不透明 ⇒ DENY（TOOL-03、SPEC-01、SPEC-02） |
| 子进程环境 | 继承 | 过滤 PATH、`PATHEXT`、清除列表、cmd/PowerShell 各自的钉值（ENV-01–05） |
| cmd 启动 | `%COMSPEC% /c` | 单字符串 + `executable=`（LAUNCH-03） |
| 子代理 | 没有引擎 | 按身份持父级引擎/fs/shell（子代理计划 SUB-01） |

---

## 2. 强制不变量

### 2.1 `TOOL` —— 工具与规则标注

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **TOOL-01** | 模型可见的 shell 工具只有一个，名字保持 `run_shell_command`。该名字下注册的任何工具 —— 构造时、`add_tool(replace=True)`、`remove_tool` 之后 —— 都必须实现 `ShellSpecProvider`，否则注册失败并点名 | 地板按名把门，名字是它唯一的钩子；不实现 provider 的替换工具会让地板拿不到方言 | §2.2、§2.14、§4 |
| **TOOL-02** | 权限规则增加可选 `dialect` 字段，取值 `posix`、`cmd`、`powershell`、`*`。带 `args.command` 条件而无标注的 shell 规则是 `unspecified`：在 POSIX 与 cmd 上照旧生效；PowerShell rung 遇到它时 spec 构造失败，逐条点名并列出全部四个标签 | 一条为 bash 写的正则套在 PowerShell 文本上，既放行不了也拒绝不了正确的东西 | §4 |
| **TOOL-03** | DENY 是地板唯一的裁定；不透明永远是 DENY，永远不是 ASK；地板的 DENY 不可被 `allow:*` 遮蔽 | 三条 transport 自动批准 ASK | §2.6 |
| **TOOL-04** | 地板对 `run_shell_command` 的门 = 工具名 **加** 随调用传入的 `ShellSpec`：`_decide` 从该调用的工具实例（`ShellSpecProvider`）读 spec，传给 `decide_detail`，后者转给 `hardline_check`；`PermissionEngine(` 的 150 处调用点不改 | 引擎在 agent 之前建，方言是执行器的性质、不是引擎的构造参数 | §2.9、§4 |

### 2.2 `SPEC` —— `ShellSpec`

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **SPEC-01** | `ShellDialect` 只有 `POSIX`、`POWERSHELL`、`CMD`、`UNKNOWN`。`UNKNOWN` 与地板不认识的任何取值 ⇒ 在匹配任何规则之前返回 `hardline:unknown-dialect-opaque` | 宿主 executor 正是无标注方言进来的地方；回退到 POSIX 扫描器等于用错的模式报一个干净的地板 | §2.9、§4 |
| **SPEC-02** | `rung` 是 spec 的第二个字段，取值 `pwsh`、`powershell`、`cmd`、`git_bash`、`system_posix`。合法配对是枚举的（§3 `LEGAL_PAIRS`），在 spec **构造时**校验，失败点名那个配对；漏到地板的非法配对或不认识的 rung ⇒ 在匹配任何规则之前返回 `hardline:unknown-rung-opaque` | 方言选分析方式，rung 决定封闭集政策是否生效；「不认识 → `system_posix`」会整个绕过封闭集 | §3.13 |
| **SPEC-03** | `system_posix` 的封闭集政策**默认关闭**（TOK-02、EFF-*、IMG-02 都不生效，裁定与今天相同），直到 §7 q4 定案；`git_bash` 政策开 | 在 Linux 上三者都是每一位现有用户的行为变更，默认值要被选出来、不是继承下来 | §3.5 |
| **SPEC-04** | `filesystem_is_local: bool`，字段缺席即 `false`。「本机」只有一个意思：子进程打开的那条路径就是地板 stat 过的那条路径；同一宿主上的容器、chroot 与 mount namespace 都不算 | 在错的文件系统上做的检查不是检查 | §2.9 |
| **SPEC-05** | 非本机执行器欠三段义务：**解析**（IMG-06 的每一问、含 NAME-* 的裸词搜索，都针对目标作答）、**证明**（答案绑定目标的主体、目标的环境、子进程实际会打开的映像）、**启动**（LAUNCH-01 的请求原样运行）。未提供 oracle 时，每一个需要映像的命令词不透明 | 裸词搜索是目标机上的一次文件系统操作，不是地板手里的一个事实 | §3.13 |
| **SPEC-06** | spec 携带 PowerShell rung 的预检结果 `closed_env_established: bool`（IMG-09 写入）。`_decide` 跑的时候它是手里的值，不是将来的一次观测 | 地板在任何子进程存在之前裁定，事后子进程报告什么都改不了已给出的裁定 | §3.13 |

### 2.3 `LAUNCH` —— 启动请求与命令行

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **LAUNCH-01** | agentao 构造启动请求，执行器**原样**运行。请求是可判别的：`PosixLaunch`（`executable`、`argv`、`env`、`cwd`）或 `WindowsLaunch`（`application_name`、`command_line`、`env`、`cwd`），外加 `execution_subject` 与 `attested_images`（证明步骤解析出的规范映像） | 单一 `argv` 表达不了 cmd 行的「字符串 + `executable=`」；不带主体与映像，执行器可以一边照办命令行、一边启动别的东西 | §3.12、§4 |
| **LAUNCH-02** | `pwsh` / `powershell.exe`：`"<path>" -NoProfile -NonInteractive -Command "<前奏>; <body>"`，`Popen(list, shell=False)`，前奏与 body 是**一个**元素、绝不拆到多个参数。G18 的哨兵断言子进程收到的 body 与地板扫过的逐字节相同；G18 红 ⇒ 该 rung 改用 LAUNCH-03 的「单字符串 + `executable=`」形式 | Windows 上列表形式一律被 `list2cmdline` 再序列化一次，「不重新加引号」只能靠哨兵核验 | §3.12 |
| **LAUNCH-03** | `cmd`：单一字符串 `"<path>" /d /e:on /v:off /s /c "<body>"`，`Popen(..., executable=<path>)` 设 `lpApplicationName`；body 绝不再次加引号 | `/s` 剥外层引号，`/d` 跳过 AutoRun，`/e:on /v:off` 钉住状态 | §3.12 |
| **LAUNCH-04** | Git Bash：`"<path>" --noprofile --norc -p -c <body>`，长选项在前，`shell=False`，环境按 ENV-03 清除，`MSYS_NO_PATHCONV=1` | `-p` 挡继承函数与 `SHELLOPTS`、覆盖 `BASH_ENV` 与 `ENV`，但只护它启动的那个进程；顺序反了报 `invalid option` | §3.16 |
| **LAUNCH-05** | 前奏逐字节固定：`$PSModuleAutoLoadingPreference='None'; if ($PSModuleAutoLoadingPreference -ne 'None' -or $PSVersionTable.PSEdition -ne '<E>' -or $PSVersionTable.PSVersion.ToString() -ne '<V>' -or (Get-Item -LiteralPath $PSHOME).FullName -ne '<H>' -or <C-check>) { exit 97 }`。`<E>`、`<V>`、`<H>`、`<C>` 是预检记录的 edition、version、`$PSHOME` 与生效控制台会话配置名，各以单引号 PowerShell 字面量代入、内嵌 `'` 双写；无法这样编码 ⇒ 拒绝该 rung，不换转义方式 | 守卫是同一个参数的后半截，没有任何 body 字节能抢在它前面运行 | §3.13、§3.20 |
| **LAUNCH-06** | `<C>` 不得悄悄省略：找不到能在子进程内报出生效控制台会话配置的表达式时，除非预检在三个来源（IMG-08）都没发现配置，否则拒绝该 rung；`<C>` 不能用 `$PSHOME` 顶替 | 安装目录替一个 endpoint 名字作证是作不了的 | §3.20 |
| **LAUNCH-07** | 前奏不改动 body、不扰动 body 的语义；地板的保证是它扫过了 body，前奏是地板从不改动的文本 | 一段第一条语句带副作用的 body 在前奏之后必须产生同样的副作用 | §3.13 |

### 2.4 `ENV` —— 子进程环境

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **ENV-01** | 每一级子进程的 `PATH` = 过滤后的 PATH：只留「子进程主体写不了」的目录（IMG-01 同一谓词），剔除空的、相对的、工作目录与项目根内的条目；由 agentao 自己搜索，绝不用 `shutil.which` | 把被剔目录留在子进程 PATH 里，等于让子进程解析地板刚拒绝的东西；`which` 在 Windows 上先搜当前目录 | §3.11、§3.13 |
| **ENV-02** | `PATHEXT=.COM;.EXE`，每一级都设；bash 忽略它，统一起见照设 | 关掉 `.cmd`/`.bat`/`.ps1` 的裸词解析 | §3.13 |
| **ENV-03** | bash rung：`BASH_ENV`、`ENV` 与每一个 `BASH_FUNC_*` 条目从子进程环境**移除**，不是覆盖 | `-p` 只护一个进程，环境贯穿整棵树：可信 `git` 经 `/bin/sh -c` 跑别名，那个 bash 就从继承环境导入 `BASH_FUNC_git%%` | §3.14、§3.16 |
| **ENV-04** | cmd rung：`NoDefaultCurrentDirectoryInExePath=1` | cmd 裸词先搜当前目录 | §3.13 |
| **ENV-05** | PowerShell rung：模块自动加载由前奏关掉（LAUNCH-05）；`PSModulePath` 仍钉死，作纵深防御、不作机制 —— 启动会重组它，交进去的值是输入不是设置 | 模块集合钉不住；CurrentUser 模块目录在工作树之外，自动加载会在 PATH 之前先搜它 | §3.13 |

### 2.5 `IMG` —— 可信映像、解释器身份与封闭可运行集

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **IMG-01** | 可信根谓词只有一个：**子进程将要以之运行的那个 token，不能修改、删除或替换这条路径或它所在的目录。** 「仅管理员可写」不是这条规则。每一个候选根都答「能」时，可信集为**空**，该 rung 被拒绝（LADDER-03）。POSIX 主机上的等价谓词（root 所有、既非组可写也非全局可写）随 §7 q4 一起定 | 提权运行的 agentao 自己就是管理员；一个谓词服务三个消费者 —— 解释器选择、IMG-02 的映像半、ENV-01 | §3.13；提权态本身是推理，未实测 |
| **IMG-02** | 可运行集按方言封闭，由两个互相独立的条件封闭：**名字** —— 归一后的命令词在该方言的可信表里有条目，并带 EFF-01 的标志；**映像** —— 子进程将要打开的那个文件落在可信根内（IMG-01）。缺任一半 ⇒ **这一条命令**不透明，不只是其后 | 有名字没映像是被拷进工作树的 `git.exe`；有映像没名字是可信目录里没人分类过的程序 | §3.13 |
| **IMG-03** | 宿主 identity allowlist 是压在位置**之上的附加条件**，永不替代位置。它的两种形式不是一回事：**content pin**（绝对路径 + 内容哈希）测「正是这个文件被换掉了」；**publisher trust**（宿主信任的签名）只证「此刻在那里的文件是可信发布者签的」。两者都不能放行一个被位置拒掉的文件 | body 内 `Copy-Item .\evil.exe <路径>; <那个词>` 不需要竞态就击破哈希，而往文件系统路径的 `Copy-Item` 在 EFF-05 下是惰性的 | §3.13 |
| **IMG-04** | 显式 `.exe`/`.com` 路径归一到 basename 作为命令词（5a）；其它扩展名（5b）、无扩展名路径（5c）、`-File`（5d）⇒ 不透明。**工作树永远不是可信根** | 静态路径不等于不可变字节 | §3.9 |
| **IMG-05** | 解释器发现分两档，不对称。**(a) 自动：** 已知绝对安装位置，目录满足 IMG-01，且映像在**任何启动之前**通过宿主侧身份检查 —— 宿主信任的签名，或 allowlist 里「绝对路径 + 内容哈希」的一条。**(b) 显式：** 用户 `shell.path`，绝对且在项目根之外，是一次明写的信任授权，不要求签名。过滤后的 PATH 命中**不是**候选 | 一个程序不能靠「把它跑起来」认证：跑起来正是这道检查要门住的事件 | §3.3、§3.11、§4 |
| **IMG-06** | 映像的四问走一个**宿主侧** identity oracle（可注入）：主体能否修改/删除/替换某路径或其目录；某路径在命令将要运行的那台机器上解不解析得到；某映像带不带宿主信任的签名；某映像的内容哈希。Windows 上它对子进程 token 读 ACL、并读 Authenticode；非本机时它是执行器自己的（SPEC-05）；测试里注入 | 第二问不是装饰：NAME-* 靠搜索过滤后的 PATH，那是目标机上的文件系统操作；有了 oracle，G04 的正例在 ubuntu 上是桩、在 Windows 上是真的 | §3.13 |
| **IMG-07** | PowerShell rung 绑定实测的解释器身份 `(绝对路径, edition, version)`，**从映像里、在宿主侧读**（PE 版本资源或安装清单），绝不取自子进程的 `$PSVersionTable`。身份不属于实测表的解释器 ⇒ 该 rung 的裸词全部不透明（NAME-02）。预检记录 launcher 的内容哈希，spawn 前立刻重哈希 | 版本资源可信是覆盖映像的签名买来的；哈希只覆盖 launcher，安装根可写就绕过它 —— 所以身份真正靠的是 IMG-01 加签名 | §3.20、§4 |
| **IMG-08** | 任何启动之前先从磁盘读**三个来源**的配置：解析出的 `$PSHOME` 下那份 AllUsers `powershell.config.json`、用户 profile 下那份 CurrentUser，以及优先于两者的 Group Policy。生效控制台会话配置不是默认 ⇒ 拒绝该 rung。`$PSHOME` 是宿主侧解析出的安装根（正在执行的 `System.Management.Automation.dll` 所在目录），解析不出 ⇒ 拒绝，绝不退回 launcher 所在目录。spawn 前立刻重读三个来源 | 去问解释器它的会话配置是什么，等于先跑了那份配置 | §3.20 |
| **IMG-09** | 只在 (a) 或 (b) 认证过映像之后，阶梯才用同一段前奏配一段 body 启动候选解释器做预检：body 报告自动加载偏好，并把身份字段作为对宿主已认证映像的**一致性核对**再报一遍，绝不作为来源。结果写入 SPEC-06 的字段 | 一次启动确立不了被启动者的任何事；预检只是核对 | §3.13 |

### 2.6 `LADDER` —— 阶梯

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **LADDER-01** | 顺序 `pwsh` → `powershell.exe` → Git Bash（仅当 `shell.allow_git_bash`）→ `cmd`。解析器缺失使 PowerShell 不可选。每一级都要过 IMG-05、IMG-01 与（PowerShell 级）IMG-07、IMG-08 | 带开关的那一级排在 `cmd` 之上，因为每个受支持的 Windows 上都有 `cmd.exe`，排在它之下不可达 | §2.1 |
| **LADDER-02** | `allow_git_bash` 默认 `false`，只在用户级 `shell` 块或构造 spec 里（CFG-01）；在末级被选定**之前**读；开关开而找不到 Git Bash ⇒ `cmd` | 守在 `cmd` 之下的开关是死代码，而门槛会绿在生产环境走不到的路径上 | §3.13 |
| **LADDER-03** | 阶梯走空（每一级被拒）⇒ 工具**仍然注册**，每次调用返回 `hardline:no-trusted-rung-opaque`；不注销工具，不退回 `%COMSPEC% /c` 加惰性地板 | 注销藏起理由；退回是实现者最顺手、也最弱的一种 | §2.4 |
| **LADDER-04** | 翻转（PR-7）的前提：G09 的三个桶降级率经接受、`ruff` 绿；Git Bash rung 只在 G20 绿时启用，红则关着这一级发布 | 不为 Windows 声称没在 Windows 上测过的东西 | §2.5 |

### 2.7 `CFG` —— 配置

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **CFG-01** | shell 配置是用户级或宿主的，永远不是工作区的；项目级 `permissions.json` 继续被忽略 | 那是信任边界：一条签入仓库的规则不能给 agent 用户没批准的能力 | §2.10、§4 |
| **CFG-02** | 按来源整体取胜：构造参数（`shell=` 执行器，或 `shell_dialect=` / `shell_path=`）> 用户级 `permissions.json` 的 `shell` 块 > `auto`（LADDER-01）。高来源提供整份 spec，更低来源被忽略 | 两个来源各出一半 spec，谁都说不清生效的是什么 | §2.11、§4 |
| **CFG-03** | 一份不可变的 `PermissionConfig { rules, sources, shell }` 穿过每个 composition root（embedding factory、ACP `session_new`、ACP `session_load`）；子代理工厂不读任何文件 | shell 块今天没有穿过任一 root 的通路 | §2.9、§2.11 |

### 2.8 `TOK` —— token 与不透明

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **TOK-01** | `Token` 是 `Literal(text)` 或 `Dynamic(kind)`；不透明是 token 与 AST 节点 kind 的属性，按方言分 | 一个 `Option<Vec<Vec<String>>>` 承载不了「这个词是动态的」 | §3.9、§4 |
| **TOK-02** | PowerShell：命令词 `Dynamic` ⇒ 不透明；命令词在表内但谓词读取位置 `Dynamic` ⇒ 不透明。POSIX/bash 同（`system_posix` 按 SPEC-03）。CMD：**任何**位置的**任何** `Dynamic`（`%VAR%`、`%1`…`%9`、`%*` 读行时；`%A` 按 FOR 迭代；`!VAR!` 在 `/v:on` 下执行时）⇒ 不透明，且任何控制结构或分组 ⇒ 不透明（CMD-01） | 三种方言的展开语义不同：PowerShell 展开后是一个参数，bash 按 IFS 拆，cmd 读行时就替换 | §3.9、§3.13 |

### 2.9 `LOWER` —— PowerShell 降级流水线（规则 0）

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **LOWER-01** | 十步按序，任一步失败 ⇒ 不透明：**1** Unicode 语法别名（弯引号、短破折号、长破折号）；**2** `--flag=value` 单字节遮蔽（不是拒绝）；**3** 树含 ERROR 或缺失节点；**4** `#Requires`（左去空白转小写后以 `#requires` 开头的 `comment`）；**5** 节点 kind（LOWER-02）；**6** 非空；**7** 字面 argv 降级，逐 command 节点（引号与反引号只在运行期取值静态可知时解码；拼接元素、空词、形如 `-Path:x` 的 attached parameter value、非规范的数字打头裸词都拒）；**8** 源码保真（LOWER-03）；**9** `using` 声明；**10** 空命令或空词 | 第 2 步只有一个字节宽，正是为了第 8 步还能拿区间和原始源码比对 | §3.19 |
| **LOWER-02** | 节点 kind 闸门是二值的（`ACCEPTED` / `REFUSED`），裁定单位是节点不是命令。接受清单恰为 21 个 kind：`program` `statement_list` `pipeline` `pipeline_chain` `pipeline_chain_tail` `command` `command_name` `command_elements` `command_argument_sep` `command_parameter` `generic_token` `array_literal_expression` `unary_expression` `expression_with_unary_operator` `string_literal` `verbatim_string_characters` `expandable_string_literal` `integer_literal` `decimal_integer_literal` `empty_statement`，以及 `comment`（**只因第 4 步已经跑过**）。其余每一个具名 kind ⇒ 不透明，含 `assignment_expression`、`variable`、成员调用与 scriptblock body。清单钉在语法 pin 上，语法升级改名 ⇒ fail closed | `$Function:git = { … }` 不形成命令词、不传任何参数，命令级规则永远看不到它 | §3.4、§3.17 |
| **LOWER-03** | 源码保真是一次**有状态走查**（`can_chain`、`needs_command`、`paren_depth`），分隔符**按位置**放行，收尾条件是「区间全部消耗 ∧ ¬needs_command ∧ paren_depth = 0」；`#` 只在 token 边界起注释 | 字符集合规定不了行为：孤立的 `)` 属于任何许可集却必须被拒 | §3.19、§4 |
| **LOWER-04** | codex 的 `powershell_lowering.json` 全部 68 例是门槛：44 条 `null` 行不透明且**逐条断言失败在哪一步**；24 条非 `null` 行断言**整个降级出的 argv 与 `expected` 相等** | 只要求「降级成功」，错的引号、错的转义或切错的参数边界都能过 | §3.19 |

### 2.10 `WRAP` —— 包装、求值器、名字表达式、生成进程者

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **WRAP-01** | 包装体按被调方的方言重新进入（规则 1）。**重新进入买到的是一次拒绝，不是一次放行：** 由子进程启动的 `pwsh`、`powershell`、`cmd` 或 shell 一条 LAUNCH/ENV 保证都不带，所以嵌套的解释器启动**本身**不透明（规则 2）；解析照跑，好按它自己的理由拒掉危险的嵌套 body | 每条 D4 保证都是 agentao 写出来的那条命令行的性质，子进程写的命令行没有 | §3.10、§4 |
| **WRAP-02** | PowerShell 启动面按 PowerShell 自己的前缀匹配解析：`-Command`/`c`、`-CommandWithArgs`/`cwa` → 重新进入；`-EncodedCommand`/`e`、`-ec` → 解码后重新进入；`-File`/`f` → 不透明；`nop` `nol` `noni` `noe` `ex` `w` → 消费；**其它任何东西** → 不透明 | 启动器 `MatchSwitch` 按前缀匹配 | §3.10、§4 |
| **WRAP-03** | `cmd` 被分析（CMD-01），不被跳过 | | §3.6 |
| **WRAP-04** | `command_name_expr` 的四种形态：**4a** 求值器源码 —— 只有不含 `Dynamic` token 的字面字符串按本方言当作 body 重新进入；**4b** 字面名字重组；**4c** 脚本块就地；**4d** 运算符之下的路径 ⇒ 不透明 | 四种形态是四种不同的东西 | §3.8 |
| **WRAP-05** | 生成进程的命令一律不透明（规则 7）：`Start-Process`/`saps`/`start`、`Invoke-Item`/`ii`、cmd `start`、`Start-Job`/`sajb`、`Invoke-Command`/`icm` 的每一个远端参数集（`-ComputerName` `-Session` `-ConnectionUri` `-VMId` `-VMName` `-ContainerId` `-HostName` `-SSHConnection`），以及尾置的 `&` 作业运算符。重新进入保留，用于按目标自己的理由拒绝；cmd `start` 的语法（可选带引号标题、开关、目标）为拒绝而保留 | 前三者经 ShellExecute 解析（当前目录、关联、PATH），不是 NAME-02 的解析器；`-UseNewEnvironment` 在被放行的 body 里装回过滤前的用户 PATH；`-Credential`/`-Verb RunAs` 改掉 IMG-01 所依据的主体；其余在另一个进程或机器上运行、不带前奏 | §3.6、§3.13 |
| **WRAP-06** | 生成进程者的目标遵守 IMG-04 与其方言的裸词规则（5f）—— 用于拒绝的理由归属，不用于放行 | 门槛要逐个理由钉格 | §3.6 |

### 2.11 `NAME` —— 裸词解析

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **NAME-01** | cmd 裸词（5e）：内部命令表 → 匹配；否则经过滤 PATH 按 `PATHEXT` 搜到 `.exe`/`.com` → 按 IMG-02；否则不透明 | cmd 当前目录优先，ENV-04 关掉它 | §3.13 |
| **NAME-02** | PowerShell 裸词（5g）：**实测解释器身份**那一张 cmdlet/alias 表 → cmdlet；否则经过滤 PATH 搜到 `.exe`/`.com` → 按 IMG-02；否则不透明。该表**在钉住的启动状态里量**（自动加载关闭），每一条在该状态下验证可解析。整条规则以 SPEC-06 为条件：封闭环境未确立 ⇒ 每个 PowerShell 裸词不透明，rung 仍按 IMG-04 服务显式路径 | 跨两个 edition 的表要么信任一个根本没有的名字、要么漏掉它确实有的；开着自动加载量出的表放行子进程随后 command-not-found 的东西 | §3.13 |
| **NAME-03** | bash 裸词（5h）：在 PATH 搜索之前解析掉的词（alias、关键字、function、内建、命令哈希）判不透明，除非它在该 rung 的惰性内建集（EFF-01）。走到 PATH 搜索的词按 bash 自己的规则经过滤 PATH 解析（精确文件名、任何可执行文件），再按 basename 对 POSIX 表匹配；找不到 ⇒ 不透明。没有扩展名约束。Windows POSIX 层上无扩展名 `git` 与 `git.exe` 的优先级**留空**，由 G20 实测后写进本条 | bash 在搜 PATH 之前就把三类重绑解析完了 | §3.15 |

### 2.12 `EFF` —— 效果标志（规则 6）

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **EFF-01** | 方言可信集里每一条 —— NAME-01 的内部命令、NAME-02 的 cmdlet 与别名、POSIX 表、每一级的内建集 —— 都带一**组**标志，结合它拿到的参数判定，标志不互斥：*（无）* 惰性；`rebinds_after`；`executes_input`；`rebinds_caller` | 一张闭表加一句「表外不是修改者」是黑名单：`printf -v PATH`、`read PATH`、`hash -p` 直接穿过 | §3.15 |
| **EFF-02** | 后果：惰性 → 受信任，再无下文；`rebinds_after` → 本 body 内其后每条不透明；`executes_input` → **这条命令自身**不透明，唯一例外是不含 `Dynamic` 的字面字符串按本方言重新进入（WRAP-04 4a），**文件目标一律不透明**、路径长什么样都一样；`rebinds_caller` → 按 EFF-03 传播 | 静态路径不等于不可变字节：`Set-Content safe.ps1 evil; . .\safe.ps1` 在一个 body 内 | §3.15 |
| **EFF-03** | 递归分析返回一份退出态摘要（这段 body 退出时有没有留下被重绑的名字）；带 `rebinds_caller` 的调用方把它并入自己的状态，使调用点之后的每条命令不透明；子进程形式（`bash ./x`）不传播，只适用 `executes_input` | 只有一行 `hash -p ./evil git` 的 `safe.sh` 单独看什么都不透明，`source ./safe.sh; git status` 却拿被重绑的表跑 `git` | §3.15 |
| **EFF-04** | 命令词根本解析不到任何条目 ⇒ **这一条**不透明，其后每条也不透明。没有任何东西隐含地带 `executes_input`；可信表之外的每一个程序都是 DENY，直到有人带着它的效果加一行 | 只污染后继是一行就能利用的洞：单命令脚本没有后继可污染 | §3.15 |
| **EFF-05** | PowerShell：参数只要点名了非文件系统的 provider 驱动器 —— 匹配 `^[A-Za-z][A-Za-z0-9]*:` 且不是盘符路径 —— 不论 cmdlet 是什么，该命令即为非惰性 | 一条规则关掉 `Env:`、`Alias:`、`Function:`、`Variable:` 与注册表驱动器；往 `C:\` 的 `Copy-Item` 仍是惰性 | §3.15 |
| **EFF-06** | 惰性断言所依赖的任何位置上出现 `Dynamic` token ⇒ 不透明（TOK-02） | | §3.9 |
| **EFF-07** | 逐方言的 `executes_input` 集合（`+` 表示同时 `rebinds_caller`）：PowerShell `Import-Module`/`ipmo`+、`Invoke-Expression`/`iex`+、作用于路径的 `.`+、`Add-Type`+、作用于路径的 `&`、`-File`；cmd `call <file>`+、`start <file>`；bash `.`/`source`+、`eval`+；以及任何被喂了脚本路径的解释器。枚举出来的修改形式（cmd `set`/`path`/`setx`/`call set`/`for /f … do set`；PowerShell `$env:`/`*-Item`/`Set-Content`/`[Environment]::SetEnvironmentVariable`；bash `PATH=`/`export`/`declare -x`/`env PATH=`/`printf -v`/`read`/`hash -p`/`alias`/函数定义/`BASH_ENV=`/`ENV=`）是**门槛用例，不是规则** | 往清单里加一种形式不改变任何行为，只是加一条规则本就通过的测试 | §3.15 |

### 2.13 `CMD` —— cmd 方言

| ID | 规则 | 为什么 | 证据 |
|---|---|---|---|
| **CMD-01** | `cmd` 是 regex 方言。`if`、`else`、`for`、`do`、`goto`、`call` 任一，或任何语法有效的分组括号 ⇒ body 不透明；引号内或 `^` 转义的括号是字面量。交付项：§3.6 的 CMD 行、§3.5 中每个有 cmd 拼法的类、NAME-01 的内部表、`start` 的语法（WRAP-05）、TOK-02 的 cmd 行、EFF-07 的 cmd 行 | 变量形式读行时或执行时展开，控制流改变哪一行被读 | §3.6、§3.12 |

---

## 3. 数据契约

类型是伪代码，镜像 PR-1/PR-2 落地的 dataclass 与 Protocol；字段名由 G01、G24 的测试钉住。

```text
ShellDialect = POSIX | POWERSHELL | CMD | UNKNOWN                      # SPEC-01
Rung         = pwsh | powershell | cmd | git_bash | system_posix       # SPEC-02
LEGAL_PAIRS  = { POWERSHELL: {pwsh, powershell},
                 CMD:        {cmd},
                 POSIX:      {git_bash, system_posix} }                # SPEC-02；其余一律拒绝

ShellSpec {
    dialect: ShellDialect
    rung: Rung                                # 构造时按 LEGAL_PAIRS 校验，失败点名配对
    filesystem_is_local: bool = False         # SPEC-04；缺席即 False
    execution_subject: Subject                # 子进程将要以之运行的 token；IMG-01 的主语
    identity_oracle: IdentityOracle | None    # 非本机时由执行器提供（SPEC-05）；测试里注入
    closed_env_established: bool = False      # SPEC-06；IMG-09 写入；PowerShell rung 之外恒 False
    interpreter: InterpreterIdentity | None   # IMG-07；PowerShell rung 才有
    policy_enabled: bool                      # SPEC-03；= rung != system_posix
}

InterpreterIdentity {                         # IMG-07、IMG-08；全部宿主侧读出
    path: AbsPath                             # 解析出的 launcher
    edition: str                              # <E>
    version: str                              # <V>
    pshome: AbsPath                           # <H>；安装根，不是 launcher 所在目录
    session_config: str                       # <C>；生效的控制台会话配置名
    launcher_hash: Sha256                     # 预检记录，spawn 前重算
}

IdentityOracle {                              # IMG-06；四问，宿主侧，可注入
    subject_can_replace(path, subject) -> bool     # 修改 / 删除 / 替换该路径或其目录
    resolves_on_target(path) -> bool               # 目标机上解不解析得到
    publisher_trusted(path) -> bool                # 宿主信任的签名
    content_hash(path) -> Sha256
}

LaunchRequest =                               # LAUNCH-01
    PosixLaunch   { executable: AbsPath, argv: list[str], env: Env, cwd: AbsPath }
  | WindowsLaunch { application_name: AbsPath, command_line: str, env: Env, cwd: AbsPath }
  with execution_subject: Subject
  with attested_images: list[ResolvedImage]  # 证明步骤解析出的规范映像；执行器只能启动这些

ResolvedImage {
    canonical_path: AbsPath
    filesystem_identity: FsId                 # 地板 stat 到的那一个（SPEC-04 的「同一路径」）
    execution_subject: Subject
    content_identity: HashPin | PublisherTrust | None
}
HashPin       { path: AbsPath, sha256: Sha256 }    # content pin：测「正是这个文件被换掉」
PublisherTrust{ signer: str }                       # publisher trust：只证发布者

trusted_image(img, subject, policy) =         # IMG-01 + IMG-02（映像半）+ IMG-03
        oracle.resolves_on_target(img.canonical_path)
    and not oracle.subject_can_replace(img.canonical_path, subject)
    and not oracle.subject_can_replace(dirname(img.canonical_path), subject)
    and (policy.allowlist is None or policy.allowlist.matches(img))   # 附加条件，永不替代

Token      = Literal(text) | Dynamic(kind)                            # TOK-01
EffectFlag = rebinds_after | executes_input | rebinds_caller           # EFF-01；空集 = 惰性
TrustedEntry {
    name: str                                 # 归一后的命令词（basename、cmdlet、别名、内建）
    dialect: ShellDialect
    rung_scope: set[Rung]                     # NAME-02 的表按解释器身份分
    flags(args) -> set[EffectFlag]            # 结合参数判定
    predicate_positions: set[int]             # EFF-06：这些位置 Dynamic ⇒ 不透明
}

PermissionRule.dialect = "posix" | "cmd" | "powershell" | "*" | absent   # TOOL-02；absent = unspecified
PermissionConfig { rules, sources, shell: ShellBlock }                  # CFG-03；不可变
ShellBlock {                                                            # 用户级 shell 块 / 构造 spec（CFG-02）
    path: AbsPath | None                      # IMG-05 (b)
    dialect: ShellDialect | None
    allow_git_bash: bool = False              # LADDER-02
    allowlist: list[HashPin | PublisherTrust] = []   # IMG-03
}

ChildEnv(rung) = scrubbed_base_env            # 已剥离 provider 凭据的基础环境
    PATH     = filtered_path(execution_subject)              # ENV-01
    PATHEXT  = ".COM;.EXE"                                   # ENV-02（每一级）
    remove   BASH_ENV, ENV, BASH_FUNC_*                      # ENV-03（git_bash）
    NoDefaultCurrentDirectoryInExePath = "1"                 # ENV-04（cmd）
    PSModulePath = pinned                                    # ENV-05（pwsh / powershell）
    MSYS_NO_PATHCONV = "1"                                   # LAUNCH-04（git_bash）
```

**地板返回的 reason 词表。** 门槛按 reason 区分理由，不只按裁定。

| reason | 规则 |
|---|---|
| `hardline:unknown-dialect-opaque` | SPEC-01 |
| `hardline:unknown-rung-opaque` | SPEC-02 |
| `hardline:no-trusted-rung-opaque` | LADDER-03 |
| `hardline:<dialect>-opaque:<原因>` —— `<原因>` 是 LOWER-01 的步骤号、或产生不透明的规则 ID、或 IMG-02 的哪一半 | 其余每一种不透明 |
| `hardline:<class> …` | 危险表命中：§3.5 的 18 类与 §3.6 的 Windows 类，与今天的拼法相同 |

---

## 4. 权限判定流水线

地板在 `PermissionEngine.decide_detail` 内部、任何规则匹配之前运行；它的 DENY 不可被规则遮蔽
（TOOL-03）。`_decide` 的三层（read-only 预设 → 引擎 → 工具自身的 `requires_confirmation`）不变。

```text
floor(spec: ShellSpec, body: str) -> Verdict:
    if spec.dialect ∉ ShellDialect or spec.dialect == UNKNOWN:
        return DENY("hardline:unknown-dialect-opaque")                    # SPEC-01
    if (spec.dialect, spec.rung) ∉ LEGAL_PAIRS:
        return DENY("hardline:unknown-rung-opaque")                       # SPEC-02
    if spec.rung == EXHAUSTED:
        return DENY("hardline:no-trusted-rung-opaque")                    # LADDER-03

    commands = analyse(spec.dialect, body)
        # POSIX、CMD：今天的 regex 地板 + Token 化（TOK-01）；CMD 另按 CMD-01 拒控制流与分组
        # POWERSHELL：LOWER-01 的十步；任一步失败 → DENY("hardline:powershell-opaque:<步骤>")
    if commands is OPAQUE: return DENY(commands.reason)

    state = { tainted: False }                                            # EFF-02 / EFF-03 的退出态
    for cmd in commands:                                                  # 按 body 顺序
        if state.tainted:              return DENY(opaque(EFF-02, "rebinds_after"))
        if cmd.word is Dynamic:        return DENY(opaque(TOK-02))
        if cmd is wrapper or interpreter launch:                          # WRAP-01、WRAP-02、WRAP-03
            inner = floor(spec.for(callee_dialect), cmd.inner_body)
            return inner if inner is DENY else DENY(opaque(WRAP-01, "nested-launch"))
        if cmd is spawner:             return DENY(opaque(WRAP-05, reason_for(cmd)))   # WRAP-06 归属理由
        if cmd.word is command_name_expr: apply WRAP-04                   # 4a 重新进入；4b/4c/4d
        entry = lookup(cmd.word, spec)                                    # NAME-01 / NAME-02 / NAME-03；显式路径按 IMG-04
        if entry is None:              return DENY(opaque(EFF-04))
        if spec.policy_enabled:                                           # SPEC-03
            img = resolve(cmd.word, spec)                                 # 经过滤 PATH（ENV-01）；非本机经 oracle（SPEC-05）
            if not trusted_image(img, spec.execution_subject, policy):
                return DENY(opaque(IMG-02, half))                         # 名字半 / 映像半 / IMG-03
        if dangerous(entry, cmd.args): return DENY("hardline:<class> …")  # §3.5 的 18 类 + §3.6
        effects = entry.flags(cmd.args)                                   # EFF-01、EFF-05、EFF-06
        if executes_input ∈ effects:
            if cmd.target is literal string without Dynamic: re-enter as body (WRAP-04 4a)
            else:                      return DENY(opaque(EFF-02, "executes_input"))
        if rebinds_after ∈ effects:    state.tainted = True
        if rebinds_caller ∈ effects:   state.merge(exit_summary(cmd.target))   # EFF-03
    return PASS   # 交给带 dialect 标注的权限规则（TOOL-02），再交给工具自身的确认设置
```

**顺序为什么是这个顺序。** 方言与 rung 的两道 fail-closed 检查在任何分析之前（SPEC-01、SPEC-02）；
LOWER-01 在任何命令级规则之前，因为第 4、5、8 步拒掉的东西从不形成命令；`rebinds_after` 的检查在循环
顶部，因为它说的是**后继**；`executes_input` 在标志判定之后，因为它说的是**自身**。

---

## 5. 各 rung 的启动矩阵

```text
select_rung(config: ShellBlock | ConstructorSpec) -> ShellSpec | EXHAUSTED:   # CFG-02、LADDER-01
    if config comes from constructor or user-level shell block:
        return build_spec(config)                            # SPEC-02 校验；IMG-05 (b) 对 shell.path
    for rung in [pwsh, powershell, (git_bash if config.allow_git_bash), cmd]:   # LADDER-02
        img = discover(rung)                                 # IMG-05 (a)：已知安装位置；PATH 命中不是候选
        if img is None: continue
        if not trusted_root(dirname(img), subject): continue                    # IMG-01
        if not host_identity_ok(img): continue                                  # IMG-05：签名 或 path+hash
        if rung ∈ {pwsh, powershell}:
            if parser is None: continue                                         # LADDER-01
            if resolve_pshome(img) is None: continue                            # IMG-08
            if config_from_disk(pshome, user, group_policy).session != default: continue   # IMG-08
            identity = read_identity_from_image(img)                            # IMG-07；不启动
            spec = build_spec(rung, identity)
            spec.closed_env_established = preflight(identity)                   # IMG-09 → SPEC-06
            return spec                                                         # 未确立时 NAME-02 整条失效
        return build_spec(rung)
    return EXHAUSTED                                                            # LADDER-03

launch(spec, body) -> LaunchRequest:                                            # LAUNCH-01
    rehash(spec.interpreter.path) == spec.interpreter.launcher_hash or refuse   # IMG-07
    reread_config(...) == recorded or refuse                                    # IMG-08
    return request per the row below, env = ChildEnv(spec.rung)
```

| rung | 发现与身份 | 启动前必须成立 | 命令行（LAUNCH） | 环境（ENV） | 封闭集政策（SPEC-03） | 门槛 |
|---|---|---|---|---|---|---|
| `pwsh` | IMG-05 (a)/(b)；IMG-07 从映像读 `(path, edition, version)`；IMG-08 三来源配置 | 解析器在场；会话配置默认；`$PSHOME` 解析得出；重哈希与重读通过 | LAUNCH-02，前奏按 LAUNCH-05、LAUNCH-06、LAUNCH-07；G18 红则改 LAUNCH-03 形式 | ENV-01、ENV-02、ENV-05 | 开 | G10、G18、G21、G23 |
| `powershell` | 同上；表按 edition 分（NAME-02） | 同上 | 同上 | 同上 | 开 | G10、G21 |
| `git_bash` | IMG-05；仅当 `allow_git_bash`（LADDER-02） | G20 绿（LADDER-04） | LAUNCH-04 | ENV-01、ENV-02、ENV-03 | 开 | G07、G11、G20 |
| `cmd` | IMG-05；每个受支持的 Windows 都有 | IMG-01 与 IMG-05 通过（`cmd` 也可能被拒） | LAUNCH-03 | ENV-01、ENV-02、ENV-04 | 开 | G06、G10、G18 |
| `system_posix` | 现有 POSIX 主机的那个 shell | — | 今天的启动 | 今天的环境 | **关**（q4） | G07 |
| *（走空）* | 每一级被拒 | — | 不启动 | — | — | G25 |

**Git Bash 那一级最弱，单独开关。** 它的地板是 POSIX 的模式集（§3.5 的 18 类，含 §2.7 的 fail-open）
外加 EFF-*、NAME-03 与 IMG-02，比 Linux 主机今天那个 shell 拿到的更多；它的裸词解析是 bash 自己的，
`PATHEXT` 收不窄它；MSYS2 下的路径翻译在这里未测。打开它等于把较弱的地板排在较强的地板之前，这正是它默认
关闭、只放用户级、且 PR-7 仅在 G20 绿时启用的原因。

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

MCP 的取消（MCP-04）与本规范无交集：shell 工具不是 MCP 工具，它的终止走 `LocalShellExecutor` 与 `kill_process_tree` 的进程树 kill。

---

## 7. 非目标、什么会改变本规范、待决问题

### 7.1 非目标

- **一个 `powershell` 工具。** **`cmd` 在 PowerShell 之上。** **macOS/Linux 上的 PowerShell。**
- **审计任何地板没有降级的文件。**
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
  身份这项声明真正靠的是 IMG-01 加签名，所以一个可写的安装根就把它打破 —— 而设计**拒绝**这样的安装根
  （IMG-01），不声称覆盖它；G23 断言这次拒绝。
- **从解释器内部认证这个解释器。** 位于解析路径上、且与记录的 edition、version、`$PSHOME` 与内容哈希
  全部相符的替换体测不出来，而且它在守卫被解析之前就握有控制权。这个窗口靠「spawn 前立刻重新哈希」
  （IMG-07）收窄，没有关闭；G21 的探针 (b) 测它。这两条正是「范围」不再不加限定地写「启动文件」的原因。

### 7.2 什么会改变本规范

- **`tree-sitter-powershell` 不再提供 wheel。** **实测的 Windows 用户数为零。** **不透明桶不可用。**
- **PowerShell、cmd、bash 或 Windows 改变本规范钉住的任何语义** —— `MatchSwitch`、命令优先级、
  `PATHEXT`、`Start-Process`、profile、`/s`、`start`、分组、`BASH_ENV`、
  `NoDefaultCurrentDirectoryInExePath`、`lpApplicationName`。
- **agentao 采纳工作区信任模型。**

### 7.3 待决问题

编号沿用拆分前的 §9，好让「q4」「q12」这些引用不变；q7 与 q8 已随 PR-0 移入子代理计划。
**q2、q3、q9、q11 是 PR-2 之前的决策门，q4 是第五道**（实现文件 §3）。

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
    有文档的例外**，不是进入可信集的第二条路，而且它同样带着那处 TOCTOU，明说出来、不是默默继承；或者
    接受这次拒绝。在开发者自己的机器上，这几乎就是他跑的全部东西，所以这是一个有用户可见答案的决定，
    不是一条脚注。
