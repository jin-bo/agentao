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
TURN_BEGIN -> (user message arrives — turn begins; carries the user text)
└── TURN_START -> (LLM call starts; resets streaming UI)
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
TURN_END   -> (turn ends; carries final assistant text + status/error)
```

`TURN_BEGIN` / `TURN_END` fire **once per user-driven turn**; `TURN_START` fires **once per LLM iteration** inside that turn. The replay recorder is wired by splicing a `ReplayAdapter` over `agent.transport` that translates the outer pair into recorder turn writes (`ReplayAdapter._mirror`) — not through a subscription. `Transport.subscribe()` (see [4.1](./1-transport-protocol)) is the path for *other* side-channel observers, and the adapter forwards it to the inner transport so subscribing keeps working while replay is on.

Most UIs only need `LLM_TEXT`, `THINKING`, `TOOL_START`, `TOOL_OUTPUT`, `TOOL_COMPLETE`, `TOOL_CONFIRMATION`, `AGENT_START`, `AGENT_END`, and `ERROR`.
The rest are primarily for session replay, audit, metrics, and debugging.

## Per-event details

### `TURN_BEGIN`

| Field | Description |
|-------|-------------|
| Trigger | Once at the start of each user-driven turn, **before** any LLM iteration |
| `data` | `{"user_message": "..."}` |
| Typical use | Open a new turn frame in an audit / observer stream via `Transport.subscribe()`. (The replay recorder is wired separately, through the `ReplayAdapter` splice — not this subscription.) |

Distinct from `TURN_START` (which fires per LLM iteration). `TURN_BEGIN` carries the user input and pairs 1-to-1 with `TURN_END`.

### `TURN_END`

| Field | Description |
|-------|-------------|
| Trigger | Once at the end of each user-driven turn, after the final assistant reply (or on error / cancellation) |
| `data` | `{"final_text": "...", "status": "ok"\|"error"\|"cancelled", "error": None, "tool_count": 3, "incomplete_reason": None}` |
| Typical use | Close the turn frame; flush per-turn metrics — `tool_count` is the number of tool calls the LLM made across all iterations of the turn, so a host can size a turn without replaying every `TOOL_START` |

`incomplete_reason` is `None` on an ordinary turn. It is set when the turn ended without a complete, model-authored answer, and says why:

| Value | Meaning |
|---|---|
| `no_output` | The model emitted nothing. |
| `reasoning_only` | The model emitted reasoning but no answer text. |
| `length_truncated` | Output was cut off at the token limit — either a tool call whose arguments were truncated (the harness then halts the turn) or a final answer cut off mid-sentence. |
| `doom_loop` | The model repeated the same call until the detector halted the turn. |
| `llm_error` | The LLM call itself failed (provider 5xx / rate limit / auth) after retries; `final_text` holds the harness's `[LLM API error: …]` notice, not a model answer. |

This is a **single closed vocabulary**: every turn-ending path commits exactly one of these, or `None` for a real answer — there is no unclassified ending. The first two mean the turn ended normally with no answer; the middle two mean the harness stopped a turn that was not converging; the last means the provider call never yielded a turn. (`max_iterations` is a sixth way a turn ends without a complete answer, but it is a deliberately separate axis — a sticky transport flag with its own exit code 4 and its own `on_max_iterations` interaction, not a value here.)

Prefer it over string-matching `final_text` — the harness substitutes a placeholder or a canned notice there (including the `[LLM API error: …]` notice, now classified `llm_error`), and a Stop hook may decorate it, so the text is not a reliable signal. A cancelled turn is never reported via `incomplete_reason` (it carries `status: "cancelled"`). `agentao run` maps a non-`None` value to a non-zero exit; other hosts are free to retry, prompt, or ignore.

Replay recorders pair this with `TURN_BEGIN` to delimit a turn. Drives the runtime → replay handoff that used to be a direct call into the replay adapter.

### `TURN_START`

| Field | Description |
|-------|-------------|
| Trigger | Before **each LLM iteration** inside a turn (a single turn can fire many) |
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
| `data` | Provider-call metadata before the call; usage / finish metadata after the call. `LLM_CALL_COMPLETED` carries `duration_ms`, `model_latency_ms` (a stable intent-named alias of `duration_ms`), `first_token_ms` (time-to-first-token in ms, or `null` when the call streamed no text — e.g. a tool-only response or a failure before the first delta), `prompt_tokens`, `completion_tokens`, `finish_reason`, plus `status` / `error_class` / `error_message` / `streamed` on the error path |
| Typical use | Metrics, cost tracking, debugging model behavior — `first_token_ms` vs `model_latency_ms` separates queueing/TTFT from total generation time |

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
| `PLUGIN_HOOK_FIRED` | A plugin hook ran. `data["hook_name"]` is one of `UserPromptSubmit` / `SessionStart` / `SessionEnd` / `PreToolUse` / `PostToolUse` / `PostToolUseFailure` / `Stop` / `PreCompact`. The hook-name-specific fields differ — for example `Stop` carries `turn_end_reason ∈ {"final_response", "max_iterations", "doom_loop"}`, `at_max_iter`, `added_context_count`, and `suppress_output`; `PreCompact` carries `compaction_type ∈ {"microcompact", "full", "minimal_history"}` and `trigger="auto"`. Every emit also carries `outcome` and `matched_rule_count` (the count of rules selected for dispatch — when zero, **no event is emitted**). For `Stop`, `outcome ∈ {"allow", "block", "continue", "continue_at_max_iter", "reentry_capped"}` reflects the chat-loop verdict at that exit site (`continue` vs `continue_at_max_iter` disambiguates which site honored a `force_continue` decision; `reentry_capped` means the loop refused a further re-entry). For `PreCompact`, `outcome` is always `"allow"` (observe-only). For the rule-author guide, see [§5.7 Plugin Hooks](/en/part-5/7-plugin-hooks). |

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
