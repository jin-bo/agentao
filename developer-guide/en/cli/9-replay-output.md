# 9. Replay & Output

Three commands for capturing what the agent did and controlling what you see: `/replay`, `/copy`, `/markdown`.

## `/replay` — record, list, inspect, prune sessions

When recording is on, every event the agent emits (LLM responses, tool calls, tool results, permission decisions, memory writes, ...) is appended to a JSONL file under `.agentao/replays/`, named `<session_id>.<instance_id>.jsonl`. Each `/clear`, `/new` or session load starts a new instance, so one session can have several files. You can list, render, tail, and prune those files later.

### `/replay on` and `/replay off`

```text
> /replay on
Replay recording ON. (max_instances=20)
```

Persists to `.agentao/settings.json` so the next session starts with the same setting. Off by default — turn it on when you want a paper trail.

If `settings.json` exists but cannot be read as a JSON object, the command prints `Could not persist replay setting: …` and leaves the file unchanged, because rewriting it would drop your other settings. Fix the file, or move it aside, and run the command again.

### `/replay` and `/replay list` — list recordings

```text
> /replay
Replay recording: on  (max_instances=20)

Saved Replays (3):

  • 3f9c2a1b.7d41e0
    Refactor the auth module to use middleware…
    47 events · 8 turns  ⚠ has errors
    Created: 2026-04-30 14:08  Updated: 2026-04-30 14:42
    File: 3f9c2a1b-5e6d-4c3b-9a8f-0e1d2c3b4a59.7d41e0a9c2b4.jsonl

  • b81e44d0.c08b52
    Find the 3 largest files under cwd
    12 events · 2 turns
    Created: 2026-04-30 13:50  Updated: 2026-04-30 13:51
    File: b81e44d0-27c4-4f1a-8e2b-6a5d9c0f3e17.c08b52f1e3a7.jsonl
  ...

Usage: /replay show <id>  or  /replay tail <id> [n]  or  /replay prune
```

Each entry shows:
- Short ID: the first 8 characters of the session id, a dot, and the first 6 of the instance id. Every other subcommand takes it
- First user message preview
- Event / turn counts; `⚠ has errors` if any error events were captured
- Created / updated timestamps (local)
- Backing file name

Newest first.

### `/replay show <id>` — full render

```text
> /replay show 3f9c2a1b.7d41e0
```

Renders all events in the replay, grouped by turn. The default view groups related events (`tool_started` + `tool_completed` together, etc.); add flags to slice differently:

| Flag | Effect |
|------|--------|
| `--raw` | Flat chronological view, no grouping |
| `--turn <tid>` | Show only one turn |
| `--kind <kind>` | Filter by event-kind prefix (`tool_` matches `tool_started`, `tool_result`, ...) |
| `--errors` | Show only events flagged as errors |

`<id>` is a **prefix** of the short ID, the full `session.instance` id, the session id or the instance id. `/replay show 3f9c` works if it's unambiguous; if several replays match, the CLI prints the matches and asks you to disambiguate. An exact match on any of those ids always wins.

### `/replay tail <id> [n]` — last N events

```text
> /replay tail 3f9c2a1b.7d41e0 30
```

Flat view of the last `n` events (default 20). Useful when a long replay finished badly and you only care about the tail.

### `/replay prune` — clean up old replays

```text
> /replay prune
Pruned 5 replay(s) beyond max_instances=20.
```

Deletes the oldest replays beyond `replay.max_instances` (configurable in `.agentao/settings.json`). Doesn't ask for confirmation — it's bounded and safe.

### `/replay delete <id>` and `/replay delete all`

```text
> /replay delete 3f9c2a1b.7d41e0
Deleted replay 3f9c2a1b.7d41e0 (3f9c2a1b-5e6d-4c3b-9a8f-0e1d2c3b4a59.7d41e0a9c2b4.jsonl).

> /replay delete all
Are you sure? This deletes all replays except the active one. [y/N]: y
Deleted 18 replays. (Skipped: 1 active)
```

`delete <id>` removes one specific replay (prefix matching, like `show`). `delete all` wipes every replay file *except* the one being recorded right now (so you don't shoot the active session in the foot). Confirmation required for `all`.

## When to use replays

| Situation | What replays give you |
|-----------|----------------------|
| Bug report — "the agent did something weird" | Exact event log including tool args and results |
| Cost analysis — "where did the tokens go" | Token counts on each `llm_call_completed` event (`--kind llm_call`) |
| Debugging a custom plugin | The plugin's hook decisions are recorded as events |
| Audit / compliance | A JSONL trail per session, with credential-shaped strings redacted |
| Checking a run in a test | `agentao.replay.ReplayReader` loads the file. A replay records a run. It does not re-execute one |

## Pitfalls

- **Recording is off by default for performance** — turn on only when needed; long sessions can produce big JSONL files
- **`max_instances` evicts the oldest files automatically** — when a recording starts and when it ends. `/replay prune` runs the same eviction on demand
- **Short IDs are prefixes of the full IDs** — two replays can share one, so scripts should use the full `session.instance` id or the filename
- **`delete all` is genuinely destructive** — there's no replay-of-replays. Audit first if these are anything you might need

## `/copy` — copy the last assistant response

```text
> /copy
Copied last response to clipboard.
```

Copies the most recent assistant response (raw Markdown) to your system clipboard. Use when you want to paste the answer into a doc, ticket, or chat.

What gets copied:
- Just the last assistant message — not the whole conversation
- Markdown source, **not** the rendered version (so headings stay as `#`, code stays in fences)
- Excludes tool-use traces and reasoning summaries

If `/copy` says "nothing to copy", you've either just started a session or the last message wasn't an assistant response.

## `/markdown` — toggle rich rendering

```text
> /markdown
Markdown rendering: ON

> /markdown
Markdown rendering: OFF
```

Toggles whether assistant responses are rendered as Markdown (with bold, code blocks, headings, etc.) or printed as raw text.

When to turn off:
- You're piping CLI output to a file or downstream tool
- The Markdown rendering is mangling output (unusual but happens with weird Unicode)
- You want to see exactly what the LLM wrote, character by character

State is per-session; persists nowhere.

## Three commands working together

A common workflow:

1. `/replay on` — start recording
2. Do the work, watch the agent
3. `/copy` — grab the final answer for your PR / doc
4. `/replay list` — find the recording
5. `/replay show <id>` — review the full session offline
6. `/replay delete <id>` after you've extracted what you need

## Where to go next

| Want to… | Read |
|----------|------|
| Use replays as input for testing | [Part 4 · Event Layer](/en/part-4/) |
| Tune replay storage limits | [10. Configuration Reference](./10-config-reference) |
| Understand the event schema being recorded | [Part 4.2 · AgentEvent](/en/part-4/2-agent-events) |

---

::: info Where this fits
Replay is implemented in `agentao.replay` — embedding hosts can read the same JSONL files with `agentao.replay.ReplayReader`. The event schema is identical across CLI and embedded paths. In CI, read a replay with `ReplayReader` to assert what a run did. File layout, event kinds and redaction: [6.6 · Axis three: session replay](/en/part-6/6-observability#axis-three-session-replay).
:::

::: tip Authoritative help
Command syntax: `/help`. Behavior: [`agentao/cli/replay_commands.py`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/replay_commands.py). Render logic: [`agentao/cli/replay_render/`](https://github.com/jin-bo/agentao/tree/main/agentao/cli/replay_render). Storage: [`agentao/replay/`](https://github.com/jin-bo/agentao/blob/main/agentao/replay/).
:::
