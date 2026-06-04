# Permission Hardening Plan

**Status:** Implementation plan, rev 3. Drafted 2026-05-03 across four review rounds. **PR 1 (P0 correctness), PR 2 (P1 optional hardline), and PRs 3–5 (P2/P3 convenience + sensitive-write preset) all landed 2026-05-04.** Plan is closed; future hardening work tracked separately. See §9 for what shipped where; §10 carries open follow-ups (notably the `bashlex`-based supersedence of PR 5's regex tier).
**Audience:** Agentao maintainers picking up the work; reviewers of subsequent PRs.
**Related docs:**
- `docs/design/embedded-host-contract.md` — `PermissionDecisionEvent` lives here
- `docs/design/path-a-roadmap.md` — locks the "embedded harness" positioning this plan must respect
- `docs/design/metacognitive-boundary.md` — the "schema + default + host-override" principle this plan re-aligned to in round 4
- `docs/guides/tool-confirmation.md` — current confirmation pipeline
- `docs/design/permission-hardening-plan.zh.md` — Chinese version

---

## 1. Why this plan exists, and why it kept being wrong

Round 1 of this work was a Hermes-import sweep — "what should Agentao adopt from `hermes-agent`'s recent updates?". Round 2 corrected technical errors against Agentao's actual code. Round 3 corrected architectural errors in the resulting plan. Round 4 — a deliberate reverse review against Agentao's locked positioning — found that rounds 1–3 had all been answering the wrong question:

> "How do we copy Hermes's hardline floor correctly?"

The right question, and the one this rev 3 finally addresses:

> "Should Agentao adopt a hardline floor at all, given that Agentao is an embedded harness — not a policy authority — and `agentao.host` exists precisely so hosts can decide policy?"

The reverse review's answer is: **partially**. The library should *offer* a hardline layer that's safe-by-default (so a CLI user or a host that hasn't thought it through is protected from prompt injection wiping their disk). But it must be **opt-out for embedded hosts** that take the policy responsibility themselves — because hardcoding "agent-can-never-do-X" inside a library that's supposed to be hostable contradicts the whole `agentao.host` contract.

This rev 3 also separates **correctness fixes** (which Agentao needs regardless of its positioning) from **policy choices** (which depend on the answer above). Correctness ships first, in its own PR, with no policy stance.

## 2. Corrections folded in from review iterations

The plan below incorporates corrections from all four rounds. Listed so future reviewers don't re-relitigate settled points.

**Round 2 (against the Hermes-import notes):**

1. **MCP retry.** `agentao/mcp/client.py:135-156` retries on *any* first-attempt exception, not only on `_session is None`. The real gap is **error classification**, not missing retry.
2. **ANSI handling.** `agentao/tools/shell.py:40` already strips ANSI escape sequences. Only OSC sequences are missing — and only matter when shell-backed file reads land.
3. **Hardline placement.** A hardline check, *if it exists*, must be a pre-check in `PermissionEngine.decide_detail()`, **not** a row inside `_PRESET_RULES` — otherwise `full-access` or user `allow` rules silently shadow it.
4. **Hardline scope.** A hardline floor, *if adopted*, carries only **unrecoverable** operations. Recoverable-but-costly commands (`git reset --hard`, `pip install`, `chmod -R 777 /tmp/x`) stay in regular preset rules.
5. **Concrete missed bug.** `agentao/permissions.py:334` does `data.get("rules", [])` inside `try/except (IOError, json.JSONDecodeError)`. Top-level non-dict JSON raises `AttributeError`, which is *not* caught.

**Round 3 (against rev 1 of this document):**

6. **Module shape.** `agentao/permissions.py` is a single 21 KB file, not a package. Inline new logic into it; do not split into a package as part of this work.
7. **`copy_context()` placement.** Capture must happen on the **parent** thread before `executor.submit()`. Calling `copy_context()` inside the worker copies the worker's empty context. The accompanying test must include an isolation assertion.
8. **Event taxonomy is not a user-vs-policy discriminator.** `PermissionDecisionEvent` fires *before* Phase 2 confirmation. User denials surface as `ToolLifecycleEvent(cancelled)`. The `reason` field is a **policy-source taxonomy**, not a user-vs-policy field.
9. **(Superseded by round 4.)** Round 3 said sensitive-write needed its own floor tier. Round 4 reversed this — see correction 11 below.
10. **Regex coverage.** Any sensitive-write regex matcher must ship with positive and negative test matrices, plus an honest statement of the coverage gap.

**Round 4 (reverse review against Agentao's positioning):**

11. **FLOOR_ASK Tier 2 was overreach.** `~/.bashrc`, `~/.zshrc`, `~/.netrc` are legitimate write targets for installers, devops scripts, and shell-config tools. A pre-check tier that "cannot be auto-allowed by `*`" forces every embedded host running such workloads to fight the framework. Sensitive-write protection of this strength belongs in **preset rules** (mode-scoped, host-overridable), not in a floor. Tier 2 is dropped in rev 3 — see §7.
12. **Hardline (Tier 1) cannot be hardcoded into `decide_detail()`.** Hermes can hardcode its floor because Hermes is the policy authority — a CLI-first application. Agentao is an embedded harness. Hardcoding "agent can never do X regardless of host config" contradicts the `agentao.host` contract that says hosts decide policy. Hardline is therefore an **opt-out layer**: default ON (so CLI users and "didn't-think-it-through" hosts are protected from prompt injection), explicit OFF for hosts that take the responsibility. See §5.
13. **The `tests/test_permissions.py:162` assertion is not a clean bug.** Under `enable_hardline=False` (literal full-access), `rm -rf /` *should* return `ALLOW` — that's what the mode says. The test isn't deleted in rev 3; it's split into a default-on case (DENY) and an explicit-opt-out case (ALLOW), preserving both contracts.
14. **Correctness fixes don't need a policy stance.** `isinstance` guard, MCP error classification, and `copy_context()` propagation are pure correctness — they apply regardless of how the hardline question is answered. They ship as their own PR (PR 1) decoupled from the hardline work (PR 2).

## 3. Priority order

Reorganized along the **correctness ↔ policy** axis. Correctness items have no policy stance and ship first.

```
P0  Correctness (no policy stance)
    ─ permissions.py top-level isinstance(dict) guard
    ─ MCP error classification
    ─ ToolRunner worker copy_context() propagation

P1  Optional hardline layer (opt-out, default ON)
    ─ enable_hardline flag on PermissionEngine
    ─ hardline pre-check in decide_detail()
    ─ test corrections (dual contract)
    ─ policy-source reason taxonomy on PermissionDecisionEvent

P2  Convenience and hygiene
    ─ Windows UTF-8 stdout/stderr enforcement
    ─ mask_secret canonical helper
    ─ OSC sequence stripping (deferred until docker/remote shell lands)
```

## 4. P0 — Correctness fixes

These three items are pure correctness and ship as one PR (PR 1). They take no position on whether Agentao should adopt a hardline floor.

### 4.1 `permissions.py` `isinstance(dict)` guard

`agentao/permissions.py:334` does:

```python
data = json.load(f)
return data.get("rules", []), True   # AttributeError if data is list/string
```

inside a `try/except (IOError, json.JSONDecodeError)` that does **not** catch `AttributeError`. Valid JSON whose top level is a list, string, or null crashes engine init.

Fix:

```python
data = json.load(f)
if not isinstance(data, dict):
    return [], False
return data.get("rules", []), True
```

One line. Defensive parity with `mcpServers` config loading.

### 4.2 MCP error classification

**Current:** `agentao/mcp/client.py:135-156` retries any first-round exception once, then surfaces the error string verbatim. No distinction between "session expired, please reconnect", "auth token invalid, do not retry", and "your tool args were wrong, do not bounce the connection".

**Plan:** Introduce a private helper:

```python
_SESSION_EXPIRED_MARKERS = (
    "session expired",
    "session not found",
    "unknown session",
    "session terminated",
)

def _is_session_expired_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _SESSION_EXPIRED_MARKERS)
```

`call_tool()` then:
- if `_is_session_expired_error(e)`: clear session, reconnect, retry once
- if it looks like an auth error (`401 / 403 / "unauthorized" / "forbidden"`): do **not** retry, surface immediately
- otherwise: surface the error directly without reconnecting

Logic change, not a refactor. ~25 lines + tests.

### 4.3 ToolRunner worker `copy_context()` propagation

**Current:** `agentao/runtime/tool_runner.py` Phase 3 dispatches to a `ThreadPoolExecutor` of 8 workers. Workers do not propagate `ContextVar` state from the parent thread.

**Plan — capture on the parent, run on the worker.** `copy_context()` snapshots the **calling** thread's context. The capture happens on the parent (submitting) thread, before `executor.submit()`; the worker invokes the captured `ctx.run`:

```python
import contextvars

# Inside ToolRunner Phase 3 dispatch — parent thread:
ctx = contextvars.copy_context()
future = self._executor.submit(ctx.run, self._run_one_tool, plan)
```

Calling `copy_context()` inside the worker copies the worker's empty context — silent failure.

**Test acceptance — two assertions, not one:**

1. **Positive propagation.** Parent calls `cv.set("X")`, dispatches a no-op tool, the worker reads `cv.get() == "X"`.
2. **Isolation.** Parent calls `cv.set("X")`, the worker calls `cv.set("Y")`, the parent observes `cv.get() == "X"` after the worker returns. Validates that workers run in a *copy*, not a shared reference.

A single positive-propagation test could pass even with the wrong placement on certain GIL orderings, masking the bug. The isolation assertion is what catches an incorrect implementation.

**Honest priority note.** No current `ContextVar` writer in Agentao depends on this. It is a structural defense for future hosts that inject OTel span context, logging session id, or tracing baggage. Ship it now because it's cheap and the test is a useful regression guard, not because there's a current bug.

## 5. P1 — Optional hardline layer

Ships as PR 2, after PR 1 lands. Independent of correctness.

### 5.1 Hardline as opt-out

**The principle this layer must respect.** Agentao is an embedded harness. Hosts decide policy. A library that hardcodes "agent can never do X regardless of host config" contradicts that. The hardline floor is therefore:

- **Default ON.** A CLI user or a host that hasn't thought through threat modeling is protected from prompt-injected disk wipes. Safe by default.
- **Explicitly opt-out-able.** A host that takes the policy responsibility — typically because it sandboxes Agentao in a container, or because it legitimately needs full system access — can disable the floor with `enable_hardline=False`. Literal `full-access` then means literal full access, which is what the mode promises.

**API shape:**

```python
class PermissionEngine:
    def __init__(
        self,
        mode: PermissionMode,
        *,
        enable_hardline: bool = True,
        ...
    ):
        ...

    def decide_detail(self, tool_name, tool_args):
        if self._enable_hardline:
            hit = _hardline_check(tool_name, tool_args)
            if hit is not None:
                return hit
        # ... existing mode/preset/user-rule routing
```

**Placement:** Inline `_HARDLINE_PATTERNS` and `_hardline_check()` at the top of `agentao/permissions.py`. Do **not** migrate `permissions.py` into a package for this PR.

**Pattern set** — the 12 patterns from `hermes-agent tools/approval.py:HARDLINE_PATTERNS`, scope strictly limited to **unrecoverable** operations:

- `rm -rf` against `/`, system roots (`/etc /usr /var /boot /bin /sbin /lib /home /root`), or `~` / `$HOME`
- `mkfs[.*]`
- `dd ... of=/dev/(sd|nvme|hd|mmcblk|vd|xvd)…` and `> /dev/(sd|nvme|…)`
- Fork bomb `:(){ :|:& };:`
- `kill -1`, `kill -9 -1`
- `shutdown / reboot / halt / poweroff` at command position
- `init [06]`, `telinit [06]`
- `systemctl (poweroff|reboot|halt|kexec)`

Each pattern uses `_CMDPOS` (start-of-line, after `;`/`&&`/`||`/backtick/`$(`, after `sudo`/`env` wrappers) so `echo "reboot logs"` does not false-positive.

`git reset --hard`, `pip install`, `chmod -R 777`, `curl | sh` deliberately stay outside hardline — they are recoverable-but-costly and belong in regular `DANGEROUS` / preset rules so a host can opt to allow them.

**Compilation:** `_HARDLINE_PATTERNS_COMPILED = [(re.compile(p, re.IGNORECASE), desc) for p, desc in _HARDLINE_PATTERNS]` at module import.

**Decision result:** When a pattern matches and `enable_hardline=True`, return `PermissionDecisionDetail(decision=DENY, reason=f"hardline:{description}")`. The `reason` field is a **policy-source taxonomy** for audit and debugging — `hardline:*`, `mode-preset:*`, `user-rule:*` — not a user-vs-policy discriminator (user denials live on `ToolLifecycleEvent(cancelled)` — see §5.3).

### 5.2 Test corrections (dual contract)

`tests/test_permissions.py:162` currently asserts that under `full-access`, `rm -rf /` returns `ALLOW`. In rev 3 this is preserved as the "explicit opt-out" contract; a new test asserts the "default safe" contract.

```python
def test_full_access_default_blocks_hardline_commands():
    """Default construction has hardline ON — protects CLI users and unconfigured hosts."""
    e = PermissionEngine(mode=PermissionMode.FULL_ACCESS)
    for cmd in [
        "rm -rf /",
        "rm -rf /home/*",
        "shutdown -h now",
        "mkfs.ext4 /dev/sda1",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
        "kill -9 -1",
        "systemctl poweroff",
    ]:
        d = e.decide("run_shell_command", {"command": cmd})
        assert d == PermissionDecision.DENY, cmd

def test_full_access_with_hardline_off_honors_literal_contract():
    """Explicit opt-out preserves the literal full-access semantic for embedded hosts."""
    e = PermissionEngine(mode=PermissionMode.FULL_ACCESS, enable_hardline=False)
    assert e.decide("run_shell_command", {"command": "rm -rf /"}) == PermissionDecision.ALLOW

def test_reason_uses_policy_source_prefix():
    """reason field is a policy-source taxonomy, not a user-action discriminator."""
    e = PermissionEngine(mode=PermissionMode.FULL_ACCESS)
    detail = e.decide_detail("run_shell_command", {"command": "rm -rf /"})
    assert detail.decision == PermissionDecision.DENY
    assert detail.reason.startswith("hardline:")

def test_workspace_write_unaffected_by_hardline_flag():
    """Hardline is below other layers — workspace-write deny rules still fire normally."""
    e = PermissionEngine(mode=PermissionMode.WORKSPACE_WRITE, enable_hardline=False)
    # The mode's existing "rm -rf|sudo|mkfs|dd if=" deny rule still catches this.
    assert e.decide("run_shell_command", {"command": "rm -rf /tmp/x"}) == PermissionDecision.DENY
```

### 5.3 Policy-source `reason` taxonomy on `PermissionDecisionEvent`

`PermissionDecisionEvent` already exists. Audit that the policy sources emit distinguishable `reason` prefixes:

- `hardline:<description>` — opt-out floor refused
- `mode-preset:<rule_id>` — preset rule matched
- `user-rule:<rule_id>` — user JSON rule matched

User-initiated denials are **not** in this taxonomy. They surface on a different event type — `ToolLifecycleEvent(cancelled)` — because the current event order is: `PermissionDecisionEvent(ASK) → prompt → ToolLifecycleEvent(cancelled|started)`. Hosts wanting "user denied vs policy denied" UI subscribe to both event types; they do not parse `reason`. No new event types are introduced.

This work ships in PR 2 (with hardline) so that the field's possible values are stable from the moment the field starts emitting `hardline:*` — making it a separate later PR would change the field's value space after release, which is a breaking event-shape change for hosts.

## 6. P2 — Convenience and hygiene

### 6.1 Windows UTF-8

Port `hermes_cli/__init__.py::_ensure_utf8()` verbatim into `agentao/__init__.py`. 33 lines, gated on `sys.platform == "win32"`. No effect on POSIX.

### 6.2 `mask_secret()` helper

New `agentao/redact.py::mask_secret(value, head=4, tail=4, floor=12, placeholder="(not set)")`. Migrate any ad-hoc secret-masking call sites discovered during P0/P1.

### 6.3 OSC sequence stripping (deferred)

Defer until shell-backed file read paths land (docker / remote executors). Today `read_file` uses `Path.read_text()`; OSC leaks cannot reach it. When that changes, port `_strip_terminal_fence_leaks` from Hermes.

## 7. Considered and rejected: Tier 2 FLOOR_ASK

This section exists so the next reviewer doesn't re-propose this and re-walk the same ground.

**The proposal (rev 2):** A second floor tier — `~/.bashrc`, `~/.zshrc`, `~/.profile`, `~/.bash_profile`, `~/.zprofile`, `~/.netrc`, `~/.pgpass`, `~/.npmrc`, `~/.pypirc` — that always ASK at minimum, never auto-allowed by `*` allow.

**Why it was attractive.** It plugs the gap that shell redirection (`echo X >> ~/.bashrc`) bypasses `write_file`'s `PathPolicy`. Hermes's `69dd0f7cf` covers exactly this case. Imported uncritically.

**Why it's wrong for Agentao.**

1. **`~/.bashrc` is a legitimate write target.** Homebrew, pyenv, nvm, rustup all write shell rc files. Devops scripts that touch `.zshrc` are normal. An embedded host running such a workload would have to fight the framework on every operation.
2. **"Cannot be auto-allowed by `*`" violates host-overrides-defaults.** Agentao's whole design is that hosts compose policy. A floor that says "no host can ever turn this off" is exactly what `agentao.host` was built to avoid.
3. **`full-access` becomes a lie.** A mode that says "everything allowed" but secretly ASKs on common targets is worse than either alternative — it surprises the host.
4. **The legitimate worry (shell bypassing PathPolicy) is solved by preset rules.** A `workspace-write` preset rule that ASKs on shell-RC writes is exactly the right shape: mode-scoped (active when the user picks workspace-write), host-overridable (a host can replace the rule), and explicit (the host sees the rule when they read the engine's active rules display).

**What we're shipping instead.** A `workspace-write` preset rule that ASKs on shell-RC and credential file writes via shell redirection / `tee` / `cp` / `mv` / `sed -i`. This is a single PR-3-or-later addition to `_PRESET_RULES`, with the standard regex test matrix from rev 2 §4.2. It is **not P0**, **not P1**, and **not a floor**. It's a normal rule that a host can replace if it wants to.

**Sentinel test that locks the rejection.** PR 2 includes:

```python
def test_no_floor_ask_tier_exists():
    """rev 3 §7: there is no Tier 2 floor. ~/.bashrc writes follow normal mode rules."""
    e = PermissionEngine(mode=PermissionMode.FULL_ACCESS, enable_hardline=False)
    # Literal full-access with hardline off must allow the write — no hidden floor.
    assert e.decide(
        "run_shell_command",
        {"command": "echo X >> ~/.bashrc"},
    ) == PermissionDecision.ALLOW
```

If a future change reintroduces a FLOOR_ASK tier, this test fails — forcing the change to either justify itself or back out.

## 8. Out of scope

- A separate "YOLO" mode. Agentao's `enable_hardline=False` + `full-access` is the equivalent.
- Per-session sudo cache (Hermes `de03a332f`). Agentao's terminal tool does not currently prompt for sudo. Revisit when interactive sudo lands.
- Plugin pre/post approval hook surface. Agentao chose host events; do not introduce a parallel plugin mechanism.
- Migrating `agentao/permissions.py` into a package. Hardline is inlined. Splitting is a separate refactor.
- A "Tier 2 FLOOR_ASK" floor at any future point — see §7. Replacing it with a normal preset rule is OK; bringing back the unconditional floor is not.

## 9. PR sequencing

```
PR 1 (P0)  permissions correctness      ✓ landed 2026-05-03
                                          ─ isinstance(dict) guard,
                                          MCP error classification,
                                          ToolRunner copy_context() propagation
                                          + propagation/isolation tests.
                                          Zero policy stance. Shipped first.

PR 2 (P1)  optional hardline layer      ✓ landed 2026-05-03
                                          ─ enable_hardline flag,
                                          _hardline_check() pre-check
                                          (in agentao/permissions_hardline.py,
                                          imported by permissions.py — the
                                          rule logic stayed inline per §5.1),
                                          dual-contract tests (default-on
                                          DENY + opt-out ALLOW + sentinel
                                          test from §7),
                                          policy-source reason taxonomy
                                          on PermissionDecisionEvent.

PR 3 (P2)  windows utf-8 enforcement    ✓ landed 2026-05-04
                                          ─ agentao/__init__.py::_ensure_utf8()
                                          forces CP_UTF8 + reconfigures
                                          stdin/stdout/stderr; gated on
                                          sys.platform == "win32"; POSIX
                                          contract test in
                                          tests/test_init_utf8.py.

PR 4 (P2)  mask_secret + redact         ✓ landed 2026-05-04
                                          ─ agentao/redact.py::mask_secret;
                                          no ad-hoc sites needed migration
                                          (P0/P1 walk-through found none).
                                          Helper is forward-looking for
                                          PermissionDecisionEvent
                                          projection + future /provider UI.

PR 5 (P3)  shell sensitive-write preset ✓ landed 2026-05-04
                                          ─ _SHELL_SENSITIVE_WRITE_RE +
                                          workspace-write preset rule.
                                          ASK, not DENY. Mode-scoped:
                                          full-access deliberately not
                                          carrying the rule (literal
                                          full-access principle, §5.1).
                                          Coverage gap (indirection,
                                          literal-expanded paths) tracked
                                          in §10's bashlex follow-up.
```

PR 1 and PR 2 were deliberately decoupled. PR 1 was uncontroversial correctness and shipped without anyone needing to agree on policy. PR 2 was the policy choice and shipped immediately after, since the dual-contract tests removed the only remaining ambiguity. PRs 3–5 followed the day after as a convenience batch.

The rev 2 plan bundled hardline into PR 1 because both were tagged P0. Round 4 split them: correctness had no policy stance, hardline was a policy choice that hosts opt out of. Keeping them as separate PRs preserved that distinction in the git log.

## 10. Open questions and post-ship follow-ups

The questions below are carried over from rev 3's pre-ship list, annotated with what landed and what remains.

- **Where does `enable_hardline` get configured for end users?** **Decided as tentative answer (no `.agentao/permissions.json` flag).** PR 2 shipped constructor-only opt-out. No CLI flag (`--no-hardline`) was added — the CLI never grew an explicit need, and adding one speculatively would have cut against the "embedded harness, host decides policy" principle. If a CLI user genuinely needs hardline off, they can `enable_hardline=False` via the embedded entry point; reopening this question requires a real workflow that demonstrates the gap.
- **`HOME` resolution under `sudo`.** **Open.** Hardline patterns still match only `~ / $HOME / ${HOME}` syntactically. A `sudo rm -rf $HOME` re-evaluates `$HOME` to root's home in the privileged process, but the regex catches the literal `$HOME` token regardless — so the practical risk is the inverse case: `sudo` with a different home variable. Track as a real bug if/when reported; no speculative fix.
- **Container-backend bypass.** **Decided as tentative answer (explicit host opt-out).** Agentao does not container-detect; hosts that sandbox set `enable_hardline=False` themselves. Re-litigate only if a host backend ships that needs runtime detection.
- **Reason audience.** **Decided as tentative answer (operator-facing).** `reason="hardline:recursive delete of root filesystem"` is treated as audit / debug copy. User-facing wording stays the host's responsibility — the ACP `request_permission` payload still carries the raw reason string and hosts choose how to render it.
- **`bashlex` supersedence of PR 5's regex.** **Open, tracked here as the only inherited follow-up.** PR 5's regex catches the common shapes (redirect, tee, cp/mv, sed -i) targeting `~`/`$HOME`-prefixed sensitive files. It cannot catch:
    1. Indirection via shell variables: `dst=~/.bashrc; echo X > "$dst"`.
    2. Literal expanded paths: `/Users/<u>/.bashrc`, `/home/<u>/.bashrc`.
    3. Process substitution wrappers: `tee >(cat > ~/.bashrc)`.
    A `bashlex`-based pass — the same approach the hardline shell-safety scanner uses for `rm -rf` indirection — would close (1) and (3). (2) needs the runtime to know the user's home dir at policy-evaluation time, which adds host coupling we may not want; defer that piece until a concrete attack surfaces.
    Not blocking — workspace-write already ASKs on everything not on the read-only allowlist, so the regex tier today is "documentation + future-proofing," not a load-bearing gate.
