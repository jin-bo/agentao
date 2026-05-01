# 附录 G · 双语术语表

本指南统一使用的 EN ↔ ZH 译名。如你发现与此表不符的译法，请提 issue——双语团队最怕译名打架。

## G.1 核心概念

| English | 中文 | 定义 |
|---------|------|------|
| Agent | 智能体 / Agent | 调工具、返回文本的 LLM 驱动循环。本指南里特指**一个 `Agentao` 实例** |
| Harness | 框架 / 运行容器 | 封装 agent 的可复用运行时——Agentao 本身 |
| Embedded Harness Contract | 嵌入式 Harness 合约 / 前向兼容宿主合约 | `agentao.harness` 暴露的**稳定宿主 API**（自 0.3.1）：Pydantic 建模的事件、策略快照、能力协议；wire 形态有 schema 快照并在 CI 强制。只触这个面的宿主代码可以跨版本不断。详见 [4.7](/zh/part-4/7-harness-contract)。 |
| Harness event | Harness 事件 | `ToolLifecycleEvent` / `SubagentLifecycleEvent` / `PermissionDecisionEvent` 三种之一——schema 稳定的投影，通过 `agent.events()` 消费（与内部 `AgentEvent` 不同） |
| Active permissions snapshot | 策略快照 / 当前权限快照 | `agent.active_permissions()` 返回的 `ActivePermissions`：`mode` + `rules` + `loaded_sources`，JSON 安全，可钉进审计日志 |
| Capability protocol | 能力协议 | `agentao.harness.protocols` 下的 `FileSystem` / `ShellExecutor` 运行时可检 Protocol，构造时注入，把 IO 路由到 Docker / 虚拟 FS / 审计代理 |
| Schema snapshot | Schema 快照 | 仓库里 checked-in 的 JSON schema 文件（`docs/schema/harness.events.v1.json`、`harness.acp.v1.json`），由 Pydantic 模型重生成并 CI 断言字节级一致——是 wire 形态的合约 |
| Session | 会话 | 一个 agent 实例的完整对话生命周期，绑定在一个 `working_directory` 上 |
| Turn | 一轮 / 一次 | 一次 `chat()` 调用；内部可能触发多次工具调用 |
| Iteration | 迭代 | 单轮内的每一次 LLM 循环，受 `max_iterations` 限制 |
| Working directory | 工作目录 | 构造时固定的文件系统根；工具调用与 memory DB 都在它下面 |
| Tool | 工具 | LLM 可调用的能力单元——`Tool` 抽象基类的子类 |
| Skill | 技能 | 一份 `SKILL.md` 组合，激活后注入系统提示 |
| System prompt | 系统提示 | 每轮拼装的头部（AGENTAO.md + 日期 + 技能 + 记忆） |

## G.2 扩展点

| English | 中文 | 定义 |
|---------|------|------|
| Transport | 传输层 | 面向 UI 的事件/确认协议（`Transport` Protocol） |
| Event | 事件 | 一轮中发出的 `AgentEvent`（TURN_START、TOOL_START、LLM_TEXT……） |
| Permission engine | 权限引擎 | `PermissionEngine`——决定每次工具调用 allow / deny / ask |
| Permission mode | 权限模式 | `READ_ONLY` / `WORKSPACE_WRITE` / `FULL_ACCESS` / `PLAN` 预设 |
| Memory | 记忆 | 存在 SQLite 里的持久笔记（project + user 两个作用域） |
| Memory scope | 记忆作用域 | `project`（`working_directory` 内）vs `user`（`~/.agentao/`） |
| MCP | MCP / Model Context Protocol | 第三方工具服务器协议（stdio / SSE） |
| Sandbox | 沙箱 | macOS `sandbox-exec` 对 shell 工具的封装 |
| Skill activation | 激活技能 | 把技能对当前 agent 打开，让它的提示文本被注入 |

## G.3 ACP 相关

| English | 中文 | 定义 |
|---------|------|------|
| ACP | ACP / Agent Client Protocol | Agentao 在 `--acp --stdio` 模式下说的 stdio + NDJSON JSON-RPC 2.0 协议 |
| ACP server | ACP 服务器 | ACP 连接里的 Agentao 子进程 |
| ACP client | ACP 客户端 | 启动并驱动 ACP 服务器的宿主进程 |
| `initialize` | 初始化 | 第一个 RPC——协商 `protocolVersion` 和能力 |
| `session/new` | 新建会话 | 创建绑定到一个 cwd 的新会话 |
| `session/load` | 加载会话 | 按 id 恢复之前保存的会话（仅当 agent 声明 `loadSession: true`） |
| `session/prompt` | 发起提示 | 一轮用户交互，有限时间内返回 |
| `session/cancel` | 取消会话 | 中止当前轮，及所有挂起的权限请求 |
| `session/update` | 会话更新（通知） | 轮内流式更新（消息片段、工具开始……） |
| `session/request_permission` | 请求权限（通知） | 服务器请求客户端确认工具调用 |
| Capability negotiation | 能力协商 | init 时交换 `clientCapabilities` / `agentCapabilities` |

## G.4 集成模式

| English | 中文 | 定义 |
|---------|------|------|
| In-process SDK | 进程内 SDK | 以 Python 库形式 `import agentao`——与宿主同进程 |
| ACP stdio | ACP stdio 集成 | 把 Agentao 作为子进程，通过 stdio 讲 JSON-RPC |
| Session pool | 会话池 | 按 `session_id` 缓存长生命周期 `Agentao` 实例 |
| TTL eviction | TTL 驱逐 | 移除闲置超过 TTL 的会话池条目 |
| LRU eviction | LRU 驱逐 | 容量满时移除最久未使用的会话池条目 |
| `prompt_once` | 一次性提示 | `ACPManager.prompt_once()`——单轮 fire-and-forget API |
| Headless runtime | 无头运行时 | 把 Agentao 作为非交互 embedding 目标运行——`ACPManager` 驱动 ACP 服务器，`get_status()` 返回类型化快照，全程无需人工介入。见 [`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md)。 |
| Tenant | 租户 | 多租户 SaaS 中最外层的隔离单元；每租户独立 working directory + memory |
| Canary | 灰度 | 先放小比例流量再全量部署 |

## G.5 安全术语

| English | 中文 | 定义 |
|---------|------|------|
| Defense in depth | 多层防御 / 纵深防御 | 堆叠且相互独立的安全层；每层都假设上一层已失守 |
| SSRF | SSRF（服务端请求伪造） | Server-Side Request Forgery——agent 被用来探内网 |
| Prompt injection | 提示词注入 | 工具输出/用户输入里夹带恶意内容挟持 LLM |
| Tool confirmation | 工具确认 | 危险工具执行前的用户确认 |
| Allowlist | 白名单 | 显式允许清单；默认拒绝 |
| Blocklist | 黑名单 | 显式禁止清单；默认允许 |
| Fail-closed | 失败即禁用 | 配置出错时拒绝动作而非放行 |
| Scrubbing | 脱敏 / 擦除 | 写日志前去掉密钥 |
| Working-directory trap | 工作目录陷阱 | 跨租户共享 `working_directory` → 数据泄漏 |

## G.6 事件类型（速查）

完整参考见 [Part 4.2](/zh/part-4/2-agent-events)。本表固定中文译名。

| EventType | 中文 |
|-----------|------|
| `TURN_START` | 轮开始 |
| `TOOL_CONFIRMATION` | 工具确认 |
| `TOOL_START` | 工具开始 |
| `TOOL_OUTPUT` | 工具输出 |
| `TOOL_COMPLETE` | 工具完成 |
| `TOOL_RESULT` | 工具结果 |
| `THINKING` | 思考 |
| `LLM_TEXT` | LLM 文本（流式） |
| `LLM_CALL_STARTED` / `LLM_CALL_COMPLETED` | LLM 调用开始 / 完成 |
| `LLM_CALL_DELTA` / `LLM_CALL_IO` | LLM 调用增量 / 完整 IO |
| `ERROR` | 错误 |
| `AGENT_START` / `AGENT_END` | Agent 启动 / 结束 |
| `ASK_USER_REQUESTED` / `ASK_USER_ANSWERED` | 用户询问发起 / 已回答 |
| `BACKGROUND_NOTIFICATION_INJECTED` | 后台通知注入 |
| `CONTEXT_COMPRESSED` | 上下文压缩 |
| `SESSION_SUMMARY_WRITTEN` | 会话摘要写入 |
| `SKILL_ACTIVATED` / `SKILL_DEACTIVATED` | Skill 激活 / 停用 |
| `MEMORY_WRITE` / `MEMORY_DELETE` / `MEMORY_CLEARED` | Memory 写入 / 删除 / 清空 |
| `MODEL_CHANGED` | 模型切换 |
| `PERMISSION_MODE_CHANGED` / `READONLY_MODE_CHANGED` | 权限模式切换 / 只读模式切换 |
| `PLUGIN_HOOK_FIRED` | Plugin hook 触发 |

## G.7 翻译原则

- **协议与 API 名保留英文**：`initialize`、`session/prompt`、`AgentEvent`、`PermissionEngine`——**不翻**符号名
- **翻概念不翻类**：正文里 "session" → "会话" 可接受；代码里 `session_id` 保持英文
- **"Agent" 与 "智能体"** 并存：中文正文指本代码库时优先 "Agent"（避开"智能体"的宽泛用法）；对比非 agent 系统时用"智能体"
- **沙箱 profile** 名保留英文（`workspace-write`、`readonly`），因为它们是配置字面量

---

附录 G 至此结束。附录 A / C / E / F 仍在补充——在此之前可查阅正文 Part 2–3（API 面）与 Part 3（ACP 消息）。
