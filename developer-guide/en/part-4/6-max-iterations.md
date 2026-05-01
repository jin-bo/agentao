# 4.6 Max-Iterations Fallback

> **What you'll learn**
> - The `on_max_iterations` contract and three return actions (`continue` / `stop` / `new_instruction`)
> - When to ask the user vs. auto-stop vs. inject "summarize and finish"
> - Sane production defaults

`max_iterations` (default 100) is the **circuit breaker** against infinite loops. If the agent keeps calling tools for 100 rounds without producing a final reply, Agentao invokes `transport.on_max_iterations(count, messages)` to ask you what to do.

## Interface

```python
def on_max_iterations(self, count: int, messages: list) -> dict: ...
```

| Param | Purpose |
|-------|---------|
| `count` | How many iterations have been consumed (== `max_iterations`, usually 100) |
| `messages` | Full conversation history (including tool calls), for heuristic decisions |

**Return**: dict with `"action"`:

| `action` | Effect | Extra key |
|----------|--------|-----------|
| `"continue"` | Give `max_iterations` more turns | none |
| `"stop"` | End; `chat()` returns the latest LLM text | none |
| `"new_instruction"` | Inject a user message into history and reset counter | required `"message": str` |

## Default (NullTransport)

Without a callback, `NullTransport.on_max_iterations` returns:

```python
{"action": "stop"}
```

The agent stops immediately and returns whatever it has. Reasonable for batch jobs; possibly wasteful for interactive ones.

## Strategy 1 · Just stop (most conservative)

```python
def on_max_iterations(count, messages):
    return {"action": "stop"}
```

Good when:

- Production API — prefer "incomplete" over runaway
- Cost/latency sensitive
- You have monitoring ready to alert on "incomplete" responses

## Strategy 2 · Force summarization

```python
def on_max_iterations(count, messages):
    return {
        "action": "new_instruction",
        "message": (
            "You've made many tool calls. **Do not call any more tools.** "
            "Give a final answer based on the information you already have."
        ),
    }
```

This is the **most pragmatic** strategy — shift the LLM from "tool-use mode" to "summarize mode" so the user gets a meaningful reply instead of nothing.

Good when:

- Conversational products (chat, support)
- Partial answers are acceptable

## Strategy 3 · Ask the user

```python
def on_max_iterations(count, messages):
    choice = user_transport.ask_user(
        f"Agent has made {count} tool calls without finishing. "
        f"Continue / Stop / Ask it to summarize?"
    )
    if choice.lower().startswith("c"):
        return {"action": "continue"}
    if choice.lower().startswith("s"):
        return {"action": "stop"}
    return {
        "action": "new_instruction",
        "message": "Based on what you have, give the final answer now; no more tool calls.",
    }
```

Good when:

- CLI or desktop app
- User is actively watching progress

## Strategy 4 · Conditional continue

```python
_max_grants = {}  # per-session extensions granted

def on_max_iterations(count, messages):
    sid = get_current_session_id()
    _max_grants[sid] = _max_grants.get(sid, 0) + 1

    # Cap at 2 extensions
    if _max_grants[sid] >= 3:
        return {
            "action": "new_instruction",
            "message": "Extensions exhausted. Provide the final answer now; no more tools.",
        }

    # Detect "obvious stuck" — same tool repeated 5+ times
    last_tools = [m.get("name") for m in messages[-20:] if m.get("role") == "tool"]
    if len(set(last_tools)) <= 1 and len(last_tools) >= 5:
        return {"action": "stop"}

    return {"action": "continue"}
```

Mixes simple heuristics and graduated limits to avoid "infinite extensions".

## Strategy 5 · Token-budget aware

For SaaS that bills by token, extensions cost the user:

```python
def make_on_max_iterations(budget: TokenBudget):
    def handler(count, messages):
        tokens_used = rough_count_tokens(messages)
        if not budget.try_reserve(tokens_used * 2):
            return {
                "action": "new_instruction",
                "message": "Budget exhausted. Give the final answer now.",
            }
        return {"action": "continue"}
    return handler
```

## Reading `messages` for "stuck" patterns

`messages` is the OpenAI-style history. Common failure shapes:

### Signal 1: repeated single tool

```python
def is_stuck_on_one_tool(messages, window=10, threshold=0.8):
    recent = [m for m in messages[-window:] if m.get("role") == "tool"]
    if not recent: return False
    names = [m.get("name") for m in recent]
    most_common = max(set(names), key=names.count)
    return names.count(most_common) / len(names) >= threshold
```

### Signal 2: all tool calls erroring

```python
def is_all_errors(messages, window=10):
    recent = [m for m in messages[-window:] if m.get("role") == "tool"]
    if not recent: return False
    error_count = sum(1 for m in recent if "Error" in str(m.get("content", "")))
    return error_count == len(recent)
```

### Signal 3: no text progress

```python
def no_text_progress(messages, window=30):
    for m in messages[-window:]:
        if m.get("role") == "assistant" and m.get("content"):
            return False
    return True
```

## Composed example

```python
from agentao.transport import SdkTransport

def make_smart_bailout(user_notify):
    _retries = {"count": 0}

    def handler(count, messages):
        _retries["count"] += 1

        # First time: force summary (transparent to user)
        if _retries["count"] == 1:
            user_notify(f"Agent reached {count} iterations. Forcing summary.")
            return {
                "action": "new_instruction",
                "message": "Give the final answer based on current info; no more tools.",
            }

        # Second+ time: conditional stop
        if is_all_errors(messages, window=10):
            user_notify("Consecutive errors detected. Stopping.")
            return {"action": "stop"}
        if is_stuck_on_one_tool(messages):
            user_notify("Tool loop detected. Stopping.")
            return {"action": "stop"}

        user_notify("Multiple bailouts. Stopping.")
        return {"action": "stop"}

    return handler


transport = SdkTransport(
    on_event=handle_event,
    confirm_tool=confirm,
    on_max_iterations=make_smart_bailout(user_notify=log.info),
)
```

## Picking `max_iterations`

Also tune the upstream cap per `chat()` call:

```python
reply = agent.chat("big task", max_iterations=200)   # relax for this turn
```

Rough heuristics:

| Scenario | Suggested cap |
|----------|---------------|
| Simple one-shot Q&A | 20–30 |
| Long tool chains (analysis, code gen) | 80–150 |
| Batch / research tasks | 200–500 |

**Don't set it absurdly high** — `max_iterations` is **the only way** you get an `on_max_iterations` signal. Set it to infinity and you lose the fallback.

## Pairing with the event stream

`TURN_START` fires every iteration. You can independently build a progress bar and an "approaching cap" warning:

```python
iteration_count = 0

def on_event(ev):
    global iteration_count
    if ev.type == EventType.TURN_START:
        iteration_count += 1
        ui.set_progress(iteration_count, MAX)
        if iteration_count == MAX - 20:
            ui.warn("Approaching iteration limit...")
```

Good UX = fallback strategy + frontend progress awareness.

---

You now have the full bridging path from agent events to user UI. Before moving on to Part 5, the next chapter introduces the **stable host contract** — `agent.events()` and `active_permissions()` — for building forward-compatible audit and observability pipelines.

## TL;DR

- `on_max_iterations` returns `{"action": ...}` — three actions: `continue` (raise the cap and keep going), `stop` (return the partial reply), `new_instruction` (inject text and reset the counter).
- Default to `stop` for production — silent loops cost real money. Bump `max_iterations` to ~30 if you pay per call.
- Use `new_instruction` to nudge the model with "summarize what you have and finish" — better than letting it hang.
- Always log the count and the last few tool calls so you can post-mortem why the loop didn't converge.

→ Next: [4.7 Embedded Harness Contract](./7-harness-contract)
