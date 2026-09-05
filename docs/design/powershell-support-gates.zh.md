# PowerShell 支持 —— 门槛矩阵（可执行验收）

> 本文件是**追踪矩阵**：每一行一个用例，固定六列，机器核对 —— 每条规则 ID 至少一行、每行的规则 ID
> 存在、每行的 PR 存在、平台为 `windows` 的行必须落在 `PR-6`（Windows job）上。§2 是各门槛的原文
> （自 rev 24 移入；矩阵改动后回修过的段落只有 G04 的传播段，其余未改写），矩阵行由它们逐条拆出；两者不一致时以矩阵为准并回修原文。
> 规则在 `powershell-support-spec.zh.md` §2；本文只引用 ID。PR-0 的门槛（G00、G13b、G17、G19、G22）在
> `subagent-runtime-safety-plan.zh.md` §6。

**日期：** 2026-09-05 · **状态：** rev 47。**rev 47 新增一行、改一行**（新增 G23-14 —— IMG-06a 拆成目标／祖先两张掩码后的四组用例；G23-12 由「探针待跑」改为已跑并记下答案，见评审记录 rev 47 行）。「§2.x」「§3.x」指证据文件。**rev 45 改零行** —— PR-4 与 PR-6 的实施轮，发现全部落在规范、契约与实现上（评审记录 rev 45 行）；`windows / PR-6` 里不需要 identity oracle 的那几行已由 `tests/test_windows_launch_matrix.py` 覆盖，其余仍等本机 oracle；本矩阵里 `ubuntu / PR-4` 的每一行现在都有代码可跑；余下未落地的只有本机 Windows oracle（实现文件 §5.4「尚未落地」）。**rev 44 改一行**（G24-20 加第三种桩答 —— `target_filesystem_is_local()` 答 `None`（答不出）时读作 `false` 且不抛异常），对应第十七轮完整安全评审的三条发现，见评审记录 rev 44 行。**rev 43 改两行**（G04-04 加一条经 `..` 绕回同一个可写目录的 PATH 条目 —— 归一之后两条是同一个目录；G14-03 加 `dialect: cmd` 在 Windows 目标与 POSIX 目标各一遍 —— 后者拒绝该来源，不是导出一个政策开启的 `cmd` spec 再在 `floor()` 末支报 `launch-oversize`），对应第十六轮完整安全评审的十七条发现，见评审记录 rev 43 行。**rev 42 未改任何门槛行**（结构修订）：契约里有实体的函数补上行为测试 `tests/test_powershell_contracts.py`，见评审记录 rev 42 行 —— 那些测试钉的是**今天契约模块的行为**，不替代本文的门槛，门槛仍是 PR-1 至 PR-7 落地时要写的那些。**rev 41 新增两行、改两行**（新增 G23-13、G24-23；改 G23-10 加「指向父目录的可信 junction 必须通过」、G24-11 的 oracle 方法清单加 `image_signer`），对应第十五轮完整安全评审的三条发现，见评审记录 rev 41 行。**rev 40 新增一行**（G23-12 —— 钉值目录逐个按 IMG-06a 对子进程 token 实测，**探针，不预设答案**，规范 §7.3 q14），对应第十四轮完整安全评审的两条发现，见评审记录 rev 40 行。**rev 39 新增一行**（G21-20 —— PowerShell 的 `<W>` 遇上四个非 ASCII 单引号必须拒绝该次调用），对应第十三轮完整安全评审的两条发现，见评审记录 rev 39 行。**rev 38 新增一行**（G25-07 —— 显式来源导出政策关闭的一级时，点名的解释器必须活到启动），对应第十二轮完整安全评审的两条发现，见评审记录 rev 38 行。**rev 37 新增一行**（G23-11 —— 只差一条 allowlist pin 的两份配置指纹必须不同，且判定路径上没有第二处读得到生效的块），对应第十一轮完整安全评审的五条发现，见评审记录 rev 37 行。**rev 36 新增一行**（G24-22 —— 钉值答不出即拒绝该 rung），对应第十轮完整安全评审的六条发现；该轮另四条只动机检与其回归测试，不落门槛。见评审记录 rev 36 行。**rev 35 新增两行、改一行**（新增 G04-38、G25-06；改 G01-12 —— 加 (g) 并把 (d) 的期望改成「`decide()` 里的作废与整体写入两处」）；本轮的 EFF-06 与 EFF-07 两条**没有新增门槛**，G05-02、G04-18、G04-32 早就写着那三条用例，缺的是契约里读它们的那个分支。对应第九轮完整安全评审的九条发现，见评审记录 rev 35 行。**rev 34 新增四行、改三行**（新增 G04-37、G18-16、G21-19、G24-21；改 G01-12、G21-15，另 G10-03 未改 —— 它早就断言了 rev 34 的 P0 该有的行为），对应第八轮完整安全评审的十五条发现，见评审记录 rev 34 行。**rev 33 新增六行、改六行**（新增 G01-12、G07-12、G18-15、G23-10、G24-19、G24-20；改 G01-07、G01-08、G01-09、G18-13、G21-04、G24-11），对应第七轮完整安全评审的六条发现，见评审记录 rev 33 行。**rev 32 未改任何门槛行**（结构修订）：规范把 11 条长规则拆成了子规则
`族-NNa`…，本矩阵仍点名母 ID —— 点名母 ID 即覆盖其全部子规则；`scripts/check_design_set.py --list` 列出只靠母 ID 覆盖的子规则，以后逐步点得更准。
**Anchors:** agentao `main@3537753`（2026-09-01）；codex `openai/codex@b7cd519c76`（2026-08-31）—— 门槛原文里的
`file:line` 在这两个锚点解析（`scripts/check_citations.py docs/design/powershell-support-gates.zh.md`）。
**列约定：** 「预期裁定」是地板的裁定（不透明 = DENY，放行 = 交给规则），或该行断言的事实；「预期
reason」是规范 §3 词表里的 reason 或理由归属，`—` 表示只断言裁定；「平台」∈ `ubuntu` / `windows` /
`both`；`xfail` 行是**刻画性探针**，不是发布门槛（预期结果写在行里，任一方向变化都让套件失败）。
**rev 31 新增五行、改五行**（新增 G01-11、G14-06、G18-14、G24-17、G24-18；改 G01-07、G10-03、G18-11、G18-12、G24-11），对应第六轮完整安全评审的八条发现，见评审记录 rev 31 行。**rev 30 新增五行、改七行**（新增 G10-03、G14-05、G18-13、G24-15、G24-16；改 G01-09、G14-03、G18-12、G21-18、G23-09、G24-11、G24-13），对应第五轮完整安全评审的九条发现，见评审记录 rev 30 行。**rev 29 新增三行、改六行**（新增 G18-12、G24-13、G24-14；改 G14-03、G18-11、G21-17、G21-18、G24-09、G24-10），对应第四轮完整安全评审的十条发现，见评审记录 rev 29 行。**rev 28 新增十行、改四行**（新增 G01-09、G01-10、G04-36、G14-04、G18-10、G18-11、G21-17、G21-18、G23-09、G24-12；改 G18-07、G18-08、G21-15、G24-09），对应第三轮完整安全评审的十条发现，见评审记录 rev 28 行。**rev 27 新增十行、改九行**（新增 G01-08、G04-34、G04-35、G14-03、G18-08、G18-09、G21-15、G21-16、G24-11、G25-05；改 G04-10、G04-16、G04-18、G04-32、G08-02、G14-01、G18-07、G24-09、G24-10、G25-03），对应第二轮完整安全评审的十二条发现，见评审记录 rev 27 行。**rev 26 新增十八行、改三行**（G01-07、G04-30–33、G07-09–11、G08-02、G10-02、G11-04、G18-07、G21-13–14、G23-06–08、G24-10、G25-04；改 G18-02、G23-02、G25-01），全部对应完整安全评审的发现，见评审记录 rev 26 行。**rev 25 新增的六行：** G14-02（TOOL-02 此前没有门槛）、G05-02（EFF-06 此前没有门槛 —— 机检第一次跑就抓到）、
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
| G01-07 | SPEC-07 | 对已构造的 `ShellSpec` 的任何字段赋值；**逐层的嵌套改动** —— `spec.pinned_env.temp = …`、`spec.env_passthrough.append(…)`、`spec.launcher.image.canonical_path = …`、对 `plan.decided` 的任一字段赋值（`body`、`cwd`、`spec`、`child_env`、`attested_images`）、改 `plan.decided.child_env` 与 `.attested_images` 里的一项；`add_tool(replace=True)` 换入新 provider | 每一处赋值 / 追加都抛错（整张对象图深度冻结，`DecidedCall` 也在内）；换入后工具实例持有**新对象**，旧引用不变 | — | ubuntu / PR-1 |
| G01-08 | SPEC-08 | `_decide` 之后、执行之前宿主显式重解析（provider 换入新对象）；`PreToolUse` hook 改写 body 触发重判 | 前者：`launch()` 拒绝，不按新旧任一 spec 启动；后者：重判重新读 spec 并**整体替换** `plan.decided`（body、cwd、环境、证明集一起换），请求的 `spec_fingerprint` 等于重判所用对象的指纹 | 前者 `hardline:<dialect>-opaque:launch-spec-changed` | ubuntu / PR-1 |
| G01-09 | LAUNCH-01、EFF-03、IMG-07 | 三段 body 各测一次：两条可信**外部**命令（**不含**任何内建）、空 body、只有注释的 body；再一段含内建与 `iex '<字面 body>'`；随后 `PreToolUse` hook 改写 body | 四次的 `plan.decided.attested_images`（也就是请求里的那一份）**都含 launcher 那一项**（来自 `spec.launcher.image`，与 body 里出现了什么无关），每一项都是完整的 `ResolvedImage`（含 `FsId`、主体、内容身份）；递归里的并集也在内；改写后整体替换，旧组不残留。**前三段之所以是门槛：** launcher 只在遇到进程内条目时才进集合的实现，会让执行器按「直接目标无条目」拒掉每一次纯外部调用 | — | ubuntu / PR-4 |
| G01-10 | SPEC-07、SPEC-08 | 两个除 `identity_oracle` 对象不同外每个投影字段都相同的 spec；再逐个改动一个投影字段 | 前者 `fingerprint` 相等；后者每改一个字段指纹都变；对 `fingerprint` 自身没有输入依赖 | — | ubuntu / PR-1 |
| G01-11 | SPEC-01、SPEC-02、SPEC-03 | 漏到地板的四种坏 spec：`UNKNOWN` 方言、非法配对、`policy_enabled` 与 rung 矛盾、`policy_enabled` 为真而 `launcher`/`pinned_env` 为 `None`（反向亦测）；oracle 桩记录自己被调用过没有 | 四种都返回 SPEC-01 / SPEC-02 的 reason，**且 oracle 一次都没被调用、`ChildEnv` 一次都没算** —— 校验在准备环境之前 | `hardline:unknown-dialect-opaque` / `hardline:unknown-rung-opaque` | ubuntu / PR-1 |
| G01-12 | SPEC-08、SPEC-08a、SPEC-08b、SPEC-08c | 判定一段放行的 body（`Get-Date`）与工作目录 W1，随后：**(a)** 用另一段 body 与另一个工作目录 W2 调 `launch()`；**(b)** 判定之后对 `plan.decided` 的 `body` / `cwd` 赋值；**(c)** 拿一个从没经过判定的 plan 调 `launch()`；**(d)** grep 全仓写 `.decided =` 的位置；**(e)** 判定一段被 DENY 的 body，再拿那个 plan 调 `launch()`；**(f)** 给 `launch()` 传一个与 `spec.identity_oracle` 不同的 oracle；**(g)** 先判定一段放行的 body，再用同一个 plan 判定一段走**早退**的调用（provider 转 `Exhausted`；`validate()` 失败；`read_env_inputs` 答不出），三种各一遍，随后调 `launch()` | (a) 调不出来 —— `launch()` 的签名里没有 body / cwd 参数，多传即**类型错误**（断言的是类型，同 G24-17）；请求里的命令行含的是 `Get-Date`、`workdir` 是 W1；(b) 每一处赋值都抛错（`DecidedCall` 冻结）；(c) 拒绝，不启动；(d) 只有 `decide()` 里的两处 —— 进门那次作废与结论那次整体写入；`decide()` 之外零处（hook 重判走的也是 `decide()`）；(e) 拒绝并原样返回记录上冻着的那个 DENY —— 记录在场不等于被放行过；(f) 同样调不出来（签名里没有 oracle 参数），重哈希与重读只由 spec 冻住的那一个作答；(g) 三种早退都返回 DENY，且随后的 `launch()` **拒绝**、绝不启动第一段那个 `Get-Date` —— `plan.decided` 已是 `None` | (c) `hardline:unknown-rung-opaque` | ubuntu / PR-1 |
| G02-01 | IMG-06 | 每个方言的每一条地板测试 | 在 ubuntu 上运行，解析器来自 `dev` 组，oracle 为桩 | — | ubuntu / PR-2 |
| G03-01 | CMD-01、LOWER-01 | §3.5 的 18 类 | 每类有 PowerShell 翻译与 CMD 行，或明写的一行 | — | ubuntu / PR-2 |
| G03-02 | LOWER-02 | 接受表里的每一个 kind | 由钉住的解析器对某个输入产出；改名的语法升级挂在这里 | — | ubuntu / PR-2 |
| G04-01 | IMG-02、IMG-04 | `.\innocent.exe` 作为脚本里唯一那条命令 | 不透明 | 映像半：工作树不是可信根 | ubuntu / PR-4 |
| G04-02 | IMG-02 | 被拷进工作树的 `git.exe` 用该路径调用 | 不透明 | 映像半（有名字没映像） | ubuntu / PR-4 |
| G04-03 | IMG-02 | 未分类的程序以绝对路径从可信目录调用 | 不透明 | 名字半（有映像没名字） | ubuntu / PR-4 |
| G04-04 | IMG-01、ENV-01 | 植入在机器 PATH 上用户可写目录里的 `git.exe`；**另在 PATH 上加一条经 `..` 绕回同一个目录的条目**（`/usr/local/../home/me/bin`），oracle 桩的 `canonicalize()` 把两条归一到同一个路径 | 不透明；**两条都不出现**在子进程 `PATH` —— 归一之后它们是同一个目录，只比字符串会漏掉后一条 | 映像半 | ubuntu / PR-4 |
| G04-05 | IMG-02 | 主体写不了的根下、可信表有条目的 `git.exe`（oracle 桩） | 放行 | — | ubuntu / PR-4 |
| G04-06 | IMG-03 | allowlist 里的绝对路径，所在目录用户可写，哈希与签名都对 | 不透明 | 位置，不是哈希 | ubuntu / PR-4 |
| G04-07 | IMG-03 | `Copy-Item .\evil.exe <allowlist 里的路径>; <那个词>` | 不透明 | 位置，不是哈希 | ubuntu / PR-4 |
| G04-08 | IMG-03 | 另一个进程在地板算哈希与子进程打开文件之间替换该路径 | 不透明 | 位置，不是哈希 | ubuntu / PR-4 |
| G04-09 | EFF-07 | EFF-07 门槛清单里每个 PowerShell 修改形式，后跟一条命令 | 不透明 | `rebinds_after` | ubuntu / PR-2 |
| G04-10 | EFF-05 | `Copy-Item Env:\A Env:\PATH; git`；`Rename-Item Env:\A PATH; git`；`Get-ChildItem Env:` 作为唯一命令 | 不透明，三条都是 | `EFF-05`（一个裁定，不是一个标志） | ubuntu / PR-2 |
| G04-11 | EFF-04 | 未识别的 cmdlet 后跟一条命令 | 不透明 | 解析不到条目 | ubuntu / PR-2 |
| G04-12 | EFF-01 | `Get-Date; git status` | 放行 | — | ubuntu / PR-2 |
| G04-13 | EFF-02 | `Import-Module .\evil.psm1`：作为唯一命令、作为最后一条、后跟 `git status` | 不透明 | `executes_input`（自身，不是后继） | ubuntu / PR-2 |
| G04-14 | EFF-02 | `Set-Content safe.ps1 evil; . .\safe.ps1`；被并发改写的 `safe.ps1` | 不透明 | `executes_input` 文件目标 | ubuntu / PR-2 |
| G04-15 | EFF-07 | bash `. ./evil.sh`、`source ./evil.sh` 单独出现 | 不透明 | `executes_input` | ubuntu / PR-2 |
| G04-16 | EFF-02、EFF-03 | `source ./safe.sh; git status`，`safe.sh` 只有一行 `hash -p ./evil git`；另一份内容只有 `true` 的 `safe.sh` | 两份都不透明，理由相同 —— 文件目标不递归、一个字节都不读 | `executes_input` 文件目标（`source` 自身，不是传播上来的退出态） | ubuntu / PR-2 |
| G04-17 | EFF-03 | `bash ./safe.sh; git status` | 不透明 | 未降级的子进程（另一个理由） | ubuntu / PR-2 |
| G04-18 | EFF-03 | `iex 'Get-Date'; git status`（PowerShell）；对照 G04-32 的第二条 | 放行 —— 重新进入的字面 body 惰性，退出态不污染后继，于是传播不是一刀切的拒绝 | — | ubuntu / PR-2 |
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
| G04-32 | EFF-02、EFF-03、WRAP-04 | `iex 'git status'`；`iex 'Set-Alias git C:\evil.exe'; git status`；`Add-Type 'class X {}'; git status` | 前者放行（4a 重新进入）；中者不透明；后者不透明 —— `Add-Type` 不是本方言求值器，字面串不重新进入 | 中者：`rebinds_after`（EFF-03 从字面 body 带回的退出态）；后者：`executes_input` | ubuntu / PR-2 |
| G04-33 | EFF-01、EFF-07 | `Set-Alias git C:\evil.exe; git status`、`New-Alias`、`Set-Variable` 各后跟 `git status` | 不透明 | `rebinds_after` —— 条目自己的标志，不是 EFF-05（这些不点名 provider 驱动器） | ubuntu / PR-2 |
| G04-34 | IMG-02、NAME-01、NAME-02、NAME-03 | `Get-Date; git status`（pwsh）、`dir & git status`（cmd）、`pwd; git status`（git_bash），oracle 桩里没有任何名为 `Get-Date`、`dir`、`pwd` 的文件；同一段 body 在 launcher 未认证的 spec 下 | 前者放行 —— 进程内条目的映像半是该级已认证的 launcher 映像（`cmd` 与 `git_bash` 也有，IMG-07），不做 PATH 搜索；后者不透明 | 后者：映像半（launcher 未认证） | ubuntu / PR-4 |
| G04-35 | NAME-02 | oracle 桩的实测表里 `git` 同时是 kind = function（无 EFF-08 登记）与外部程序；`mkdir x; git status`，`mkdir` 为 kind = function 且登记为惰性 | 前者不透明 —— function 先于外部程序；后者放行 | 前者：解析到 function 条目却无登记 | ubuntu / PR-4 |
| G04-36 | IMG-02、NAME-02 | 实测表里一个指向**外部** `C:\\evil\\git.exe` 的 alias，别名本身的名字在可信表里；同一个 alias 指向可信根下的 `git.exe` | 前者不透明，理由归于**目标**映像而不是别名名字；后者放行 | 前者：映像半（alias 目标） | ubuntu / PR-4 |
| G04-37 | EFF-03 | 一段把字面串重新进入嵌套到 `MAX_ANALYSIS_DEPTH` 之上的 body（`iex 'iex ''iex …'''`，逐层加深）；另一段嵌套在上限之内；再一段用包装体嵌套（`bash -c "bash -c …"`）到上限之上 | 第一与第三段不透明并**返回**（不抛 `RecursionError`、不崩掉这次工具调用）；中间那段按各层自己的理由判 | 超限那两段 `reenter-depth` | ubuntu / PR-2 |
| G04-38 | ENV-01a、ENV-01、IMG-02 | 注入 oracle 桩，机器 PATH 上一个目录主体可写、另一个不可写；判定 `git status`，记下 `resolve()` 收到的搜索路径与请求里子进程 `PATH` 的值；再让桩在两次读之间把可写那个目录改答「不可写」 | 两者是**同一份**条目序列（可写那个不在里面）；桩中途改口不改变这次调用的任何一半 —— 过滤只发生过一次 | — | ubuntu / PR-4 |
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
| G07-12 | BASH-01a | rung = `git_bash`：`git {-c,core.fsmonitor=./evil} status`、`git {status,log}`、`ls *.py`、`cat ?.txt`、`cat [ab].txt`、`cat ~/x`、`cat ~root/x`、`git $ARGS status`、`git "$ARGS" status`、`echo '*'`、`echo "{a,b}"` | 前八条整段不透明（会改变 argv 的未引用展开）；`git "$ARGS" status` 是一个 `Dynamic` 参数、按 TOK-02 判；后两条加了引号，是普通字面词 | 前八条 `BASH-01`：改变 argv 的展开 | ubuntu / PR-2 |
| G07-11 | BASH-01 | `git status; git log`、`git status && git log`、`git log \| head`、`git status & git log`；`echo 'a; evil'`、`echo "a && evil"`、`echo a\; evil` | 前四条各切成两条简单命令逐条判；后三条切成一条（引号与转义按 bash 语义） | — | ubuntu / PR-2 |
| G08-01 | TOOL-03、SUB-01 | 不透明的 body，经 `NullTransport`；经一个 PowerShell 子代理 | DENY，两处都是 | — | ubuntu / PR-1 |
| G08-02 | TOOL-04、SPEC-08 | `PreToolUse` hook 改写 body（hooks 计划 G8）：改成不透明文本；改成放行文本 | 前者 DENY；后者不沿用改写前的裁定，对最终文本重判，重判重新读 spec 并记到 plan | — | ubuntu / PR-1 |
| G09-01 | LADDER-04 | 三个桶的降级率；`uv run ruff check .` | 在 PR-7 之前经接受；ruff 绿 | — | ubuntu / PR-7 |
| G10-01 | LAUNCH-02、LAUNCH-03、LAUNCH-04、ENV-02、ENV-05 | 逐级的 Windows 矩阵（规范 §5 的表，含每级的 `PATH`、`PATHEXT` 与 PowerShell 级的 `PSModulePath` 钉值） | 每一级按它那一行启动 | — | windows / PR-6 |
| G10-02 | LADDER-05、SPEC-03 | 翻转前的 Windows 默认执行器；G04–G07 的每段 body | 报 `CMD × legacy_cmd`，走 `%COMSPEC% /c`，每段 body 的裁定与 `main@3537753` 相同 | — | windows / PR-6 |
| G10-03 | LADDER-05、SPEC-03、LAUNCH-01、CFG-02 | 三条路径各跑一次 shell 调用：翻转前的 Windows 默认（`legacy_cmd`，用 fake executor 构造，不需要真 Windows）、POSIX 主机的 `auto`、**以及显式 `path` + `dialect: posix` 在 POSIX 目标上导出的 `system_posix`** | 三次的请求都是 `LegacyLaunch`：命令、环境、工作目录与 `main@3537753` 逐字段相同，`cwd` 是本次调用的工作目录（没有 launcher 目录可用），没有 `workdir`/`execution_subject`/`attested_images` 三个字段；`launcher` 与 `pinned_env` 为 `None`；第三条**不得**走 `attested_spec`（oracle 桩断言 `target_pinned_env` 没被调用）；不算 `ChildEnv`、执行器不做复核 | — | ubuntu / PR-1 |
| G11-01 | LADDER-01、LADDER-02 | `allow_git_bash` 关着 | 阶梯止于 `cmd` | — | ubuntu / PR-4 |
| G11-02 | LADDER-02 | 开着且 Git Bash 在场 | 选 Git Bash，排在 `cmd` 之前 | — | ubuntu / PR-4 |
| G11-03 | LADDER-02 | 开着且 Git Bash 不在场 | 回退 `cmd` | — | ubuntu / PR-4 |
| G11-04 | LADDER-05 | PR-7 之后一个报 `legacy_cmd` 的 spec | 构造失败并点名；漏到地板 ⇒ DENY | `hardline:unknown-rung-opaque` | ubuntu / PR-7 |
| G12-01 | CFG-01 | `settings.json` / 项目文件里的 `shell` 块 | 被忽略 | — | ubuntu / PR-3 |
| G13-01 | CFG-03 | embedding factory、ACP `session_new`、ACP `session_load` | 同一份 `PermissionConfig` 快照抵达每个 root | — | ubuntu / PR-3 |
| G14-01 | CFG-02 | 缺 provider；两个来源各出半份 spec；只给 `path` 不给 `dialect`、只给 `dialect` 不给 `path` | 被拒，点名缺的字段；provider 进入 `Exhausted`，不落到 `auto` | `hardline:no-trusted-rung-opaque:<原因>` | ubuntu / PR-3 |
| G14-03 | CFG-02、IMG-07 | `path` + `dialect: powershell`，oracle 桩报映像 edition 为 Core、Desktop、读不出三种；`dialect: posix` + `path` 在 Windows 与 ubuntu；**`dialect: cmd` + `path` 在 Windows 目标与 POSIX 目标各一遍** | rung 分别导出为 `pwsh`、`powershell`、该来源被拒；`posix` 在 Windows 导出 `git_bash`、在 ubuntu 导出 `system_posix`；**`cmd` 在 Windows 目标导出 `cmd`、在 POSIX 目标拒绝该来源**（不是导出一个政策开启的 `cmd` spec 再在 `floor()` 末支报 `launch-oversize`）；配置里给 `rung` 字段是未知键。**并且断言没有循环依赖：** oracle 桩记录 `read_identity` 收到的第二个参数，它必须是**方言**，导出 rung 之前不得有任何调用向 oracle 报出 rung。**另加三种坏身份：** 返回 `None`；返回的身份内嵌的是**另一个** `ResolvedImage`（或主体不是本次的执行主体）；在 `pwsh` 候选位置返回 Desktop edition —— 前两种拒绝该来源，第三种跳过这一级、绝不按候选位置构造 `pwsh` spec | 读不出：`hardline:no-trusted-rung-opaque:<原因>` | ubuntu / PR-4 |
| G14-02 | TOOL-02 | 带 `args.command` 而无 `dialect` 标注的规则，rung 为 `pwsh` | spec 构造失败，逐条点名并列出四个标签；POSIX 与 cmd 下照旧生效 | — | ubuntu / PR-3 |
| G14-04 | CFG-02、SPEC-05 | oracle 桩答 `target_platform()` 为 Windows 而宿主是 POSIX，显式 `dialect: posix` + `path`；再反过来（宿主 Windows、目标 POSIX） | 前者导出 `git_bash`（政策**开**），不是 `system_posix`；后者导出 `system_posix`；两次都不看宿主平台 | — | ubuntu / PR-4 |
| G14-05 | ENV-06、IMG-01 | oracle 桩返回的 `PinnedEnv`：多一个未登记的键；`system_drive` 给成 `C:\\`（不是 `C:`）、`home_path` 给成绝对路径、`com_spec` 给成目录；某个系统目录对主体可写 | 四种全部拒绝该 rung，理由分开（未知键 / 形态 / IMG-01）；profile 那一类主体可写**不**拒绝 | `hardline:no-trusted-rung-opaque:<原因>` | ubuntu / PR-4 |
| G14-06 | SPEC-05、ENV-06 | 非本机：oracle 绑定主体 A，用主体 B 去问 `target_base_env` / `target_path_entries` / `resolve_image` / `discover` / `target_pinned_env`；另一次 oracle 答的目标项目根与宿主项目根**不同**，一个被授权的键的值落在**目标**项目根内、不在宿主项目根内 | 前者每一处都拒绝作答、该 rung 未认证；后者那个键被移除 —— 「项目根之内」按目标那条路径算 | 前者：映像半（launcher 未认证） | ubuntu / PR-4 |
| G15-01 | IMG-04 | 工作树里的二进制 | 不解析 | — | ubuntu / PR-4 |
| G18-14 | SPEC-07、CFG-02 | oracle 桩第一次 `target_platform()` 答 Windows、第二次起答 POSIX，跑一次完整的 `select_rung` 加一次调用 | 全程只调用一次；导出的 rung、`spec.target_platform` 与 `ChildEnv` 用的都是第一次那个值 —— 第二次起的答案不改变任何结果 | — | ubuntu / PR-4 |
| G16-01 | CFG-02 | 构造参数与用户级 `shell` 块同时给出 | 按来源整体优先，低来源被忽略 | — | ubuntu / PR-3 |
| G18-01 | ENV-04 | cmd rung 的子进程 | `NoDefaultCurrentDirectoryInExePath=1` | — | windows / PR-6 |
| G18-02 | LAUNCH-02、LAUNCH-03 | 哨兵 body，含非 ASCII、`%`、`"`、换行与 `^` | 子进程收到的 body 与地板扫过的逐字节一致；观测手段写明（若靠 `$MyInvocation.Line` 自报，量的是引号不是身份） | — | windows / PR-6 |
| G18-03 | ENV-01、ENV-02 | 子进程 `PATH` 与 `PATHEXT`；机器 PATH 上一个用户可写的目录 | 如钉；该目录不在子进程 `PATH` | — | windows / PR-6 |
| G18-04 | ENV-02 | 同目录 `git.cmd` 与 `git.exe` | 跑 `.exe` | — | windows / PR-6 |
| G18-05 | LAUNCH-03 | 含空格的 cmd 路径 | 按该解释器调用 | — | windows / PR-6 |
| G18-06 | ENV-03 | `pwsh` 与 `cmd` rung 的子进程，父环境导出 `BASH_FUNC_git%%` 并设 `BASH_ENV`；一条可信 `git` 经 `!` 别名再起 `sh.exe` | 那个 `sh` 的环境里没有任何 `BASH_FUNC_*`、`BASH_ENV`、`ENV` —— 清除不限于 bash rung | — | windows / PR-6 |
| G18-07 | LAUNCH-08 | `pwsh` rung 上序列化后恰为 32766 个 UTF-16 code unit 的命令行；恰为 32767 的；含非 BMP 字符、`len()` 为 32766 而 code unit 数为 32767 的 | 第一条不因长度被拒；后两条分析之前拒绝，不截断，不启动 | `hardline:<dialect>-opaque:launch-oversize` | windows / PR-6 |
| G18-08 | ENV-06、ENV-03 | 父环境带 `PASSTHROUGH` 之外的键 —— `SHELLOPTS`、`BASHOPTS`、`GIT_CONFIG_GLOBAL`、`GIT_CONFIG_COUNT`、`GIT_CONFIG_KEY_0`、`NODE_OPTIONS`、`PYTHONPATH`、`LD_PRELOAD` —— 另加 `XDG_CONFIG_HOME`、`SSL_CERT_FILE`、`SSL_CERT_DIR`；且 `GIT_CONFIG_GLOBAL` 指向工作树里写了 `core.fsmonitor` 的文件；四级各一次，body 为 `git status` | 子进程环境里没有那一组键（含三个配置根与信任根）；`SystemRoot`、`USERPROFILE`、`APPDATA`、`TEMP` 是钉值，`LANG`、`LC_*`、`HTTP_PROXY` 透传，`ComSpec` 是钉值；fsmonitor 哨兵文件不存在 | — | windows / PR-6 |
| G18-09 | ENV-06、ENV-03 | ubuntu：oracle 桩答出的目标基础环境含 G18-08 那组键；用户级 `shell.env_passthrough: ["JAVA_HOME", "BASH_ENV"]` | 请求的 `env` 是完整映射、由 `ChildEnv` 算出：只含透传集 ∪ `JAVA_HOME` 与钉值；`BASH_ENV` 仍不在（保留键，用户扩展无效）；`PATH` 等于过滤后的目标 PATH | — | ubuntu / PR-4 |
| G18-10 | ENV-06 | 基础环境同时有 `Path` 与 `PATH`、`ComSpec` 与 `COMSPEC`（取值不同）；`USERNAME` 取值含路径分隔符；`TERM` 正常；`APPDATA`、`SystemRoot`、`HOME` 在基础环境里指向主体可写的别处；`XDG_CONFIG_HOME` 指向工作树之外主体可写的目录，其中放 `git/config` 且 `core.fsmonitor` 指向哨兵程序，body 为 `git status` | 折叠碰撞的键移除并诊断一次；`USERNAME` 移除、`TERM` 保留；`APPDATA`/`SystemRoot`/`HOME` 是 agentao 从 OS 与主体求出的钉值，不等于基础环境里的值；`XDG_CONFIG_HOME` 不在子进程环境里，哨兵不运行 | — | windows / PR-6 |
| G18-11 | LAUNCH-08 | 翻转后的 `cmd` rung：命令行正文 8191 与 8192 **字符**（`cmd_line_chars`，不含结尾 NUL；都远小于 32767）；单条超过 8191 的环境变量分别取 `PATH`、`HTTPS_PROXY`、一个用户授权键 —— 每次只有这一条超长；**并且每一格都带一份非空环境** | 8191 不因长度被拒（断言非空环境**不计入**命令行上限）；8192 与每一种超长单条都拒绝、不启动 —— 逐条比、不是整块比 | `launch-oversize` / `launch-env-oversize` | windows / PR-6 |
| G18-12 | LAUNCH-08 | **对三套计量函数与 `floor` 的 POSIX 分支直接测**（桩住 `sysconf` 与目标平台）：一条超过 `PAGE_SIZE * 32` 的 envp 字符串、同样长度的 `-c <body>`、两者各取 `上限 − 1` 与 `上限` 边界、一次总量超过 `ARG_MAX` 而每条都不超限；页大小分别桩成 4 KiB 与 16 KiB。**端到端不可达，写明原因：** 今天唯一的 POSIX rung 是政策关闭的 `system_posix`，LAUNCH-08 在它之前就返回了（SPEC-03），所以这一行测的是函数与分支，等 q4 定案让 POSIX 那一级政策开启后再补端到端的一格 | 超限的都拒绝、边界内的都不拒；理由按它是谁分 —— envp 那条 `launch-env-oversize`，`-c <body>` 与总量那次 `launch-oversize`；页大小变了上限跟着变，断言用的是查出来的值、不是 131072 | `launch-env-oversize` / `launch-oversize` | ubuntu / PR-4 |
| G18-15 | LAUNCH-01e、ENV-06 | 四级各跑一次后台调用（`is_background=true`），父环境带 G18-08 那组键 | 后台子进程的环境与同一段 body 的前台调用逐字段相同：`ChildEnv` 的封闭集，那组键一个都不在；后台子进程的起始目录同样是 launcher 所在目录 | — | windows / PR-6 |
| G18-13 | ENV-06、LAUNCH-08 | 判定与启动之间改变基础环境（新增一个透传键、改一个透传键的值） | 请求里的 `env` 是**判定时算的那一个对象**（`plan.decided.child_env`），与长度守卫量的那份逐字段相同；改动不出现在请求里 | — | ubuntu / PR-4 |
| G18-16 | ENV-06 | 用户级 `shell.env_passthrough` 分别给 `["*"]`、`["GIT_*"]`、`["BASH_ENV"]`、`["JAVA_HOME"]`，父环境带 G18-08 那组键 | 前两条整条丢弃并诊断一次 —— 授权的单位是字面键名，一条 `*` 会把整份继承环境放回来；第三条无效（ENV-03 保留键）；只有第四条把该键加回 (2) 并过 `value_ok` | — | ubuntu / PR-4 |
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
| G21-04 | LAUNCH-07 | 第一条语句带可观察副作用的 body；另一格：同一 launcher 启动两次 —— 一次带前奏、一次不带（对照）—— body 报告该级的启动状态快照（PowerShell：偏好变量全集、当前位置、`$?`、`$Error`、语言模式、`$PSDefaultParameterValues`；cmd：`CD`、`ERRORLEVEL`、`PROMPT`、扩展与延迟展开的开关；bash：`$PWD`、`$OLDPWD`、`$?`、`set -o` 全集、`IFS`） | 前者：前奏之后仍产生同样的副作用；后者：两次快照的差集**恰好**是 LAUNCH-07a 为该级列出的那几项，一项不多 | — | windows / PR-6 |
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
| G21-15 | LAUNCH-09 | 先量出解释器启动时探测失败（系统中不存在）的 DLL 名，在工作树里放一个同名 DLL；`Popen(cwd=)` 为 launcher 目录、前奏切到工作树；另给含 `"` 与含 CR/LF 的两个工作目录 | 该 DLL 不在解释器进程的模块列表里；body 里 `Get-Location` 报工作树；工作目录不存在时 body（含 `a & b` 形）一条都不跑、退出码 98，三级各测；含 `"` 或含换行的工作目录在 cmd rung 都拒绝该次调用、不启动 —— 换行会把 `/c` 字符串切开 | 后两者 `hardline:cmd-opaque:launch-cwd` | windows / PR-6 |
| G21-17 | LAUNCH-09 | **逐 rung 的刻画性探针，不预设答案。** 每一级各测三样：前奏之后 body 里报 `Get-Location`（或 `cd`）与原生当前目录（PowerShell 读 `[System.Environment]::CurrentDirectory`，cmd/bash 读 `/proc/self/cwd` 或等价物）；再放一个解释器前奏之后才按需加载的同名 DLL | 记录三样的实测值，写进 LAUNCH-09 与本文 §1 的残留行。**已知的不对称：** PowerShell 的 location 是运行空间状态，与进程的 `[System.Environment]::CurrentDirectory` 不是一回事（§3.22 (f)），所以 `pwsh`/`powershell` 级的原生 cwd 很可能整个进程生命期都停在 launcher 目录，而 `cmd`/`git_bash` 的 `cd` 改的就是原生 cwd —— 探针要分别落定，不能拿一级的结论套另一级 | — | windows / PR-6 |
| G21-16 | NAME-02 | `-NoProfile` 下 `Get-Command -All` 量出的表里 `mkdir`、`more`、`help` 的 kind 为 function；`mkdir x; git status`；一个在表里同时有 function 与外部程序的名字 | 查表命中的是 kind = function 的条目并按其 EFF-08 登记判；遮蔽时按 function 判、不按外部程序判 | 未登记者：解析到 function 条目却无登记 | windows / PR-6 |
| G21-18 | LAUNCH-09 | 与 G21-17 同一支探针的另一半，**同样逐 rung 跑**：一条被放行的可信工具链（`git status`）以什么当前目录启动、会不会加载工作树里的同名 DLL | 记录实测值，**不预置**。子进程的当前目录由**启动它的那个解释器**的行为决定，而各解释器传给子进程的工作目录未必一致（PowerShell 传的是运行空间位置，cmd/bash 传的是自己的当前目录）—— 所以这一半也按 rung 落定，测出来是什么就写回规范 §1、§7.1 与 LAUNCH-09 | — | windows / PR-6 |
| G21-19 | IMG-08、IMG-07 | oracle 桩让 `read_identity` 报出的 `pshome` / `session_config` 与 `resolve_pshome` / `read_config_sources` 读出的**不一致**（各测一次），随后跑 `select_rung` 与一次 `launch()` | 构造阶段就拒绝该 rung 并点名；绝不构造出一个「构造时按 A 验证、spawn 前按 B 重读」的 spec —— 那样 `launch()` 要么每次 `launch-reread`，要么重读的是一个从没验证过的安装根 | — | ubuntu / PR-4 |
| G21-20 | LAUNCH-09e、LAUNCH-09b | `encode_workdir` 逐个吃下四个非 ASCII 单引号（U+2018、U+2019、U+201A、U+201B），外加对抗串 `C:\’; Start-Process calc; Write-Output ‘`、一个含 ASCII `'` 的普通工作目录、以及同样四个字符送进 POSIX 与 cmd 两支 | PowerShell 支：四个各拒一次、对抗串也拒（`launch-cwd`，绝不启动）；普通工作目录照常双写通过；POSIX 与 cmd 两支不受影响（两种词法都不认这四个字符），断言的是「拒绝」而不是「转义成什么」—— 双写能不能转义它们未实测 | `hardline:powershell-opaque:launch-cwd` | ubuntu / PR-4 |
| G23-01 | IMG-05 | 丢进「恰好在机器 PATH 上的用户可写目录」的 `pwsh.exe`，body 会写哨兵文件 | 永不自动选中；哨兵文件不存在（没被启动） | — | windows / PR-6 |
| G23-02 | IMG-05、IMG-01 | 同一个二进制经 `shell.path` 显式点名，仍在用户可写目录 | 拒绝，点名 IMG-01（(b) 免签名，不免位置）；把它放到主体写不了的目录、无签名、经 `shell.path` 点名 ⇒ 被选中 | — | windows / PR-6 |
| G23-03 | IMG-05 | 已知安装位置里没有签名的映像 | 拒 | — | windows / PR-6 |
| G23-04 | IMG-08 | launcher 所在目录不是 `$PSHOME`（shim、符号链接、拷贝） | AllUsers 配置从宿主侧解析出的安装根读；解析不出则拒绝该级 | — | windows / PR-6 |
| G23-05 | IMG-01、IMG-02 | 主体写不了的根下、可信表有条目的 `git.exe`，对子进程 token 做真实 ACL 检查，无签名、不在 allowlist | 放行 | — | windows / PR-6 |
| G23-06 | IMG-01、IMG-06 | 只读的 `D:\tools\vendor\bin\git.exe`，而 `D:\tools` 对主体可写（可重命名） | 不透明 | 映像半：可写的祖先 | windows / PR-6 |
| G23-07 | IMG-06 | 主体写不了的目录里一个指向用户目录的 junction / symlink / app execution alias；及其反向 | 不透明，两个方向都是 | 映像半：链上的 reparse 目标或别名所在目录可写 | windows / PR-6 |
| G23-10 | IMG-06c | oracle 桩对链上某一条路径把 reparse 报成**解析失败**（不是「不是 reparse」）；另一次给一对互指的 junction（A → B → A）；再一次给一条比 `MAX_REPARSE_DEPTH` 更深的链；再一次给一条指向自己**父目录**的可信 junction（`C:\Trusted\alias` → `C:\Trusted`），链上每一问都通过 | 三次都判为不可信、每一次都返回（不递归到栈溢出）；失败那次**不得**被当成「不是 reparse」放过；最后那条**通过** —— 它解析得完、每一问都过，入口集记的是这一趟 reparse 遍历，不是已经查过权限的祖先，否则一条正常的 junction 会被判成环而排除在 launcher 选择与 PATH 过滤之外 | 映像半 | ubuntu / PR-4 |
| G23-08 | IMG-06、IMG-03 | `C:\PROGRA~1\Git\cmd\git.exe`、大小写变体、尾随点、`\\?\` 前缀；`git.exe:ads` | 前四种与规范拼法裁定相同，且 allowlist pin 生效；ADS 不透明 | — | windows / PR-6 |
| G23-09 | IMG-07、LAUNCH-09 | `cmd` 与 `git_bash` 两级：spec 构造后、**`launch()` 重哈希之前**把 launcher 换成另一份内容 | 两级都拒绝启动（`LauncherIdentity` 不只 PowerShell 有）；正常路径下 `cwd` 是该 launcher 所在目录。**与 G24-13 的分界：** 这一格是 agentao 自己发现的，报 `launch-rehash` | `hardline:<dialect>-opaque:launch-rehash` | windows / PR-6 |
| G23-11 | IMG-03a、SPEC-08 | 两份只在 allowlist 的一条 content pin 上不同的配置，其余逐字段相同，各构造一次 spec；再把判定用的那份 spec 的 allowlist 与 `trusted_image` 实际查的那一份对照 | 两份 spec 的 `fingerprint` **不同**；`trusted_image` 查的就是 `spec.allowlist`，判定路径上再没有第二处读得到「当前生效的块」（按 grep 断言：契约里没有 `policy_of`） | — | ubuntu / PR-4 |
| G23-12 | ENV-06g、ENV-06a、IMG-06a | **探针（q14）：** 出厂 Windows 上对 `C:\ProgramData`、`C:\Users\Public` 与每一个系统那一类的钉值目录，按 IMG-06a 的目录判据（`FILE_ADD_FILE`、`FILE_DELETE_CHILD`、`DELETE`、`WRITE_DAC`、`WRITE_OWNER`、所有权）对**子进程 token** 做一次真实 `AccessCheck`（不是 `icacls` 的目视）；同一遍记下 `PUBLIC` 这个键的归类是否成立；域加入与非域各一台 | **已跑（run 33977368667，`scripts/windows_oracle_probe.py`）：** `C:\ProgramData` 与 `C:\Users\Public` 对标准用户都授予 `FILE_ADD_FILE`＋`FILE_ADD_SUBDIRECTORY`、既无 `DELETE` 也无 `FILE_DELETE_CHILD` ⇒ 两者都过不了 IMG-01（前者定了 q14，后者印证 ENV-06g 的归类）；同一遍量出**卷根 `C:\` 也授予 `FILE_ADD_SUBDIRECTORY`**，IMG-06a 因此拆成两张掩码。runner 的 token 是管理员且持六项等同「能替换」的特权 ⇒ 默认身份只测得到拒绝路径，接受路径改用现场建的标准用户 | — | windows / PR-6 |
| G23-14 | IMG-06a、IMG-01 | 一条映像链 `<root>\dir\img.exe`，桩 oracle 的两问分开作答，四组：(a) 只有卷根答得上目标掩码、祖先掩码全假；(b) 只有某个祖先答得上祖先掩码；(c) 只有**装着映像的那个目录**答得上目标掩码；(d) 链头换成目录、其父目录只答得上目标掩码 | (a) 通过、(d) 通过；(b) 与 (c) 拒绝 | — | ubuntu / PR-4 |
| G23-13 | IMG-03b、IMG-03、IMG-05 | allowlist 里一条 `PublisherTrust`，oracle 桩的 `image_signer` 依次答「与它逐字相等」「另一个签名者」「答不出（无签名或链验不过）」；再各跑一遍空 allowlist × 宿主信任存储答真 / 答假；另一遍只有 content pin | 第一种放行，其余两种拒；空 allowlist 时只由宿主信任存储说了算；**往 allowlist 里加一条受信发布者与留空必须产出不同的裁定** —— 否则这个配置取值等于不存在 | — | ubuntu / PR-4 |
| G24-01 | WRAP-01 | `pwsh -NoProfile -Command "git status"` | 不透明 | 嵌套解释器启动 | ubuntu / PR-2 |
| G24-02 | WRAP-01 | `pwsh -Command "Remove-Item -Recurse -Force C:\"` | 不透明 | 重新进入后的危险表命中（§3.6） | ubuntu / PR-2 |
| G24-03 | WRAP-01 | `cmd /c git status`；`bash -c 'git status'` | 不透明 | 嵌套解释器启动 | ubuntu / PR-2 |
| G24-04 | SPEC-04、SPEC-05 | `filesystem_is_local` 为假且执行器无 oracle；整个省掉该字段的 spec | 每一个需要映像的命令词不透明 | — | ubuntu / PR-4 |
| G24-05 | SPEC-04 | 同一段 body 在本机 spec 下 | 原来的裁定 | — | ubuntu / PR-4 |
| G24-06 | SPEC-05、IMG-06 | 提供了 oracle：在目标 PATH 上解析得到、在地板 PATH 上解析不到的裸词 | 放行 | — | ubuntu / PR-4 |
| G24-07 | SPEC-05、IMG-06 | 反过来：地板 PATH 上有、目标 PATH 上没有 | 不透明 | 映像半 | ubuntu / PR-4 |
| G24-08 | WRAP-05 | `Start-Job { … }`，本机与非本机 | 不透明，两边都是 | 另起进程 | ubuntu / PR-2 |
| G24-09 | LAUNCH-01 | 一个 fake executor | 逐字段断言启动请求：判别体（`argv` 或 `application_name` + `command_line`）、`env`（完整映射）、`execution_subject`、`attested_images`、`spec_fingerprint` —— 不只是「解析发生过」；`cwd`/`workdir` 的语义属 PR-4，见 G24-12 | — | ubuntu / PR-1 |
| G24-10 | LAUNCH-01、SPEC-05、ENV-06 | 非本机执行器，目标 PATH 与基础环境都与地板机器不同 | `env` 从 oracle 答出的目标基础环境算出（ENV-06 过滤 + ENV-01 的目标 PATH + `target_pinned_env` 的钉值），执行器原样设定；请求里没有地板机器的任何值。系统根 / `HOME` / `TEMP` 三项的逐项断言在 G24-13 与 G24-14 | — | ubuntu / PR-4 |
| G24-11 | SPEC-05、IMG-06 | 非本机执行器的 oracle 缺 **`IdentityOracle` 的任意一个方法**（逐个缺一次，用接口的方法清单参数化，新增方法自动进用例）—— `canonicalize`、`subject_can_replace`、`resolve_reparse`、`resolves_on_target`、`publisher_trusted`、`content_hash`、`target_base_env`、`target_path_entries`、`target_project_root`、`target_platform`、`target_filesystem_is_local`、`target_pinned_env`、`image_signer`、`resolve_image`、`discover`、`read_identity`、`resolve_pshome`、`read_config_sources`、`preflight`；齐全的 oracle | 缺者：该 rung 未认证 —— 每个需要映像的词（含 `Get-Date`）不透明，政策关闭的 rung 照旧；齐全者：spec 的 rung、身份与预检结果都来自 oracle，`launch()` 的重哈希与重读也经 oracle，本机文件系统一次都不读 | 缺者：映像半（launcher 未认证） | ubuntu / PR-4 |
| G24-13 | LAUNCH-01、SPEC-05 | **请求交给执行器之后、spawn 之前**替换 launcher（`launch()` 的重哈希已经过了）；另一次替换 argv 里以路径点名的一个外部工具；再一次给出证明集里根本没有条目的直接目标 | 三次都被**执行器的复核**拒绝，body 一字节不跑；执行器不得「照办命令行」。**与 G23-09 的分界：** 替换发生在重哈希之后，只有执行器能发现，报 `launch-attest` | `hardline:<dialect>-opaque:launch-attest` | ubuntu / PR-4 |
| G24-14 | LAUNCH-01、ENV-06、SPEC-05 | 非本机执行器：oracle 桩答出的目标系统根、`HOME`、`TEMP` 与地板机器的三者**都不同**；再让 `target_pinned_env` 返回 `None` | 前者：请求的 `env` 里这三项（POSIX 目标上另加 `TMPDIR`）全部是目标值，地板机器的值一个都不出现；后者：该 rung 未认证，每个需要映像的词不透明 | 后者：映像半（launcher 未认证） | ubuntu / PR-4 |
| G24-12 | LAUNCH-09、LAUNCH-01 | PR-4 之后的每一级，工作目录含空格与非 ASCII | `cwd` 等于 launcher 所在目录，`workdir` 等于规范化后的工作目录绝对路径（**不是**方言编码形态），编码后的 `<W>` 只出现在 `argv` / `command_line` 里 | — | ubuntu / PR-4 |
| G24-15 | LAUNCH-01、SPEC-03 | 政策关闭的两级各发一次 `LegacyLaunch`；同一个 fake executor | 执行器不对 `LegacyLaunch` 做证明复核（没有证明集可比），也不拒绝它；`ChildEnv` 一次都没被调用 | — | ubuntu / PR-1 |
| G24-16 | ENV-06、SPEC-07 | 构造 `ShellSpec` 时不给 `env_passthrough` / `target_platform`；再给一份 | 缺席时按默认（空授权集、oracle 答出的平台）构造成功，并进 `fingerprint` 投影；两份只差 `env_passthrough` 的 spec 指纹不同 | — | ubuntu / PR-4 |
| G24-17 | LAUNCH-01、SPEC-03 | 对 `LegacyLaunch` 读 `workdir` / `execution_subject` / `attested_images`；对 `AttestedLaunch` 读 `command` | 四次都是类型错误（两组字段不共用）—— 断言的是**类型**，不是运行时值为 `None` | — | ubuntu / PR-1 |
| G24-18 | LAUNCH-08 | `pwsh` rung：命令行正文恰好 32766 个 UTF-16 code unit，配一份**很大的**非空环境（远超 32767 字符总量） | 不因长度被拒 —— Windows 的 32767 只约束命令行，环境不计入（环境变量各自另有上限） | — | windows / PR-6 |
| G24-19 | LAUNCH-01e | 同一段 body 分别以 `is_background=false` 与 `true` 跑一次（政策开启的一级与政策关闭的一级各一遍，fake executor）；再让后台那次的 launcher 在重哈希之前被换掉 | 两次收到的是**同一个变体**的 `LaunchRequest` 且逐字段相同（政策开启：判别体、`env`、`execution_subject`、`attested_images`、`spec_fingerprint`；政策关闭：`LegacyLaunch` 的四个字段）；后台那次同样经 `launch()` 做 spec 核对、重哈希、重读与复核，被换掉的 launcher 在后台路径上同样拒绝；两条路径上都没有第二处自己拼的 `Popen(shell=True, executable=resolve_shell_executable())` | 后者 `hardline:<dialect>-opaque:launch-rehash` | ubuntu / PR-1 |
| G24-20 | SPEC-04a | oracle 桩的 `target_filesystem_is_local()` 分别答真、答假、**答 `None`（答不出）**，各跑一次完整的 `select_rung`（政策开启的一级与政策关闭的一级各一遍） | 答真答假的四份 spec 的 `filesystem_is_local` 都等于桩答出的值，**答 `None` 的两份读作 `false` 且不抛异常** —— **本机那两份由规范构造器产出，不是靠字段默认值**；答真与答假的两份指纹不同（该字段在投影里）；全程只调用一次 | — | ubuntu / PR-4 |
| G24-21 | LAUNCH-01、IMG-02 | 同一 `canonical_path` 在证明集里出现两条：一条来自 `spec.launcher.image`（构造时冻下），一条来自分析期 `resolve()`，两者的 `content_identity` 或 `FsId` 不同 | 该次调用不透明、不启动 —— 冲突不得靠「保留首见」静默压掉，那正是执行器复核（LAUNCH-01d）要抓的情形 | 映像半 | ubuntu / PR-4 |
| G24-22 | ENV-06f、ENV-06b | oracle 桩在 Windows 目标上把 `PinnedEnv` 的必答字段逐个答成 `None`（用字段清单参数化，新增字段自动进用例）；另一遍只把 WOW64 那三个（`ProgramFiles(x86)`、`ProgramW6432`、`CommonProgramFiles(x86)`）答成 `None`；再在 POSIX 目标上把 `TMPDIR` 答成 `None` | 必答字段缺任一个 ⇒ 该 rung 未认证（`Exhausted("ENV-06: pinned env")`），**绝不是**产出一份少了那个键的子进程环境；只缺 WOW64 那三个 ⇒ 照常构造，子进程环境里没有那三个键 | — | ubuntu / PR-4 |
| G24-23 | LAUNCH-08e、LAUNCH-08、LAUNCH-09b | body 含一个落单的 `U+D800`（工具参数走 JSON 的 `\ud800` 转义），再各来一次：子进程环境的某个值含它、工作目录含它；对照组是一个非 BMP 字符（成对的代理项，如 U+1F600） | 前三种都返回 DENY、理由 `lone-surrogate`（工作目录那次可以是 `launch-cwd`），**绝不抛 `UnicodeEncodeError`** —— 断言的是「返回一个裁定」，不是「抛什么异常」；对照组照常计量通过 | `hardline:<方言>-opaque:lone-surrogate` | ubuntu / PR-4 |
| G25-01 | IMG-01、LADDER-03 | agentao 以管理员或容器 `root` 运行；`Copy-Item .\evil.exe 'C:\Program Files\Git\cmd\git.exe'; git status` | 该级被拒绝；任何一步都不得走到「放行」 | 可信集为空 | windows / PR-6 |
| G25-02 | IMG-01 | 非特权时同一段 body | 放行；拷贝在 OS 层失败，跑起来的是可信的 `git` | — | windows / PR-6 |
| G25-03 | LADDER-03 | 每一级都被拒绝 | 每次 shell 调用返回 reason，工具仍注册着，不退回 `%COMSPEC% /c`；provider 暴露 `Exhausted(reason)`，检查先于方言与 rung —— 一个同时报 `UNKNOWN` 方言的 provider 仍得到这一条 reason | `hardline:no-trusted-rung-opaque:<原因>` | ubuntu / PR-4 |
| G25-05 | LADDER-03、IMG-05 | 显式 `shell.path` 被 IMG-05 (b) 拒（用户可写目录），而机器上有可自动选中的 `pwsh` | 不落到 `auto`：provider 处于 `Exhausted`，每次调用 DENY | `hardline:no-trusted-rung-opaque:<原因>` | ubuntu / PR-4 |
| G25-04 | IMG-01 | ubuntu：注入 oracle 桩、rung = `pwsh` 的 spec，每个候选根都答「能写」 | 该级被拒绝；阶梯走空 ⇒ LADDER-03 | `hardline:no-trusted-rung-opaque` | ubuntu / PR-4 |
| G25-06 | IMG-05a、IMG-04 | 显式 `shell.path` 指向项目根内一个**只读**检出里仓库自带的解释器（主体换不掉它、也换不掉它的任何祖先，可信根链因此答「可信」）；同一个二进制移到项目根之外再点名一次；另加 `target_project_root()` 答不出的一遍 | 前者拒绝、点名 IMG-05a（位置，不是可信根链）；中者被选中；后者也拒绝（答不出 ⇒ 未认证） | — | ubuntu / PR-4 |
| G25-07 | CFG-02c、CFG-02a、LADDER-05 | POSIX 目标上显式 `shell.path` 指向 `/bin/bash`，再指向 `/bin/zsh`，两者都在主体写不了的目录、都在项目根之外；另一遍在政策开启的一级上构造 spec | 两份 spec 的 `rung` 都是 `system_posix`、`launcher` 与 `pinned_env` 都是 `None`，但 `explicit_shell` 各是自己的路径、`fingerprint` 不同，`LegacyLaunch.command` 用的是点名的那个而不是 `resolve_shell_executable()` 求出的；政策开启那一遍 `explicit_shell` 为 `None`，非 `None` 时 `validate()` 拒绝 | — | ubuntu / PR-4 |
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
**`rebinds_caller` 的传播只经字面串重新进入（回修于矩阵之后）：** `source ./safe.sh; git status` 判
**不透明**，理由是 `source` 自身的文件目标，`safe.sh` 的内容一个字节都不读 —— 内容只有 `true` 的另一份
判得一样；`bash ./safe.sh; git status` 因*另一个*理由（一个未降级的子进程）不透明；而传播发生在
`iex 'Set-Alias git C:\evil.exe'; git status`（**不透明**，退出态并入 `iex` 之后）与 `iex 'Get-Date'; git status`
（**放行**）这一对上，于是传播不是一刀切的拒绝。**规则 0 拿 codex 的语料测，而不是我自己挑的例子：**
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
到了什么，它必须解析到可信的 `git.exe`，绝不能是那个模块。前奏被断言不改动 body：一段第一条语句
有可观察副作用的 body，在前奏之后仍产生同样的副作用；而前奏**自己**改变的启动状态，靠带前奏与不带
前奏两次启动的状态快照求差集，差集恰好是 LAUNCH-07a 为该级列出的那几项。**任一作用域的 `powershell.config.json`
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