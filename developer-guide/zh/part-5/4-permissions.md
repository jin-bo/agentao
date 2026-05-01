# 5.4 权限引擎（PermissionEngine）

**PermissionEngine 是 Agentao 的第一道防线**——它在工具真正执行前做规则判决，把"明显安全"和"明显危险"两类请求自动处理掉，只让边缘情况进入 `confirm_tool()`（[第 4.5 节](/zh/part-4/5-tool-confirmation-ui)）。

## 两层防御模型

```
                ┌────────────────────────┐
 LLM 发起工具 ─► │ 1. PermissionEngine   │──┬─ ALLOW → 直接执行
                │   (JSON 规则 + 预设)   │  ├─ DENY  → 直接拒绝
                └────────────────────────┘  └─ ASK   ──┐
                                                       ▼
                                            ┌────────────────────┐
                                            │ 2. confirm_tool()   │
                                            │   (用户 UI 确认)    │
                                            └────────────────────┘
```

层 1 **零延迟**（JSON 规则匹配），层 2 **秒级延迟**（等用户点按钮）。好的配置让 90% 的工具调用根本不打扰用户。

## PermissionDecision

源码：`agentao/permissions.py:11-14`

```python
class PermissionDecision(Enum):
    ALLOW = "allow"   # 直接执行
    DENY  = "deny"    # 直接拒绝（Agent 收到 cancelled 字符串）
    ASK   = "ask"     # 走 confirm_tool 问用户
```

如果所有规则都不匹配（`decide()` 返回 `None`），Agent 回退到工具自己的 `requires_confirmation` 属性决定是否问。

## PermissionMode：四种预设

源码：`agentao/permissions.py:17-21`

| 模式 | 写操作 | Shell | Web | 适合 |
|------|-------|-------|-----|------|
| `READ_ONLY` | 拒 | 拒 | 拒 | 只读探索、审计 |
| `WORKSPACE_WRITE` | 允 | 按规则 | 白名单允 / 黑名单拒 / 其余问 | **生产嵌入默认** |
| `FULL_ACCESS` | 全允 | 全允 | 全允 | 开发环境 / 完全信任 |
| `PLAN` | 拒（仅 plan_* 允） | 拒（仅 git 等允） | 白名单允 | Plan 模式内部使用 |

**切换模式**：

```python
from agentao.permissions import PermissionMode

agent.permission_engine.set_mode(PermissionMode.READ_ONLY)
```

运行时可随时切换——下一次工具调用就生效。

## 规则 JSON 格式

**加载位置**：

```
~/.agentao/permissions.json      ← 用户级
<cwd>/.agentao/permissions.json  ← 项目级（优先级更高）
```

**基本结构**：

```json
{
  "rules": [
    {"tool": "read_file", "action": "allow"},
    {"tool": "write_file", "args": {"path": "^/tmp/"}, "action": "allow"},
    {"tool": "write_file", "action": "ask"},
    {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf"}, "action": "deny"},
    {"tool": "*", "action": "ask"}
  ]
}
```

**评估顺序**（最先匹配者胜）：

| 模式 | 规则顺序 |
|------|---------|
| FULL_ACCESS / PLAN | 预设规则 → 项目 JSON → 用户 JSON |
| 其他 | 项目 JSON → 用户 JSON → 预设规则 |

## 规则字段详解

### `tool` — 工具名匹配

```json
{"tool": "write_file"}     // 精确匹配
{"tool": "mcp_github_*"}    // 通配符（匹配前缀）
{"tool": "*"}               // 匹配所有
```

### `args` — 参数正则匹配

键是参数名，值是 Python 正则。**所有**键都必须命中才算规则匹配：

```json
{
  "tool": "write_file",
  "args": {
    "path": "^/tmp/safe-dir/"     // path 必须以 /tmp/safe-dir/ 开头
  },
  "action": "allow"
}
```

多个 args 同时要满足：

```json
{
  "tool": "run_shell_command",
  "args": {
    "command": "^docker ",         // 命令以 docker 开头
    "cwd": "^/var/app/"            // 且 cwd 在 /var/app/ 下
  },
  "action": "allow"
}
```

### `domain` — URL 域名匹配（专为 web_fetch 等）

```json
{
  "tool": "web_fetch",
  "domain": {
    "allowlist": [".github.com", ".docs.python.org", "r.jina.ai"]
  },
  "action": "allow"
}
```

**匹配语义**：

| 模式 | 含义 |
|------|------|
| `.github.com` | **后缀匹配**：匹配 `github.com` 和 `api.github.com`，不匹配 `notgithub.com` |
| `github.com` | **精确匹配**：只匹配 `github.com` 本身 |

一个 domain 规则里可以同时设 `allowlist` 和 `blocklist`——两者都算命中（所以要写两条规则区分 allow/deny，而不是一条）。

默认内置的 WORKSPACE_WRITE 预设已经带了一套合理的 allow/block 域名（参见下方）。

### `action` — 动作

```json
{"action": "allow"}   // 默认；也是未写 action 字段时的值
{"action": "deny"}
{"action": "ask"}
```

## 预设规则速览

WORKSPACE_WRITE 模式自带规则（源码 `agentao/permissions.py:68-118`）：

```json
[
  {"tool": "write_file", "action": "allow"},
  {"tool": "replace", "action": "allow"},
  // 只读 shell 命令白名单（git status/log/diff、ls、cat、echo、pwd、which、head、tail 等）
  {"tool": "run_shell_command", "args": {"command": "^(git (status|log|...)|ls\\b|cat\\b|...)"}, "action": "allow"},
  // 危险命令黑名单（rm -rf、sudo、mkfs、dd if=）
  {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf|sudo\\s|mkfs|dd\\s+if="}, "action": "deny"},
  // 其他 shell 问用户
  {"tool": "run_shell_command", "action": "ask"},
  // 可信域名直接允许 web_fetch
  {"tool": "web_fetch", "domain": {"allowlist": [".github.com", ".docs.python.org", ".wikipedia.org", "r.jina.ai", ".pypi.org", ".readthedocs.io"]}, "action": "allow"},
  // SSRF 目标直接拒绝
  {"tool": "web_fetch", "domain": {"blocklist": ["localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", ".internal", ".local", "::1"]}, "action": "deny"},
  {"tool": "web_fetch", "action": "ask"},
  {"tool": "web_search", "action": "ask"}
]
```

**SSRF 防护**：blocklist 包含常见内网/元数据地址，防止 Agent 被诱导访问内部服务。生产环境建议**扩展**而非删除这个列表。

## 程序式定制

把自定义 Engine 传给 Agent：

```python
from agentao import Agentao
from agentao.permissions import PermissionEngine, PermissionMode

engine = PermissionEngine(project_root=Path("/data/tenant-a"))
engine.set_mode(PermissionMode.WORKSPACE_WRITE)
# 动态加规则（比如根据租户订阅等级）
engine.rules.insert(0, {
    "tool": "mcp_slack_*",
    "action": "ask" if tenant.free_tier else "allow",
})

agent = Agentao(
    working_directory=Path("/data/tenant-a"),
    permission_engine=engine,
)
```

### 从宿主侧读取当前策略

需要在自家 UI 上展示当前策略（或写入审计日志）的宿主，调用 harness 合约里的稳定 getter `agent.active_permissions()`：

```python
snap = agent.active_permissions()
# snap.mode            -> "workspace-write"
# snap.rules           -> [...]                 # list[dict]，JSON-safe
# snap.loaded_sources  -> ["preset:workspace-write",
#                          "project:.agentao/permissions.json",
#                          "user:/Users/me/.agentao/permissions.json"]
```

`loaded_sources` 是稳定字符串标签：`preset:<mode>`、`project:<path>`、`user:<path>`、`injected:<name>`。MVP **不** 暴露逐规则 provenance —— 需要规则级 provenance 的宿主应将 `loaded_sources` 与自己注入的策略元数据组合。

宿主在引擎之上叠加额外策略时（运行期计算的 allowlist、租户级 overlay 等），通过 `add_loaded_source(...)` 标注自己的 provenance：

```python
engine.rules.insert(0, {"tool": "mcp_slack_*", "action": "ask"})
engine.add_loaded_source("injected:tenant-overlay")

snap = agent.active_permissions()
# snap.loaded_sources 中包含 "injected:tenant-overlay"
```

快照带缓存；缓存在 `set_mode()` 时失效，在 `add_loaded_source(...)` **传入新标签** 时失效（重复标签会被合并、不触发重建）。直接修改 `engine.rules` 不会让缓存失效 —— 原地改完后请补一次 `set_mode(engine.active_mode)`（同模式重设也会清缓存）或 `add_loaded_source("injected:<unique-name>")` 触发重建。

同一份数据也驱动公共事件流上 `PermissionDecisionEvent.loaded_sources` —— 详见[附录 A.10](/zh/appendix/a-api-reference#a-10-嵌入-harness-合约)。

## 典型配置模板

### 模板 A · 严格生产（客户端产品）

```json
{
  "rules": [
    {"tool": "read_file", "action": "allow"},
    {"tool": "glob", "action": "allow"},
    {"tool": "grep", "action": "allow"},

    {"tool": "write_file", "args": {"path": "^/workspace/"}, "action": "allow"},
    {"tool": "write_file", "action": "deny"},

    {"tool": "run_shell_command", "action": "deny"},

    {"tool": "web_fetch", "domain": {"allowlist": [".your-company.com"]}, "action": "allow"},
    {"tool": "web_fetch", "action": "deny"},

    {"tool": "*", "action": "ask"}
  ]
}
```

### 模板 B · 开发沙箱

```json
{
  "rules": [
    {"tool": "run_shell_command", "args": {"command": "^docker |^npm |^python |^node "}, "action": "allow"},
    {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf /|sudo|mkfs"}, "action": "deny"},
    {"tool": "run_shell_command", "action": "ask"}
  ]
}
```

### 模板 C · CI / 无人值守

CI 里你没人来点"允许"，所以应明确允许/拒绝，杜绝 "ask"：

```json
{
  "rules": [
    {"tool": "write_file", "args": {"path": "^/tmp/ci/"}, "action": "allow"},
    {"tool": "read_file", "action": "allow"},
    {"tool": "glob", "action": "allow"},
    {"tool": "grep", "action": "allow"},
    {"tool": "*", "action": "deny"}
  ]
}
```

## 与 confirm_tool 的协同

`decide()` 返回 `ASK` 时，Agent 会调 `transport.confirm_tool(...)` 问用户。所以你的 UI 只需处理"边缘情况"，不必每次工具都弹窗。

**验证规则是否生效**：

```python
from agentao.permissions import PermissionDecision

# 手动测试几个关键场景
for tool, args in [
    ("write_file", {"path": "/tmp/safe.txt", "content": "..."}),
    ("write_file", {"path": "/etc/passwd", "content": "..."}),
    ("run_shell_command", {"command": "rm -rf /"}),
    ("web_fetch", {"url": "http://127.0.0.1:8080"}),
]:
    dec = engine.decide(tool, args)
    print(f"{tool}({args}) → {dec}")
```

部署前把这段 sanity check 做成单元测试，保证预期的规则都命中。

## 常见陷阱

### ❌ 规则顺序写反了

```json
[
  {"tool": "write_file", "action": "ask"},
  {"tool": "write_file", "args": {"path": "^/tmp/"}, "action": "allow"}
]
```

第一条无条件匹配所有 write_file，第二条**永远不会被评估**。把更具体的规则放前面：

```json
[
  {"tool": "write_file", "args": {"path": "^/tmp/"}, "action": "allow"},
  {"tool": "write_file", "action": "ask"}
]
```

### ❌ 没有兜底规则

没写 `{"tool": "*", ...}` 兜底，未命中的工具会走各自 `requires_confirmation`——结果可能与你预期不符。**生产环境建议明确兜底**。

### ❌ 正则没转义

JSON 里 `\` 要写两次：`"rm\\s+-rf"`。

### ❌ allowlist 写成 `"github.com"` 想做后缀匹配

少了前导点就是**精确匹配**。想匹配所有子域名要写 `".github.com"`。

→ 下一节：[5.5 记忆系统](./5-memory)
