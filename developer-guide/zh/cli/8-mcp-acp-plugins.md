# 8. MCP / ACP / 插件

这三个命令把**外部**的工具能力接进 CLI 会话。

| 命令 | 接的是 | 方向 |
|---|---|---|
| `/mcp` | MCP 服务器 — 外部工具 provider（filesystem / github / db ...） | Agent **向外**调它们 |
| `/acp` | ACP 服务器 — 完整的、说 Agent Client Protocol 的另一个 agent | Agent **与其他 agent** 协作 |
| `/plugins` | 生命周期 hooks（Stop / PreToolUse / UserPromptSubmit / PreCompact） | Hooks 拦截 **agent 自身**的生命周期事件 |

只用内建工具就用不到这一章。一旦说"我要让 agent 通过官方 MCP 服务器接公司 GitHub"或"我要让这个 agent 通过 stdio 调到另一个 agent"，从这里开始。

## `/mcp` — MCP 服务器

[Model Context Protocol](https://modelcontextprotocol.io) 是一个开放标准，定义工具服务器。一个 MCP 服务器把一组工具（`fs.read_file` `github.create_issue` ...）通过 stdio JSON-RPC 或 HTTP/SSE 暴露出来；agent 像用任何其他工具一样使用它们。

### 配置文件

实时配置：`.agentao/mcp.json`（项目）和 `~/.agentao/mcp.json`（用户全局）。

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/data"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "$GITHUB_TOKEN" },
      "trust": false
    },
    "remote": {
      "url": "https://api.example.com/mcp",
      "headers": { "Authorization": "Bearer $API_KEY" },
      "timeout": 30
    }
  }
}
```

三种 transport：

| Transport | 触发条件 | 跑的是什么 |
|---|---|---|
| stdio | 配置里有 `"command": "..."` | 本地子进程，stdio JSON-RPC |
| Streamable HTTP | 配置里有 `"url": "..."`（默认），或 `"type": "http"` | 远端 Streamable HTTP 端点 |
| SSE（legacy） | `"type": "sse"` + `"url"` | 远端 SSE 端点 |

裸 `url` 现在默认走 **Streamable HTTP**——旧版 SSE 端点需加 `"type": "sse"`。（破坏性变更；见 [MCP 深入章节](/zh/part-5/3-mcp)。）

配置里的 env vars 用 `$VAR_NAME` 写法，加载时从你的 shell 环境 / `.env` 展开。

`"trust": true` 跳过这台服务器工具的确认 UI。**别给会用你凭证调外部 API 的服务器开 trust。**

### 子命令

```text
> /mcp                                  # /mcp list 的别名
> /mcp list                             # 列出所有配置的服务器
> /mcp add github npx -y @modelcontextprotocol/server-github
> /mcp add remote https://api.example.com/mcp
> /mcp remove github
```

`/mcp list` 输出：

```text
MCP Servers (3):

  ● filesystem  command — connected, 12 tool(s)
  ● github      command — connected, 24 tool(s) (trusted)
  ● remote      url     — failed
    Connection refused
```

`/mcp add` 写到**项目**配置（`.agentao/mcp.json`）— 不动用户全局那一份。

`/mcp remove` 从项目配置里删条目，但**改动需要重启**才生效（CLI 会提示）。当前会话保留运行中的连接。

### 工具命名

MCP 工具注册成 `mcp_{server}_{tool}`。所以 `filesystem.read_file` 在 agent 工具列表里是 `mcp_filesystem_read_file`。`/help` 里就是这样辨认 MCP 工具的。

### 容易踩的坑

- **连接失败不会让 CLI 崩** — 服务器在 `/mcp list` 里红色显示，它的工具不可用，其他都正常
- **`/mcp add` 不会自动启动** — 有些配置改动要重启（CLI 会告诉你）
- **Trust 是会话级决定，不是逐次调用级** — `"trust": true` 意味着该服务器**所有**工具调用都不弹确认。这里没有逐工具粒度，要更细就用权限引擎
- **stdio 服务器在 `Ctrl+C` 退出时会留下进程残留** — 一律 `/exit`

## `/acp` — ACP 服务器

[ACP（Agent Client Protocol）](/zh/part-3/) 是 Agentao 用来 agent 间通信的协议。`/acp` 让你在 CLI 会话里启停、对话于其他说 ACP 的 agent。

跟 MCP（给*你的* agent 加工具）不同，ACP 接进来的是**另一个 agent**，你可以把 prompt 派给它。把它想成 agent 版的 `gh repo clone`。

### 配置文件

实时配置：`.agentao/acp.json`。格式类似 `mcp.json`，每条描述一个完整的 agent 进程。

### 子命令

```text
> /acp                          # /acp list 的别名
> /acp list                     # 已配置服务器 + 状态
> /acp start <name>             # 启动
> /acp stop <name>              # 关闭
> /acp restart <name>
> /acp send <name> <prompt>     # 发一轮，权限/输入内联处理
> /acp cancel <name>            # 取消进行中的一轮
> /acp status <name>            # 详细状态
> /acp logs <name> [lines]      # 看 stderr 尾部（默认最后 20 行）
```

状态机：

```
configured → starting → initializing → ready → busy → ready
                                          ↘   waiting_for_user → ready
                                            ↘ stopping → stopped
                                              ↘ failed
```

`/acp list` 显示运行中数量 + inbox / pending interaction 队列：

```text
ACP Servers (1/2 running):
Inbox: 3 queued
Pending interactions: 1

  ● local-coder    ready pid=8421  通用编码 agent
  ● remote-helper  failed          Connection refused
```

状态颜色：
- `ready`（绿）、`busy`（青）、`waiting_for_user`（紫）
- `starting`/`initializing`/`stopping`（黄）
- `configured`/`stopped`（暗）
- `failed`（红）

### ACP 还是 MCP

| 你想要 | 用 |
|---|---|
| 工具调用（读文件、查 DB） | MCP |
| 让另一个 agent 思考、回答某个子问题 | ACP |
| 跨语言互通（你的 agent 是 Python，别人的是 Go） | ACP |
| 把已有的公开工具服务器组合起来 | MCP |

### 容易踩的坑

- **`/acp send` 默认阻塞 REPL** — ACP 一轮跑得长，你这边的本地 agent 就要等。需要时 `/acp cancel`。
- **`waiting_for_user` 状态意味着远端要你输入** — `/acp status <name>` 看 prompt，用 `/acp send` 应答。
- **Inbox 不处理会越积越多** — 没回的 ACP 服务器消息排队，用 `/acp send` 回应清空，或重启。
- **ACP 服务器带着死掉的 PID** — 主机重启了但 `acp.json` 还指着旧 pid。`/acp restart <name>` 修。

## `/plugins` — 生命周期 hooks

`/plugins`（别名 `/plugin`）显示当前工作目录下加载了哪些 hook 插件。

插件是外部 Python 包，hook 进 agent 的生命周期事件：

- `UserPromptSubmit` — 在 agent 看到新用户消息之前
- `PreToolUse` — 在某次具体工具调用之前
- `Stop` — agent 决定结束一轮时（审计 / 续轮）
- `PreCompact` — context manager 压缩历史之前

### 输出长这样

```text
> /plugins
Agentao Plugin Diagnostics

Loaded plugins (2):
  • my-org/audit-logger  v1.2.0
    Hooks: UserPromptSubmit, PreToolUse, Stop
    Source: pip-installed (agentao_plugin_audit_logger)

  • ./plugins/dev-only-injector  (inline)
    Hooks: UserPromptSubmit
    Source: inline

Warnings: 0
Errors: 0
```

诊断报告涵盖：
- 加载了哪些插件，从哪儿来（pip 装的还是 inline）
- 每个插件注册了哪些 hooks
- Warnings（如插件声明了 hook 但注册失败）
- Errors（如 import 失败）

### 什么时候用

- **排查 "agent 怎么会做这事？"** — 可能某个插件在静悄悄地注入系统提示，或拒掉某个工具调用
- **验证 CI / 生产配置** — 你期望的插件确实加载了、注册到正确的 hooks 上
- **更新插件之后** — 确认新版本生效了

### `/plugins` *不是*什么

- 不是插件**管理** CLI — 没有 `/plugins install` 或 `/plugins remove`。插件是 pip 装的（或 inline）然后被自动发现。要卸载就 `pip uninstall <pkg>` 然后重启。
- 不是写插件的地方 — 看 [Part 5.7 · 插件 Hooks](/zh/part-5/7-plugin-hooks)。

## 接下来读什么

| 想做的事 | 读 |
|---|---|
| 给团队搭个自定义 MCP 服务器 | [Part 5.3 · MCP](/zh/part-5/3-mcp) |
| 嵌入 ACP 服务器 / 让其他语言驱动 agent | [Part 3 · ACP 协议](/zh/part-3/) |
| 写生命周期 hook 插件 | [Part 5.7 · 插件 Hooks](/zh/part-5/7-plugin-hooks) |

---

::: info 这一章在体系里的位置
- MCP：`agent.mcp_manager` — 嵌入式宿主可以调 `manager.get_server_status()` 拿到本页一样的数据
- ACP：`agent.acp_manager` — ACP 状态、send、cancel 同上
- Plugins：`PluginManager`（在 `agentao.embedding.plugins.manager`）— 诊断报告由 `agentao.embedding.plugins.diagnostics.build_diagnostics` 生成，宿主也可以调。完整的可编程接口见 [Part 5.7](/zh/part-5/7-plugin-hooks)
:::

::: tip 真相源头
命令语法：`/help`。行为锚点：
- [`agentao/cli/commands.py:handle_mcp_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py)
- [`agentao/cli/commands_ext/acp.py:handle_acp_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/acp.py)
- [`agentao/cli/subcommands.py:_handle_plugins_interactive`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/subcommands.py)
:::
