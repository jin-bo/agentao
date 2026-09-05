# Vendored test fixtures

Files here come from another project and are kept **byte-for-byte**. That is the point: a
lowering graded only against cases its own author wrote is graded against the author's belief
about the language it lowers.

## `powershell_lowering.json`

| | |
|---|---|
| Upstream | `openai/codex`, `codex-rs/shell-command/src/command_safety/fixtures/powershell_lowering.json` |
| Anchor | `openai/codex@b7cd519c76` (2026-08-31) |
| Licence | Apache-2.0 (upstream); this repository is MIT |
| Used by | `tests/test_powershell_lowering.py` (LOWER-04) |

68 cases: 24 pin an exact argv, 44 pin a refusal. Do not edit it to make a test pass — an
edited corpus is a corpus that agrees with this implementation by construction, which is the
one thing it exists not to do. If upstream changes it, re-copy the whole file and record the
new anchor here.
