# 6.6 Observability & Audit

> **What you'll learn**
> - The four observation axes: structured logs, metrics, traces, session replay
> - The minimum metrics every production deployment should emit
> - When session replay is the only thing that lets you diagnose a "why did the agent do that?" incident

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

- Every LLM request/response (full content — untruncated, but credential-shaped strings are redacted to `[REDACTED:<kind>]` on write — plus tokens, model)
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

By default `LLMClient.__init__` mutates `logging.getLogger("agentao")` —
sets level to `DEBUG`, attaches a `RotatingFileHandler` for
`<wd>/agentao.log`, and evicts its own marker-tagged handlers on
re-construction. Hosts that want to own their logging stack should
**inject a logger explicitly** so that mutation never runs:

```python
import logging
from agentao import Agentao

# Your own logger — JSON handler shipped to Loki / CloudWatch / ELK
import pythonjsonlogger.jsonlogger as jl
my_logger = logging.getLogger("myapp.agentao")
handler = logging.StreamHandler()
handler.setFormatter(jl.JsonFormatter())
my_logger.addHandler(handler)
my_logger.setLevel(logging.INFO)

agent = Agentao(
    api_key=..., base_url=..., model=...,
    working_directory=workdir,
    logger=my_logger,            # ← skips package-root mutation
)
```

When you pass `logger=`, `LLMClient` short-circuits before building any
file handler — so even the default `<wd>/agentao.log` is **not** created.
For a fully silent agent, use a `NullHandler`:

```python
quiet = logging.getLogger("myapp.agentao")
quiet.addHandler(logging.NullHandler())
quiet.propagate = False
agent = Agentao(..., logger=quiet)
```

::: warning Gotcha
Adding a handler to `getLogger("agentao")` **without** also passing
`logger=` works, but the package-root level still gets forced to `DEBUG`
and the rolling `agentao.log` file still gets written alongside your
handler. To fully suppress the file, either inject your own logger
(above) or pass `log_file=None` when building `LLMClient` yourself.
:::

The two knobs, both on `LLMClient.__init__` ([`llm/client.py`](https://github.com/jin-bo/agentao/blob/main/agentao/llm/client.py)):

| You want | Pass |
|----------|------|
| No `agentao.log` file | `log_file=None` on an `LLMClient` you build |
| No change to `logging.getLogger("agentao")`: no level, no handler, no eviction | `logger=` on `Agentao` or `LLMClient` |
| Both | `logger=` alone is enough, because it skips the file handler too |

`log_file=None` alone still sets the `"agentao"` logger to `DEBUG`, so every `agentao.*` module logs at DEBUG into whatever handlers sit upstream. A host that builds its own client can set both:

```python
from agentao.llm import LLMClient

llm = LLMClient(
    api_key=..., base_url=..., model=...,
    log_file=None,     # no agentao.log
    logger=quiet,      # leave the "agentao" logger alone
)
agent = Agentao(llm_client=llm, working_directory=workdir)
```

The other package loggers (`agentao.mcp`, `agentao.tools.web`, `agentao.acp_client`, …) never attach handlers of their own. With `logger=`, they follow the propagation and handlers your host already configured.

### Several agents in one process

The file handler lives on the shared `"agentao"` logger, and each new `LLMClient` replaces the previous one's. So with several agents in one process, records go to the `agentao.log` of the **most recently built agent that is still open**. Closing that agent hands the logger back to the next most recent open agent, so the others keep logging. A client dropped without `close()` is not handed the log back.

`agent.close()` (or `with` / `async with` / `aclose()`) detaches and closes the handler its own client attached, so a host can delete the working directory afterwards, Windows included. A client you built and passed as `llm_client=` is yours: call `llm.close()`.

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

Recording is off by default. A toggle takes effect on the next session and leaves the file being written alone. Existing replay files remain readable after `/replay off`. A broken `settings.json` falls back to the defaults and never blocks startup.

`replay.max_instances` caps the number of files under `.agentao/replays/` (default 20). It does not touch `.agentao/sessions/`. The oldest files are pruned when an instance starts and when it ends. A value that is not a count (`true`, `"abc"`, `0`) is ignored and 20 is used, and `agentao doctor` reports an error. A number written as a string (`"50"`) or with a fraction (`2.9` becomes 2) still applies, and doctor warns. The rules are in [`check_max_instances`](https://github.com/jin-bo/agentao/blob/main/agentao/replay/config.py).

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
/replay show <id> --kind tool_    # kind prefix
/replay show <id> --errors
/replay tail <id> 50
/replay prune
/replay delete <id> | /replay delete all
```

Every command reads up to the current end of the file. There is no follow or live-tail mode. For live observation, use normal output and `agentao.log`. Command details: [CLI · Replay & Output](/en/cli/9-replay-output).

Replay files are separate from saved sessions: `save_session` / `load_session` restore conversation state, while replay records what the runtime did. ACP `session/load` keeps the logical `session_id` but starts a new replay file. It never appends to the old one.

### Replay file layout

- **Path:** `.agentao/replays/<session_id>.<instance_id>.jsonl`. Characters other than letters, digits, `_` and `-` in the session id become `_`.
- **One event per line.** Each event has `event_id`, `session_id`, `instance_id`, `seq`, `ts`, `kind`, `turn_id`, `parent_turn_id` and `payload`.
- **First line:** a `replay_header` with `schema_version` (currently `1.4`), `session_id`, `instance_id`, `created_at`, and `capture_flags` when any are set.
- **Last line:** a `replay_footer`, written on a clean close. It has `event_count` and the per-kind `redaction_hits`, `dropped_field_hits` and `truncated_field_hits`.
- **A new instance per session birth.** `/clear`, `/new`, `/sessions resume`, ACP `session/new` and ACP `session/load` each start a fresh file. A file never spans those boundaries.

The JSON Schemas are in [`schemas/`](https://github.com/jin-bo/agentao/tree/main/schemas), one file per schema version. Version policy: [`docs/reference/replay-schema-policy.md`](https://github.com/jin-bo/agentao/blob/main/docs/reference/replay-schema-policy.md).

### What a replay records

One turn is one `Agentao.chat()` call. The kinds, grouped (full list: [`agentao/replay/events.py`](https://github.com/jin-bo/agentao/blob/main/agentao/replay/events.py)):

| Group | Kinds | Notes |
|-------|-------|-------|
| File and session | `replay_header`, `replay_footer`, `session_started`, `session_ended` | `session_started` has `cwd` and `model` |
| Turn | `turn_started`, `user_message`, `turn_completed` | `turn_completed` has `final_text`, `status` (`ok` / `cancelled` / `error`) and an optional `incomplete_reason` |
| Model output | `assistant_text_chunk`, `assistant_thought_chunk` | Streamed text and reasoning |
| LLM calls | `llm_call_started`, `llm_call_completed`, `llm_call_delta`, `llm_call_io` | `llm_call_completed` has token counts and latency. `llm_call_delta` needs `capture_llm_delta`, `llm_call_io` needs `capture_full_llm_io` |
| Tools | `tool_confirmation_requested`, `tool_confirmation_resolved`, `tool_started`, `tool_output_chunk`, `tool_completed`, `tool_result` | `tool_started.tool_source` is `mcp` for an `mcp_*` tool, else `builtin` |
| Host projections | `tool_lifecycle`, `subagent_lifecycle`, `permission_decision` | Same shapes as the [`agentao.host`](/en/part-4/7-host-contract) models |
| Sub-agents | `subagent_started`, `subagent_completed` | The child's events get their own `turn_id` and point back with `parent_turn_id` |
| Interaction | `ask_user_requested`, `ask_user_answered`, `background_notification_injected` | |
| State changes | `model_changed`, `permission_mode_changed`, `readonly_mode_changed`, `skill_activated`, `skill_deactivated`, `memory_write`, `memory_delete`, `memory_cleared`, `session_summary_written`, `plugin_hook_fired` | |
| Context | `context_compressed`, `compaction_settled`, `images_removed` | Read `compaction_settled` for every attempt, `context_compressed` for the successes |
| Errors | `error` | |

Three kinds are declared but not emitted: `session_saved`, `session_loaded` and `session_forked`. `session_saved` is reserved for a future explicit save entry point. The auto-save on `/clear`, `/new` or exit does not emit it.

### Privacy and truncation

Every payload goes through a sanitizer before it is written ([`agentao/replay/sanitize.py`](https://github.com/jin-bo/agentao/blob/main/agentao/replay/sanitize.py)):

- **JSON coercion.** Values become JSON-native types. A field that cannot be converted is dropped on its own. The event then carries `redacted: "filter_error"` and the field names in `redacted_fields`.
- **Secret scan.** Every string is scanned for credential-shaped patterns. A match becomes `[REDACTED:<kind>]`. Each event counts its hits in `redaction_hits`, and the footer sums them. This is pattern matching, not full secret scanning. A credential that does not look like one passes through. The scanner is [`agentao/security/secret_scan.py`](https://github.com/jin-bo/agentao/blob/main/agentao/security/secret_scan.py), shared with `agentao.log` and `.agentao/tool-outputs/`.
- **Head + tail truncation.** Long fields keep a head and a tail, with `truncated`, `original_chars` and `omitted_chars`. The caps: `tool_output_chunk` 4,000 chars, `tool_result` 8,000 (20% head, because errors land at the end), `memory_write` 2,000, `ask_user_answered` 500, `plugin_hook_fired` output 500. `assistant_text_chunk` is not truncated.

The deep-capture flags below lift some of these caps. The secret scan still runs.

### Capture depth

Default replay capture includes turn boundaries, user messages, assistant chunks, tool lifecycle, permission decisions, sub-agent lifecycle, errors, state changes, and compact LLM deltas.

Deep capture flags live under `replay.capture_flags` in `.agentao/settings.json`:

| Flag | Default | Risk |
|------|---------|------|
| `capture_llm_delta` | `true` | Normal replay history delta |
| `capture_full_llm_io` | `false` | Full provider payloads; sensitive |
| `capture_tool_result_full` | `false` | Full tool output; may be large or sensitive |
| `capture_plugin_hook_output_full` | `false` | Full plugin hook output |

### Failure model and readers

- Replay never breaks a turn. A sanitizer or write failure is logged as a warning and swallowed.
- A crash can leave a partial last line. Readers skip it and still return the earlier events.
- To read replays from code, use `agentao.replay.ReplayReader` (and `list_replays`). Any tool you build on replays should read through it, not rebuild the timeline from `agent.messages`. There is no ACP method that serves replays.

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

### Inner spans: use the event stream, don't monkey-patch

There is no need to wrap `LLMClient` or `Tool.execute` — the runtime already emits its lifecycle as events. Two entry points, different trade-offs:

| Entry point | What you get | Stability |
|---|---|---|
| `Agentao.events()` / `add_host_event_observer()` | Typed `HostEvent`: tool / sub-agent / permission decisions, carrying `session_id`, `turn_id`, and RFC3339 start/end timestamps | The `agentao.host` public contract |
| `Agentao(transport=SdkTransport(on_event=...))` / `transport.subscribe()` | Every `AgentEvent`, **including LLM calls** | **Not** under the host stability boundary; fields may change between versions |

Use the first for tool spans — `ToolLifecycleEvent`'s fields are already span-shaped. LLM spans currently need the second: the `HostEvent` union has no LLM event (`agentao/host/models.py:158`).

```python
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from agentao.transport import EventType, SdkTransport

tracer = trace.get_tracer(__name__)
open_spans = {}

def on_event(ev):
    if ev.type == EventType.LLM_CALL_STARTED:
        span = tracer.start_span("chat")
        span.set_attribute("gen_ai.system", "openai")
        span.set_attribute("gen_ai.request.model", ev.data["model"])
        open_spans[ev.data["attempt"]] = span
    elif ev.type == EventType.LLM_CALL_COMPLETED:
        span = open_spans.pop(ev.data["attempt"], None)
        if span is None:
            return
        for attr, key in (
            ("gen_ai.usage.prompt_tokens", "prompt_tokens"),
            ("gen_ai.usage.completion_tokens", "completion_tokens"),
            ("gen_ai.response.finish_reason", "finish_reason"),
        ):
            if ev.data.get(key) is not None:   # all three are None on a failed call
                span.set_attribute(attr, ev.data[key])
        if ev.data["status"] == "error":
            span.set_status(Status(StatusCode.ERROR, ev.data["error_class"]))
        span.end()

agent = Agentao(working_directory=".", transport=SdkTransport(on_event=on_event))
```

### Which event supplies which attribute

| Attribute | Source event | Payload key |
|---|---|---|
| `gen_ai.system` | — | set it yourself, per provider |
| `gen_ai.request.model` / `.temperature` / `.max_tokens` | `LLM_CALL_STARTED` | `model` / `temperature` (`None` when unset — none was sent; skip the attribute) / `max_tokens` |
| `gen_ai.usage.prompt_tokens` / `completion_tokens` | `LLM_CALL_COMPLETED` | same names |
| `gen_ai.response.finish_reason` | `LLM_CALL_COMPLETED` | `finish_reason` (`None` on failure) |
| Tool span name / span id / start-end | `ToolLifecycleEvent` | `tool_name` / `tool_call_id` / `started_at`, `completed_at` |

Follow OpenTelemetry GenAI semantic conventions.

### Two traps

- **A raw `AgentEvent` carries no timestamp and no turn_id.** `TURN_BEGIN`'s payload is just `user_message`, so a subscriber stamps the time on receipt and pairs turns positionally between `TURN_BEGIN` and `TURN_END`. The typed `HostEvent` carries both — prefer the first entry point wherever it covers you.
- **Listeners run synchronously on the emitting thread**, and anything they raise is logged and swallowed (`agentao/transport/broadcast.py:42`). Your exporter must sit behind an OTel batch processor; do not do network I/O inside the callback, or you are adding latency to every turn.

> **Agentao itself has no OpenTelemetry dependency and ships no built-in exporter.** Everything above is host-side wiring — `opentelemetry-*` is installed in your application. That boundary is deliberate; for how peer harnesses divide it four different ways, see `docs/design/otel-peer-survey.zh.md`.

## Audit and compliance

### Must-keep audit events

| Scenario | Trigger | Retention |
|----------|---------|-----------|
| User starts a session | Agent construction | 90–365 days |
| User approves dangerous tool | confirm_tool = True | 180–365 days |
| Permission rule denied | decide = DENY | 90 days |
| Agent modified user data | Business tool execution | Business-defined (usually 1–7 years) |
| User requests "forget me" | `MemoryManager.wipe_all()` | Indefinite (compliance evidence) |

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

## TL;DR

- **Four axes**: structured logs (`agentao.log`), metrics (Prometheus / StatsD), traces (OpenTelemetry), session replay.
- Minimum metrics: tool call rate by name, tool failure rate, LLM 5xx rate, confirm-timeout rate, turn duration p50/p95/p99, max-iterations hit rate.
- **Session replay** is the killer feature — when "why did the agent do X?" comes up, record sessions with `replay_config=` and `start_replay()`, then step through the recorded timeline. Replay records a run; it does not re-execute one.
- Cost monitoring is a first-class observable: track tokens-per-turn and tokens-per-tenant; sudden 2× spikes usually mean a model swap or skill change.

→ [6.7 Resource Governance & Concurrency](./7-resource-concurrency)
