# Replay schema versioning policy

This document defines how the JSON Schema files under `schemas/` evolve.
It exists so that "what does `SCHEMA_VERSION = "1.2"` mean?" has a
machine-checkable answer instead of living in a dataclass docstring.

## Source of truth

```
agentao/replay/events.py    # EventKind vocabulary + ReplayEvent envelope
agentao/replay/schema.py    # emitter that turns those into JSON Schema
schemas/replay-event-*.json # generated artefact, committed to the repo
```

Code is canonical. The schema file is a generated artifact and must
never be hand-edited — `scripts/write_replay_schema.py --check` runs in
CI and fails on drift.

## What `SCHEMA_VERSION` covers

The `SCHEMA_VERSION` constant in `agentao/replay/events.py` describes
the **on-disk JSONL format**: envelope shape + kind vocabulary +
per-kind payload contract (the third tier is being introduced
incrementally). Every change to that on-disk format must bump the
version under the rules below.

## Compatibility rules

### Minor bump (`1.x` -> `1.(x+1)`)

Allowed without notice:

- Adding a new event kind to `EventKind`.
- Adding a new **optional** field to the envelope (rare; envelope is
  intentionally narrow).
- Adding a new optional field to a kind's payload schema once that
  payload is modelled.
- Tightening a previously-permissive payload schema in a way that every
  existing emitter already satisfies (i.e. discovering that a field was
  always present and marking it required).

A 1.0 reader **may** see kinds it does not recognise from a 1.1+ writer
and **must** skip them rather than error. This is the forward-compat
contract.

### Major bump (`1.x` -> `2.0`)

Required when:

- Removing or renaming an envelope field, or changing its type.
- Removing or renaming an existing event kind. (Replacing one with
  another is a remove + add; both halves are major.)
- Changing the meaning of an existing field (e.g. switching `ts` from
  ISO-8601 to epoch seconds).
- Tightening a payload schema in a way that existing on-disk replays
  would fail validation.

Major bumps freeze the previous schema file. `schemas/replay-event-1.0.json`,
`schemas/replay-event-1.1.json`, and `schemas/replay-event-1.2.json` each
keep validating every replay file written under their respective minor,
indefinitely.

### Deprecation

A kind or field is deprecated by:

1. Marking it `"deprecated": true` in the schema (JSON Schema 2019-09+).
2. Adding a one-line note in `EventKind` and the policy changelog below.
3. Continuing to emit it for at least one minor cycle so readers can
   adapt.

Removal is a major bump; deprecation alone never is.

## Unknown-fields policy

| Layer    | Policy                              | Why                                                                  |
|----------|-------------------------------------|----------------------------------------------------------------------|
| Envelope | `additionalProperties: false`       | The envelope is small and shared. Surprises here mean a bug.         |
| `kind`   | `enum` of the version's vocabulary  | Discriminator must be exhaustive for `oneOf` to type-check cleanly.  |
| Payload  | mixed — see below                   | v1.2 starts the per-kind tightening; everything else stays lenient.  |

Per-kind payload policy:

- **v1.2 harness-projected kinds** (`tool_lifecycle`,
  `subagent_lifecycle`, `permission_decision`) — payload is a typed
  schema derived from the public Pydantic model in
  `agentao.harness.models` (`extra="forbid"`, so
  `additionalProperties: false`), extended with the sanitizer's
  optional projection metadata (`redaction_hits`, `redacted`,
  `redacted_fields`). A model field rename / removal therefore
  surfaces as schema drift in CI rather than silently producing
  replays that fail downstream validators.
- **All other kinds** — payload is `additionalProperties: true`. Long-tail
  diagnostic kinds may stay lenient; protocol-shaped kinds
  (everything that another runtime might re-emit) follow the v1.2
  pattern when they are tightened.

## Backward-compatibility guarantees

- A replay file written under `SCHEMA_VERSION = X` validates against
  `schemas/replay-event-X.json` forever.
- The reader at `SCHEMA_VERSION = X` accepts any file that validates
  against `schemas/replay-event-Y.json` for `Y <= X` within the same
  major.
- Cross-major reads are best-effort: the reader may opt to load a 1.x
  file under a 2.x reader, but is not required to.

The fixture suite in `tests/test_replay_schema.py` enforces the first
guarantee for every committed schema version. New major versions must
add a fresh fixture before merge.

## Regeneration

```bash
# Rewrite schemas/ from agentao/replay/events.py
uv run python scripts/write_replay_schema.py

# CI invocation (fails on drift)
uv run python scripts/write_replay_schema.py --check
```

The script is intentionally tiny: anything more than "render and write"
belongs in `agentao/replay/schema.py` so it is unit-testable.

## Changelog

- **1.0** (initial) — envelope + 17 kinds covering session, turn, user,
  assistant, tool confirm/start/output/complete, subagent, error.
- **1.1** — adds `replay_footer` plus 21 kinds covering tool_result,
  llm_call_*, ask_user_*, background_notification_injected,
  context_compressed, session_summary_written, skill_*, memory_*,
  model_changed, permission_mode_changed, readonly_mode_changed,
  plugin_hook_fired, session_loaded, session_forked. Backward-compatible
  with 1.0 — every 1.0 kind survives.
- **1.2** — adds three harness-projected lifecycle kinds so embedded
  hosts have a single audit artifact instead of two parallel streams
  (`Agentao.events()` + replay JSONL): `tool_lifecycle`,
  `subagent_lifecycle`, `permission_decision`. Each new variant carries
  a **typed payload** generated from the matching public Pydantic model
  (`ToolLifecycleEvent` / `SubagentLifecycleEvent` /
  `PermissionDecisionEvent`) in `agentao.harness.models`, extended with
  the sanitizer's optional projection metadata
  (`redaction_hits`, `redacted`, `redacted_fields` — see
  `agentao.replay.sanitize.SANITIZER_INJECTED_FIELDS`, which is the
  single source of truth shared with the schema generator). Forward
  projection (`HarnessReplaySink`) and reverse projection
  (`replay_payload_to_harness_event`) live in
  `agentao.harness.replay_projection`; `start_replay()` auto-attaches
  the sink so every published harness event also lands in the JSONL.
  Backward-compatible with 1.1 — every 1.1 kind survives, and a 1.0 /
  1.1 reader treats the three new kinds as unknown and skips them.
