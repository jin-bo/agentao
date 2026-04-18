# 2.4 Session State & Persistence

A running `Agentao` instance carries a lot more than the last assistant message. Knowing **exactly what lives on the instance and where** is the difference between "users keep their context when the pod restarts" and "every restart throws away the conversation."

This section is the reference for three questions:

1. What state does an `Agentao` instance hold?
2. Which parts must the host persist to survive a restart?
3. How do you restore that state cleanly?

## 2.4.1 The four state buckets

Every `Agentao` instance has four independent storage areas — they are **not** the same thing and must be treated separately when you persist.

| Bucket | Lives on | Survives `close()`? | Who owns durability |
|--------|----------|---------------------|----------------------|
| **Conversation messages** | `agent.messages` (in-memory list) | ❌ no | Host |
| **Memory (persistent)** | `.agentao/memory.db` + `~/.agentao/memory.db` (SQLite) | ✅ yes | Agentao |
| **Session summaries** | `session_summaries` table in project SQLite | ✅ yes | Agentao |
| **Skill activation** | `agent.skill_manager.active_skills` (in-memory dict) | ❌ no | Host (re-activate on restore) |

Rule of thumb: **if it lives on SQLite, Agentao handles it; if it lives in a Python list or dict, your host must persist it.**

## 2.4.2 `agent.messages` — the turn-by-turn log

The core conversation state. List of OpenAI-style message dicts:

```python
agent.messages = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "Hi! How can I help?"},
    {"role": "user", "content": "run git status"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {
                    "name": "run_shell_command",
                    "arguments": '{"command":"git status"}',
                },
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_abc123", "content": "On branch main…"},
    {"role": "assistant", "content": "You're on main with a clean tree."},
]
```

### What is NOT in `agent.messages`

- The system prompt — rebuilt on every `chat()` call from `AGENTAO.md` + date + active skills + memory blocks. Do not persist it.
- Tool schemas — rebuilt from the tool registry on every call.
- The user's working directory — set at construction, immutable after.

### Rules for persistence

1. **Keep pairs together**: an `assistant` message with `tool_calls` and the matching `{role: "tool", tool_call_id: ...}` result **must both be stored or both be dropped**. Persisting one without the other breaks the OpenAI tool-call schema and the next `chat()` will error.
2. **Serialize as JSON**: every entry is already a plain dict. `json.dumps(agent.messages)` is safe.
3. **Skip nothing**: don't filter out tool messages just because they're noisy. The LLM needs them to understand what it already did.

## 2.4.3 Persist + restore recipe

Minimal pattern — one column per session, store the whole message list as JSON:

```python
import json
from pathlib import Path
from agentao import Agentao

def save_session(agent: Agentao, session_id: str, db) -> None:
    """Call after every chat() turn."""
    db.upsert(
        session_id,
        {
            "messages": json.dumps(agent.messages),
            "active_skills": list(agent.skill_manager.get_active_skills().keys()),
            "working_directory": str(agent.working_directory),
            "model": agent.get_current_model(),
        },
    )

def load_session(session_id: str, db) -> Agentao:
    """Call on host startup or request arrival."""
    row = db.get(session_id)
    if row is None:
        raise KeyError(session_id)

    agent = Agentao(
        working_directory=Path(row["working_directory"]),
        model=row["model"],
    )

    # Replay messages — add_message does NOT trigger the LLM.
    for msg in json.loads(row["messages"]):
        agent.messages.append(msg)  # or: agent.add_message(msg["role"], msg["content"])

    # Re-activate skills. Activation is idempotent.
    for name in row["active_skills"]:
        agent.skill_manager.activate_skill(name)

    return agent
```

### Why append directly to `agent.messages`?

`add_message(role, content)` is the public helper — but it only handles plain text messages. For entries containing `tool_calls` or a `tool_call_id`, writing to `agent.messages` directly preserves the full structure. Both paths are supported.

## 2.4.4 Memory restores itself

You do **not** need to persist memory — Agentao already does.

```python
agent = Agentao(working_directory=Path("/app/users/alice"))
# MemoryManager auto-opens /app/users/alice/.agentao/memory.db
# Persistent memories from previous sessions are loaded automatically.
```

As long as `working_directory` is stable across restarts for the same tenant, memory picks up where it left off. This is why [multi-tenant isolation](/en/part-6/4-multi-tenant-fs) insists on **per-user `working_directory`** — it's what scopes memory too.

## 2.4.5 Session summaries — don't manage them

When the context window fills, Agentao's compaction pipeline writes a `[Conversation Summary]` block into `agent.messages` and stores the same summary in `session_summaries` (SQLite). This is transparent:

- Compacted messages stay in `agent.messages`, so your persisted JSON still round-trips cleanly
- On restart, you don't need to rehydrate `session_summaries` separately — the summary block is already inside `messages`

**Don't touch the `session_summaries` table from host code.** It's internal plumbing for the compaction pipeline, not an integration API.

## 2.4.6 Restoring into an ACP server

If you chose the ACP path, use `session/load` instead — the SDK recipe above does not apply. The host sends previously captured `{role, content}` pairs over the wire:

```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "session/load",
  "params": {
    "sessionId": "sess-restored",
    "cwd": "/app/users/alice",
    "history": [
      {"role": "user",      "content": [{"type": "text", "text": "previous question"}]},
      {"role": "assistant", "content": [{"type": "text", "text": "previous answer"}]}
    ]
  }
}
```

Field format is **different from the SDK list** — ACP wraps content in an array of typed chunks. See [Appendix C · session/load](/en/appendix/c-acp-messages#c-7-session-load) for the full schema.

## 2.4.7 Common mistakes

| Mistake | Symptom | Fix |
|---------|---------|-----|
| Dropping tool-call pairs on save | Next `chat()` raises `tool_call_id not found` | Persist the whole list; don't filter |
| Persisting the system prompt | Stale dates, missing new skills | Only persist `agent.messages` — system prompt rebuilds |
| Re-constructing with a **different** `working_directory` | Memory appears empty on restore | Pin `working_directory` per tenant; store it alongside messages |
| Forgetting to re-activate skills | Restored agent forgets "which persona" | Save `active_skills` list; re-activate on load |
| Using `clear_history()` then replaying | `clear_history()` **also** deactivates skills — easy to miss | Either keep the agent and do a full rebuild, or re-activate skills after clearing |

## 2.4.8 When to hold agents in memory vs. rebuild from DB

| Pattern | When | Pros | Cons |
|---------|------|------|------|
| **Live pool** (agent held in memory between turns) | Chat UIs, IDE integrations | Zero rebuild latency; MCP / skill state preloaded | RAM cost scales with active sessions; hard crash loses unpersisted turns |
| **Rebuild-per-request** (load from DB each time) | Serverless, request-sparse workloads | Stateless pods; easy scaling | Extra ~50–200 ms per turn to replay + re-open MCP |
| **Hybrid** (hot pool + fallback to DB) | SaaS chatbots | Hot sessions are fast; cold sessions self-heal | More code |

Production deployments typically run the hybrid pattern — see [7.2 Stateless vs stateful service](/en/part-7/2-stateless-vs-stateful) for the full design.

---

Next: [2.5 Runtime LLM switching →](./5-runtime-llm-switch)
