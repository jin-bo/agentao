# 4.3 SdkTransport Bridging

> **What you'll learn**
> - The four optional callbacks `SdkTransport` exposes and their fallbacks
> - Idiomatic patterns: dispatcher, fan-out, class-grouped state
> - Common pitfalls: hangs, exceptions, mixing transport with legacy callbacks

`SdkTransport` is Agentao's official **general-purpose Transport implementation** — four callbacks that cover 90% of embeddings.

## Constructor

```python
class SdkTransport:
    def __init__(
        self,
        on_event:          Optional[Callable[[AgentEvent], None]]       = None,
        confirm_tool:      Optional[Callable[[str, str, dict], bool]]    = None,
        ask_user:          Optional[Callable[[str], str]]                = None,
        on_max_iterations: Optional[Callable[[int, list], dict]]         = None,
    ) -> None: ...
```

**Everything is optional.** Missing methods fall back to `NullTransport` behavior:

| Missing | Fallback |
|---------|----------|
| `on_event` | Events silently discarded |
| `confirm_tool` | Auto-approve (returns `True`) |
| `ask_user` | Returns `"[ask_user: not available in non-interactive mode]"` |
| `on_max_iterations` | `{"action": "stop"}` |

## Minimum use

```python
from agentao.transport import SdkTransport

transport = SdkTransport(
    on_event=lambda ev: print(ev.type.value, ev.data),
)
```

Already enough to print the full event stream.

## Typical callback implementations

### 1) `on_event` — event dispatcher

```python
from agentao.transport import EventType

def on_event(event):
    match event.type:
        case EventType.LLM_TEXT:
            render_chunk(event.data["chunk"])
        case EventType.TOOL_START:
            open_tool_card(event.data)
        case EventType.TOOL_OUTPUT:
            append_tool_output(event.data)
        case EventType.TOOL_COMPLETE:
            close_tool_card(event.data)
        case EventType.ERROR:
            show_error(event.data)
        # ignore the rest
```

Python 3.10+ has `match`; older versions use `if/elif`.

### 2) `confirm_tool` — approval modal

```python
def confirm_tool(tool_name: str, description: str, args: dict) -> bool:
    if tool_name in {"read_file", "glob", "grep"}:
        return True          # auto-approve read-only

    return user_confirm_dialog(
        title=f"Allow {tool_name}?",
        details=f"{description}\n\n{json.dumps(args, indent=2)}",
    )
```

⚠️ This is **synchronous and blocking**. Async UIs (Flask async, Electron) must bridge to the UI thread and synchronously wait for the result — see [4.5](./5-tool-confirmation-ui).

### 3) `ask_user` — text input

```python
def ask_user(question: str) -> str:
    return user_text_input_dialog(question) or ""
```

Also blocking. Return an empty string on cancel so the agent can handle gracefully.

### 4) `on_max_iterations` — fallback

```python
def on_max_iterations(count: int, messages: list) -> dict:
    answer = user_confirm_dialog(
        f"Agent reached {count} iterations. Continue?"
    )
    return {"action": "continue"} if answer else {"action": "stop"}
```

## Grouping callbacks in a class

When callbacks **share state** (UI object, session id), a class is cleaner:

```python
class ChatSession:
    def __init__(self, ui, session_id: str):
        self.ui = ui
        self.session_id = session_id
        self._events = []

    def on_event(self, event):
        self._events.append(event)
        self.ui.push_event(self.session_id, event)

    def confirm_tool(self, name, desc, args):
        return self.ui.ask_approval(self.session_id, name, desc, args)

    def ask_user(self, q):
        return self.ui.ask_text(self.session_id, q)

    def on_max_iterations(self, count, msgs):
        return self.ui.ask_continue(self.session_id, count)

session = ChatSession(ui, "sess-123")
transport = SdkTransport(
    on_event=session.on_event,
    confirm_tool=session.confirm_tool,
    ask_user=session.ask_user,
    on_max_iterations=session.on_max_iterations,
)
agent = Agentao(transport=transport, working_directory=Path("/tmp/sess-123"))
```

## Fan-out: multiple subscribers

One `on_event` can fan out to many consumers:

```python
class EventFanout:
    def __init__(self):
        self.subscribers = []

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def __call__(self, event):
        for cb in self.subscribers:
            try:
                cb(event)
            except Exception as e:
                logger.warning(f"Subscriber failed: {e}")

fanout = EventFanout()
fanout.subscribe(write_to_database)
fanout.subscribe(push_to_websocket)
fanout.subscribe(update_ui_state)

transport = SdkTransport(on_event=fanout)
```

## Legacy 8-callback API

Pre-0.2.10 Agentao used 8 standalone callbacks (`confirmation_callback`, `step_callback`, `thinking_callback`…). **They still work** — internally Agentao auto-wraps them via `build_compat_transport()` into an `SdkTransport`:

```python
# Old (still works)
agent = Agentao(
    confirmation_callback=lambda n, d, a: True,
    llm_text_callback=lambda chunk: print(chunk, end=""),
    step_callback=lambda name, args: print(f"[{name}]"),
)

# New (preferred)
def on_event(ev):
    if ev.type == EventType.LLM_TEXT:
        print(ev.data["chunk"], end="")
    elif ev.type == EventType.TOOL_START:
        print(f"[{ev.data['tool']}]")

agent = Agentao(transport=SdkTransport(
    on_event=on_event,
    confirm_tool=lambda n, d, a: True,
))
```

See [2.2 Deprecated 8 callbacks](/en/part-2/2-constructor-reference#deprecated-8-callbacks-legacy).

## ⚠️ Common pitfalls

::: warning Don't ship without these
- ❌ **Raising inside `on_event`** — `emit` swallows it but downstream side-effects may be half-done
- ❌ **Hanging forever in `confirm_tool`** — the agent loop hangs along with you
- ❌ **Mixing `transport=` with legacy callbacks** — legacy ones are silently ignored

Each pitfall below has the full fix.
:::

### ❌ Raising inside `on_event`

```python
def on_event(ev):
    if ev.type == EventType.LLM_TEXT:
        ui.append(ev.data["chunk"])   # what if ui is broken?
```

`SdkTransport.emit` swallows exceptions to protect the agent, but **your downstream side-effects may be half-done**. Guard each branch:

```python
def on_event(ev):
    try:
        dispatch(ev)
    except Exception as e:
        logger.warning("event dispatch failed", exc_info=e)
```

### ❌ Hanging forever in `confirm_tool`

If your confirmation dialog bugs out and never returns, the agent **hangs indefinitely**. Always give sync waits a **timeout** (see 4.5).

### ❌ Mixing `transport` with legacy callbacks

```python
# Both provided — legacy ones are IGNORED
agent = Agentao(
    transport=my_transport,
    confirmation_callback=my_callback,  # not called!
)
```

Pick one. `transport` wins.

## Minimal "handle everything" template

```python
from agentao import Agentao
from agentao.transport import SdkTransport, EventType
from pathlib import Path

class AgentBridge:
    def on_event(self, ev):
        handlers = {
            EventType.TURN_START: self._turn,
            EventType.LLM_TEXT: self._text,
            EventType.THINKING: self._thinking,
            EventType.TOOL_START: self._tool_start,
            EventType.TOOL_OUTPUT: self._tool_out,
            EventType.TOOL_COMPLETE: self._tool_done,
            EventType.ERROR: self._error,
            EventType.AGENT_START: self._sub_start,
            EventType.AGENT_END: self._sub_end,
        }
        h = handlers.get(ev.type)
        if h: h(ev.data)

    def _turn(self, d): pass
    def _text(self, d): print(d["chunk"], end="", flush=True)
    def _thinking(self, d): print(f"\n[💭 {d['text']}]", flush=True)
    def _tool_start(self, d): print(f"\n[🔧 {d['tool']}]")
    def _tool_out(self, d): pass
    def _tool_done(self, d): print(f" ✓ ({d['duration_ms']}ms)")
    def _error(self, d): print(f"\n[❌ {d['message']}]")
    def _sub_start(self, d): print(f"\n[🧭 sub: {d['agent']}]")
    def _sub_end(self, d): print(f" ✓ {d['turns']} turns")

    def confirm_tool(self, name, desc, args):
        return input(f"Allow {name}? [y/N] ").lower() == "y"

    def ask_user(self, q):
        return input(f"Agent asks: {q}\n> ")

    def on_max_iterations(self, count, msgs):
        return {"action": "stop"}


bridge = AgentBridge()
transport = SdkTransport(
    on_event=bridge.on_event,
    confirm_tool=bridge.confirm_tool,
    ask_user=bridge.ask_user,
    on_max_iterations=bridge.on_max_iterations,
)
agent = Agentao(transport=transport, working_directory=Path.cwd())
print(agent.chat("hello"))
agent.close()
```

## TL;DR

- 4 optional callbacks; each missing one falls back to `NullTransport` behavior (silent / auto-approve / non-interactive string / `{"action": "stop"}`).
- Group callbacks in a class when they share UI state or session id — closures + per-session `self` is the cleanest pattern.
- Fan out events with a small dispatcher when multiple consumers (DB log + WebSocket + UI) need them.
- **Never raise inside `on_event`** — wrap each branch in try/except, or `SdkTransport.emit` will swallow it for you (but downstream side-effects may be half-done).

→ Next: [4.4 Streaming UI](./4-streaming-ui)
