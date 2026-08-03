# Agentao Project Instructions

When working with Python, use uv for package management instead of pip.

## Workspace

Generated files (scripts, reports, data outputs, downloads, notes) go in the `workspace/` subdirectory by default:

| Type | Directory |
|------|-----------|
| Documentation / notes | `workspace/docs/` |
| Data files | `workspace/data/` |
| Raw / source materials | `workspace/raw/` |
| Downloaded files | `workspace/Downloads/` |
| Scripts | `workspace/scripts/` |
| Reports / output | `workspace/reports/` |
| Cloned repos | `workspace/src/` |

Only place files in the project root or source tree when they are part of the agentao codebase itself.

## Codebase Claims

When making factual claims about this codebase:
- Read the relevant file first before asserting what it contains.
- If a tool call returns an error or unexpected result, explain why before retrying.

## Before Pushing

Two required CI checks, both runnable locally:

```bash
uv run python -m pytest tests/   # default suite (the `slow` marker is excluded)
uv run ruff check .              # lint gate — defect rules only, no style
```

A green test run is not sufficient on its own; the lint gate is a separate
required check. See [docs/design/lint-gate.md](docs/design/lint-gate.md) for
which rules are enabled and why.
- Distinguish what you have read (cite the file and line) from what you infer.