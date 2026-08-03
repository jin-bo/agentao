# Lint gate

**Status:** landed. CI job `lint-gate`, config in `pyproject.toml :: [tool.ruff.lint]`.

## What it is

`ruff check agentao/ tests/ examples/` with exactly five rule groups:

| Rule | Catches | Scope |
|---|---|---|
| `E9` | Syntax / IO errors — code that cannot run at all | everywhere |
| `F402` | Import shadowed by a loop variable — latent `UnboundLocalError` | everywhere |
| `F811` | Redefinition of an unused name — one of the two is dead | everywhere |
| `F821` | Undefined name — guaranteed `NameError` at runtime | everywhere |
| `F401` | Unused import | `tests/` + `examples/` only — see below |

Nothing else. No style, no formatting, no import sorting, no modernization.

## Why this narrow

The selection came from measuring, not from taste. Run against the tree at
`7eb762e`:

| Ruleset | Findings | Live bugs found |
|---|---|---|
| `UP` (pyupgrade) | 2652 | — not a defect class |
| `F401` (unused-import) | 255 (169 `tests/`, 68 `agentao/`) | 0 |
| `E9,F402,F811,F821` | 4 | **0** |

(The `tests/` figure grew to 184 by the time F401 was applied — the two
preceding test-cleanup PRs stranded imports of their own.)

All four gate findings were read individually before being fixed; none was a
live bug. So the honest claim for this gate is **not** "it found bugs" — it is
"it costs ~15s of CI and pins a class that has already bitten this repo once."

That once is `eef5b70` (PR #141), whose title is *"declare plugin types for
F821"*. F821 measured 0 when this gate landed; the gate is what keeps it there.

The opinionated sets were rejected on evidence. `UP`'s 2652 findings would
rewrite working code across the tree and bury real changes in review. This
repo has already declined a comparable ratchet on the same grounds — see
`docs/design/refactor-audit-2026-07.md`, where 27 of 89 mypy findings were
mixin false positives.

## Why F401 is off for `agentao/`

`F401` is enforced on `tests/` and `examples/`, and **exempted wholesale for
`agentao/`** via `per-file-ignores`. The exemption is deliberately the whole
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

`tests/` has no such ambiguity: nothing imports from a test module, so an
unused import there is unambiguously dead. That half was cleaned (184
findings across 91 files) and is now gated.

To make `agentao/` decidable later, give every re-export hub an explicit
`__all__` — ruff treats `__all__` membership as usage, at which point the rule
can be turned on per-module with the ambiguity actually resolved rather than
assumed away.

## The four findings this gate fixed on landing

| Location | Finding | Fix |
|---|---|---|
| `agentao/sandbox/policy.py:213` | `for field, ...` shadowed `dataclasses.field` | Renamed to `field_name`, matching `_absolutize_path_fields` in the same file. The function never called `dataclasses.field`, so this was a latent trap rather than a live bug — adding such a call later would have failed with a confusing `UnboundLocalError`. |
| `agentao/cli/app.py:272` | `PlanController` imported twice, first copy unused | Dropped it from the first import. |
| `agentao/cli/_utils.py:154` | `console` parameter shadowed the module-level import | Removed the parameter. Both call sites passed the same `._globals.console` this module already imports, so the parameter only shadowed it. |
| `tests/test_host_typing.py:227` | `import sys` inside a function already importing it at module scope | Dropped the local re-import. |

Note the third was fixed by deleting the redundant parameter rather than by
adding `# noqa`. A suppression would have preserved exactly the confusion the
rule exists to flag.

## Running locally

```bash
uv run ruff check agentao/ tests/ examples/
```

Rule selection is in `pyproject.toml`, so the local and CI invocations cannot
drift apart.

## Raising the bar later

Add rules one at a time, and only after measuring what each would fire on and
reading a sample of the hits. The precedent this document sets is that a
ruleset earns its place by finding defects, not by being a recognised standard.
