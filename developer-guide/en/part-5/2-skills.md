# 5.2 Skills & Plugin Directories

> **What you'll learn**
> - The skill directory layout (`SKILL.md` + optional `references/*.md`)
> - The three-layer search (user / project-config / project-repo) and override semantics
> - How to write a skill the LLM activates at the right time — and only then

**Skills are not code — they are Markdown instructions for the LLM.** When "make the agent follow our company's conventions" needs new constraints or workflows (not new capabilities), a skill beats writing a tool by 10x.

## Skill shape

Every skill is a directory:

```
my-skill/
├── SKILL.md              # required: entry file
├── references/           # optional: load-on-demand docs (*.md)
│   ├── conventions.md
│   └── templates.md
├── assets/               # optional: more docs (*.md)
└── scripts/              # optional: helper scripts (any file)
```

A subdirectory without a `SKILL.md` is skipped. The `references/`, `assets/` and `scripts/` files are never inlined. Activation lists them by absolute path, and the model reads them with `read_file` when it needs them (see [On-demand references](#on-demand-references)).

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

For exceptions, consult `references/conventions.md`.
```

### YAML frontmatter

| Field | Required | Purpose |
|-------|----------|---------|
| `name` | ✅ | Unique id; no spaces, kebab-case recommended. Names are case-sensitive. |
| `description` | ✅ | **Trigger description** — tells the LLM when to activate this skill |
| `when-to-use` | — | Extra trigger hint. The catalogue prints it as `Activate when: …` under the description. |
| `license`, anything else | — | Kept in the skill's `frontmatter` dict and otherwise ignored |

The `description` is injected into the system prompt's skills catalog. When the LLM sees the user's request match that description, it calls `activate_skill` itself.

The local loader is lenient, so a broken skill fails quietly:

- A missing `name` falls back to the directory name.
- A skill with an empty `description` loads, and `/skills` lists it. The catalogue leaves it out, so the model never activates it.
- Malformed YAML counts as no frontmatter. The loader logs a warning with the file path to `agentao.log`. The usual cause is an unquoted colon: `description: Deploy to AWS: ECS` fails, `description: "Deploy to AWS: ECS"` works.
- A name that starts with `mcp:` is refused. That prefix is reserved for [MCP skills](/en/part-5/3-mcp#mcp-skills).

`agentao skill install` is strict: it refuses a package whose `SKILL.md` lacks `name` or `description` ([`skills/installer.py`](https://github.com/jin-bo/agentao/blob/main/agentao/skills/installer.py)).

## Three-layer directory search

```
1. ~/.agentao/skills/             ← global (shared across projects)
2. <cwd>/.agentao/skills/         ← project config dir
3. <cwd>/skills/                  ← project repo dir
```

**Later layers override earlier ones** (`.agentao/skills/` overrides global, `skills/` overrides both). On first run, Agentao copies its bundled skills (today only `skill-creator`) into `~/.agentao/skills/`. It never overwrites a copy that is already there. So:

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

But **`~/.agentao/skills/` is process-global** (user HOME). The path is fixed when `agentao.skills.manager` is imported, so changing `HOME` later has no effect. To keep it out, pass your own manager. `SkillManager(skills_dir=...)` scans only that one directory and skips the bundled-skill copy:

```python
from agentao.skills import SkillManager

agent = Agentao(
    working_directory=Path("/data/tenant-a"),
    skill_manager=SkillManager(skills_dir="/data/tenant-a/skills"),
)
```

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

Where each part goes in the request:

- **The catalogue** (name + description of every enabled skill) is in the system prompt, which stays byte-identical across turns so the provider's prompt cache holds. It lists active skills too, so activating a skill does not change it. It changes only when the enabled set changes: enable, disable, install or reload. It is rendered only if the agent has the `activate_skill` tool.
- **Active skill bodies** go into the per-request volatile tail, a trailing `<system-reminder>`. They are never stored in history. An active body is re-sent uncached on every request, so deactivate a skill when you are done with it.

See [`prompts/builder.py`](https://github.com/jin-bo/agentao/blob/main/agentao/prompts/builder.py) for the order of the sections.

## Disabling a skill

`activate_skill` is not the only gate. A skill can be **disabled** for the project, which is stronger than deactivating it:

```bash
/skills disable <name>     # persistent — survives restart
/skills enable <name>      # undo
/skills                    # list, with its own Disabled section
```

The state lives in `.agentao/skills_config.json` under `disabled_skills` (project scope only — see [Appendix B](/en/appendix/b-config-keys)). Three things matter when you embed:

- **Disable is not deactivate.** `/skills deactivate` drops a skill out of *this session's* `active_skills`; the skill is still discoverable and the model can activate it again. A disable means the skill does not load, does not appear in the catalogue, and is **refused at activation** — `activate_skill` answers `Unknown skill`, because to a caller trying to activate it a disabled skill is not there. `/skills activate` is the one surface that names the state and the remedy instead.
- **A sub-agent copies the disabled set when it starts.** A disable made while a sub-agent is running reaches the *next* sub-agent, not that one.
- **A scan never prunes the list.** A `/skills reload` that cannot see the skill — unmounted share, directory renamed mid-session — leaves the name in place and writes nothing, so the skill comes back still disabled. A scan cannot tell "deleted" from "not discoverable right now", and since a disable is an activation gate, guessing wrong would silently re-arm the skill for the model.

The cost of that last rule is that a name can outlive the skill it disabled. `/skills` lists those separately, under **Disabled, not found by the last scan**, and `/skills enable <name>` clears such a name whether or not the skill is currently present.

### How `skills_config.json` is written

The file is safe to hand-edit and safe to share between processes:

- Each `disable` / `enable` changes one name. It reads, changes and writes the file under one lock (`skills_config.json.lock`), then swaps a temp file into place.
- Every other disabled name and every other key in the file survives. This includes names that another Agentao process wrote after this one started.
- The write path parses strictly. Malformed JSON, a `disabled_skills` that is not an array, or a non-string entry refuses the write and names the path. Writing back a lenient reading would delete the names the file holds.
- The startup read is lenient. A damaged file logs a warning and loads as "nothing disabled", so the CLI still starts.

The host API is `skill_manager.disable_skill(name)` / `enable_skill(name)`. Both return the message string that the CLI prints. Code: [`skills/manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/skills/manager.py).

## On-demand references

`references/*.md` is not auto-loaded. Inside the skill body you can say: "If you hit a special case, read `references/edge-cases.md`."

Plain relative paths work from any working directory. On activation the model gets two things:

- **The skill directory**, with a note that relative paths in `SKILL.md` (such as `scripts/foo.py`) resolve against that directory, not the agent's working directory. Installed skills usually live under `~/.agentao/skills/` or `.agentao/skills/`, not in the cwd.
- **A list of resource files by absolute path**: `references/*.md`, `assets/*.md` and every file under `scripts/`. Hidden files and `__pycache__` are skipped. Each folder lists at most 50 files, plus a count of the rest.

So never hardcode absolute paths in a skill. Benefits:

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
└── references/
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

1. **Security first**: check `references/security-checklist.md`
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

# Activate manually (normally the LLM does this). The task text is required.
print(agent.skill_manager.activate_skill("customer-ticket-handler", "Answer ticket #4521"))

# See what the volatile tail gets
print(agent.skill_manager.get_skills_context())
```

### `SkillManager` API

| Method | Returns |
|--------|---------|
| `list_available_skills()` | Names of enabled skills |
| `list_all_skills()` | Names of all discovered skills, disabled ones included |
| `get_skill_info(name)` | Metadata dict (`name`, `title`, `description`, `path`, `frontmatter`, …) or `None` |
| `get_skill_content(name)` | Full `SKILL.md` text, or `None` |
| `activate_skill(name, task_description)` | The activation message. An unknown or disabled name returns `Error: Unknown skill …`. |
| `deactivate_skill(name)` | `True` if the skill was active |
| `get_skills_context()` | The active-skills block of the volatile tail |
| `reload_skills()` | Re-scans the three layers and returns the enabled count. Plugin and MCP skills are kept. |
| `disable_skill(name)` / `enable_skill(name)` | The message the CLI prints (see [above](#how-skills-config-json-is-written)) |

`available_skills` and `active_skills` are plain dicts keyed by name.

### Troubleshooting

| Symptom | Check |
|---------|-------|
| Missing from `/skills` | Is the file named exactly `SKILL.md`, in a direct subdirectory of one of the three layers? Is it UTF-8? Did you run `/skills reload` or restart? |
| Listed, but the model never activates it | Empty or unparsed `description` — look for the frontmatter warning in `agentao.log`. Is the skill disabled? Does the agent have `activate_skill`? |
| `Unknown skill` on activation | Names are case-sensitive and come from `name:`, not the directory. A disabled skill also answers `Unknown skill`. |
| Wrong copy is loaded | Two layers hold the same name. The later layer wins: `<wd>/skills/` over `<wd>/.agentao/skills/` over `~/.agentao/skills/`. |

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

## TL;DR

- A skill is a **directory** with `SKILL.md` (YAML frontmatter + body) and optional `references/`, `assets/` and `scripts/`, which activation lists by absolute path rather than inlines — the model `read_file`s them on demand (see [On-demand references](#on-demand-references)).
- The `description` decides *when* the LLM activates the skill — write it from the LLM's POV ("activate this when the user asks about X").
- 3 search layers: `~/.agentao/skills/` (global) → `<wd>/.agentao/skills/` → `<wd>/skills/`. Project-local wins.
- Many small specific skills > one monster skill. Activating a skill costs context tokens; small ones stay cheap.
- `/skills disable` is an **activation gate**, not a display filter, and a scan never prunes it — a name can outlive its skill and shows up under *Disabled, not found by the last scan* (see [Disabling a skill](#disabling-a-skill)).

→ Next: [5.3 MCP Integration](./3-mcp)
