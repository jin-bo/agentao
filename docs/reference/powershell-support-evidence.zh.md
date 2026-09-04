# PowerShell 支持 —— 证据（源码测量、上游引用、实验输出）

> 本文件只收**测量结果与引用**，不含任何规范。规则只在
> `docs/design/powershell-support-spec.zh.md` 定义一次，以规则 ID 引用本文的小节；
> 别的文件写「§2.12」「§3.16」时，指的都是本文的同号小节。小节编号沿用拆分前
> `powershell-support-plan.zh.md`（冻结于 rev 24，commit `e01293f`）的编号，所以旧的引用不必改。

**日期：** 2026-09-03
**Anchors:** agentao `main@3537753`（2026-09-01）；codex `openai/codex@b7cd519c76`（2026-08-31）；
pi-mono `@853a80d26`（2026-08-28）。三者均从锚定 commit 的本地 worktree 读取。§3.10 另读了
`PowerShell/PowerShell` 的一个上游文件，它的 commit、blob 与 sha256 记在那一节。
**方法：** 每条前提都带本仓锚点上的行内 `file:line`。§2.7、§2.12、§3.4、§3.8–§3.12 与 §3.14–§3.16 是
**实测，不是推理**；§3.10 与 §3.20 是**按钉住的 commit 从上游抓取**的，带哈希与重抓。
**文件集：** `docs/design/powershell-support-spec.zh.md`（规范）·
`docs/design/powershell-support-implementation.zh.md`（PR 阶梯）·
`docs/design/powershell-support-gates.zh.md`（门槛矩阵）·
`docs/reference/powershell-support-evidence.zh.md`（证据）·
`docs/design/powershell-support-review-log.zh.md`（评审记录）·
`docs/design/subagent-runtime-safety-plan.zh.md`（原 PR-0）。

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

## 4. 决策节引用的其它来源

拆分前的决策节（rev 24 的 D1–D7）里有二十一处引用不落在 §2、§3 的任何小节里；规范文件不带引文，
所以它们收在这里，按支持的规则 ID 归档。移入子代理计划的那部分（原 D2 的 PR-0、MCP 与引擎三段）连同
它们的五十一处引用原样在那份计划里，不在此重复。

| 引用 | 支持的规则 | 说的是什么 |
|---|---|---|
| `agentao/permissions.py:747-750` | TOOL-02 | `args` 条件是对原始命令文本的正则 |
| `agentao/permissions.py:76` | TOOL-02 | `_LEGAL_RULE_FIELDS`，`dialect` 要加进这张表 |
| `agentao/agent.py:418`、`agentao/agent.py:906` | TOOL-01 | 名字守护要重跑的两处：构造期注册，以及 `add_tool(replace=True)` |
| `agentao/agent.py:390-392`、`agentao/agent.py:411-416` | TOOL-01（q6 的备选） | `_PLAN_ONLY_TOOLS` 模式 —— 「干脆保留名字」之外的另一条路 |
| `agentao/tools/shell.py:248-252` | SPEC-01 | 今天 `ShellTool` 里的方言常量 |
| `agentao/capabilities/shell.py:107-123` | SPEC-01、TOOL-04 | `ShellExecutor` Protocol 只有 `run` 与 `run_background`，没有声明方言的位置 |
| `tests/test_shell_capability_swap.py:20-30` | SPEC-01 | 执行器注入的现有测试形状 —— G01 说的「唯一被迫改动的 fake」就是它 |
| `agentao/capabilities/shell.py:77-84` | LAUNCH-01 | 今天的 `ShellRequest` 带 `command: str`、`cwd`、`timeout`、`on_chunk`、`env` —— 没有启动形态、主体或映像 |
| `codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:13` | TOK-01 | codex 的降级返回 `Option<Vec<Vec<String>>>`，承载不了「这个词是动态的」 |
| `codex-rs/shell-command/src/shell_detect.rs:257-262`、`codex-rs/core/src/exec_policy/executable_identity.rs:62-72` | IMG-05 | codex 的已知绝对安装位置（与 §3.3 引的 pi-mono `packages/coding-agent/src/utils/shell.ts:76-92` 同类） |
| `codex-rs/shell-command/src/powershell.rs:98-101` | IMG-07 | *"pwsh.exe is the cross-platform PowerShell Core (v6+) executable"* 对 *"powershell.exe is the Windows PowerShell (v5.1 and earlier) executable"* —— 两个不同的程序，别名集不同 |
| `agentao/permissions_hardline/_scanner.py:143-146`、`agentao/permissions_hardline/_scanner.py:166-168` | WRAP-01 | 今天地板对 POSIX 包装的递归 |
| `codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:92` | WRAP-01、WRAP-03 | codex 对 CMD 包装的处理 |
| `codex-rs/core/src/exec_policy.rs:104-108` | WRAP-02 | codex 对启动前缀的处理 |
| `codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:67` | LOWER-03 | fixture `uncovered_closing_paren`（`Get-Content --flag=value )`）—— 孤立的 `)` 必须被拒的那一格 |
| `agentao/embedding/permission_loader.py:11-14`（与 §2.10 的 `agentao/embedding/permission_loader.py:131-136`） | CFG-01 | 只读用户级 `permissions.json` |
| `agentao/embedding/factory.py:144-145`（与 §2.9 的 `agentao/embedding/factory.py:146-148`） | CFG-02 | 按来源整体优先的现有形状 |

## 引文方法

上文每一条 `file:line` 都在 `scripts/check_citations.py` 下于锚点解析（`uv run python
scripts/check_citations.py docs/reference/powershell-support-evidence.zh.md`）。§3.10 与 §3.20 各带
commit、完整哈希与一次重抓；§3.11、§3.12 与 §3.14–§3.16 读并跑了本地软件。没有任何测量拿早期版本来
陈述自己；本文出现版本号的地方，标的都是一次*更正* —— 改了什么、在什么时候 —— 绝不是某条规则所依赖的
条件。
