# Session Replay

Session Replay captures the runtime timeline of an Agentao session — turns,
tool calls, permission decisions, streaming chunks, errors — as an
append-only JSONL file under `.agentao/replays/`. It is separate from
`save_session` / `load_session`: sessions restore the conversation you
can continue; replays record *what the agent did* for debugging, audit,
and future protocol replay.

## Enabling recording

Recording is **disabled by default**. Turn it on with either:

```bash
/replay on        # writes replay.enabled=true into .agentao/settings.json
```

or by editing `.agentao/settings.json` directly:

```json
{
  "mode": "workspace-write",
  "replay": {
    "enabled": true,
    "max_instances": 20
  }
}
```

- `replay.enabled` — boolean master switch.
- `replay.max_instances` — retention cap under `.agentao/replays/`. Defaults
  to 20. Does not affect `.agentao/sessions/`.

Toggling recording takes effect on the **next** session; the currently
running instance is not touched. Use `/replay off` to stop recording
future sessions without deleting existing files.

## File layout

- Directory: `.agentao/replays/`
- Filename: `<session_id>.<instance_id>.jsonl`
- First line: a `replay_header` event carrying `schema_version`,
  `session_id`, `instance_id`, and `created_at`.
- Each subsequent line: one JSON object, one event.

A new `instance_id` is minted for each session birth — so `/clear`,
`/new`, and ACP `session/load` each produce a fresh file. Replay files
never span across those boundaries.

## Inspecting replays

```bash
/replay list           # list recorded instances (newest first)
/replay show <id>      # render events for one instance in sequence
/replay tail <id> [n]  # show the last N events (default 20)
/replay prune          # delete instances above replay.max_instances
```

`<id>` can be a full `session_id.instance_id` or any unambiguous prefix
(an `instance_id` is unique enough in practice). Readers stop at the
current end of the file — there is no follow / live-tail mode; live
observation remains the job of normal output and `agentao.log`.

## What is recorded

Each `turn` corresponds to one `Agentao.chat()` call. Inside that turn
you will see, typically in this order:

| Kind | When |
|------|------|
| `turn_started` / `user_message` | Start of the turn |
| `assistant_thought_chunk` | Reasoning / thinking output |
| `tool_confirmation_requested` / `tool_confirmation_resolved` | When a permission prompt was shown |
| `tool_started` / `tool_output_chunk` / `tool_completed` | Per tool call |
| `assistant_text_chunk` | Streaming assistant text |
| `subagent_started` / `subagent_completed` | Nested agent invocations (new `turn_id`, back-pointer via `parent_turn_id`) |
| `turn_completed` | Final message, status (`ok` / `cancelled`), optional `final_text` |

Built-in and MCP tools share the same event kinds; `tool_source` on
`tool_started` differentiates them (`builtin` vs `mcp`).

Explicitly reserved but **not emitted in v1**:

- `session_saved` — reserved for a future explicit save entrypoint. The
  auto-save triggered by `/clear`, `/new`, or process exit does not emit
  this event.

## Privacy and truncation

- v1 does not do full secrets scanning. Every payload passes through a
  field-level sanitizer that coerces values to JSON-native types; a
  field that cannot be serialized is dropped individually and the event
  keeps a `redacted: "filter_error"` marker plus `redacted_fields`.
- `tool_output_chunk` payloads over the per-event cap are kept as
  head+tail excerpts with `truncated`, `original_chars`, and
  `omitted_chars` metadata. `assistant_text_chunk` is not truncated by
  default.

## Failure model

- A broken `.agentao/settings.json` falls back to safe defaults rather
  than blocking startup.
- Sanitizer or write failures are logged (warning level) and never
  raise. The runtime is unaffected by replay bookkeeping.
- If the process crashes mid-write, the final JSONL line may be
  partial. The reader skips it and still returns earlier events.

## Relationship to other surfaces

- `save_session` / `load_session` continue to own conversation
  persistence and resume.
- ACP `session/load` reuses the logical `session_id` but creates a new
  replay instance file rather than appending to the old one.
- v1 does not add an ACP `session/replay` method or any protocol-level
  replay contract. Any future protocol surface must read from
  `ReplayReader` rather than reconstructing from `messages`.

See [`../implementation/SESSION_REPLAY_PLAN.md`](../implementation/SESSION_REPLAY_PLAN.md)
for the full design plan and roadmap.
