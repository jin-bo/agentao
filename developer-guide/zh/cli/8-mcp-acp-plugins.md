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
> /mcp resources                       # 列出资源和资源模板（不消耗模型轮次）
> /mcp resources docs                  # 只看一个 server
> /mcp login remote                     # 在浏览器里授权一个 OAuth server
> /mcp login remote --no-browser        # 只打印 URL，再把跳转地址粘贴回来
> /mcp logout remote                    # 删除它保存的凭据
```

`/mcp list` 输出：

```text
MCP Servers (3):

  ● filesystem  command — connected, 12 tool(s)
  ● github      command — connected, 24 tool(s) (trusted)
  ● remote      url     — failed
    Connection refused
```

对开启了 `"skills": true` 的 server，`/mcp list` 会在它下面多显示一行：它提供的 skill 数量，或者 skills 不可用的原因。

`/mcp add` 写到**项目**配置（`.agentao/mcp.json`）— 不动用户全局那一份。如果该文件存在但无法读成 JSON object，`/mcp add` 和 `/mcp remove` 会打印错误并保持文件不变，而不是覆盖它、丢掉其中的 server。修好文件或把它移开后再运行一次。

`/mcp remove` 从项目配置里删条目，但**改动需要重启**才生效（CLI 会提示）。当前会话保留运行中的连接。

### `/mcp login` 与 `/mcp logout` —— OAuth server

URL 类 server（Streamable HTTP 或 SSE）默认使用 OAuth，不需要在 `mcp.json` 里开启。以下情况不适用：stdio server、设置了 `"oauth": false` 的 server，以及 `headers` 里已经带 `Authorization` 的 server。这类 server 返回 401 时，agentao 不会自己打开浏览器，而是在 `/mcp list` 里显示为 `needs login`，REPL 启动时也会打印：

```text
MCP server 'remote' needs login — run /mcp login remote
```

登录步骤：

1. `/mcp login remote` 打印授权 URL 并打开浏览器，最多等待 300 秒，按 Ctrl+C 取消。
2. 在浏览器里同意授权。浏览器会跳转回 `127.0.0.1` 上的监听地址（`/callback/<server>`），页面提示你可以关闭它。
3. agentao 保存凭据，并在当前会话中重新连接该 server：`Logged in to 'remote' — connected, 5 tool(s).`

加上 `--no-browser`，或者无法打开浏览器时（例如通过 SSH 登录、没有图形界面），agentao 只打印 URL，并提示 `Redirect URL:`。在任意一台机器上打开 URL 并同意，然后把浏览器跳转到的完整地址粘贴回来，即使那个页面没有打开成功也可以。输入内容不会显示。监听一直在运行，所以 SSH 端口转发同样可行。

工具只在 agentao 启动时加载。如果会话启动时该 server 没有任何工具，登录结束时会提示 `Restart agentao to load the tools of 'remote'.`；如果已有工具，下一次调用就会使用新 token。

`/mcp logout remote` 删除保存的凭据并断开该 server。它的工具仍然列在会话里；如果该 server 要求登录，在重新登录之前，调用会失败并提示登录命令。没有保存凭据时提示 `'remote' had no stored credential.`

在 shell 里，`agentao mcp login <name> [--no-browser]` 和 `agentao mcp logout <name>` 效果相同，只装库（不含 CLI 扩展）也能用。退出码：`0` 已连接，`1` 失败（或凭据已保存但重连失败），`2` 用法错误，`130` 已取消。无人值守的 `agentao run` 和 ACP 会话从不自己发起登录，需要用这两个命令在终端完成。这些进程会在该 server 下一次连接或调用时读取新凭据。

需要知道的几点：

- **凭据**保存在 `~/.agentao/mcp-oauth/`，每个 server URL 加档案一个文件（文件 0600，目录 0700）。内容是明文 JSON，不使用操作系统钥匙串。URL 相同且都没有档案的两个 server 名共用一份凭据，在所有项目中都是如此。
- **刷新**是自动的：过期前 60 秒刷新一次，收到 401 后再试一次。授权服务器拒绝刷新时，该 server 回到 `needs login`；网络故障只报普通错误，不会要求重新登录。
- **配置**写在 server 上可选的 `oauth` 对象里：`client_id`、`client_secret`（需要同时有 `client_id`，支持 `$VAR` 展开）、`callback_port`（1–65535）、`redirect_host`（`localhost` 或回环地址）、`profile`（见下条）。其他键都会报错。没有 `scopes` 键：登录时申请的是 server 在 401 中指明的 scope。不设 `client_id` 时，agentao 会以公共客户端 `agentao` 的身份向 server 注册。
- **同一 server 用两个账号。** 给每个条目设置各自的 `oauth.profile`。每个档案是该 URL 的一份独立登录，两个账号可以同时保持登录：

  ```json
  {
    "mcpServers": {
      "tracker-work":     { "url": "https://mcp.example.com/mcp", "oauth": { "profile": "work" } },
      "tracker-personal": { "url": "https://mcp.example.com/mcp", "oauth": { "profile": "personal" } }
    }
  }
  ```

  分别运行一次 `/mcp login tracker-work` 和 `/mcp login tracker-personal`。`/mcp logout` 只删除该条目对应档案的凭据。档案标识的是账号，不是条目：两个项目里 URL 和档案都相同的条目共用一份登录，改条目名也不影响。设了档案的条目从不使用该 URL 无档案的登录，所以即使那份登录存在，它的初始状态也是 `needs login`。档案出现之前的登录对没有档案的条目照常有效。登录哪个账号由浏览器决定；如果浏览器直接以另一个账号完成登录，先在浏览器里退出，或者用 `--no-browser`，在另一个浏览器档案里打开打印出的链接。档案是非空字符串，首尾不能有空格，精确比较：`Work` 和 `work` 是两个档案。
- **ACP 传入的 server**（来自编辑器的 `session/new`）从不使用 agentao 的 OAuth，由编辑器负责授权。
- **限制。**
  - 在 mcp 1.x 上，SDK 不检查授权服务器的 `iss`（RFC 9207）；在 mcp 1.26 上，每次登录还会注册一个新的客户端。这两点都建议用 mcp 2.x 解决。
  - 收到 `403 insufficient_scope` 时会报出 server 需要的 scope，但重新登录只会申请 server 在 401 中指明的 scope，所以重新登录不一定能消除这个错误。
  - 登录过程会读取 SDK 的内部字段。如果将来的 SDK 版本移动了这些字段，登录会停止并给出提示，已经登录的 server 不受影响。

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

实时配置：项目根下的 `.agentao/acp.json`。`servers` 下每一条描述一个完整的 agent 进程，它通过 stdio 说 ACP（JSON-RPC 2.0，每行一条 JSON 消息）。

```json
{
  "servers": {
    "planner": {
      "command": "node",
      "args": ["./agents/planner/index.js"],
      "env": { "LOG_LEVEL": "info" },
      "cwd": ".",
      "description": "规划 agent"
    },
    "reviewer": {
      "command": "python",
      "args": ["-m", "review_agent"],
      "env": { "REVIEW_API_KEY": "${REVIEW_API_KEY}" },
      "cwd": "./agents/reviewer",
      "description": "代码评审 agent",
      "requestTimeoutMs": 120000
    }
  }
}
```

`command`、`args`、`env`、`cwd` 必填。相对 `cwd` 相对项目根解析。`env` 里的值会从 shell 或 `.env` 展开 `$VAR` / `${VAR}`，所以密钥不必写进文件。所有键及默认值见 [附录 B.3.4](/zh/appendix/b-config-keys#b-3-4-acp-json)。

要点：

- **只有项目级。** 没有用户全局的 `~/.agentao/acp.json`。
- **不会自己启动。** CLI 在第一次 `/acp` 命令或第一条被路由的消息时才读 `acp.json`，启动时不读。`autoStart` 为 `true` 也不会让 CLI 拉起服务器。第一次 `/acp start` 或 `/acp send` 才启动它，`/acp send` 还会顺带建立连接。
- **改动会被读到。** `acp.json` 变了之后，下一条被路由的消息会重新加载它。
- **回复单独存放。** 服务器的输出进 ACP inbox 并显示在屏幕上，永远不进主 agent 的对话。
- **超时。** `startupTimeoutMs` 是等 `initialize` 回应的时长，不管设多少，实际都不少于 30 秒。`requestTimeoutMs`（默认 60 秒）限制一轮 `/acp send`。

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
> /acp logs <name> [lines]      # 看 stderr 尾部（默认最后 50 行）
> /acp login <name> [method-id] # 运行服务器的终端登录，然后重启并重连
> /acp registry search <keyword>  # 搜索官方 ACP Registry
> /acp registry add <id> [name]   # 把 Registry 里的 agent（npx / uvx）加入 acp.json
```

### 从 ACP Registry 添加 agent（0.5.8+）

[ACP Registry](https://github.com/agentclientprotocol/registry) 列出各个 ACP agent 及其启动方式。Agentao 读取它的稳定索引 `https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json`。

```text
> /acp registry search claude
> /acp registry add claude-acp            # 服务器名默认取 id
> /acp registry add gemini my-gemini      # 也可以自己起名
```

`add` 会显示 agent、版本和启动命令，你确认之前什么都不写。两个命令都不会启动 agent。第一次 `/acp send` 或 `/acp start` 才启动，这一次会下载包。新条目马上可用，不用重启 CLI，正在运行的服务器也不受影响。

`add` 写入的就是一条普通的 `acp.json` 条目：

```json
"claude-acp": {
  "command": "npx",
  "args": ["--yes", "@agentclientprotocol/claude-agent-acp@0.84.0"],
  "env": {},
  "cwd": ".",
  "autoStart": false,
  "startupTimeoutMs": 120000,
  "description": "Claude Agent 0.84.0 (ACP Registry: claude-acp)"
}
```

规则：

- **只支持 `npx` 和 `uvx`。** Node.js（用于 `npx`）或 [uv](https://docs.astral.sh/uv/)（用于 `uvx`）必须已在 `PATH` 上，Agentao 不替你安装。两者都提供时用 `npx`。只有二进制分发的条目会被拒绝。
- **只接受钉死的版本。** 包规格必须钉住条目自身的版本：`name@1.2.3`、`@scope/name@1.2.3` 或 `name==1.2.3`（uv 可带 `[extras]`）。其他写法一律拒绝，不做猜测。
- **不会悄悄覆盖。** 名字已存在就拒绝，换个名字即可。现有 `acp.json` 读不了或不合法时也拒绝，而不是重写。其他条目和键原样保留，文件以原子方式替换。
- **含 `$` 的 `env` 值会被拒绝。** `acp.json` 会展开 `env` 里的 `$VAR`，而且没有字面 `$` 的转义写法。这类 agent 请手工添加。
- **不写入任何凭据。** 见 [认证与 `/acp login`](#认证与-acp-login)。

嵌入 Agentao 的宿主不经 CLI 也能走同样的步骤，见 [3.4.5 · 运行时添加 server](/zh/part-3/4-reverse-acp-call#运行时添加-server)。源码：[`agentao/acp_client/registry.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/registry.py)。

### 认证与 `/acp login`

需要凭据的 agent 会对 `session/new` 回 `auth_required`（JSON-RPC `-32000`）。Agentao 会如实报告，并列出 agent 提供的登录方法。这不算握手失败，所以登录前再发一次也不会把服务器标成失败。在会话打开之前，`/acp list` 和 `/acp status` 把它显示为 `needs login`。

#### 终端登录

agent 提供 `terminal` 登录方法时，运行：

```text
> /acp login <name>              # 只有一个终端方法：直接运行
> /acp login <name> <method-id>  # 有多个：指定一个（或从编号列表里选）
```

运行方式：

- **命令就是服务器自己的。** Agentao 用服务器的 `command`、`args` 和 `cwd`，追加该方法的 `args`，再把该方法的 `env` 叠加到服务器环境上。这遵循 ACP 认证规范。
- **登录独占终端。** 在 macOS 和 Linux 上，登录作为前台作业在自己的进程组里运行。Ctrl+C 只取消登录，不会打断 Agentao。在 Windows 上，登录共用控制台，并运行在一个 job object 里。
- **失败会清理干净。** 取消或失败的登录会连同它启动的一切一起结束，比如 `npx` 或 `uvx` 启动器的子进程。
- **退出码 `0` 才算成功。** 其他退出码、取消或启动失败都报告为失败，不会重启任何东西。
- **成功之后**，Agentao 重启服务器、重新初始化并打开会话，同时清除记录下的 `auth_required` 错误。如果 agent 仍要求认证，只提示一次，不会再次运行登录。
- **服务器被预留。** 登录期间，对该服务器发起一轮或再发起一次登录都会得到 `SERVER_BUSY`。有进行中的一轮时，登录会被拒绝。其他服务器不受影响。
- **必须有终端。** 只有 stdin 和 stdout 都是 TTY 时，Agentao 才向 agent 声明支持 Terminal Auth。agent 只会向声明过的客户端提供 `terminal` 方法，所以 `/acp login` 需要交互式终端会话。

源码：[`agentao/cli/commands_ext/acp_login.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/acp_login.py)。

#### 其他认证方法

Agentao 里只能运行 `terminal` 方法。`agent` 方法（方法没写 `type` 时的默认值）或其他类型，请在 Agentao 之外完成认证，然后运行 `/acp restart <name>`。

#### 不继承服务商密钥

Agentao 启动 ACP 服务器时，会从环境里去掉自己的服务商密钥（`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY` 等），与 MCP 服务器和 hooks 相同。因此从环境读密钥的 agent 会回 `auth_required`，哪怕你的 shell 里设了这个密钥。请登录，或在该服务器的 `env` 块里显式传入：

```json
"env": { "GEMINI_API_KEY": "${GEMINI_API_KEY}" }
```

终端登录拿到的是同一份环境，规则相同。

### 服务器生命周期

```
configured → starting → initializing → ready → busy → ready
                                          ↘   waiting_for_user → ready
                                            ↘ stopping → stopped
                                              ↘ failed
```

- `initializing` 是 ACP 握手（先 `initialize`，再 `session/new`）。
- `busy` 表示一轮正在进行。`waiting_for_user` 表示服务器在问你问题（见 [权限请求与输入请求](#权限请求与输入请求)）。
- 服务器挂掉后，会在下一次调用时重建；空闲时最多重建 `maxRecoverableRestarts` 次（默认 3）。一轮成功后计数清零。被信号杀掉、连续两次握手失败或重建次数超限，服务器会停在 `failed`，直到你运行 `/acp restart <name>` 或 `/acp start <name>`。

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
- `needs login`（黄）——服务器回了 `auth_required`；在打开会话之前代替 `configured` / `stopped` / `failed` 显示

### 权限请求与输入请求

服务器可以请你批准它的某次工具调用，也可以向你要一段自由文本（[`_agentao.cn/ask_user`](/zh/appendix/c-acp-messages#c-11-agentao-cn-ask-user-⇠-请求-扩展) 扩展）。请求会成为一条**待处理交互**，CLI 内联地问你：`/acp send` 期间当场问，其他时候在下一个空闲点问。

权限请求会显示工具、类型和最多六个参数。按一个键作答：

```text
 1. Approve once  2. Approve all  3. Reject once  4. Reject all
 Press 1-4 · Esc to reject
```

输入请求则在 `>` 提示符后输入回复。空回复会取消该请求。

`/acp status <name>` 列出仍待处理的请求，`/status` 显示其数量。你作答所花的时间不计入 `requestTimeoutMs`，每次作答后计时重新开始。

### 把消息路由给服务器

不用 `/acp send` 也能把一轮交给已配置的服务器。消息以服务器名开头，写成下面任一形式：

```text
> @reviewer check the latest diff
> reviewer: check the latest diff
> 让 reviewer 检查最新的 diff
> 请 reviewer 检查最新的 diff
```

CLI 打印 `ACP Delegation → reviewer`，然后与 `/acp send` 完全一样地执行这一轮，权限和输入请求同样内联处理。

- **只匹配已配置的名字。** 未知名字会作为普通消息交给主 agent。名字有重叠时取最长的（`qa.bot` 优先于 `qa`）。
- **`让` / `请` 后面要有空格**再接名字，这样 `请问…` 这类普通中文不会被路由。冒号形式也接受全角 `：`。
- **没有任务就不发。** 名字后面没有任务文本时，只打印用法提示。
- **`acp.json` 坏了怎么办。** `@`、`让`、`请` 三种形式会打印加载错误并吞掉这条消息，免得你的委派意图误交给主 agent。`name:` 形式在普通文字里很常见（`Note: …`），所以只警告一次，然后交给主 agent。
- **主 agent 看不到结果。** 路由的含义是“把这一轮交给那个服务器”，而不是“告诉主 agent”。回复留在 ACP inbox 里。

路由不靠猜，只认上面这几种前缀。（曾有一个把服务器结果推进主对话的设计，在发布前就放弃了。）源码：[`agentao/acp_client/router.py`](https://github.com/jin-bo/agentao/blob/main/agentao/acp_client/router.py)。

### 日志与状态

Agentao 为每个服务器保留最近 200 行 stderr。

```text
> /acp logs reviewer        # 最后 50 行
> /acp logs reviewer 100    # 最后 100 行
```

`/acp status <name>` 显示状态、PID、最近错误、最近活动时间、会话 ID、协议版本、待处理交互和最后 5 行 stderr。配置了服务器时，`/status` 会附上一段摘要：

```text
ACP servers: 1/2 running
ACP inbox: 3 queued
ACP interactions: 1 pending
```

### ACP 还是 MCP

| 你想要 | 用 |
|---|---|
| 工具调用（读文件、查 DB） | MCP |
| 让另一个 agent 思考、回答某个子问题 | ACP |
| 跨语言互通（你的 agent 是 Python，别人的是 Go） | ACP |
| 把已有的公开工具服务器组合起来 | MCP |

### 容易踩的坑

- **`/acp send` 会阻塞 REPL** — 这一轮结束或 `requestTimeoutMs` 用完之前，你没法跟本地 agent 对话。按 Ctrl+C 取消这一轮。路由出去的 `@name` 消息同样会阻塞。
- **`waiting_for_user` 表示服务器在问你** — CLI 会内联显示问题，不需要用 `/acp send` 回答。
- **Inbox 有上限** — 最多存 256 条，满了会丢掉最旧的。推理最先被丢：同一会话相邻的 thought chunk 会合并成一条，满了时先丢最旧的推理，再轮到回复、错误或提示。消息在空闲点显示：提示符出现前、斜杠命令之后、agent 回复之后。
- **反复崩溃后服务器会停在 `failed`** — 自动重建到 `maxRecoverableRestarts` 次就停止。先看 `/acp logs <name>`，排除原因后再 `/acp restart <name>`。

### 排错

**服务器启动失败。**
1. 在 `/acp status <name>` 里看错误。
2. 在 `/acp logs <name>` 里看 stderr。
3. 确认 `command` 存在且可执行，`cwd` 是一个目录。

**`requires authentication`。**
- agent 回了 `auth_required`。如果它提供终端登录，运行 `/acp login <name>`；否则在 Agentao 之外完成认证，再运行 `/acp restart <name>`。
- 从环境读服务商密钥的 agent 看不到你 shell 里的密钥，见 [不继承服务商密钥](#不继承服务商密钥)。

**Registry agent 第一次启动超时。**
- 第一次 `npx` 或 `uvx` 运行要下载包。Registry 条目等 `initialize` 最多 120 秒。网络慢就调大 `startupTimeoutMs`，或先在 shell 里跑一次启动命令，把启动器的缓存填好。
- `npx` 或 `uvx` 必须在 `PATH` 上。

**服务器启动了，但握手失败。**
- 服务器必须用合法的 ACP 响应回答 `initialize`，并在 `session/new` 的回答里给出 `sessionId`。
- 在 `/acp logs <name>` 里找协议错误。

**提示已有进行中的一轮。**
- `/acp send` 会取消那一轮旧的，再发一次。仍然失败的话，运行 `/acp cancel <name>` 或 `/acp restart <name>`。

**`Timeout waiting for response`。**
- 这一轮超过了 `requestTimeoutMs`，CLI 已将其取消。调大该服务器的 `requestTimeoutMs`。

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
- ACP：CLI 持有自己的 `ACPManager`。宿主用 `ACPManager.from_project()` 建一个，就能拿到同样的状态、send、cancel，见 [3.4 · 调用外部 ACP Agent](/zh/part-3/4-reverse-acp-call)
- Plugins：`PluginManager`（在 `agentao.embedding.plugins.manager`）— 诊断报告由 `agentao.embedding.plugins.diagnostics.build_diagnostics` 生成，宿主也可以调。完整的可编程接口见 [Part 5.7](/zh/part-5/7-plugin-hooks)
:::

::: tip 真相源头
命令语法：`/help`。行为锚点：
- [`agentao/cli/commands/mcp.py:handle_mcp_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands/mcp.py)
- [`agentao/cli/commands_ext/acp.py:handle_acp_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands_ext/acp.py)
- [`agentao/cli/subcommands.py:_handle_plugins_interactive`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/subcommands.py)
:::
