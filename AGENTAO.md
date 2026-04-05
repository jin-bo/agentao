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
- Distinguish what you have read (cite the file and line) from what you infer.