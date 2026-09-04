# PowerShell support — making the shell floor dialect-aware

> **Frozen at rev 24 (2026-09-03, commit `e01293f`) and superseded by the split file set — start from `powershell-support-spec.zh.md` (spec), `powershell-support-implementation.zh.md`, `powershell-support-gates.zh.md`, `../reference/powershell-support-evidence.zh.md`, `powershell-support-review-log.zh.md` and `subagent-runtime-safety-plan.zh.md` (the former PR-0). This file is not maintained; it stays only until the split spec has had its own full security review.**

> ⚠️ **Design only. Nothing here is authorized for implementation — except that §2.12 records a
> live defect in the sub-agent path that predates this plan, is measured, and should be fixed on
> its own (§5, PR-0) whether or not PowerShell is ever built.** That exception covers PR-0's
> **engine** half, which is independently shippable; it does not cover PR-0's **MCP** half, where
> rev 20 found a stated guarantee with no mechanism behind it — nothing carries a cancelled token
> to the in-flight call the close sequence must cancel (D2). §5's PR ladder is a **dependency
> order**, not a schedule. This revision is self-contained.

**Date:** 2026-09-03
**Status:** design, **rev 24** — twenty-two maintainer reviews, one hundred and forty-three
findings, all confirmed against source and folded into the body. rev 23 changed the trusted-root
predicate and wrote rule 11 to catch what a rule change leaves behind; **rev 24 is what rule 11
caught when it was finally run over every term rev 23 changed, not just the one that produced
its finding** — the predicate, the `BASH_FUNC_*` scrub and the signature all survived in
normative entries. It also closes rule 7, where three wrappers still launched through a
different resolver, a different environment or a different subject and were re-entered as
permissions.
**Anchors:** agentao `main@3537753` (2026-09-01); codex `openai/codex@b7cd519c76` (2026-08-31);
pi-mono `@853a80d26` (2026-08-28). All three read from a local worktree at the pinned commit.
§3.10 additionally reads one upstream `PowerShell/PowerShell` file, and records its commit, blob
and sha256 there.
**Method:** every premise carries an inline `file:line` at its own repo's anchor. §2.7, §2.12,
§3.4, §3.8–§3.12 and §3.14–§3.16 are **measured, not reasoned**; §3.10 and §3.20 are **fetched
from upstream at a pinned commit**, with a hash and a re-fetch.
**Scope:** running the model's shell commands under PowerShell on Windows, and what
`agentao/permissions_hardline/_scanner.py` must become first. Injected capabilities, the
sub-agent path and its concurrency, registry provenance, MCP ownership, both composition roots,
host tool replacement, interpreter and bare-word resolution in the floor *and in the child*, shell
profiles, inherited functions, name rebinding that never touches `PATH`, and the Windows
command-line serialisation are in scope. Out of scope: WSL; PowerShell on macOS/Linux; and **two
races this design narrows but does not close** — a console session configuration or a replacement
interpreter installed between rung resolution and a spawn (§7, D4).
**Twin:** `powershell-support-plan.zh.md`.
**Related:** `builtin-tools-four-way-codex-gemini-pi-agentao.md`, `permission-hardening-plan.md`,
`lint-gate.md`, `path-a-roadmap.md`.

### Revision history

The rules themselves are in the body; this table exists so a future editor can recognise a repeat,
and §10 carries the method lessons the rounds produced. Each row names the class of mistake and
where the corrected rule now lives. **`Found` counts that round's findings, and rev 19 was an
editorial pass rather than a review round** — which is the whole of the difference between the
rows (twenty-three with findings, one hundred and forty-six between them) and the header's count
(twenty-two reviews, one hundred and forty-three findings). Anyone re-deriving those numbers needs the
convention, not just the arithmetic, and §10's own count is derived from the same header rather
than kept by hand. **A row counts what its round *raised*, not what it folded in:** rev 23
resolved six findings raised in rounds 20 and 21, which are counted in those rows and not again
in its own — a fix landing two rounds after the finding is the normal case, and the table would
otherwise double-count it.

| rev | Found | What it got wrong | Rule now in |
|---|---|---|---|
| 24 | 2 P0, 2 P1, 4 P2, 2 minor | Rule 7 re-entered `Start-Process`, `Invoke-Item` and cmd `start` as permissions, though each resolves through ShellExecute rather than 5g and `-UseNewEnvironment` alone restores the unfiltered user PATH inside an allowed body; rule 11's own sweep ran over one term instead of every term its round changed, leaving the predicate, the `BASH_FUNC_*` scrub and the signature stale in the summaries, the tables and the PR rows; the ladder can now run out and nothing said what that means; the launch request could not express the two launch forms already specified, nor carry the attestation; the MCP token was still a mutable attribute on a tool instance; a signature was called a content pin | D5, D2, D4, D6, §10 |
| 23 | 2 P0, 4 P1, 2 P2, 3 minor | The trusted-root predicate was *administrator-writable*, which an elevated agentao satisfies itself, so the subject could write the root the rule exists to keep it out of; the summaries still offered the allowlist as an alternative to the location after the rule stopped doing so, and the rule left it with no function at all; `-p` protects one process and the environment reaches the whole tree, so `BASH_FUNC_*` arrived at a descendant of an allowed command; rule 7 still re-entered a spawner that starts an interpreter; the executor contract was three questions where it is three obligations; a positive case was scheduled by no gate; the task set had no removal. **Six findings from earlier rounds had been dropped without a record; they are resolved here** | D4, D5, D2, §6 |
| 22 | 1 P0, 2 P1, 1 P2, 4 more | An allowlisted hash or signature stood *instead of* a trusted location, so it admitted user-writable images by construction and an in-body `Copy-Item` beat it with no race; one token holds many MCP tasks and a cancel can land before the task is registered; the `rung` had no verdict for an unknown value or an illegal pairing; a nested interpreter launch kept none of D4's guarantees; image checks read the floor's filesystem, which a non-local executor is not | D5, D2, D4, §6 |
| 21 | 1 P0, 1 P1, 2 P2, 2 minor | Fixed the trusted-image hole in one rule and left the filtered PATH admitted as a trusted root in another, which reopened it and made the image half vacuous for every bare word; the bash rung's floor was specified three ways with no key that could select between them; a non-contiguous gate set written as a range | D4, D5, D2, §5 |
| 20 | 2 P0, 3 P1, 1 P2 | The "closed" runnable set admitted any explicit `.exe`, and an unclassified command poisoned only its successors; the interpreter was authenticated by launching it, from a PATH the project filter does not narrow to the administrator; `UNKNOWN` had no verdict; the close sequence cancelled a token nothing routes to the MCP future; a rung under `cmd` is unreachable | D5, D4, D2, D6, §5 |
| 19 | 3 (editorial pass) | One rule stated in two places disagreed with itself once the copies were read together: a gate "allowed to be red" after §6 had abolished that category, a two-file configuration read under a rule that needs all three sources, a nine-step count over a ten-row table. The zh twin was also still narrating superseded revisions this twin had dropped | D4, D5, §6 |
| 18 | 4 P0, 2 P1 | Guard checked `$PSHOME` where the prose required the session-configuration name; a guard running inside the interpreter cannot authenticate it; a static path is not immutable bytes; source fidelity written as a character set, not the automaton it is | D4, D5, §3.19, §7 |
| 17 | 3 P0, 2 P1 | Prose and the normative table disagreed, twice, on the side an implementer copies from; a lowering step was missing between two of mine | D4, D5, §3.19 |
| 16 | 3 P0, 2 P1 | Borrowed two of codex's nine lowering gates; effect classes made mutually exclusive; the preflight learned the session configuration by starting the interpreter | §3.19, D4, D5 |
| 15 | 2 P0, 2 P1 | The rebinding rule only looked backwards, so an executing command as the last statement passed | D5 |
| 14 | 2 P0, 3 P1 | Adopted the node-kind list without the `#Requires` check beside it; the `PSModulePath` variable mistaken for the effective value | §3.18, D4 |
| 13 | 2 P0, 4 P1, 1 P2 | Inertness quantified over commands, in a language that rebinds without forming one | §3.17, D5 |
| 12 | 3 P0, 4 P1 | The rebinding rule was a closed table with a fail-open sentence under it | D5, §3.15, §3.16 |
| 11 | 3 P0, 3 P1 | An atomic record stopped torn reads and not lost updates; the registry was rebuilt from the whitelist rather than the registry; bash launched with an inherited environment | D2, D4, §3.14 |
| 10 | 3 P0, 3 P1, 1 P2 | The sub-agent would have run different tools; a shared engine had no synchronisation; the floor's PATH was not the child's | D2, §2.15, §3.13 |
| 9 | 3 P0, 2 P1, 1 P2 | Named `_bind_and_register` without reading it | §2.14, D5 |
| 8 | 3 P0, 2 P1, 1 P2 | PR-0 rebuilt the engine from disk, discarding in-memory host policy | §2.13, §3.12 |
| 7 | 5 P0, 2 P1, 1 P2 | Claimed the sub-agent path needed no change; measured, it has no engine at all | §2.12, §3.11 |
| 6 | 2 P0, 4 P1 | Launch parameters match by prefix; project-scope `permissions.json` is ignored by design | §3.10, §2.10 |
| 5 | 5 P1 | Made the shell spec a constructor argument, which the composition order forbids | §2.9 |
| 4 | 4 P1, 2 P2 | "Bound at construction" was one constructor's property, not the contract | §2.8 |
| 3 | 4 P1, 3 P2 | Wrappers were closed and evaluators left open | §3.7, D5 |
| 2 | 1 P0, 3 P1, 2 P2 | Routed opaque to ASK, which three transports auto-approve | §2.6 |
| 1 | — | Initial design | — |

---

## TL;DR

1. **PR-0 first and independently.** Sub-agents have no engine (§2.12). They get the parent's
   engine, one effective filesystem and shell, and a registry rebuilt from the parent's **live**
   registry by name and origin — never shared tool objects, never re-created disabled ones (D2).
2. **The engine has one writer lock and lock-free readers**, and every decision carries the
   snapshot it was made from (D2).
3. **The floor and the child resolve names in the same environment**, and nothing the
   environment carries runs or rebinds before the body: the filtered PATH — **only directories
   the child's own subject cannot write**, one predicate serving selection, 5a's image half and
   the child's `PATH` (D4) — `PATHEXT=.COM;.EXE`,
   `-NoProfile -NonInteractive`, a pinned `PSModulePath`, and `bash --noprofile --norc -p` —
   `-p` is what stops an inherited function, not the two long options (§3.16). A command that is
   not **provably resolution-inert** makes everything after it opaque (D4, D5).
4. **One tool, not two** (D2). **DENY is the only floor verdict** (§2.6). **The dialect travels
   with the call** (§2.9). **Opaque is a property of tokens and of AST node kinds, per dialect**
   (§3.17, D3, D5, D7). **The runnable set is closed per dialect, by two independent conditions**
   — an entry in the dialect's trusted table for the *name*, and for the file an *image* under a
   root the subject cannot write, which a host allowlist may pin further but never replace — and
   a command word **missing either half** is opaque **itself**, not
   merely a poisoner of what follows it (D5). For bash the filtered PATH is the image half only.
5. **Shell configuration is user-scope or host, never workspace** (§2.10, D6).

---

## 1. Target architecture

| | Today | Target |
|---|---|---|
| Model-facing tool | `run_shell_command` | `run_shell_command`, name guarded |
| Dialect on Windows | `cmd.exe` via `%COMSPEC% /c` | `pwsh` → `powershell.exe` → Git Bash (only with `shell.allow_git_bash`) → `cmd` |
| Floor's gate | tool name | tool name **and** the dialect passed with the call |
| Analysis mode | regex over raw text | **regex** (posix, cmd) or **lowered** (powershell) |
| Runnable targets | anything | a **trusted-table entry for the name** *and* a **trusted image for the file**: explicit `.exe`/`.com` from a root the child's subject cannot write, which a host allowlist may pin further but never replace, known cmdlets/internals, bare words resolved through the filtered PATH, which is the child's PATH. Anything else — an unclassified program, a trusted basename on an untrusted image — is opaque |
| Unanalysable input | no match means allow | `hardline:<dialect>-opaque` ⇒ **DENY** |
| Sub-agent | no engine; fresh tools bound to `None`; registry from the definition | parent's engine/fs/shell by identity; registry from the parent's live registry by name + origin |
| Engine under concurrency | unsynchronised fields | writer lock, lock-free snapshot readers, decision carries its snapshot |
| Child environment | inherited | filtered PATH (**only directories the child's subject cannot write**), `PATHEXT=.COM;.EXE`, `BASH_ENV`/`ENV`/**`BASH_FUNC_*`** removed, `NoDefaultCurrentDirectoryInExePath=1` on cmd |
| cmd launch | `%COMSPEC% /c` | `Popen(string, executable=<cmd>)`, `"<cmd>" /d /e:on /v:off /s /c "<body>"` |

## 2. Current state — measured

### 2.1 Windows runs `cmd.exe` today, and says so honestly

`agentao/capabilities/shell.py:58-59` — *"Windows is untouched: ``shell=True`` there means
``%COMSPEC% /c``, and ``executable=`` would replace cmd.exe rather than select a dialect"*
(`agentao/capabilities/shell.py:55-56`); `agentao/capabilities/shell.py:71-72`;
`agentao/tools/shell.py:156-160`; `agentao/capabilities/shell.py:141-143`;
`shutil.which("bash")` at `agentao/capabilities/shell.py:62`.

### 2.2 The floor is gated on the tool *name*

`agentao/permissions_hardline/_scanner.py:155-156` — *"the floor is about preventing
unrecoverable operations, and ``run_shell_command`` is the single surface that can express them"*
(`agentao/permissions_hardline/_scanner.py:129-131`). `grep -rn '"run_shell_command"' agentao/ |
wc -l` → 32, across 13 files; four of them decide behaviour: the floor,
`agentao/runtime/tool_executor.py:390`, `agentao/plugins/hooks/_alias.py:16`, the presets. The
count is written as its command because a bare number is not a premise anyone can re-check.

### 2.3 `plan` mode denies by exact name, with no catch-all

`agentao/runtime/tool_planning.py:487-495`; `agentao/permissions.py:444-457`,
`agentao/permissions.py:458`, `agentao/permissions.py:459`.

### 2.4 On Windows the floor is already inert

`agentao/permissions_hardline/_patterns.py:380`; zero hits for the four Windows tokens. §2.7.

### 2.5 Eight CI jobs in `ci.yml`, zero Windows

`.github/workflows/ci.yml` — `schema-check`, `typing-gate`, `lint-gate`, `test`, `mcp-compat`,
`build`, `smoke`, `examples`, and `grep -c 'runs-on' .github/workflows/ci.yml` → 8, every one
`ubuntu-latest`. Earlier revisions cited `pyproject.toml:10-21` here, which is the classifier
list: a true claim standing on a citation that cannot support it.

### 2.6 DENY is unshadowable; ASK is auto-approvable by three transports

*"so a ``full-access`` ``allow:*`` rule cannot silently shadow it"*
(`agentao/permissions.py:684-687`); `agentao/permissions.py:688-694`;
`agentao/runtime/tool_planning.py:510-514`.

| Transport | Behaviour | Site |
|---|---|---|
| `NullTransport` | `return True` | `agentao/transport/null.py:28` |
| `SdkTransport` | `return True` without a callback | `agentao/transport/sdk.py:101-103` |
| CLI | `return True` under `full-access` / `allow_all_tools` | `agentao/cli/transport.py:76-77` |

### 2.7 The floor's current reach, measured — including its fail-open

```
rm -rf /                           hardline:recursive delete of root / …
timeout 5 rm -rf /                 hardline:recursive delete of root / …
D=rm; $D -rf /etc                  None
X=/; rm -rf $X                     None
del /f /s /q C:\*                  None
rd /s /q C:\                       None
set D=del & call %D% /f /s /q C:\* None
```

### 2.8 The sub-agent path replaces the `PermissionEngine` after construction

`agentao/agents/tools/_wrapper.py:563-570`; *"losing them was a permission bypass"*
(`agentao/agents/tools/_wrapper.py:549-553`).

### 2.9 Both composition roots build the engine before the agent

`agentao/embedding/factory.py:186-192`, `agentao/embedding/factory.py:270`;
`agentao/acp/session_new.py:366-374`. 150 `PermissionEngine(` sites. `_decide` takes the tool
first (`agentao/runtime/tool_planning.py:473-475`) and calls `decide_detail`
(`agentao/runtime/tool_planning.py:498`).

### 2.10 Project-scope `permissions.json` is ignored by design — and that is the trust boundary

`agentao/embedding/permission_loader.py:131-136`; *"Project-scope ``.agentao/permissions.json``
is intentionally NOT loaded: a checked-in rule could grant the agent capabilities the user never
approved"* (`agentao/permissions.py:483-485`); *"Permissions are a user/host concern, not a cwd
concern — the same model OS permissions and IDE workspace-trust use"*
(`agentao/permissions.py:487-489`).

### 2.11 The shell block had no path through either composition root

`agentao/embedding/permission_loader.py:107-111`; `agentao/embedding/factory.py:186-192`;
`agentao/acp/session_new.py:366-374`, `agentao/acp/session_load.py:262-270`,
`agentao/acp/session_load.py:278-282`.

### 2.12 A sub-agent has no permission engine, and the assignment meant to give it one is dead — measured

The wrapper constructs with a fixed keyword list (`agentao/agents/tools/_wrapper.py:513-535`) and
no `permission_engine=`, `filesystem=` or `shell=`; the constructor stores `None`
(`agentao/agent.py:112`, `agentao/agent.py:295`) and passes it on (`agentao/agent.py:648`); the
runner stores it (`agentao/runtime/tool_runner.py:80`) and copies it into the planner
(`agentao/runtime/tool_runner.py:86`, `agentao/runtime/tool_planning.py:307-308`), which is what
`_decide` reads (`agentao/runtime/tool_planning.py:498`). `sub_agent.tools = scoped_registry`
(`agentao/agents/tools/_wrapper.py:538`) misses the runner's captured registry
(`agentao/runtime/tool_runner.py:79`) — the wrapper's own comment says so
(`agentao/agents/tools/_wrapper.py:522-526`). The engine assignment
(`agentao/agents/tools/_wrapper.py:570`) has one reader (`agentao/tooling/agent_tools.py:98`).

```
bare Agentao(...)   (= the wrapper's construction)  ASK    tool requires_confirmation fallback
… then tool_runner._permission_engine = engine      ASK    tool requires_confirmation fallback
a real PermissionEngine.decide_detail(...)          DENY   hardline:recursive delete of root / …
```

What executes is the sub-agent's own fresh built-ins (`agentao/tooling/registry.py:95-100`),
bound at `agentao/tooling/registry.py:77-80` to `None` (`agentao/tools/base.py:43-48`,
`agentao/tools/base.py:50-55`). The parent's instances in `scoped_registry`
(`agentao/agents/tools/_wrapper.py:463-465`) are what the model is *shown*.

### 2.13 What a sub-agent could inherit, and what a disk rebuild throws away

Three getters (`agentao/tooling/agent_tools.py:97-99`), never the engine. Engine state that
exists nowhere on disk:

| State | Set by | Site |
|---|---|---|
| `_enable_hardline` | `enable_hardline=` | `agentao/permissions.py:561` |
| `_run_scope_rules` | `add_run_rules` | `agentao/permissions.py:591`, `agentao/permissions.py:601` |
| `_injected_sources` | `add_loaded_source` | `agentao/permissions.py:640-650` |
| the rule list | in-memory `rules=` | `agentao/permissions.py:579` |

No `snapshot` / `copy` / `fork` exists on the class.

### 2.14 `_bind_and_register` overwrites three slots unconditionally, and `register` binds nothing

`agentao/tooling/registry.py:77-80`; *"inherit the exact same working-directory / filesystem /
shell binding as built-ins"* (`agentao/tooling/registry.py:72-75`).

### 2.15 Tool instances carry per-agent runtime state, and nothing serialises them across agents

The executor rebinds `output_callback` to its own transport
(`agentao/runtime/tool_executor.py:405-410`) under a lock documented as serialising *"concurrent
calls to the same tool within this batch"* (`agentao/runtime/tool_executor.py:200-201`);
`TodoWriteTool` keeps its list on the instance (`agentao/tools/todo.py:16`,
`agentao/tools/todo.py:62`); `Tool` has no copy API.

### 2.16 The registry's scope is not the definition's whitelist, and the public kwargs cannot express it

Rebuilding the sub-agent's registry by passing the *definition's* whitelist to `enabled_tools=`
leaves three things wrong. **First**, the whitelist is not the parent's
current tool set: a built-in the parent disabled or removed is absent from the parent's registry
but present in the whitelist, so the sub-agent re-creates it. **Second**, `apply_enabled_tools`
keeps every extra tool regardless — *"``extra_tools`` are always kept — the host injected those
instances explicitly"* (`agentao/tooling/registry.py:207-209`) — so a forked host tool outside the
whitelist is still exposed. **Third**, a host tool that replaced a built-in name
(`agentao/tooling/registry.py:145-147`) and is not forkable would leave the sub-agent with the
*original* built-in under that name — the opposite of what the host chose. And an MCP branch
cannot be built with the public kwargs at all: `enabled_tools` rejects `mcp_*`
through `_reject_reserved_tool_name` (`agentao/agent.py:489`), and `remove_tool` refuses the same
names (`agentao/agent.py:953`). The registry has to be rebuilt from what the parent *has*, by
origin, through an internal path that does not go through those guards.

### 2.17 Agent tools are registered at construction, so `agent_manager = None` afterwards removes nothing

`AgentManager(...)` is created and `_register_agent_tools()` runs inside `__init__`
(`agentao/agent.py:625-629`); only built-in agents are opt-in (`agentao/agent.py:151`) —
project and plugin agents are discovered unconditionally. The wrapper's `sub_agent.agent_manager =
None  # prevent recursive spawning` (`agentao/agents/tools/_wrapper.py:541`) nulls an attribute
after the wrappers that would spawn are already in the registry with their callbacks captured
(`agentao/tooling/agent_tools.py:88-102`). By the plan's own criterion — safe only if the reader
reads the attribute at use time — this assignment is not safe.

### 2.18 The MCP manager drives one loop from whichever thread calls it

`McpClientManager` holds a single loop (`agentao/mcp/client.py:982-992`) and bridges with
`loop.run_until_complete(coro)` (`agentao/mcp/client.py:999`); `grep -n "Lock\|
run_coroutine_threadsafe\|call_soon_threadsafe" agentao/mcp/client.py` returns nothing. A parent
and a background sub-agent sharing the manager would each call `run_until_complete` on the same
loop from their own thread. A lock around that call is the wrong instrument (D2). And `close()`
calls `disconnect_all()` on whatever manager the agent holds (`agentao/agent.py:1015-1017`), so a
sub-agent sharing one and closing would drop the parent's connections.

### 2.19 The registry records no origin, and six built-ins are constructed from the agent

`ToolRegistry.__init__` is `self.tools = {}` (`agentao/tools/base.py:207`) and `register` takes
only `replace` (`agentao/tools/base.py:209`): a name maps to an instance, and nothing records
where it came from, so after the fact a built-in and a host tool that replaced a built-in are
indistinguishable. "A fresh instance of the same class" is also not a construction recipe.
`register_builtin_tools` (`agentao/tooling/registry.py:83`) supplies dependencies from the agent —
the agent's own `memory_tool` (`agentao/tooling/registry.py:117`),
`ActivateSkillTool(agent.skill_manager)` (`agentao/tooling/registry.py:118`), an `AskUserTool`
closing over the transport (`agentao/tooling/registry.py:119`), the agent's `todo_tool`
(`agentao/tooling/registry.py:120`) and two tools bound to `bg_store`
(`agentao/tooling/registry.py:126`) — while the web tools exist only when `bs4` does
(`agentao/tooling/registry.py:109`). Six built-ins cannot be re-created from their class alone,
and re-creating them from the *parent's* dependencies would hand the sub-agent the parent's
transport and the parent's todo list — the state §2.15 says must not be shared.

## 3. Verified premises

### 3.1 codex's gate is the dialect; agentao's is the tool name

`codex-rs/shell-command/src/shell_detect.rs:6-13`; `codex-rs/core/src/shell.rs:32-40`;
`codex-rs/core/src/exec_policy/executable_identity.rs:35-37`.

### 3.2 codex's real PowerShell parser is a test oracle, not production

`codex-rs/shell-command/src/command_safety/mod.rs:1-2`;
`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:6-8`,
`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:10-12`;
`codex-rs/shell-command/src/command_safety/is_dangerous_command.rs:45-50`.

### 3.3 pi-mono has no floor to break

`packages/coding-agent/src/utils/shell.ts:125-133`, `packages/coding-agent/src/utils/shell.ts:122`,
`packages/coding-agent/src/core/tools/powershell.ts:16`; codex `codex-rs/core/src/shell.rs:32-40`;
known locations first (`packages/coding-agent/src/utils/shell.ts:76-92`).

### 3.4 The grammar is available to Python at codex's exact pin — measured

`codex-rs/Cargo.toml:485`; `pyproject.toml:6`.

```
Remove-Item -Recurse -Force C:\               no error  command_name, command_elements
echo 'Remove-Item -Force is dangerous'        no error  command_name(echo), command_elements
Get-ChildItem C:\tmp | Remove-Item -Force     no error  TWO command nodes under pipeline_chain
& (gcm ('Remove' + '-Item')) -Force C:\       no error  command_invokation_operator, command_name_expr
```

### 3.5 What the floor covers today

18 classes; `agentao/permissions_hardline/_patterns.py:35-37`.

### 3.6 What codex's Windows table actually covers

| Class | Dialect | Site |
|---|---|---|
| URL-bearing launch, **only when an argument parses as an http(s) URL** | mixed | `codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:47-53` |
| Forced delete cmdlets | **PowerShell** | `codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:226` |
| `del` / `erase` force; `rd` / `rmdir` recursive+quiet | **CMD** | `codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:143-152` |

### 3.7 Two evaluation holes the blueprint does not close — measured

`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:143-150`,
`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:322-324`.

### 3.8 `command_name_expr` is four different things — measured

`& 'name'` / `& { }` / `. .\x.ps1` / `Import-Module`.

### 3.9 A bare path is an ordinary `command_name`; a dynamic argument looks like a literal one

`.\setup.ps1`, `C:\tools\x.cmd`, `Remove-Item $flags C:\`, `Get-ChildItem $dir`.

### 3.10 PowerShell's launcher matches parameters by prefix — measured from upstream source

`PowerShell/PowerShell` at commit `2ca393d6b82f5c270440604d205fc37adbdf674a` (the last commit
touching the file, 2026-08-10T17:29:03Z; `master` was
`d0f43b00343b04a699d81c325a63a88ab83fec53` at fetch time 2026-09-02T16:40Z), blob
`src/Microsoft.PowerShell.ConsoleHost/host/msh/CommandLineParameterParser.cs`, sha256
`727de30f58506d55cb7e363f0f5dbb777bee48c545258255b5ce69c5185e209b` — re-fetched at that commit
and byte-identical.

```
798| private static bool MatchSwitch(string switchKey, string match, string smallestUnambiguousMatch)
805|     return (switchKey.Length >= smallestUnambiguousMatch.Length
806|             && match.StartsWith(switchKey, StringComparison.OrdinalIgnoreCase));
1090| … "commandwithargs" … || … "cwa" …
1103| … "command", "c"
1141| … "file", "f"
1182| … "encodedcommand", "e" || … "ec", "e"
```

codex: `codex-rs/shell-command/src/powershell.rs:9`, `codex-rs/shell-command/src/powershell.rs:60-62`.

### 3.11 `shutil.which` on Windows searches the current directory first on 3.10 and 3.11 — measured

```
3.11    if sys.platform == "win32":  …  path.insert(0, curdir)                          — unconditional
3.12    if sys.platform == "win32" and _win_path_needs_curdir(cmd, mode): path.insert(0, curdir)
```

### 3.12 Python re-serialises a cmd argv into a string cmd does not parse the same way — measured

```
list2cmdline(['cmd','/d','/e:on','/v:off','/c', 'echo "a b" & del /f /s /q C:\*'])
   → cmd /d /e:on /v:off /c "echo \"a b\" & del /f /s /q C:\*"
```

### 3.13 Where each dialect looks for a bare word — and that the floor's environment was not the child's

cmd: current directory first, then PATH per `PATHEXT`. PowerShell: aliases, functions, cmdlets —
the last of which module autoloading can supply out of `PSModulePath` — then PATH per `PATHEXT`.
**bash: aliases, keywords, functions, builtins and the command hash all resolve before `$PATH` is
searched at all** (§3.15); the PATH step then matches an exact filename and runs any executable
file including a script, and `PATHEXT` plays no part. **On a Windows POSIX layer that filename
match is not plain** — MSYS2 resolves bare `git` to `git.exe` — and this plan does not state the
precedence between an extensionless `git` and a `git.exe` in one directory; gate 20 measures it
before the rung ships. cmd `start` launches by association. With
`PATH=<project>;<trusted>`, the floor's filtered search allowed `git` and the child ran the
project's `git.cmd`.

### 3.14 A non-interactive `bash -c` runs `$BASH_ENV` before its body — measured

```
$ printf 'echo "[BASH_ENV file ran first]"\n' > payload.sh
$ BASH_ENV=./payload.sh bash -c 'echo "[body ran]"'
[BASH_ENV file ran first]
[body ran]
$ env -u BASH_ENV bash -c 'echo "[body ran]"'
[body ran]
```

(local `bash 3.2.57`; the behaviour is bash's documented startup rule for non-interactive
shells.) So a Git Bash rung launched as `"<path>" -c <body>` with an inherited environment runs a
working-tree `BASH_ENV` before the body the floor scanned.
`sh` has the same hook under `ENV`.

### 3.15 Three bash rebindings rule 6's table did not name — measured

```
$ bash --noprofile --norc -c 'export PATH=/usr/bin:/bin; printf -v PATH "/private/tmp"; \
    echo "PATH now: $PATH"; env | grep "^PATH="'
PATH now: /private/tmp
bash: env: command not found
$ bash --noprofile --norc -c 'export PATH=/usr/bin:/bin; read PATH <<< "/private/tmp"; \
    echo "PATH now: $PATH"; env | grep "^PATH="'
PATH now: /private/tmp
bash: env: command not found
$ bash --noprofile --norc -c 'export PATH=/usr/bin:/bin; hash -p ./evil/notgit git; git --version'
[EVIL git ran]
```

(local `bash 3.2.57`.) `printf -v` and `read` assign `PATH` with no assignment syntax; since
`PATH` was already exported the child search changes with it, which is what the two
`command not found` lines prove. `hash -p` rebinds one command name and **never touches `PATH`**,
so no rule keyed on a target variable could have caught it. All three are shell builtins, which
bash resolves before it searches `PATH` at all.

### 3.16 An inherited function survives `--noprofile --norc`; `-p` is what stops it — measured

```
$ env 'BASH_FUNC_git%%=() { echo "[EVIL function git ran]"; }' \
      bash --noprofile --norc -c 'type git; git --version'
git is a function
[EVIL function git ran]
$ env 'BASH_FUNC_git%%=() { echo "[EVIL function git ran]"; }' \
      bash --noprofile --norc -p -c 'type git'
git is /usr/bin/git
$ env SHELLOPTS=xtrace bash --noprofile --norc -c 'echo body'
+ echo body
$ env SHELLOPTS=xtrace bash --noprofile --norc -p -c 'echo body'
body
$ env BASH_ENV=./payload.sh bash -p -c 'echo "[body]"'
[body]
```

(local `bash 3.2.57`.) Under `--noprofile --norc` alone, a trusted bare `git` resolves to a
function the environment carried in. `-p` is the interpreter's own closed answer **for the
process it starts**: it blocks inherited functions and `SHELLOPTS`, and covers `BASH_ENV` and
`ENV` there without a scrub list. **It says nothing about that process's children.** A descendant
bash — `/bin/sh -c` inside a trusted `git` alias, an npm script, a `make` recipe, a git hook —
is a fresh process started without `-p`, and it imports `BASH_FUNC_git%%` from the environment
it inherited. So the environment is scrubbed as well as the flag passed (D4): a flag protects one
process, an environment reaches the whole tree. **Flag order matters** — `bash -p --noprofile …`
fails with `bash: --: invalid option`, so the long options come first.

### 3.17 codex judges by AST node kind, and refuses any kind it has not reviewed

`first_unrecognized_named_kind` walks every named node and returns the first kind outside an
accept-list (`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:132-133`); the
list is twenty-odd kinds — pipelines, commands, command elements, literals, comments — and nothing
else (`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:143-169`). An
`assignment_expression`, a `variable`, a member invocation and a nested scriptblock are all absent
from it, so a script containing one is refused before any command-level analysis runs. The
comment above it says the rejection is *"until its lowering semantics are reviewed"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:128`). An inertness rule
stated over *commands* is strictly weaker than this: `$Function:git = { … }` forms no command word
and passes no argument.

### 3.18 codex refuses `#Requires` in a content check, before the kind walk

`has_requires_directive` runs **first**, and its failure is
*"requires directives can execute before command lowering"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:47-48`); only then does
`first_unrecognized_named_kind` run. The reason is in the function's own comment: *"Tree-sitter
exposes #requires as a comment, but PowerShell evaluates it before the script body and can load
modules or assemblies"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:104-105`), and it matches
by lowercasing the comment text and testing `starts_with("#requires")`
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:113-117`). `comment` is on
the accept-list (§3.17), so a kind gate alone passes `#Requires -Modules Evil` — the directive
imports the module before the first command the floor scanned ever runs.

### 3.19 codex's lowering is nine gates in order, and its fixture file is the enumeration

`lower_with_tree_sitter` refuses, in this order: a Unicode syntax alias — smart quotes, en and em
dashes — with *"PowerShell Unicode syntax alias"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:25`); then it masks
`--flag=value` with a one-byte replacement, because *"the one-byte replacement keeps CST ranges
valid"* (`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:33`) and a later
gate compares byte ranges against the original; then *"tree contains ERROR or missing nodes"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:45`); then `#Requires`
(§3.18); then the unrecognised node kind (§3.17); then an empty command list; then, **per command
node and before the fidelity check**, `lower_command_text`
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:66`); then the source
fidelity check, failing with *"source outside literal command nodes"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:69`); then
*"using declarations require the PowerShell AST oracle"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:76`); then *"empty lowered
command or word"* (`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:82`).

**`lower_command_text` is argv lowering, not classification**, and its own comment says the
decoding is *"only for forms whose runtime value is statically known"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:310-311`). It parses single
and double quotes and backticks, and refuses *"adjacent/concatenated command elements"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:334`), an empty word, and —
through `reject_unsupported_bare_word`
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:462`) — an *"attached
PowerShell parameter value"* and a *"non-canonical numeric-leading bare word"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:466`,
`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:474`), because those need
PowerShell's own value conversion. Several fixture rows fail **here** and nowhere else.

**And the fidelity check is not "every byte inside a command node".** Its comment is *"Command
nodes alone are not enough: reject any source outside the literal commands and
separators/comments we explicitly understand"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:208-209`), and it is a
**stateful walk, not a character filter**: it carries `can_chain`, `needs_command` and
`paren_depth` (`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:212-214`)
and returns only on `range_index == command_ranges.len() && !needs_command && paren_depth == 0`
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:306`). So the separators it
admits — newlines, whitespace, `;`, a pipe, the chaining operators, parentheses and comments —
are admitted **positionally**: a closing paren must match one it opened, a chain operator needs a
command before it and demands one after, and the walk must end with nothing owed (D5 step 8).
`source_is_covered_by_commands`
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:207`) walks the raw bytes,
and where it meets a `#` it records that *"`#` starts a comment only at a token boundary"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:274`) and refuses when
tree-sitter has split one out of a bare token — otherwise `git status --short#; Remove-Item
victim` lowers to a lone `git status --short`, the rest of the line having become an accepted
`comment`, while PowerShell runs the `Remove-Item` after the semicolon.

The adversarial inputs are already written down. `powershell_lowering.json` holds 68 cases, **44
of which must lower to nothing** — `Remove-Item test –Force`
(`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:43`), the embedded
hash above
(`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:46`), a smart-quote
delimiter (`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:47`), the
stop-parsing token `--%`
(`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:50`), plus
here-strings, `param` / `begin` / `end` / `trap` blocks, redirection, the invocation operator,
subexpressions and array expressions.

### 3.20 The AllUsers configuration lives in `$PSHOME`, and `$PSHOME` is the assembly's directory — fetched from upstream documentation

`MicrosoftDocs/PowerShell-Docs` at commit `49b1bd052bfacc7e6c7651ef9396be8933de28ce` (the last
commit touching the file, 2026-08-17T20:55:27Z; `main` was
`32b128ab35e7c5321579cf844d9e1f9aa5a03c39` at fetch time 2026-09-03T15:48Z), blob
`reference/7.5/Microsoft.PowerShell.Core/About/about_PowerShell_Config.md`, sha256
`264a66318761cf5767cd3d86efeefc12dd3ef2eb64e7d9a578deb198fbacdb9f` — re-fetched at that commit
and byte-identical.

```
63| - Settings managed by Windows Group Policy take precedence over settings in the
64|   configuration files.
74| A `powershell.config.json` file in the `$PSHOME` directory defines the
75| configuration for all PowerShell sessions running from that PowerShell
76| installation.
79| > The `$PSHOME` location is defined as the same directory as the executing
80| > System.Management.Automation.dll assembly. This applies to hosted PowerShell
81| > SDK instances as well.
```

So the AllUsers file is not "beside the interpreter": it is in the install root of the assembly
the process loads, which for a launcher that is a shim, a symlink or a copy is a different
directory — and it is the same directory that lets whoever can write it change what the
interpreter *is* without touching the launcher the preflight hashed (D4). Group Policy's
precedence over both files is upstream's own statement rather than an inference from the two file
scopes.

## 4. Decisions

### D1 — One tool, one name; unlabelled command rules are `unspecified`; D1 runs again on replacement

`run_shell_command` keeps its name (§2.2). `args` conditions are regexes over raw text
(`agentao/permissions.py:747-750`); rules gain an optional `dialect` field — `"posix"`, `"cmd"`,
`"powershell"`, `"*"` — extending `_LEGAL_RULE_FIELDS` (`agentao/permissions.py:76`). An
unlabelled shell-tool rule with an `args.command` condition is `unspecified`, unchanged on POSIX
and cmd (`agentao/capabilities/shell.py:58-59`); PowerShell with `unspecified` rules fails
construction naming each rule and all four labels. Re-run at `agentao/agent.py:418` for
`add_tool(replace=True)` (`agentao/agent.py:906`).

### D2 — Call-time dialect; guarded name; a sub-agent built by an internal factory from the parent's live state; an engine with one writer lock

**Dialect.** The `ShellExecutor` declares it; `ShellTool` exposes `shell_spec` from
`_get_shell()` (`agentao/tools/base.py:50-55`); `_decide` passes it to `decide_detail`, which
forwards to `hardline_check`. `PermissionEngine(` untouched at all 150 sites.

**Name.** `ShellSpecProvider` is required of any tool under the name
(`agentao/tooling/registry.py:145-147`, `agentao/agent.py:906`, `agentao/agent.py:418`);
reservation on the model of `_PLAN_ONLY_TOOLS` (`agentao/agent.py:390-392`,
`agentao/agent.py:411-416`) stays available (§9 q6).

**`ShellDialect`:** `POSIX`, `POWERSHELL`, `CMD`, `UNKNOWN`
(`agentao/tools/shell.py:248-252`, `agentao/capabilities/shell.py:107-123`,
`tests/test_shell_capability_swap.py:20-30`). **`UNKNOWN` has a verdict, and the verdict is the
whole of its semantics: the floor returns `hardline:unknown-dialect-opaque` before it matches a
single rule, so DENY.** So does any value the floor does not recognise. A host's own
`ShellExecutor` is exactly where an unlabelled dialect arrives, and the two things an implementer
would otherwise do with it are the two this rule forbids: falling back to the POSIX scanner
reports a clean floor after scanning cmd or PowerShell with POSIX patterns, and skipping the scan
is `no match means allow` (§1). Gate 1 covers a custom executor that reports `UNKNOWN` and one
that reports a value outside the enum.

**The rung is a second field, because the dialect cannot carry it.** `ShellDialect` has four
values and the Git Bash rung reports `POSIX`, so nothing keyed on the dialect can hand the
Windows rung one floor and the shell a Linux host already has another. The spec therefore
carries a **`rung`** — `pwsh | powershell | cmd | git_bash | system_posix` — beside the dialect
and beside the preflight answer D4 puts there. **The dialect selects the analysis; the rung
decides whether this plan's closed-set policy is in force.** Rule labels in `permissions.json`
stay the four dialect values (D1) — a user writing `dialect: "posix"` means both, which is what
a permission rule should mean. `system_posix` is what every existing POSIX host reports and it
has the policy **off** until §9 q4 decides otherwise, so PR-2 can ship every primitive without
moving a single Linux verdict. Gate 7 asserts the pair: one body, opaque under `git_bash` and
unchanged under `system_posix`.

**The legal pairs are enumerated, and an unknown value is not a policy-off default.**

| Dialect | Legal rungs |
|---|---|
| `POWERSHELL` | `pwsh`, `powershell` |
| `CMD` | `cmd` |
| `POSIX` | `git_bash`, `system_posix` |

Anything else — an unrecognised rung, or a legal rung under the wrong dialect such as
`POWERSHELL × system_posix` — is refused. **Where that refusal happens matters, because the spec
is what an executor *declares*:** a host `ShellExecutor` can report any pair it likes, so the
matrix is validated when the spec is constructed, failing construction and naming the pair the
way D1 fails on an unlabelled rule, **and** the floor keeps a fail-closed verdict for anything
that reaches it anyway — `hardline:unknown-rung-opaque` before a single rule matches, exactly as
`UNKNOWN` does for the dialect. The failure this forbids is the one an implementer reaches for:
routing "not recognised" to `system_posix`, which is the one value whose policy is **off**, so
the unknown case would skip the closed set entirely. Gates 1 and 7.

**And the spec says whether the floor and the child share a filesystem.** `ShellExecutor` is
host-injectable (§2.9, D6), so a host may run the command in a container, over SSH or on another
machine, and such an executor can report `POWERSHELL × pwsh` perfectly truthfully while every
image check the floor performs — a directory's ACL, a content hash, a signature, whether a PATH
entry exists at all — reads the **floor's** filesystem and not the one the command will run on.
So the spec carries `filesystem_is_local`, **absent means `false`**, and "local" means one thing:
the path the child opens is the path the floor stat'd. A container on the same host is not local
by that test, and neither is a chroot or a mount namespace.

**What a non-local executor owes is three obligations, not three answers.** *Resolve*: every
question in the oracle (D4) is answered for the target, the bare-word search of 5e/5g/5h
included, since that search is a filesystem operation on the machine the command will run on.
*Attest*: the answers bind the target's subject, the target's environment and the image the
child will actually open, not a namesake on the floor's disk. *Launch*: the prelude,
`-NoProfile -NonInteractive`, `-p`, the filtered `PATH`, `/d /e:on /v:off /s` — every one is a
property of a command line **agentao writes**, and today `ShellRequest` carries `command: str`
and `env` and nothing else (`agentao/capabilities/shell.py:77-84`), while D6 lets a `shell=`
executor supply the whole spec. Nothing in between obliges the executor to launch the way D4
pins. **The plan takes the first of two ways out:** agentao builds the argv and the environment,
the request carries them, and the executor runs them verbatim — PR-1 is already a protocol
change, and this is the version where the guarantees travel with the request rather than being
re-implemented per host. **The request has to be able to say both launch forms this plan already
pins, and one `argv` cannot:** the `cmd` row below is a *single string* with `executable=` set,
because `/s` strips the outer quotes and re-quoting the body would change it (§3.12), while the
pwsh and Git Bash rows are argv. So the request carries a discriminated launch — `argv: list` on
POSIX, or `application_name` plus `command_line` on Windows — alongside the environment, **the
subject the child must run as**, and **the canonical image the attestation resolved**, so an
executor cannot honour the command line and quietly launch something else. Gate 24 asserts every
field against a fake executor rather than asserting that resolution happened. The alternative,
writing resolve-attest-launch into the `ShellExecutor`
contract with a conformance gate, is recorded rather than chosen: it moves the obligation to
every host that ever ships an executor. Where neither holds, every command word that needs an
image is opaque. This is not a judgement about remote execution — it is that a check performed
against the wrong filesystem is not a check (§10 rule 6).

**PR-0 — the sub-agent is built by an internal factory, `Agentao._for_subagent(parent, definition)`,
not by the public constructor's kwargs.** §2.16 shows that `enabled_tools=` / `extra_tools=` /
`remove_tool` each have a guard or a semantic that defeats this use. The factory:

1. **Shares capabilities by identity**: `permission_engine`, the parent's one effective
   `filesystem` and `shell`, `working_directory` (compared by resolved value).
2. **Re-runs the real registration pass against the sub-agent, over a registry that records
   provenance.** A snapshot of instances re-created by class is not available (§2.19): six
   built-ins take their dependencies from the agent, so the only correct construction is the one
   that
   already exists — `register_builtin_tools(sub_agent)` with the sub-agent's `_disable_tools` set
   to the parent's disabled names **plus** every built-in name outside the definition's
   whitelist, which is the one filter that pass already honours
   (`agentao/tooling/registry.py:135-136`). Each dependency is then the sub-agent's own: its
   transport backs `AskUserTool`, its `todo_tool` is its own list. And `ToolRegistry.register`
   gains an `origin` — `builtin | host | mcp | agent | plan` — stored beside the instance
   (`agentao/tools/base.py:209`); a replacement also records what it displaced, which is what
   makes the fourth row below decidable at all. **It is keyword-only with a default of `host`,
   not required.** The in-tree sites pass it explicitly — `_bind_and_register`
   (`agentao/tooling/registry.py:80`), MCP (`agentao/tooling/mcp_tools.py:144`), agent tools
   (`agentao/tooling/agent_tools.py:104`), the plan tools (`agentao/cli/app.py:336`), and the
   sub-agent's own registry and `CompleteTaskTool`
   (`agentao/agents/tools/_wrapper.py:465-466`) — but `agent.tools.register(...)` is a path hosts
   already use and this repo's own examples call
   (`examples/ticket-automation/src/triage.py:199-202`), so a required argument would be a
   user-visible break in the one PR whose claim is that it is not one. `host` is also the
   fail-closed default: an unclassified tool is a host tool, and a host tool that cannot fork is
   absent from every sub-agent. Per origin, read from the parent's *live*
   registry and intersected with the definition's whitelist:

   | Origin in the parent | In the sub-agent |
   |---|---|
   | built-in, present and whitelisted | constructed by `register_builtin_tools(sub_agent)` (`agentao/tooling/registry.py:83`) from the sub-agent's own dependencies |
   | built-in, disabled or removed by the parent, or not whitelisted | **absent** — its name joins the sub-agent's `_disable_tools` |
   | host tool (`extra_tools` or `add_tool`) that is `ToolForkable` | `fork_for_agent()`'s new instance, bound with `_bind_and_register` (`agentao/tooling/registry.py:77-80`) |
   | host tool that is not `ToolForkable` | **absent, and the name it held is absent too** — a host that replaced `read_file` and cannot fork it does not get the built-in `read_file` back underneath; a one-time warning names the tool |
   | agent tool | re-registered only if the definition **names it explicitly**, through `_register_agent_tools` against the *sub-agent* so the wrapper captures the sub-agent's getters (§2.17); otherwise **none are registered** — the factory skips `_register_agent_tools()`, and `agent_manager = None` (`agentao/agents/tools/_wrapper.py:541`) is deleted. **"Whitelisted" is not enough here:** an absent `tools:` key means *all tools* (`agentao/agents/manager.py:57`) and the built-in generalist omits it (`agentao/agents/definitions/generalist.md:1-4`), so a `None` whitelist read as "all" would hand `agent_generalist` to itself and restore exactly the recursion that assignment was defending against. A `None` whitelist implies every **non-agent** origin and no agent one |
   | `mcp_*` | through the scoped MCP view below, only if whitelisted; never via `enabled_tools` or `remove_tool`, whose guards (`agentao/agent.py:489`, `agentao/agent.py:953`) stand |
   | plan-only | never |

   `CompleteTaskTool()` is added last. The result is the one registry the runner and planner
   also hold (gate 17).
3. **Skips** the built-in, MCP and agent registration passes of `__init__`; nothing is assigned
   to `sub_agent.tools` afterwards (`agentao/agents/tools/_wrapper.py:538` deleted), nothing to
   `tool_runner._permission_engine` (`agentao/agents/tools/_wrapper.py:570` deleted), no file is
   read (`agentao/agents/tools/_wrapper.py:559-562` deleted), and `engine.set_mode(mode)`
   (`agentao/agents/tools/_wrapper.py:569`) is deleted.
4. **Keeps** `set_readonly_mode(True)` — the runner's own field
   (`agentao/runtime/tool_runner.py:106-109`), read at plan time; passes `project_instructions`
   and `skill_manager` as kwargs (`agentao/embedding/factory.py:146-148`); keeps
   `llm.omit_temperature` with a comment naming its reader.

**MCP ownership: an owner thread, and a non-owning view.** A lock around the bridge is the wrong
instrument — it serialises the callers and still hands the loop to a different OS thread on each
acquisition (§2.18). `McpClientManager` gains an **owner thread** that creates the loop
and runs it, and every sync bridge becomes
`asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)` in place of the bare
`run_until_complete` (`agentao/mcp/client.py:999`). On top of that it gains
`scoped(names) -> McpToolView`, a read-only view over the parent's connections exposing only the
whitelisted tools. The view is what the factory registers; it holds no per-agent state, and it is
**non-owning** — a sub-agent's `close()` neither disconnects the shared manager nor stops the
loop (`agentao/agent.py:1015-1017`).

**The other direction is the owner closing first, and a lease alone cannot do it.** A background
sub-agent runs on a daemon thread (`agentao/agents/tools/_wrapper.py:761`) whose handle is
discarded at `start()`, and `bg_store` registers a `CancellationToken` per agent
(`agentao/agents/bg_store.py:490`, `agentao/agents/bg_store.py:494`) and no thread — so nothing
in the process can join it, and the manager cannot join what nothing recorded. Nor is there a
release point: the normal return is `return result, stats`
(`agentao/agents/tools/_wrapper.py:653`), which closes no sub-agent. Five parts, all extending
what exists rather than adding a registry beside it:

1. `bg_store` records the **thread** alongside the token it already holds, and `cancel`
   (`agentao/agents/bg_store.py:380`) keeps its meaning.
2. The sub-agent runner wraps its body in `try/finally` — foreground and background — and the
   `finally` closes the sub-agent, which unregisters its view.
3. **A lease is one thing and one thing only: an in-flight call** — not an agent's lifetime,
   which behaves differently. Every MCP call acquires one for its duration and releases it in a
   `finally`, whoever the caller is: a background sub-agent, a foreground turn, or an embedding
   host's own thread. An agent's lifetime is the *view's* registration, which is not a lease.
4. **Cancelling a token has to reach the call, and today nothing carries it there.**
   `McpTool.execute()` is sync and takes only `**kwargs` (`agentao/mcp/tool.py:118`), and the
   executor injects a token only into a tool that already carries the attribute —
   `if cancellation_token and hasattr(tool, "_cancellation_token")`
   (`agentao/runtime/tool_executor.py:351-352`) — which in-tree is `AgentToolWrapper` alone
   (`agentao/agents/tools/_wrapper.py:220`). `McpTool` has no such attribute, so a cancelled
   `bg_store` token never reaches the coroutine on the owner loop: step 5's cancel is a no-op,
   and the whole deadline is then spent waiting for a lease nothing has asked to release. So the
   sync bridge takes a **call context** carrying the token — **an explicit parameter or a
   `contextvar`, not the mutable attribute the executor already writes.** That attribute lives on
   the tool instance, the executor sets it only when a token is truthy
   (`agentao/runtime/tool_executor.py:351-352`), and the per-tool lock serialises only *within*
   one batch (`agentao/runtime/tool_executor.py:200-202`) — so a call that arrives with no token,
   which is what a host calling `ToolRunner.execute()` directly produces, reads whatever the
   previous call left there. A stale **cancelled** token is the bad case rather than a harmless
   one: `add_done_callback` fires immediately on an already-cancelled token, so the new call is
   cancelled the moment it registers. A context is per-call and per-worker-thread and cannot be
   left behind — and the manager registers
   the lease's `asyncio.Task` **in a set keyed by that token**, not one task per token. **One
   token covers a whole batch:** `execute_batch(plans, *, cancellation_token=None, …)`
   (`agentao/runtime/tool_executor.py:188-192`) takes a single token for every plan in it, so a
   sub-agent issuing N parallel MCP calls puts N tasks under one key, and a `dict` would keep
   only the last one — cancelling would then leave N−1 calls running under a manager that
   believes it cancelled them. Cancelling the token cancels **every** task in the set, each
   **through the loop** (`loop.call_soon_threadsafe(task.cancel)`, never `task.cancel()` from the
   calling thread), and each cancelled coroutine's `finally` releases its own lease, which is the
   acknowledgement step 5 waits for. **The `finally` also empties the registry, in one order:**
   unregister the cancellation callback, `discard` the task from its set, delete the key when that
   set is empty, then release the lease — a manager that only ever inserts keeps a task reference
   per call for its whole life, which on a long-lived owner is a leak rather than a wrong verdict,
   and gate 0 asserts the registry is empty on both the completed and the cancelled path. The
   context is per-call, so none of this rests on each agent registering **its own**
   `McpToolView` instances (the scoped view above) — which it does, and which the attribute form
   would have depended on. **And the subscription is taken atomically with the
   registration, not sequenced after it:** `CancellationToken.add_done_callback` runs the callback
   immediately when the token is already cancelled and hands back an unregister callable for the
   `finally` (`agentao/cancellation.py:97-105`), so a cancel landing between "lease acquired" and
   "task registered" still cancels. The earlier answer — a token with no registered task is a call
   that has not started, and `CLOSING` refuses new leases — covers `close()` only: an ordinary
   `bg_store.cancel()` never enters `CLOSING`, so without the atomic subscribe a task registered a
   moment later would run on.
5. The parent's `close()` runs one sequence, and **cancellation comes before the wait**:
   `CLOSING` → refuse new leases → **cancel every token**, which by part 4 cancels every task
   registered under it → wait for the active lease count to
   reach zero *and* join every recorded thread, both under **one** deadline → disconnect and stop
   the loop, logging what it gave up on. The order is load-bearing: a long MCP call releases its
   lease only when it is cancelled, so waiting for the count to fall first spends the whole budget
   waiting for something that has not been asked to stop. Draining leases is the primary wait and the thread join is secondary: a foreground or
   host-thread call holds a lease and appears in no thread set, so a design that waits only on
   `bg_store`'s threads disconnects underneath exactly the callers it does not know about.

**`close()` tears down only what an agent owns, and a sub-agent owns nothing it was handed.**
The store is the parent's, passed in at construction so the sub-agent's registry can service
`check_background_agent` — *"Inherit the parent's background-task store"*
(`agentao/agents/tools/_wrapper.py:522-527`). Without this rule, step 2 above is a defect rather
than a fix: a sub-agent's `finally` running step 5 would cancel its **siblings**, and it would
try to join the very thread it is running on. So ownership is recorded at construction — engine,
filesystem, shell, MCP manager and `bg_store` are all *shared by identity*, and the sub-agent's
`close()` unregisters its own view, flushes its own state, and touches none of them — it holds
no lease to release, since a lease is one in-flight call and a `close()` is not one. Only the
owning agent's `close()` runs step 5. Gate 0 asserts a sibling still running is unaffected.

**Cancelling is not refusing, which is why `CLOSING` is a phase of its own.** A thread that
outlives the join budget is still alive and would otherwise acquire a fresh lease after the
manager decided it was done.

And a timed-out `result(timeout)` is **not** a cancellation — the coroutine keeps running on the
owner loop — so the timeout path also schedules `task.cancel()` through the loop, the rule
`agentao/tools/web.py:634-639` already follows for its worker hand-offs. Gate 0 checks both
directions alongside the callback and todo checks.

**The engine: one writer lock, lock-free readers, and a decision that carries its snapshot.**
Collapsing the engine's mutable fields into an atomically swapped record fixes torn reads
(`agentao/permissions.py:597-598` writing, `agentao/permissions.py:702`,
`agentao/permissions.py:705`, `agentao/permissions.py:712` reading) and **not** lost updates: two
mutators loading the same old record and each assigning a new one drop the loser's change —
`add_run_rules`'s deny under a concurrent `set_mode`. So:

- **The state's values are immutable, not merely swapped.** An atomic record is not enough while
  `_mode_rules` *is* the module-level preset list rather than a copy of it
  (`agentao/permissions.py:598`) and `add_run_rules` extends the live lists in place
  (`agentao/permissions.py:633`, `agentao/permissions.py:635`): a lock-free reader holding the
  record still aliases a list another thread is growing, and a caller handed `rules` can edit
  the presets for every engine in the process. Rules are normalised to frozen values at the
  validator boundary, the state holds tuples of them, and every mutator builds a new tuple.
- **Nothing hands out a backing object.** `rules` and `active_mode` survive as compatibility
  properties that copy out — they have readers inside the engine itself
  (`agentao/permissions.py:810`) and unknown ones outside it — so neither a caller mutating what
  it was given nor one mutating the list it passed to the constructor
  (`agentao/permissions.py:579`) changes any policy.
- **Every mutator** (`set_mode`, `add_run_rules`, `add_loaded_source`, and any host setter) runs
  under one `threading.Lock`, loading the current record inside the lock and assigning the new
  one before releasing.
- **Every reader** (`decide_detail`, `active_permissions`) loads `self._state` once, without the
  lock, and reads only from that record.
- **`_active_cache` is not in the record.** A cached derivation written back after a newer
  record was installed would resurrect old policy. The cache is keyed by the record's identity
  and is discarded with it.
- **`PermissionDecisionDetail` carries the record it was decided from.** The host projection
  builds its event by calling `active_permissions()` at a later moment
  (`agentao/host/projection.py:245`); it now reports the decision's own snapshot, so the mode
  and rule set an event names are the ones that produced the verdict.

**Why the writer lock cannot deadlock against the runner's:** the runner's per-tool locks are
held during *execution* (`agentao/runtime/tool_executor.py:405`); `decide_detail` is a *planning*
call and takes no lock; the writer lock is taken only by mutators, none of which is called from
inside a tool execution. The two lock families never nest.

### D3 — Opaque is a property of tokens, and the token rule is per dialect

`Token = Literal(text) | Dynamic(kind)`; codex's `Option<Vec<Vec<String>>>`
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:13`) cannot carry it.

| Dialect | Expansion semantics | Opaque when |
|---|---|---|
| PowerShell | an expanded argument is one argument | word `Dynamic`, or word in table and a predicate-read position `Dynamic` |
| POSIX / bash | expanded words split on IFS, not re-parsed for operators (except `eval`) | same as PowerShell — §9 q4 |
| CMD | `%VAR%`, `%1`…`%9`, `%*` at line read; `%A` per FOR iteration; `!VAR!` at execution under `/v:on` | **any** `Dynamic` anywhere, **and** any control structure or grouping (D7) |
| `UNKNOWN`, or a value outside the enum | not analysed | **always, before any rule matches** — `hardline:unknown-dialect-opaque` (D2) |

### D4 — Flip only after D3, only with the parser, only from trusted locations; the child resolves names in the floor's environment; profiles cannot run and two races are narrowed, not closed

**Order:** `pwsh` → `powershell.exe` → **Git Bash, only when `shell.allow_git_bash` is on** →
`cmd`. A missing parser makes PowerShell unselectable. **The switched rung sits *above* `cmd`,
because a rung below it is unreachable:** `cmd.exe` is present on every supported Windows, so a
ladder that reaches `cmd` stops there and never falls through. A switch guarding a rung under
`cmd` is dead code — it can be `true`, the rung can be present and installed, and auto-resolution
still never selects it — while gate 11 passes and gate 20 exercises a path production cannot
take. So `allow_git_bash` buys a **replacement** for the last rung rather than an addition after
it, and `cmd` stays the fallback when the switch is on and no Git Bash is found (D6).

**Where each interpreter is looked for, and what a location is allowed to establish.** Two
tiers, and they are not symmetric. **(a) Automatic:** known absolute install locations
(`packages/coding-agent/src/utils/shell.ts:76-92`,
`codex-rs/shell-command/src/shell_detect.rs:257-262`,
`codex-rs/core/src/exec_policy/executable_identity.rs:62-72`) whose directory **the child's own
subject cannot write** — the one predicate, asked of the token the child will run under, not of
administrators in general (below) — and whose image passes a **host-side**
identity check **before anything is launched**: a code signature the host trusts, or an entry in
a host-configured allowlist of absolute path plus content hash. **(b) Explicit:** a user
`shell.path`, absolute and outside the project root. That is a *trust grant*, and is documented
as one — the user named this file, so it is admitted without a signature, and nothing else is.

**A filtered-PATH hit is not a candidate, and the filter itself is stricter than it was.** rev 20
dropped empty, relative, working-directory and project-root entries (searched by agentao's own
code, never `shutil.which` — §3.11) and read an absolute result as selectable. That filter
narrows the *project* out and leaves the *user* in: a `pwsh.exe` dropped into any user-writable
directory that happens to be on PATH still resolved to an absolute path, and the next thing the
design did was **start it** and ask what it was. A binary that runs first can answer anything —
edition, version, `$PSHOME`, the autoloading preference are all self-reported — so every field
the ladder collected came from the program under suspicion, and it had already executed by the
time the floor had an opinion. **A program cannot be authenticated by running it: running it is
the event the check exists to gate.** So a PATH hit is not a selection candidate, and gate 23
asserts both tiers, including that the planted binary is never *started*.

**And the filter keeps only directories the child's own subject cannot write — one predicate with
three consumers.** Selection here, 5a's *image* half, and the `PATH` handed to the child are the
same question asked three times: a directory trusted enough to resolve a name out of is exactly
a directory trusted enough to run one from. **The predicate is *not writable by the subject*, and
"administrator-writable only" is the wrong spelling of it.** The two coincide exactly when
agentao runs unprivileged, and rev 22 wrote the coincidence rather than the rule: an agentao
started from an elevated terminal, or as `root` in a container, is *itself* the administrator, so
`C:\Program Files` and `/usr/bin` are writable by the subject and
`Copy-Item .\evil.exe 'C:\Program Files\Git\cmd\git.exe'; git status` is the same raceless
sequence 5a uses to refuse the allowlist. So the question the oracle answers is **"can the token
the child will run under modify, delete or replace this path, or the directory containing
it?"** — and where the answer is yes for every candidate root, the trusted set is **empty**: the
    rung is refused rather than served with a set the subject can rewrite. An elevated posture is
    a posture this floor cannot secure, and saying so is the verdict; gate 25 asserts it in both
directions. **And a refused rung can now empty the ladder, which needs its own answer:** with
every rung refused there is no `cmd` fallback left, so the shell tool answers
`hardline:no-trusted-rung-opaque` on every call — registered, refusing, and saying why — rather
than unregistering itself, which hides the reason, and rather than falling back to today's
`%COMSPEC% /c` with the inert floor, which is the most convenient thing an implementer could do
and the weakest (§2.4). Gate 25 asserts which of the three it is.

rev 20 answered it with two different strengths —
D4 disqualified a user-writable PATH entry for the interpreter while 5a admitted one as a
trusted root — which reopened the hole rev 20 had just closed, since a `git.exe` planted there
passes the name half by basename and the image half by location, and the child's `PATH` then
resolves it. A user-writable entry is therefore **dropped**, not merely refused for selection:
leaving it in the child's `PATH` would let the child resolve what the floor declined to. On a
POSIX host the equivalent predicate is root-owned and neither group- nor world-writable, which
belongs to §9 q4 with the rest of that decision.

**The image questions go through a host-side identity oracle, which is also the seam the tests
need.** Whether **the subject the child will run as** can modify, delete or replace a path or its
directory, whether a path resolves on the machine the command will run on at all, whether an
image carries a signature the host trusts, and what an image's content hash is, are **four**
questions the floor asks an oracle rather than answering inline: on Windows it reads ACLs against
the child's token and Authenticode, for a non-local executor it is the executor's (D2), and in
tests it is injected. The fourth is not decoration — 5e, 5g and 5h resolve a bare word *by
searching the filtered PATH*, which is a filesystem operation on the target and not a fact the
floor holds. Gate 2 runs every dialect's floor tests on ubuntu, and gate 4's **positive** case —
a `git.exe` under a root the subject cannot write, with a trusted-table entry, is allowed —
cannot be produced there at all; with the oracle that case is a stub on ubuntu and the real thing
on the Windows job (gate 23), which is the only arrangement in which both gates are true at once.

**The child's environment is the floor's environment, and nothing it carries may run or rebind
before the body.** Every rung's child carries `PATH=<filtered>` — the same predicate as selection,
so the child cannot resolve a name the floor refused — and `PATHEXT=.COM;.EXE`; the cmd rung adds
`NoDefaultCurrentDirectoryInExePath=1`; the PowerShell rung **turns module autoloading off** and
does not try to pin the set of modules that could otherwise be loaded; and the bash rung passes
`-p`, which is the interpreter's own closed answer to inherited functions, `BASH_ENV`, `ENV` and
`SHELLOPTS` **for the process it launches** (§3.16). **That is one process, and the environment
reaches the whole tree** — so `BASH_ENV`, `ENV` **and every `BASH_FUNC_*` entry** are removed from
the child environment as well (§3.14, §3.16). The measured case is not the model writing a nested
launch, which rule 2 already refuses: a trusted `git` runs an alias through `/bin/sh -c`, that
`sh` is bash, bash imports `BASH_FUNC_git%%` from the environment it inherited, and the function
runs — inside a command the floor allowed, two processes down. npm scripts, `make` and git hooks
are the same shape. `-p` protects the shell agentao starts; only the environment protects its
descendants, which is why the scrub list is back and why it is a *removal* rather than a flag. The
bash rung's `PATHEXT` is set for uniformity but bash ignores it — see rule 5h.

**The PowerShell rung is keyed to a measured interpreter identity.** `pwsh` and `powershell.exe`
are different programs — codex's own comment separates them, *"pwsh.exe is the cross-platform
PowerShell Core (v6+) executable"* against *"powershell.exe is the Windows PowerShell (v5.1 and
earlier) executable"* (`codex-rs/shell-command/src/powershell.rs:98-101`) — and their alias sets
differ, so 5g's table cannot be one unversioned list. codex needs no such table: its Windows
policy asks whether a command is dangerous
(`codex-rs/shell-command/src/command_safety/is_dangerous_command.rs:45-50`), not whether it is
trusted, and an alias it has never heard of costs it nothing. A closed trusted set is the
opposite: an alias it has never heard of is exactly what it gets wrong. The resolved
interpreter's `(absolute path, edition, version)` is recorded at flip time, and **read
host-side from the image** — the PE version resource, or the install manifest — never from a
child's `$PSVersionTable`. That the version resource can be trusted is exactly what the
signature over the image buys; a self-report buys nothing. An interpreter whose identity is not
one the table was measured against is **opaque**, not close enough.

**Autoloading is turned off, because a module set cannot be pinned and a path is not the thing.**
Startup re-composes `PSModulePath`, so a value handed in is an input and not a setting; a path is
not a set, because the files under it change after any recording; and even a perfect pin leaves
the CurrentUser module directory, which is outside the working tree and which autoloading searches
before 5g ever falls through to PATH. So:

- The launch sets `$PSModuleAutoLoadingPreference = 'None'` in a **pinned prelude** — byte-exact
  text in the command-line table below, in the same sense `-NoProfile` is, not part of the body.
  The floor's guarantee is that it scanned the body; the prelude is text the floor never varies,
  and gate 21 asserts the body's own first statement is unaffected by running one whose first
  statement has an observable effect.
- `PSModulePath` is still pinned, as defence in depth and not as the mechanism.
- **No single check establishes that the preference is in force, so there are three — and one
  thing none of them can do.**
  "5g degrades where the child cannot demonstrate the preference" is unimplementable: the floor
  decides *before* any child exists, and nothing a child reports afterwards revises a verdict
  already returned. So:
  - **Before any launch at all, the configuration is read from disk.** Asking the interpreter what
    its session configuration is cannot work: a custom console session configuration can import
    modules, define commands and run its own scripts at startup, so by the time a body could
    report "non-default", that configuration has already run. **Nothing about a program can be
    learned by starting the program you are trying to decide about.** The resolution step reads
    **all three sources** as data — the AllUsers `powershell.config.json` in the interpreter's
    `$PSHOME`, the CurrentUser file under the user profile, and Group Policy, which takes
    precedence over both files — and refuses the rung unless the effective console session
    configuration is the default one. **`$PSHOME` is not "beside the launcher":** upstream
    defines it as the directory of the executing `System.Management.Automation.dll` (§3.20), so
    for a launcher that is a shim, a symlink or a copy it is a different directory. The install
    root resolved host-side in tier (a) is what the read uses; where it cannot be resolved
    host-side, the rung is refused rather than read from the launcher's directory.
  - **At rung resolution, once, a preflight** — and only after tier (a) or (b) has already
    authenticated the image, because a launch establishes nothing about the thing launched. Only
    then does D4's ladder launch the candidate interpreter with the same prelude and a body that
    reports the preference and re-reports the identity fields as a **consistency check** on an
    image the host has authenticated — never as the source of them.
    Its result is a field on the `ShellSpec` the floor already reads through
    `ShellSpecProvider` (D2) — so by the time `_decide` runs, "is the closed resolution
    environment established" is a value in hand, not a future observation. **A `false` there
    makes 5g's bare-word rule inert: every PowerShell bare word is opaque** and the rung serves
    explicit `.exe`/`.com` paths under 5a.
  - **At every launch, the same prelude re-verifies what it can and aborts.** The preflight's
    answer can go stale — a config file written between the two, a different interpreter resolved
    by the same path — so the prelude's guard checks **the preference, the edition, the version,
    `$PSHOME`, and the effective console session configuration name**, against values baked into
    the pinned command line, and **exits non-zero before a single byte of the body runs** if any
    differ. The four **substituted** values are `<E>`, `<V>`, `<H>` and `<C>` — the preference is
    compared against the literal `'None'` and needs no substitution, which is why the guard reads
    five things and this list has four entries; each is
    substituted as a **single-quoted PowerShell literal with every embedded `'` doubled**, and a
    preflight value that cannot be encoded that way refuses the rung rather than being escaped
    some other way. `<C>` is not substitutable by `$PSHOME`: an install directory cannot testify
    about an endpoint name. **If no in-child expression is found that reports the effective console session
    configuration, `<C>` is not silently omitted** — the rung is refused unless the preflight
    also found none configured in any of the three sources, and gate 21 records which it was.
  - **What the guard cannot do, said plainly: it cannot authenticate the interpreter it is
    running inside.** A replacement binary at the same path with the same edition, version and
    `$PSHOME` satisfies every field, and it had control before the guard's first token was
    parsed — a check evaluated by the suspect is not an identity check. The floor narrows this
    with **host-side** file identity: the preflight records the executable's content hash and
    the spawn re-hashes immediately before launch, which shrinks the window to the interval
    between hash and `CreateProcess` and does not close it. **The hash covers the launcher and
    not the load closure.** `System.Management.Automation.dll` and everything else the process
    loads sit outside it — and on Windows that assembly's own directory *is* `$PSHOME`
    (§3.20) — so whoever can write the install root can change what the interpreter is without
    touching the file that was hashed. The identity claim therefore rests on the install root
    being unwritable by the subject and the image being signed, with the hash detecting only the
    replacement of that one file. The remainder is a non-goal (§7), and
    gate 21's "swap the interpreter" case is honest about which swaps it detects: one that
    changes a recorded field or the hash, not one that matches them all.
- `-NoProfile` does **not** cover `powershell.config.json`. That file exists in an AllUsers scope
  under the resolved `$PSHOME` (§3.20) and a CurrentUser scope, and it can select a console session
  configuration, which binds commands and visibility before any of the above applies. **Group
  Policy is a third source and it overrides both files**, which is why the read above is of all
  three, and why the identity records that assertion's result rather than the file.
- **The residual this design does not close, stated rather than implied.** A session
  configuration installed *after* the preflight runs its own startup script **before** the
  `-Command` prelude — that is what selecting a configuration means. The in-child guard can
  refuse the body, and it cannot un-run a script that preceded it. So the honest claim is
  narrower than "startup files cannot run": profiles cannot (`-NoProfile`), and a configuration
  present at resolution time cannot (the rung is refused), but a configuration installed in the
  window between resolution and spawn runs once before the guard stops the body. The window is
  narrowed by re-reading all three sources immediately before spawn and is not closed. It is a
  non-goal (§7) and gate 21's characterization probe (a), with its expected result written
  down — not a release gate, and not a claim of closure.

**Top-level command lines, pinned per rung:**

| Rung | Command line |
|---|---|
| `pwsh` / `powershell.exe` | `"<path>" -NoProfile -NonInteractive -Command "<prelude>; <body>"`, with `PSModulePath` pinned and `<prelude>` the byte-exact `$PSModuleAutoLoadingPreference='None'; if ($PSModuleAutoLoadingPreference -ne 'None' -or $PSVersionTable.PSEdition -ne '<E>' -or $PSVersionTable.PSVersion.ToString() -ne '<V>' -or (Get-Item -LiteralPath $PSHOME).FullName -ne '<H>' -or <C-check>) { exit 97 }`, where `<E>`, `<V>`, `<H>` and `<C>` are the edition, version, `$PSHOME` and effective console session configuration name **recorded by the preflight**, each substituted as a single-quoted PowerShell literal with embedded `'` doubled, and `<C-check>` is the expression that reads the effective configuration name — the one field whose in-child expression this plan has not verified, so the rung is refused unless the preflight found no configuration in any source (D4). The guard is the second half of the same argument, so no body byte runs before it (§3.13). Constructed as **`Popen(list, shell=False)`** with the prelude and body as a **single** element, never split across arguments. **"Not re-quoted" is not available on Windows** — a list is always re-serialised by `list2cmdline` (§3.12) — so the claim this row makes is the checkable one: gate 18's sentinel asserts the body the child receives is byte-identical to the body the floor scanned, and if that gate cannot be made green the rung falls back to the single-string form with `executable=`, the way the `cmd` row below already does. codex passes `-NoProfile` (`codex-rs/core/src/shell.rs:32-40`), pi-mono adds `-NonInteractive` and `-ExecutionPolicy Bypass` (`packages/coding-agent/src/utils/shell.ts:122`); agentao takes two and declines the third |
| `cmd` | `"<path>" /d /e:on /v:off /s /c "<body>"` as one string, with `Popen(..., executable=<path>)` so `lpApplicationName` is set; `/s` strips the outer quotes, `/d` skips AutoRun, `/e:on /v:off` pin state; the body is never re-quoted (§3.12) |
| Git Bash | `"<path>" --noprofile --norc -p -c <body>`, in that order (§3.16), `shell=False`, `BASH_ENV`, `ENV` **and every `BASH_FUNC_*`** absent from the environment (§3.14, §3.16 — `-p` covers the process it starts, the scrub covers its descendants); `MSYS_NO_PATHCONV=1` so MSYS2 does not rewrite `/c/…`-shaped arguments |

**The Git Bash rung is the weakest and is switched separately.** Its floor is the POSIX
**pattern set** — the 18 classes of §3.5, including §2.7's measured fail-open — **plus** rule 6,
rule 5h and the closed runnable set, which is more than the shell a Linux host has today and is
why the spec carries a `rung` (D2): `git_bash` has the policy on, `system_posix` does not until
§9 q4 says so, and the dialect could not have told them apart. Its bare-word resolution is
bash's own (rule 5h), which `PATHEXT` cannot narrow; and its path-translation behaviour under
MSYS2 is untested here. Since the
switch now admits the rung **above** `cmd` (D6), turning it on prefers the weaker floor to the
stronger one — which is why it is off by default, why it is user-scope rather than project-scope,
and why PR-7
enables it only if gate 20 is green on the Windows job, and may ship with it off. This is not a
demotion of bash — the rung's patterns are bash's own floor and its extra rules only refuse more
— it is a refusal to claim for Windows what has not been measured on Windows.

### D5 — Wrappers, evaluators, name-expressions, a closed runnable set, spawners, and rebinding

POSIX recursion (`agentao/permissions_hardline/_scanner.py:143-146`,
`agentao/permissions_hardline/_scanner.py:166-168`); codex CMD wrappers
(`codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:92`) and prefixes
(`codex-rs/core/src/exec_policy.rs:104-108`).

**Rule 0 is the whole lowering pipeline, in this order, and a script that fails any step is
opaque** (§3.19). The order is not decorative: step 2's
masking is one byte wide *so that* step 8 can compare ranges against the original source.

| # | Step | Refuses |
|---|---|---|
| 1 | Unicode syntax aliases | smart quotes, en/em dashes — PowerShell treats them as syntax, the grammar does not |
| 2 | `--flag=value` masking | not a refusal; a one-byte substitution that keeps byte ranges valid for step 8 |
| 3 | Parse integrity | any tree with an ERROR or missing node |
| 4 | **`#Requires`** | a `comment` whose text, lowercased and left-trimmed, starts with `#requires` (§3.18) |
| 5 | **Node kinds** | any named kind outside the table below |
| 6 | Non-empty | a script that lowered to no commands at all |
| 7 | **Literal argv lowering**, per command node | quotes and backticks decoded only where the runtime value is statically known; concatenated elements, an empty word, an attached parameter value such as `-Path:x`, and a non-canonical numeric bare word (hex, leading zero) all refuse here (§3.19) |
| 8 | **Source fidelity**, a **stateful walk** and not a character set | every byte between command ranges must be admitted by an automaton carrying `can_chain`, `needs_command` and `paren_depth`, which also constrains *where* each separator may appear and requires the closing state `all ranges consumed ∧ ¬needs_command ∧ paren_depth = 0` (§3.19) |
| 9 | `using` declarations | they need an AST oracle this floor does not have |
| 10 | Empty command or word | the post-lowering invariant: no lowered command and no word may be empty |

Step 8 is why `git status --short#; Remove-Item victim` is opaque rather than a lone
`git status --short`: tree-sitter can split the embedded `#` into an accepted `comment`, and only
a raw-byte walk notices that the rest of the line went missing. **A rule that reads the tree
cannot check the tree against the text.** Two things step 8 is *not*. It is not "every byte
inside a command node", which would refuse a pipeline, a semicolon and a trailing comment — all
correct positives, and a fail-closed step still has to admit the separators that make a script a
script. And it is **not a set of permitted characters**: a bare `)` is in any such set and must
still be refused, because the automaton only admits a closing paren
that matches one it opened and requires `paren_depth = 0` at the end. Fixture
`uncovered_closing_paren` (`Get-Content --flag=value )`) is exactly that case
(`codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json:67`). **A
specification that lists a stateful checker's alphabet has specified none of its behaviour.**

**Step 5 in detail — the unit of judgement is the AST node, not the command.** Lowering walks
every named node and asks only whether its kind is on the accept-list; anything else makes the
whole script opaque. **This axis is `ACCEPTED` / `REFUSED` and nothing more** — rule 6's effect
flags are about *commands*, and reusing their names here would put two different questions under
one word.

| Node kinds | Verdict |
|---|---|
| `program`, `statement_list`, `pipeline`, `pipeline_chain`, `pipeline_chain_tail`, `command`, `command_name`, `command_elements`, `command_argument_sep`, `command_parameter`, `generic_token`, `array_literal_expression`, `unary_expression`, `expression_with_unary_operator`, `string_literal`, `verbatim_string_characters`, `expandable_string_literal`, `integer_literal`, `decimal_integer_literal`, `empty_statement` | `ACCEPTED` |
| `comment` | `ACCEPTED`, **and only because step 4 already ran** |
| every other named kind, including `assignment_expression`, `variable`, member invocation and scriptblock bodies | `REFUSED` → opaque |

That closes the forms that never become a command: `$Function:git = { … }` and
`$Alias:git = 'Remove-Item'` are assignments to a provider-drive variable,
`[Environment]::SetEnvironmentVariable('PATH', …)` is a member invocation, and a nested
scriptblock is a body the command-level rules never enter. **Nothing here is "rebinding" —
every kind that could rebind is simply refused**, which is why this axis needs two values and
rule 6's needs four.

Those twenty-one kinds are codex's list adopted whole
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:143-169`), because it is
measured against the same grammar pin this plan uses (§3.4) and its own comment says the
rejection stands *"until its lowering semantics are reviewed"*
(`codex-rs/shell-command/src/command_safety/powershell_tree_sitter.rs:128`) — the review is the
cost of each row, and inheriting the list inherits the reviews. Adding one is a row plus a test;
a tree-sitter upgrade that renames a kind fails closed, which gate 3 checks. Rule 6's effects are
then a later gate, over the commands that survive every step of rule 0.

1. **A wrapper's body is re-entered with the callee's dialect.**
2. **The PowerShell launch surface is parsed as PowerShell parses it** (§3.10): `-Command`/`c`,
   `-CommandWithArgs`/`cwa` → re-entered; `-EncodedCommand`/`e`, `-ec` → decoded and re-entered;
   `-File`/`f` → opaque; `nop`, `nol`, `noni`, `noe`, `ex`, `w` → consumed; **anything else →
   opaque** (`codex-rs/shell-command/src/powershell.rs:9`,
   `codex-rs/shell-command/src/powershell.rs:60-62`). **What re-entry buys is a refusal, not a
   permission — a nested interpreter launch is itself opaque.** Every guarantee D4 pins is a
   property of a command line *agentao* writes: the byte-exact prelude, `-NoProfile
   -NonInteractive`, the identity guard, the pinned `PSModulePath`, the per-rung argv. A `pwsh`,
   `powershell`, `cmd` or shell that the **child** launches carries none of them — module
   autoloading is back on, which is the exact condition 5g's table was measured *not* to be in
   (D4), and the interpreter is whichever one the child resolves rather than the one the host
   authenticated. So the parse still runs, and still refuses a dangerous nested body for its own
   reason rather than for a vague one; the launch itself does not become allowed by surviving it.
   This is what rule 6 already says for `bash`/`cmd`/`pwsh` **on a script path** — rev 22 makes
   the `-Command` form agree with the `-File` form instead of contradicting it.
3. **`cmd` is analysed** (D7).
4. **The four `command_name_expr` shapes** (§3.8): 4a evaluator source; 4b literal name
   recomposed; 4c scriptblock in place; 4d path under an operator → opaque.
5. **The runnable set is closed, per dialect — and in force per rung.** What the rung switches is
   the list D2 gives once and this rule does not restate: D3's token rule, rule 6's effect flags,
   and this closed runnable set. `system_posix` keeps today's behaviour until §9 q4 decides
   otherwise (D2).
   - **5a.** Explicit `.exe`/`.com` → basename-normalised
     (`agentao/permissions_hardline/_patterns.py:35-37`), matched as a command word — **and
     runnable only if both halves hold.** *Name*: the normalised basename has an entry in the
     dialect's trusted table, carrying the effect flags rule 6 gives it. *Image*: the file the
     child will open is inside a trusted root — a directory **the child's own subject cannot
     write**, which is what the filtered PATH is made of, so the host-configured roots and the
     PATH are one predicate rather than two strengths (D4) — **and nothing substitutes for
     that location.** A host identity allowlist (absolute path plus content hash, or a signature
     the host trusts) is an **additional** condition on top of it, never an alternative to it,
     and the two forms in it are not one thing: a **content pin** (absolute path plus hash)
     detects that this exact file was replaced, even where the root itself still holds, which is
     the launcher hash's job in D4; a **publisher trust** (a signature) says only that whatever
     file is there now was signed by someone the host trusts, which a replacement by the same
     publisher satisfies and a replacement by anyone else does not. Neither can admit a file the
     location rejects. As an alternative it admitted subject-writable images *by
     construction*, and it needs no race to defeat:
     `Copy-Item .\evil.exe <allowlisted path>; <that word>` replaces the file between the
     floor's hash and the child's open, inside one body, and `Copy-Item` to a filesystem path is
     inert under rule 6 — the provider-drive rule fires on `Env:` and its siblings, not on
     `C:\`— so nothing refuses the copy. It is the argument the file form of `executes_input`
     already makes, with the allowlist as its renamed exception: **the floor hashes and the child
     opens, and only a location the executing subject cannot write closes that gap.** That is the
     pattern D4 uses for the launcher, where the root the subject cannot write is the defence and
     the hash detects replacement of one file rather than standing in for the root. What it costs a
     user-installed toolchain is §9 q12, which is a decision and not a footnote. **The working
     tree is never a trusted root** (§7). Either half missing makes **this command** opaque, not
     merely what follows it. The halves are independent because each closes a different hole: a
     name without an image is how a `git.exe` copied into the working tree borrows `git`'s
     entry, and an image without a name is how a program nobody has classified runs unanalysed
     out of a trusted directory. **And the image half only bites because of that predicate:**
     5e, 5g and 5h resolve *through* the filtered PATH, so while any PATH directory counted as a
     trusted root the image half was true by construction for every bare word and 5a was the
     name half wearing two hats. Gate 4 carries all three counterexamples.
   - **5b.** Every other extension → opaque. **5c.** Extensionless path → opaque. **5d.** `-File`
     → opaque.
   - **5e. cmd bare word:** internal command → matched; else filtered-PATH search to
     `.exe`/`.com` → 5a; else opaque.
   - **5f.** Spawner targets obey 5a–5c and the bare-word rule of their dialect.
   - **5g. PowerShell bare word:** the cmdlet/alias table **of the measured interpreter
     identity** (D4) → cmdlet; else filtered-PATH search to `.exe`/`.com` → 5a; else opaque. One
     table across both editions either trusts a name one of them does not have or misses one it
     does. **The table is measured in the pinned startup state, not in an ordinary session** —
     with autoloading off, a command that an ordinary session resolves by loading its module on
     demand does not resolve at all, so a table measured the easy way would allow what the child
     then fails with a command-not-found. Every entry is verified resolvable in that state, and
     the state is part of the table's identity. **The whole rule is conditional on the
     preflight's answer (D4): where the closed environment was not established, every PowerShell
     bare word is opaque** and the rung still serves explicit `.exe`/`.com` paths under 5a — a
     bare word is only as trustworthy as the smallest set of things that could supply it, and an
     autoloading interpreter's set is whatever is on disk under a user module directory at the
     moment it runs.
   - **5h. bash bare word.** The PATH search is the last step, not the rule: bash resolves
     aliases, keywords, functions, builtins and the command hash first (§3.15). A word that
     resolves earlier than the PATH search is **opaque** unless it is in the rung's inert builtin
     set (rule 6). A word that reaches the PATH search resolves through the filtered PATH by
     bash's own rule — an exact filename, any executable file, script or binary — and is matched
     as its basename against the POSIX table, the same treatment `/bin/rm` gets today; not found
     → opaque. **There is no extension constraint, and none is claimed**: a script in a trusted
     PATH directory is trusted-directory content, and the filtered PATH — only directories the
     child's subject cannot write (D4) — is the whole of the closed-set property on this rung. **On the
     Windows POSIX layer the filename match is left unstated** — MSYS2 resolves bare `git` to
     `git.exe`, and the precedence against an extensionless `git` beside it is measured by gate
     20, and written here, before PR-7 turns the rung on (§3.13).
6. **Every command carries a *set* of effects, and only one of them is about what comes after it.**
   A closed table of mutating forms with "a form outside the table is not a mutator" underneath is
   a blacklist however carefully it is drawn, and three bash forms walk through it: `printf -v
   PATH …` and `read PATH <<< …` assign `PATH` with no assignment syntax, and
   `hash -p <path> git` rebinds a command name without touching `PATH` at all (§3.15). PowerShell
   is the same shape, where the Environment provider takes the whole `*-Item` family, so
   `Copy-Item Env:\A Env:\PATH` and `Rename-Item Env:\A PATH` are mutators no list of four
   `*-Item` rows names. So the quantifier inverts:

   - Every entry in a dialect's trusted set — 5e's cmd internals, 5g's cmdlets and aliases, the
     POSIX table, and each rung's builtin set — carries a **set of flags**, decided **with the
     arguments it was given**. The flags are **not** mutually exclusive: `.`, `source`,
     `Import-Module`, `eval` and `Invoke-Expression` execute input **and** rebind the caller,
     which is the whole reason they exist.

     | Flag | Claim | Consequence |
     |---|---|---|
     | *(no flag)* — inert | writes no environment variable, binds no name, changes no current location or provider drive, and runs no input the floor has not lowered | trusted; nothing else follows |
     | `rebinds_after` | changes what a later name in **this** body resolves to | every command after it here is opaque |
     | `executes_input` | runs the contents of a file or a string as code | **this command is itself opaque.** The single exception is a **literal string with no `Dynamic` token**, which is re-entered as a body in this dialect (rule 4a) — the floor can do that because the string is part of the command line it already scanned. **A file target is always opaque, whatever its path looks like** |
     | `rebinds_caller` | its effects land in the **invoker's** scope, not a child process | see the propagation rule below |

   - **The file form is opaque because a static path is not immutable bytes.** Allowing "a
     literal path whose contents are read" confuses the name with the content: the floor reads
     `safe.ps1`, decides, and then the child re-opens that path at execution time. `Set-Content safe.ps1 evil; . .\safe.ps1` does it inside one body; another process
     does it between the decision and the launch. Executing the analysed snapshot instead of the
     path is not available — the floor does not run the script, PowerShell does, and it opens the
     path. Nothing here tracks ordinary file writes either, and adding that would still leave the
     concurrent writer. So the file form gets no exception, which also deletes the effect-state
     bookkeeping such an exception would have needed.
   - **A recursive analysis returns an exit-state summary, and same-scope invocations propagate
     it.** Analysing a target answers "did this body do anything the caller must know about",
     which is **not** the same question as "was any command inside it poisoned". Take a
     `safe.sh` whose only line is `hash -p ./evil git`: analysed on its own that is a
     `rebinds_after` with no successor, so nothing inside it is opaque and a per-body rule passes
     the file. Then `source ./safe.sh; git status` runs `git` against a rebound hash. So the
     recursive analysis returns a summary — *did this body exit with names rebound* — and a
     caller invoking it with `rebinds_caller` set carries that into its own state, making every
     command after the invocation opaque. `bash ./safe.sh` does not: a child process's rebinding
     dies with it, and only `executes_input` applies. **A rule about the last element of a
     sequence has to say what the sequence *leaves behind*, not only what it poisoned on the
     way.**

   - **`executes_input` is what stops `Import-Module .\evil.psm1`, `. ./evil.sh` and
     `source ./evil.sh` from passing as the last command in a script.** Classified as name
     rebinding alone, they have nothing after them to poison, and each has already run a file the
     floor never saw. Turning autoloading off (D4) does not touch an explicit import. The flag also covers
     `Invoke-Expression` and `iex`, `&` and `.` on a path, `-File`, `eval`, and
     `bash`/`cmd`/`pwsh` given a script path — which is why rules 1, 4a and 5d already re-enter
     or refuse those, and rule 6 now says the same thing once for the whole set rather than case
     by case.
   - A command word resolving to **no entry at all makes *this* command opaque, and everything
     after it as well.** Poisoning only the successors was a hole with a one-line exploit: a
     script whose single command is an unclassified program has no successor to poison, so the
     floor allowed exactly the case it knew least about. An unrecognised name is not "not a
     mutator" and not "probably harmless" — it is *not established to be inert*, which on these
     dialects is the same thing as opaque. **This is the rule that makes "closed" literal**, and
     it is why §9 q9 — how wide the inert set is worth making — now decides what runs at all
     rather than only what poisons a successor. The cost is stated here rather than discovered
     at PR-7: on a fresh PowerShell or cmd rung every program outside the trusted table is DENY
     until someone adds a row with its effects. Nothing carries `executes_input` implicitly — an
     unknown name that ran code would have run it before any rule applied, which is what rule 0
     and the closed runnable set exist to prevent upstream.
   - In PowerShell an argument naming a non-filesystem provider drive — matching
     `^[A-Za-z][A-Za-z0-9]*:` and not a drive-letter filesystem path — makes its command
     non-inert whatever the cmdlet is. That closes `Env:`, `Alias:`, `Function:`, `Variable:`
     and the registry drives with one rule instead of one row each.
   - A `Dynamic` token in any position the inert claim depends on → opaque, as everywhere else
     (D3).

   The `executes_input` set per dialect, with `rebinds_caller` marked where the effects land in
   the invoker's scope: PowerShell `Import-Module` and `ipmo` **(+caller)**, `Invoke-Expression`
   and `iex` **(+caller)**, `.` on a path **(+caller)**, `Add-Type` **(+caller)**, `&` on a path,
   `-File`; cmd `call <file>` **(+caller)**, `start <file>`; bash `.` and `source`
   **(+caller)**, `eval` **(+caller)**, and any interpreter invoked on a script path. The
   enumerated mutating forms survive as **gate rows, not as the rule** — `set`, `path`, `setx`,
   `call set`, `for /f … do set` on cmd; `$env:`, `Set-Item`, `Set-Content`, `New-Item`,
   `Remove-Item`, `Clear-Item`, `Copy-Item`, `Rename-Item`,
   `[Environment]::SetEnvironmentVariable` on PowerShell; `PATH=`, `export`, `declare -x`,
   `env PATH=…`, `printf -v`, `read`, `hash -p`, `alias`, a function definition, `.`/`source`,
   `BASH_ENV=`, `ENV=` on bash. Adding a form to that list changes no behaviour; it adds a test
   the rule already passes. What the list cannot do any more is decide the cases it omits.
7. **Process-spawning commands are opaque, and re-entry is how they are refused rather than how
   they are allowed.** Rule 2's argument is not about the word `pwsh`: a launch is trustworthy
   only where agentao wrote the command line, and every command in this rule hands the launch to
   something else. **The three that were still permissions** —
   `Start-Process` / `saps` / `start`, `Invoke-Item` / `ii`, and cmd `start`
   (`codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs:47-53`) — resolve
   their target through **ShellExecute**: the current directory, then the file association, then
   PATH. That is not 5g's resolver, so the table 5g was measured against says nothing about what
   runs, and an association is a registry entry no rule here reads. `Start-Process` then changes,
   one parameter at a time, exactly what D4 pins: **`-UseNewEnvironment` re-reads the user's
   environment from the registry**, which puts the unfiltered `PATH` back inside a body the floor
   allowed; `-WorkingDirectory` moves the directory ShellExecute searches first;
   `-Environment` (7.4+) replaces the environment outright; `-Credential` and `-Verb RunAs`
   change the **subject**, which is the predicate 5a's image half is written against (D4).
   **The rest** — `Start-Job` / `sajb`, `Invoke-Command` / `icm` in every remote parameter set
   (`-ComputerName`, `-Session`, `-ConnectionUri`, `-VMId`, `-VMName`, `-ContainerId`,
   `-HostName`, `-SSHConnection`), and the **trailing `&` job operator**, which is `Start-Job`
   spelled as syntax — run in a separate process or on another machine, with no prelude,
   autoloading back to its default, and an interpreter nobody authenticated. So the verdict for
   this whole rule is **opaque**, and modelling the launch surface well enough to allow any of it
   would mean proving the final image, environment and subject match D4 — which is a design, not
   a parameter list, and is not in this plan. Re-entry stays: it refuses a dangerous target for
   its own reason. cmd `start`'s grammar — optional quoted title, switches, then a target under
   5a–5c and 5e — survives for that refusal, not for a permission. Gate 26 pins one case per
   reason, and `&` gets a row because which node kind tree-sitter gives it is unverified here.

### D6 — argv launch; configuration by source, whole; one snapshot through every root

`agentao/capabilities/shell.py:141-143`. User-scope `permissions.json` only
(`agentao/embedding/permission_loader.py:131-136`, `agentao/embedding/permission_loader.py:11-14`);
precedence by source, whole (`agentao/embedding/factory.py:144-145`,
`agentao/embedding/factory.py:146-148`):

| Source, highest first | Provides | Lower sources are |
|---|---|---|
| Constructor: `shell=` executor, or `shell_dialect=` / `shell_path=` | the whole spec | **ignored** |
| User-scope `permissions.json` `shell` block | the whole spec | ignored |
| `auto` | D4's ladder | — |

**The Git Bash rung's switch is a field of that spec, not a separate mechanism.**
`shell.allow_git_bash` is a boolean in the same user-scope `shell` block and the same constructor
spec, **default `false`**, and it is read **before the last rung is chosen**, not after. The
ladder is `pwsh` → `powershell.exe` → Git Bash → `cmd`: the switch admits or removes the Git
Bash rung, and `cmd` is the fallback either way — when the switch is off, and when it is on and
no Git Bash is found. **"Either way" is about the switch, not about trust:** a `cmd` that fails
D4's identity or location test is refused like any other rung, and the ladder can therefore end
with nothing selected, which D4 answers with `hardline:no-trusted-rung-opaque` rather than a
silent return to `%COMSPEC% /c`. **A switch under `cmd` would be unreachable**, since `cmd.exe`
exists on
every supported Windows and the ladder always stops there; that was the earlier position, and it
made the switch dead — `true`, rung installed, Git Bash never selected, gate 11 green, gate 20
exercising a path production could not take. Gate 11 now pins the order in **both** switch
states, and "red ⇒ ships off" sets the default.

One immutable `PermissionConfig { rules, sources, shell }` through
`agentao/acp/session_new.py:366-374`, `agentao/acp/session_load.py:262-270` and
`agentao/embedding/factory.py:186-192`. The sub-agent factory reads no file.

### D7 — `cmd` is a regex dialect; control flow, grouping, and every variable form are opaque

Deliverable: the CMD row of §3.6, every §3.5 class with a cmd spelling, rule 5e's internal table,
`start`'s grammar, D3's cmd rule, and rule 6's cmd row. Any of `if`, `else`, `for`, `do`,
`goto`, `call`, or any syntactically valid grouping parenthesis makes the body opaque; quoted or
`^`-escaped parens are literal. Last rung.

## 5. The PR ladder

| PR | What | User-visible | Depends on |
|---|---|---|---|
| **PR-0** | (gates 0, 19, 22) **`Agentao._for_subagent`: parent's engine, one effective fs/shell, `origin` recorded on every registration, registry rebuilt by re-running `register_builtin_tools` against the sub-agent, `ToolForkable`, MCP owner thread + non-owning scoped view, no agent-tool registration; engine state immutable behind one writer lock, decisions carrying their snapshot; projection reports the decision's snapshot** (§2.12–§2.19) | no — closes a live bypass | — |
| PR-1 | `ShellDialect` **and the spec's `rung` and `filesystem_is_local` fields, with the dialect × rung matrix validated at construction** (D2); **`ShellRequest` carries the argv and environment agentao built, which the executor runs verbatim** — today it carries `command: str` and `env` only (`agentao/capabilities/shell.py:77-84`); executor declares; tool exposes; `ShellSpecProvider`; `_decide` passes; D1 on replacement | protocol change | PR-0 |
| PR-2 | **State-free primitives only:** token IR + rule 0's lowering pipeline + codex's fixture corpus + danger tables + cmd internal table + the effect flags on every trusted entry + the D5 rules that need no runtime state | no | PR-1 |
| PR-3 | Presets; rule `dialect`; `PermissionConfig`; user-scope `shell`; propagation | no | PR-2 |
| PR-4 | Trusted resolution — install roots **the agent's subject cannot write**, the **host-side identity oracle** (ACL, signature, hash; injectable, so gate 4's positive case is testable off Windows) and the install-root (`$PSHOME`) resolution (D4) — + bare-word resolvers (5e, 5g, 5h) + **the per-identity cmdlet/alias table, since it must be measured in the startup state this PR establishes**, and 5g's dependence on the preflight field + child environment (filtered PATH, `PATHEXT`, pinned `PSModulePath`, `-p`, `BASH_ENV`/`ENV`/`BASH_FUNC_*` removed) + the disk-read config refusal + per-rung command lines | no | PR-2, PR-3 |
| PR-5 | System-prompt guidance per dialect (`agentao/prompts/sections.py:199-202`, `agentao/prompts/sections.py:206`, `agentao/prompts/sections.py:208`, `agentao/prompts/sections.py:222`) | no | PR-1 |
| PR-6 | `windows-latest` job: D4 matrix, §3.12 sentinels, gates **18, 20, 21, 23, and gate 25's Windows half** — the set is not a range: gate 19 (the only test of PR-0's writer lock and carried snapshot) and gate 22 are PR-0's and platform-independent, and gate 25's `root`-in-container half runs on ubuntu | no | PR-3, PR-4, PR-5 |
| PR-7 | The flip; **the Git Bash rung behind its own switch, on only if gate 20 is green** (`packages/coding-agent/src/core/tools/powershell.ts:16`, `codex-rs/shell-command/src/powershell.rs:15`, `agentao/tools/shell.py:156-160`, `agentao/plugins/hooks/_alias.py:16`) | **yes** | PR-6 |

**PR-0 needs nothing from PR-1** — an internal factory, an origin field on the registry, a
protocol, a view, an owner thread, a lock, a token-to-**task-set** registry with the call context
that feeds it, and a field on the decision detail. **PR-4 needs PR-2 and PR-3, not just PR-1:** its
bare-word resolvers hand words to rule 5, its trusted tables carry rule 6's effect flags — both
PR-2 — and the `shell` block it reads for `shell.path` and `allow_git_bash` arrives with PR-3's
`PermissionConfig`. **PR-2's dependency:** `tree-sitter` and `tree-sitter-powershell` under
`[project.dependencies]` with `sys_platform == "win32"` and under `[dependency-groups].dev`
(`pyproject.toml:117-125`) unconditionally.

**Five open questions are decision gates before PR-2, not backlog.** §9 q2, q3, q9 and q11 fix
the danger tables, the inert set and cmd's `rebinds_caller` scopes — all PR-2 deliverables — and
since rev 20 an absence from the inert set is a DENY rather than a successor poison (D5 rule 6),
so "PR-2 is done" is not a statement anyone can make while they are open. **q4 is the fifth**:
it does not change what PR-2 builds, because the `rung` field lets the primitives ship with
`system_posix` untouched (D2) — it decides that default, and a default that arrives with the
code and is never chosen is the kind of decision this ladder exists to make explicit.

## 6. Release gates

0. **PR-0's probe** (§2.12) returns DENY through a `NullTransport`, foreground and background;
   a parent with an in-memory deny, a run-scope deny and `enable_hardline=False` produces
   sub-agents honouring all three; a readonly parent produces a readonly sub-agent; the
   sub-agent's engine, filesystem and shell are the parent's by identity and its tools are not
   the parent's instances; after a background sub-agent runs a tool, the parent's
   `output_callback` and todo list are unchanged; a built-in the parent disabled is absent
   from the sub-agent; a forkable host tool outside the whitelist is absent; a non-forkable host
   tool that replaced `read_file` leaves the sub-agent with no `read_file`; a sub-agent whose
   definition whitelists no agent tool has zero `agent_*` tools; a parent and a background
   sub-agent calling the same MCP server concurrently both complete correctly. The sub-agent's
   `ask_user` reaches the sub-agent's transport and its `todo_write` its own list — the six
   agent-supplied built-ins are constructed from the child (§2.19); every registered tool
   carries an `origin`, and a host tool that replaced a built-in records what it displaced; a
   sub-agent's `close()` leaves the parent's MCP connections and loop alive, **and leaves a
   sibling sub-agent still running untouched — it cancels no token but its own and joins no
   thread, least of all the one it is running on**; a tool registered
   through bare `agent.tools.register(tool)` still registers, as `host`. **And the other
   direction: closing the parent while a background sub-agent is inside a long MCP call cancels
   and joins it before disconnecting, and a `result(timeout)` that expires leaves no coroutine
   running on the owner loop. The same holds when the in-flight call is on a **foreground turn
   or a host's own thread**, which is in no thread set: `close()` waits for the lease to drain
   and the call completes. **And the cancel is asserted to *arrive*, not merely to be issued:**
   a background sub-agent inside a long MCP call has its `bg_store` token cancelled, and the
   registered task is cancelled on the owner loop, its `finally` releases the lease, and the
   loop holds no live task afterwards — a test that only checks `close()` returns would pass on
   today's code, where `McpTool` never receives the token at all
   (`agentao/mcp/tool.py:118`, `agentao/runtime/tool_executor.py:351-352`). **Two barriers, both
   about the shape of the registry rather than the fact of it:** a sub-agent issuing **two**
   parallel MCP calls in one batch — one token, two tasks — has **both** cancelled and both
   leases released, which a one-task-per-token registry fails; and a token cancelled **before**
   its task is registered still cancels that task, asserted by holding the registration at a
   barrier until after `cancel()` returns, on the ordinary `bg_store.cancel()` path that never
   enters `CLOSING`.**
1. PR-1: the `ShellExecutor` fake is the only forced test edit; `PermissionEngine(` untouched.
   **And the unlabelled dialect has a verdict:** a custom `ShellExecutor` reporting `UNKNOWN`,
   and one reporting a value outside the enum, each produce `hardline:unknown-dialect-opaque`
   ⇒ DENY before any rule matches — asserted on a body that no POSIX pattern would flag, so a
   fallback to the POSIX scanner fails the gate instead of passing it silently (D2). **And the
   `rung` the same way:** each legal pair constructs, `POWERSHELL × system_posix` and an
   unrecognised rung both **fail spec construction** naming the pair, and a spec that reaches the
   floor with either anyway returns `hardline:unknown-rung-opaque` ⇒ DENY — asserted, again, on a
   body no POSIX pattern flags, because the implementation error being gated is "route the
   unknown case to `system_posix`", whose policy is off (D2).
2. Every floor test for every dialect runs **on ubuntu**, with the parser from the `dev` group.
3. Each of §3.5's 18 classes: PowerShell translation and CMD row or explicit line. **And the
   node table is pinned to the grammar:** every kind in D5's step 5 table is produced by the
   pinned parser for some input, so a grammar upgrade that renames one fails this gate rather
   than silently turning a `REFUSED` kind into an `ACCEPTED` one.
4. **The closed runnable set, both halves (D5 5a, rule 6):** `.\innocent.exe` as the **only**
   command in the script is **opaque** — the working tree is no trusted root, and the floor
   analysed neither the image nor its effects; a `git.exe` **copied into the working tree** and
   invoked by that path is **opaque** although its basename has a trusted entry (a name without
   an image); an **unclassified** program invoked by absolute path out of a trusted directory is
   **opaque** (an image without a name); a `git.exe` planted in a **user-writable directory that
   is on the machine's PATH** is **opaque**, and the same directory is **absent from the child's
   `PATH`** — the case that passed both halves while any PATH entry counted as a trusted root,
   and the one neither working-tree counterexample reaches (D4); and a signed `git.exe` under an
   root the subject cannot write, with a trusted-table entry, is **allowed**. Each opaque case is
   asserted to fail for its own reason, since they would otherwise pass for the wrong one.
   **And the allowlist is asserted not to stand alone (D5 5a):** an allowlisted absolute path
   whose directory is user-writable is **opaque** on the location alone, with its hash and
   signature valid; the same path **replaced inside the body** —
   `Copy-Item .\evil.exe <allowlisted path>; <that word>` — is **opaque**, and so is the version
   where a second process replaces it between the floor's hash and the child's open, both
   asserted to fail on the location rather than on the hash, since a hash check that happens to
   fire would hide the rule being tested. **The positive case runs where it can:** "signed, under
   a root the subject cannot write, with a trusted-table entry, allowed" is a stub of the
   identity oracle on ubuntu (gate 2) and the real ACL check on the Windows job, **written into
   gate 23 rather than pointed at it** — a case that cannot be produced on the platform its gate
   runs on is not a gate, and a case no gate schedules is not one either (D4). The signature is
   defence in depth inside that root, not the condition that admits the file (D5 5a).
   Then PowerShell adversarial cases, plus every PowerShell form in rule 6's gate list
   followed by a command (**opaque**), `Copy-Item Env:\A Env:\PATH; git` and
   `Rename-Item Env:\A PATH; git` (**opaque** by the provider-drive rule, which no row names),
   an unrecognised cmdlet followed by a command (**opaque** — no entry is not inert), and
   `Get-Date; git status` (**allowed** — an inert entry). **`executes_input` on its own, with
   nothing after it:** `Import-Module .\evil.psm1`,
   `. ./evil.ps1` and `& ./evil.ps1` as the **only** command in the script, and each again as the
   **last** of several — all **opaque**, and now **opaque even when the target is a literal path
   the floor could read**, with `Set-Content safe.ps1 evil; . .\safe.ps1` and a
   concurrently-rewritten `safe.ps1` as the two cases that rule out any literal-path exception;
   the same three followed by `git status` are opaque for the same reason and not because of the
   successor. On bash: `. ./evil.sh` and `source ./evil.sh` alone. **`rebinds_caller`
   propagation:**
   a `safe.sh` whose only line is `hash -p ./evil git` is analysed and found to contain nothing
   opaque, and then `source ./safe.sh; git status` is **opaque** — asserted to fail on the
   propagated exit state, not on the `source` itself; `bash ./safe.sh; git status` is opaque for
   the *other* reason (an unlowered child) and a lowered-and-inert `helper.sh` followed by
   `git status` stays **allowed**, so propagation is not just a blanket refusal. **Rule 0 is
   tested against codex's corpus rather than examples of my choosing:** all 68 cases of
   `powershell_lowering.json`, with the 44 `null` rows required to be opaque here too, and the
   step each fails at asserted by name — a case that becomes opaque for the wrong reason is a
   failure. Named separately because each reaches a different step:
   `git status --short#; Remove-Item victim` (step 8, source fidelity), `Remove-Item test –Force`
   (step 1, Unicode alias), `git log --% HEAD` (stop-parsing), `using module ./x.psm1` (step 9),
   an attached parameter value and a hex or leading-zero numeric bare word (**step 7**, argv
   lowering), `$Function:git = { & C:\evil.exe }; git` and
   `[Environment]::SetEnvironmentVariable('PATH','C:\x'); git` (step 5, node kind), and
   `#Requires -Modules Evil` followed by a trusted bare word, plus the same with leading
   whitespace and mixed case (step 4) — while an ordinary `# comment` followed by that word stays
   **allowed**, so step 4 catches the directive and not the comment. **The 24 non-`null` rows are
   gates too, in the other direction, and compared the way codex compares them:** not "lowers
   successfully" but **the whole lowered `argv` equal to the fixture's `expected`**, which is what
   its own test asserts
   (`codex-rs/shell-command/src/command_safety/powershell_tree_sitter_tests.rs:22-24`). Asking
   only for success lets wrong quoting, a wrong escape or a mis-split parameter boundary pass and
   then hand a wrong value to the argument predicates that decide danger. `a | b`, `a; b` and a
   trailing comment are among these rows.
5. Launch-parameter cases per stem and past it.
6. CMD adversarial cases, plus every cmd form in rule 6's gate list — `path C:\x & git`,
   `setx PATH …`, `set "PATH=…"` (**opaque**) — and an internal command flagged inert followed by
   `git` (**allowed**).
7. **bash cases:** `PATH=/x git`, `export PATH=…; git`, `BASH_ENV=./p bash -c …`, `alias
   rm=…; rm`, `. ./f; rm` (**opaque**); **`printf -v PATH /x; git`, `read PATH <<< /x; git` and
   `hash -p ./evil git; git` (**opaque** — the three §3.15 measured); an unrecognised builtin
   followed by `git` (**opaque**)**; bare `git` resolving through the filtered PATH
   (**allowed**); bare `evil` not on the filtered PATH (**opaque**); and bare `evil` **on** the
   filtered PATH but absent from the POSIX table (**opaque** — an image without a name, D5 5a).
   **And the rung is asserted to key something:** every verdict above is taken under a spec whose
   `rung` is `git_bash`, and the same bodies under `system_posix` produce **today's** verdicts —
   a pair, because a policy that cannot be selected against is indistinguishable from one that is
   always on, and §9 q4 is open precisely so that it is not (D2).
8. Opaque refused through `NullTransport` and by a PowerShell sub-agent.
9. Lowering rate in three buckets, accepted before PR-7. `uv run ruff check .` green.
10. Windows matrix per rung. 11. Ladder order pinned **in both `allow_git_bash` states**: off
    ends the ladder at `cmd`; on selects Git Bash ahead of `cmd` where Git Bash is present, and
    falls back to `cmd` where it is not — so the switch is exercised on the path production
    takes (D4, D6). 12. `shell` in `settings.json` / project
    file per D6. 13. Snapshot on every root; sub-agent engine by identity. 14. Provider
    missing / conflicting refused. 15. No working-tree binary resolved. 16. Source-whole
    precedence. 17. Registry identity after construction, `add_tool`, `remove_tool`.
18. On the Windows job: `NoDefaultCurrentDirectoryInExePath=1`; sentinel bodies byte-identical;
    child `PATH` and `PATHEXT` as pinned, **with a user-writable directory that is on the
    machine's PATH absent from the child's** (D4); `git.cmd` vs `git.exe` runs the `.exe`; spaced
    cmd path invoked as that interpreter.
19. **Concurrency, multi-writer, and immutability:** while a background sub-agent decides in a
    tight loop, the parent interleaves `set_mode` ×1000, `add_run_rules` ×100 with a **distinct
    deny each time**, and `active_permissions` ×1000 from three threads; afterwards every one of
    the 100 denies is present, every decision's carried snapshot is internally consistent, and
    every projected event names the snapshot of its own decision. Separately, and without
    threads: mutating the list passed to the constructor, the list returned by `rules`, a returned
    `ActivePermissions`, and a decision's carried snapshot each leave every subsequent verdict
    unchanged, and a preset list is unchanged for a second engine built afterwards.
20. **Git Bash on the Windows job:** with `BASH_ENV` set in the parent environment to a
    working-tree file, the child runs the body only; with `BASH_FUNC_git%%` exported, bare `git`
    is `/usr/bin/git` and not the function (§3.16), **and the same is asserted two processes
    down**: a trusted command that itself runs `/bin/sh -c` sees no `BASH_FUNC_*` in its
    environment, which `-p` alone does not give and only the scrub does (D4); `/c/Users`-shaped
    and `C:\Users`-shaped arguments reach the body unchanged under `MSYS_NO_PATHCONV=1`; bare
    `git` runs the trusted `git.exe`; an `evil.sh` in the working tree is not run by bare `evil`.
    **And it measures what 5h leaves unstated:** with an extensionless `git` script beside
    `git.exe` in one trusted
    directory, which one bare `git` runs — the answer is written into 5h before the rung ships.
    Red ⇒ PR-7 ships with the rung off.
21. **PowerShell edition matrix on the Windows job:** the same script under `powershell.exe` and
    under `pwsh`, with each interpreter's own measured table; a bare word that is an alias in one
    edition and absent in the other decides differently in the two, and an interpreter whose
    recorded identity matches neither table is **opaque**. **Autoloading, measured from inside
    the child and adversarially:** a module exporting a function named `git` is placed in the
    **CurrentUser module directory, outside the working tree**, which is the case a
    "no working-tree path" assertion passes and this one does not — the child reports
    `$PSModuleAutoLoadingPreference` and what bare `git` resolves to, and it must resolve to the
    trusted `git.exe` and never to the module. The prelude is asserted not to disturb the body:
    a body whose first statement has an observable effect produces that effect, unchanged, with
    the prelude in front of it. **With a `powershell.config.json` in either scope selecting a
    non-default session configuration, the rung refuses without starting the interpreter even
    once** — asserted by giving that configuration a startup script that writes a sentinel file,
    and requiring the file not to exist; a design that asked the interpreter what its
    configuration was would have run that script to find out. Where the preflight could not
    establish the closed environment, the floor treats every PowerShell bare word as opaque and
    the gate asserts that degradation, not a failure. **And the prelude's guard is asserted to
    abort, not merely to report:** with the preference forced back by a session configuration, a
    body whose first statement has an observable effect produces **no** effect at all and the
    launch exits non-zero — a gate that only checked the success path and the degraded verdict
    would never have run the body that must not run. **The TOCTOU pair, both directions:** change
    the configuration, and separately swap the interpreter behind the resolved path, *after* the
    preflight and before a launch — the guard's identity check fails, it exits non-zero, and the
    body's side effect never happens. **Two of gate 21's items are characterization probes, not release
    gates, and the difference is now explicit.** A release gate red blocks the flip; a
    characterization probe records measured behaviour for a residual §7 already declares, and its
    expected result is written into the probe. The two probes: (a) the configuration installed
    after the preflight — the **startup sentinel is expected to exist**, because that script runs
    before the prelude, and the assertion is that the *body's* side effect does not; (b) the
    interpreter replaced with one matching every recorded field and the recorded hash — expected
    **not** detected. Both are `xfail`-style with the expectation stated, so a change in either
    direction fails the suite. Everything else in gate 21 is a release gate. **"A gate allowed to
    be red" is not a category this suite has** — a gate that may be red is a gate that gates
    nothing.
22. **Recursion and the default whitelist:** a sub-agent of the built-in generalist
    (`agentao/agents/definitions/generalist.md:1-4`), whose definition has no `tools:` key, has
    every non-agent tool and no `agent_*` tool at all, so it cannot spawn itself; a definition
    that names an agent tool explicitly gets that one and no other.

23. **Interpreter discovery and identity, host-side (D4):** a `pwsh.exe` planted in a
    user-writable directory that is on the machine's PATH is **never auto-selected** — that
    directory does not survive the filter either (D4), and the
    assertion is that it is not *started*, by giving the planted binary a body that writes a
    sentinel file and requiring the file not to exist; the same binary named explicitly through
    `shell.path` **is** selected, which is what makes the two tiers different rather than
    inconsistent; an unsigned image inside an otherwise-known install location is refused; and a
    launcher whose own directory is **not** `$PSHOME` — a shim, a symlink or a copy — has its
    AllUsers `powershell.config.json` read from the resolved install root, or the rung refused
    when that root cannot be resolved host-side, never read from the launcher's directory
    (§3.20). **And the positive case lives here rather than being pointed at from gate 4:** a
    `git.exe` under a root the agent's subject cannot write, with a trusted-table entry, is
    **allowed** — with a real ACL check against the child's token, and with **no signature and no
    allowlist entry**, so the gate cannot pass by treating either as the admitting condition
    (D5 5a).
24. **Nested launches and non-local executors (D5 rule 2, D2):** `pwsh -NoProfile -Command "git
    status"` inside a body is **opaque** although every byte of the nested body is allowed on its
    own, and `pwsh -Command "Remove-Item -Recurse -Force C:\"` is refused as a **danger-table
    hit** (§3.6) inside the re-entered body, so the two are distinguished by reason and not only
    by verdict; `cmd /c git status` and
    `bash -c 'git status'` likewise. And a spec with `filesystem_is_local` false and no executor
    oracle makes every command word needing an image opaque — as does a spec that omits the field
    entirely, since absent means `false` — while the same body under a local spec keeps its
    verdicts. **With an oracle supplied, the verdicts follow the target and not the floor:** a
    bare word that resolves on the target's PATH and not on the floor's is **allowed**, one that
    resolves on the floor's and not the target's is **opaque**, and `Start-Job { … }` is opaque
    on both (rule 7). A check that reads the wrong filesystem passes for the wrong reason (D2).

25. **The elevated posture has a verdict (D4):** with agentao running as an administrator on
    Windows, or as `root` in a container, every candidate root is writable by the subject, so the
    trusted set is **empty** and the rung is **refused** — asserted with the sequence that makes
    it matter, `Copy-Item .\evil.exe 'C:\Program Files\Git\cmd\git.exe'; git status`, which
    must not reach a verdict of allowed at any point. **Unprivileged, that same body is
    *allowed*** — the floor has nothing to refuse, since `Copy-Item` to a filesystem path is
    inert and `git` passes both halves — **and the copy fails at the OS**, so the `git` that runs
    is the trusted one. That is the pair: the same text, one verdict under each posture, since a
    predicate that never changes answer is not being evaluated. **And the exhausted ladder is
    asserted too:** with every rung refused, a shell call returns
    `hardline:no-trusted-rung-opaque` and the tool is still registered — not absent, and not
    falling back to `%COMSPEC% /c` (D4, D6).
26. **Rule 7's wrappers, one case per reason (D5 rule 7):** `Start-Process git` is **opaque**
    because ShellExecute is not 5g's resolver; `Start-Process -UseNewEnvironment git` is opaque
    **and asserted on the environment reason**, since that switch alone restores the unfiltered
    user `PATH` inside an allowed body; `Start-Process -Verb RunAs git` on the subject reason;
    `Invoke-Item .\x` and cmd `start x` on the association reason; `Invoke-Command -ComputerName
    a { git status }` and `git status &` as separate-process launches. **The `&` row also records
    what this plan could not verify:** which node kind the pinned tree-sitter grammar gives the
    trailing job operator is unmeasured here, so the row asserts only that it is refused, at
    step 5 or step 8, and names which — a case whose *reason* is unknown is still a case whose
    verdict is pinned.

## 7. Non-goals

- **A `powershell` tool.** **`cmd` above PowerShell.** **PowerShell on macOS/Linux.**
- **Auditing any file the floor did not lower.**
- **Trusting any workspace file or binary for shell, bare-word, child-process, or startup-file
  resolution.**
- **Sharing tool instances or MCP tool objects between agents** — capabilities and a scoped
  view, never objects.
- **A per-sub-agent permission mode.** **A `rebind()` API.**
- **An extension-based closed set for bash.** bash has no `PATHEXT`; rule 5h says so.
- **Closing the POSIX indirection gap** — §9 q4.
- **Closing the session-configuration TOCTOU.** A console session configuration installed between
  rung resolution and a spawn runs its startup script before the prelude that would refuse it. The
  window is narrowed by re-reading all three sources immediately before spawn, and not closed
  (D4); gate 21 probe (a) measures it.
- **Authenticating the interpreter's load closure.** The preflight hashes the launcher;
  `System.Management.Automation.dll` and everything else the process loads are outside that
  hash, and on Windows that assembly's directory *is* `$PSHOME` (§3.20). The identity claim
  rests on the install root being unwritable by the subject and the image being signed, so a writable
  install root defeats it — and the design **refuses** such a root (D4) rather than claiming to
  cover it; gate 23 asserts the refusal.
- **Authenticating the interpreter from inside it.** A replacement binary at the resolved path
  matching the recorded edition, version, `$PSHOME` and content hash is not detected, and it holds
  control before the guard parses. The window is narrowed by re-hashing immediately before spawn,
  and not closed (D4); gate 21 probe (b) measures it. **These two are the reason `Scope` no longer
  says "startup files" without qualification.**

## 8. What would change this plan

- **`tree-sitter-powershell` stops shipping the wheels.** **A measured Windows user population
  of zero.** **An unusable opaque bucket.**
- **PowerShell, cmd, bash or Windows changing any semantics this plan pins** — `MatchSwitch`,
  command precedence, `PATHEXT`, `Start-Process`, profiles, `/s`, `start`, grouping,
  `BASH_ENV`, `NoDefaultCurrentDirectoryInExePath`, `lpApplicationName`.
- **agentao adopting a workspace-trust model.**
- **An MCP tool wrapper found to hold per-agent state** — gate 0's concurrent-call check is
  where it appears.

## 9. Open questions

1. **What lowering distribution is acceptable?**
2. **The Windows analogue of `cryptsetup luksFormat`.**
3. **codex's URL-bearing-launch class.**
4. **Should the `system_posix` rung adopt D3's token rule, rule 6's inertness requirement and
   the closed runnable set?** On Linux all three would be a behaviour change for every existing
   user, and since rev 20 rule 6 in particular makes an unrecognised command word **deny the
   call it appears in**, not merely poison the rest of a script the floor allows today. The
   `rung` field (D2) is what keeps the question open instead of answering it by shipping: the
   Windows rungs have the policy on, `system_posix` does not, and §5 lists this as the fifth
   decision gate before PR-2 so the default is chosen rather than inherited.
5. **Does the hook payload need the dialect as a field?**
6. **Reserve `run_shell_command` outright?**
7. **Should a bare `Agentao(...)` construct a default engine?**
8. **Should `_for_subagent` become a public `Agentao.fork(...)`?** Hosts that spawn their own
   sub-agents have the same problem the wrapper had.
9. **How wide is the inert set worth making?** A minimal one is safe and refuses a great deal;
   each entry added is a claim someone has to check. **Since rev 20 this decides what runs at
   all** rather than only what poisons a successor (D5 rule 6), which is why §5 makes it a
   decision gate before PR-2 instead of an open-ended question.
10. **What is prior to the kind gate that codex has not found either?** Rule 0 mirrors codex's
    pipeline, which is only as good as codex's own coverage — its accept-list comment says the
    refusals stand until each kind's lowering semantics are reviewed, so those gates are a floor
    someone else drew for a different policy. Every gap found in this area so far was found by
    reading that source, not by anything in this document's method.
11. **Which `rebinds_caller` forms carry which scope in cmd?** The PowerShell and bash cases are
    well documented; `call` and `start` are not, and the flag is only worth what its per-dialect
    table is worth.
12. **How does a user-installed toolchain become runnable?** With the allowlist demoted to an
    additional condition (D5 5a), a `uv`, a python.org Python and a scoop shim — installed under
    a user-writable prefix **by design** — have no route into the trusted set: not the filtered
    PATH, which drops their directories, and no longer the allowlist on its own. The options are
    a host that installs them under a root the agent's subject cannot write, an explicit per-path
    trust grant the user makes the way `shell.path` does for the interpreter — which is a
    **documented exception in the shape of D4's tier (b)**, not a second route into the trusted
    set, and it carries the same TOCTOU said out loud rather than inherited — or accepting the
    denial. On a developer's own machine
    this is most of what they run, so it is a decision with a user-visible answer, not a
    footnote.

## 10. Citation method

Every `file:line` resolves at the anchors under `scripts/check_citations.py`. §3.10 and §3.20
each carry a commit, a full hash and a re-fetch; §3.11, §3.12 and §3.14–§3.16 read and run local
software. No rule states itself in terms of an earlier revision; where a revision number appears
in the body it is dating a *correction* — what changed and when — never a condition a rule
depends on.

**Twenty-two rounds produced these rules — the header's count, not a second one kept by hand. Each
is here because a defect got through without it, and they are ordered by how often that
happened.**

1. **Read the whole function you are borrowing from, in order, and take its test corpus with it.**
   Three rounds took one named piece of codex's lowering and left the gates around it; the
   counterexamples were in its fixture file the whole time. A defence borrowed piecemeal has holes
   at the joins nobody looked at.
2. **State every requirement where an implementer will copy it, then check the prose and the table
   agree.** A requirement stated in prose and contradicted by the normative table beside it is not
   stated at all. This produced four separate P0s. **And when two rules name the same object —
   a trusted root, a floor, a filter — check they name it at the same strength:** rev 21 found
   the filtered PATH disqualified as a trust level by D4 and admitted as a trusted root by 5a,
   in the revision that closed that very hole. A fix has to reach every rule that names the
   thing it fixed, which is a grep, not a reading.
3. **Ask what a rule quantifies over, and check both ends of any direction it has.** A fail-closed
   claim about the wrong unit is fail-open about the right one; a predicate over predecessors says
   nothing about the last element, and a recursive analysis that reports what it poisoned *inside*
   a file says nothing about what the file leaves behind for its caller.
4. **When a design says "closed", ask what the rule does with the case the list omits.** If the
   answer is "allowed", it is a blacklist. And listing a stateful checker's alphabet specifies none
   of its behaviour. **This rule was written down three revisions before it was run against 5a**,
   where the omitted case was "any explicit `.exe`" and the answer was "allowed" — a written
   rule audits nothing until someone applies it to each rule that claims the word.
5. **When it says "no lock", count the writers; when it says "as today", check today.**
6. **A check evaluated by the thing it is checking is not a check.** A guard inside the interpreter
   cannot authenticate that interpreter, and a static path is not immutable bytes. **Nor can a
   program be authenticated by launching it** — the launch is the event the check exists to
   gate, and every field a child reports about itself is reported by the suspect. Selection has
   to be decided host-side, before the first byte executes.
7. **Run the code.** §2.7, §2.12, §3.4, §3.8–§3.12 and §3.14–§3.16 exist because reasoning had the
   wrong answer for each of them.
8. **When a revision writes down a lesson, audit that revision against it first.** The round that
   recorded rule 1 violated rule 1 in the same pass. **A number about this document is a claim
   in it:** this line said eighteen rounds while the header said nineteen, because the header was
   updated and the sentence under the rules was not — every self-referential count has to be
   re-derived when the thing it counts moves.
9. **A gate that may be red gates nothing.** Separate release gates from characterization probes
   and write the expected result into the probe (§6).
10. **A branch nothing can reach is not a safeguard.** `allow_git_bash` guarded a rung below
    `cmd`, which exists on every supported Windows — so the switch, the gate that pinned the
    order and the probe that exercised the rung were all green over a path production could not
    take. When a rule has an order, check that each position in it is reachable.
11. **Changing a rule is not done until every summary, table and gate that quotes it has been
    re-read.** rev 22 rewrote 5a and left the TL;DR and §1 still offering the allowlist as an
    alternative, a gate pointing at a case no gate scheduled, and §5 still describing one task
    per token — three of the next round's findings, all of them the old text surviving in a
    place the edit did not visit. The mechanical form is a grep, run before the round closes
    rather than by the next reviewer, with three clauses the round that wrote this rule needed
    and did not have: **normalise whitespace first**, because the phrase it missed was split
    across a line break by a hyphen; **sweep each twin in its own wording**, since a translated
    document does not contain the phrase you changed; and **the checklist is every term the round
    changed**, not the one that produced the finding — rev 23 changed a predicate, a scrub list
    and a signature, swept the predicate, and left the other two in the tables.
