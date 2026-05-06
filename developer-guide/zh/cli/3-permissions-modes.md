# 3. 权限与模式

CLI 在安全上做的最重要一件事就是**危险的工具调用前先问你**。本页讲三种权限模式、确认 UI、macOS 沙箱。

## 四种模式

Agentao 共有四种权限模式。三种用户可切；第四种 (`plan`) 由 `/plan` 设置，详见 [4. Plan 模式](./4-plan-mode)。

| 模式 | 写 & shell | 网络 | 确认 UI |
|---|---|---|---|
| `read-only` | **拒绝** | 拒绝 | 不出现 — 直接拒 |
| `workspace-write` | 安全操作放行 | **按域名问** | 风险操作时弹 |
| `full-access` | 放行 | 放行 | **永不弹** |
| `plan` | 拒绝 | 拒绝 | （只读研究模式，见第 4 章） |

`workspace-write` 是默认值，95% 时间应该用它。

## `/mode` — 查看或切换

```text
> /mode
Permission mode: workspace-write

> /mode read-only
✓ Permission mode: read-only  (write & shell tools are blocked)

> /mode full-access
✓ Permission mode: full-access  (all tools allowed without prompting)
```

切换会写入 `.agentao/settings.json` 的 `mode` 字段；下一次从同一项目启动会沿用这个模式。权限规则本身只从用户级 `~/.agentao/permissions.json` 读取 — 见 [10. 配置文件参考](./10-config-reference)。

::: warning 与 plan 模式的互斥
plan 模式激活时不能 `/mode` — 先 `/plan implement` 或 `/plan clear` 退出 plan，再切。`/mode` 会拒绝并提示。
:::

## 各模式具体拦什么

### `read-only`

不问直接拒：

- `write_file`、`replace` — 任何文件改动
- `run_shell_command` — 任何命令，包括只读的
- `web_fetch`、`web_search` — 任何网络

不问直接放：

- `read_file`、`list_directory`、`glob`、`search_file_content` — 被动读

什么时候用：让 agent 调查、解释，**绝不**改东西。代码评审、审计、"这个 codebase 在干嘛"的导览。

### `workspace-write`（默认）

不问直接放：

- 所有读工具
- `write_file` / `replace`，**作用范围在工作目录内**
- `run_shell_command` 的 safe-read 白名单（`ls` `cat` `grep` `git status` `git diff` `git log` `pwd` `which` `env` 等）

执行前问：

- `run_shell_command` 落到 safe-read 白名单外
- `web_fetch` 域名不在 allow / deny 列表里（默认列表预放行受信文档站，预拒绝 SSRF 目标如 `localhost`、`127.0.0.1`、`169.254.169.254`）
- `web_search`
- `write_file` 写到工作目录之外

不问直接拒：

- `web_fetch` 命中 blocklist 域名（SSRF 防护） — 不能通过弹框临时放行；要放就改 `~/.agentao/permissions.json`

什么时候用：日常开发。默认就是它。

### `full-access`

全放行，不弹确认。

::: danger 别让 full-access 一直开着
切到 full-access 是**整个会话级别**的决定。下一轮可能 `rm -rf`、可能把数据外泄、可能高频调付费 API。只在以下情况开：
- 在一次性 VM 或沙箱里
- 在跑一段非交互脚本，prompt 已经定死
- 已经评审过 plan，不想被 50 次确认弹框打断

干完事用 `/mode workspace-write` 退回去，重启 CLI 也会自动复位。
:::

## 确认 UI

工具需要确认时，agent 暂停、spinner 停下，你看到：

```text
⚠️  Tool Confirmation Required
Tool: run_shell_command
Arguments:
  • command: rm -rf node_modules

Choose an option:
 1. Yes
 2. Yes, allow all tools during this session
 3. No

Press 1, 2, or 3 (single key, no Enter needed) · Esc to cancel
```

单键输入，不必按回车。具体行为：

| 键 | 效果 |
|---|---|
| `1` | 跑**这一次**调用，下一次危险调用还会再问。 |
| `2` | 整个会话切到 `full-access`。**之后所有工具调用一律不再问**，直到你 `/mode workspace-write` 或重启。 |
| `3` | 取消本次调用。Agent 收到 `Tool execution cancelled by user` 的结果继续，通常会调整方向。 |
| `Esc` / `Ctrl+C` | 同 `3`。 |
| 其他键 | 静默忽略。 |

::: tip "取消" 是个真正的选项
按 `3` 不会破坏对话。Agent 看到取消信号通常会换方向（"那我换个做法"），你继续聊。该用就用。
:::

## `/sandbox` — macOS sandbox-exec（仅 macOS）

`/sandbox` 在 `run_shell_command` **下面**再加一层：即使 agent 拿到了执行 shell 命令的批准，macOS `sandbox-exec` profile 会限制这条命令实际能碰什么（文件系统、网络）。

Linux 和 Windows 没有这玩意，那两个平台 `/sandbox` 是 no-op。

```text
> /sandbox
Sandbox: enabled
  Default profile: workspace-only
  Workspace root:  /Users/you/projects/my-app
  Available:       workspace-only, network-allowed, strict-readonly
```

子命令：

| 命令 | 作用 |
|---|---|
| `/sandbox` 或 `/sandbox status` | 状态、默认 profile、工作目录根、可用 profile 列表 |
| `/sandbox on` | 本会话启用 |
| `/sandbox off` | 本会话停用 |
| `/sandbox profile <name>` | 切 profile（仅会话） |
| `/sandbox profiles` | 列出可用 profile 名 |

::: danger 配置坏掉时 fail-closed
`/sandbox` 已启用但 profile 配置坏（拼错、文件不存在），**所有 shell 命令都会失败**，直到你修好或 `/sandbox off`。状态输出会用红字标 `enabled but BROKEN`。这是有意为之 — 静默退化到"无沙箱"是安全倒退。
:::

## 规则从哪来

`/status` 会显示当前模式 + 一行 `Loaded sources:` 列出规则来源。项目级 `.agentao/permissions.json` 会被忽略；文件规则只从用户级读取。优先级：

1. `~/.agentao/permissions.json`（用户全局，自定义规则）
2. 当前模式的内建 preset（safe-shell 白名单、SSRF 黑名单等）
3. CLI `/mode` 切换（写入 `.agentao/settings.json`，决定当前模式）

要持久化规则改动，直接改 `~/.agentao/permissions.json`。CLI 启动时重新读取。

## 进阶：`/permission`

还有一个 `/permission` 命令用于不离开 CLI 查看当前生效规则。这是高级面，多数 CLI 用户用不到 — 完整参考见 [Part 5.4 · 权限引擎](/zh/part-5/4-permissions)。

## 容易踩的坑

- **确认 UI 看着卡住** — 弹确认时 spinner 会停。看着像冻住了，往上滚一下，菜单在那儿。
- **`workspace-write` 也会拦 cwd 外的写** — 项目在 `/repo/foo` 但你要让 agent 改 `~/Documents/...`，会弹确认。这是有意的 — "workspace" = 启动目录。
- **确认时按 `2` 整个会话翻车** — 没有 undo，只能 `/mode workspace-write` 或重启。把 `2` 当成"我不打算继续盯这个会话了"。
- **沙箱 profile 切换是会话级** — 想跨重启保持，改 `.agentao/sandbox.json` 或 `~/.agentao/sandbox.json` 的 `default_profile`。

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 先想清楚再动手 | [4. Plan 模式](./4-plan-mode) |
| 永久定制规则集 | [10. 配置文件参考](./10-config-reference) → `permissions.json` |
| 深入理解规则引擎 | [Part 5.4 · 权限引擎](/zh/part-5/4-permissions) |
| 看清这些默认值背后的威胁模型 | [Part 6.1 · 防御模型](/zh/part-6/1-defense-model) |

---

::: info 这一章在体系里的位置
CLI 的确认 UI 只是 harness `confirmation_callback` 钩子的一种实现。嵌入式宿主可以传自己的回调进 `Agentao(confirmation_callback=...)`，从而把 UI 换成 IDE 弹窗、Web 按钮、CI 审计日志等。模式模型和规则引擎在 CLI 和嵌入两条路径上完全一致。见 [Part 4.5 · 工具确认 UI](/zh/part-4/5-tool-confirmation-ui)。
:::

::: tip 真相源头
本页描述的行为锚定在 [`agentao/cli/transport.py:confirm_tool_execution`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/transport.py)（UI）、[`agentao/permissions.py`](https://github.com/jin-bo/agentao/blob/main/agentao/permissions.py)（规则）和 [`agentao/cli/help_text.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/help_text.py)（命令）。
:::
