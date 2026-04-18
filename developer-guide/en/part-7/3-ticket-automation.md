# 7.3 Blueprint C · Customer-Support Ticket Automation

> **Run this example**: [`examples/ticket-automation/`](https://github.com/jin-bo/agentao/tree/main/examples/ticket-automation) — `uv run python -m src.triage "ticket text"`

**Scenario**: incoming support tickets pile up faster than your agents can triage. You want Agentao to read each new ticket, classify it, pull context from the CRM, propose a reply, and **only auto-send when confidence is high** — otherwise leave a draft for a human.

## Who & why

- **Product shape**: webhook-driven worker, no UI (HTTP trigger per ticket)
- **Users**: support managers who review the agent's drafts
- **Pain**: 80% of tickets are predictable (password reset, shipping status), but your best agents burn out answering them by hand

## Architecture

```
Zendesk / Intercom / HubSpot
          │ webhook (ticket.created)
          ▼
    FastAPI worker
          │
          ├─ Agentao instance (single, reused — stateless triage)
          │    ├─ Custom tools:
          │    │    - get_customer_profile(email)
          │    │    - search_kb(query)
          │    │    - get_order_status(order_id)
          │    │    - draft_reply(text, confidence)
          │    │    - send_reply(text)    ← requires_confirmation=True
          │    ├─ Skill: "support-triage" (tone, policy, escalation rules)
          │    └─ PermissionEngine: deny send_reply by default; allow only on confidence > 0.9
          │
          └─ Outbox: draft or send + audit trail
```

## Key code

### 1 · The workhorse tools

```python
# tools/support.py
from agentao.tools.base import Tool
import httpx

class GetCustomerProfile(Tool):
    def __init__(self, crm: httpx.Client): self._crm = crm
    @property
    def name(self): return "get_customer_profile"
    @property
    def description(self): return "Look up customer plan, LTV, open tickets"
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
    def description(self): return "Send the reply to the customer. USE ONLY when confidence > 0.9."
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
            return "ERROR: confidence too low — use draft_reply instead"
        r = self._crm.post(f"/tickets/{self._ticket_id}/reply",
                           json={"text": text}, timeout=10)
        return "Reply sent."
```

### 2 · Confidence-gated PermissionEngine

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

### 3 · Skill that shapes behavior

```markdown
<!-- skills/support-triage/SKILL.md -->
---
name: support-triage
description: Use for every inbound support ticket. Defines triage policy, tone, and escalation rules.
---

# Support Triage

## Steps
1. Fetch customer profile with `get_customer_profile`.
2. If order-related, call `get_order_status`.
3. Search knowledge base.
4. If the answer is unambiguous AND policy-compliant AND confidence > 0.9: call `send_reply`.
5. Otherwise call `draft_reply` with your best answer — a human will review.

## Tone
- Empathetic, concise, never condescending.
- Sign off with "— The [Product] team" (no personal names).

## Never
- Promise refunds, discounts, or SLA exceptions. Always escalate.
- Send PII back to the customer beyond what they already know.

## Escalation matrix
| Signal | Action |
|--------|--------|
| Customer mentions churn / cancel | draft_reply + tag "retention" |
| Legal / compliance keywords ("GDPR", "lawsuit") | draft_reply + tag "legal-review" |
| Enterprise plan (from profile) | draft_reply always — never auto-send |
```

### 4 · Webhook handler

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
        task_description=f"Triage ticket {ticket.id}",
    )
    return agent

@app.post("/webhook/ticket")
async def on_ticket(ticket: dict):
    agent = build_agent(ticket)
    try:
        reply = agent.chat(
            f"Ticket #{ticket['id']} from {ticket['customer_email']}:\n\n{ticket['body']}"
        )
        return {"status": "processed", "summary": reply}
    finally:
        agent.close()
```

## Output contract with the reviewer

Store every turn to your audit log — managers need to see why the agent drafted what it drafted:

```python
from agentao.transport import SdkTransport

def archive(ev):
    db.insert("ticket_agent_events", {
        "ticket_id": ticket.id, "type": ev.type.value,
        "data": ev.data, "ts": time.time(),
    })

agent = Agentao(transport=SdkTransport(on_event=archive), ...)
```

Reviewers query: "show me all cases where confidence was 0.85–0.92 last week" — that's your continuous-training signal.

## Pitfalls

| Day-2 bug | Root cause | Fix |
|-----------|------------|-----|
| Agent calls `send_reply` with `confidence: 0.95` when it shouldn't | LLM learned it could cheat the gate | Add post-hoc checks: skill rule + second LLM pass as judge, or classifier before send |
| Same ticket replied twice | Webhook retried, no idempotency | Key the session by `ticket_id`; refuse second invocation while first is in flight |
| Confidential fields leak into logs | Raw customer body in event archive | Apply a secrets scrubber filter ([6.5](/en/part-6/5-secrets-injection)) before archive |
| Escalation rule forgotten | Skill updated but cache not reloaded | SkillManager re-reads on new agent; ensure no global singleton caching old skill text |
| Quality drift over time | Model changed / KB changed, no canary | Run a daily eval set — 50 historical tickets, diff the new drafts ([6.8 canary](/en/part-6/8-deployment#what-you-can-canary)) |

## Runnable code

The full project lives in-repo at [`examples/ticket-automation/`](https://github.com/jin-bo/agentao/tree/main/examples/ticket-automation) — see the top-of-page "Run this example" link.

```bash
cd examples/ticket-automation
uv sync && uv run python -m src.triage "I forgot my password"
```

---

→ [7.4 Data Analyst Workbench](./4-data-workbench)
