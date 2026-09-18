# 5.6 系统提示定制

> **本节你会学到**
> - 指令由 16 个块拼成，每个块落在**哪一条消息**上
> - 你真正能定制的 3 个块：AGENTAO.md、技能、自定义 Tool description
> - 其余由运行时注入、不该被覆盖

Agent 的指令是**每轮 chat() 动态重建**的，不是一个静态字符串，而且分**两条消息**送达：一条稳定的 system 消息，以及一条按请求拼装、从不落盘的易变 `user` 尾消息。本节讲这些块的来源、哪些可以被你定制、哪些不建议碰。

## 系统提示的构成

指令由**两条**消息承载，不是一条。**system 消息**是稳定的那一半；易变的那一半挂在一条尾部 `user` 消息上，每个请求重建一次，从不写进对话记录。

```
┌──────────────────────────────────────────────┐
│ system 消息 —— 整个会话内保持稳定             │
│                                              │
│  1. 项目说明 (AGENTAO.md)                     │  ← 你可以写
│  2. 身份 / 基础能力描述                        │  ← 固定
│  3. 可靠性原则                                 │  ← 固定
│  4. 任务分级                                   │  ← 固定
│  5. 执行协议                                   │  ← 固定
│  6. 完成标准                                   │  ← 固定
│  7. 不可信输入边界                             │  ← 固定
│  8. 操作规范                                   │  ← 固定
│  9. 推理指令（如启用 thinking）                 │  ← 条件
│ 10. 可用子 Agent 列表                          │  ← 固定
│ 11. 可用技能清单                               │  ← 随启用集合变
│ 12. <memory-stable> 稳定记忆                   │  ← 慢变
│     === 可缓存前缀到此结束 ===                 │
└──────────────────────────────────────────────┘
┌──────────────────────────────────────────────┐
│ ……对话历史……                                  │
│ 本轮的 user 消息（会落盘）：                    │
│   <system-reminder>                          │  ← 每轮变
│   Current Date/Time: 2026-04-16 15:30        │
│   </system-reminder>                         │
│   <你传给 chat() 的文本>                       │
└──────────────────────────────────────────────┘
┌──────────────────────────────────────────────┐
│ 易变尾消息 —— 一条 user 消息，仅属于本次请求    │
│   <system-reminder>                          │
│ 13. 激活技能全文                               │  ← 激活即变
│ 14. 当前 Todo 列表                             │  ← 动态，每请求
│ 15. <memory-context> 动态召回                  │  ← 每轮变
│ 16. Plan 模式提示（条件）                       │  ← 条件
│   </system-reminder>                         │
└──────────────────────────────────────────────┘
```

**尾消息只属于这一次请求。** 它为一次外发请求拼装，从不追加进 `agent.messages`，因此不会进入会话文件、replay 记录或压缩。新增易变内容时**不要**照抄日期提醒那套写法：那一条**是**落盘的，而一条落盘的尾消息会让每轮往对话里堆一份 todos 快照。

块 13–16 是在 0.4.26 从 system 消息里搬出来的（`docs/design/llm-api-adapters.zh.md` §2.3 的阶段 0a）。它们正是"稳定"前缀其实并不稳定的原因：`<memory-context>` 按查询生成，每轮都变，而 `messages[0]` 是 provider 缓存前缀的头部，于是每轮都会废掉覆盖**整段历史**的缓存。一个值得知道的副作用：因为尾消息是按**请求**而不是按轮重建的，模型在某个工具迭代里写的 `todo_write` 下一个迭代就能看到。

**技能清单（块 11）在 0.4.26 跟着一起搬了出去，0.4.27 又搬了回来。** 它列出每一个已启用的技能，**包括**已激活的，所以激活一个技能不会改动 system 消息的任何字节；激活改变的是尾消息里的块 13，模型也正是从那里得知清单中哪些已经激活。清单只在启用集合变化时才变 —— 启用、禁用、安装、reload —— 而这些事件本来就会改写 `activate_skill` 工具的 `skill_name` 枚举，前缀在这些时刻原本就要重建。它排在 `<memory-stable>` 之前，因为它变得更少：一次 `save_memory` 只从块 12 起重建，清单仍然命中缓存。

## 你能定制的 3 个注入点

### 1. `AGENTAO.md` — 项目级指令

放在 `working_directory` 根下，构造 Agent 时**自动读取**。

```markdown
# 项目说明

## 技术栈
- Python 3.12 + FastAPI + Pydantic v2
- 前端：Next.js 14 App Router + shadcn/ui

## 代码规范
- 用 Ruff + black，行长 100
- async 函数不用 threading；计算密集用 `asyncio.to_thread`
- 新 endpoint 必须加 OpenAPI docstring

## 项目特殊约束
- 不要跨 tenant 查询（每个 endpoint 必须有 tenant_id 守卫）
- 数据库 schema 改动必须走 Alembic migration
- 不要直接用 `datetime.now()`，用 `app.utils.time.now()`（UTC + 租户时区感知）
```

**最佳实践**：

- 内容应是**硬约束**和**项目事实**（不是操作手册）
- 长度控制在 500-1500 字——太长挤掉其他空间
- 进 git 让团队共享
- 用 `##` 分段便于 LLM 吸收

### 2. 技能 — 按需加载的长文档

需要写 > 1500 字的规范？拆成技能（[5.2 节](./2-skills)）。用户/LLM 触发激活时才注入全文。

### 3. 记忆 `<memory-stable>` — 用户级持久化

适合"跨项目、用户本人稳定不变"的事实：

```python
# LLM 在对话中自动写
save_memory("user-profile", "Senior Python dev, prefers tabs, UTC+8 Shanghai")
```

这会在**所有**后续会话里都注入稳定块。

## 你不能（也不建议）定制的

| 块 | 原因 |
|----|------|
| Agent 基础能力 | 决定 Agent 怎么用工具、怎么思考 |
| 可靠性 / 操作规范 | Agentao 核心质量保证 |
| 可用子 Agent / 技能清单 | 来自注册状态，不是静态文本 |

如果你想彻底改造 Agent 行为（比如去掉某些能力），目前没有公开 API。**推荐做法**：通过 `AGENTAO.md` 和技能**覆盖/增强**，而不是试图替换。

## 验证系统提示

```python
# 构造后立即看
agent = Agentao(working_directory=Path.cwd())
print(agent._build_system_prompt())   # 稳定的 system 消息
print(agent._build_volatile_tail())   # 仅属于请求的尾消息（为空时返回 ""）
```

⚠️ 两者都是私有 API，不保证稳定。仅用于调试。`_build_system_prompt()` **只**返回稳定的那一半 —— 要找 todos、已激活技能的正文或动态召回，它们在尾消息里。

生产里可以打印**字符长度**做监控：

```python
sp = agent._build_system_prompt()
logger.info("system_prompt_chars", extra={"len": len(sp)})
```

系统提示过大会：
- 占压有效 context
- 提高每轮成本
- 降低 cache 命中率（如果 cache prefix 之后有太多动态内容）

## Prompt Cache 的实战技巧

system 消息在一个会话的各轮之间逐字节相同，所以厂商的 prompt cache 能复用它 —— 更要紧的是，能复用它后面的历史。

### 什么进了稳定前缀

整条 system 消息（块 1–12），以及对话历史本身。provider 能复用的前缀，止于自上次请求以来第一条发生变化的消息之前。

### 什么破坏 cache

- 两轮之间改 `AGENTAO.md` —— 它是块 1，一改全废。
- 一次落进 `<memory-stable>`（块 12）的 `save_memory`。
- 启用、禁用、安装或 reload 一个技能（块 11，连同工具块里 `activate_skill` 的枚举）。**激活**一个技能不算。
- 切换模型或端点（agentao 自己的 token 锚点也会在那里失效）。
- **不包括** todos、已激活技能的正文、召回和 plan 提示：0.4.26 起它们在仅属于请求的尾消息里，位于历史**之后**，改动它们只代价一条未缓存的尾消息。

代价是：尾消息每个**请求**都要未缓存地完整重发 —— 是每次工具迭代，不是每轮。没有激活技能时它是空的，或只有几十 tokens。**大头是已激活技能的正文**：在本仓（盘上 14 个技能）实测，system 消息约 5.2k tokens、尾消息为空；激活一个技能（`doc-coauthoring`）后，只要它保持激活，每个请求的尾消息约 4.1k tokens。用完的技能请取消激活，并在你自己的部署里实测 —— 这些是本地 token 估算，不是账单数字。

### 显式断点（opt-in）

如果你的端点在普通 Chat Completions 线路上认 Anthropic 风格的 `cache_control`，设 `LLM_PROMPT_CACHE=anthropic`（可选 `LLM_PROMPT_CACHE_TTL=1h`）。agentao 随后会在每个 agent 回合的请求上最多打三个断点 —— system 消息、最后一个工具定义、稳定历史的末尾 —— 并把第四个槽留给端点自己的自动缓存。

默认关闭，而且刻意不从 base URL 或模型名推断：agentao 核实的是 OpenAI SDK 会原样转发这个键，不是你的网关会认它。先验证你的端点，再打开。见 `docs/reference/configuration.zh.md` §2。

### 调试 cache 命中率

如果用 OpenAI：响应里有 `usage.prompt_tokens_details.cached_tokens`。理想情况是从第二轮起，除了最新的几条消息和尾消息，其余都命中缓存。

## 为不同业务配置不同 `AGENTAO.md`

多租户 / 多业务线时，每个 `working_directory` 可以有**不同**的 `AGENTAO.md`：

```
/data/tenants/acme-corp/
├── AGENTAO.md           ← acme 的规范
└── .agentao/

/data/tenants/globex/
├── AGENTAO.md           ← globex 的规范
└── .agentao/
```

这是**最干净**的租户级定制方式——不需要代码分支，只靠目录布局。

## 动态生成 AGENTAO.md

有些信息是每个会话动态的（例如用户当前的订阅等级、语言偏好、所在地区）。做法：构造 Agent 前把 `AGENTAO.md` **写到会话专属目录**：

```python
def prepare_workdir(tenant, user) -> Path:
    workdir = Path(f"/tmp/session-{user.id}")
    workdir.mkdir(exist_ok=True)
    (workdir / "AGENTAO.md").write_text(f"""
# User Context

- Tenant: {tenant.name} ({tenant.plan})
- User: {user.name}, role: {user.role}, locale: {user.locale}
- Today: {datetime.now().isoformat()}
- Current feature: {user.current_feature}

## Allowed actions
{format_allowed_actions(tenant.plan)}
""")
    return workdir

agent = Agentao(working_directory=prepare_workdir(tenant, user))
```

这让每个会话看到的系统提示就是**为它量身定制**的。

## 我要是真的想把整个系统提示换掉呢？

没有公开 API。但你可以继承 `Agentao` 并 override 私有方法：

```python
from agentao import Agentao

class MyAgentao(Agentao):
    def _build_system_prompt(self) -> str:
        parent = super()._build_system_prompt()
        # 在最前面加一段你的总纲
        return "# Your company's top-level charter\n\n...\n\n" + parent

agent = MyAgentao(working_directory=Path.cwd())
```

⚠️ 这依赖私有方法名，版本升级要重新测试。**尽量用 AGENTAO.md + 技能组合替代**。

## ⚠️ 常见陷阱

::: warning 上线前先确认这几条
- ❌ **AGENTAO.md 过长** —— 长篇前置说明稀释模型对真正用户消息的注意力
- ❌ **不同会话共享同一 AGENTAO.md** —— 一个租户的规则跑到了另一个租户身上
- ❌ **把敏感信息写进 AGENTAO.md** —— 会被发到每次 LLM 调用，且可能被记日志

下面每一条都附完整修法。
:::

### ❌ AGENTAO.md 过长

2000+ 字的 AGENTAO.md 会吃掉太多 context。把"操作指南类"内容拆成技能，AGENTAO.md 只保留"硬约束+关键事实"。

### ❌ 不同会话共享同一 AGENTAO.md

多租户场景如果所有 Agent 都指向同一个 `working_directory`，他们会共享 AGENTAO.md——但你可能有想按租户定制的需求。**按会话独立 working_directory** 是唯一干净解。

### ❌ 把敏感信息写进 AGENTAO.md

AGENTAO.md 是项目文件，可能进 git、被 LLM 记忆、出现在日志里。**不要**放 API key、真实凭据、客户 PII。

---

**第 5 部分到此完成。** 你现在有了让 Agent 理解你业务的完整工具链：工具、技能、MCP、权限、记忆、系统提示。下一部分讲怎么在**生产环境**下安全地部署这一切。

## TL;DR

- 指令**每轮重建**——不要假设它是个静态字符串可以缓存。
- 它分**两条消息**送达：稳定的 system 消息（块 1–12）与仅属于请求的易变尾消息（块 13–16，一条从不进入对话记录的 `user` 消息）。
- 你拥有其中 3 个块：**`AGENTAO.md`**（项目硬规则）、**技能正文**（激活后的知识）、**自定义 Tool 的 description**（什么时候/怎么调）。
- 其余（日期、工作目录、可用工具/技能目录、记忆召回、todos 等）是运行时注入的，不该被覆盖。
- `AGENTAO.md` 写得**短而绝对**（"永远不要做 X"、"永远用 Y 格式"）——长篇前置说明会稀释注意力。

→ [第 6 部分 · 安全与生产化部署](/zh/part-6/)
