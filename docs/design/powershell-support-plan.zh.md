# PowerShell 支持 —— 让 shell 地板认方言

> ⚠️ **仅设计，未授权实施 —— 例外是 §2.12 记录了子代理路径上一处早于本计划、已实测的活缺陷，无论
> PowerShell 是否会被建，它都应单独修掉（§5，PR-0）。** 这条例外只覆盖 PR-0 的**引擎**那一半，它可以
> 独立发布；不覆盖 PR-0 的 **MCP** 那一半 —— rev 20 在那里发现一句有保证、没机制的话：没有任何东西
> 把被取消的 token 送到关闭序列必须取消的那次在飞行中的调用上（D2）。§5 的 PR 阶梯是**依赖顺序**，
> 不是排期。本版自足。

**日期：** 2026-09-03
**状态：** 设计，**rev 24** —— 二十二轮维护者评审，一百四十三条发现，全部对源码核实属实并折进正文。
rev 23 改了可信根的谓词，并写下规则 11 来抓「改一条规则会在别处留下什么」；**rev 24 就是把规则 11 真
正跑一遍、跑过 rev 23 改动过的每一个术语（而不只是那个产生了发现的词）之后抓到的东西** —— 谓词、
`BASH_FUNC_*` 清除、签名，三者都还活在规范入口里。它同时关掉规则 7：那里还有三个包装器，各自按另一套
解析器、另一份环境或另一个主体去启动，却被当作放行重新进入。
**锚点：** agentao `main@3537753`（2026-09-01）；codex `openai/codex@b7cd519c76`（2026-08-31）；
pi-mono `@853a80d26`（2026-08-28）。三者均从锚定 commit 的本地 worktree 读取。§3.10 另读了
`PowerShell/PowerShell` 的一个上游文件，它的 commit、blob 与 sha256 记在那一节。
**方法：** 每条前提都带本仓锚点上的行内 `file:line`。§2.7、§2.12、§3.4、§3.8–§3.12 与 §3.14–§3.16 是
**实测，不是推理**；§3.10 与 §3.20 是**按钉住的 commit 从上游抓取**的，带哈希与重抓。
**范围：** 在 Windows 上用 PowerShell 跑模型的 shell 命令，以及
`agentao/permissions_hardline/_scanner.py` 必须先变成什么样。注入能力、子代理路径及其并发、registry
来源、MCP 所有权、两个 composition root、宿主工具替换、地板*与子进程*两侧的解释器与裸词解析、shell
profile、继承来的函数、根本不碰 `PATH` 的名字重绑，以及 Windows 命令行序列化都在范围内。不在范围内：WSL；macOS/Linux 上的 PowerShell；以及**本设计只收窄、不关闭的两处竞态** —— 在阶梯解析与 spawn 之间被装上的控制台会话配置，或被替换掉的解释器（§7、D4）。
**孪生件：** `powershell-support-plan.md`。
**相关：** `builtin-tools-four-way-codex-gemini-pi-agentao.md`、`permission-hardening-plan.md`、
`lint-gate.md`、`path-a-roadmap.md`。

### 修订历史

规则本身在正文里；这张表存在的意义是让后来的编辑者认得出「又犯了同一类」，而 §10 收着这些轮次产出的
方法教训。每一行只点明错的**类别**，以及被改正后的规则现在在哪。**「发现」一栏记的是那一轮的发现数，
而 rev 19 是一遍编辑性整理、不是一轮评审** —— 这就是「表里二十三行、合计一百四十六条」与「表头写
二十二轮评审、一百四十三条发现」之间差别的全部。谁要重算这两个数，需要的是这条约定，不只是算术；而
§10 自己的那个数是从表头推出来的，不是另外手记一份。**每一行记的是那一轮*提出*的条数，不是它折入的
条数：** rev 23 处理了第 20、21 轮提出的六条，那六条计在那两行里，不在它自己那一行里再计一次 ——
「发现晚两轮才落地」是常态，否则这张表会重复计数。

| rev | 发现 | 错在哪一类 | 规则现在在 |
|---|---|---|---|
| 24 | 2 P0、2 P1、4 P2、2 小项 | 规则 7 仍把 `Start-Process`、`Invoke-Item` 与 cmd `start` 当放行重新进入，而它们经 ShellExecute 解析、不走 5g，光一个 `-UseNewEnvironment` 就能在被放行的 body 里把过滤前的用户 PATH 装回来；规则 11 自己那一轮的清扫只查了一个词、没查它改过的每一个词，于是谓词、`BASH_FUNC_*` 清除与签名在摘要、表格与 PR 行里全部留旧；阶梯现在会走空，而走空是什么没定义；启动请求表达不了已规定的两种启动形态，也不携带证明结果；MCP token 仍是工具实例上的可变属性；签名被当成了 content pin | D5、D2、D4、D6、§10 |
| 23 | 2 P0、4 P1、2 P2、3 小项 | 可信根的谓词写成了「仅管理员可写」，而提权运行的 agentao 自己就满足它 —— 于是执行主体能写进这条规则本要把它挡在外面的那个根；规则改了之后，摘要仍把 allowlist 当作位置的替代项，而规则里又把它写得毫无功能；`-p` 只护一个进程、环境却贯穿整棵树，于是 `BASH_FUNC_*` 到达了被放行命令的子孙；规则 7 仍在重新进入一个会启动解释器的生成者；执行器契约被写成三个问题，而它是三段义务；一个正例没有任何门槛调度；task 集合只登记不移除。**早前轮次的六条曾被无记录丢弃，这一版全部处理** | D4、D5、D2、§6 |
| 22 | 1 P0、2 P1、1 P2、另 4 条 | allowlist 里的哈希或签名可以**代替**可信位置，于是它按构造准入用户可写的映像，而 body 内一句 `Copy-Item` 不需要竞态就能赢它；一个 token 名下挂着多个 MCP task，且取消可能早于登记；`rung` 对未知取值与非法配对都没有裁定；嵌套的解释器启动一条 D4 的保证都不带；映像检查读的是地板的文件系统，而非本机执行器并不在那上面 | D5、D2、D4、§6 |
| 21 | 1 P0、1 P1、2 P2、2 小项 | 在一条规则里修好了可信映像那个洞，却在另一条规则里继续把过滤后的 PATH 当可信根 —— 洞被重新打开，而且对每个裸词来说映像那一半退化成恒真；bash 那一级的地板被写成三种，而没有任何键能在它们之间做选择；一个非连续的门槛集合被写成区间 | D4、D5、D2、§5 |
| 20 | 2 P0、3 P1、1 P2 | 「封闭」的可运行集放行任何显式 `.exe`，而分类不到的命令只污染后继；解释器靠「跑起来」认证，而项目过滤器并不把 PATH 收窄到管理员；`UNKNOWN` 没有裁定；关闭序列取消的 token 没有任何东西送到 MCP future；`cmd` 之下的一级不可达 | D5、D4、D2、D6、§5 |
| 19 | 3 条（编辑性整理） | 同一条规则写在两处，副本被并排读时自相矛盾：§6 已废掉「允许判红的门槛」这个类别，D4 还留着这个说法；一条需要读三个来源的规则被写成只读两个文件；一张十行的表被引成「九步」。另外，本孪生件仍在叙述已被取代的旧版本，英文孪生件早已不叙述 | D4、D5、§6 |
| 18 | 4 P0、2 P1 | 守卫校验 `$PSHOME`，而散文要求会话配置名；跑在解释器内部的守卫无法认证这个解释器；静态路径不等于不可变字节；源码保真被写成字符集合，而它是台自动机 | D4、D5、§3.19、§7 |
| 17 | 3 P0、2 P1 | 散文与规范表两次不一致，且都错在实现者照抄的那一边；有一道降级步骤漏在我的两步之间 | D4、D5、§3.19 |
| 16 | 3 P0、2 P1 | 只借了 codex 九道降级闸里的两道；效果类别被做成互斥；预检靠启动解释器去得知会话配置 | §3.19、D4、D5 |
| 15 | 2 P0、2 P1 | 重绑规则只往回看，于是作为末条语句的「执行型」命令被放行 | D5 |
| 14 | 2 P0、3 P1 | 照搬节点 kind 清单，却没带上它旁边的 `#Requires` 检查；把 `PSModulePath` 变量当成了生效值 | §3.18、D4 |
| 13 | 2 P0、4 P1、1 P2 | 惰性量化在「命令」上，而这门语言不形成命令就能重绑 | §3.17、D5 |
| 12 | 3 P0、4 P1 | 重绑规则是一张闭表，底下压着一句 fail-open | D5、§3.15、§3.16 |
| 11 | 3 P0、3 P1 | 原子记录止住撕裂读却止不住丢失更新；registry 按白名单而非按 registry 重建；bash 带继承环境启动 | D2、D4、§3.14 |
| 10 | 3 P0、3 P1、1 P2 | 子代理会跑成另一套工具；共享引擎没有同步；地板的 PATH 不是子进程的 | D2、§2.15、§3.13 |
| 9 | 3 P0、2 P1、1 P2 | 点了 `_bind_and_register` 的名却没读它 | §2.14、D5 |
| 8 | 3 P0、2 P1、1 P2 | PR-0 从磁盘重建引擎，丢掉内存里的宿主政策 | §2.13、§3.12 |
| 7 | 5 P0、2 P1、1 P2 | 声称子代理路径无需改动；实测它根本没有引擎 | §2.12、§3.11 |
| 6 | 2 P0、4 P1 | 启动参数按前缀匹配；项目级 `permissions.json` 按设计被忽略 | §3.10、§2.10 |
| 5 | 5 P1 | 把 shell spec 做成构造参数，而构造顺序不允许 | §2.9 |
| 4 | 4 P1、2 P2 | 「构造期绑定」只是某一个构造函数的性质，不是契约 | §2.8 |
| 3 | 4 P1、3 P2 | 包装关上了，求值器敞着 | §3.7、D5 |
| 2 | 1 P0、3 P1、2 P2 | 把不透明路由到 ASK，而三条传输路径会自动批准 | §2.6 |
| 1 | — | 初版设计 | — |

---

## TL;DR

1. **PR-0 先做、独立做。** 子代理没有引擎（§2.12）。它们拿到父级的引擎、一个有效的 filesystem 与
   shell，以及一份按名称与来源从父级**活** registry 重建的 registry —— 绝不共享工具对象，绝不重新造出
   已禁用的（D2）。
2. **引擎一把写者锁、读者无锁**，且每次裁定都携带它据以作出的快照（D2）。
3. **地板与子进程在同一环境里解析名字**，且环境带进来的东西不能在 body 之前运行或重绑：过滤后的
   PATH —— **只留「子进程的主体写不了」的目录**，一个谓词同时服务解释器选择、5a 的映像那一半与子进程的
   `PATH`（D4）—— `PATHEXT=.COM;.EXE`、`-NoProfile -NonInteractive`、钉死的 `PSModulePath`，以及
   `bash --noprofile --norc -p` —— 拦住继承函数的是 `-p`，不是那两个长选项（§3.16）。**无法证明其
   解析惰性**的命令，让它之后的一切不透明（D4、D5）。
4. **一个工具，不是两个**（D2）。**DENY 是地板唯一的裁定**（§2.6）。**方言随调用传递**（§2.9）。
   **不透明是 token 与 AST 节点 kind 的属性，按方言分**（§3.17、D3、D5、D7）。**可运行集按方言封闭，
   由两个互相独立的条件封闭** —— *名字*在该方言可信表里有条目；*映像*落在一个「主体写不了」的根下，
   宿主 allowlist 可以在其中再钉一层，但永远不能顶替它 ——
   缺任一半的命令词**本身**就不透明，而不只是污染其后（D5）。对 bash，过滤后的 PATH 只是映像
   那一半。
5. **shell 配置是用户级或宿主的，永远不是工作区的**（§2.10、D6）。

---

## 1. 目标架构

| | 今天 | 目标 |
|---|---|---|
| 模型可见工具 | `run_shell_command` | `run_shell_command`，名字受守护 |
| Windows 上的方言 | 经 `%COMSPEC% /c` 的 `cmd.exe` | `pwsh` → `powershell.exe` → Git Bash（仅当 `shell.allow_git_bash` 开）→ `cmd` |
| 地板的门 | 工具名 | 工具名**加**随调用传入的方言 |
| 分析模式 | 对原始文本做正则 | **regex**（posix、cmd）或 **lowered**（powershell） |
| 可运行目标 | 任何东西 | 名字要有**可信表条目**，文件还要有**可信映像**：落在「子进程主体写不了」的根下的显式 `.exe`/`.com`（宿主 allowlist 可在其中再钉一层，但永不顶替）、已知 cmdlet/内部命令、经过滤 PATH 解析到的裸词（那也是子进程的 PATH）。其余一律不透明 —— 未分类的程序，或落在不可信映像上的可信 basename |
| 无法分析的输入 | 不匹配即放行 | `hardline:<dialect>-opaque` ⇒ **DENY** |
| 子代理 | 没有引擎；新建工具绑到 `None`；registry 来自定义 | 按身份持有父级引擎/fs/shell；registry 按名称 + 来源来自父级活 registry |
| 并发下的引擎 | 未同步的字段 | 写者锁、无锁快照读者、裁定携带其快照 |
| 子进程环境 | 继承 | 过滤后的 PATH（**只留「子进程主体写不了」的目录**）、`PATHEXT=.COM;.EXE`、移除 `BASH_ENV`/`ENV`/**`BASH_FUNC_*`**、cmd 上 `NoDefaultCurrentDirectoryInExePath=1` |
| cmd 启动 | `%COMSPEC% /c` | `Popen(string, executable=<cmd>)`，`"<cmd>" /d /e:on /v:off /s /c "<body>"` |

## 2. 现状——实测

### 2.1 Windows 今天跑的是 `cmd.exe`，而且如实说了

`agentao/capabilities/shell.py:58-59` —— *"Windows is untouched: ``shell=True`` there means
``%COMSPEC% /c``, and ``executable=`` would replace cmd.exe rather than select a dialect"*
（`agentao/capabilities/shell.py:55-56`）；`agentao/capabilities/shell.py:71-72`；
`agentao/tools/shell.py:156-160`；`agentao/capabilities/shell.py:141-143`；
`shutil.which("bash")` 在 `agentao/capabilities/shell.py:62`。

### 2.2 地板按工具**名**把门

`agentao/permissions_hardline/_scanner.py:155-156` —— *"the floor is about preventing
unrecoverable operations, and ``run_shell_command`` is the single surface that can express them"*
（`agentao/permissions_hardline/_scanner.py:129-131`）。`grep -rn '"run_shell_command"' agentao/ |
wc -l` → 32，横跨 13 个文件；其中四处决定行为：地板、
`agentao/runtime/tool_executor.py:390`、`agentao/plugins/hooks/_alias.py:16`、预设。

### 2.3 `plan` 模式按精确名拒绝，且没有兜底

`agentao/runtime/tool_planning.py:487-495`；`agentao/permissions.py:444-457`、
`agentao/permissions.py:458`、`agentao/permissions.py:459`。

### 2.4 Windows 上的地板已经是空转的

`agentao/permissions_hardline/_patterns.py:380`；四个 Windows token 零命中。见 §2.7。

### 2.5 `ci.yml` 里八个 job，零个 Windows

`.github/workflows/ci.yml` —— `schema-check`、`typing-gate`、`lint-gate`、`test`、`mcp-compat`、
`build`、`smoke`、`examples`，而 `grep -c 'runs-on' .github/workflows/ci.yml` → 8，全部是
`ubuntu-latest`。早前版本在这里引的是 `pyproject.toml:10-21`，那是 classifier 清单：一句真话，
却站在一条撑不住它的引文上。

### 2.6 DENY 不可被遮蔽；ASK 会被三种 transport 自动批准

*"so a ``full-access`` ``allow:*`` rule cannot silently shadow it"*
（`agentao/permissions.py:684-687`）；`agentao/permissions.py:688-694`；
`agentao/runtime/tool_planning.py:510-514`。

| Transport | 行为 | 位置 |
|---|---|---|
| `NullTransport` | `return True` | `agentao/transport/null.py:28` |
| `SdkTransport` | 无回调时 `return True` | `agentao/transport/sdk.py:101-103` |
| CLI | `full-access` / `allow_all_tools` 下 `return True` | `agentao/cli/transport.py:76-77` |

### 2.7 地板当前的实际覆盖 —— 含它的 fail-open

```
rm -rf /                           hardline:recursive delete of root / …
timeout 5 rm -rf /                 hardline:recursive delete of root / …
D=rm; $D -rf /etc                  None
X=/; rm -rf $X                     None
del /f /s /q C:\*                  None
rd /s /q C:\                       None
set D=del & call %D% /f /s /q C:\* None
```

### 2.8 子代理路径会在构造之后替换 `PermissionEngine`

`agentao/agents/tools/_wrapper.py:563-570`；*"losing them was a permission bypass"*
（`agentao/agents/tools/_wrapper.py:549-553`）。

### 2.9 两个 composition root 都在 agent 之前建引擎

`agentao/embedding/factory.py:186-192`、`agentao/embedding/factory.py:270`；
`agentao/acp/session_new.py:366-374`。150 处 `PermissionEngine(`。`_decide` 把工具作为第一参数收下
（`agentao/runtime/tool_planning.py:473-475`），再调 `decide_detail`
（`agentao/runtime/tool_planning.py:498`）。

### 2.10 项目级 `permissions.json` 被设计为忽略 —— 那就是信任边界

`agentao/embedding/permission_loader.py:131-136`；*"Project-scope ``.agentao/permissions.json`` is
intentionally NOT loaded: a checked-in rule could grant the agent capabilities the user never
approved"*（`agentao/permissions.py:483-485`）；*"Permissions are a user/host concern, not a cwd
concern — the same model OS permissions and IDE workspace-trust use"*
（`agentao/permissions.py:487-489`）。

### 2.11 shell 块没有穿过任一 composition root 的通路

`agentao/embedding/permission_loader.py:107-111`；`agentao/embedding/factory.py:186-192`；
`agentao/acp/session_new.py:366-374`、`agentao/acp/session_load.py:262-270`、
`agentao/acp/session_load.py:278-282`。

### 2.12 子代理没有权限引擎，本该给它引擎的那次赋值是死的 —— 实测

wrapper 用固定关键字列表构造（`agentao/agents/tools/_wrapper.py:513-535`），不传
`permission_engine=`、`filesystem=` 或 `shell=`；构造函数存下 `None`（`agentao/agent.py:112`、
`agentao/agent.py:295`）并传下去（`agentao/agent.py:648`）；runner 存下它
（`agentao/runtime/tool_runner.py:80`）并复制进 planner（`agentao/runtime/tool_runner.py:86`、
`agentao/runtime/tool_planning.py:307-308`），那正是 `_decide` 读的
（`agentao/runtime/tool_planning.py:498`）。`sub_agent.tools = scoped_registry`
（`agentao/agents/tools/_wrapper.py:538`）到不了 runner 捕获的 registry
（`agentao/runtime/tool_runner.py:79`）—— wrapper 自己的注释就这么说
（`agentao/agents/tools/_wrapper.py:522-526`）。引擎赋值（`agentao/agents/tools/_wrapper.py:570`）
只有一个读者（`agentao/tooling/agent_tools.py:98`）。

```
裸 Agentao(...)（= wrapper 的构造方式）              ASK    tool requires_confirmation fallback
… 然后 tool_runner._permission_engine = engine      ASK    tool requires_confirmation fallback
真实 PermissionEngine.decide_detail(...)             DENY   hardline:recursive delete of root / …
```

跑的是子代理自己新建的内置工具（`agentao/tooling/registry.py:95-100`），在
`agentao/tooling/registry.py:77-80` 绑到 `None`（`agentao/tools/base.py:43-48`、
`agentao/tools/base.py:50-55`）。`scoped_registry` 里父级的实例
（`agentao/agents/tools/_wrapper.py:463-465`）是模型*看到*的。

### 2.13 子代理本可继承什么，而从磁盘重建会扔掉什么

三个 getter（`agentao/tooling/agent_tools.py:97-99`），从没有引擎。磁盘上任何地方都不存在的引擎状态：

| 状态 | 由谁设置 | 位置 |
|---|---|---|
| `_enable_hardline` | `enable_hardline=` | `agentao/permissions.py:561` |
| `_run_scope_rules` | `add_run_rules` | `agentao/permissions.py:591`、`agentao/permissions.py:601` |
| `_injected_sources` | `add_loaded_source` | `agentao/permissions.py:640-650` |
| 规则列表 | 内存里的 `rules=` | `agentao/permissions.py:579` |

该类上没有 `snapshot` / `copy` / `fork`。

### 2.14 `_bind_and_register` 无条件覆盖三个槽位，而 `register` 什么都不绑

`agentao/tooling/registry.py:77-80`；*"inherit the exact same working-directory / filesystem /
shell binding as built-ins"*（`agentao/tooling/registry.py:72-75`）。

### 2.15 工具实例携带每个 agent 的运行期状态，而跨 agent 没有任何东西给它们串行化

执行器把 `output_callback` 重绑到自己的 transport（`agentao/runtime/tool_executor.py:405-410`），
所持的锁在文档里只串行化 *"concurrent calls to the same tool within this batch"*
（`agentao/runtime/tool_executor.py:200-201`）；`TodoWriteTool` 把列表放在实例上
（`agentao/tools/todo.py:16`、`agentao/tools/todo.py:62`）；`Tool` 没有复制 API。

### 2.16 registry 的范围不是定义的白名单，而公开参数表达不了它

把*定义*的白名单传给 `enabled_tools=` 来重建子代理的 registry，会留下三处错误。**其一**，
白名单不是父级当前的工具集：父级已禁用或移除的内置工具不在父级 registry 里、却在白名单里，子代理
会重新造出它。**其二**，`apply_enabled_tools` 无论如何都保留所有 extra 工具 —— *"``extra_tools`` are
always kept — the host injected those instances explicitly"*（`agentao/tooling/registry.py:207-209`）
—— 所以白名单外的 fork 宿主工具仍会暴露。**其三**，一个替换了内置名的宿主工具
（`agentao/tooling/registry.py:145-147`）若不可 fork，会让子代理在该名下拿到*原始*内置工具 ——
与宿主的选择相反。而 MCP 分支用公开参数根本造不出来：`enabled_tools` 经
`_reject_reserved_tool_name` 拒绝 `mcp_*`（`agentao/agent.py:489`），`remove_tool` 拒绝同样的名字
（`agentao/agent.py:953`）。registry 必须按来源、从父级*拥有*的东西、经一条不过那些守卫的内部路径
重建。

### 2.17 agent 工具在构造时注册，所以事后 `agent_manager = None` 什么都移不掉

`AgentManager(...)` 的创建与 `_register_agent_tools()` 都在 `__init__` 里
（`agentao/agent.py:625-629`）；只有内置 agent 是 opt-in（`agentao/agent.py:151`）—— 项目与插件
agent 无条件发现。wrapper 的 `sub_agent.agent_manager = None  # prevent recursive spawning`
（`agentao/agents/tools/_wrapper.py:541`）在会发起 spawn 的 wrapper 已带着捕获的回调进入 registry
（`agentao/tooling/agent_tools.py:88-102`）之后才把属性置空。按计划自己的判据 —— 只有读者在使用时读
属性才安全 —— 这次赋值不安全。

### 2.18 MCP manager 用任何调用它的线程驱动同一个 loop

`McpClientManager` 持有单一 loop（`agentao/mcp/client.py:982-992`），以
`loop.run_until_complete(coro)` 桥接（`agentao/mcp/client.py:999`）；
`grep -n "Lock\|run_coroutine_threadsafe\|call_soon_threadsafe" agentao/mcp/client.py` 一无所获。
共享 manager 的父级与后台子代理会各自从自己的线程对同一个 loop 调 `run_until_complete`。给这个调用
加锁是选错了器械（D2）。并且 `close()` 会对该 agent 持有的 manager 调
`disconnect_all()`（`agentao/agent.py:1015-1017`），共享同一个 manager 的子代理一关，父级的连接就断。

### 2.19 registry 不记来源，六个内置工具由 agent 构造

`ToolRegistry.__init__` 就是 `self.tools = {}`（`agentao/tools/base.py:207`），`register` 只收
`replace`（`agentao/tools/base.py:209`）：名字映射到实例，来源无处记录，事后无法区分「内置」与「替换了
内置的宿主工具」。「同类的新实例」也不是一份构造配方。`register_builtin_tools`
（`agentao/tooling/registry.py:83`）的依赖来自 agent —— agent 自己的 `memory_tool`
（`agentao/tooling/registry.py:117`）、`ActivateSkillTool(agent.skill_manager)`
（`agentao/tooling/registry.py:118`）、闭包住 transport 的 `AskUserTool`
（`agentao/tooling/registry.py:119`）、agent 的 `todo_tool`（`agentao/tooling/registry.py:120`），以及
两个绑 `bg_store` 的工具（`agentao/tooling/registry.py:126`）—— 而 web 工具只在有 `bs4` 时才存在
（`agentao/tooling/registry.py:109`）。六个内置工具无法只凭类重建，而用*父级*的依赖重建，等于把父级的
transport 和父级的 todo 列表交给子代理 —— 正是 §2.15 说不能共享的那些状态。

## 3. 已核验的前提

### 3.1 codex 的门是方言，agentao 的门是工具名

`codex-rs/shell-command/src/shell_detect.rs:6-13`；`codex-rs/core/src/shell.rs:32-40`；
`codex-rs/core/src/exec_policy/executable_identity.rs:35-37`。

### 3.2 codex 的真 PowerShell 解析器是测试 oracle，不进生产

`codex-rs/shell-command/src/command_safety/mod.rs:1-2`；
`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:6-8`、
`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:10-12`；
`codex-rs/shell-command/src/command_safety/is_dangerous_command.rs:45-50`。

### 3.3 pi-mono 没有地板可破坏

`packages/coding-agent/src/utils/shell.ts:125-133`、`packages/coding-agent/src/utils/shell.ts:122`、
`packages/coding-agent/src/core/tools/powershell.ts:16`；codex `codex-rs/core/src/shell.rs:32-40`；
先查已知位置（`packages/coding-agent/src/utils/shell.ts:76-92`）。

### 3.4 该语法在 Python 侧就是 codex 的那个锁定版本——实测

`codex-rs/Cargo.toml:485`；`pyproject.toml:6`。

```
Remove-Item -Recurse -Force C:\               无错误     command_name, command_elements
echo 'Remove-Item -Force is dangerous'        无错误     command_name(echo), command_elements
Get-ChildItem C:\tmp | Remove-Item -Force     无错误     pipeline_chain 下有两个 command 节点
& (gcm ('Remove' + '-Item')) -Force C:\       无错误     command_invokation_operator, command_name_expr
```

### 3.5 地板今天覆盖什么

18 个类；`agentao/permissions_hardline/_patterns.py:35-37`。

### 3.6 codex 的 Windows 表实际覆盖什么

| 类别 | 方言 | 位置 |
|---|---|---|
| 带 URL 的启动，**且只在某个参数能解析成 http(s) URL 时** | 混合 | `codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:47-53` |
| 强制删除 cmdlet | **PowerShell** | `codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:226` |
| `del` / `erase` 带 force；`rd` / `rmdir` 递归+静默 | **CMD** | `codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:143-152` |

### 3.7 蓝本没堵上的两个求值洞 —— 实测

`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:143-150`、
`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:322-324`。

### 3.8 `command_name_expr` 是四种不同的东西 —— 实测

`& 'name'` / `& { }` / `. .\x.ps1` / `Import-Module`。

### 3.9 裸路径是普通 `command_name`；动态参数看起来与字面参数一样

`.\setup.ps1`、`C:\tools\x.cmd`、`Remove-Item $flags C:\`、`Get-ChildItem $dir`。

### 3.10 PowerShell 的启动器按前缀匹配参数 —— 从上游源码实测

`PowerShell/PowerShell`，commit `2ca393d6b82f5c270440604d205fc37adbdf674a`（最后一次触及该文件的
提交，2026-08-10T17:29:03Z；抓取时刻 2026-09-02T16:40Z 的 `master` 为
`d0f43b00343b04a699d81c325a63a88ab83fec53`），blob
`src/Microsoft.PowerShell.ConsoleHost/host/msh/CommandLineParameterParser.cs`，sha256
`727de30f58506d55cb7e363f0f5dbb777bee48c545258255b5ce69c5185e209b` —— 按该 commit 重抓，逐字节一致。

```
798| private static bool MatchSwitch(string switchKey, string match, string smallestUnambiguousMatch)
805|     return (switchKey.Length >= smallestUnambiguousMatch.Length
806|             && match.StartsWith(switchKey, StringComparison.OrdinalIgnoreCase));
1090| … "commandwithargs" … || … "cwa" …
1103| … "command", "c"
1141| … "file", "f"
1182| … "encodedcommand", "e" || … "ec", "e"
```

codex：`codex-rs/shell-command/src/powershell.rs:9`、`codex-rs/shell-command/src/powershell.rs:60-62`。

### 3.11 3.10 与 3.11 的 `shutil.which` 在 Windows 上先搜当前目录 —— 实测

```
3.11    if sys.platform == "win32":  …  path.insert(0, curdir)                          —— 无条件
3.12    if sys.platform == "win32" and _win_path_needs_curdir(cmd, mode): path.insert(0, curdir)
```

### 3.12 Python 把 cmd 的 argv 重新序列化成一个 cmd 不按同样方式解析的字符串 —— 实测

```
list2cmdline(['cmd','/d','/e:on','/v:off','/c', 'echo "a b" & del /f /s /q C:\*'])
   → cmd /d /e:on /v:off /c "echo \"a b\" & del /f /s /q C:\*"
```

### 3.13 每个方言去哪里找裸词 —— 以及地板的环境不是子进程的环境

cmd：当前目录优先，然后按 `PATHEXT` 搜 PATH。PowerShell：alias、function、cmdlet —— 最后这一类模块
自动加载能从 `PSModulePath` 供出来 —— 之后按 `PATHEXT` 搜 PATH。**bash：alias、关键字、function、
内建命令和命令哈希统统在搜 `$PATH` 之前就解析完了**（§3.15）；轮到 PATH 那一步才是精确文件名匹配、
跑任何可执行文件含脚本，`PATHEXT` 不起作用。**在 Windows POSIX 层上这个文件名匹配并不平凡** ——
MSYS2 把裸 `git` 解析成 `git.exe` —— 本计划不写死无扩展名 `git` 与同目录 `git.exe` 之间的优先级，
门槛 20 在该级上线前实测它。cmd `start` 按关联启动。`PATH=<project>;<trusted>` 时，地板的过滤搜索放行了 `git`，子进程跑的是项目里的 `git.cmd`。

### 3.14 非交互 `bash -c` 在 body 之前运行 `$BASH_ENV` —— 实测

```
$ printf 'echo "[BASH_ENV file ran first]"\n' > payload.sh
$ BASH_ENV=./payload.sh bash -c 'echo "[body ran]"'
[BASH_ENV file ran first]
[body ran]
$ env -u BASH_ENV bash -c 'echo "[body ran]"'
[body ran]
```

（本机 `bash 3.2.57`；这是 bash 对非交互 shell 的文档化启动规则。）所以 Git Bash 那一级若以
`"<path>" -c <body>` 带继承环境启动，一个工作树里的 `BASH_ENV` 就会在地板扫过的 body 之前运行。
`sh` 在 `ENV` 下有同样的钩子。

### 3.15 规则 6 的表没点到名的三种 bash 重绑 —— 实测

```
$ bash --noprofile --norc -c 'export PATH=/usr/bin:/bin; printf -v PATH "/private/tmp"; \
    echo "PATH now: $PATH"; env | grep "^PATH="'
PATH now: /private/tmp
bash: env: command not found
$ bash --noprofile --norc -c 'export PATH=/usr/bin:/bin; read PATH <<< "/private/tmp"; \
    echo "PATH now: $PATH"; env | grep "^PATH="'
PATH now: /private/tmp
bash: env: command not found
$ bash --noprofile --norc -c 'export PATH=/usr/bin:/bin; hash -p ./evil/notgit git; git --version'
[EVIL git ran]
```

（本机 `bash 3.2.57`。）`printf -v` 与 `read` 不用赋值语法就写了 `PATH`；由于 `PATH` 早已 export，
子进程搜索也随之改变 —— 那两行 `command not found` 就是证据。`hash -p` 只重绑一个命令名，
**完全不碰 `PATH`**，任何以目标变量为键的规则都抓不到它。三者都是 shell 内建命令，bash 根本还没搜到
`PATH` 就解析掉了。

### 3.16 继承来的函数能穿过 `--noprofile --norc`；拦住它的是 `-p` —— 实测

```
$ env 'BASH_FUNC_git%%=() { echo "[EVIL function git ran]"; }' \
      bash --noprofile --norc -c 'type git; git --version'
git is a function
[EVIL function git ran]
$ env 'BASH_FUNC_git%%=() { echo "[EVIL function git ran]"; }' \
      bash --noprofile --norc -p -c 'type git'
git is /usr/bin/git
$ env SHELLOPTS=xtrace bash --noprofile --norc -c 'echo body'
+ echo body
$ env SHELLOPTS=xtrace bash --noprofile --norc -p -c 'echo body'
body
$ env BASH_ENV=./payload.sh bash -p -c 'echo "[body]"'
[body]
```

（本机 `bash 3.2.57`。）只有 `--noprofile --norc` 时，一个被信任的裸 `git` 会解析成环境带进来的
函数。`-p` 是解释器**对它所启动的那个进程**给出的封闭答案：它挡掉继承函数与 `SHELLOPTS`，并且在那里
不靠清除列表就覆盖 `BASH_ENV` 与 `ENV`。**但它对这个进程的子进程什么也没说。** 一个后代 bash ——
可信 `git` 别名里的 `/bin/sh -c`、一个 npm script、一条 `make` 规则、一个 git hook —— 是一个不带 `-p`
新起的进程，它会从继承来的环境里导入 `BASH_FUNC_git%%`。所以既传标志、也清环境（D4）：标志护一个进程，
环境贯穿整棵树。**标志顺序有讲究** —— `bash -p --noprofile …` 会报
`bash: --: invalid option`，长选项必须在前。

### 3.17 codex 按 AST 节点 kind 裁定，并拒绝任何它没审过的 kind

`first_unrecognized_named_kind` 遍历每个具名节点，返回第一个不在接受清单里的 kind
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:132-133`）；该清单是二十来个
kind —— 管道、命令、命令元素、字面量、注释 —— 除此之外什么都没有
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:143-169`）。
`assignment_expression`、`variable`、成员调用、嵌套 scriptblock 统统不在其中，于是含有其一的脚本在
任何命令级分析开始之前就被拒。它上面那行注释说这条拒绝是 *"until its lowering semantics are
reviewed"*（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:128`）。把惰性规则
写成关于一条*命令*的断言，严格弱于这一条：`$Function:git = { … }` 不形成命令词，也不传任何参数。

### 3.18 codex 在 kind 遍历之前，用一道内容检查拒掉 `#Requires`

`has_requires_directive` **先跑**，它的失败信息是
*"requires directives can execute before command lowering"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:47-48`），之后才轮到
`first_unrecognized_named_kind`。理由写在该函数自己的注释里：*"Tree-sitter exposes #requires as a
comment, but PowerShell evaluates it before the script body and can load modules or assemblies"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:104-105`），匹配方式是把注释
文本转小写后测 `starts_with("#requires")`
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:113-117`）。`comment` 在接受
清单上（§3.17），所以只有 kind 闸门时 `#Requires -Modules Evil` 会通过 —— 该指令在地板扫过的第一条
命令运行之前就把模块导进来了。

### 3.19 codex 的降级是按序的九道闸，而它的 fixture 文件就是那份枚举

`lower_with_tree_sitter` 按这个顺序拒绝：Unicode 语法别名 —— 弯引号、短破折号与长破折号 ——
报 *"PowerShell Unicode syntax alias"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:25`）；然后用**一个字节**的
替换来遮蔽 `--flag=value`，因为 *"the one-byte replacement keeps CST ranges valid"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:33`），而后面有一道闸要拿
字节区间和原始源码比对；然后 *"tree contains ERROR or missing nodes"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:45`）；然后 `#Requires`
（§3.18）；然后未识别的节点 kind（§3.17）；然后空命令列表；然后**逐个 command 节点、在保真检查之前**
跑 `lower_command_text`（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:66`）；
然后源码保真检查，失败信息是 *"source outside literal command nodes"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:69`）；然后 *"using
declarations require the PowerShell AST oracle"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:76`）；然后 *"empty lowered
command or word"*（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:82`）。

**`lower_command_text` 做的是 argv 降级，不是分类**，它自己的注释说解码只针对
*"only for forms whose runtime value is statically known"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:310-311`）。它解析单引号、
双引号与反引号，并拒绝 *"adjacent/concatenated command elements"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:334`）、空词，以及经
`reject_unsupported_bare_word`
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:462`）拒绝的
*"attached PowerShell parameter value"* 与 *"non-canonical numeric-leading bare word"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:466`、
`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:474`）—— 因为那些需要
PowerShell 自己的取值转换。fixture 里有好几行就败在**这里**，别处都不败。

**而保真检查并不是「每个字节都在 command 节点内」。** 它的注释是 *"Command nodes alone are not
enough: reject any source outside the literal commands and separators/comments we explicitly
understand"*（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:208-209`），而且它是台
**有状态的走查，不是字符过滤器**：它带着 `can_chain`、`needs_command` 与 `paren_depth`
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:212-214`），且只在
`range_index == command_ranges.len() && !needs_command && paren_depth == 0` 时返回真
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:306`）。所以它放行的那些分隔符
—— 换行、空白、`;`、管道、链接运算符、圆括号与注释 —— 都是**按位置**放行的：右括号必须配上它开过的
左括号，链接运算符前面要有命令、后面还欠一条，走完时不能有欠账（D5 第 8 步）。
`source_is_covered_by_commands`
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:207`）走的是原始字节，遇到
`#` 时它记下 *"`#` starts a comment only at a token boundary"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:274`），并在 tree-sitter 把
一个 `#` 从裸 token 里切出来时拒绝 —— 否则 `git status --short#; Remove-Item victim` 会降级成孤零零
一条 `git status --short`，该行其余部分变成一个被接受的 `comment`，而 PowerShell 会照跑分号后面那条
`Remove-Item`。

对抗性输入早就写好了。`powershell_lowering.json` 有 68 例，**其中 44 例必须降级为空** ——
`Remove-Item test –Force`
（`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:43`）、上面那条内嵌
井号（`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:46`）、弯引号
定界符（`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:47`）、停止
解析记号 `--%`
（`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:50`），另有
here-string、`param`／`begin`／`end`／`trap` 块、重定向、调用运算符、子表达式与数组表达式。

### 3.20 AllUsers 配置在 `$PSHOME` 下，而 `$PSHOME` 是那个程序集所在的目录 —— 抓自上游文档

`MicrosoftDocs/PowerShell-Docs`，commit `49b1bd052bfacc7e6c7651ef9396be8933de28ce`（最后一次触及该
文件的提交，2026-08-17T20:55:27Z；抓取时刻 2026-09-03T15:48Z 的 `main` 为
`32b128ab35e7c5321579cf844d9e1f9aa5a03c39`），blob
`reference/7.5/Microsoft.PowerShell.Core/About/about_PowerShell_Config.md`，sha256
`264a66318761cf5767cd3d86efeefc12dd3ef2eb64e7d9a578deb198fbacdb9f` —— 按该 commit 重抓，逐字节一致。

```
63| - Settings managed by Windows Group Policy take precedence over settings in the
64|   configuration files.
74| A `powershell.config.json` file in the `$PSHOME` directory defines the
75| configuration for all PowerShell sessions running from that PowerShell
76| installation.
79| > The `$PSHOME` location is defined as the same directory as the executing
80| > System.Management.Automation.dll assembly. This applies to hosted PowerShell
81| > SDK instances as well.
```

所以 AllUsers 那份文件并不在「解释器旁边」：它在该进程所加载的那个程序集的安装根里 —— 当 launcher 是
shim、符号链接或一份拷贝时，那是另一个目录 —— 而且正是这同一个目录，让能写它的人在不碰预检哈希过的
launcher 的前提下改变「这个解释器是什么」（D4）。Group Policy 优先于这两个文件，是上游自己的陈述，
不是从两个文件作用域推出来的。

## 4. 决策

### D1 —— 一个工具、一个名字；无标注的命令规则是 `unspecified`；替换时重跑 D1

`run_shell_command` 保留原名（§2.2）。`args` 条件是对原始文本的正则
（`agentao/permissions.py:747-750`）；规则增加可选 `dialect` 字段 —— `"posix"`、`"cmd"`、
`"powershell"`、`"*"` —— 扩展 `_LEGAL_RULE_FIELDS`（`agentao/permissions.py:76`）。带 `args.command`
条件而无标注的 shell 工具规则是 `unspecified`，在 POSIX 与 cmd 上不变
（`agentao/capabilities/shell.py:58-59`）；PowerShell 遇 `unspecified` 规则时构造失败并逐条点名与列出
全部四个标签。在 `agentao/agent.py:418` 对 `add_tool(replace=True)`（`agentao/agent.py:906`）重跑。

### D2 —— 随调用的方言；受守护的名字；由内部工厂按父级活状态构建的子代理；一把写者锁的引擎

**方言。** `ShellExecutor` 声明它；`ShellTool` 从 `_get_shell()` 暴露 `shell_spec`
（`agentao/tools/base.py:50-55`）；`_decide` 传给 `decide_detail`，后者转给 `hardline_check`。
`PermissionEngine(` 150 处全部不动。

**名字。** 该名字下的任何工具都须实现 `ShellSpecProvider`（`agentao/tooling/registry.py:145-147`、
`agentao/agent.py:906`、`agentao/agent.py:418`）；按 `_PLAN_ONLY_TOOLS` 模式保留
（`agentao/agent.py:390-392`、`agentao/agent.py:411-416`）仍是备选（§9 q6）。

**`ShellDialect`：** `POSIX`、`POWERSHELL`、`CMD`、`UNKNOWN`（`agentao/tools/shell.py:248-252`、
`agentao/capabilities/shell.py:107-123`、`tests/test_shell_capability_swap.py:20-30`）。
**`UNKNOWN` 有裁定，而这个裁定就是它语义的全部：地板在匹配任何一条规则之前返回
`hardline:unknown-dialect-opaque`，即 DENY。** 地板不认识的任何取值同理。宿主自己的
`ShellExecutor` 恰恰就是「没有标注的方言」进来的地方，而实现者拿到它会做的两件事正是这条规则禁止
的：回退到 POSIX 扫描器，等于用 POSIX 模式扫了 cmd 或 PowerShell 再报一个干净的地板；跳过扫描，就是
「不匹配即放行」（§1）。门槛 1 覆盖「自定义 executor 报 `UNKNOWN`」与「报枚举之外的取值」两种。

**rung 是第二个字段，因为方言承载不了它。** `ShellDialect` 只有四个取值，而 Git Bash 那一级报的就是
`POSIX`，于是任何按方言取键的东西，都没法把一份地板给 Windows 这一级、另一份给 Linux 主机本来就有的
那个 shell。所以 spec 在方言旁边、也在 D4 放的那个预检答案旁边，再带一个 **`rung`** ——
`pwsh | powershell | cmd | git_bash | system_posix`。**方言选的是分析方式；rung 决定本计划的封闭集
政策是否生效。** `permissions.json` 里的规则标注仍是那四个方言取值（D1）—— 用户写 `dialect: "posix"`
就是两者都要，而一条权限规则本就该是这个意思。`system_posix` 是每一台现有 POSIX 主机报的那个值，它的
政策**默认关闭**，直到 §9 q4 另行决定 —— 于是 PR-2 可以把每个原语都发出去，而不动任何一条 Linux 上的
裁定。门槛 7 断言这一对：同一段 body，在 `git_bash` 下不透明，在 `system_posix` 下不变。

**合法配对是枚举出来的，而未知取值不是一个「政策关闭」的默认值。**

| 方言 | 合法的 rung |
|---|---|
| `POWERSHELL` | `pwsh`、`powershell` |
| `CMD` | `cmd` |
| `POSIX` | `git_bash`、`system_posix` |

其余一律拒绝 —— 不认识的 rung，或配在错误方言下的合法 rung（例如 `POWERSHELL × system_posix`）。
**这次拒绝发生在哪里是要紧的，因为 spec 是执行器*声明*出来的：** 宿主 `ShellExecutor` 想报什么配对都
能报，所以这张矩阵在 spec 构造时校验 —— 构造失败并点名那个配对，就像 D1 对无标注规则那样 —— **并且**
地板对漏到它面前的任何东西保留一个 fail-closed 裁定：在任何规则匹配之前返回
`hardline:unknown-rung-opaque`，与方言的 `UNKNOWN` 完全一致。这条规则禁止的正是实现者会顺手做的那件
事：把「不认识」路由到 `system_posix` —— 而那是唯一一个政策**关闭**的取值，于是未知的那种情形会整个
绕过封闭集。门槛 1 与门槛 7。

**spec 还要说明地板与子进程是不是共用一个文件系统。** `ShellExecutor` 是可由宿主注入的（§2.9、D6），
所以宿主可以把命令跑在容器里、跑在 SSH 那头、或跑在另一台机器上，而这样的执行器完全可以如实报出
`POWERSHELL × pwsh`，与此同时地板做的每一次映像检查 —— 某个目录的 ACL、内容哈希、签名、乃至某个 PATH
条目存不存在 —— 读的都是**地板**的文件系统，不是命令将要运行的那一个。所以 spec 带一个
`filesystem_is_local`，**字段缺席即为 `false`**，而「本机」只有一个意思：子进程打开的那条路径，就是
地板 stat 过的那条路径。按这个判据，同一台宿主上的容器不算本机，chroot 与 mount namespace 也不算。

**非本机执行器欠下的是三段义务，不是三个答案。** *解析*：oracle 里的每一问（D4）都要针对目标作答，
包括 5e/5g/5h 的裸词搜索 —— 因为那次搜索就是命令将要运行的那台机器上的一次文件系统操作。*证明*：这些
答案要绑定目标的主体、目标的环境，以及子进程实际会打开的那个映像，而不是地板磁盘上的同名者。*启动*：
前奏、`-NoProfile -NonInteractive`、`-p`、过滤后的 `PATH`、`/d /e:on /v:off /s` —— 每一样都是**agentao
写出来的那条命令行**的性质，而今天 `ShellRequest` 只带 `command: str` 与 `env`，别的什么都没有
（`agentao/capabilities/shell.py:77-84`），D6 又让 `shell=` 执行器提供整份 spec。中间没有任何一句话
要求执行器按 D4 钉的方式去启动。**本方案在两条出路里取第一条：** agentao 构造 argv 与环境，请求携带
它们，执行器原样运行 —— PR-1 本就是一次协议变更，而这一版让那些保证随请求一起走，而不是让每个宿主各
实现一遍。**这个请求必须能说出本计划已经
钉住的两种启动形态，而单一的 `argv` 说不出来：** 下面 `cmd` 那一格是**单个字符串**加 `executable=`，
因为 `/s` 会剥掉外层引号、而给 body 重新加引号会改变它（§3.12）；pwsh 与 Git Bash 两格则是 argv。所以
请求携带一个可判别的启动体 —— POSIX 上 `argv: list`，Windows 上 `application_name` 加 `command_line`
—— 连同环境、**子进程必须以之运行的主体**，以及**证明步骤解析出的规范映像**，好让执行器没法一边照办
命令行、一边悄悄启动别的东西。门槛 24 对着一个 fake executor 逐字段断言，而不是只断言「解析发生过」。

另一条 —— 把「解析—证明—启动」写进 `ShellExecutor` 契约并配一道合规门槛 —— 记录在案但不
采用：它把义务摊给了每一个将来会发执行器的宿主。两条都不成立时，每一个需要映像的命令词都不透明。这不
是对远程执行的评价 —— 而是「在错的文件系统上做的检查不是检查」（§10 规则 6）。

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

### D3 —— 不透明是 token 的属性，而 token 规则按方言分

`Token = Literal(text) | Dynamic(kind)`；codex 的 `Option<Vec<Vec<String>>>`
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:13`）承载不了它。

| 方言 | 展开语义 | 何时不透明 |
|---|---|---|
| PowerShell | 展开后的参数是一个参数 | 命令词 `Dynamic`，或命令词在表内且谓词读取位置 `Dynamic` |
| POSIX / bash | 展开后的词按 IFS 拆分，不重新解析为运算符（`eval` 除外） | 与 PowerShell 同 —— §9 q4 |
| CMD | `%VAR%`、`%1`…`%9`、`%*` 读行时；`%A` 按 FOR 迭代；`!VAR!` 在 `/v:on` 下执行时 | **任何**位置的**任何** `Dynamic`，**以及**任何控制结构或分组（D7） |
| `UNKNOWN`，或枚举之外的取值 | 不做分析 | **总是，且在任何规则匹配之前** —— `hardline:unknown-dialect-opaque`（D2） |

### D4 —— 只在 D3 之后、只在解析器在场时、只从可信位置翻转；子进程在地板的环境里解析名字；profile 不能运行，另有两处竞态只收窄不关闭

**顺序：** `pwsh` → `powershell.exe` → **Git Bash（仅当 `shell.allow_git_bash` 打开）** →
`cmd`。解析器缺失使 PowerShell 不可选。**带开关的那一级排在 `cmd` *之上*，因为排在它之下就不可达：**
每一个受支持的 Windows 上都有 `cmd.exe`，所以阶梯走到 `cmd` 就停在那里，永不继续往下落。守在 `cmd`
之下的开关是死代码 —— 它可以为 `true`、那一级也确实装着，自动解析依然永远选不到它 —— 而门槛 11 会
通过，门槛 20 测的是生产环境走不到的路径。所以 `allow_git_bash` 买到的是末级的一次**替换**，不是
排在它之后的一次追加；开关打开而找不到 Git Bash 时，`cmd` 仍是回退（D6）。

**每个解释器去哪里找，以及一个位置可以确立什么。** 两档，而且并不对称。**(a) 自动：** 已知绝对安装
位置（`packages/coding-agent/src/utils/shell.ts:76-92`、
`codex-rs/shell-command/src/shell_detect.rs:257-262`、
`codex-rs/core/src/exec_policy/executable_identity.rs:62-72`），且该目录**子进程自己的主体写不了**
—— 就是那一个谓词，问的是子进程将要以之运行的那个 token，而不是笼统的「管理员」（见下）—— 且它的
映像在**任何东西被启动之前**通过一次**宿主侧**身份检查：宿主信任的代码签名，或宿主
配置的「绝对路径 + 内容哈希」allowlist 里的一条。**(b) 显式：** 用户的 `shell.path`，绝对路径且在项目
根之外。那是一次*信任授权*，并且明写为授权 —— 用户点名了这个文件，所以它不要求签名，而除它以外的
任何东西都要求。

**过滤后的 PATH 命中不再是候选，而这道过滤本身也比原来严。** rev 20 剔除空的、相对的、工作目录与
项目根内的条目（由 agentao 自己的代码搜索，绝不用 `shutil.which` —— §3.11），并把绝对路径的结果读作
可选。那道过滤收窄掉的是*项目*、留下的是*用户*：任何一个恰好在 PATH 上的用户可写目录里丢一个
`pwsh.exe`，照样解析出一条绝对路径，而设计接下来做的第一件事就是**把它启动起来**、问它是什么。一个先
跑起来的二进制什么都能回答 —— edition、version、`$PSHOME`、自动加载偏好全是自报 —— 于是阶梯收集到的
每一个字段都出自受怀疑的那个程序，而地板还没有意见时它就已经执行过了。**一个程序不能靠「把它跑起来」
来认证：跑起来这件事，正是这道检查要门住的那个事件。** 所以 PATH 命中不是选择候选，门槛 23 断言这两
档，其中包括「植入的那个二进制从没被*启动*过」。
**而且这道过滤只留「子进程的主体写不了」的目录 —— 一个谓词，三个消费者。** 这里的解释器选择、5a 的
*映像*那一半、以及交给子进程的 `PATH`，问的是同一个问题三遍：一个「可信到能从里面解析出一个名字」的
目录，恰恰就是一个「可信到能从里面跑起一个名字」的目录。**这个谓词是*主体写不了*，而「仅管理员可写」
是它写错了的拼法。** 两者只在 agentao 以非特权身份运行时重合，而 rev 22 写下的正是这次重合、不是这条
规则：从提权终端启动的 agentao，或容器里以 `root` 运行的 agentao，**自己就是管理员**，于是
`C:\Program Files` 与 `/usr/bin` 对执行主体可写，而
`Copy-Item .\evil.exe 'C:\Program Files\Git\cmd\git.exe'; git status` 正是 5a 用来否掉 allowlist 的
那条无竞态序列。所以 oracle 要答的问题是**「子进程将要以之运行的那个 token，能不能修改、删除或替换
这条路径、或它所在的目录？」** —— 而当每一个候选根的答案都是「能」时，可信集为**空**：拒绝该级，而
不是把一个执行主体可以随手改写的集合交出去。提权态是这道地板守不住的一种姿态，把它说出来就是裁定；
门槛 25 从两个方向断言它。**而一个被拒绝的级现在可能把阶梯走空，这需要它自己的答案：** 每一级都被
拒绝时，就没有 `cmd` 兜底可言，于是 shell 工具对每一次调用返回 `hardline:no-trusted-rung-opaque` ——
仍然注册着、仍然拒绝、并说出理由 —— 而不是把自己注销掉（那会藏起理由），也不是退回今天的
`%COMSPEC% /c` 加惰性地板（那是实现者最顺手、也最弱的一种，§2.4）。门槛 25 断言选的是三者中的哪一种。
rev 20 用两种不同的强度回答了它 —— D4 为解释器否掉了
用户可写的 PATH 条目，5a 却把这样的条目当作可信根 —— 于是 rev 20 刚关上的洞又被打开：一个植入在那里
的 `git.exe`，名字那一半靠 basename 过、映像那一半靠位置过，而子进程的 `PATH` 随后就解析到它。所以
用户可写的条目是被**剔掉**，不只是不许用于选择：把它留在子进程的 `PATH` 里，等于让子进程去解析地板
刚拒绝的东西。在 POSIX 主机上，等价的谓词是 root 所有、且既非组可写也非全局可写 —— 这一条与那个决定
的其余部分一起归 §9 q4。

**映像那几个问题都走一个宿主侧的身份 oracle，而它同时也是测试需要的那道接缝。** **子进程将要以之运行
的那个主体**能不能修改、删除或替换某条路径或它所在目录、某条路径在命令将要运行的那台机器上究竟解不
解析得到、某个映像带不带宿主信任的签名、某个映像的内容哈希是什么 —— 这**四**问由地板去问一个 oracle，
而不是就地作答：在 Windows 上它对着子进程的 token 读 ACL、并读 Authenticode，对非本机执行器它是执行器
自己的（D2），在测试里它是注入进来的。第四问不是装饰 —— 5e、5g、5h 解析裸词靠的就是*搜索过滤后的
PATH*，那是目标机上的一次文件系统操作，不是地板手里的一个事实。门槛 2 要求每个方言的地板测试都在
ubuntu 上跑，而门槛 4 的**正例** —— 一个落在「主体写不了」的根下、且在可信表里有条目的 `git.exe` 判
放行 —— 在那上面根本造不出来；有了 oracle，这一格在 ubuntu 上是桩、在 Windows job 上是真的
（门槛 23），这也是让两道门槛同时成立的唯一安排。

**子进程的环境就是地板的环境，环境带进来的东西不得在 body 之前运行或重绑。** 每一级的子进程携带
`PATH=<过滤后>` —— 与选择用的是同一个谓词，于是子进程解析不到地板拒绝过的名字 —— 与
`PATHEXT=.COM;.EXE`；cmd 那一级另加 `NoDefaultCurrentDirectoryInExePath=1`； PowerShell 那一级**关掉
模块自动加载**，不去试图钉住那个本可被加载的模块集合；bash 那一级传 `-p`，那是解释器自己给出的封闭答
案，管住继承函数、`BASH_ENV`、`ENV` 与 `SHELLOPTS`，**但只管它启动的那一个进程**（§3.16）。**那是一
个进程，而环境贯穿整棵树** —— 所以 `BASH_ENV`、`ENV` **以及每一个 `BASH_FUNC_*` 条目**同样从子进程环
境里移除（§3.14、§3.16）。实测到的那一格并不是模型写出一次嵌套启动（那已被规则 2 拒掉）：一个可信的
`git` 经 `/bin/sh -c` 跑别名，那个 `sh` 就是 bash，bash 从它继承来的环境里导入 `BASH_FUNC_git%%`，于
是那个函数运行了 —— 在一条地板放行过的命令内部、往下两层进程。npm scripts、`make`、git hooks 都是同
一个形状。`-p` 护的是 agentao 启动的那个 shell，护它的子孙只能靠环境，这就是清除列表回来的原因，也是
它是一次*移除*而不是一个标志的原因。bash 那一级的 `PATHEXT` 为统一起见照设，但 bash 忽略它 —— 见规则
5h。
**PowerShell 那一级绑定实测的解释器身份。** `pwsh` 与 `powershell.exe` 是两个不同的程序 —— codex 自
己的注释就把它们分开，*"pwsh.exe is the cross-platform PowerShell Core (v6+) executable"* 对
*"powershell.exe is the Windows PowerShell (v5.1 and earlier) executable"*
（`codex-rs/shell-command/src/powershell.rs:98-101`）—— 两者的别名集不同，所以 5g 的表不可能是一张未
版本化的清单。codex 不需要这样一张表：它的 Windows 政策问的是某条命令危不危险
（`codex-rs/shell-command/src/command_safety/is_dangerous_command.rs:45-50`），不是它可不可信，一个
它没听说过的别名对它没有代价。封闭可信集正相反：它没听说过的别名恰恰就是它会判错的东西。翻转时刻记录
解析出的解释器的 `(绝对路径, edition, version)`，而且**从映像里、在宿主侧读** —— PE 版本资源，或安装
清单 —— 绝不取自某个子进程的 `$PSVersionTable`。版本资源之所以可信，正是那份覆盖映像的签名买来的；自
报什么都买不到。身份不属于该表实测过的那几个的解释器，判**不透明**，而不是「差不多」。

**自动加载被关掉，因为模块集合钉不住，而路径不是那个关键的东西。** 启动会重新组合
`PSModulePath`，所以交进去的值是输入不是设置；
路径不是集合，因为它下面的文件在任何一次记录之后都会变；而且就算钉得完美，CurrentUser 模块目录仍在
那里 —— 它在工作树之外，而自动加载会在 5g 落到 PATH 之前先搜它。所以：

- 启动在一段**钉死的前奏**里设 `$PSModuleAutoLoadingPreference = 'None'` —— 它是下面命令行表里
  逐字节固定的文本，与 `-NoProfile` 同一种意义上属于命令行，不属于 body。地板的保证是它扫过了
  body；前奏是地板从不改动的文本，门槛 21 用一段第一条语句带可观察副作用的 body 来断言 body 不受
  影响。
- `PSModulePath` 仍然钉死，作为纵深防御，而不是作为机制。
- **没有单独一道检查能确立「该偏好确实生效」，所以有三道 —— 外加一件三道都做不到的事。**「子进程无
  法证明该偏好生效时 5g 降级」不可实施：地板在任何子进程存在*之前*就已裁定，事后子进程报告什么都改
  不了已经给出的裁定。所以：
  - **在任何启动之前，先从磁盘读配置。** 去*问解释器*它的会话配置是什么行不通 —— 一个自定义控制台
    会话配置能在启动时导入模块、定义命令、跑自己的脚本，等到某段 body 能报出「非默认」时，那份配置
    早就跑过了。**关于一个程序的事，没法靠启动这个你正要对它做判断的程序来知道。** 所以解析这一步
    把**三个来源**当作数据读 —— 解释器 `$PSHOME` 下那份 AllUsers `powershell.config.json`、用户
    profile 下那份 CurrentUser，以及优先于这两个文件的 Group Policy —— 除非生效的控制台会话配置就是
    默认那一个，否则拒绝该级。**`$PSHOME` 不是「launcher 旁边」：** 上游把它定义为正在执行的
    `System.Management.Automation.dll` 所在的那个目录（§3.20），所以当 launcher 是 shim、符号链接或
    一份拷贝时，它是另一个目录。这一步读的是 (a) 档在宿主侧解析出的安装根；宿主侧解析不出来时，拒绝
    该级，而不是退回去读 launcher 所在目录。
  - **在阶梯解析处做一次预检** —— 而且只在 (a) 或 (b) 已经认证过映像之后，因为一次启动确立不了被启动
    者的任何事。之后 D4 的阶梯才用同一段前奏、配一段 body 启动候选解释器：那段 body 报告该偏好，并把
    身份字段作为对「宿主已认证过的映像」的**一致性核对**再报一遍，绝不作为它们的来源。它的结果是地板
    本就经 `ShellSpecProvider` 读到的那份 `ShellSpec` 上的一个字段（D2）—— 于是 `_decide` 跑的时候，
    「封闭解析环境是否已确立」是手里的一个值，不是将来的一次观测。**该值为假时 5g 的裸词规则整条失
    效：每个 PowerShell 裸词都不透明**，该级按 5a 服务显式 `.exe`/`.com` 路径。
  - **每次启动，同一段前奏校验它能校验的部分并中止。** 预检的答案会过期 —— 两次之间被写入的配置
    文件、同一条路径解析到的另一个解释器 —— 所以前奏那道守卫检查**该偏好、edition、version、
    `$PSHOME`，以及生效的控制台会话配置名**，对照钉进命令行里的值，任一不符就**在 body 的任何一个
    字节运行之前非零退出**。被**代入**的四个值是 `<E>`、`<V>`、`<H>`、`<C>` —— 那个偏好是与字面量
    `'None'` 比较、不需要代入，这就是「守卫读五样、这张清单只有四项」的原因；每个都以**单引号
    PowerShell 字面量**代入、内嵌的 `'` 一律双写，而预检得到的值若无法这样编码，就拒绝这一级，而不是
    换别的转义方式。`<C>` 不能用 `$PSHOME` 顶替：安装目录替一个 endpoint 名字作证是作不了的。**如果找不到能在子进程内报出生效控制台会话配置的表达式，
    `<C>` 也不会被悄悄省掉** —— 除非预检在三个来源里都没发现配置，否则拒绝这一级，而门槛 21 记录是
    哪一种情况。
  - **守卫做不到的事，明说：它无法认证自己正跑在里面的那个解释器。** 同一路径上、edition、version 与
    `$PSHOME` 全都相同的替换体能满足每一个字段，而且它在守卫的第一个 token 被解析之前就已经拿到控制
    权 —— 由嫌疑人自己求值的检查不是身份检查。地板用**宿主侧**文件身份来收窄：预检记录该可执行文件的
    内容哈希，spawn 前立刻重新哈希一次，把窗口压到「哈希到 `CreateProcess`」这段区间，但关不掉。
**这个哈希覆盖的是 launcher，不是加载闭包。** `System.Management.Automation.dll` 以及该进程加载的
其他一切都在它之外 —— 而在 Windows 上那个程序集自己所在的目录*就是* `$PSHOME`（§3.20）—— 所以谁能
写安装根，谁就能在不碰那个被哈希过的文件的前提下改变「这个解释器是什么」。于是身份这项声明真正靠的
是「安装根对主体不可写」加「映像有签名」，哈希只测得出那一个文件被换掉。剩下
    的部分是非目标（§7），而门槛 21 的「换掉解释器」那一格如实说明它能测出哪种替换：改变了某个记录
    字段或哈希的那种，不是把它们全都对上的那种。
- `-NoProfile` **管不到** `powershell.config.json`。该文件在解析出的 `$PSHOME` 下有一个 AllUsers 作
  用域（§3.20），另有一个 CurrentUser 作用域，并且它能选择控制台会话配置，而会话配置在上述一切之前就
  绑定命令与可见性。**Group Policy 是第三个来源，而且它覆盖那两个文件** —— 这正是上面那一步要把三个
  来源都读的原因，也是身份记录这条断言的结果、而不是记那个文件的原因。
- **本设计不关闭的那处残留，明写出来而不是暗示。** 在预检*之后*装上的会话配置，其启动脚本会在
  `-Command` 前奏**之前**运行 —— 「选择一份配置」的含义本就如此。子进程内的守卫能拒掉 body，却撤不
  回一段先于它跑完的脚本。所以诚实的说法比「启动文件不能运行」更窄：profile 不能（`-NoProfile`），
  解析时刻已存在的配置也不能（该级直接被拒），但在「解析到 spawn」这个窗口里装上的配置，会在守卫拦
  下 body 之前跑一次。这个窗口靠「spawn 之前立刻重读三个来源」收窄，但不关闭。它是一条非目标
  （§7），以及门槛 21 的刻画性探针 (a)，预期结果已写进探针，既不是发布门槛，也不是一句「已关闭」。

**顶层命令行，逐级钉死：**

| 级 | 命令行 |
|---|---|
| `pwsh` / `powershell.exe` | `"<path>" -NoProfile -NonInteractive -Command "<前奏>; <body>"`，钉死 `PSModulePath`，其中 `<前奏>` 是逐字节固定的 `$PSModuleAutoLoadingPreference='None'; if ($PSModuleAutoLoadingPreference -ne 'None' -or $PSVersionTable.PSEdition -ne '<E>' -or $PSVersionTable.PSVersion.ToString() -ne '<V>' -or (Get-Item -LiteralPath $PSHOME).FullName -ne '<H>' -or <C-check>) { exit 97 }`，其中 `<E>`、`<V>`、`<H>`、`<C>` 是**预检记录下的** edition、version、`$PSHOME` 与生效的控制台会话配置名，各以单引号 PowerShell 字面量代入、内嵌 `'` 双写，而 `<C-check>` 是读取生效配置名的那个表达式 —— 它是本计划唯一尚未核实其子进程内写法的字段，所以除非预检在所有来源里都没发现配置，否则拒绝这一级（D4）。那道守卫是同一个参数的后半截，所以没有任何 body 字节能抢在它前面运行（§3.13）。构造方式是 **`Popen(list, shell=False)`**，前奏与 body 作为**一个**元素，绝不拆到多个参数。**「不重新加引号」在 Windows 上做不到** —— 列表形式一律会被 `list2cmdline` 再序列化一次（§3.12）—— 所以这一格给出的是可核验的那句：门槛 18 的哨兵断言子进程收到的 body 与地板扫过的 body 逐字节相同；若那道门槛绿不了，该级退回下面 `cmd` 那一格已经在用的「单字符串 + `executable=`」形式。codex 传 `-NoProfile`（`codex-rs/core/src/shell.rs:32-40`），pi-mono 加 `-NonInteractive` 与 `-ExecutionPolicy Bypass`（`packages/coding-agent/src/utils/shell.ts:122`）；agentao 取二谢一 |
| `cmd` | 单一字符串 `"<path>" /d /e:on /v:off /s /c "<body>"`，并以 `Popen(..., executable=<path>)` 设置 `lpApplicationName`；`/s` 剥外层引号，`/d` 跳过 AutoRun，`/e:on /v:off` 钉住状态；body 绝不再次加引号（§3.12） |
| Git Bash | `"<path>" --noprofile --norc -p -c <body>`，顺序如此（§3.16），`shell=False`，环境中不含 `BASH_ENV`、`ENV` **与任何 `BASH_FUNC_*`**（§3.14、§3.16 —— `-p` 管它启动的那个进程，清除环境才管得住它的子孙）；`MSYS_NO_PATHCONV=1` 使 MSYS2 不改写 `/c/…` 形的参数 |

**Git Bash 那一级最弱，单独开关。** 它的地板是 POSIX 的**模式集** —— §3.5 的 18 个类，含 §2.7 实测的
fail-open —— **外加**规则 6、规则 5h 与封闭可运行集，这比 Linux 主机今天那个 shell 拿到的更多，也正是
spec 要带一个 `rung` 的原因（D2）：`git_bash` 政策开着，`system_posix` 在 §9 q4 定案之前不开，而方言
根本分不出这两者。它的裸词解析是 bash 自己的（规则 5h），`PATHEXT` 收不窄它；它在 MSYS2 下的路径翻译
行为在这里未测。既然开关现在准入的是 `cmd` **之上**的那一级（D6），打开它就等于把较弱的地板排在较强
的地板之前 —— 这正是它默认关闭、只放在用户级而不是项目级的原因，也是 PR-7 仅在门槛 20 于 Windows job
上转绿时才启用它、可以关着发布的原因。这不是对 bash 的降级 —— 这一级的模式集就是 bash 自己那道地板，
额外那些规则只会拒得更多 —— 而是拒绝为 Windows 声称未在 Windows 上测过的东西。

### D5 —— 包装、求值器、名字表达式、封闭的可运行集、生成进程者，以及重绑

POSIX 递归（`agentao/permissions_hardline/_scanner.py:143-146`、
`agentao/permissions_hardline/_scanner.py:166-168`）；codex 的 CMD 包装
（`codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:92`）与前缀
（`codex-rs/core/src/exec_policy.rs:104-108`）。

**规则 0 就是整条降级流水线，顺序如下，任何一步失败即不透明**（§3.19）。这个顺序不是摆设：第 2 步的
遮蔽之所以只有一个字节宽，**正是为了**第 8 步还能拿区间和原始源码比对。

| # | 步骤 | 拒绝什么 |
|---|---|---|
| 1 | Unicode 语法别名 | 弯引号、短破折号与长破折号 —— PowerShell 当它们是语法，语法规则不当 |
| 2 | `--flag=value` 遮蔽 | 不是拒绝；是一次单字节替换，好让第 8 步的字节区间仍然有效 |
| 3 | 解析完整性 | 任何含 ERROR 或缺失节点的树 |
| 4 | **`#Requires`** | 文本左去空白并转小写后以 `#requires` 开头的 `comment`（§3.18） |
| 5 | **节点 kind** | 任何不在下表里的具名 kind |
| 6 | 非空 | 一段根本没降级出任何命令的脚本 |
| 7 | **字面 argv 降级**，逐 command 节点 | 引号与反引号只在运行期取值静态可知时才解码；拼接元素、空词、形如 `-Path:x` 的 attached parameter value，以及非规范的数字打头裸词（十六进制、前导零）都在这里被拒（§3.19） |
| 8 | **源码保真**，一次**有状态走查**，不是字符集合 | command 区间之间的每个字节都要被一台带 `can_chain`／`needs_command`／`paren_depth` 的自动机放行，它还约束每种分隔符**能出现在哪里**，并要求收尾状态为「区间全部消耗完 ∧ ¬needs_command ∧ paren_depth = 0」（§3.19） |
| 9 | `using` 声明 | 它们需要一个本地板没有的 AST oracle |
| 10 | 空命令或空词 | 降级之后的最终不变式：任何降级出的命令与词都不得为空 |

第 8 步正是 `git status --short#; Remove-Item victim` 不透明、而不是变成一条孤零零
`git status --short` 的原因：tree-sitter 能把内嵌的 `#` 切成一个被接受的 `comment`，只有走原始字节
才看得出该行其余部分不见了。**读树的规则没法拿树去对文本。** 第 8 步有两件「不是」。它不是「每个字节
都在 command 节点内」—— 那会拒掉管道、分号与行尾注释，而那些都是正例，一道 fail-closed 的闸仍然得放行
那些让脚本成为脚本的分隔符。它也**不是一组许可字符**：一个孤立的 `)` 属于任何这样的集合，却必须被拒，
因为自动机只放行配得上它开过的左括号的右括号，并要求收尾时 `paren_depth = 0`。fixture
`uncovered_closing_paren`（`Get-Content --flag=value )`）正是这一格
（`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:67`）。**把一台有状态
检查器的字母表列出来，等于它的行为一条都没规定。**

**第 5 步细说 —— 裁定单位是 AST 节点，不是命令。** 降级遍历每个具名节点，只问它的 kind 在不在接受
清单上；不在就让整段脚本不透明。**这条轴只有 `ACCEPTED` / `REFUSED` 两个值，再无其他** —— 规则 6 的
效果标志说的是*命令*，把它们的名字挪用到这里，就是把两个不同的问题塞进同一个词。

| 节点 kind | 裁定 |
|---|---|
| `program`、`statement_list`、`pipeline`、`pipeline_chain`、`pipeline_chain_tail`、`command`、`command_name`、`command_elements`、`command_argument_sep`、`command_parameter`、`generic_token`、`array_literal_expression`、`unary_expression`、`expression_with_unary_operator`、`string_literal`、`verbatim_string_characters`、`expandable_string_literal`、`integer_literal`、`decimal_integer_literal`、`empty_statement` | `ACCEPTED` |
| `comment` | `ACCEPTED`，**且只因为第 4 步已经跑过** |
| 其余每一个具名 kind，含 `assignment_expression`、`variable`、成员调用与 scriptblock body | `REFUSED` → 不透明 |

这就关掉了那些从不变成命令的形式：`$Function:git = { … }` 与 `$Alias:git = 'Remove-Item'` 是对
provider 驱动器变量的赋值，`[Environment]::SetEnvironmentVariable('PATH', …)` 是成员调用，而嵌套
scriptblock 是命令级规则从不进入的 body。**这里没有「重绑」这一档 —— 每一个可能重绑的 kind 直接被
拒**，这也正是这条轴要两个值、而规则 6 那条要四个值的原因。

这二十一个 kind 是整体照搬 codex 的清单
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:143-169`），因为它是对着本计划
用的同一份语法 pin 量出来的（§3.4），而它自己的注释说这条拒绝要维持到
*"until its lowering semantics are reviewed"*
（`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:128`）—— 那份审查就是每一行的
成本，照搬清单等于连它的审查一起继承。加一个就是加一行加一条测试；而语法升级若改了某个 kind 的名字，
会 fail closed，由门槛 3 核查。规则 6 的效果于是是更后面的一道闸，作用在通过规则 0 每一步的那些
命令上。

1. **包装体按被调方的方言重新进入。**
2. **PowerShell 启动面按 PowerShell 自己的方式解析**（§3.10）：`-Command`/`c`、
   `-CommandWithArgs`/`cwa` → 重新进入；`-EncodedCommand`/`e`、`-ec` → 解码后重新进入；
   `-File`/`f` → 不透明；`nop`、`nol`、`noni`、`noe`、`ex`、`w` → 消费；**其它任何东西 → 不透明**
   （`codex-rs/shell-command/src/powershell.rs:9`、`codex-rs/shell-command/src/powershell.rs:60-62`）。
   **重新进入买到的是一次拒绝，不是一次放行 ——
   嵌套的解释器启动，它自己就是不透明的。** D4 钉住的每一条保证，都是*agentao 自己写出来的那条命令行*
   的性质：逐字节固定的前奏、`-NoProfile -NonInteractive`、身份守卫、钉死的 `PSModulePath`、逐级的
   argv。由**子进程**启动的 `pwsh`、`powershell`、`cmd` 或 shell，一条都不带 —— 模块自动加载又开着，
   而那恰恰是 5g 那张表被实测时*不*处在的状态（D4），解释器也是子进程解析到的那个，不是宿主认证过的
   那个。所以解析照跑，仍然按它自己的理由（而不是某个含糊的理由）拒掉危险的嵌套 body；而启动本身不会
   因为熬过了这次解析就变成放行。规则 6 对**脚本路径**上的 `bash`/`cmd`/`pwsh` 早就是这么说的 ——
   rev 22 让 `-Command` 形式与 `-File` 形式一致，而不是自相矛盾。
3. **`cmd` 要被分析**（D7）。
4. **`command_name_expr` 的四种形态**（§3.8）：4a 求值器源码；4b 字面名字重组；4c 脚本块就地；
   4d 运算符之下的路径 → 不透明。
5. **可运行集按方言封闭 —— 且按 rung 生效。** rung 切换的是 D2 给过一次、这里不再复述的那份清单：
   D3 的 token 规则、规则 6 的效果标志，以及这个封闭可运行集。`system_posix` 维持今天的行为，直到
   §9 q4 另行决定（D2）。
   - **5a.** 显式 `.exe`/`.com` → 归一到 basename（`agentao/permissions_hardline/_patterns.py:35-37`）
     并作为命令词匹配 —— **而且只有两半都成立才可运行。** *名字*：归一后的 basename 在该方言的可信表
     里有条目，并带着规则 6 给它的那组效果标志。*映像*：子进程将要打开的那个文件落在可信根内 ——
     一个**子进程自己的主体写不了**的目录，而过滤后的 PATH 就是由这种目录构成的，于是宿主配置的
     那些根与 PATH 是一个谓词、不是两种强度（D4）—— **而且没有任何东西能替代这个位置。** 宿主
     identity allowlist（绝对路径 + 内容哈希，或宿主信任的签名）是压在它**之上的附加条件**，绝不是
     它的替代项 —— 而它里面那两种形式并不是一回事：**content pin**（绝对路径 + 哈希）测的是「正是这个
     文件被换掉了」，即便那个根仍然成立也测得出来，这就是 D4 里 launcher 哈希的活计；**publisher
     trust**（签名）说的只是「此刻在那里的那个文件是宿主信任的发布者签的」—— 同一发布者的替换体满足
     它，别人的替换体不满足。两者都做不到的是：放行一个被位置拒掉的文件。当作替代项时，它按构造准入
     的就是主体可写的映像，而且不需要竞态
     就能被击破：`Copy-Item .\evil.exe <allowlist 里的路径>;
     <那个词>` 在一个 body 之内、在地板算哈希与子进程打开文件之间把文件换掉，而往文件系统路径
     `Copy-Item` 在规则 6 下是惰性的 —— provider 驱动器那条规则打的是 `Env:` 一族，不是 `C:\` ——
     于是没有任何规则会拒掉这次拷贝。这正是 `executes_input` 的文件形式早就讲过的那套论证，只不过
     allowlist 是它换了个名字的例外：**地板算哈希、子进程打开文件，只有「执行主体写不了的位置」能关上
     这道缝。** 这也正是 D4 对 launcher 用的那个模式 —— 主体写不了的那个根是防线，哈希测的是「那一个文件
     被换掉」，而不是顶替那个根。它对用户自装工具链的代价是 §9 q12，那是一个决定，不是一条脚注。
     **工作树永远不是可信根**（§7）。任一半缺失，就让**这一条命令**
     不透明，而不只是让它之后的东西不透明。两半互相独立，因为各自堵的洞不同：有名字没映像，是一个
     被拷进工作树的 `git.exe` 借用 `git` 的条目；有映像没名字，是一个谁都没分类过的程序从可信目录里
     未经分析地跑起来。**而映像那一半之所以真的咬得住，全靠那个谓词：** 5e、5g、5h 都是*经*过滤后的
     PATH 解析的，所以只要任何 PATH 目录都算可信根，对每一个裸词来说映像那一半就按构造恒真，5a 等于
     名字那一半戴了两顶帽子。三个反例都在门槛 4 里。
   - **5b.** 其它所有扩展名 → 不透明。**5c.** 无扩展名路径 → 不透明。**5d.** `-File` → 不透明。
   - **5e. cmd 裸词：** 内部命令 → 匹配；否则过滤 PATH 搜索到 `.exe`/`.com` → 5a；否则不透明。
   - **5f.** 生成进程者的目标遵守 5a–5c 与其方言的裸词规则。
   - **5g. PowerShell 裸词：** **实测解释器身份**那一张 cmdlet/alias 表（D4）→ cmdlet；否则过滤
     PATH 搜索到 `.exe`/`.com` → 5a；否则不透明。一张跨两个 edition 的表，要么信任其中一个根本没有
     的名字，要么漏掉它确实有的。**该表在钉住的启动状态里量，不在普通会话里量** —— 关掉自动加载
     之后，普通会话靠按需加载模块才解析得到的命令根本解析不到，图省事量出来的表会放行子进程随后
     command-not-found 的东西。每一条都在该状态下验证可解析，而该状态是这张表身份的一部分。**整条
     规则以预检的答案为条件（D4）：封闭环境未被确立时，PowerShell 的每一个裸词都不透明**，该级仍按
     5a 服务显式 `.exe`/`.com` 路径 —— 一个裸词的可信度，只等于「可能供出它的那个集合」的最小规模，
     而开着自动加载的解释器，它那个集合就是运行那一刻某个用户模块目录下磁盘上的东西。
   - **5h. bash 裸词。** PATH 搜索是最后一步，不是规则本身：bash 先解析别名、关键字、函数、内建
     命令与命令哈希（§3.15）。在 PATH 搜索之前就解析掉的词判**不透明**，除非它在该级的惰性内建集里
     （规则 6）。走到 PATH 搜索的词按 bash 自己的规则经过滤 PATH 解析 —— 精确文件名、任何可执行文
     件，脚本或二进制 —— 再按其 basename 对 POSIX 表匹配，与今天 `/bin/rm` 的待遇相同；找不到 → 不透
     明。**没有扩展名约束，也不声称有**：可信 PATH 目录里的脚本是可信目录的内容，而过滤后的 PATH ——
     只留「子进程主体写不了」的目录（D4）—— 就是这一级闭集性质的全部。**在 Windows POSIX 层上，文件
     名匹配这一条留空** —— MSYS2 把裸 `git` 解析成 `git.exe`，它与同目录里无扩展名 `git` 的优先级由
     门槛 20 实测、写进本条，然后 PR-7 才打开这一级（§3.13）。
6. **每条命令都带一*组*效果，其中只有一种说的是它之后的事。** 一张封闭的修改形式表、底下压一句「表
   外的形式不是修改者」，无论表画得多细都是黑名单，而三种 bash 形式直接穿过去：`printf -v PATH …`
   与 `read PATH <<< …` 不用赋值语法就写了 `PATH`，`hash -p <path> git` 更是完全不碰 `PATH` 就重绑
   了命令名（§3.15）。PowerShell 同形，Environment provider 认整个 `*-Item` 家族：
   `Copy-Item Env:\A Env:\PATH` 与 `Rename-Item Env:\A PATH` 都是修改者，而表里那四行
   `*-Item` 没点到它们。于是量词反转：

   - 方言可信集里的每一条 —— 5e 的 cmd 内部命令、5g 的 cmdlet 与别名、POSIX 表、以及每一级的内建
     集 —— 都带一**组标志**，**结合它拿到的参数**判定。这些标志**不是**互斥的：`.`、
     `source`、`Import-Module`、`eval` 与 `Invoke-Expression` 既执行输入**又**重绑调用方，而那正是
     它们存在的理由。

     | 标志 | 断言 | 后果 |
     |---|---|---|
     | *（无标志）*—— 惰性 | 不写任何环境变量、不绑定任何名字、不改变当前位置或 provider 驱动器，也不运行任何地板未降级的输入 | 受信任；再无下文 |
     | `rebinds_after` | 改变**本 body 内**后面某个名字解析到什么 | 它之后在这里的每条命令不透明 |
     | `executes_input` | 把某个文件或字符串的内容当代码运行 | **这条命令自身不透明。** 唯一的例外是**不含任何 `Dynamic` token 的字面字符串**，它被按本方言当作 body 重新进入（规则 4a）—— 地板做得到，是因为那个字符串本就是它已经扫过的命令行的一部分。**文件目标一律不透明，路径长什么样都一样** |
     | `rebinds_caller` | 它的效果落在**调用方**作用域里，不是落在一个子进程里 | 见下面的传播规则 |

   - **文件形式不透明，因为静态的路径不等于不可变的字节。** 放行「读取其内容的字面路径」，是把名字
     和内容混为一谈：地板读 `safe.ps1`、做出裁定，而子进程在执行时刻重新打开那个路径。
     `Set-Content safe.ps1 evil; . .\safe.ps1` 在一个 body 内就做到了；另一个进程可以在裁定与启动
     之间做到。「执行分析过的那份快照而不是那个路径」不可得 —— 脚本不是地板跑的，是 PowerShell 跑的，
     而它打开的是路径。本文也没有跟踪普通文件写入，就算加上，并发写入者依然在。所以文件形式没有例外，
     这同时也免掉了那种例外本来需要的效果状态记账。
   - **递归分析返回一份退出态摘要，同作用域的调用形式把它传播出去。** 分析一个目标回答的是「这段
     body 有没有做过调用方必须知道的事」，这**不是**「它里面有没有哪条命令被污染」那个问题。看一个只
     有一行 `hash -p ./evil git` 的 `safe.sh`：单独分析时那是一条没有后继的 `rebinds_after`，里面什
     么都不透明，于是一条逐 body 的规则就把这个文件放过了。然后 `source ./safe.sh; git status` 就拿
     一张被重绑的哈希表去跑 `git`。所以递归分析返回一份摘要 —— *这段 body 退出时有没有留下被重绑的名
     字* —— 而带 `rebinds_caller` 的调用方把它并进自己的状态，使调用点之后的每条命令不透明。
     `bash ./safe.sh` 不会：子进程里的重绑随它一起消失，只有 `executes_input` 适用。**一条关于序列末
     元素的规则，必须说清这个序列*留下了什么*，而不只是它一路污染了什么。**

   - **`executes_input` 正是拦住 `Import-Module .\evil.psm1`、`. ./evil.sh` 与 `source ./evil.sh` 作
     为脚本最后一条命令被放行的东西。** 只归为名字重绑，它们后面就没有东西可污染，而它们各自早已跑完
     一个地板没看见的文件。关掉自动加载（D4）碰不到一次显式 import。该标志还覆盖 `Invoke-Expression`
     与 `iex`、作用于路径的 `&` 与 `.`、`-File`、`eval`，以及被喂了脚本路径的 `bash`/`cmd`/`pwsh` ——
     规则 1、4a 与 5d 本就重新进入或拒绝它们，规则 6 现在把同一件事对整个集合说一次，而不是逐例说。
   - 命令词**根本解析不到任何条目**时，让**这一条**命令不透明，其后每一条也不透明。只污染后继是
     一个一行就能利用的洞：一段脚本只有一条命令、而它是个未分类的程序，那就没有后继可污染，于是地板
     恰恰放行了它了解最少的那种情形。未识别的名字不是「不是修改者」，也不是「大概无害」—— 它是
     「未被确立为惰性」，而在这几个方言上这与不透明是同一件事。**正是这一条让「封闭」名副其实**，也
     正因如此 §9 q9 —— 惰性集值得做多宽 —— 现在决定的是「什么能跑」，而不只是「什么会污染后继」。
     代价写在这里，而不是留到 PR-7 才发现：一个新的 PowerShell 或 cmd 级上，可信表之外的每一个程序
     都是 DENY，直到有人带着它的效果加一行。没有任何东西隐含地带 `executes_input` —— 一个会跑代码的
     未知名字，在任何规则生效之前就已经跑了，而那正是规则 0 与封闭可运行集在上游要防的。
   - 在 PowerShell 里，参数只要点名了非文件系统的 provider 驱动器 —— 匹配
     `^[A-Za-z][A-Za-z0-9]*:` 且不是盘符文件系统路径 —— 不论 cmdlet 是什么，该命令即为非惰性。
     这一条规则就关掉了 `Env:`、`Alias:`、`Function:`、`Variable:` 与注册表驱动器，不必一行一条。
   - 惰性断言所依赖的任何位置上出现 `Dynamic` token → 不透明，与别处一致（D3）。

   逐方言的 `executes_input` 集合，效果落在调用方作用域的另标 `rebinds_caller`：PowerShell 的
   `Import-Module` 与 `ipmo`**（+调用方）**、`Invoke-Expression` 与 `iex`**（+调用方）**、作用于
   路径的 `.`**（+调用方）**、`Add-Type`**（+调用方）**、作用于路径的 `&`、`-File`；cmd 的
   `call <file>`**（+调用方）**、`start <file>`；bash 的 `.` 与 `source`**（+调用方）**、
   `eval`**（+调用方）**，以及任何被喂了脚本路径的解释器。那些枚举出来的修改形式作为**门槛用例保留，
   而不是规则** —— cmd 的 `set`、`path`、`setx`、
   `call set`、`for /f … do set`；PowerShell 的 `$env:`、`Set-Item`、`Set-Content`、`New-Item`、
   `Remove-Item`、`Clear-Item`、`Copy-Item`、`Rename-Item`、`[Environment]::SetEnvironmentVariable`；
   bash 的 `PATH=`、`export`、`declare -x`、`env PATH=…`、`printf -v`、`read`、`hash -p`、`alias`、
   函数定义、`.`/`source`、`BASH_ENV=`、`ENV=`。往这张清单里加一种形式不改变任何行为，只是加一条
   规则本就通过的测试。它再也不能做的，是裁定它漏掉的那些情形。
7. **生成进程的命令一律不透明，而重新进入是用来拒绝它们的，不是用来放行它们的。** 规则 2 的论证从来
   不是关于 `pwsh` 这个词：只有当那条命令行是 agentao 写的，这次启动才可信，而本条里的每一个命令都把
   启动交了出去。**此前仍被当作放行的那三个** —— `Start-Process` / `saps` / `start`、
   `Invoke-Item` / `ii`、cmd `start`
   （`codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:47-53`）—— 通过
   **ShellExecute** 解析目标：先当前目录、再文件关联、再 PATH。那不是 5g 的解析器，所以 5g 那张实测出
   来的表，对「究竟跑了什么」什么都没说，而一个关联是注册表里的一条记录，本文没有任何规则去读它。
   `Start-Process` 接着一个参数一个参数地改掉 D4 钉住的东西：**`-UseNewEnvironment` 从注册表重取用户
   环境**，等于在地板放行过的 body 里把过滤前的 `PATH` 装回去；`-WorkingDirectory` 改掉 ShellExecute
   最先搜的那个目录；`-Environment`（7.4+）整份换掉环境；`-Credential` 与 `-Verb RunAs` 改掉**主体**，
   而主体正是 5a 映像那一半所依据的谓词（D4）。**其余那些** —— `Start-Job` / `sajb`、
   `Invoke-Command` / `icm` 的每一个远端参数集（`-ComputerName`、`-Session`、`-ConnectionUri`、
   `-VMId`、`-VMName`、`-ContainerId`、`-HostName`、`-SSHConnection`），以及**尾置的 `&` 作业运算符**
   （它就是写成语法的 `Start-Job`）—— 都在另一个进程或另一台机器上运行，不带前奏、自动加载回默认、
   解释器没人认证过。所以整条规则的裁定是**不透明**；要把启动面建模到足以放行其中任何一个，就得证明
   最终的映像、环境与主体都与 D4 一致 —— 那是一套设计，不是一张参数表，本计划里没有。重新进入保留：
   它按目标自己的理由拒绝危险的目标。cmd `start` 的语法 —— 可选带引号标题、开关、然后受 5a–5c 与 5e
   约束的目标 —— 为这次拒绝而保留，不是为放行而保留。门槛 26 逐个理由各钉一格，而 `&` 单占一行，因为
   钉住的 tree-sitter 语法给它什么节点 kind，在这里没能核实。

### D6 —— argv 启动；配置按来源整体取胜；一份快照穿过每个 root

`agentao/capabilities/shell.py:141-143`。仅用户级 `permissions.json`
（`agentao/embedding/permission_loader.py:131-136`、`agentao/embedding/permission_loader.py:11-14`）；
按来源整体优先（`agentao/embedding/factory.py:144-145`、`agentao/embedding/factory.py:146-148`）：

| 来源，由高到低 | 提供 | 更低的来源 |
|---|---|---|
| 构造参数：`shell=` 执行器，或 `shell_dialect=` / `shell_path=` | 整份 spec | **被忽略** |
| 用户级 `permissions.json` 的 `shell` 块 | 整份 spec | 被忽略 |
| `auto` | D4 的阶梯 | — |

**Git Bash 那一级的开关是这份 spec 的一个字段，不是另一套机制。** `shell.allow_git_bash` 是同一个
用户级 `shell` 块与同一份构造 spec 里的布尔值，**默认 `false`**，而且它是在**末级被选定之前**读的，
不是之后。阶梯是 `pwsh` → `powershell.exe` → Git Bash → `cmd`：开关决定 Git Bash 那一级在不在，而
`cmd` 两种情况下都是回退 —— 开关关着时，以及开关开着但找不到 Git Bash 时。**「两种情况」说的是开关，
不是信任**：一个过不了 D4 身份或位置检查的 `cmd`，与任何其它级一样被拒绝，于是阶梯可能以「什么都没
选中」结束 —— D4 对此的回答是 `hardline:no-trusted-rung-opaque`，而不是悄悄退回 `%COMSPEC% /c`。
**守在 `cmd` 之下的开关
不可达**，因为每一个受支持的 Windows 上都有 `cmd.exe`、阶梯总是停在那里；那正是先前那个位置的问题：
开关为 `true`、那一级也装着，Git Bash 依然永远选不到，而门槛 11 绿着、门槛 20 测的是生产环境走不到的
路径。门槛 11 现在钉的是**两种开关状态下**的顺序，而「红则关着发布」设定的是默认值。

一份不可变的 `PermissionConfig { rules, sources, shell }` 穿过 `agentao/acp/session_new.py:366-374`、
`agentao/acp/session_load.py:262-270` 与 `agentao/embedding/factory.py:186-192`。子代理工厂不读任何
文件。

### D7 —— `cmd` 是 regex 方言；控制流、分组与每种变量形式都不透明

交付项：§3.6 的 CMD 行、§3.5 中每个有 cmd 拼法的类、规则 5e 的内部表、`start` 的语法、D3 的 cmd
规则、规则 6 的 cmd 行。`if`、`else`、`for`、`do`、`goto`、`call` 任一或任何语法有效的分组括号让
body 不透明；引号内或 `^` 转义的括号是字面量。最后一级。

## 5. PR 阶梯

| PR | 内容 | 用户可见 | 依赖 |
|---|---|---|---|
| **PR-0** | （门槛 0、19、22）**`Agentao._for_subagent`：父级引擎、一个有效 fs/shell、每次注册都记 `origin`、以针对子代理重跑 `register_builtin_tools` 的方式重建 registry、`ToolForkable`、MCP 所有者线程 + 非所有的作用域视图、不注册 agent 工具；引擎状态在一把写者锁后面不可变、裁定携带其快照；投射报告裁定的快照**（§2.12–§2.19） | 否 —— 关上一处活绕过 | — |
| PR-1 | `ShellDialect` **以及 spec 上的 `rung` 与 `filesystem_is_local` 字段，并在构造时校验「方言 × rung」矩阵**（D2）；**`ShellRequest` 携带 agentao 构造好的 argv 与环境，由执行器原样运行** —— 今天它只带 `command: str` 与 `env`（`agentao/capabilities/shell.py:77-84`）；执行器声明；工具暴露；`ShellSpecProvider`；`_decide` 传递；替换时的 D1 | 协议变更 | PR-0 |
| PR-2 | **只交付与状态无关的原语：** token IR + 规则 0 的降级流水线 + codex 的 fixture 语料 + 危险表 + cmd 内部表 + 每条可信条目上的效果标志 + 不依赖运行期状态的那些 D5 规则 | 否 | PR-1 |
| PR-3 | 预设；规则 `dialect`；`PermissionConfig`；用户级 `shell`；透传 | 否 | PR-2 |
| PR-4 | 可信解析 —— **该 agent 主体写不了**的安装根、**宿主侧身份 oracle**（ACL、签名、哈希；可注入，于是门槛 4 的正例在非 Windows 上也测得了）、安装根（`$PSHOME`）解析（D4）—— + 裸词解析器（5e、5g、5h）+ **按身份分的 cmdlet/alias 表，因为它必须在本 PR 建立的启动状态里量出来**、以及 5g 对预检字段的依赖 + 子进程环境（过滤 PATH、`PATHEXT`、钉死 `PSModulePath`、`-p`、移除 `BASH_ENV`/`ENV`/`BASH_FUNC_*`）+ 从磁盘读配置的拒绝 + 逐级命令行 | 否 | PR-2、PR-3 |
| PR-5 | 系统提示按方言渲染（`agentao/prompts/sections.py:199-202`、`agentao/prompts/sections.py:206`、`agentao/prompts/sections.py:208`、`agentao/prompts/sections.py:222`） | 否 | PR-1 |
| PR-6 | `windows-latest` job：D4 矩阵、§3.12 哨兵、门槛 **18、20、21、23，以及门槛 25 的 Windows 那一半** —— 这个集合不是一个区间：门槛 19（PR-0 那把写者锁与携带快照的**唯一**验证手段）与门槛 22 属于 PR-0、与平台无关，而门槛 25 里「容器 `root`」那一半在 ubuntu 上跑 | 否 | PR-3、PR-4、PR-5 |
| PR-7 | 翻转；**Git Bash 那一级在自己的开关后面，仅当门槛 20 绿时开启**（`packages/coding-agent/src/core/tools/powershell.ts:16`、`codex-rs/shell-command/src/powershell.rs:15`、`agentao/tools/shell.py:156-160`、`agentao/plugins/hooks/_alias.py:16`） | **是** | PR-6 |

**PR-0 不需要 PR-1 的任何东西** —— 一个内部工厂、registry 上的一个来源字段、一个协议、一个视图、
一个所有者线程、一把锁、一份「token → task **集合**」登记表连同喂给它的调用上下文，以及裁定详情上的一个
字段。**PR-4 需要 PR-2 与 PR-3，不只是 PR-1：** 它的裸词解析器把词交给规则 5，它的可信表带着规则 6
的效果标志 —— 两样都属 PR-2 —— 而它读 `shell.path` 与 `allow_git_bash` 用的那个 `shell` 块，是随
PR-3 的 `PermissionConfig` 一起到的。**PR-2 的依赖：** `tree-sitter` 与 `tree-sitter-powershell` 在
`[project.dependencies]` 下带 `sys_platform == "win32"`，并在 `[dependency-groups].dev`
（`pyproject.toml:117-125`）下无条件。

**五个待决问题是 PR-2 之前的决策门，不是待办堆。** §9 的 q2、q3、q9、q11 定的是危险表、惰性集与 cmd
的 `rebinds_caller` 作用域 —— 全是 PR-2 的交付物 —— 而自 rev 20 起，「不在惰性集里」意味着 DENY 而
不是污染后继（D5 规则 6），所以只要它们还开着，「PR-2 做完了」这句话谁都说不出口。**q4 是第五道**：
它不改变 PR-2 造什么，因为有 `rung` 字段，原语可以在不碰 `system_posix` 的前提下发出去（D2）—— 它决定
的是那个默认值，而一个「随代码一起到、从没被人选过」的默认值，正是这条阶梯存在的意义所在。

## 6. 发布门槛

0. **PR-0 的探针**（§2.12）经 `NullTransport` 返回 DENY，前台与后台都如此；带内存 deny、run-scope
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
1. PR-1：`ShellExecutor` 的 fake 是唯一被迫的测试改动；`PermissionEngine(` 不动。**并且没有标注的
   方言有裁定：** 自定义 `ShellExecutor` 报 `UNKNOWN`、以及报枚举之外的取值，各自都产出
   `hardline:unknown-dialect-opaque` ⇒ DENY，且发生在任何规则匹配之前 —— 断言用的是一段任何 POSIX
   模式都不会命中的 body，于是「回退到 POSIX 扫描器」会挂在这道门槛上，而不是静悄悄地通过（D2）。
   **`rung` 也照此办理：** 每个合法配对都能构造成功，`POWERSHELL × system_posix` 与一个不认识的 rung
   都**在 spec 构造时失败**并点名那个配对，而带着它们之一漏到地板的 spec 返回
   `hardline:unknown-rung-opaque` ⇒ DENY —— 同样用一段任何 POSIX 模式都不命中的 body 来断言，因为要
   门住的实现错误正是「把未知那种情形路由到 `system_posix`」，而它的政策是关着的（D2）。
2. 每个方言的每一条地板测试都**在 ubuntu 上**运行，解析器来自 `dev` 组。
3. §3.5 的 18 个类：PowerShell 翻译与 CMD 行或明写的一行。**并且节点表钉在语法上：** D5 第 5 步那张
   表里的每一个 kind，都能由钉住的解析器对某个输入产出，于是重命名了某个 kind 的语法升级会挂在这道
   门槛上，而不是悄悄把一个 `REFUSED` 的 kind 变成 `ACCEPTED`。
4. **封闭可运行集的两半（D5 5a、规则 6）：** `.\innocent.exe` 作为脚本里**唯一**那条命令判
   **不透明** —— 工作树不是可信根，而地板既没分析它的映像也没分析它的效果；一个**被拷进工作树**的
   `git.exe` 用该路径调用，判**不透明**，尽管它的 basename 在可信表里有条目（有名字没映像）；一个
   **未分类**的程序以绝对路径从可信目录里调用，判**不透明**（有映像没名字）；一个植入在**机器 PATH
   上的用户可写目录**里的 `git.exe` 判**不透明**，且该目录**不出现在子进程的 `PATH` 里** —— 这正是
   「只要任何 PATH 条目都算可信根，它就两半皆过」的那一格，也是两个工作树反例都到不了的那一格
   （D4）；而在一个主体写不了的根下、且在可信表里有条目的 `git.exe`，判**放行**。每一个判不
   透明的用例都断言失败于它自己的那条理由 —— 否则它们都会因为错的理由而通过。**并且断言 allowlist 不
   能单独成立（D5 5a）：** 一个 allowlist 里的绝对路径，若它所在目录用户可写，仅凭位置这一条就判
   **不透明**，即便它的哈希与签名都对；同一条路径**在 body 内被替换** ——
   `Copy-Item .\evil.exe <allowlist 里的路径>; <那个词>` —— 判**不透明**，而「另一个进程在地板算哈希
   与子进程打开文件之间替换它」那一版同样判不透明，两者都断言失败于位置、而不是失败于哈希 —— 因为一
   次碰巧命中的哈希核验会把正在被测的那条规则挡住。**正例跑在它能跑的地方：** 「有签名、在仅管理员可
   写的根下、判放行」在 ubuntu 上是身份 oracle 的桩（门槛 2），在 Windows job 上是真的 ACL 加签名
   （门槛 23）—— 一个在它自己那道门槛所运行的平台上根本造不出来的用例，不是门槛（D4）。然后是
   PowerShell 对抗
   性用例，外加规则 6 门槛清单里每个 PowerShell 形式后跟一条命令
   （**不透明**）、`Copy-Item Env:\A Env:\PATH; git` 与 `Rename-Item Env:\A PATH; git`
   （**不透明**，由 provider 驱动器规则判定，没有任何一行点过它们）、未识别的 cmdlet 后跟一条命令
   （**不透明** —— 解析不到条目即非惰性），以及 `Get-Date; git status`（**放行** —— 惰性条目）。
   **`executes_input` 单独测，后面什么都不跟：**
   `Import-Module .\evil.psm1`、`. ./evil.ps1` 与 `& ./evil.ps1` 作为脚本里**唯一**那条命令，以及
   各自再作为若干条里的**最后**一条 —— 全部**不透明**，而且现在**即便目标是地板本可以读到的字面路径
   也依然不透明**，`Set-Content safe.ps1 evil; . .\safe.ps1` 与一个被并发改写的 `safe.ps1` 就是排除
   任何字面路径例外的那两格；这三条后跟 `git status` 时同样不透明，理由
   还是它们自己，不是那个后继。bash 上：`. ./evil.sh` 与 `source ./evil.sh` 单独出现。
   **`rebinds_caller` 的传播：** 一个只有一行 `hash -p ./evil git` 的
   `safe.sh` 被分析出「里面没有不透明的东西」，随后 `source ./safe.sh; git status` 判**不透明** ——
   断言它失败于被传播上来的退出态，而不是失败于 `source` 本身；`bash ./safe.sh; git status` 因*另一
   个*理由（一个未降级的子进程）不透明，而一个已降级且惰性的 `helper.sh` 后跟 `git status` 仍然
   **放行**，于是传播不是一刀切的拒绝。**规则 0 拿 codex 的语料测，而不是我自己挑的例子：**
   `powershell_lowering.json` 全部 68 例，其中 44 条 `null` 行在这里也必须不透明，且逐条断言它失败在
   哪一步 —— 因错误的理由而不透明，算失败。逐条点名，因为它们落在不同步骤：
   `git status --short#; Remove-Item victim`（第 8 步，源码保真）、`Remove-Item test –Force`（第 1
   步，Unicode 别名）、`git log --% HEAD`（停止解析）、`using module ./x.psm1`（第 9 步）、一个
   attached parameter value 与一个十六进制或前导零的数字打头裸词（**第 7 步**，argv 降级）、
   `$Function:git = { & C:\evil.exe }; git` 与
   `[Environment]::SetEnvironmentVariable('PATH','C:\x'); git`（第 5 步，节点 kind），以及
   `#Requires -Modules Evil` 后跟一个可信裸词、连同它带前导空白与大小写混写的版本（第 4 步）—— 而一
   条普通的 `# comment` 后跟同一个词仍然**放行**，于是第 4 步抓的是指令，不是注释。**另外那 24 条非
   `null` 行也是门槛，方向相反，而且要按 codex 的比法比：** 不是「降级成功」，而是**整个降级出的
   `argv` 与 fixture 的 `expected` 相等**，那正是它自己的测试所断言的
   （`codex-rs/shell-command/src/command_safety/powershell_tree_sitter_tests.rs:22-24`）。
   只要求「降级成功」，错的引号、错的转义或切错的参数边界都能过，然后把错的值交给那些判定危险的参数
   谓词。`a | b`、`a; b` 与行尾注释也在这些行里。
5. 每个词干的启动参数用例及越界用例。
6. CMD 对抗性用例，外加规则 6 门槛清单里每个 cmd 形式 —— `path C:\x & git`、
   `setx PATH …`、`set "PATH=…"`（**不透明**）—— 以及标记为惰性的内部命令后跟 `git`（**放行**）。
7. **bash 用例：** `PATH=/x git`、`export PATH=…; git`、`BASH_ENV=./p bash -c …`、`alias rm=…; rm`、
   `. ./f; rm`（**不透明**）；**`printf -v PATH /x; git`、`read PATH <<< /x; git` 与
   `hash -p ./evil git; git`（**不透明** —— §3.15 实测的三种）；未识别的内建命令后跟 `git`
   （**不透明**）**；经过滤 PATH 解析到的裸 `git`（**放行**）；不在过滤 PATH 上的裸 `evil`
   （**不透明**）；以及**在**过滤 PATH 上、但不在 POSIX 表里的裸 `evil`（**不透明** —— 有映像没名字，
   D5 5a）。**并且断言 rung 真的键住了什么：** 上面每一条裁定都是在 `rung` 为 `git_bash` 的 spec 下取
   的，而同样这些 body 在 `system_posix` 下产出**今天**的裁定 —— 成对，因为一条无法被选择所区分的政
   策，与一条永远开着的政策不可分辨，而 §9 q4 之所以开着，正是为了让它可分辨（D2）。
8. 不透明经 `NullTransport` 与 PowerShell 子代理都被拒绝。
9. 三个桶的降级率，在 PR-7 之前经接受。`uv run ruff check .` 绿。
10. 逐级的 Windows 矩阵。11. **两种 `allow_git_bash` 状态下**都钉住阶梯顺序：关着时阶梯止于 `cmd`；
    开着且 Git Bash 在场时选 Git Bash、排在 `cmd` 之前，不在场时回退 `cmd` —— 于是开关是在生产环境
    真正走的那条路径上被测的（D4、D6）。12. `settings.json` / 项目文件里的 `shell` 按 D6。13. 快照
    抵达每个 root；子代理按身份持父级引擎。14. 缺 provider / 冲突被拒。15. 不解析工作树二进制。16. 按
    来源整体优先。17. 构造、`add_tool`、`remove_tool` 之后 registry 身份成立。
18. 在 Windows job 上：`NoDefaultCurrentDirectoryInExePath=1`；哨兵 body 逐字节一致；子进程 `PATH` 与
    `PATHEXT` 如钉，**且机器 PATH 上一个用户可写的目录不出现在子进程的 PATH 里**（D4）；
    `git.cmd` vs `git.exe` 跑 `.exe`；含空格的 cmd 路径按该解释器调用。
19. **并发、多写者与不可变性：** 后台子代理在紧循环里裁定的同时，父级从三个线程交错执行
    `set_mode` ×1000、`add_run_rules` ×100（**每次一条互不相同的 deny**）、`active_permissions`
    ×1000；之后那 100 条 deny 一条不少，每次裁定携带的快照内部一致，每个投射出的事件点名的是其自身
    裁定的快照。另有一组不开线程的：改动传给构造函数的列表、`rules` 返回的列表、返回的
    `ActivePermissions`、以及裁定携带的快照，之后每一次裁定都不变；随后新建的第二个引擎的 preset
    也未被改动。
20. **Windows job 上的 Git Bash：** 父环境中 `BASH_ENV` 指向工作树文件时，子进程只跑 body；导出
    `BASH_FUNC_git%%` 时裸 `git` 是 `/usr/bin/git` 而不是那个函数（§3.16），**并且往下两层进程同样
    断言**：一条可信命令自己再跑 `/bin/sh -c` 时，那个环境里看不到任何 `BASH_FUNC_*` —— 这一条 `-p`
    单独给不了，只有清除环境才给得了（D4）；`/c/Users` 形与
    `C:\Users` 形的参数在 `MSYS_NO_PATHCONV=1` 下原样抵达 body；裸 `git` 跑可信的 `git.exe`；工作树
    里的 `evil.sh` 不被裸 `evil` 执行。**并且它实测 5h 留空的那一条：** 一个可信目录里无扩展名的
    `git` 脚本与 `git.exe` 并存时，裸 `git` 跑的是哪一个 —— 答案在该级上线前写进 5h。红 ⇒ PR-7 关着
    这一级发布。
21. **Windows job 上的 PowerShell edition 矩阵：** 同一段脚本在 `powershell.exe` 与 `pwsh` 下各用
    自己实测的表；一个在一个 edition 里是别名、在另一个里不存在的裸词在两边判定不同，而记录身份两张
    表都不匹配的解释器判**不透明**。**自动加载，从子进程内部量、并且对抗性地量：** 把一个导出名为
    `git` 的函数的模块放进 **CurrentUser 模块目录、位于工作树之外** —— 正是「没有工作树路径」那条
    断言会放过、而这一条不放过的情形 —— 子进程报告 `$PSModuleAutoLoadingPreference` 与裸 `git` 解析
    到了什么，它必须解析到可信的 `git.exe`，绝不能是那个模块。前奏被断言不扰动 body：一段第一条语句
    有可观察副作用的 body，在前奏之后仍产生同样的副作用。**任一作用域的 `powershell.config.json`
    选择了非默认会话配置时，该级连一次都不启动解释器就拒绝** —— 断言方式是给那份配置一个会写下哨兵
    文件的启动脚本，并要求该文件不存在；一个「问解释器它的配置是什么」的设计，为了问出来就已经把那个
    脚本跑了。预检无法确立封闭环境的地方，地板把每个 PowerShell 裸词都当作不透明，门槛断言的是这次
    降级，而不是一次失败。**并且断言前奏那道守卫是中止而不只是上报：** 当会话配置把该偏好改回去时，
    一段第一条语句带可观察副作用的 body 产生**零**副作用，启动以非零码退出 —— 只查成功路径与降级
    裁定的门槛，永远不会去跑那段「必须跑不起来」的 body。**TOCTOU 两个方向都测：** 在预检之后、启动
    之前改掉配置，以及另测把解析路径底下的解释器换掉 —— 守卫的身份校验失败、非零退出，body 的副作用
    一次都没发生。**门槛 21 里有两项是刻画性探针，不是发布门槛，这个区别现在写明了。** 发布门槛判红就
    挡住翻转；刻画性探针记录的是 §7 已经声明过的残留的实测行为，它的预期结果写在探针里。两个探针：
    (a) 预检之后装上的配置 —— **启动哨兵预期存在**，因为那段脚本跑在前奏之前，断言的是 *body* 的副作
    用不发生；(b) 把解释器换成记录字段与记录哈希全都对上的那一个 —— 预期**测不出来**。两者都是
    `xfail` 式、写明预期，于是任一方向的变化都会让套件失败。门槛 21 的其余各项都是发布门槛。**套件里
    没有「允许判红的门槛」这个类别** —— 一道可以判红的门槛，什么都门不住。
22. **递归与缺省白名单：** 内置 generalist（`agentao/agents/definitions/generalist.md:1-4`）的定义
    没有 `tools:` 键，它的子代理拥有每一个非 agent 工具、以及零个 `agent_*` 工具，因而无法生成
    自身；显式点名了某个 agent 工具的定义只拿到那一个。
23. **解释器的发现与身份，宿主侧（D4）：** 一个被丢进「恰好在机器 PATH 上的用户可写目录」的
    `pwsh.exe`，**永远不被自动选中** —— 那个目录也过不了过滤器（D4）；断言的是它没有被*启动*：给这个
    植入的二进制一段会写下哨兵文件的 body，并要求该文件不存在；同一个二进制经 `shell.path` 显式点名
    时**会**被选中，这正是两档的区别所在，而不是自相矛盾；位于某个已知安装位置里、但没有签名的映像被
    拒；而一个自身目录**不是** `$PSHOME` 的 launcher —— shim、符号链接或一份拷贝 —— 它的 AllUsers
    `powershell.config.json` 从宿主侧解析出的安装根读，或在该安装根解析不出来时拒绝该级，绝不从
    launcher 所在目录读（§3.20）。**而正例就写在这里，不再是从门槛 4 指过来：** 一个落在「该 agent 的
    主体写不了」的根下、且在可信表里有条目的 `git.exe` 判**放行** —— 对着子进程的 token 做真实的 ACL
    检查，且**既不带签名、也不在 allowlist 里**，于是这道门槛没法靠「把两者之一当作准入条件」蒙混过去
    （D5 5a）。
24. **嵌套启动与非本机执行器（D5 规则 2、D2）：** body 里的 `pwsh -NoProfile -Command "git status"`
    判**不透明**，尽管那段嵌套 body 单独看每个字节都放行；而 `pwsh -Command "Remove-Item -Recurse
    -Force C:\"` 是在重新进入的那段 body 里被**危险表命中**（§3.6）拒掉 —— 于是两者靠理由区分，而不
    只是靠裁定；`cmd /c git status` 与
    `bash -c 'git status'` 同理。以及：`filesystem_is_local` 为假、执行器又没提供 oracle 的 spec，让
    每一个需要映像的命令词都不透明 —— 一个整个省掉该字段的 spec 同理，因为缺席即 `false` —— 而同一段
    body 在本机 spec 下保持它原来的裁定。**提供了 oracle 之后，裁定跟着目标走、不跟着地板走：** 一个在
    目标 PATH 上解析得到、在地板 PATH 上解析不到的裸词判**放行**，反过来那个判**不透明**，而
    `Start-Job { … }` 在两边都不透明（规则 7）。一次读错文件系统的检查，是因为错的理由才通过的（D2）。

25. **提权态有裁定（D4）：** agentao 以 Windows 管理员身份、或容器里的 `root` 运行时，每一个候选根都
    对执行主体可写，于是可信集为**空**、该级被**拒绝** —— 用那条让它要紧的序列来断言：
    `Copy-Item .\evil.exe 'C:\Program Files\Git\cmd\git.exe'; git status`，它在任何一步都不得走到
    「放行」。**非特权时，同一段 body 是*被放行*的** —— 地板没有可拒之处，因为往文件系统路径
    `Copy-Item` 是惰性的、`git` 两半都过 —— **而那次拷贝会在 OS 层失败**，于是跑起来的是那个可信的
    `git`。这一对才是门槛：同一段文本，两种姿态下各一个裁定，因为一个从不改变答案的谓词，等于没有被
    求值。**走空的阶梯也一并断言：** 每一级都被拒绝时，一次 shell 调用返回
    `hardline:no-trusted-rung-opaque`，而工具仍然注册着 —— 不是消失，也不是退回 `%COMSPEC% /c`
    （D4、D6）。
26. **规则 7 的那些包装器，逐个理由各一格（D5 规则 7）：** `Start-Process git` 判**不透明**，理由是
    ShellExecute 不是 5g 的解析器；`Start-Process -UseNewEnvironment git` 不透明，**且断言在环境这条
    理由上**，因为光这一个开关就能在被放行的 body 里把过滤前的用户 `PATH` 装回来；
    `Start-Process -Verb RunAs git` 在主体这条理由上；`Invoke-Item .\x` 与 cmd `start x` 在文件关联
    这条理由上；`Invoke-Command -ComputerName a { git status }` 与 `git status &` 作为「另起进程」的
    启动。**`&` 那一行同时记下本计划没能核实的东西：** 钉住的 tree-sitter 语法给尾置作业运算符什么
    节点 kind，这里没测过，所以那一行只断言它被拒、发生在第 5 步或第 8 步，并写明是哪一步 —— 一个
    *理由*未知的用例，它的裁定仍然是钉住的。

## 7. 非目标

- **一个 `powershell` 工具。** **`cmd` 在 PowerShell 之上。** **macOS/Linux 上的 PowerShell。**
- **审计任何地板没有降级的文件。**
- **为 shell、裸词、子进程或启动文件解析信任任何工作区文件或二进制。**
- **在 agent 之间共享工具实例或 MCP 工具对象** —— 共享能力与作用域视图，绝不共享对象。
- **子代理专属的权限模式。** **`rebind()` API。**
- **给 bash 一个基于扩展名的闭集。** bash 没有 `PATHEXT`；规则 5h 如此说明。
- **关闭 POSIX 间接缺口** —— §9 q4。
- **关闭会话配置的 TOCTOU。** 在阶梯解析与 spawn 之间装上的控制台会话配置，其启动脚本跑在那段本应
  拒绝它的前奏之前。这个窗口靠「spawn 前立刻重读三个来源」收窄，没有关闭（D4）；门槛 21 的探针 (a)
  测它。
- **认证解释器的加载闭包。** 预检哈希的是 launcher；`System.Management.Automation.dll` 以及该进程
  加载的其他一切都在这个哈希之外，而在 Windows 上那个程序集所在的目录*就是* `$PSHOME`（§3.20）。
  身份这项声明真正靠的是「安装根对主体不可写」加「映像有签名」，所以一个可写的安装根就把它打破 ——
  而设计**拒绝**这样的安装根（D4），不声称覆盖它；门槛 23 断言这次拒绝。
- **从解释器内部认证这个解释器。** 位于解析路径上、且与记录的 edition、version、`$PSHOME` 与内容哈希
  全部相符的替换体测不出来，而且它在守卫被解析之前就握有控制权。这个窗口靠「spawn 前立刻重新哈希」
  收窄，没有关闭（D4）；门槛 21 的探针 (b) 测它。**这两条正是 `Scope` 不再不加限定地写「启动文件」的
  原因。**

## 8. 什么会改变本计划

- **`tree-sitter-powershell` 不再提供 wheel。** **实测的 Windows 用户数为零。** **不透明桶不可用。**
- **PowerShell、cmd、bash 或 Windows 改变本计划钉住的任何语义** —— `MatchSwitch`、命令优先级、
  `PATHEXT`、`Start-Process`、profile、`/s`、`start`、分组、`BASH_ENV`、
  `NoDefaultCurrentDirectoryInExePath`、`lpApplicationName`。
- **agentao 采纳工作区信任模型。**
- **某个 MCP 工具包装被发现持有每 agent 状态** —— 门槛 0 的并发调用检查是它浮现的地方。

## 9. 待决问题

1. **哪种降级分布可接受？**
2. **`cryptsetup luksFormat` 的 Windows 对应物。**
3. **codex 的「带 URL 的启动」类。**
4. **`system_posix` 那一级该不该采纳 D3 的 token 规则、规则 6 的惰性要求与封闭可运行集？** 在 Linux 上
   三者都是对每一位现有用户的行为变更，而自 rev 20 起，规则 6 尤其会让一个未识别的命令词**拒掉它所在
   的那次调用**，而不只是污染今天地板放行的整段脚本。`rung` 字段（D2）正是让这个问题保持开着、而不是
   靠发布来回答它的东西：Windows 那几级政策开着，`system_posix` 不开，而 §5 把这一条列为 PR-2 之前的
   第五道决策门，好让那个默认值是被选出来的，不是被继承下来的。
5. **hook payload 需要方言作为一个字段吗？**
6. **干脆保留 `run_shell_command`？**
7. **裸 `Agentao(...)` 该不该构造默认引擎？**
8. **`_for_subagent` 该不该成为公开的 `Agentao.fork(...)`？** 自己生成子代理的宿主有 wrapper 曾有的
   同一个问题。
9. **惰性集值得做多宽？** 最小的那个安全，也会拒掉很多；每加一条都是一份需要有人核验的断言。
   **自 rev 20 起，这决定的是「什么能跑」**，而不只是「什么会污染后继」（D5 规则 6）—— 所以 §5 把它
   列为 PR-2 之前的决策门，而不是一个开放式问题。
10. **还有什么先于 kind 闸门、而 codex 自己也没找到？** 规则 0 照着 codex 的流水线走，也就只和
    codex 自己的覆盖面一样好 —— 它接受清单上的注释写着那些拒绝要维持到逐 kind 的降级语义被审过
    为止，所以它的闸门是别人为另一套政策画下的底线。这一带每一处缺口，至今都是靠读那份源码找出
    来的，不是靠本文的方法找出来的。
11. **cmd 里哪些 `rebinds_caller` 形式带哪种作用域？** PowerShell 与 bash 那几个有充分文档，
    `call` 与 `start` 没有，而这个标志值多少钱，全看它那张逐方言表值多少钱。
12. **用户自装的工具链怎么才跑得起来？** allowlist 降级为附加条件之后（D5 5a），`uv`、python.org 的
    Python、scoop 的 shim —— 它们**按设计**就装在用户可写的前缀下 —— 进不了可信集：过滤后的 PATH 会
    丢掉它们的目录，而 allowlist 也不再能单独成立。可选项是：宿主把它们装到「该 agent 主体写不了」的
    根下；由用户做一次逐路径的显式信任授权，就像 `shell.path` 对解释器那样 —— 那是**照 D4 (b) 档形状
    写的、有文档的例外**，不是进入可信集的第二条路，而且它同样带着那处 TOCTOU，明说出来、不是默默
    继承；或者接受这次拒绝。在开发者自己的机器上，这几乎就是他跑的全部东西，所
    以这是一个有用户可见答案的决定，不是一条脚注。

## 10. 引文方法

上文每一条 `file:line` 都在 `scripts/check_citations.py` 下于锚点解析。§3.10 与 §3.20 各带 commit、
完整哈希与一次重抓；§3.11、§3.12 与 §3.14–§3.16 读并跑了本地软件。没有任何规则拿早期版本来陈述自己；
正文里出现版本号的地方，标的都是一次*更正* —— 改了什么、在什么时候 —— 绝不是某条规则所依赖的条件。

**二十二轮评审产出了下面这些规则 —— 这个数取自表头，不是另记一份。每一条都是因为「没有它」而漏过了
缺陷，并按漏过的次数排序。**

1. **借鉴时把整个函数按序读完，并把它的测试语料一起拿走。** 有三轮各自只拿了 codex 降级里被点到名的
   那一块，把它周围的闸门留在原地；而反例一直就写在它的 fixture 文件里。零敲碎打借来的防线，洞就在
   没人看的那些接缝上。
2. **每条要求都写在实现者会照抄的地方，然后核对散文与表是否一致。** 写在散文里、却被旁边那张规范表
   否掉的要求，等于没写。这一条造成过四个独立的 P0。**而当两条规则点到同一个对象 —— 一个可信根、一道
   地板、一道过滤 —— 要核对它们点它时用的是同一个强度：** rev 21 发现过滤后的 PATH 被 D4 当作不够格的
   信任级别否掉、又被 5a 当作可信根收下，而这发生在关掉这个洞的那同一版里。一处修复必须触及每一条点到
   被修对象的规则，那是一次 grep，不是一次通读。
3. **问一条规则在量化什么，并检查它所带方向的两头。** 套在错单位上的 fail-closed 断言，对正确的单位
   就是 fail-open；对前驱做的谓词对末元素什么也说不出来；而只报告「文件*内部*被污染了什么」的递归
   分析，对这个文件给调用方留下了什么只字未提。
4. **当设计说「封闭」时，问规则拿清单漏掉的那种情形怎么办。** 若答案是「放行」，它就是黑名单。而把
   一台有状态检查器的字母表列出来，等于它的行为一条都没规定。**这条规则写下来三个版本之后才被拿去
   审 5a**，而那里漏掉的情形是「任何显式 `.exe`」、答案正是「放行」—— 一条写下来的规则，在有人把它
   逐一套到每条自称「封闭」的规则上之前，什么都审不到。
5. **当它说「无锁」时数一数写者；当它说「同今天」时核实今天。**
6. **由被检查者自己求值的检查不是检查。** 跑在解释器内部的守卫无法认证这个解释器；静态路径不等于
   不可变字节。**一个程序也不能靠「把它启动起来」来认证** —— 启动正是这道检查要门住的那个事件，而
   子进程报告的关于它自己的每一个字段，都是受怀疑者报的。选择必须在宿主侧、在第一个字节执行之前
   决定。
7. **跑代码。** §2.7、§2.12、§3.4、§3.8–§3.12 与 §3.14–§3.16 之所以存在，是因为对它们每一条，推理都
   给出了错的答案。
8. **当某一版写下一条教训时，先拿它审这一版自己。** 记下第 1 条的那一轮，在同一遍里就违反了第 1 条。
   **一个关于本文的数字，就是本文里的一条断言：** 这一行曾写着十八轮，而表头写着十九轮 —— 因为表头被
   更新了，规则底下这句没有；每一个自指的计数，在它所计的东西变动时都必须重新推一遍。
9. **一道可以判红的门槛什么都门不住。** 把发布门槛与刻画性探针分开，并把预期结果写进探针（§6）。
10. **走不到的分支不是防线。** `allow_git_bash` 守的是 `cmd` 之下的一级，而每个受支持的 Windows 上都
    有 `cmd.exe` —— 于是开关、钉住顺序的那道门槛、以及测这一级的那支探针，全都绿在生产环境走不到的
    路径上。一条规则带顺序时，要检查其中每个位置都可达。
11. **改一条规则，不到「引用它的每一处摘要、表格与门槛都重读过」就不算改完。** rev 22 重写了 5a，却
    留下 TL;DR 与 §1 仍把 allowlist 当作替代项、一道门槛指着没有任何门槛调度的用例、§5 仍写「一个
    token 一个 task」—— 下一轮三条发现，全都是旧文本活在这次编辑没去过的地方。机械做法是 grep，在本轮
    收尾之前跑、而不是留给下一位评审，并且带上写下这条规则的那一轮所欠缺的三条子句：**先把空白归一**
    ——它漏掉的那处措辞正是被连字符折到了下一行；**每个孪生件按它自己的措辞各扫一遍**，因为翻译过的
    文档里根本没有你改的那个词；**清单是这一轮改过的每一个术语**，而不是产生了发现的那一个 —— rev 23
    改了谓词、清除列表与签名三样，只扫了谓词，另外两样留在了表里。
