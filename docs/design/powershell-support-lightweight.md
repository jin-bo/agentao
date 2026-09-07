# PowerShell support: the lightweight design

**Status:** implemented · **Date:** 2026-09-06

> §1–§4 are the plan as it was written, implemented point by point with no deviation.
> **§6 records the behaviour that actually landed in the code**, together with the handful of
> things the plan did not cover and the implementation had to settle itself. The user-facing
> documentation is `docs/reference/configuration.md` §4, "the `shell` block" (same section in
> the Chinese edition). The seven authoritative documents of the retired design and their
> rule-numbering framework are deleted; the history is in Git. The thirty-two method rules
> that review produced outlived it and moved to `review-method-rules.md`.

The goal is to use PowerShell correctly on Windows. **Keep the existing legacy cmd, remove the
strict system, then implement lightweight PowerShell.** Work forward from the current HEAD and
keep the independent Windows defect fixes; no hard revert, and no policy-tier framework to
maintain.

## 1. Configuration and defaults

The default stays legacy cmd for this round. PowerShell is opted into explicitly:

| Configuration | Windows behaviour |
|---|---|
| No shell configured, or `dialect: cmd` alone | today's `%COMSPEC% /c`; environment and permission behaviour stay compatible |
| `dialect: powershell` alone | discover `pwsh.exe → powershell.exe`; **error if neither is present**, never a fall back to cmd |
| `path` and `dialect` given as a pair | use the named interpreter and dialect; an error is never silently substituted |

`dialect` on its own is legal; `path` on its own is still an error. The `shell` block never
reached v0.4.21, so `ladder`, `allowlist`, `env_passthrough` and `allow_git_bash` are removed
straight out of the loader's allowed key set, keeping the existing unknown-key error — no
compatibility aliases and no migration framework. `LADDER_FLIPPED` is deleted; whether the
default ever moves is a separate decision.

macOS and Linux keep today's POSIX behaviour; asking for `powershell` or `cmd` there is an
unsupported-platform configuration error. Changes take effect on a rebuilt session or a
restart.

## 2. Launch and encoding

- **Discovery** joins each candidate filename onto known install locations and the absolute
  directories of the parent process's `PATH`, then checks `isfile`. Not `shutil.which`, and no
  implicit search of the project's working directory. User-level installations are allowed;
  Git Bash is never picked automatically. The chosen path is fixed — no retry with a different
  shell after a failed execution.
- **One launch shape, always `-EncodedCommand`.** A fixed prelude, the original body and a
  fixed exit trailer, a newline between each, encoded as UTF-16LE and base64'd. Started with
  `shell=False` at a fixed interpreter path, with the arguments `-NoLogo -NoProfile
  -NonInteractive -OutputFormat Text -EncodedCommand <base64>`. Prelude and trailer are
  agentao's own fixed text and contain no byte of the body or of any configuration; the body is
  passed through untouched. The encoding solves transport quoting only — it does not exempt the
  text from PowerShell's own parsing.
  [Microsoft's parameter reference](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1)
- **The prelude** is `$OutputEncoding = [System.Text.UTF8Encoding]::new($false); try {
  [Console]::OutputEncoding = $OutputEncoding } catch {}`, followed by `$LASTEXITCODE = 0`. The
  pipe encoding — what PowerShell writes to a native command's stdin — is set *first*, so a
  background launch whose console assignment fails does not skip it; the catch wraps the console
  assignment only, never the body. The console setting also decides how PowerShell decodes what
  it captures from a native program: a program that writes some other encoding can come back
  mangled through either `native.exe` or `$out = native.exe`, and no universal transcode is
  promised.
  [Why `$OutputEncoding` matters](https://devblogs.microsoft.com/powershell/outputencoding-to-the-rescue/)
- **The exit trailer captures `$?` before anything else can change it:** `$__agentao_ok = $?; if
  ($__agentao_ok) { exit 0 }; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; exit 1`. A
  successful last statement gives 0; a failure preserves a non-zero native exit code, and
  otherwise gives 1. An explicit `exit N` and a terminating error are PowerShell's own exit. A
  bare `exit $LASTEXITCODE` is deliberately not appended, because it reports a stale 0 for a
  cmdlet that only wrote an error. The trailer follows the last statement: an earlier failure
  followed by a success can still return 0, and the body is not rewritten into fail-fast.
  [Exit status reference](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_automatic_variables?view=powershell-7.6)
- Prelude, body and trailer are parsed together, so a trailing continuation backtick, an
  unclosed string or a comment can absorb the trailer. A syntax error is PowerShell's to report
  — nothing auto-closes a delimiter — and absorbing the trailer does not necessarily produce
  one, so **the exit-code guarantee is stated only for a body that ends on its own**.
- **CLIXML gets bounded text extraction, never an XML parser.** stdout and stderr stay separate.
  Within the existing output budget, content carrying `#< CLIXML` is scanned and complete
  `<S …>text</S>` elements are extracted in order. A single unescape of XML's five predefined
  entities and of legal numeric character references, then a single decode of PowerShell's
  `_xHHHH_` — no recursive expansion and no external resources. A DOCTYPE or ENTITY declaration,
  an unknown structure or a truncated wrapper keeps the length-limited original plus a
  diagnostic; ordinary text passes through unchanged. The output cap still applies after
  extraction, and `-OutputFormat Text` is not treated as a guarantee of no wrapper.
- Foreground and background share one launch request, with the working directory passed
  straight through. `build_child_env()` keeps its inheritance and its credential scrub; `PATH`
  and `PATHEXT` are not filtered, module auto-loading is not disabled, and
  `NoDefaultCurrentDirectoryInExePath` is not added. `-NoProfile` means functions and aliases
  from a user profile do not exist.
- The child environment is built from the host's current environment on **every** call. An
  installer does not update the `PATH` of an agentao that is already running; a new tool is
  reachable by absolute path, by updating the host environment explicitly, or after a restart.
- **Length is measured on the final encoded command line.** For n bytes of UTF-16LE the base64
  length is `4 * ceil(n / 3)`, plus the path, the arguments and the terminator. The existing
  length check is reused to give a readable error rather than inventing a second limit
  framework; an over-long command or an environment error reported by the operating system comes
  back just as clearly. Never truncated, never silently turned into a temporary script.

## 3. Permissions and execution

**Same body, same permission settings: an input the old general floor refused, the new
PowerShell path must refuse too.** The old general check is extracted into a standalone
function; legacy calls it exactly as before, and PowerShell **always** runs it and then layers
the Windows danger check on top — never as a fallback for when lowering failed. The old check's
compatibility false positives are kept, and its result is not read as a complete statement about
PowerShell semantics.

**A compatibility decision.** The default legacy cmd keeps running only the old general floor;
no Windows danger table is added there. `format C:` and `vssadmin delete shadows` are therefore
still not stopped by that floor, and the permission rules run as usual. This preserves the
status quo; it is not a claim that those operations are safe.

PowerShell danger recognition covers canonical names, the built-in alias family and valid
parameter abbreviations: `Remove-Item` collects `rm/ri/del/erase/rd/rmdir`, and the recursion
parameter covers `-Recurse`, `-r` and any other valid abbreviation, handling parameter order and
quoted paths. Only the small table the danger classes need is maintained — not a restored
per-build command table — and no promise is made to recognise every alias a user rebinds at
runtime.

The reusable PowerShell tokenizer/parser and the danger table are kept. A successfully extracted
command is checked at the command position, because searching the raw text misreads strings and
comments. **A lowering failure refuses nothing by itself:** the general floor still runs, an
already reliable danger hit still refuses, and otherwise the call carries on to the ordinary
permission rules — never a direct ALLOW.

Only the dialect and launch data that is actually needed survives internally:

- `_scanner.py::_policy_dialect` is no longer gated on `policy_enabled`; the forced invariants
  over rung and attestation fields are deleted; legacy keeps its original check and PowerShell
  calls the combined check above.
- `LegacyLaunch` is kept and `WindowsLaunch` is reduced to a fixed target, a command line, a
  working directory and an environment. `_Attested`, `verify_attested_launch` and the strict
  request types with no consumer are deleted; no Basic/Strict type hierarchy replaces them.
- `spec_fingerprint`, written and never read, is deleted, and no substitute hash scheme is
  built. The launch request decided at planning time is still what runs; a hook rewrite is
  re-judged, and execution never re-selects a shell.

**An independent defect fix: an explicit shell path was ignored.** `explicit_shell` did not
reach the real launch target. That is fixed separately — the request passes it through and
`_popen_target` consumes it — covering foreground and background, with a check that a
user-named cmd really runs. This fix must not be folded into the strict-feature deletion.

## 4. Scope of the retirement

Nothing that exists only for the strict path is kept. Consumers and their tests are deleted
by reference; a reusable function moves first into the module that actually uses it.

| Scope | Disposition |
|---|---|
| `_effects`, `_measured_commands`, `_wrappers`, `_bash`, `_cmd`, `_windows_identity` | delete the strict-only modules; extract and keep the lexing functions the danger table and the basic scan need |
| `_trust`, `_analysis`, `shell_spec` | delete trusted resolution, closed sets, attestation, the strict environment and effect propagation; keep only basic discovery, launch construction and the data planning needs. Collect the `Rung` members with no construction site and the enums that served only the old tiering |
| `classify_refusal` and its tests | collect the retired reason family; delete the tallying code with no runtime consumer along with it |
| oracle / command-table / config probes | delete `scripts/windows_oracle_probe.py`, `windows_command_table_probe.py`, `windows_git_config_probe.ps1` and `.github/workflows/windows-oracle-probe.yml`; keep the ordinary Windows CI |
| specifications and contracts | retire IMG, NAME, EFF and the strict-only rules within TOK/LOWER/WRAP, and rewrite the remaining launch and configuration requirements. Delete the old contract files, their dedicated gates, the implementation ladder and the matching tests; Git keeps the history |
| document machine-checks | delete `scripts/check_design_set.py`, `tests/test_design_set.py` and the CI call that serve only this document set, and clean up the dead imports and lists. Keep the standalone citation-check use and the sub-agent design documents |

The live PowerShell documentation converges to two files, the plan and the user reference: once
implementation is done, the final behaviour is recorded here, and the seven authoritative
documents and the rule-numbering framework are no longer maintained. The independent Windows
fixes, the general permission tests and the sub-agent design are not deleted with them.

## 5. Order and acceptance

Fix the explicit shell path first, then, on the same implementation branch, **delete strict,
build lightweight, accept, and merge**. The current strict PowerShell planning chain refuses a
clean body on a floor call that carries no decided record; that is not treated as existing
PowerShell behaviour to stay compatible with. The deletion phase is not released on its own, and
the default stays legacy throughout.

Windows CI verifies through the real tool, the real planner and the real permission engine,
against fixed versions:

- **Discovery and launch:** explicit selection of PowerShell 7 and 5.1, and their absence; a
  same-named candidate placed in the working directory, confirming discovery does not hit it
  implicitly; spaces, Chinese, quotes, percent signs, newlines, working directory; the final
  encoded command line's boundary, an over-long body and a long `PATH`, with no truncation.
- **Output and background:** foreground Chinese stdout/stderr uncorrupted on both versions;
  Chinese piped input verified against a native test program that reads UTF-8 stdin, and
  captured UTF-8 native output likewise. The real `run_background` uses `DETACHED_PROCESS` with
  all three streams at DEVNULL — have the body write a completion marker and watch the process
  end, rather than asserting only that a PID came back — and the background pipe's encoding is
  verified too. Console-less behaviour must be measured on Windows CI.
- **CLIXML text extraction:** a real redirected error becomes readable text; covering ordinary
  text, character references, escaped underscores, truncation and over-budget input. Input
  carrying an entity declaration is not expanded, external references are not fetched, output
  is always length-limited, and an unknown structure keeps a diagnostic rather than silently
  losing content.
- **Exit codes:** `cmd.exe /c exit 7` returns 7; a last-statement cmdlet error after a
  successful native command returns 1; plus a cmdlet error on its own, a terminating error, an
  explicit `exit 9`, and a success after a failure returning 0. Record what a trailing
  continuation and an unclosed structure actually do rather than assuming both are parse errors.
  Check the return value in the foreground and observe the real process exit in the background —
  "started successfully" is not "the body succeeded".
- **Permissions:** `Remove-Item -Recurse -Force C:\`, `ri -r -fo C:\`, `rm -Recurse -Force C:\`
  and `rm -rf /` are all refused; printing the same text adds no false positive. The old general
  refusal corpus stays refused item by item on the new path; a lowering failure still reaches the
  ordinary rules; and at least one clean body travels the real planning chain to a launch. The
  dangerous cases test the verdict only and never execute.
- **Development:** check out a small project and build and test it after `uv sync` and after
  `python -m venv` plus `pip install`; a user-directory tool and a `.venv` are runnable, and a
  bare-name test updates the host `PATH` explicitly.
- **Compatibility:** an unconfigured Windows cmd keeps its command, environment and permission
  behaviour; macOS and Linux regressions pass; both an administrator and an ordinary user can
  use lightweight PowerShell; and the deleted unreleased keys fail through the ordinary
  unknown-key validation.

This round revises the plan only; the Windows acceptance above has not been run. Deletion, the
fixes and the measurements follow with the implementation.

---

## 6. Final behaviour (implementation record)

### 6.1 Modules, and where things went

| Where it is now | What it is |
|---|---|
| `agentao/capabilities/shell_spec.py` | the dialect vocabulary, `ShellBlock`, `ShellSpec`, the two launch requests, `DecidedCall`. About 260 lines, replacing 775 |
| `agentao/capabilities/powershell.py` | discovery, the `-EncodedCommand` wrapping and encoding, length measurement, CLIXML text extraction. New |
| `agentao/permissions_hardline/_scanner.py` | `generic_floor()` (the extracted old general check) and `hardline_check()` (the entry that combines both) |
| `agentao/permissions_hardline/_windows.py` | the Windows danger table, PowerShell alias resolution, and the structural test for a recursive delete of a drive root |
| `agentao/permissions_hardline/_powershell.py` | tree-sitter lowering (kept) and `scan_powershell()` (rewritten: a lowering failure returns `None`) |

Deleted: `_analysis`, `_bash`, `_cmd`, `_effects`, `_measured_commands`, `_refusals`, `_trust`,
`_windows_identity`, `_wrappers` (about 4,700 lines), the four probe scripts,
`windows-oracle-probe.yml`, `scripts/check_design_set.py`, `tests/test_design_set.py`, and
twelve test files belonging to the strict path.

### 6.2 What the plan did not write, and the implementation settled

1. **`ShellSpec` no longer has a `Rung`.** The plan only said to collect the strict members with
   no construction site. Measured, none of the remaining members (`pwsh` / `powershell` / `cmd` /
   `system_posix`) had a reader: the difference between `pwsh` and `powershell` *is* the
   interpreter path, and the spec already carries it. `ShellSpec` is therefore two fields,
   `(dialect, interpreter)`, with `interpreter=None` meaning "the platform's own answer". The
   `Platform` enum goes the same way — `default_spec(windows: bool)` is enough.
2. **The tool description follows the spec.** Not mentioned in the plan. But the description is
   the model's only statement of which syntax to write, and telling it `cmd /c` while PowerShell
   does the reading means it writes the wrong syntax on every call. `ShellTool._invocation()`
   reads the spec; the platform-default arm goes through `shell_display_name()` to keep a single
   definition point (method rule 30: `from … import` makes a second copy that a monkeypatch
   cannot reach).
3. **`dialect: posix` on Windows must carry a `path`.** The plan said Git Bash is never picked
   automatically, but not what this configuration does. It is an error, for the same reason as
   "never fall back to cmd": the candidates (Git Bash, WSL, MSYS) disagree about path translation
   and reach, and picking the wrong one does not fail — it means something else.
4. **CLIXML extraction applies only to a PowerShell launch** (`_format_result(powershell=…)`).
   Running the text scan unconditionally on other dialects gains nothing and adds one more path
   that can rewrite a user's output.
5. **An over-long command line goes through `LaunchRefused`.** The plan said to reuse the
   existing length check for a readable error but not through which channel. Refusal at launch
   time, because this is not a policy verdict: both delivery faces already catch it and report it
   as a refusal rather than a "failed to start", which is exactly the shape this error needs — a
   model does not retry a refusal.

### 6.3 One deviation from the plan

§3 says "`_scanner.py::_policy_dialect` is no longer gated on `policy_enabled`". In the
implementation that function became `_dialect()`, which only returns the dialect value — because
the combination happens in `hardline_check()` rather than in the dialect dispatch: the general
floor runs first and always, and PowerShell layers the danger table on top. That way
`_powershell.py` does not have to import `_scanner` backwards, and the package's dependency
direction stays one-way. The behaviour matches the plan.

### 6.4 Three things the first Windows run measured

The Windows acceptance items in §5 run in CI's windows job, and this round was the first time
they actually executed. The foreground half — discovery, launch, body integrity, Chinese in both
directions, all seven exit-code cases, CLIXML, the over-long refusal, a clean body through the
real planning chain, and a dangerous body judged but never executed — was 316 green on each of
the two Python versions. The background half and 5.1 each produced findings, and all of them are
**measurements, not reasoning**:

**Background launches use `CREATE_NO_WINDOW`, never `DETACHED_PROCESS`.** The implementation
passed `DETACHED_PROCESS` as §2 wrote it. Measured, `pwsh` and `powershell` **both exit 0 with
empty stdout and empty stderr without running a single statement of the body** — confirmed by
pointing both streams at real files, so the output was absent rather than discarded, and by
running a bare body with no prelude or trailer, which also did not run. PowerShell needs a
console to host itself, and `DETACHED_PROCESS` leaves it none. With `CREATE_NO_WINDOW` — a
console of its own that is never shown — the body runs. cmd works under both flags, which is
exactly why the default path never exposed this. The two flags are mutually exclusive, so it is
a swap rather than an addition. `tests/test_powershell_launch.py` carries a cross-platform shape
case pinning the choice, because the edit that would undo it happens on a machine that is not
Windows.

**Windows PowerShell 5.1's `Set-Content` writes ANSI by default, and Chinese becomes `??`.** The
background case originally wrote its completion marker with `Set-Content` to prove the body ran.
After the `DETACHED_PROCESS` swap the body did run, but 5.1 wrote `?? finished` where pwsh wrote
the characters. That is the cmdlet's own default encoding and has nothing to do with the launch:
the prelude governs the two *streams* between agentao and the child and does not change what a
user's command means — and 5.1's own `utf8` carries a BOM. The case now writes through
`[System.IO.File]::WriteAllText` with a named encoder, so it measures what it says it measures,
and the documentation records the trap.

**What Windows PowerShell 5.1 pipes into a native command begins with a UTF-8 BOM, and that
cannot be changed.** Five prelude variants were measured on a runner: the shipped one,
`$OutputEncoding` alone, the console assignment first, the static `[Text.Encoding]::UTF8`, and
**no prelude at all**. 5.1 emitted the mark in all five and pwsh in none, so it is 5.1's
behaviour rather than the wrapper's. The same measurement proves the prelude earns its place:
without it 5.1 sent `GOT:\ufeff??` and the Chinese was gone. The case now tolerates a leading BOM
with the measurement written beside it, and the configuration reference says so, leaving whoever
is downstream of the pipe to strip it.

### 6.5 Acceptance status

The full suite passes locally (macOS) and `ruff check .` is clean. All 21 CI jobs are green: the
shell cases on both Windows versions are 326 passed / 6 skipped each, the full suite behind them
4929 passed / 48 skipped each, and the `-m slow` clean-install tier newly wired into the build
job passes all 9. The acceptance conditions in §5 are met.
