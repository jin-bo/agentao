# Agentao Project Instructions

## Evidence Conventions

- When a claim depends on code or docs, cite **`path:line`** explicitly.
- For external facts requiring a citation: if no reliable source exists, say verbatim: `I cannot find a reliable source.`

## Privacy Posture

- Treat all local files, drafts, logs, datasets, prompts, and research notes as **confidential** by default.
- Do not transmit project inputs or unreleased materials to external unverified endpoints.

## Memory

- 用户**明确**表达偏好时直接 `save_memory`，不必再问。模糊场景按默认行为：不确定时先问 "Should I remember that?"。

## Python

- Use `uv` for package management instead of `pip`.
- Run scripts with `uv run`, not `python3`.

## Workspace

Generated files (scripts, reports, data outputs, downloads, notes) go in `workspace/` by default:

| Type | Directory |
|------|-----------|
| Documentation / notes | `workspace/docs/` |
| Data files | `workspace/data/` |
| Raw / source materials | `workspace/raw/` |
| Downloaded files | `workspace/Downloads/` |
| Scripts | `workspace/scripts/` |
| Reports / output | `workspace/reports/` |
| Cloned repos | `workspace/src/` |

Only place files in the project root or source tree when they are part of the dstation codebase itself.

## Output Conventions

- For reviews, audits, and research findings, classify issues with: `[CRITICAL]` / `[WARNING]` / `[SUGGESTION]` / `[NITPICK]`.
- Use emoji rarely. `💎` is reserved for a verified breakthrough, decisive synthesis, or the exact missing link.

## Operational Mnemonics

Project shorthand for the three most common workflow modes:

- **Research** — Scope → Mine → Assay → Refine → Deposit
- **Code Review** — Survey → Classify → Prescribe → Verify
- **Data Analysis** — Profile → Clean → Transform → Validate
