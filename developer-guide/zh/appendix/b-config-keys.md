# 附录 B · 配置键索引

Agentao 读取的所有开关——先看环境变量，再看磁盘上的 JSON。除非标注**必填**，其余均为可选。

## B.1 环境变量

### LLM 凭据

`LLM_PROVIDER` 选一个**提供商前缀**（默认 `OPENAI`）。其余三个键按 `{PROVIDER}_API_KEY`、`{PROVIDER}_BASE_URL`、`{PROVIDER}_MODEL` 读。

| 键 | 必填 | 默认 | 说明 |
|----|------|------|------|
| `LLM_PROVIDER` | — | `OPENAI` | 选择 `{PROVIDER}_*` 前缀；任意大写名均可（如 `DEEPSEEK`、`ANTHROPIC`、`GEMINI`） |
| `{PROVIDER}_API_KEY` | **必填** | — | 构造器 `api_key=` 可覆盖 |
| `{PROVIDER}_BASE_URL` | — | 提供商默认 | OpenAI 兼容端点 |
| `{PROVIDER}_MODEL` | — | `gpt-5.4` | 运行时可用 `agent.set_model()` 切换 |

### 全局运行时

| 键 | 默认 | 含义 |
|----|------|------|
| `LLM_TEMPERATURE` | `0.2` | 采样温度（0.0–2.0） |
| `LLM_MAX_TOKENS` | 未设 | 单次调用的 LLM completion tokens 上限 |
| `AGENTAO_CONTEXT_TOKENS` | `200000` | 上下文预算；超出触发压缩 |
| `AGENTAO_WORKING_DIRECTORY` | — | 启动时覆盖工作目录（等价于构造器 `working_directory=`） |

### 内置工具读取的第三方键

| 键 | 使用方 | 作用 |
|----|--------|------|
| `GITHUB_TOKEN` | 技能目录抓取器 | 提升 GitHub API 频控上限 |

MCP 服务器和自定义工具通常有自己的 env 键——写在 `.agentao/mcp.json` 或自定义工具代码里，不在此表。

## B.2 优先级

每个设置：**构造器参数 > 环境变量 > 磁盘 JSON > 硬编码默认值**。

磁盘 JSON 内部分层：**项目 `<cwd>/.agentao/*.json` > 用户 `~/.agentao/*.json`**（同一键以项目为准）。

## B.3 磁盘 JSON 文件

所有文件位于 `.agentao/` 目录下。**项目**文件（`<working_directory>/.agentao/`）优先于**用户**文件（`~/.agentao/`）。任何文件都可缺省。

| 文件 | 作用域 | 章节 | 作用 |
|------|--------|------|------|
| `mcp.json` | 项目 + 用户 | [5.3](/zh/part-5/3-mcp) | MCP 服务器（stdio / SSE） |
| `permissions.json` | 项目 + 用户 | [5.4](/zh/part-5/4-permissions) | 权限模式 + 规则 |
| `sandbox.json` | 项目 + 用户 | [6.2](/zh/part-6/2-shell-sandbox) | Shell 沙箱 profile |
| `acp.json` | 仅项目 | [3.2](/zh/part-3/2-agentao-as-server) | ACP 服务器配置（Agentao 作为客户端时） |
| `memory.db` | 项目 + 用户 | [5.5](/zh/part-5/5-memory) | SQLite 持久化记忆（非 JSON；此处完整列出） |

### B.3.1 `mcp.json`

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "env": { "LOG_LEVEL": "info" },
      "trust": false,
      "timeout": 30
    },
    "remote": {
      "url": "https://api.example.com/sse",
      "headers": { "Authorization": "Bearer $API_TOKEN" },
      "timeout": 60
    }
  }
}
```

**字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `command` | string | stdio 服务器——与 `url` 互斥 |
| `args` | string[] | stdio 参数 |
| `env` | object | 会做 `$VAR` / `${VAR}` 展开 |
| `cwd` | string | stdio 子进程的 cwd |
| `url` | string | SSE 服务器 URL |
| `headers` | object | SSE 请求头（`$VAR` 展开） |
| `trust` | bool | 该服务器的工具跳过确认 |
| `timeout` | number (秒) | 单次工具调用超时 |

v0.2.x **不**支持 HTTP 传输，只支持 stdio + SSE。

### B.3.2 `permissions.json`

```json
{
  "mode": "WORKSPACE_WRITE",
  "rules": [
    { "tool": "run_shell_command", "args": { "command": "rm -rf *" }, "action": "deny" },
    { "tool": "web_fetch", "domain": "*.internal", "action": "deny" },
    { "tool": "write_file", "action": "allow" }
  ]
}
```

**模式**：`READ_ONLY`、`WORKSPACE_WRITE`、`FULL_ACCESS`、`PLAN`。
**规则动作**：`allow`、`deny`、`ask`。
**规则键**：必须有 `tool`，可选 `args`（局部匹配）和 `domain`（用于 `web_fetch`）。

求值顺序：显式规则 → 模式预设 → 写工具默认 `ask`。

### B.3.3 `sandbox.json`

```json
{
  "shell": {
    "enabled": true,
    "default_profile": "workspace-write",
    "allow_network": true,
    "allowed_commands_without_confirm": ["ls", "cat", "head", "git status"],
    "profiles_dir": "~/.agentao/sandbox-profiles"
  }
}
```

**内置 profile**：`readonly`、`workspace-write-no-network`、`workspace-write`。
**失败即禁用**：profile 文件缺失会抛 `SandboxMisconfiguredError`，shell 工具直接拒绝运行。

### B.3.4 `acp.json`

仅在 **Agentao 作为 ACP 客户端**时读取（见 [3.4 ACPManager](/zh/part-3/)——待补）。结构与 `mcp.json` 一致，只是键为 `acpServers`。大部分集成无需关心。

## B.4 构造器参数对应表

上面每个 env 或 JSON 键，`Agentao(...)` 都有对应参数：

| JSON / env | 构造器 |
|------------|--------|
| `{PROVIDER}_API_KEY` | `api_key=` |
| `{PROVIDER}_BASE_URL` | `base_url=` |
| `{PROVIDER}_MODEL` | `model=` |
| `AGENTAO_WORKING_DIRECTORY` | `working_directory=` |
| `AGENTAO_CONTEXT_TOKENS` | `max_context_tokens=` |
| `mcp.json` | `extra_mcp_servers=`（叠加在文件之上） |
| `permissions.json` | `permission_engine=` |
| `sandbox.json` | 无直接参数——策略在工具调用时读 |

构造器永远胜出。SaaS 宿主按租户注入配置而不改 env 时很有用。

---

→ [附录 D · 错误码](./d-error-codes)
