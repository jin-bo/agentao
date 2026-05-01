# Appendix G · Bilingual Glossary

Canonical EN ↔ ZH terms used in this guide. If you find a translation that doesn't match this table, file an issue — consistency matters when teams speak both languages.

## G.1 Core concepts

| English | 中文 | Definition |
|---------|------|------------|
| Agent | 智能体 / Agent | The LLM-driven loop that calls tools and returns text. In this guide, an **instance of `Agentao`** |
| Harness | 框架 / 运行容器 | The reusable runtime that wraps an agent — Agentao itself |
| Embedded Harness Contract | 嵌入式 Harness 合约 / 前向兼容宿主合约 | The **stable host-facing API** at `agentao.harness` (since 0.3.1): Pydantic-modeled events, policy snapshot, and capability protocols whose wire form is schema-snapshotted and CI-enforced. Hosts that target only this surface stay forward-compatible across releases. See [4.7](/en/part-4/7-harness-contract). |
| Harness event | Harness 事件 | One of `ToolLifecycleEvent` / `SubagentLifecycleEvent` / `PermissionDecisionEvent` — the schema-stable projection consumed via `agent.events()` (distinct from internal `AgentEvent`) |
| Active permissions snapshot | 策略快照 / 当前权限快照 | The `ActivePermissions` value returned by `agent.active_permissions()` — `mode` + `rules` + `loaded_sources`, JSON-safe and pinnable into audit logs |
| Capability protocol | 能力协议 | `FileSystem` / `ShellExecutor` runtime-checkable Protocols at `agentao.harness.protocols`, injected at construction time to route IO through Docker / virtual FS / audit proxies |
| Schema snapshot | Schema 快照 | The checked-in JSON schema files (`docs/schema/harness.events.v1.json`, `harness.acp.v1.json`) re-generated from the Pydantic models and CI-asserted byte-equal — the wire-form contract |
| Session | 会话 | One agent instance's lifetime of conversation, bound to a `working_directory` |
| Turn | 一轮 / 一次 | One `chat()` call; may fire many tool calls internally |
| Iteration | 迭代 | Each LLM round within a single turn, capped by `max_iterations` |
| Working directory | 工作目录 | The filesystem root pinned at construction time; tool calls and memory DBs are scoped under it |
| Tool | 工具 | A unit of capability the LLM can call — subclass of `Tool` ABC |
| Skill | 技能 | A `SKILL.md` bundle injected into the system prompt when activated |
| System prompt | 系统提示 | The composed header (AGENTAO.md + date + skills + memory) prepended to every turn |

## G.2 Extension points

| English | 中文 | Definition |
|---------|------|------------|
| Transport | 传输层 | The UI-facing event/confirmation protocol (`Transport` Protocol) |
| Event | 事件 | One `AgentEvent` emitted during a turn (TURN_START, TOOL_START, LLM_TEXT, …) |
| Permission engine | 权限引擎 | `PermissionEngine` — decides allow / deny / ask per tool call |
| Permission mode | 权限模式 | `READ_ONLY` / `WORKSPACE_WRITE` / `FULL_ACCESS` / `PLAN` presets |
| Memory | 记忆 | Persistent notes stored in SQLite (project + user scopes) |
| Memory scope | 记忆作用域 | `project` (inside `working_directory`) vs `user` (`~/.agentao/`) |
| MCP | MCP / Model Context Protocol | Third-party tool server protocol (stdio / SSE) |
| Sandbox | 沙箱 | macOS `sandbox-exec` profile wrapping the shell tool |
| Skill activation | 激活技能 | Turning a skill on for the current agent so its prompt text is injected |

## G.3 ACP terms

| English | 中文 | Definition |
|---------|------|------------|
| ACP | ACP / Agent Client Protocol | The stdio + NDJSON JSON-RPC 2.0 protocol Agentao speaks when run with `--acp --stdio` |
| ACP server | ACP 服务器 | The Agentao subprocess in an ACP connection |
| ACP client | ACP 客户端 | The host process that spawns and drives the ACP server |
| `initialize` | 初始化 | The first RPC call — negotiates `protocolVersion` and capabilities |
| `session/new` | 新建会话 | Creates a fresh session bound to a cwd |
| `session/load` | 加载会话 | Resumes a previously saved session by id (only when agent advertises `loadSession: true`) |
| `session/prompt` | 发起提示 | One user turn, bounded — returns when the agent stops |
| `session/cancel` | 取消会话 | Abort the current turn and all pending permission requests |
| `session/update` | 会话更新（通知） | Streaming updates emitted during a turn (message chunks, tool starts, …) |
| `session/request_permission` | 请求权限（通知） | Server asks the client to confirm a tool call |
| Capability negotiation | 能力协商 | Exchange of `clientCapabilities` / `agentCapabilities` at init time |

## G.4 Integration patterns

| English | 中文 | Definition |
|---------|------|------------|
| In-process SDK | 进程内 SDK | Importing `agentao` as a Python library — same process as the host |
| ACP stdio | ACP stdio 集成 | Running Agentao as a subprocess and speaking JSON-RPC over stdio |
| Session pool | 会话池 | Cache of long-lived `Agentao` instances keyed by `session_id` |
| TTL eviction | TTL 驱逐 | Removing pool entries idle beyond a time-to-live |
| LRU eviction | LRU 驱逐 | Removing the least-recently-used pool entry when capacity is hit |
| `prompt_once` | 一次性提示 | `ACPManager.prompt_once()` — single-turn fire-and-forget API |
| Headless runtime | 无头运行时 | Running Agentao as a non-interactive embedding target — `ACPManager` driving ACP servers with typed `get_status()` snapshots and no human in the loop. See [`docs/features/headless-runtime.md`](../../../docs/features/headless-runtime.md). |
| Tenant | 租户 | The top-level isolation unit in multi-tenant SaaS — each tenant has its own working directory + memory |
| Canary | 灰度 | Rolling out a change to a small traffic % before full deployment |

## G.5 Security vocabulary

| English | 中文 | Definition |
|---------|------|------------|
| Defense in depth | 多层防御 / 纵深防御 | Stacked, independent security layers; each assumes the one above failed |
| SSRF | SSRF（服务端请求伪造） | Server-Side Request Forgery — agent used to probe internal network |
| Prompt injection | 提示词注入 | Malicious content in tool output / user input that hijacks the LLM |
| Tool confirmation | 工具确认 | User approval before a dangerous tool runs |
| Allowlist | 白名单 | Explicit set of permitted items; default-deny |
| Blocklist | 黑名单 | Explicit set of forbidden items; default-allow |
| Fail-closed | 失败即禁用 | On config error, block the action rather than allow it |
| Scrubbing | 脱敏 / 擦除 | Removing secrets from logs before writing |
| Working-directory trap | 工作目录陷阱 | Sharing `working_directory` across tenants → cross-tenant data leak |

## G.6 Event types (quick reference)

Full reference in [Part 4.2](/en/part-4/2-agent-events). This table fixes the translations.

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

## G.7 Translation rules of thumb

- **Keep English for protocols and APIs**: `initialize`, `session/prompt`, `AgentEvent`, `PermissionEngine`. Do **not** translate symbol names.
- **Translate concepts, not classes**: "session" → "会话" is fine when the word appears in prose; `session_id` stays English.
- **"Agent" and "智能体"** coexist. In Chinese prose prefer "Agent" when referring to this codebase (avoids confusion with broader Chinese "智能体" usage); use "智能体" when contrasting with non-agentic systems.
- **Sandbox profiles** keep English names (`workspace-write`, `readonly`) because they're config literals.

---

End of Appendix G. Appendices A / C / E / F remain to be written — see the main guide for API surface (Parts 2–3) and ACP message details (Part 3) in the meantime.
