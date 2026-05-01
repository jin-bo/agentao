# 7.3 蓝图 C · 客服 / 工单自动化

::: tip ⚡ 端到端可跑
**产出** —— 异步处理器读工单 → 分类 → 拉 CRM 上下文 → 草拟回复，仅在置信度高时自动发，否则交人工。
**技术栈** —— Python · `prompt_once` 形式（无流式 UI）· 自定义 CRM 工具 · PermissionEngine 把"自动发"卡死。
**源代码** —— [`examples/ticket-automation/`](https://github.com/jin-bo/agentao/tree/main/examples/ticket-automation)
**运行** —— `uv run python -m src.triage "工单内容"`
:::

**场景**：工单进来的速度比客服分拣的速度快。你希望 Agentao 读新工单 → 分类 → 从 CRM 拉上下文 → 草拟回复 → **只在置信度够高时自动发**，否则留个草稿给人工审核。

## 谁 & 为什么

- **产品形态**：webhook 驱动的 worker，无 UI（每个工单一次 HTTP 触发）
- **用户**：审核 agent 草稿的客服主管
- **痛点**：80% 的工单模式可预测（改密码、物流查询），但最有经验的客服一直在手工回这些

## 架构

```
Zendesk / Intercom / HubSpot
          │ webhook (ticket.created)
          ▼
    FastAPI worker
          │
          ├─ Agentao 实例（单例复用——无状态分拣）
          │    ├─ 自定义工具:
          │    │    - get_customer_profile(email)
          │    │    - search_kb(query)
          │    │    - get_order_status(order_id)
          │    │    - draft_reply(text, confidence)
          │    │    - send_reply(text)    ← requires_confirmation=True
          │    ├─ 技能: "support-triage"（语气、政策、升级规则）
          │    └─ PermissionEngine: 默认拒绝 send_reply；只在 confidence > 0.9 时放行
          │
          └─ 发件箱: 草稿 或 发送 + 审计轨迹
```

## 关键代码

### 1 · 主力工具

```python
# tools/support.py
from agentao.tools.base import Tool
import httpx

class GetCustomerProfile(Tool):
    def __init__(self, crm: httpx.Client): self._crm = crm
    @property
    def name(self): return "get_customer_profile"
    @property
    def description(self): return "查询客户的套餐、LTV、未结工单"
    @property
    def parameters(self):
        return {"type": "object", "required": ["email"],
                "properties": {"email": {"type": "string"}}}
    @property
    def is_read_only(self): return True
    def execute(self, email: str) -> str:
        r = self._crm.get(f"/customers?email={email}", timeout=10)
        return r.text

class SendReply(Tool):
    def __init__(self, crm: httpx.Client, ticket_id: str):
        self._crm, self._ticket_id = crm, ticket_id
    @property
    def name(self): return "send_reply"
    @property
    def description(self): return "将回复发送给客户。仅当 confidence > 0.9 时调用。"
    @property
    def parameters(self):
        return {"type": "object", "required": ["text", "confidence"],
                "properties": {
                    "text": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1}}}
    @property
    def requires_confirmation(self): return True
    def execute(self, text: str, confidence: float) -> str:
        if confidence < 0.9:
            return "ERROR: 置信度太低，请改用 draft_reply"
        r = self._crm.post(f"/tickets/{self._ticket_id}/reply",
                           json={"text": text}, timeout=10)
        return "已发送。"
```

### 2 · 按置信度门控的 PermissionEngine

```python
# permissions.py
from agentao.permissions import PermissionEngine, PermissionDecision, PermissionMode

class ConfidenceGatedEngine(PermissionEngine):
    def decide(self, tool_name: str, tool_args: dict):
        if tool_name == "send_reply":
            conf = float(tool_args.get("confidence", 0))
            return (
                PermissionDecision.ALLOW
                if conf >= 0.9
                else PermissionDecision.DENY
            )
        return super().decide(tool_name, tool_args)
```

### 3 · 塑造行为的技能

```markdown
<!-- skills/support-triage/SKILL.md -->
---
name: support-triage
description: 用于每一条新进工单。定义分拣流程、语气与升级规则。
---

# 工单分拣

## 步骤
1. 用 `get_customer_profile` 拿客户画像
2. 如果涉及订单，调 `get_order_status`
3. 搜知识库
4. 如果答案明确 AND 符合政策 AND 置信度 > 0.9：调 `send_reply`
5. 否则用 `draft_reply` 留草稿，由人工审核

## 语气
- 共情、简洁、不居高临下
- 落款 "— [产品] 团队"（不用个人名）

## 永远不做
- 承诺退款、折扣、SLA 例外。一律升级。
- 往客户那边回复他们本就不知道的 PII。

## 升级矩阵
| 信号 | 动作 |
|------|------|
| 客户提到流失 / 取消 | draft_reply + 标签 "retention" |
| 法律 / 合规关键词（"GDPR"、"诉讼"） | draft_reply + 标签 "legal-review" |
| 企业级套餐（从 profile 判定） | 一律 draft_reply，永不自动发 |
```

### 4 · Webhook 处理器

```python
# worker.py
from fastapi import FastAPI
from agentao import Agentao
from pathlib import Path
from .tools.support import GetCustomerProfile, SearchKb, GetOrderStatus, DraftReply, SendReply
from .permissions import ConfidenceGatedEngine

app = FastAPI()

def build_agent(ticket):
    workdir = Path(f"/tmp/ticket-{ticket.id}")
    workdir.mkdir(exist_ok=True)
    engine = ConfidenceGatedEngine(project_root=workdir)
    engine.set_mode(PermissionMode.READ_ONLY)
    agent = Agentao(
        working_directory=workdir,
        permission_engine=engine,
    )
    agent.tools.register(GetCustomerProfile(crm))
    agent.tools.register(SearchKb(kb))
    agent.tools.register(GetOrderStatus(crm))
    agent.tools.register(DraftReply(crm, ticket.id))
    agent.tools.register(SendReply(crm, ticket.id))
    agent.skill_manager.activate_skill(
        "support-triage",
        task_description=f"分拣工单 {ticket.id}",
    )
    return agent

@app.post("/webhook/ticket")
async def on_ticket(ticket: dict):
    agent = build_agent(ticket)
    try:
        reply = agent.chat(
            f"工单 #{ticket['id']}（来自 {ticket['customer_email']}）：\n\n{ticket['body']}"
        )
        return {"status": "processed", "summary": reply}
    finally:
        agent.close()
```

## 跟审核员的输出契约

把每一轮都写进审计日志——主管需要看 agent 为什么这么草拟：

```python
from agentao.transport import SdkTransport

def archive(ev):
    db.insert("ticket_agent_events", {
        "ticket_id": ticket.id, "type": ev.type.value,
        "data": ev.data, "ts": time.time(),
    })

agent = Agentao(transport=SdkTransport(on_event=archive), ...)
```

审核员会查："上周所有 confidence 在 0.85–0.92 之间的案子"——这就是你持续训练的信号。

## ⚠️ 陷阱

::: warning 工单自动化真实部署中的 Day-2 bug
下面每一行都是一次真实的生产事故。**上线前先扫一遍**——现在改便宜，事后查代价大。
:::

| 上线第二天的 bug | 根因 | 修法 |
|------------------|------|------|
| 不该发却发了（agent 填了 `confidence: 0.95`） | LLM 学会"绕门禁" | 加事后校验：技能规则 + 第二遍 LLM 当法官，或发送前跑分类器 |
| 同一工单回复两次 | webhook 重试，没做幂等 | 用 `ticket_id` 做 session key；第一次未完成时拒绝第二次 |
| 机密字段泄到日志 | 原始工单正文进了事件存档 | 存档前过 [6.5](/zh/part-6/5-secrets-injection) 的 secrets scrubber |
| 升级规则被忘记 | 改了技能但有缓存 | SkillManager 在新 agent 里会重读；避免全局单例 |
| 长期质量漂移 | 模型 / KB 变了但没灰度 | 每天跑评估集——50 条历史工单，diff 新草稿（[6.8 灰度](/zh/part-6/8-deployment#可灰度的维度)） |

## 可运行代码

完整项目就在主仓 [`examples/ticket-automation/`](https://github.com/jin-bo/agentao/tree/main/examples/ticket-automation)——参考本页顶部的 "运行此例" 链接。

```bash
cd examples/ticket-automation
uv sync && uv run python -m src.triage "我忘了密码"
```

---

→ [7.4 数据分析工作台](./4-data-workbench)
