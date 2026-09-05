# PowerShell 支持 —— 评审记录（修订史、发现、方法规则）

> 本文件收**历史与方法**：四十轮评审的修订表、每轮发现的类别，以及这些轮次产出的二十七条方法规则。
> 它不定义任何规则；「规则现在在」一栏指向规范文件里的规则 ID 族。规范只描述**现在必须怎样**，
> 「rev N 曾经怎样」只在这里。拆分之前的单体文档冻结于 rev 24（commit `e01293f`，
> `docs/design/powershell-support-plan.zh.md`），是这张表每一行的原始上下文。

**日期：** 2026-09-04
**文件集：** `docs/design/powershell-support-spec.zh.md`（规范）·
`docs/design/powershell-support-implementation.zh.md`（PR 阶梯）·
`docs/design/powershell-support-gates.zh.md`（门槛矩阵）·
`docs/reference/powershell-support-evidence.zh.md`（证据）·
`docs/design/powershell-support-review-log.zh.md`（评审记录）·
`docs/design/subagent-runtime-safety-plan.zh.md`（原 PR-0）。

## 1. 修订历史

**rev 25（2026-09-03）—— 拆分。** 单体文档拆成上面六个文件；每条规则分配稳定 ID，只在规范里定义一次，
其余文件只引用 ID；门槛改成追踪矩阵；PR-0 移出为独立计划。本次拆分**不改任何规则的语义**，
第 3 步的映射表（下）保证 rev 24 的每一条规则都落到恰好一个 ID 上。rev 24 的六个 D 节与 rev 25 的规则
族对应如下：D1 → `TOOL`；D2 → `SPEC`、`LAUNCH`（以及移入子代理计划的 `SUB`、`MCP`、`ENG`）；D3 → `TOK`；
D4 → `IMG`、`ENV`、`LADDER`、`LAUNCH`；D5 → `LOWER`、`WRAP`、`NAME`、`EFF`、`IMG`；D6 → `CFG`、`LADDER`；
D7 → `CMD`。

规则本身在正文里；这张表存在的意义是让后来的编辑者认得出「又犯了同一类」，而本文 §2 收着这些轮次产出的
方法教训。每一行只点明错的**类别**，以及被改正后的规则现在在哪。**「发现」一栏记的是那一轮的发现数，
而 rev 19 是一遍编辑性整理、rev 32 与 rev 42 是结构修订、rev 45 是 PR-4 的实施轮，都不是一轮评审** —— 这就是「rev 2–45 那四十四行、合计三百一十九条」与「表头写
四十轮评审、三百零一条发现」之间差别的全部（rev 25 的十五条、rev 26 的二十四条、rev 27 的十二条、rev 28 的十条、rev 29 的十条、rev 30 的九条、rev 31 的八条、rev 33 的六条、rev 34 的十五条、rev 35 的九条、rev 36 的六条、rev 37 的五条、rev 38 的两条、rev 39 的两条、rev 40 的两条、rev 41 的三条、rev 43 的十七条、rev 44 的三条都在表里）。谁要重算这两个数，需要的是这条约定，不只是算术；而
本文 §2 自己的那个数是从表头推出来的，不是另外手记一份。**每一行记的是那一轮*提出*的条数，不是它折入的
条数：** rev 23 处理了第 20、21 轮提出的六条，那六条计在那两行里，不在它自己那一行里再计一次 ——
「发现晚两轮才落地」是常态，否则这张表会重复计数。

| rev | 发现 | 错在哪一类 | 规则现在在 |
|---|---|---|---|
| 45 | 8 P1、7 P2 | **PR-4 与 PR-6 的实施轮，不是一轮评审 —— 十五条都来自把规则写成代码时它自己不闭合。** (1) `attested_spec` 在 `identity_measured` 为假时拒掉整级，而 IMG-07 说的是「该 rung 的**裸词**全部不透明」，NAME-02 早为它的同族条件（封闭环境未确立）写下同一种降级 —— 根因是**两张表在散文里同名「实测表」**：一张是 `derive_rung` 读的 edition 表（缺 ⇒ 拒绝该来源，CFG-02a），一张是 NAME-02 的实测命令表（缺 ⇒ 裸词不透明而 rung 仍在）；一张的缺席被套上了另一张的后果，代价是为一张**名字**表把 Windows 降到 `cmd` 那级更粗的地板上。(2) `ancestors_to_volume_root`／`trusted_root_chain`／`trusted_image` 按目标平台走祖先链与包含判定，签名却只收 oracle —— 现问 `target_platform()` 会撞上 G18-14 断言的「全程只调用一次」。(3) `prelude_for(identity)` 拼不出 LAUNCH-05：`<W>` 逐次调用、identity 逐 rung，签名没地方放它。(4) 常用工具链只登记在 POSIX 表里，而 G04-34 要求 `git status` 在三级都放行 —— 外部程序条目改为定义一次、按方言实例化三份（三份手维护的副本会无声漂移）。(5) 指纹的规范编码没有 frozenset 一格，而 `PinnedEnv.unknown_keys` 在投影里 —— **任何**政策开启的 spec 一构造就抛 `TypeError`。(6) `is_abs_file` 收下带尾分隔符的路径。(7) 给三个函数加 `target` 之后设计集检查与 `mypy --strict` 都绿，而契约的**行为测试**七条红 —— 它们的桩按旧签名写；rev 42 建那份测试挡的就是这一族。(8) IMG-09 的预检用**同一段**前奏，而那次调用传的是空 `<W>` —— `Set-Location -LiteralPath ''` 失败、子进程退出 98，于是每个健康的解释器都读回「封闭环境未确立」、NAME-02 整条失效；桩 oracle 恒答 `True`，只能靠读代码发现。(9) **整张 Windows 危险表只有 cmd 地板读得到** —— 它定义在 `_cmd.py`，而 `scan_powershell` 一条危险类都不跑，于是政策开启的 PowerShell rung 上 `Format-Volume`、`Clear-Disk`、`vssadmin delete shadows` 全部放行；而表里本来就有两条 PowerShell 拼法，这件事本身就说明它不是 cmd 的表（**一个类拒的是什么，是平台的性质，不是语法的性质**）。(10) `classify_body` 从**抹掉引号的视图**里取命令词，于是它恢复出来的嵌套 body 是假的 —— `bash -c "rm -rf /"` 恢复出一个孤零零的 `"`；只要那份 body 唯一的用途是拼进理由字符串就看不出来。(11) **WRAP-06 九轮以来没有实现过**：`nested_launch` 恢复 body 的唯一理由就是「危险的嵌套 body 要按它自己的理由被拒」，而没有任何一处读它。(12) **上一轮外部评审的头条修复没有任何测试** —— 我用探针逐条核过、也如实报告了，而探针只活在对话记录里；全仓 grep 零命中，这一轮重构本可以把它悄悄改回去（方法规则 27）。(13) **冻结记录把 `enable_hardline=False` 变成空操作**（本轮自伤）：判定链改成「planner 先建记录、引擎读它」之后，建记录那一步直接调地板、绕过了引擎的开关，于是一个明写「我自己负政策责任」的宿主照样被记录里的 DENY 拦在启动前 —— 修法是让 planner 从引擎读同一个标志，关掉时**只**压裁定、不压 LAUNCH-08／09 的守卫（「这条命令行拼不出来」不是政策问题）。(14) `verify_attested_launch` 把**答不出的文件系统身份**当成匹配（两个 `None` 相等），于是这道检查报出干净结果却什么都没证明 —— 而 `local_filesystem_identity` 自己的 docstring 明写「每一个调用者都把答不出当拒绝」，那句话在这一处是假的；`st_ino == 0` 恰在本阶梯瞄准的平台上可达。(15) **自伤一条，被自己的测试抓回：** `allowlist_entry_for` 一度按目标平台宽松查找而 `HashPin.matches` 仍逐字相等确认 —— 同一对路径两种比法，一条大小写不同的 pin 会被找到然后匹配失败，把「没被 pin」变成「不可信」（方法规则 26）。代价明说：没有东西规范化用户写的 pin ⇒ q15 | `IMG-07`、`CFG-02a`、`NAME-02`；规范 §7.3 q15；契约模块；实现文件 §5.4 |
| 44 | 3 P2 | **rev 43 的反向评审（第十七轮完整安全评审）—— 三条都是「某个状态没有落脚处」。** (1) `typecheck_contract` 在 mypy 退出码非零、两条流都空时得出空失败列表，于是**设计集检查与 `test_live_contract_typechecks` 都报通过，而类型检查根本没跑完** —— 真会这样退出的正是 OOM 被 `SIGKILL`、插件崩溃、段错误这几种（方法规则 25）。(2) SPEC-04a 明写「缺席与答不出都读作 `false`」，而 `target_filesystem_is_local() -> bool` 只装得下「缺席」那一种：一个答得上方法名却答不出结果的 oracle 只剩抛异常一条路，而那个异常会在政策关闭的两级都还没选出来之前穿出 `select_rung` —— 正是 rev 39 那条「答不出不该让阶梯走空」的第二种形态。签名改成 `-> bool \| None`，`mypy --strict` 自己就拦住旧写法；G24-20 加第三种桩答。(3) `gates_for()` 的两条路由里，母子继承只写在矩阵行那一条上，规则自己那一行里的 `Gnn` 没有 —— 于是覆盖与否取决于母规则**碰巧是哪一种门槛**，SUB / MCP / ENG 三族全靠 bullet 定义，给它们任何一条加子规则都会被判无门槛。**这是同一个形状第五次出现，而这次洞在单一定义点**里面**（方法规则 12 补一句：单一定义点不等于单一应用点）。 | `SPEC-04a`；`scripts/check_design_set.py`、`tests/test_design_set.py`、`tests/test_powershell_contracts.py` |
| 43 | 15 外部 + 2 自查 | **外部 `/code-review --fix` 的十五条，加上自查这批修复时找到的一条（第十六轮完整安全评审）—— 两条是评审自己没敢下的设计判断，其中一条它给的修法是反的。** (1) `derive_rung` 的固定表里 `cmd` 是**唯一不读目标平台的一行**：POSIX 目标上一个显式 `dialect: cmd` 会一路过完 IMG-01、IMG-05a 与 IMG-07，导出**政策开启**的 `cmd` spec，而 `floor()` 的三套计量没有一套适用它，末支按 `launch-oversize` 拒绝 —— 一个平台错误报成长度理由，且这份 spec 本就不该构造得出来。这与 rev 34 的头条（`legacy_cmd` 无人构造）是同一个洞长在枚举取值上，**第二次** → CFG-02a 补目标平台限定、G14-03 加两遍。(2) `IdentityOracle.canonicalize` 全契约零调用点，评审建议删掉；**删是反的** —— 它的调用点是 `filtered_path_entries` 那个 `Unspecified` 接缝，而 `path_within()` 的义务文本明写收「两条**已规范化**的路径」，PATH 条目却是环境里的原始字符串：不归一，`..`、短名与符号链接就能绕开「工作目录与项目根之内」那两道包含判定 → 接缝里点名调用者、ENV-01 补一句、G04-04 加一条经 `..` 绕回同一个可写目录的条目（方法规则 24）。(3) 机检六条：`--list` 少了 `coverage` 的第二条路由（15 条 SUB / MCP / ENG 规则显示无门槛，而 `check_set` 认为有）、env 检查对短键必真、**每一个** docstring 都算锚点、门槛 ID 没查重、why 格按列号取而两个定义文件版式不同、三个 `subprocess.run` 无 `timeout` 也无 stdin。(4) **第十六条是自查上面那条 env 修复时找到的**：它把子串换成整词**加全大小写折叠**，而契约里有 47 处小写 `path` 参数名 —— 同一个空洞下移一层，`Path` 照样能被一个从不提这个变量的模块答掉 → 只认两种拼法（原样与全大写），Windows 折叠的是环境键名，不是任意标识符（方法规则 23）。**第十七条同样来自自查**：`## 3. 已否决的备选` 这一行在工作树里重复了两遍而机检没看见 —— `duplicated_phrase` 要 30 个字符，这行标题不到 → 补一条「相邻两行完全相同」的结构检查，全套扫过没有第二处。 | `CFG-02a`、`ENV-01`；`scripts/check_design_set.py`、`tests/test_design_set.py`、`tests/test_powershell_contracts.py` |
| 42 | 结构（0） | **结构修订，不是一轮评审；无语义偏离。** 十五轮评审、二百八十一条发现之后回看，**约 10 条由上一轮的修复引入**（下文 §1.18 的两行表逐条列出：机检 6 条、契约模块 4 条），而它们分成两族：一族是「一条规则有几个消费点，只改了一个」（连着三轮，直到 rev 38 把判据提成共用函数才止住）；另一族全在契约模块里，**是上一轮的修复被悄悄撤销** —— `select_rung` 两次、`PinnedEnv.shapes_ok` 一次、`decide` 一次，而这四个函数一条测试都没有。整份契约有 46 个带实体的函数、25 个 `raise Unspecified` 的接缝、22 个 `...` 的 Protocol 方法（后两类都只有签名），此前跑在它上面的只有 `mypy --strict`（查类型，不查行为）；每一轮我都在临时脚本里手跑一遍用例验证修复，然后把脚本丢掉。本次把那些用例落成 `tests/test_powershell_contracts.py`（61 条，全套 115 条绿）：只测有实体的函数，接缝一律打桩、绝不断言，每条点名它钉的规则 ID。**12 处修复逐个改回旧写法验证会红**（含 EFF-07 的 caller_scope、EFF-06 的谓词位、SPEC-08c 的作废、IMG-06c 的遍历集、LAUNCH-09e 的引号族、LAUNCH-08e 的落单代理项、ENV-06f 的必答项、ENV-06g 的 `PUBLIC`、IMG-03b 的发布者、CFG-02c 的显式解释器、IMG-05a 的项目根、SPEC-04a 的 locality 默认）。它挡的是自伤那一族，**挡不住新缺陷** —— 排印引号注入与死配置那两条来自评审者想到了没人想过的用例，回归套件发明不出它们（方法规则 16 补一句）。 | `tests/test_powershell_contracts.py`；契约模块 docstring |
| 41 | 3 P2 | **rev 40 的反向评审（第十五轮完整安全评审）—— 一条死配置、一条误报、一条异常代替裁定。** (1) `PublisherTrust` **在整个契约里没有消费点**：`allowlist_entry_for` 只挑 content pin，`publisher_trusted(path)` 既收不到 allowlist 也收不到签名者 —— 往 allowlist 里加一个受信发布者与留空产出**完全相同**的裁定，而 IMG-03 把它写成 allowlist 的两种形式之一（方法规则 18 的又一例，这次死的是一个**配置取值**）→ 新增 oracle 一问 `image_signer`（链验不过即答不出）、IMG-03b 定消费点与判据、G23-13 断言「加与不加必须不同」。(2) `trusted_root_chain` 把「已查过权限的祖先」与「这一趟 reparse 遍历的入口」混成同一个集合，于是一条指向自己**父目录**的可信 junction（`C:\Trusted\alias` → `C:\Trusted`）被判成环拒掉 —— 它解析得完、每一问都过，却被排除在 launcher 选择与 PATH 过滤之外 → 只记本趟入口（实测：该条通过，互指的一对仍拒）。(3) 工具参数走 JSON，一个 `\ud800` 转义解码后原样留在字符串里，而三套计量都要先编码 —— `floor()` 在**任何分析之前**抛 `UnicodeEncodeError`。**一个异常不是 DENY 通道上的裁定**：它绕过理由词表、绕过 TOOL-03 的「地板的 DENY 不可被规则遮蔽」，在 ACP 上还可能变成一次可重试的工具错误 → LAUNCH-08e、G24-23（方法规则 22）。 | `IMG-03b`、`IMG-06c`、`LAUNCH-08e` |
| 40 | 2 P2 | **rev 39 的反向评审（第十四轮完整安全评审）。** 第一条：`PUBLIC`（`C:\Users\Public`）被列进「系统那一类，须过 IMG-01」，而它是**共享的用户数据目录、设计上人人可写**，本规范没有一处从它加载或读配置；IMG-06a 的目录判据含 `FILE_ADD_FILE`，于是它一条就让 `attested_spec` 拒掉**每一个**政策开启的 rung —— 翻转之后每次 shell 调用 DENY，一个凑数的环境键把整条阶梯关掉。根因是两类之间**从来没写过成员判据**，`PUBLIC` 是按名字像不像系统目录归的类（方法规则 15 的对偶，作用在划分上）→ ENV-06g 写下判据（「有没有规则依赖这个目录的内容」）并把 `PUBLIC` 归到 profile 那一类。**同一个判据把 `C:\ProgramData` 留在系统那一类（工具链确实从它读配置），而它的出厂 ACL 会不会同样让每一级被拒 —— 不知道，也不猜**：新增 q14 与探针 G23-12，PR-4 之前定（方法规则 9：不预设探针的答案）。第二条是我自己 rev 38 那处修复的越界：为表达「改了子规则等于动了母规则的门槛」，我把母 ID **并进了 `touched` 集合**，于是母规则的**其它子规则**也一并显示为已改 —— 只改了一条子规则的那次编辑里，一个只点名它某个**兄弟**的门槛因此丢掉了「门槛改了、规则没动」的告警。**关系要写成谓词，不能靠污染集合本身**（方法规则 12 补一句）。 | `ENV-06g`、`ENV-06a`；`scripts/check_design_set.py`、`tests/test_design_set.py`；规范 §7.3 q14 |
| 39 | 1 P1、1 P2 | **rev 38 的反向评审（第十三轮完整安全评审）—— 第一条是十三轮里第一次打在 agentao 自己生成的文本上。** P1：`<W>` 在 PowerShell 支只把 ASCII `'` 双写，而 PowerShell 的词法认**五个**单引号定界符 —— `‘` `’` `‚` `‛` 与 `'`；一个名字里带 `’` 的工作目录（`C:\’; Start-Process calc; Write-Output ‘`）闭合那条 `Set-Location -LiteralPath` 字面量，把其后文本接进**前奏**。地板一步都拦不住它：`analyse_body` 扫的是 body，前奏是 agentao 自己拼的，十三轮评审、二十条方法规则、二百多条发现全部盯着「不可信输入穿过判定」，而这一条是**可信路径自己生成的文本**→ LAUNCH-09e：另外四个一律拒绝该次调用（能否靠双写转义未实测，取拒绝那一侧），G21-20；方法规则 21。P2：rev 37 修「完整性闸挡住政策关闭的两级」时，把 `target_filesystem_is_local` 也算进了选级的必答项，而 SPEC-04a 明写它缺席与答不出都读作 `false` —— 一个只缺这一个方法的默认 POSIX 执行器于是走空、每次调用 DENY，正是同一条规则的例外子句、同一个受害者、**同一处修复引入的第二版**（方法规则 20 第三次记账）→ `SELECTION_METHODS` 只留 `target_platform`，locality 走 `target_is_local()` 的 `false` 默认。 | `LAUNCH-09e`、`SPEC-04a`、`SPEC-05c` |
| 38 | 2 P2 | **rev 37 的反向评审（第十二轮完整安全评审）—— 两条，一条是「校验过的输入被丢掉」，一条是同一处镜像不全第三次复发。** POSIX 目标上一个显式 `shell.path` 会先过 IMG-01 的可信根链、IMG-05a 的位置检查与IMG-07 的身份读取，然后 `derive_rung(posix, POSIX)` 导出 `system_posix` —— 政策关闭，`legacy_spec()` 收不到路径也收不到身份，于是 `/bin/bash` 与 `/bin/zsh` 产出同一份 spec、同一个指纹，`launch()` 用今天求出的那个解释器跑，用户点名的那个被静默丢弃，而 CFG-02 明写「高来源提供整份 spec」→ CFG-02c：`explicit_shell` 随 spec 冻结并进指纹，`today_command()` 收它，`validate()` 核「政策开启 ⇒ 恒 None」（G25-07）。第二条：评审包的 `gates_naming` 只看矩阵行，而 `coverage` 还接受**规则自己那一行里写的 `Gnn`** —— SUB / MCP / ENG 的门槛正是 bullet 定义而不是矩阵行，于是改一条 SUB-02 通过全部检查、评审包却报 `无门槛`（实测三条 SUB 规则全中）。这与 rev 36 的四条 P2、rev 37 的锚点标记是同一个失效方式，**连着三轮**，所以这一轮不只补条件：把 `gates_for()` 与 `anchored()` 提成单一定义点，`check_set` 与评审包都调它们，两份评审包输出逐字节比对确认重构无行为变化（方法规则 12 补一句：谓词也只有一个定义点）。 | `CFG-02c`；`scripts/check_design_set.py`、`tests/test_design_set.py` |
| 37 | 4 P2、1 P3 | **rev 36 的反向评审（第十一轮完整安全评审）—— 五条里三条是上一轮刚写下的方法规则，没有拿去审写它时正在改的那段代码。** 最重的一条是**我自己修出来的回归**：为了兑现 SPEC-05c 的「缺任一方法即拒绝」，rev 36 把 `oracle_complete` 放在 `select_rung` 进门处，而 SPEC-05c 自己的末句是「**只有政策关闭的 rung 照旧运行**」——一个缺 `preflight` 的默认执行器于是让阶梯走空，LADDER-03 再把走空变成每次 shell 调用 DENY，正是 rev 34 那条 P0 的受害者、同一个位置第二次 → 选级只要 `SELECTION_METHODS` 两问，完整性挪到两个**政策开启**的入口（方法规则 20）。`PinnedEnv` 的 `home` / `temp` / `tmp` 声明成非 `Optional` 就没进 rev 36 新写的必答清单，而 `dirs` 那一行明写 `v is None or …` ——方法规则 19 是上一轮写的，写它的那次编辑就在同一个函数里（方法规则 19 因此补一句：非 `Optional` 的注解同样强制不了什么）。评审包的「契约里无锚点」标记只补了`check_set` 三个条件里的一个（子锚点算母锚点），漏掉 `anchor_definer` 作用域与 `anchor_exempt`——实测：拿 `subagent-runtime-safety-plan.zh.md` 建档那一版当基线，SUB-01 至 SUB-03 三条全被误报，而 `check_set` 一条都不要求。另两条：`policy_of(spec)` 是 allowlist 的**隐式来源** —— 两份只在一条 pin 上不同的配置产出同一个指纹，SPEC-08 于是看不出配置在判定与启动之间换过 → allowlist 冻进 spec 并进指纹（IMG-03a、G23-11），`policy_of` 删除；`git show` 只写 `text=True`，按宿主 locale 解码，Windows 上 cp1252 与 GBK 都读不了这些中文文档 → 三处 `subprocess.run` 显式 utf-8，并补一条按源码扫全部调用点的机检。 | `IMG-03a`、`ENV-06f`、`SPEC-05c`；`scripts/check_design_set.py`、`tests/test_design_set.py` |
| 36 | 2 P1、4 P2 | **rev 35 的反向评审（第十轮完整安全评审）—— 一半是「完整性没有一处在运行期真的去数」，一半是上一轮自己修的那条关系规则只改了一个消费点。** P1 两条：`PinnedEnv.shapes_ok` 查形态、查「平台专属字段在另一平台为 `None`」，却不查**在场** —— Windows 目标上 `system_root=None` 一路通过，`attested_spec` 接受这份记录，`child_env` 对 `None` 的处理是「这个键不出现」，于是交出去的是一份没有 `SystemRoot`、没人验证过的子进程环境，而 ENV-06a 的原话正是「少一个系统根的环境不是『更安全的环境』」→ ENV-06f + G24-22（WOW64 那三个键除外：32 位 Windows 上不存在，缺席是平台事实）。SPEC-05c 写的是「oracle 缺席**或缺任一方法** ⇒ 该 rung 未认证」，rev 35 只实现了前半 —— `Protocol` 是静态的，执行器递进来的对象少一个 `content_hash` 在类型检查里看不见，签名的 launcher 照样被选中、`decide()` 照样放行，到 `launch()` 的重哈希才抛 `AttributeError`，而异常不是 DENY 通道上的裁定 → `ORACLE_METHODS` + `oracle_complete()`，构造与判定两处各答一次（G24-11 早就按这份方法清单参数化）。P2 四条**是同一条**：rev 35 把「子规则可以靠母规则」这条关系在 `coverage` 与 `gates_naming` 上改对了，却没去看同一份脚本里另外三处消费同一关系的地方 —— 陈旧豁免检查、评审包的「契约里无锚点」标记、以及「门槛改了、它点名的规则一条没动」的判据，全都只认精确 ID，于是主检查放行而报告说有缝；外加豁免只查键在不在、空理由照样生效，而那条测试的名字里就写着 `needs_a_reason`。四条各补一个先证伪再通过的回归测试。 | `ENV-06f`、`SPEC-05c`；`scripts/check_design_set.py`、`tests/test_design_set.py` |
| 35 | 5 P1、4 P2 | **rev 34 的反向评审（第九轮完整安全评审）—— 缝全在「登记了一个字段、或承诺了一条规则，却没有一处读得到它，或读得到也答不出」。** P1 五条：`predicate_positions` 登记着 EFF-06，**整个契约没有一处读它**，而 `ArgPattern.matches` 的注释还写着「已在调用方判不透明」—— 调用方从来没判过；于是 `Remove-Item $flags C:\` 一个效果触发都不命中，被当作惰性放行，而 G05-02 早就把这条用例写在门槛里 → 移到查表之后、`flags()` 与 `dangerous()` 之前判（顺带更正 `analyse()` 自己对 TOK-02 的转述：「任何位置的任何 Dynamic」只是 CMD 那一句，PowerShell 与 POSIX 只有命令词与谓词位）。`caller_scope` 只在 `rebind_triggers` 那一支被读，而 EFF-07 的 `+` 标在**executes_input 表**上：`iex` 没有内在的重绑触发，EFF-03 要并入的退出态永远拿不到（G04-32 中者漏拒）；补一条无条件重绑触发又会让 `iex 'Get-Date'; git status` 污染后继（G04-18 漏放行）—— 两个门槛同时成立只有「执行触发命中时同样带上 `rebinds_caller`」这一种写法。`decide()` 的四条早退都不写 `plan.decided`，于是**上一次调用**判过并放行的 body、工作目录与环境原样留给 `launch()`，spec 对象还可能是同一个（SPEC-08 的比对因此看不出来）→ SPEC-08c：进门先作废。`filtered_path` 承诺按 IMG-01 过滤，参数表里**没有 oracle** ——访问掩码与 reparse 无人可问，那条谓词在这里根本无法实施 → 收 oracle。`resolve` 承诺「在过滤后的 PATH 上解析」，同样收不到那份 PATH，只能自己重算 —— 两次求值之间 PATH 变了，判定证明的映像就不是子进程会打开的那个 → ENV-01a：算一次，`child_env` 与 `resolve` 各收一次。P2 四条：显式 `shell.path` 只过了可信根链，从没与项目根比过，而 IMG-05a 明写「绝对且在项目根之外」—— 一个**只读**检出里仓库自带的解释器，主体换不掉它、链因此答「可信」（G25-06）。机检三条：`anchors` 把整份契约源码当锚点池，母 ID 只要在模块 docstring 或任何散文里出现过就算锚住，删掉真正的 `# FAM-NN` 照样绿（实测：删掉 `# ENV-02` 只在模块 docstring 留一句，旧检查通过、新检查报红）→ 只认注释与函数内字符串、排除模块 docstring；`--changed-since` 的 `gates_naming` 只认精确 ID，与 `coverage` 允许的「子靠母」不一致，新增子规则一律标 `无门槛`；`git_show` 把「这个 revision 解析不了」与「这个文件当时不存在」压成同一个 `None`，`--changed-since mian` 于是把每一条规则与门槛报成新增、退出 0 → 先 `rev-parse --verify`，解析不了退出 2。 | `EFF-06`、`EFF-07`、`SPEC-08c`、`ENV-01a`、`IMG-05a`；`scripts/check_design_set.py` |
| 34 | 1 P0、7 P1、7 P2 | **rev 33 的反向评审（第八轮完整安全评审）—— 缝全在「契约文件里的枚举与散文里的承诺对不上」。** P0：`select_rung` **造不出政策关闭的那两级** —— 阶梯是 `pwsh → powershell → cmd`，POSIX 目标上每一级 `discover` 都答不出、翻转前的 Windows 默认根本没有构造器，于是两条路径都落到 `Exhausted`，而 LADDER-03 把「走空」变成**每次 shell 调用 DENY**；G10-03 早就断言这两条路径产出 `LegacyLaunch`，契约却过不了它自己的门槛 —— 与 SPEC-04a 记下的是同一句教训（「没有一处写入它的构造器，等于这个字段恒为假」），一版之后换成枚举取值又犯了一次 → `LADDER_FLIPPED` 常量 + `auto` 在阶梯之前先给出 `system_posix` / `legacy_cmd`。P1 七条：`analyse_body` **两个重新进入点都没有深度上限**，而同一轮刚给 `trusted_root_chain` 加了 `MAX_REPARSE_DEPTH`，理由一字不差（body 是不可信输入，栈溢出不是一次拒绝）→ `MAX_ANALYSIS_DEPTH` + `reenter-depth`（EFF-03、G04-37）；`env_passthrough` 的元素类型是`KeyPattern`（允许 `*` 前缀）且直接进 `matches_any`，一条 `["*"]` 就把除 ENV-03 保留键外的整份继承环境放回来 —— 正是 ENV-06 关掉的那条链→ `EnvKey` 字面键名 + 丢弃模式条目（ENV-06d、G18-16）；`launch()` / `deliver()` **另收一个 `oracle`**，而 SPEC-05 要求 oracle 绑定主体、随 spec 冻结，重哈希与重读因此可以由一个绑在别的主体上的 oracle 作答 → 改读 `spec.identity_oracle`（SPEC-08b、G01-12 (f)）；LAUNCH-01e 的「`shell=True` 与 `resolve_shell_executable()` 都不再出现」是**无条件的**，而 `LegacyLaunch` 只带一个命令字符串、LADDER-05 又承诺「与今天逐段相同」—— 一条 MUST 让政策关闭的两级跑不起来 → 收窄到政策开启的 rung，两面共用同一处启动点（G24-19 本就只禁「第二处」）；`<C>` 与 `<H>` 有**两个来源**（`read_identity` 一份、`resolve_pshome` / `read_config_sources` 一份），构造时验证前者、`launch()` 的重读比后者，两者从不对齐 → 构造期核对（IMG-08、G21-19）；`encode_workdir` 的 cmd 分支拒 `" % ^ & \| < >` 却不拒 CR/LF，而 `/c` 字符串里一个换行就把命令行切开→ 加入拒绝集（LAUNCH-09b、G21-15）；`floor()` 的长度闸是 `if Windows … elif PosixLaunch`，**没有 else** —— 落不进两支的请求一道计量都不过就去分析了 → 补 fail-closed 分支。P2 七条：`decide()` 在 DENY 上照样写 `plan.decided`，而 `launch()` 只查「记录缺席」 → 记录带上裁定（SPEC-08b、G01-12 (e)）；`dedupe_images` 用 `setdefault`，同一 `canonical_path` 上两条不一致的证明静默保留先见的那条，执行器于是按过时身份复核 → `merge_images` fail closed（G24-21）；TOOL-02 说未标注的规则让 **spec 构造**失败，而两个构造器只收 `ShellBlock`、根本拿不到 `rules`（**未修，留给下一轮定它该落在哪一层**）；ENV-06e 写 `value_ok(key, value, target_platform)`、EFF-03 写 `Analysis { verdict, exit_state }`，契约里各是四参数与三字段 —— 单一定义被拆分动作自己破了两处 → 散文改指契约；§3 的 reason 词表把 `<原因>` 列成封闭三项，而流水线与门槛矩阵一直在发 `rebinds_after` / `executes_input` / `nested-launch` → 补齐具名理由；§3 说机检「每个 ID 至少锚一次」，实际只核母 ID（§2 的约定写对了，§3 写宽了）→ 改齐；`coverage` 让**母规则**靠任一子规则的门槛算作已覆盖 —— 反方向的倾斜，母行的判据可以一条门槛都没有 → 只允许子靠母，并补一条会红的测试。 | `LADDER-05`、`EFF-03`、`ENV-06d`、`SPEC-08b`、`LAUNCH-01e`、`IMG-08`、`LAUNCH-09b`、`LAUNCH-08` |
| 33 | 1 P0、3 P1、2 P2 | **rev 32 的反向评审（第七轮完整安全评审）—— 缝全在「判定过的东西怎么到达执行」上。** P0：判定绑住了 spec，却没绑住 body 与工作目录 —— `decide()` 分析调用方传进来的那两份，`launch()` 又**各收一次**（`body` 是它自己的参数，工作目录改读 `plan.cwd`），只核对 spec 对象身份；于是判定 `Get-Date`、再以同一个 plan 启动另一段危险文本是一次合法调用，长度检查、映像证明与环境里的路径检查全都对着已经作废的输入 → 冻结记录 `DecidedCall`（spec + body + cwd + 环境 + 证明集）一次写入，`launch()` 的签名里没有那两个参数（SPEC-08a、SPEC-08b、G01-12）。P1 三条：**后台是另一条启动路径** —— 模型置 `is_background=true` 就走 `run_background`，它自己拼 `Popen(shell=True, executable=…)`，而 LAUNCH-01 没说它同样受约束、实施表只点名 `LocalShellExecutor.run`；于是一份「按 PowerShell 判定、按 cmd 启动」加旧环境继承的实现能让全部门槛照旧变绿 → LAUNCH-01e：两个交付面共用一份请求与一套复核，`is_background` 只选最后一步调哪个方法。**BASH-01 的语法闸漏掉了会改变 argv 的未引用展开** —— 花括号、路径名、波浪号与未加引号的 `$VAR`：`git {-c,core.fsmonitor=./evil} status` 在效果表里只是一个普通字面词，`execution_triggers` 一条都不命中，而 `*` 与 `?` 展开成什么由工作树里的文件名决定 → BASH-01a。**`resolve_reparse() -> AbsPath \| None` 把「不是 reparse」与「解析不了」压成同一个取值**，`trusted_root_chain` 又把 `None` 直接滤掉（答不出即放行），一对互指的 junction 还会让它递归到栈溢出 → IMG-06c：三态结果 + 已访问集 + 深度上限。P2 两条：`filesystem_is_local` 在规范的构造路径上**恒为假** —— `select_rung` 没有 locality 输入，两个构造器都不写它，G24-05 要的那份「本机 spec」根本造不出来 → 新增 `target_filesystem_is_local()`，两处构造都显式写入。前奏的次序有两条互相矛盾的 MUST（LAUNCH-05 要守卫在前、LAUNCH-09a 称切目录是前奏第一条），而 LAUNCH-07 的「不扰动 body 的语义」被前奏自己每一条违反 → 次序逐级写清，LAUNCH-07a 逐级列出允许发生的启动状态改变。**另修两处自指计数**：方法规则实为十六条，而文件头与 README 都停在十五条（方法规则 8 作用在本文自身上） | 规范 §1、§2、§3、§4、§5；门槛矩阵；实现文件；证据 §4 |
| 32 | 结构（0） | **结构修订，不是一轮评审；无语义偏离。** rev 26 至 rev 31 每一轮的修法都是往同一格里追加限定语：规则表 33 → 49 KB，改过的 24 行全部变长、没有一行变短，ENV-06 一格 2.9 KB 八个句号，八行门槛引用它却说不出各自测的是哪一句；§3 至 §5 的伪代码只有人读，rev 27（十二条里八条）、rev 29（七条里五条，自查再两条）、rev 30、rev 31 的头条全是类型收不拢。三处改动：**(1)** §3 至 §5 外移为 `powershell-support-contracts.py`（stdlib-only，`from __future__ import annotations`），`tests/test_design_set.py` 用 `mypy --strict` 核它 —— 一支 scratchpad 探针证明 rev 29 / rev 30 那三类缺陷（对 `Optional` launcher 解引用、对冻结对象赋值、`AbsPath` 送进 `ResolvedImage` 槽）它不用评审轮次就全报；规范 §3 至 §5 只剩指针，每个 ID 在契约里至少锚一次（70/70）。**(2)** 一行一条规则：11 条超过 900 字节或三个句号的行按**句子原文**拆成 32 条子规则 `族-NNa`…（SPEC-05/07、LAUNCH-01/08/09、ENV-06、IMG-05/06/08、CFG-02、EFF-07），拆分脚本断言拆前拆后规范化文本逐字相等，规则表总字节 39438 → 39440；门槛与 PR 仍点名母 ID（`--list` 列出只靠母 ID 覆盖的子规则）。**(3)** `check_design_set.py` 增加锚点、行长（900 B / 三个句号 / 为什么 450 B）、子规则与 `--changed-since <commit>` 评审包（改了哪些 ID、各自的门槛动没动、门槛改了而定义没动）；规范 108 → 76 KB | 规范 §2、§3 至 §5；`powershell-support-contracts.py`；`scripts/check_design_set.py`；`tests/test_design_set.py` |
| 31 | 6 P1、2 漂移 | **rev 30 的反向评审 —— 上一轮的修法本身留下的缝。** `LegacyLaunch` 加进了 union，可 union 之后那三个 `with`（`env` / `execution_subject` / `attested_images`）照样落在它头上，紧邻的注释还说 `cwd = launcher 所在目录` —— **加变体不等于分字段**；同一条的另一半：显式 POSIX shell 在 POSIX 目标上导出 `system_posix` 之后仍进 `attested_spec`，与矩阵要求的 `None` 冲突 → `AttestedLaunch` / `LegacyLaunch` 两组字段彻底分开，显式来源导出政策关闭的一级直接走 `legacy_spec`。第二条：`decide()` 在 `floor` 校验方言与配对**之前**就算 `ChildEnv`、按 `policy_enabled` 解引用 launcher —— 一份漏进来的坏 spec 会先去问 oracle 或抛异常，而不是产出 SPEC-01/02 的 reason；`policy_enabled` 还是个能与 rung 各说各话的存储字段 → `validate` 前移 + SPEC-03 三条交叉不变量。第三条：oracle 的五个方法都不收 subject，却答着与主体有关的东西；`project_root` 取自宿主，而远端工作树路径不同 → oracle 绑定一个主体、逐个显式收、新增 `target_project_root()`。第四条：`target_platform` 自称快照，`select_rung` 却在显式路径与每轮候选里重复调用 → 入口读一次 `T` 往下传。第五条：`request_total_units` 把 argv 与 envp 相加去比 Windows 的 32767/8191，而那两个上限只约束**命令行**（带非空环境就会误拒 G18-07 的 32766 边界）；cmd 的 8191 与「含 NUL」的通用计量差一；G18-12 测 Linux `MAX_ARG_STRLEN`，而唯一的 POSIX rung 政策关闭、`floor` 在它之前就返回了 —— **门槛不可达** → 三套计量分开，G18-12 改测函数与分支并写明端到端要等 q4。第六条：「冻结」是浅的 —— `env_passthrough`、`PinnedEnv`、`ResolvedImage`、`plan.child_env` 都还可改，改完 provider 仍是同一个对象、指纹也不变，SPEC-08 看不出来 → 整张对象图深度冻结。两处漂移：ENV-06 前半「移除该键」与后半 / G14-05「拒绝该 rung」二选一（取拒绝）；G24-11 的「缺任一方法」漏了 `IdentityOracle` 的六个老方法 → 改成按接口方法清单参数化 | 规范 §2、§3、§4、§5；门槛矩阵 |
| 30 | 7 P1、2 漂移 | **rev 29 的反向评审 —— 端到端跑一遍，缝全在「政策关闭」与「算一次还是算两次」上。** 头两条是同一个源头：`launch()` 无条件解引用 `spec.launcher`、算 `ChildEnv`、交空证明集，于是**政策关闭的两级根本启动不了**（还与 LADDER-05 的「与今天逐段相同」冲突，且 `pinned_env` 必填而 PR-1 就要建 `legacy_cmd` spec）→ `LegacyLaunch` 变体 + 字段改可选 + 复核义务只约束政策开启的 rung；而政策开启时**证明集也不一定含 launcher** —— `analyse_body` 从空集起、只有遇到进程内条目才加它，于是 `git status`、空 body、纯注释的 body 都会被执行器按「直接目标无条目」拒掉（G01-09 恰好塞了个内建命令把这个洞盖住）→ launcher 无条件进集合，门槛补纯外部与空 body 正例。第三条：`ChildEnv` 仍引用了没传进来的 `path_entries`/`target`/两处 `env_passthrough`/工作目录/项目根，`base` 也没来源，而判定与启动各算一次的话长度守卫量的不是最终那份 → `EnvInputs` 显式化、`env_passthrough` 与 `target_platform` 冻进 spec、**算一次记到 plan**。第四条：`PinnedEnv` 两个任意 `dict` 且全声明 `AbsPath`，而 `SystemDrive`/`HOMEDRIVE` 是 `C:`、`HOMEPATH` 根相对、`ComSpec` 是文件；「逐项过 IMG-01」也从没有执行点 → 固定字段 + 封闭键集 + 构造前校验。第五条：`read_identity` 返回 `None` 后照样 `derive_rung`，没核 `identity.image is img` 与主体，自动阶梯也没核「读出的 edition 导出的 rung == 候选 rung」（pwsh 位置读出 Desktop 仍能建 pwsh spec）。第六条：逐条超限混在一个 `or` 里统一报 `launch-env-oversize`，与 G18-12 要求的 `-c <body>` 报 `launch-oversize` 冲突；NUL 与总量记账也没定义 → `per_string_units` / `request_total_units` + 理由分开 + 边界门槛。第七条：**上一轮「不预置探针答案」的修复只改了门槛，规范里三处散文仍断言「前奏后原生 cwd 是工作树」**，G21-18 还自称「与 rung 无关」——子进程 cwd 恰恰由启动它的解释器决定。两处漂移：G24-11 的「缺任一方法」没覆盖新增的四个 oracle 方法；reason 词表「其余三个」实为四个，指纹对新增映射字段没定键序 | 规范 §1、§2、§3、§4、§5、§7；门槛矩阵 |
| 29 | 7 P1、3 漂移 | **rev 28 的反向评审。** 七条里五条是**新引入的接口自己不闭合**：`read_identity(img, rung)` 收 rung，而 `powershell` 方言的 rung 要靠读出来的 edition 才能定 —— `identity → rung → identity` 的循环（改收方言）；ENV-06 要求钉值来自「目标 OS 与主体」，`IdentityOracle` 却没有目标侧的 known-folders / profile / temp 查询，`ChildEnv` 也拿不到 oracle（新增 `target_pinned_env` 与 `ShellSpec.pinned_env`）；`LauncherIdentity` 只有 path + hash，却被送进只收 `ResolvedImage` 的 `trusted_image()` 并进 `attested`（改成 `LauncherIdentity.image: ResolvedImage`）；LAUNCH-08 明写覆盖 `legacy_cmd`，而 `floor` 在 `policy_enabled` 闸就返回了今天的地板，与 LADDER-05 的「与今天逐段相同」冲突（本条只约束政策开启的 rung）；cmd 的 8191 是**逐条**继承变量的上限，`MAX_ARG_STRLEN` 同样逐条约束 argv/envp，而伪代码拿整块环境比（改逐条 + 总量两道闸）。另两条：`attested_images` 只说「供执行器复核」，没有 MUST，兑现不了 SPEC-05（补执行器复核义务 + `launch-attest` + G24-13）；**G21-17 预置了探针要取的答案** —— 而 PowerShell 的位置是运行空间状态、与进程 `[System.Environment]::CurrentDirectory` 不是一回事，残留按 rung 不一样（改逐 rung 问三样、不预置）。三处漂移：ChildEnv 注释让 `HOME`/`TEMP` 也过 IMG-01（那会把它们全删掉）、SPEC-05 与 LADDER-01 还写「解释器」而模型已通用化、证据 §3.22 把「cmd 丢弃超长变量」说成「命令行被截断」（上游没这句） | 规范 §2、§3、§4、§5；证据 §3.22；门槛矩阵 |
| 28 | 1 P0、6 P1、3 P2 | **rev 27 的反向评审（第三轮完整安全评审）。** P0 与上一轮同一处：ENV-06 把环境改成了封闭集，**却没写成员判据** —— 默认集是照着一份典型环境列的，于是 `HOME`、`XDG_CONFIG_HOME`、`USERPROFILE`、`APPDATA`、`SSL_CERT_*` 这些**配置根与信任根**又被列了进去，而 `value_ok` 只查「工作树之内」，配置根指到工作树*之外*主体可写的目录就整条放行（git 读 `$XDG_CONFIG_HOME/git/config`，`core.fsmonitor` 是会被执行的路径）→ 改三分法：**路径值的键一律由 OS 与主体求出钉值、非路径的描述性键才透传、配置根与信任根一律移除**。P1：键与值的规范化契约缺失（Windows 大小写折叠，`Path`/`PATH` 谁生效靠实现顺序；`value_ok` 要按键与目标平台判）；`cmd`/`git_bash` 没有 launcher 身份，`launch()` 却无条件读 `spec.interpreter.path` → `LauncherIdentity` 提到每一级，`InterpreterIdentity` 是它的扩展；`attested_images` 在判定到启动之间丢失（`Analysis` 不带它，`launch()` 无处可取）→ 证明集随 `Analysis` 递归合并、记到 plan；rung 按**宿主**平台导出，非本机 POSIX 目标会落到 `system_posix` 把政策关掉 → `oracle.target_platform()`；LAUNCH-09 只关掉启动期一段却声称护整个进程（加载器在 `LoadLibrary` 发生时才用当前目录，`SetDllDirectory("")` 父进程替不了子进程调）→ 收窄承诺 + 两支探针；LAUNCH-08 漏了 cmd 自己的 8191、`MAX_ARG_STRLEN` 不是常数、`floor()` 没有 cwd 与 env 算不出最终命令行。P2：`fingerprint` 自指且 `identity_oracle` 无规范序列化 → 定义规范投影；`workdir` 字段类型是路径却被赋了方言编码值，且 G24-09 在 PR-1 断言 PR-4 的语义；alias 指向外部程序时应解析 `alias_target` | 规范 §1、§2、§3、§4、§5、§7；门槛矩阵 |
| 27 | 1 P0、8 P1、3 P2 | **rev 26 的反向评审（第二轮完整安全评审）。** P0：威胁模型把继承环境列为不可信输入，ENV 规则却只清 bash 自己的三个钩子 —— 效果表只量命令行（EFF-01），`GIT_CONFIG_GLOBAL` 指向工作树文件、`GIT_CONFIG_COUNT` 配 `core.fsmonitor`、`NODE_OPTIONS=--require`、`PYTHONPATH` 让命令行惰性的可信程序从环境里拿到要跑的代码，一张移除清单对这个面是黑名单 —— 方法规则 4 写下八个版本，从没被拿去审 ENV-03 → ENV-06 封闭透传集 + 取值检查 + 用户级扩展，`env_delta` 改回完整 `env`。P1：加载闭包的「当前目录」那一半被归因给安装根保护，而 Windows 搜索顺序在 PATH 之前含当前目录 → LAUNCH-09 + 明写残留；NAME-02 漏了 function（alias → function → cmdlet → 外部）；`rebinds_caller` 的递归按 EFF-02 不可达 —— 文件目标判死之后 G04-16/18 没跟着改、`floor()` 也返回不了退出态 → EFF-03 只经字面串重新进入、`Analysis` 类型；cmdlet/内部命令/内建没有映像可供 IMG-02 检查 → 映像半绑定已认证解释器；EFF-05 的「非惰性」没有裁定 → 直接不透明；判定用的 spec 没绑到启动 → SPEC-08；`ShellBlock` 只给 `dialect` 或只给 `path` 定不了 rung → 两者成对、rung 按表导出；`IdentityOracle` 缺目标侧的发现/身份/配置/预检 → 五个方法。P2：`EXHAUSTED` 排在把它判成未知 rung 的检查之后 → `Exhausted` 是 provider 状态、检查最先；32767 含结尾 NUL 且 `len()` 不是 code unit；「不可变」伪代码原地赋值 → 预检后一次性构造。**十二条全过了机检，其中八条是伪代码或类型契约与规则表之间的缝** | 规范 §1、§2、§3、§4、§5、§7；门槛矩阵 |
| 26 | 2 P0、7 P1、13 P2、2 小项 | **拆分后规范的完整安全评审（计划第 7 步）。** 两条 P0 都不是拆分引入的：「惰性」的定义被拆分弄丢，而补回来就撞上「可信工具链按设计执行工作树内容」—— 封闭集只能保证启动哪个程序，不能保证它跑了什么，威胁模型此前把后者也许诺了；`git_bash` 那一级政策「开」却没有任何语法闸，`echo $(curl … \| sh)` 在惰性的 `echo` 上通过。P1：翻转前 Windows 没有 rung 取值；IMG-01 只看一级目录、没有访问掩码与 reparse 语义；(b) 档与 LADDER-01 矛盾且把加载闭包放进可写目录；非本机执行器收到的是地板机器的环境；`ShellSpec` 可变性未定；可信表是函数、无准入标准；G25-01 的 ubuntu 半在 SPEC-03 下测不到东西。**二十二轮逐条评审加一轮拆分都没问「每一级的语法闸在哪」「核心谓词定义在哪」** | 规范 §1、§2、§3、§5；门槛矩阵 |
| 25 | 4 P1（自伤）、1 P1（继承）、1 P1（收窄歧义）、9 P2 | **拆分本身的反向评审。** 单体拆成六个文件、规则分配稳定 ID、门槛改追踪矩阵、PR-0 移出之后，对着源码与 rev 24 复审规范：伪代码把 EFF-04 与效果污染放在 `policy_enabled` 闸之外，等于替 q4 作答；`trusted_image` 把 allowlist 写成全局必要条件、与 G23-05 矛盾；`select_rung` 一见用户级块就跳过阶梯；ENV-03 被收窄到 bash rung，而可信 `git` 在任何 rung 上都会再起 `sh`；继承自 rev 24 的一条 —— codex 接受清单不含 `command_name_expr`/`command_invokation_operator`，规则 4 的 4b/4c 在第 5 步之下不可达、G04-13 的理由拿不到；状态行「语义同 rev 24」是假话。**全部机检绿着，一条都抓不到。** 本行同时是 rev 25 相对 rev 24 的**偏离清单**（见下） | 规范 §2、§4、§5；门槛矩阵 |
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

## 1.1 rev 25 相对 rev 24 的偏离清单

规范状态行不再声称「语义相同」；每一处偏离都在这里，按「为什么偏离」分类。

**收紧（更安全，零成本）：** ENV-03 在每一级清除 `BASH_ENV`/`ENV`/`BASH_FUNC_*`，rev 24 的 §1 表与 PR-4 行本就不带限定，
D4 正文与命令行表把它放在 bash 段落里；可信 `git` 经 `sh -c` 导入函数的链（§3.16 实测）与启动它的 rung 无关。

**把 rev 24 的隐含义写成规则：** WRAP-07（前缀运行者 `timeout`/`env`/`xargs`… 带 `executes_input`，是 EFF-01 惰性定义的直接推论）；
WRAP-01 给「包装体」下了定义（今天 `_SHELL_SCRIPT_WRAPPER` 那一类）；WRAP-02 写明 `ex`/`w` 各消费一个值；WRAP-04 写明 4b/4c/4d 在
LOWER-02 之下不可达、可达理由是第 5 步（G04-29），并记录未采纳的另一条路 —— 把两个 kind 加进接受清单。

**拆分时作的决定（rev 24 未定）：** allowlist 放在用户级 `shell` 块与构造 spec 里（`ShellBlock.allowlist`，PR-3），因为 rev 24 只说
「宿主配置」而 CFG-02 的两个宿主来源恰是这两个；reason 词表加 `hardline:<dialect>-opaque:<原因>` 后缀，因为门槛要求按理由区分而 rev 24
没定字符串形状；`ShellSpec` 增加 `execution_subject`、`policy_enabled`、`closed_env_established`，`TrustedEntry` 增加 `rung_scope`、
`predicate_positions`，`ResolvedImage` 增加 `filesystem_identity` —— 都是把 rev 24 正文里的量落成字段。

**补回拆分时丢掉的三个决定：** `dialect: "posix"` 一条规则覆盖两个 POSIX rung（TOOL-02）；LAUNCH-02 不传 `-ExecutionPolicy Bypass`；
非本机执行器的另一条出路记在 §3。

**拆分时写错、本轮改回的：** §4 伪代码的 `policy_enabled` 闸位置、`trusted_image` 的 allowlist 子句、`select_rung` 的短路条件、
TOOL-01 多出的「`remove_tool` 之后」。

## 1.2 rev 26 相对 rev 25 的偏离清单

**新规则（把 rev 24 没有的守卫补上）：** BASH-01（git_bash 的语法闸，与 LOWER-02 同强度）；EFF-08（可信表是数据，条目登记触发参数）；
SPEC-07（`ShellSpec` 不可变，三个重解析事件）；LAUNCH-08（命令行长度守卫，不截断）；LADDER-05（翻转前的 `legacy_cmd`，PR-7 删除）。

**改定义：** EFF-01 补回 rev 24 第 1069 行的惰性定义并加范围词「命令行供给的」，去掉「不改变当前位置」（相对路径永不可信、ENV-04 已关 cmd 的当前目录搜索）；
§1 与 §7.1 写明封闭集的边界 —— 启动哪个程序，不是它跑了什么。

**收紧：** IMG-01 对到卷根的每一个祖先求值；IMG-06 给「能替换」访问掩码语义、reparse 解析与路径规范化；IMG-05 (b) **不免位置**（rev 24 的 G23 让
`shell.path` 点名的用户可写目录里的二进制被选中，现在被拒 —— 加载闭包不能落在主体可写的目录里；用户范围安装归 q12）；ENV-05 的 `PSModulePath`
只含 IMG-01 目录。

**接口：** `IdentityOracle` 增加 `canonicalize`、`resolve_reparse`、`target_base_env`、`target_path_entries`；`LaunchRequest` 的 `env` 改为
`env_delta`；`TrustedEntry` 改成数据字段；reason 词表加 `launch-<原因>`。

**门槛：** 新增十八行、改三行（见门槛文件头）。G25-01 改为 `windows`，ubuntu 半由 G25-04 用桩测。

**推理、未实测（本机无 Windows）：** edition 5.1 无 `powershell.config.json`（G21-14）；PowerShell 是否先找 `.ps1`（G21-13）；祖先重命名与 reparse 的
ACL 细节（G23-06、G23-07）。

## 1.3 rev 27 相对 rev 26 的偏离清单

**新规则：** ENV-06（子进程环境是封闭透传集，带取值检查与用户级 `env_passthrough`）；SPEC-08（判定时读到的 `ShellSpec` 对象记在 plan 上、贯穿到 `launch()`，
不同则拒）；LAUNCH-09（解释器以 launcher 所在目录启动，前奏切到 `<W>`）。

**改定义：** ENV-03 从「移除三个变量」改成「保留键任何来源都透传不进来」；LAUNCH-01 的 `env_delta` 改回**完整** `env` —— 封闭集是基础环境的函数不是补丁，
取值检查本就要读到值，非本机的基础环境仍由 oracle 答出（G24-10 的意图不变）；LAUNCH-05 的前奏多一个 `<W>`；LAUNCH-08 按平台单位量、含结尾 NUL、
Linux 另查单参数上限；IMG-02 给进程内条目定义映像半（已认证的解释器）；LADDER-03 的走空改成 provider 的 `Exhausted(reason)` 状态、检查先于一切；
CFG-02 要求 `path` 与 `dialect` 成对、`rung` 按表导出而不是配置字段；NAME-02 按 alias → function → cmdlet → 外部解析、表用 `Get-Command -All` 量；
EFF-02 的字面串例外只给本方言求值器（EFF-07 的 `=`）；EFF-03 只经字面串重新进入递归并返回 `Analysis`；EFF-05 直接不透明；SPEC-05 把阶梯与预检也放到目标上；
SPEC-07 加 `fingerprint`、构造在预检之后一次完成。

**接口：** `IdentityOracle` 增加 `discover`、`read_identity`、`resolve_pshome`、`read_config_sources`、`preflight`；`TrustedEntry` 增加 `kind`、`alias_target`、`reenters`；
`ShellBlock` 增加 `env_passthrough`；`LaunchRequest` 的 `env_delta` → `env`，增加 `workdir` 与 `spec_fingerprint`；新增 `Exhausted`、`ExitState`、`Analysis`。

**门槛：** 新增十行、改九行（见门槛文件头）；G04-16/18 从「文件递归」改成「文件不读、字面串传播」这一对。**待决：** q13（默认透传集的宽窄）。

**上游抓取（证据 §3.21）：** PowerShell 命令优先级、Windows DLL 搜索顺序、`CreateProcessW` 上限、git 的 `GIT_CONFIG_*` 与 `core.fsmonitor`、node 的 `NODE_OPTIONS`，
各带 commit 与 sha256。**推理、未实测：** 解释器启动时探测失败的 DLL 名（G21-15 先量再放）；`-NoProfile` 下哪些名字是 function（G21-16 用 `Get-Command -All` 量，
`mkdir`、`more`、`help` 是从上游文档与经验推的）。

## 1.4 rev 28 相对 rev 27 的偏离清单

**改定义（无新 ID）：** ENV-06 从「封闭透传集」改成**三类**（钉值 / 透传（值检查）/ 移除），成员判据写成一句话 ——
「这个键的值是不是一条路径」；键先按目标平台折叠再做集合运算，碰撞取值不同即移除并诊断；`value_ok(key, value, target)`。
IMG-07 从「PowerShell rung 的身份」扩成「每一个政策开启的 rung 都有 `LauncherIdentity`」，PowerShell 级另绑实测身份。
CFG-02 的 rung 导出改用 `oracle.target_platform()`，不再用宿主平台。LAUNCH-08 按 rung 取上限（cmd 8191）、对环境设同一道闸、
`MAX_ARG_STRLEN` 运行期查、守卫收 cwd 与算好的 env。LAUNCH-09 的承诺收窄到「进程创建到前奏切目录」这一段，其余入残留。
SPEC-07 的 `fingerprint` 定义成规范投影（排除自身与 `identity_oracle`）。EFF-03 的 `Analysis` 增加 `attested`，递归并集合并。
IMG-02 的进程内条目改绑 `ShellSpec.launcher`。伪代码里 alias 解析 `alias_target`。

**接口：** `ShellSpec.interpreter` → `launcher: LauncherIdentity | None`；新增 `LauncherIdentity`，`InterpreterIdentity`
成为它的扩展；`IdentityOracle` 增加 `target_platform()`，`read_identity(img, rung)` 收 rung；`LaunchRequest.workdir` 明确为
规范化绝对路径（方言编码形态只在命令行里）；`ChildEnv(rung, base, subject, target)`。

**决策（用户在本轮定的）：** 配置根与信任根不进默认集；代理四键留在默认集，明写为「改流量去向、不改子进程跑什么」的
接受项；cmd 的 UNC 工作目录退出 98 维持。q13 因此从「默认集该多宽」收窄为「工具链的路径值变量怎么授权」。

**门槛：** 新增十行、改四行（见门槛文件头）。**上游抓取（证据 §3.22）：** CPython 的 Windows 键折叠、cmd 8191、
`MAX_ARG_STRLEN = PAGE_SIZE * 32`、DLL 安全文档的加载时机与 `SetDllDirectory`、git 的 `XDG_CONFIG_HOME`，各带 commit 与 sha256。
**推理、未实测：** PowerShell `Set-Location` 之后进程当前目录是否跟着变（G21-17 记录）；钉值该从哪个 Windows API 求
（`GetSystemWindowsDirectory` / `SHGetKnownFolderPath` 是推的，PR-4 落地时核）。

## 1.5 rev 29 相对 rev 28 的偏离清单

**接口（无新规则 ID）：** `IdentityOracle` 增加 `target_pinned_env(subject)` 与 `resolve_image(path)`，`discover` 改返回 `ResolvedImage`，
`read_identity` 改收**方言**、并让返回的身份内嵌那个 `ResolvedImage`（于是构造期不再对冻结对象赋值）；新增 `PinnedEnv`；
`LauncherIdentity` 改为携带完整的 `ResolvedImage`（`path` 降为投影）；`ShellSpec` 增加 `pinned_env` 并进 `fingerprint` 投影；
`ChildEnv(spec, base)`；reason 词表加 `launch-attest`。

**改定义：** LAUNCH-01 给执行器一条 **MUST** 级复核义务（直接目标按 `attested_images` 比对，不一致 / 无条目 / 复核不了 ⇒ 拒绝启动），
并写明它覆盖不到子进程自解析的命令词 —— SPEC-05 的绑定由它与 ENV-01 合起来兑现；LAUNCH-08 只约束政策开启的 rung，且逐条与总量分成两道闸；
ENV-06 的 IMG-01 只作用于系统目录那一类，用户身份目录按定义主体可写、钉值只挡重定向；LAUNCH-09 的残留改为**按 rung 未定**，由探针落定。

**门槛：** 新增三行、改六行（见门槛文件头）。**上游抓取（证据 §3.22 (f)）：** `about_Locations` 的运行空间位置 ≠ 进程当前目录，带 commit 与 sha256。
**推理、未实测：** 各 rung 前奏之后的原生当前目录（G21-17）；钉值该从哪个 Windows API 求（PR-4 落地时核）。
**自查又抓到两条同族的（本轮新内容违反本轮新规则，方法规则 4 的对偶用法）：** `discover` 返回 `AbsPath` 却被送进只收 `ResolvedImage` 的 `trusted_image()` —— 与本轮修的第 4 条是同一个洞、只是高一层；`attested_spec` 里 `identity.image = img` 是对声明冻结的对象赋值，正是 rev 27 修掉过的那种写法。

## 1.6 rev 30 相对 rev 29 的偏离清单

**新增类型 / 变体：** `LegacyLaunch`（政策关闭两级的启动请求，逐字段等价于今天的 `ShellRequest`）；`EnvInputs`（`ChildEnv` 的全部外部输入）；
`per_string_units` / `request_total_units`（LAUNCH-08 的两种计量）。

**改定义：** LAUNCH-01 的复核义务**只约束政策开启的 rung**；`ShellSpec.launcher` 与 `pinned_env` 对政策关闭的两级为 `None`，新增
`env_passthrough` 与 `target_platform` 两个冻结字段（都进指纹投影，映射按键的字典序编码）；`PinnedEnv` 从两个任意 `dict` 改成固定字段 +
封闭键集 + 逐字段形态，系统那一类在**构造 spec 之前**逐项过 IMG-01；`ChildEnv(spec, inputs)` 没有隐式来源、**每次调用只算一次**并记到
`plan.child_env`，长度守卫与请求用同一个对象；LAUNCH-08 的逐条超限按「是 argv 还是环境」分理由，总量单独一道；LAUNCH-09 与 §1、§7.1 的
残留改为**范围按 rung 未定，由探针落定**（上一轮只改了门槛、没改散文）。

**流程：** `decide()` 把 launcher 的映像**无条件**放进 `attested_images`；`select_rung` 补三处身份校验（`None`、`identity.image is img`
与主体、导出 rung == 候选 rung）。

**门槛：** 新增五行、改七行（见门槛文件头），其中 G23-09 与 G24-13 明确分界 —— 重哈希之前被换报 `launch-rehash`，请求交出去之后被换报
`launch-attest`。**推理、未实测：** `request_total_units` 里每条一个指针的开销（PR-4 按目标内核核）。

## 1.7 rev 31 相对 rev 30 的偏离清单

**类型：** `LaunchRequest` 拆成 `AttestedLaunch`（两个变体 + 四个共享字段）与 `LegacyLaunch`（四个字段，**没有** `workdir` /
`execution_subject` / `attested_images`）；`env` 与 `attested_images` 改为 `FrozenEnv` / `tuple`；三套计量 `createprocess_units` /
`cmd_line_chars` / `execve_total_units` 取代 `per_string_units` / `request_total_units`。

**改定义：** SPEC-07 改为**整张对象图深度不可变**（浅冻结等于没冻结：改 `spec.pinned_env.temp` 指纹不变）；SPEC-03 增加三条交叉不变量
（`policy_enabled` ⇔ rung，⇒ launcher/pinned_env 在或都为 `None`），构造时校验、漏到地板按 SPEC-02 拒；SPEC-05 声明**一个 oracle 绑定一个
执行主体**并新增目标项目根；ENV-06 的系统钉值不过 IMG-01 = **拒绝该 rung**（不是移除该键）；LAUNCH-08 三套计量互不相加，Windows 的两个只量
命令行。

**流程：** `decide()` 先 `validate(spec)` 再碰 oracle 与环境；`select_rung` 入口读一次 `target_platform` 并往下传，显式来源导出政策关闭的
一级走 `legacy_spec`；oracle 的 `target_base_env` / `target_path_entries` / `resolve_image` / `discover` 逐个收 `subject`。

**门槛：** 新增五行、改五行（见门槛文件头）。G18-12 明确记为「端到端不可达，等 q4」，改测计量函数与 `floor` 的 POSIX 分支。

## 1.8 rev 32 相对 rev 31 的偏离清单

**无语义偏离。** 三处结构改动见修订表 rev 32 行。子规则的拆分点与母行保留的判据：SPEC-05（母 = oracle 绑定主体；a 目标项目根；
b 三段义务 + 阶梯预检在目标上；c 缺 oracle / 缺方法 ⇒ 未认证）、SPEC-07（母 = 深度不可变 + 浅冻结等于没冻结；a 构造在预检之后 + 指纹投影；
b 重解析的三个事件 + 子代理共享）、LAUNCH-01（母 = 原样运行 + 可判别体；a 另带的字段 + `sandbox-exec`；b `launch()` 拒绝走 DENY 通道；
c 政策关闭的两级走 `LegacyLaunch`；d 执行器复核 MUST + 覆盖不到的部分）、LAUNCH-08（母 = 只约束政策开启 + 超限拒绝不截断；a 单位按平台与 rung；
b 计量 (i)(ii)；c 计量 (iii) + 理由归属；d 守卫读 cwd 与环境、PR-4 前退化）、LAUNCH-09（母 = 以 launcher 目录启动；a 前奏切目录 + `\|\| exit 98`；
b `<W>` 编码不了 ⇒ `launch-cwd`；c 关掉的窗口 + 残留按 rung 未定；d 启动级缓解不可用）、ENV-06（母 = 封闭集三类的判据；a (1) 钉值；
b 非本机钉值 + `PinnedEnv` 固定字段 + 未认证条件；c (2) 透传；d (3) 移除 + 用户扩展；e 键折叠 + `value_ok`）、IMG-05（母 = 两档 + (a)；
a (b) 显式；b 用户范围安装被拒 + PATH 命中不是候选）、IMG-06（母 = 四问 + Windows / 非本机；a 访问掩码语义；b 规范化）、
IMG-08（母 = 三来源 + 非默认拒绝；a `$PSHOME` + spawn 前重读；b 5.1）、CFG-02（母 = 来源取胜 + 成对；a rung 导出表；b 部分块参数化 auto）、
EFF-07（母 = 逐方言集合；a 可达性纵深；b 修改形式是门槛用例）。

**把伪代码翻成能过类型检查的代码时收口的缝**（每一处都按规则文本最贴近的读法收口；改的是契约文件，不是 §2）。**第七轮（rev 33）已复核，
并在最后那一处翻了案：** `ToolCallPlan.cwd` 显式化本身没错，错的是它把工作目录显式成了 `launch()` 可以另行取用的第二个来源 —— 见 rev 33 的 P0。
`PinnedEnv` 里 Windows 专属字段在 rev 31 写成非 `Optional`、同一段又说 POSIX 目标上为 `None` —— 现在按平台适配写成 `| None`，`shapes_ok`
核另一平台确为 `None`；`program_files_x86` / `common_program_files_x86` 两个字段在 `ChildEnv` 的键清单里没有对应键，补 `ProgramFiles(x86)` /
`CommonProgramFiles(x86)`；(1) 钉值与 (2) 透传的优先级在 rev 31 的集合运算里是隐含的，现在钉值键不从 base 抄、用户扩展也覆盖不了；
`read_env_inputs` 答不出时 rev 31 只说「该 rung 未认证」，而判定期没有 rung 可以未认证，现在 `decide()` 报 `hardline:<dialect>-opaque:ENV-06`；
`resolve()` 解析不到外部映像时 rev 31 把可能不存在的 img 送进 `trusted_image()`，现在先判映像半不透明；`validate` 多核一条：PowerShell rung 的
launcher 是 `InterpreterIdentity`（rev 31 只在散文里断言）；`InterpreterIdentity.session_config` 与 `SessionConfig.session` 改 `str | None`，
`None` = 三来源都没发现配置，与 IMG-08 的 5.1 情形和 LAUNCH-06 对齐（rev 31 写 `str` 再与一个未定义的 `default` 比）；LADDER-04 的「该级关着
发布」成为契约里的 `GIT_BASH_RELEASED` 常量，阶梯与显式来源都读它（CFG-02 已这么说）；`floor()` 组装的请求证明集为空、只为计量，与 `launch()`
的请求只差这一项；`ToolCallPlan.cwd` 显式化（rev 31 写 `plan.cwd` 却没声明字段）。

**未定的缝（写成 `raise Unspecified(...)`，不假装已定）：** POSIX 的 `sysconf(ARG_MAX)` / `PAGE_SIZE` 在非本机目标上由谁答 —— SPEC-05 要求经
oracle，LAUNCH-08 写的是裸 `sysconf`，而 POSIX 分支端到端要等 q4（G18-12），定案时再补接口；分析期生效的 `ShellBlock`（allowlist 的来源）怎么到
`analyse_body` 手里（rev 31 用自由变量 `policy`）；allowlist 里的 `PublisherTrust` 条目不点名路径，`entry_for` 只能命中 `HashPin`，它怎么进入
`oracle.publisher_trusted` 未写；`today_command` / `today_env` 是 LADDER-05 的接缝。**一个未采纳的备选**：把 `ShellSpec` 拆成 `AttestedSpec | LegacySpec`
两个变体，SPEC-03 的三条交叉不变量就成了类型结构而不是 `validate` 的运行期检查 —— 与 rev 31 给 `LaunchRequest` 做的是同一件事，留给评审定。

**过程记录：** 拆分之前先把 rev 31 的五个文件与对 `main` 的 diff 存进会话 scratchpad（`rev31/`），因为 rev 27 至 rev 31 都未提交；本轮编辑期间另一个
会话在 14:38–14:40 写入了 rev 31，此后无改动。**建议：rev 31 与 rev 32 分开提交**，好让结构修订单独可审。

## 1.9 rev 33 相对 rev 32 的偏离清单

**类型：** `ToolCallPlan` 的 `shell_spec` / `cwd` / `child_env` / `attested_images` 四个字段收成一个冻结记录 `DecidedCall`
（`plan.decided`），`launch()` 去掉 `body` 参数、不再读 `plan.cwd`；`resolve_reparse` 的返回类型由 `AbsPath | None` 改为三态的
`ReparseResult`；新增 `ShellExecutor` / `ShellResult` / `BackgroundHandle` 三个 Protocol 与 `deliver()`；`IdentityOracle` 新增
`target_filesystem_is_local()`；`trusted_root_chain` 增加 `visited` 与 `depth` 两个有界参数与 `MAX_REPARSE_DEPTH`。

**改定义：** SPEC-08 只管 spec 对象，新增 SPEC-08a（body 与 cwd 与结论一起冻进 `plan.decided`）与 SPEC-08b（`launch()` 不另收
这两样）；SPEC-04a 要求两个构造器都显式写入执行器声明的 locality；LAUNCH-01e 把前台与后台并成一份请求；LAUNCH-07 收窄为
「不改动 body 字节、守卫前不跑 body」，允许的启动状态改变逐级列在 LAUNCH-07a；LAUNCH-09a 的「前奏第一条语句」改为「紧接在
body 之前」（PowerShell 级在守卫之后）；IMG-06c 定义 reparse 的三态与递归上限；BASH-01a 把会改变 argv 的未引用展开判为整段
不透明，母行末句「裸 `$VAR` 仍是 `Dynamic`」随之改指 BASH-01a。

**门槛：** 新增六行、改五行（见门槛文件头）。G01-12 是 P0 的门槛，(a) 那一格断言的是**类型**（`launch()` 多传即类型错误），
与 G24-17 同一手法。

**自查：** 方法规则的条数（十六）与文件头、README 写的（十五）对不上 —— rev 32 加了第 16 条，两处计数没跟上；rev 33 一并
补上并新增第 17 条，两处都改成十七。

## 1.10 rev 34 相对 rev 33 的偏离清单

**类型 / 常量：** 新增 `LADDER_FLIPPED`（LADDER-05：翻转前阶梯不运行）、`MAX_ANALYSIS_DEPTH`（EFF-03 的重新进入上限）、
`EnvKey`（ENV-06d 的授权单位是字面键名；不再有 `KeyPattern` —— `*` 前缀只出现在 agentao 自己的表里，由 `matches_any` 解释）；`DecidedCall` 增加 `verdict`；
`dedupe_images` → `merge_images`（冲突返回 `None`）；`launch()` 与 `deliver()` 去掉 `oracle` 参数，改读 `spec.identity_oracle`。

**改定义：** LAUNCH-01e 的「不再出现 `shell=True`」收窄到政策开启的 rung（政策关闭的两级按 LAUNCH-01c 与今天逐段相同，
但两面共用同一处启动点）；LAUNCH-09b 的 cmd 拒绝集加入 CR、LF；SPEC-08b 加「记录上的裁定是 DENY ⇒ 同样拒绝」与
「不另收 oracle」；EFF-03 加深度上限与 `reenter-depth`，并把 `Analysis { verdict, exit_state }` 改指契约（它有三个字段）；
ENV-06d 加「授权的单位是字面键名，含 `*` 的条目丢弃」；ENV-06e 的 `value_ok` 签名改指契约；§3 的 reason 词表补齐具名理由；
§3 的机检承诺改成「每个**母** ID」，与 §2 的约定对齐。

**流水线：** `select_rung` 在阶梯之前先给出 `auto` 的政策关闭默认（POSIX 目标 → `system_posix`，翻转前 Windows → `legacy_cmd`）；
`floor()` 的长度闸补 fail-closed 的 else；`attested_spec` 核对 `identity.pshome` / `identity.session_config` 与三来源读出的那一份；
`analyse_body` 的进程内条目直接用已认证的 launcher 映像，不再逐条重走 ACL 链（每个命令词一次 O(祖先) 的 oracle 往返）——
它与 IMG-02 的措辞（「**已认证的** launcher 映像」）一致，但**它的理由曾写错**：原注释说「构造之后被换掉由 spawn 前的重哈希抓」，
而重哈希只比内容、执行器复核只比文件系统身份与内容身份，**没有一处重看访问掩码**。这一条不是本轮 15 条发现里的任何一条，
是代改时顺手做的一处收窄；保留改动、更正理由，并把「安装根在构造之后变得可写」写进规范 §1 的残留行。

**门槛：** 新增四行（G04-37、G18-16、G21-19、G24-21）、改三行（G01-12 加 (e)(f)、G21-15 加换行工作目录、见门槛文件头）。

**机检：** `scripts/check_design_set.py` 的 `coverage` 改成单向 —— 子规则可以靠母规则的门槛，母规则不能靠子规则的，
并补一条会红的测试（`test_a_parent_may_not_lean_on_a_child_gate`）。

**未修，留给下一轮：** TOOL-02 说未标注的 shell 规则让 **spec 构造**失败，而 `select_rung` / `attested_spec` 只收 `ShellBlock`、
拿不到 `PermissionConfig.rules` —— 是把 rules 送进构造器，还是把这道检查挪到配置加载或 `decide()`，是一处设计决定，不是一处笔误。

**这一轮的教训：** P0 与 SPEC-04a 的那句是同一句 —— 「没有一处写入它的构造器，等于这个字段恒为假」。上一版把它记在一个
`bool` 字段上，这一版同样的洞长在一个**枚举取值**上（`Rung.legacy_cmd` 在整个契约里只被 `LEGAL_PAIRS` 与 `POLICY_OFF_RUNGS`
读过，没有一处产生它）。**伪代码翻成代码之后，要逐个枚举取值、逐条矩阵行问「谁造得出它」，并拿本文件集自己的门槛当反例跑一遍**
—— G10-03 早就写着这两条路径产出 `LegacyLaunch`，缺的不是门槛，是把门槛与新写的代码对一遍。

## 1.11 rev 35 相对 rev 34 的偏离清单

**新规则：** SPEC-08c（`decide()` 进门先作废 `plan.decided`）、ENV-01a（过滤后的 PATH 每次调用只算一次，判定期解析与子进程环境用同一份）。
两条都是把一条**本来就该成立**的不变量写下来 —— rev 34 之前没人说过它们不成立，也没人说过它们成立。

**改签名：** `filtered_path(subject, entries, cwd, project_root, target)` → `filtered_path_entries(…, oracle)`，返回条目序列而不是字符串
（`join_path` 负责写成 `PATH`）；`resolve(name, spec, oracle)` 加 `search_path`；`child_env` 加 `search_path`；
`floor` 与 `analyse_body`（含两个递归点）沿途传它。`decide()` 因此在算环境之前先收窄 `identity_oracle`（政策开启却没有 oracle ⇒ `SPEC-05c`，
ENV-01 的过滤谓词无人作答）。新增接缝 `path_within`（IMG-05a 的「在项目根之外」与 ENV-01 / ENV-06 的「工作目录与项目根之内」是同一个谓词）。

**流水线：** `TrustedEntry.flags()` 在执行触发命中时也发 `rebinds_caller`；`analyse_body` 在 `lookup` 之后立即判 EFF-06 的谓词位；
`select_rung` 的显式来源分支在可信根链之后再比一次项目根。

**门槛：** 新增两行（G04-38、G25-06）、改一行（G01-12 加 (g)，并把 (d) 的期望改成「`decide()` 里的两处 —— 作废与整体写入」）。
EFF-06 与 EFF-07 这两条**没有新增门槛** —— G05-02、G04-18、G04-32 早就写着这一轮的三条用例，缺的从来不是门槛。

**机检：** `contract_anchors()` 取代 `rule_refs(整份源码)`；`resolve_rev()` 先解析基线，`changed_since` 解析不了就抛，
`main` 退出 2；`gates_naming` 与 `coverage` 一样按 `{rid, parent_of(rid)}` 找。三条各自跑过一次证伪（改回旧写法后结论相反）。

**这一轮的教训：** 门槛齐、机检绿、`mypy --strict` 过，九条一条都拦不住 —— 因为契约是**签名**，门槛是**用例**，
而这九条全长在两者之间那一层：字段登记了没人读，谓词承诺了没有输入可答。**机检查得出「规则没有锚点」，查不出「锚点下面那个分支是空的」。**
唯一有效的手法是逐个登记字段、逐条「按 X 规则」的接缝去 grep 它的读者，读者为零、或读者的参数表里缺答案来源，那条规则就还只是一句注释（方法规则 18）。

## 1.12 rev 36 相对 rev 35 的偏离清单

**新规则：** ENV-06f（钉值答不出 ⇒ 拒绝该 rung，不是少一个键）。SPEC-05c **没有新规则** —— 它 rev 29 就写着「缺任一方法」，
这一轮补的是契约里实现它的那一处。

**类型 / 常量：** 新增 `ORACLE_METHODS`（`IdentityOracle` 的十八个方法，G24-11 按同一份清单参数化）与 `oracle_complete()`；
`PinnedEnv.shapes_ok` 增加逐平台的必答字段清单。

**流水线：** `select_rung` 进门先答 `oracle_complete`，不完整即 `Exhausted("SPEC-05c: incomplete oracle")`，之后一问都不再问它；
`decide()` 的 oracle 收窄改成「缺席或缺任一方法」，`is None` 那一半只为收窄类型。

**门槛：** 新增一行（G24-22）。

**机检：** 陈旧豁免检查改用与接受路径同一个锚点集合（`{rid} | children`）；豁免的理由为空即报错；`--changed-since` 的
「契约里无锚点」标记同样认子规则的锚点；「门槛改了、规则没动」的 `touched` 并入每个被改动规则的母 ID。
四处各补一条回归测试（`tests/test_design_set.py` 41 → 44），逐条改回旧写法验证会红。

**核过但不成立：** 评审进程报了三条命令失败（`pytest`、`check_design_set.py`、`mypy`）。逐条按同一解释器重跑，
三条都是绿的、退出码 0 —— 全量 `pytest tests/` 本机确有一处既有失败（家目录 `.env` 被 dotenv 向上搜到），与本文件集无关。

**这一轮的教训：** P2 那四条是方法规则 11 换了个介质重演 —— 上一轮改的是**代码里的一个谓词**，而它的消费者是同一份脚本里的
另外几个函数，不是文档里的表格，于是「grep 每一处引用」这条规则没有被想起来。规则 11 因此补上代码那一句。P1 两条则是新的一类：
**一份「完整性」的承诺，`Optional` 字段与 `Protocol` 方法都强制不了** —— 前者缺席是一个合法取值，后者缺席是一次运行期异常，
两者都不是拒绝（方法规则 19）。

## 1.13 rev 37 相对 rev 36 的偏离清单

**新规则：** IMG-03a（判定期生效的 allowlist 与 spec 一起冻结并进指纹）。ENV-06f 与 SPEC-05c **没有新规则** ——
两条都是 rev 36 写下之后没实施到底的地方。

**类型 / 函数：** `ShellSpec` 增加 `allowlist` 并进 `fingerprint_projection`；`allowlist_entry_for` 从 `ShellBlock` 的方法
提为模块函数，`trusted_image` / `host_identity_ok` 改收 `Allowlist` 而不是「生效的块」；**删除 `policy_of`**；
新增 `SELECTION_METHODS` 与 `oracle_answers`；`shapes_ok` 的必答清单补 `home` / `temp` / `tmp`。

**流水线：** `select_rung` 进门只要选级那两问，完整性检查移到显式来源分支与阶梯之前 —— 政策关闭的两级在它上面就返回了。

**门槛：** 新增一行（G23-11：两份只差一条 pin 的配置指纹必须不同，且判定路径上没有第二处读得到生效的块，按 grep 断言）。

**机检：** 三处 `subprocess.run` 显式 `encoding="utf-8"`；评审包的锚点标记补齐 `check_set` 的另外两个条件
（`anchor_definer` 作用域、`anchor_exempt`）。新增两条测试（44 → 46），各自改回旧写法验证会红 ——
其中 utf-8 那条第一次证伪时**改错了地方**（同一段文本在两处出现，替换断言失败、测试跑的是没改过的源码），
重做后才见红：与评审记录里那条老教训同源，**跑完要按内容复验，别信脚本自己的成功输出**。

**这一轮的教训：** 五条里三条落在上一轮刚写下的规则上，而且落点就是写规则时正在编辑的那个函数。
方法规则 8 说「当某一版写下一条教训时，先拿它审这一版自己」—— 这一轮把它读窄了：审的是**这一版的新内容**，
没审**写规则的那一次编辑本身**。规则 8 因此补一句。另外两条是老熟人：隐式来源（规则 17）与放错位置的 fail-closed 闸（新的规则 20）。

## 1.14 rev 38 相对 rev 37 的偏离清单

**新规则：** CFG-02c（显式来源导出政策关闭的一级时，用户点名的可执行文件随 spec 冻结并进指纹）。

**类型 / 函数：** `ShellSpec` 增加 `explicit_shell` 并进 `fingerprint_projection`；`legacy_spec` 增加同名参数；
`today_command(body, shell)`；`validate()` 增加「政策开启 ⇒ `explicit_shell` 为 `None`」。

**门槛：** 新增一行（G25-07：`/bin/bash` 与 `/bin/zsh` 两份 spec 指纹必须不同，`LegacyLaunch.command` 用点名的那个）。

**机检：** `gates_for()` 与 `anchored()` 提为模块函数，`check_set` 的 coverage / anchors 与评审包的
`gates_naming` / 锚点标记全部调它们；`gate_changed()` 对矩阵之外的门槛答「不知道」而不是「改了」，
列表里标 `（定义内引用，未比对）`。新增一条测试（46 → 47），两半各证伪一次。
**重构按两份评审包输出逐字节比对确认无行为变化** —— 第一次比对把旧脚本放在 `/tmp` 下跑，
它的 `ROOT` 因此解析到 `/`、七个文件全部 `SKIP`，diff 比的是一句 SKIP 和一份完整报告；
把旧脚本复制回 `scripts/` 才是有效的对照（本轮第二次踩同一个坑，见 §1.13）。

**这一轮的教训：** 第二条是同一个失效方式连着第三轮 —— rev 36 四条、rev 37 一条、rev 38 一条，
每次都是「`check_set` 接受的某一条路径，评审包没有跟着写」。补条件治不了它，因为**每补一次就多一处要同步的副本**；
这一轮改成让两边调同一个函数，方法规则 12 的「一个定义点」从规则扩到谓词。

## 1.15 rev 39 相对 rev 38 的偏离清单

**新规则：** LAUNCH-09e（PowerShell 级的 `<W>` 另拒四个非 ASCII 单引号）。

**类型 / 函数：** 新增 `PS_SINGLE_QUOTES` 与 `target_is_local()`；`SELECTION_METHODS` 从两问收到一问。

**门槛：** 新增一行（G21-20：四个字符逐个拒、对抗串拒、普通工作目录照常通过、POSIX 与 cmd 两支不受影响）。

**实测：** 直接加载契约模块跑了 `encode_workdir` 六个用例与 `target_is_local` 两个用例，
另跑一次 `select_rung`（缺 locality 方法的 POSIX oracle）确认它落到 `legacy_spec` 而不再是 `Exhausted` ——
它停在 `fingerprint_of` 这个接缝上，那正是规范模块该停的地方。

**这一轮的教训：** P1 是十三轮里第一次打在**不是 body 的那一半**上。整套规则、二十条方法规则与两百多条发现，
判据一直是「不可信的 body 怎么穿过判定」；而 `<W>` 走的是另一条路 —— 它是 agentao 自己拼进前奏的文本，
`analyse_body` 根本不看它，LOWER-01 与 BASH-01 的语法闸也都在它之外。方法规则 21 记这一条。
P2 是方法规则 20 第三次记账，而且这次是**修那条规则时引入的第二版**：例外子句写在 SPEC-04a 里、
不在 SPEC-05c 里，于是「把两个方法都算成必答」看起来毫无问题。

## 1.16 rev 40 相对 rev 39 的偏离清单

**新规则：** ENV-06g（钉值两类的成员判据；`PUBLIC` 归 profile 那一类）。

**新待决问题：** q14（出厂 Windows 上 `C:\ProgramData` 过不过得了 IMG-01），PR-4 之前定，探针 G23-12。

**类型 / 函数：** `PinnedEnv.system_paths()` 去掉 `public`（docstring 改成原始字符串 —— `C:\Users\Public` 里的 `\U`
是一个 unicode 转义，`mypy` 当场报语法错，这是把契约写成能过类型检查器的代码换来的第二次即时反馈）。

**门槛：** 新增一行（G23-12，探针，不预设答案）。

**机检：** 「改了子规则等于动了母规则的门槛」改用谓词 `synchronized()` 表达，不再把母 ID 并进 `touched`。
新增一条测试（47 → 48），改回 rev 38 的写法验证会红，同时确认 rev 38 那条测试在旧写法下仍绿 —— 两个方向都钉住。

**这一轮的教训：** 两条都是「一个东西被放进了它不属于的那一类」。`PUBLIC` 被按名字归进强谓词那一类，
划分却从没写过判据；母 ID 被并进「实际改了什么」那个集合，好让一条关系成立，而集合的每一个其它读者
从此读到的都是假话。**表达一条关系，用谓词，不要动数据。**

## 1.17 rev 41 相对 rev 40 的偏离清单

**新规则：** IMG-03b（`PublisherTrust` 的消费点与判据）、LAUNCH-08e（未配对代理项在计量之前拒）。
**改定义：** IMG-06c 的「已访问集」改成「这一趟 reparse 遍历的入口集」，并写明混成一个会误拒什么。

**类型 / 函数：** `IdentityOracle` 新增 `image_signer`（同时进 `ORACLE_METHODS` 与 G24-11 的清单）；
新增 `has_lone_surrogate`；`host_identity_ok` 改成三条路各走一次；`trusted_root_chain` 的参数
`visited` → `following`，递归只传本趟入口。

**门槛：** 新增两行（G23-13、G24-23）、改一行（G23-10 加「指向父目录的可信 junction 必须通过」）。

**实测：** 直接加载契约模块跑了三组用例 —— 可信 junction 指向父目录（通过）、互指的一对（拒）、普通目录（通过）；
`has_lone_surrogate` 三例含一个非 BMP 对照组；`host_identity_ok` 七例覆盖三条路的成立与不成立。

**顺手修的一处：** 新写的 docstring 里 `C:\Trusted` 的 `\T` 是一个无效转义，Python 只发 `SyntaxWarning`、
`mypy` 不报。改成原始字符串，并按 `-W error::SyntaxWarning` 重新加载整个模块确认没有第二处 ——
这是本文件集里同类问题的第二次（上一次 `\U` 是硬错误，当场就被 `mypy` 抓到；这一次是警告，不主动去找就看不见）。

**这一轮的教训：** 第三条是新的一类，值得单列（方法规则 22）。前两条各是老规则的新实例：
一个**配置取值**没有读者（规则 18），一个集合被两种含义共用（规则 17 的近亲 —— 这次不是一个取值兼两种意思，
而是一个集合装两种成员）。

## 1.18 rev 42：契约模块的行为测试

**动机（数据）：** 十五轮评审 281 条发现里，约 10 条由上一轮的修复引入。按落点分：

| 落点 | 自伤条数 | 当时的行为测试 |
|---|---|---|
| `scripts/check_design_set.py` | 6 | 54 条 |
| 契约模块 | 4 | 0 条 |

前 6 条是同一形状（一条规则多个消费点，只改了一个），写方法规则没能止住它，把判据提成
`gates_for()` / `anchored()` 两个共用函数之后才止住（rev 38）。后 4 条全是「上一轮的修复被悄悄撤销」，
而它们所在的四个函数一条测试都没有。

**做了什么：** 新增 `tests/test_powershell_contracts.py`，61 条。边界写死：**只测有实体的函数**
（46 个；另有 25 个 `raise Unspecified` 接缝与 22 个 `...` 的 Protocol 方法，两类都只有签名），接缝一律 `monkeypatch` 打桩、绝不对它断言；每条测试点名它钉的规则 ID。
覆盖 EFF-01/06/07、IMG-01/03b/05a/06c、ENV-06f/06g、LAUNCH-08a/08e/09b/09e、SPEC-04a/05c/08b/08c、
CFG-02a/02c、LADDER-05。

**证伪：** 12 处修复逐个改回旧写法，确认对应测试**变红**再还原；契约模块最后与备份逐字节比对。
证伪脚本自己先断言每处待替换文本在全文中恰好出现一次 —— 本轮之前有三次证伪因为替换没生效而什么都没证明。

**边界（不夸大）：** 回归套件挡的是自伤那一族。rev 39 的排印引号注入、rev 41 的死配置都来自评审者
想到了一个没人想过的用例，任何回归套件都发明不出它们。它管的是「修复不被下一次修复悄悄撤销」，
不是「找出新缺陷」。

## 1.19 rev 43：外部 code-review 与它交回的两条设计判断

**十五条外部发现 + 两条自查。** 十三条由评审直接改进工作树；两条它标为「需要设计决定」交回，都在本轮定案，
其中一条它建议的方向是反的。

| 交回的两条 | 评审的建议 | 定案 |
|---|---|---|
| `derive_rung(CMD, POSIX)` 导出 `Rung.cmd` | 不动 —— CFG-02a 写的是无限定的 `cmd` → `cmd`，且有测试钉着 | **改规范**：固定表三行里只有 `cmd` 不读目标平台；补限定，POSIX 目标上拒绝该来源 |
| `IdentityOracle.canonicalize` 零调用点 | 不动 —— 删它要连着动 `ORACLE_METHODS`、`oracle_complete` 与 G24-11 | **保留并接上**：调用点是 `filtered_path_entries` 接缝，在它的义务文本里点名（方法规则 24） |

**自查那一条。** 评审对 env 检查的修复（子串 → 整词 + `IGNORECASE`）在它举的例子上成立，但在真文件上
仍有 47 处小写 `path` 参数名能答掉 `Path`。改成只认原样与全大写两种拼法（方法规则 23）。

**另有一处工作树损伤，是这轮自查顺带抓到的：** `## 3. 已否决的备选` 这一行重复了两遍。
`duplicated_phrase` 看不见它 —— 那条检查要 30 个字符，而这行标题不到。补一条「相邻两行完全相同」的结构检查，
全套文件扫过没有第二处。这与 rev 15 那次「批改后必须核结构计数」同族：**插入写成了替换，机检要看得见**。

**证伪。** 三处有行为的改动逐个改回旧写法，确认对应测试变红再还原（`names_key` 的折叠、`derive_rung` 的
平台位、结构检查的相邻重复行）；接缝文本与门槛行是文档改动，由 G04-04、G14-03 承接。五道闸全绿：
设计集检查、`mypy --strict`、`pytest tests/test_design_set.py tests/test_powershell_contracts.py`、
`ruff check .`、引用校验。本轮之后机检 56 条、契约 61 条，全套 117 条绿（§1.18 里的 54 / 61 / 115 是 rev 42 当时的数）。

## 2. 方法规则

**四十轮评审产出了下面这些规则 —— 轮次数取自表头，不是另记一份；**规则条数则要自己数，rev 32 加了第 16 条而这句话
底下的计数没跟上，rev 33 在自查里补回（方法规则 8）。**每一条都是因为「没有它」而漏过了缺陷，并按漏过的次数排序。**

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
   逐一套到每条自称「封闭」的规则上之前，什么都审不到。**又过了八个版本才被拿去审 ENV-03：** 威胁模型
   说整个继承环境不可信，ENV-03 却是三个名字的移除清单，漏掉的情形（`GIT_CONFIG_*`、`NODE_OPTIONS`）答案
   是「透传」。「封闭」二字不在规则里，规则就不会被这条审到 —— 要按**威胁模型列出的每一个不可信输入**去问，
   不按规则的措辞去问。
5. **当它说「无锁」时数一数写者；当它说「同今天」时核实今天。**
6. **由被检查者自己求值的检查不是检查。** 跑在解释器内部的守卫无法认证这个解释器；静态路径不等于
   不可变字节。**一个程序也不能靠「把它启动起来」来认证** —— 启动正是这道检查要门住的那个事件，而
   子进程报告的关于它自己的每一个字段，都是受怀疑者报的。选择必须在宿主侧、在第一个字节执行之前
   决定。
7. **跑代码。** §2.7、§2.12、§3.4、§3.8–§3.12 与 §3.14–§3.16 之所以存在，是因为对它们每一条，推理都
   给出了错的答案。
8. **当某一版写下一条教训时，先拿它审这一版自己 —— 包括写下它的那一次编辑本身。** 记下第 1 条的那一轮，在同一遍里就违反了第 1 条。
   **一个关于本文的数字，就是本文里的一条断言：** 这一行曾写着十八轮，而表头写着十九轮 —— 因为表头被
   更新了，规则底下这句没有；每一个自指的计数，在它所计的东西变动时都必须重新推一遍。**rev 37 又加一句：
   「这一版自己」包括写下这条规则的那一次编辑。** rev 36 写了方法规则 19（完整性要有一处真的去数），
   而它漏掉的三个必答字段就在同一个函数里；rev 36 给规则 11 补了「代码那一句」，同一次编辑却只镜像了
   评审包三个条件里的一个 —— **镜像一个条件不是镜像那道检查**。写下一条规则的那一刻，最该被它审的
   就是手边这几行。
9. **一道可以判红的门槛什么都门不住。** 把发布门槛与刻画性探针分开，并把预期结果写进探针（§6）。**但「写进预期」只对
   已经量过的事成立：** rev 28 给 G21-17 预置了「预期该 DLL 被从工作树加载」，而那正是探针要去问的东西 —— 而且推错了，
   PowerShell 的位置是运行空间状态、与进程当前目录不是一回事，残留按 rung 根本不一样。**能推出来的不需要探针，推不出来的
   就不许把答案写进去** —— 这类探针写成「记录这三样实测值」，任一方向变化仍然让套件失败。同一个错误在 hooks 计划的
   rev 16 已经犯过一次（翻案清单预填探针答案），这是第二次。
10. **走不到的分支不是防线。** `allow_git_bash` 守的是 `cmd` 之下的一级，而每个受支持的 Windows 上都
    有 `cmd.exe` —— 于是开关、钉住顺序的那道门槛、以及测这一级的那支探针，全都绿在生产环境走不到的
    路径上。一条规则带顺序时，要检查其中每个位置都可达。
11. **改一条规则，不到「引用它的每一处摘要、表格与门槛都重读过」就不算改完。** rev 22 重写了 5a，却
    留下 TL;DR 与 §1 仍把 allowlist 当作替代项、一道门槛指着没有任何门槛调度的用例、§5 仍写「一个
    token 一个 task」—— 下一轮三条发现，全都是旧文本活在这次编辑没去过的地方。机械做法是 grep，在本轮
    收尾之前跑、而不是留给下一位评审，并且带上写下这条规则的那一轮所欠缺的三条子句：**先把空白归一**
    ——它漏掉的那处措辞正是被连字符折到了下一行；**每个孪生件按它自己的措辞各扫一遍**，因为翻译过的
    文档里根本没有你改的那个词；**清单是这一轮改过的每一个术语**，而不是产生了发现的那一个 —— rev 23
    改了谓词、清除列表与签名三样，只扫了谓词，另外两样留在了表里。**rev 36 又加一句：清单不只是文档。**
    rev 35 把「子规则可以靠母规则」这条关系在 `coverage` 与 `gates_naming` 两处改对，同一份脚本里另外三处消费同一关系的
    函数一处没动，于是主检查放行、评审包却报同一处有缝 —— **改一个谓词，要 grep 的是这条关系的每一个消费点，
    与改一条规则要 grep 每一处引用是同一件事。**

12. **同一条规则只能有一个定义点，其余地方只引用它的 ID。** rev 21–24 连续四轮的头条都是「改了一条
    规则、副本没跟上」，而规则 11 写下的那一轮自己就漏扫 —— 一条靠人记得去 grep 的规则，和没有这条
    规则的失效方式相同。所以 rev 25 把定义点收成一个：规范文件的不变量表；TL;DR、目标架构、PR 表、
    门槛与索引都只写 ID。`scripts/check_design_set.py` 机械地核：每个 ID 恰好定义一次、每个被引用的
    ID 存在、每个 ID 至少有一条门槛与一个 PR、Windows 专属用例落在 Windows job 的 PR 上、编号列表项
    不出现在行中、相邻重复短语（rev 23 的 `task.cancel()` 残句）不出现。**rev 38 把它从规则扩到谓词：**
    「一条规则算不算有门槛」「契约有没有锚住它」这两问，`check_set` 与 `--changed-since` 评审包各写了一份，
    于是 rev 36、37、38 连着三轮都是「检查接受的某条路径，报告没跟着写」。补条件只会多一处副本 ——
    提成 `gates_for()` 与 `anchored()`、两边都调，才是把定义点收成一个。**rev 40 补它的另一半：关系写成谓词，
    不要靠污染数据。** 为了表达「改了子规则等于动了母规则的门槛」，我把母 ID 并进了「实际改了什么」那个集合 ——
    关系是成立了，而集合的每一个其它读者从此读到假话：母规则的其它子规则也成了「已改」，一条只点名兄弟规则的
    门槛因此丢掉了它该发的告警。**加宽一个集合来表达一条关系，等于把这条关系强加给该集合的每一个使用者。**
    **rev 44 补第五次，而这次洞在单一定义点\*\*里面\*\*：** `gates_for()` 早已是「哪些门槛覆盖这条规则」的唯一
    定义点，可它内部有两条路由（矩阵行、规则自己那一行里的 `Gnn`），母子继承只写在第一条上。
    结果是覆盖与否取决于母规则\*\*碰巧是哪一种门槛\*\*：SUB / MCP / ENG 三族全靠 bullet 定义，给它们任何一条
    加子规则都会被判无门槛。**单一定义点不等于单一应用点 —— 一个谓词收进一个函数之后，还要问它函数体里
    的每一条分支是不是都应用了它。**
13. **每一级方言各自要有一条与 LOWER-02 同强度的语法闸规则，而每一个被门槛当作放行例子的名字，都要能按惰性定义逐字核过。** 二十二轮逐条评审
    加一轮拆分，都没有问「bash 那一级的语法闸在哪」和「惰性到底指什么」—— 逐条评审看的是每一条规则对不对，看不到整个层级缺了一条规则。
    整体性的问题要单独列成检查表项，而不是指望它从逐条评审里浮出来。
14. **规则表与伪代码、类型契约要互为门槛：每一条产生裁定的规则，伪代码里要有一条能到达的分支，类型里要有一个能表达它的值。** rev 27
    的十二条里八条是这道缝：EFF-05 说「非惰性」而 `EffectFlag` 没有这个值；`EXHAUSTED` 排在把它判成未知 rung 的检查之后；`floor()` 返回不了
    EFF-03 要的退出态；`select_rung` 对声明不可变的对象赋值；cmdlet 被送去做一次永远失败的 PATH 搜索。逐规则评审读的是表，逐函数评审读的是
    伪代码，谁都不读另一边 —— 所以要**从每条规则出发在伪代码里找它的分支，再从伪代码的每个分支出发在表里找它的规则**，两个方向各走一遍。

15. **封闭集要连成员判据一起写，然后逐个成员按判据核一遍。** rev 27 把子进程环境改成封闭集，关掉了 `GIT_CONFIG_*`
    与 `NODE_OPTIONS`；默认集却是照着一份典型环境列出来的，于是 `XDG_CONFIG_HOME`、`HOME`、`SSL_CERT_*` 这些同一类的键
    又被列了回去 —— **一张没有判据的白名单，和黑名单一样是靠直觉列的**，只是错的方向从「漏掉一个」变成「多列一个」。
    判据写下来（「值是不是一条路径」）之后，三类成员各自该怎么处理是可推导的，新键也有地方问。这是方法规则 4 的对偶：
    规则 4 问「清单漏掉的怎么办」，这一条问「清单里的每一个凭什么在」。**rev 40 把它推到划分上：**
    钉值分成「须过 IMG-01」与「只查形态」两类，判据从没写下来，于是 `PUBLIC` 按名字像不像系统目录归了类 ——
    而它是设计上人人可写的共享用户数据目录，IMG-06a 一条 `FILE_ADD_FILE` 就让每一个政策开启的 rung 被拒。
    **一个集合分成两类时，判据要写在划分上，不是靠逐个成员看着办**；写下来之后（「有没有规则依赖这个目录的内容」），
    新键有地方问，归错的也能被指出来。

16. **契约写成能过类型检查器的代码，不写成伪代码；规则格一行一条，超了就拆。** rev 27（十二条里八条）、rev 29（七条里五条，自查再两条）、
    rev 30、rev 31 的头条是同一类 —— 返回类型装不下规则要的东西、对声明冻结的对象赋值、解引用可能为 `None` 的 launcher、加变体不分字段 ——
    每一条都是 `mypy --strict` 对一个真 Python 模块零成本能报的（rev 32 用探针验过三类）；方法规则 14 要求的「两个方向各走一遍」于是有了机械的
    一半：每个 ID 在契约里至少锚一次。另一半是格子：rev 26 至 rev 31 改过的 24 行全部变长、没有一行变短，一格 2.9 KB 里有八条 MUST，门槛点名
    它却说不出测的是哪一句 —— **一个没有上限的格子，就是一份只在文件级做了单一定义、在句子级又回到多处定义的规范。** 上限（900 字节、三个
    句号）由 `check_design_set.py` 核；拆分按句子原文搬、机检拆前拆后逐字相等，语义偏离为零才叫结构修订。

    **rev 42 补一句：类型不是行为。** `mypy --strict` 查得出「返回类型装不下」「对冻结对象赋值」「解引用可能
    为 None 的 launcher」，查不出「这个分支再也到不了了」「这个字段没人读」「这条修复被下一次修复撤销了」——
    十五轮里约十条自伤发现，四条就长在契约里**一条测试都没有**的四个函数上。有实体的函数要按**行为**钉住
    （`tests/test_powershell_contracts.py`），接缝打桩、不断言；每处修复落地时把它的用例一起落下来，
    并**改回旧写法确认会红**。边界要说清楚：它挡的是修复互相撤销，不是找出新缺陷。
17. **判定过的东西到执行只能有一条路：执行那一端不许再收一次，而一个取值也不许兼职两种含义。** rev 33 的 P0 是前半 ——
    `decide()` 收 body 与工作目录、`launch()` 又各收一次，判定只核对了 spec 的对象身份，于是「判定 `Get-Date`、启动别的文本」
    是一次合法调用；**把第二个入口从签名里删掉，比在那里加一道比对更强**，因为没有第二份就没有什么可比。后半是它的对偶：
    `resolve_reparse() -> AbsPath | None` 里 `None` 同时表示「不是 reparse」与「解析不了」，调用方只能选一种读法，而它选了
    放行的那种。**问每个接口两件事 —— 这个值有几个来源，这个取值有几种意思；答案不是一，就是一个没被规定的分支。**
18. **每一个登记的字段都要有读它的那一处，而承诺一条规则的签名要收得到答那条规则所需的输入。** rev 35 九条里五条是这一条的
    两个方向。方向一，登记了没人读：`predicate_positions` 是 EFF-06 在契约里的全部落脚点，整个文件没有一处读它，
    而 `ArgPattern.matches` 的注释写着「已在调用方判不透明」—— 一句关于调用方的断言，没有任何调用方兑现；`caller_scope`
    读了，但只在两个分支里的一个读，于是 EFF-07 标在另一张表上的那半个含义没有出口。方向二，读了也答不出：
    `filtered_path` 承诺按 IMG-01 过滤而参数表里没有 oracle，`resolve` 承诺在过滤后的 PATH 上解析而收不到那份 PATH ——
    签名一旦答不出，实现只能自己再造一份，于是同一条规则有了两个不一致的答案。**这一类缺陷机检抓不到**：
    锚点检查证明规则在契约里被点过名，`mypy --strict` 证明类型自洽，门槛证明用例存在（G05-02、G04-18、G04-32
    这一轮一条都没改）—— 没有一样能证明那个分支不是空的。手法只有一个：**加一个登记字段或一条「按 X 规则」的接缝时，
    当场 grep 它的读者，并逐一核对读者的参数表里有没有答案的来源。** **rev 38 补上它的对偶：每一个读进来并
    通过校验的输入，也要有写下它的那一处。** 显式 `shell.path` 过了三道检查，导出的 rung 恰好政策关闭，
    而那条构造路径没有装它的字段 —— 校验因此是为一个随即被丢掉的值做的，用户点名 `/bin/zsh` 与不点名产出同一个指纹。
    **一个值被检查过，不等于它被留下来了。**
19. **一份「完整性」的承诺要有一处在运行期真的去数 —— `Optional` 字段与 `Protocol` 方法都强制不了它。** rev 36 的两条 P1
    是同一件事的两面。`PinnedEnv` 的字段声明成 `AbsDir | None` 是为了让同一个记录能装两个平台，可这样一来「Windows 上答不出
    `SystemRoot`」就是一个**合法取值**，校验器查了形态、查了跨平台的 `None`，唯独没查在场，而下游对 `None` 的处理是
    「这个键不出现」。`IdentityOracle` 是 `Protocol`，规范却写着「缺任一方法 ⇒ 该 rung 未认证」—— 静态协议对执行器递进来的
    对象一个字都强制不了，缺席不是拒绝，是 `launch()` 里一次 `AttributeError`，而那时这次调用已经判为放行。
    **凡是规范说「齐全 / 封闭 / 缺任一即拒绝」的地方，都要有一份显式的清单和一处显式的清点**，并且清点要发生在
    「基于它作出裁定」之前，不是在第一次解引用的地方。**rev 37 补一句：非 `Optional` 的注解同样强制不了什么。**
    `PinnedEnv.home` 声明成 `AbsDir`，可它由 oracle 的答案构造，`None` 照样进得来，而同一个函数里的形态检查
    明写 `v is None or …` —— **「这个字段不可能是 None」是类型检查器的结论，不是运行期的事实**，只要产出它的
    那一端不在类型检查的覆盖里。
20. **一道新加的 fail-closed 闸放在哪里，与它拒什么同样重要 —— 先问它上游挡住的路径里，有没有一条规则明写「这一支照旧运行」。**
    rev 34 的 P0 与 rev 37 最重的那条是同一个受害者：政策关闭的两级。前者是阶梯根本造不出它们；后者是我为了兑现
    SPEC-05c 的「缺任一方法即拒绝」，把完整性检查放在 `select_rung` 进门处 —— 而 SPEC-05c 自己的末句就是
    「只有政策关闭的 rung 照旧运行」，那两级恰恰在这道闸的下游、且根本不问 oracle。**规则的例外子句要和规则本体
    一起实现，否则实现的是另一条规则。** 机械做法：写下这道闸的那一刻，把它所在函数的每一条 `return` 数一遍，
    问哪几条从此到不了了 —— 而不是等下一轮评审替你数。**rev 39 是它的第三次记账，而且是修它时引入的第二版：**
    把 `target_filesystem_is_local` 算进选级的必答项，一个只缺这个方法的默认执行器又走空了 —— 那条例外写在
    SPEC-04a 里（「答不出读作 false」），不在 SPEC-05c 里，**例外子句常常不和规则住在一起，所以要按受害者找，
    不是按规则找**：问「这道闸挡住的那两级，全文有没有别处许诺过它们」。
21. **判定扫的是 body；agentao 自己拼进命令行的每一段文本，一道语法闸都不管。** rev 39 的 P1 是十三轮里第一次
    打在这一半上：`<W>` 由 `encode_workdir` 编码后拼进前奏，而 `analyse_body` 只看 body，LOWER-01 与 BASH-01
    的闸也都在它之外 —— PowerShell 认五个单引号定界符而编码只双写了 ASCII 那一个，一个带 `’` 的工作目录
    就把命令接进了前奏。**清单是每一个进入命令行却不经判定的值**：工作目录、前奏里代入的 `<E>` `<V>` `<H>` `<C>`、
    环境值、launcher 路径。每一个都要问「它的编码规则是按哪一版词法写的，那一版认几种定界符」，
    并且答不确定时取拒绝 —— 猜错编码是一条注入，拒绝只是一次 `launch-cwd`。
22. **地板里抛出来的异常不是裁定。** rev 41 的第三条：工具参数走 JSON，一个 `\ud800` 转义解码后原样留在 Python
    字符串里，而 LAUNCH-08 的三套计量都要先编码 —— UTF-16 与 UTF-8 都拒绝落单的代理项，于是 `floor()` 在**任何分析
    之前**抛 `UnicodeEncodeError`。它不经过 DENY 通道：没有 `hardline:` 理由、不受 TOOL-03「地板的 DENY 不可被规则
    遮蔽」的保护，在 ACP 上还可能被上层包成一次工具错误让模型原样重试。**判定路径上每一个会抛的调用都要问：
    这个异常出去之后，谁把它变成裁定？** 编码、解码、路径规范化、正则与解析器都在这一类里；答案不该是「上层会 catch」，
    而该是判定之前一道显式的拒绝，带自己的理由。断言也要写成「返回了什么裁定」，不是「抛了什么异常」。

23. **收窄一个谓词之后，按它原本能通过的那一批重新量一遍 —— 不是只量发现里点名的那一个例子。** rev 43 的
    第十六条：env 检查原本用 `key not in contract`，对短键必真（`Path` 藏在 `AbsPath`、`join_path`、`PSModulePath`
    里），修法是整词匹配**加全大小写折叠**；发现里那个例子确实不再必真了，可契约里有 47 处小写 `path` 作为普通
    参数名，`Path` 照样能被一个从不提这个环境变量的模块答掉 —— **空洞没有被堵上，只是下移了一层**。要量的是
    「这条检查在这份真文件上还剩多少种通过方式」，不是「发现里那一条现在红不红」；量出来的 47 才说明该收到
    哪一步（只认原样与全大写两种拼法，因为 Windows 折叠的是环境键名，不是任意标识符）。
24. **契约里一个没有调用点的声明，不等于死配置 —— 先问有没有接缝欠着它。** rev 43 的第二条：`canonicalize` 在
    全契约只有两处引用（Protocol 声明与 `ORACLE_METHODS` 那一行），都不是调用，评审据此建议删掉它 ——
    与 rev 41 删 `PublisherTrust` 死路径同形，所以看着很像。但它的调用点是 `filtered_path_entries` 这个
    `raise Unspecified` 的接缝：那段义务文本要「剔除工作目录与项目根内的条目」，而 `path_within()` 明写收的是
    **两条已规范化的路径**，PATH 条目却是环境里的原始字符串。**接缝欠下的义务也是调用点**；删掉声明，会把一条
    还没实现的要求变成一条不存在的要求，而这一条正是 `..`、短名与符号链接绕开包含判定的那道门。区分二者的问题
    只有一个：**这个声明如果不存在，哪个接缝的义务文本会变得无法兑现？** 答得出，它就不是死的。

25. **一个子进程的沉默不是通过。** rev 44 的头条：`typecheck_contract` 在 mypy 退出码非零时把 stdout 与
    stderr 拼起来切成行，逐行报为失败 —— 而两条流都空的时候，那个列表推导得出空列表，`main()` 与
    `test_live_contract_typechecks` 都读作「类型检查通过」。真正会这样退出的恰恰是最该被看见的那些：OOM
    被 `SIGKILL`、插件崩溃、段错误。**把一个子进程的结果翻成裁定时，「它什么都没说」必须有自己的落脚处，
    绝不能落进成功那一支** —— 这和规则 22「地板里抛出来的异常不是裁定」是同一个问题的两面：那一条问
    「异常出去之后谁把它变成裁定」，这一条问「什么都没有出来的时候，谁说了通过」。同族的第三面在规则 18：
    **一条规则枚举出来的状态，要有一个装得下它的类型。** SPEC-04a 写着「缺席与答不出都读作 false」，而
    `target_filesystem_is_local() -> bool` 只装得下前一种，于是「答不出」除了抛异常无路可走，而那个异常会在
    政策关闭的两级都还没选出来之前穿出 `select_rung`；签名改成 `-> bool | None` 之后，`mypy --strict` 自己
    就会拦住旧写法。

26. **同一对值在两处比较，两处必须是同一条规则。** rev 45 的自伤条：`allowlist_entry_for` 一度按目标平台的
    路径规则宽松查找，而 `HashPin.matches` 仍按逐字相等确认。看起来是「先找到、再确认」的双保险，实际是把
    两种**不同的结果**压成了一条路 —— 一条大小写不同的 pin 会被找到、然后匹配失败，于是「这个映像没有被
    pin」变成了「这个映像不可信」。宽松的一侧在哪个方向上错，取决于调用方拿它当必要条件还是当放行路径：
    `trusted_image` 是前者（更严），`host_identity_ok` 是后者（更松），**同一处放宽同时朝两个方向偏**。
    做法：先决定这一对值按什么规则相等，再让每一处都用它；两处规则不同时，差的那一格必须有名字
    —— 这里是「谁来规范化用户写的路径」，它成了 q15，而不是被两条不一致的比较悄悄吸收掉。

    这一条也是规则 12「一条规则有几个消费点，只改了一个」的对偶：那一条说的是**同一条规则**漏了消费点，
    这一条说的是**同一个判据**长出了第二条规则。

27. **用探针核过的修复，不等于套件护住的修复。** rev 45 的第 12 条：上一轮外部评审的头条是「包装体识别只看
    body 的第一个 token」，它的修复我逐条探针核过、也如实报告了 —— 而全仓 grep `classify_body` 与
    `unreadable-command-word`，测试里**零命中**。探针的结论活在对话记录里，下一次重构读不到它；这一轮我恰好
    把那段代码整个换掉，是证伪习惯（改回旧写法看测试红不红）把这个洞照出来的，不是套件。

    与规则 16「回归套件挡的是自伤那一族，发明不出新缺陷」是一对：那一条说测试**能**挡什么，这一条说
    **没有测试**时什么都挡不住。判据很短：**一个修复报告完成之前，问「哪条测试改回旧写法会红」，答不出就还没完成。**

## 3. 已否决的备选

- **非本机执行器：把「解析—证明—启动」三段义务写进 `ShellExecutor` 契约，配一道合规门槛。** rev 22 记录、未采用：它把义务摊给
  每一个将来会发执行器的宿主。采用的是 LAUNCH-01 —— agentao 构造启动请求、请求携带它、执行器原样运行。两条都不成立时，每一个需要
  映像的命令词不透明。
- **让 WRAP-04 的 4b/4c 真正运行：把 `command_name_expr` 与 `command_invokation_operator` 加进 LOWER-02 的接受清单并限定字面形态。**
  rev 25 记录、未采用：那是对 codex 清单的偏离，要付 §3.17 说的那份逐 kind 审查成本；现状是这两个 kind 在第 5 步就不透明，规则 4 的
  三条分支是纵深（G04-29）。
- **`_PLAN_ONLY_TOOLS` 模式替代名字守护（TOOL-01）。** 仍是 q6 的备选，未否决、未采用。
- **ENV-06 的另一条路：把配置根留在默认集，靠 `value_ok` 判「主体可写」。** rev 28 记录、未采用：用户自己的家目录按定义
  就是主体可写的，这条谓词要么把 `HOME` 也拒掉（工具链全崩），要么给配置根开一个正好覆盖攻击面的例外。钉值不需要这个判断：
  值不来自环境，就没有可判的东西。
- **LAUNCH-09 更强的一档：前奏之后不切目录，改为每条命令自己带工作目录。** rev 28 记录、未采用：那要求地板重写模型写出的
  每一条命令（LAUNCH-07 禁止改动 body），而相对路径在 body 里到处都是。
- **ENV-06 的备选：给每条可信条目登记 `env_triggers`（哪些环境键让它把环境当代码或当配置读）。** rev 27 记录、未采用：`git` 一家就有几十个
  这样的键（`GIT_CONFIG_*`、`GIT_EXEC_PATH`、`GIT_SSH_COMMAND`、`GIT_PAGER`、`GIT_EDITOR`、`GIT_ASKPASS`……），按方法规则 4 这是黑名单；采用封闭透传集 +
  取值检查 + 用户级扩展，代价记在 q13。代理变量留在默认集里：它们改流量去向，不改子进程跑什么。
- **`rebinds_caller` 的另一条路：对文件目标做带内容身份绑定的递归分析。** rev 27 记录、未采用：绑定要求子进程读到的字节就是地板读过的字节，
  而 LAUNCH-07 禁止改写 body（不能把文件内容内联进去），静态路径不等于不可变字节；文件目标维持一律不透明，传播只经字面串重新进入。
- **`env_delta` 保留为补丁形式，由执行器施加。** rev 27 记录、未采用：封闭集是基础环境的函数、不是补丁，取值检查本就要读到值；非本机的基础环境
  仍由 oracle 答出，请求里照旧没有地板机器的值。
- **LAUNCH-09 的备选：进程级或机器级的 DLL 搜索缓解。** rev 27 记录、未采用：进程级的 `SetDefaultDllDirectories` 只作用于调用它的进程、不随
  `CreateProcess` 继承；把当前目录踢出搜索顺序的是机器级注册表项，不是启动参数；逐程序审计每个可信工具链的探测清单则没有尽头。
