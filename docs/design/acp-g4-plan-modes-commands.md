# ACP G4 — Plan, Modes & Commands session/update Design

**Status:** Design proposal — **revised 2026-06-18 per review; converged, pending
approval.** Build design for **G4** from `acp-server-conformance-review.md` — the
top chat-relevant ACP gap after the maintainer set the target-client class to
**chat/automation** (so G1 fs/terminal is a non-goal and G4/G3/G2-diff are the
now-work). Review pinned the `current_mode_update` emit path (handler, not the
non-firing engine event), added defensive `todo_write`→`plan` validation, noted
`modes` is already a loose schema field, and cut Commands to a clean defer. **Not
yet implemented.**
**Audience:** Agentao maintainers; the DeepChat/TensorChat integration owner.
**Companion:** `acp-g4-plan-modes-commands.zh.md`.
**Related:**
- `acp-server-conformance-review.md` — defines G4 and the chat/automation decision this design serves.
- `deepchat-acp-patch-revision.md` — `session/set_mode` accept-unknown + free-form model; the back-compat constraints in §4.1 trace here.
- `embedding-vs-acp.md` — ACP is a frontend over the embedded core; keep mappings in the ACP layer, not the runtime.

**Method:** ACP shapes are quoted verbatim from the official v1 schema
(`schema/v1/schema.json`, fetched 2026-06-18). agentao primitives are anchored to
`main`@`bcdb8e4`. Every mapping below was checked against actual source — no
intuition mappings.

---

## TL;DR

G4 surfaces three internal Agentao concepts as standard ACP `session/update`
notifications a chat client renders natively: the **task checklist** (→ `plan`),
the **permission mode** (→ `modes` + `current_mode_update`), and **slash
commands** (→ `available_commands_update`). Each maps to an existing primitive:

| ACP surface (schema-verified) | Agentao primitive | Fit | Recommendation |
|---|---|---|---|
| `plan` update — `Plan{entries:[PlanEntry{content, priority, status}]}` | `todo_write` tool (`tools/todo.py`); `todos:[{content,status}]`, status enum identical | High — only `priority` missing | **Do 2nd** — transport special-case, synthesize `priority:"medium"` |
| `modes` (session/new) + `current_mode_update` | `PermissionMode{read-only, workspace-write, full-access, plan}` (`permissions.py:75`); typed-up the existing loose `modes` schema field; emit `current_mode_update` **from the set_mode handler** | High — 4 presets → availableModes | **Do 1st** — smallest, highest conformance value |
| `available_commands_update` — `[{name, description, input?}]` | slash commands (`cli/help_text.py`) — but **host/CLI-control, no agent-runtime semantics** | Low | **Not this round** — design only if DeepChat asks for a command palette |

**Sequencing: PR-1 = Modes + Plan only.** Both are pure ACP-layer + schema (no
runtime change). Commands is **deferred, no implementation planned** this round.

Two conformance asides surfaced while verifying (fold back into the review doc):
**(1)** ACP `ToolKind` has **10** values (`read, edit, delete, move, search,
execute, think, fetch, switch_mode, other`) — the review said 9. **(2)** the ACP
standard `SetSessionModeResponse` is an **empty object**; the mode change is
communicated via `current_mode_update`, whereas agentao currently returns
`{modeId}` (`session_set_mode.py:86`).

---

## 1. Scope

G4 is **outbound-only and client-agnostic** — it emits richer `session/update`
notifications (and one session/new response field). It does **not** require the
client to call back (unlike G1). It is the right now-work for a chat/automation
target because a chat UI renders plans, mode selectors, and command palettes
natively, and DeepChat's existing `set_mode`/`set_model` work already signals
demand in this direction.

Out of scope: the UI-mode-vs-permission-axis *split* (`session_set_mode.py:15-19`
defers it) — for the chat target, mapping the 4 permission presets directly as ACP
modes is the pragmatic v1.

---

## 2. Verified ACP shapes (v1 schema, 2026-06-18)

```jsonc
// session/update variant: "plan"  →  Plan
Plan       = { entries: PlanEntry[] }                       // client replaces whole plan each update
PlanEntry  = { content: string,                             // required
               priority: "high"|"medium"|"low",             // required
               status:   "pending"|"in_progress"|"completed" } // required

// session/update variant: "current_mode_update"  →  CurrentModeUpdate
CurrentModeUpdate = { currentModeId: string }

// NewSessionResponse.modes (optional)  →  SessionModeState
SessionModeState = { currentModeId: string, availableModes: SessionMode[] }
SessionMode      = { id: string, name: string, description?: string|null }

// session/set_mode
SetSessionModeRequest  = { sessionId, modeId }
SetSessionModeResponse = {}                                 // EMPTY (only _meta)

// session/update variant: "available_commands_update"  →  AvailableCommandsUpdate
AvailableCommandsUpdate = { availableCommands: AvailableCommand[] }
AvailableCommand        = { name: string, description: string, input?: AvailableCommandInput|null }
AvailableCommandInput   = { hint: string }                 // "all text after the command name is the input"
```

---

## 3. Agentao source primitives

- **`todo_write`** (`tools/todo.py` — `TodoWriteTool`): holds `self.todos:
  List[{content, status}]`; status enum `pending|in_progress|completed` is a **1:1
  match** to `PlanEntryStatus`. `execute()` replaces the whole list (matches ACP's
  "replace the entire plan"). No `priority` field. `get_todos()` accessor exists.
- **`PermissionMode`** (`permissions.py:75-79`): `read-only`, `workspace-write`,
  `full-access`, `plan`. Default `WORKSPACE_WRITE`. These string values are the
  natural `SessionModeId`s. `session.mode_id` already persisted (`acp/models.py:255`).
- **`EventType.PERMISSION_MODE_CHANGED`** (`transport/events.py:45`): **emitted by
  the CLI** (`cli/app.py:172`), **not** by `PermissionEngine.set_mode()`
  (`permissions.py:382` — no emit) and **not** by the ACP `session/set_mode`
  handler. So on the ACP path this event never fires — `current_mode_update` must
  be emitted by the handler itself (see §4.1). Transport *may* additionally map
  this event for completeness, but the ACP path must not depend on it.
- **Slash commands** (`cli/help_text.py`): `/memory /compact /mcp /sessions /model
  /mode /skills /replay /sandbox …` — verified to be **host/CLI subsystem control**,
  not agent-task commands. This is the crux of the G4c recommendation.

---

## 4. Design

### 4.1 Modes (do first)

**Schema** (`agentao/acp/schema.py`): add `AcpSessionMode{id, name, description?}`,
`AcpSessionModeState{currentModeId, availableModes}`, and an
`AcpSessionUpdateCurrentMode{sessionUpdate:"current_mode_update", currentModeId}`;
add the latter to the `AcpSessionUpdate` union (`schema.py:567`). Note `modes`
already exists on the session/new response as a **loose placeholder**
(`schema.py:198`: `modes: Optional[Dict[str, Any]] = None`) — **replace it with the
typed `AcpSessionModeState`**, don't add a second field.

**session/new** (`session_new.py:421`): build `modes` from the live engine —
```python
"modes": {
  "currentModeId": state.agent.permission_engine.active_mode.value,   # e.g. "workspace-write"
  "availableModes": [
    {"id": "read-only",       "name": "Read-only",       "description": "No writes or shell."},
    {"id": "workspace-write", "name": "Workspace write",  "description": "Writes + safe shell; asks for web."},
    {"id": "full-access",     "name": "Full access",      "description": "All tools, no prompts."},
    {"id": "plan",            "name": "Plan",             "description": "Plans only; does not execute."},
  ],
}
```
(Build the list from the `PermissionMode` enum + a names map so it can't drift.)

**current_mode_update** — emit from the **handler**, not via the event. Verified:
`PermissionEngine.set_mode()` does **not** emit (`permissions.py:382`); the
`PERMISSION_MODE_CHANGED` event is emitted only by the CLI (`cli/app.py:172`), and
the ACP `session_set_mode` handler emits nothing. So mapping the event in transport
would **miss the ACP path entirely.** The minimal correct route: in
`session_set_mode`, after `session.mode_id = mode_id`, send
`{sessionUpdate:"current_mode_update", currentModeId: mode_id}` via the session
transport. (Transport *may* also map `PERMISSION_MODE_CHANGED` so a future
runtime-internal switch surfaces too, but the handler must not rely on it.)

**session/set_mode response** (`session_set_mode.py:86`): the ACP standard response
is empty + change-via-notification. **Keep returning `{modeId}` for DeepChat
back-compat** (a standard client reads `current_mode_update` and ignores the extra
field) but *also* emit `current_mode_update`. Non-preset modeIds (DeepChat
`code`/`ask`) stay UI-only state and are echoed in `current_mode_update` even
though they are not in `availableModes`.

### 4.2 Plan (do second)

**Schema**: add `AcpPlanEntry{content, priority, status}` and
`AcpSessionUpdatePlan{sessionUpdate:"plan", entries:[AcpPlanEntry]}`; add to the
`AcpSessionUpdate` union.

**Transport special-case, defensive** (`transport.py::_build_update`): when the
tool is `todo_write`, map its `TOOL_START` (whose `rawInput.todos` carries the
list) to a **`plan`** update instead of a `tool_call`. The `todos` come from the
LLM and may be malformed, so **validate** rather than trust:
```python
_STATUS = {"pending", "in_progress", "completed"}
if tool == "todo_write":
    raw = data.get("args", {}).get("todos", [])
    entries = [
        {"content": t["content"], "priority": "medium", "status": t["status"]}
        for t in raw
        if isinstance(t, dict)
        and isinstance(t.get("content"), str)
        and t.get("status") in _STATUS
    ]
    # If nothing survives validation, fall through to the normal tool_call
    # mapping rather than emit an empty/garbage plan.
    if entries:
        return {"sessionUpdate": "plan", "entries": entries}
```
**Don't drop `TOOL_COMPLETE` unconditionally.** Drop the `todo_write`
`TOOL_COMPLETE` **only when `status == "ok"`** (the plan landed). If the call
failed, **keep** the `tool_call_update` with `status:"failed"` so the client learns
the plan did *not* settle — otherwise it would be left showing a "settled" plan
from `TOOL_START` that never actually applied.

**Priority**: agentao todos have no priority; ACP requires it → emit `"medium"`
for all. **Out of scope for this PR:** adding a `priority` field to the
`todo_write` tool schema — that turns an ACP-adapter change into a runtime/tool
contract change and isn't worth it. Zero runtime change: the mapping lives entirely
in the ACP transport, consistent with `embedding-vs-acp.md`.

### 4.3 Commands — deferred, no implementation planned this round

agentao's slash commands are host/CLI subsystem control with **no agent-runtime
meaning** over ACP, and ACP command *invocation* routes the command back as
`session/prompt` text (`UnstructuredCommandInput` = "all text after the name"),
which the agent would have to parse and dispatch — a separate mechanism from
advertising. **Do not port the CLI command list.**

**Decision:** Commands is **not built this round.** Open a fresh design only if
DeepChat (or another target client) explicitly asks for a command palette — at
which point the question is *which* agent-meaningful commands exist and how
invocation routes, not how to mirror the CLI. Per the demand-gated rule, no
speculative advertise-only tier.

---

## 5. Conformance asides (fold into `acp-server-conformance-review.md`)

1. **ToolKind = 10 values**, not 9: `read, edit, delete, move, search, execute,
   think, fetch, switch_mode, other`. The review's G2 note (and the local
   `kind` enum, currently 6) should target 10. `switch_mode` is also a candidate
   kind for surfacing a mode switch as a tool call (optional).
2. **`session/set_mode` response**: ACP standard is empty; change via
   `current_mode_update`. agentao's `{modeId}` is a non-standard extra (kept for
   DeepChat). G4.1 resolves this by adding the notification.

---

## 6. Implementation plan

**PR-1 — and only this PR. Two things:** typed modes + `current_mode_update`, and
the defensive `todo_write`→`plan` transport mapping.
- `agentao/acp/schema.py`: add `AcpSessionMode` / `AcpSessionModeState` /
  `AcpSessionUpdateCurrentMode` / `AcpPlanEntry` / `AcpSessionUpdatePlan`; **replace
  the loose `modes: Optional[Dict[str,Any]]`** (`schema.py:198`) with
  `AcpSessionModeState`; add the two new updates to the `AcpSessionUpdate` union.
  Regenerate `docs/schema/host.acp.v1.json`; update schema snapshot tests.
- `agentao/acp/transport.py`: defensive `todo_write`→`plan` (validate entries; drop
  `TOOL_COMPLETE` only on `status=="ok"`). Optionally also map
  `PERMISSION_MODE_CHANGED`→`current_mode_update` for completeness — but it is not
  load-bearing for the ACP path (see §4.1).
- `agentao/acp/session_new.py`: emit the typed `modes` in the response.
- `agentao/acp/session_set_mode.py`: **emit `current_mode_update` from the handler**
  after `session.mode_id = mode_id`; keep returning `{modeId}` for DeepChat.
- Tests: `tests/test_acp_transport.py` (plan happy-path + malformed-todos fallback +
  failed-call keeps `tool_call_update`; mode-change emits `current_mode_update`),
  session/new typed-modes assertion, schema snapshot.

**Commands:** deferred, **no PR planned** (see §4.3).

**Verification:** validate emitted notifications against the regenerated
`host.acp.v1.json`; ideally also against the upstream ACP schema (ties to G6).

---

## 7. Resolved in review (2026-06-18)

1. **`current_mode_update` trigger** — *resolved.* `PermissionEngine.set_mode()`
   does not emit and the ACP handler emits nothing (`permissions.py:382`,
   `session_set_mode.py`), so the event-mapping route would miss the ACP path.
   **Emit from the handler** (§4.1); transport event-mapping is optional, not
   load-bearing.
2. **`TOOL_COMPLETE` for `todo_write`** — *resolved.* Drop it **only on
   `status=="ok"`**; on failure keep the `tool_call_update:failed` so the client
   doesn't see a settled-but-unapplied plan (§4.2). Plan emits on `TOOL_START`
   (list is final at call time; `todo_write` is synchronous).
3. **Priority** — *resolved.* Ship all-`medium`. Adding a `todo_write.priority`
   field is **out of scope** — it expands an ACP-adapter change into a runtime/tool
   contract change (§4.2).
