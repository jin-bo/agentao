# Session Replay Plan

## Summary

Add a `Session Replay` capability to Agentao that records and replays the actual runtime process of a session, separate from `save_session/load_session`. The v1 design uses runtime events as the source of truth, persists replay data as append-only JSONL, does not provide a UI, does not re-execute tools, and does not change the semantics of `session/load`.

This version also locks two implementation constraints:

- replay recording must have a configuration switch
- the existing `.agentao/settings.json` remains JSON; replay settings are added there rather than migrating to YAML

Key principles:

- separate "resume session" from "replay process"
- replay truth comes from runtime events, not `messages` or `agentao.log`
- each replay file represents one interpretable session lifecycle instance
- v1 recording is disabled by default and must be explicitly enabled
- ACP gets no new method in v1; storage and reader behavior come first

## Goals

- Capture a structured, append-only event timeline for debugging, audit, and failure replay.
- Keep replay storage independent from session checkpoints and textual logs.
- Support offline inspection through a reader and minimal CLI commands.
- Preserve room for future ACP replay support without committing to a wire API in v1.

## Non-Goals

- No terminal or IDE replay UI in v1.
- No tool re-execution or behavioral simulation.
- No attempt to reconstruct replay from `messages` or `agentao.log`.
- No ACP `session/replay` method in v1.
- No migration of settings storage from JSON to YAML.

## Configuration And Settings

Continue using `.agentao/settings.json`.

Rationale:

- the current settings file is primarily machine-managed state
- the CLI already reads and writes it via `_load_settings()` / `_save_settings()`
- v1 should not introduce a second config format or a JSON to YAML migration path

Add replay settings under the existing file:

```json
{
  "mode": "workspace-write",
  "replay": {
    "enabled": false,
    "max_instances": 20
  }
}
```

Semantics:

- `replay.enabled`
  - `true`: create a recorder for new replay instances and persist events
  - `false`: do not create a recorder and do not write replay files
- `replay.max_instances`
  - maximum replay instances retained under `.agentao/replays/`
  - affects replay files only
  - does not affect `.agentao/sessions/`

Defaults:

- `replay.enabled = false`
- `replay.max_instances = 20`

Scope:

- v1 supports project-local persisted settings only
- no environment-variable override
- no ACP override parameter
- replay readers and `/replays` commands may still inspect historical files when recording is disabled

## Replay Data Model And File Format

Add a dedicated replay module with:

- `ReplayRecorder`
- `ReplayReader`
- `ReplayMeta`
- `ReplayRetentionPolicy`

Storage path:

- `.agentao/replays/<session_id>.<instance_id>.jsonl`

Filename rules:

- `session_id` identifies the logical session
- `instance_id` identifies one interpretable replay lifecycle instance
- a new session, a post-`/clear` session, and an ACP `session/load` continuation all create a new `instance_id`

File format:

- first line is always a `replay_header` event
- each following line is one JSON object representing a `ReplayEvent`
- all fields must stay JSON-native and serializable without Python-specific types

Common event fields:

- `event_id`
- `session_id`
- `instance_id`
- `turn_id`
- `parent_turn_id`
- `seq`
- `ts`
- `kind`
- `payload`

Format rules:

- `schema_version` lives only in `replay_header.payload.schema_version`
- `seq` is allocated only by the unique `ReplayRecorder` for that replay instance
- `ReplayRecorder` owns the monotonic counter and a `threading.Lock`
- no other module may provide `seq`
- `ts` is always an ISO 8601 string
- `turn_id` may be null for session-level events only
- `parent_turn_id` is used only for sub-agent or nested workflow linkage

Suggested event kinds:

- `replay_header`
- `session_started`
- `turn_started`
- `user_message`
- `assistant_text_chunk`
- `assistant_thought_chunk`
- `tool_confirmation_requested`
- `tool_confirmation_resolved`
- `tool_started`
- `tool_output_chunk`
- `tool_completed`
- `subagent_started`
- `subagent_completed`
- `turn_completed`
- `error`
- `session_saved`
- `session_ended`

## Turn, Resume, And Session Boundaries

Turn semantics are fixed in v1:

- a `turn` is the full lifecycle of one `Agentao.chat()` call
- all assistant text chunks, thinking chunks, tool loops, and tool outputs inside that call share one `turn_id`
- sub-agents do not reuse the parent `turn_id`
- sub-agent events carry their own `turn_id` and point back with `parent_turn_id`

Session boundary rules:

- one replay file maps to exactly one `instance_id`
- `/clear` ends the current replay instance and must emit `session_ended`
- `/clear` then creates a new `session_id` and a new replay file
- ACP `session/load` may reuse an existing logical `session_id`, but it must create a new replay instance file rather than append to the old one
- replay files must never span across `/clear` or `session/load`

`session_saved` rules:

- the current repository does not expose a standalone `/save` or ACP `session/save`
- therefore v1 does not actually emit `session_saved`
- the event is reserved in the schema only
- if an explicit save entrypoint is added later, that path can begin emitting `session_saved`
- automatic saves triggered by `/clear`, `/new`, or process exit must not be represented as `session_saved`

## Runtime Integration

Use a single subscription seam plus a small set of lifecycle hooks rather than letting multiple layers write replay files directly.

Integration rules:

- the runtime continues to emit `AgentEvent`
- add a replay adapter that subscribes to `AgentEvent` and translates them into `ReplayEvent`
- `transport` continues to handle display and ACP mapping, not replay truth
- only session lifecycle events that exist outside the runtime event stream call the recorder directly:
  - session start
  - session end

Additional constraints:

- tool execution events that already flow through `AgentEvent` continue through the replay adapter
- if permission request or permission resolution currently lacks a complete runtime event, add a runtime event first and let replay observe that event
- CLI, ACP, and `ToolRunner` must not write replay files directly
- `ReplayRecorder` is instantiated only when `replay.enabled=true`; otherwise the replay path is a no-op

## Chunking, Completion State, And Payload Strategy

Text and output rules:

- keep `assistant_text_chunk` at true streaming granularity
- add `turn_completed.payload.final_text` with the final assistant text for fast display
- keep `assistant_thought_chunk` in its original streaming or paragraph-level shape
- keep `tool_output_chunk` as streamed output, subject to a per-event size limit
- `tool_completed` stores final state and any minimal summary, not the full duplicated output

Defaults:

- `text_chunk_mode = "stream"`
- v1 does not implement `final_only`
- readers and future UIs that want a compact summary should prefer `turn_completed.final_text`

Tool source strategy:

- built-in tools and MCP tools use the same event kinds: `tool_started`, `tool_output_chunk`, `tool_completed`
- payload adds `tool_source = "builtin" | "mcp"`

## Privacy, Truncation, And Failure Fallback

V1 does not attempt full secrets scanning, but the failure model must be deterministic.

Default rules:

- all event payloads pass through a field-level sanitizer
- if sanitization succeeds, the field is retained
- if sanitization fails for a field, drop that field rather than dropping the whole event
- add a marker such as:
  - `redacted: "filter_error"`
  - `redacted_fields: [...]`
- sanitization and replay-write failures must never break the main runtime path; they only log warnings

Truncation rules:

- `tool_output_chunk` has a per-event character cap
- oversized chunks keep head and tail and record:
  - `truncated: true`
  - `original_chars`
  - `omitted_chars`
- `assistant_text_chunk` is not truncated by default
- `turn_completed.final_text` should not carry redundant extra long-form duplication fields

## Reader And CLI Surface

Active-session behavior:

- `ReplayReader` in v1 reads until the current EOF and returns
- no follow or live-tail mode
- `/replays tail <current>` shows what is already flushed and exits
- live operational observation remains the job of existing real-time output and logs

Reader interfaces:

- `list_replays(project_root) -> list[ReplayMeta]`
- `open_replay(session_id, instance_id | None, project_root) -> ReplayReader`
- `ReplayReader.iter_events(kinds: set[str] | None = None, turn_id: str | None = None)`

`ReplayMeta` should include at least:

- `session_id`
- `instance_id`
- `path`
- `created_at`
- `updated_at`
- `event_count`
- `turn_count`
- `has_errors`

Reader robustness:

- if a crash leaves a partial final JSON line, skip that final line and log a warning
- one malformed line must not abort reading the whole replay

CLI v1 commands:

- `/replays`
- `/replays show <id>`
- `/replays tail <id> [n]`
- `/replays prune`
- `/replay on`
- `/replay off`

Command semantics:

- read-only commands do not execute tools
- replay listing is separate from `/sessions`
- `show` renders in `seq` order
- `tail` slices by event count only
- `<id>` must resolve uniquely to a replay instance, not just a logical `session_id`
- `/replay on|off` updates `.agentao/settings.json` under `replay.enabled`
- toggling recording affects only future replay instances, not the currently-running instance

## Retention

Retention rules:

- v1 keeps the most recent `N` replay instances, default `N = 20`
- prune runs best-effort after a new replay instance is created or an instance ends
- `/replays prune` provides a manual cleanup path
- retention deletes replay files only and must not touch `.agentao/sessions/`

When recording is disabled:

- no new replay file is created
- old replay files remain readable
- `/replays prune` can still clean up old files manually

## ACP And Existing Surface Boundaries

The following behaviors remain unchanged:

- `save_session/load_session` continue to handle conversation-state persistence and resume
- `session/load` continues to replay historical messages for continuation
- `agentao.log` remains a textual debug log

Replay is responsible for:

- structured runtime timelines
- serving as the future source of truth for any ACP replay feature

ACP constraints for v1:

- do not add `session/replay`
- do not add ACP parameters for replay overrides
- replay reader outputs must already be easy to serialize into future protocol payloads
- if ACP replay is added later, it must read from `ReplayReader`, not reconstruct from `messages`

## Documentation Requirements

Implementation of this feature should include documentation updates at the appropriate layer.

Required docs updates:

- keep this plan current while the feature is being implemented
- update `docs/README.md` so the implementation note remains discoverable
- if CLI commands or user-visible settings ship, add or update the relevant user-facing docs under `docs/`

`developer-guide/` updates are conditional rather than mandatory in v1:

- if no ACP method, protocol field, or external replay contract is added, no `developer-guide` update is required
- if a future iteration adds ACP replay APIs, protocol payloads, or externally-consumed replay schemas, update the matching `developer-guide` protocol and config docs in the same change

## Test Plan

Must cover:

- when `replay.enabled=false`, no recorder is created and no replay file is written
- when `replay.enabled=true`, a new session creates a replay file
- `/replay on` and `/replay off` correctly update `.agentao/settings.json`
- missing, corrupt, or partial settings still fall back to stable defaults
- a tool-free turn emits the expected events and a correct `turn_completed.final_text`
- multi-chunk assistant output preserves chunk ordering and final text
- multiple tools in one chat call share one `turn_id`
- tool confirmation request and allow or reject outcomes are recorded correctly
- tool failure, cancellation, and readonly deny states are represented correctly
- MCP and built-in tools share event kinds but differ by `tool_source`
- sub-agents get their own `turn_id` and point back with `parent_turn_id`
- `/clear` emits `session_ended` and starts a new replay file
- ACP `session/load` with the same logical `session_id` still creates a new replay instance file
- `session_saved` is not emitted in v1
- replay write failures and sanitizer failures do not break normal runtime behavior
- large tool output is truncated with the correct metadata
- active replay reads stop at EOF rather than following
- partial final JSON lines are skipped without losing earlier events
- retention only removes replay files and leaves saved sessions intact
- `/replays`, `show`, `tail`, and `prune` behave correctly in empty, single-file, and multi-file cases

## Assumptions

- v1 is for debugging and audit, not a user-facing replay product
- one `ReplayRecorder` exists per replay instance and its lifecycle matches the replay file lifecycle
- `replay_header` is the first JSONL event and owns schema metadata
- `turn = chat() call` is a fixed v1 rule
- recording is disabled by default and must be explicitly enabled through `.agentao/settings.json` or `/replay on`
- `.agentao/settings.json` remains JSON rather than migrating to YAML
- v1 provides only minimal CLI control and read paths, not ACP replay methods or advanced querying
