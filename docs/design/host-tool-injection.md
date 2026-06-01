# Host tool injection: `extra_tools` / `disable_tools` (v1)

**Status:** **v1 landed** (`extra_tools` + `disable_tools` + `WebSearchTool(backend/api_key)`, see §11; tests in `tests/test_host_tool_injection.py`). `tool_options` / settings.json still deferred, see §10.
**Audience:** agentao maintainers building a declarative host tool-injection surface; reviewers of the follow-up PR.
**Companions:**
- `docs/design/host-tool-injection.zh.md` — Chinese version
- `docs/design/pi-mono-tools-review.md` / `.zh.md` — where the idea was surfaced (pi-mono tool-design comparison)
- `docs/design/embedded-host-contract.md` — the host-contract stability boundary (where this design belongs)
- `agentao/tooling/registry.py` — `register_builtin_tools`, the main change site
- `agentao/tools/base.py` — `Tool` / `AsyncToolBase` / `ToolRegistry.register`

---

## 1. Problem: agentao has no host tool-injection surface

agentao ships ~19 built-in tools, but **whether a given tool exists** is controlled by **five unrelated mechanisms**:

- `[web]` extra detection (`registry.py:57`; no `bs4` → web tools not registered)
- `bg_store` opt-in (`registry.py:74`; no store → bg-agent tools not registered)
- plan mode (`base.py:242`; `plan_*` enter the schema only in plan mode)
- agent registration path (`_register_agent_tools`; `codebase_investigator` / `cli_help`)
- MCP runtime discovery (`mcp_{server}_{tool}`)

**None of them is a host injection surface.** A host has no first-class way to do the three most basic things:

1. **Add** a custom tool
2. **Replace** a built-in's implementation (e.g. swap `web_search` for an in-house retriever)
3. **Remove** a built-in (e.g. drop `run_shell_command` in a read-only deployment)

Today the only option is to poke the runtime registry after construction via `agent.tools.register(...)` — it works, but it mutates runtime internals directly and is **not part of the stability-guaranteed `agentao.host` contract**.

There is a fourth, related gap: **configuring a built-in tool's behavior**. `WebSearchTool.__init__` (`web.py:279`) reads `os.getenv("BOCHA_API_KEY")` at construction — process-global — so two same-process `Agentao` instances cannot use different search backends. This contradicts the invariant stated in `agent.py`'s docstring / CLAUDE.md that "two same-process instances with different `working_directory` can coexist." The v1 fix is in §7: give built-in tools constructor args and pass pre-configured instances via `extra_tools`, **without introducing an extra config layer**.

## 2. Scope decision: v1 does exactly two things

**The need comes from agentao's own embedded-host posture**: a host needs a stable API to **add/replace tools** and **hide inapplicable built-ins**, while injected tools automatically get capability binding (`working_directory` / `filesystem` / `shell`) — rather than reaching into `agent.tools.register(...)` after construction (runtime internals, off-contract). This is the agentao gap described in §1, independent of any external framework.

> Background: pi-mono's `createTool` / preset / bare-`AgentTool`-override trio (see `pi-mono-tools-review`) is where the idea was surfaced, but it is background only. v1 scope is decided by the agentao gap in §1, **not by parity with pi-mono**.

**v1 ships only `extra_tools` + `disable_tools`:**

| Host need | v1 mechanism | Form |
|---|---|---|
| Add / replace a tool | `extra_tools` (same name → replaces) | Code (instance) |
| Hide an inapplicable built-in | `disable_tools={...}` | Pure data (names) |
| Configure a built-in's behavior | **no dedicated mechanism** → `extra_tools=[WebSearchTool(api_key=...)]` | Code (instance) |

**Why `tool_options` is not in v1 (see §10):**
- Configuring a built-in is satisfied in v1 by passing a **pre-constructed, configured instance** via `extra_tools` — provided built-in classes accept constructor args (§7), which they should anyway.
- `tool_options` + settings.json + env placeholders + unset rules introduce a semi-public kwargs contract, settings fields, and loader behavior differences — too broad for v1 host injection. Build it when a real "CLI user wants to configure a built-in" need appears, and start from one concrete tool (gap≠need).

**v1 also explicitly does not:**
- Copy pi-mono's per-tool `operations?` capability DI — agentao already has a single `FileSystem` / `ShellExecutor` Protocol (`capabilities/`); one object to redirect all IO uniformly is the better answer.
- Load `extra_tools` from JSON — tools are implementations and cannot be serialized.

## 3. Constructor signature

Slot into `agent.py`'s existing embedded-injection kwargs block (near `extra_mcp_servers`):

```python
def __init__(
    self,
    ...,
    *,
    working_directory: Path,
    extra_mcp_servers: Optional[Dict[str, Dict[str, Any]]] = None,
    # ── Host tool injection (NEW) ─────────────────────────────────
    extra_tools: Optional[Sequence["RegistrableTool"]] = None,
    disable_tools: Optional[Iterable[str]] = None,
    ...,
):
    ...
    self._extra_tools = list(extra_tools or ())
    self._disable_tools = frozenset(disable_tools or ())
    self._validate_tool_injection()   # extra: unique names, no `mcp_`; disable: must be in static built-in name set
```

- **`extra_tools`** — a list of already-constructed `Tool` / `AsyncToolBase` instances. Construction-time validation rejects duplicate names and the `mcp_` prefix (MCP namespace is reserved; MCP replacement goes through `mcp_manager=` etc., see §4).
- **`disable_tools`** — a set of built-in tool names to skip during registration. Construction-time validation: **every name must belong to the static built-in name set, else `ValueError`** — pure typo protection (`{"web_serach"}` fails immediately instead of silently no-op'ing). Validation is against **static registration eligibility** (all possible built-in names), **not** live availability — so `disable_tools={"web_search"}` is valid (a no-op) even without `[web]` installed, the same "eligibility ≠ dependency availability" principle as §10 risk 3. Agent tools are out of `disable_tools` scope (separate registration path).

## 4. Semantics and precedence

Two rules, no ambiguity:

1. **`disable_tools` only skips built-in registration** — it is **not** a global denylist, does not affect `extra_tools`, does not affect MCP. It is also **not a security boundary**: security/authorization stays with the permission engine; the value of `disable_tools` is reducing the schema and keeping the model from attempting inapplicable built-in capabilities.
2. **`extra_tools` registers after built-ins and agent tools** — a separate pass (see §5b), so it can override same-named tools among **built-ins and agent tools**. It **does not enter the `mcp_` namespace**: extra tool names are forbidden the `mcp_` prefix (§3 validation), so by construction they cannot and will not override MCP tools. **Replacing MCP tools goes through the existing host injection surfaces** (`extra_mcp_servers=` / `mcp_manager=` / `mcp_registry=`), not `extra_tools` — keeping the boundary clean.

The combinations follow with **no warn-then-continue conflict arbitration needed**:

| Host passes | Result |
|---|---|
| `extra_tools` name doesn't collide with a registered built-in/agent tool | Added |
| `extra_tools` name == a built-in / agent tool | That tool registers first; extra overrides it in the final pass (explicit replace) |
| `extra_tools` name carries an `mcp_` prefix | §3 validation **rejects** it (`ValueError`) — MCP namespace reserved |
| `disable_tools={"web_search"}` | Skip built-in `web_search` |
| `disable_tools={"web_search"}` + `extra_tools=[a tool named web_search]` | Built-in skipped, extra registered — **net effect is the host's web_search is active**. A legitimate, meaningful combination (drop the built-in, supply your own); no error |
| `disable_tools={"web_serach"}` (typo / unknown name) | Construction-time **`ValueError`** (see §3 validation) — typo protection, not silent |

## 5. `register_builtin_tools` change: filter built-ins only

`disable_tools` filters the built-in list. **`extra_tools` is not registered here** (see §5b). No `tool_options` injection, no options-eligible / dependency-wired classification:

```python
def register_builtin_tools(agent: "Agentao") -> None:
    disabled = agent._disable_tools

    tools = [ReadFileTool(), WriteFileTool(), EditTool(), ReadFolderTool(),
             FindFilesTool(), SearchTextTool(), ShellTool()]
    if importlib.util.find_spec("bs4") is not None:
        tools += [WebFetchTool(), WebSearchTool()]
    tools += [agent.memory_tool, ActivateSkillTool(agent.skill_manager),
              AskUserTool(...), agent.todo_tool]
    if agent.bg_store is not None:
        tools += [CheckBackgroundAgentTool(bg_store=agent.bg_store),
                  CancelBackgroundAgentTool(bg_store=agent.bg_store)]

    # disable_tools: skip built-in registration only
    tools = [t for t in tools if t.name not in disabled]

    for tool in tools:
        _bind_and_register(agent, tool)   # shared binding helper, see §5b
```

## 5b. `register_extra_tools`: the genuinely-last pass

`extra_tools` must register after **all** built-in, MCP, and agent tools, otherwise it isn't "last wins." The current registration order (`agent.py`) is:

```
355  self.tools = ToolRegistry()
356  self._register_tools()        # register_builtin_tools  →  built-ins
366  self.mcp_manager = ...        # init_mcp / register_mcp_tools  →  mcp_{server}_{tool}
381  self._register_agent_tools()  # codebase_investigator / cli_help / bg-agent
387  self.tool_runner = ToolRunner(tools=self.tools, ...)
```

Insert the extra-tools registration **after 381, before 387** — add `register_extra_tools(agent)` and call it from `agent.py`:

```python
# agent.py, after _register_agent_tools(), before constructing ToolRunner:
self._register_agent_tools()
register_extra_tools(self)        # NEW — host extras register genuinely last
self.tool_runner = ToolRunner(tools=self.tools, ...)
```

```python
# tooling/registry.py
def _bind_and_register(agent, tool, *, replace=False):
    """Shared by built-ins and extras: bind capabilities, then register."""
    tool.working_directory = agent._working_directory
    tool.filesystem = agent.filesystem    # same capability binding as built-ins
    tool.shell = agent.shell              # (was registry.py:78-83)
    agent.tools.register(tool, replace=replace)

def register_extra_tools(agent: "Agentao") -> None:
    for tool in agent._extra_tools:
        # Decide override against the live registry. It now holds built-in +
        # MCP + agent tools, but extra names are forbidden the `mcp_` prefix
        # (§3), so in practice they only collide with built-in/agent tools.
        replace = tool.name in agent.tools.tools
        _bind_and_register(agent, tool, replace=replace)
```

Two key points:

1. **Extras go through exactly the same capability binding** (`working_directory` / `filesystem` / `shell`) — injected tools automatically inherit the ACP session's cwd isolation and the host's FS/shell redirection, and never become "bare" tools.
2. **Placing the pass after `_register_agent_tools()` is specifically to override agent tools** (codebase_investigator / cli_help / bg-agent). `replace=` checks the live registry, so the implementation needs no per-source special-casing; but because the `mcp_` prefix is forbidden, what can actually be overridden is built-in and agent tools only, **not MCP** (MCP replacement goes through `mcp_manager=` etc.).

## 6. Add an explicit override to `ToolRegistry.register`

To support §5's `replace=`, add a parameter to `register` (`tools/base.py:209`). Semantics: **an explicit override (host deliberately replacing) is silent; a non-explicit collision (MCP / plugin accidental same-name) still warns** — keeping the existing last-write-wins behavior, only giving deliberate replacement a warn-free path:

```python
def register(self, tool: RegistrableTool, *, replace: bool = False) -> None:
    if tool.name in self.tools and not replace:
        # Non-explicit collision: keep historical behavior — overwrite and warn
        # (visible when MCP/plugins accidentally collide).
        _logger.warning(
            "Tool '%s' already registered; overwriting with %s",
            tool.name, type(tool).__name__)
    self.tools[tool.name] = tool
```

Do not turn "non-explicit collision" into an outright `raise`: that would affect the MCP / plugin collision path, where the risk outweighs the benefit.

## 7. Give built-in tools constructor args (the v1 path to configuring built-ins)

For `extra_tools=[WebSearchTool(api_key=...)]` to configure a built-in, the built-in class must accept constructor args while **keeping a zero-arg default** (backward compatible). This also fixes the §1 multi-instance crack — explicit arg > env, with env as fallback:

```python
class WebSearchTool(Tool):
    def __init__(self, *, backend: str | None = None, api_key: str | None = None):
        self._bocha_api_key = api_key or os.getenv("BOCHA_API_KEY")
        self._provider = backend or ("bocha" if self._bocha_api_key else "duckduckgo")
```

**v1 commits to constructor args for `WebSearchTool` only:**

| Tool | v1 kwargs | Env replaced | Why required in v1 |
|---|---|---|---|
| `web_search` | `backend`, `api_key` | `BOCHA_API_KEY` | The §1 multi-instance env leak — a **demonstrated** defect, not "might be useful" |

**`web_fetch`'s `fallback`: same-class multi-instance crack** (`WebFetchTool.__init__` likewise reads the process-global `AGENTAO_WEB_FETCH_FALLBACK`, `web.py:139/30`), **but its env is a non-secret, deployment-level mode switch (none/jina/crawl4ai) with low per-instance-variance priority, so it is deferred in v1.** Detailed rationale left to a follow-up issue/ADR.

**The genuinely "might be useful" tier**: `read/write`'s `max_bytes/max_lines`, shell's `timeout/prefix` — no evidence agentao needs them now; add them one tool at a time when a concrete need appears.

**Contract-burden note (and why it is *not* an argument against `tool_options`)**: configuring a built-in **necessarily** makes the depended-upon kwarg names a **semi-public contract** subject to deprecation on rename — **whether via `extra_tools` reusing the built-in class or, later, via `tool_options`**. This burden is not a new cost introduced by `tool_options`; it is the cost of "letting a host configure built-ins" at all:
- v1 bears it via `extra_tools`, at the cost of being **wide-open** — the whole constructor signature is exposed and the class must be importable;
- later, `tool_options` is the mechanism that **narrows the same contract into an explicit option schema** (agentao keeps construction control, exposes only the option keys it commits to), see §10.

v1 commits only to `WebSearchTool`'s `api_key`/`backend` precisely to keep this wide-open contract minimal — avoiding a PR that silently expands the public contract surface.

## 8. End-to-end usage

```python
from agentao import Agentao

# Configure a built-in (the secret is held by host code, never in any config file)
agent = Agentao(
    working_directory=wd,
    extra_tools=[WebSearchTool(backend="bocha", api_key=key)],  # same name → replaces built-in
)

# Remove the built-in search/fetch and add a separate in-house retriever
# (different name — this is an addition, NOT a replacement of web_search semantics)
agent = Agentao(
    working_directory=wd,
    disable_tools={"web_search", "web_fetch"},
    extra_tools=[MyRetrievalTool()],
)
```

## 9. Implementation prerequisites and out-of-scope notes

- **`RegistrableTool` entering the contract = re-export only, no abstraction layer**: export the existing `Tool` / `AsyncToolBase` / `RegistrableTool` from `agentao.host`. Do **not** create a new host-tool protocol / adapter / wrapper — that is a separate design task, unrelated to this one. Implementation: a direct export from `agentao.host.__init__`; do not touch `host/protocols.py` or add a protocol.
- **Concentrated change surface**: `Agentao.__init__` (accept `extra_tools` / `disable_tools` + construction-time validation), `register_builtin_tools` (add `disable_tools` filtering), new `register_extra_tools` called after `agent.py`'s `_register_agent_tools()`, `ToolRegistry.register(replace=...)`, plus `WebSearchTool` constructor args. Simple route.
- **Static built-in name set = a constant/small function in registry.py**: the "all possible built-in names" needed for `disable_tools` validation should be a simple constant in `registry.py` (or a small function derived from the factory list) — **do not introduce a tool-metadata registry**.
- **Registration paths left untouched**: plan-only (`plan_*`) and agent-tool (`codebase_investigator` / `cli_help`) registration paths are orthogonal to this design and unchanged in v1.

## 10. Future need (not in v1): `tool_options` + settings.json

`tool_options: Dict[str, Dict[str, Any]]` (`name → kwargs`) has exactly one irreplaceable value: **it can live in JSON** (letting non-programmatic CLI users tune built-ins via settings.json). Its other increments are already covered by `extra_tools`. Not in v1.

**Trigger gates (either one → reopen for evaluation via a dedicated ADR):**
- **A (JSON-driven)**: a real "non-programmatic / CLI host wants to configure built-ins via settings.json" need — then `tool_options` ships **with JSON together**.
- **B (scale-driven)**: configurable built-ins reach ~3+, where a uniform map beats N constructor calls.

> Evaluated and rejected: "`tool_options` but without JSON." It cuts the one irreplaceable increment (JSON) while keeping the medium cost — ≈ a stringly-typed `extra_tools`. If JSON's secret/versioning risk is the worry, the right answer is to not ship `tool_options` at all (the status quo), not a stripped version.

**Three risk classes the ADR must cover (details left to the ADR):** settings-schema contract surface, env-secret expansion, unknown-vs-missing-dependency validation. The one easy-to-trip, non-obvious point worth recording now: **when an env placeholder doesn't resolve, warn + drop the key — do not adopt MCP's silent `""`** (otherwise an unset `$BOCHA_API_KEY` would silently fall web_search back to duckduckgo).

## 11. Quick reference (v1)

| Dimension | `extra_tools` | `disable_tools` |
|---|---|---|
| Form | Code (instance) | Pure data (name set) |
| v1 source | in-process `Agentao(...)` API | in-process `Agentao(...)` API |
| settings.json | No (implementations can't serialize) | **No in v1** — pure data is serialization-friendly, but v1 has no settings loader and doesn't read settings.json; CLI/JSON needs come later |
| Can do | add / replace impl / configure built-in (pass a configured instance) | skip built-in registration only (not a security boundary — security is the permission engine's) |
| Registers | after built-in + agent tools (separate pass, §5b) | filters the built-in list |
| On name collision | registers last, overrides same-named **built-in and agent tools** (`replace=True` silent); **does not enter the `mcp_` namespace** — MCP replacement goes through `mcp_manager=` etc. | does not participate in collision arbitration — only decides whether a built-in exists |

(`tool_options` is the §10 future need; not delivered in v1.)
