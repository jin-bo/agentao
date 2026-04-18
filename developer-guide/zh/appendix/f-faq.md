# 附录 F · FAQ 与排错

按**现象**组织，而非按章节。每条都回跳到正文细节。

## F.1 安装与启动

### "ImportError: cannot import name 'Agentao'"

- 检查是否装了包（`uv add agentao` 或 `pip install agentao`，不是只装 `openai`）
- 从顶层导入：`from agentao import Agentao`（别走 `from agentao.agent import Agentao`——那条路径不保证稳定）

### "No module named 'openai' / 'mcp'"

需要 MCP 时带上 extras：

```bash
uv add 'agentao[mcp]'          # 或
uv add 'agentao[all]'
```

### "ValueError: OPENAI_API_KEY is not set"

三条解决路径：

1. 工作目录根的 `.env`，写 `OPENAI_API_KEY=…`
2. 进程环境变量：`export OPENAI_API_KEY=…`
3. 构造器：`Agentao(api_key="sk-…")`

构造器 > env > `.env`。见 [附录 B](./b-config-keys)。

### "Model 'gpt-5.4' not found"（自建端点）

默认模型 id 是 `gpt-5.4`。如果你的端点提供别的模型，传 `model=` 或设 `OPENAI_MODEL`。见 [2.2](/zh/part-2/2-constructor-reference)。

## F.2 运行时行为

### 所有写操作都被"已取消"

你设了 `PermissionMode.READ_ONLY`（显式或默认）。两条路：

- 构造后显式切模式：`e = PermissionEngine(); e.set_mode(PermissionMode.WORKSPACE_WRITE); agent = Agentao(permission_engine=e, ...)`
- 或在 transport 上实现 `confirm_tool`，让用户交互确认

### `chat()` 永不返回

三个可能原因：

1. **工具死循环** —— 撞到 `max_iterations`。调低上限或接 `on_max_iterations`（[4.6](/zh/part-4/6-max-iterations)）
2. **工具卡住** —— 自定义工具无超时。用 `timeout=` 包住子进程/HTTP 调用（[6.7](/zh/part-6/7-resource-concurrency#控制-4-工具超时)）
3. **需要用户输入** —— 默认 `ask_user` 在无头模式会永久等待。用 `SdkTransport(ask_user=…)` override

宿主侧硬兜底：

```python
reply = await asyncio.wait_for(asyncio.to_thread(agent.chat, msg), timeout=120)
```

### "我的工具拿到了奇怪的路径"

`execute()` 收到的是 LLM 给的原样参数。校验参数，并用 `self._resolve_path(raw)` 把相对路径接到 `working_directory` 下——见 [Tool 基类](/zh/appendix/a-api-reference#a-3-工具)。

### 输出里有奇怪的转义序列

终端不支持颜色码。要么：

- 显示前在 transport 侧关掉颜色（`rich.console.Console(no_color=True)`）
- 加后处理过滤；Agentao 本身不强制任何颜色策略

## F.3 记忆与会话

### "我清了历史但老上下文还在"

`clear_history()` 只清 `self.messages`。**memory DB** 是特意保留的。要把 memory 一起清：

```python
agent.clear_history()
agent.memory.clear(scope="project")
```

### 记忆跨租户泄漏

经典多租户陷阱——你把 `~/.agentao/memory.db` 的 user 作用域跨租户挂了。两条路：

- 固定每租户的 working directory 并**禁用** user 作用域；或
- user 作用域用 `tenant_id+user_id` 做 key

见 [6.4](/zh/part-6/4-multi-tenant-fs)。

### "重启后会话丢了"

两处要修：

- **SDK**：自己序列化 `agent.messages`；重启后 `agent.messages = saved_messages`
- **ACP**：用 `session/load` + 存好的 `sessionId`——agent 必须声明 `loadSession: true`（[7.2 模式](/zh/part-7/2-ide-plugin#3-ide-重启后的会话恢复)）

## F.4 MCP

### "MCP 服务器声明了但看不到工具"

按顺序排查：

1. `/mcp` 命令（或 `agent.mcp_manager.get_status()`）——服务器是 `ready` 吗？
2. 子进程 stderr——服务器把日志打到 stdout 会破坏帧格式
3. 工具名冲突——同名 `{server}_{tool}` 注册两次会在 `agentao.log` 里警告

### "'mcp' 命令找不到"

装 MCP extras。如果 MCP 服务器是 `npx` 启动的，Linux 上可能还要装 Node。

### "服务器 timeout"

三层：

1. `mcp.json` 的单工具超时（`"timeout": 30`）
2. 传输默认（stdio 约 30s，SSE 约 60s）
3. 外层 `asyncio.wait_for`

最严格那一层胜出。见 [附录 B.3.1](./b-config-keys#b-3-1-mcp-json)。

## F.5 安全与沙箱

### "macOS 报 sandbox-exec 拒绝"

看 `agentao.log`——具体原因有记录。常见修法：

- Shell profile 太严 → 把 `default_profile` 从 `readonly` 换成 `workspace-write-no-network`
- 命令跑到 workspace 外了 → `working_directory` 内用绝对路径
- 见 [6.2](/zh/part-6/2-shell-sandbox)

### "生产上有人把沙箱关了——如何强制"

沙箱配置是合并的：项目 `.agentao/sandbox.json` 覆盖用户。容器里把项目配置以只读挂载，LLM 改不了持久化。见 [7.4 陷阱表](/zh/part-7/4-data-workbench#陷阱)。

### "Agent 试图抓 169.254.169.254"

正常——SSRF 尝试会被内置黑名单拦。`agentao.log` 里会有 deny 记录；核对 `PermissionEngine` 规则（[6.3](/zh/part-6/3-network-ssrf)）。

## F.6 ACP 集成

### initialize 时报 `handshake_fail`

多半是版本不匹配。Agentao v0.2.x 讲 `protocolVersion: 1`（整数）。如果你的客户端发 `"2025-09-01"` 这样的字符串，服务器会拒。见 [3.1](/zh/part-3/1-acp-tour)。

### `prompt_once` 报 `server_busy`

Fail-fast 语义——已有别人在 turn 里。两条路：

- 等 + 重试
- 可排队的场景改用会话式 API（`send_prompt`）
- 或每租户独立子进程

见 [附录 D.5](./d-error-codes#d-5-重试策略)。

### "session/cancel 停不下我的长工具"

取消通过 `CancellationToken` 传播，但**你的自定义工具必须配合**。长循环内定期读 `self._current_token` 并 `token.check()`。

## F.7 部署与运维

### "Docker 镜像巨大"

多阶段构建——见 [6.8 Dockerfile 模板](/zh/part-6/8-deployment#dockerfile-模板)。关键一步：别把 `uv` 带进运行时镜像。

### "K8s pod 重启后会话丢了"

用 `StatefulSet`（不是 `Deployment`），`/data` 挂 PVC。Service 设 `sessionAffinity: ClientIP`。见 [6.8](/zh/part-6/8-deployment#kubernetes-要点)。

### "每租户怎么控 token 花销"

`TokenBudget` 模式——见 [6.7](/zh/part-6/7-resource-concurrency#token-预算)。要精确计数装 `agentao[tokenizer]`（拉 `tiktoken`）。

### 成本一夜翻倍

可能原因：

- 模型版本切换（查部署审计）
- 技能改了导致每轮调的工具变多
- 上下文压缩触发更频繁——查 `max_context_tokens`

对比昨天 vs 今天的 `LLM_TEXT` 事件 token 数。事件存档（[6.6](/zh/part-6/6-observability#事件流存档)）让这件事可行。

## F.8 开发与测试

### "怎么单测自定义工具"

Tool 就是普通类——`MyTool().execute(**args)`，不需要 Agentao 实例。要动磁盘的传临时 `working_directory`。

### "怎么断言 agent 做对了事"

不要对 LLM 文本断言（不确定）。改为：

- 用 `SdkTransport(on_event=spy)` 监听 `EventType.TOOL_START`；断言工具以预期参数被调
- 或把工具 mock 了，断言交互

### "测试时 LLM 响应每次不一样"

测试时设 `temperature=0`，但措辞还是会漂。断言**效果**（工具调用、最终文件、返回形状），不断言文字。

## F.9 还是卡住？

最小复现 Bug 报告：

1. Agentao 版本（`python -c "import agentao; print(agentao.__version__)"`）
2. OS、Python 版本
3. ≤ 30 行可复现的脚本
4. 失败前后的 `agentao.log` 尾部
5. ACP 问题：`AcpClientError.code` + `.details`

提到 <https://github.com/jin-bo/agentao/issues>。
