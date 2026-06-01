# 第五部分 · 扩展 Agent 行为

这一部分分成两条轴：**能力平面**让 Agent 获得业务能力、知识和规则；**控制平面**让你在 Agent 走到关键生命周期点时介入。5.1–5.6 是能力平面，5.7 是控制平面。

::: info 本部分关键词
- **Tool 子类** — 暴露业务能力的标准形式：`name` / `description` / `parameters` / `execute()` · [§5.1](/zh/part-5/1-custom-tools)、[G.2](/zh/appendix/g-glossary#g-2-扩展点)
- **`requires_confirmation`** — Tool 标志位 → 副作用调用触发 `ask_confirmation` UI · [§5.1](/zh/part-5/1-custom-tools)、[§5.4](/zh/part-5/4-permissions)
- **PermissionEngine** — Tier 0 硬护栏 + 预设 + 自定义规则；规则化防线 · [§5.4](/zh/part-5/4-permissions)、[G.5](/zh/appendix/g-glossary#g-5-安全术语)
- **Skill（技能）** — `SKILL.md` + YAML frontmatter，从 `skills/` 自动发现；LLM 侧指令 · [§5.2](/zh/part-5/2-skills)、[G.2](/zh/appendix/g-glossary#g-2-扩展点)
- **MemoryManager** — SQLite 后端，双作用域（`project` + `user`）的持久化 + 会话记忆 · [§5.5](/zh/part-5/5-memory)、[G.1](/zh/appendix/g-glossary#g-1-核心概念)
- **Plugin Hook** — `hooks.json` 规则，对齐 Claude Code；在生命周期点拦截 / 注入 / 续轮 · [§5.7](/zh/part-5/7-plugin-hooks)
:::

## 本部分覆盖

**能力平面**

- [**5.1 自定义工具与宿主注入**](./1-custom-tools) — 写工具、注入 / 选择工具面，以及常见坑
- [**5.2 技能（Skills）**](./2-skills) — 给 LLM 看的 Markdown 指令
- [**5.3 MCP 服务器接入**](./3-mcp) — 复用社区/官方工具生态
- [**5.4 权限引擎**](./4-permissions) — 第一道规则防线，与 confirm_tool 分层配合
- [**5.5 记忆系统**](./5-memory) — 跨会话持久化与合规性
- [**5.6 系统提示定制**](./6-system-prompt) — 11 个提示块中你能动的 3 个

**控制平面**

- [**5.7 插件 Hooks**](./7-plugin-hooks) — `hooks.json` 规则；UserPromptSubmit / PreToolUse / Stop / PreCompact 等生命周期点的注入与拦截

## 按任务阅读

| 你要做什么 | 推荐路径 | 读完应能完成 |
|-----------|---------|-------------|
| 让 Agent 调你的业务 API | [5.1](./1-custom-tools) → [5.4](./4-permissions) | 写一个 Tool 并注入 Agent，再给副作用能力加确认或权限边界 |
| 从宿主侧选择 / 收缩工具面 | [5.1](./1-custom-tools) | 在构造期或运行期注入、替换、裁剪工具——并清楚它不是安全边界 |
| 让 Agent 遵守团队规范 | [5.2](./2-skills) → [5.6](./6-system-prompt) | 区分“按需触发的技能”和“全局生效的项目提示” |
| 接入已有服务生态 | [5.3](./3-mcp) → [5.4](./4-permissions) | 接入 MCP，同时限制工具可见性和可执行范围 |
| 记住长期事实或用户偏好 | [5.5](./5-memory) → [6.4](/zh/part-6/4-multi-tenant-fs) | 设计记忆作用域、清理策略和租户边界 |
| 在生命周期点拦截、注入或续轮 | [5.7](./7-plugin-hooks) → [4.7](/zh/part-4/7-host-contract) | 写 `hooks.json`，并知道何时该用稳定事件流做审计 |
| 不确定该选 Tool、Skill、MCP 还是 Hook | [5.1](./1-custom-tools) → [5.2](./2-skills) → [5.3](./3-mcp) → [5.7](./7-plugin-hooks) | 按能力、指令、外部生态、生命周期介入四类做取舍 |

## 三条落地建议

1. **先从 5.4 开始**：部署前就把权限规则写好，避免"先跑着后面再加安全"的技术债
2. **5.1 和 5.3 二选一**：同一能力**不要**同时实现成工具和 MCP，LLM 会困惑
3. **技能贵精不贵多**：每个技能只做一件事，触发描述具体；多技能比大技能好

→ [5.1 自定义工具 →](./1-custom-tools)
