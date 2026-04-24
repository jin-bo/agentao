# 5.6 System Prompt Customization

The agent's system prompt is **rebuilt every `chat()` turn**, not a static string. This section walks through the 11 stitched blocks — where each comes from, which you can customize, and which you shouldn't touch.

## System prompt structure

Source: `agentao/prompts/builder.py::SystemPromptBuilder.build()` (composition), invoked via the `agentao/agent.py::_build_system_prompt()` facade.

```
┌────────────────────────────────────────────┐
│ System prompt                               │
│                                            │
│ ┌─────────────────────────────────────┐    │
│ │ 1. Project instructions (AGENTAO.md)│    │  ← you write
│ ├─────────────────────────────────────┤    │
│ │ 2. Agent capability description     │    │  ← fixed
│ ├─────────────────────────────────────┤    │
│ │ 3. Reliability principles           │    │  ← fixed
│ ├─────────────────────────────────────┤    │
│ │ 4. Operational rules (tone, tools)  │    │  ← fixed
│ ├─────────────────────────────────────┤    │
│ │ 5. Reasoning directive (if thinking)│    │  ← conditional
│ ├─────────────────────────────────────┤    │
│ │ 6. Available sub-agents             │    │  ← fixed list
│ ├─────────────────────────────────────┤    │
│ │ === STABLE PREFIX ENDS (cacheable) =│    │
│ ├─────────────────────────────────────┤    │
│ │ 7. Available skills catalog         │    │  ← changes on activation
│ ├─────────────────────────────────────┤    │
│ │ 8. Active skills full text          │    │  ← changes on activation
│ ├─────────────────────────────────────┤    │
│ │ 9. Current todos                    │    │  ← dynamic
│ ├─────────────────────────────────────┤    │
│ │ 10. <memory-stable> stable memories │    │  ← slow-changing
│ ├─────────────────────────────────────┤    │
│ │ 11. <memory-context> dynamic recall │    │  ← per-turn
│ ├─────────────────────────────────────┤    │
│ │ 12. Plan-mode suffix (conditional)  │    │  ← conditional
│ └─────────────────────────────────────┘    │
│                                            │
│ ┌─────────────────────────────────────┐    │
│ │ <system-reminder>                   │    │  ← per-turn
│ │ Current Date/Time: 2026-04-16 15:30 │    │
│ │ </system-reminder>                  │    │
│ └─────────────────────────────────────┘    │
└────────────────────────────────────────────┘
```

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
print(agent._build_system_prompt())
```

⚠️ `_build_system_prompt()` is private; not guaranteed stable. Debug only.

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

Agentao splits the prompt into "stable prefix + dynamic suffix" — the prefix is identical across turns, so vendor prompt caches **cut cost and latency significantly**.

### What goes in the stable prefix

Blocks 1–6 (AGENTAO.md, capability, rules, reasoning, sub-agents).

### What breaks the cache

- Mutating `AGENTAO.md` between turns
- Switching activated skills (block 8 moves — the cache invalidates for that suffix, but blocks 1-6 stay cached — that's fine)
- Adding todos / memories (blocks 9-11) **does not** break the prefix cache — they live after it

### Monitor cache hit rate

On OpenAI: response includes `usage.prompt_tokens_details.cached_tokens`. Ideal case: from turn 2 onward most of the system prompt is cached.

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

## Common pitfalls

### ❌ Oversized AGENTAO.md

2000+ words eats too much context. Move operational how-to into skills; keep AGENTAO.md for hard constraints + key facts.

### ❌ Shared AGENTAO.md across sessions

If all agents point to the same `working_directory`, they share AGENTAO.md — but you may want per-tenant customization. **Per-session working_directory** is the clean answer.

### ❌ Sensitive info in AGENTAO.md

AGENTAO.md is a project file — it may land in git, in memory, or in logs. **Never** include API keys, real credentials, or customer PII.

---

**End of Part 5.** You now have the full toolkit to teach the agent your business: tools, skills, MCP, permissions, memory, system prompt. Next: safely running all of this in production.

→ [Part 6 · Security & Sandbox](/en/part-6/) (coming soon)
