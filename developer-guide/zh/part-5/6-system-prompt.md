# 5.6 系统提示定制

Agent 的系统提示是**每轮 chat() 动态重建**的，不是一个静态字符串。本节讲 11 个拼接块的来源、哪些可以被你定制、哪些不建议碰。

## 系统提示的构成

源码：`agentao/agent.py::_build_system_prompt()` (603-734 行)

```
┌────────────────────────────────────────────┐
│ 系统提示                                    │
│                                            │
│ ┌─────────────────────────────────────┐    │
│ │ 1. 项目说明 (AGENTAO.md)            │    │  ← 你可以写
│ ├─────────────────────────────────────┤    │
│ │ 2. Agent 基础能力描述                │    │  ← 固定
│ ├─────────────────────────────────────┤    │
│ │ 3. 可靠性原则                        │    │  ← 固定
│ ├─────────────────────────────────────┤    │
│ │ 4. 操作规范（工具效率、语气）         │    │  ← 固定
│ ├─────────────────────────────────────┤    │
│ │ 5. 推理指令（如启用 thinking）       │    │  ← 条件
│ ├─────────────────────────────────────┤    │
│ │ 6. 可用子 Agent 列表                 │    │  ← 固定
│ ├─────────────────────────────────────┤    │
│ │ === 稳定前缀结束（以上享受 cache） ===│    │
│ ├─────────────────────────────────────┤    │
│ │ 7. 可用技能清单                      │    │  ← 激活即变
│ ├─────────────────────────────────────┤    │
│ │ 8. 激活技能全文                      │    │  ← 激活即变
│ ├─────────────────────────────────────┤    │
│ │ 9. 当前 Todo 列表                    │    │  ← 动态
│ ├─────────────────────────────────────┤    │
│ │ 10. <memory-stable> 稳定记忆         │    │  ← 慢变
│ ├─────────────────────────────────────┤    │
│ │ 11. <memory-context> 动态召回        │    │  ← 每轮变
│ ├─────────────────────────────────────┤    │
│ │ 12. Plan 模式后缀（条件）            │    │  ← 条件
│ └─────────────────────────────────────┘    │
│                                            │
│ ┌─────────────────────────────────────┐    │
│ │ <system-reminder>                   │    │  ← 每轮动态
│ │ Current Date/Time: 2026-04-16 15:30 │    │
│ │ </system-reminder>                  │    │
│ └─────────────────────────────────────┘    │
└────────────────────────────────────────────┘
```

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
print(agent._build_system_prompt())
```

⚠️ `_build_system_prompt()` 是私有 API，不保证稳定。仅用于调试。

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

Agentao 把系统提示分成"稳定前缀 + 动态后缀"——前缀的内容在多轮 `chat()` 间不变，利用大多数 LLM 厂商的 prompt cache 可以**显著降本降延迟**。

### 什么进了稳定前缀

块 1-6（AGENTAO.md、基础能力、规范、推理、子 Agent）。

### 什么破坏 cache

- 每轮 `chat()` 前改 `AGENTAO.md` 内容
- 切换激活技能（块 8 一变，后面的 cache 失效没关系——但块 1-6 还在 cache）
- 添加 todos、memories（块 9-11）**不**破坏前缀 cache，因为它们在后面

### 调试 cache 命中率

如果用 OpenAI：响应里有 `usage.prompt_tokens_details.cached_tokens`——理想情况第二轮起大部分系统提示都 cached。

```python
# 在你的 on_event 里监控（ERROR 块也可带出）
def on_event(ev):
    # 或通过 LLM 日志看
    ...
```

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

## 常见陷阱

### ❌ AGENTAO.md 过长

2000+ 字的 AGENTAO.md 会吃掉太多 context。把"操作指南类"内容拆成技能，AGENTAO.md 只保留"硬约束+关键事实"。

### ❌ 不同会话共享同一 AGENTAO.md

多租户场景如果所有 Agent 都指向同一个 `working_directory`，他们会共享 AGENTAO.md——但你可能有想按租户定制的需求。**按会话独立 working_directory** 是唯一干净解。

### ❌ 把敏感信息写进 AGENTAO.md

AGENTAO.md 是项目文件，可能进 git、被 LLM 记忆、出现在日志里。**不要**放 API key、真实凭据、客户 PII。

---

**第 5 部分到此完成。** 你现在有了让 Agent 理解你业务的完整工具链：工具、技能、MCP、权限、记忆、系统提示。下一部分讲怎么在**生产环境**下安全地部署这一切。

→ [第 6 部分 · 安全与沙箱](/zh/part-6/)（撰写中）
