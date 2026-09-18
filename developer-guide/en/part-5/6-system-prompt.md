# 5.6 System Prompt Customization

> **What you'll learn**
> - The 16 blocks the agent's instructions are composed from, and which of the
>   two messages each one rides
> - Which 3 you actually own (AGENTAO.md, skills, custom Tool descriptions)
> - Which ones are runtime-injected and not safe to override

The agent's instructions are **rebuilt every `chat()` turn**, not a static string, and they arrive in **two** messages: a stable system message, and a volatile `user` tail assembled per request and never persisted. This section walks through the stitched blocks — where each comes from, which you can customize, and which you shouldn't touch.

## System prompt structure

Two messages carry the instructions, not one. The **system message** is the
stable half; the volatile half rides a trailing `user` message rebuilt on every
request and never written into the transcript.

```
┌──────────────────────────────────────────────┐
│ System message — stable for a whole session   │
│                                              │
│  1. Project instructions (AGENTAO.md)        │  ← you write
│  2. Identity / capability description        │  ← fixed
│  3. Reliability principles                   │  ← fixed
│  4. Task classification                      │  ← fixed
│  5. Execution protocol                       │  ← fixed
│  6. Completion standard                      │  ← fixed
│  7. Untrusted-input boundary                 │  ← fixed
│  8. Operational guidelines                   │  ← fixed
│  9. Reasoning directive (if thinking)        │  ← conditional
│ 10. Available sub-agents                     │  ← fixed list
│ 11. <memory-stable> stable memories          │  ← slow-changing
│     === the cacheable prefix ends here ===   │
└──────────────────────────────────────────────┘
┌──────────────────────────────────────────────┐
│ …conversation history…                        │
│ this turn's user message (persisted):        │
│   <system-reminder>                          │  ← per turn
│   Current Date/Time: 2026-04-16 15:30        │
│   </system-reminder>                         │
│   <the text you passed to chat()>            │
└──────────────────────────────────────────────┘
┌──────────────────────────────────────────────┐
│ Volatile tail — one user message, request-only│
│   <system-reminder>                          │
│ 12. Available skills catalog                 │  ← changes on activation
│ 13. Active skills full text                  │  ← changes on activation
│ 14. Current todos                            │  ← dynamic, per request
│ 15. <memory-context> dynamic recall          │  ← per turn
│ 16. Plan-mode prompt (conditional)           │  ← conditional
│   </system-reminder>                         │
└──────────────────────────────────────────────┘
```

**The tail is request-only.** It is assembled for one outgoing request and is
never appended to `agent.messages`, so it does not reach the session file, the
replay record or compaction. Do not copy the date/time reminder's pattern for
new volatile content: that one *is* persisted, and a persisted tail would pile
one todos snapshot into the transcript per turn.

Blocks 12–16 left the system message in 0.4.26 (stage 0a of
`docs/design/llm-api-adapters.md` §2.3). They were the reason the "stable"
prefix was not actually stable: `<memory-context>` is query-specific, so it
changed every turn, and since `messages[0]` is the head of the provider's
cached prefix, that invalidated the cache covering the **whole history** every
turn. One side effect worth knowing: because the tail is rebuilt per *request*
rather than per turn, a `todo_write` the model makes in one tool iteration is
visible to the next one.

## Three injection points you own

### 1. `AGENTAO.md` — project instructions

Place it at the root of `working_directory`. Loaded automatically on construction.

```markdown
# Project

## Stack
- Python 3.12 + FastAPI + Pydantic v2
- Frontend: Next.js 14 App Router + shadcn/ui

## Code conventions
- Ruff + black, line length 100
- async functions never use threading; for CPU-bound use `asyncio.to_thread`
- New endpoints must include an OpenAPI docstring

## Hard constraints
- Never cross-query tenants (every endpoint must have a tenant_id guard)
- DB schema changes must go through Alembic migrations
- Do not use `datetime.now()` directly; use `app.utils.time.now()` (UTC + tenant TZ aware)
```

**Best practices**:

- Keep it to **hard constraints** and **project facts** (not operational how-to)
- 500–1500 words — longer pushes out other blocks
- Commit to git so the team shares
- Use `##` sections so the LLM absorbs structure

### 2. Skills — on-demand long docs

Need more than 1500 words of guidance? Split into a Skill ([5.2](./2-skills)). Full body is only injected when activated.

### 3. Memory `<memory-stable>` — user-level persistent

Good for "cross-project, user-stable" facts:

```python
# The LLM auto-saves during conversation
save_memory("user-profile", "Senior Python dev, prefers tabs, UTC+8 Shanghai")
```

Injected in the stable block on every subsequent session.

## What you can't (and shouldn't) customize

| Block | Why |
|-------|-----|
| Agent capability description | Governs how the agent uses tools, thinks |
| Reliability / operational rules | Core Agentao quality |
| Sub-agent / skills catalog | Reflects registration state, not static text |

If you want to **drastically reshape** agent behavior (remove a capability, impose a totally different mission), there's no public API. **Recommended pattern**: layer via AGENTAO.md + skills — don't try to replace.

## Inspect the system prompt

```python
# Right after construction
agent = Agentao(working_directory=Path.cwd())
print(agent._build_system_prompt())   # the stable system message
print(agent._build_volatile_tail())   # the request-only tail ("" when empty)
```

⚠️ Both are private; not guaranteed stable. Debug only. `_build_system_prompt()`
returns the stable half **only** — if you are looking for todos, the skills
catalog or dynamic recall, they are in the tail.

In production, log the **character length** as a metric:

```python
sp = agent._build_system_prompt()
logger.info("system_prompt_chars", extra={"len": len(sp)})
```

A bloated prompt will:

- Eat useful context
- Increase per-turn cost
- Degrade cache hit rate (if too much dynamic content after the cache prefix)

## Prompt Cache tactics

The system message is byte-identical across the turns of a session, so vendor
prompt caches can reuse it — and, more importantly, reuse the history behind it.

### What goes in the stable prefix

The whole system message (blocks 1–11), and then the conversation history
itself. The prefix a provider can reuse ends at the last message that has not
changed since the previous request.

### What breaks the cache

- Mutating `AGENTAO.md` between turns — it is block 1, so this invalidates
  everything.
- A `save_memory` that lands in `<memory-stable>` (block 11).
- A model or endpoint switch (agentao drops its own token anchor there too).
- **Not** todos, skills, recall or the plan prompt: since 0.4.26 those live in
  the request-only tail, *after* the history, so changing them costs one
  uncached tail and nothing else.

The trade: the tail is re-sent in full on every request. In this repo that is
about 1.8k tokens, nearly all of it the available-skills catalog, against a
2.3k-token cached system message. Measure it for your own deployment — a
project with no skills on disk has a tail of a few dozen tokens.

### Explicit breakpoints (opt-in)

For an endpoint that honours Anthropic-style `cache_control` over the ordinary
Chat Completions wire, set `LLM_PROMPT_CACHE=anthropic` (optionally
`LLM_PROMPT_CACHE_TTL=1h`). agentao then marks at most three breakpoints per
agent-turn request — the system message, the last tool definition, and the end
of stable history — and leaves the fourth slot for the endpoint's own automatic
caching.

Off by default, and deliberately not inferred from your base URL or model name:
agentao verified that the OpenAI SDK forwards the key unchanged, not that your
gateway honours it. Verify your endpoint, then turn it on. See
`docs/reference/configuration.md` §2.

### Monitor cache hit rate

On OpenAI: the response includes `usage.prompt_tokens_details.cached_tokens`.
Ideal case: from turn 2 onward, the system message and everything but the newest
messages and the tail is cached.

## Different AGENTAO.md per business line

Multi-tenant / multi-product: each `working_directory` can have a **different** `AGENTAO.md`:

```
/data/tenants/acme-corp/
├── AGENTAO.md           ← acme's conventions
└── .agentao/

/data/tenants/globex/
├── AGENTAO.md           ← globex's conventions
└── .agentao/
```

This is the **cleanest** way to do per-tenant customization — no code branching, just directory layout.

## Dynamic AGENTAO.md

Some facts are per-session (subscription tier, language, region). Write `AGENTAO.md` into the session's working directory before constructing the agent:

```python
def prepare_workdir(tenant, user) -> Path:
    workdir = Path(f"/tmp/session-{user.id}")
    workdir.mkdir(exist_ok=True)
    (workdir / "AGENTAO.md").write_text(f"""
# User Context

- Tenant: {tenant.name} ({tenant.plan})
- User: {user.name}, role: {user.role}, locale: {user.locale}
- Today: {datetime.now().isoformat()}
- Current feature: {user.current_feature}

## Allowed actions
{format_allowed_actions(tenant.plan)}
""")
    return workdir

agent = Agentao(working_directory=prepare_workdir(tenant, user))
```

Now the agent's system prompt is **tailored to this session**.

## "But I really want to replace the whole prompt"

No public API. You can subclass `Agentao` and override the private method:

```python
from agentao import Agentao

class MyAgentao(Agentao):
    def _build_system_prompt(self) -> str:
        parent = super()._build_system_prompt()
        return "# Your company's top-level charter\n\n...\n\n" + parent

agent = MyAgentao(working_directory=Path.cwd())
```

⚠️ Relies on a private method name; retest on every version upgrade. **Prefer AGENTAO.md + skills**.

## ⚠️ Common pitfalls

::: warning Don't ship without these
- ❌ **Oversized AGENTAO.md** — long preambles dilute the model's attention to the actual user message
- ❌ **Shared AGENTAO.md across sessions** — one tenant's rules apply to another
- ❌ **Sensitive info in AGENTAO.md** — gets shipped to every LLM call (and possibly logged)

Each pitfall below has the full fix.
:::

### ❌ Oversized AGENTAO.md

2000+ words eats too much context. Move operational how-to into skills; keep AGENTAO.md for hard constraints + key facts.

### ❌ Shared AGENTAO.md across sessions

If all agents point to the same `working_directory`, they share AGENTAO.md — but you may want per-tenant customization. **Per-session working_directory** is the clean answer.

### ❌ Sensitive info in AGENTAO.md

AGENTAO.md is a project file — it may land in git, in memory, or in logs. **Never** include API keys, real credentials, or customer PII.

---

**End of Part 5.** You now have the full toolkit to teach the agent your business: tools, skills, MCP, permissions, memory, system prompt. Next: safely running all of this in production.

## TL;DR

- The instructions are **rebuilt every turn** — never assume they're a static string you can cache.
- They arrive in **two messages**: a stable system message (blocks 1–11) and a request-only volatile tail (blocks 12–16, one `user` message that never enters the transcript).
- You own 3 blocks: **`AGENTAO.md`** (project hard rules), **skill bodies** (activated knowledge), **custom Tool descriptions** (when/how to call).
- The rest — date, working directory, available tools/skills catalog, memory recall, todos, etc. — are runtime-injected and should not be overridden.
- Keep `AGENTAO.md` short and absolute ("never run X", "always use Y format") — long preambles dilute attention.

→ [Part 6 · Security & Production](/en/part-6/)
