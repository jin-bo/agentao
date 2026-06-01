# Runtime tool injection: `add_tool` / `remove_tool` (deferred design)

**Status:** Design draft. **Implementation deferred** until a demand-gate (§8) is met. Tracked in [issue #65](https://github.com/jin-bo/agentao/issues/65).
**Audience:** agentao maintainers considering, on top of construction-time tool injection (PR #64), a runtime (post-construction) entry point for hosts to add / remove tools.
**Companions:**
- `docs/design/runtime-tool-injection.zh.md` — Chinese version
- `docs/design/host-tool-injection.md` — construction-time injection (`extra_tools` / `disable_tools`), the predecessor to this design
- `docs/design/embedded-host-contract.md` — the host-contract stability boundary (where this design belongs)
- `agentao/tools/base.py` — `ToolRegistry` (main change site: add `unregister`)
- `agentao/tooling/registry.py` — `_bind_and_register` (reused by runtime injection)

---

## 1. Problem: we have construction-time injection, not runtime

PR #64 landed **construction-time** tool injection — `Agentao(extra_tools=..., disable_tools=...)` (see `host-tool-injection.md`). Those two kwargs are consumed once in `_wire_tooling` and **never re-read**.

This design tracks the **runtime** counterpart: a host wanting to add / replace / remove a tool *after* construction, *between* `chat()` calls. Real scenarios:

- An ACP session attaching a connector mid-conversation (the dual of `extra_mcp_servers=` session-scoped injection in `session/new`, Issue 11).
- A host toggling a capability based on runtime state (e.g. exposing a tool only after login).
- A long-lived embedded session trimming the tool surface in response to user actions.

## 2. Current state (grep-verified on `main`)

| Fact | Anchor | Implication |
|---|---|---|
| **Schema is rebuilt once per `chat()` / `arun()` call** | `runtime/chat_loop/_runner.py:201` (`to_openai_format()` snapshots once, *before* the inner LLM iteration loop) | A registry change made *between* `chat()`/`arun()` calls takes effect on the next call; *within* a single call (across later LLM iterations, stop-hook re-entry) the tool list is frozen |
| **Dynamic add / replace technically works** | `tools/base.py:209` `ToolRegistry.register(tool, replace=)` | …but that pokes runtime internals; it is not on the `agentao.host` contract surface |
| **…and it bypasses two safeguards** | `tooling/registry.py:67` `_bind_and_register` | (a) capability binding (`working_directory`/`filesystem`/`shell`) is lost → the tool goes "bare" (ACP cwd isolation + host FS/shell redirection break); (b) the construction-time validation (`mcp_` prefix ban, empty/non-str name guard, override audit log) does not run |
| **Dynamic removal has no API at all** | `tools/base.py:198-263` (only `register`/`get`/`list_tools`/`to_openai_format`) | No `unregister`/`remove`; `disable_tools` filters at construction only |

In one line: **runtime add/replace is possible but unsafe (bare tools + no validation), and runtime removal has no entry point at all.** Neither is on the contract surface. (Visibility of a change = the next `chat()`/`arun()` call, see §5.)

## 3. Scope: `add_tool` + `remove_tool`, demand-gated

**Only once §8 is triggered**, add two methods to the `agentao.host` contract, **reusing existing infrastructure rather than special-casing**:

```python
agent.add_tool(tool, *, replace=False)   # reuse _bind_and_register + the extra_tools validation
agent.remove_tool(name) -> bool          # reuse the new ToolRegistry.unregister(name)
```

Placed in the `Agentao` public-method cluster alongside `events()` (`agent.py:681`), `active_permissions()` (`agent.py:699`), and `add_host_event_observer()` (`agent.py:658`) — already contract-surface methods.

**Explicitly out of scope:**
- No mid-turn (within a single `chat()`) visibility — the snapshot already constrains this, see §5.
- No cross-task concurrent registry mutation — v1 only supports calls *between* `chat()`/`arun()` calls, see §7.
- No add/remove/replace of plan tools (`_PLAN_ONLY_TOOLS`) — they are reserved names; `add_tool` / `remove_tool` both `ValueError`, see §5.
- No `tool_options` / settings.json (deferred in `host-tool-injection` §10).
- No add/remove of MCP tools — those belong to the MCP lifecycle (`mcp_manager=` / `extra_mcp_servers=`), see §5.

## 4. API

### 4.1 `Agentao.add_tool`

```python
def add_tool(self, tool: "RegistrableTool", *, replace: bool = False) -> None:
    """Register a tool post-construction. Visible to the model on the next
    ``chat()`` / ``arun()`` call (see §5).

    Goes through the SAME validation + capability-binding path as
    ``extra_tools=``:
    - reject **reserved names** — the ``mcp_`` prefix (MCP namespace) and
      ``_PLAN_ONLY_TOOLS`` (``plan_save`` / ``plan_finalize``, bound to the
      plan state machine) — plus empty / non-string names;
    - bind ``working_directory`` / ``filesystem`` / ``shell``;
    - ``replace=False`` with a name clash → ``ValueError`` (require an
      explicit ``replace=True`` — stricter than ``register``'s
      warn-and-overwrite, fitting a deliberate host call);
    - ``replace=True`` overrides a built-in / agent / other extra tool,
      silently, with an INFO audit line.
    """
```

### 4.2 `Agentao.remove_tool`

```python
def remove_tool(self, name: str) -> bool:
    """Unregister a tool post-construction. Returns whether it was actually
    removed (absent → False, not an exception).

    - ``mcp_`` prefix / plan tools (``plan_save`` / ``plan_finalize``, i.e.
      ``_PLAN_ONLY_TOOLS``) → ``ValueError``: the former belongs to the MCP
      lifecycle, the latter to the plan state machine; neither is removed here.
    - built-in / extra / agent tools may be removed.
    - Gone from the model's view on the next ``chat()`` / ``arun()`` call.
    """
```

### 4.3 `ToolRegistry.unregister` (new substrate)

```python
def unregister(self, name: str) -> bool:
    """Remove ``name`` from the registry. Returns whether it existed. Pure
    dict op, no side effects."""
    return self.tools.pop(name, None) is not None
```

## 5. Semantics & precedence

1. **Visibility = "the next `chat()` / `arun()` call"**. `to_openai_format()` snapshots only *before* each `chat()` enters the inner LLM iteration loop (`_runner.py:201`), so a post-construction add/remove takes effect on the **next prompt/chat call**: *within the same `chat()`* (later LLM iterations, stop-hook re-entry) the tool surface does not change. This is a contract, not a defect — it makes "a consistent tool surface within a single call" an invariant (same source as plan-mode's `plan_*` filtering).
2. **`add_tool` validates and binds exactly like `extra_tools`**. Extract the **per-tool** validation from PR #64's `_validate_tool_injection` (`agent.py`) into a reusable function (e.g. `_validate_one_extra_tool(tool)`), shared by `add_tool` and the construction-time loop — so runtime injection cannot produce a bare tool, nor use a reserved name (`mcp_` prefix ∪ `_PLAN_ONLY_TOOLS`) to replace an MCP / plan tool. Note: once the reserved-name set includes `_PLAN_ONLY_TOOLS`, construction-time `extra_tools` benefits too — it incidentally closes the old gray zone where an extra named `plan_save` would be overwritten by the CLI's later registration. A consistent tightening.
3. **Coverage: built-in + agent + extra, not MCP**. The `mcp_` prefix is rejected in both `add_tool`/`remove_tool` — MCP tool names always start with `mcp_` (`mcp/tool.py:19-21` `make_mcp_tool_name`), so runtime injection structurally cannot touch MCP. Runtime MCP add/remove goes through `mcp_manager=` / `extra_mcp_servers=`; clean boundary.
4. **Plan tools cannot be added / removed / replaced (reserved names)**. `plan_save`/`plan_finalize` are registered by the CLI post-construction (`cli/app.py:91-92`), enter the schema only in plan mode (`_PLAN_ONLY_TOOLS`), and are bound to the plan state machine. `add_tool` (including `replace=True`) **and** `remove_tool` both `ValueError` on `_PLAN_ONLY_TOOLS` names — banning only removal would leave the `add_tool(name="plan_save", replace=True)` loophole, so both ends are banned, leaving no public-API gray zone.

## 6. Implementation sketch (change surface)

| Change | Location |
|---|---|
| `ToolRegistry.unregister(name) -> bool` | `tools/base.py`, next to `register` (:209) |
| Extract per-tool validation `_validate_one_extra_tool(tool)` | `agent.py`, from the existing `_validate_tool_injection`; shared by the construction-time loop and `add_tool`. **Reserved-name set = `mcp_` prefix ∪ `_PLAN_ONLY_TOOLS`**, plus the empty/non-string name guard |
| `Agentao.add_tool` / `remove_tool` | `agent.py`, in the `events()`/`active_permissions()` cluster (near :681/:699). Both validate against the reserved-name set first (`add_tool` via `_validate_one_extra_tool`; `remove_tool` likewise rejects `mcp_` + `_PLAN_ONLY_TOOLS`), then bind / `unregister` |
| `add_tool` reuses `_bind_and_register` | already at `tooling/registry.py:67`, no change |
| Docs + contract surface | add two rows to the public-method table in `docs/api/host.md`; `host/__init__.py` needs no change (these are `Agentao` methods, not package-level symbols) |

A simple route, same shape as PR #64: **grep-verify → design (this doc) → patch + tests**.

## 7. Call timing & concurrency (kept narrow)

v1 **only supports calling `add_tool` / `remove_tool` between `chat()` / `arun()` calls**. It does **not** promise: cross-task concurrent registry mutation, or mutating the registry while a `chat()` is in progress (from another task or from a tool's own `execute()`).

Why: `to_openai_format()` iterates `self.tools` via `sorted(self.tools.values(), ...)` to snapshot, which combined with concurrent `dict` add/remove is **not** safe (can hit "dict changed size during iteration"). Once the semantics are narrowed to "between calls", that combination cannot arise — so v1 **needs no lock**, and **needs no** appeal to single-op GIL atomicity.

If a real "must mutate the tool surface mid-session, concurrently" scenario appears, *then* evaluate adding a lock to `ToolRegistry`, or versioning the snapshot — that's a separate design, not a promise of this doc.

## 8. Implementation trigger (demand gate)

Start implementation only when **one** of these holds (gap ≠ need):

1. A real embedding scenario needs to add/remove tools **mid-session**, where construction-time `extra_tools`/`disable_tools` + rebuilding `Agentao` won't do (e.g. a long ACP session that can't be rebuilt).
2. An ACP `session/update`-style need appears, requiring a session-scoped dynamic tool surface.
3. A host reports being forced to poke `agent.tools.register(...)` and hitting the bare-tool (FS/shell unbound) pitfall.

Until then this doc is a spec on the shelf, ensuring the implementation lands in days, not weeks, when triggered.

## 9. Open questions (out of v1 scope, recorded for later)

**v1 converges to three steps, no expansion:** `ToolRegistry.unregister()` + `Agentao.add_tool()` (reusing `_bind_and_register`) + `Agentao.remove_tool()` (name validation then `unregister`). All of the following are **not done** for now:

- **Should `add_tool(replace=False)` raise or warn on a name clash?** (Decide at implementation; leaning raise — a deliberate host call, stricter than `register`'s warn — and distinguish it from `register`'s semantics in the docs.)
- **Concurrency lock / snapshot versioning** — see §7, await a real signal.
- **`has_tool(name)` / `list_tool_names()` read-only query surface** — `list_tools()` exists (`base.py:240`) but returns instances; add on demand.
- **`AgentManager` coupling after `remove_tool` removes an agent tool** — v1 assumes the registry is the sole exposure surface and the sub-agent path holds no dangling references; confirm at implementation, don't pre-build a coupling mechanism.

## 10. What this doc is not

- **Not mid-turn mutation**. A frozen tool surface within a single `chat()` / `arun()` is a deliberate invariant.
- **Not MCP runtime management**. MCP add/remove goes through `mcp_manager=` / `extra_mcp_servers=`.
- **Not `tool_options`**. Configuring built-ins stays deferred per `host-tool-injection` §10.
- **Not a permission-engine bypass**. `remove_tool` reduces schema exposure, not a security boundary; security / authorization stays with the permission engine.

## 11. References

- **Predecessor design:** `host-tool-injection.md` (construction-time injection, PR #64), its §9 (landing prerequisites).
- **Agentao touch points (verified 2026-06-01):**
  - `agentao/tools/base.py:198-263` — `ToolRegistry` (no `unregister`).
  - `agentao/runtime/chat_loop/_runner.py:201` — the `to_openai_format` snapshot taken before `chat()`/`arun()` enters the LLM iteration loop (the *only* runtime tool-surface build point; the `to_openai_format` in `agent.py`'s `get_conversation_summary()` is token estimation, not the runtime tool surface).
  - `agentao/tooling/registry.py:67` — `_bind_and_register` (capability binding, reused at runtime).
  - `agentao/tooling/registry.py:138` — `register_extra_tools` (the construction-time final pass, same-shape reference).
  - `agentao/mcp/tool.py:19-21` — `make_mcp_tool_name` (source of the `mcp_` prefix).
  - `agentao/cli/app.py:91-92` — plan tools registered post-construction (the orthogonal path).
  - `agentao/agent.py:658/681/699` — `add_host_event_observer` / `events` / `active_permissions` (the public-method cluster, where the new methods go).
- **Tracking:** [issue #65](https://github.com/jin-bo/agentao/issues/65).
