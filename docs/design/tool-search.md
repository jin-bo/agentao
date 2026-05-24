# Tool Search: Deferred-Loading Tool Discovery

**Status:** Design draft. Decision captured 2026-05-24. Implementation deferred
until a trigger condition (below) is met.
**Audience:** agentao maintainers considering tool-list-budget pressure from
MCP / plugin growth.
**Companion:** `tool-search.zh.md`.

## Problem

`ToolRegistry.to_openai_format()` (`agentao/tools/base.py:244-263`) emits every
registered tool's `{name, description, parameters}` on every turn, sorted
alphabetically for cache stability. Every native `Tool`, every `AsyncToolBase`,
and every MCP-bridged tool (`agentao/mcp/tool.py:71-81`) flows through this one
list.

This works at the current scale (~15 native tools, light MCP). It does not at:

- Multiple MCP servers connected simultaneously (filesystem + github + slack +
  custom = easily 60+ tools).
- A plugin ecosystem where plugins routinely contribute 5–15 tools each.
- Long-running embeds where users dynamically attach connectors mid-session.

Costs that grow linearly with tool count:

1. **Initial-prompt tokens.** Every tool's name + description + JSON Schema is
   in the cached prefix, but still counts against the model's context window.
2. **Selection accuracy.** Large flat tool lists hurt model tool-selection
   accuracy; the practical breakpoint is widely cited around 50 tools.
3. **Schema injection cost.** MCP servers often emit verbose schemas — hundreds
   of tokens each — that the model rarely consults.

agentao currently has **no mechanism** to register a tool for dispatch without
exposing it to the model in the initial schema.

## Codex's design (reference)

Codex's solution has four moving parts (verified by grep in the codex tree on
2026-05-24):

1. **`ToolExposure` enum** (`codex-rs/tools/src/tool_executor.rs:8-27`):
   - `Direct` — listed upfront, model-visible.
   - `Deferred` — registered for dispatch, hidden from the initial list,
     discoverable via `tool_search`.
   - `DirectModelOnly` — listed upfront but excluded from the code-mode nested
     table (irrelevant to agentao; agentao has no code-mode).
   - `Hidden` — registered for dispatch, never shown.

2. **`tool_search` tool**
   (`codex-rs/core/src/tools/handlers/tool_search.rs`) — a `Direct` tool whose
   handler runs **BM25** (`bm25` Rust crate) over deferred tools' search text
   and returns matching `LoadableToolSpec`s for the next model call.

3. **Decision rules** (`codex-rs/core/src/mcp_tool_exposure.rs:17-48`):
   `should_defer = search_tool_enabled && (always_defer_flag || tool_count >= 100)`.
   Multi-agent v2's 5-tool family (`SpawnAgent`, `SendInput`, `ResumeAgent`,
   `WaitAgent`, `CloseAgent`) is hard-coded `Deferred` whenever search and
   namespace tools are both on. Native tools (shell, apply_patch, …) always
   `Direct`.

4. **No-op when empty.** `append_tool_search_executor`
   (`codex-rs/core/src/tools/spec_plan.rs:780`) does not register
   `tool_search` if no deferred tools exist — zero cost at low scale.

A companion tool, `request_plugin_install`, handles "plugin not yet installed"
flows. That depends on Codex's plugin marketplace and is not relevant here.

## Decision

agentao **adopts the design but defers implementation** until a real trigger
appears. The trigger is binary:

> A specific embed reports measurable tool-list bloat — token-budget pressure,
> selection-accuracy regression backed by evals, or user-visible latency —
> caused by MCP / plugin tool count.

Until then this is spec-on-the-shelf. Premature implementation adds a
tool-exposure axis with no measurable user, complicating every future tool
change for a benefit no one has asked for.

This stance is consistent with the [codex reverse review](codex-reverse-review.md):
codex's design was driven by their connector marketplace and multi-agent v2.
agentao has neither today.

## Schema (when implemented)

### 1. Exposure axis on `Tool`

Add one property to `_BaseTool` so it propagates to both `Tool` and
`AsyncToolBase`:

```python
class ToolExposure(str, Enum):
    DIRECT = "direct"     # default, current behavior
    DEFERRED = "deferred" # registered for dispatch, hidden from initial schema
    HIDDEN = "hidden"     # registered for dispatch, never model-visible

class _BaseTool:
    @property
    def exposure(self) -> ToolExposure:
        return ToolExposure.DIRECT
```

Skip `DirectModelOnly` — no code-mode in agentao.

### 2. `ToolRegistry.to_openai_format()` filter

Add one clause to the existing comprehension
(`agentao/tools/base.py:259-263`):

```python
return [
    tool.to_openai_format()
    for tool in sorted(self.tools.values(), key=lambda t: t.name)
    if (plan_mode or tool.name not in self._PLAN_ONLY_TOOLS)
    and tool.exposure is ToolExposure.DIRECT
]
```

The alphabetical-sort prefix-cache invariant is preserved.

### 3. The `tool_search` tool itself

A first-class agentao `Tool` (not MCP, not plugin) that the registry
auto-injects iff at least one registered tool has `exposure == DEFERRED`:

```python
class ToolSearchTool(Tool):
    name = "tool_search"
    description = (
        "Search deferred tools by name/description. "
        "Returns matching tool specs; they become callable on the next turn."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 8},
        },
        "required": ["query"],
    }
```

**Backend.** Start with `rank-bm25` (PyPI, ~200 LOC pure Python) over
`{name} {description} {tags}`. Reasons: same algorithm as codex, no Rust
toolchain, swappable for tfidf or vector search later if quality is
insufficient. Rebuild the index per turn — at 100s of tools it is microseconds.

### 4. Activation model

Two candidates:

- **(a) Stateless.** `tool_search` returns matched specs in its tool result.
  The model issues the call by name on the next turn; dispatch already works
  because the tool was registered the whole time. History naturally carries
  the spec forward in the prior `tool` message.
- **(b) Stateful.** `tool_search` marks tools as "promoted" on the session;
  `to_openai_format` includes promoted tools on subsequent turns until session
  end.

**Recommendation: (a) Stateless.** Simpler. No session-state divergence. No
replay / compaction interaction to design. Matches codex's behavior — codex
re-injects via search results, not via persistent state.

### 5. Default decision rule for MCP

Mirror codex's pattern with an agentao-appropriate threshold:

```python
DEFAULT_MCP_DEFER_THRESHOLD = 50  # codex uses 100; start lower
```

Native tools default `Direct`. Plugin tools default `Direct`. MCP tools
auto-defer when their total count crosses the threshold, **or** when the host
explicitly configures deferral. Hosts can override per-tool via the registry.

### 6. Host opt-in surface

Add to the embedded harness contract:

```python
agent = Agentao(
    ...,
    tool_exposure_policy=ToolExposurePolicy(
        auto_defer_mcp_threshold=50,
        always_defer_plugins=False,
    ),
)
```

Default policy: no deferral until threshold hit. Existing embeds see no
behavior change.

## What this document is NOT

- **Not code mode.** Code mode is a separate codex design (one freeform `exec`
  tool + embedded V8). agentao's chat-completions function-calling path does
  not support freeform tools without significant rework, and V8 embedding is
  not on the roadmap.
- **Not a plugin marketplace.** Codex's `request_plugin_install` flow assumes
  installable plugins from a marketplace. agentao's plugin model is local-file.
- **Not a vector search system.** BM25 is sufficient at the scale where this
  matters (tens to low hundreds of tools).
- **Not an excuse to skip the tool-budget conversation.** If a host is hitting
  tool-list bloat, the first response is usually "fewer MCP servers" or "split
  the embed into focused agents", not "add `tool_search`".

## Trigger conditions for implementation

Implementation begins when **one** of these is observed:

1. A real agentao embed reports >30 MCP tools simultaneously connected with
   measurable token-budget pressure.
2. Selection-accuracy regression observed empirically (eval-suite evidence of
   wrong-tool selection rate rising with tool count).
3. agentao adds first-class plugin tool registration where plugins routinely
   contribute 5+ tools each.

Until then this document records the design so implementation ships in days
rather than weeks when triggered.

## Open questions

- **Threshold default.** Codex uses 100. agentao's user base trends toward
  smaller embeds; 50 may still be too high. Set when the first real signal
  arrives.
- **Per-server granularity.** Should hosts defer "this MCP server" while
  keeping "that MCP server" direct? Probably yes; needs registry-side UX.
- **Tool result shape.** Return full schemas (large but one round-trip) or
  name + brief description (model must follow up to learn the schema)? Codex
  returns full `LoadableToolSpec`. Default to full; revisit if it costs more
  than it saves.
- **Index lifecycle.** Rebuild per turn (cheap, simple) vs cached +
  invalidated on registry change (faster, more code). Start with per-turn.
- **Interaction with a future `Hidden` axis.** Is `Hidden` an exposure value
  or an orthogonal flag? Lean orthogonal: `exposure=DIRECT, hidden=True` is
  semantically distinct from `exposure=DEFERRED`. Decide alongside the Hidden
  proposal (Worth-Considering item from the codex reverse review).

## References

- **Codex implementation (verified 2026-05-24):**
  - `codex-rs/tools/src/tool_executor.rs:8-27` — `ToolExposure` enum.
  - `codex-rs/core/src/tools/handlers/tool_search.rs` — handler with BM25.
  - `codex-rs/core/src/tools/handlers/tool_search_spec.rs` — tool schema +
    model-facing description.
  - `codex-rs/core/src/mcp_tool_exposure.rs:17-48` — MCP defer decision.
  - `codex-rs/core/src/tools/spec_plan.rs:762-781` — `append_tool_search_executor`.
  - `codex-rs/core/templates/search_tool/tool_description.md` — model-facing
    prompt copy.
- **Agentao surfaces touched:**
  - `agentao/tools/base.py:198-263` — `ToolRegistry.to_openai_format`.
  - `agentao/mcp/tool.py:71-81` — `McpTool` schema bridging.
- **Related agentao designs:** [codex-reverse-review.md](codex-reverse-review.md).
- **BM25 implementation:** `rank-bm25` (PyPI, MIT, pure Python).
