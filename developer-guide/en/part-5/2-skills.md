# 5.2 Skills & Plugin Directories

**Skills are not code — they are Markdown instructions for the LLM.** When "make the agent follow our company's conventions" needs new constraints or workflows (not new capabilities), a skill beats writing a tool by 10x.

## Skill shape

Every skill is a directory:

```
my-skill/
├── SKILL.md              # required: entry file
└── reference/            # optional: load-on-demand docs
    ├── conventions.md
    └── templates.md
```

### `SKILL.md` format

```markdown
---
name: customer-ticket-handler
description: Handle customer tickets — enforces the refund policy, uses approved wording, queries the order before replying.
---

# Customer Ticket Handling

When the user's ticket asks about refunds / complaints / delivery:

## Steps

1. Query the order with `get_customer_orders`
2. If status is "delivered" and older than 30 days → politely decline
3. If it's a shipping issue → use template + escalate to logistics
4. ...

## Tone constraints

- Do not commit to specific compensation amounts
- Do not mention internal system names
- Sign off as "Customer Support Team"

## Edge cases

For exceptions, consult `reference/conventions.md`.
```

### YAML frontmatter

Two fields:

| Field | Required | Purpose |
|-------|----------|---------|
| `name` | ✅ | Unique id; no spaces, kebab-case recommended |
| `description` | ✅ | **Trigger description** — tells the LLM when to activate this skill |

The `description` is injected into the system prompt's skills catalog. When the LLM sees the user's request match that description, it calls `activate_skill` itself.

## Three-layer directory search

Source: `agentao/skills/manager.py:27-43`

```
1. ~/.agentao/skills/             ← global (shared across projects)
2. <cwd>/.agentao/skills/         ← project config dir
3. <cwd>/skills/                  ← project repo dir
```

**Later layers override earlier ones** (`.agentao/skills/` overrides global, `skills/` overrides both). So:

- **When shipping with your product**: place skills in your repo's `skills/` — users get them on clone
- **User-customizable**: leave `~/.agentao/skills/` for user-authored skills
- **Ephemeral/test**: use `.agentao/skills/` for project-local skills you don't want in git

### Remote installs

The CLI can install managed skills from GitHub:

```bash
agentao skill install owner/repo[:path][@ref]
```

If `:path` is present, Agentao installs that repository subdirectory as a single
skill package. For example, Anthropic's official PDF skill lives inside a larger
repository:

```bash
agentao skill install anthropics/skills:skills/pdf
```

Project-scoped installs go to `<project-root>/.agentao/skills/`; global installs
use `~/.agentao/skills/`. The repo-level `skills/` directory still has the
highest priority, so product-shipped skills can override managed installs.

### Multi-tenant isolation

In ACP / multi-instance Python, each agent's `working_directory` determines which project-level skills it sees — tenants stay isolated.

```python
# tenant-a sees only skills under /data/tenant-a
agent_a = Agentao(working_directory=Path("/data/tenant-a"))
# tenant-b sees only /data/tenant-b
agent_b = Agentao(working_directory=Path("/data/tenant-b"))
```

But **`~/.agentao/skills/` is process-global** (user HOME). If you don't want it loaded, change HOME before constructing the agent or use `SkillManager(skills_dir=...)` with a private path.

## Activation flow

Skills are **not passive** — they are not in the system prompt by default. Only when the LLM calls `activate_skill` is the full `SKILL.md` body injected.

```
┌─────────────────┐
│ available_skills │  ← all skills (LLM sees only name + description)
└─────────────────┘
         │ LLM decides: "I need customer-ticket-handler here"
         ▼
   activate_skill("customer-ticket-handler")
         │
         ▼
┌─────────────────┐
│  active_skills   │  ← activated skills (full body injected)
└─────────────────┘
```

This design lets you **stack many skills** without bloating context: only the ones actually used cost tokens.

## On-demand references

`reference/*.md` is not auto-loaded. Inside the skill body you can say: "If you hit a special case, `read_file skills/my-skill/reference/edge-cases.md`." Benefits:

- The main SKILL.md stays < 2 KB, easy for the LLM to absorb
- Extra docs load **only when needed**, saving tokens
- Complex knowledge can be decomposed by topic

## Three rules for a good skill

### 1. Be specific in the trigger

❌ `description: "Handles customer issues"` — too broad, LLM activates everywhere
✅ `description: "Activate when user asks about refunds, returns, or delivery issues. Not for sales questions."` — clear when to use and when not

### 2. Use imperatives and concrete steps

❌ `"We generally prefer JSON responses..."` — "generally" is noise
✅ `"When using get_customer_orders, always filter by customer_id. Never include internal tenant_id in user-facing replies."` — verifiable

### 3. List the don'ts first

Start the skill with what the LLM must **not** do, then what it should:

```markdown
## Hard rules (never)
- Never query across tenants
- Never commit to specific refund timelines
- Never expose internal API endpoints

## Correct approach
- ...
```

LLMs are more reliable at obeying prohibitions than inferring best practices.

## Example: "Code Review" skill for an engineering team

```
skills/code-review/
├── SKILL.md
└── reference/
    ├── security-checklist.md
    └── performance-patterns.md
```

**SKILL.md**:

```markdown
---
name: code-review
description: Activate before reviewing any pull request or diff. Enforces our team's review standards for Python / TypeScript code.
---

# Code Review Standards

## Your role

You are a principal engineer doing a pre-merge review. Be direct, be specific, cite line numbers.

## Review order

1. **Security first**: check `reference/security-checklist.md`
2. **Correctness**: edge cases, error paths, async correctness
3. **Performance**: N+1 queries, unbounded loops, memory leaks
4. **Style**: conforms to existing patterns in the same file — do not impose
   new abstractions unless justified

## Output format

For each finding, produce exactly:

- **Severity** (Blocker / Major / Minor / Nit)
- **File:line**
- **The issue** (one sentence)
- **Suggested fix** (code or concrete action)

End with one paragraph on overall quality and merge readiness.

## Hard rules

- Never say "looks good to me" without reading every changed file
- Never suggest refactors outside the diff unless they are Blockers
- Never be vague ("consider refactoring") — always name the concrete issue
```

## Debugging: is the skill actually in the prompt?

```python
agent = Agentao(working_directory=Path.cwd())
print(list(agent.skill_manager.available_skills.keys()))   # all discovered
print(list(agent.skill_manager.active_skills.keys()))       # currently active

# Activate manually (normally the LLM does this)
agent.skill_manager.activate_skill("customer-ticket-handler")

# See what gets injected
print(agent.skill_manager.get_skills_context())
```

## Skill vs Tool: when to add which

| Case | Pick |
|------|------|
| "Always use Python 3.12 type hints" | **Skill** (style constraint) |
| "Query the database" | **Tool** (capability) |
| "Use the template for refund replies" | **Skill** |
| "Send a Slack message" | **Tool** |
| "Always ask for clarification before acting" | **Skill** |
| "Upload a file to S3" | **Tool** |

**Slogan**: new *ability* → Tool; new *constraint* → Skill.

→ Next: [5.3 MCP Integration](./3-mcp)
