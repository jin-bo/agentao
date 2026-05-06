# 7. Context & Status

`/context` shows token usage and lets you raise or lower the limit. `/status` is a one-screen snapshot of session state. Together they're your dashboard.

`/status` was already covered in [1. Getting Started](./1-getting-started#status-what-state-am-i-in); this page focuses on `/context` and the broader question of "why is my session getting expensive."

## `/context` — token-budget dashboard

```text
> /context

Context Window Status:
  Estimated tokens: 47,231
  Max tokens:       200,000
  Usage:            23.6%
  Messages:         54
  Compact failures: 0/3
  Last compact:     2026-04-30 14:08:12  82,419 → 31,204 tokens | 21 summarized, 18 kept
  Re-injected files: src/auth.py, tests/test_auth.py
```

| Field | Meaning |
|-------|---------|
| Estimated tokens | Approximate token count of the conversation as it would be sent now |
| Max tokens | Configured upper bound (default 200,000) |
| Usage | `Estimated / Max`. Color: green (<55%), yellow (<65%), red (>=65%) |
| Messages | Number of messages in `agent.messages` |
| Compact failures | How many times the auto-compactor has failed in this session, out of the circuit-breaker limit. Hits the limit → auto-compact disables for safety. |
| Last compact | When auto-compaction last ran; pre/post token counts; how many messages were summarized vs kept |
| Re-injected files | Files re-attached to context after compaction (kept "live" because the agent recently read them) |

::: tip Why the color thresholds are below 100%
The bar isn't "you crashed at 100%". Auto-compaction triggers well before the model's hard limit because part of the budget is reserved for the next response and tool outputs. By the time you're at 65%+ red, compaction is imminent.
:::

## `/context limit <n>` — change the budget

```text
> /context limit 100000
Context limit set to 100,000 tokens

> /context limit 500000
Context limit set to 500,000 tokens
```

What it does:

- Sets `context_manager.max_tokens` for **this session**
- Affects when auto-compaction triggers (compaction fires before approaching this number)
- Resets on restart — to make it persistent, set the `AGENTAO_CONTEXT_TOKENS` environment variable (see [10. Configuration Reference](./10-config-reference))

Minimum: 1,000. Below that the CLI refuses.

When to lower:
- You want compaction to kick in earlier (cheaper turns at the cost of more summarization)
- You're using a smaller model with a smaller real context window than 200K

When to raise:
- You're on a 1M-context model and want fewer compactions
- You're running long, file-heavy plan sessions where the model genuinely needs more state

## What auto-compaction actually does

When `/context` usage approaches the configured limit, the context manager:

1. Picks an older block of messages from the conversation
2. Asks the LLM to summarize them into a `[Conversation Summary]` block
3. Replaces those messages with the summary in `agent.messages`
4. Keeps the most recent N messages and the in-progress tool loop intact
5. Re-injects file contents the agent recently read (the `Re-injected files` line)
6. Writes the summary to the `session_summaries` table (see [6. Memory](./6-memory))

The summary lives both in the live message history (so the next turn sees it) and in the DB (so future sessions can reference it via memory).

The "circuit breaker" is a safety: if compaction itself fails (LLM timeout, parse error) more than `CIRCUIT_BREAKER_LIMIT` times in a row, auto-compaction disables for the rest of the session — better to refuse a turn than spiral.

## `/status` quick-reference (full content in chapter 1)

```text
> /status
```

| Line | Action it suggests |
|------|--------------------|
| Conversation summary shows huge message count | Consider `/clear` to rotate |
| Permission Mode is `full-access` | Consider stepping back to `workspace-write` |
| Loaded sources lists unexpected paths | Audit `~/.agentao/permissions.json` and the built-in preset |
| Markdown rendering OFF and you wanted it ON | `/markdown` toggles |
| Task List shows pending items | Agent has open todos — ask it to continue |
| ACP servers `0/N running` | Servers crashed or never started — `/acp status` to investigate |

## Combining the two: triage flow

When something feels off, run both:

```text
> /status      # see what's loaded and how
> /context     # see how much you're spending
```

| Symptom | First check |
|---------|-------------|
| Each turn is slow | `/context` — usage % and last compact time |
| Bills spiking | `/context` for tokens, `/status` for active skills (each adds prompt size) |
| Agent forgot something obvious | `/memory status` (chapter 6) — recall errors > 0? |
| Tool calls keep failing | `/status` permission mode + `/mcp` / `/acp` (chapter 8) |

## Pitfalls

- **`Estimated tokens` is approximate** — the manager uses a heuristic per character, not the model's tokenizer. Real OpenAI/Anthropic counts can be 5–15% off. Use it as a trend, not a precise gauge.
- **Compaction is lossy** — anything not in the summary or in re-injected files is gone from the agent's perspective. If the agent suddenly "forgets" something specific, check `Last compact` — it may have been summarized.
- **Lowering `limit` mid-session can trigger immediate compaction** — if you set a limit below current usage, the next turn will compact aggressively. Sometimes desirable, sometimes surprising.
- **Re-injected files reflect recency, not importance** — if a critical file hasn't been touched in a while, it may not survive compaction. To force preservation, ask the agent to read it again.

## Where to go next

| Want to… | Read |
|----------|------|
| Inspect what memory is bloating context | [6. Memory](./6-memory) → `/memory status` |
| Tune compaction thresholds in config | [10. Configuration Reference](./10-config-reference) |
| Understand the embedded compaction API | [Part 4 · Event Layer](/en/part-4/) |

---

::: info Where this fits
The context manager is `agent.context_manager`. Embedding hosts can read `cm.get_usage_stats(agent.messages)` to power a host-side "context bar" UI, or call `cm.compact()` directly to force a compaction. The auto-compaction trigger is the same in both paths.
:::

::: tip Authoritative help
Command syntax: `/help`. `/context` body: [`agentao/cli/commands.py:handle_context_command`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/commands.py). Compaction logic: [`agentao/context_manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/context_manager.py).
:::
