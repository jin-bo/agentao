# Changelog

All notable changes to Agentao are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.2.8-rc1] — 2026-04-11

Headline: **ACP Client for project-local server management** — Agentao can
now act as an ACP client, connecting to and managing external ACP-compatible
agent processes configured per-project. The old monolithic CLI is refactored
into a modular `agentao/cli/` package for maintainability.

### Added

- **ACP client subsystem** (`agentao/acp_client/`, ~2 400 lines)
  - `ACPManager` — top-level façade: lazy init on first `/acp` command,
    config loading, server lifecycle orchestration
  - `ACPClient` — per-server JSON-RPC 2.0 client over stdio with NDJSON
    framing; handles `initialize` + `session/new` handshake, `session/prompt`,
    `session/cancel`, and notification dispatch
  - `ACPProcessHandle` — subprocess lifecycle (spawn, graceful shutdown,
    stderr ring buffer for diagnostics)
  - `Inbox` — bounded message queue with idle-point flush; messages from
    ACP servers stay separate from the main conversation context
  - `InteractionRegistry` — tracks pending permission and input requests
    from servers; supports `approve`, `reject`, and `reply` resolution
  - `AcpServerConfig` / `AcpClientConfig` models with validation
  - `load_acp_client_config()` — reads `.agentao/acp.json` (project-only;
    no global config)
  - Rich-based `render.py` for CLI output formatting
- **`/acp` CLI commands**: `list`, `start`, `stop`, `restart`, `send`,
  `cancel`, `status`, `logs`, `approve`, `reject`, `reply`
- **ACP extension method `_agentao.cn/ask_user`** — advertised in
  `initialize` response `extensions` array; enables ACP servers to request
  free-form text input from the user. `ACPTransport.ask_user()` implemented
  with full error handling (all failures resolve to a sentinel, never crash
  the turn)
- **`ACPTransport.on_max_iterations()`** — conservative default: stops the
  turn when max iterations reached (no interactive menu in ACP mode)
- **Domain-based permission rules for `web_fetch`** in `PermissionEngine`:
  - `_extract_domain()` — URL parsing with missing-scheme handling
  - `_domain_matches()` — supports leading-dot suffix matching
    (`.github.com` matches `github.com` and `api.github.com`) and exact
    matching (`r.jina.ai`)
  - Preset allowlist: `.github.com`, `.docs.python.org`, `.wikipedia.org`,
    `r.jina.ai`, `.pypi.org`, `.readthedocs.io` → auto-allow
  - Preset blocklist: `localhost`, `127.0.0.1`, `0.0.0.0`,
    `169.254.169.254`, `.internal`, `.local`, `::1` → auto-deny
  - Domain rules displayed in `/permissions` output
- **`docs/features/acp-client.md`** — full configuration reference,
  lifecycle, interaction bridge protocol, diagnostics, and troubleshooting

### Changed

- **CLI refactored from monolith to package** — the old `agentao/cli.py` (3 246
  lines) replaced by `agentao/cli/` package (~3 800 lines across 12
  modules): `app.py`, `commands.py`, `commands_ext.py`, `entrypoints.py`,
  `session.py`, `subcommands.py`, `transport.py`, `_globals.py`, `_utils.py`
- `PermissionEngine.evaluate()` now checks `domain` rules before falling
  through to regex-based `args` matching
- `PermissionEngine.explain()` renders domain allowlist/blocklist in the
  rule detail output
- README.md / README.zh.md updated with ACP Client section

### Tests

- **7 new test files** (~2 300 lines): `test_acp_client_cli.py`,
  `test_acp_client_config.py`, `test_acp_client_inbox.py`,
  `test_acp_client_jsonrpc.py`, `test_acp_client_process.py`,
  `test_acp_client_prompt.py`, `test_acp_ask_user.py`
- Existing CLI tests updated for the `agentao.cli` → `agentao.cli.app`
  import path change

---

## [0.2.7] — 2026-04-09

Headline: **Agent Client Protocol (ACP)** — Agentao can now be driven as
a headless JSON-RPC agent runtime by ACP-compatible clients (e.g. Zed).
The entire ACP wire protocol, per-session working directory isolation,
session-scoped MCP injection, and multi-session lifecycle are new.

The retriever's CJK tokenization is upgraded from character bigrams to
jieba word segmentation, and the memory subsystem's startup resilience is
hardened so restricted / read-only environments no longer crash the
constructor.

### Added

- **ACP stdio JSON-RPC server** (`agentao/acp/`, ~3 500 lines)
  - Launch with `agentao --acp --stdio` or `python -m agentao --acp --stdio`
  - Methods: `initialize`, `session/new`, `session/prompt`,
    `session/cancel`, `session/load`
  - Server→client `session/request_permission` with `allow_once` /
    `allow_always` / `reject_once` / `reject_always` options
  - Stdout guard: `sys.stdout` redirected to stderr on ACP entry so
    stray `print()` anywhere in the process never corrupts the NDJSON
    wire; JSON-RPC responses use a captured handle to the real stdout
  - Capability advertisement: `text` + `resource_link` content blocks,
    stdio + sse MCP transport, no `fs.*`/`terminal.*` host proxying
  - `AcpServer`, `AcpSessionManager`, `AcpSessionState`, `ACPTransport`
    (maps Agentao transport events to ACP `session/update` notifications)
- **`python -m agentao` module entry point** (`agentao/__main__.py`) so
  the CLI works even when the console script is not on PATH
- **Per-session working directory isolation** (Issue 05)
  - `Agentao(working_directory=Path)` freezes memory, permissions, MCP
    config, AGENTAO.md loading, system-prompt rendering, file tools, and
    shell tool against that path
  - `Agentao.working_directory` property: `None` → lazy `Path.cwd()`
    (CLI compatibility); `Path` → frozen resolved path (ACP sessions)
  - `Tool._resolve_path()` / `_resolve_directory()` helpers on the base
    class; all file, search, and shell tools use them
  - `PermissionEngine(project_root=...)`, `load_mcp_config(project_root=...)`,
    `SkillManager(working_directory=...)`, `save_session(project_root=...)`,
    `load_session(project_root=...)`, `list_sessions(project_root=...)`,
    `delete_session(project_root=...)`, `delete_all_sessions(project_root=...)`
    all accept an explicit project root; `None` falls back to `Path.cwd()`
- **Session-scoped MCP server injection** (Issue 11)
  - `Agentao(extra_mcp_servers=...)` merges in-memory configs on top of
    file-loaded `.agentao/mcp.json` (name-level override, no disk writes)
  - ACP `session/new` `mcpServers` wire field → translated by
    `agentao.acp.mcp_translate.translate_acp_mcp_servers()`
- **LLM log file fallback** — `LLMClient._build_file_handler()` resolves
  `agentao.log` to an absolute path anchored to the working directory;
  when the target is unwritable (ACP launches with cwd `/` on macOS),
  falls back to `<home>/.agentao/agentao.log`
- **jieba word segmentation for CJK retrieval** — `MemoryRetriever` now
  segments Chinese/Japanese/Korean text with jieba instead of character
  bigrams. `"版本管理"` → `{"版本", "管理"}` (was `{"版本", "本管", "管理"}`).
  Single-character CJK tokens filtered out (matches the Latin `len > 1`
  rule). Custom dictionary: `<home>/.agentao/userdict.txt` (lazy-loaded on
  first recall). New dependency: `jieba>=0.42.1`
- **Inverted index in `MemoryRetriever`** — `write_version`-gated
  token → record-ID map so recall scores only records sharing at least
  one query token; avoids full-scan as memory store grows

### Changed

- `MemoryManager.__init__` widened exception handling from `OSError` to
  `(OSError, sqlite3.Error)` on both project-store and user-store init
  branches. The previous `OSError`-only catch missed
  `sqlite3.OperationalError: unable to open database file` raised when
  the directory exists but the DB cannot be opened/WAL-journaled,
  crashing `Agentao()` in restricted environments and killing every ACP
  session spawn. Each fallback now logs a `WARNING` (was silent)
- `_cjk_bigrams()` replaced by `_cjk_segment()` (jieba-backed); bigram
  noise eliminated from CJK recall scoring
- CLI `entrypoint()` extended: `--acp` and `--stdio` flags; `--acp`
  short-circuits to `run_acp_mode()` before any Rich/interactive setup;
  `--stdio` without `--acp` exits with error code 2
- `SkillManager` now resolves project-scoped skill dirs and config files
  from an explicit `working_directory` at construction time; two ACP
  sessions in different repos see independent skill sets and
  disabled-skill state

### Fixed

- **`Agentao()` crash in restricted / non-writable environments** —
  `sqlite3.OperationalError` from the user-scope memory DB now triggers
  the fallback path (user store disabled, project store in-memory) instead
  of propagating as an unhandled exception. Root cause of ACP subprocess
  smoke-test failures and plain `Agentao(api_key='x')` startup failure
  when `<home>/.agentao/memory.db` is unwritable

### Tests

- **336 new ACP tests** across `test_acp_initialize.py`,
  `test_acp_session_new.py`, `test_acp_session_prompt.py`,
  `test_acp_session_cancel.py`, `test_acp_session_load.py`,
  `test_acp_session_manager.py`, `test_acp_protocol.py`,
  `test_acp_mcp_injection.py`, `test_acp_multi_session.py`,
  `test_acp_request_permission.py`, `test_acp_transport.py`,
  `test_acp_cli_entrypoint.py`
- **Per-session cwd isolation tests** in `test_per_session_cwd.py`:
  tool path resolution, memory DB binding, skill isolation, LLM log
  anchoring, ACP factory wiring, and two sqlite-fault-injection
  regressions for the restricted-env crash
- **Memory init fallback regressions** in `test_memory_manager.py`:
  `test_project_store_sqlite_error_falls_back_to_memory`,
  `test_user_store_sqlite_error_leaves_user_store_none`
- Suite total: **1035 tests** (1034 passing, 1 skipped), up from 657

---

## [0.2.6] — 2026-04-09

Promotes 0.2.6-rc1 to general availability. The substantive Added /
Changed / Removed / Fixed / Tests breakdown of the memory subsystem rewrite
lives in the `[0.2.6-rc1]` entry below — that is the content of this
release. The only commits between rc1 and final are CI-only workflow
hardening so the publish pipeline actually succeeds.

### Packaging / CI

- Bump `actions/checkout@v4` → `@v5` and `astral-sh/setup-uv@v5` → `@v6`
  so CI workflows run on the Node.js 24 runner. GitHub deprecated
  Node.js 20 actions on 2025-09-19; bumping to the next major of each
  clears the deprecation warning on every run
- Drop the invalid `--repository` flag from `twine check` in
  `publish-testpypi.yml`. `--repository` is valid for `twine upload` but
  not `twine check`, which only validates dist metadata locally — the
  flag was causing the TestPyPI workflow to exit with code 2 on every
  RC attempt
- Activate the venv with `source .publish-venv/bin/activate` (and
  `.publish-testpypi-venv`) before the publish smoke step instead of
  invoking `.venv/bin/python` directly. Direct-invoke does not put the
  venv's `bin/` on `PATH`, so `shutil.which('agentao')` returned `None`
  and failed the smoke test even though the entry-point script was
  installed correctly. Applied to both `publish.yml` and
  `publish-testpypi.yml`

### Notes

**No library-code changes between 0.2.6-rc1 and 0.2.6.** Memory
subsystem, prompt injection, crystallization pipeline, retriever
scoring, and CLI surface are byte-identical to the RC; only
`.github/workflows/*.yml` was touched.

---

## [0.2.6-rc1] — 2026-04-09

Headline: complete memory subsystem rewrite. SQLite replaces the old JSON
files; persistent memories, session summaries, and dynamic recall candidates
are now distinct, structured data types; conservative rule-based
crystallization sediments user statements into a review queue rather than
silently writing.

### Added

- **SQLite-backed memory subsystem** — `agentao/memory/`
  - Two stores: `.agentao/memory.db` (project) and `<home>/.agentao/memory.db` (user)
  - Schema v3 with `memories`, `session_summaries`, `memory_review_queue`,
    `memory_events`, `schema_meta`
  - Three data types modeled separately: persistent `MemoryRecord`,
    `SessionSummaryRecord`, in-memory `RecallCandidate`
- **Two prompt-injection blocks** built per turn
  - `<memory-stable>`: durable facts (`get_stable_entries()` policy:
    user-scope always, structural types always, project_fact/note capped at
    3 most-recent) plus a pre-reserved cross-session summary tail
  - `<memory-context>`: top-k recall candidates scored against the current
    user query
- **Cross-session summary recall** — `MemoryManager.get_cross_session_tail()`
  surfaces summaries from prior sessions through `<memory-stable>` so
  conversation continuity survives a restart, not only an in-process
  compaction
- **`MemoryRetriever` with five-factor scoring**
  - tag match (4.0, dampened to 1.5/2.5 for ≤2-token queries to prevent
    single-tag over-recall)
  - title Jaccard (3.0)
  - tokenized keyword match (2.0; compound keywords like `agent.py` are
    sub-tokenized so they match a query token `agent`)
  - content snippet match on first 500 chars (1.0)
  - filepath hint from context (2.0)
  - recency / staleness modifiers
  - CJK bigram tokenization, light Latin normalization (plurals, version
    prefixes), Latin↔CJK boundary splitting, dynamic char budget,
    `exclude_ids` parameter so dynamic recall never duplicates stable entries
- **Conservative rule-based crystallization with review queue**
  - `MemoryCrystallizer` rule patterns extract preference / constraint /
    decision / workflow only, in English and Chinese
  - Extraction runs on **raw user messages** (`extract_from_user_messages`),
    never on LLM-generated summary prose — assistant narration that happens
    to contain pattern words can never trigger a false match
  - Candidates land in `memory_review_queue` with `source="crystallized"`,
    not silently into live memories
  - Repetition aggregation: same `(scope, key)` matched in multiple user
    messages folds into one row with incremented `occurrences`; confidence
    is auto-raised to `inferred` at 2+ hits
  - Auto-trigger inside `ContextManager.compress_messages()` (Step 4b),
    against the about-to-be-compacted user-message window
- **CLI memory commands**: `/memory list/search/tag/delete/clear/user/project/session/status/crystallize/review`
  including `/memory review approve <id>` and `/memory review reject <id>`
- **Recall observability**: `/memory status` reports retrieval hits, recall
  errors, last error message, stable block size, and latest session summary
  size
- **`clear_all_session_summaries()`** for hard reset across all sessions
- **Memory subsystem decoupled from the LLM stack** —
  `agentao/__init__.py` uses PEP 562 `__getattr__` for lazy `Agentao` /
  `SkillManager` resolution, so `import agentao.memory` no longer pulls
  `openai`, `mcp`, `agentao.tools.*`, or `agentao.llm.*`. Cold import:
  **334 ms → 35 ms** (~10×); zero heavy modules leaked. Locked in by
  subprocess-isolated regression tests in `tests/test_memory_decoupling.py`

### Changed

- **Search unified across five fields** — `SQLiteMemoryStore.search_memories`
  LIKEs over `title`, `content`, `key_normalized`, `tags_json`, and
  `keywords_json` (was three). `/memory search` and `MemoryRetriever` now
  cover the same surface
- **Stable block budget eviction is recency-priority** — under budget
  pressure, the renderer admits records newest-first (greedy fit walking
  records in reverse) so a fresh decision/constraint is never crowded out
  by long-tail history. Survivors render in created_at-ASC order so the
  prompt-cache prefix stays stable across turns
- **Review queue duplicate folding refreshes ALL presentation fields** —
  re-hits update `type`, `title`, `content`, `tags_json` (not just
  `evidence` / `occurrences`) so the reviewer always sees the latest
  extraction instead of the first one
- **`/memory clear` and `/clear`** now wipe ALL session summaries via
  `clear_all_session_summaries()`. Previously they only deleted the current
  session, leaving prior-session summaries to silently resurface via the
  cross-session tail
- **`MemoryManager.save_session_summary()`** is now a pure persistence call.
  Crystallization moved upstream to `compress_messages()` so it sees raw
  user text instead of LLM-narrated summaries
- **Manager facade methods** rewired: `crystallize_recent_sessions(limit)`
  → `crystallize_user_messages(messages)`; same approve/reject API
- **`MemoryGuard.classify_type` / `classify_scope`** drive tag-based memory
  type and scope inference

### Removed

- `pinned`, `ttl_days`, `expires_at` fields from `MemoryRecord` — added
  speculatively, never had a functional write path. SQL schema bumped to v3
  with a `DROP COLUMN` migration for existing databases (silent skip on
  SQLite < 3.35.0)
- `MemoryCrystallizer.extract_from_sessions()` — operated on LLM-narrated
  session summaries, exactly the regex-on-summary path the new design
  rejects
- `MemoryManager.crystallize_recent_sessions()` — superseded by
  `crystallize_user_messages()`

### Fixed

- **`/new` was wiping the just-finished session's summaries** — the branch
  called `clear_session()` before `archive_session()`, so cross-session
  recall lost the most recent context. `clear_session()` is no longer
  invoked from `/new`; `archive_session()` (in `on_session_start()`) is the
  correct primitive. (Codex P2)
- **`Agentao._extract_context_hints` read the wrong key on text blocks** —
  list-shaped message content had `block.get("content")` instead of
  `block.get("text")`, silently dropping every multimodal/tool-use message
  and breaking `filepath_hint` scoring. Now matches the canonical
  `{"type": "text", "text": ...}` shape used by `_format_for_summary` and
  `_user_message_text`. (Codex P2)
- **Recall errors are now observable** — exceptions inside
  `MemoryRetriever.recall_candidates()` log a WARNING with traceback,
  increment `_error_count`, and record `_last_error` instead of being
  swallowed silently
- **`<memory-stable>` cross-session tail is pre-reserved** so persistent
  facts can never crowd out the previous-session summary
- **Dynamic recall hard budget** — `render_dynamic_block()` enforces
  `DYNAMIC_RECALL_MAX_CHARS` (~1200) and trims candidates that don't fit
- **Stable block budget pre-reservation refactor** uses a deterministic
  greedy fit instead of "stop at first overflow"

### Tests

- ~300 new memory-subsystem tests across `test_memory_store.py`,
  `test_memory_manager.py`, `test_memory_session.py`, `test_memory_renderer.py`,
  `test_retriever.py`, `test_crystallizer.py`, `test_memory_guards.py`,
  `test_memory_injection.py`, and `test_memory_decoupling.py`
- Suite total: **657 passing**, 1 skipped, 0 failing
- Notable regression guards:
  - `test_new_session_flow_preserves_cross_session_recall` (Codex P2 fix)
  - `test_extracts_paths_from_list_text_blocks` (Codex P2 fix)
  - `test_budget_eviction_preserves_newest_decision` (eviction priority)
  - `test_assistant_narration_does_not_trigger` (crystallization safety)
  - `test_clear_all_session_summaries_removes_cross_session_summaries`
  - `test_search_unified_finds_record_via_any_field`

---

## [0.2.5] — 2026-04-07

### Added

- **`agentao init` setup wizard** — first-run interactive bootstrap for
  `.agentao/` config, API keys, and skill discovery
- **Background agent lifecycle** — pending state, cancellation token plumbing,
  on-disk persistence so the dashboard survives restarts
- **`cwd/skills/`** added as a third highest-priority skills layer
  (overrides project and bundled skills); two-layer scan with first-run
  bootstrap of bundled skills
- **Windows compatibility** for the shell tool and terminal handling
- **130 new tests** covering permissions, skills, MCP, and background agents
- README "Minimum Viable Configuration" section

### Changed

- Bundled office / pdf / ocr skills removed from the default install
  (slimmer wheel; users opt in via the `pdf` / `excel` / `image` extras)
- Install path unified to `pip install agentao` across docs and README
- ChatAgent / Claude naming remnants cleaned up

### Packaging / CI

- GitHub Actions workflow with test / build / smoke matrix
- PyPI release workflows
- `main.py` and `.claude/` excluded from sdist
- `skills/skill-creator` included in wheel; other internal skills marked private

### Fixed

- `/plan save` CLI command removed to match the documented plan-mode v2
  contract (model-driven `plan_save` tool only)

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
