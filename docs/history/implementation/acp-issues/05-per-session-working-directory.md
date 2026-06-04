# Refactor Agentao To Support Per-Session Working Directories

## Problem

Current runtime code relies on `Path.cwd()` in several places. ACP sessions require working directory semantics that are not tied to the server process global cwd.

## Scope

- Introduce session/runtime working directory context
- Remove direct dependence on process-global cwd in ACP execution paths

## Implementation Checklist

- [ ] Audit all runtime `Path.cwd()` usage
- [ ] Add optional `working_directory` to `Agentao.__init__`
- [ ] Route system prompt cwd rendering through session context
- [ ] Update file, shell, memory, and session persistence paths as needed
- [ ] Define project-local `.agentao/` behavior under ACP sessions
- [ ] Add tests for multiple sessions with different cwd values

## Acceptance Criteria

- [ ] Two ACP sessions with different cwd values do not leak state
- [ ] File and shell operations resolve relative paths against the correct session cwd
- [ ] Session metadata and prompts report the correct working directory

## Dependencies

- Depends on: `03-acp-session-registry.md`
- Strongly related to: `04-acp-session-new.md`
