# 4.1 Transport Protocol

> **What you'll learn**
> - The four methods that make up the entire Transport interface
> - The contract for each: what's blocking, what's fire-and-forget
> - How `NullTransport` behaves and when it's the right default

Transport is the **only interface** between the agent runtime and your host UI / business logic. Master its four methods and you can integrate Agentao into any UI framework.

## Definition

```python
@runtime_checkable
class Transport(Protocol):
    # One-way events (fire-and-forget)
    def emit(self, event: AgentEvent) -> None: ...

    # Blocking request-response
    def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool: ...
    def ask_user(self, question: str) -> str: ...
    def on_max_iterations(self, count: int, messages: list) -> dict: ...
```

**Key design**:

- `Transport` is a `Protocol` (PEP 544) — **you do not inherit any base class**; implementing the four methods is enough
- `@runtime_checkable` makes `isinstance(x, Transport)` available (but it doesn't verify method signatures — use a static type checker for that)
- Four methods split 1 + 3: **one-way event push** + **three synchronous Q&A**

## Method 1: `emit(event)` — push events

```python
def emit(self, event: AgentEvent) -> None:
    """Receive runtime events. Must not raise; errors must be swallowed."""
```

**Contract**:

- The agent calls `emit` at key points (turn start, tool start/output/complete, LLM streamed text, thinking, errors…)
- Implementations **must not raise** — exceptions will be caught upstream, but may leave state inconsistent
- Implementations should **return fast** — this is synchronous; slow handlers block the agent loop

**Typical implementation**:

```python
def emit(self, event: AgentEvent) -> None:
    try:
        self._queue.put_nowait(event)   # hand off to another thread
    except Exception:
        pass  # never raise
```

Full event catalog: [4.2 AgentEvent Reference](./2-agent-events).

## Method 2: `confirm_tool(name, desc, args)` — tool approval

```python
def confirm_tool(self, tool_name: str, description: str, args: dict) -> bool:
    """Ask whether the tool may execute.
    True  → allow
    False → cancel (agent receives "Tool execution cancelled by user" and keeps reasoning)
    """
```

**When called**:

- Before invoking any tool with `requires_confirmation=True`
- Default triggers: `write_file`, `run_shell_command`, `web_fetch`, `web_search`

**Blocking semantics**: this is a **synchronous call** — until you return True/False, the agent's execution thread is stuck here. For async hosts, block internally (see 4.5).

**When you return False**:

- The tool **does not execute**
- The agent sees a "cancelled by user" synthetic result
- The LLM keeps reasoning on that (usually pivots or stops and reports)

## Method 3: `ask_user(question)` — ask the user

```python
def ask_user(self, question: str) -> str:
    """Agent asks the user an open question and gets a text answer."""
```

**When called**:

- The agent invokes the built-in `ask_user` tool
- Typical use cases: missing info, decision point, ambiguity clarification

**Fallback**: `NullTransport` returns the fixed string `"[ask_user: not available in non-interactive mode]"`; the agent handles it gracefully.

## Method 4: `on_max_iterations(count, messages)` — iteration-cap fallback

```python
def on_max_iterations(self, count: int, messages: list) -> dict:
    """Called when the agent reaches max_iterations (default 100).
    Return a dict with key "action":
        "continue"        — give it another N iterations
        "stop"            — stop, return current result
        "new_instruction" — inject a new user message; requires "message" key
    """
```

**Canonical use**:

```python
def on_max_iterations(self, count, messages):
    # Auto-extend once
    if not hasattr(self, "_continued"):
        self._continued = True
        return {"action": "continue"}
    # Already extended, still stuck → force summarization
    return {
        "action": "new_instruction",
        "message": "Based on what you have, give the final answer now; do not call any more tools.",
    }
```

Deep-dive: [4.6 Max-iterations strategies](./6-max-iterations).

## Three implementation paths

| Path | When | Complexity |
|------|------|------------|
| **`SdkTransport` + callbacks** | 90% of embeddings | Lowest |
| **Subclass `NullTransport`, override some** | You care about only a few events | Low |
| **Implement `Transport` from scratch** | Fully custom (e.g. ACP, message queue bridge) | Medium |

### Path A · SdkTransport

See [4.3](./3-sdk-transport):

```python
from agentao.transport import SdkTransport

transport = SdkTransport(
    on_event=handle,
    confirm_tool=approve,
    ask_user=prompt,
    on_max_iterations=bail_out,
)
```

### Path B · Subclass NullTransport

When you only care about **some events** and want explicit control:

```python
from agentao.transport import NullTransport, EventType

class MyTransport(NullTransport):
    def __init__(self, on_token):
        self.on_token = on_token

    def emit(self, event):
        if event.type == EventType.LLM_TEXT:
            self.on_token(event.data["chunk"])
        # other events fall through to NullTransport (pass)

    def confirm_tool(self, name, desc, args):
        # Allow only read-like tools
        return name.startswith("read_") or name == "glob"
```

### Path C · From scratch

The canonical real-world case: **ACP server**. It does not inherit from anything — each `emit` becomes a `session/update` notification, each `confirm_tool` becomes a `session/request_permission` request sent to the ACP client.

```python
class MyCustomTransport:
    """Bridge agent events into your own message protocol."""
    def __init__(self, send_to_client):
        self.send = send_to_client

    def emit(self, event):
        self.send({"type": "agent_event",
                   "event": event.type.value,
                   "data": event.data})

    def confirm_tool(self, name, desc, args):
        return self.send({"type": "confirm", ...}, wait=True)

    def ask_user(self, q):
        return self.send({"type": "ask", "question": q}, wait=True)

    def on_max_iterations(self, count, msgs):
        return {"action": "stop"}
```

## Threading / async notes

- **All 4 methods are called synchronously from the agent's `chat()` thread**
- If your host is asyncio:
  - `emit` can `asyncio.run_coroutine_threadsafe(...)` back to the main loop
  - `confirm_tool` / `ask_user` need cross-thread blocking-wait (see 4.5)

## Testing your Transport

```python
from agentao.transport import AgentEvent, EventType

def test_my_transport():
    t = MyTransport()
    # 1. emit must not raise
    t.emit(AgentEvent(EventType.LLM_TEXT, {"chunk": "hi"}))
    # 2. confirm_tool must return bool
    assert isinstance(t.confirm_tool("x", "", {}), bool)
    # 3. ask_user must return str
    assert isinstance(t.ask_user("q?"), str)
    # 4. on_max_iterations must return dict with "action"
    r = t.on_max_iterations(100, [])
    assert r["action"] in {"continue", "stop", "new_instruction"}
```

## TL;DR

- Transport = **4 methods**: `emit` (fire-and-forget), `confirm_tool` (blocking bool), `ask_user` (blocking str), `on_max_iterations` (blocking dict).
- `emit` exceptions are swallowed; the other three's exceptions propagate.
- `NullTransport` = silent + auto-approve — fine for tests and headless batch jobs.
- Implement all 4 if you build a custom transport — even a no-op stub keeps the agent loop honest.

→ Next: [4.2 AgentEvent Reference](./2-agent-events)
