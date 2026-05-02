# 附录 B · 配置键索引

Agentao 读取的所有开关——先看环境变量，再看磁盘上的 JSON。除非标注**必填**，其余均为可选。

## B.1 环境变量

### LLM 凭据

`LLM_PROVIDER` 选一个**提供商前缀**（默认 `OPENAI`）。其余三个键按 `{PROVIDER}_API_KEY`、`{PROVIDER}_BASE_URL`、`{PROVIDER}_MODEL` 读。

| 键 | 必填 | 默认 | 说明 |
|----|------|------|------|
| `LLM_PROVIDER` | — | `OPENAI` | 选择 `{PROVIDER}_*` 前缀；任意大写名均可（如 `DEEPSEEK`、`ANTHROPIC`、`GEMINI`） |
| `{PROVIDER}_API_KEY` | **必填** | — | 构造器 `api_key=` 可覆盖 |
| `{PROVIDER}_BASE_URL` | **必填** | — | 构造器 `base_url=` 可覆盖；OpenAI 兼容端点 |
| `{PROVIDER}_MODEL` | **必填** | — | 构造器 `model=` 可覆盖；运行时可用 `agent.set_model()` 切换 |

> **失败即止规则：** `LLMClient.__init__` 在启动时立即检查。若 `{PROVIDER}_API_KEY`、`{PROVIDER}_BASE_URL` 或 `{PROVIDER}_MODEL` 任一缺失且未通过构造器传入，直接抛 `ValueError`。`/provider` 列表与切换命令同样执行此校验——三者必须全部设置，provider 才会出现在列表中并允许切换。

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
| `BOCHA_API_KEY` | `web_search` 工具 | 使用 Bocha Search API（结果质量更高）。不设置时自动回退到 DuckDuckGo，无需账号。 |

MCP 服务器和自定义工具通常有自己的 env 键——写在 `.agentao/mcp.json` 或自定义工具代码里，不在此表。

## B.2 优先级

每个设置：**构造器参数 > 环境变量 > 磁盘 JSON > 硬编码默认值**。

磁盘 JSON 的合并规则因配置面而异：

- **`sandbox.json`** — 项目文件覆盖用户文件中的同名键。
- **`mcp.json`** — 两个文件都加载；同名冲突时**用户胜**，项目级是**仅可新增**的（可声明新 server name，但不能覆盖用户级同名条目；冲突时打 warning 并跳过）。
- **`permissions.json`** — **仅用户级**。`<cwd>/.agentao/permissions.json` 故意不加载（一条 checked-in `{"tool": "*", "action": "allow"}` 会因首个命中即返回而当场作废用户策略）。模式预设规则最后跑（在 `full-access` / `plan` 模式下预设最先跑且不可覆盖）。
- **`memory.db`** — 项目与用户两个 store **独立**读取；prompt 渲染器同时可见两者；项目并不覆盖用户。
- **`acp.json`、`settings.json`、`skills_config.json`、`AGENTAO.md`** — 仅项目级；不做合并。

## B.3 磁盘 JSON 文件

JSON 配置文件位于 `.agentao/` 目录（项目位于 `<working_directory>/.agentao/`，用户位于 `~/.agentao/`）；项目级 `AGENTAO.md` 位于项目根。各配置面的合并优先级见 B.2。任何文件都可缺省。

| 文件 | 作用域 | 章节 | 作用 |
|------|--------|------|------|
| `mcp.json` | 项目（仅可新增） + 用户 | [5.3](/zh/part-5/3-mcp) | MCP 服务器（stdio / SSE） |
| `permissions.json` | 仅用户 *（项目级文件被忽略）* | [5.4](/zh/part-5/4-permissions) | 单工具权限规则 |
| `sandbox.json` | 项目 + 用户 | [6.2](/zh/part-6/2-shell-sandbox) | Shell 沙箱 profile |
| `acp.json` | 仅项目 | [3.2](/zh/part-3/2-agentao-as-server) | ACP 子智能体注册表（Agentao 作为客户端时） |
| `settings.json` | 仅项目 | [6.6](/zh/part-6/6-observability) | 持久化的权限模式、built-in agents 开关、replay 块 |
| `skills_config.json` | 仅项目 | [5.2](/zh/part-5/2-skills) | 已禁用技能列表（用 `/skills disable` 管理） |
| `AGENTAO.md` | 仅项目 | [5.6](/zh/part-5/6-system-prompt) | 项目专属指令，注入到 system prompt 顶端 |
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
| `trust` | bool | 该服务器的工具跳过确认。当 `true` 时，server 的 `ToolAnnotations`（`readOnlyHint`、`destructiveHint`）也会被读取；`destructiveHint=true` 会重新引入对该 op 的确认。`trust` 为 `false` 时注解被完全忽略。 |
| `timeout` | number (秒) | 单次工具调用超时 |

**不**支持 HTTP 传输，只支持 stdio + SSE。

### B.3.2 `permissions.json`

```json
{
  "rules": [
    { "tool": "run_shell_command", "args": { "command": "^git " }, "action": "allow" },
    { "tool": "run_shell_command", "args": { "command": "rm\\s+-rf" }, "action": "deny" },
    { "tool": "write_file", "action": "ask" },
    {
      "tool": "web_fetch",
      "domain": { "allowlist": [".github.com"], "url_arg": "url" },
      "action": "allow"
    }
  ]
}
```

**规则字段**：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `tool` | string | 是 | 工具名；通过 `re.fullmatch` 按正则匹配（用 `"*"` 通配） |
| `args` | object | 否 | `<arg_name>` → 正则的映射；**全部**条目都要 `re.search` 命中规则才生效 |
| `domain` | object | 否 | 仅 URL 类工具（`web_fetch`）；键：`url_arg`（默认 `"url"`）、`allowlist`、`blocklist`。`.` 开头的模式做后缀匹配（如 `.github.com` 命中 `api.github.com`），否则精确匹配 |
| `action` | string | 是 | `"allow"` \| `"deny"` \| `"ask"`（大小写不敏感） |

`mode` 字段**不**写在 `permissions.json` 里 — 它属于 `settings.json`（B.3.5），运行时通过 `/permissions` 切换。模式取值：`read-only`、`workspace-write`、`full-access`、`plan`（小写连字符；`plan` 为内部模式）。

求值顺序：

- `read-only` / `workspace-write`：`[用户规则] → [当前模式预设]`，命中即停。
- `full-access` / `plan`：`[当前模式预设] → [用户规则]`，预设不可覆盖。
- 都未命中 → 回退到该工具的 `requires_confirmation` 属性。

项目级 `<cwd>/.agentao/permissions.json` **不会**被加载 —— 见 B.2。

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

`servers.{name}` 下的每服务器键：

| 键 | 类型 | 默认 | 说明 |
|----|------|------|------|
| `command` | string | — | 必填 |
| `args` | list[str] | — | 必填 |
| `env` | dict | — | 必填；`$VAR` / `${VAR}` 会被展开 |
| `cwd` | string | — | 必填；相对路径相对项目根解析 |
| `autoStart` | bool | `true` | |
| `startupTimeoutMs` | int | `10000` | |
| `requestTimeoutMs` | int | `60000` | |
| `maxRecoverableRestarts` | int | `3` | 子进程可恢复死亡后的自动重启上限；首次成功对话后清零 |
| `capabilities` | dict | `{}` | |
| `description` | string | `""` | |
| `nonInteractivePolicy` | `{"mode": "reject_all" \| "accept_all"}` | `{"mode": "reject_all"}` | 结构化对象（Week 3）。**历史裸字符串形式在配置加载阶段直接报错**，迁移见 [附录 E](./e-migration)。 |

### B.3.5 `settings.json`

项目级运行时设置。从 `<working_directory>/.agentao/settings.json` 读取。包含三块：持久化的权限模式、built-in 子智能体开关、replay 块。

```json
{
  "mode": "workspace-write",
  "agents": {
    "enable_builtin": false
  },
  "replay": {
    "enabled": false,
    "max_instances": 20,
    "capture_flags": {
      "capture_llm_delta": true,
      "capture_full_llm_io": false,
      "capture_tool_result_full": false,
      "capture_plugin_hook_output_full": false
    }
  }
}
```

顶层键：

| 键 | 类型 | 默认 | 说明 |
|----|------|------|------|
| `mode` | string | `"workspace-write"`（缺省时） | 持久化的最近一次权限模式，用于恢复路径和 `/permissions` 查看。允许值：`"read-only"`、`"workspace-write"`、`"full-access"`。（`"plan"` 为内部模式，由 `/plan` 流程设置，用户不应直接写入。） |
| `agents.enable_builtin` | bool | `false` | 启用内置子智能体集。历史顶层别名 `enable_builtin_agents`（bool）仍被识别。 |

Replay 键：

| 键 | 类型 | 默认 | 说明 |
|----|------|------|------|
| `replay.enabled` | bool | `false` | 为后续 session 开启记录。`/replay on` 和 `/replay off` 会写这个值。 |
| `replay.max_instances` | int | `20` | `.agentao/replays/` 下的保留上限；不影响 `.agentao/sessions/`。 |
| `replay.capture_flags.capture_llm_delta` | bool | `true` | 记录每次 LLM 调用新增的 messages。 |
| `replay.capture_flags.capture_full_llm_io` | bool | `false` | deep capture 完整 LLM 输入/输出；按敏感内容处理。 |
| `replay.capture_flags.capture_tool_result_full` | bool | `false` | 在普通 replay 截断策略之外，deep capture 完整工具结果。 |
| `replay.capture_flags.capture_plugin_hook_output_full` | bool | `false` | deep capture plugin hook 输出。 |

`settings.json` 格式错误时会回退到安全默认值，不阻塞启动。

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
| `settings.json` replay 块 | 无直接参数——用 `/replay on/off` 或直接编辑文件 |

构造器永远胜出。SaaS 宿主按租户注入配置而不改 env 时很有用。

---

→ [附录 D · 错误码](./d-error-codes)
