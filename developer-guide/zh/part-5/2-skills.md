# 5.2 技能（Skills）与插件目录

> **本节你会学到**
> - 技能目录布局（`SKILL.md` + 可选 `references/*.md`）
> - 三层目录搜索顺序（用户 / 项目配置 / 项目仓库）和覆盖语义
> - 怎样写出 LLM 在恰当时机激活、其他时候不打扰的技能

**技能不是代码，是给 LLM 看的 Markdown 指令**。当"让 Agent 按你们公司的规范做事"不需要新工具、只需要新约束/新流程时，写一个技能比写代码高效 10 倍。

## 技能的形态

每个技能是一个目录：

```
my-skill/
├── SKILL.md              # 必需：入口文件
├── references/           # 可选：按需加载的辅助文档（*.md）
│   ├── conventions.md
│   └── templates.md
├── assets/               # 可选：更多文档（*.md）
└── scripts/              # 可选：辅助脚本（任意文件）
```

没有 `SKILL.md` 的子目录会被跳过。`references/`、`assets/`、`scripts/` 里的文件从不内联：激活时按绝对路径列出，模型需要时再用 `read_file` 读（见[按需参考目录](#按需参考目录)）。

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

需要例外处理时，参考 `references/conventions.md` 中的边界情况清单。
```

### YAML Frontmatter

| 字段 | 必填 | 用途 |
|------|------|------|
| `name` | ✅ | 唯一 id；不能含空格，建议 kebab-case。区分大小写 |
| `description` | ✅ | **触发描述**——告诉 LLM 什么时候激活这个技能 |
| `when-to-use` | — | 额外的触发提示，目录里显示为描述下面的 `Activate when: …` |
| `license` 及其他字段 | — | 保存在技能的 `frontmatter` 字典里，其余一概不用 |

`description` 被注入到系统提示的"可用技能清单"里。LLM 看到用户请求匹配 description 时，会主动调用 `activate_skill` 工具激活它。

本地加载器很宽松，所以写坏的技能往往是悄无声息地失效：

- 缺 `name` 时退回用目录名。
- `description` 为空的技能照样加载，`/skills` 也列得出，但目录里没有它，模型永远不会激活。
- YAML 写坏了就当作没有 frontmatter，加载器会往 `agentao.log` 写一条带文件路径的警告。最常见的原因是没加引号的冒号：`description: Deploy to AWS: ECS` 解析失败，`description: "Deploy to AWS: ECS"` 才对。
- 以 `mcp:` 开头的名字会被拒绝，这个前缀留给 [MCP 技能](/zh/part-5/3-mcp#mcp-skills)。

`agentao skill install` 则是严格的：`SKILL.md` 缺 `name` 或 `description` 就拒绝安装（[`skills/installer.py`](https://github.com/jin-bo/agentao/blob/main/agentao/skills/installer.py)）。

## 三层目录搜索顺序

```
1. ~/.agentao/skills/            ← 全局（所有项目共享）
2. <cwd>/.agentao/skills/        ← 项目配置目录
3. <cwd>/skills/                 ← 项目仓库目录
```

**同名冲突时，后加载的覆盖先加载的**（也就是 `.agentao/skills/` 覆盖全局，`skills/` 覆盖前两层）。首次运行时，Agentao 会把内置技能（目前只有 `skill-creator`）复制到 `~/.agentao/skills/`，已有的副本不会被覆盖。所以：

- **嵌入你的产品时**：把技能放在**项目 repo 根的 `skills/` 下**，用户拉代码即有
- **用户自定义**：留出 `~/.agentao/skills/` 让用户自己写个人技能
- **临时/试验**：`.agentao/skills/` 适合本项目专属但不想污染 git

### 远程安装

CLI 可以从 GitHub 安装受管理的技能：

```bash
agentao skill install owner/repo[:path][@ref]
```

如果带 `:path`，Agentao 会把仓库里的这个子目录当作单个技能包安装。比如 Anthropic 官方 PDF 技能位于一个更大的仓库中：

```bash
agentao skill install anthropics/skills:skills/pdf
```

项目级安装写入 `<project-root>/.agentao/skills/`；全局安装写入 `~/.agentao/skills/`。仓库根目录的 `skills/` 仍然优先级最高，所以产品随 repo 分发的技能可以覆盖受管理安装。

### 多租户场景的隔离

ACP 或多实例 Python 嵌入时，每个 Agent 构造时传的 `working_directory` 决定它看到的项目级技能目录——两个租户互不干扰。

```python
# tenant-a 只能看到 /data/tenant-a 下的技能
agent_a = Agentao(working_directory=Path("/data/tenant-a"))
# tenant-b 只能看到 /data/tenant-b 下的技能
agent_b = Agentao(working_directory=Path("/data/tenant-b"))
```

但 **`~/.agentao/skills/` 是全局共享**的（进程级用户 HOME）。这个路径在导入 `agentao.skills.manager` 时就定下了，之后再改 `HOME` 不起作用。不想加载它，就自己传一个 manager：`SkillManager(skills_dir=...)` 只扫这一个目录，也不复制内置技能。

```python
from agentao.skills import SkillManager

agent = Agentao(
    working_directory=Path("/data/tenant-a"),
    skill_manager=SkillManager(skills_dir="/data/tenant-a/skills"),
)
```

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

两部分在请求里各自的位置：

- **技能目录**（每个已启用技能的 name + description）在系统提示里。系统提示跨轮次逐字节不变，provider 的提示缓存因此能命中。目录也列出已激活的技能，所以激活不会改动它；只有已启用集合变化（enable、disable、安装、reload）时它才变。只有 Agent 拥有 `activate_skill` 工具时才渲染目录。
- **本地技能和 plugin 技能的正文只在历史里存一份。** 在下一次模型请求之前，运行时追加一条 user `<system-reminder>`，它以 `Skill loaded: "<name>"` 开头，包含技能目录、`SKILL.md` 全文和资源列表。所有激活方式都这样处理：`activate_skill` 工具、`/skills activate`、`agentao run` 的 `spec.skills`、会话恢复。此后正文就是缓存历史的一部分。每次请求的易变尾部（末尾那条从不写入历史的 `<system-reminder>`）只给出该技能的 `Task:` 和一行指向提示。
  - **正文是激活时的快照。** 修改 `SKILL.md` 或资源目录后，要再次 `/skills activate <name>` 或运行 `/skills reload`（`/crystallize` 会运行它）才生效。新版本作为一条新的 `Skill loaded` 消息追加，并说明它取代之前的版本。每个技能只以最后一条这样的消息为准。
  - **取消激活会追加一条通知**：`Skill no longer active: "<name>"`，告诉模型不要再遵循之前的指令。旧正文留在历史里。
  - **压缩移除了正文时**（完整摘要、溢出时的最小历史裁剪），运行时会在下一次请求前重新追加一次。微压缩不会碰它。恢复的会话如果已经含有当前正文，不会再追加第二份。
- **MCP 技能的正文**放在易变尾部，每次请求都会不走缓存地重发一遍，用完就取消激活。一个例外：模型用 `activate_skill` 激活的 MCP 技能，只要最近一次激活的结果仍原样留在历史里，尾部就只给出该技能的 `Task:` 和一行指向那条结果的提示，不再发第二份。技能激活期间，微压缩不会截短这条结果；同一技能更早的结果，以及技能取消激活后的这条结果，照旧截短。完整压缩或会话恢复之后，尾部会重新发送整块内容。MCP 技能的内容从不移进 user 消息，因为来源识别和恢复时的扣留都按返回它的工具来认。

各段的顺序见 [`prompts/builder.py`](https://github.com/jin-bo/agentao/blob/main/agentao/prompts/builder.py)。

## 禁用一个技能

`activate_skill` 不是唯一的门禁。技能可以按项目**禁用**，这比「取消激活」更强：

```bash
/skills disable <name>     # 持久化，重启后仍然有效
/skills enable <name>      # 撤销
/skills                    # 列表，其中有独立的 Disabled 段
```

状态存在 `.agentao/skills_config.json` 的 `disabled_skills` 里（仅项目级作用域，见 [附录 B](/zh/appendix/b-config-keys)）。嵌入时要注意三点：

- **禁用不是取消激活。** `/skills deactivate` 只是把技能移出**本次会话**的 `active_skills`，技能仍可被发现，模型可以再次激活它。禁用则意味着技能不加载、不出现在目录里，并且**在激活时被拒绝** —— `activate_skill` 回答 `Unknown skill`，因为对一个试图激活它的调用方而言，被禁用的技能就是不存在。只有 `/skills activate` 会说明状态并给出补救办法。
- **子代理在启动那一刻复制这个禁用集合。** 在子代理运行期间做的禁用，只对**下一个**子代理生效，对正在跑的那个无效。
- **扫描从不修剪这份名单。** 一次扫不到该技能的 `/skills reload`（共享盘未挂载、目录运行中被改名）会原样保留名字、也不写盘，于是技能回来时仍是禁用状态。扫描分不清「已删除」和「此刻发现不到」，而禁用本身是激活门禁，猜错就等于静默把这个技能重新放行给模型。

最后一条的代价是：名字可能比它禁用的技能活得更久。`/skills` 会把这类名字单列在 **Disabled, not found by the last scan** 之下，而 `/skills enable <name>` 不论该技能当前是否存在都能清掉它。

### `skills_config.json` 怎样写入

这个文件可以放心手改，也可以放心在多个进程间共享：

- 每次 `disable` / `enable` 只改一个名字：在同一把锁（`skills_config.json.lock`）内读、改、写，再用临时文件替换原文件。
- 其他禁用名和文件里的其他键都会保留，包括本进程启动后另一个 Agentao 进程写进去的名字。
- 写入路径严格解析。JSON 写坏了、`disabled_skills` 不是数组、或数组里有非字符串项，都会拒绝写入并报出路径。若按宽松读法写回去，就会删掉文件里原有的名字。
- 启动时的读取是宽松的。文件损坏时记一条警告，按"没有禁用任何技能"加载，CLI 照常启动。

宿主侧的接口是 `skill_manager.disable_skill(name)` / `enable_skill(name)`，都返回 CLI 打印的那条消息字符串。代码见 [`skills/manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/skills/manager.py)。

## 按需参考目录

`references/*.md` 不会自动加载。技能正文里可以告诉 LLM："如果你遇到特殊情况，读 `references/edge-cases.md`"。

普通的相对路径在任何工作目录下都能用。激活时模型会拿到两样东西：

- **技能目录**，外加一句说明：`SKILL.md` 里的相对路径（比如 `scripts/foo.py`）相对技能目录解析，而不是相对 Agent 的工作目录。装好的技能通常在 `~/.agentao/skills/` 或 `.agentao/skills/` 下，不在 cwd 里。
- **按绝对路径列出的资源文件**：`references/*.md`、`assets/*.md` 和 `scripts/` 下的所有文件。隐藏文件和 `__pycache__` 会跳过；每个文件夹最多列 50 个，其余只给个数。

所以技能里不要硬编码绝对路径。这样做的好处：

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
└── references/
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

1. **Security first**: check `references/security-checklist.md` for the full list
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

# 手动激活（通常 LLM 自己激活）。任务描述是必填参数
print(agent.skill_manager.activate_skill("customer-ticket-handler", "回复工单 #4521"))

# 查看易变尾部会拿到的内容
print(agent.skill_manager.get_skills_context())
```

### `SkillManager` 接口

| 方法 | 返回 |
|------|------|
| `list_available_skills()` | 已启用技能的名字 |
| `list_all_skills()` | 发现到的全部技能名，包括已禁用的 |
| `get_skill_info(name)` | 元数据字典（`name`、`title`、`description`、`path`、`frontmatter` 等），或 `None` |
| `get_skill_content(name)` | `SKILL.md` 全文，或 `None` |
| `activate_skill(name, task_description)` | 激活消息。名字未知或已禁用时返回 `Error: Unknown skill …` |
| `deactivate_skill(name)` | 原本处于激活状态时返回 `True` |
| `get_skills_context(*, history=None)` | 易变尾部里的已激活技能块。传入 `history` 时，最近一次 `activate_skill` 结果仍原样在其中的已激活 MCP 技能只得到一行指向提示，不再附整块内容；最后一条 `Skill loaded` 消息就是当前版本的本地技能也一样。 |
| `reload_skills()` | 重扫三层目录，返回已启用技能数。插件和 MCP 技能会保留 |
| `disable_skill(name)` / `enable_skill(name)` | CLI 打印的那条消息（见[上文](#skills-config-json-怎样写入)） |

`available_skills` 和 `active_skills` 都是以名字为键的普通字典。

### 排查

| 现象 | 检查什么 |
|------|----------|
| `/skills` 里没有 | 文件名是否正好是 `SKILL.md`，且位于三层目录之一的直接子目录里？是不是 UTF-8？跑过 `/skills reload` 或重启了吗？ |
| 列出来了，模型却从不激活 | `description` 为空或没解析出来——去 `agentao.log` 找 frontmatter 警告。技能是否被禁用？Agent 有没有 `activate_skill`？ |
| 激活时报 `Unknown skill` | 名字区分大小写，取自 `name:` 而不是目录名。被禁用的技能也会回答 `Unknown skill`。 |
| 加载的不是你想要的那份 | 两层目录里有同名技能，后面的层胜出：`<wd>/skills/` 优先于 `<wd>/.agentao/skills/`，再优先于 `~/.agentao/skills/`。 |

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

## TL;DR

- 技能是一个**目录**：`SKILL.md`（YAML frontmatter + 正文）+ 可选的 `references/`、`assets/`、`scripts/`。激活时只按绝对路径列出、不内联，模型按需 `read_file`（见[按需参考目录](#按需参考目录)）。
- `description` 决定 LLM **何时**激活——按 LLM 第一视角写（"用户问到 X 时激活我"）。
- 三层搜索顺序：`~/.agentao/skills/`（全局）→ `<wd>/.agentao/skills/` → `<wd>/skills/`，**项目内的优先级最高**。
- 多个小而具体的技能 > 一个大杂烩。激活会消耗上下文 token，技能小才不肉疼。
- `/skills disable` 是**激活门禁**而不是显示过滤，而且扫描从不修剪它 —— 名字可能比技能活得更久，会出现在 *Disabled, not found by the last scan* 一节（见[禁用一个技能](#禁用一个技能)）。

→ 下一节：[5.3 MCP 服务器接入](./3-mcp)
