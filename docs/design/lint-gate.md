# Lint gate

**Status:** landed. CI job `lint-gate`, config in `pyproject.toml :: [tool.ruff.lint]`.

## What it is

`ruff check agentao/ tests/ examples/` with exactly four rule groups:

| Rule | Catches |
|---|---|
| `E9` | Syntax / IO errors — code that cannot run at all |
| `F402` | Import shadowed by a loop variable — latent `UnboundLocalError` |
| `F811` | Redefinition of an unused name — one of the two is dead |
| `F821` | Undefined name — guaranteed `NameError` at runtime |

Nothing else. No style, no formatting, no import sorting, no modernization.

## Why this narrow

The selection came from measuring, not from taste. Run against the tree at
`7eb762e`:

| Ruleset | Findings | Live bugs found |
|---|---|---|
| `UP` (pyupgrade) | 2652 | — not a defect class |
| `F401` (unused-import) | 255 (169 `tests/`, 68 `agentao/`) | 0 |
| `E9,F402,F811,F821` | 4 | **0** |

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

## Why F401 is not in the gate

`F401` (unused-import) is the largest single category and looks like the
obvious win. It is not enforceable on `agentao/` as-is, because **a re-export
reads as unused inside the module that defines it.**

Measured, not assumed: running `ruff check --select F401 --fix` over
`agentao/` + `tests/` applied 212 fixes and then broke **9 test modules at
collection**:

```
E ImportError: cannot import name 'AcpInteractionRequiredError' from 'agentao.acp_client.client'
E ImportError: cannot import name '_parse_retry_after' from 'agentao.llm.client'
E ImportError: cannot import name 'acp_client' from 'agentao'
```

Every failure was in `agentao/`, none in `tests/`. The deleted names were the
public surface of `agentao/llm/client.py`, `agentao/acp_client/client.py` and
the `__init__.py` re-export hubs (`agentao/cli/__init__.py` alone accounts for
20 of the 68).

Enforcing F401 therefore needs `per-file-ignores` for the re-export modules
first. That is tracked as separate work: clean the 169 in `tests/` (no
re-export semantics there), exempt the re-export modules in `agentao/`, then
add `F401` to `select`.

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
