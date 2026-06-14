# Changelog

All notable changes to Agentao are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

_Targeting 0.4.11. Add entries under the relevant heading as work lands._

### Added

### Changed

- **`AGENTAO.md` now drops a leading YAML frontmatter block before injecting
  it into the system prompt.** Previously the file was read verbatim, so a
  frontmatter block carried over from a Cursor rule or another tool's
  instruction file (`---\nname: ...\n---`) leaked into the prompt as literal
  text. `load_project_instructions` now routes the contents through a new
  `agentao.prompts.strip_frontmatter()` helper. Stripping is conservative — it
  only fires when the document genuinely opens with a frontmatter *mapping*
  (opening `---` fence + YAML mapping + closing `---` fence); an absent block,
  malformed YAML, or a stray `---` thematic break wrapping prose is left
  untouched so real instructions are never silently dropped. The disk-read
  path only; a host-injected `project_instructions=` string stays the host's
  responsibility. The ignored-frontmatter case is logged at INFO.

### Fixed

---

## [0.4.10] — 2026-06-14

### Added

- **Host LLM request passthrough: `Agentao(extra_body=...)`.** The LLM request
  kwargs were a closed set, so a host could not reach `reasoning_effort` /
  `top_p` / `seed` / `response_format` or any provider-specific body field
  without subclassing `LLMClient` or monkeypatching — both off the
  `agentao.host` contract. `extra_body` is the SDK-blessed escape hatch, now a
  first-class harness primitive. (#91)
  - Forwarded verbatim to the OpenAI SDK `.create()` body on both the
    non-streaming and streaming paths via a single `_build_request_kwargs()`
    (de-dups the previously closed kwargs dict); omitted when empty so requests
    stay byte-identical.
  - **Keyword-only** on the constructor so it never shifts the legacy
    positional callback args; mutually exclusive with `llm_client=` (a loud
    `ValueError`, not a silent no-op); deep-copied at construction so a host
    mutating a nested value it still holds cannot alter in-flight requests.
  - Sub-agents inherit it via the `_llm_config` snapshot. `reconfigure()`
    preserves it across a model switch with **no auto-recovery latch** — the
    host owns dropping model-specific keys (a stale `reasoning_effort` will 400
    until cleared), unlike the `omit_temperature` / `max_completion_tokens`
    latches that are reset.
  - Logged with recursive credential redaction (exact lower-cased key-name
    match over dict/list/tuple; covers body and header-style names like
    `x-api-key`, `authorization`, `client_secret`) so a benign `*_tokens` key
    is not over-redacted.
  - Env var `LLM_EXTRA_BODY` (JSON object; malformed or valid-but-non-object →
    warn + skip; empty → unset). `extra_headers`, `settings.json`, and a
    `/param` command are deferred (§8).
  - Design: `docs/design/host-llm-extra-params.md` / `.zh.md`.

### Changed

- **Context-window threshold checks anchor to the real Tier-1 prompt-token
  count.** `needs_compression` / `needs_microcompaction` re-encoded the entire
  history through tiktoken twice per turn even though the real `prompt_tokens`
  from the prior API response was already recorded and went unused. The
  threshold estimate now reuses that count for the already-sent prefix and
  locally estimates only the messages appended since. (#90)
  - Behavior note: the Tier-1 count includes system + tool-schema tokens the
    old local estimate omitted, so thresholds now fire on the true prompt
    size — marginally earlier and more accurate.
  - The anchor is invalidated on every history-replace path — full
    compression, microcompaction (previously a latent staleness bug),
    `/sessions resume`, ACP session load, in-loop overflow recovery, manual
    `/compact`, `clear_history`, and model/provider switch — via a single
    `invalidate_token_anchor()` helper that keeps the paired `(count, tokens)`
    invariant. A malformed provider `prompt_tokens` field falls back to the
    full local estimate rather than crashing the threshold path.

### Fixed

- **Context-overflow detection hardened with a provider table + negative
  guard.** `is_context_too_long_error` was a 7-phrase substring matcher with no
  negative guard: it missed overflow errors from xAI / Google / Bedrock /
  OpenRouter / Together / Mistral / llama.cpp / LM Studio / Kimi / Ollama, and
  its fallback phrase "too many tokens" misclassified Bedrock throttling
  (`ThrottlingException: Too many tokens...`) as overflow — triggering a
  destructive history compaction on a transient error. Rewritten as a two-tier
  compiled-regex table: a broadened positive pattern set, plus a negative set
  (throttling / rate limit / 429 / 503) checked first that short-circuits to
  `False`. Adds 22 positive provider cases + 5 guard cases. (#88)
- **Compaction summary now closes with an end-of-summary marker.** The summary
  was opened by the `[Compact Boundary]` system message but had no terminator,
  so the summarizer's "## 9. Next Step" section could read as a live
  instruction and the kept messages after it blurred into the summary. A
  `SUMMARY_END_MARKER` plus a one-line "historical context, resume below, do
  not re-execute completed work" frame gives the block a symmetric open/close
  bracket; on re-summarization the marker and frame are stripped (anchored on
  the `[Conversation Summary]` prefix) so they never accumulate or truncate an
  unrelated message that merely contains the marker substring. (#89)

### Security

- **Resolve-based SSRF guard for `web_fetch`.** The only SSRF defense was the
  `PermissionEngine` string blocklist, matched on the original URL at the
  plan-phase gate — it missed hostnames that *resolve* to private / loopback /
  link-local addresses (DNS rebinding, public names pointed at `127.0.0.1` /
  `169.254.169.254`), alternate IP encodings, and redirect hops into the
  internal network (the client used `follow_redirects=True` with no per-hop
  check); 8 of 13 bypass vectors slipped through. New
  `agentao/security/url_policy.py`: `validate_outbound_url()` resolves the host
  and rejects any non-global address, normalizes the hostname (trailing dot /
  case), rejects local/internal names, single-label hosts and embedded creds,
  and unwraps v4-mapped / 6to4 / NAT64 IPv6 forms; `guarded_get()` drives the
  redirect chase with `follow_redirects=False` and re-validates every hop. Both
  are wired into `WebFetchTool.execute()`; the static blocklist stays as the
  I/O-free first layer (defense in depth). (#92)

---

## [0.4.9] — 2026-06-10

### Added

- **Host tool injection: `Agentao(extra_tools=..., disable_tools=...)`.** Two
  first-class, construction-time kwargs on the embedded-host contract for
  adding, replacing, and hiding tools — replacing post-construction pokes at
  the runtime `agent.tools.register(...)`.
  - `extra_tools` — pre-built `Tool` / `AsyncToolBase` instances, registered as
    the true final pass (after built-in, MCP, and agent tools) so a same-named
    entry overrides a built-in or agent tool. Injected tools inherit the same
    working-directory / filesystem / shell capability binding as built-ins.
    Names using the reserved `mcp_` prefix raise (MCP replacement stays on
    `mcp_manager=` / `extra_mcp_servers=`); duplicates raise.
  - `disable_tools` — a set of built-in tool names to skip registering. A typo
    or unknown name raises at construction (validated against the static
    `BUILTIN_TOOL_NAMES` set) rather than silently no-op'ing. It only skips
    built-in registration — not a global denylist, not a security boundary
    (that stays with the permission engine).
  - `WebSearchTool(backend=..., api_key=...)` — explicit constructor args now
    take precedence over the `BOCHA_API_KEY` env var, so two in-process
    Agentao instances can use different search backends. Pass a configured
    instance via `extra_tools=[...]`.
  - `Tool`, `AsyncToolBase`, `RegistrableTool` are now re-exported from
    `agentao.host` as a stable import path for host tool authors.
  - Design: `docs/design/host-tool-injection.md` / `.zh.md`.

- **Runtime tool injection: `Agentao.add_tool` / `remove_tool`.** The
  post-construction dual of `extra_tools=` / `disable_tools=`, for hosts that
  add or drop a tool between `chat()` / `arun()` calls without rebuilding the
  agent (e.g. long ACP sessions). (#65)
  - `add_tool(tool, *, replace=False)` routes through the same validation +
    capability binding as `extra_tools=` (never a "bare" tool). `replace=False`
    + a name clash raises (stricter than the runtime `register`'s
    warn-and-overwrite); `replace=True` overrides a built-in / agent / extra
    tool with an INFO audit line.
  - `remove_tool(name) -> bool` unregisters via the new
    `ToolRegistry.unregister(name)`; an unknown name returns `False` instead of
    raising. It shrinks the model-visible schema — not a security boundary.
  - Reserved namespaces (`mcp_` prefix, plus the plan-mode `plan_save` /
    `plan_finalize`) are rejected by both `add_tool` (incl. `replace=True`) and
    `remove_tool` — closing the `add_tool(name="plan_save", replace=True)`
    loophole. The plan-name reservation also tightens construction-time
    `extra_tools=`.
  - Visibility: the tool schema is snapshotted once per `chat()` / `arun()`
    call, so changes take effect on the **next** call, never mid-turn.
  - Design: `docs/design/runtime-tool-injection.md` / `.zh.md`.

- **Host tool allowlist: `Agentao(enabled_tools=...)`.** The additive dual of
  `disable_tools=` — declare the minimal tool set to keep instead of
  enumerating everything to drop. Closes two gaps a blocklist can't: it's
  concise for a minimal core, and a built-in added later can't silently leak
  in (not listed → excluded). It also reaches agent-path tools at construction
  time, which `disable_tools` cannot.
  - `enabled_tools=None` (default) keeps today's behavior. Any iterable —
    including the empty set — *enables* the allowlist (`is not None`
    semantics): after registration, every built-in / agent-path tool whose
    name is absent is pruned.
  - Scope is agentao-owned tools only: `extra_tools` (host injected them
    explicitly), MCP (`mcp_*`), and plan-only tools are always kept. Minimize
    MCP at the MCP layer instead.
  - Mutually exclusive with `disable_tools` (passing both raises). Reserved
    names (`mcp_` prefix, plan-only) raise at construction; unknown names raise
    after registration (typo guard against the live registry — sharper than
    `disable_tools` since a bad allowlist name silently excludes a tool).
  - Design: `docs/design/host-tool-allowlist.md` / `.zh.md`.

- **MCP connect-time preflight: fast-fail when a remote `url` is not an MCP
  endpoint.** A misconfigured `url` in `mcp.json` pointing at a plain web page
  (website, login portal, wrong path) used to stall `connect_all()` for the
  full SSE `timeout` (default 60 s) before failing opaquely. `_connect_sse`
  now runs a cheap 5 s HEAD probe (GET fallback on 405/501) and rejects a 2xx
  response advertising a definite non-MCP content type with an actionable
  `NonMcpEndpointError`. Detection is allow-list based (`application/json` /
  `text/event-stream`) and strictly best-effort: missing/empty content type,
  non-2xx status, or any transport error passes through — the real handshake
  stays authoritative. (#71)

- **ACP server startup resume: `agentao --acp --resume [SESSION_ID]`.** The
  ACP server can reattach to a persisted session: a one-shot
  `ResumeDirective` is consumed by the first `session/new`, which hydrates
  and replays the saved history (reusing the `session/load` loader) and
  returns the persisted `sessionId`; later `session/new` calls behave
  normally. The fallback is permissive — empty store, unknown id,
  corrupt/unreadable file, or an already-active id all degrade to a fresh
  session (logged at WARNING) rather than failing the client's first
  `session/new`. Hosts get the same seam via
  `AcpServer(resume_directive=...)`. (#76)

### Changed

- **Split six oversized modules into focused, cohesive units (internal,
  behavior-preserving).** No public API, runtime behavior, or import path
  changed — method/class bodies moved verbatim and every historically
  imported name is re-exported from its original module. (#63)
  - `agent.py`: the ~246-line `Agentao.__init__` is now a short sequence of
    ordered phase helpers (`_validate_construction_args`, `_init_mcp_sources`,
    `_init_session_state`, `_init_replay`, `_wire_tooling`).
  - `cli/diagnostics_cli.py` (862 lines) → a `cli/diagnostics/` package
    (`models` / `loaders` / `collectors` / `render` / `commands`); the old
    module is now a thin re-export shim.
  - `acp/transport.py` (950 lines) → `ACPTransport(_ReplayMixin,
    _InteractionMixin)` plus a shared `_transport_helpers` module.
  - `llm/client.py` (890 → 688 lines): request/response logging moved to a
    `_LoggingMixin` (`agentao/llm/_logging.py`).
  - `acp_client/client.py` (937 → 779 lines): the exception hierarchy moved to
    `acp_client/errors.py`.
  - `plugins/hooks/_dispatcher.py` (756 → 542 lines): the structured-stdout
    parsers moved to a `_OutputParsingMixin` (`_output_parsing.py`).

- **Vision degradation rewrites rejected image turns as `<attachment>` tags.**
  When a model rejects image input, the retry now rewrites the user turn with
  one self-closing `<attachment uri="..." mimetype="..."/>` tag per image
  appended at the end of the message (uri from the image's `_source`, else
  `inline-image-N`; attribute-escaped) instead of the previous bracketed
  prose + bullet list. Canonical format documented as dev-guide appendix A.1
  "Image input and vision degradation" (EN+ZH); the engine enforces no
  size/count caps — hosts must. (#84)

- **docs/ reorganized into an audience-oriented layout.** 11 subdirs + 14
  loose top-level files restructured into seven purpose dirs — `start/`,
  `guides/`, `reference/`, `design/`, `releases/`, `migration/`, `history/` —
  with filenames normalized to kebab-case. The code-coupled `schema/` dir is
  intentionally unchanged (tests read it by hardcoded path). (#80)

### Fixed

- **De-flake the ACP-client nonblocking-serialization subprocess test.**
  `test_prompt_once_server_busy_during_nonblocking_turn`'s final post-cancel
  `prompt_once` used a 5 s budget; under CI contention on the Python 3.12
  runner the real subprocess round-trip occasionally exceeded it
  (`AcpClientError: timeout waiting for session/prompt response`). Bumped to
  the file's existing 10 s "slow round-trip" budget. Test-only; no runtime
  change.

- **Subprocess timeouts now kill the whole process tree (search, plugin
  hooks, shell).** A bare `subprocess.run(timeout=)` signals only the direct
  child, so a grandchild holding the captured pipe (Windows `git`
  credential helpers, a user hook backgrounding a process) hung
  `communicate()` past the timeout — five parallel hangs saturated the tool
  pool and wedged ACP-stdio turns until the client dropped the connection.
  New shared `capabilities/process.run_captured()`: own process
  group/session, explicit stdin handling (`input=` over a pipe, else
  `DEVNULL` so a child can never read the JSON-RPC channel),
  `kill_process_tree()` on timeout (`taskkill /T` on Windows, `killpg(pid)`
  elsewhere — never `getpgid`, which races a zombie child), and
  `errors="replace"` decoding. `search_file_content` and the plugin hook
  dispatcher route through it; `LocalShellExecutor.run` keeps its streaming
  loop but shares `kill_process_tree`. (#73, #74, #75)

- **ACP sessions persist on server shutdown.** When an ACP client stopped an
  agentao server subprocess, chat history was dropped (unlike the CLI, which
  saves on session end). `AcpSessionState.close()` now persists the
  conversation keyed by the ACP `sessionId` (so `session/load` can resume
  it) before cancelling the turn token, and the bundled `acp_client` closes
  the server's stdin first so its read loop reaches the save path before
  SIGTERM/SIGKILL escalation. Shared `persist_agent_session()` helper keeps
  the CLI and ACP teardown paths in one place. (#78)

- **Sub-agent construction no longer crashes on `working_directory`.**
  `AgentToolWrapper._run_sync` built the sub-`Agentao(...)` without
  `working_directory` — required since 0.3.0 — so every sub-agent invocation
  raised `TypeError`. The parent's project root is now threaded through
  (`create_agent_tools` passes `working_directory=self.project_root`), with a
  regression test on the construction kwarg. (#80)

- **Session resume no longer rebinds the persisted model.** A session stores
  only the model *name*, never its provider, so re-applying it onto the
  current process's provider could yield an inconsistent `(provider, model)`
  pair that fails on the next LLM call. Both resume paths (CLI
  `/sessions resume`, ACP `session/load` / startup resume) now keep the
  process-default model; the saved name is still persisted and shown for
  reference. (#81)

- **SKILL.md relative paths resolve against the skill directory.** Skill
  instructions referencing `scripts/foo.py` failed whenever cwd ≠ skill
  directory — the common case, since skills install under
  `~/.agentao/skills/` or `<project>/.agentao/skills/`. Fixed at the
  activation layer so every skill (including third-party) self-heals:
  `activate_skill` and the per-turn skills context now report
  `Skill directory: <abs path>` plus the resolution rule, `scripts/` is
  enumerated alongside `references/`/`assets/` (hidden files and
  `__pycache__` skipped, listings capped with an explicit truncation
  marker), plugin-command entries are exempted (a shared `commands/` folder
  is not a skill directory), and skill paths are resolved to absolute at
  load. The bundled `ocr` example carries PEP 723 inline metadata so
  `uv run` works from any cwd. (#83, #85)

---

## [0.4.8] — 2026-05-30

### Changed

- **ACP `session/set_mode` uses the standard `modeId` field and accepts
  unknown values.** The ACP-standard field is `modeId` (not the pre-existing
  non-standard `mode`), and a `modeId` is a UI/behavioural selector that need
  not be an Agentao permission preset. The handler now reads `modeId`, applies
  a `PermissionEngine` preset **only** on an exact match (`read-only` /
  `workspace-write` / `full-access` / `plan`), and **persists + echoes** any
  other value without changing permission posture — so a client mode like
  DeepChat's `code` / `ask` round-trips instead of being rejected with
  `-32602`. A non-preset `modeId` needs no permission engine; a recognized
  preset still does. `AcpSessionSetModeRequest.modeId` /
  `AcpSessionSetModeResponse.modeId` are now open strings (snapshot
  `docs/schema/host.acp.v1.json` bumped). The permission-axis split and
  `availableModes` / `currentModeId` + `current_mode_update` remain deferred
  to their own design.
- **ACP `initialize` advertises extensions through `_meta`, not a top-level
  `extensions` array.** The ACP-standard `initialize` response carries only
  `protocolVersion` / `agentCapabilities` / `agentInfo` / `authMethods`;
  extension data belongs under `_meta`. Agentao now returns its
  `_agentao.cn/ask_user` advertisement under
  `_meta["_agentao.cn/extensions"]` (vendor-namespaced so it never collides
  with another extension's `_meta` payload) instead of the non-standard
  top-level `extensions: [...]`. `AcpInitializeResponse` drops the
  `extensions` field for an open `_meta` object (`AcpInitializeMeta`); the
  schema snapshot (`docs/schema/host.acp.v1.json`) is bumped. Agentao's own
  `acp_client` never read `extensions`, so no client code changes; a
  schema-following host that still sends a top-level `extensions` is now
  rejected (`extra="forbid"`).

### Fixed

- **Silence jieba's `SyntaxWarning` noise on first Chinese-text recall.**
  jieba 0.42.1 (its latest and last PyPI release) uses non-raw regex
  literals that Python 3.12 flags as `SyntaxWarning: invalid escape
  sequence` when its modules first compile — surfacing in the terminal the
  first time CJK memory retrieval imports it. Routed every jieba import in
  `memory/retriever.py` through a single `_import_jieba()` helper that mutes
  `SyntaxWarning` at the import chokepoint (jieba pulls in `finalseg` at
  module top, so one wrap covers all three warnings). No behavior change;
  other warnings are untouched.
- **Robust home-directory resolution when `$HOME` is unset.** Added
  `agentao.paths.user_home()` — `Path.home()` with a fallback to
  `$HOME` / `USERPROFILE` and finally the system temp dir, so an
  environment that can't resolve a home directory (stripped service
  accounts, some container/CI sandboxes, headless ACP launches) no longer
  raises `RuntimeError`. The fallback is a *private, per-user* temp
  subdirectory created `0700` and validated for ownership/permissions
  (a pre-existing world/group-accessible path is abandoned for a fresh
  `mkdtemp`), so a co-tenant on a shared host can't plant config/plugins
  at a predictable path for us to load. `user_root()` now routes through
  it, and the
  scattered direct `Path.home()` call sites (memory dictionary/skills
  paths, the skills registry, plugin discovery, the sandbox config, the
  log-handler fallback, the CLI history file) go through `user_home()` /
  `user_root()` — fixing the import-time crash risk where module-level
  `~/.agentao` constants resolved `Path.home()` at import. Subsystem
  constructors still take explicit roots (unchanged from the Issue 5
  no-implicit-fallback contract); this only hardens the path helpers
  themselves.

### Added

- **ACP model/provider switching that keeps secrets off the wire.** Added the
  ACP-standard `session/set_config_option` handler for `configId="model"`: a
  client switches model/provider by sending a `provider/model` *identifier*
  (e.g. `"openai/gpt-4o"`), and credentials are resolved **server-side**
  through a host-injectable `provider_resolver` — they never travel on the
  ACP wire (nor into `agentao.log`). The value is split on the *first* `/`
  (so `huggingface/meta-llama/Llama-3` → provider `huggingface`, model
  `meta-llama/Llama-3`); a bare value with no `/` is a model-only switch that
  keeps the current provider. Two mechanisms reject any credential-bearing
  field (`apiKey` / `baseUrl` / `_meta`): a handler whitelist (only
  `sessionId`/`configId`/`value` are read) **and** the
  `AcpSessionSetConfigOptionRequest` schema (`extra="forbid"`). The default
  resolver accepts **only** the configured `LLM_PROVIDER` (`{PROVIDER}_API_KEY`
  / `_BASE_URL`); any other provider id → `INVALID_REQUEST`. It never scans
  the environment for a provider list — multi-provider switching requires a
  host-injected resolver paired with a host-injected catalog
  (`AcpServer(provider_resolver=..., model_catalog=...)`). `session/new` and
  `session/load` now advertise the `model` `configOptions` (default catalog is
  the single current `provider/model`); a successful switch returns the
  refreshed `configOptions` in its **response only** — no
  `config_option_update` notification. Also added the vendor
  `_agentao.cn/set_model` (`{sessionId, model}`, free-form, secret-free,
  model-only) for "type any model" UX a `select` can't express; it shares the
  core `agent.set_model()` path. The existing `session/set_model` and
  `session/list_models` are kept unchanged as one-release compatibility
  endpoints. Host ACP schema gains `AcpSessionSetConfigOptionRequest/Response`,
  `AcpConfigOption`, `AcpConfigOptionChoice`, `AcpAgentaoSetModelRequest/Response`,
  and `configOptions` on the `session/new` / `session/load` responses
  (snapshot `docs/schema/host.acp.v1.json` bumped).
- **Multimodal image input through the turn.** `chat()` / `arun()` accept
  an optional `images=[{"data": <base64>, "mimeType": <type>}, ...]`. When
  present, the user turn is emitted as an OpenAI-style multimodal content
  list (a `text` part plus one `image_url` part per image with an inline
  `data:` URL) instead of a plain string; text-only turns are unchanged.
  The LLM request logger summarizes multimodal parts (`text (N chars)`,
  `image_url (N chars, inline base64)`) rather than dumping the raw base64
  blob into `agentao.log`. Image data arrives as standard content blocks,
  so this is decoupled from any specific ACP client.
- **ACP image input.** `session/prompt` now accepts ACP `image` content
  blocks (`{"type":"image","data":<base64>,"mimeType":...}`), rendering
  them into the multimodal turn via `agent.chat(images=...)`; text and
  `resource_link` blocks are unchanged and `audio`/embedded `resource`
  still raise `INVALID_PARAMS`. The `initialize` handshake now advertises
  `promptCapabilities.image: true`, and the host ACP schema gains
  `AcpImageContentBlock` (snapshot `docs/schema/host.acp.v1.json` bumped).
  The untrusted payload is validated at the boundary: `mimeType` must be
  `image/*`, `data` must be valid base64 within a 20 MB per-image cap, and
  a prompt may carry at most 16 images — each violation is a clean
  `-32602 INVALID_PARAMS`. The CLI `/image` command enforces the same
  size/count caps, and `/clear` · `/new` now drop any staged images so
  they cannot leak into the next session. `/image` re-validates the bytes
  it actually read (not just the earlier `stat()`), closing a TOCTOU gap
  where a file truncated or grown between the size check and the read could
  stage an empty or oversized block. The image wire carries only inline
  `{data, mimeType}`: any other key (`uri`, `path`, `apiKey`, `baseUrl`,
  `_meta`, …) is rejected both at the schema layer (`extra="forbid"`) and in
  the runtime `_parse_prompt` parser (which works on raw dicts and so needs
  its own allowlist), so the handler can never be coaxed into dereferencing a
  host path or smuggling a secret.
  Saving a session whose first user message is image+text now derives its
  title from the text part instead of persisting an empty title.
- **Structured `ask_user`.** The `ask_user` tool now accepts optional
  `header` / `options` / `multiple` / `allow_custom` hints alongside the
  free-form `question`, so the model can offer a choice list while still
  letting the user type a custom answer. The hints flow through the
  `Transport.ask_user` contract to every transport: the CLI renders a
  numbered menu and accepts a number, comma-separated numbers (when
  `multiple`), or custom text — re-prompting when `allow_custom` is false
  and the entry isn't one of the options; the ACP transport forwards them on
  `_agentao.cn/ask_user` (host-agnostic plain-string options, not
  option-cards) and the host ACP schema's `AcpAskUserParams` gains the
  matching fields (snapshot `docs/schema/host.acp.v1.json` bumped); the
  replay recorder captures them. The reply stays a single string (a client
  joins `multiple` selections itself). Backward-compatible: a plain
  `ask_user(question)` keeps its original wire/recording shape, and legacy
  1-arg `Callable[[str], str]` callbacks (the deprecated `ask_user_callback`
  constructor arg, or an `SdkTransport(ask_user=...)` callback) keep working
  — the structured kwargs are forwarded only to callbacks whose signature
  accepts them, dropped otherwise.

## [0.4.7] — 2026-05-17

### Added

- **`agentao doctor` and `agentao config validate` — diagnostics CLI** (#45).
  Two new non-interactive subcommands that aggregate or validate the
  harness's existing signals without instantiating an agent. `doctor`
  covers the `.env` provider check (API-key *presence*, never the value),
  `settings.json`, permissions, MCP, replay, ACP schema export, project +
  user memory stores, plugin diagnostics, and optional-dep probes.
  `config validate` is the narrower companion that only checks
  user-editable config (no plugin section). Output contract is
  `{"ok": bool, "sections": {...}, "findings": [...]}`; errors exit `1`,
  warnings keep exit `0`. `--json` is the contract surface for CI/hosts,
  human-readable is the default. Both are **read-only** (probing an
  absent `memory.db` reports `"absent"` instead of bootstrapping it) and
  reject unknown flags with exit `2`. Implementation in
  `agentao/cli/diagnostics_cli.py`; documented under
  `developer-guide/{en,zh}/cli/12-non-interactive.md`; design rationale in
  `docs/design/codex-reverse-review.md` (2026-05-17 follow-up).

- **`collect_full_plugin_diagnostics()` helper** (#45) in
  `agentao/embedding/plugins/diagnostics.py`. Shared by
  `agentao plugin list` and `agentao doctor` so the two commands cannot
  drift on which plugins they consider failed (it runs the post-load
  `resolve_plugin_entries` / `resolve_plugin_agents` simulation in
  addition to `PluginManager.load_plugins`).

- **`PreToolUse` plugin hooks are now decision-capable** (#39). A hook
  returning `hookSpecificOutput.permissionDecision: "deny"` cancels the tool
  call; `"ask"` flips the plan to the existing confirmation path; `"allow"`
  is a no-op. First `deny` wins, then first `ask`; hook decisions cannot
  override an engine `deny`/`ask`. The dispatch was moved to Phase 1.5 of
  `ToolRunner` so the new `PermissionDecisionEvent` precedes any tool
  `started` event (the ordering contract holds without an after-the-fact
  `cancelled`). A `PLUGIN_HOOK_FIRED` replay event with
  `hook_name: "PreToolUse"` is emitted for parity with the other hook sites.
  MVP supports only the JSON `hookSpecificOutput.permissionDecision` shape;
  exit-code-2 "block" parity, `additionalContext` injection into the model
  prompt, and `updatedInput` rewriting stay out of scope. Full design in
  `docs/design/codex-reverse-review.md`; tests in
  `tests/test_hooks_pre_tool_use_decision.py`.

- **Model latency / TTFT / per-turn tool count telemetry** (#41). Optional
  fields on existing transport/replay events — *no new event types, no
  public host-schema bump*:
  - `LLM_CALL_COMPLETED` now carries `model_latency_ms` (a stable
    intent-named alias of the existing `duration_ms`) and `first_token_ms`
    (TTFT — the monotonic timestamp of the first streamed text chunk
    minus call start; `None` for tool-only responses or failures before
    the first delta). Both the ok and error emit paths include them.
  - `TURN_END` carries `tool_count` (the per-turn count bumped after each
    tool batch in the chat loop); the replay adapter mirrors it onto the
    `TURN_COMPLETED` replay record.
  - Compaction duration was already covered: `CONTEXT_COMPRESSED` has long
    carried `duration_ms` and is now documented as the stable
    compaction-duration field.
  Hosts subscribe via the transport event stream (same path cost/usage
  tracking already uses). Not re-exposed on `agentao.host` Pydantic models
  yet — the host contract does not currently project LLM-call events, and
  adding that surface is a larger decision than this increment.

- **`/compact` manual-compaction command** (#38, #40). Runs full history
  compaction (`compress_messages(is_auto=False)`) on demand, without waiting
  for the auto-compaction threshold. Handler in
  `agentao/cli/commands/compact.py`; documented in `CLAUDE.md` and in
  `developer-guide/{en,zh}/cli/7-context-status.md`.

### Fixed

- **Empty final assistant content is no longer pushed into history** (#42).
  The chat loop used to append an empty assistant message when the model
  produced only a tool call and no text on the final iteration, leaving a
  malformed history entry that some providers reject on the next turn.

### Changed

- **`web_fetch` no longer silently falls back to crawl4ai.** The previous
  behavior auto-routed JS-rendered or failing fetches through a local
  headless browser whenever `crawl4ai` was installed — meaning a `[full]`
  install changed runtime behavior just by being present. The new model is
  opt-in via the `AGENTAO_WEB_FETCH_FALLBACK` env var:
  - `none` (default) — direct httpx fetch only; on a JS-detected page the
    static shell is returned with a `Note:` line telling the LLM the content
    is likely incomplete.
  - `jina` — falls back to [Jina Reader](https://r.jina.ai); the URL is sent
    to a third-party service, disclosed in the tool description and in a
    `Fallback: jina reader (https://r.jina.ai/<url>)` line on the result so
    the audit surface matches the actual outbound request.
  - `crawl4ai` — falls back to the local headless browser (requires
    `pip install agentao[crawl4ai]` and `playwright install chromium`).
  Rationale: the confirmation prompt and host-visible URL must match the
  actual outbound destination — silently proxying user-supplied URLs through
  a third party breaks the audit contract embedded hosts depend on.

### Added

- **`AGENTAO_WEB_FETCH_FALLBACK`** env var (values: `none` | `jina` |
  `crawl4ai`). Read once at `WebFetchTool` construction; invalid values log
  a warning and degrade to `none`.
- **`JINA_API_KEY`** env var (optional). When set, the Jina Reader fallback
  sends `Authorization: Bearer <key>` for higher rate limits.

### Internal

- `crawl4ai` import moved from module top to inside `_fetch_with_crawl4ai()`
  so `[full]` users no longer pay the Playwright/Chromium import cost on
  `web.py` import.

## [0.4.6] — 2026-05-08

A non-interactive automation release on top of 0.4.5. The new
`agentao run` subcommand is the headline; `agentao -p` is
reimplemented as a thin shim so both surfaces share one structured
result envelope and one exit-code table. **No public Python API or
wire-format change**, but `-p` callers that script on exit codes
must read the migration note below — `max_iterations` moved from
exit `2` to exit `4`, and exit `2` now means "invalid usage / spec
validation failed". Everything else upgrades in place via
`pip install -U agentao`.

### Added

- **`agentao run` subcommand (M0).** Structured automation surface:
  YAML/JSON spec on stdin or `--spec FILE`, merged with explicit
  CLI overrides, executed as one Agentao turn, emitting either the
  final assistant text (`--format text`) or one machine-readable
  envelope (`--format json`). The spec contract (`RunSpec` /
  `RunPermissionRule(s)` / `RunOutputOptions`) and the result
  envelope (`RunResult` / `RunErrorEnvelope` / `RunUsage`) are
  Pydantic models with `extra="forbid"`, so unknown spec fields
  fail loudly (exit `2`). `--spec` and piped stdin are mutually
  exclusive. Secrets (`api_key`) are never accepted in the spec —
  they stay in the environment or in a host-injected client.
  Inline `--prompt` is supported for ad-hoc callers who want
  structured output without a YAML file. Full M0 design captured in
  `docs/implementation/NON_INTERACTIVE_RUN_PLAN.{md,zh.md}` and
  documented for users in
  `developer-guide/{en,zh}/cli/12-non-interactive.md`.

- **Unified exit-code table for `agentao run` and `agentao -p`.**
  Both surfaces now exit `0` on success, `1` on runtime error, `2`
  on invalid usage / spec validation failure / unknown spec field,
  `3` on permission / interaction required (no interactive
  approval), `4` on max iterations, and `130` on SIGINT / SIGTERM.
  Implemented in `agentao/cli/run.py:_classify_outcome`.

- **Non-interactive transport (`agentao.transport.non_interactive`).**
  Records permission rejections, max-iteration hits, and
  interaction-required prompts as `RunErrorEnvelope` shapes the run
  pipeline can read off without touching `Agentao` internals.
  Composes `EventBroadcaster`, so the run pipeline can subscribe to
  `TOOL_COMPLETE` for the `tool_calls` counter.

- **`Agentao.add_event_observer` / `Agentao.remove_event_observer`.**
  Sync pass-throughs to `EventStream.add_observer` for consumers
  (notably the `agentao run` pipeline) that cannot drive the async
  `events()` iterator. The callback fires inline on the producer
  thread; `EventStream` swallows raised exceptions.

- **`EventStream._has_listeners()`.** Returns `True` when an async
  subscriber **or** a sync observer is attached. The narrower
  `_has_subscribers()` is kept for callers that care specifically
  about async-subscriber state.

- **§7.7 Multi-Agent Kanban Scheduling blueprint.** New cookbook
  chapter in Part 7 of the developer guide that anchors on the
  external derivative project
  [`jin-bo/agentao-kanban`](https://github.com/jin-bo/agentao-kanban).
  This is the first Part 7 blueprint that addresses the
  "many specialized agents as a system" shape rather than
  "embed one Agentao instance into a product"; it fills the gap
  that 7.1–7.6 leave open.

- **Plugin runtime/loader import-boundary contract test
  (`tests/test_plugin_boundary_contract.py`).** Imports
  `agentao.plugins` in a fresh subprocess and asserts that none of
  `agentao.embedding.plugins.{manager, manifest, diagnostics, mcp,
  resolvers}.*` and no YAML parser is pulled in transitively. Turns
  the runtime/loader split that landed in 5a/5b into an executable
  invariant rather than a convention. Documented under "Import map
  after 5a/5b" in `docs/design/core-boundary-review.{md,zh.md}`.

### Changed

- **`agentao -p` is now a thin shim** over
  `agentao run --format text --prompt …` (`run_print_mode` in
  `agentao/cli/entrypoints.py`). The success path
  (`prompt → final_text → exit 0`) and runtime-error path
  (exit `1`) are unchanged. The behavior delta is the exit code
  mapping listed above and the appearance of exit `3` for
  permission / interaction requirements that previously never
  surfaced from `-p` (it had no permission rejection path). Tests
  covering the delta are in `tests/test_run_subcommand.py`.

- **Spec-side permission rules layered on top of user rules.**
  `PermissionEngine` now accepts spec-injected `allow` / `deny`
  rules from `agentao run`'s spec without disturbing the existing
  project + user precedence. Action injection is isolated in
  `RunPermissionRule.to_engine_dict` so `extra="forbid"` can flatly
  reject hand-written `action:` fields in YAML.

- **Logger / `agentao.log` silencing knobs documented across the
  embedding entrypoints.** New canonical anchor at `docs/guides/embedding.md
  §2 → "Optional: silencing or redirecting agentao.log"` with a knob
  matrix and `Agentao(...)` / `LLMClient(...)` recipes; the rest of
  the doc set
  (`docs/guides/logging.md`, `developer-guide/{en,zh}/part-2/2-constructor-reference.md`,
  `developer-guide/{en,zh}/part-6/6-observability.md`) crosslinks
  to it. Pure documentation; the
  `LLMClient.__init__` short-circuit on `logger=` and the
  `log_file=None` knob both already exist.

### Deferred

Carried over from 0.4.5 unless noted:

- `--format jsonl` live event stream + a new `RunLifecycleEvent`
  type. Tracked in `NON_INTERACTIVE_RUN_PLAN.md` Post-MVP.
- Spec `attachments:` / `provider:` (multi-provider env-var prefix
  selection) / per-run `plugins:` fields. Same tracker.
- SIGINT-precise JSONL termination (M0 ships best-effort signal
  routing through `CancellationToken`).
- Session resume from `agentao run`.
- A checked-in JSON Schema snapshot for `RunSpec` / `RunResult`.
- `agentao.harness` deprecated alias removal — still scheduled for
  0.5.0.
- The eight legacy `Agentao(...)` callback kwargs — signature
  surgery scheduled for 0.5.0; they continue to emit a single
  `DeprecationWarning` per construction.
- `agentao/session.py` shim removal + `Path.cwd()` fallback removal
  — scheduled for 0.5.0.
- `PermissionEngine` legacy auto-load path tightening into a hard
  error.
- `bashlex`-based supersedence of the workspace-write
  sensitive-write preset's regex tier. Carried over from 0.4.3.
- PreCompact gate, `http`-type Stop hooks, plugin-hook events in
  the host public model, hook attachment pipeline. All carried
  over from 0.4.4.
- `docs/releases/v0.4.0.md` and `v0.4.1.md` backfill.

### Migration

- **`-p` callers that script on exit codes:** the only mapping that
  changed is `max_iterations`; if your CI checks
  `[ $? -eq 2 ]` to detect "answer may be incomplete", change it to
  `[ $? -eq 4 ]`. Treat `2` as "invalid usage / spec error" going
  forward. New exits `3` (permission / interaction) and `130`
  (SIGINT) are additive — they never appeared from `-p` before.
- **Hosts that build `PermissionEngine` from a spec-shaped object**
  can now pass `RunPermissionRule.to_engine_dict("allow"|"deny")`
  to get an engine-ready dict, or use the existing
  `PermissionEngine(rules=...)` ctor kwarg directly.
- **Hosts that don't want `agentao` to mutate their root logger**
  should pass `logger=` to `Agentao(...)` (or to `LLMClient` if
  building it directly). That single switch also silences the
  default `<wd>/agentao.log` file. See `docs/guides/embedding.md §2`.
## [0.4.5] — 2026-05-07

A core-boundary review release. Architectural cleanup of the embedded-host
boundary — replay state externalized from `Agentao`, persistent-session
module relocated, `PermissionEngine` file I/O extracted, plugin loader
relocated, and the legacy `Agentao(...)` callback surface formally
deprecated. **No breaking changes; no public API or wire-format change.**
`pip install -U agentao` upgrades in place from any 0.4.x release.

### Added

- **`Transport.subscribe(listener)`** — optional fan-out method on the
  `Transport` Protocol. Returns an idempotent unsubscribe callable;
  notify uses snapshot iteration so subscribing or unsubscribing
  mid-emit is safe; listener exceptions are swallowed and never poison
  the runtime emit path. `NullTransport` and `SdkTransport` provide it
  by composing `agentao.transport.EventBroadcaster` (also re-exported
  from `agentao.transport`) so from-scratch transports (ACP, message
  queues) can opt in the same way. Probe with
  `getattr(transport, "subscribe", None)` since bespoke implementations
  may omit it.

- **`TURN_BEGIN` / `TURN_END` event types** — fire **once per
  user-driven turn**, distinct from `TURN_START` (which fires once per
  LLM iteration inside the turn). `TURN_BEGIN` carries the user
  message; `TURN_END` carries final assistant text + `status` (`ok` /
  `error` / `cancelled`) + `error`. Replay recorders subscribe to these
  via `Transport.subscribe()` instead of being reached through agent
  state, removing the runtime-to-replay-adapter direct call path.

- **`agentao.embedding.permission_loader`** — new module hosting the
  on-disk loading of `permissions.json` (project + user scope, JSON
  parsing, env-var expansion). The `PermissionEngine` constructor now
  accepts `rules=` / `loaded_sources=` kwargs to skip disk reads
  entirely — relevant for embedded hosts that build rule sets
  programmatically. The legacy auto-load constructor path
  (`PermissionEngine(project_root=...)` without explicit `rules=`) is
  preserved via lazy delegation to the loader and is **not** deprecated
  in this release.

- **`agentao.embedding.sessions`** — new module hosting
  `save_session` / `load_session` / `list_sessions` /
  `delete_session` / `delete_all_sessions` and their helpers.
  `agentao/session.py` becomes a deprecation shim that wraps the new
  module with the old permissive signature
  (`project_root: Optional[Path] = None`, falling back to
  `Path.cwd()`); the new module's API will require `project_root`
  explicitly once the shim is removed in 0.5.0.

- **`agentao.embedding.plugins/*`** — plugin loader (`manager`,
  `manifest`, `diagnostics`, `mcp`, `resolvers/{skills,agents}`)
  relocated from `agentao/plugins/` to `agentao/embedding/plugins/`.
  `agentao/plugins/` is now runtime-only (validators + LLM-facing
  surfaces); the boundary between "what core needs at runtime" and
  "what the embedding layer needs to discover from disk" matches the
  rest of `agentao.embedding`.

- **`agentao.acp.schema_export`** — host-facing
  `export_host_acp_json_schema()` now lazy-delegates here so
  `agentao.host.schema` no longer eagerly imports `agentao.acp` at
  import time. Function signature, return type, and the snapshot at
  `docs/schema/host.acp.v1.json` are unchanged.

### Changed

- **Replay state externalized into `ReplayManager`.** The `Agentao`
  facade's replay surface (`replay_config` constructor kwarg + 4
  instance attributes + 6 facade methods + `close()` teardown leg) is
  consolidated behind `agentao.replay.manager.ReplayManager`. The
  recorder is now wired by `agentao.embedding.factory` as a
  `Transport.subscribe()` listener, so `chat_loop` no longer reaches
  into `agent._emit_*(...)` and `runtime/turn.py` /
  `runtime/llm_call.py` no longer read agent attributes directly. Six
  deprecated facade methods (`replay_*` etc.) and the
  `replay_config=` kwarg remain as back-compat shims; **scheduled
  removal in 0.5.0**. Touched: `agentao/agent.py`,
  `agentao/transport/{base,broadcast,events,null,sdk}.py`,
  `agentao/replay/{adapter,lifecycle,manager}.py`,
  `agentao/runtime/{turn,llm_call}.py`, `agentao/embedding/factory.py`,
  `agentao/acp/transport.py`.

- **Eight `Agentao(...)` legacy callback kwargs now emit a single
  `DeprecationWarning` per construction.** `confirmation_callback`,
  `step_callback`, `thinking_callback`, `ask_user_callback`,
  `output_callback`, `tool_complete_callback`, `llm_text_callback`,
  `on_max_iterations_callback` — passing any of them surfaces one
  warning that names all eight and points at
  `agentao.embedding.compat.build_compat_transport` as the documented
  migration path. Mixing `transport=` with legacy callbacks (which
  silently ignored the callbacks) now also emits a warning so the
  dead kwargs surface in test runs. Hosts that already pass
  `transport=SdkTransport(...)` or build a compat transport directly
  bypass the warning entirely. The kwargs themselves remain accepted;
  **scheduled removal in 0.5.0**.

- **Plugin validators split from resolvers.** `agentao/plugins/skills.py`
  and `agentao/plugins/agents.py` are now validators-only (runtime
  shape checks, LLM-facing surfaces); resolution (front-matter parsing,
  manifest reading, file discovery) moved to
  `agentao/plugins/resolvers/{skills,agents}.py`. Prerequisite for the
  loader relocation under `agentao/embedding/plugins/`.

- **Persistent-session module path migration.** Production import sites
  in `cli/{commands,session,replay_commands}.py` and
  `acp/session_load.py` now import from `agentao.embedding.sessions`
  and pass `project_root` explicitly. The legacy import path
  (`agentao.session.*`) keeps working through the wrapper shim;
  external test files migrate at 0.5.0 alongside the shim removal.

### Documentation

- **Developer guide — full CLI section ported.** New `cli/` subtree
  (12 chapters in `developer-guide/{en,zh}/cli/`) covering install,
  config, slash commands, sessions, replay, plugins, and the embedding
  cross-references. `developer-guide/index.md` hoisted to top; README
  slimmed to ~210 lines.

- **Developer guide §5.7 Plugin Hooks** — rule-author guide for the
  plugin-hook system (`UserPromptSubmit` / `SessionStart` /
  `SessionEnd` / `PreToolUse` / `PostToolUse` / `PostToolUseFailure` /
  `Stop` / `PreCompact`).

- **Developer guide §4.1 Transport Protocol** (en + zh) — full
  `Transport.subscribe()` section with semantics, probe-before-call
  recipe, and an `EventBroadcaster` composition example for from-scratch
  transports.

- **Developer guide §4.2 AgentEvent Reference** (en + zh) — event-group
  tree updated to wrap `TURN_START` inside the new `TURN_BEGIN` /
  `TURN_END` outer pair; per-event detail blocks added for the two new
  types with the per-turn-vs-per-iteration semantics call-out.

- **Developer guide §2.2 Constructor Reference** (en + zh) and
  **Appendix A API Reference** (en + zh) — legacy-callback collapsible
  relabeled "removed in 0.5.0"; new version-note entries for the
  0.4.x `DeprecationWarning` emission and the 0.5.0 planned
  signature surgery; `build_compat_transport` doc expanded to mark
  `agentao.embedding.compat` as the documented migration surface.

- **`docs/design/core-boundary-review.{md,zh.md}`** — full audit
  doc with verified reverse-import maps, codex baseline comparison,
  per-PR commit-hash backfills, and the priority-table execution log.
  This release ships PRs #1–#5b plus the #6 acp/ wheel-split boundary
  prep; #7 (`agentao.harness/` alias removal) remains scheduled for
  0.5.0.

### 0.5.0 runway (no action required for 0.4.x users)

The following surgeries are scheduled for **0.5.0** and deliberately
**not** shipped here:

- **Eight legacy callback kwargs** removed from the `Agentao(...)`
  signature. Migrate via `transport=SdkTransport(...)` or
  `agentao.embedding.compat.build_compat_transport(...)`.
- **`agentao/session.py` shim** removed; callers migrate to
  `agentao.embedding.sessions` with explicit `project_root=`. The
  shim's `Path.cwd()` fallback is removed at the same time.
- **`agentao.harness` alias** removed (carried over from 0.4.x —
  use `agentao.host` instead). One `DeprecationWarning` emitted on
  first import in 0.4.x.
- **Six `Agentao.replay_*` facade methods** plus the
  `replay_config=` constructor kwarg removed; embedded hosts that need
  the recorder wire it through `agentao.embedding.factory` (already
  the default since 0.4.5).

## [0.4.4] — 2026-05-06

A Claude-Code compatibility + tool-hardening release. **No breaking
changes; no public API or wire-format change.** `pip install -U agentao`
upgrades in place from any 0.4.x release.

### Added

- **`Stop` and `PreCompact` lifecycle hooks** — two new plugin-hook
  events alongside the six existing lifecycle events
  (`UserPromptSubmit`, `SessionStart`, `SessionEnd`, `PreToolUse`,
  `PostToolUse`, `PostToolUseFailure`). Wire shape is Claude Code's
  flat snake_case top-level payload — a hook script written against
  Claude Code's documented Stop / PreCompact stdin shape runs
  unchanged. Stop dispatches at three turn-end sites (`final_response`
  / `max_iterations` / `doom_loop`); PreCompact dispatches at four
  compaction sites (`microcompact`, `full` from `compression_threshold`
  or `api_overflow`, `minimal_history`). `select_matching_rules`
  filters `event + is_supported + _matches` and gates the
  `PLUGIN_HOOK_FIRED` emit so zero-match dispatch produces no event.
  Per-event hook-type allowlist (`SUPPORTED_HOOK_TYPES_BY_EVENT`)
  rejects `prompt`-type rules under Stop / PreCompact at parse time.
  Design rationale in
  `docs/implementation/STOP_PRECOMPACT_HOOKS_PLAN.{md,zh.md}`.

- **Stop control-aware gate** — Stop hooks honor Claude Code's full
  Stop output schema: exit code 2 (block + stderr-as-reason), JSON
  `decision: "block"` + `reason`, plus the documented common output
  fields (`continue`, `stopReason`, `suppressOutput`, `systemMessage`,
  `hookSpecificOutput.additionalContext`). The chat loop is wired at
  three exit sites (natural turn / max-iter / doom-loop) with a
  per-`chat()` re-entry cap to prevent infinite `force_continue`
  loops. `_dispatch_stop` returns a `StopHookResult` and a separate
  `_emit_stop_hook_fired` emits `PLUGIN_HOOK_FIRED` with the
  branch-specific outcome label. Schema additions on the Stop emit:
  `outcome ∈ {"allow", "block", "continue", "continue_at_max_iter",
  "reentry_capped"}`, `turn_end_reason` (discriminator for `continue`
  emits across the three exit sites), `added_context_count`,
  `suppress_output`. PreCompact stays observe-only; PreCompact gate
  is intentionally out of scope (see "Out of scope" below).

- **Edit tool — unicode-fuzzy tier-3 match** — `EditTool` gains a
  third match tier that maps typographic codepoints (smart quotes
  `“ ” ‘ ’`, em-/en-dash `—`/`–`, NBSP, ideographic space, …) to
  ASCII before line-window comparison, mirroring `git apply` fuzzy
  behaviour. Tiers 1 (byte-exact) and 2 (whitespace-flex) hit first;
  the shared `_line_window_matches` / `_apply_match` helpers preserve
  CRLF byte offsets and `replace_all` spans every normalized-equivalent
  occurrence. New `tests/test_edit_unicode_fuzzy.py` covers tier-3
  hits, `replace_all` across mixed dash variants, CRLF preservation,
  and tier-precedence (byte-exact and whitespace-flex must hit before
  tier 3).

### Changed

- **`SearchTextTool` argv hardening** — `_git_grep` now passes the
  pattern via `-e <pattern>` (git grep's `--` is the *pathspec*
  separator, not an option terminator); `_ripgrep` places the pattern
  after `--`. A user-supplied pattern beginning with `-` (`--help`,
  `--pre=...`, a leading `-e` payload) can no longer be parsed as a
  flag by the underlying engine. New
  `tests/test_search_argument_injection.py` covers both engines.

- **`SearchTextTool` rg source-level skip pruning** — `_ripgrep` now
  translates the effective `skip` set into `--glob '!<dir>'` flags so
  heavyweight directories (`node_modules`, `build`, `target`, …) are
  excluded by rg itself rather than post-filtered out of its output.
  Matters most in the non-git fallback path where there is no
  `.gitignore` to lean on. Negative globs are appended *after* the
  positive `file_pattern` glob because rg gives later globs precedence
  — a regression test locks in the ordering. `_effective_skip_dirs`
  opt-in semantics are preserved (a caller who explicitly references
  `node_modules` in their query still searches it).

### Fixed

- **Doom-loop turn finalizer no longer double-dispatches Stop.**
  Previously the `doom_triggered` branch dispatched Stop once and then
  `break`'d into the max-iter finalizer, which dispatched Stop a
  second time with the wrong `turn_end_reason`. The branch now
  finalizes the assistant message and returns directly. Pinned by
  `tests/test_hooks_stop_doom_loop_no_double_dispatch.py`.

- **Hook parser non-string trigger no longer raises.** `_regex_match_full`
  guards non-string trigger / value with an `isinstance` check so a
  malformed config degrades to no-match instead of raising
  `TypeError` from `re.fullmatch`.

### Documentation

- **`docs/implementation/STOP_PRECOMPACT_HOOKS_PLAN.{md,zh.md}`** —
  full two-phase plan with Claude Code compatibility matrix, payload
  shape, dispatcher signatures, replay-event projection, and
  `matched_rule_count` no-emit gate semantics.
- **Developer guide `part-4/2-agent-events.md`** (en + zh) — the
  `PLUGIN_HOOK_FIRED` row now lists `Stop` / `PreCompact` in the
  `hook_name` enumeration with per-name fields and the five-label
  Stop `outcome` matrix.
- **`docs/design/pi-mono-borrow-review.{md,zh.md}`** — reframed as
  Phase A event surface vs Phase B control-aware gate.
- **`docs/design/pi-mono-tools-review.{md,zh.md}`** — new decision
  record covering the Edit tier-3 match and Search argv hardening
  borrow candidates.

## [0.4.3] — 2026-05-04

A permission-hardening + LLM-resilience release. **No breaking changes;
no public API or wire-format change.** `pip install -U agentao` upgrades
in place from any 0.4.x release.

### Added

- **Hardline shell-safety floor** — a new
  `agentao/permissions_hardline.py` denies unrecoverable shell
  operations (`rm -rf /`, `mkfs`, `dd of=/dev/sda`, fork bombs,
  `shutdown`/`reboot`/`halt`, `init 0|6`, `systemctl poweroff|reboot`)
  *before* any rule — including `full-access` — is evaluated. The pattern
  set handles `sudo`/`env` wrappers (with flags and value args), quoted
  paths (`"$HOME"`, `'/etc/passwd'`), split rm flags (`-r -f`,
  `--recursive --force`), path-qualified `rm` (`/bin/rm`), `;`/`&&`/`||`
  + newline separators, and command substitution. Embedded hosts that
  take policy responsibility themselves can opt out via
  `PermissionEngine(enable_hardline=False)`. Decision results now carry
  a stable `reason` taxonomy (`hardline:*`, `mode-preset:*`,
  `user-rule:*`) that hosts can render in audit UIs without parsing
  free-form text. Design rationale in
  `docs/design/permission-hardening-plan.{md,zh.md}`.

- **Workspace-write sensitive-write preset** — a mode-scoped
  preset rule flags shell writes to `~/.bashrc`, `~/.zshrc`, `~/.netrc`,
  `~/.pgpass`, `~/.npmrc`, `~/.pypirc`, and friends via redirection
  (`>`, `>>`, `2>`), `tee`, `cp`/`mv`, or `sed -i`. The rule emits
  **ASK**, not DENY — installers (Homebrew, pyenv, rustup, nvm) and
  devops scripts legitimately edit these files, so the operator gets a
  prompt rather than a wall. `full-access` deliberately doesn't carry
  the rule (literal full access stays literal).

- **MCP `call_tool` error classification** — replaces "retry once on
  any failure" with explicit buckets (`AUTH`, `SESSION_EXPIRED`,
  `TRANSPORT_DROPPED`, `OTHER`). 401/403 surfaces immediately (no
  reconnect storm); session-expired and transport-dropped reconnect
  and retry once; everything else surfaces without reconnecting.
  `connection refused` is intentionally not classified as transport-
  dropped — the server isn't listening at all. The live transport is
  now disconnected before reconnecting so the old subprocess / SSE
  stream doesn't leak for the manager's lifetime. New
  `agentao.mcp.client.classify_mcp_error` + `McpErrorKind` are public.

- **Explicit LLM HTTP retry policy with progress-aware streaming** —
  `LLMClient` now controls retries end-to-end (the OpenAI SDK's built-
  in retry is disabled with `max_retries=0`):

  | Aspect | Behavior |
  |---|---|
  | Retryable | 429 + 5xx + connection errors |
  | Non-retryable | 4xx other than 429 |
  | Backoff | `Retry-After` if present (seconds or HTTP-date), capped at `MAX_BACKOFF_SECONDS`; otherwise jittered exponential |
  | Wall-clock budget | `MAX_TOTAL_RETRY_SECONDS` so a long `Retry-After` cannot strand the caller |
  | Cancellation | Sleeps run through `_interruptible_sleep`; a cancelled token aborts the loop immediately |
  | Streaming | `chat_stream` retries only **before** any chunk has reached `on_text_chunk` — once text is delivered, mid-stream errors propagate so retrying doesn't replay text the user already saw |

  Raised exceptions carry a `.streamed` flag so hosts (and the
  `LLM_CALL_COMPLETED` error event in `runtime/llm_call.py`) can pick
  between regenerate-from-scratch and resume-style retry without
  counting `LLM_TEXT` events themselves. Guards against the historical
  "upstream" substring matching `502` bodies and silently falling back
  to non-streaming, bypassing the retry policy.

- **Tightened config trust boundary** — three escalation paths closed
  (#25):

  1. `PermissionEngine` no longer loads project-scope
     `.agentao/permissions.json`. A checked-in
     `{"tool": "*", "action": "allow"}` would have defeated the user
     policy because the engine returns on the first matching rule. Only
     `<user_root>/permissions.json` is honored; a stray project file is
     logged once and ignored. Permissions are a user/host concern, not
     a cwd concern.
  2. Project `.agentao/mcp.json` is **add-only** — it may declare new
     server names but cannot override a user-scope entry with the same
     key. Collisions warn and skip. Prevents a checked-in `mcp.json`
     from silently redirecting a known name (e.g. `"github"`) to a
     different transport.
  3. `McpTool` surfaces MCP `readOnlyHint` / `destructiveHint`
     annotations as protocol hints — but only when the server is
     trusted, and only to **add** friction (`destructiveHint=true` →
     confirm anyway, regardless of `readOnlyHint`). Per spec, we never
     make tool-use decisions from annotations on untrusted servers.
     New `McpTool.mcp_annotations` property for host introspection.

- **`SearchTextTool` skip-list + ripgrep fallback** — kills the
  "effectively stuck on large trees" failure mode. Default-skips
  `.git`, `node_modules`, `.venv`/`venv`, `__pycache__`, `.tox`,
  `dist`, `build`, `target`, `.next`/`.nuxt`, and the usual language
  caches. Fallback chain becomes `git grep` → `rg` → Python, so
  non-git trees and Windows boxes with `rg.exe` but no `git` are
  rescued. Caller opts back in by naming a skip-dir in `directory` or
  `file_pattern`. Cross-platform via `shutil.which`; no
  `IS_WINDOWS` branching.

- **`/replay delete <id>` and `/replay delete all`** — mirrors
  `/sessions delete`. Removes specific replay files by id-prefix or
  wipes them all (single-key confirmation). The active recorder's file
  is skipped/refused so an in-flight write isn't orphaned.

- **`agentao.redact.mask_secret`** — canonical helper for hiding
  credentials in logs, audit events, and host UIs. Default shape
  `sk-A...3xZk` for long values, `********` (same length) for short
  values, `(not set)` for missing. Forward-looking: no internal
  callers migrated in this release; replaces the next ad-hoc
  truncation site that needs it.

- **Windows UTF-8 console enforcement** — `agentao/__init__.py`
  now calls `_ensure_utf8()` at package import. On Windows, sets
  `PYTHONIOENCODING=utf-8` (only if unset), switches the console
  code page to CP_UTF8 via `kernel32.SetConsoleOutputCP/SetConsoleCP`,
  and reconfigures `sys.stdin`/`stdout`/`stderr` with
  encoding=utf-8 + lenient error handler. POSIX is a single-instruction
  no-op. Embedded hosts that import Agentao from a Windows console
  no longer hit `UnicodeEncodeError` on CJK file paths, curly quotes
  from skill metadata, or model-output emoji.

### Fixed

- **`ToolExecutor` parallel batches now propagate contextvars** —
  `ThreadPoolExecutor` workers don't inherit the parent thread's
  context, so `contextvars.ContextVar` state set on the orchestration
  thread (host turn IDs, request metadata, structured-logging scopes)
  was lost when tools ran in parallel. Capture
  `contextvars.copy_context()` on the **parent** thread per submission
  and dispatch through `Context.run()` — calling `copy_context()`
  inside the worker would copy the worker's empty context. Each plan
  gets a fresh context (`Context.run()` may be invoked at most once per
  object).

- **MCP `destructiveHint=true` no longer bypasses read-only mode**
  when `readOnlyHint=true` is also set on the same trusted server. The
  conflict resolves security-positive: destructive wins. Caught by
  Codex review during #25's `/simplify` pass.

### Documentation

- **Developer guide §7.6 — WeChat bot blueprint** (mirrored to
  `examples/wechat-bot/`): a long-polling daemon that runs one Agentao
  turn per inbound message, with a `WeChatClient` Protocol that fits
  ilink / wechaty / itchat alike and a contact-scoped permission preset
  (allowlist → `WORKSPACE_WRITE`, otherwise `READ_ONLY`). Up-front
  contrast table draws a hard line between this personal-account path
  and the Official Account / Enterprise WeChat webhook track.
- **Host contract clarifications** — `EventStream.add_observer`
  fan-out documented; new `architecture/embedding-vs-acp.{md,zh.md}`
  decision tree distinguishing in-process embedding vs ACP server vs
  ACP client vs ACP schema surface; internal Transport / `AgentEvent`
  channel documented as a peer surface (`SdkTransport.on_event`) with
  full inventory and stability comparison; new
  `PUBLIC_EVENT_PROMOTION_PLAN.md` staging `MCPLifecycleEvent` and
  `LLMCallEvent` promotion to the stable contract.
- **`examples/protocol-injection/`** — runnable end-to-end sample
  replacing every host→Agentao injection slot with a small adapter
  (in-memory FileSystem, audit-logging ShellExecutor, dict-backed
  MemoryStore, programmatic MCPRegistry). Six smoke tests assert each
  slot is actually consulted; no `OPENAI_API_KEY` required. Wired into
  developer-guide Part 2.2 and Part 4.7.
- **`examples/personas/`** — new home for prompt-configuration
  samples (as opposed to host-integration code). Seeded with
  `daily-driver` (evidence-first daily assistant, citation-format
  table) and `kawaii-buddy` (情绪价值小助手 with mascot board).
- **`examples/skills/`** — host-agnostic skill gallery (#26):
  `zootopia-ppt`, `pro-ppt`, `ocr`, plus bilingual README explaining
  the three `SkillManager` discovery paths and the dual-placement
  story. Co-located skills in `data-workbench`, `ticket-automation`,
  and `batch-scheduler` now carry one-line callouts pointing back to
  the gallery and explaining why they can't be lifted out.
- **`docs/design/permission-hardening-plan.{md,zh.md}` rev 3** —
  status updated to mark PRs 1–5 all landed (PR 1 + PR 2 on
  2026-05-03; PR 3 + PR 4 + PR 5 on 2026-05-04). §10 reframed as
  post-ship follow-ups; only the `bashlex` supersedence of PR 5's
  regex tier remains open.
- **`docs/design/pi-mono-borrow-review.{md,zh.md}`** — decision
  record from surveying ~590 commits in `pi-mono` between v0.66 and
  v0.73. Keep / cut / reframe verdicts for each candidate.

## [0.4.2] — 2026-05-01

### Changed

- **Public package rename: `agentao.harness` → `agentao.host`** (with
  matching renames to public types, schema-export functions, and wire
  schema file names). The host-facing contract package is renamed to
  reflect what it actually is — the surface a host application talks
  to, around the Agentao runtime (the design doc still calls Agentao
  itself the "harness" embedded inside the host). The old names are
  inconsistent under the new package (`from agentao.host import
  HarnessEvent` reads wrong), so the cleanup is bundled.

  Renamed surface:

  | Old | New |
  |---|---|
  | `agentao.harness` (module path) | `agentao.host` |
  | `HarnessEvent` | `HostEvent` |
  | `export_harness_event_json_schema()` | `export_host_event_json_schema()` |
  | `export_harness_acp_json_schema()` | `export_host_acp_json_schema()` |
  | `HarnessReplaySink` | `HostReplaySink` |
  | `harness_event_to_replay_kind()` | `host_event_to_replay_kind()` |
  | `harness_event_to_replay_payload()` | `host_event_to_replay_payload()` |
  | `replay_payload_to_harness_event()` | `replay_payload_to_host_event()` |
  | `docs/schema/harness.events.v1.json` | `docs/schema/host.events.v1.json` |
  | `docs/schema/harness.acp.v1.json` | `docs/schema/host.acp.v1.json` |

  All old names continue to work as deprecated aliases on the
  `agentao.harness` shim package, with one `DeprecationWarning` on
  first import naming the new path. The whole alias surface — package,
  types, functions — is scheduled for removal in 0.5.0.

  Migration is a literal find/replace:

  ```diff
  - from agentao.harness import HarnessEvent, EventStream
  + from agentao.host    import HostEvent,    EventStream

  - from agentao.harness.protocols import FileSystem, ShellExecutor
  + from agentao.host.protocols    import FileSystem, ShellExecutor

  - export_harness_event_json_schema()
  + export_host_event_json_schema()
  ```

  Wire schema bytes are byte-for-byte regenerated from the same
  Pydantic models; the only differences vs. the 0.4.1 snapshots are the
  identifier names (`HostEvent` instead of `HarnessEvent`) and the
  top-level title (`AgentaoHostEvents`). The `v1` lineage is unchanged
  — adding optional fields stays in v1; removing or renaming a field
  still requires a v2 bump.

## [0.4.0] — 2026-05-01

The single break release of the Path A P0 plan
(see `docs/design/path-a-roadmap.md` §3.2). The break is a packaging
change only — no public Python API is renamed, removed, or signature-
changed. The "no-change" upgrade line is `pip install 'agentao[full]'`,
which reproduces the 0.3.x bundled closure exactly (CI-enforced against
a 122-package baseline).

### Breaking changes

- **P0.9 dependency split** — `pip install agentao` now installs only
  the core (7 packages) needed to construct an `Agentao()` instance and
  call `chat()` against an OpenAI-compatible endpoint. CLI, web fetch,
  and Chinese tokenization become opt-in extras.

  | 0.3.x direct dep | 0.4.0 location |
  |---|---|
  | `openai` / `httpx` / `pydantic` / `pyyaml` / `mcp` / `python-dotenv` / `filelock` | core |
  | `rich` / `prompt-toolkit` / `readchar` / `pygments` | `[cli]` |
  | `beautifulsoup4` | `[web]` |
  | `jieba` | `[i18n]` |

  Migration matrix:

  | You are… | Install line |
  |---|---|
  | Embedding host (Python `from agentao import Agentao`) | `pip install agentao` |
  | CLI user (`agentao` console script) | `pip install 'agentao[cli]'` |
  | Want zero behaviour change | `pip install 'agentao[full]'` |

  Closure equivalence is enforced by `tests/test_dependency_split.py`
  against `tests/data/full_extras_baseline.txt` (122 packages frozen
  on 2026-05-01). See `docs/migration/0.3.x-to-0.4.0.md` for the full
  guide.

### Added

- **P0.10 friendly missing-dep error** — running the `agentao` CLI in
  a core-only install (no `[cli]` extra) now exits 2 with a one-line
  actionable message instead of crashing with an opaque
  `ModuleNotFoundError: rich`:

  ```
  agentao CLI requires extra packages (missing: rich).
    pip install 'agentao[cli]'   # CLI surface only
    pip install 'agentao[full]'  # 0.3.x-equivalent closure
  See docs/migration/0.3.x-to-0.4.0.md for details.
  ```

  Implementation: `agentao/cli/__init__.py` defines `entrypoint()`
  inline (no module-level imports of rich / prompt_toolkit / readchar /
  pygments) so the module load itself stays free of CLI deps; every
  `[cli]` dep is preflighted via `importlib.util.find_spec` so a
  partial install (rich present, prompt_toolkit missing) still hits
  the friendly path instead of leaking a "Fatal error" from
  `entrypoints.run_init_wizard`'s broad `except Exception`. All other
  public names in `agentao.cli` lazy-load via PEP 562 `__getattr__`.
  Slow-marked tests in `tests/test_cli_missing_dep_message.py` cover
  the friendly-message path, the post-`[cli]` boot path, the
  no-trace-leak invariant, and the partial-install regression.

- **`docs/migration/0.3.x-to-0.4.0.md`** — full migration guide with
  install matrix, dependency map, common project-shape recipes, and
  a `[full]` fallback for any path the migration may have missed.

### Changed

- **Web tools omitted from the registry without `[web]`** —
  `WebFetchTool` and `WebSearchTool` register only when
  `beautifulsoup4` is importable. In a core install the model never
  sees `web_fetch` / `web_search` in its tool schema (vs. the previous
  behaviour of registering them and failing at execute time with an
  opaque ImportError). Mirrors the existing `bg_store is not None`
  pattern that already conditionally registers the background-agent
  tools.

- **Memory recall degrades gracefully without `[i18n]`** —
  `MemoryRetriever.tokenize()` skips the jieba code path entirely
  when the query has no CJK characters (cheap regex check). On a
  CJK-bearing query in a `[i18n]`-less install, `_cjk_segment()`
  returns an empty set with a one-time warning pointing at
  `pip install 'agentao[i18n]'` instead of silently failing.

- **CI test environment installs `[cli,web,i18n]`** — the existing
  unit-test surface imports `from agentao.cli import AgentaoCLI`,
  the web-fetch tool, and the jieba memory path. The default `Test`
  matrix job now installs those three extras so the suite still
  resolves in the core-split world. The core-only contract is
  independently validated by the smoke job and
  `tests/test_dependency_split.py`.

- **Shared test helper `tests/support/wheel.py`** — `REPO_ROOT`,
  `find_wheel()`, `require_wheel()`, and `make_venv()` centralized
  out of the venv-creation pattern that was duplicated across
  `test_clean_install_smoke.py`, `test_dependency_split.py`, and
  `test_cli_missing_dep_message.py`.

## [0.3.4] — 2026-05-01

Second release executing the **Path A roadmap** (see
`docs/design/path-a-roadmap.md`). Lands the §11 P0.4–P0.8 working set
in five logical commits. Still fully additive — no required code
change to upgrade from 0.3.3 (the only namespace move,
`agentao/display.py` → `agentao/cli/display.py`, had no in-tree
consumers).

### Added

- **P0.4 typing gate** — `agentao.harness` now ships clean under
  `mypy --strict`. New `agentao.harness.protocols` submodule re-exports
  the capability `Protocol` types (`FileSystem`, `ShellExecutor`,
  `MCPRegistry`, `MemoryStore`) plus their value shapes so embedding
  hosts have one stable import path instead of reaching into
  `agentao.capabilities.*`. CI gains a `Typing gate` job; tests cover
  the package, a downstream-shaped consumer, and `__all__` drift.
- **P0.5 lazy imports** — `from agentao import Agentao` no longer pulls
  in the OpenAI SDK, BeautifulSoup, jieba, filelock, or rich (or their
  transitive click/pygments/starlette/uvicorn closure via the MCP SDK).
  Embedded hosts pay only for what they use; the deferred libs load on
  first runtime use. Two new enforcement tests
  (`tests/test_no_cli_deps_in_core.py`, `tests/test_import_cost.py`)
  catch regressions both statically (AST walk for top-level imports
  outside `agentao/cli/`) and at runtime (`python -X importtime`).
- **P0.7 embedded-contract regression tests** — four new test files
  guard the host-facing properties most likely to silently break:
  `tests/test_no_host_logger_pollution.py` (no root-logger mutation
  through import + construction), `tests/test_multi_agentao_isolation.py`
  (two `Agentao()` instances share no state across messages, tools,
  skills, working_directory, or session_id), `tests/test_arun_events_cancel.py`
  (asyncio cancellation propagates to the chat token; events drain;
  no orphan tasks), and `tests/test_clean_install_smoke.py` (slow,
  CI-only — installs the wheel into a fresh venv and runs the README
  embed snippet). A `slow` pytest marker is registered; default runs
  skip it.
- **P0.8 replay schema v1.2 + harness→replay projection** — the
  replay JSONL format gains three harness-projected event kinds
  (`tool_lifecycle`, `subagent_lifecycle`, `permission_decision`) and
  `start_replay()` auto-wires a `HostReplaySink` that observes the
  agent's harness `EventStream` and projects every published event
  into the recorder, so embedded hosts have one audit artifact
  instead of two parallel streams. Each new kind's `oneOf` variant carries a typed payload
  derived from the public Pydantic model in `agentao.harness.models`,
  so a model field rename / removal surfaces as schema drift in CI.
  v1.0 / v1.1 schemas remain frozen and continue to validate older
  replays. New `agentao.harness.replay_projection` module:
  `HostReplaySink` (forward projection), `replay_payload_to_host_event`
  (reverse). The typed payload schemas explicitly allow the sanitizer's
  optional projection metadata (`redaction_hits`, `redacted`,
  `redacted_fields`) so a redacted harness event still validates against
  the v1.2 schema while genuine model drift still surfaces as a property
  mismatch. New `tests/test_host_to_replay_projection.py` covers the
  round trip, validates produced payloads against the v1.2 schema, and
  verifies a redacted payload (with a planted SECRET_PATTERN-shaped
  string) still passes schema validation. `SCHEMA_VERSION` bumps from
  `1.1` → `1.2`.
- **P0.6 five canonical embedding examples** — minimum-shape samples
  that run end-to-end against a fake LLM (no API key) under their own
  `pyproject.toml`: `examples/fastapi-background/` (per-request agent
  + asyncio background task), `examples/pytest-fixture/` (drop-in
  `agent` / `agent_with_reply` / `fake_llm_client` fixtures),
  `examples/jupyter-session/` (one agent per kernel, `events()`
  driving display, with a runnable `session.ipynb`),
  `examples/slack-bot/` (slack-bolt `app_mention` handler with
  channel-scoped `PermissionEngine` injection), and
  `examples/wechat-bot/` (polling daemon with contact-scoped
  `PermissionEngine`, transport-agnostic via a `WeChatClient`
  Protocol — inspired by `Wechat-ggGitHub/wechat-claude-code`). New CI
  `examples` job matrix runs each example's smoke suite in a fresh
  venv. `examples/README.md` gains a top-of-file table mapping each
  host shape to its directory.

### Changed

- **`agentao/display.py` moved to `agentao/cli/display.py`** — the
  `DisplayController` was used only by the CLI. Hosts that imported
  `agentao.display` directly should now import from `agentao.cli.display`
  (no in-tree consumers were affected).

## [0.3.3] — 2026-04-30

First release executing the **Path A roadmap** (see
`docs/design/path-a-roadmap.md`). Pure-additive patch. No required
code change to upgrade.

### Added

- **PEP 561 `py.typed` marker** — `agentao/py.typed` ships in wheel
  and sdist so downstream `mypy` / `pyright` consumers pick up
  Agentao's type hints instead of treating the package as untyped.

### Changed

- **README leads with embedding (`## Embed in 30 lines`)** — the
  CLI walkthrough is preserved verbatim under `## CLI Quickstart`.
  Reflects the locked Path A positioning: `agentao` is primarily a
  library to embed in Python hosts.

### Internal

- CI smoke job now asserts `py.typed` presence in the installed
  wheel and verifies bare `Agentao(...)` construction (the README
  snippet, verbatim) succeeds without env discovery or network.

## [0.3.1] — 2026-04-30

Added-only patch in the 0.3.x series. Lands the **embedded harness
contract** as the stable host-facing API surface for embedding
Agentao: typed event stream, JSON-safe permission snapshot, and
checked-in JSON schema snapshots for both events and ACP payloads.
No required code change to upgrade from 0.3.0.

### Added

- **`agentao.harness` public package** — the host-facing
  compatibility boundary for embedding Agentao. Exports the
  Pydantic event models, the `EventStream` primitive, the
  `ActivePermissions` snapshot, and schema export helpers:
  ```python
  from agentao.harness import (
      ActivePermissions,
      EventStream,
      StreamSubscribeError,
      HostEvent,
      ToolLifecycleEvent,
      SubagentLifecycleEvent,
      PermissionDecisionEvent,
      RFC3339UTCString,
      export_host_event_json_schema,
      export_host_acp_json_schema,
  )
  ```
  Internal runtime types (`AgentEvent`, `ToolExecutionResult`,
  `PermissionEngine`) are intentionally **not** re-exported — the
  harness package is the version-stable boundary. Hosts that target
  only `agentao.harness` (plus the `Agentao(...)` constructor and
  the new methods below) stay forward-compatible across releases.

- **`Agentao.events(session_id: str | None = None)`** — async
  iterator over `HostEvent`. No replay; bounded backpressure
  (slow consumers block the producer for matching events rather
  than dropping them). Same-session ordering is guaranteed; within
  one `tool_call_id`, `PermissionDecisionEvent` precedes
  `ToolLifecycleEvent(phase="started")`. MVP supports one stream
  consumer per `Agentao` instance; a second concurrent subscriber
  for the same `session_id` filter raises `StreamSubscribeError`.

- **`Agentao.active_permissions() -> ActivePermissions`** — JSON-safe
  snapshot of the active permission policy (`mode`, `rules`,
  `loaded_sources`). Cached; invalidated on `set_mode()` and on
  `add_loaded_source(...)` with a new label.

- **`PermissionEngine.active_permissions()` + `add_loaded_source()`**
  — engine-level snapshot getter and a host-injection point for
  provenance labels. `loaded_sources` carries stable string labels:
  `preset:<mode>`, `project:<path>`, `user:<path>`,
  `injected:<name>`. MVP intentionally does not expose per-rule
  provenance.

- **Three public lifecycle event families:**
  - `ToolLifecycleEvent` — phase ∈ `{started, completed, failed}`;
    cancellation surfaces as `phase="failed", outcome="cancelled",
    error_type=None`. Raw args / outputs are never present on the
    public payload (redacted/truncated `summary` only).
  - `SubagentLifecycleEvent` — phase ∈ `{spawned, completed, failed,
    cancelled}` (cancelled is a distinct phase here). Parent/child
    ids captured at spawn time, not inferred at completion.
  - `PermissionDecisionEvent` — fires on every decision
    (`allow` / `deny` / `prompt`), not only deny/prompt. Per-call
    `decision_id`; `matched_rule` projected when a rule fires,
    `None` on fallback semantics.

- **ACP host-facing Pydantic schema** (`agentao.acp.schema`) —
  `initialize`, `session/new`, `session/load`, `session/prompt`,
  `session/cancel`, `session/setModel`, `session/setMode`,
  `session/listModels`, `session/update` notifications,
  `request_permission`, `ask_user`, and the shared `AcpError`
  envelope as Pydantic models.

- **JSON schema snapshots** under `docs/schema/`:
  `host.events.v1.json` (events + permissions) and
  `host.acp.v1.json` (ACP payloads). Generated from the Pydantic
  models, byte-equality-checked by `tests/test_host_schema.py`
  and `tests/test_acp_schema.py`. A model change that shifts the
  wire form must update the snapshot in the same PR.

- **CI fast-fail schema drift check** —
  `scripts/write_host_schema.py --check` runs in `.github/workflows/ci.yml`
  Job 0 alongside the existing replay-schema check, so harness
  schema drift fails CI before the test matrix.

- **Runtime identity helpers** (`agentao.runtime.identity`,
  internal) — `session_id` / `turn_id` / `tool_call_id` /
  `decision_id` generation and normalization. Public events depend
  on stable id propagation; the helpers are not re-exported from
  `agentao.harness`.

- **`examples/host_events.py`** — single-file runnable demo
  showing `agent.events()` + `agent.active_permissions()` wired
  alongside `agent.arun(...)` via `asyncio.gather`. Exits cleanly
  with instructions when `OPENAI_API_KEY` is missing.

- **`docs/reference/host-api.md`** + `docs/reference/host-api.zh.md` — public
  API reference, schema-snapshot policy, runtime identity contract,
  and event delivery semantics. **`docs/design/embedded-host-contract.md`**
  documents the design decision and non-goals.

- **`docs/guides/embedding.md` §7 "Host-facing harness contract"** — full
  embedding-shaped walkthrough with the `asyncio.gather` pattern;
  §8 migration guide extended with a "From 0.3.0" subsection.

- **Developer guide updates** — Appendix A.10 lists the
  `agentao.harness` exports; A.1 Methods table marks `events()` and
  `active_permissions()` as `(0.3.1+)`; Part 4.2 adds an admonition
  distinguishing `HostEvent` (host-stable) from `AgentEvent`
  (internal); Part 5.4 gains a "Reading the active policy from the
  host" subsection.

### Changed

- `agentao.runtime.sanitize.normalize_tool_calls` now synthesizes a
  UUID4 `tool_call_id` when the LLM provider returns a missing or
  empty `id`, using the same `runtime.identity.normalize_tool_call_id`
  helper the planner uses downstream. Strict Chat Completions APIs
  reject mismatched `tool_call_id` between assistant and tool roles;
  before this fix, a missing provider id left the assistant message
  with no id while the planner synthesized one for the tool result,
  producing a 400 on the next turn.

- `cli /status` permission-mode banner now reads from
  `agent.active_permissions()` instead of reaching into private
  `PermissionEngine` state, and displays `loaded_sources` for
  transparency. The CLI consumes the same public surface that
  external embedders see.

- ACP `session/new` and `session/load` now bind the session id onto
  the agent at session creation/load time so harness lifecycle
  events for that session carry the id the host knows it by.

### Dependencies

- **New direct dependency: `pydantic>=2`.** If your environment
  pins Pydantic v1, lift the pin before upgrading.

### Notes

- This is an **Added-only patch** — the 0.3.x series treats
  additive public surfaces as patch-eligible during pre-1.0. Strict
  SemVer consumers should read it as equivalent to a minor bump.
- Public events deliberately omit raw tool args, raw stdout/stderr,
  raw diffs, and MCP raw responses. Only redacted/truncated
  `summary` / `task_summary` / `reason` strings reach hosts.

## [0.3.0] — 2026-04-29

### Added

- **`MCPRegistry` capability protocol** (Issue #17). Embedded hosts
  can now enumerate MCP servers from any source (in-process dict,
  plugin system, dynamic discovery, remote registry) without writing
  to `.agentao/mcp.json`. Two default implementations ship in
  `agentao.mcp.registry`: `FileBackedMCPRegistry` (CLI/ACP default —
  reads `<wd>/.agentao/mcp.json` + `~/.agentao/mcp.json`,
  byte-equivalent to the pre-Protocol behavior) and
  `InMemoryMCPRegistry` (programmatic counterpart for hosts and
  tests). Re-exported from `agentao.capabilities` for symmetry with
  `FileSystem` / `MemoryStore`:
  ```python
  from agentao.capabilities import (
      MCPRegistry, FileBackedMCPRegistry, InMemoryMCPRegistry,
  )
  ```
- `Agentao(mcp_registry=...)` keyword. Mutually exclusive with
  `mcp_manager=` (which is the pre-built construction outcome — the
  registry is the config source for construction). Bare
  `Agentao(working_directory=...)` outside the factory still falls
  back to `load_mcp_config` so existing CLI-shaped scripts keep
  working.

- **`MemoryStore` capability protocol** (Issue #16). Embedded hosts
  can now swap memory backends — Redis, Postgres, in-process dict,
  remote API — without subclassing or forking `MemoryManager`. The
  `SQLiteMemoryStore` default is unchanged and remains the CLI/ACP
  backing store. Re-exported from `agentao.capabilities` for symmetry
  with `FileSystem` / `LocalFileSystem` / `ShellExecutor`:
  ```python
  from agentao.capabilities import MemoryStore, SQLiteMemoryStore
  ```
- `SQLiteMemoryStore.open(path)` — strict path-based constructor that
  creates the parent dir and propagates `OSError` / `sqlite3.Error`
  on failure. Use this for the user-scope store where a failure
  should disable the scope rather than silently degrade.
- `SQLiteMemoryStore.open_or_memory(path)` — graceful constructor
  that degrades to `:memory:` on `OSError` / `sqlite3.Error`. Use
  this for the project-scope store where a missing DB is preferable
  to a crashed agent (matches the pre-#16 ACP fault-tolerance).
  The two classmethods make the asymmetry between
  project-falls-back and user-disables explicit at every call site;
  no boolean disambiguation needed.

### Changed

- `agentao.embedding.build_from_environment()` now constructs a
  `FileBackedMCPRegistry(project_root=wd, user_root=user_root())` and
  passes it to `Agentao` as `mcp_registry=`. CLI and ACP behavior is
  unchanged because the registry resolves the same files. Hosts that
  want programmatic registration pass an explicit `mcp_registry=`
  (or any `MCPRegistry`-compatible object) to override the default.
- `agentao.memory.MemoryStore` is no longer re-exported from
  `agentao.memory` — the canonical home is `agentao.capabilities`.
  Re-exporting it from the memory package would force
  `import agentao.memory` to load all of `agentao.capabilities`,
  which after Issue #17 transitively pulls the MCP SDK and breaks
  the `tests/test_memory_decoupling.py` decoupling guarantee.

- `MemoryManager(project_store=..., user_store=...)` now accepts
  pre-built `MemoryStore` instances. Path-based construction (the
  pre-#16 shape) moves to the call site:
  ```python
  # before:
  mgr = MemoryManager(project_root=p, global_root=g)
  # after:
  mgr = MemoryManager(
      project_store=SQLiteMemoryStore.open_or_memory(p / "memory.db"),
      user_store=SQLiteMemoryStore.open(g / "memory.db") if g else None,
  )
  ```
  CLI and ACP users see no change because the factory
  (`agentao.embedding.build_from_environment()`) absorbs the new
  construction shape internally.
- The `:memory:` fallback for unwritable project DBs has moved from
  `MemoryManager.__init__` into `SQLiteMemoryStore.open_or_memory`.
  Behavior is observably identical: project store still degrades to
  `:memory:` on `OSError` / `sqlite3.OperationalError`, user store
  is still disabled with a warning on the same errors.
- `agentao.memory.MemoryManager` no longer imports `sqlite3` and has
  no filesystem knowledge. Embedded hosts that construct it directly
  with custom stores see zero disk I/O from the manager.

### Removed

- `MemoryManager.__init__(project_root=, global_root=)` — replaced by
  the explicit-store signature above. **Migration:** build the stores
  via `SQLiteMemoryStore.open_or_memory(path)` (or `.open(path)`) and
  pass them as `project_store=` / `user_store=` kwargs.
- `MemoryManager._project_root` / `MemoryManager._global_root` private
  attributes are gone. Tests / introspectors that probed these
  should read `manager.project_store.db_path` (or accept that a
  swapped backend may not expose any path at all).

### BREAKING

- **`Agentao(working_directory=)` is now required** (Issue #14, the
  hard break promised in the 0.2.16 soft-deprecation cycle).
  `working_directory` is a required keyword argument; calling
  `Agentao()` without it raises `TypeError` from Python's signature
  dispatch — there is no longer a `Path.cwd()` lazy fallback. Two
  Agentao instances created with different `working_directory`
  values report independent paths even in the same process; an
  `os.chdir` inside the host has no effect on an already-constructed
  Agentao. **Migration:** pass an explicit `Path` (preferred for
  embedded hosts), or use
  `agentao.embedding.build_from_environment()` for CLI-style
  auto-detection from the surrounding `cwd` / `.env` / `.agentao/`.
  CLI and ACP behavior is unchanged because both already route
  through the factory; the audit confirmed `os.chdir` is never
  called inside `agentao/`, so no mid-process cwd retargeting is
  affected.

### Removed

- `Agentao.__init__` no longer emits a `DeprecationWarning` when
  `working_directory` is missing — the warning was the 0.2.16
  one-cycle migration aid and is now obsolete because the argument
  is required at the signature level.
- `Agentao._explicit_working_directory` private attribute renamed
  to `Agentao._working_directory` (always populated, never
  `Optional`). External code should not read this; callers should
  use the `agent.working_directory` property.
- `Agentao.working_directory` property no longer falls back to
  `Path.cwd()`. The "lazy cwd" branch (`agent.py:376-378` in
  0.2.16) is deleted; the property now returns the frozen value
  unconditionally.

---

## [0.2.16] — 2026-04-28

Maintenance release that completes the **embedded-harness M2/M3
milestones**. `Agentao(...)` is now a pure-injection construction
surface: nothing in the constructor implicitly reads `os.environ`,
`Path.home()`, `Path.cwd()`, or `<wd>/.agentao/*.json` unless the
caller routes through `agentao.embedding.build_from_environment()`.
CLI and ACP both go through the factory, so end-user behavior is
unchanged; embedded hosts get a deterministic, side-effect-free
construction surface plus an `await agent.arun(...)` async path,
opt-in `replay` / `sandbox` / `bg_store`, and a
`DeprecationWarning` for `Agentao()` constructed without
`working_directory=` (a `TypeError` in `0.3.0`).

See [`docs/releases/v0.2.16.md`](docs/releases/v0.2.16.md) for the
release summary and maintainer checklist.

### Added

- **Embedded harness foundations** (Issues #9-#13). Agentao is now
  positioned as an embedded agent runtime that hosts can drop into
  their own apps without the implicit cwd/env/.agentao/ side effects
  the CLI relies on. Headline pieces:
  - `agentao.capabilities.FileSystem` / `ShellExecutor` protocols
    plus `LocalFileSystem` / `LocalShellExecutor` defaults. File,
    search, and shell tools route through them, so embedded hosts
    can swap in Docker exec, virtual filesystems, or remote runners
    without monkey-patching `subprocess` / `pathlib`.
  - `agentao.embedding.build_from_environment(...)` factory that
    captures every implicit `.env` / `.agentao/permissions.json` /
    `.agentao/mcp.json` / cwd read in one place. CLI and ACP route
    through it so subsystem fallbacks become dead code from their
    perspective.
  - `Agentao.__init__` accepts explicit injections for
    `llm_client`, `logger`, `memory_manager`, `skill_manager`,
    `project_instructions`, `mcp_manager`, `filesystem`, and
    `shell`. When `skill_manager` or `project_instructions` is
    injected, the auto-discovery / disk-read paths are skipped.
  - `Agentao.arun(...)` async surface that bridges sync chat
    internals through `loop.run_in_executor`. Async hosts can
    `await agent.arun(...)` without rolling their own thread
    bridge; cancellation, replay, and `max_iterations` behave
    identically across `chat()` and `arun()`.
- Sub-agent construction in `agentao/agents/tools.py` no longer
  re-reads provider env vars (`{PROVIDER}_API_KEY` / `_BASE_URL`).
  Children inherit the parent's already-resolved LLM config so a
  mid-run env mutation cannot create a credential split.

### Changed

- **`Replay` / `Sandbox` / `BackgroundTaskStore` are now opt-in**
  (Issue 9 of the embedded-harness epic). `Agentao.__init__` accepts
  three new keyword-only kwargs: `replay_config`, `sandbox_policy`,
  and `bg_store`. Each defaults to `None`, which now means *fully
  disabled* — embedded hosts that didn't ask for the feature pay
  zero cost. `agentao.embedding.build_from_environment()` constructs
  CLI defaults for all three (anchored to the session's working
  directory) and passes them explicitly, so CLI and ACP behavior is
  unchanged. Callers can pass `bg_store=None` etc. as a factory
  override to disable a feature even on the CLI path.
  - When `bg_store=None`: `check_background_agent` and
    `cancel_background_agent` are not registered, the chat loop's
    background-notification drain short-circuits, and the
    `run_in_background` field is **schema-level removed** from
    sub-agent tool definitions (not expose-then-error). The LLM
    cannot be tempted to call a disabled feature, and ACP / OpenAI
    tool catalogs do not advertise it. `/agent bg|dashboard|cancel|
    delete|logs|result` CLI subcommands short-circuit with a clear
    warning when invoked against an Agentao with `bg_store=None`.
  - When `sandbox_policy=None`: `ToolRunner` runs shell commands
    without the macOS sandbox-exec wrapper.
  - When `replay_config=None`: no `<wd>/.agentao/replay.json` is
    read at construction time; `Agentao._replay_config` falls back
    to the no-op `ReplayConfig()` default.

- **Subsystem constructors no longer fall back to `os.environ` /
  `Path.cwd()` / `Path.home()`** (Issue 5 of the embedded-harness
  epic, PR 3b). Callers must now supply explicit arguments — CLI,
  ACP, and `agentao.embedding.build_from_environment()` already do,
  so end-user behavior is unchanged. Direct constructions in
  embedded-host code or test code may break; the migration is to
  pass the previously-implicit values explicitly.
  - `LLMClient(api_key=, base_url=, model=)` are required keyword
    arguments; `temperature` defaults to `0.2` and `max_tokens` to
    `65536` in code (no more `LLM_TEMPERATURE` / `LLM_MAX_TOKENS`
    env reads). `Agentao` now also accepts a top-level
    `max_tokens=` kwarg that forwards to `LLMClient`. The factory
    is the single place that resolves `LLM_PROVIDER` /
    `*_API_KEY` / `*_BASE_URL` / `*_MODEL` / `LLM_TEMPERATURE` /
    `LLM_MAX_TOKENS`.
  - `PermissionEngine(project_root=)` is required; new keyword-only
    `user_root=` (defaults to `None`) replaces the implicit
    `Path.home() / ".agentao"` user-rules read. The factory and ACP
    `session/new` / `session/load` pass both roots explicitly.
  - `load_mcp_config(project_root=)` is required; new keyword-only
    `user_root=` (defaults to `None`) replaces the implicit
    `Path.home() / ".agentao"` user-scope read. `save_mcp_config()`
    drops `global_config: bool` in favor of an explicit
    `config_dir: Path`. CLI `/mcp add` / `/mcp remove` resolve the
    project directory through `cli.agent.working_directory`
    instead of `Path.cwd()`.
  - `Agentao.__init__` no longer defaults `MemoryManager`'s
    `global_root` to `Path.home() / ".agentao"` when no
    `memory_manager` is injected; pure-injection construction is
    now project-scope only. CLI / ACP receive the user root through
    the factory exactly as before.

### Deprecated

- `Agentao()` without `working_directory=` emits a `DeprecationWarning`
  and will become a `TypeError` in 0.3.0. Pass an explicit `Path` —
  or use `agentao.embedding.build_from_environment()` for CLI-style
  cwd / `.env` / `.agentao/` auto-discovery.

---

## [0.2.15] — 2026-04-27

Maintenance follow-up to `0.2.14`. Headline: **ACP control-plane
parity** — `session/set_model`, `session/set_mode`, and
`session/list_models` handlers land so ACP clients (Zed and others)
can drive model switching, permission-mode toggles, and capability
discovery on a live session. The same release fixes three
correctness gaps around the ACP stdio channel and streaming
`reasoning_content`.

### Added

- **`session/set_model` handler** (`agentao/acp/session_set_model.py`):
  apply `model` / `contextLength` / `maxTokens` independently on a
  running session via `agent.set_model()` and `agent.context_manager.max_tokens`
  / `agent.llm.max_tokens`. Each knob is optional; partial requests
  do not reset untouched fields. Holds the session's idle turn lock
  so an in-flight `session/prompt` cannot observe a mid-stream
  change. Conversation history and tool state are preserved.
- **`session/set_mode` handler** (`agentao/acp/session_set_mode.py`):
  toggle `PermissionEngine` mode (`default` / `acceptEdits` /
  `bypassPermissions` / `plan`) per session via
  `permission_engine.set_mode(...)`.
- **`session/list_models` handler** (`agentao/acp/session_list_models.py`):
  call `agent.list_available_models()` and cache the result on
  `AcpSessionState.last_known_models`. On provider lookup failure,
  returns the cached list plus a `warning` field instead of a
  JSON-RPC error so transient provider outages don't blank the UI.
- **Shared session-validation helper**
  (`agentao/acp/_handler_utils.py`): single point for "does this
  `session_id` exist, is it ours, did the client send a well-formed
  request" so each new handler does not re-derive the contract.
- **Streaming `reasoning_content` capture** (`agentao/llm/client.py`):
  thinking-model output arriving on the streaming `delta` is now
  forwarded the same way as the non-streaming
  `message.reasoning_content` field, so transport `THINKING` events
  no longer drop reasoning text from streaming backends.
- **Test coverage** for all of the above:
  `tests/test_acp_session_set_model.py` (484 lines, 31 cases),
  `tests/test_chat_stream_reasoning.py`,
  `tests/test_llm_handler_marker.py`,
  `tests/test_shell_stdin_devnull.py`.

### Fixed

- **Outsider log handlers preserved across `LLMClient` reconstruction**
  (`agentao/llm/client.py`): the package-root handler eviction now
  only drops handlers tagged with `_agentao_llm_file_handler=True`.
  Previously, every `LLMClient` rebuild (which `set_model` triggers,
  and which the test suite triggers repeatedly) silently evicted
  unrelated handlers — including the `AcpServer` stderr-guard handler
  that protects the ACP JSON-RPC stdout/stdin channel.
- **Shell subprocess no longer inherits parent stdin**
  (`agentao/tools/shell.py`): `Popen(..., stdin=subprocess.DEVNULL)`.
  Children that read from stdin (interactive prompts, `read`-style
  tooling) can no longer consume bytes from the ACP JSON-RPC stdin
  channel that the parent process owns.

### Packaging

- `.gitignore`: ignore rotated `*.log.*` files (avoid tracking the
  bounded-rotation artifacts introduced in `0.2.14`).
- `.github/workflows/ci.yml`: `actions/upload-artifact` pinned at v7
  (v8 does not exist; resolved on-branch in `e84fc0b`).

See [`docs/releases/v0.2.15.md`](docs/releases/v0.2.15.md) for the
release summary and maintainer checklist.

---

## [0.2.14] — 2026-04-25

Maintenance follow-up to `0.2.13` GA. Headline: **tool-call resilience
layer** for local / open-source models that drift from the OpenAI
function-call schema, plus per-session isolation polish, replay schema
drift gating, and the GitHub-Actions Node 24 prep.

### Added

- **Tool-call repair / outbound sanitize subsystem** (`agentao/runtime/`):
  three cooperating modules that sit between the LLM and the tool
  dispatcher so models like GLM, DeepSeek, Kimi and local Ollama still
  land in a runnable shape.
  - `arg_repair.py`: conservative JSON repair for malformed function
    arguments — double-encoded JSON, fenced JSON, lenient Python
    literals, trailing commas, bracket imbalance. No punctuation
    guessing.
  - `name_repair.py`: fuzzy matching that maps near-miss tool names
    (CamelCase / suffix variants) onto a registered tool when the score
    is unambiguous.
  - `sanitize.py`: outbound scrubbing — replaces lone UTF-16 surrogates
    and re-emits canonical compact JSON for repaired arguments before
    assistant / tool messages reach strict provider APIs.
  Wired into `chat_loop`, `tool_planning`, and `tool_runner`; repair is
  invisible to the model itself (only logged), preserving prompt-cache
  behaviour. Coverage: `tests/test_tool_argument_repair.py`,
  `tests/test_tool_name_repair.py`, `tests/test_outbound_sanitize.py`,
  helper `tests/support/tool_calls.py`. Documented in developer-guide
  §5.1 ("Tool-call normalization").
- **Per-instance background-task store** (commit `82edb55`): the
  background-agent registry is now per-`Agentao` instance rather than
  process-global, so concurrent ACP sessions / multi-tenant embeddings
  no longer leak handles across each other. Adds path-containment
  guards and prompt-diagnostics surfacing.
- **Replay JSON Schema export** (commit `5c85179`): `agentao/replay/`
  now ships an exported JSON Schema and a CI drift-detection job
  (`tests/test_replay_schema.py`) that fails fast when
  `agentao/replay/events.py` evolves without the schema being
  regenerated.

### Changed

- **`ToolRunner` split** (commit `f5dc034`): the monolithic
  `tool_runner` decomposed into focused `tool_planning`,
  `tool_runner` (executor), and `tool_result_formatter` modules under
  `agentao/runtime/`. Public `Agentao.chat()` contract preserved.
- **Test scaffolding** (commit `e6ccfee`): ACP test helpers extracted
  into `tests/support/` so individual test files stay focused on
  scenarios rather than fixture wiring.
- **Logging rotation**: `agentao.log` now uses
  `RotatingFileHandler(maxBytes=10_000_000, backupCount=5)` instead of
  a plain `FileHandler`, capping disk footprint at ~60 MB. The home-dir
  fallback (`~/.agentao/agentao.log`) gets the same rotation. Long-
  running sessions that previously grew the log into the hundreds of
  megabytes now self-cap.

### Fixed

- **VitePress docs at custom-domain root** (commit `875e526`): the
  developer-guide deploy now serves correctly at the `agentao.cn`
  custom-domain root rather than under a subpath.

### Packaging / CI

- `actions/upload-artifact` v4 → v7, `actions/download-artifact` v4 →
  v8, `actions/setup-python` v5 → v6 — clears the GitHub Node 24
  default cutover (2026-06-02). (`upload-artifact` has no v8 line yet;
  v7 is the current major.) `setup-uv` had already moved v6 → v7 in
  `0.2.14.dev0`.
- Version pins refreshed from `0.2.13` to `0.2.14` across `docs/guides/acp.md`
  and the developer-guide install / version-check examples.

---

## [0.2.13] — 2026-04-24

Promotes `0.2.13rc1` to general availability, plus one additive feature
(monorepo skill install) folded into the GA cut.

Headline: **runtime decomposition + session replay subsystem**, now with
**monorepo-aware `skill install`** layered on top. The substantive
Added / Changed breakdown — session replay (`agentao/replay/`), the
`agentao --help` / `-h` entry-point fix, and the four-module runtime
split (`runtime/`, `acp_client/manager/`, `cli/commands_ext/`, new
`prompts/` and `tooling/` packages) — is preserved below from the
`[0.2.13rc1]` soak entry.

The GA cut also carries a packaging + documentation pass: version string
aligned from `0.2.13rc1` → `0.2.13`, `docs/guides/acp.md` examples bumped,
Quick Start env var guidance synced with the strict provider-gating
behaviour shipped in `0.2.11`, the GitHub Pages workflow switched from
the legacy Jekyll template to the actual VitePress developer-guide
build, and lingering `0.2.10` / `0.2.11` install-pin examples in the
developer guide refreshed to the current line.

### Added

- **Monorepo skill install** (`agentao skill install owner/repo:path[@ref]`): extends the GitHub installer to pull a single skill out of a multi-skill repository — e.g. `agentao skill install anthropics/skills:pptx@main` installs only the `pptx/` subdirectory instead of rejecting the archive for missing a top-level `SKILL.md`. `SourceSpec.package_path` (`agentao/skills/sources.py`) carries the subpath; `GitHubSkillSource.resolve()` parses the `:path` segment and rejects empty / absolute / `.` / `..` components. `SkillInstaller._find_package_root()` (`agentao/skills/installer.py`) validates the subdirectory exists, is a directory, and contains `SKILL.md`; the recorded `source_ref` preserves the full `owner/repo:path@ref` string so `skill update` round-trips. CLI help on `skill install` now advertises the new form. Coverage: `tests/test_skill_installer.py` (+119 lines across success / empty-path / parent-dir-traversal / update paths), `tests/test_skill_cli.py`.
- **Session replay subsystem** (`agentao/replay/`): JSONL timeline of runtime events written to `.agentao/replays/`, with recorder, reader, redaction, retention, and sanitization. Wired through `transport/events.py` and surfaced via the new `cli/replay_commands.py` / `replay_render.py`. Feature docs: `docs/guides/session-replay.md`. Tests: `tests/test_replay*`, `tests/test_replay_redact.py`.
- **`agentao --help` / `agentao -h`**: explicit `-h` / `--help` handler on the top-level CLI parser. Prints usage and exits `0` instead of silently falling through to interactive mode (the previous `add_help=False` + `parse_known_args()` combination swallowed the flag). Regression coverage: `tests/test_acp_cli_entrypoint.py::TestEntrypointArgparse::test_help_flag_prints_help_and_exits` and `test_short_help_flag_prints_help_and_exits`.

### Changed

- **Runtime decomposition** — four monolithic modules split into focused packages; public `Agentao.chat()` / `tool_runner` contract preserved (`agentao/tool_runner.py` kept as a compat shim):
  - `agentao/runtime/` (new): `chat_loop`, `tool_runner`, `model`, `llm_call`, `turn` extracted from `agent.py` (~660 net lines removed from `agent.py`).
  - `agentao/acp_client/manager.py` (2938 lines) → `manager/` package (`connection`, `core`, `helpers`, `interactions`, `lifecycle`, `recovery`, `status`, `turns`).
  - `agentao/cli/commands_ext.py` (1688 lines) → `commands_ext/` package (`acp`, `agents`, `crystallize`, `memory`).
  - `agentao/cli/app.py` shrunk by ~800 lines; new CLI modules `input_loop`, `ui`, `acp_inbox`.
  - `agentao/prompts/` (new): `builder` + `sections` + `helpers` for system-prompt composition. `agent._build_system_prompt()` and `agent._load_project_instructions()` retained as thin facades so existing tests and external patches keep working.
  - `agentao/tooling/` (new): `registry`, `agent_tools`, `mcp_tools`.
- **Docs**: `docs/guides/acp.md` version examples bumped from `0.2.10` to `0.2.13`. Developer-guide `part-2/2-constructor-reference.md`, `part-5/5-memory.md`, `part-5/6-system-prompt.md` (en + zh mirrors) updated to reference the new `prompts/builder.py` location for system-prompt composition.

### Packaging / Release (GA)

- Align package version, changelog, release notes, and publish workflow usage to the final `0.2.13` release line.
- README / `docs/start/quickstart.md` Quick Start: document all three required provider variables (`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`) up front. Previously only `OPENAI_API_KEY` was shown, contradicting the strict-provider-gating behaviour introduced in `0.2.11` — the single-key snippet would raise `ValueError` at startup.
- `.github/workflows/jekyll-gh-pages.yml` replaced by a VitePress build + deploy pipeline pointed at `developer-guide/`. The Jekyll template was a repo-init leftover; the actual docs site is VitePress, so the previous workflow was deploying nothing useful.
- Developer-guide install-pin / version-check examples refreshed from `0.2.10` / `0.2.11` to `0.2.13` in `part-1/5-requirements.md`, `part-2/1-install-import.md`, `part-3/2-agentao-as-server.md` (JSON response example), and `part-3/5-zed-ide-integration.md` (en + zh mirrors). Historical statements ("Since v0.2.10…", "Pre-0.2.10 Agentao used…") are kept — they describe when a surface was introduced, not the current pin.

### Documentation

- Add `docs/releases/v0.2.13.md`.
- `docs/guides/skills.md` and `developer-guide/en|zh/part-5/2-skills.md` document the new monorepo `skill install` form with worked examples against `anthropics/skills` (pptx, docx, xlsx, pdf, doc-coauthoring).

---

## [0.2.12] — 2026-04-22

### Added

- **Headless runtime v1** (`docs/guides/headless-runtime.md`): operator-facing contract for `ACPManager` as a non-interactive embedding target — public entry points (`prompt_once`, `send_prompt`), single-active-turn concurrency pinned to `AcpErrorCode.SERVER_BUSY`, typed status snapshot. `send_prompt_nonblocking` family is classified **internal / unstable** and removed from the embedding contract.
- **`ServerStatus` dataclass** (`agentao/acp_client/models.py`, re-exported from `agentao.acp_client`): frozen v1 shape with `server`, `state`, `pid`, `has_active_turn`.
- **`examples/headless_worker.py`**: runnable headless smoke consumer. Spins up an inline mock ACP server, exercises success / non-interactive error / cancel paths, and prints the typed snapshot after each.
- **`tests/test_headless_runtime.py`**: baseline smoke tests pinning the Week 1 contract — typed snapshot shape, `has_active_turn` derivation, `SERVER_BUSY` on concurrent submit, cancel-then-continue, non-interactive reject non-pollution, timeout recovery, session reuse.
- **Headless runtime Week 2 diagnostics** (`docs/guides/headless-runtime.md` §3-§4, additive on `ServerStatus`): `active_session_id`, `last_error`, `last_error_at` (tz-aware UTC `datetime` assigned at *store time* inside the manager, not raise time), `inbox_pending`, `interaction_pending` (singular, replaces the pre-v1 `interactions_pending` alias), `config_warnings` (per-server list; Week 3 will populate on legacy config).
- **`ACPManager.readiness(name)` / `.is_ready(name)`**: typed 4-valued classifier (`"ready" | "busy" | "failed" | "not_ready"`) over the combination of handle state and the active-turn slot. Consumers that only need a gating signal should prefer this over string-matching on `state`.
- **`ACPManager.reset_last_error(name)`**: explicit clear for the sticky `last_error` / `last_error_at` surface. A new error overwrites automatically; this method is only needed when the host wants to drop the stored error without waiting for a new one.
- **State-vs-error contract**: the recorded-error surface is diagnostic, not gating — `state` is the authoritative readiness signal, `last_error` is history. `SERVER_BUSY` and `SERVER_NOT_FOUND` are intentionally excluded from the store so fail-fast retries do not overwrite real failures. Pinned by tests (`tests/test_headless_runtime.py::TestLastErrorStore`) including a `datetime`-patch proof that the timestamp is taken inside `_record_last_error`, not pre-computed.
- **`InteractionPolicy` dataclass** (Week 3, Issue 11) re-exported from `agentao.acp_client`. Minimal single-dimension policy model over the non-interactive interaction decision: `InteractionPolicy(mode="reject_all" | "accept_all")`. No other knobs — additional dimensions belong on a new options object.
- **`interaction_policy=` per-call override** on `ACPManager.send_prompt` and `ACPManager.prompt_once`. Accepts `InteractionPolicy` or the bare strings `"reject_all"` / `"accept_all"`. Precedence: per-call override > server default (`nonInteractivePolicy`). `None` falls back to the server default. `send_prompt_nonblocking` is **internal / unstable** per the Week 1 decision and deliberately does **not** accept this kwarg — the Week 3 policy surface is `send_prompt` + `prompt_once` only.
- **Headless runtime Week 4 lifecycle & recovery** (`docs/guides/headless-runtime.md` §7). Pins the deterministic release order on every failure path (pending-slot drop → turn-slot clear → lock release → `last_error` record) and introduces the client/process-death classifier.
- **`classify_process_death` pure classifier** exported from `agentao.acp_client`. Maps `(exit_code, signaled, during_active_turn, restart_count, max_recoverable_restarts, handshake_fail_streak)` to `"recoverable"` / `"fatal"` per the Issue 16 decision matrix. Testable in isolation; the manager calls it inside `ensure_connected` to decide whether to lazy-rebuild or flip the server into the sticky fatal state.
- **`ACPManager.is_fatal(name)` / `.restart_count(name)`** surfaces for the recovery state. `is_fatal(name)` is sticky — cleared only by an explicit `restart_server` or `start_server` call (operator action required).
- **`AcpServerConfig.max_recoverable_restarts`** (JSON: `maxRecoverableRestarts`, default 3). Caps consecutive auto-recoveries on recoverable idle non-zero exits before the manager flips the server to fatal. Active-turn deaths bypass the cap; each is always allowed at least one rebuild attempt.
- **Daemon-style regression suite** (`tests/test_headless_runtime.py::TestDaemonRegression`): long session reuse, reject-then-continue, cancel-then-continue, timeout-then-continue, and process-death recovery (both recoverable and fatal). Pinned against the mock ACP server from `test_acp_client_embedding` so the scenarios stay executable in CI.
- **`/crystallize` evidence + feedback loop**: `SkillEvidence` and `SkillFeedbackEntry` dataclasses (`agentao/skills/drafts.py`) extend `SkillDraft` with structured tool-activity grounding (`user_goals`, `assistant_conclusions`, `tool_calls`, `tool_results`, `key_files`, `workflow_steps`, `outcome_signals`), a `feedback_history` rewrite log, and `open_questions`. Drafts persist forward- and backward-compatible JSON — legacy payloads load with empty evidence/history.
- **`collect_crystallize_evidence` / `render_crystallize_context`** (`agentao/cli/commands_ext.py`): pull structured evidence from the live `AgentaoCLI` message history (tool calls + tool results, not just narrated text) and render it as the `# Structured evidence` block consumed by `/crystallize suggest|refine|feedback`.
- **`feedback_prompt` + `FEEDBACK_SYSTEM_PROMPT`** (`agentao/memory/crystallizer.py`): drive user-feedback-driven draft rewrites; `suggest_prompt()` and `refine_prompt()` gained an optional `evidence_text=` parameter so all three prompts share the same evidence grounding. Drafts grounded in tool activity, not just raw transcript.
- **`append_skill_feedback` + `summarize_draft_status`** (`agentao/skills/drafts.py`): durable feedback log and lightweight status view for `/crystallize status`.
- **`tests/test_skill_crystallize_enhancement.py`**: 15 tests covering the new dataclass schema, persistence round-trip, backward-compatible load of legacy drafts, prompt-builder evidence injection, and feedback append/history rendering.
- **Plan doc** `docs/history/implementation/skill-crystallize-enhancement-plan.md`: design rationale and API surface for the three-problem scope (structured evidence in drafts, user feedback loop, `/help` discoverability).

### Changed

- **Breaking: `ACPManager.get_status()` now returns `list[ServerStatus]`** instead of `list[dict]`. This is a deliberate, once-for-all API convergence — there is no `get_status_typed()` side channel and no permanent dict alias. Migration table and field semantics are in `docs/guides/headless-runtime.md#3-status-snapshot-v1--v2`.
  - The legacy `"name"` dict key is renamed to `ServerStatus.server`.
  - Week-1 core fields are `server` / `state` / `pid` / `has_active_turn`. Week 2 adds `active_session_id`, `last_error`, `last_error_at`, `inbox_pending`, `interaction_pending`, `config_warnings` **additively** — the Week 1 shape is unchanged.
  - `has_active_turn` is derived from the manager's active turn slot (not handle state), so it stays `True` across the in-flight interaction phase of non-interactive turns.
  - `last_error` is sticky across successful turns by design (so once-per-minute pollers still see the last-known failure); clear explicitly via `reset_last_error(name)` or wait for a new error to overwrite.
- CLI `/acp list` / session status readouts and the embedding developer-guide pages (part-1 mode 3, part-3 reverse-ACP, appendix A / D / F / G, zh + en mirrors) are migrated to the typed contract.
- **Breaking: `nonInteractivePolicy` bare-string config form is removed** (Week 3, Issue 12). `.agentao/acp.json` must now use the structured object form — `"nonInteractivePolicy": {"mode": "reject_all" | "accept_all"}`. The legacy strings `"reject_all"` / `"accept_all"` as a bare value raise `AcpConfigError` **at config-load time** (`AcpClientConfig.from_dict` / `load_acp_client_config`). There is no silent upgrade and no deferred runtime failure — a drifted config cannot slip through to `send_prompt` execution. Migration: see [developer-guide appendix E.7](./developer-guide/en/appendix/e-migration.md#e7-headless-runtime--noninteractivepolicy-shape-change-week-3) (and the zh mirror).
- `AcpServerConfig.non_interactive_policy` is now typed as `InteractionPolicy` (previously `str`). Downstream callers that read `server_cfg.non_interactive_policy` should read `.mode` instead.

---

## [0.2.11] — 2026-04-19

### Added

- **Multi-provider `web_search`**: `WebSearchTool` now reads `BOCHA_API_KEY` once at startup. When present, all web searches route through Bocha Search API (`POST https://api.bochaai.com/v1/web-search`, Bearer auth, structured JSON results). When absent, the tool falls back to DuckDuckGo — no configuration change required for existing users.

### Changed

- **Strict LLM provider gating** (breaking): `LLMClient.__init__` now raises `ValueError` at startup if any of `{PROVIDER}_API_KEY`, `{PROVIDER}_BASE_URL`, or `{PROVIDER}_MODEL` is absent and was not supplied via constructor args. Previously a missing model silently fell back to a hardcoded default. Migrate: add all three to `.env`.
- `/provider` listing now only shows providers that have all three of `{PROVIDER}_API_KEY`, `{PROVIDER}_BASE_URL`, and `{PROVIDER}_MODEL` set. Switching to an incomplete provider also errors with a clear message.
- Removed `_PROVIDER_DEFAULT_MODELS` internal dict from `LLMClient`.
- `gpt-5.4` added to context-manager tokenizer mapping (`o200k_base` encoding, same as `gpt-4o` family).
- Default model in all examples, templates, and documentation updated from `gpt-4o` → `gpt-5.4`.

### Migration

```bash
# Before (silently used default model fallback):
OPENAI_API_KEY=sk-...

# After (all three required):
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-5.4        # or whichever model you target
```

---

## [0.2.10] — 2026-04-15

Promotes the `0.2.10` line to general availability.

The feature set — ACP embedding facade and `/crystallize refine` — is
the same as the `[0.2.10-rc2]` entry below; this GA release is the first
cut that actually ships the feature code. Both `v0.2.10-rc1` and
`v0.2.10-rc2` were tagged against commits that carried only the version
bump and release notes, so the rc tarballs on TestPyPI are effectively
empty. **Do not depend on `v0.2.10-rc1` or `v0.2.10-rc2`** — upgrade
directly from `0.2.9` to `0.2.10`.

### Packaging / Release

- Align package version, changelog, release notes, and publish workflow
  usage to the final `0.2.10` GA line
- Bundle the ACP embedding facade, `/crystallize refine`, skill draft
  helpers, and the associated tests/docs into the GA commit so the
  sdist/wheel actually contains the advertised feature set

### Documentation

- Add `docs/releases/v0.2.10.md`
- Update `docs/guides/acp.md` version examples from `0.2.9` to `0.2.10`

## [0.2.10-rc2] — 2026-04-15

Re-cut of `0.2.10-rc1`. `rc1` failed the CI tag-vs-package version
consistency check because the `v0.2.10-rc1` tag was pushed against a
commit where `agentao/__init__.py` still reported `0.2.9`. `rc2` carries
the identical feature set with the version string aligned to the tag.

> **Note:** Neither `rc1` nor `rc2` actually shipped the feature code
> described below — both tags pointed at docs-only commits. The feature
> set first ships in the GA `0.2.10` release above.

Prerelease focused on two initiatives: promoting `agentao.acp_client` as a
stable **embedding facade for non-interactive runtimes**, and adding an
explicit **`/crystallize refine` stage** to the skill-crystallization flow.

### Added

- **ACP embedding facade** (`agentao/acp_client/`): non-interactive
  `send_prompt(..., interactive=False)` plus a one-shot `prompt_once(...)`
  entry point for daemon/workflow runtimes. Ephemeral clients created by
  `prompt_once` are tracked separately from durable `_clients` and do not
  appear in `get_status()`.
- **Structured client-side error taxonomy** (`AcpErrorCode`, `AcpClientError`,
  `AcpRpcError`, `AcpInteractionRequiredError`): embedding callers can branch
  on failure category without string matching. `AcpRpcError` preserves the
  raw JSON-RPC numeric `rpc_code` alongside the structured classification.
- **`HANDSHAKE_FAIL` classification**: `initialize` / `session/new` failures
  are re-labelled on both the `connect_server()` path and the ephemeral
  `prompt_once()` path, including RPC errors, so embedders can distinguish
  startup failures from in-session RPC failures uniformly.
- **Per-call `cwd` and `mcp_servers` session reuse**: a mismatch on either
  field triggers a fresh session; otherwise sessions are reused per named
  server under a strict single-active-turn contract.
- **`/crystallize refine`** (`agentao/cli/commands_ext.py`,
  `agentao/skills/drafts.py`): three-stage workflow
  `suggest -> refine -> create`, where `refine` re-runs the draft through
  the bundled `skill-creator` guidance. `suggest` now persists drafts under
  `.agentao/skill-drafts/` so `refine`/`create` can pick them up across
  turns.
- **Skill draft helpers** (`agentao/skills/drafts.py`): `new_draft`,
  `save_skill_draft`, `load_skill_draft`, `clear_skill_draft` with
  session-scoped paths and graceful handling of missing or malformed state.

### Fixed

- **`stop_all()` closes ephemeral clients** — in-flight `prompt_once()`
  callers previously blocked until their request timeout when the manager
  was shut down mid-call; ephemeral slots now receive the synthetic
  transport-closed signal alongside durable clients.
- **`load_skill_draft()` tolerates non-object JSON** — a corrupted draft
  file containing `[]` or a bare string no longer crashes
  `/crystallize status|refine|create`; the helper now returns `None` for
  any non-dict payload.
- **`/crystallize suggest` degrades when the draft directory is not
  writable** — the generated `SKILL.md` is still displayed, the save
  failure is surfaced as a warning, and the user is pointed at
  `/crystallize create [name]` instead of aborting the command.

### Tests

- New `tests/test_acp_client_embedding.py` covering non-interactive
  `send_prompt`, `prompt_once`, session reuse, ephemeral lifecycle,
  cancellation precedence, and handshake error classification.
- New `tests/test_skill_drafts.py` covering draft persistence, session
  scoping, corrupt-file tolerance, and path selection.
- Updated `tests/test_acp_client_cli.py`, `tests/test_acp_client_jsonrpc.py`,
  `tests/test_crystallizer.py`, `tests/test_reliability_prompt.py` for the
  new surfaces.

### Documentation

- Add `docs/guides/acp-embedding.md` (embedding facade overview)
- Add `docs/history/implementation/acp-embedding-implementation-plan.md`
- Add `docs/history/implementation/skill-crystallize-refinement-plan.md`
- Add `docs/history/kanban-acp-embedded-client-issue.md` (design parent doc)
- Add `docs/releases/v0.2.10-rc2.md`

## [0.2.9] — 2026-04-11

Small GA follow-up to `0.2.8` with three independently useful fixes on top
of the ACP client subsystem and the default-model rollout.

### Added

- **Explicit `@server` routing for the ACP client** (`agentao/acp_client/router.py`,
  `agentao/cli/app.py::_try_acp_explicit_route`) — `@server-name <task>`,
  `server-name: <task>`, and `让 / 请 server-name <task>` forms route
  deterministically to the named ACP server from the main CLI input. Longest-first
  name matching handles overlapping names (`qa` vs `qa.bot`). High-confidence shapes
  (`@…`, `让 …`, `请 …`) consume the turn when config is unavailable so delegation
  intent never silently falls back to the main agent; ambiguous colon-prefix shapes
  fall through so `Note:` / `url:` prose is never hijacked. ACP config is re-stat'd
  by mtime each attempt, so new/renamed servers are picked up without a CLI restart.
- **`$VAR` / `${VAR}` expansion in `AcpServerConfig.env`** — API keys and tokens
  can live in `.env` or the shell environment instead of being pasted into
  `.agentao/acp.json`.

### Fixed

- **ACP stdio is now forced to UTF-8 with `errors="strict"` before the server starts**
  (`agentao/acp/__main__.py`). Non-UTF-8 default encodings silently corrupt the
  JSON-RPC stream; the entry point now reconfigures stdin/stdout/stderr, verifies the
  result, and exits with a diagnostic on stderr if the streams cannot be made safe.
- **Default-model messaging realigned with the runtime** across the init wizard,
  `.env.example`, `README.md`, and `README.zh.md`. `LLMClient._PROVIDER_DEFAULT_MODELS`
  is the canonical source; surfaces previously suggested `gpt-5.4` / `gemini-2.0-flash`
  / `claude-opus-4-6`, contradicting the actual defaults (`gpt-5.4`,
  `gemini-flash-latest`, `claude-sonnet-4-6`, `deepseek-chat`). Unknown-provider
  fallback in `LLMClient.__init__` also returns `gpt-5.4` now instead of `gpt-5.4`.

### Documentation

- Add `docs/releases/v0.2.9.md`
- Update `docs/guides/acp.md` version examples from `0.2.8` to `0.2.9`

## [0.2.8] — 2026-04-11

Promotes `0.2.8-rc1` to general availability.

The substantive Added / Changed / Tests breakdown for the ACP client and CLI
refactor remains in the `[0.2.8-rc1]` entry below. The final 0.2.8 release
locks down release-facing metadata and documentation so the package version,
Git tag, release notes, and maintainer workflow all agree on the GA path.

### Packaging / Release

- Align package version, changelog, release notes, and publish workflow usage
  to the final `0.2.8` release line
- Document a maintainer smoke path (`uv run python -m pytest tests/`,
  `uv build`, `uv run twine check dist/*`) that runs tests, builds
  sdist/wheel, and validates metadata
- Add `build` and `twine` to the dev dependency group so release checks can be
  reproduced from a local source checkout

### Documentation

- Update `.env.example`, quickstart guides, and README snippets to reflect the
  current default model line (`gpt-5.4` / `gpt-5.4` examples) instead of stale
  `gpt-4-turbo-preview` examples
- Add final release notes at `docs/releases/v0.2.8.md`
- Update `docs/guides/acp.md` version examples from `0.2.8-rc1` to `0.2.8`

## [0.2.8-rc1] — 2026-04-11

Headline: **ACP Client for project-local server management** — Agentao can
now act as an ACP client, connecting to and managing external ACP-compatible
agent processes configured per-project. The old monolithic CLI is refactored
into a modular `agentao/cli/` package for maintainability.

Release intent: **prerelease / TestPyPI path**. Use tag `v0.2.8-rc1` and a
GitHub pre-release so `.github/workflows/publish-testpypi.yml` runs instead
of the full PyPI publish workflow.

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
- **`docs/guides/acp-client.md`** — full configuration reference,
  lifecycle, interaction bridge protocol, diagnostics, and troubleshooting

### Changed

- **CLI refactored from monolith to package** — the old single-file CLI (3 246
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
- Tool system: `read_file`, `write_file`, `replace`, `glob`, `grep`, `run_shell_command`, `web_fetch`, `web_search`, `save_memory`
- Skills system — auto-discovery from `skills/` with YAML frontmatter
- `AGENTAO.md` auto-loading for project-specific instructions
- Current date injected as `<system-reminder>`
- Complete LLM interaction logging to `agentao.log`
