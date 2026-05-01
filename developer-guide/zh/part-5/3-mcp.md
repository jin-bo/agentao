# 5.3 MCP 服务器接入

> **本节你会学到**
> - 什么场景适合用 MCP，什么场景应当写自定义 Tool
> - Agentao 支持的两种 transport（stdio + SSE；**不支持** HTTP）
> - 多租户模式：会话级 `extra_mcp_servers`、环境变量展开、`trust:` 的边界

**MCP（Model Context Protocol）** 是"工具互操作的事实标准"。Agentao 作为 MCP Client，可以接入任何 MCP 兼容服务器——GitHub、Filesystem、Postgres、Slack、Jira、你自己写的……所有这些工具都会**自动**以 `mcp_{server}_{tool}` 的形式出现在 Agent 可用工具列表里。

## MCP 能做什么

| 场景 | 推荐的 MCP Server |
|------|-----------------|
| 读写文件 / 代码仓库 | `@modelcontextprotocol/server-filesystem` |
| GitHub issues/PR | `@modelcontextprotocol/server-github` |
| 数据库查询 | `@modelcontextprotocol/server-postgres` |
| Slack / Linear / Jira | 官方或社区 MCP |
| 内部工具 | 自建 MCP Server（见末尾） |

优势：**不用自己写 Tool 子类**——社区已经写好并维护。

## 配置的两种方式

### 方式 A · JSON 配置文件

**文件位置**（加载优先级，项目级覆盖用户级）：

```
~/.agentao/mcp.json         ← 用户级（所有项目共享）
<working_dir>/.agentao/mcp.json ← 项目级（优先级更高）
```

**格式**：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/code"],
      "env": {},
      "trust": false,
      "timeout": 60
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"
      }
    },
    "analytics-sse": {
      "url": "https://mcp.your-company.com/sse",
      "headers": {
        "Authorization": "Bearer ${ANALYTICS_TOKEN}"
      },
      "timeout": 30
    }
  }
}
```

### 方式 B · 程序式（嵌入首选）

构造 Agent 时通过 `extra_mcp_servers` 参数注入——**完全跳过 JSON 文件**，按会话/租户动态生成：

```python
from agentao import Agentao

agent = Agentao(
    working_directory=Path(f"/tmp/tenant-{tenant.id}"),
    extra_mcp_servers={
        "github-per-tenant": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": tenant.github_token},
        },
    },
)
```

合并规则：**同名覆盖** `.agentao/mcp.json` 的同名条目。

## 配置字段详解

### stdio 传输（子进程）

| 字段 | 必填 | 说明 |
|------|------|------|
| `command` | ✅ | 可执行文件（`npx`, `python`, 绝对路径） |
| `args` | ❌ | 命令行参数列表 |
| `env` | ❌ | 额外环境变量；支持 `$VAR` / `${VAR}` 从进程环境展开 |
| `cwd` | ❌ | 子进程工作目录 |
| `timeout` | ❌ | 初始化超时（秒），默认 60 |
| `trust` | ❌ | 为 true 时跳过工具确认 |

### SSE 传输（远程服务）

| 字段 | 必填 | 说明 |
|------|------|------|
| `url` | ✅ | SSE endpoint URL |
| `headers` | ❌ | HTTP 头；支持 `${VAR}` 展开 |
| `timeout` | ❌ | 秒，默认 60 |
| `trust` | ❌ | 同上 |

⚠️ **HTTP 不支持**：Agentao 的 MCP 客户端只导入了 `stdio_client` 和 `sse_client`，`http` 类型的 MCP Server 无法接入（ACP 握手也会通告 `mcpCapabilities.http: false`）。

## 环境变量展开

```json
"env": {
  "TOKEN": "${MY_TOKEN}",     // ${...} 形式
  "REGION": "$AWS_REGION"     // $... 形式
}
```

展开时机：**加载配置时**——也就是 Agent 构造时。展开后的字面量值进入子进程 env。

未定义的变量展开成空字符串（不抛错）。

## MCP 工具的命名

一个 MCP Server 发现的每个工具都被包装为 Agentao Tool，**名字加前缀**：

```
Server: "github"
MCP 工具: "create_issue"
Agentao 里的名字: "mcp_github_create_issue"
```

名字里的非 `[a-zA-Z0-9_]` 字符会被替换成 `_`。

这意味着：

- 你写自己的 Tool 时，**不要以 `mcp_` 打头**（避免看起来像 MCP 工具）
- 权限规则可以按前缀匹配：`{"tool": "mcp_github_*", ...}`

## 调试 MCP 接入

```python
# 查看所有发现的工具
for t in agent.tools.list_tools():
    if t.name.startswith("mcp_"):
        print(t.name, "—", t.description[:60])

# 查看 MCP manager 状态
if agent.mcp_manager:
    print(f"{len(agent.mcp_manager.clients)} server(s) connected")
```

日志文件 `agentao.log` 会记录：
- MCP Server 启动成功/失败
- 每个工具发现
- 工具调用参数和结果

## 自己写一个 MCP Server（3 分钟上手）

最小的 MCP Server 用 Python 写：

```python
# my_mcp_server.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-internal-tools")

@mcp.tool()
def get_user_info(user_id: str) -> str:
    """Query internal user info by ID."""
    return my_backend.get_user(user_id).to_json()

@mcp.tool()
def send_notification(user_id: str, message: str) -> str:
    """Send an in-app notification to a user."""
    my_backend.notify(user_id, message)
    return "ok"

if __name__ == "__main__":
    mcp.run()   # 默认 stdio
```

然后在 `.agentao/mcp.json` 里：

```json
{
  "mcpServers": {
    "internal": {
      "command": "python",
      "args": ["/path/to/my_mcp_server.py"]
    }
  }
}
```

Agent 重启后自动发现 `mcp_internal_get_user_info` 和 `mcp_internal_send_notification`。

## 多租户策略

生产 SaaS 里，典型 MCP 使用模式：

| Server | 谁写 | 范围 |
|--------|------|------|
| 官方/开源（github、filesystem、postgres） | 用户/运维配 | 全局或项目级 JSON 文件 |
| 你自己的业务 MCP | 你写 Python/Node | 每租户一个实例，经 `extra_mcp_servers=` 按会话启 |
| 租户自带的 MCP | 租户配置（SaaS 控制台） | 存 DB，构造 Agent 时翻译成 `extra_mcp_servers=` |

**安全要点**：
- 租户 token/密钥**永远不要**写进 JSON 文件——通过环境变量或 `extra_mcp_servers` 的 `env` 动态注入
- MCP 子进程**继承父进程环境变量**——确保没有泄漏其他租户的凭据
- 每会话独立子进程，避免跨租户状态污染

## 与权限引擎的配合

MCP 工具默认**也需要确认**（等同 `requires_confirmation=True`），除非配置 `trust: true`：

```json
{
  "mcpServers": {
    "trusted-internal": {
      "command": "...",
      "trust": true     ← 这些工具直接允许执行，不走 confirm_tool
    }
  }
}
```

或者用权限规则细粒度控制：

```json
{
  "rules": [
    {"tool": "mcp_github_get_*", "action": "allow"},
    {"tool": "mcp_github_delete_*", "action": "deny"},
    {"tool": "mcp_github_create_*", "action": "ask"}
  ]
}
```

权限详见 [5.4](./4-permissions)。

## ⚠️ 常见陷阱

::: warning 上线前先确认这几条
- ❌ **Server 启动失败但 Agent 静默继续** —— `agent.chat()` 不会暴露 MCP init 失败
- ❌ **工具名超长** —— provider 会截断，function calling 直接断
- ❌ **`trust: true` 用得太宽** —— 绕过所有安全确认

下面每一条都附完整修法。
:::

### ❌ Server 启动失败但 Agent 静默继续

Agentao 的 MCP 初始化是**容错**的——单个 Server 失败只会 log warning，不会阻塞 Agent 构造。检查 `agentao.log`：

```
MCP: failed to start 'github': ...
MCP: 12 tools from 2 server(s)       ← 有些 Server 没起来
```

部署前务必确认期望数量的 Server 都在。

### ❌ 工具名超长

有些 MCP Server 工具名很长。拼上前缀后可能超过 OpenAI function calling 的名字长度限制（64 字符）。如果发现 LLM 不认某工具，检查名字长度。

### ❌ `trust: true` 用得太宽

写过/删过东西的 Server 不要轻易设 `trust: true`——等于绕过所有安全确认。只给纯读或已经有自己权限层的 Server 用。

## TL;DR

- **MCP** 用来消费第三方工具生态（GitHub / 文件系统 / Postgres / Slack …）；**自定义 Tool** 用来封装你自己的业务逻辑。
- 两种 transport：**stdio** 子进程或 **SSE** URL。**HTTP 不支持。**
- 多租户 token：构造时传 `extra_mcp_servers`（`{name: {command, args, env}}`），同名会覆盖 `.agentao/mcp.json`。
- 工具命名：`mcp_{server}_{tool}` ——自动加前缀避免不同服务器的同名冲突。
- 写操作能力的 Server **绝对不要**设 `trust: true`，会绕过所有确认。

→ 下一节：[5.4 权限引擎](./4-permissions)
