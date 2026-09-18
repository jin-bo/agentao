# 0.5.0 back-compat removal checklist

**Status:** **implemented 2026-09-18**, authorized the same day; unreleased, targeting
0.5.0. Inventory measured against `main@2f51570` on 2026-09-18; every row below was
read in the tree, not recalled from the deprecation notes. What implementing it found
that three review rounds had not is at the end — see *Implementation record*.

**Two sequencing rules, both load-bearing.**

1. **0.4.26 ships first** — *done: released 2026-09-18.* It already carried a user-facing fix (#283 — a 429 for
   exhausted quota no longer retries four times). Switching the version now is the
   difference between "upgrade for the fix" and "accept a breaking release for the fix".
2. **`chore: open 0.5.0.dev0` lands in the same batch as the removals, never alone.**
   A lone version bump leaves `__version__` claiming 0.5.0 while the shims are still
   present and their own `DeprecationWarning`s still say "will be removed in 0.5.0" —
   the tree would contradict itself, and `grep 0.5.0` would stop being a usable
   worklist. Everything in §A–§F goes in with the number.

**Read §C, §D and §E before estimating.** Three rows that look like deletions are
migrations with live callers today: the compaction coordinator calls two of §E's
delegations, the CLI reads three of §D's private views, and §C's `thinking_callback`
silently controls whether a prompt section is emitted at all.

---

## A. `agentao/harness/` — the deprecated alias package

Renamed to `agentao.host` in 0.4.2. Delete all seven files (**158 lines**). Each file
is a star-import re-export plus a handful of old-name aliases:

| File | Lines | Old names it keeps alive |
|---|---|---|
| `__init__.py` | 66 | `HarnessEvent`, `export_harness_event_json_schema`, `export_harness_acp_json_schema`; emits the `DeprecationWarning` |
| `events.py` | 4 | re-export only |
| `models.py` | 13 | `HarnessEvent` |
| `projection.py` | 24 | `HarnessToolEmitter`, `HarnessPermissionEmitter`, `HarnessSubagentEmitter` |
| `protocols.py` | 4 | re-export only |
| `replay_projection.py` | 27 | `HarnessReplaySink`, `harness_event_to_replay_*`, `replay_payload_to_harness_event` |
| `schema.py` | 20 | `export_harness_*` |

`harness/__init__.py:55-57` states the intent plainly: the aliases "ship and die
together" with the shim.

Then delete the paragraph in `agentao/host/__init__.py:30-35` that documents the alias.

## B. `agentao/session.py` — a shim **and** a coupled tightening

`agentao/session.py` (**105 lines**) is not a docstring note; it is a deprecated module
that re-exports `agentao.embedding.sessions` and warns on import (`:38-44`).

It exists for one reason beyond the import path: it supplies the implicit
`Path.cwd()` fallback for `project_root` before delegating. `embedding/sessions.py`
keeps `project_root` optional **only** for this migration window, and says so at two
sites — `:131` (`save_session`) and `:450` (`load_session`): *"Optional during the
0.4.x migration window; will become required in 0.5.0."*

So this is **one coupled change, not two**:

**Identify the entry points by their signature, not by the note.** Only two carry the
migration note; **nine** functions in `embedding/sessions.py` take
`project_root: Optional[Path] = None`, and the cwd fallback lives in exactly one of them:

| Line | Function | Has the note? |
|---|---|---|
| `:52` | `_session_dir` — **`root = project_root if ... else Path.cwd()`, the only fallback** | no |
| `:124` | `save_session` | yes |
| `:177` | `persist_agent_session` | **no** |
| `:341` | `_resolve_session_file` (private) | no |
| `:388` | `load_session_record` | **no** |
| `:440` | `load_session` | yes |
| `:464` | `list_sessions` | no |
| `:521` | `delete_session` | no |
| `:554` | `delete_all_sessions` | no |

- [x] delete `agentao/session.py` (**105 lines**)
- [x] **delete the fallback in `_session_dir`'s body**, not just its parameter default:
      `:54` is `root = project_root if project_root is not None else Path.cwd()`. Dropping
      the default alone leaves that `else` branch reachable, so an explicit
      `project_root=None` still resolves to cwd. Make the parameter required **and** let
      `None` fail — tightening only the public signatures moves the failure inward instead
      of removing it
- [x] make `project_root` required on all seven public entry points above, not just the
      two that documented it
- [x] audit every in-tree caller of those seven for one relying on the default
- [x] test both omitting the argument **and** passing `project_root=None` explicitly —
      the current signature accepts the latter and silently means cwd

**This is the only row that changes behaviour for a caller who is not using a
deprecated name.** It needs its own test and its own migration-note paragraph; it must
not ride the alias deletions silently.

## C. The eight `Agentao.__init__` callbacks

Five separate regions in `agentao/agent.py`, not one:

| # | Site | What |
|---|---|---|
| C1 | `:104-113` | the eight `*_callback: Optional[Callable[...]] = None` kwargs |
| C2 | `:219-223` | the "Deprecated args … scheduled for removal in 0.5.0" docstring block |
| C3 | `:301-308` | the dict that collects them for the transport resolver |
| C4 | `:781-810` | `_has_legacy` detection + the `DeprecationWarning` naming all eight |
| C5 | `:813-820` | the eight `self.<name> = callbacks[...]` attribute assignments |

Coupled, in the same batch (`runtime/tool_runner.py:74-82` says "not before"):

- [x] `runtime/tool_runner.py:74-82` — four **accepted-but-ignored** kwargs
      (`confirmation_callback`, `step_callback`, `output_callback`,
      `tool_complete_callback`), never stored, kept so an existing caller does not hit
      `TypeError`

**The sub-agent factory passes five of them, and that blocks every spawn.**
`agents/tools/_wrapper.py` constructs each sub-agent with `transport=transport` (`:997`)
**and** five callback kwargs on the next five lines (`:998-1002`):
`confirmation_callback=`, `step_callback=`, `output_callback=`,
`tool_complete_callback=`, `ask_user_callback=`. Deleting C1 makes every sub-agent spawn
raise `TypeError: unexpected keyword argument` — foreground and background alike, and
**regardless of the values**, since `None` is still an unknown kwarg once the parameter is
gone. This is in-tree first-party code, so it is a prerequisite, not a downstream
migration:

- [x] fold the foreground callbacks into the transport the factory already builds —
      `build_compat_transport(...)` at `:942` where `transport = None` today
- [x] leave the background branch's `SdkTransport(confirm_tool=lambda *_: False)`
      (`:946`) exactly as it is — that refusal is the documented background posture
- [x] pass `transport=` only, with no callback kwargs
- [x] acceptance: a foreground and a background sub-agent both spawn, emit their
      lifecycle events, and keep their confirmation behaviour (background still refuses)

**A deprecated kwarg drives a prompt section, and nothing else sets it.**
`agent.py:823` reads `callbacks["thinking_callback"] is not None` into
`_has_thinking_handler`, and `prompts/builder.py:133` gates the Reasoning Requirement
section on it. Deleting C1–C5 silently drops that section for every host —
`build_compat_transport()` does **not** preserve it, because it produces a transport and
never touches this flag. Decide the destination before deleting:

- *not taken:* either derive it from the transport (does the live transport handle reasoning
      output?) or from an explicit constructor argument
- [x] or state plainly that the conditional section is retired and the block becomes
      unconditional / removed
- [x] a before/after prompt test either way — the section's presence is observable and
      currently depends on a kwarg that is going away

**Keep `agentao/embedding/compat.py`.** Its own docstring calls it the "public
migration surface" (`:1`), `agent.py:230` points hosts at it, and `CLAUDE.md:405` names
it as *the* documented migration surface for exactly this removal. It is how a host that
cannot rewire onto `AgentEvent` still builds a transport. Only its docstring needs an
edit: `:9` currently reads "Until 0.5.0 they remain accepted on `Agentao.__init__`",
which stops being true.

## D. The four private replay views — **blocked: migrate the CLI first**

`agent.py:1072`, `:1076`, `:1080`, `:1084` expose `_replay_recorder` / `_replay_adapter` /
`_host_replay_sink` / `_replay_config` as property views over `ReplayManager`, "scheduled for removal in
0.5.0". The docstring says they are kept because "Tests and CLI code still reach for"
them — **and that is still true**:

| Reader | Line | Reads |
|---|---|---|
| `cli/replay_commands.py` | `:129` | `cli.agent._replay_config` |
| `cli/replay_commands.py` | `:220` | `cli.agent._replay_config` |
| `cli/replay_commands.py` | `:240` | `getattr(cli.agent, "_replay_recorder", None)` |

- [x] migrate those three call sites to `agent.replay_manager.config` / `.recorder`
      (with the no-manager case handled explicitly — the property's current fallback is
      what the CLI is relying on)
- [x] then delete the four properties and the banner comment at `:1066-1070`

**Do not widen this row.** The comment at `agent.py:1047-1058` is explicit that the
*public* replay methods (`start_replay` / `end_replay` / `reload_replay_config`) are
LIVE API called by `cli/session.py`, `cli/commands/sessions.py`,
`cli/replay_commands.py`, `acp/session_new.py` and `acp/session_load.py`, and are **not**
slated for removal. Only the private views go.

## E. Replay observability delegations — **the compaction engine calls two of them**

Three delegations onto `agentao.replay.observability`, **not four, and not contiguous**:
`_latest_session_summary_id` (`agent.py:1143`), `_emit_context_compressed` (`:1196`),
`_emit_session_summary_if_new` (`:1219`).

**The banner comment at `:1137-1141` is wrong, and believing it breaks the runtime.** It
says the delegations "remain for tests that patch them on the agent". Two of them are
live calls from the compaction coordinator:

| Caller | Line | Calls |
|---|---|---|
| `compaction/coordinator.py` | `:238` | `agent._emit_session_summary_if_new(...)` |
| `compaction/coordinator.py` | `:719` | `agent._emit_context_compressed(...)` |

Deleting the methods first makes every **successful** compaction raise `AttributeError`
in its settle path — the failure would not appear until a session was long enough to
compact.

- [x] migrate `coordinator.py:238` and `:719` to call
      `agentao.replay.observability` directly (the same way `runtime/chat_loop` already
      imports it)
- [x] then migrate the tests that patch these on the agent
- [x] then delete the three delegations and correct the banner
- [x] a test that a **successful full compaction that produced a new summary** still
      emits both events after the migration. Scope it that way: `_emit_session_summary_if_new`
      is conditional by name, so a microcompaction or a run with no new summary must not
      be asserted to emit `SESSION_SUMMARY_WRITTEN`

## F. Two traps

- **`output_callback` is two different things.** The deprecated constructor kwarg
  (§C) is unrelated to `Tool.output_callback` (`tools/base.py:29`), which is live,
  rebound per call by `runtime/tool_executor.py:417,462` and cleared for sub-agent
  copies at `agents/tools/_wrapper.py:291`. A name-based sweep across `agentao/` will
  break tool output streaming. Same caution for `confirmation_callback` /
  `step_callback` / `tool_complete_callback`, which appear in both §C regions **and**
  in `runtime/tool_runner.py`'s live signature area.
- **`agentao/tool_runner.py` (24 lines) is a different shim** — the old module path for
  `agentao.runtime.tool_runner`. It is **not** marked for 0.5.0 and is out of scope
  here. It matters only for §H.

## G. Tests — at least eight files, in three categories

A `*_callback=` grep in `tests/` returns three different things, and only one of them is
out of scope. Sort before touching anything.

**(a) Deprecated names / kwargs — migrate:**

| File | Site | What |
|---|---|---|
| `tests/test_host_typing.py` | `:224-256` | asserts `agentao.harness` warns and re-exports each `agentao.host` name — delete, keep the `agentao.host` assertions |
| `tests/test_session.py` | `:23` | imports from `agentao.session` — repoint at `agentao.embedding.sessions`, pass `project_root` (§B) |
| `tests/test_tool_confirmation.py` | `:50`, `:84` | `Agentao(confirmation_callback=...)`, and reads the old attribute back |
| `tests/test_reliability_prompt.py` | `:12`, `:18`, `:83`, `:92`, `:151` | `Agentao(thinking_callback=...)` — these are exactly the §C prompt-section tests |
| `tests/test_system_prompt_sections.py` | `:19`, `:25` | `Agentao(thinking_callback=...)` — same |

**(b) Replay private views (§D) — migrate with the CLI:**

| File | Site |
|---|---|
| `tests/test_replay.py` | `:575`, `:588`, `:618-619`, `:671-672` |
| `tests/test_host_to_replay_projection.py` | `:358`, `:373` (`_host_replay_sink`) |
| `tests/test_agent_subsystems_optional.py` | `:60` (`_replay_config`) |

**(c) Keep, do not touch — these are not the deprecated constructor kwargs:**

| File | Site | Why it stays |
|---|---|---|
| `tests/test_transport.py` | `:173` | `build_compat_transport(step_callback=...)` — that surface survives 0.5.0 (§C) |
| `tests/test_subagent_tool_call_id.py` | `:75` | `AgentToolWrapper(step_callback=...)` — the **wrapper's own** constructor parameter, not `Agentao`'s |
| `tests/test_subagent_tool_call_id.py` | `:89` | `build_compat_transport(...)`, same surviving surface |

Every `tool.output_callback` usage is the live tool callback (§F). The wrapper-level
comments may need a wording refresh once §C lands, but the assertions stand — and the
real thing to test there is the factory migration in §C, not these two lines.

Add:

- [x] `import agentao.harness` raises `ModuleNotFoundError`
- [x] `import agentao.session` raises `ModuleNotFoundError`
- [x] each `embedding/sessions.py` entry point without `project_root` raises `TypeError`,
      and passing `project_root=None` explicitly does too (§B)
- [x] `build_compat_transport()` still accepts all eight names after the constructor
      kwargs are gone

## H. The lint gate's rationale is built on this package

`docs/design/lint-gate.md:20-36` justifies selecting `F405` with the **8 star-import
modules** where `F821` is silently inert, and lists them. Measured today, **7 of the 8
are `agentao/harness/*`**; after §A the only one left is `agentao/tool_runner.py`.

- [x] rewrite "Why `F405` is in the list" around the surviving module, or state
      plainly that the rule is kept for the next star-import shim
- [x] update the module list in both `lint-gate.md` and `lint-gate.zh.md` (the zh twin
      carries the same list at `:29-32`)
- [x] re-check the `F401` exemption rationale, which names `agentao.harness` as its
      canonical example

The gate itself should stay selected; only its stated evidence changes.

## I. Live docs — 10 files. The other 22 are records.

- [x] `CLAUDE.md` — the `agentao/harness/` subpackage-map row, the
      `agentao.harness → agentao.host` gotcha, the "8 legacy callbacks" gotcha
- [x] `developer-guide/{en,zh}/part-2/2-constructor-reference.md`
- [x] `developer-guide/{en,zh}/part-4/7-host-contract.md`
- [x] `developer-guide/{en,zh}/appendix/a-api-reference.md`
- [x] `developer-guide/{en,zh}/part-4/3-sdk-transport.md` — documents "mixing transport
      with legacy callbacks" and, in the zh twin at `:171`, that the eight callbacks are
      "still accepted" and auto-wrapped via `build_compat_transport()`
- [x] `docs/guides/embed-for-agents.md`
- [x] new `docs/migration/0.4.x-to-0.5.0.md` **and its `.zh.md` twin** — **decided
      2026-09-18: write both.** The precedent `docs/migration/0.3.x-to-0.4.0.md` is
      en-only, but that is the gap, not the rule: CLAUDE.md requires twins under `docs/`,
      and a migration guide is the one document a reader hits precisely because something
      of theirs broke. The 0.3.x precedent is left as it is — it documents a migration
      that is already over — so `docs/migration/` will hold one twinned guide and one
      en-only historical one.

**Do not edit:** `docs/releases/v0.4.*.md`, `docs/design/*` (except `lint-gate` per
§H), `docs/migration/0.3.x-to-0.4.0.md`, and the historical `CHANGELOG.md` entries —
22 files that record what was true when written.

## J. Release mechanics

- [x] `agentao/__init__.py:10` → `0.5.0.dev0` (single source; `pyproject.toml` has
      `dynamic = ["version"]` + `[tool.hatch.version] path = "agentao/__init__.py"`)
- [x] `CHANGELOG.md` `[Unreleased]` → `_Targeting 0.5.0._` with a **Removed** heading
- [x] `uv run python -m pytest tests/` and `uv run ruff check .` (required CI check)
- [x] `uv build`, then `uv run python -m pytest -m slow` (clean-install smoke; needs a
      `dist/*.whl`, and it is also a required check)
- [x] `agentao/embedding/__init__.py:10-14` — still says the legacy `agentao.session`
      import path "remains as a deprecation shim until 0.5.0"; §B removes that path, so
      the sentence goes with it
- [x] final `grep -rn "0\.5\.0" agentao/` returns nothing but the version string

## Measured context

- **Runway:** `agentao.harness` deprecated in 0.4.2, released **2026-05-01** → 24
  releases (v0.4.2 … v0.4.25), ~4.5 months, every one emitting `DeprecationWarning`.
- **In-tree non-test importers of the aliases:** zero. Only docstrings mention them.
- **External dependents:** measured at **0** (2026-08-14).
- **Size:** 158 lines of alias package + 105 lines of `session.py` + 8 kwargs across 5
  regions + 4 properties + **3** delegations + 4 ignored kwargs in `tool_runner`.
- **The work is not the deletion — and three of these rows are runtime migrations, not
  removals.** §E must move two live `compaction/coordinator.py` calls first (`:238`,
  `:719`); §D must move three live `cli/replay_commands.py` reads first; §C must decide
  where `_has_thinking_handler` comes from or a prompt section silently disappears.
  Beyond those: §B (a coupled API tightening across nine signatures), §H (a lint-gate
  rationale that loses its evidence), §G (test files in three categories) and §I
  (ten live docs plus a migration guide).
- **Three rounds of review, and each one found the same shape of defect**: a row that
  reads as a deletion but has a live caller. §E's banner claims its delegations exist only
  for tests while `compaction/coordinator.py` calls two of them; §C's `thinking_callback`
  silently gates a prompt section; §C's sub-agent factory passes five of the kwargs and
  would `TypeError` on every spawn. §B's migration note appears on two of the nine
  `project_root` signatures.
- **And the search that finds them widens each time.** Reading the deprecation notes finds
  nothing — they are what is wrong. Grepping for *callers of the symbol* found §E and §B.
  The sub-agent factory needed a different question: **who constructs `Agentao`** — the
  kwargs are not referenced by name anywhere near the spawn path, they are just passed. For
  a constructor-surface removal, grep the constructor's call sites, not only its parameter
  names.

## Implementation record (2026-09-18)

Implemented in one PR, as rule 2 requires, in three commits: the caller
migrations (§D, §E) first and on their own, then the constructor (§C), then the
deletions with the version (§A, §B, §J). Line numbers above are as measured at
`main@2f51570` and are left as written; four PRs landed in between, so they
were re-grepped before each edit rather than trusted.

**Decisions this inventory left open, and how they were made.**

- **§C — the Reasoning Requirement section is retired**, not re-derived and not
  made unconditional. The deciding evidence is in git, not in the code:
  `bcd3909` (2026-04-04) did derive the flag from "a transport is attached",
  and `af67cbc` narrowed it back to the kwarg **the same day** — "transport
  presence alone doesn't imply a thinking UI is wired up". Since then no
  first-party surface (CLI, ACP, `agentao run`) has rendered the section, and
  neither has a host that followed the migration advice, since
  `build_compat_transport` never touched the flag. Making it unconditional
  would have changed the prompt of every host that never had it; a new
  constructor argument would have been new API in a removal release, for a
  measured zero consumers. The text is preserved in the migration guide §7.
- **§B — the signature split.** `project_root` is keyword-only where it follows
  optional parameters (four functions) and a plain required parameter where it
  does not (three). One rule would have been simpler to state, but
  keyword-only everywhere breaks `list_sessions(root)` for a caller who was
  already doing the right thing. `None` is refused in `_session_dir` with a
  message that names the parameter.

**What the inventory did not have — the fourth round, found by doing it.**

- **Deleting parameters from the middle of a signature re-binds positional
  callers silently.** The eight callbacks were interleaved with
  `max_context_tokens` / `permission_engine` / `transport` / `plan_session`.
  §C lists five regions to delete and none of them is "the parameters that
  were *behind* these". Everything after `max_tokens` is keyword-only now.
  The same shape as the three review rounds — a row that reads as a deletion —
  but found by a different question again: not "who calls this" or "who
  constructs this" but **"what moves when this goes"**.
- `tests/test_llm_client_extra_body.py` pinned `pos[5] ==
  "confirmation_callback"` — a ninth test file, outside §G's three categories
  because it names the kwarg as a string, not as a keyword.
- §E's "tests that patch these on the agent" were eight files, six of them
  `SimpleNamespace` fakes carrying the old method names. Those now emit through
  the fake's transport, so the real emitters run; one of them had been
  asserting the *kwarg* names of `_emit_context_compressed` under a docstring
  that claimed to pin the wire payload's keys.
- §D: `acp/models.py` was the one in-tree caller passing `project_root`
  positionally, and its value can be `None`.
- §I's ten live docs were not all of them — also `cli/3-permissions-modes`,
  `part-5/6-system-prompt` (the prompt diagram numbers its blocks),
  `part-6/7-resource-concurrency`, each with a zh twin, plus
  `docs/guides/tool-confirmation.md` and the `pyproject.toml` comment that
  repeats §H's rationale.
- §H's third item found nothing to change: the `F401` rationale names
  `HostEvent` and `agentao.host`, not the alias package.
- The 0.4.5 CHANGELOG also promised "six `Agentao.replay_*` facade methods plus
  the `replay_config=` constructor kwarg" for 0.5.0. §D's "do not widen this
  row" already overrides that; the migration guide and the CHANGELOG now say so
  to a reader who planned around the old note.
- A source checkout keeps `agentao/harness/__pycache__`, which Python imports
  as an empty namespace package, so the removal test asserts `ImportError` on
  a *from*-import rather than `ModuleNotFoundError` on the package.
- §J's last box, read literally, fails: `grep 0.5.0 agentao/` returns eleven
  lines besides the version. All are past tense ("removed in 0.5.0"). The box's
  intent — no promise left standing — holds.

`.zh.md` twin: `docs/design/0-5-0-removal-checklist.zh.md`.
