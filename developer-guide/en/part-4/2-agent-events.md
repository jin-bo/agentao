# 4.2 AgentEvent Reference

> **What you'll learn**
> - When to use `AgentEvent` (UI / debug / replay) vs. `HostEvent` (stable host contract)
> - The complete catalog of event types, triggers, and `data` payloads
> - How to safely serialize events for SSE / WebSocket transport

The agent pushes structured events through `transport.emit(event)`. This section is the **complete event catalog** — triggers, `data` payloads, typical use.

::: warning Building a production audit pipeline? Use `HostEvent` instead.
The events on **this page** are the **internal transport events** — they drive the CLI, replay, and debug tooling, and their fields/enum values may change between releases. They're the right pick for **streaming UI** (LLM_TEXT chunks, THINKING bubbles, in-flight tool views).

For **production audit / observability / SIEM pipelines**, use the **stable host contract** in **[4.7 Embedded Harness Contract](./7-host-contract)** instead. Quick comparison:

| Surface | Where | Stability | When to use |
|---|---|---|---|
| `agentao.transport.AgentEvent` (this page) | `Transport.emit()` push callback | Internal — may change per release | CLI / streaming UI that needs rich detail |
| `agentao.host.HostEvent` ([4.7](./7-host-contract)) | `agent.events()` async pull iterator | **Stable**, schema-snapshotted, CI-enforced | Production audit, billing, multi-tenant compliance |

The two surfaces are **complementary, not alternatives** — most production deployments use both: Transport for UI, `events()` for audit. They share zero code paths.
:::

## `AgentEvent` data structure

```python
@dataclass
class AgentEvent:
    type: EventType              # enum
    data: Dict[str, Any] = ...   # must be JSON-serializable
```

The **JSON-serializable** constraint means every `data` payload can ship over SSE / WebSocket / JSON-RPC with no extra marshaling.

## Event groups

```
TURN_START -> (LLM call starts)
├── LLM_CALL_STARTED        (metadata before the provider call)
├── THINKING *              (optional, 0 or more)
├── LLM_TEXT *              (visible streaming chunks)
├── LLM_CALL_DELTA          (new messages since previous call)
├── LLM_CALL_COMPLETED      (usage + finish reason)
├── TOOL_START              (tool begins)
│   ├── TOOL_CONFIRMATION   (optional, mirrors confirm prompt)
│   ├── TOOL_OUTPUT *       (streaming chunks)
│   ├── TOOL_COMPLETE       (status + duration)
│   └── TOOL_RESULT         (final content/hash/disk metadata)
├── AGENT_START / AGENT_END (sub-agent lifecycle)
├── ERROR                   (optional, on errors)
└── replay-only observability events
```

Most UIs only need `LLM_TEXT`, `THINKING`, `TOOL_START`, `TOOL_OUTPUT`, `TOOL_COMPLETE`, `TOOL_CONFIRMATION`, `AGENT_START`, `AGENT_END`, and `ERROR`.
The rest are primarily for session replay, audit, metrics, and debugging.

## Per-event details

### `TURN_START`

| Field | Description |
|-------|-------------|
| Trigger | Before each LLM call |
| `data` | `{}` empty |
| Typical use | Reset UI display, set spinner to "Thinking…" |

```python
if event.type == EventType.TURN_START:
    ui.spinner.text = "Thinking..."
    ui.reset_streaming_buffer()
```

### `THINKING`

| Field | Description |
|-------|-------------|
| Trigger | LLM emits reasoning/thought content (o1, Claude thinking, etc.) |
| `data` | `{"text": "Let me think..."}` |
| Typical use | Render into a collapsible "thinking" panel |

```python
if event.type == EventType.THINKING:
    ui.thinking_panel.append(event.data["text"])
```

### `LLM_TEXT`

| Field | Description |
|-------|-------------|
| Trigger | LLM streams a chunk of the visible reply |
| `data` | `{"chunk": "Sure, I can help"}` |
| Typical use | Append each chunk to the visible reply area |

```python
if event.type == EventType.LLM_TEXT:
    ui.response_area.append(event.data["chunk"])
```

⚠️ A `chunk` can be a few letters, half a word, or an entire paragraph — only ordering is guaranteed, not granularity.

### `TOOL_START`

| Field | Description |
|-------|-------------|
| Trigger | About to execute a tool |
| `data` | `{"tool": "run_shell_command", "args": {...}, "call_id": "uuid"}` |
| Typical use | Insert a "Running X..." card; remember `call_id` to correlate |

`call_id` is the unique key for this invocation. Later `TOOL_OUTPUT`, `TOOL_COMPLETE`, and `TOOL_RESULT` carry the same id, so you can route streamed output to the right card.

### `TOOL_CONFIRMATION`

| Field | Description |
|-------|-------------|
| Trigger | Just before `confirm_tool()` is called |
| `data` | `{"tool": "run_shell_command", "args": {...}}` |
| Typical use | **Optional** mirror event — lets read-only observers see "a prompt is coming" |

You usually don't handle this — real confirmation flows through `confirm_tool()`. `TOOL_CONFIRMATION` is mostly for audit/log stream completeness.

### `TOOL_OUTPUT`

| Field | Description |
|-------|-------------|
| Trigger | Tool emits streaming output mid-execution |
| `data` | `{"tool": "...", "chunk": "...", "call_id": "uuid"}` |
| Typical use | Append chunk to the matching tool card |

Streaming tools include `run_shell_command` (stdout/stderr live), long `web_fetch`, custom "paginated fetch" tools.

### `TOOL_COMPLETE`

| Field | Description |
|-------|-------------|
| Trigger | Tool finishes (success/error/cancelled) |
| `data` | `{"tool": "...", "call_id": "uuid", "status": "ok"\|"error"\|"cancelled", "duration_ms": 123, "error": None}` |
| Typical use | Close spinner, color by `status`, record timing |

```python
if event.type == EventType.TOOL_COMPLETE:
    d = event.data
    ui.close_tool_card(d["call_id"],
                       status=d["status"],
                       duration=d["duration_ms"])
```

### `TOOL_RESULT`

| Field | Description |
|-------|-------------|
| Trigger | After a tool result is available |
| `data` | `{"tool": "...", "call_id": "uuid", "content": "...", "content_hash": "sha256:...", "original_chars": 123, "saved_to_disk": false, "disk_path": null, "status": "ok"\|"error"\|"cancelled", "duration_ms": 123, "error": None}` |
| Typical use | Persist or inspect final tool output without relying on streamed chunks |

For normal UI spinners, prefer `TOOL_COMPLETE`. Use `TOOL_RESULT` for replay, audit, result hashing, and large-output workflows.

### `LLM_CALL_STARTED` / `LLM_CALL_COMPLETED`

| Field | Description |
|-------|-------------|
| Trigger | Around each provider call |
| `data` | Provider-call metadata before the call; usage / finish metadata after the call |
| Typical use | Metrics, cost tracking, debugging model behavior |

### `LLM_CALL_DELTA`

| Field | Description |
|-------|-------------|
| Trigger | After an LLM call adds messages to history |
| `data` | Messages newly added since the previous call |
| Typical use | Session replay with compact per-call history |

### `LLM_CALL_IO`

| Field | Description |
|-------|-------------|
| Trigger | Only when deep capture is enabled |
| `data` | Full prompt/tool payloads for the LLM call |
| Typical use | Offline debugging; treat as sensitive content |

### `ERROR`

| Field | Description |
|-------|-------------|
| Trigger | Runtime caught an exception (LLM, network, MCP disconnect…) |
| `data` | `{"message": "...", "detail": "..."}` |
| Typical use | Show toast, log — **does not** end the session; the agent decides |

```python
if event.type == EventType.ERROR:
    logger.error(event.data["message"], extra=event.data)
    ui.toast(event.data["message"])
```

### `AGENT_START`

| Field | Description |
|-------|-------------|
| Trigger | Agent spawns a sub-agent (e.g. `codebase-investigator`, `Explore`) |
| `data` | `{"agent": "codebase-investigator", "task": "...", "max_turns": 15}` |
| Typical use | Open a "sub-task" collapsible in the UI |

### `AGENT_END`

| Field | Description |
|-------|-------------|
| Trigger | Sub-agent finishes |
| `data` | `{"agent": "...", "state": "completed"\|"...", "turns": 3, "tool_calls": 5, "tokens": 1200, "duration_ms": 8000, "error": None}` |
| Typical use | Collapse sub-task, show summary (3 turns / 5 tool calls / 8s) |

### Replay observability events

These events are emitted for session replay and operational audit. Most interactive UIs can ignore them.

| Event | Typical payload / use |
|-------|------------------------|
| `ASK_USER_REQUESTED` / `ASK_USER_ANSWERED` | Records `ask_user()` prompts and answers |
| `BACKGROUND_NOTIFICATION_INJECTED` | Background notification was injected into the turn |
| `CONTEXT_COMPRESSED` | Context compression occurred |
| `SESSION_SUMMARY_WRITTEN` | Session summary persisted |
| `SKILL_ACTIVATED` / `SKILL_DEACTIVATED` | Skill lifecycle |
| `MEMORY_WRITE` / `MEMORY_DELETE` / `MEMORY_CLEARED` | Memory mutations |
| `MODEL_CHANGED` | Runtime model switched |
| `PERMISSION_MODE_CHANGED` / `READONLY_MODE_CHANGED` | Runtime safety mode changed |
| `PLUGIN_HOOK_FIRED` | Plugin hook ran |

## Enum as string

`EventType` subclasses `str`, so:

```python
>>> EventType.LLM_TEXT
<EventType.LLM_TEXT: 'llm_text'>
>>> str(EventType.LLM_TEXT)
'llm_text'
>>> EventType.LLM_TEXT == "llm_text"
True
```

Drop `event.type` into an SSE/WebSocket field directly:

```python
json.dumps({"type": event.type, "data": event.data})
# → {"type": "llm_text", "data": {"chunk": "..."}}
```

## Event-filter boilerplate

In embedded contexts you usually care only about a handful of event types:

```python
TEXT_EVENTS = {EventType.LLM_TEXT, EventType.TOOL_OUTPUT}
CONTROL_EVENTS = {EventType.TOOL_START, EventType.TOOL_COMPLETE,
                  EventType.AGENT_START, EventType.AGENT_END}

def on_event(event):
    if event.type in TEXT_EVENTS:
        stream_to_ui(event.data.get("chunk", ""))
    elif event.type in CONTROL_EVENTS:
        update_structural_ui(event)
    elif event.type == EventType.ERROR:
        log_and_toast(event)
    # ignore the rest
```

## Event → JSON helper

For SSE / WebSocket / message queues:

```python
def event_to_json(event: AgentEvent) -> str:
    return json.dumps({
        "type": event.type.value,   # "llm_text" / "tool_start" / ...
        "data": event.data,
        "ts": time.time(),
    })
```

Reverse (rebuild from JSON, useful for tests):

```python
from agentao.transport import AgentEvent, EventType

def event_from_json(j: str) -> AgentEvent:
    obj = json.loads(j)
    return AgentEvent(type=EventType(obj["type"]), data=obj["data"])
```

## TL;DR

- `AgentEvent` is **internal** — fields and `EventType` values may change between releases. For a stable host surface (audit / observability), use `HostEvent` — see **[4.7 Embedded Harness Contract](./7-host-contract)**.
- The most common types you'll handle: `LLM_TEXT` (streaming chunks), `TOOL_START` / `TOOL_COMPLETE`, `THINKING`, `ERROR`.
- Treat unknown event types defensively — new ones are added across releases. Always have a default branch.
- Serialize via `event.type.value` + `event.data` (already JSON-safe) — don't pickle.

→ Next: [4.3 SdkTransport Bridging](./3-sdk-transport)
