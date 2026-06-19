# ACP G4 — Plan, Modes & Commands session/update Design

**Status:** Design proposal. Drafted 2026-06-18 as the build design for **G4** from
`acp-server-conformance-review.md` — the top chat-relevant ACP gap after the
maintainer set the target-client class to **chat/automation** (so G1 fs/terminal
is a non-goal and G4/G3/G2-diff are the now-work). **Not yet approved or
implemented.**
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
| `modes` (session/new) + `current_mode_update` | `PermissionMode{read-only, workspace-write, full-access, plan}` (`permissions.py:75`) + `EventType.PERMISSION_MODE_CHANGED` | High — 4 presets → availableModes | **Do 1st** — smallest, highest conformance value |
| `available_commands_update` — `[{name, description, input?}]` | slash commands (`cli/help_text.py`) — but **host/CLI-control, no agent-runtime semantics** | Low | **Scope down or defer** (demand-gate on DeepChat) |

**Sequencing: Modes → Plan → Commands.** Modes + Plan are one compact PR (pure
ACP-layer + schema, no runtime change). Commands is evaluated separately.

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
- **`EventType.PERMISSION_MODE_CHANGED`** (`transport/events.py:45`): fires on mode
  change — the single hook for `current_mode_update` (covers both client-driven
  `session/set_mode` and runtime-internal switches like `/plan implement`).
- **Slash commands** (`cli/help_text.py`): `/memory /compact /mcp /sessions /model
  /mode /skills /replay /sandbox …` — verified to be **host/CLI subsystem control**,
  not agent-task commands. This is the crux of the G4c recommendation.

---

## 4. Design

### 4.1 Modes (do first)

**Schema** (`agentao/acp/schema.py`): add `AcpSessionMode{id, name, description?}`,
`AcpSessionModeState{currentModeId, availableModes}`, and an
`AcpSessionUpdateCurrentMode{sessionUpdate:"current_mode_update", currentModeId}`;
add the latter to the `AcpSessionUpdate` union (`schema.py:567`); add `modes` to
the session/new response model.

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

**current_mode_update**: map `EventType.PERMISSION_MODE_CHANGED` → `{sessionUpdate:
"current_mode_update", currentModeId: <new mode>}` in `transport.py::_build_update`.
One mapping covers every trigger. *Verify* `permission_engine.set_mode()` actually
emits `PERMISSION_MODE_CHANGED` onto the session transport; if it does not, emit
`current_mode_update` directly from the `session_set_mode` handler as a fallback.

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

**Transport special-case** (`transport.py::_build_update`): when the tool is
`todo_write`, map its `TOOL_START` (whose `rawInput.todos` carries the list) to a
**`plan`** update instead of a `tool_call`, and drop the `todo_write`
`TOOL_COMPLETE`:
```python
if tool == "todo_write":
    todos = data.get("args", {}).get("todos", [])
    return {"sessionUpdate": "plan",
            "entries": [{"content": t["content"],
                         "priority": "medium",          # agentao todos carry no priority
                         "status": t["status"]} for t in todos]}
```
**Priority synthesis**: agentao todos have no priority; ACP requires it. v1 emits
`"medium"` for all. *Optional follow-up*: add an optional `priority` field to the
`todo_write` schema so the LLM can set it (then pass through). Zero runtime change
— the mapping lives entirely in the ACP transport, consistent with
`embedding-vs-acp.md`. (Alternative considered: a new `EventType.PLAN_UPDATED`
emitted by the tool — cleaner decoupling but touches the runtime; deferred unless a
non-ACP frontend also needs plan events.)

### 4.3 Commands (scope down or defer)

**Finding:** agentao's slash commands are host/CLI subsystem control with **no
agent-runtime meaning** over ACP, and ACP command *invocation* routes the command
back as `session/prompt` text (`UnstructuredCommandInput` = "all text after the
name"), which the agent would have to parse and dispatch — a separate mechanism
from advertising.

**Recommendation (two tiers):**
- **Advertise-only (cheap, safe):** if any agent-meaningful commands exist, emit
  `available_commands_update` listing them right after `session/new`; with no
  special routing, a selected command simply arrives as prompt text the LLM
  interprets. Low risk, modest value.
- **Defer:** host-action commands (`/memory`, `/compact`, `/sessions`, …) need
  prompt-routing + host round-trips and have little value for a chat client that
  has its own UI. Per the demand-gated rule, build only when DeepChat asks for
  slash-command autocomplete.

Net: **do not port the CLI command list**; commands is the weakest third of G4.

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

**PR-1 (Modes + Plan — one compact change):**
- `agentao/acp/schema.py`: 5 new models + union additions + session/new `modes`
  field. Regenerate `docs/schema/host.acp.v1.json`; update schema snapshot tests.
- `agentao/acp/transport.py`: `todo_write`→`plan`; `PERMISSION_MODE_CHANGED`→
  `current_mode_update`.
- `agentao/acp/session_new.py`: emit `modes` in the response.
- `agentao/acp/session_set_mode.py`: emit `current_mode_update`; keep `{modeId}`.
- Tests: extend `tests/test_acp_transport.py` (plan + mode mappings), session/new
  modes assertion, schema snapshot.

**PR-2 (Commands — only if greenlit):** advertise-only minimal set; no CLI port.

**Verification:** validate emitted notifications against the regenerated
`host.acp.v1.json`; ideally also against the upstream ACP schema (ties to G6).

---

## 7. Open questions

1. Does `permission_engine.set_mode()` emit `PERMISSION_MODE_CHANGED` onto the
   session's ACP transport? (Determines whether `current_mode_update` is one
   mapping or needs a handler-side emit.) — **verify in impl.**
2. Should the `plan` mapping also fire on `TOOL_COMPLETE` (in case the client wants
   a terminal "plan settled" signal), or is `TOOL_START`-only sufficient? — lean
   start-only (the list is final at call time; `todo_write` is synchronous).
3. Priority: ship all-`medium`, or add the optional `todo_write` `priority` field in
   the same PR? — lean all-`medium` first, field as a fast-follow.
