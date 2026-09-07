# PowerShell 支持：轻量方案

**状态：** 已实施 · **日期：** 2026-09-06

**对照件：** `powershell-support-lightweight.md`。

> §1–§4 是当初的方案，逐条实现，未偏离。**§6 记录最终落到代码里的行为**，
> 以及实施过程中方案没有覆盖、由实现自己定下的几处。用户说明在
> `docs/reference/configuration.md` §4「`shell` 块」（中文版同节）。
> 退役的七份权威文件与规则编号框架已删除，历史在 Git 里；
> 那套评审产出的三十二条方法规则移到了 `docs/design/review-method-rules.zh.md`。

目标是在 Windows 上正确使用 PowerShell。**保留现有 legacy cmd，移除严格体系，再实现轻量 PowerShell。** 从当前 HEAD 修改，保留独立的 Windows 缺陷修复；不硬回退，不维护政策等级框架。

## 1. 配置与默认

本轮默认仍是 legacy cmd，PowerShell 显式选入：

| 配置 | Windows 行为 |
|---|---|
| 未指定 shell，或仅设 `dialect: cmd` | 现有 `%COMSPEC% /c`，环境和权限行为保持兼容 |
| 仅设 `dialect: powershell` | 自动发现 `pwsh.exe → powershell.exe`；均缺失时报错，不回退 cmd |
| `path` 与 `dialect` 成对指定 | 使用指定解释器和方言；错误不静默替换 |

允许单独指定 dialect，path 单独出现仍报错。`shell` 块未进入 v0.4.21；直接从加载器允许键集中删除 `ladder`、`allowlist`、`env_passthrough`、`allow_git_bash`，沿用未知键报错，不做兼容别名或迁移框架。删除 `LADDER_FLIPPED`；以后是否改变默认另行决定。

macOS/Linux 保持现有 POSIX 行为；显式选择 `powershell` 或 `cmd` 报不支持的平台配置错误。修改配置后重建会话或重启。

## 2. 启动与编码

- 自动发现按已知安装位置和父进程 PATH 中的绝对目录逐个拼接候选文件名、检查 `isfile`，不用 `shutil.which`，不隐式搜索项目当前目录；允许用户级安装，不自动选 Git Bash。固定选中路径，不在执行失败后换 shell 重试。
- **统一使用 `-EncodedCommand`**：固定前缀、原正文和固定退出尾行之间各加换行，按 UTF-16LE 编码后转 Base64；以 `shell=False`、固定解释器路径启动，参数为 `-NoLogo -NoProfile -NonInteractive -OutputFormat Text -EncodedCommand <base64>`。前后缀是 agentao 固定文本，不插入正文或配置的任何字节；正文保持原样。编码只解决传输引用，不免除 PowerShell 的代码解析。[微软参数说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1)
- 输出前缀使用 `$OutputEncoding = [System.Text.UTF8Encoding]::new($false); try { [Console]::OutputEncoding = $OutputEncoding } catch {}`，随后初始化 `$LASTEXITCODE = 0`。先设置管道写给原生命令 stdin 的 UTF-8 编码，避免后台控制台赋值失败时跳过它；只捕获控制台设置异常，不包住正文。控制台输出设置还影响 PowerShell 捕获原生 stdout 时的解码：原生程序若自行使用其他编码，直接输出或 `$out = native.exe` 都可能乱码，不能承诺统一转码。[管道编码说明](https://devblogs.microsoft.com/powershell/outputencoding-to-the-rescue/)
- 退出尾行先保存正文结束时的 `$?`：`$__agentao_ok = $?; if ($__agentao_ok) { exit 0 }; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; exit 1`。末条成功返回 0，失败时保留非零原生退出码，否则返回 1；显式 `exit N` 和终止错误由 PowerShell 自身退出。不直接追加 `exit $LASTEXITCODE`，避免 cmdlet 失败被旧的 0 掩盖。遵循末条结果，早先失败后又成功可能返回 0，不把整个正文改成 fail-fast。[退出状态说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_automatic_variables?view=powershell-7.6)
- 前缀、正文、尾行整体解析；尾部续行反引号、未闭合字符串或注释可能吸收尾行。语法错误交由 PowerShell 报错，不自动补闭合符；吸收尾行不一定产生语法错误，因此退出尾行的保证仅适用于能独立结束的完整正文。
- **CLIXML 只做限长文本提取，不引入 XML 解析器。** 保持 stdout/stderr 分离，在既有输出预算内扫描带 `#< CLIXML` 的内容，顺序提取完整 `<S ...>文本</S>`；只单次反转义 XML 五个预定义实体及合法数字字符引用，再单次解码 PowerShell `_xHHHH_`，不递归展开、不加载外部资源。遇到 DOCTYPE/ENTITY 声明、未知结构或截断包装，保留限长原文和诊断；普通文本不变。提取后仍遵守输出上限，`-OutputFormat Text` 不作为无包装保证。
- 前后台复用同一启动请求，cwd 直接传入。保留 `build_child_env()` 的环境继承和凭据清理，不过滤 PATH/PATHEXT，不关闭模块自动加载，不新增 `NoDefaultCurrentDirectoryInExePath`。`-NoProfile` 不加载用户 profile 中的函数和 alias。
- 每次调用从宿主当前环境构造子环境；安装程序不会自动更新已运行的 agentao 的 PATH。新工具通过绝对路径、显式更新宿主环境或重启后使用。
- 长度按**编码后的最终命令行**计算：UTF-16LE 字节数为 n 时，Base64 长度为 `4 * ceil(n / 3)`，另计路径、参数和终止符。复用已有长度检查提供友好错误，不另造限制框架；操作系统报告的超长命令或环境错误同样清楚返回，不截断、不自动改为临时脚本。

## 3. 权限与执行

**同一正文、相同权限开关下，旧通用地板拒绝的输入，PowerShell 新路径也必须拒绝。** 把旧通用检查提取成独立函数，legacy 原样调用，PowerShell 始终执行它，再叠加 Windows 危险检查；不能只在解析失败时兜底。保留旧检查的兼容性误报，不将其结果解释为 PowerShell 语义的完整证明。

**兼容决定：** 默认 legacy cmd 继续只跑旧通用地板，不新增 Windows 危险表。因此 `format C:`、`vssadmin delete shadows` 等 Windows 专属危险操作仍不由该地板拦截，后续权限规则照常执行；这是维持现状，不代表这些操作安全。

PowerShell 危险识别覆盖正名、内置别名族和合法参数缩写：`Remove-Item` 收 `rm/ri/del/erase/rd/rmdir`，递归参数覆盖 `-Recurse`、`-r` 等有效缩写，处理参数顺序和引用路径。只维护危险操作所需的小表，不恢复逐 build 的完整命令表；不承诺识别用户动态重绑的所有别名。

保留可复用的 PowerShell 分词/解析和危险表；成功提取的命令按命令位置检查，不能直接 search 原文而误判字符串或注释。降级失败本身不拒绝：通用地板仍执行，已有可靠危险命中仍拒绝，否则继续普通权限规则，绝不直接返回 ALLOW。

内部只保留必要的方言与启动数据：

- `_scanner.py::_policy_dialect` 不再受 `policy_enabled` 闸住；删除 rung/认证字段的强制不变量，legacy 保留原检查，PowerShell 调用上述组合检查。
- 保留 `LegacyLaunch`，将 `WindowsLaunch` 简化为固定目标、命令行、cwd 和 env；删除 `_Attested`、`verify_attested_launch` 及无消费者的严格请求类型，不新增 Basic/Strict 类型层次。
- 删除只写不读的 `spec_fingerprint`，不建立替代哈希体系。仍使用规划阶段确定的启动请求，hook 改写后重判，执行阶段不重新选择 shell。

**独立缺陷修复：显式 shell 路径被忽略。** 当前 `explicit_shell` 未送达真正启动目标；单独修复请求透传及 `_popen_target` 的消费，覆盖前后台，并验证用户指定的 cmd 确实运行。不能把这条修复混作严格功能删除。

## 4. 退役范围

不保留“仅严格路径”的规则和代码。实现时按引用删除消费者与对应测试；可复用函数先移到其实际使用模块。

| 范围 | 处置 |
|---|---|
| `_effects`、`_measured_commands`、`_wrappers`、`_bash`、`_cmd`、`_windows_identity` | 删除严格专用模块；危险表和基础扫描需要的词法函数先提取保留 |
| `_trust`、`_analysis`、`shell_spec` | 删除可信解析、封闭集、认证、严格环境和效果传播；只留基础发现、启动构造及规划所需数据。收掉 `Rung` 中无构造点的严格成员和只服务旧分层的枚举 |
| `classify_refusal` 及其测试 | 收掉退役理由族；无运行消费者的统计代码一并删除 |
| oracle/命令表/配置探针 | 删除 `scripts/windows_oracle_probe.py`、`windows_command_table_probe.py`、`windows_git_config_probe.ps1` 和 `.github/workflows/windows-oracle-probe.yml`；保留常规 Windows CI |
| 规范与契约 | 退役 IMG、NAME、EFF 及 TOK/LOWER/WRAP 中严格专用规则，重写剩余启动与配置要求。删除旧契约文件、专属门槛、实施阶梯及对应测试；历史由 Git 保存 |
| 文档机检 | 删除仅服务该设计集的 `scripts/check_design_set.py`、`tests/test_design_set.py` 及 CI 调用，清理失效导入和列表；保留独立的引用检查用途与子代理设计文档 |

现行 PowerShell 文档收敛为方案与用户说明两份：实施完成后在本文记录最终行为，不再维护七份权威文件和规则编号框架。独立 Windows 修复、通用权限测试和子代理设计不随之删除。

## 5. 顺序与验收

先单独修复显式 shell 路径，再在同一实施分支**删严格、建轻量、验收后合入**。当前严格 PowerShell 规划链在未携带 decided record 的地板调用上会拒绝干净正文，不把它当作需要兼容的既有 PowerShell 行为。删除阶段不单独发布；默认始终保持 legacy。

Windows CI 通过实际工具、规划器和权限引擎验证，依赖固定版本：

- **发现与启动：** PowerShell 7/5.1 的显式选择、缺失；在工作目录放同名候选，确认自动发现不隐式命中；空格、中文、引号、百分号、换行、cwd；最终编码命令行边界、超长正文和长 PATH 不截断。
- **输出与后台：** 两版本前台中文 stdout/stderr 不乱码；中文管道输入由读取 UTF-8 stdin 的原生测试程序验证，捕获 UTF-8 原生输出也不乱码。实际 `run_background` 使用 `DETACHED_PROCESS`、三个流 DEVNULL，让正文写完成标记并观察进程结束，不只断言拿到 PID；同时验证后台管道编码。无控制台行为必须在 Windows CI 实测。
- **CLIXML 文本提取：** 真实重定向错误转为可读文本；覆盖普通文本、字符引用、转义下划线、截断及超预算输入。带实体声明的输入不展开，外部引用不访问，输出始终限长；未知结构保留诊断，不静默丢失内容。
- **退出码：** `cmd.exe /c exit 7` 返回 7；成功原生命令之后的末条 cmdlet 报错返回 1；另测 cmdlet 单独报错、终止错误、显式 `exit 9`、失败后末条成功返回 0。记录尾部续行和未闭合结构的实际结果，不假定它们都报解析错误。前台核对返回值，后台观察实际进程退出，不把“启动成功”当成正文成功。
- **权限：** `Remove-Item -Recurse -Force C:\`、`ri -r -fo C:\`、`rm -Recurse -Force C:\`、`rm -rf /` 均拒绝；打印相同文本不新增误报。旧通用拒绝语料在新路径逐条保持拒绝；解析失败仍经过普通规则；至少一条干净正文通过真实规划链到达启动。危险用例只测判定，不执行。
- **开发：** checkout 小型项目，分别通过 `uv sync` 和 `python -m venv` 加 `pip install` 后构建、测试；用户目录工具及 `.venv` 可运行，裸名测试明确更新宿主 PATH。
- **兼容：** 未配置 Windows cmd 的命令、环境和权限行为不变；macOS/Linux 回归通过；管理员和普通用户均能使用轻量 PowerShell；删除的未发布键由正常未知键校验报错。

本轮只修订方案，未执行上述 Windows 验收；删除、修复及实测随实现完成。

---

## 6. 最终行为（实施记录）

### 6.1 模块与去向

| 现在在哪 | 是什么 |
|---|---|
| `agentao/capabilities/shell_spec.py` | 方言词汇、`ShellBlock`、`ShellSpec`、两种启动请求、`DecidedCall`。约 260 行，替代原来的 775 行 |
| `agentao/capabilities/powershell.py` | 自动发现、`-EncodedCommand` 包装与编码、长度测量、CLIXML 文本提取。新增 |
| `agentao/permissions_hardline/_scanner.py` | `generic_floor()`（提取出来的旧通用检查）与 `hardline_check()`（入口，组合两者） |
| `agentao/permissions_hardline/_windows.py` | Windows 危险表 + PowerShell 别名解析 + 递归删盘符根的结构化判定 |
| `agentao/permissions_hardline/_powershell.py` | tree-sitter 降级（保留）与 `scan_powershell()`（重写：降级失败返回 `None`） |

删除：`_analysis`、`_bash`、`_cmd`、`_effects`、`_measured_commands`、`_refusals`、`_trust`、`_windows_identity`、`_wrappers`
（约 4,700 行），以及四个 probe 脚本、`windows-oracle-probe.yml`、`scripts/check_design_set.py`、
`tests/test_design_set.py` 与十二份严格路径的测试文件。

### 6.2 方案没写、由实现定下的几处

1. **`ShellSpec` 不再有 `Rung`。** 方案只说「收掉无构造点的严格成员」。实测下来剩余成员（`pwsh`/`powershell`/`cmd`/
   `system_posix`）没有一个读者：`pwsh` 与 `powershell` 的区别就是解释器路径本身，而 spec 已经带着它。
   `ShellSpec` 因此是 `(dialect, interpreter)` 两个字段，`interpreter=None` 表示「用平台自己的答案」。
   `Platform` 枚举同样删除，`default_spec(windows: bool)` 就够。
2. **工具描述随 spec 走。** 方案未提。但描述是模型唯一一处「该写哪种语法」的说明，告诉它 `cmd /c` 而实际由
   PowerShell 读，等于让它每一次调用都写错语法。`ShellTool._invocation()` 读 spec；平台默认那一支走
   `shell_display_name()`，保持单一定义点（方法规则 30：`from … import` 会造出第二份拷贝，monkeypatch 打不到）。
3. **`dialect: posix` 在 Windows 上必须带 `path`。** 方案说「不自动选 Git Bash」，但没说这种配置的结果。
   报错，理由与「不回落 cmd」同源：候选（Git Bash / WSL / MSYS）在路径翻译和可达范围上并不一致，选错一个不会失败、
   只会变成别的意思。
4. **CLIXML 提取只对 PowerShell 启动生效。** `_format_result(powershell=...)`。对其他方言无条件跑一遍文本扫描，
   收益为零而多一条能改写用户输出的路径。
5. **超长命令行走 `LaunchRefused`。** 方案说「复用已有长度检查提供友好错误」，未说走哪条通道。用启动期拒绝，
   因为它不是策略判定：两个交付面都已经捕获它并按拒绝而非「启动失败」上报，正好是这条错误需要的形状
   （模型不会重试一条拒绝）。

### 6.3 与方案的一处差异

方案 §3 写「`_scanner.py::_policy_dialect` 不再受 `policy_enabled` 闸住」。实现里这个函数变成了
`_dialect()`，只返回方言值 —— 因为组合发生在 `hardline_check()` 里而不是在方言分发里：
通用地板先跑、总是跑，PowerShell 再叠危险表。这样 `_powershell.py` 不必反向 import `_scanner`，
包内的依赖方向仍是单向的。行为与方案一致。

### 6.4 Windows 首跑测出的三件事

§5 列出的 Windows 实测项由 CI 的 windows job 执行，本轮是它们第一次真正运行。前台一侧
（发现、启动、正文完整性、双向中文编码、退出码七种情形、CLIXML、超长拒绝、真实规划链到达启动、
危险正文只判定不执行）两个 Python 版本各 316 条全绿。后台一侧与 5.1 各测出几件事，
全部是**实测结论**，不是推理：

**后台启动用 `CREATE_NO_WINDOW`，不能用 `DETACHED_PROCESS`。** 原实现按 §2 写的
`DETACHED_PROCESS`，实测下 `pwsh` 与 `powershell` **都以退出码 0、stdout 与 stderr 全空的方式结束，
正文一条都没跑**（把两个流指向真实文件确认过是空，不是被丢弃；去掉前缀与尾缀的裸正文同样不跑）。
PowerShell 要有控制台才能自宿主，`DETACHED_PROCESS` 下没有。改成 `CREATE_NO_WINDOW`（给它一个
不显示的自有控制台）后正文正常执行。cmd 在两种 flag 下都正常 —— 这正是默认路径一直没暴露它的原因。
两个 flag 互斥，所以是替换而非叠加。`tests/test_powershell_launch.py` 里有一条跨平台的形状用例
把这个选择钉住，因为会把它改回去的那次编辑发生在非 Windows 机器上。

**Windows PowerShell 5.1 的 `Set-Content` 默认写 ANSI，中文变 `??`。** 后台用例原本用
`Set-Content` 写完成标记来验证正文跑完；换掉 `DETACHED_PROCESS` 之后正文确实跑了，但 5.1 写出来是
`?? finished`，pwsh 是对的。这是那个 cmdlet 的默认编码，与启动无关：前缀只负责 agentao 与子进程之间
两个**流**的编码，不改用户命令的语义 —— 何况 5.1 的 `utf8` 是带 BOM 的。用例改成用
`[System.IO.File]::WriteAllText` 指定编码器，量的才是它要量的那件事；文档里写明了这个坑。

**Windows PowerShell 5.1 管道给原生命令的内容开头带 UTF-8 BOM，这件事改不掉。** 在 runner 上实测
五种前缀写法（当前写法、只设 `$OutputEncoding`、先设控制台再设管道、用 `[Text.Encoding]::UTF8`、
以及**完全不加前缀**），5.1 五种全带 BOM，pwsh 五种全不带。所以它是 5.1 的行为，不是包装的问题。
顺带证明了前缀本身是有用的：不加前缀那一种是 `GOT:\ufeff??`，中文被打成问号。用例改为容忍开头的
BOM 并把测量写在旁边，配置文档也写明了，让管道下游自己去掉。

### 6.5 验收状态

本机（macOS）全套通过，`ruff check .` 通过。CI 21 个 job 全绿：Windows 两个版本的 shell 用例各
326 passed / 6 skipped，其后的全量套件各 4929 passed / 48 skipped；build 上首次接上的 `-m slow`
净装层 9 条全过。§5 的验收条件到此满足。
