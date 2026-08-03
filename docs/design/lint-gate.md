# Lint gate

**Status:** landed. CI job `lint-gate`, config in `pyproject.toml :: [tool.ruff.lint]`.

## What it is

`ruff check .` with exactly six rule groups:

| Rule | Catches | Scope |
|---|---|---|
| `E9` | Syntax / IO errors — code that cannot run at all | everywhere |
| `F402` | Import shadowed by a loop variable — latent `UnboundLocalError` | everywhere |
| `F811` | Redefinition of an unused name — one of the two is dead | everywhere |
| `F821` | Undefined name — guaranteed `NameError` at runtime | everywhere *except* star-import modules |
| `F405` | Undefined name **in** a star-import module — F821's blind spot | the 8 star-import modules |
| `F401` | Unused import | everywhere except `agentao/` — see below |

Nothing else. No style, no formatting, no import sorting, no modernization.

### Why `F405` is in the list

Not as a second ambition — it is what makes `F821` actually hold. pyflakes
reclassifies *every* undefined name as `F405` ("may be undefined, or defined
from star imports") in any module containing `from x import *`. With `F821`
alone the flagship rule is silently inert in exactly the modules where it is
least affordable:

```
agentao/harness/__init__.py            agentao/harness/projection.py
agentao/harness/events.py              agentao/harness/replay_projection.py
agentao/harness/models.py              agentao/harness/schema.py
agentao/harness/protocols.py           agentao/tool_runner.py
```

`agentao/harness/` is the deprecated **public** alias for `agentao.host`, and
every one of those files carries real post-import logic (`HarnessEvent =
_HostEvent`, `__all__ = list(_host_all) + [...]`, `__getattr__`/`__dir__`) —
not just the star import. Verified by probe: an undefined name in such a
module reports `All checks passed` under `F821`, and `F405` under `F405`.
Left unclosed, a typo introduced during the scheduled 0.5.0 alias removal
ships green and raises `NameError` at `import agentao.harness` for an
embedder still on the deprecated path.

`F405` measures **0** across the repo, so it costs nothing today.

### Scope is `.`, not a directory list

The gate walks the whole repository (ruff honours `.gitignore` and
`[tool.ruff] exclude`). An earlier revision passed `agentao/ tests/
examples/`, which silently omitted 13 tracked files: `main.py`, the two
`scripts/write_*_schema.py` that CI itself executes, and 10 files under
`skills/skill-creator/` — the only Python outside `agentao/` that is
**force-included into the wheel** (`pyproject.toml :: force-include`) and
executed by the agent when the skill is active. That omission was not
theoretical: `skills/skill-creator/scripts/quick_validate.py` carried an
unused `import os` the gate never saw.

## Why this narrow

The selection came from measuring, not from taste. Re-measured with the
locked ruff 0.16.1 against the merge-base tree (`7eb762e`), using
`--isolated` so the figures are of the raw rule, not of the configured gate:

| Ruleset | Findings | Live bugs found |
|---|---|---|
| `UP` (pyupgrade) | 2743 | — not a defect class |
| ruff's own default (`E4,E7,E9,F`) | 344 | 1 (see below) |
| `F401` (unused-import) | 253 = 68 `agentao/` + 169 `tests/` + 16 `examples/` | 0 |
| `F841` (unused variable) | 25 | 1 — fixed, see below |
| `E9,F402,F811,F821` | 4 | **0** |
| `F405` | 0 | 0 |

All four gate findings were read individually before being fixed; none was a
live bug. So the honest claim for this gate is **not** "it found bugs" — it is
"it costs a few seconds of CI and pins a class that has already bitten this
repo once."

That once is `eef5b70` (PR #141), whose title is *"declare plugin types for
F821"*. F821 measured 0 when this gate landed; the gate is what keeps it there.

The opinionated sets were rejected on evidence. `UP`'s 2743 findings would
rewrite working code across the tree and bury real changes in review. This
repo has already declined a comparable ratchet on the same grounds — see
`docs/design/refactor-audit-2026-07.md`, where 27 of 89 mypy findings were
mixin false positives.

### `select` replaces ruff's default — including F841

`select` is a replacement, not an extension: configuring it turns *off*
ruff's default `E4,E7,E9,F`. The practical consequence is that `F841`
(unused variable, 25 hits) is not enforced even though ruff enables it out of
the box.

That is a deliberate call, not an oversight, and it was not free. One of the
25 was a real defect in shipped code, **since fixed**:
`agentao/skills/drafts.py:262` bound `old_fm = m.group(0)` under the comment
*"Drop any extra trailing newline beyond what old_fm had, keep body intact"*,
then returned a hardcoded `new_fm` and never consulted `old_fm`.

`replace_skill_name` rewrites a file the user wrote, so the contract is that
everything except the `name:` value survives byte-for-byte. Rebuilding the
frontmatter from the literal `f"---\n{block}\n---\n"` broke that for **6 of 7**
layouts measured — including the one every skill in this repo uses:

| Input shape | Old output |
|---|---|
| `---\n…\n---\n\n# Body` (blank line before body) | blank line deleted |
| `---\n…\n---` (file ends at the fence) | trailing newline added |
| `\n---\n…` (leading blank line) | leading whitespace dropped |
| `---  \n…` (spaces on the fence line) | spaces dropped |
| CRLF document | rewritten to LF |
| `name: a\n\ndesc:` (blank line inside frontmatter) | blank line deleted |

The fix splices the new value into the original string by the frontmatter
block's span, and within that block replaces only the `name:` *value* span —
replacing the whole match would take the trailing whitespace with it, because
`_NAME_LINE_RE` ends in `\s*$` and `\s` swallows a trailing `\r`. 16 tests
now compare whole strings; the pre-existing test passed against the buggy
version because `"# Python Testing" in out` cannot see a deleted blank line.

F841 is still left out, because the remaining hits need reading one at a time
(loop variables and deliberate `_`-style bindings are common false-positive
shapes) and because that is a separate change from standing this gate up. It
remains the strongest candidate for the next rule to add.

## Why F401 is off for `agentao/`

`F401` is enforced everywhere the gate walks — `tests/`, `examples/`,
`skills/`, `scripts/`, `main.py` — and **exempted wholesale for `agentao/`**
via `per-file-ignores`. The exemption is deliberately the whole
package, not a per-module list, and that is the interesting part.

The original plan was to exempt only the "re-export modules". That turned out
not to be a knowable set. Three independent checks disagreed with each other:

**1. Empirical — deleting them breaks the build.** `ruff check --select F401
--fix` over `agentao/` + `tests/` applied 212 fixes, then broke **9 test
modules at collection**:

```
E ImportError: cannot import name 'AcpInteractionRequiredError' from 'agentao.acp_client.client'
E ImportError: cannot import name '_parse_retry_after' from 'agentao.llm.client'
E ImportError: cannot import name 'acp_client' from 'agentao'
```

Every failure was in `agentao/`, none in `tests/`.

**2. Static classification contradicts it.** A "is this name imported from
this module anywhere else in the repo" pass classified
`AcpInteractionRequiredError` and `_parse_retry_after` as *dead* — the two
names check 1 had just proved were load-bearing. Multi-line parenthesised
imports defeat the grep.

**3. The same pass called `HostEvent` dead.** `HostEvent` is in
`agentao.host.__all__` — the documented stability boundary this package
advertises to embedders.

The root cause is not tooling quality. **agentao is a published library.** A
name re-exported for downstream embedders is imported by nobody in this
repository — which is precisely what a public API looks like to a single-file
linter, and to an in-repo grep, and to the test suite. None of the three
signals available can separate "public surface" from "dead", so deleting on
their advice risks a silent breaking change for embedders.

`tests/` and `examples/` have no such ambiguity: nothing imports from a test
module, so an unused import there is unambiguously dead. That half was
cleaned (185 findings across 91 files — 184 in `tests/`, and the `examples/`
remainder) and is now gated.

To make `agentao/` decidable later, give every re-export hub an explicit
`__all__` — ruff treats `__all__` membership as usage, at which point the rule
can be turned on per-module with the ambiguity actually resolved rather than
assumed away.

### The exemption is wider than the evidence, knowingly

Worth stating plainly, because the argument above does not cover all of it:
of the 68 F401 hits in `agentao/`, 25 are in `__init__.py` files (where
re-export is the module's whole job) and 42 are in 22 leaf modules. The two
names that empirically broke the build — `_parse_retry_after` and
`AcpInteractionRequiredError` — live in exactly two of those leaf modules,
both documented compat hubs.

So the blanket ignore silences ~40 findings to protect 2, and at least one of
the silenced ones is genuinely dead: `agentao/sandbox/policy.py:32` carries an
unused `import platform`, in the very file this gate's landing PR edited.

The narrower configuration — ignore `agentao/**/__init__.py` plus the two
named hubs — would keep the rule live for the remaining ~260 modules at
arguably the same safety. It is not adopted here because "arguably" is doing
real work in that sentence: the two hubs were found by breaking the build,
not by a method that generalises, and there is no reason to believe the
enumeration is complete. Prefer the `__all__` route above to guessing at the
list.

## The four findings this gate fixed on landing

| Location | Finding | Fix |
|---|---|---|
| `agentao/sandbox/policy.py:213` | `for field, ...` shadowed `dataclasses.field` | Renamed to `field_name`, matching `_absolutize_path_fields` in the same file. The function never called `dataclasses.field`, so this was a latent trap rather than a live bug — adding such a call later would have failed with a confusing `UnboundLocalError`. |
| `agentao/cli/app.py:272` | `PlanController` imported twice, first copy unused | Dropped it from the first import. |
| `agentao/cli/_utils.py:154` | `console` parameter shadowed the module-level import | Renamed to `console_`, matching the identical fix in `replay_render/_turn.py`, `_views.py` and `_banners.py`. |
| `tests/test_host_typing.py:227` | `import sys` inside a function already importing it at module scope | Dropped the local re-import. |

Note the third was **not** fixed with `# noqa` — a suppression preserves
exactly the confusion the rule exists to flag. It was also not fixed by
deleting the parameter, which a first pass did and which was wrong for a
different reason: this repo captures and redirects CLI output by swapping the
`console` attribute on the *handler* module (`patch.object(acp_mod, "console",
...)` in `tests/test_acp_client_cli.py`), and the renderer lives in a
different module with its own import. Deleting the parameter would have made
`/memory user` print its chrome into the captured sink and its entries onto
the real stdout. Rename, do not remove, when the shadowed thing is an
injection point.

## Suppressing a false positive

`F401` has one known-legitimate suppression shape in `tests/`: a shared
fixture imported for pytest's name-based injection rather than for direct
use. `tests/support/acp_server.py` explicitly invites this ("wrap these in
`@pytest.fixture` when parameterising over `tmp_path`"), and ruff cannot see
that kind of reference — worse, its offered autofix deletes the import and
turns every test in the file into `fixture ... not found` at collection.

Write it as:

```python
from tests.support.acp_server import mock_server_fixture  # noqa: F401 — pytest fixture injection
```

Always with a reason after the code. A bare `# noqa` is not reviewable.

## Running locally

```bash
uv run ruff check .
```

This is character-for-character the CI command. Both the rules
(`[tool.ruff.lint]`) and the scope (ruff's repo walk plus `[tool.ruff]`
exclusions) live in `pyproject.toml`, so there is no directory list to keep
in sync in two places — an earlier revision hand-duplicated one across the
workflow and two spots in this document, and it had already drifted from what
shipped.

## Raising the bar later

Add rules one at a time, and only after measuring what each would fire on and
reading a sample of the hits. The precedent this document sets is that a
ruleset earns its place by finding defects, not by being a recognised standard.

Next candidate, in order: **`F841`** — it already earned its place by
surfacing the `drafts.py` frontmatter defect (see above); what remains is
reading its other hits. After that, `B` (flake8-bugbear) is worth measuring; `UP`, `SIM`
and `I` are explicitly out until someone can point at a defect they would
have caught here.
