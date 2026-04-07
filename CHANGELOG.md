# Changelog

All notable changes to Agentao are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.2.3] — 2026-04-06

### Added
- **Plan mode v2** — tool-driven save/finalize workflow
  - `plan_save(content)` tool: persists a draft and returns a `draft_id`
  - `plan_finalize(draft_id)` tool: triggers the approval prompt; stale IDs are rejected
  - Approval prompt shows the full plan before asking "Execute this plan? [y/N]"
  - One-shot `consume_approval_request()` flag prevents repeated approval prompts
  - Auto-save fallback skipped when a draft is already finalized
- `agentao/plan/` sub-package: `session.py` (3-state FSM), `controller.py` (single exit path), `prompt.py` (mandatory turn protocol)
- 44 new tests covering FSM transitions, lifecycle, tools, and prompt structure

### Changed
- Plan approval prompt now only appears after the model explicitly calls `plan_finalize`
- `/plan save` removed as a CLI command; saving is now model-driven via the `plan_save` tool

### Fixed
- Finalized drafts can no longer be overwritten by the auto-save fallback path

### Packaging
- Added MIT `LICENSE` file (Bo Jin)
- Heavy optional dependencies (`pymupdf`, `pdfplumber`, `pandas`, `openpyxl`, `Pillow`, `pycryptodome`, `google-genai`) moved to optional extras: `pdf`, `excel`, `image`, `crypto`, `google`; `full` installs everything
- `skills/` and `workspace/` excluded from both wheel and sdist
- `requires-python` lowered from `>=3.12` to `>=3.10`
- Added `authors`, `license`, `keywords`, `classifiers`, `[project.urls]`
- Version is now defined once in `agentao/__init__.py` and read dynamically by hatchling

---

## [0.2.1] — 2026-03-xx

### Added
- **Permission mode system** — three named presets: `read-only`, `workspace-write` (default), `full-access`
- `/mode` command to switch and persist permission mode to `.agentao/settings.json`
- Plan mode enforced via `PLAN` permission preset (no writes, no dangerous shell)
- Mode restored exactly on `/plan implement` or `/plan clear`

### Changed
- Tool confirmation now driven by the active permission mode rather than per-tool flags
- `/clear` resets permission escalation (`allow_all_tools`) back to False

---

## [0.2.0] — 2026-03-xx

### Added
- **Plan mode** — `/plan` enters a read-only research-and-draft workflow; agent proposes a structured Markdown plan before any mutations
- **Display engine v2** — semantic tool headers (`→ read`, `← edit`, `$ shell`, `✱ search`), buffered output, tail-biased truncation, diff rendering, warning consolidation, live elapsed timer
- **Background agent dashboard** — `/agents`, `/agent dashboard`, `/agent status`
- **Transport protocol** — decoupled runtime from UI via `EventType` stream

### Fixed
- Streaming fallback, thinking handler scope, on_max_iterations guard
- Buffer all shell output; robust `\r`/ANSI/CRLF handling

---

## [0.1.11] — 2026-02-xx

### Added
- **Three-tier context compression** — microcompaction (55% usage) + LLM summarization (65%) + circuit breaker after 3 failures
- Structured 9-section LLM summary; partial compaction keeps last 20 messages verbatim
- Three-tier overflow recovery on context-too-long API error
- **Three-tier token counting** — real `prompt_tokens` from API → `count_tokens` API → local estimator (tiktoken / CJK heuristic)
- `/context` command with token breakdown by component
- Background agent push via `CancellationToken`

---

## [0.1.8] — 2026-01-xx

### Added
- **Sub-agent system** — foreground and background sub-agents with parent context injection and stats footer
- `/agent bg <name> <task>` for background execution
- Tool output file saving, head+tail truncation, per-line length limit
- `/new` command; auto `max_completion_tokens`; session lifecycle hooks

---

## [0.1.5] — 2025-12-xx

### Added
- **Task checklist** (`todo_write`) — LLM-managed task list injected into system prompt; visible via `/todos`
- **MCP (Model Context Protocol)** support — stdio and SSE transports; `mcp_*` tool registration
- **Memory management** — persistent `.agentao_memory.json`; `save_memory`, `search_memory`, `delete_memory` tools; `/memory` commands
- **Permission system** — per-tool confirmation with single-key menu; session escalation with **2** (Yes to all)
- Cognitive Resonance — automatic memory recall with injection confirmation before each response
- Session save/resume (`/sessions`)

---

## [0.1.1] — 2025-11-xx

### Added
- Renamed to **Agentao** (Agent + Tao)
- Gemini provider support (`google-genai`)
- `web_fetch` with automatic crawl4ai fallback for JS-heavy pages
- `/confirm`, `/stream`, `/tools`, `/provider` commands
- Sub-agent system (early version)
- `ask_user` tool for LLM-initiated clarification
- `-p` / `--print` flag for non-interactive print mode
- Multi-line paste via `prompt_toolkit`; single-key confirmation via `readchar`

### Changed
- System prompt: reliability principles, structured reasoning (Action / Expectation / If wrong), operational guidelines
- Context management: `ContextManager`, pinned messages, tool result truncation

---

## [0.1.0] — 2025-10-xx

### Added
- Initial release as **ChatAgent**
- CLI chat loop with OpenAI-compatible API
- Tool system: `read_file`, `write_file`, `replace`, `glob`, `grep`, `run_shell_command`, `web_fetch`, `google_web_search`, `save_memory`
- Skills system — auto-discovery from `skills/` with YAML frontmatter
- `AGENTAO.md` auto-loading for project-specific instructions
- Current date injected as `<system-reminder>`
- Complete LLM interaction logging to `agentao.log`
