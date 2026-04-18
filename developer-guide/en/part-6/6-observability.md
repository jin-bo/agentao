# 6.6 Observability & Audit

Agents are classic "long-tail bug" workloads — fine 90% of the time, then 10% of incomprehensible behavior. No observability = no diagnosis = no improvement.

## Four observation axes

```
┌─────────────────────────────────────────────┐
│ 1. Structured logs: what happened?           │
│    agentao.log + your app logs               │
├─────────────────────────────────────────────┤
│ 2. Metrics: how much, how fast, how expensive? │
│    calls / latency / tokens / failure rate   │
├─────────────────────────────────────────────┤
│ 3. Event stream: per-session replay           │
│    AgentEvent archive                        │
├─────────────────────────────────────────────┤
│ 4. Distributed tracing: end-to-end per request │
│    OpenTelemetry                             │
└─────────────────────────────────────────────┘
```

## Axis one: structured logs

### The built-in agentao.log

Defaults to `<working_directory>/agentao.log`. It's **very thorough**:

- Every LLM request/response (full content, tokens, model)
- Every tool call with args and result
- MCP server start/stop
- Plugin hook dispatch
- Context compression triggers

This is your most important **debugging tool**. In production:

1. Mount it on a persistent volume (survives restarts)
2. Rotate daily + keep 7–30 days
3. Scrub (see [6.5](./5-secrets-injection#four-log-scrubbing))
4. Isolate per tenant (natural with `working_directory`)

### Take over Agentao's logger

```python
import logging

agentao_logger = logging.getLogger("agentao")

# JSON handler to Loki / CloudWatch / ELK
import pythonjsonlogger.jsonlogger as jl
handler = logging.StreamHandler()
handler.setFormatter(jl.JsonFormatter())
agentao_logger.addHandler(handler)
agentao_logger.setLevel(logging.INFO)
```

### Essential fields

Inject business context via `on_event`:

```python
def on_event(ev):
    logger.info("agent_event", extra={
        "event_type": ev.type.value,
        "session_id": current_session_id(),
        "tenant_id": current_tenant_id(),
        "user_id": current_user_id(),
        **ev.data,
    })
```

**session_id / tenant_id / user_id** are the most-used filter fields when debugging.

## Axis two: metrics

### Must-have metrics

| Metric | Type | Meaning |
|--------|------|---------|
| `agent.turn.count` | counter | Turns per chat() |
| `agent.turn.duration_ms` | histogram | Per-turn duration |
| `agent.tool.calls` | counter, by tool | Tool invocations |
| `agent.tool.failures` | counter, by tool | Tool failures |
| `agent.tool.duration_ms` | histogram, by tool | Tool latency |
| `agent.llm.tokens.prompt` | counter | Prompt tokens |
| `agent.llm.tokens.completion` | counter | Completion tokens |
| `agent.llm.tokens.cached` | counter | Prompt-cache hits |
| `agent.llm.errors` | counter, by error_type | LLM errors |
| `agent.confirm.requests` | counter, by outcome | Confirm request / allow / reject / timeout |
| `agent.max_iterations.hits` | counter | Bailouts triggered |

### Prometheus template

```python
from prometheus_client import Counter, Histogram

turn_dur = Histogram("agent_turn_duration_ms", "Turn duration",
                     buckets=[100, 500, 1000, 3000, 10_000, 30_000])
tool_calls = Counter("agent_tool_calls", "Tool invocations", ["tool", "status"])

def on_event(ev):
    if ev.type == EventType.TOOL_COMPLETE:
        tool_calls.labels(tool=ev.data["tool"], status=ev.data["status"]).inc()

start = time.time()
reply = agent.chat(msg)
turn_dur.observe((time.time() - start) * 1000)
```

### Alert thresholds

| Metric | Typical threshold |
|--------|-------------------|
| Tool failure rate > 10% | Tools broken or misconfigured |
| LLM 5xx rate > 2% | Vendor issue |
| max_iterations hits > 5% | Agent stuck pattern |
| Cache hit rate < 30% | System prompt is churning |
| Confirm timeout rate > 10% | UI issue or user drop-off |

## Axis three: event stream archive

Saving each session's `AgentEvent` stream enables:

- **Session replay** (reconstruct issue scene in UI)
- **Retroactive debugging** (see where the LLM made the wrong call)
- **Compliance audit** (user X had the agent do Z at time Y)

### Format

One event per line JSONL:

```json
{"ts": 1704067200.1, "session": "sess-123", "type": "turn_start", "data": {}}
{"ts": 1704067200.3, "session": "sess-123", "type": "tool_start", "data": {"tool": "get_customer_orders", "args": {"customer_id": "c-42"}, "call_id": "..."}}
{"ts": 1704067200.8, "session": "sess-123", "type": "tool_complete", "data": {"tool": "get_customer_orders", "status": "ok", "duration_ms": 500, "call_id": "..."}}
```

### Implementation

```python
import json, time
from pathlib import Path

class EventArchiver:
    def __init__(self, path: Path, session_id: str, tenant_id: str):
        self.f = path.open("a", encoding="utf-8")
        self.session_id = session_id
        self.tenant_id = tenant_id

    def __call__(self, ev):
        self.f.write(json.dumps({
            "ts": time.time(),
            "session": self.session_id,
            "tenant": self.tenant_id,
            "type": ev.type.value,
            "data": ev.data,
        }) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()

archiver = EventArchiver(
    path=Path(f"/data/tenant-{tenant.id}/events.jsonl"),
    session_id=session_id, tenant_id=tenant.id,
)
transport = SdkTransport(on_event=archiver)
```

## Axis four: distributed tracing

When the agent is embedded in your web service, one user request can span:

```
Browser → your API → Agent.chat() → LLM API → Agent → custom tool → database
```

**OpenTelemetry** stitches these into one trace:

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

@app.post("/chat")
async def chat(req: ChatRequest):
    with tracer.start_as_current_span("user_chat") as span:
        span.set_attribute("user.id", req.user_id)
        span.set_attribute("session.id", req.session_id)
        with tracer.start_as_current_span("agent_chat"):
            reply = await asyncio.to_thread(agent.chat, req.message)
        return {"reply": reply}
```

Deeper: wrap `LLMClient` / `Tool.execute` and instrument each call.

### Recommended LLM span attributes

- `gen_ai.system` = "openai"
- `gen_ai.request.model` = model name
- `gen_ai.usage.prompt_tokens` / `completion_tokens`
- `gen_ai.response.finish_reason`

Follow OpenTelemetry GenAI semantic conventions.

## Audit and compliance

### Must-keep audit events

| Scenario | Trigger | Retention |
|----------|---------|-----------|
| User starts a session | Agent construction | 90–365 days |
| User approves dangerous tool | confirm_tool = True | 180–365 days |
| Permission rule denied | decide = DENY | 90 days |
| Agent modified user data | Business tool execution | Business-defined (usually 1–7 years) |
| User requests "forget me" | memory.clear_all | Indefinite (compliance evidence) |

### Scrubbing and retention

Audit logs **should not** be scrubbed (you lose evidence), but **must be** encrypted-at-rest with strict access control.

Under compliance regimes: append-only storage (WORM) required.

## Minimum viable observability

On a budget:

1. `agentao.log` → per-tenant files, daily rotate, 14d retention
2. `prometheus_client` → the 5 key metrics, Grafana dashboard
3. Event JSONL → per session file, 7d retention
4. No OpenTelemetry

Enough for 99% of small/mid SaaS. Add APM when you scale.

→ [6.7 Resource Governance & Concurrency](./7-resource-concurrency)
