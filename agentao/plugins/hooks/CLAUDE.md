# Plugin hooks — rules and invariants

This file loads when Claude works under this folder. It was moved verbatim from the root `CLAUDE.md`.

## Plugin hooks

There are eight events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SessionStart`, `SessionEnd`, `PreCompact`.
- Runtime: `agentao/plugins/hooks/`. Discovery: `embedding/plugins/`. Config: `docs/reference/configuration.md` §11.
- Design: `docs/design/hooks-claude-contract-conformance-plan.md`. Measured upstream behaviour: `docs/reference/hooks-probe-2.1.251.md`.

**Contract detection.** The parser finds the contract of each file from its shape (`_parser.py::_detect_entry_shape`):
- `hooks: []` without `type`: official.
- `type` without an array: flat.
- **Both**: the parser disables the file.
- **Neither**: the parser gives a warning for that rule only.

**The two contracts:**
- `agentao-v1` is **frozen**. Gate every behaviour change on the contract, and test both halves.
- `claude-code@profile-1` is an **enumerated capability profile** (`_profile.py`, data).
- The dispatcher **ignores an unimplemented key with a one-time diagnostic, never a schema error.** To add a field, add its row.
- The dispatcher reports profile disposition (`accept` / `ignore`) once per (rule, field). Delivery (`honored` / `discarded`) is **silent**.

**Precedence** is `_resolve.py::resolve`: exit 2, then `continue`, then the event's own decision. Exit 2 can mean block, feed the model, or notify, as each event defines.

**A stop from a tool worker:**
- The verdict travels on `ToolExecutionResult.hook_stop_reason`, then `ToolRunner.last_hook_stop`, then `INCOMPLETE_HOOK_STOP`.
- The runner selects it in **plan order, never completion order**.
- Reset `last_hook_stop` at the **top** of `execute()`.
- Both seams read a **string**, because a `MagicMock` answers any attribute.

**Mixed-contract dispatch** partitions, runs, and merges once (`_dispatcher.py::_merge_pre_tool_use`). The lattice is `deny > ask > allow`. The reason tie-break ranks in the winning class, by declaration order.

- **For the path placeholders, the only correct spelling is `env=build_child_env({...})`.** `env={...}` or `env=os.environ | {...}` removes the credential scrub.
- The profile matcher is `re.fullmatch`, with special cases for `*` and `""` (`_matchers.py`).
- The diagnostic registry has **session** scope. Its key is the rule *content* (`_diagnostics.py::rule_key`), never `id(rule)`.
