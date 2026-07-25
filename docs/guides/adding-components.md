# Adding New Components

How to add a new **tool** or a new **skill** to the Agentao codebase.

This is about extending Agentao itself. If you are an embedding host injecting tools at
construction time instead, see [../design/host-tool-injection.md](../design/host-tool-injection.md).

## A tool

1. Create `agentao/tools/<module>.py` and implement `Tool` (or `AsyncToolBase` for async).
   Both live in `agentao/tools/base.py`; `RegistrableTool = Tool | AsyncToolBase`.
2. Set `requires_confirmation=True` for anything dangerous: arbitrary shell, network
   requests, file writes, deletions. This is what routes the tool through
   `PermissionEngine` — see [tool-confirmation.md](tool-confirmation.md).
3. Register in `agentao/tooling/registry.py::register_builtin_tools()`.

Note that `agent.py::_register_tools()` is a thin delegation — the real wiring lives in
`register_builtin_tools()`, so registering in `agent.py` is the wrong place.

## A skill

1. Create `skills/<my-skill>/SKILL.md` with YAML frontmatter (`name:`, `description:` —
   the trigger text the model sees).
2. Optionally add `references/*.md` files. These load only on activation, which keeps the
   always-resident cost to just the name and description.
3. Restart the agent or run `/skills reload`.

For discovery, activation, and the full frontmatter schema, see [skills.md](skills.md).
