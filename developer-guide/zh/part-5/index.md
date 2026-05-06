# 第五部分 · 让 Agent 理解你的业务

前六节构成 Agentao 的**业务化能力接口**——按它们扩展，你能把 Agent 调教成真正"懂你业务"的智能助理。第七节换一条轴：**控制平面**——在 agent 走到关键步骤时介入。

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

- [**5.1 自定义工具**](./1-custom-tools) — 把业务 API 暴露给 LLM 的首选方式
- [**5.2 技能（Skills）**](./2-skills) — 给 LLM 看的 Markdown 指令
- [**5.3 MCP 服务器接入**](./3-mcp) — 复用社区/官方工具生态
- [**5.4 权限引擎**](./4-permissions) — 第一道规则防线，与 confirm_tool 分层配合
- [**5.5 记忆系统**](./5-memory) — 跨会话持久化与合规性
- [**5.6 系统提示定制**](./6-system-prompt) — 11 个提示块中你能动的 3 个

**控制平面**

- [**5.7 插件 Hooks**](./7-plugin-hooks) — `hooks.json` 规则；UserPromptSubmit / PreToolUse / Stop / PreCompact 等生命周期点的注入与拦截

## 如何挑选

| 你的需求 | 最佳扩展点 |
|---------|----------|
| 让 Agent 能调用你的业务 API | 5.1 工具 |
| 让 Agent 按公司规范说话/做事 | 5.2 技能 + 5.6 AGENTAO.md |
| 接入 GitHub / 数据库 / Slack 等现成服务 | 5.3 MCP |
| 控制"什么能做、什么不行" | 5.4 权限 |
| 记住用户偏好、项目事实 | 5.5 记忆 |
| 注入项目级硬约束 | 5.6 AGENTAO.md |
| 在生命周期点拦截 / 审计 / 续轮 | 5.7 Hooks |

## 三条落地建议

1. **先从 5.4 开始**：部署前就把权限规则写好，避免"先跑着后面再加安全"的技术债
2. **5.1 和 5.3 二选一**：同一能力**不要**同时实现成工具和 MCP，LLM 会困惑
3. **技能贵精不贵多**：每个技能只做一件事，触发描述具体；多技能比大技能好

→ [5.1 自定义工具 →](./1-custom-tools)
