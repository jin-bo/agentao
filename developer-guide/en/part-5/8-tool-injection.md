# 5.8 Host Tool Injection

> **What you'll learn**
> - The three construction kwargs that select the tool surface: `extra_tools`, `disable_tools`, `enabled_tools`
> - The two runtime methods for mutating it between turns: `add_tool` / `remove_tool`
> - Which combinations are legal, what each is *not* (none is a security boundary), and where the worked example lives

[5.1](./1-custom-tools) shows how to **write** a `Tool`. This section shows how a **host** gets tools into — and out of — an embedded `Agentao`, as a stable part of the `agentao.host` contract rather than by poking runtime internals.

There are two planes:

| Plane | API | Visible to the model |
|---|---|---|
| **Construction** | `Agentao(extra_tools=, disable_tools=, enabled_tools=)` | from the first `chat()` |
| **Runtime** | `agent.add_tool(...)` / `agent.remove_tool(name)` | on the **next** `chat()` / `arun()` (schema is snapshotted per call, never mid-turn) |

All injected tools go through the **same capability binding** as built-ins (`working_directory` / `filesystem` / `shell`), so they inherit the session's cwd isolation and the host's FS/shell redirection — they never become "bare" tools. Reaching into `agent.tools.register(...)` directly skips that binding and the validation below; prefer the contract APIs.

## Construction-time injection

### `extra_tools` — add or replace (code)

A sequence of already-constructed `Tool` / `AsyncToolBase` instances. They register **last** (after built-ins and agent tools), so a same-named entry **replaces** a built-in or agent tool silently. This is also how you *configure* a built-in: pass a pre-constructed instance instead of relying on env.

```python
from agentao import Agentao

# Swap the built-in web_search for an in-house backend — same name → replaces.
agent = Agentao(
    working_directory=wd,
    extra_tools=[WebSearchTool(backend="bocha", api_key=key)],
)
```

- Names must be unique and **must not** carry the `mcp_` prefix (that namespace is reserved — replace MCP tools via `mcp_manager=` / `extra_mcp_servers=`, not here).
- `extra_tools` is **never** loaded from JSON — implementations can't be serialized.

### `disable_tools` — hide a built-in (data)

A set of built-in tool **names** to skip during registration. Pure data, so it's serialization-friendly (though v1 has no settings.json loader for it).

```python
# Read-only deployment: drop shell and the default web tools.
agent = Agentao(
    working_directory=wd,
    disable_tools={"run_shell_command", "web_search", "web_fetch"},
)
```

- Every name **must** be a real built-in, else `ValueError` at construction — typo protection (`{"web_serach"}` fails loudly instead of silently no-op'ing).
- Validation is against *static eligibility*, not live availability: `disable_tools={"web_search"}` is a legal no-op even without the `[web]` extra installed.
- It only skips **built-in** registration. It does not touch `extra_tools`, MCP, or agent tools.

### `enabled_tools` — allowlist (data)

The additive dual of `disable_tools`: instead of naming what to drop, name the **only** agentao-owned tools to keep.

```python
# Minimal coding surface — everything else agentao-owned is pruned.
agent = Agentao(
    working_directory=wd,
    enabled_tools={"read_file", "write_file", "edit_file", "search_text", "run_shell_command"},
)
```

The semantics turn on `None` vs. not-`None` — **not** on emptiness:

| `enabled_tools` value | Effect |
|---|---|
| `None` (default) | Status quo — no allowlist, every eligible tool registers |
| any iterable, **including `set()`** | Allowlist *on* — prune every agentao-owned tool not named |

So `enabled_tools=set()` is "enable the allowlist, allow nothing agentao-owned" — a deliberate, legal way to strip the agent down to just your `extra_tools` + MCP.

**Scope = agentao-owned only.** The prune pass touches built-in and agent-path tools. It **always keeps**:
- `extra_tools` you injected (you already chose those explicitly),
- MCP tools (`mcp_*` — managed by the MCP lifecycle),
- plan-only tools (`plan_save` / `plan_finalize` — bound to the plan state machine).

**Mutually exclusive with `disable_tools`** — passing both raises `ValueError` (an allowlist and a denylist at once is ambiguous). This holds even for `enabled_tools=set()` + a non-empty `disable_tools`.

Validation is split: the **construction** check catches the mutual-exclusion clash and rejects reserved names (`mcp_` prefix, plan-only); the **apply-time** check (after `extra_tools` register) runs the typo guard against the *live* registry — an unknown name like `read_fil` raises there, because agent-path tool names aren't known until after construction.

## Runtime injection

Between `chat()` / `arun()` calls a host can mutate the surface — the dual of construction-time injection, for long-lived sessions that can't be rebuilt (e.g. an ACP session attaching a connector mid-conversation, or exposing a tool only after login).

```python
agent.add_tool(my_tool)                 # add; name clash → ValueError
agent.add_tool(my_tool, replace=True)   # deliberate replace (silent, INFO audit line)
removed = agent.remove_tool("web_fetch")  # True if it existed, False if absent (no raise)
```

- `add_tool` runs the **same** validation + capability binding as `extra_tools`. Note it is **stricter** than the low-level `agent.tools.register`: a name clash with `replace=False` raises, rather than warn-and-overwrite — a deliberate host call deserves an explicit `replace=True`.
- Both reject **reserved names**: the `mcp_` prefix (MCP lifecycle) and the plan-only tools (`plan_save` / `plan_finalize`). Both ends are banned so there's no `add_tool(name="plan_save", replace=True)` loophole.
- **Visibility = the next call.** `to_openai_format()` snapshots the schema once *before* each `chat()` enters its LLM-iteration loop, so a mid-turn mutation does not change what the model sees this turn — a deliberate "consistent schema within one call" invariant. Don't mutate the registry concurrently with an in-flight `chat()`.

## What none of these are

::: warning Not a security boundary
`disable_tools` / `enabled_tools` / `remove_tool` reduce the **schema the model sees** — they keep the LLM from *attempting* an inapplicable capability. They are **not** authorization. Security and authorization stay with the **[PermissionEngine](./4-permissions)**; a tool that must never run for this tenant belongs in a permission rule, not (only) an allowlist.
:::

- They don't touch MCP: MCP tools are added/removed through the MCP lifecycle (`mcp_manager=` / `extra_mcp_servers=`), never through these kwargs or methods.
- They don't serialize implementations: `extra_tools` is code-only; the name-based kwargs are data but, in v1, are construction-API only (no settings.json field).

## Worked example: Jina-backed `web_fetch` / `web_search`

[`examples/tool-injection/`](https://github.com/jin-bo/agentao/tree/main/examples/tool-injection) is a runnable, offline-testable demo of both planes — it swaps two built-ins for a [Jina](https://jina.ai) backend by name:

| Surface | When | Tool | Jina endpoint |
|---|---|---|---|
| `Agentao(extra_tools=[...])` | construction | `web_fetch` | `https://r.jina.ai/{url}` (Reader) |
| `agent.add_tool(...)` | runtime | `web_search` | `https://s.jina.ai/{query}` (Search) |

The smoke tests drive both tools through an `httpx.MockTransport`, asserting the correct endpoint + `Authorization: Bearer <JINA_API_KEY>` header are used with **no network call**:

```bash
cd examples/tool-injection
uv sync --extra dev
PYTHONPATH=. uv run pytest tests/ -v
```

## Choosing between them

| You want to… | Use |
|---|---|
| Add a custom tool, or replace a built-in's implementation | `extra_tools=` (construction) / `add_tool()` (runtime) |
| Hide a few inapplicable built-ins | `disable_tools={...}` |
| Pin the surface to a small allowlist | `enabled_tools={...}` (everything else agentao-owned is pruned) |
| Strip to just your own tools + MCP | `enabled_tools=set()` |
| Mutate the surface mid-session (between turns) | `add_tool()` / `remove_tool()` |
| Enforce that a tool never *runs* | [PermissionEngine](./4-permissions) — not these |

## TL;DR

- **Construction:** `extra_tools` (code; add/replace, registers last), `disable_tools` (data; skip built-ins), `enabled_tools` (data; allowlist — `None`=off, any iterable incl. `set()`=on).
- **Runtime:** `add_tool` / `remove_tool` between turns; visible next call; stricter clash semantics than the raw registry.
- **Scope:** `disable_tools` only skips built-ins; `enabled_tools` prunes built-in / agent-path tools while keeping `extra_tools`, MCP, and plan-only tools. Runtime `remove_tool()` can remove built-in, extra, or agent tools, but not MCP or plan-only tools.
- **Capability binding** comes free through the contract APIs; reaching into `agent.tools.register(...)` skips it.
- **Not a security boundary** — schema reduction, not authorization. That's the [PermissionEngine](./4-permissions).

→ Design records: [`host-tool-injection.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/host-tool-injection.md) · [`host-tool-allowlist.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/host-tool-allowlist.md) · [`runtime-tool-injection.md`](https://github.com/jin-bo/agentao/blob/main/docs/design/runtime-tool-injection.md). Contract surface: [`docs/api/host.md`](https://github.com/jin-bo/agentao/blob/main/docs/api/host.md).

→ Next stop: [Part 6 · Security & Production Deployment](/en/part-6/)
