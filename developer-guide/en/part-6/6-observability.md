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

## Axis three: session replay

Agentao can record each session's runtime timeline as append-only JSONL under `.agentao/replays/`. Enable it per project:

```bash
/replay on
```

This writes `.agentao/settings.json`:

```json
{
  "replay": {
    "enabled": true,
    "max_instances": 20
  }
}
```

Recording starts on the next session. Existing replay files remain readable after `/replay off`.

Replay enables:

- **Session replay** (reconstruct issue scene in UI)
- **Retroactive debugging** (see where the LLM made the wrong call)
- **Compliance audit** (user X had the agent do Z at time Y)

### Commands

```bash
/replay list            # list replay instances (also the default of bare /replay)
/replay on | /replay off  # toggle recording (persists to .agentao/settings.json)
/replay show <id>       # grouped render
/replay show <id> --raw
/replay show <id> --turn <turn_id>
/replay show <id> --kind tool_
/replay show <id> --errors
/replay tail <id> 50
/replay prune
```

Replay files are separate from saved sessions: `save_session` / `load_session` restore conversation state, while replay records what the runtime did.

### Capture depth

Default replay capture includes turn boundaries, user messages, assistant chunks, tool lifecycle, permission decisions, sub-agent lifecycle, errors, state changes, and compact LLM deltas.

Deep capture flags live under `replay.capture_flags` in `.agentao/settings.json`:

| Flag | Default | Risk |
|------|---------|------|
| `capture_llm_delta` | `true` | Normal replay history delta |
| `capture_full_llm_io` | `false` | Full provider payloads; sensitive |
| `capture_tool_result_full` | `false` | Full tool output; may be large or sensitive |
| `capture_plugin_hook_output_full` | `false` | Full plugin hook output |

### Custom archive hook

Use the built-in replay recorder first. Add a custom `on_event` archiver only when you need to send selected events into your own audit pipeline:

```python
def audit_event(ev):
    if ev.type in {EventType.TOOL_COMPLETE, EventType.ERROR}:
        audit_log.info("agent_event", extra={
            "type": ev.type.value,
            "session_id": session_id,
            "tenant_id": tenant.id,
            **ev.data,
        })

transport = SdkTransport(on_event=audit_event)
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
3. Built-in replay JSONL → `.agentao/replays/`, tune `replay.max_instances`
4. No OpenTelemetry

Enough for 99% of small/mid SaaS. Add APM when you scale.

→ [6.7 Resource Governance & Concurrency](./7-resource-concurrency)
