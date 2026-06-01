# Host tool allowlist: `enabled_tools` (design draft · converged)

**Status:** **Draft, pending review.** Not yet implemented. The additive dual of `host-tool-injection` (`extra_tools` / `disable_tools`, already shipped).
**Audience:** agentao maintainers who want to give hosts a declarative "minimal tool set" selector; and reviewers of the follow-up PR.
**Companions:**
- `docs/design/host-tool-allowlist.zh.md` — Chinese version (authoritative source; this file mirrors it)
- `docs/design/host-tool-injection.md` — the subtractive/per-instance additive entry points `extra_tools` / `disable_tools` (direct predecessor)
- `docs/design/runtime-tool-injection.md` — the runtime dual `add_tool` / `remove_tool`
- `docs/design/embedded-host-contract.md` — host-contract stability boundary (where this design belongs)
- `agentao/tooling/registry.py` — `register_builtin_tools` / `BUILTIN_TOOL_NAMES`
- `agentao/agent.py` — constructor, registration order, `remove_tool`

> **Convergence note (2nd pass):** The first draft modeled `enabled_tools` as a "final visible set spanning built-in + agent + extra, exempting MCP/plan," raised on `extra_tools` conflict, and exported a companion `CORE_TOOL_NAMES`. Review judged that over-designed. This draft converges to the **shortest viable path**: `enabled_tools` only prunes agentao-owned built-in + agent-path tools; extra is always kept; MCP / profiles / `CORE_TOOL_NAMES` / bringing extra under the allowlist are all demand-gated (see §8).

---

## 1. The problem: subtractive only, no additive

After `host-tool-injection` shipped, a host has exactly two construction-time entry points for "is this tool present":

- `disable_tools={...}` — **skip** named built-ins (subtractive). Validated at construction against `BUILTIN_TOOL_NAMES` (`agent.py:366`).
- `extra_tools=[...]` — **inject** instances one by one (per-instance additive).

The single most common embedded need — "**keep only a minimal core set**" — can today only be expressed by enumerating everything you *don't* want via `disable_tools`. Three grep-verified flaws:

1. **Verbose.** To keep the ~6 file/shell tools you must hand-write ~9 exclusions (`web_fetch`, `web_search`, `todo_write`, `activate_skill`, `save_memory`, `check_background_agent`, `cancel_background_agent`, …).
2. **Silently leaks.** `BUILTIN_TOOL_NAMES` is a flat constant whose comment explicitly says it is **"NOT a tool-metadata registry"** (`registry.py:38-47`). A built-in added later **silently enters** the host's set — a blocklist inherently can't express "only these, nothing else."
3. **Can't reach agent-path tools (construction-time only).** `disable_tools` acts on the built-in name set only; project/plugin agent tools register via `_register_agent_tools` (`agent.py:506`), out of its scope.

> **Two boundary clarifications (review corrections, verified):**
> - **Agent tools *can* be removed at runtime.** The `remove_tool()` docstring states "Built-in / extra / agent tools can be removed" (`agent.py:819`). So the gap is precisely: **no construction-time declarative mechanism to keep agent-path tools out of the schema** — not "cannot be removed."
> - **Built-in subagents are off by default.** `enable_builtin_agents: bool = False` (`agent.py:92`). "Drop subagent" only bites when project/plugin agents exist, or built-in agents were explicitly enabled.

## 2. Scope decision: v1 adds exactly one kwarg, `enabled_tools`

**v1 ships only the constructor parameter `enabled_tools`**: an additive allowlist that **acts only on agentao-owned (built-in + agent-path) tools**. It closes the additive gap of §1 and kills "new built-in silently enters" in one stroke — a built-in/agent tool not named in the allowlist never enters, whenever it is added.

| Host need | Existing mechanism | This design |
|---|---|---|
| Hide an individual inapplicable built-in | `disable_tools={...}` (subtractive) | unchanged |
| Add / replace a tool | `extra_tools=[...]` | unchanged |
| **Keep only a minimal core set** | (none — only reverse-enumerate a blocklist) | **`enabled_tools={...}` (additive allowlist)** |

**Explicitly out of v1 (all demand-gated; triggers in §8):**
- MCP is not brought under the allowlist.
- No profile tiers (`tool_profile="core"|...`).
- No exported `CORE_TOOL_NAMES` constant — examples **hand-write** the core set.
- No conflict policy for "extra not in the allowlist" — extra is **always kept** (§4).
- No per-axis switch (`enable_agent_tools=False` and the like).

## 3. Constructor signature

Sits right after the `host-tool-injection` block:

```python
def __init__(
    self,
    ...,
    *,
    working_directory: Path,
    extra_tools: Optional[Sequence["RegistrableTool"]] = None,
    disable_tools: Optional[Iterable[str]] = None,
    enabled_tools: Optional[Iterable[str]] = None,   # NEW
    ...,
):
    ...
    # None = allowlist disabled; any iterable (incl. empty set) = enabled
    self._enabled_tools = frozenset(enabled_tools) if enabled_tools is not None else None
    self._validate_tool_injection()   # only order-independent checks here, see §5
```

**Why `enabled_tools`, not `tools=`:** `tools=` collides/confuses with (a) the existing kwarg `extra_tools=` and (b) the runtime instance attribute `agent.tools` (`ToolRegistry`). `enabled_tools` pairs with `disable_tools` — symmetric and unambiguous.

**Enablement is always decided by `is not None`, never "non-empty":**

| Passed | Meaning |
|---|---|
| `enabled_tools=None` (default) | Allowlist **disabled**; built-in + agent + extra all register (byte-for-byte today's behavior) |
| `enabled_tools={"read_file", ...}` | Enabled; keep only the built-in / agent tools named in the set |
| `enabled_tools=set()` (empty) | Enabled; **remove all built-in + agent tools** (extra / MCP / plan-only still present, see §4). A legal minimal config — not an error |

## 4. Semantics

**One rule: when `enabled_tools is not None`, remove every built-in and agent-path tool whose name is not in the allowlist. Touch nothing else.**

| Category | Governed by `enabled_tools`? | Reason |
|---|---|---|
| Built-in tools (`BUILTIN_TOOL_NAMES`) | **Yes** | the subject being filtered |
| Agent-path tools (project/plugin/built-in agent) | **Yes** | the target of §1 gap 3; mind the all-or-nothing below |
| `extra_tools` injections | **No, always kept** | the host explicitly constructed and passed instances — that *is* the selection; requiring the name be re-listed in the allowlist is redundant config. To drop an extra, the host simply doesn't pass it (it's the host's own code), or uses `remove_tool()` at runtime |
| MCP tools (`mcp_*`) | **No, out of scope** | different lifecycle/namespace; the host controls them via `mcp.json` / `mcp_manager=` / `extra_mcp_servers=`. **Note:** enabling the allowlist does *not* hide already-configured `mcp_*` — to minimize MCP, do it at the MCP layer |
| plan-only (`_PLAN_ONLY_TOOLS` = `plan_save`/`plan_finalize`, `base.py:256`) | **No, always kept** | bound to the plan-mode state machine, already gated by `plan_mode` at schema-build time; not a host-selectable tool |

**Mutually exclusive with `disable_tools`:** `enabled_tools is not None` *and* a non-empty `disable_tools` → `ValueError` (§5). The allowlist already expresses "only these"; layering a blocklist on top only muddies the combined semantics for no net gain.

**Agent-path names are dynamic ⇒ cross-category all-or-nothing (must be documented):** built-in names are a static set of 15; project/plugin agent tool names come from frontmatter `name:` and **vary by project**. Once the allowlist is enabled, an agent name not written out = that agent is pruned. Consequence: `enabled_tools` **cannot express** "keep all agents + only a subset of built-ins" — keeping an agent means enumerating its (project-specific) name. For the minimal-core goal that is exactly the intent; if "keep all agents, filter only built-ins" turns out to be a real use case, that is the trigger for the per-axis switch in §8.

## 5. Validation: split in two (key — incorporates review Finding 2)

`_validate_tool_injection()` is called at `agent.py:183`, **before** `AgentManager` is created (`502`) and `_register_agent_tools()` runs (`506`) — at which point agent tool names are **not available**. So validation must be split:

**(a) Construction-time `_validate_tool_injection()` — only order-independent string checks:**
1. **Mutual exclusion:** `enabled_tools is not None` and a non-empty `disable_tools` → `ValueError`.
2. **Reserved-name rejection:** `enabled_tools` containing an `mcp_`-prefixed name or a `_PLAN_ONLY_TOOLS` name → `ValueError` (these are not governed by the allowlist, so listing them is meaningless; consistent with the reserved-name rules of `extra_tools` / `add_tool`). String-only, no ordering dependency.

**(b) Apply-time `apply_enabled_tools()` — typo guard against the live registry:**
3. After full registration, each name in `enabled_tools` must exist in **live registry ∪ `BUILTIN_TOOL_NAMES`**, else `ValueError` listing the unknown names.
   (The union with `BUILTIN_TOOL_NAMES` is so that, e.g., `web_search` — a legal built-in name that only enters the registry when `[web]` is installed — isn't flagged as a typo merely because the extra is currently absent. Same "registration eligibility ≠ dependency availability" principle as `disable_tools`.)

> An `enabled_tools` typo is more dangerous than a `disable_tools` one: a misspelled `disable_tools` name is a harmless no-op, but a misspelled `enabled_tools` name **silently excludes** that tool and is hard for the host to notice — so guard (b) is not optional.

## 6. Implementation site: a single prune pass after all registration

Registration order (`host-tool-injection §5b`, verified at `agent.py:480-512`):

```
self.tools = ToolRegistry()
self._register_tools()        # built-in (with disable_tools filter)
self.mcp_manager = ...        # mcp_{server}_{tool}
self._register_agent_tools()  # agent-path
register_extra_tools(self)    # host extras (final pass)
apply_enabled_tools(self)     # NEW — prune, see below
self.tool_runner = ToolRunner(tools=self.tools, ...)
```

```python
# tooling/registry.py
def apply_enabled_tools(agent: "Agentao") -> None:
    allow = agent._enabled_tools
    if allow is None:                       # default: disabled
        return

    # (b) typo guard: fail-fast on unknown names (§5-3)
    from agentao.tools.base import ToolRegistry
    known = set(agent.tools.tools) | BUILTIN_TOOL_NAMES
    unknown = sorted(allow - known)
    if unknown:
        raise ValueError(f"Agentao(enabled_tools=): unknown tool name(s) {unknown}")

    # extra is always kept (§4) — computed locally, not stored as state
    extra_names = {tool.name for tool in agent._extra_tools}

    # prune: remove only built-in / agent-path names absent from the allowlist
    for name in list(agent.tools.tools):
        if name.startswith("mcp_"):                 # §4 out of scope
            continue
        if name in ToolRegistry._PLAN_ONLY_TOOLS:   # §4 always kept
            continue
        if name in extra_names:                     # §4 extra always kept
            continue
        if name not in allow:
            agent.tools.unregister(name)
            _logger.info("enabled_tools: pruned '%s' (not in allowlist)", name)
```

- `extra_names` is computed locally inside `apply_enabled_tools()` (`agent._extra_tools` already exists by then) — **no new instance field**.
- **Observability:** pruning is explicit intent, so no warning; but emit an INFO audit line so a host can diagnose "where did my agent go."

**Change surface:** `Agentao.__init__` (accept `enabled_tools` + the §5a checks); add `apply_enabled_tools` and call it after `register_extra_tools` and before `ToolRunner`. Leaves the existing `disable_tools` / `extra_tools` / MCP / plan paths untouched; introduces no constant/profile/instance field.

## 7. Usage

```python
from agentao import Agentao

# 1) Minimal core: hand-write the name set (v1 does not export CORE_TOOL_NAMES, see §8)
CORE = {"read_file", "write_file", "replace",
        "list_directory", "glob", "search_file_content", "run_shell_command"}
agent = Agentao(working_directory=wd, enabled_tools=CORE)

# 2) Core + custom retrieval: extra is always kept, no need to re-list its name (§4)
agent = Agentao(
    working_directory=wd,
    extra_tools=[MyRetrievalTool()],
    enabled_tools=CORE,            # MyRetrievalTool still present — because it's an extra
)

# 3) Bare minimum: remove all built-in + agent (extra / MCP / plan still present)
agent = Agentao(working_directory=wd, enabled_tools=set())

# 4) Illegal: mutually exclusive
Agentao(working_directory=wd,
        enabled_tools={"read_file"}, disable_tools={"web_search"})   # ValueError (§5a-1)
```

## 8. Demand-gated follow-ups (not in v1, with triggers)

| Item | Trigger |
|---|---|
| **Export `CORE_TOOL_NAMES`** | When a **second** host also wants the same core set — export it then, settling the "does core include `ask_user`/`list_directory`" boundary debate at that point, with a pin test against drift |
| **Profile tiers** `tool_profile=` | When multiple hosts repeatedly want the **same** subset; and build it as a host-extensible dict, not fixed strings (else it re-imports the tool categorization `BUILTIN_TOOL_NAMES` deliberately rejected) |
| **Bring MCP under the allowlist** | When a real need arises to minimize `mcp_*` at the agentao layer rather than the MCP layer — would need matching/wildcard semantics for the `mcp_` prefix |
| **Bring extra under the allowlist** ("single final set" semantics) | When "reusable extra list + per-instance subset selection" becomes a real need; decide raise-vs-drop on conflict then |
| **Per-axis switch** `enable_agent_tools=False` | When "keep all agents, filter only built-ins" (the inverse of §4's all-or-nothing) becomes a real pain — today only `enable_builtin_agents` exists, which can't reach project/plugin agents |

## 9. Quick reference

| Dimension | `enabled_tools` (this design) | `disable_tools` (existing) | `extra_tools` (existing) |
|---|---|---|---|
| Direction | additive allowlist | subtractive blocklist | per-instance additive |
| Form | pure data (name set) | pure data (name set) | code (instances) |
| Enablement test | `is not None` (incl. empty set) | non-empty | non-empty |
| Default behavior | full (status quo) | skip nothing | no extras |
| Scope | **built-in + agent-path only**; ignores extra / MCP / plan-only | built-in only | final pass after built-in + agent |
| New built-in silently enters | **No** (not listed → excluded) | Yes (blocklist inherently leaks) | n/a |
| Interaction with the other | **mutually exclusive** with `disable_tools` (§5a-1) | mutually exclusive with `enabled_tools` | unaffected by `enabled_tools` (always kept) |
| settings.json | v1 no | v1 no | no (instances aren't serializable) |
| Validation | construction: mutual-excl + reject reserved names; apply: unknown-name fail-fast | construction: unknown built-in name | construction: dup name + reject `mcp_` |
