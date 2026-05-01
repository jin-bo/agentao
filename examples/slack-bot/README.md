# Slack · channel-scoped Agentao bot

A `slack-bolt` app where each `app_mention` runs one Agentao turn and
the reply is posted as a thread reply. The channel id selects a
permission preset: an explicit allowlist gets workspace-write, every
other channel falls back to read-only.

## Run (with real Slack creds)

```bash
cd examples/slack-bot
uv sync
SLACK_BOT_TOKEN=xoxb-... \
SLACK_APP_TOKEN=xapp-... \
OPENAI_API_KEY=sk-... \
uv run python -m slack_bolt.adapter.socket_mode -- src.bot:build_app
```

(or wire it into an HTTP receiver — `build_app` returns a configured
`AsyncApp`.)

## Smoke test (no Slack, no API key)

```bash
cd examples/slack-bot
uv sync --extra dev
uv run pytest tests/ -v
```

The tests exercise `handle_mention` with a recording `say` callable
and a fake LLM. The HTTP stack is never started.

## What this demonstrates

- **Per-mention agent** — fresh `Agentao` per inbound mention; nothing
  shared between channels or threads.
- **Channel-scoped permissions** — `make_permission_engine_for_channel`
  returns a fresh `PermissionEngine` whose `active_mode` reflects the
  channel allowlist. The engine is injected via
  `Agentao(permission_engine=...)` so policy is data, not code.
- **`llm_client_factory` seam** — production reads from env, smoke
  tests inject a `MagicMock`. No conditional code paths.

## Not included

- Multi-workspace support (the allowlist is a constant; production
  reads it from a per-workspace settings store).
- Streaming the reply chunk-by-chunk (Slack supports `chat.update` for
  this; the example posts once at the end).
- Rate limiting / retry on Slack 429s.
