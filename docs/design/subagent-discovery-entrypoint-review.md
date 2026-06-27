# Sub-Agent Discovery — Entry-Point Asymmetry & the Skill-vs-Plugin Conflation

**Status:** Review record. Drafted 2026-06-23 from a grep-verified read of the
agent-definition, skill, and plugin discovery paths across all three runtime
entry points (interactive CLI / `agentao run`, Python embedding via
`build_from_environment`, ACP `session/new`). **This is a gap analysis +
prioritized proposal, not an approved plan.** It exists to settle a specific
question raised about sub-agent registration — and, in doing so, to correct the
premise that question was built on.
**Audience:** Agentao maintainers; anyone embedding agentao who expects skill- or
plugin-bundled sub-agents to be visible.
**Companion:** `subagent-discovery-entrypoint-review.zh.md`.
**Related:**
- `embedding-vs-acp.md` — why ACP and `agentao run` are *frontends* over the
  embedded core, not the core contract. The "should plugin-loading live in the
  core or the frontend?" question in §5 is a direct application of that boundary.
- `host-tool-injection.md` — the host's explicit tool-injection contract
  (`extra_tools` / `disable_tools` / `enabled_tools`). The opt-in plugin-load
  proposal in §6 follows the same "host opts in explicitly, default is minimal"
  posture.
- `acp-server-conformance-review.md` — establishes ACP target-client = chat/
  automation; relevant to whether ACP sessions *should* inherit global plugins.

**Method:** read `AgentManager._load_definitions`, `SkillManager._load_skills`,
the plugin discovery/resolution chain (`embedding/plugins/`), and the three
construction paths; grep every call site of `_load_and_register_plugins`; grep
for any code that bridges a skill root to `agents/`; `find` for plugin manifests
under `skills/`. Code references anchored to `main`@`e477ad3` (2026-06-23).

---

## TL;DR

The triggering report claimed: *"a skill's bundled sub-agents (e.g.
`skills/skill-creator/agents/analyzer.md`) are discovered under the CLI but
skipped by the embedding and ACP entry points."*

**The headline premise is false.** Skill-bundled `agents/` subdirectories are
bridged to **nothing** — not the CLI, not embedding, not ACP. `agents/`
auto-discovery is a **plugin** feature; a bundled *skill* is not a *plugin* root,
and the plugin discovery scan never looks inside `skills/`. So `skill-creator`'s
three agent files are invisible to **every** entry point, including the
interactive CLI. The report conflated *skill* with *plugin*.

What **is** true, once the conflation is removed, is a smaller and different set
of facts — two independent issues plus one absent feature:

1. **Plugin loading is CLI-only** (real). `build_from_environment` and ACP
   `session/new` load **zero** plugins — not just plugin-agents, but plugin
   *skills, MCP servers, and hooks* too.
2. **`AgentManager` has no global layer** (real, minor). It scans builtin +
   project `.agentao/agents/` only; unlike `SkillManager`'s three layers, there
   is no `~/.agentao/agents/`.
3. **"A skill carries sub-agents" does not exist as wired** (absent feature, not
   a bug). Nothing maps a skill's `agents/` into `AgentManager`.

Whether (1) is a *bug* or an *intentional isolation boundary* is the maintainer's
call — and there is a real trust argument that CLI-only is deliberate (§5). The
recommendation (§6) is to fix (2) cheaply, treat (1) as a **default-off opt-in**
to preserve host isolation, and treat (3) as a separate feature proposal if there
is demand.

> **Reverse-review correction (2026-06-23, adversarial re-check).** This doc was
> re-verified against its own claims. The central thesis (§3 — a skill's
> `agents/` is bridged to *nothing*, **including the CLI**) **survived and
> strengthened**: `_plugin_inline_dirs` is populated *only* from the CLI
> `--plugin-dir` flags (`cli/entrypoints.py:371-373`), never auto-seeded with
> `skills/*`, so the CLI does not load skill directories as inline plugins.
> Three *secondary* framings were over-stated and are corrected in place below:
> 1. **Issue B is *not* boundary-free** (corrects §4.2 / §6-P1).
>    `build_from_environment` already gates even *builtin* agents behind a
>    default-**off** settings flag (`_builtin_agents_enabled`,
>    `factory.py:48,238`; `enable_builtin_agents` defaults `False`,
>    `agent.py:102`). Always-on scanning of user-global `~/.agentao/agents/`
>    would contradict that conservative posture — yet `SkillManager`'s global
>    layer *is* always-on. So B carries a real, if smaller, judgment call (*which
>    precedent to follow*), not "no boundary question."
> 2. **ACP already has a plugin-load escape hatch** (corrects §5 / §6-P2).
>    `session/new` and `session/load` take an injected `agent_factory`
>    (`acp/session_new.py:304`, `acp/session_load.py:122`). The asymmetry is only
>    in the *default* factory; a host can already supply one that loads plugins.
>    This weakens "ACP is a gap" and gives P2 a natural seam.
> 3. **P2's settings-gated default-off pattern is not novel** (refines §6-P2). It
>    is exactly how `enable_builtin_agents` already works in the same function —
>    cite it as precedent, not invention. The non-trivial parts of the extraction
>    are decoupling `_plugin_inline_dirs` into an `inline_dirs` parameter **and**
>    binding `PluginManager` to the factory's frozen `cwd` (both detailed in
>    §6-P2 step 1).

---

## 1. The triggering report (premise, verbatim)

> *Skills carrying sub-agent definitions in their `agents/` subdir (e.g.
> `skills/skill-creator/agents/analyzer.md`) are only discovered and registered
> under the CLI entry point. The other two entry points — Python embedding
> (`build_from_environment`) and ACP (`session/new`) — skip plugin/skill-agent
> loading, making skill-defined sub-agents invisible to them.*

The report then cited four mechanisms: `AgentManager` scanning only two dirs;
`build_from_environment` calling no plugin loader; ACP delegating to it; and
`_load_and_register_plugins` being CLI-only.

The four *mechanisms* are mostly accurate (§2). The *premise they were marshalled
to support* — "skill sub-agents are visible in the CLI" — is not (§3).

## 2. What is verified true

| Claim | Verdict | Evidence (`main`@`e477ad3`) |
|---|---|---|
| `_load_and_register_plugins` is CLI-only | ✅ true | Defined `cli/subcommands.py:283`; non-test call sites only `cli/app.py:112` and `cli/run.py:543`. |
| `build_from_environment` loads no plugins | ✅ true | `grep "plugin" agentao/embedding/factory.py` → no match. Constructs `Agentao(**kwargs)` and returns. |
| ACP `session/new` loads no plugins | ✅ true | `acp/session_new.py:158` — `default_agent_factory` returns `build_from_environment(working_directory=cwd, **overrides)` directly; no plugin step. |
| `AgentManager._load_definitions` scans only two dirs | ✅ true | `agents/manager.py:31-37` — builtin `definitions/` (gated on `include_builtin_agents`) + project `.agentao/agents/`. No global layer. |
| `SkillManager` scans three layers | ✅ true | `skills/manager.py` — global `~/.agentao/skills` (`:14`), project `.agentao/skills` (`:99`/`104`), repo `skills/` (`:100`/`105`), plus bundled `_BUNDLED_SKILLS_DIR` (`:17`). |

So the report's *directory-comparison* table (AgentManager 2 layers vs
SkillManager 3) is correct, and the *plugin-load-is-CLI-only* observation is
correct.

## 3. The false premise: a skill's `agents/` is bridged to nothing

The report's example — `skills/skill-creator/agents/analyzer.md` — drives the
whole framing. It does not survive a grep.

**(a) `agents/` discovery belongs to the *plugin* subsystem, not skills.**
The only code that joins a root to `agents/` is the plugin agent resolver:

```
embedding/plugins/resolvers/agents.py:41   default_dir = plugin.root_path / "agents"
embedding/plugins/resolvers/agents.py:85   def _scan_agents_dir(plugin_name, agents_dir): ...
```

`SkillManager` has **no** `agents/` handling at all — grep of
`agentao/skills/manager.py` for `agents` returns only the *skill* directory
constants, never an `agents/` subscan.

**(b) Plugin discovery never looks inside `skills/`.**
`PluginManager.discover_candidates` (`embedding/plugins/manager.py:91-110`) scans
exactly three sources:

```
:96   global   ~/.agentao/plugins
:100  project  <cwd>/.agentao/plugins
:104  inline   self._inline_dirs   (populated only by the CLI — entrypoints.py:373)
```

The repo's `skills/` tree is not among them.

**(c) `skill-creator` is a bundled *skill*, not a *plugin*.**
`find skills/skill-creator` shows `SKILL.md` + `agents/{analyzer,comparator,
grader}.md` and **no plugin manifest** (`agentao-plugin.json` does not exist
anywhere in the tree). It reaches the runtime via `SkillManager`'s bundled-skill
seeding (`skills/manager.py:17`, `:60`), which copies it to `~/.agentao/skills/`
— a *skills* directory, never a *plugins* directory.

**Conclusion.** For `skills/skill-creator/agents/analyzer.md` to be registered,
`skill-creator` would have to be loaded as a **plugin** (a plugin root whose
`agents/` is auto-scanned). It is not, and nothing in any entry point makes it
one. The agent files are therefore invisible to **all three** entry points,
**including the CLI** — exactly the opposite of "visible in the CLI, missing
elsewhere."

> This is the failure mode `CLAUDE.md` warns about under *"Don't intuition-audit
> architecture."* The `agents/` directory name is shared vocabulary between the
> skill and plugin subsystems, and the report assumed a bridge that the code does
> not contain.

## 4. The two real, independent issues (and one absent feature)

Dropping the conflation, three separable things remain:

### 4.1 Issue A — plugin loading is CLI-only (real)

`_load_and_register_plugins` (`cli/subcommands.py:283-372`) is not just an
agent loader. In one pass it registers the **entire plugin surface** onto the
agent:

- plugin **skills** → `agent.skill_manager.register_plugin_skills` (`:301`)
- plugin **agents** → `agent.agent_manager.register_plugin_agents` (`:321`),
  then `agent._register_agent_tools()` (`:337`)
- plugin **MCP servers** → merged + MCP manager re-init (`:347-359`)
- plugin **hooks** → `agent._plugin_hook_rules` / `tool_runner` (`:361-372`)

Because this lives in `cli/` and is called only from `cli/app.py` and
`cli/run.py`, an embedded host (and every ACP/IDE session, which routes through
`build_from_environment`) gets **none** of it. The asymmetry is real, and it is
*broader* than the report framed it — it is the whole plugin subsystem, not
sub-agents specifically.

### 4.2 Issue B — `AgentManager` lacks a global layer (real, minor)

`AgentManager._load_definitions` scans builtin + project `.agentao/agents/`
only (`agents/manager.py:31-37`). `SkillManager` scans a global
`~/.agentao/skills/` layer; the agent manager has no `~/.agentao/agents/`
equivalent. A user who drops a personal agent definition in their home config
dir — mirroring how user-global skills work — gets nothing. This is independent
of the plugin question and is the cheapest thing here to *implement* (one
`_scan_directory(user_root() / "agents")` call, ordered for the
right precedence).

> **It is cheap to implement but it is *not* boundary-free** (reverse-review
> correction). The codebase is already inconsistent about auto-loading
> ambient agent state: `SkillManager`'s global layer is always-on, but
> `build_from_environment` keeps even *builtin* agents **off by default** behind
> a settings flag (`_builtin_agents_enabled`, `factory.py:48,238`;
> `enable_builtin_agents=False`, `agent.py:102`). So "should user-global agent
> definitions be auto-registered everywhere?" is a genuine judgment call —
> *follow the always-on skills precedent, or the default-off builtin-agents
> precedent?* An agent definition is lower-risk than a plugin (it carries only a
> system prompt + tool allowlist, runs only when the LLM invokes it — closer to a
> passive skill than to a side-effecting MCP server/hook), which argues for the
> skills precedent; but it is not the no-decision change the first draft implied.

### 4.3 Absent feature — "a skill carries sub-agents"

The capability the report *assumed* exists — a skill bundling sub-agents in its
own `agents/` subdir and having them registered when the skill is active — is
**not implemented**. Building it is a *new feature* (a skill→AgentManager
bridge), not a bug fix, and it raises its own design questions (do a skill's
sub-agents register on skill *activation* or on *discovery*? are they namespaced
by skill? do they deactivate with the skill?). It should be evaluated on demand,
not folded into an "entry-point parity" fix.

> **A sharper finding** (reverse-review note, corrects the first-draft "inert
> payload" claim): the reporter's example refutes the *mechanism* twice over.
> `skill-creator`'s `agents/{analyzer,comparator,grader}.md` are **not agent
> definitions** — they carry no YAML frontmatter (`name:`/`description:`), just a
> prose H1, so `parse_frontmatter` would yield an empty name even if scanned. And
> the skill does **not want them registered**: its SKILL.md uses them as
> *reference docs it reads at runtime* ("spawn a grader subagent that **reads
> `agents/grader.md`**", "**Read `agents/comparator.md`**", "the agents/ directory
> contains instructions … **Read them when you need to spawn** the relevant
> subagent" — `SKILL.md:225,327,455`), then spawns a *generic* subagent with that
> content as instructions. So the files are neither inert nor registrable — they
> already work, as instruction payload, exactly as the upstream skill format
> intends. That makes C a **demand-gated hypothetical**: the one in-tree artifact
> that *looks* like it needs a skill→agent registration bridge does not. See
> §6-P3 for when that would change.

> **Upstream packaging corroborates this** (cross-checked against Claude Code's
> own plugin install, 2026-06-23). Claude Code distributes skill-creator as a
> *plugin* (`~/.claude/plugins/installed_plugins.json` →
> `skill-creator@claude-plugins-official`, with a `.claude-plugin/plugin.json`
> manifest) — yet its `agents/{analyzer,comparator,grader}` are **still not
> registered as sub-agents** there either. Two reasons, both structural: the
> plugin manifest declares no `agents` field, and the files sit at
> `skills/skill-creator/agents/` *inside the skill*, not at the plugin's agent
> root `<plugin>/agents/` that plugin agent-discovery scans (the same
> `plugin.root_path / "agents"` rule agentao uses, §3). Confirmed empirically:
> those names are absent from the session's available agent types. So the
> upstream **deliberately** packages them as skill assets, not plugin-agents —
> and in *both* Claude Code (where skill-creator is a plugin) and agentao (where
> it is a bundled skill), they are skill-internal reference docs, never
> registered sub-agents. The reporter's premise — "a skill's bundled agents get
> registered" — fails even by the precedent it most likely came from.

## 5. Is Issue A a bug, or an intentional isolation boundary?

This is the load-bearing judgment, and it is the maintainer's call — not
self-evidently a defect.

**The argument that CLI-only is deliberate.** Plugins are sourced primarily from
the user's **global** `~/.agentao/plugins`. Having `build_from_environment`
auto-load them would mean **every embedded host, and every ACP/IDE session,
silently inherits the end user's global agents, MCP servers, and hooks.** For an
*embedded harness* that is a trust/isolation footgun: a host application that
embeds `Agentao(...)` to do one bounded job generally does **not** want its agent
surface, tool set, and outbound MCP connections silently expanded by whatever the
machine's user happens to have installed globally. Hooks especially — a global
plugin hook firing inside an embedded host's tool pipeline is an injection
surface the host never opted into. The CLI is the one context where "load the
user's plugins" is unambiguously the user's intent, because the user *is* the
operator. So routing plugin loading through the CLI layer, and leaving the core
construction path plugin-free, is a defensible **default-deny** posture
consistent with how agentao treats other ambient inputs.

**The argument that it is a gap.** The asymmetry is **undocumented**. An embedder
reasonably expects the three entry points to be at parity, and there is no
*first-class* host-API surface today to say "yes, load my plugins." The real
smell is not the default — it is the *absence of a documented opt-in*, which
leaves the behavior looking accidental rather than chosen.

**A seam already exists, which tilts this toward "intentional, just
undocumented."** ACP `session/new` and `session/load` already accept an injected
`agent_factory` (`acp/session_new.py:304`, `acp/session_load.py:122`), and an
embedded host constructs `Agentao` itself — so *both* non-CLI paths can already
load plugins by supplying a factory / post-construction step that calls the
loader. The capability is reachable today; what is missing is a blessed, named,
documented switch rather than an ad-hoc workaround. That a deliberate injection
point exists at exactly the construction seam is itself weak evidence the
plugin-free default is a choice, not an oversight.

Per the project's standing rule (*pain judgment is the user's call*), this doc
does not declare Issue A a "real pain." It presents the trade-off and recommends
a posture that satisfies both readings.

## 6. Proposals (prioritized — maintainer's call)

**P1 — close Issue B (cheap to implement; one small boundary call to settle
first).**
Add the global layer to `AgentManager._load_definitions`:
`_scan_directory(user_root() / "agents")` — note `user_root()` is **already**
`~/.agentao`, so the path is `~/.agentao/agents`, **not** `user_root()/".agentao"
/"agents"` (that would scan `~/.agentao/.agentao/agents`). Place it to give the
right precedence vs project defs (mirror `SkillManager`'s global<project
ordering, and decide whether project overrides global as skills do), and **add a
covering test** for the override order. Self-contained; ship independently.
**Before merging, settle the one judgment call** (per the
reverse-review correction in §4.2): does user-global agent auto-loading follow
the *always-on* skills precedent or the *default-off, settings-gated*
builtin-agents precedent? Recommendation: follow skills (always-on) — an agent
*definition* is passive (system prompt + tool allowlist, runs only on LLM
invocation), unlike a plugin's side-effecting MCP/hooks — but make that an
explicit decision in the PR, not an implicit one.

**P2 — make plugin loading reachable from the core, default-off (resolves Issue
A without breaking isolation).**
1. Extract a **narrow helper** from `cli/subcommands.py` into `embedding/` —
   e.g. `load_plugins_for_agent(agent, *, cwd, inline_dirs=None)` — wrapping the
   current `_load_and_register_plugins` body. The move is mostly mechanical (the
   body already imports almost exclusively from `embedding/plugins/*`), but it
   needs **two** explicit parameterizations, not one:
   - **`inline_dirs`** — today it reads the CLI global `_plugin_inline_dirs`
     (`cli/subcommands.py:291`); pass it in (default `None`, since inline dirs
     come only from the CLI `--plugin-dir` flags).
   - **`cwd`** — `PluginManager` defaults its project-scan root to `Path.cwd()`
     (`PluginManager.__init__` → `self._cwd = _find_project_root(cwd or
     Path.cwd())`, `embedding/plugins/manager.py:79`). The CLI gets away with the
     default because cwd == working dir there, but a host calling
     `build_from_environment(working_directory=wd)` deliberately **freezes** the
     runtime to `wd`. The helper must therefore pass `cwd=agent.working_directory`
     into `PluginManager(cwd=..., inline_dirs=...)`; otherwise project plugins are
     scanned at the process cwd, breaking the frozen-cwd contract the factory
     exists to uphold.

   (`logger` is a plain module logger and moves cleanly — not a real coupling.)
2. CLI keeps calling the helper by default (unchanged behavior). Have
   `build_from_environment` accept an explicit `load_plugins: bool = False` (or
   honor a `.agentao/settings.json` flag), default **off**; when set, call
   `load_plugins_for_agent(agent, cwd=wd, ...)` after construction. **This
   default-off-settings-gated pattern already exists in the same function**:
   `enable_builtin_agents` is resolved exactly this way
   (`_builtin_agents_enabled(settings)` → default-`False` override,
   `factory.py:48,238`). Follow that precedent so the switch is idiomatic, not a
   new mechanism.
3. ACP `session/new` / `session/load` inherit the switch for free via
   `default_agent_factory` (the injected `agent_factory` seam already noted in
   §5); expose it through whatever ACP/host config surface is appropriate (gated
   by the chat/automation target decision in `acp-server-conformance-review.md`).
4. Document the switch and the default-off rationale in `host-api.md` /
   `host-tool-injection.md`, so the posture reads as *chosen*, not *accidental*.

This keeps the safe default (no silent global-plugin inheritance) while giving
embedders and ACP hosts an explicit, documented way to opt in — the same shape as
the existing host tool-injection contract.

**P3 — "skill carries sub-agents" (build only on a concrete demand signal).**
Treat §4.3 as a separate feature spec; do not bundle it into P2. **When to build
it — three triggers, none of which fire today:**
1. **A skill genuinely needs registration.** A skill ships `agents/*.md` *in
   agent-definition format* (YAML `name:`/`description:` frontmatter) and its
   author expects them to surface as invokable sub-agent tools while the skill is
   active — and reports they don't. The one in-tree example, `skill-creator`,
   explicitly does **not** meet this: its `agents/` are read-at-runtime
   instruction docs, not definitions (§4.3). So in-tree demand is **zero** right
   now.
2. **Skills become a distribution unit** (published/shared, à la plugins). Until
   then, anyone wanting *distributable* bundled agents already has a path — ship
   a **plugin**, whose `agents/` is auto-scanned (§3). P3's niche only opens if
   skill-as-distribution diverges from plugin-as-distribution.
3. **A host/user asks for it directly.** Per the demand-gated rule (gap ≠ need),
   the request is the signal — not the mere existence of unbridged `agents/`
   dirs.

**Decide the design on paper now; build later.** Settle §4.3's three questions
(activation-vs-discovery timing, skill-namespacing, lifecycle coupling) before
any code, so the feature is ready the moment a trigger fires. The natural shape
is a `SkillManager`→`AgentManager` bridge firing on skill *activation* (so
sub-agents share the skill's lifecycle) — **not** teaching `PluginManager` to
scan `skills/`, which would merge two distinct trust/lifecycle models (§7).

## 7. Non-goals / what NOT to do

- **Do not** make `build_from_environment` auto-load global plugins by default to
  "fix parity." That silently expands every embedded host's agent/MCP/hook
  surface from the machine's global config — the isolation footgun in §5.
- **Do not** teach `PluginManager` to scan `skills/` as a plugin source. Skills
  and plugins are distinct subsystems with distinct trust and lifecycle models;
  merging them to make one example work is the wrong layer.
- **Do not** describe P1 (Issue B) and P2 (Issue A) as one change. They are
  independent; P1 carries only a *small* default-loading decision of its own
  (§4.2 — which precedent the global agent layer follows) and should not be
  bound to, or held hostage by, the broader plugin-isolation decision that
  governs P2.
