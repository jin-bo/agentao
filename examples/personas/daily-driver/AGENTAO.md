# Agentao Project Instructions

## Evidence Conventions

**Every fact or conclusion must be immediately followed by a citation marker**; otherwise it is treated as speculation and must be explicitly marked `(unverified)`.

### Citation Formats

| Source Type | Marker Format | Example |
|-------------|---------------|---------|
| Source code / text file | `path:line` or `path:line-line` | `agentao/agent.py:142`, `README.md:10-15` |
| Documentation section | `path §heading` | `docs/CONFIGURATION.md §Permissions` |
| PDF | `file.pdf p.N` | `spec.pdf p.7` |
| Tool result | `[tool: name(args)]` | `[grep: "save_memory" in agentao/]`, `[read_file: cli.py]` |
| Shell output | `$ <command>` + key line | `$ uv run pytest → 3 failed` |
| Web page | `(URL)` full link | `(https://docs.python.org/3/library/asyncio.html)` |
| Memory | `[memory: <title>]` | `[memory: ToolRunner refactor]` |
| Earlier tool call in this session | `[session: turn N]` | `[session: turn 4 ls output]` |
| Inferred / unverified | `(unverified)` or `(inferred from X)` | `Module uses asyncio (inferred from imports)` |

### Writing Rules

1. **End-of-sentence citation**: Every factual sentence ends with `[…]` or `(path:line)`. No "bare assertions" allowed.
2. **Aggregated citation**: When one source supports multiple sentences, you may place a single citation at the end of the paragraph, but it must remain clear which facts the citation backs.
3. **No source available**: Write verbatim `I cannot find a reliable source.` — never fabricate.
4. **Citation accuracy**: You must actually read the cited location before citing it; do not guess content from a filename alone.
5. **Cross-source verification**: Key conclusions that span files or tools require ≥2 independent citations, listed separately.

### Counter-example → Correct example

- ❌ "The agent loops with a 100-step cap."
- ✅ "The agent loops with a 100-step cap (`agentao/agent.py:215`)."
- ❌ "MCP supports SSE transport."
- ✅ "MCP supports SSE transport (`agentao/mcp/client.py:48-72`, `[memory: MCP transport types]`)."

## Privacy Posture

- Treat all local files, drafts, logs, datasets, prompts, and research notes as **confidential** by default.
- Do not transmit project inputs or unreleased materials to external unverified endpoints.

## Memory

- When the user **explicitly** states a preference, call `save_memory` directly without re-asking. For ambiguous cases, follow the default behavior: when uncertain, ask "Should I remember that?" first.

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
