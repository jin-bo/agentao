# 7.6 Blueprint F · WeChat Intelligent Bot (ilink-style)

::: tip ⚡ Runnable end-to-end
**Outcome** — a long-polling daemon that runs one Agentao turn per inbound WeChat message (DM or group) and posts the reply back to the same contact. The contact id selects a permission preset (allowlist → workspace-write; everyone else → read-only).
**Stack** — Python · `asyncio` long-poll · bring-your-own ilink / wechaty / itchat client behind a `WeChatClient` Protocol · fresh `Agentao` per message · `llm_client_factory` test seam.
**Source** — [`examples/wechat-bot/`](https://github.com/jin-bo/agentao/tree/main/examples/wechat-bot)
**Run** — `uv sync`, then feed the daemon any client speaking the ilink-style protocol.
:::

**Scenario**: you already have a personal WeChat account (a phone, or a server running an ilink / wechaty / itchat bridge) and you want to add "natural-language conversation + tool execution" to it. **This is *not* the Official Account / Enterprise WeChat platform** — that path uses an official webhook (signature verification, 5-second ACK, 48-hour customer-service window) and is not what this blueprint covers. We're building a personal-account bot that long-polls a local/remote bot HTTP API, modeled after [`Wechat-ggGitHub/wechat-claude-code`](https://github.com/Wechat-ggGitHub/wechat-claude-code) (same shape, TypeScript with the Claude Code SDK; this is the Python + Agentao version).

## Who & why

- **Product shape**: single-process long-polling daemon; no public ingress required
- **Users**: yourself, your team, or members of a group chat — the "bot" feels like a WeChat friend that does work
- **Pain**: the Official Account platform is heavyweight (ICP filing, business verification, 5-second ACK, 48h window). A personal-account bot is simply "an assistant glued to your WeChat" — read messages in, run an agent, send the reply back

## ilink-style ≠ Official Account webhook

::: warning Don't pick the wrong rail
The two tracks are **architecturally very different**. Don't conflate them:

| Dimension | ilink-style personal-account bot (this blueprint) | Official Account / Enterprise WeChat |
|-----------|---------------------------------------------------|---------------------------------------|
| Trigger | Daemon **long-polls** the bot's HTTP endpoint | WeChat servers **call back** your public webhook |
| 5-second window | **None** — you pull, you reply when you're done | Yes; must ACK first, push asynchronously |
| Signature / AES | Client-dependent; usually plain | Required; AES-CBC in secure mode |
| 48-hour customer-service window | **None** | Yes |
| User ID | wxid (DM) / `<id>@chatroom` (group) | OpenID / UnionID |
| ICP filing / verification | Not required | Required |

If you want the **former** (assistant glued to your own account, internal tools, team automation), keep reading.
If you want the **latter** (consumer-facing brand Official Account, customer service), this blueprint doesn't apply — consult the WeChat Official Platform docs.
:::

## Architecture

```
WeChat phone / bridge client (ilink, wechaty, itchat, …)
       │ exposes a bot API (HTTP / ws)
       ▼
Python daemon (single asyncio process)
       │
       ├─ run_polling_loop(WeChatClient)
       │    while not stop:
       │      msgs = await client.fetch_messages()
       │      for m in msgs:
       │          await handle_message(...)
       │
       └─ handle_message(text, contact_id, send)
            ├─ tempdir = mkdtemp("agentao-wechat-")
            ├─ engine = make_permission_engine_for_contact(contact_id)
            │     allowlist hit → WORKSPACE_WRITE
            │     otherwise    → READ_ONLY
            ├─ agent = Agentao(working_directory=tempdir,
            │                  llm_client=llm_client_factory(),
            │                  permission_engine=engine)
            ├─ reply = await agent.arun(text)
            ├─ agent.close() + rmtree(tempdir)
            └─ await send(contact_id=contact_id, text=reply)
```

## Key code

> All snippets come from [`examples/wechat-bot/src/bot.py`](https://github.com/jin-bo/agentao/tree/main/examples/wechat-bot/src/bot.py) — the whole module is under 170 lines.

### 1 · The client Protocol — fits ilink, wechaty, itchat alike

```python
class WeChatMessage(Protocol):
    text: str
    contact_id: str          # wxid or <id>@chatroom
    message_id: str

class WeChatClient(Protocol):
    async def fetch_messages(self) -> list[WeChatMessage]: ...
    async def send_message(self, *, contact_id: str, text: str) -> None: ...
```

The single most copy-worthy idea: **bot logic only ever talks to the Protocol**. Use ilink today, swap to wechaty tomorrow, hand-roll an HTTP-hooks client next week — **the daemon doesn't change a line**. Streaming previews, QR-code login, rate-limit retry, reconnect — all transport concerns — **stay in the concrete client**.

### 2 · Contact → permission mode

```python
WRITE_ALLOWLIST_CONTACTS: frozenset[str] = frozenset(
    {"wxid_owner_self", "ROOM_devops@chatroom"}
)

def make_permission_engine_for_contact(
    contact_id: str, *, project_root: Path
) -> PermissionEngine:
    engine = PermissionEngine(project_root=project_root)
    mode = (
        PermissionMode.WORKSPACE_WRITE
        if contact_id in WRITE_ALLOWLIST_CONTACTS
        else PermissionMode.READ_ONLY
    )
    engine.set_mode(mode)
    return engine
```

DM wxids and group `@chatroom` ids share the same field, so they can live in the **same allowlist**. In production, load the allowlist from config / a database — never hard-code.

### 3 · One message → one turn

```python
async def handle_message(
    *,
    text: str,
    contact_id: str,
    send: Callable[..., Awaitable[Any]],
    llm_client_factory: Callable[[], LLMClient] = make_llm_client,
) -> str:
    work_dir = Path(tempfile.mkdtemp(prefix="agentao-wechat-"))
    agent = Agentao(
        working_directory=work_dir,
        llm_client=llm_client_factory(),
        permission_engine=make_permission_engine_for_contact(
            contact_id, project_root=work_dir
        ),
    )
    try:
        reply = await agent.arun(text)
    finally:
        agent.close()
        shutil.rmtree(work_dir, ignore_errors=True)   # close() does NOT delete tempdir
    await send(contact_id=contact_id, text=reply)
    return reply
```

Trade-offs baked in:

- **Fresh agent per message** — simple, well-isolated; for higher throughput, pool by `contact_id`
- **Tempdir must be `rmtree`d explicitly** — `agent.close()` releases handles but leaves the directory; without this the daemon leaks one tempdir per inbound message
- **`llm_client_factory` is the test seam** — production reads env, smoke tests inject a `MagicMock`; no `if testing` branches anywhere

### 4 · The long-poll loop

```python
async def run_polling_loop(
    client: WeChatClient,
    *,
    poll_interval_s: float = 1.0,
    stop_event: Optional[asyncio.Event] = None,
    llm_client_factory: Callable[[], LLMClient] = make_llm_client,
) -> None:
    stop = stop_event or asyncio.Event()
    while not stop.is_set():
        messages = await client.fetch_messages()
        for msg in messages:
            await handle_message(
                text=msg.text,
                contact_id=msg.contact_id,
                send=client.send_message,
                llm_client_factory=llm_client_factory,
            )
        if stop.is_set():
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval_s)
        except asyncio.TimeoutError:
            continue
```

`stop_event` is the graceful-shutdown hook — tests fire it from a `FakeWeChatClient` once its queue drains so the loop exits cleanly. In production, wire it to SIGTERM.

## Offline smoke test — runs without WeChat

The other point worth stealing from this example: **`llm_client_factory` + the client Protocol** make the entire pipeline runnable in CI with zero external dependencies.

```python
# tests/test_smoke.py (excerpt)
class _FakeWeChatClient:
    """In-memory client: yields a queued batch, then sets stop_event to exit."""
    async def fetch_messages(self) -> list[_Msg]:
        if self._queued:
            batch, self._queued = self._queued, []
            return batch
        self._stop.set()
        return []
    async def send_message(self, *, contact_id: str, text: str) -> None:
        self.sent.append({"contact_id": contact_id, "text": text})

async def test_run_polling_loop_processes_one_batch_then_exits() -> None:
    stop = asyncio.Event()
    client = _FakeWeChatClient(
        queued=[
            _Msg(text="ping",    contact_id="wxid_a", message_id="1"),
            _Msg(text="status?", contact_id="wxid_b", message_id="2"),
        ],
        stop=stop,
    )
    with patch("agentao.agent.Agentao._llm_call",
               lambda self, msgs, tools, token: _fake_response("ok")):
        await run_polling_loop(client, stop_event=stop, llm_client_factory=_fake_llm)
    assert client.sent == [
        {"contact_id": "wxid_a", "text": "ok"},
        {"contact_id": "wxid_b", "text": "ok"},
    ]
```

```bash
cd examples/wechat-bot
uv sync --extra dev
uv run pytest tests/ -v   # no WeChat, no API key, no network
```

## Want streaming previews? Wire `Agentao.events()`

The reference repo (`wechat-claude-code`) streams partial LLM output back to the chat as a "typing…" preview. To do that on Agentao, replace the `agent.arun(text)` shot with a subscription to `agent.events()` and forward `LLM_TEXT` increments to `client.send_message` on a "every N chars / every M ms" cadence (see [§4 Event Streams](/en/part-4/)). Most ilink-style clients enforce a per-message rate limit and will throttle aggressive streaming — **1.5 seconds per chunk is a safe default**.

## ⚠️ Pitfalls

::: warning Day-2 bugs from real ilink-style WeChat bots
Each row below is a real production incident. Skim them before you ship — fixes are cheap *now* and expensive *later*.
:::

| Day-2 bug | Root cause | Fix |
|-----------|------------|-----|
| `/tmp` filled up | `agent.close()` doesn't remove the tempdir; one leak per message | Explicit `shutil.rmtree(work_dir, ignore_errors=True)` (already in the example) |
| Two replies in the same group come back out of order | Switching to parallel processing without serializing per `contact_id` | Bucket by `contact_id` and hold an `asyncio.Lock` per bucket |
| Group messages get replies even without `@bot` | Personal-account bots receive every group message by default | Filter `@<self>` either in the client adapter or at the top of `handle_message` |
| Identity spoofing | Granting `WORKSPACE_WRITE` based on `contact_id` alone | Add a second factor (passphrase, signed command envelope) — `contact_id` is a transport identifier, not an auth credential |
| 5,000-char LLM output blows past the per-message cap | ilink-style clients impose per-message limits (often 1024–4096 bytes) | Chunk on the way out of `handle_message`, or hard-cap output length in the skill |
| Group-chat flood (agent loops) | `max_iterations` unbounded; agent has a tool that itself sends messages | Hard-cap `agent.arun`; keep "outbound = daemon only" — don't give tools the ability to send messages |
| Bridge client gets banned / disconnected | `fetch_messages` returns 401 or a network error, not an empty batch | Loud alert + reconnect with backoff on exceptions; never `try/except: pass` |
| Test fixtures pass while production breaks | Protocol changed in tests but the real client wasn't updated | Add a contract test in `tests/` that pins the `fetch_messages` / `send_message` signatures |
| API key shows up in logs | `LLMClient` construction printed in trace | Apply the secrets scrubber from [6.5](/en/part-6/5-secrets-injection) |

## Going further: a long-lived agent per contact

The example **builds a fresh agent per message** — simple and easy to reclaim. If you need:

- multi-turn memory across messages
- a shared working directory (so the agent can edit a file in the third message that it created in the first)

… cache `_agents: dict[str, Agentao]` keyed by `contact_id`, **pair it with an `asyncio.Lock` per key** to serialize, and add LRU + idle timeout (e.g. close the agent and delete its working directory after 30 min of silence). This is the most common evolution from "per-message isolation" to "per-contact session" — but the **multi-tenant security calculus** changes too; see [§6.4 Multi-Tenant & FS](/en/part-6/4-multi-tenant-fs).

## Runnable code

The full project lives in-repo at [`examples/wechat-bot/`](https://github.com/jin-bo/agentao/tree/main/examples/wechat-bot):

```bash
cd examples/wechat-bot
uv sync --extra dev
uv run pytest tests/ -v          # offline smoke

# Real run (bring your own ilink / wechaty / itchat client)
OPENAI_API_KEY=sk-... uv run python -c "
import asyncio
from src.bot import run_polling_loop
from your_wechat_client import IlinkClient   # your concrete ilink client
asyncio.run(run_polling_loop(IlinkClient()))
"
```

---

## End of Part 7 — and of the guide's main arc

You now have:

- Two embedding paths ([Part 2](/en/part-2/) SDK, [Part 3](/en/part-3/) ACP)
- Event + UI integration ([Part 4](/en/part-4/))
- Six extension points ([Part 5](/en/part-5/))
- Security + production deployment ([Part 6](/en/part-6/))
- Six real-world blueprints (this part)

The appendices — full API reference, config key index, ACP message fields, error codes, migration notes, FAQ, glossary — are what you'll reach for as you build. They follow.

→ Appendices (coming soon)
