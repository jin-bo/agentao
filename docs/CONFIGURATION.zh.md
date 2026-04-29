# 配置参考（Configuration Reference）

> **本文档定位。** 这是 Agentao 在启动或运行期读取的**全部配置面**的参考手册：文件路径、schema、默认值、优先级。它**只**记录"事实"，不解释"为什么这样设计"或"怎么用"——后者请跳到对应的 feature 文档。
>
> 如果你发现自己开始在这份文档里写动机段落，把它挪到 feature 文档里，这里只留一行链接。

英文原版：[CONFIGURATION.md](CONFIGURATION.md)。两份文档结构、段号、字段表完全一致；如果某次改动只更新了其中一份，请同步另一份。

---

## 1. 配置面总览

**用户可手动编辑的配置文件**：

| # | 配置面 | 项目路径 | 用户（全局）路径 | Loader | Feature 文档 |
|---|---|---|---|---|---|
| 1 | LLM 环境变量 | `.env`（cwd） | shell env | `dotenv.load_dotenv` → `discover_llm_kwargs` | —（参见 `.env.example`） |
| 2 | 运行模式 + 内置子代理 | `.agentao/settings.json` | — | `embedding/factory.py::_load_settings`、`plan/controller.py::_load_settings` | [TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md) |
| 3 | 工具权限规则 | `.agentao/permissions.json` | `~/.agentao/permissions.json` | `permissions.py::PermissionEngine` | [TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md) |
| 4 | MCP 服务器 | `.agentao/mcp.json` | `~/.agentao/mcp.json` | `mcp/FileBackedMCPRegistry`（见 `mcp/config.py`） | `CLAUDE.md` § MCP |
| 5 | ACP 子代理 | `.agentao/acp.json` | — *（仅项目级）* | `acp_client/config.py` | [acp-client.md](features/acp-client.md) / [acp-embedding.md](features/acp-embedding.md) |
| 6 | 禁用 skill 列表 | `.agentao/skills_config.json` | — | `skills/manager.py` | [SKILLS_GUIDE.md](SKILLS_GUIDE.md) |
| 7 | 项目说明 | `AGENTAO.md`（cwd） | — | `agent.py::_build_system_prompt` | [CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md) |
| 8 | 记忆库 | `.agentao/memory.db` | `~/.agentao/memory.db` | `memory/manager.py::MemoryManager` | [memory-management.md](features/memory-management.md) |

**内部状态文件**（自动管理；列出仅为告知，请勿手动编辑）：

| 配置面 | 路径 | 维护者 | 备注 |
|---|---|---|---|
| 后台子代理任务状态 | `.agentao/background_tasks.json` | `agents/bg_store.py::BackgroundTaskStore` | 锚定到 `working_directory`；未传 `persistence_dir` 时只在内存中。手改会与运行中的线程脱钩。 |
| 回放事件 | `.agentao/replay/*.jsonl` | `replay/` | 见 [session-replay.md](features/session-replay.md)。 |
| 会话 / 计划 / 工具产物 | `.agentao/sessions/`、`.agentao/plan-history/`、`.agentao/tool-outputs/` | 多模块 | 单次会话产物。 |

**优先级规则**（仅适用于同时存在项目与用户两份的配置面）：

- **Permissions** — 先读 user 文件，再把 project 文件**前置**到规则列表头。Project 规则**先于** user 规则求值（首个命中的规则胜出）。custom 规则之后，当前 mode 的 **preset** 规则放在最后求值（例外：`full-access` / `plan` 模式下，preset 规则放在**最前**且无法被覆盖）。
- **MCP** — 两份文件都会读；命名冲突时的覆盖策略由 `FileBackedMCPRegistry` 决定（依赖具体行为前请到该处确认）。
- **Memory** — project DB 与 user DB **独立读取**；prompt 里两者都可见，project 不会覆盖 user。
- 其他用户级配置面均为项目级独占——不存在合并。

---

## 2. `.env` — LLM provider 配置

- **路径。** `<cwd>/.env`。在 `embedding/factory.py::build_from_environment` 的最前面通过 `dotenv.load_dotenv()` 加载。
- **Loader。** `embedding/factory.py::discover_llm_kwargs`。
- **机制。** Provider 前缀化：`LLM_PROVIDER`（缺省 `OPENAI`）选定要读哪一组 `{PROVIDER}_API_KEY` / `{PROVIDER}_BASE_URL` / `{PROVIDER}_MODEL`。

### Schema

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `LLM_PROVIDER` | 否 | `OPENAI` | 选定后续三个变量的前缀。例如：`OPENAI`、`DEEPSEEK`、`GEMINI`、`ANTHROPIC`。 |
| `{PROVIDER}_API_KEY` | **是** | — | 缺失则启动失败。 |
| `{PROVIDER}_BASE_URL` | **是** | — | 缺失则启动抛 `ValueError`。 |
| `{PROVIDER}_MODEL` | **是** | — | 缺失则启动抛 `ValueError`。 |
| `LLM_TEMPERATURE` | 否 | `0.2` | 范围 `0.0`–`2.0`。 |
| `LLM_MAX_TOKENS` | 否 | — | 不设则使用 provider 缺省。 |
| `BOCHA_API_KEY` | 否 | — | 设了之后 `web_search` 走 Bocha；否则回退到 DuckDuckGo。 |

> 标准范例：仓库根目录的 `.env.example`。

---

## 3. `.agentao/settings.json` — 运行模式 + 内置子代理

- **路径。** `<cwd>/.agentao/settings.json`（仅项目级，无用户变体）。
- **Loaders。**
  - `embedding/factory.py::_load_settings` — 读取 `agents.enable_builtin` / `enable_builtin_agents` 用作构造器的 `enable_builtin_agents` 默认值。
  - `plan/controller.py::_load_settings` — 在 plan-mode 会话结束后**恢复**权限模式时读取 `mode`。
- **失败行为。** 文件缺失或 JSON 损坏 → 静默当作 `{}` 处理（不会启动报错）。
- **重要。** factory 启动时**不会**把 `mode` 应用到引擎；`PermissionEngine` 始终以 `workspace-write` 初始化。`mode` 字段是"上次持久化的模式"，用于恢复路径与 CLI 展示——运行期模式切换走 CLI 命令或 `PermissionEngine.set_mode()`。

### Schema

```json
{
  "mode": "workspace-write",
  "agents": {
    "enable_builtin": false
  }
}
```

| 键 | 类型 | 默认 | 合法值 | 说明 |
|---|---|---|---|---|
| `mode` | string | `"workspace-write"`（键缺失时） | `"read-only"`、`"workspace-write"`、`"full-access"` | `"plan"` 是内部模式，由 `/plan` 流程设置，用户不应手写。`"full-access"` 关闭所有按工具的二次确认，请审慎使用。 |
| `agents.enable_builtin` | bool | `false` | — | 启用内置子代理集合。兼容老的顶层别名 `enable_builtin_agents`（bool）。 |

每个 `mode` 的具体放行/拦截语义详见 [TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md)。

---

## 4. `permissions.json` — 按工具的权限规则

- **路径。**
  1. `~/.agentao/permissions.json`（用户级）—— 先加载。
  2. `<cwd>/.agentao/permissions.json`（项目级）—— **前置**到规则列表头，**先于**用户规则求值。
- **Loader。** `permissions.py::PermissionEngine._load_file`。文件缺失或 JSON 损坏 → 空规则列表（不报错）。
- **求值顺序。**
  - `read-only` / `workspace-write` 模式：`[项目规则] → [用户规则] → [当前 mode 的 preset 规则]`，首个命中胜出。
  - `full-access` / `plan` 模式：`[当前 mode 的 preset 规则] → [项目规则] → [用户规则]`——preset 不可被覆盖。
  - 没有命中 → `decide()` 返回 `None`；runner 退回到工具自身的 `requires_confirmation` 属性。

### Schema

```json
{
  "rules": [
    {"tool": "run_shell_command", "args": {"command": "^git "}, "action": "allow"},
    {"tool": "write_file", "action": "ask"},
    {"tool": "run_shell_command", "args": {"command": "rm\\s+-rf"}, "action": "deny"},
    {
      "tool": "web_fetch",
      "domain": {"allowlist": [".example.com"], "url_arg": "url"},
      "action": "allow"
    }
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `tool` | string | 是 | 工具名；通过 `re.fullmatch` 当作 regex 匹配（`"*"` 表示通配）。 |
| `args` | object | 否 | `<arg_name>` → regex 的映射；**所有**条目都需 `re.search` 命中规则才生效。坏 regex 会回退为字面相等比较。 |
| `domain` | object | 否 | URL 类工具专用（如 `web_fetch`）。键：`url_arg`（默认 `"url"`）、`allowlist`、`blocklist`。以 `.` 开头的 pattern 做后缀匹配（如 `.github.com` 同时匹配 `github.com` 与 `api.github.com`）；否则做精确匹配。带 `domain` 的规则**只有**当 hostname 命中其中一个列表时才匹配。 |
| `action` | string | 是 | `"allow"` \| `"deny"` \| `"ask"`（大小写不敏感；未知值按 `"ask"` 处理）。 |

**内置 preset** 在 `permissions.py::_PRESET_RULES`，按上述顺序追加在自定义规则之后（或在 `full-access` / `plan` 下放在前面）：

- `workspace-write` —— 自动放行 `write_file` / `replace`；放行约 16 条只读 shell（`ls`、`cat`、`grep`、`git status|log|diff|show|…`…）；拒绝 `rm -rf` / `sudo` / `mkfs` / `dd if=`；放行受信任文档站点（`.github.com`、`.docs.python.org`、`.wikipedia.org`、`.pypi.org`、`.readthedocs.io`、`r.jina.ai`）；屏蔽 SSRF 目标（`localhost`、`127.0.0.1`、`0.0.0.0`、`169.254.169.254`、`.internal`、`.local`、`::1`）；其余 → ask。
- `read-only` —— preset 为空；`ToolRunner` 用 `tool.is_read_only` 直接短路。
- `full-access` —— 单条 `{"tool": "*", "action": "allow"}`。
- `plan` —— 拒绝所有写入与记忆改动；放行只读 shell allowlist；web 规则与 `workspace-write` 相同。

完整的规则分类、示例、运行期语义 → [TOOL_CONFIRMATION_FEATURE.md](features/TOOL_CONFIRMATION_FEATURE.md)。

---

## 5. `mcp.json` — MCP 服务器注册表

- **路径。**
  1. `~/.agentao/mcp.json`（用户级）—— 先加载。
  2. `<cwd>/.agentao/mcp.json`（项目级）—— 命名冲突时覆盖用户级。
- **Loader。** `mcp/config.py`。值里的环境变量会被展开（`$VAR` 形式）。

### Schema

```json
{
  "mcpServers": {
    "<name>": {
      "command": "...",
      "args": ["..."],
      "env": { "TOKEN": "$MY_TOKEN" },
      "trust": false
    },
    "<remote-name>": {
      "url": "https://...",
      "headers": { "Authorization": "Bearer $API_KEY" },
      "timeout": 30
    }
  }
}
```

| Transport | 必填键 | 可选键 |
|---|---|---|
| stdio 子进程 | `command`、`args` | `env`、`trust`、`cwd` |
| SSE | `url` | `headers`、`timeout` |

工具会以 `mcp_{server}_{tool}` 名称注册。完整生命周期请见 `CLAUDE.md` 的 "MCP" 段。

> 如果 MCP 后续单独有了 user-facing feature 文档（`features/mcp.md`），请同步更新 §1 表中这一行的链接。

---

## 6. `.agentao/acp.json` — ACP 子代理注册表

- **路径。** 仅 `<cwd>/.agentao/acp.json`。**没有用户级变体**——ACP 服务器明确按项目隔离。
- **Loader。** `acp_client/config.py::load_acp_config`（解析后通过 `acp_client/models.py::AcpServerConfig.from_dict` 转为 `AcpServerConfig`）。
- **失败行为。** `command` / `args` / `env` / `cwd` 缺失会在配置加载时抛 `AcpConfigError`——直接启动失败。
- **热加载。** CLI 监听文件 mtime；编辑会在下一次 inbox 轮询时被发现（`cli/acp_inbox.py`）。

### Schema

```json
{
  "servers": {
    "<name>": {
      "command": "/abs/or/PATH/binary",
      "args": ["..."],
      "env": { "TOKEN": "$MY_TOKEN" },
      "cwd": ".",
      "description": "human-readable",
      "capabilities": { "chat": true, "web": true },
      "autoStart": true,
      "startupTimeoutMs": 10000,
      "requestTimeoutMs": 60000,
      "maxRecoverableRestarts": 3,
      "nonInteractivePolicy": { "mode": "reject_all" }
    }
  }
}
```

| 键 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `command` | string | 是 | — | 绝对路径或可在 PATH 中解析的可执行文件。 |
| `args` | string[] | 是 | — | 空列表 `[]` 合法。 |
| `env` | object | 是 | — | 值支持 `$VAR` 环境变量展开。 |
| `cwd` | string | 是 | — | 相对路径会按 `project_root` 解析为绝对路径。 |
| `description` | string | 否 | `""` | 自由文本。 |
| `capabilities` | object | 否 | `{}` | 自由 KV（如 `chat`、`web`、`role: "worker"`）。loader 不做强校验。 |
| `autoStart` | bool | 否 | `true` | 为 `false` 时服务在首次调用前保持冷启状态。 |
| `startupTimeoutMs` | int | 否 | `10000` | 握手预算。 |
| `requestTimeoutMs` | int | 否 | `60000` | 单次请求预算。 |
| `maxRecoverableRestarts` | int | 否 | `3` | 可恢复型子进程死亡时的自动重启上限；首次成功一轮后清零。 |
| `nonInteractivePolicy` | object | 否 | `{"mode": "reject_all"}` | **只**接受对象形式——裸字符串形式会被拒绝并提示迁移信息。`mode` 合法值：`"reject_all"`、`"accept_all"`。 |

完整 ACP 语义 → [acp-client.md](features/acp-client.md) 与 [acp-embedding.md](features/acp-embedding.md)。

---

## 7. `.agentao/skills_config.json` — 禁用 skill 列表

- **路径。** `<cwd>/.agentao/skills_config.json`（仅项目级）。
- **Loader。** `skills/manager.py`。

### Schema

```json
{
  "disabled_skills": []
}
```

| 键 | 类型 | 说明 |
|---|---|---|
| `disabled_skills` | string[] | 要从自动发现中**排除**的 skill 名。CLI 里用 `/skills disable <name>` 管理。 |

skill 的发现与激活规则见 [SKILLS_GUIDE.md](SKILLS_GUIDE.md)。

---

## 8. `AGENTAO.md` — 项目说明

- **路径。** `<cwd>/AGENTAO.md`。可选。
- **Loader。** `agent.py::_build_system_prompt` —— 文件存在时其内容会被前置到 system prompt。
- **Schema。** 自由 Markdown，无强制结构。

prompt 组成规则与约定见 [CHATAGENT_MD_FEATURE.md](features/CHATAGENT_MD_FEATURE.md)。

---

## 9. 记忆库（`memory.db`）

- **路径。**
  - 项目：`<cwd>/.agentao/memory.db`
  - 用户：`~/.agentao/memory.db`
- **格式。** SQLite；schema 由 `memory/manager.py::MemoryManager` 拥有。**不要手编辑**。
- **优先级。** 两个 DB 独立读取；prompt 渲染器都会看到。Project 不会覆盖 user。

完整 schema、表结构、生命周期 → [memory-management.md](features/memory-management.md)。

---

## 附录 A —— 新增配置面时的 checklist

新增配置文件时，**两处必须同步更新**：

1. 本文档（§1 加一行；像 §3–§9 那样补一节完整说明）。
2. 对应 feature 文档 —— *为什么*与*怎么用*留在那里，不要写到这里来。

Checklist：

- [ ] §1 表里加一行（路径、scope、loader、feature-doc 链接）。
- [ ] 每个键都标了必填/可选/默认值的 Schema。
- [ ] 同时存在项目与用户变体时，写明优先级规则。
- [ ] 写出 loader 的源码路径，方便读者去代码里反查行为。
- [ ] 加上 feature 文档的交叉链接（暂时不存在的话留 `<!-- TODO -->`）。
