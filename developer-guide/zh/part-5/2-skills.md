# 5.2 技能（Skills）与插件目录

**技能不是代码，是给 LLM 看的 Markdown 指令**。当"让 Agent 按你们公司的规范做事"不需要新工具、只需要新约束/新流程时，写一个技能比写代码高效 10 倍。

## 技能的形态

每个技能是一个目录：

```
my-skill/
├── SKILL.md              # 必需：入口文件
└── reference/            # 可选：按需加载的辅助文档
    ├── conventions.md
    └── templates.md
```

### `SKILL.md` 格式

```markdown
---
name: customer-ticket-handler
description: 处理客户工单时使用——遵循公司退款规范、用固定措辞、先查订单再给答复。
---

# 客户工单处理技能

当用户在工单里询问退款/投诉/发货问题时，按本指南执行：

## 步骤

1. 先用 `get_customer_orders` 工具查订单
2. 如果订单状态是 "delivered" 超过 30 天 → 礼貌拒绝
3. 如果是物流问题 → 模板回复 + 转物流部
4. ...

## 口径约束

- 不承诺具体赔偿金额
- 不透露内部系统名
- 签名统一："客户服务团队"

## 复杂情况

需要例外处理时，参考 `reference/conventions.md` 中的边界情况清单。
```

### YAML Frontmatter

只有两个字段：

| 字段 | 必填 | 用途 |
|------|------|------|
| `name` | ✅ | 唯一 id；不能含空格，建议 kebab-case |
| `description` | ✅ | **触发描述**——告诉 LLM 什么时候激活这个技能 |

`description` 被注入到系统提示的"可用技能清单"里。LLM 看到用户请求匹配 description 时，会主动调用 `activate_skill` 工具激活它。

## 三层目录搜索顺序

源码：`agentao/skills/manager.py:27-43`

```
1. ~/.agentao/skills/            ← 全局（所有项目共享）
2. <cwd>/.agentao/skills/        ← 项目配置目录
3. <cwd>/skills/                 ← 项目仓库目录
```

**同名冲突时，后加载的覆盖先加载的**（也就是 `.agentao/skills/` 覆盖全局，`skills/` 覆盖前两层）。所以：

- **嵌入你的产品时**：把技能放在**项目 repo 根的 `skills/` 下**，用户拉代码即有
- **用户自定义**：留出 `~/.agentao/skills/` 让用户自己写个人技能
- **临时/试验**：`.agentao/skills/` 适合本项目专属但不想污染 git

### 多租户场景的隔离

ACP 或多实例 Python 嵌入时，每个 Agent 构造时传的 `working_directory` 决定它看到的项目级技能目录——两个租户互不干扰。

```python
# tenant-a 只能看到 /data/tenant-a 下的技能
agent_a = Agentao(working_directory=Path("/data/tenant-a"))
# tenant-b 只能看到 /data/tenant-b 下的技能
agent_b = Agentao(working_directory=Path("/data/tenant-b"))
```

但 **`~/.agentao/skills/` 是全局共享**的（进程级用户 HOME），如果你不希望它被加载，在构造 Agent 之前用环境变量改 HOME 或者设计成 `SkillManager(skills_dir=...)` 的私有目录。

## 激活机制

技能**不是被动加载的** —— 它们默认**不在**系统提示里。只有当 LLM 通过 `activate_skill` 工具主动激活时，技能的 SKILL.md 全文才会被注入。

```
┌─────────────────┐
│ available_skills │  ← 全部技能（只看到 name + description）
└─────────────────┘
         │ LLM 决定："这次需要 customer-ticket-handler"
         ▼
   activate_skill("customer-ticket-handler")
         │
         ▼
┌─────────────────┐
│  active_skills   │  ← 激活的技能（全文注入系统提示）
└─────────────────┘
```

这个设计让你可以**堆很多技能**而不污染上下文：只有真正用到的才占 token。

## 按需参考目录

`reference/*.md` 不会自动加载。技能正文里可以告诉 LLM："如果你遇到特殊情况，用 `read_file` 读 `skills/my-skill/reference/edge-cases.md`"。

这样做的好处：

- 技能主文件保持 < 2KB，容易被 LLM 吸收
- 附属文档只在**真正需要时**才加载，节省 token
- 复杂知识可以按主题拆分

## 写一个好技能的 3 条原则

### 1. 触发描述必须具体

❌ `description: "Handles customer issues"` — 太宽，LLM 会在一切场景激活
✅ `description: "Activate when user asks about refunds, returns, or delivery issues. Not for sales questions."` — 明确何时用、何时不用

### 2. 用祈使句和具体步骤

❌ `"We generally prefer JSON responses..."` — LLM 不知道"generally"是啥
✅ `"When using get_customer_orders, always filter by customer_id. Never include internal tenant_id in user-facing replies."` — 确切、可验证

### 3. 失败场景优先

写技能时先列**不该做的**再列**该做的**：

```markdown
## 绝对禁止
- 不得跨客户查询数据
- 不得承诺具体退款时效
- 不得暴露内部 API endpoint

## 正确做法
- ...
```

LLM 更擅长遵守"禁止"比学会"应该"，这个顺序能提高合规率。

## 示例：给工程团队的"代码审查"技能

```
skills/code-review/
├── SKILL.md
└── reference/
    ├── security-checklist.md
    └── performance-patterns.md
```

**SKILL.md**:

```markdown
---
name: code-review
description: Activate before reviewing any pull request or diff. Enforces our team's review standards for Python / TypeScript code.
---

# Code Review Standards

## Your role

You are a principal engineer doing a pre-merge review. Be direct, be specific, cite line numbers.

## Review order

1. **Security first**: check `reference/security-checklist.md` for the full list
2. **Correctness**: edge cases, error paths, async correctness
3. **Performance**: N+1 queries, unbounded loops, memory leaks
4. **Style**: conforms to existing patterns in the same file — do not impose
   new abstractions unless justified

## Output format

For each finding, produce exactly:

- **Severity** (Blocker / Major / Minor / Nit)
- **File:line**
- **The issue** (one sentence)
- **Suggested fix** (code or concrete action)

End with one paragraph summarizing the overall quality and whether it's
ready to merge.

## Strong rules

- Never say "looks good to me" without reading every changed file
- Never suggest refactors outside the diff scope unless they are Blockers
- Never be vague ("consider refactoring") — always name the concrete issue
```

## 调试技能：看它到底进没进系统提示

```python
# 构造后检查
agent = Agentao(working_directory=Path.cwd())
print(list(agent.skill_manager.available_skills.keys()))   # 都发现了吗？
print(list(agent.skill_manager.active_skills.keys()))       # 当前激活了哪些？

# 手动激活（通常 LLM 自己激活）
agent.skill_manager.activate_skill("customer-ticket-handler")

# 查看激活后的注入内容
print(agent.skill_manager.get_skills_context())
```

## 技能 vs 工具：何时加工具、何时加技能？

| 场景 | 选哪个 |
|------|-------|
| "每次写代码都要用 Python 3.12 类型注解" | **技能**（约束风格） |
| "查询数据库" | **工具**（实现能力） |
| "回答退款问题用固定模板" | **技能** |
| "发 Slack 消息" | **工具** |
| "先问澄清再做，不要贸然动手" | **技能** |
| "上传文件到 S3" | **工具** |

**口诀**：需要新能力 → 工具；需要新约束 → 技能。

→ 下一节：[5.3 MCP 服务器接入](./3-mcp)
