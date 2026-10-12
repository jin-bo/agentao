# 5. Skills & Crystallize

A **skill** is a markdown file that teaches the agent how to handle a specific kind of task. The agent activates skills automatically when they look relevant, but you can also activate them manually. **Crystallize** is the reverse direction: distill a successful conversation into a new skill you can reuse.

## Skills, in 30 seconds

Each skill is a directory with a `SKILL.md`, in one of three places (see [Where skills live](#where-skills-live)). A `SKILL.md` looks like:

```markdown
---
name: pdf-to-markdown
description: Convert PDF files to Markdown using marker_single. Trigger on requests to convert .pdf files to markdown.
---

# PDF to Markdown

When the user asks for a PDF → markdown conversion:
1. Run `marker_single <path> --output_format markdown`
2. ...
```

The YAML frontmatter is what the agent reads when deciding whether to activate. Once active, the full SKILL.md body goes into the per-request `<system-reminder>` (the volatile tail, not the cached system prompt). Files under `references/`, `assets/` and `scripts/` are not inlined: activation lists them by absolute path, and the agent reads them with `read_file` when it needs them.

You don't have to know the skill exists — `/skills` lists them, the agent picks them up. You only step in when you want to force, suppress, or build a new one.

## `/skills` — list, activate, deactivate, reload

```text
> /skills

Available Skills (3):

  • pdf-to-markdown - PDF to Markdown
    Convert PDF files to Markdown using marker_single. Trigger on requests...
  • skill-creator - Skill Creator
    ...

Active Skills:
  • pdf-to-markdown: Convert report.pdf
```

The list shows each enabled skill's name, title and description. **Active Skills** names the skills active in this session, with the task each was activated for. Disabled skills get their own section.

| Subcommand | Effect |
|-----------|--------|
| `/skills` | List all skills, then the active ones |
| `/skills activate <name>` | Force-activate. The body is added to every request from now on. |
| `/skills deactivate <name>` | Remove from active set. The body is dropped. |
| `/skills disable <name>` | **Persistent** — saved to `skills_config.json`. Skill won't load even after restart. |
| `/skills enable <name>` | Reverse `/skills disable`. Skill is loadable again. |
| `/skills reload` | Re-scan all three skill directories. Use after editing a `SKILL.md` or adding a new skill folder. |

`/clear` and `/new` deactivate every active skill. `/status` shows how many are active.

`skills_config.json` is safe to hand-edit, and two Agentao processes can share it. Each `disable` / `enable` changes one name under a lock and keeps everything else in the file. If the file is malformed, the command refuses to write and names the path. Fix the file, then run the command again. Details: [Part 5.2, How `skills_config.json` is written](/en/part-5/2-skills#how-skills-config-json-is-written).

### Skills from MCP servers

An MCP server can serve skills too. Turn it on per server in `.agentao/mcp.json`:

```json
{ "mcpServers": { "docs": { "url": "https://docs.example/mcp", "skills": true } } }
```

`/skills` then shows an **MCP Skills** group. It lists the skills of each server by full name, `mcp:<server>:<SKILL.md URI>`, for example `mcp:docs:skill://pdf-processing/SKILL.md`. It also gives the reason for each skill that cannot be loaded. Compared with a local skill:

- **You approve it.** When the model activates one, you are asked once per conversation. The prompt shows the server, the description, and the file count and size. The approval binds to the files the server listed. If the server changes them, you are asked again. `/skills activate mcp:<server>:<uri>` counts as your consent, so it does not ask.
- **It is verified.** `SKILL.md` and every file read later must match the size, SHA-256 digest and frontmatter the server published. A file that does not match is not used.
- **Its files stay on the server.** The model reads them with `read_skill_file`, never from disk.
- **It tightens permissions.** From the first MCP skill loaded until `/clear`, shell commands, sub-agents that can run shell commands, and resource reads from other servers ask first, even in `full-access`. Deactivating the skill does not lift this.

Limits: mcp 2.x, a server on protocol 2026-07-28 or later, at most 512 files and 16 MiB per skill, and a `SKILL.md` of at most 100,000 bytes. `/skills` shows a skill over a limit, or a `"dynamic"` one, as unavailable. Depth for hosts: [Part 5.3, MCP skills](/en/part-5/3-mcp#mcp-skills).

::: tip activate vs. enable
- **activate / deactivate** is per-session — temporary, for *this* conversation
- **enable / disable** is persistent — config-level, affects all future sessions

Don't confuse the two. `disable` is a stronger statement; `deactivate` just means "not right now".
:::

## When to manually activate

Most of the time you don't. The agent reads skill descriptions and self-activates. You manually activate when:

- The agent doesn't see the trigger ("activate the xlsx skill so you can edit my spreadsheet")
- You want a skill's docs in the prompt for *your* benefit (you'll ask the agent to follow it)
- You're testing whether a new skill loads correctly

Activation is cheap, but not free. The SKILL.md body is re-sent, uncached, with every request while the skill is active. `/skills deactivate <name>` when you are done with it. The skill list itself sits in the cached system prompt and does not change when you activate a skill.

## `/crystallize` — turn this session into a skill

Run `/crystallize` after a session where you and the agent figured out a non-obvious way to do something. The CLI looks at the conversation, drafts a SKILL.md, and lets you iterate before saving.

### The flow

```
/crystallize           ──→  drafts a skill from the session
       │
       ├── /crystallize feedback "..."  ──→  rewrites with your guidance
       ├── /crystallize revise           ──→  interactively prompts for feedback
       ├── /crystallize refine           ──→  improves with skill-creator guidance
       ├── /crystallize status           ──→  shows current draft
       ├── /crystallize clear            ──→  discards the draft
       └── /crystallize create [name]    ──→  saves to <scope>/.agentao/skills/<name>/SKILL.md
```

### Subcommands

| Command | Effect |
|---------|--------|
| `/crystallize` (or `/crystallize suggest`) | Analyze the session, generate a draft. First time: pure analysis. After a draft exists: regenerates from scratch. |
| `/crystallize feedback <text>` | Pass a one-line note ("more specific triggers") and rewrite the draft. Repeat as many times as you like. |
| `/crystallize revise` | Same as `feedback`, but the CLI prompts you for the note interactively. |
| `/crystallize refine` | Hand the draft to the `skill-creator` skill for a structural pass — fixes frontmatter, sharpens triggers, tightens prose. |
| `/crystallize status` | Show the current pending draft, plus what's still in flight. |
| `/crystallize clear` | Discard the current draft (no save). |
| `/crystallize create` | Save the draft. Default name is inferred from the draft's `name:` frontmatter. |
| `/crystallize create my-name` | Save under the name `my-name`. The name is sanitized to a slug. |

`create` asks for a scope: press `g` for `~/.agentao/skills/<name>/SKILL.md` or `p` for `<project>/.agentao/skills/<name>/SKILL.md`. After `create`, the new skill is auto-loaded and immediately available — `/skills` shows it, the agent can self-activate it from the next turn.

### When to crystallize

| Situation | Worth crystallizing? |
|-----------|---------------------|
| You spent 5+ turns nudging the agent toward a specific approach | Yes — bake the approach in |
| You wrote a long prompt that worked once | Yes if reusable; no if one-off |
| The session involved a niche tool / API the agent doesn't know | Yes — the SKILL.md becomes the agent's reference |
| You did a generic refactor / bug fix | No — too specific or too generic; nothing reusable |
| You used `/plan` and got a great plan | Maybe — sometimes a plan template is more useful as a skill than as a one-shot plan |

### Pitfalls

- **First `/crystallize` on a clean session does nothing useful** — needs conversation evidence. Run it after the work, not before.
- **`feedback` accumulates** — each `/crystallize feedback` rewrites with all prior feedback applied. There's no undo within a draft; use `/crystallize` to start fresh from the session.
- **`refine` may overwrite your edits** — if you've manually edited the draft file, `refine` runs the LLM-driven skill-creator pass over the result and may smooth out your changes. Use `refine` *before* hand-editing.
- **Skill name conflicts** — `/crystallize create existing-name` overwrites the `SKILL.md` already in that scope, without asking. Run `/skills` first and pick a unique name.

## Installing skills from GitHub

Above the slash commands, Agentao ships a top-level shell subcommand for managing skills sourced from public repos. This runs **outside** the REPL — at your shell prompt, not after the `>`.

```bash
agentao skill install owner/repo[:path][@ref]
```

The ref format:

| Form | Example | Meaning |
|------|---------|---------|
| `owner/repo` | `myorg/my-skill` | The `SKILL.md` at the repo root, or the one subdirectory that holds a `SKILL.md`. Several such subdirectories are refused as ambiguous. |
| `owner/repo:path` | `anthropics/skills:skills/pdf` | Specific subdirectory |
| `owner/repo@ref` | `myorg/myskills@v1.2.0` | Pin to a tag, branch, or commit SHA |
| `owner/repo:path@ref` | `anthropics/skills:skills/pdf@main` | Both |

The package's `SKILL.md` must have `name` and `description` frontmatter, or the install is refused. Anthropic's official skills live in [`anthropics/skills`](https://github.com/anthropics/skills), for example `skills/pptx`, `skills/docx`, `skills/xlsx`, `skills/pdf` and `skills/doc-coauthoring`.

Scope:

```bash
agentao skill install anthropics/skills:skills/pdf --scope global
agentao skill install myorg/internal-skills:billing      --scope project
```

| Scope | Installs to | When to use |
|-------|-------------|-------------|
| `global` | `~/.agentao/skills/` | Personal, cross-project |
| `project` | `<project-root>/.agentao/skills/` | Team-shared, checked into the repo |

If `--scope` is omitted, the CLI auto-detects: `project` if a project root is found walking up from cwd (a `.agentao`, `.git`, `pyproject.toml` or `package.json` marker), else `global`.

`--force` overwrites an existing skill of the same name (otherwise the install refuses).

### The other three subcommands

```bash
agentao skill list                  # everything Agentao knows about
agentao skill list --installed      # only the ones managed by 'skill install'
agentao skill list --json           # machine-readable

agentao skill remove pdf            # uninstall by name
agentao skill remove pdf --scope global

agentao skill update pdf            # check for updates and pull if newer
agentao skill update --all          # check every managed skill across scopes
```

`update` only acts on skills with `source_type` ≠ `manual` — i.e. ones that were installed via `skill install`. Hand-written skills are left alone.

### After install: making it visible to a running session

Installing puts the SKILL.md on disk; an already-running CLI session won't see it until you tell it to. Two options:

- **In a running session**: `/skills reload` — re-scans the skills directories
- **Otherwise**: restart `agentao` — the next launch picks it up

Once visible, the skill behaves exactly like a hand-written one: `/skills activate <name>` to pull it into the prompt, or let the agent self-activate when the description matches.

### Pitfalls

- **GitHub rate limits hit fast on unauthenticated calls** — set `GITHUB_TOKEN` in your env if you're installing several at once
- **`@ref` pinning is your friend** — without it, you re-resolve the latest default branch on every `update`, which can yank changes into your environment unannounced
- **`skill install` doesn't activate** — it only puts files on disk. `/skills activate` (or self-activation) is still a separate step
- **Two scopes can shadow** — a `global` skill named `pdf` and a `project` skill named `pdf` both exist; project wins. Use `agentao skill list` to see what's authoritative
- **Runtime dependencies are not installed** — a skill may need LibreOffice, Poppler, Node or Python packages. Its `SKILL.md` says which. `skill install` copies only the skill's files.
- **A damaged `skills_registry.json` is never overwritten** — skill commands warn and treat the file as empty, but `install`, `update` and `remove` refuse to write it, so its records are not lost. Fix the file or move it aside to continue. A single unusable entry is skipped with a warning and kept in the file.

## Skill gallery in this repo

The repo ships a small gallery under [`examples/skills/`](https://github.com/jin-bo/agentao/tree/main/examples/skills) — host-agnostic, drop-in skills you can copy into any of the discovery locations. Use them as a starting point or as reference material when authoring your own.

| Skill | What it does | Trigger | Needs |
|-------|--------------|---------|-------|
| [`zootopia-ppt/`](https://github.com/jin-bo/agentao/tree/main/examples/skills/zootopia-ppt) | Turn a presentation script into a PPT-ready image set in *anthropomorphic-animal 3D-animation* style. Pipeline: outline → per-slide image prompts → batched generation. | "make this deck in Zootopia / 3D-animation style" | `TENSORLAB_API_KEY` (default backend) — or Gemini / Qwen / OpenRouter for alternates |
| [`pro-ppt/`](https://github.com/jin-bo/agentao/tree/main/examples/skills/pro-ppt) | Same pipeline, *editorial-premium business* style (light-grey base + gold accents + deep navy, McKinsey/Apple-Keynote vibe). Reuses `zootopia-ppt`'s scripts — install both together. | "make this deck in a premium / consulting / editorial style" | Same as `zootopia-ppt` |
| [`ocr/`](https://github.com/jin-bo/agentao/tree/main/examples/skills/ocr) | One-shot OCR on an image via Qwen-VL. | "OCR this screenshot" / "extract text from this image" | `QWEN_API_KEY` + `QWEN_BASE_URL` in `.env` |

**Install** by copy or symlink (these are not on GitHub via `skill install` — they live in this repo):

```bash
# Globally (any project you launch agentao from sees them)
cp -R examples/skills/ocr ~/.agentao/skills/

# Or scoped to one project
cp -R examples/skills/zootopia-ppt /path/to/your/project/.agentao/skills/
cp -R examples/skills/pro-ppt      /path/to/your/project/.agentao/skills/

# The PPT skills ship a requirements.txt — install Python deps once
# (ocr needs none: its script carries PEP 723 inline metadata for `uv run`)
pip install -r examples/skills/zootopia-ppt/requirements.txt
```

Then `/skills reload` (or restart) and the new skills show up in `/skills`.

::: tip Image-gen skills cost real money
`zootopia-ppt` and `pro-ppt` call paid image-generation APIs (TensorsLab / Gemini / DashScope-Wan / OpenRouter). Run an outline first to gauge token + image cost before generating a 30-page deck. None of the keys are hardcoded — they're read from `.env` or `--api-key`.
:::

The full gallery README — including the embedded-harness perspective on host-coupled vs host-agnostic skills — lives at [`examples/skills/README.md`](https://github.com/jin-bo/agentao/blob/main/examples/skills/README.md).

## Where skills live

| Path | Purpose |
|------|---------|
| `<project>/skills/<name>/SKILL.md` | The skill itself (frontmatter + body). This layer wins over the two below. |
| `skills/<name>/references/`, `assets/`, `scripts/` | On-demand files. Activation lists them by absolute path. |
| `~/.agentao/skills/<name>/` | Same layout, global scope (managed installs) |
| `<project-root>/.agentao/skills/<name>/` | Same layout, project scope (managed installs) |
| `~/.agentao/skills_registry.json` · `<project-root>/.agentao/skills_registry.json` | Tracks managed installs (source ref, version, install scope) |
| `.agentao/skills_config.json` (project) | Persistent enable/disable state — see [10. Configuration Reference](./10-config-reference) |
| [`examples/skills/`](https://github.com/jin-bo/agentao/tree/main/examples/skills) | Repo-shipped gallery (copy into one of the locations above) |

## Where to go next

| Want to… | Read |
|----------|------|
| Inspect what's in active skills (memory pressure) | [7. Context & Status](./7-context-status) |
| Author a skill from scratch (without crystallize) | [Part 5.2 · Skills](/en/part-5/2-skills) |
| Use Anthropic's `skill-creator` to author skills | Activate `skill-creator` and ask it directly |

---

::: info Where this fits
The skill manager is `agentao.skills.manager.SkillManager`, exposed on the agent as `agent.skill_manager`. Embedding hosts can `activate_skill()` / `deactivate_skill()` / read `available_skills` directly. The SKILL.md format is identical across CLI and embedded paths.
:::

::: tip Authoritative help
Command syntax: `/help`. Skill listing: [`agentao/cli/ui.py:list_skills`](https://github.com/jin-bo/agentao/blob/main/agentao/cli/ui.py). Crystallize logic: [`agentao/cli/commands_ext/crystallize/`](https://github.com/jin-bo/agentao/tree/main/agentao/cli/commands_ext/crystallize). Skill manager: [`agentao/skills/manager.py`](https://github.com/jin-bo/agentao/blob/main/agentao/skills/manager.py).
:::
