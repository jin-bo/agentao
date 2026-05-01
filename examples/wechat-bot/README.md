# WeChat · contact-scoped Agentao bot

A polling daemon where each inbound WeChat message runs one Agentao
turn and the reply is sent back to the same contact. Contact id (or
group room id) selects a permission preset: an explicit allowlist
gets workspace-write, every other contact falls back to read-only.

Inspired by [`Wechat-ggGitHub/wechat-claude-code`](https://github.com/Wechat-ggGitHub/wechat-claude-code) — same
shape, only the transport changes.

## Run (bring your own WeChat client)

```bash
cd examples/wechat-bot
uv sync
OPENAI_API_KEY=sk-... uv run python -c "
import asyncio
from src.bot import run_polling_loop
from your_wechat_client import IlinkClient   # ilink, wechaty, itchat, …
asyncio.run(run_polling_loop(IlinkClient()))
"
```

`WeChatClient` is a Protocol — any object exposing
`async fetch_messages()` and `async send_message(contact_id=, text=)`
slots in. The example is client-agnostic on purpose.

## Smoke test (no WeChat, no API key)

```bash
cd examples/wechat-bot
uv sync --extra dev
uv run pytest tests/ -v
```

The tests exercise `handle_message` and `run_polling_loop` with a
recording client and a fake LLM.

## What this demonstrates

- **Per-message agent** — fresh `Agentao` per inbound message.
- **Contact-scoped permissions** — `make_permission_engine_for_contact`
  returns a fresh `PermissionEngine` whose `active_mode` reflects the
  allowlist, injected via `Agentao(permission_engine=...)`.
- **`llm_client_factory` seam** — production reads env, tests inject
  a `MagicMock`. No conditional code paths.
- **Transport-agnostic** — the Protocol lets you wire ilink, wechaty,
  itchat, or custom HTTP without touching the bot logic. Streaming,
  QR-code login, and rate-limit retry belong in the client.
