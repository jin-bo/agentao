# macOS sandbox-exec 集成

## 概述

在 macOS 上，为 `run_shell_command` 工具的子进程加一层**能力限制** (capability restriction)，作为对现有"用户确认 + 权限引擎"**同意机制** (consent) 之外的 defense-in-depth。即使模型误操作或遭遇 prompt injection，被包装的 shell 子进程也只能在工作区内写、不能发网络请求、不能改系统文件。

本功能利用 macOS 内置的 `sandbox-exec`（Apple Seatbelt，TinyScheme profile），对用户完全可选——默认关闭，不改变任何现有交互流程。

## 设计原则

| 维度 | 选择 | 说明 |
|---|---|---|
| 作用范围 | 仅 `run_shell_command` | `sandbox-exec` 是 per-process 包装。`write_file` / `replace` / `web_fetch` 跑在 agent 宿主进程内，事后无法套沙箱——它们继续走权限引擎。 |
| 与 `PermissionMode` 关系 | 正交维度 | 沙箱是独立开关，不绑定到 `READ_ONLY` / `WORKSPACE_WRITE` / `FULL_ACCESS`。用户可以在 `FULL_ACCESS` 下仍开沙箱兜底。 |
| 与**同意**机制关系 | 叠加 | 权限引擎决定"是否允许"，沙箱决定"允许了之后能做什么"。两者语义分离。 |
| 平台 | macOS only | Linux / Windows 下 policy 静默 disabled。未来可加 `bwrap` 后端。 |
| 默认值 | 关闭 | 避免破坏依赖网络的 workflow（`npm install`、`pip install`、`git clone`）。用户按需 `/sandbox on`。 |

## 架构

```
ToolRunner._execute_one()  (tool_runner.py:252)
     │
     │ Phase 3: ALLOW decision
     │
     ├─► if tool.name == "run_shell_command" and SandboxPolicy.enabled:
     │       _args["_sandbox_profile"] = policy.resolve(...)
     │
     ▼
  ShellTool.execute(_sandbox_profile=..., command=..., ...)
     │
     ├─► _wrap_with_sandbox(command, profile, cwd)
     │   → "sandbox-exec -D _RW1=<cwd> -f <abs.sb> /bin/sh -c '<quoted>'"
     │
     ▼
  subprocess.Popen(wrapped_command, shell=True, ...)
     (shell.py:227 背景 / shell.py:262 前台)
```

### 关键模块

- **`agentao/sandbox/policy.py`** — 配置加载 + 决策。`SandboxPolicy.resolve(tool_name, args) -> Optional[SandboxProfile]`。
- **`agentao/sandbox/profiles/`** — 3 个内置 `.sb` 模板。
- **`agentao/tools/shell.py`** — 增加 `_sandbox_profile` 私有 kwarg 与 `_wrap_with_sandbox` 函数。
- **`agentao/tool_runner.py`** — Phase 3 执行前按策略注入 profile。
- **`agentao/agent.py`** — `Agentao.__init__` 初始化 policy 并透传给 `ToolRunner`。
- **`agentao/cli/`** — `/sandbox status|on|off|profile <name>` 命令。

## 配置

### 文件位置（遵循 `.agentao/` 约定）

| 文件 | 作用域 | 优先级 |
|---|---|---|
| `~/.agentao/sandbox.json` | 用户级 | 低 |
| `<project>/.agentao/sandbox.json` | 项目级 | 高（覆盖用户级） |

### Schema

```json
{
  "enabled": false,
  "platform": "darwin",
  "default_profile": "workspace-write-no-network",
  "rules": [
    {"tool": "run_shell_command", "profile": "workspace-write-no-network"}
  ],
  "profiles_dir": null,
  "workspace_root": null
}
```

| 字段 | 说明 |
|---|---|
| `enabled` | 总开关。`false` 时 `resolve()` 永远返回 `None`。 |
| `platform` | 非 `darwin` 则静默禁用。 |
| `default_profile` | 无 rule 命中时使用的模板名。 |
| `rules` | Per-tool override。当前仅 `run_shell_command` 有效。 |
| `profiles_dir` | 用户自定义 profile 目录；优先级高于内置。相对路径按配置文件所在层级解析：home 配置锚定 `~/.agentao/`，project 配置锚定 `<project>/`。 |
| `workspace_root` | 传给 `-D _RW1` 的工作区绝对路径；默认 `Path.cwd()`。相对路径解析规则同上。 |

## 内置 Profile

三个开箱即用模板位于 `agentao/sandbox/profiles/`：

### `readonly.sb`
- 读 `/`
- 写仅允许 `/tmp`、`/var/tmp`、`/dev/null`
- 拒绝网络
- 适合：让 agent 只做 code review / 分析，不能落任何文件

### `workspace-write.sb`
- 读 `/`
- 写仅允许 `$_RW1`（工作区）+ `/tmp` + `/var/tmp`
- **允许网络**
- 适合：日常开发，需要 `npm install` / `pip install` / `git clone` 等

### `workspace-write-no-network.sb`
- 同 `workspace-write.sb`，但拒绝所有网络
- 适合：离线 refactor / debug；对 prompt injection 的最强防线

### Profile 通用骨架（参考）

```scheme
(version 1)
(debug deny)
(import "system.sb")

(allow file-read* (subpath "/"))

(allow file-write*
  (subpath (param "_RW1"))
  (subpath "/tmp")
  (subpath "/var/tmp")
  (literal "/dev/null"))

(deny network*)  ;; 省略则默认允许

(allow process-exec (subpath "/usr") (subpath "/bin") (subpath "/sbin"))
(allow process-fork)
(allow signal (target self))
(allow mach-lookup
  (global-name "com.apple.system.notification_center"))
```

## CLI 命令

```
/sandbox status              显示 enabled、当前 profile、workspace_root
/sandbox on                  会话级启用（不落盘）
/sandbox off                 会话级禁用
/sandbox profile <name>      切换到指定 profile
```

持久配置请手动编辑 `.agentao/sandbox.json`。首次在 macOS 启动且配置缺失时，CLI 会打印一次提示：

```
Tip: enable macOS sandbox-exec for shell commands with /sandbox on
```

## 失败语义

沙箱拒绝（子进程出现 `Operation not permitted` + `sandbox-exec` 相关输出）不会让 agent 崩溃。`ShellTool` 识别到该模式后，把原输出 + 一段说明一起回传给模型，例如：

```
[Sandbox denied] The command was blocked by the active macOS sandbox profile
'workspace-write-no-network'. If this capability is intentional, ask the user
to run `/sandbox off` or switch profile via `/sandbox profile <name>`.
```

模型据此可以主动请求用户放宽，而不是把沙箱错误误判成命令本身 bug。

## Gotchas

- **`sandbox-exec` 被标 deprecated**（自 macOS 10.12）**但仍在 macOS 15 / Darwin 25 上正常工作**。Bazel、Claude Code 等均在用，短期不会被移除。
- 必须用 `-D _RW1=$(pwd)` 传工作区绝对路径；profile 里用 `(subpath (param "_RW1"))` 引用。
- 对子 shell (`/bin/sh -c '...'`) 完全友好：stdout、stderr、exit code 透明传播。
- **无法按域名限制网络**——只能 all-or-nothing。要域名级控制仍需靠 `web_fetch` 的 `PermissionEngine`。
- 进入沙箱前打开的 fd 仍可在沙箱内使用（不要在外面预开文件再传给子进程）。
- SIP / TCC 是独立层，不会被 sandbox-exec 绕过，也不会绕过它。
- 性能开销约 1–2%，可忽略。
- Profile 写错会让 `sandbox-exec` 返回非零退出码并打印 `Invalid sandbox profile` 之类错误——我们会把这个错误原样传给模型。

## 与现有机制的关系

| 机制 | 决定 | 生效位置 |
|---|---|---|
| `PermissionEngine` (`permissions.py`) | 工具是否被允许执行（ALLOW / DENY / ASK） | `tool_runner.py` Phase 1 |
| 用户确认 callback | 当决策为 ASK 时，用户手动点击 yes/no | `tool_runner.py` Phase 2 |
| **Sandbox policy**（本功能） | **允许后，子进程的能力边界** | `tool_runner.py` Phase 3 |

三层独立、可组合。即使 `allow_all_tools=True` 跳过了用户确认，沙箱仍在兜底。

## 验证

### 单元测试

```bash
uv run python -m pytest tests/test_sandbox_policy.py -v
```

覆盖：
- 项目 + 家目录 config 合并
- 平台非 darwin → 静默禁用
- `enabled=false` → `resolve()` 返回 `None`
- `_wrap_with_sandbox` 的 argv 正确（`shlex.quote`、`-D _RW1=...`）
- 内置 `.sb` profile 通过 `sandbox-exec -f <path> /bin/true` 预检（macOS only）
- 集成测试：`curl example.com` 在 `workspace-write-no-network.sb` 下被拒（macOS only）

### 手动 end-to-end（macOS）

1. `cp .env.example .env && <配置 key>`
2. 建 `.agentao/sandbox.json`：`{"enabled": true, "default_profile": "workspace-write-no-network"}`
3. `uv run agentao`
4. `run_shell_command("echo hello > /tmp/ok.txt")` → 成功
5. `run_shell_command("echo bad > /etc/passwd")` → 沙箱拒绝
6. `run_shell_command("curl https://example.com")` → 沙箱拒绝
7. `/sandbox off` 后重试 #6 → 成功
8. `/sandbox status` 与实际行为一致

### 跨平台回归

在 Linux / Windows 上：`uv run agentao` 不报错，policy 静默 disabled，shell 命令走原路径。

## 后续（本方案 out-of-scope）

- **Linux `bwrap` 后端**：`SandboxPolicy.resolve()` 接口已抽象，未来加 `BwrapProfile` 可并存。
- **Profile 自动生成**：基于 `.gitignore` / 项目结构推断写区域。
- **风险追踪**：在 `/memory` 里记录"用户放宽过哪些 profile"。
