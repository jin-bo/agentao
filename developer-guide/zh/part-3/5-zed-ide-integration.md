# 3.5 Zed / IDE 集成实操

Zed 的"外部 agent"功能原生讲 ACP——意思是 Agentao 不需要任何胶水就能接入。同样的模式适用于任何支持外部 ACP agent 的 IDE / 编辑器。本节讲端到端接入。

## 3.5.1 "ACP 集成"在 IDE 里意味着什么

实现了 ACP 客户端侧的 IDE 能做到：

1. 把任意 ACP server 当子进程启动
2. 把用户聊天转成 `session/prompt`
3. 把 `session/update` 通知渲染成滚动的聊天 UI
4. 把 `session/request_permission` 弹成模态
5. 通过 `cwd` + prompt 文本转发编辑器上下文（打开的文件、选中区）

用户只要在 IDE 设置里加上你的 agent——剩下全是传输。

## 3.5.2 在 Zed 里注册 Agentao

Zed 从 `~/.config/zed/settings.json`（或 UI 的 **Settings → Agents**）读 agent 配置。加一条指向 `agentao --acp --stdio`：

```json
{
  "agents": [
    {
      "name": "agentao",
      "command": "agentao",
      "args": ["--acp", "--stdio"],
      "env": {
        "OPENAI_API_KEY": "sk-...",
        "OPENAI_MODEL": "gpt-5.4"
      }
    }
  ]
}
```

重启 Zed，打开任意项目，Agentao 就出现在 agent 选择器里。

### 注意

- `command` 必须在 `PATH` 上。`uv tool install agentao` 装的已经在了；否则用绝对路径：`/Users/you/.local/bin/agentao`
- `env` 块会和 Zed 自己的环境合并。API key 建议放到 `~/.zshenv` / `~/.bashrc`，别写进 `settings.json`
- Zed 会把 workspace 根作为 `cwd` 传进 `session/new`——所以 `AGENTAO.md`、`.agentao/mcp.json`、技能、记忆自动按 workspace 作用域生效

## 3.5.3 端到端线协议轨迹

用户在 Zed 的 agent 窗格里打字时会发生：

```
USER 在 Zed 里输入 "找 TODO"                                  time
                                                              ────▶
Zed → agentao:  initialize（进程启动时一次）
agentao → Zed:  capabilities + extensions

Zed → agentao:  session/new  {cwd: "/workspace"}
agentao → Zed:  {sessionId: "sess-1"}

Zed → agentao:  session/prompt  {sessionId, prompt:[{"type":"text",
                                                      "text":"找 TODO"}]}

agentao → Zed:  session/update  agent_message_chunk  "让我搜..."
agentao → Zed:  session/update  tool_call            {title:"grep -r TODO"}
agentao → Zed:  session/update  tool_call_update     {output:"src/x.py:42: TODO ..."}
agentao → Zed:  session/update  agent_message_chunk  "找到 3 处 TODO..."
agentao → Zed:  response        {stopReason: "end_turn"}

（持续）        session/update 通知流推到 Zed UI
```

Zed 把每条 `session/update` 如你所料地渲染：文本 chunk 流进回复、工具调用作为内联卡片、权限请求弹模态。

## 3.5.4 Zed 需要具备的能力

Agentao 在 `initialize` 里声明：

```json
{
  "agentCapabilities": {
    "loadSession": true,
    "promptCapabilities": { "image": false, "audio": false, "embeddedContext": false },
    "mcpCapabilities":   { "http": false, "sse": true }
  }
}
```

含义：

- ✅ `loadSession`：能从磁盘恢复历史会话
- ❌ `image` / `audio`：v1 只支持纯文本 prompt
- ❌ `embeddedContext`：Zed 无法把内嵌资源拉取推过来
- ✅ `sse`：Zed 可以转发 SSE MCP server
- ❌ `http`：Agentao 不支持 HTTP MCP 传输

Zed 对不支持的能力会优雅降级。

## 3.5.5 多 workspace / 多窗口

Zed **每个 agent 实例起一个 `agentao` 子进程**，每个项目通过 `session/new` 拿独立 session。也就是：

- 多个 Zed 窗口共用一个 `agentao` 进程
- 每个 workspace 的 `.agentao/memory.db` 按 `cwd` 隔离
- 按 session 传入 `mcpServers` 启动的 MCP 在那个 session 内独立

想每 workspace 一个进程（更严格隔离，但更耗 RAM），在 Zed 那边配置每 workspace 独立 agent——具体开关名看 Zed 最新文档。

## 3.5.6 VS Code / Cursor / 其它编辑器

模式一样：

1. 以子进程启动 `agentao --acp --stdio`
2. 在 stdio 上讲 NDJSON JSON-RPC 2.0
3. `session/update` → UI，`session/request_permission` → 模态

使用 [3.3.4](./3-host-client-architecture#3-3-4-typescript-node-参考实现) 里 TS `ACPClient` 的 VS Code 插件见[蓝图 B](/zh/part-7/2-ide-plugin)。

### JetBrains / IntelliJ

JetBrains 插件能起子进程；用 Kotlin / Java 按同样的"三回路"模式实现 ACP 客户端即可，没有 JetBrains 特有难点。

### Neovim

用 `vim.fn.jobstart()`（Lua）之类起 `agentao --acp --stdio`。把 `session/update` 转到浮窗。社区已有成熟的 LSP 传输插件，把它们改造成 ACP 很直接。

## 3.5.7 环境与密钥

无论哪个 IDE：

- API key **绝不**写进 workspace 设置
- 优先用 OS keychain（macOS 的 `security`、Windows 的 Credential Manager、Linux 的 `secret-tool`）+ 小 wrapper 脚本：读 key 再 exec agentao
- 或：让用户在 shell profile 里设置 `OPENAI_API_KEY`，IDE 继承环境

macOS wrapper 示例：

```bash
#!/usr/bin/env bash
# /usr/local/bin/agentao-wrapper
export OPENAI_API_KEY="$(security find-generic-password -ws openai-api-key)"
exec agentao "$@"
```

让 IDE 指向 `agentao-wrapper` 而不是 `agentao`。

## 3.5.8 IDE 集成排错

出问题时：

| 症状 | 去哪查 |
|-----|-------|
| agent 在选择器里看不到 | IDE 自己的日志（Zed 是 `Help → Open Log`） |
| 首条消息就崩 | `<workspace>/agentao.log` |
| 工具调用一直挂起 | IDE 大概没回 `session/request_permission`——查它的权限 UI 代码 |
| 所有文本一次性砸出来 | IDE 没按流式渲染 `session/update`——查它的 UI 渲染 |
| MCP server 没出现 | `mcpCapabilities.http=false`——只能用 stdio 或 SSE |
| 重启后历史消失 | IDE 没调 `session/load`——这个特性可能还没实现 |

手动打线协议轨迹：

```bash
# stdin 里塞手搓 JSON
agentao --acp --stdio < trace.ndjson > output.ndjson 2> agentao.stderr.log
```

这样能划清"是你 JSON 的锅、Agentao 处理的锅，还是 IDE 渲染的锅"。

## 3.5.9 升级 Agentao 的正确姿势

用户更新 `agentao` 二进制时：

- Zed / VS Code 下次启动自然起新进程——什么都不用做
- `protocolVersion` 协商处理版本不匹配：IDE 发 `2`，Agentao 只支持 `1`，Agentao 回 `1`，IDE 决定继续或断开
- 技能、MCP、记忆按 **workspace** 作用域，不受二进制升级影响

破坏性协议变更被规避。想锁定版本，请用户 `uv tool install agentao==0.2.13`。

## 3.5.10 新 IDE 集成自查

- [ ] ACP 客户端已实现（按 [3.3](./3-host-client-architecture) 的三回路架构）
- [ ] 握手 `protocolVersion: 1` 发的是**整数**
- [ ] `session/update` 按流式 UI 渲染（chunk 立即显现）
- [ ] `session/request_permission` 必在超时前回复，不会默默吞掉
- [ ] `session/cancel` 接上"停止"按钮
- [ ] IDE 关停时清理子进程（没有孤儿）
- [ ] 错误路径：子进程退出 → 显示错误 + 允许重连
- [ ] 调试用途下记录完整线协议轨迹

---

Part 3 结束。下一节：[Part 4 · 事件层与 UI 集成](/zh/part-4/)。
