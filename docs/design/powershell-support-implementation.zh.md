# PowerShell 支持 —— 实现阶梯（PR 依赖、模块归属、迁移顺序）

> ⚠️ **仅设计，未授权实施。** 本文只回答三个问题：哪个 PR 交付哪些规则 ID、每个 PR 碰哪些模块、先后顺序。
> 规则本身在 `powershell-support-spec.zh.md` §2，本文只引用 ID。§5 的阶梯是**依赖顺序**，不是排期。

**日期：** 2026-09-03 · **状态：** rev 25（拆分版，语义同 rev 24）
**文件集：** 见规范文件头。「§2.x」「§3.x」指证据文件。

## 1. PR 阶梯

| PR | 交付 | 实现的规则 | 用户可见 | 依赖 |
|---|---|---|---|---|
| PR-0 | **已拆出为 `subagent-runtime-safety-plan.zh.md`。** 子代理工厂、registry `origin`、`ToolForkable`、MCP 所有者线程与作用域视图、引擎写者锁与携带快照的裁定 | SUB-01–05、MCP-01–06、ENG-01–05（那边定义） | 否 —— 关上一处活绕过 | — |
| PR-1 | `ShellDialect`、spec 上的 `rung` 与 `filesystem_is_local`，构造时校验「方言 × rung」矩阵；`ShellRequest` 改为携带 agentao 构造好的启动请求（可判别体 + 环境 + 主体 + 证明映像），执行器原样运行 —— 今天它带 `command`、`cwd`、`timeout`、`on_chunk`、`env`，没有启动形态、主体或映像；执行器声明 spec；工具经 `ShellSpecProvider` 暴露；`_decide` 传递；替换时重跑名字守护 | TOOL-01、TOOL-04、SPEC-01、SPEC-02、SPEC-04、SPEC-06（字段）、LAUNCH-01 | **协议变更** | PR-0（SUB-01） |
| PR-2 | **只交付与运行期状态无关的原语：** token IR、LOWER-01 的十步、codex 的 fixture 语料、危险表、cmd 内部表、每条可信条目上的效果标志、按 rung 生效的政策开关、不依赖运行期状态的每一条 WRAP/EFF/NAME/CMD 规则 | TOOL-03、SPEC-03、TOK-01、TOK-02、LOWER-01–04、WRAP-01–07、NAME-01、EFF-01–07、CMD-01 | 否 | PR-1 |
| PR-3 | 预设；规则的 `dialect` 字段；`PermissionConfig`；用户级 `shell` 块（含 `allow_git_bash`、`allowlist`）；三个 composition root 的透传 | TOOL-02、CFG-01、CFG-02、CFG-03、LADDER-02 | 否 | PR-2 |
| PR-4 | 可信解析：IMG-01 的谓词、宿主侧 identity oracle（可注入）、解释器发现两档、从映像读身份、从磁盘读三来源配置、预检；裸词解析器（NAME-02、NAME-03）与按身份分的 cmdlet/alias 表（**必须在本 PR 建立的启动状态里量**）；子进程环境；逐级命令行与前奏；阶梯与走空 | IMG-01–09、NAME-02、NAME-03、ENV-01–05、LAUNCH-02–07、LADDER-01、LADDER-03、SPEC-05 | 否 | PR-2、PR-3 |
| PR-5 | 系统提示按方言渲染（`agentao/prompts/sections.py`） | —（渲染 SPEC-01 的方言，不定义规则） | 否 | PR-1 |
| PR-6 | `windows-latest` job：§5 启动矩阵、§3.12 哨兵、门槛矩阵里平台为 `windows` 的每一行 —— 集合不是区间：G19 与 G22 属于 PR-0、与平台无关；G25 的「容器 `root`」半在 ubuntu | —（只跑门槛） | 否 | PR-3、PR-4、PR-5 |
| PR-7 | 翻转：Windows 默认走阶梯；Git Bash 那一级在自己的开关后面、仅当 G20 绿时开启 | LADDER-04 | **是** | PR-6 |

**PR-0 不需要本阶梯的任何东西**（子代理计划 §5）。**PR-1 依赖 PR-0** 只因 SUB-01：子代理按身份持父级的
`shell`，否则本阶梯的每一条在子代理里都不生效（规范 §6）。**PR-4 需要 PR-2 与 PR-3，不只是 PR-1：**
它的裸词解析器把词交给 NAME-*，它的可信表带着 EFF-01 的效果标志 —— 两样都属 PR-2 —— 而它读 `shell.path`
与 `allow_git_bash` 用的那个 `shell` 块，是随 PR-3 的 `PermissionConfig` 一起到的。**PR-2 的依赖：**
`tree-sitter` 与 `tree-sitter-powershell` 在 `[project.dependencies]` 下带 `sys_platform == "win32"`，并在
`[dependency-groups].dev` 下无条件（§2.5 之后的 `pyproject.toml` 位置见证据）。

## 2. 模块归属

| 模块 | 今天 | PR | 改动 |
|---|---|---|---|
| `agentao/capabilities/shell.py` | `ShellRequest { command, cwd, timeout, on_chunk, env }`；`ShellExecutor` Protocol 只有 `run`/`run_background`；`LocalShellExecutor.run` 用 `shell=True, executable=resolve_shell_executable()` | PR-1、PR-4 | `ShellSpec`、`ShellDialect`、`Rung`、`LEGAL_PAIRS`；`ShellRequest` 携带 `LaunchRequest`；本机执行器按 LAUNCH-02、LAUNCH-03、LAUNCH-04 启动 |
| `agentao/tools/shell.py` | `ShellTool`，方言常量在 `:248-252` | PR-1、PR-5 | `ShellSpecProvider`；`shell_spec` 从 `_get_shell()` 暴露 |
| `agentao/tools/base.py` | `_get_shell()`（`:50-55`）；`ToolRegistry.register(replace)` | PR-1（PR-0 加 `origin`） | 名字守护（TOOL-01） |
| `agentao/runtime/tool_planning.py` | `_decide` 三层；`decide_detail(tool, …)`（`:498`） | PR-1 | 把工具的 spec 传给 `decide_detail`（TOOL-04） |
| `agentao/permissions.py` | `_LEGAL_RULE_FIELDS`（`:76`）；`args` 正则（`:747-750`）；hardline 不可遮蔽（`:684-694`） | PR-3 | 规则 `dialect` 字段（TOOL-02）；`PermissionConfig`（CFG-03） |
| `agentao/permissions_hardline/_scanner.py`、`_patterns.py` | 按工具名把门（`:155-156`）；18 类（`agentao/permissions_hardline/_patterns.py:35-37`）；Windows token 零命中（`:380`） | PR-2 | 方言分派；token IR；LOWER-01；EFF 标志；CMD-01；reason 词表 |
| `agentao/permissions_hardline/_powershell.py`（新） | — | PR-2 | tree-sitter 降级、21 kind 表、源码保真自动机、codex 语料测试 |
| `agentao/permissions_hardline/_trust.py`（新） | — | PR-4 | IMG-01 谓词、identity oracle 接口与 Windows 实现、解释器发现、身份读取、三来源配置读取、预检、按身份分的表 |
| `agentao/embedding/permission_loader.py`、`embedding/factory.py`、`acp/session_new.py`、`acp/session_load.py` | 用户级 `permissions.json` 只读 `rules`（`agentao/embedding/permission_loader.py:107-111`、`agentao/embedding/permission_loader.py:131-136`） | PR-3 | 读 `shell` 块；`PermissionConfig` 穿过三个 root（CFG-02、CFG-03） |
| `agentao/prompts/sections.py` | shell 提示写死（`:199-222`） | PR-5 | 按方言渲染 |
| `.github/workflows/ci.yml` | 8 个 job，零 Windows（§2.5） | PR-6 | `windows-latest` job |
| `pyproject.toml` | 无 tree-sitter | PR-2 | 依赖（上文） |

行号是 `main@3537753` 的，全部可在证据文件的同名引用下用 `scripts/check_citations.py` 解析。

## 3. PR-2 之前的五道决策门

规范 §7.3 的 **q2、q3、q9、q11** 定的是危险表、惰性集与 cmd 的 `rebinds_caller` 作用域 —— 全是 PR-2 的
交付物 —— 而 EFF-04 让「不在惰性集里」意味着 DENY 而不是污染后继，所以只要它们还开着，「PR-2 做完了」
这句话谁都说不出口。**q4 是第五道：** 它不改变 PR-2 造什么，因为有 `rung` 字段（SPEC-02、SPEC-03），原语
可以在不碰 `system_posix` 的前提下发出去 —— 它决定的是那个默认值，而一个「随代码一起到、从没被人选过」
的默认值，正是这条阶梯存在的意义所在。

## 4. 迁移顺序

1. PR-0（子代理计划）：引擎半先发，MCP 半后发；两者都不碰 shell。
2. PR-1 → PR-2 → PR-3 → PR-4：每一步都在 Windows 默认仍走 `%COMSPEC% /c` 的前提下落地，用户不可见；
   PR-1 是唯一的协议变更（`ShellRequest` 形状），宿主自定义执行器要跟着改，G01 断言这是唯一被迫的改动。
3. PR-5 与 PR-4 并行（都只依赖 PR-1 以上）。
4. PR-6 把平台为 `windows` 的每一行门槛跑起来；G20 红则 PR-7 关着 Git Bash 发布（LADDER-04）。
5. PR-7 翻转默认。

## 5. 英文版

本文件集当前**只有中文版**。英文版在进入实现之前一次性生成，并从那时起由 `check_design_set.py` 的孪生检查
核对规则 ID、枚举、伪代码块与门槛矩阵字节相同。
