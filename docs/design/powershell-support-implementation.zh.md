# PowerShell 支持 —— 实现阶梯（PR 依赖、模块归属、迁移顺序）

> **PR-1 至 PR-6 已实施**（2026-09-05，用户授权），全量套件 5223 绿（另 11 条 skip —— 9 条是仅 Windows 的启动矩阵） —— PR-4 只差本机 Windows oracle，PR-6 的 `windows` job 首次运行的结果还没有（本仓库此前从未在 Windows 上跑过测试，那次运行本身就是一次测量）；§3 的五道门已由用户关掉四道（规范 §7.3），**q11 仍开着且只能在 Windows 上实测**；PR-4 之前的两道门里 q13 已定案、**q14 仍开着**（同样只能在 Windows 上实测，而 ENV-06a 现在的写法要它答「不可写」）。**PR-7 未开工，它的翻转以 PR-6 绿为前提（LADDER-04）。** 本文回答三个问题：哪个 PR 交付哪些规则 ID、每个 PR 碰哪些模块、先后顺序。
> 规则本身在 `powershell-support-spec.zh.md` §2，本文只引用 ID。§5 的阶梯是**依赖顺序**，不是排期。

**日期：** 2026-09-05 · **状态：** rev 45
**文件集：** 见规范文件头。「§2.x」「§3.x」指证据文件。

## 1. PR 阶梯

| PR | 交付 | 实现的规则 | 用户可见 | 依赖 |
|---|---|---|---|---|
| PR-0 | **已拆出为 `subagent-runtime-safety-plan.zh.md`。** 子代理工厂、registry `origin`、`ToolForkable`、MCP 所有者线程与作用域视图、引擎写者锁与携带快照的裁定 | SUB-01–05、MCP-01–06、ENG-01–05（那边定义） | 否 —— 关上一处活绕过 | — |
| PR-1 | `ShellDialect`、spec 上的 `rung` 与 `filesystem_is_local`，构造时校验「方言 × rung」矩阵；`ShellRequest` 改为携带 agentao 构造好的启动请求（可判别体 —— 含政策关闭两级用的 `LegacyLaunch` —— + 环境 + 主体 + 证明映像），执行器原样运行 —— 今天它带 `command`、`cwd`、`timeout`、`on_chunk`、`env`，没有启动形态、主体或映像；执行器声明 spec 或 `Exhausted`，并承担 LAUNCH-01 的**复核义务**（spawn 前按 `attested_images` 比对直接目标，不一致即拒）；工具经 `ShellSpecProvider` 暴露；`_decide` 先跑 `validate`（SPEC-01/02/03 的交叉不变量）再碰 oracle 与环境，并把读到的 spec、扫过的 body、判定用的 cwd、算好的环境与证明过的映像一起写进冻结记录 `ToolCallPlan.decided`，hook 重判整体替换它，`launch()` 只读它、核对同一 spec 对象（它的签名里没有 body / cwd 参数）；`run` 与 `run_background` 消费同一份请求；替换时重跑名字守护 | TOOL-01、TOOL-04、SPEC-01、SPEC-02、SPEC-04、SPEC-06（字段）、SPEC-07、SPEC-08、SPEC-08c、LAUNCH-01、LADDER-05（`legacy_cmd` 取值与翻转前的默认） | **协议变更** | PR-0（SUB-01） |
| PR-2 | **只交付与运行期状态无关的原语：** token IR、LOWER-01 的十步、codex 的 fixture 语料、危险表、cmd 内部表、每条可信条目上的效果标志、按 rung 生效的政策开关、不依赖运行期状态的每一条 WRAP/EFF/NAME/CMD 规则 | TOOL-03、SPEC-03、TOK-01、TOK-02、LOWER-01–04、BASH-01、WRAP-01–07、NAME-01、EFF-01–08、CMD-01 | 否 | PR-1 |
| PR-3 | 预设；规则的 `dialect` 字段；`PermissionConfig`；用户级 `shell` 块（含 `allow_git_bash`、`allowlist`、`env_passthrough`；`path` 与 `dialect` 成对，`rung` 不是字段）；三个 composition root 的透传 | TOOL-02、CFG-01、CFG-02、CFG-03、LADDER-02 | 否 | PR-2 |
| PR-4 | 可信解析：IMG-01 的谓词、宿主侧 identity oracle（可注入；本机实现与非本机契约同一接口，**绑定一个执行主体**、与主体有关的方法逐个收它，含目标平台、目标文件系统是否本机、目标钉值环境、目标项目根、发现、身份（收方言不收 rung）、三来源、预检）；每一级的 `LauncherIdentity` 与 spawn 前重哈希、解释器发现两档、从映像读身份、从磁盘读三来源配置、预检、rung 导出表；裸词解析器（NAME-02、NAME-03）与按身份分的 alias/function/cmdlet 表（`Get-Command -All`，**必须在本 PR 建立的启动状态里量**）；子进程环境（ENV-06 三分法：钉值 / 值检查透传 / 移除，键按目标平台折叠；`EnvInputs` 显式化，每次调用算一次）；`PinnedEnv` 的封闭键集与构造前校验；逐级命令行与前奏（含起始目录与 `<W>` 切换）；阶梯与 `Exhausted` 状态 | IMG-01–09、NAME-02、NAME-03、ENV-01–06、ENV-01a、LAUNCH-02–09、LADDER-01、LADDER-03、SPEC-04a、SPEC-05 | 否 | PR-2、PR-3 |
| PR-5 | 系统提示按方言渲染（`agentao/prompts/sections.py`） | —（渲染 SPEC-01 的方言，不定义规则） | 否 | PR-1 |
| PR-6 | `windows-latest` job：§5 启动矩阵、§3.12 哨兵、门槛矩阵里平台为 `windows` 的每一行 —— 集合不是区间：G19 与 G22 属于 PR-0、与平台无关；G25 的「容器 `root`」半在 ubuntu | —（只跑门槛） | 否 | PR-3、PR-4、PR-5 |
| PR-7 | 翻转：Windows 默认走阶梯；删除 `legacy_cmd`；Git Bash 那一级在自己的开关后面、仅当 G20 绿时开启 | LADDER-04、LADDER-05（删除） | **是** | PR-6 |

**PR-0 不需要本阶梯的任何东西**（子代理计划 §5）。**PR-1 依赖 PR-0** 只因 SUB-01：子代理按身份持父级的
`shell`，否则本阶梯的每一条在子代理里都不生效（规范 §6）。**PR-4 需要 PR-2 与 PR-3，不只是 PR-1：**
它的裸词解析器把词交给 NAME-*，它的可信表带着 EFF-01 的效果标志 —— 两样都属 PR-2 —— 而它读 `shell.path`
与 `allow_git_bash` 用的那个 `shell` 块，是随 PR-3 的 `PermissionConfig` 一起到的。**PR-2 的依赖：**
`tree-sitter` 与 `tree-sitter-powershell` 在 `[project.dependencies]` 下带 `sys_platform == "win32"`，并在
`[dependency-groups].dev` 下无条件（§2.5 之后的 `pyproject.toml` 位置见证据）。

## 2. 模块归属

| 模块 | 今天 | PR | 改动 |
|---|---|---|---|
| `agentao/capabilities/shell.py` | `ShellRequest { command, cwd, timeout, on_chunk, env }`；`ShellExecutor` Protocol 只有 `run`/`run_background`；`LocalShellExecutor.run` **与 `LocalShellExecutor.run_background`（`agentao/capabilities/shell.py:212`）各自**用 `shell=True, executable=resolve_shell_executable()`，环境是 `build_child_env()` 的继承减凭据 | PR-1、PR-4 | `ShellSpec`、`ShellDialect`、`Rung`、`LEGAL_PAIRS`、`Exhausted`；`ShellRequest` 携带 `LaunchRequest`（完整 `env`、`cwd` 与 `workdir`、`spec_fingerprint`）；**两个方法都只收 `LaunchRequest` 并做同一套复核（LAUNCH-01）**；本机执行器按 LAUNCH-02、LAUNCH-03、LAUNCH-04、LAUNCH-09 启动 |
| `agentao/tools/shell.py` | `ShellTool`，方言常量在 `:248-252`；`is_background` 在 `:263` 分叉到 `run_background` | PR-1、PR-5 | `ShellSpecProvider`；`shell_spec` 从 `_get_shell()` 暴露；`is_background` 只选交付面，两面同一份请求（LAUNCH-01） |
| `agentao/tools/base.py` | `_get_shell()`（`:50-55`）；`ToolRegistry.register(replace)` | PR-1（PR-0 加 `origin`） | 名字守护（TOOL-01） |
| `agentao/runtime/tool_planning.py` | `_decide` 三层；`decide_detail(tool, …)`（`:498`）；`ToolCallPlan` 不带 spec | PR-1 | 把工具的 spec 传给 `decide_detail`（TOOL-04）；`ToolCallPlan.decided`（SPEC-08） |
| `agentao/runtime/tool_runner.py` | `_apply_updated_input` 对 hook 改写后的输入重判（hooks 计划 G8） | PR-1 | 重判重新读 spec 并整体替换 `plan.decided`（SPEC-08） |
| `agentao/permissions.py` | `_LEGAL_RULE_FIELDS`（`:76`）；`args` 正则（`:747-750`）；hardline 不可遮蔽（`:684-694`） | PR-3 | 规则 `dialect` 字段（TOOL-02）；`PermissionConfig`（CFG-03） |
| `agentao/permissions_hardline/_scanner.py`、`_patterns.py` | 按工具名把门（`:155-156`）；18 类（`agentao/permissions_hardline/_patterns.py:35-37`）；Windows token 零命中（`:380`） | PR-2 | 方言分派；token IR；LOWER-01；EFF 标志；CMD-01；reason 词表 |
| `agentao/permissions_hardline/_powershell.py`（新） | — | PR-2 | tree-sitter 降级、21 kind 表、源码保真自动机、codex 语料测试 |
| `agentao/permissions_hardline/_trust.py`（新） | — | PR-4 | IMG-01 谓词、identity oracle 接口与 Windows 实现、解释器发现、身份读取、三来源配置读取、预检、按身份分的表 |
| `agentao/embedding/permission_loader.py`、`embedding/factory.py`、`acp/session_new.py`、`acp/session_load.py` | 用户级 `permissions.json` 只读 `rules`（`agentao/embedding/permission_loader.py:107-111`、`agentao/embedding/permission_loader.py:131-136`） | PR-3 | 读 `shell` 块；`PermissionConfig` 穿过三个 root（CFG-02、CFG-03） |
| `agentao/capabilities/process.py` | `build_child_env()`（`agentao/capabilities/process.py:100`）= 继承整份环境再剥掉 provider 凭据 | PR-4 | shell 路径改走 `ChildEnv`（ENV-06）：封闭集、三类、钉值取自 `spec.pinned_env`；其余调用者不变 |
| `agentao/prompts/sections.py` | shell 提示写死（`:199-222`） | PR-5 | 按方言渲染 |
| `.github/workflows/ci.yml` | 8 个 job，零 Windows（§2.5） | PR-6 | `windows-latest` job |
| `pyproject.toml` | 无 tree-sitter | PR-2 | 依赖（上文） |

行号是 `main@3537753` 的，全部可在证据文件的同名引用下用 `scripts/check_citations.py` 解析。

## 3. PR-2 之前的五道决策门

规范 §7.3 的 **q2、q3、q9、q11** 定的是危险表、惰性集与 cmd 的 `rebinds_caller` 作用域 —— 全是 PR-2 的
交付物 —— 而 EFF-04 让「不在惰性集里」意味着 DENY 而不是污染后继，所以只要它们还开着，「PR-2 做完了」
这句话谁都说不出口。**q4 是第五道：** 它不改变 PR-2 造什么，因为有 `rung` 字段（SPEC-02、SPEC-03），原语
可以在不碰 `system_posix` 的前提下发出去 —— 它决定的是那个默认值，而一个「随代码一起到、从没被人选过」
的默认值，正是这条阶梯存在的意义所在。**q13 是 PR-4 之前的一道**（同一节）：ENV-06 的三分法已定，q13 定的是
工具链那些带路径值的变量（`JAVA_HOME`、`VIRTUAL_ENV`…）怎么授权 —— 在开发者自己的机器上，那几乎是他跑的全部东西。

## 4. 迁移顺序

1. PR-0（子代理计划）：引擎半先发，MCP 半后发；两者都不碰 shell。
2. PR-1 → PR-2 → PR-3 → PR-4：每一步都在 Windows 默认仍走 `%COMSPEC% /c` 的前提下落地，用户不可见；
   PR-1 是唯一的协议变更（`ShellRequest` 形状），宿主自定义执行器要跟着改，G01 断言这是唯一被迫的改动。
3. PR-5 与 PR-4 并行（都只依赖 PR-1 以上）。
4. PR-6 把平台为 `windows` 的每一行门槛跑起来；G20 红则 PR-7 关着 Git Bash 发布（LADDER-04）。
5. PR-7 翻转默认。

## 5. PR-1 与 PR-5 的实施偏离

已落地：`ShellDialect`/`Rung`/`LEGAL_PAIRS`/`ShellSpec` 与构造时校验、`ShellRequest` 携带 `LaunchRequest`、
两个交付面共用一份请求、执行器声明 spec、TOOL-01 的注册守卫、TOOL-04 把 spec 送进 `hardline_check`、
`ToolCallPlan.decided` 与 `launch` 只读它。全量套件 4773 绿，行为与 `main` 逐段相同（每一级都是政策关闭的）。

PR-5 一并落地：操作守则里三处写死 POSIX 语法的地方（`echo >`、`/tmp/out.log` + grep/head/tail、`rm -rf`）改为按方言渲染，方言从**执行器声明的 spec** 读，不是从宿主平台推 —— Docker 或远端执行器跑的是另一个 shell。答不出时回落 POSIX，也就是这段文字此前的原样：提示词是建议，不该让一个回合失败。

下面六条是实施时相对本文的偏离，逐条记下理由 —— 不记下来，下一个 PR 就会把它们当成本来就该如此。

| # | 偏离 | 为什么 |
|---|---|---|
| 1 | 词表与启动请求落在**新文件** `agentao/capabilities/shell_spec.py`，`shell.py` 再导出；§2 的模块表写的是 `shell.py` | PR-2 的地板要读 `ShellDialect` 与 `ShellSpec`，而 `shell.py` 里是 subprocess 执行器 —— 放一起等于让 `permissions_hardline` 每次都拖进子进程机器，`tests/test_import_layering.py` 正是为这类方向而设。导入路径不变 |
| 2 | TOOL-01 的守卫只查 `shell_spec` **成员在不在**，绝不取值 | 取值会在注册期跑宿主代码：走阶梯的 provider 可能慢，抛异常的会被读成「没有这个成员」而不是「解析失败」 |
| 3 | 没有 `shell_spec` 的旧执行器读作**今天的平台默认**，不是 `Exhausted` | LADDER-05 承诺 PR-1 至 PR-6 用户不可见。判成 `Exhausted` 会让每一个没改过一行代码的宿主每次 shell 调用都 DENY |
| 4 | SPEC-08b 的「记录缺席 ⇒ 拒绝该次调用」只在运行时链路上成立；直接调 `ShellTool.execute()` 仍走参数 | 工具是公开面，宿主本来就可以不经 plan 直接调它，而那条路径今天也不过权限引擎。运行时链路上 `plan.decided` 恒在 |
| 5 | LAUNCH-01d 的「argv 里以路径点名的每一个映像」按**保守读法**实现：argv 中指向已存在文件的绝对路径必须有证明条目 | 哪些 argv 元素是映像名要看每种方言怎么组 argv，那是 PR-4。今天没有任何东西构造得出 `AttestedLaunch`，所以这是**故意从严**；宽松的默认值一旦随代码到场就再没人选过它。PR-4 要拿真实 argv 形态复核这条谓词 |
| 6 | 契约里 `fingerprint_of` 是 `Unspecified`，实施取**带类型标记、带长度前缀**的规范编码再 sha256 | 不带标记的拼接会让 `None` 与字符串 `"None"` 撞成同一个指纹，而 IMG-03a 要的恰恰是「allowlist 少一条 pin」这种差别 |

**外部 code-review（2026-09-05，15 条 / 修 13 条）之后另加两条约束，后续 PR 必须守住：**

- **`ShellExecutor` 只能有方法成员。** 它是 `@runtime_checkable`，加一个非方法成员会让 `issubclass()`
  直接抛 `TypeError`，并把每一个早于该成员的宿主执行器的 `isinstance()` 翻成 `False`。「声明解释器」因此
  是一个**可选的伴随协议** `ShellSpecProvider`，不是 `ShellExecutor` 的成员。
- **宿主要用到的启动/spec 类型都从 `agentao.host.protocols` 再导出。** 宿主实现 `ShellExecutor` 就必须
  构造 `ShellRequest`，而 `agentao.capabilities.*` 是内部面；不导出等于逼宿主伸手进内部。
- 复核（LAUNCH-01d）比初版更严三处：`PublisherTrust` 与任何不认识的 pin 形态一律拒（没有验签实现，
  「查不了就拒」）、`st_ino == 0` 视为答不出（Windows 上取不到 file index 时正是 0）、HashPin 比对
  绑定路径（用 `HashPin.matches`，否则给别的路径签的 pin 能顶替这一条）。

## 5.1 PR-2 进度

**已落地：方言分派 + cmd 方言。** `hardline_check` 现在按 spec 的方言选地板，**而这道选择本身被
`policy_enabled` 闸住**：政策关闭的两级（`legacy_cmd`、`system_posix`）照旧跑今天那套 POSIX regex 地板，
一个字节都不变 —— LADDER-05 与 q4 都要求如此。新增 `agentao/permissions_hardline/_cmd.py`：
CMD-01（六个控制关键字在命令位置、任何分组括号；引号内与 `^` 转义的是字面量）、TOK-02 的 cmd 行
（`%VAR%`、`%1`–`%9`、`%*`、`%%A`、`!VAR!` 任一出现在任何位置即不透明）、NAME-01 的内部命令表、
以及 q2 定案的 Windows 不可恢复类（`format`、`diskpart clean`、`cipher /w`、BitLocker 擦除与密钥保护器删除、
卷影副本删除、盘根递归删除）。39 条测试，三处修复逐个证伪。

**已落地：效果表（`_effects.py`）。** TOK-01 的 `Literal`/`Dynamic`；EFF-01 的四个取值与「空集 = 惰性」；
EFF-08 的登记字段（`execution_triggers`、`rebind_triggers`、`caller_scope`、`predicate_positions`，
**每条带出处**）与只从这些字段推出的 `flags()`；三张表按 q9 定案取「最小 + 常用只读工具链」——
`git` 的读子命令惰性而 `-c core.pager=`／`--exec-path=` 触发 `executes_input`，WRAP-07 的九个前缀运行者
（`timeout`、`nice`、`env`、`sudo`、`xargs`…）永不惰性，EFF-07b 的重绑形式逐方言登记，
EFF-05 的 provider 驱动器谓词。40 条测试，三处证伪（含 `caller_scope` 必须挂在 **executes_input** 那一侧 ——
挂错任一侧都有一个门槛会漏）。**不含**映像解析与 oracle：那是 PR-4，也正是「与运行期状态无关」这条界线。

**已落地：bash 语法闸（`_bash.py`）。** BASH-01 的全部构造（命令替换与反引号、进程替换、参数展开与算术展开、
`{ }` 分组与 `( )` 子 shell、十七个关键字、heredoc 与 herestring、指向 `/dev/tcp`／`/dev/udp` 的重定向、
`trap`／`exec`／`eval`）与 BASH-01a 的四类会改 argv 的未引号展开；引用状态逐字符求出，切分失败（引号未闭合）
即不透明。接在 `posix` **且政策开启**（即 git_bash 一级）之下、且**先于**命令级规则 —— 过了这道闸仍要过危险表。
54 条测试，三处证伪。

两条实施记录：

- **`"$FLAGS"` 一度被判成未加引号的变量。** 原写法按词扫描再在词内搜索，而词的首字符是引号 —— 引号本身是标点、
  状态为「未引号」，于是整个词被当作未引号处理。改为先按引用状态把引号内的字节抹掉，再在抹掉后的文本上匹配。
- **关键字取「未加引号、任意位置」这一钝读法**，`echo if` 也拒。理由写进模块：判断一个词在不在命令位置，
  要先有正确的切分，而切分要先知道展开 —— 那正是这道闸拒绝去假设的东西。代价有一条测试专门钉住。

**已落地：PowerShell 降级（`_powershell.py`）。** LOWER-01 的十步、LOWER-02 的 21 个 kind 接受清单、
LOWER-03 的有状态源码保真走查，按 codex 的 `powershell_tree_sitter.rs` 逐函数移植 —— 移植而不是照着规范
重写，是为了让 LOWER-04 的语料评的是**这份实现**、不是我对 PowerShell 的理解。依赖已加：
`tree-sitter` 与 `tree-sitter-powershell` 在 `[project.dependencies]` 下带 `sys_platform == "win32"`，
在 dev 组下无条件（每个平台都要跑得了这 68 条，否则唯一没人能本地验的那套语法正好是要发出去的那套）。

**LOWER-04：codex 的 `powershell_lowering.json` 原样拷进 `tests/fixtures/`，68 条首跑全对。**
24 条断言整条 argv 相等；44 条断言拒绝，**并钉住每条拒在第几步**（分布 `{1:2, 3:5, 5:23, 7:11, 8:1, 9:2}`）——
只问「拒没拒」的话，一个把所有脚本都拒在第 1 步的实现能全过。三处证伪各让 15、若干条变红。

接线：`powershell` **且政策开启**时先降级；降级失败返回它的步号，**降级成功也拒**（理由写明命令级分析尚未实现）。
这一支今天不可达（政策开启的 PowerShell 一级还构造不出来），正因如此才要 fail closed —— 留成放行，
就是给「真的把这一级造出来」的那个 PR 留一扇没人打开过的门。

**已落地：嵌套启动与生成进程（`_wrappers.py`）。** WRAP-01 的包装体表（十二个命令词 → 被调方言）、
WRAP-02 的 PowerShell 启动面解析（文档化短名优先，其余按前缀匹配，**歧义即拒**；`-ExecutionPolicy` 与
`-WindowStyle` 各吃掉一个值；`-EncodedCommand` 先按 base64/UTF-16LE 解码再给理由；清单外的开关一律拒）、
WRAP-03 的 cmd 分析、WRAP-05 的生成进程集（`Invoke-Command` **只有带远端参数集时**才算，八个取值逐个测）。
接进 cmd 与 bash 两处地板；PowerShell 那一支在「命令级分析未实现」的 fail-closed 接缝之后。39 条测试，三处证伪。
WRAP-04 无需实现：`command_name_expr` 与 `command_invokation_operator` 都不在 LOWER-02 的接受清单里，
`& …` 与 `. …` 在第 5 步就已不透明（规范里记的可达性）。WRAP-06 是理由归属，由各处 reason 承担；WRAP-07 在效果表里。

**PR-2 至此交付完毕，唯一未闭的是 q11** —— `call` 与 `start` 的 `rebinds_caller` 作用域无文档，只能在
Windows 上实测；定案之前两者取保守读法（`call` 已被 CMD-01 拒、`start` 已被 WRAP-05 拒，所以今天不可达）。
按本文 §3 的原话：**只要 q11 还开着，「PR-2 做完了」这句话谁都说不出口。**

两条实施记录：

- **cmd 的危险表必须带命令位置锚点。** 第一版按词搜索，于是 `echo format C:` 被判成一次格式化 ——
  与 POSIX 表早就有的 `_CMDPOS` 是同一个理由，而我把它漏掉了一轮。
- **`^` 要把它转义的那个字符移出「非引号区间」，不能只是跳过。** 只推进下标的话，`echo ^(hello^)`
  里的括号仍留在非引号文本里，被判成分组。

## 5.2 PR-3 进度

**已落地。** TOOL-02 的规则 `dialect` 字段（四个取值，未知即拒；**未标注的规则照旧匹配一切**，
所以另有 `unspecified_shell_rules()` —— 带 `args.command` 而无标注的规则在 PowerShell rung 上无从解读：
套用是拿别的语言的模式去匹配，跳过是悄悄丢掉作者依赖的一条规则，所以那一级拒绝被构造出来）；
引擎按方言过滤规则，排在其余匹配之前。CFG-01/CFG-02 的用户级 `shell` 块（`path` 与 `dialect` 成对，
只给其一即拒并点名缺的那半；`rung` **不是字段**，给了即拒并说明它由方言、目标平台与映像身份导出；
`allow_git_bash` 默认 `false`，LADDER-02）。CFG-03 的 `PermissionConfig { rules, sources, shell }`
穿过 embedding factory 与两个 ACP root —— 有一条测试直接读三个 root 的源码，确认没有一个还在调旧的
只读规则的加载器。16 条测试，三处证伪。

两条实施记录：

- **用户级 `permissions.json` 的顶层键集是封闭的**，加 `shell` 要同时开 `_LEGAL_DOCUMENT_FIELDS`；
  否则整份文件被拒，报「未知顶层键」。这正是那个封闭键集该有的行为，记在这里是因为它会让人以为加错了地方。
- **CFG-03 说「子代理工厂不读任何文件」，而 `agentao/agents/tools/_wrapper.py` 今天仍在调加载器。**
  未改：改它属于子代理计划的范围，不在本阶梯里。记为已知偏离，交给拥有子代理构造的那个 PR。

## 5.3 PR-1 至 PR-3 的外部 code-review（2026-09-05，15 条全修 + 自查 1 条）

**头条是我自己引入的绕过，而且我的测试结构保证抓不到它。** 包装体／生成进程的识别只看了 body 的
**第一个** token，于是 `echo hi & start notepad`、`echo hi && cmd /c format C:`、`"cmd" /c del C:\*`、
`c^md /c dir` 全部返回 `None` —— 而 `scan_cmd` 是 cmd 方言唯一的地板，后面没有 POSIX 表兜底。
bash 侧同形。**我的 37 条包装体测试全部直接调 `classify(word, args, dialect)`，没有一条把包装体放在
第 0 位以外的位置，也没有一条走 `scan_cmd`／`scan_bash`** —— 单元测得很细，接线一条没测。
修复：新增 `classify_body()` 扫描每一个命令位置，并在引号／`^` 抹除改变了命令词本身时报
`WRAP-01:unreadable-command-word`。

其余 14 条中值得记的：hook 重判时 `_decide()` 没带 spec，于是改写后的命令按 `shell_spec=None` 重判 ——
`Exhausted` 的 provider 不再拒绝、方言地板整个跳过，而 plan 里冻的却是那份 spec；
`rule_matches_dialect` 在方言未知时对**带标注**的规则返回 True，一条 `dialect: "powershell"` 的 allow
因此对 POSIX 主机上的 `write_file` 生效；`shell.allowlist` 在封闭键集里却从没传给 `ShellBlock`，
配置读回来像是生效了、实际一条 pin 都没钉；`PermissionConfigError` 少传一个参数，
在 composition root 抛 `TypeError` 而不是加载器自己的错误；`default_spec` 把 SPEC-04 的本机声明默认成
`True`；`clean` 那条危险模式没锚到 diskpart，`build && clean` 被判成抹盘。文档两处也补了：
`host-api.md` 的宿主契约破坏性变更、`configuration.md` 孪生里已被删掉的 `ShellRequest.env` 说法。

**自查补的第 16 条：给参数加引号能绕过 cmd 危险表** —— `del /f /s /q "C:\*"` 返回 `None`。
引号是人写路径的方式，不是另一种意思。改为按「引号里有没有结构字符」分两类：只包着参数的引号拆掉，
包着分隔符的引号照旧抹掉 —— 所以 `echo "a & format C:"` 仍然放行，它确实一次格式化都不会跑。

评审另点出两处**未改**且判为覆盖面决定而非缺陷：多条命令用 `;`／`&&` 连起来时，效果表还没有逐条走
（那是可信解析那一级的 `analyse_body`）。

## 5.4 PR-4 进度（可信解析）

**已落地：IMG-01 的谓词与 oracle 契约（`agentao/permissions_hardline/_trust.py`）。**
`IdentityOracle` 的 19 个方法、`ORACLE_METHODS` 与 `SELECTION_METHODS`、`oracle_answers`／
`target_is_local`／`oracle_complete`；IMG-06c 的三态 `ReparseResult`；`ancestors_to_volume_root`、
`path_within`、`trusted_root_chain`（本趟入口集 + 深度上限）、`trusted_image`、`host_identity_ok`。
`select_rung`／`attested_spec`／`derive_rung` 与 `Exhausted` 全部按契约实现，**翻转前两条政策关闭的
分支就是它今天返回的东西** —— 阶梯本体只有 `LADDER_FLIPPED` 之后才跑。116 条测试（`tests/test_trusted_resolution.py`，桩 oracle 在 `tests/_trust_fakes.py`）。

**已落地：子进程环境（ENV-01、ENV-01a、ENV-02–06）。** `EnvInputs`、`read_env_inputs`、
`filtered_path_entries`（先归一、再剔相对与工作树内、再过 IMG-01、按归一结果去重）、`child_env` 的三分法、
`value_ok` 的两种登记形状加两条通用子句、`fold_key`／`fold_base` 的 Windows 折叠与碰撞丢弃、
`pinned_psmodulepath`。`PinnedEnv` 补齐 `appdata`／`local_appdata`／`tmpdir`／`unknown_keys` 与
`shapes_ok`／`system_paths`，形态谓词四个。

**已落地：逐级命令行与前奏（LAUNCH-02–09e）。** 三套计量（`createprocess_units`、`cmd_line_chars`、
`execve_total_units` 与 `posix_limits`）、`has_lone_surrogate`、`encode_workdir` 的三种方言、
`prelude_for`、`request_for` 与 `oversize_reason`。

**已落地：裸词解析（NAME-01、NAME-02、NAME-03）。** `resolve`（在**交进来的**搜索路径上按 `PATHEXT` 或
精确文件名解析）、`resolve_name` 的三条规则、`NameResolution`。PowerShell 那一支按实测命令表分派
alias → function → cmdlet → 外部，**而那张表今天是空的**：它必须在本 PR 建立的钉住启动状态里量，
只有 Windows job（G21）量得到。空表的后果是每个 PowerShell 裸词不透明、rung 仍服务显式路径 —— 见下面的偏离 3。

**已落地：封闭集的组合层（`agentao/permissions_hardline/_analysis.py`）。** `analyse` 按方言切命令
（三个方言各用自己的分词器：`_cmd.commands_of`、`_bash.commands_of`、`_powershell.commands_of`）、
`analyse_body` 的 EFF-03 递归（退出态、`rebinds_caller` 的并入、WRAP-01 的重新进入、WRAP-05、EFF-05、EFF-06、
IMG-02 的两半）、`floor` 的 LAUNCH-08 计量与 `decided_call` 的一次性记录。判定链现在是：
planner 先建 `DecidedCall`（今天的 regex 地板 → 方言地板 → 封闭集 → 环境与证明集），
再把它交给权限引擎，引擎读它的裁定而不是另算一遍（TOOL-03 的顺序不变，来源只有一个）。22 条测试。

**Windows 危险表移出 `_cmd.py`（新 `_windows.py`），两个方言共用。** 见下面的偏离 12。

**尚未落地：本机 Windows oracle。** PR-4 的每一条门槛都在 ubuntu 上注入桩 oracle
（门槛矩阵里 `ubuntu / PR-4` 的 54 行），访问掩码与 Authenticode 的真实答案属 PR-6 的 Windows job。

### 5.4.1 PR-4 的实施偏离

| # | 偏离 | 为什么 |
|---|---|---|
| 6 | 可信解析在 `_trust.py`，`analyse_body` 计划放 `_analysis.py`，都不在 `_scanner.py` | 模块归属表把方言分派与 EFF 标志都记在 `_scanner.py` 名下。分派留在那里，但可信解析是**运行期状态**那一半：它要 oracle、要目标平台、要子进程环境。合在一个文件里，`_scanner` 会同时是分派器、分析引擎与信任引擎 |
| 7 | `read_env_inputs` 两种情况都问 oracle，不分本机／非本机两支 | 契约写了两支（本机读进程环境、非本机问 oracle）。两支算同三个值就是两次机会算出不同的东西，而 G24-10 要断言的正是「请求里没有地板机器的任何值」—— 让本机 oracle 去答本机，这条就成了结构性的 |
| 8 | `MEASURED_COMMAND_TABLES` 是空表，`identity_measured` 因此恒假 | 诚实的状态：NAME-02 明写这张表要在**钉住的启动状态**里量（`-NoProfile`、自动加载关闭、会话配置默认），那只能在 Windows 上发生。开着自动加载量出的表会放行子进程随后 command-not-found 的东西 |
| 9 | `posix_limits` 用本机 `sysconf`，非本机 POSIX 目标没有接口 | 政策开启的每一级目标平台都是 Windows（`derive_rung` 把 POSIX 目标映到政策关闭的 `system_posix`），所以 POSIX 那套计量端到端不可达 —— G18-12 也是这么写的。契约留着的那个空缺没有东西要它填 |
| 11 | `default_spec` 委托给 `select_rung`，不再自己造那两级 | 两个函数各返回一遍翻转前的默认值，就是同一个值有两个家 —— 本文件集整套办法都是冲着这一族去的。委托之后 `select_rung` 也不再是生产路径上够不着的代码：它的两条政策关闭分支就是今天每一次 shell 调用读到的那份 spec。它收一个只答两问的 oracle（目标平台、是否本机），**故意不是一份残缺的 `IdentityOracle`**：SPEC-05c 说缺方法的 oracle 让**政策开启**的 rung 未认证，而这个对象只为够到那两级不问 oracle 的 rung 而存在 |
| 12 | Windows 危险表移到 `_windows.py`，cmd 与 PowerShell 共用；PowerShell 侧按命令锚定（`match`）而不是按文本搜索 | 它本来就不是 cmd 的表 —— 里面已经有两条 PowerShell 拼法（`Remove-BitlockerKeyProtector`、`Remove-Item … Win32_ShadowCopy`）。**一个类拒的是什么，是平台的性质，不是语法的性质**：格式化一个卷毁掉的是同样的字节，不管哪个解释器敲的。锚定方式不同是因为两边输入不同：cmd 扫的是文本、要命令位置锚，PowerShell 已经把 body 切成命令、匹配到第 0 位就等于在命令位置 |
| 13 | 包装体识别放进各方言的地板（用它自己的分词器），`classify_body` 删除 | 它从**抹掉引号的视图**里取命令词。找边界这样做是安全的，读文本就不是：一个带引号的参数在那个视图里变成空白，于是它恢复出来的嵌套 body 不是子进程会跑的那个 —— 而 WRAP-06 要的正是逐字节的那一份。换成各自的分词器之后，`"cmd"` 与 `c^md` 会被**解析成** `cmd`，比原先报「读不出这个命令词」更准 |
| 10 | 可信表里的**外部程序**条目定义一次、按方言实例化三份 | IMG-02 的名字半问的是「**该方言**的可信表」，所以每个方言都要有条目；而 `git` 是同一个程序、同一套参数形状、同一组效果，分词差异在查表之前就发生完了。三份手维护的副本会漂移，且漂移无声 —— 给 POSIX 的 `git` 加一条触发而忘了 PowerShell 的那条，就是一个没人看得见的 Windows 绕过 |

### 5.4.2 实施中发现的十七条

1. **契约为错的那张表拒掉了整级。** `attested_spec` 在 `identity_measured` 为假时返回 `Exhausted`，
   而 IMG-07 说的是「该 rung 的**裸词**全部不透明」，NAME-02 早就为它的同族条件（封闭环境未确立）写下
   同一种降级：「rung 仍按 IMG-04 服务显式路径」。CFG-02a 那句「不在实测表 ⇒ 拒绝该来源」说的是**另一张表**
   —— `derive_rung` 读的 edition 表。**两张表在散文里同名**，于是一张的缺席被套上了另一张的后果，
   代价是为一张名字表把 Windows 降到 `cmd` 那一级更粗的地板上。三处各自点名了自己那一张。
2. **路径谓词按目标平台比较，签名里却没有目标平台。** `ancestors_to_volume_root`、`trusted_root_chain`、
   `trusted_image` 都要按目标的路径规则走祖先链与包含判定，而契约的签名只收 oracle ——
   现问 `oracle.target_platform()` 会撞上 G18-14 断言的「全程只调用一次」。三个签名各加一个 `target`。
3. **`prelude_for(identity)` 拼不出 LAUNCH-05 的前奏。** `<W>` 是逐次调用的、identity 是逐 rung 的，
   只收 identity 的签名没有地方放它。加 `workdir_literal`，并把返回类型改成 `str | None`
   （身份四项或 `<W>` 编码不出来 ⇒ 拒绝，绝不换转义方式）。
4. **常用工具链只登记在 POSIX 表里**，而 G04-34 要求 `git status` 在 pwsh、cmd、git_bash 三级都放行 ——
   见偏离 10。
5. **`fingerprint` 的规范编码没有 frozenset 一格。** `PinnedEnv.unknown_keys` 是 frozenset 且在投影里，
   于是**任何**政策开启的 spec 一构造就抛 `TypeError`。补一格，按编码结果排序 ——
   集合无序，两个相等的集合必须编出同一个串。
6. **`is_abs_file` 收下了带尾分隔符的路径。** `C:\Windows\System32\` 通过了「绝对文件」形态检查，
   而 G14-05 正要它被拒。
7. **契约的行为测试是唯一抓到签名改动落地的东西。** 给 `trusted_root_chain` 等三个函数加 `target` 之后，
   `check_design_set.py` 绿、`mypy --strict` 绿 —— 而 `tests/test_powershell_contracts.py` 里七条测试红：
   它们的桩按旧签名写。**五道闸缺一不可，不是一句口号**，这一处正是 rev 42 建那份测试要挡的那一族。
8. **预检的前奏拿到了空的 `<W>`。** IMG-09 明写预检用**同一段**前奏，而契约里那次调用传的是空串 ——
   `Set-Location -LiteralPath ''` 会失败、子进程退出 98，于是**每一个健康的解释器**都读回
   「封闭环境未确立」，NAME-02 整条随之失效。桩 oracle 恒答 `True`，这一条只能靠读代码发现。
   改取 launcher 自己所在的目录 —— LAUNCH-09 的起始目录本来就是它。
9. **整张 Windows 危险表只有 cmd 地板读得到。** 它定义在 `_cmd.py` 里，而 `scan_powershell` 一条危险类都不跑 ——
   于是一个政策开启的 PowerShell rung 上 `Format-Volume`、`Clear-Disk`、`vssadmin delete shadows` 全部放行。
   表里本来就有两条 PowerShell 拼法，这件事本身就说明它不是 cmd 的表。移到 `_windows.py`，补上 q2 那几类缺的
   PowerShell 拼法（`Format-Volume`、`Clear-Disk`、`Disable-BitLocker`、递归删盘根的 `Remove-Item`），
   两个方言共用一份。
10. **`classify_body` 恢复出来的嵌套 body 是假的。** 它从抹掉引号的视图里取词，而带引号的参数在那个视图里
    是空白 —— `bash -c "rm -rf /"` 恢复出来的 body 是一个孤零零的 `"`，送进扫描器得到「引号未闭合」。
    只要那份 body 唯一的用途是拼进理由字符串，这个错就看不出来；一旦真的拿去扫，它立刻现形。
11. **WRAP-06 九轮以来没有实现过。** `nested_launch` 恢复 body 的唯一理由就是「危险的嵌套 body 要按它**自己**的
    理由被拒」，而没有任何一处读它 —— `bash -c "rm -rf /"` 只报「启动了第二个解释器」。现在按被调方方言重跑
    那一层地板：cmd 侧 `echo hi && cmd /c format C:` 报 `hardline:format-volume`，
    POSIX 侧 `bash -c "rm -rf /"` 报递归删除类。递归有上限（`MAX_NESTED_DEPTH`）。
12. **上一轮外部评审的头条修复没有任何测试。** 它加的 `classify_body` 关掉了「只看第 0 个 token」这个绕过，
    我当时用探针逐条核过、也如实报告了 —— 而**探针只活在对话记录里**。全仓 grep `classify_body` 与
    `unreadable-command-word`，测试里零命中；这一轮重构本可以把它悄悄改回去，是证伪习惯把这个洞照出来的。
    补 `test_a_wrapper_is_recognised_at_every_command_position`（cmd 五种、bash 四种，外加两条仍需放行的对照）。
13. **冻结记录把 `enable_hardline=False` 变成了空操作（我这一轮引入的）。** 判定链改成「planner 先建记录、
    引擎读它的裁定」之后，`_decided_call` 直接调 `hardline_check`，绕过了引擎的那个开关 —— 于是一个明写
    「我自己负政策责任」的宿主，shell 调用照样被记录里的 DENY 拦在启动前，而引擎那边根本没跑地板。
    修法：planner 从引擎读 `hardline_enabled`（新加的只读属性，两个读者读同一个标志），
    关掉时 `closed_set=False` **只**压掉裁定 —— 子进程环境、证明集与 LAUNCH-08／09 的守卫照跑，
    因为「这条命令行拼不出来」不是一个政策问题：把它们一起关掉，等于拿一个 DENY 换一个
    `UnicodeEncodeError`，而 LAUNCH-08e 存在的意义正是不让那件事发生。
14. **`verify_attested_launch` 把「答不出的文件系统身份」当成了匹配。** 它写的是
    `if local_filesystem_identity(path) != entry.filesystem_identity`，而两个 `None` 相等 ——
    于是这道检查报出一个干净的结果、什么都没证明。`local_filesystem_identity` 自己的 docstring 明写
    「每一个调用者都把答不出当拒绝」，那句话在这一处是假的；而 `st_ino == 0` 恰恰在这条阶梯瞄准的那个平台上
    可达（网络卷与可移动卷）。改为：现场答不出即拒（`launch-attest:unidentifiable`），不看条目那一侧。
15. **Windows job 首跑的唯一失败，是规范自己写不通的前奏。** LAUNCH-05 先把
    `$PSModuleAutoLoadingPreference` 设成 `None`，再调 `Get-Item` 与 `Set-Location` —— 而实测
    （证据 §3.20a）：这个偏好一开，`Microsoft.PowerShell.Management` 与 `Microsoft.PowerShell.Utility`
    里**一个命令都解析不到**，因为 `pwsh -NoProfile -Command` 的默认会话根本没有预加载它们。
    于是守卫里的 `Get-Item` 报 command-not-found、脚本继续、`Set-Location` 被 `catch` 接住 ⇒ 退出 98。
    连带后果比那条测试大得多：**可信表里 18 条 PowerShell 条目在这个「钉住的启动状态」里绝大多数不可解析**，
    而 NAME-02 要求每一条在该状态下验证可解析 —— 整个 rung 会变成什么都跑不了。
    改法：LAUNCH-05a 四段，身份守卫只用 Core 与 .NET 静态方法，随后显式导入那两个模块（从刚验过的安装根），
    再关自动加载并复查，最后切目录。**这是十七轮文档评审谁都没看出来、只有真机能答的一条。**
16. **我的 Windows 测试说不出失败原因。** 它带的是退出码与 stdout，而 PowerShell 拒绝的理由在 stderr 上；
    首跑只报出 `AssertionError: (98, '')`。给一个自己复现不了的平台写测试，就得让它自带诊断。
    补 `Launched` 记录（退出码、stdout、stderr、组装好的命令行、两个目录），**并且要写 `__repr__` 不是 `__str__`** ——
    `assert x, obj` 走的是 `repr`，第一版只定义了 `__str__`，于是消息印出来是 `<Launched object at 0x…>`，
    还是什么都没说。
17. **我自己引入又被测试抓回的一条：** `allowlist_entry_for` 一度按目标平台的路径规则宽松查找，
   而 `HashPin.matches` 仍按逐字相等确认 —— 同一对路径两种比法，于是一条大小写不同的 pin 会**被找到**
   然后匹配失败，把「这个映像没被 pin」变成「这个映像不可信」。改回逐字相等，两处同一条规则。
   **代价明说、不掩盖：** 没有任何东西把用户写的 allowlist 路径规范化（读配置的加载器没有 oracle），
   一条非规范写法的 pin 会静默不生效 —— 记进规范 §7.3 q15。

## 5.5 PR-6 进度（Windows job）

**已落地：`.github/workflows/ci.yml` 的 `windows` job。** `windows-latest`，Python 3.10 与 3.12，
装与 ubuntu `test` job 相同的 extras，跑**全量套件**。这是本仓库有史以来第一次在 Windows 上跑测试 ——
此前每一条 Windows 专属路径（路径分隔符、`%COMSPEC%`、`list2cmdline`、控制台编码、
`NoDefaultCurrentDirectoryInExePath`）都是没量过就发出去的。job 分**两步**跑，好让红的时候读得懂：先跑阶梯自己的那十二个模块（为 Windows 写的，本该绿），再跑全量套件（从没在这里跑过，首跑是测量不是回归）—— 第一步红说明阶梯坏了，只有第二步红说明既有套件第一次撞上 Windows。job 里还先打印这台 runner 上 `pwsh`／`powershell`／`cmd`／`bash` 各在不在：**一次静悄悄跳过全部测量的绿，和一次真做了测量的绿，
长得一模一样**。它同时也是 `pyproject.toml` 里两个 `sys_platform == 'win32'` 依赖唯一真正装上的地方 ——
PowerShell 降级那 86 条测试在别的 job 上全是 skip。

**已落地：`tests/test_windows_launch_matrix.py`（9 条，仅 Windows）。** 门槛矩阵里不需要 identity oracle 的
那几行：LADDER-05 的翻转前默认（G10-02）、LAUNCH-03 的命令行真的启动得起来、LAUNCH-09a 的
`|| exit 98`、ENV-04 的 `NoDefaultCurrentDirectoryInExePath`、ENV-06 的封闭集、G18-05 的带空格路径、
LAUNCH-02／LAUNCH-05 的「前奏与 body 是一个参数」以及身份不符时的退出码 97。
oracle 仍是桩（访问掩码与 Authenticode 属本机实现，还没有），**桩之后的每一步都是真的**：
真的 `cmd.exe`、按真实文件身份与哈希建的 `ResolvedImage`、真的 `Popen`。

**已落地：`tests/test_attested_launch_smoke.py`（5 条，非 Windows）。** Git Bash 那一级的拼法
（`--noprofile --norc -p -c "cd -P -- '<W>' || exit 98; <body>"`）本身就是普通 POSIX shell，
所以它可以**在这台机器上**量：证明书启动请求端到端跑得通（LAUNCH-01d 的复核、起始目录、
`|| exit 98`、封闭环境、换掉映像即拒）。spec 是手搭的、阶梯永远导不出它（`derive_rung` 把 POSIX 目标
映到政策关闭的 `system_posix`），但 spec 之后的每一步都是真的。**在这里量出来的每一个接线错误，
Windows job 就不必第一个发现它。**

**PR-7 的前提不止 PR-6 绿。** LADDER-04 列的是 G09 的降级率、`ruff` 与 G20，而还有一条它没写：
翻转之后阶梯要真的走得通，而阶梯每一级都要 `oracle_complete`。今天 `LocalShellExecutor` 交出的是
一个只答两问的 oracle（目标平台、是否本机），够用是因为翻转前那两级政策关闭、根本不问 oracle。
翻转之后它会让阶梯**走空**，LADDER-03 再把走空变成每次 shell 调用 DENY —— 也就是说，
**没有本机 Windows oracle 就不能翻**。`default_spec` 在 `LADDER_FLIPPED` 为真时直接抛异常，
所以今天把那个常量改成 `True` 会立刻炸开，而不是安静地拒掉每一次调用。

**尚未落地：** 本机 Windows oracle（门槛矩阵里需要它的那些 `windows / PR-6` 行还跑不了），
以及 G18-02 的另一半 —— body 含非 ASCII、`%`、`"` 与换行时逐字节抵达。后者需要一件仪器
（一个把 `GetCommandLineW()` 打出来的程序），没有它就只能靠子进程 stdout，而那量的是控制台代码页
不是传输。**明说这一半还欠着，好过一个看起来像覆盖的脆弱断言。**

## 5.6 Windows job 首跑：155 条失败的分诊

首跑 155 条失败（3.10）／162 条（3.12），5017 通过 —— 97% 绿；第二跑降到 46／39。
用户批准了「全部修到绿」。分诊分三部分：**真产品缺陷**（§5.6.1）、
**没修的产品问题**（§5.6.2）、**测试侧对 POSIX 的假设**（§5.6.3）。
每一节自己那张表是该类唯一的清单 —— 引用条数时以表为准，不要在这里重述数字。

**第二跑值得单独记一笔**：46 条里有 17 条是我第一跑的修复**没修干净** ——
`newline=""` 我加在了两个 `open()` 上，而已存在文件走的是第三个写入口 `os.fdopen`，
CRLF 翻倍原封不动。**是我自己新写的那条守卫在 Windows 上把它抓出来的**，
这正是方法规则 27（报告修好之前，先说出把它改回去会变红的那个测试）要的效果 ——
但也说明**规则 27 只保证「有测试」，不保证「测了整个 sink 类」**：
补充规则见 §5.6.4。

### 5.6.1 四条真缺陷

**(1) 每一次编辑都会把 CRLF 文件的回车翻倍。** `LocalFileSystem.write_text` 用
`open(..., "w", encoding="utf-8")` 而没有 `newline=""`，于是 Python 把每个 `\n` 翻译成
`os.linesep`；而编辑工具读的是**字节**再解码（`file_ops.py:454`），一个本来就用 CRLF 的文件
读进来带着 `\r\n`、写回去变成 `\r\r\n`。**一个编辑文件的工具不许改写没被要求改的字节。**
修法是两处 `open` 都加 `newline=""`。
**这条回归守卫只在 Windows 上咬得动** —— POSIX 上 `os.linesep` 就是 `\n`，把修复改回去测试照样绿
（实测过，不是推测）。这也正是它能活到今天的原因。

**(2) 没有家目录时，Windows 上的状态目录每次运行都不一样。** `_is_private_to_current_user` 查的是
POSIX 权限位，而 `mkdir(mode=0o700)` 在 Windows 上不设它们，于是这个判据恒假、
`_private_dir_or_mkdtemp` 每次都落到 `mkdtemp` —— 配置与记忆每跑一次换一个地方。
修法：Windows 上改查「是不是一个真目录、而不是 reparse point」，保证来自
`%LOCALAPPDATA%\Temp` 本身就是逐用户的这一事实。**限制明写**：`TEMP` 若被指到共享目录，这比
POSIX 那版弱 —— 「另一个账户能不能写这里」要 ACL 才答得出，而那正是本阶梯里那个还没实现的 identity oracle 的活。

**(3) 每一次记忆读写都漏一个 SQLite 连接。** `storage.py` 的每处调用都写作
`with self._connect() as conn:` —— 读起来像资源作用域，而它不是：
`sqlite3.Connection.__exit__` 只提交或回滚事务，**连接照开不误**，直到 GC 才关。
POSIX 上完全看不见（打开着的文件 unlink 掉就没了），账单落在 Windows：文件锁还在，
宿主删不掉自己的工作目录 —— `WinError 32`，点名 `memory.db`，WAL 的两个附属文件同理。
修法：`_connect` 改成真正的上下文管理器（提交／回滚**并**关闭），
`:memory:` 那份连接是故意留着的（关掉就把库丢了），另给 store／manager／`Agentao.close()`
补上显式的 `close()`。

**(4) 同一时钟刻度内的两次会话保存，会静默吃掉前一次。** 会话文件名完全由
`datetime.now()` 生成（到微秒），**没有任何一处检查这个名字是否已被占用**。
Windows 报出的时钟粒度远粗于那六位数字暗示的精度，于是接连两次保存撞名、
后一次直接覆盖前一次 —— 一个用户还能按 id 恢复的会话就这么没了。
POSIX 上这只是把竞态收窄，并没有消除它。修法：撞名就往后让一位。

**(4b) 与 (4) 同源、但没法靠重试关掉：** 一个**持续**占着句柄的读者（本套件里就有一个热循环
读线程）会让 `os.replace` 每一次都失败，任何有界重试都赢不了。这不是缺陷而是平台事实：
Windows 上 agentao 的原子写是**「要么原子，要么拒绝」** —— 读者仍然永远看不到写了一半的文件，
**代价改由写方承担**。重试只对短命句柄（编辑器、索引器、杀毒软件）有效，那也正是真实场景。
对应的测试因此把断言拆开：不撕裂这条到处都查，被拒次数在 POSIX 上必须为零。

**(3) 和 (4) 都不是「Windows 专属」** —— 它们是 POSIX 宽容掉的缺陷，
在 Windows 上才收到账单。守卫因此都写成平台无关的：(3) 数开／关次数而不问「文件能否删除」，
(4) 冻住时钟而不去赛跑。

### 5.6.2 两条没修的产品问题

**为上游契约写的 `hooks.json` 在 Windows 上跑不起来。** 命令 hook 走 `shell=True`，
POSIX 上是 `/bin/sh`、Windows 上是 `cmd.exe`，而 cmd 不认 `'` 作引号、`printf` 不存在、
重定向前的空格会留在 echo 出来的文本里。约六十条测试栽在这上面。
**agentao 是否应当在 Windows 上用 `sh` 跑 hook（上游就是 `sh -c`）是一个产品决定，本轮没有做**：
从 PATH 上挑一个 `sh.exe` 恰恰是本设计的 ENV-01 说不能干的事。
hooks 契约文档 §G5 自己那句「agentao has no Windows CI job」正是这块没被量到的原因，现在量到了。
测试改用**产品自己的 exec 形式**（`args`，无 shell），v1 契约那些改用 `cat`／`type` 打印文件 ——
两种拼法都不让 payload 经过任何 shell 的解析器。

**hook 溢出文件在 Windows 上没有 0600 这条保证。** `_budget.py` 用
`os.open(..., 0o600)` 落盘，而在 Windows 上那个 mode 只影响只读位，真正的访问权由
ACL 决定、并从父目录继承；溢出文件落在**项目树内**（`.agentao/hook-outputs`），
所以在仓库对多个账户可读的机器上，可能带着凭据的 hook 输出也一并可读。
要关掉它得写真正的 ACL —— 与 `agentao/paths.py`、`permissions_hardline/_trust.py`
推给「那个还没实现的 Windows identity oracle」的是同一件事。
本轮**没有**修，只把那条断言在 Windows 上跳过，并在测试的 docstring 里写明缺口本身。

### 5.6.3 测试侧的类别

| 类别 | 条数 | 修法 |
|---|---|---|
| prompt_toolkit 要真实控制台 | ~54 | conftest 里在拿不到输出时装一个 `DummyOutput` 的 app session |
| hook fixture 用 POSIX shell 拼写 | ~60 | 改 exec 形式；v1 那些改 `cat`／`type` |
| 文件编码留给平台 | ~20 | 读写一律显式 `encoding="utf-8"` |
| 断言里写死 `/` | ~10 | 按 `os.sep` 拼，或直接比 `str(Path(...))` |
| `HOME` 改了但 Windows 读 `USERPROFILE` | ~6 | 两个都设，另加 `HOMEDRIVE`／`HOMEPATH` |
| POSIX 专属 API（`getpgid`、权限位、`chmod 000`） | ~6 | 有则打桩，无则按平台跳过并写明理由 |
| `Path("/abs")` 在 Windows 上不是绝对路径 | 2 | 用本平台的绝对路径造 |
| ACP 的 UTF-8 stdio 闸对上 pytest 的捕获 stdin | 4 | 给它一对真的 UTF-8 流，而不是把闸拆掉 |
| `startswith(home)` 把 tmp_path 也算成 home | 1 | 先判项目路径 |

第二跑又清出四类（29 条），都是第一跑没扫到的角落：

| 类别 | 条数 | 修法 |
|---|---|---|
| 还剩的 `echo '<JSON>'` hook fixture | 17 | 同上：profile 契约走 exec 形式，v1 走 `cat`／`type` |
| Windows 路径插进 f-string 当 JSON（反斜杠成了非法转义） | 1 | 用 `json.dumps` 造参数 |
| 显式 `env=` 里少了 `SystemRoot`，Python 子进程起不来 | 1 | 补上解释器启动所需的几个变量 |
| POSIX 专属 API 不存在时 `monkeypatch` 直接抛错 | 1 | `raising=False`，并写明该平台无此调用路径 |

### 5.6.4 方法规则 28：修一个 sink，要把整个 sink 类一起验

方法规则 27 说的是「报告修好之前，先说出把它改回去会变红的那个测试」。
第二跑证明这条**不够**：我为 CRLF 写了守卫、守卫也确实存在，
但**修复本身漏掉了同一函数里的第三个写入口**，而本机（POSIX）跑不出差别，
所以「有一个会变红的测试」这个条件在本机是满足的 —— 只是它红不起来。

补充规则：**当修复的形式是「给某个调用补一个参数」时，
必须 grep 出该函数（以及该 sink 类）里的每一个同类调用，逐个确认，
再宣布修好** —— 特别是 `os.fdopen`／`os.open` 这种不写作 `open(` 的写入口。
配套地：**这类修复的验证必须发生在缺陷真会发作的平台上**；
本机绿只证明守卫不咬人，不证明缺陷已除。

## 5.7 本机 Windows oracle：先落访问掩码内核

PR-7 卡在本机 oracle 上，而它十九问里只有一问是真工程：`subject_can_replace`。
按 §5.6 之后定下的顺序 —— **先探针、再访问掩码内核（用真 ACL 验）、再补齐其余方法、最后接完整性** ——
本节记第二步。

**落点：** `agentao/permissions_hardline/_windows_identity.py`，`WindowsAccessOracle`。
它**刻意不是一个完整 oracle**：`oracle_complete` 要求 `ORACLE_METHODS` 全在，这个类只答其中一部分。
这不是疏漏 —— 政策开启的 rung 本就按设计拒绝不完整的 oracle，所以它可以先落地、先被真 ACL 量，
其余答案再写；反过来，在它完整之前把它接成阶梯的 oracle，只会让阶梯走空，而 LADDER-03 把走空变成
每次 shell 调用 DENY。

**纯标准库，是被逼的不是偏好。** 一个可选依赖**盖不住** oracle：缺了它不会降级成「未认证」，
而是降级成「根本没有 shell」。所以 Win32 面走 `ctypes`，pywin32 将来只能是它之上的快路。
每一个原型都显式声明 —— 不声明，`ctypes` 按 C `int` 取返回值，64 位上截断句柄，
`GetFileAttributesW` 的 `INVALID_FILE_ATTRIBUTES` 变成 -1、失败判据永不触发；
**一个悄悄量错东西的信任检查，比一个跑不起来的更糟**。

**探针带出来的一条正确性要点，务必留着：** `AccessCheck` 在这张清单上只认
`SeTakeOwnershipPrivilege`，而 `SeRestorePrivilege` 与 `SeBackupPrivilege` 是文件系统在**打开句柄时**
才查的，远在这次调用之后。所以一个持有它们的 token 能通过纯掩码检查、却照样替换得了映像 ——
这正是「提权的 agentao 是自己的攻击者」的机制。`REPLACE_PRIVILEGES` 因此在读任何 DACL 之前先短路，
且判据是**持有**而不是**已启用**：持有者随时可以自己启用它。

**每一处失败都答「能替换」。** 读不到安全描述符、路径不存在、`AccessCheck` 跑不起来 ——
每一种都是一条没人查过的链，而 IMG-06c 的全部意思就是这种链不许当作「查过了、很正常」放行。

**测试怎么才不是自说自话。** 掩码检查没法用桩证伪 —— 一个「返回掩码」的桩只会复述代码已经相信的东西。
所以 `tests/test_windows_identity_oracle.py` 在 Windows 上**自己写 ACL** 再问：
`icacls /inheritance:r /grant *<SID>:(RX)` ⇒ 两问都答不能；`(M)` ⇒ 两问都答能；
**`(RX,AD,WD)` ⇒ 目标掩码答能、祖先掩码答不能** —— 这一条就是把出厂卷根的形态搬进一个测试自己拥有的目录，
IMG-06a 拆分的直接回归。**runner 是管理员**（§3.23 实测），特权短路会让每一条 DACL 断言变空，
所以短路单独断言一次，其余用例把它关掉再测底下的掩码算术；关掉是诚实的，因为被测的正是算术那一层。

跨平台那几条（掩码之间的包含关系、祖先掩码不含两个 ADD 位）到处都跑：它们把 IMG-06a 的拆分写成算术，
免得日后被无声改回去。

## 6. 英文版

本文件集当前**只有中文版**。英文版在进入实现之前一次性生成，并从那时起由 `check_design_set.py` 的孪生检查
核对规则 ID、枚举与门槛矩阵字节相同；契约模块 `powershell-support-contracts.py` 只有一份、不翻译，两个语言版都指向它。
